# AgentCore Runtime — Problem Analysis & Assessment

> Date: 2026-04-14
> Context: After 2 weeks of production deployment and 40+ real conversations across 4 AgentCore Runtimes.
> Environments: ap-northeast-1 (production), us-east-2 (test)

---

## 1. Problem List (sorted by severity)

### P0: Impacts Basic Usability

#### 1.1 server.py Single-Thread HTTPServer — Healthcheck Blocks Agent Requests

- **Symptom:** Agent returns 502 on complex tasks (Bedrock calls taking >10s). Admin sees "Gateway Timeout" in Portal chat.
- **Root Cause:** Python's `http.server.HTTPServer` is single-threaded by default. AgentCore sends periodic `/ping` healthchecks on the same port (8080) as `/invocations`. When an agent invocation is in progress (Bedrock Converse call, 10-60s), the healthcheck TCP connection queues. If it times out before the invocation completes, AgentCore marks the microVM unhealthy and may kill it — returning 502 to the caller.
  - Code: `server.py:1228-1232` — original `HTTPServer` (single-threaded)
  - Fix: `server.py:1230` — changed to `ThreadedServer(ThreadingMixIn, HTTPServer)`
- **What We Tried:** Changed to `ThreadingMixIn` in source code.
- **Result:** Code fix works but is NOT in the running Docker image. Needs Docker rebuild + ECR push + runtime update.
- **User Impact:**
  - Demo: Complex tasks (deep-research, multi-tool) fail intermittently. Must retry.
  - Production: Unreliable for any task >10s. Unacceptable for real workloads.
- **AgentCore Platform Limitation:** No — this is our bug. Fix is ready, just needs rebuild.
- **BrokenPipeError variant:** The healthcheck arriving on a single-threaded server also generates `BrokenPipeError` log noise (Issue #12 below).

#### 1.2 OpenClaw Gateway Startup >30 Seconds — Tools Unavailable

- **Symptom:** First message in a new session gets a response, but tools (shell, web_search, file, browser) don't work. Agent says "I don't have access to tools" or only uses text-based responses. Tools become available on the 2nd or 3rd message.
- **Root Cause:** `entrypoint.sh:170-187` starts OpenClaw Gateway and waits up to 30s for it to be ready. The Gateway is a Node.js process that:
  1. Parses openclaw.json config (~2s)
  2. Discovers available tools from plugins + skills directories (~5s)
  3. Establishes local WebSocket server (~1s)
  4. First request triggers tool registration with the model (~20s on cold start)

  Total: 25-35s. During this window, `openclaw agent --message` falls back to "embedded mode" (no Gateway) which has no tool definitions.
- **What We Tried:**
  - Pre-warmed V8 compile cache at Docker build time (`entrypoint.sh:37-40`)
  - Synchronous workspace assembly before Gateway start (`entrypoint.sh:113-128`)
  - `--dns-result-order=ipv4first` to avoid IPv6 timeout (`entrypoint.sh:44`)
- **Result:** Reduced from ~45s to ~30s. Still not fast enough for first-message tool use.
- **User Impact:**
  - Demo: First message after cold start can't use tools. Must send a throwaway message, wait, then send the real request.
  - Production: Employees will think tools are broken. High support load.
- **AgentCore Platform Limitation:** Partially. Gateway startup time is an OpenClaw issue, but AgentCore's microVM lifecycle forces a cold start every time the session expires (15-60min idle). A persistent container (Fargate) eliminates this entirely.

#### 1.3 Cold Start 25-Second Latency

- **Symptom:** Employee sends first message → waits 25 seconds → gets response. Subsequent messages are fast (0.5-3s).
- **Root Cause:** AgentCore microVM lifecycle:
  1. Firecracker microVM provisioning (~3s)
  2. Container startup + entrypoint.sh (~5s)
  3. S3 workspace sync (~3s)
  4. workspace_assembler.py SOUL merge + DynamoDB reads (~4s)
  5. skill_loader.py S3 skill download (~3s)
  6. Gateway startup (~7s minimum, up to 30s)
  7. server.py ready for first /invocations

  Total: 25-35s for first invocation.

  Session Storage optimization reduces this to ~0.5s for resumed sessions (no S3 sync needed). But after idle timeout (15-60min), the microVM is destroyed and cold start happens again.
- **What We Tried:**
  - Session Storage optimization in `server.py:310-317` — skip assembly if workspace intact
  - V8 compile cache (`entrypoint.sh:37-40`)
  - Background S3 sync (`entrypoint.sh:234-325`) — server.py starts before sync completes
- **Result:** Resumed sessions are fast (0.5s). But first cold start of the day or after idle is still 25s.
- **User Impact:**
  - Demo: Awkward 25s pause. Must warn audiences.
  - Production: Employees using agent sporadically (every few hours) hit cold start every time.
- **AgentCore Platform Limitation:** Yes. Firecracker microVM provisioning + mandatory restart after idle = cold start is inherent to the serverless model.

#### 1.4 Runtime Update Clears ALL Sessions

- **Symptom:** Admin changes model, guardrail, or env vars via `update-agent-runtime` → ALL employees' sessions are terminated. Next message triggers full cold start for everyone.
- **Root Cause:** `update-agent-runtime` API replaces the runtime configuration atomically. AgentCore cannot hot-swap config on running microVMs. All existing sessions are invalidated, and new microVMs are created with the updated config on next invocation.
- **What We Tried:**
  - "Force Refresh All Sessions" button in Playground for testing purposes
  - per-employee session stop via `stop_runtime_session` API
- **Result:** No workaround. Runtime update = mass eviction.
- **User Impact:**
  - Demo: Changing model during demo disrupts all live conversations.
  - Production: Config changes must be scheduled during low-usage hours. No rolling update possible.
- **AgentCore Platform Limitation:** Yes. No hot-reload capability in AgentCore Runtime API.

### P1: Impacts Experience

#### 1.5 Session Storage Black Box — No API, ~1GB Limit

- **Symptom:** Admin cannot control what Session Storage saves, how much, or when it clears. Storage fills up silently; agent workspace becomes slow.
- **Root Cause:** AgentCore Session Storage is an opaque snapshot mechanism:
  - Automatically snapshots the microVM's writable overlay between sessions
  - No API to query usage, configure retention, or selectively clear
  - Documented limit: ~1GB writable space
  - Undocumented behavior: may snapshot more than expected (e.g., node_modules, skill downloads)
- **What We Tried:**
  - `_enforce_workspace_budget()` in `workspace_assembler.py:293-326` — caps user files at 100MB
  - `entrypoint.sh:72-74` — cleans output/ on session restore
  - `entrypoint.sh:236` — excludes output/ from cold start S3 sync
  - Watchdog sync excludes platform files (SOUL.md, skills/, knowledge/, node_modules)
- **Result:** Our workarounds keep workspace lean. But we cannot guarantee what Session Storage snapshots beyond our workspace directory. If OpenClaw's own files (node_modules, .cache) grow, Session Storage could still fill.
- **User Impact:**
  - Demo: Manageable with 100MB budget.
  - Production: Risk of silent degradation after weeks of use. No monitoring available.
- **AgentCore Platform Limitation:** Yes. No Session Storage management API.

#### 1.6 tenant=unknown at Startup — SOUL Not Assembled

- **Symptom:** First message to a new microVM gets a generic response without employee identity, position context, or KB references. SOUL.md is just "You are a helpful AI assistant."
- **Root Cause:** AgentCore starts the container without knowing which employee will send the first message. `entrypoint.sh:10` sets `TENANT_ID` from the `SESSION_ID` env var — but for the Standard runtime (shared by many employees), this is either "unknown" or a runtime-level session ID, not an employee ID.

  The real tenant_id arrives in the first `/invocations` request header (`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`). Only then can `server.py:295-635` (`_ensure_workspace_assembled`) run the full SOUL merge with the correct employee's position and personal SOUL.

  - `entrypoint.sh:260-271` — skips workspace assembly when tenant=unknown
  - `server.py:302-303` — early return if tenant_id is "unknown"
- **What We Tried:**
  - Pre-Gateway synchronous assembly for always-on containers (`entrypoint.sh:113-128`) — only works when SESSION_ID is known
  - Lazy assembly on first invocation (`server.py:295`) — works but adds 6s to first response
- **Result:** Lazy assembly works correctly. First invocation takes 6s longer (S3 sync + SOUL merge + DynamoDB reads). Subsequent invocations are instant.
- **User Impact:**
  - Demo: First message is slightly slower. Acceptable.
  - Production: First response takes ~30s (cold start) + ~6s (assembly) = ~36s total. Poor.
- **AgentCore Platform Limitation:** Partially. AgentCore doesn't forward tenant_id at container startup — only at invocation time. Our lazy assembly pattern works but adds latency.

#### 1.7 Model ID Format Confusion — global. Prefix

- **Symptom:** Agent fails with "on-demand throughput not supported" error. Model ID looks correct in config but Bedrock rejects it.
- **Root Cause:** Bedrock cross-region inference requires the `global.` prefix on model IDs (e.g., `global.anthropic.claude-sonnet-4-5-20250929-v1:0`). The native Bedrock Console shows models without the prefix. Our config templates initially used unprefixed IDs.

  Additionally, different model families use different prefix conventions:
  - Anthropic: `global.anthropic.*` (cross-region)
  - Amazon: `global.amazon.*` (cross-region) or `us.amazon.*` (single-region)
  - Meta: `us.meta.*` (single-region only)
  - DeepSeek: `us.deepseek.*` (single-region only)
  - MiniMax: `us.minimax.*` (single-region only)
  - Moonshot/Kimi: `moonshotai.*` (Project Mantle, different endpoint)
- **What We Tried:**
  - Hardcoded the correct IDs in CloudFormation `AllowedValues`
  - `deploy.sh:322` uses the correct default model ID
  - Settings page model selection uses validated IDs from DynamoDB CONFIG#model
- **Result:** Fixed after correcting all model IDs. But easy to misconfigure.
- **User Impact:**
  - Demo: Initial setup confusion. Fixed once.
  - Production: Admin must use exact model IDs from our allowed list.
- **AgentCore Platform Limitation:** No. This is Bedrock model API design, not AgentCore-specific.

#### 1.8 Guardrail Only via Environment Variable — Not in Runtime API

- **Symptom:** Cannot dynamically change guardrail per employee or per request. Must update the entire runtime (which clears all sessions, see #1.4).
- **Root Cause:** `create-agent-runtime` API does not accept guardrail parameters. The only way to apply guardrails is:
  1. Set `GUARDRAIL_ID` and `GUARDRAIL_VERSION` as environment variables on the runtime
  2. Apply guardrail in `server.py:851-897` (`_apply_guardrail()`) using `bedrock-runtime:ApplyGuardrail` API before/after each invocation

  Our implementation uses approach #2 — the container reads `GUARDRAIL_ID` from env and calls `apply_guardrail()` on every request. This works per-runtime but not per-employee within a shared runtime.

  We work around this by having 4 runtimes with different guardrail settings:
  - Standard: moderate guardrail
  - Restricted: strict guardrail
  - Engineering/Executive: no guardrail
- **What We Tried:**
  - Approach #2: `server.py:1079-1083` applies guardrail on INPUT, `server.py:1131-1135` on OUTPUT
  - 4-runtime architecture with different GUARDRAIL_ID per runtime
- **Result:** Works. Per-tier guardrails are effective. But granularity is limited to runtime level.
- **User Impact:**
  - Demo: Works well with 4-tier architecture.
  - Production: If two employees in the same position need different guardrails, they need different runtimes.
- **AgentCore Platform Limitation:** Partially. Runtime API doesn't support guardrail natively, but our workaround (bedrock-runtime:ApplyGuardrail) achieves the same result.

### P2: Acceptable Limitations

#### 1.9 No MicroVM Logs from Admin Console — Only CloudWatch

- **Symptom:** Admin cannot see what's happening inside an agent's microVM from the Admin Console. Must go to CloudWatch Logs → AgentCore log group → find the correct log stream.
- **Root Cause:** AgentCore streams container stdout/stderr to CloudWatch Logs. There's no API to query logs for a specific session or tenant from outside. The log group uses a naming convention based on runtime ID, not tenant ID.
- **What We Tried:** Nothing — this is a fundamental AgentCore limitation.
- **Result:** Admin must use AWS Console or CLI for debugging.
- **User Impact:**
  - Demo: Not visible to demo audience.
  - Production: Slows down debugging. Admin needs CloudWatch access.
- **AgentCore Platform Limitation:** Yes. No log query API.

#### 1.10 Workspace Persistence — 3-Way State Entanglement

- **Symptom:** Agent workspace data exists in 3 places simultaneously: microVM local filesystem, Session Storage snapshot, and S3. They can diverge.
- **Root Cause:** The persistence model:
  1. **MicroVM local FS** — the live workspace during a session
  2. **Session Storage** — automatic snapshot between sessions (black box)
  3. **S3** — our watchdog syncs workspace/ to S3 every 60s + on SIGTERM

  Divergence scenarios:
  - MicroVM killed (SIGKILL, not SIGTERM): local changes lost, Session Storage has last snapshot, S3 has last watchdog sync (up to 60s stale)
  - Session Storage restore + S3 sync: Session Storage may have stale files that `aws s3 sync` won't overwrite (local files are newer)
  - Config change via admin: S3 SOUL updated, Session Storage has old SOUL, microVM has old SOUL

  Code references:
  - Watchdog sync: `entrypoint.sh:301-324`
  - SIGTERM cleanup: `entrypoint.sh:332-397`
  - S3 cp --recursive to force overwrite: `server.py:396-408`
  - Post-invocation sync: `server.py:678-712` (fire-and-forget HEARTBEAT + memory)
- **What We Tried:**
  - `aws s3 cp --recursive` instead of `sync` for initial pull (forces S3 → local overwrite)
  - Post-invocation fire-and-forget sync for HEARTBEAT.md and memory/
  - Session Storage optimization with config_version check
  - EFS mode for always-on containers (eliminates the 3-way split)
- **Result:** Mostly works. Rare edge case: SIGKILL after invocation but before fire-and-forget sync completes → memory/HEARTBEAT.md lost for that turn.
- **User Impact:**
  - Demo: Not observable.
  - Production: Occasional lost memory turn (rare). HEARTBEAT reminders may miss one cycle.
- **AgentCore Platform Limitation:** Partially. Session Storage being a black box forces us into the 3-way sync pattern. Fargate + EFS eliminates this entirely.

#### 1.11 Docker Image Rebuild Doesn't Auto-Trigger Runtime Update

- **Symptom:** After `docker build` + `docker push` to ECR, running agents still use the old image. Must manually call `update-agent-runtime` API.
- **Root Cause:** AgentCore caches the Docker image digest. A new `:latest` tag in ECR doesn't trigger automatic re-pull. The `update-agent-runtime` API with the same container URI forces a re-pull.
  - `deploy.sh:305-310` — calls `update-agent-runtime` with the same image URI to force refresh
- **What We Tried:** `deploy.sh` Step 4 calls update-agent-runtime after every build.
- **Result:** Works but requires manual deploy script execution. No CI/CD integration.
- **User Impact:**
  - Demo: Remember to run deploy.sh or manually call update-agent-runtime.
  - Production: Must be part of deployment pipeline.
- **AgentCore Platform Limitation:** Partially. Standard ECR + ECS behavior — ECS Services can use `forceNewDeployment`. AgentCore has no equivalent automatic mechanism.

#### 1.12 BrokenPipeError Log Flood — Healthcheck Noise

- **Symptom:** Server logs filled with `BrokenPipeError` at high frequency. Obscures real errors.
- **Root Cause:** AgentCore sends HTTP healthchecks to `/ping`. When the server is busy (single-thread, see #1.1), the healthcheck connection times out and the client disconnects before the server can respond. Python's `BaseHTTPRequestHandler.wfile.write()` then fails with `BrokenPipeError`.

  With ThreadingMixIn (#1.1 fix), the healthcheck runs on its own thread, eliminating the timeout. But the log noise persists in the current deployed image.
- **What We Tried:** ThreadingMixIn fix (resolves both #1.1 and #1.12).
- **Result:** Fix not deployed yet (needs Docker rebuild).
- **User Impact:**
  - Demo: Noisy logs but no functional impact.
  - Production: Harder to debug real issues in CloudWatch.
- **AgentCore Platform Limitation:** No — our bug, fixed by ThreadingMixIn.

---

## 2. AgentCore Platform Limitations (we cannot change)

| # | Limitation | Impact | Workaround |
|---|-----------|--------|------------|
| L1 | Cold start 3-7s (Firecracker provisioning) | First message slow | None possible |
| L2 | Session invalidation on runtime update | Config change = mass eviction | Schedule changes, use 4 runtimes |
| L3 | Session Storage is black box (~1GB, no API) | Cannot control, monitor, or clear | 100MB workspace budget enforcement |
| L4 | No tenant_id at container startup | Can't pre-assemble workspace | Lazy assembly on first invocation |
| L5 | No log query API | Must use CloudWatch Console | Write key events to DynamoDB AUDIT# |
| L6 | No rolling update / canary deployment | All-or-nothing runtime update | 4 separate runtimes for isolation |
| L7 | Idle timeout destroys microVM (15-60min) | Frequent cold starts for sporadic users | Fargate for VIP/heavy users |
| L8 | No guardrail in create-agent-runtime API | Per-runtime only, not per-employee | Apply guardrail in server.py + 4 tiers |

---

## 3. Our Workarounds (already implemented)

| # | Problem | Workaround | Code Location | Effectiveness |
|---|---------|-----------|---------------|---------------|
| W1 | Single-thread server | ThreadingMixIn | server.py:1230 | Full fix (pending rebuild) |
| W2 | Gateway slow start | V8 compile cache + IPv4 first | entrypoint.sh:37-44 | Partial (30s→25s) |
| W3 | Cold start latency | Session Storage optimization | server.py:310-317 | Full for resumed sessions |
| W4 | Session Storage budget | 100MB workspace enforcement | workspace_assembler.py:293-326 | Effective |
| W5 | output/ accumulation | Clean on session restore + exclude from sync | entrypoint.sh:72-74, 236 | Full fix |
| W6 | tenant=unknown | Lazy assembly on first invocation | server.py:295-635 | Works (adds 6s) |
| W7 | Model ID confusion | AllowedValues + validated config | CloudFormation + DynamoDB | Full fix |
| W8 | Guardrail binding | GUARDRAIL_ID env var + apply_guardrail() | server.py:851-897 | Full fix |
| W9 | 3-way state sync | Post-invocation fire-and-forget S3 sync | server.py:678-712 | 95% (SIGKILL edge case) |
| W10 | Log noise | ThreadingMixIn | server.py:1230 | Full fix (pending rebuild) |
| W11 | Config version | DynamoDB poll every 5 min + cache eviction | server.py:70-91 | Works well |
| W12 | Memory persistence | Daily memory files + MEMORY.md synthesis | server.py:101-163, 463-499 | Full fix |

---

## 4. Customer Demo Impact Assessment

| Scenario | Impact | Mitigation |
|----------|--------|-----------|
| **First message cold start** | 25-35s wait | Warn audience, send warm-up message beforehand |
| **Tools unavailable** | First msg has no tools | Gateway needs >30s; send throwaway message first |
| **502 on complex tasks** | Intermittent failure | ThreadingMixIn fix pending; retry resolves |
| **Config change during demo** | All sessions reset | Don't change runtime config during live demo |
| **Multi-model showcase** | Switching model → mass eviction | Use 4 pre-configured runtimes, route by position |
| **Guardrail demo** | Works via GUARDRAIL_ID | Send message to Restricted tier employee |
| **Memory across sessions** | Works via daily memory files | Fire-and-forget sync ensures persistence |
| **4-tier security** | Works well | Route via Security Center assignment |

**Overall demo readiness: 7/10.** Cold start and tool availability are the main pain points. Everything else works.

---

## 5. Production Deployment Impact Assessment

| Requirement | Status | Gap |
|-------------|--------|-----|
| **Reliability** | Poor — 502 on complex tasks | ThreadingMixIn rebuild needed (P0.5.1) |
| **Latency** | Poor — 25-35s cold start | Inherent to AgentCore serverless. Fargate solves. |
| **Scalability** | Good — AgentCore auto-scales microVMs | No issues observed |
| **Cost** | Good — pay per invocation | ~$0.001/invocation overhead |
| **Security** | Good — 4-tier architecture works | Guardrails effective, Plan A tool whitelist works |
| **Observability** | Poor — no in-console logs | Must use CloudWatch. DynamoDB AUDIT# helps. |
| **Config management** | Poor — update = mass eviction | Must schedule updates during off-hours |
| **Storage** | Acceptable — 100MB budget, S3 backup | EFS needed for heavy users (Fargate) |
| **Multi-tenancy** | Good — per-session isolation | Firecracker provides strong isolation |
| **Always-on (IM bots)** | Not possible | AgentCore destroys microVM on idle. Fargate required. |

**Overall production readiness: 5/10.** Reliability (502) and latency (cold start) are blocking issues. Config management is operationally painful. Always-on use cases are impossible.

---

## 6. Conclusion: Fargate Alternative is Required

### Why AgentCore Alone Is Not Sufficient

1. **Always-on use cases (IM bots, scheduled tasks, real-time customer service) are impossible** with AgentCore's serverless model. The microVM is destroyed after idle.

2. **Cold start latency (25-35s) is unacceptable for production** where employees use agents sporadically throughout the day.

3. **Config changes causing mass session eviction** makes operations painful and risky.

4. **No rolling updates** means any runtime change is all-or-nothing.

### Recommended Architecture: Hybrid (AgentCore + Fargate)

| Workload | Deploy Mode | Why |
|----------|------------|-----|
| Light chat, sporadic use, 80% of employees | AgentCore serverless | Cost-effective, good enough latency after warm-up |
| Always-on IM bots, customer service, executive assistants | **Fargate** | No cold start, persistent connections, EFS storage |
| Heavy file processing, large datasets | **Fargate** | Unlimited EFS storage, no 100MB budget |
| Development/testing | AgentCore | Scale to zero, cheap |

### What Already Exists for Fargate

| Component | Status | Location |
|-----------|--------|----------|
| ECS Cluster + Task Definition | Deployed (CloudFormation) | clawdbot-bedrock-agentcore-multitenancy.yaml:976-1214 |
| EFS filesystem + mount targets | Deployed (CloudFormation) | clawdbot-bedrock-agentcore-multitenancy.yaml:1016-1035 |
| IAM roles (execution + task) | Deployed (CloudFormation) | clawdbot-bedrock-agentcore-multitenancy.yaml:1057-1128 |
| Security groups (ECS task + EFS) | Deployed (CloudFormation) | clawdbot-bedrock-agentcore-multitenancy.yaml:995-1141 |
| entrypoint.sh EFS mode | Implemented | entrypoint.sh:50-65 |
| entrypoint.sh SIGTERM cleanup + EFS→S3 snapshot | Implemented | entrypoint.sh:332-397 |
| SSM endpoint registration | Implemented | entrypoint.sh:400-445 |
| Tenant Router always-on routing | Implemented | tenant_router.py:387-425, 507-510 |
| Admin Console start/stop/reload/assign APIs | Implemented | admin_always_on.py (full file) |
| ECS Service (auto-restart) | Implemented | admin_always_on.py:107-236 |
| Bot token injection (Telegram/Discord) | Implemented | entrypoint.sh:136-162 |

**The Fargate path is ~70% built.** What's needed: per-tier task definitions, deploy.sh integration, and end-to-end testing.
