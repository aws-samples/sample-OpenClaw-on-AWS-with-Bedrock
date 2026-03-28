# OpenClaw Enterprise — Technical Architecture

**Version**: 2026-03-28
**Scope**: Complete technical reference for OpenClaw Enterprise on AWS Bedrock AgentCore.
**Principle**: Zero OpenClaw source modification. All enterprise controls operate via workspace files (SOUL.md, TOOLS.md, AGENTS.md) and external routing layers.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [EC2 Gateway Layer](#2-ec2-gateway-layer)
3. [Request Paths](#3-request-paths)
4. [Tenant ID Derivation](#4-tenant-id-derivation)
5. [Agent Runtime Modes](#5-agent-runtime-modes)
6. [Workspace Assembly](#6-workspace-assembly)
7. [Knowledge Base Injection](#7-knowledge-base-injection)
8. [Storage Architecture](#8-storage-architecture)
9. [Memory Persistence](#9-memory-persistence)
10. [Scheduled Tasks](#10-scheduled-tasks)
11. [Multi-Runtime Architecture](#11-multi-runtime-architecture)
12. [Digital Twin](#12-digital-twin)
13. [IM Channel Management](#13-im-channel-management)
14. [Security Model](#14-security-model)
15. [Data Architecture](#15-data-architecture)
16. [Cost Model](#16-cost-model)
17. [Known Limitations & Design Decisions](#17-known-limitations--design-decisions)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  User Entry Points                                                          │
│  Discord · Telegram · Feishu · Slack · WhatsApp · Portal · Admin Console   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ all IM channels
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  EC2 Gateway  (single instance, c7g.large, ~$52/mo)                        │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────────────────┐  │
│  │ OpenClaw Gateway│  │  H2 Proxy       │  │  Tenant Router             │  │
│  │ port 18789      │  │  port 8091      │  │  port 8090                 │  │
│  │ IM connections  │  │  intercepts     │  │  3-tier routing:           │  │
│  │ Web UI          │  │  Bedrock SDK    │  │  1. employee SSM override  │  │
│  │ bot tokens      │  │  extracts       │  │  2. position rule          │  │
│  │ heartbeat daemon│  │  sender_id      │  │  3. default runtime        │  │
│  └────────┬────────┘  └────────┬────────┘  └──────────┬─────────────────┘  │
│           │ inbound message    │ rewrite               │                    │
│           └───────────────────►│──────────────────────►│                    │
│                                                        │                    │
│  ┌─────────────────────────────────────────────────────┘                    │
│  │                 routing decision                                         │
│  ├──── always-on assigned? ──► ECS Fargate task (always-on agent)          │
│  └──── default ──────────────► AgentCore Runtime (Firecracker microVM)     │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Admin Console   port 8099                                           │   │
│  │  React (24 pages) + FastAPI (50+ endpoints) + DynamoDB + S3         │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
         │                                    │
         ▼ Firecracker microVM                ▼ ECS Fargate task
┌─────────────────────────────┐    ┌─────────────────────────────────────────┐
│  AgentCore Runtime          │    │  Always-on Agent Container (ECS)        │
│  per-request, per-tenant    │    │  1 task per shared agent                │
│  ~6s cold start             │    │  0ms cold start, persistent state       │
│  scales to zero at idle     │    │  OpenClaw Gateway + CronService running │
│  SOUL assembled from S3     │    │  workspace on EFS (planned) / S3 (now)  │
└─────────────────────────────┘    └─────────────────────────────────────────┘
         │                                    │
         └────────────────────┬───────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  AWS Services                                                               │
│  DynamoDB · S3 · SSM Parameter Store · Bedrock · ECR · CloudWatch · ECS    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. EC2 Gateway Layer

One EC2 instance hosts four independent processes. This is the only always-on compute component.

### 2.1 OpenClaw Gateway (port 18789)

The unmodified OpenClaw process. Responsibilities:

- Maintains persistent connections to all IM bots (Discord, Telegram, Feishu, Slack, WhatsApp)
- Runs the Web UI (served from npm package)
- Manages bot tokens (stored in `~/.openclaw/openclaw.json`, token value from SSM SecureString)
- Runs the heartbeat daemon (for always-on agents only — microVMs do not use this)
- Runs CronService (for always-on agents — microVMs do not use this)

**Critical**: This Gateway is the single IM entry point for the entire organization. All employees share the same bots. Routing to the correct agent happens downstream in the Tenant Router.

### 2.2 H2 Proxy (port 8091, `bedrock_proxy_h2.js`)

Intercepts Bedrock SDK HTTP/2 calls made by OpenClaw. Purpose: extract the sender's identity from the message context before OpenClaw makes a Bedrock API call.

Flow:
```
OpenClaw Gateway receives Discord DM from user 1484960930608578580
  → OpenClaw prepares Bedrock Converse API call
  → H2 Proxy intercepts the call (configured as Bedrock endpoint)
  → Extracts: channel="discord", userId="1484960930608578580"
  → POSTs {channel, userId, message} to Tenant Router
  → Tenant Router returns agent response
  → H2 Proxy returns response to OpenClaw as if it came from Bedrock
  → OpenClaw sends response back to Discord
```

The H2 Proxy also handles IM pairing: when an employee sends `/start TOKEN` to the bot, H2 Proxy intercepts it and writes the `channel__userId → emp_id` mapping to SSM.

### 2.3 Tenant Router (port 8090, `tenant_router.py`)

Python HTTP server. Single responsibility: given `{channel, user_id, message}`, route the message to the correct agent backend and return the response.

Routing logic (3-tier):

```python
# Tier 1: employee-level always-on override
SSM: /openclaw/{stack}/tenants/{emp_id}/always-on-agent = "agent-helpdesk"
  → SSM: /openclaw/{stack}/always-on/agent-helpdesk/endpoint = "http://{ecs_ip}:8080"
  → POST to ECS task directly

# Tier 2: position-level runtime assignment
SSM: /openclaw/{stack}/tenants/{emp_id}/position = "pos-exec"
SSM: /openclaw/{stack}/positions/pos-exec/runtime-id = "exec-runtime-id"
  → invoke AgentCore with exec-runtime-id

# Tier 3: default runtime
env: AGENTCORE_RUNTIME_ID (loaded from SSM at startup)
  → invoke AgentCore with default runtime
```

All lookups are cached for 5 minutes (SSM cache TTL) to reduce API costs.

### 2.4 Admin Console (port 8099, `main.py` + React)

FastAPI backend serving both the API and React static files. Accessed via SSM port forwarding (no public port).

- 24 pages: Dashboard, Org Tree, Agent Factory, Security Center, IM Channels, Knowledge Base, Usage & Cost, Audit Center, Approvals, Settings, Playground, + 5 Portal pages
- 50+ API endpoints
- Authentication: JWT signed with `JWT_SECRET` (stored in SSM SecureString)
- RBAC enforced at API level (admin / manager / employee)

Environment loaded from `/etc/openclaw/env` via systemd `EnvironmentFile`. Key vars:

```
STACK_NAME, AWS_REGION, GATEWAY_INSTANCE_ID, ECS_CLUSTER_NAME,
ECS_SUBNET_ID, ECS_TASK_SG_ID, DYNAMODB_REGION, PUBLIC_URL
```

---

## 3. Request Paths

### Path A: IM Message (Discord / Telegram / Feishu / Slack)

```
1. Employee sends Discord DM to @ACME Agent
2. Discord → OpenClaw Gateway EC2 (persistent bot connection)
3. OpenClaw prepares to invoke Bedrock
4. H2 Proxy intercepts → extracts channel="discord", userId="1484960930608578580"
5. H2 Proxy → POST /route to Tenant Router:
     {channel: "discord", user_id: "1484960930608578580", message: "..."}
6. Tenant Router:
     a. SSM: user-mapping/discord__1484960930608578580 → "emp-jiade"
     b. SSM: tenants/emp-jiade/always-on-agent → (not set)
     c. SSM: tenants/emp-jiade/position → "pos-sa"
     d. SSM: positions/pos-sa/runtime-id → (not set, use default)
     e. invoke_agent_runtime(runtimeSessionId="dc__emp-jiade__abc123")
7. AgentCore: Firecracker microVM boots with session "dc__emp-jiade__abc123"
8. entrypoint.sh: S3 sync → workspace_assembler.py → server.py starts
9. server.py receives request via AgentCore /invocations
10. openclaw agent CLI invoked → Bedrock → response
11. Response flows back: server.py → AgentCore → Tenant Router → H2 Proxy
    → OpenClaw Gateway → Discord bot → employee
```

### Path B: Portal (Web UI)

```
1. Employee opens http://localhost:8199 (via SSM port forward) or public CloudFront URL
2. Browser → POST /api/v1/portal/chat {message: "..."}
3. main.py (Admin Console) → POST /route to Tenant Router:
     {channel: "portal", user_id: "emp-david", message: "..."}
4. Same routing logic as Path A (Tenant Router)
5. Response returned to browser
```

Portal and IM share the same routing layer. No special handling. The `channel="portal"` prefix in the tenant_id (`port__emp-david__hash`) means Portal and IM sessions are separate (different `runtimeSessionId`), so memory is not shared across channels by default.

### Path C: Digital Twin (Public URL, no auth)

```
1. Visitor opens https://your-domain.com/twin/abc123token
2. React Router → TwinChat.tsx (SPA fallback, no auth)
3. Visitor sends message → POST /api/v1/public/twin/abc123token/chat
4. main.py: DynamoDB lookup TWIN#abc123token → {empId: "emp-jiade", active: true}
5. POST /route to Tenant Router:
     {channel: "twin", user_id: "emp-jiade", message: "..."}
6. Tenant Router derives tenant_id = "twin__emp-jiade__hash"
7. Routes to AgentCore (or always-on if assigned)
8. server.py detects tenant_id.startswith("twin__") → injects Digital Twin SOUL context
9. Agent responds as employee's AI representative
10. Response → main.py → browser
```

---

## 4. Tenant ID Derivation

Every message gets a stable, unique session identifier:

```python
def derive_tenant_id(channel: str, user_id: str) -> str:
    channel_short = CHANNEL_ALIASES.get(channel, channel[:4])
    # "discord"→"dc", "telegram"→"tg", "portal"→"port", "twin"→"twin"

    # SHA-256 suffix (daily rotation) ensures minimum 33 chars for AgentCore
    hash_suffix = sha256(f"{channel}:{user_id}:{date.today()}").hexdigest()[:19]
    return f"{channel_short}__{user_id}__{hash_suffix}"
    # e.g. "dc__emp-jiade__a1b2c3d4e5f6g7h8i"
```

**Why daily rotation**: AgentCore requires `runtimeSessionId >= 33 chars`. The date-based hash ensures this while keeping IDs human-readable. The side effect: a new session starts each calendar day, which is acceptable (memory persists via S3/EFS across sessions).

**Base ID extraction** (used for S3 paths, DynamoDB keys):

```python
parts = tenant_id.split("__")
base_id = parts[1]  # "emp-jiade" from "dc__emp-jiade__abc123"
```

**SSM user-mapping** (IM userId → emp_id): written by H2 Proxy on pairing:

```
/openclaw/{stack}/user-mapping/discord__1484960930608578580 = "emp-jiade"
```

server.py resolves IM user IDs to emp_ids via this mapping before workspace assembly.

---

## 5. Agent Runtime Modes

### 5.1 Serverless (AgentCore Firecracker microVM) — Default

| Property | Value |
|----------|-------|
| Startup | ~6s cold start (first message), near-instant warm |
| Lifecycle | Starts on request, terminates after response |
| Compute | Firecracker microVM (hardware VM isolation, ~1GB RAM) |
| State | Stateless between sessions; workspace loaded from S3 each session |
| Cost | Pay per invocation (included in Bedrock pricing) |
| Idle cost | Zero |
| CronService | Not available (process terminates after response) |
| Use case | Individual employees, most conversations |

Cold start sequence:
```
entrypoint.sh:
  1. Node.js runtime optimizations (V8 compile cache, IPv4 DNS)
  2. Write openclaw.json from env vars
  3. Start OpenClaw Gateway (port 18789) — wait up to 20s
  4. Start server.py (immediately, for health check)
  5. Background: S3 sync workspace, run workspace_assembler.py, load skills
  6. Watchdog loop: sync workspace back to S3 every SYNC_INTERVAL seconds
  SIGTERM handler: flush memory to S3, wait for Gateway to write MEMORY.md
```

### 5.2 Shared Always-on (ECS Fargate) — Team Agents

| Property | Value |
|----------|-------|
| Startup | 0ms (container already running) |
| Lifecycle | Persistent Docker container, `--restart unless-stopped` behavior via ECS |
| Compute | ECS Fargate task (512 CPU, 1024MB RAM) |
| State | In-memory workspace, synced to S3 periodically |
| Cost | ~$15-20/mo per always-on task (Fargate + ECS) |
| CronService | Active (OpenClaw Gateway runs continuously) |
| HEARTBEAT.md | Active (HeartbeatRunner polls it) |
| Use case | Shared team agents (help desk, HR bot, onboarding assistant) |
| Multi-employee | Multiple employees can be assigned to one shared agent |

**Workspace note**: Multiple employees routing to the same always-on container share the same OpenClaw workspace. This is intentional for shared agents (shared memory, shared identity). For personal agents, each employee should have their own container (see Planned: Personal Always-on below).

**ECS endpoint registration**: On container startup, `entrypoint.sh` reads the task's private IP from ECS metadata (v4) and writes it to SSM:
```
SSM: /openclaw/{stack}/always-on/{agent_id}/endpoint = "http://10.0.1.45:8080"
```
The Tenant Router reads this endpoint to route requests. No port mapping required (ECS awsvpc networking).

### 5.3 Personal Always-on (ECS Fargate) — Planned

One ECS Fargate task per employee who opts in. Identical to Shared Always-on but:
- Assigned to a single employee (`SSM: /tenants/{emp_id}/always-on-agent = {emp_id}`)
- Workspace loaded from the employee's personal S3 path
- `SHARED_AGENT_ID` not set (uses personal workspace paths)
- No workspace collision (1:1 employee-to-container)
- Enables reliable scheduled tasks for that employee

**Storage evolution for always-on (current → target)**:

| | Current | Target |
|--|---------|--------|
| Workspace storage | S3 sync (watchdog, every 60s) | EFS mounted at container path |
| Persistence on crash | Up to 60s data loss | Zero loss (NFS write-through) |
| S3 API overhead | 1440 LIST calls/day per container | Zero (EFS replaces S3 for hot data) |
| Mode handoff | S3 is source of truth | S3 on startup/shutdown, EFS during run |

**Why EFS over S3 sync for always-on**: A low-engagement always-on container (e.g., a department teacher agent used 5x/day) would still incur 1440 S3 LIST API calls/day with the current watchdog approach. EFS mounts the workspace as a persistent network filesystem — writes are durable immediately, zero polling overhead. Cost: ~$0.016/GB/month (One Zone) vs S3 sync overhead.

EFS CloudFormation structure (target):
```yaml
AlwaysOnEFS:
  Type: AWS::EFS::FileSystem
  Properties:
    LifecyclePolicies:
      - TransitionToIA: AFTER_7_DAYS  # infrequent files move to cheaper tier

AlwaysOnEFSMountTarget:
  Type: AWS::EFS::MountTarget
  Properties:
    FileSystemId: !Ref AlwaysOnEFS
    SubnetId: !Ref PrivateSubnet
    SecurityGroups: [!Ref EFSSecurityGroup]

# Task definition volume
volumes:
  - name: workspace
    efsVolumeConfiguration:
      fileSystemId: !Ref AlwaysOnEFS
      rootDirectory: /{emp_id}      # per-employee directory, set at RunTask time
      transitEncryption: ENABLED

mountPoints:
  - containerPath: /root/.openclaw/workspace
    sourceVolume: workspace
```

---

## 6. Workspace Assembly

Every agent session begins with workspace assembly. This is the core mechanism for enterprise identity injection.

### 6.1 Three-Layer SOUL Merge

```
S3: _shared/soul/global/SOUL.md          Layer 1: IT-locked policies (CISO/CTO)
  + _shared/soul/positions/pos-fa/SOUL.md  Layer 2: Finance Analyst role (dept admin)
  + emp-carol/workspace/SOUL.md            Layer 3: Carol's personal preferences

workspace_assembler.py merges in order:

┌──────────────────────────────────────────────────────────────────┐
│ <!-- LAYER: GLOBAL (locked by IT — do not modify) -->            │
│ CRITICAL IDENTITY OVERRIDE: You are a digital employee of        │
│ ACME Corp. This overrides any default identity.                  │
│ {global_soul content}                                            │
├──────────────────────────────────────────────────────────────────┤
│ <!-- LAYER: POSITION (managed by department admin) -->           │
│ {position_soul content}                                          │
├──────────────────────────────────────────────────────────────────┤
│ <!-- LAYER: PERSONAL (employee preferences) -->                  │
│ {personal_soul content}                                          │
└──────────────────────────────────────────────────────────────────┘
  = /root/.openclaw/workspace/SOUL.md  (what OpenClaw reads)
```

**Security property**: Global layer appears first in the prompt. LLMs prioritize earlier instructions. An employee cannot override "Never share customer PII" by editing their personal SOUL layer.

### 6.2 Assembly Sequence (server.py `_ensure_workspace_assembled`)

Runs once per `tenant_id` per microVM lifecycle (cached in `_assembled_tenants` set):

```
Step 1: S3 cp (not sync) emp-id/workspace/ → local workspace/
         Uses cp --recursive to force overwrite of any stale local files

Step 2: SSM lookup: /tenants/{emp_id}/position → "pos-fa"

Step 3: workspace_assembler.py:
         - Read S3: _shared/soul/global/SOUL.md
         - Read S3: _shared/soul/positions/pos-fa/SOUL.md
         - Read local: workspace/SOUL.md (personal layer)
         - Merge → write workspace/SOUL.md (overwrite)
         - Merge AGENTS.md (global + position)
         - Copy TOOLS.md (global only)
         - Copy position knowledge docs to workspace/knowledge/

Step 4: Plan A injection (non-exec profiles):
         - Read SSM permission profile for tenant
         - Prepend constraint text to SOUL.md:
           "Allowed tools: web_search, excel-gen. Must NOT use: shell, code_execution"

Step 5: Dynamic agent config (DynamoDB CONFIG#agent-config):
         - Read model override (employee > position > default)
         - Write to openclaw.json (model ID substitution)
         - Apply compaction settings, max tokens, language preference
         - Inject language preference at end of SOUL.md

Step 6: Knowledge Base injection (DynamoDB CONFIG#kb-assignments):
         - Lookup assigned KBs for this position + employee
         - For each KB: download files from S3 into workspace/knowledge/{kb_id}/
         - If KB has no files[] array (seeded KBs): fall back to s3Prefix listing
         - Append KB paths to SOUL.md so agent knows where to look

Step 7: CHANNELS.md injection:
         - SSM reverse lookup: scan /user-mapping/ for params where value=emp_id
         - Extract channel + userId pairs (e.g., discord: 1484960930608578580)
         - Write workspace/CHANNELS.md
         - OpenClaw uses this for proactive notification delivery (reminders)

Step 8: Digital Twin context (if tenant_id starts with "twin__"):
         - Append DIGITAL TWIN MODE context to SOUL.md
         - Instructions: act as employee's AI representative, use memory, no PII

Step 9: Write /tmp/tenant_id and /tmp/base_tenant_id for watchdog sync
```

### 6.3 Why Assembled Files Are NOT Synced Back

Writeback exclusion list (watchdog sync):
```
--exclude "SOUL.md"          ← assembled, would overwrite personal layer
--exclude "AGENTS.md"        ← assembled
--exclude "TOOLS.md"         ← assembled (global only)
--exclude "IDENTITY.md"      ← generated, not source
--exclude ".personal_soul_backup.md"  ← backup of personal layer pre-assembly
--exclude "knowledge/*"      ← downloaded from S3 KB store, not user-owned
```

If SOUL.md were synced back, a future assembly would read the already-merged version as the "personal layer", accumulating merges across sessions and potentially diluting the global security policies.

### 6.4 Config Change Propagation (Zero Redeploy)

Changes made in Admin Console take effect on next cold start:

| Change | Storage | Propagation |
|--------|---------|-------------|
| Global SOUL edit | S3 | Next session of every agent |
| Position SOUL edit | S3 | Next session of agents in that position |
| KB assignment change | DynamoDB CONFIG#kb-assignments | Next session |
| Model override | DynamoDB CONFIG#model | Next session (openclaw.json rewritten) |
| Language preference | DynamoDB CONFIG#agent-config | Next session |
| Runtime assignment | SSM positions/{pos_id}/runtime-id | Next message (cached 5min) |

For always-on containers: changes require container restart to re-run assembly. Planned mitigation: periodic DynamoDB config version check; clear `_assembled_tenants` on version change.

---

## 7. Knowledge Base Injection

### 7.1 KB Storage Structure

```
S3: _shared/knowledge/
  ├── company-policies/      KB: kb-policies
  │   ├── data-handling-policy.md
  │   ├── security-baseline.md
  │   └── code-of-conduct.md
  ├── org-directory/         KB: kb-org-directory  ← injected to ALL positions
  │   └── company-directory.md  (all employees, roles, IM channels, agent capabilities)
  ├── onboarding/            KB: kb-onboarding
  ├── arch-standards/        KB: kb-arch
  ├── runbooks/              KB: kb-runbooks
  ├── financial-reports/     KB: kb-finance
  ├── hr-policies/           KB: kb-hr
  ├── legal/                 KB: kb-legal
  ├── case-studies/          KB: kb-cases
  ├── product-docs/          KB: kb-product
  └── customer-playbooks/    KB: kb-customer
```

### 7.2 Assignment Model

```
DynamoDB CONFIG#kb-assignments:
  positionKBs:
    pos-sa:  [kb-policies, kb-onboarding, kb-org-directory, kb-cases, kb-arch]
    pos-fa:  [kb-policies, kb-onboarding, kb-org-directory, kb-finance]
    pos-sde: [kb-policies, kb-onboarding, kb-org-directory, kb-arch, kb-runbooks]
    ...all positions include kb-org-directory by default
  employeeKBs:
    emp-specific-override: [kb-extra]  ← employee-level additions
```

### 7.3 Runtime Injection

At session start, server.py downloads assigned KB files to `workspace/knowledge/{kb_id}/` and appends a reference block to SOUL.md:

```markdown
<!-- KNOWLEDGE BASES -->
You have access to the following knowledge base documents in your workspace:
- **Company Directory**: /root/.openclaw/workspace/knowledge/kb-org-directory/
- **Company Policies**: /root/.openclaw/workspace/knowledge/kb-policies/
Use the `file` tool to read these documents when relevant to the user's question.
```

The agent uses its `file` tool (if permitted) to read documents on demand. Documents are not injected into the context wholesale — the agent reads them when needed.

---

## 8. Storage Architecture

### 8.1 S3 — Workspace and Shared Assets

**Bucket**: `openclaw-tenants-{account_id}` (single bucket, all tenants)

```
_shared/                         ← Organization-wide shared assets
  soul/global/                   SOUL.md, AGENTS.md, TOOLS.md (Layer 1)
  soul/positions/{pos_id}/       Position SOUL templates (Layer 2)
  knowledge/{category}/          KB documents (Markdown files)
  skills/{skill_name}/           Skill packages (code + manifest)
  memory/{shared_agent_id}/      Shared agent memory (always-on)
  openclaw-creds/                Shared OpenClaw credentials (Discord allowFrom list)
  templates/                     SOUL templates for new employee bootstrap
  _deploy/                       Deployment artifacts (admin-deploy.tar.gz, gateway files)

emp-{id}/                        ← Per-employee personal workspace
  workspace/
    USER.md                      Preferences (communication style, timezone)
    MEMORY.md                    Compacted long-term memory
    memory/YYYY-MM-DD.md         Daily conversation checkpoints
    cron/jobs.json               OpenClaw CronService job store
    HEARTBEAT.md                 Scheduled reminder configuration
    skills/                      Employee-specific skill overrides
```

**Access pattern**: Serverless AgentCore microVMs read/write via `aws s3 cp --recursive` (forced overwrite) and `aws s3 sync`. Always-on containers (current): same S3 sync. Always-on containers (planned): EFS for hot workspace, S3 for cold backup.

### 8.2 DynamoDB — Organization Data

Single table: `openclaw-enterprise` (region: `us-east-2` by default, separate from deployment region).

**Single-table design** — PK/SK pattern:

```
PK              SK                      Entity
──────────────  ──────────────────────  ──────────────────────────────────────
ORG#acme        DEPT#dept-eng           Department
ORG#acme        POS#pos-sa              Position + skills + SOUL metadata
ORG#acme        EMP#emp-carol           Employee (name, position, channels)
ORG#acme        AGENT#agent-fa-carol    Agent (employeeId, positionId, status)
ORG#acme        BIND#bind-001           IM channel binding (employee ↔ agent)
ORG#acme        AUDIT#aud-{ts}          Audit event (every invocation, config change)
ORG#acme        USAGE#emp-carol#date    Daily token usage + cost (atomic ADD)
ORG#acme        SESSION#sess-id         Session metadata (turns, lastActive)
ORG#acme        CONV#sess-id#0001       Conversation turn (user + assistant)
ORG#acme        CONFIG#model            Model overrides (default + position + employee)
ORG#acme        CONFIG#agent-config     Compaction, maxTokens, language config
ORG#acme        CONFIG#kb-assignments   KB → position/employee assignments
ORG#acme        CONFIG#security         Security policy (PII detection, blocked tools)
ORG#acme        CONFIG#budgets          Department monthly budgets
ORG#acme        KB#kb-policies          Knowledge base metadata
ORG#acme        TWIN#token              Digital Twin (token → empId, active, stats)
ORG#acme        TWINOWNER#emp-id        Digital Twin reverse lookup (empId → token)
ORG#acme        APPROVAL#apr-001        Pending approvals (skill requests)
ORG#acme        RULE#rule-001           Channel routing rules
```

**GSI1**: `GSI1PK + GSI1SK` — enables type-based queries (list all employees, list all agents, usage by date).

### 8.3 SSM Parameter Store — Runtime Configuration

SSM stores mutable runtime state. Separated from DynamoDB because SSM parameters are accessed directly by agent containers (no DynamoDB SDK required in every operation).

```
/openclaw/{stack}/
  gateway-token                               OpenClaw Gateway auth token (SecureString)
  admin-password                              Admin Console login (SecureString)
  jwt-secret                                  JWT signing key (SecureString)
  runtime-id                                  Default AgentCore Runtime ID

  tenants/{emp_id}/
    position                                  pos-sa, pos-fa, etc.
    runtime-id                                Employee-level runtime override (optional)
    always-on-agent                           Agent ID if employee has always-on assignment

  positions/{pos_id}/
    runtime-id                                Position-level runtime assignment

  user-mapping/{channel}__{userId}            emp_id (IM pairing mapping)
    e.g. discord__1484960930608578580 = emp-jiade

  always-on/{agent_id}/
    endpoint                                  http://{ecs_task_ip}:8080
    task-arn                                  ECS task ARN (for stop/status)

  ecs/
    cluster-name, task-definition             ECS config (written by deploy script)
    subnet-id, task-sg-id
```

### 8.4 ECR — Container Images

**Repository**: `{stack}-multitenancy-agent`

Two image variants:
- `standard-agent:latest` — base OpenClaw + skills for standard tier (Nova 2 Lite, scoped IAM)
- `exec-agent:latest` — all skills pre-installed, Sonnet 4.6 config (executive tier)

Images are built locally and pushed to ECR. AgentCore Runtime and ECS task definitions reference the ECR URI. After each push, AgentCore Runtime must be updated (it resolves `:latest` digest at update time, not at run time).

---

## 9. Memory Persistence

### 9.1 Write Path — Three Checkpoints

**Checkpoint 1: Per-turn (immediate, server.py)**

After every OpenClaw invocation, `_append_conversation_turn()` writes locally AND `_sync_heartbeat_and_memory()` immediately syncs to S3:

```python
# Written to: workspace/memory/YYYY-MM-DD.md
# Format:
## 14:32 UTC
**User:** 帮我分析一下这个季度的预算差异
**Agent:** 根据你的 SAP 数据...
```

This ensures memory survives even if the microVM is SIGKILL'd (not SIGTERM'd) after responding.

**Checkpoint 2: Watchdog (every SYNC_INTERVAL=120s, entrypoint.sh)**

```bash
aws s3 sync "$WORKSPACE/" "s3://${S3_BUCKET}/${BASE_ID}/workspace/" \
    --exclude "SOUL.md" --exclude "AGENTS.md" --exclude "TOOLS.md" \
    --exclude "IDENTITY.md" --exclude ".personal_soul_backup.md" \
    --exclude "knowledge/*" \
    --size-only --region "$AWS_REGION"
```

**Checkpoint 3: SIGTERM flush (graceful shutdown, entrypoint.sh)**

```bash
cleanup() {
    kill $SERVER_PID
    kill -SIGTERM $GATEWAY_PID   # Gateway writes MEMORY.md during graceful shutdown
    wait for Gateway (up to 15s)
    # Final sync WITHOUT --size-only (catches Gateway-written MEMORY.md)
    aws s3 sync "$WORKSPACE/memory/" "${S3_TARGET}memory/"
    aws s3 cp "$WORKSPACE/MEMORY.md" "${S3_TARGET}MEMORY.md"
    aws s3 sync "$WORKSPACE/" "$S3_TARGET" --size-only ...
}
trap cleanup SIGTERM SIGINT
```

### 9.2 Read Path — Next Session

```
server.py._ensure_workspace_assembled():
  → aws s3 cp s3://bucket/emp-carol/workspace/ local/ --recursive
    (cp, not sync — forces overwrite of empty workspace files from entrypoint.sh)
  → workspace_assembler.py: assemble SOUL from 3 layers
  → OpenClaw reads workspace/memory/*.md at session start
  → Agent has context from all previous sessions
```

### 9.3 Cross-Channel Memory

Portal and IM channels create different tenant_ids (`port__emp-carol__hash` vs `tg__emp-carol__hash`). Both resolve to `base_id = emp-carol`, which maps to the same S3 path `emp-carol/workspace/`. Memory written from Discord is available in Portal and vice versa.

---

## 10. Scheduled Tasks

### 10.1 OpenClaw's Native Scheduling Mechanisms

OpenClaw has **two independent scheduling systems**:

**System 1: CronService** (`~/.openclaw/cron/jobs.json`)
- Powered by the `croner` JavaScript library (not Linux cron)
- In-process scheduler running inside the OpenClaw Gateway daemon
- Supports full cron expressions: `0 9 * * MON`, `*/5 * * * *`
- Supports one-time schedules: `at(2026-03-28T05:00:00Z)`
- Persists jobs in `cron/jobs.json` (included in S3 workspace sync)
- Requires Gateway to be continuously running to fire jobs
- Delivery target stored per job: channel, to (userId), accountId

**System 2: HEARTBEAT.md** (polling)
- Gateway HeartbeatRunner fires periodically (configurable interval)
- Sends special prompt to the agent: "Read HEARTBEAT.md, follow it strictly"
- Agent reads the file and acts on any pending tasks
- If nothing to do: agent replies `HEARTBEAT_OK` (suppressed from user)
- HEARTBEAT.md is free-form Markdown; the LLM interprets it
- Requires Gateway to be continuously running

### 10.2 Why Scheduled Tasks Fail in AgentCore microVMs

Both systems require the Gateway daemon to be continuously running. In the serverless model:

```
User: "5分钟后提醒我买咖啡"
  → AgentCore microVM starts
  → OpenClaw Gateway starts inside microVM
  → LLM writes HEARTBEAT.md (or creates cron job)
  → microVM returns response
  → AgentCore terminates microVM ← process dies
  → Gateway daemon (HeartbeatRunner, CronService) terminated
  → Nobody is watching HEARTBEAT.md or cron/jobs.json
  → 5 minutes pass. Nothing happens.
```

Even if the files are synced to S3 before termination, the next microVM to start doesn't know to check for due tasks unless the user sends a new message.

### 10.3 Product Design Decision

Rather than building complex external scheduling infrastructure (S3 events → Lambda → EventBridge → DynamoDB), the product takes a simpler approach:

**Scheduled tasks are a feature of always-on mode.**

UI messaging when user sets a reminder in serverless mode:

```
Agent response (injected via global SOUL.md):
"好的，我已记录这个提醒。由于我当前是按需模式，我会在你下次发消息
时提醒你。如需在指定时间收到提醒，可以在 Portal 开启持续模式。"
```

**When always-on is active** (ECS container, Gateway running 24/7):
- HEARTBEAT.md checked periodically → reminder fires on time ✅
- CronService fires cron jobs on schedule ✅
- CHANNELS.md in workspace tells OpenClaw which IM channel to use for delivery

### 10.4 CHANNELS.md — Outbound Delivery

Injected at workspace assembly. Enables the always-on container to deliver proactive notifications (reminders, cron jobs) via the correct IM channel:

```markdown
# Notification Channels

When sending reminders or proactive notifications, use these channels:
- **discord**: 1484960930608578580
- **telegram**: 123456789

Prefer the first available channel in the list above.
For portal/webchat sessions, fall back to the IM channel listed here.
```

Source: SSM user-mapping reverse lookup (`scan params where value == emp_id`), executed at workspace assembly time.

---

## 11. Multi-Runtime Architecture

### 11.1 Purpose

Different employee groups run in different AgentCore Runtimes, each backed by its own Docker image, IAM role, and model. This provides infrastructure-level isolation — an employee cannot access another tier's resources via prompt injection because their IAM role literally lacks the permissions.

### 11.2 Runtime Configuration

**Standard Runtime** (Engineering, Finance, HR, Sales, etc.):
```
Docker image: standard-agent:latest
  Skills: web-search, jina-reader, deep-research, github-pr (for SDE), excel-gen (for FA)
Model: Amazon Nova 2 Lite (global.amazon.nova-2-lite-v1:0)
IAM role: own S3 workspace only, own DynamoDB partition, own Bedrock models
```

**Executive Runtime** (C-Suite, Senior Leadership):
```
Docker image: exec-agent:latest
  Skills: all skills pre-installed, no role filtering
Model: Claude Sonnet 4.6 (global.anthropic.claude-sonnet-4-6-v1)
IAM role: full S3 access, cross-department DynamoDB read, all Bedrock models
Plan A: skipped (no permission constraints injected)
```

### 11.3 Routing Assignment

Three-tier lookup (Tenant Router + Security Center UI):

```
Tier 1 (employee override):
  SSM: /tenants/{emp_id}/runtime-id = "exec-runtime-id"
  Set from Agent Factory → Configuration tab (per-employee model override)

Tier 2 (position rule):
  SSM: /positions/{pos_id}/runtime-id = "exec-runtime-id"
  Set from Security Center → Runtimes → Position Assignments
  Propagates to ALL employees in the position automatically

Tier 3 (default):
  AGENTCORE_RUNTIME_ID env var on Tenant Router
  Loaded from SSM: /openclaw/{stack}/runtime-id at startup
```

### 11.4 Security Layers

| Layer | Mechanism | Can LLM bypass? |
|-------|-----------|----------------|
| L1 — Prompt | SOUL.md rules ("Finance cannot use shell") | Theoretically via injection |
| L2 — Application | Skills manifest `allowedRoles`/`blockedRoles` | Code bug risk |
| **L3 — IAM** | **Runtime role has no permission on target** | **Impossible** |
| **L4 — Compute** | **Firecracker microVM per invocation** | **Impossible** |

L3 and L4 are enforced at the AWS infrastructure level. A Finance Analyst's AgentCore execution role cannot `s3:GetObject` from the Engineering bucket regardless of what the LLM outputs.

---

## 12. Digital Twin

### 12.1 Concept

Every employee can generate a public URL for their agent. Anyone with the URL can chat with their AI representative — no login required.

```
Employee (Portal → My Profile → Digital Twin toggle ON)
  → Admin Console generates: token = secrets.token_urlsafe(20)
  → DynamoDB: TWIN#{token} = {empId, empName, positionName, active: true}
  → DynamoDB: TWINOWNER#{empId} = {tokenRef: token}
  → URL returned: https://{PUBLIC_URL}/twin/{token}

Visitor opens URL:
  → SPA fallback → TwinChat.tsx (no auth)
  → GET /api/v1/public/twin/{token} → employee info (no sensitive data)
  → Visitor sends message
  → POST /api/v1/public/twin/{token}/chat
  → Lookup TWIN#{token} → empId
  → POST /route to Tenant Router: {channel: "twin", user_id: empId}
  → tenant_id = "twin__empId__hash"
  → Route to AgentCore (or always-on if assigned)
  → server.py detects "twin__" prefix → inject Digital Twin SOUL context
  → Agent responds as: "I'm [Name]'s AI assistant..."

Employee turns OFF:
  → DynamoDB: TWIN#{token}.active = false, delete TWINOWNER#{empId}
  → Next visitor request: 404 ("This digital twin is not available")
```

### 12.2 Security Properties

- Token is random (20 URL-safe bytes = 160 bits entropy)
- Active flag checked on every request (revocation is instant)
- Digital Twin SOUL context explicitly forbids sharing private/sensitive data
- Employee can regenerate URL (old token immediately invalidated)
- View count and chat count tracked per token in DynamoDB

### 12.3 PUBLIC_URL Configuration

The twin URL must reflect the actual public address of the Admin Console. Set in `/etc/openclaw/env`:
```
PUBLIC_URL=https://your-domain.com
```
Default falls back to `https://openclaw.awspsa.com` (the demo site) — must be overridden in production.

---

## 13. IM Channel Management

### 13.1 One Bot, All Employees

IT admin connects bots once. All employees share the same bots:

```
Discord: IT creates "ACME Agent" app → connects token to OpenClaw Gateway
Telegram: IT creates @acme_bot → connects token to OpenClaw Gateway
Feishu: IT creates enterprise bot → connects token to OpenClaw Gateway

All employees DM the same bot.
Each gets their own Agent via Tenant Router routing.
```

### 13.2 Employee Self-Service Pairing

```
Step 1: Employee → Portal → Connect IM → select channel
Step 2: Admin Console generates pairing token, creates QR code
Step 3: Employee scans QR with phone → IM app opens, sends "/start TOKEN"
Step 4: H2 Proxy intercepts "/start TOKEN" message:
          - Validates token against DynamoDB pair_tokens
          - Writes SSM: /user-mapping/{channel}__{userId} = emp_id
          - Deletes one-time token from DynamoDB
          - Updates employee record in DynamoDB (channels list)
Step 5: Employee messages the bot → Tenant Router resolves emp_id → routes correctly
```

No admin approval needed. The token is single-use and expires. Admin sees all pairings in IM Channels page and can revoke with one click.

### 13.3 IM Channel Disconnect

Admin: Admin Console → IM Channels → select channel → Disconnect button
- Calls `DELETE /api/v1/admin/channels/{channel}/disconnect/{emp_id}`
- Deletes SSM user-mapping parameter
- Updates DynamoDB employee record
- Employee's next message: SSM lookup fails → Tenant Router cannot resolve emp_id → no routing

Employee self-service: Portal → Connect IM → disconnect button
- Same flow, scoped to own account only

---

## 14. Security Model

### 14.1 Network

- No public ports on EC2 (no port 80/443 directly exposed)
- Admin Console: SSM Session Manager port forwarding only (or CloudFront with origin restriction)
- All inter-service traffic within VPC
- VPC Endpoints for Bedrock, SSM (optional, +$22/mo) — traffic stays on AWS private network
- ECS tasks: awsvpc mode, security group allows inbound 8080 only from EC2 security group

### 14.2 Credentials

| Secret | Storage | Access |
|--------|---------|--------|
| Gateway token | SSM SecureString | EC2 instance role |
| Admin Console password | SSM SecureString | EC2 instance role |
| JWT signing key | SSM SecureString | EC2 instance role |
| Digital Twin tokens | DynamoDB (plaintext, random) | Admin Console API |
| IM bot tokens | OpenClaw config (SSM-seeded) | OpenClaw Gateway process only |
| AWS credentials | IAM roles | No hardcoded keys anywhere |

### 14.3 IAM Roles

**EC2 Instance Role**: SSM Session Manager, CloudWatch, Bedrock, SSM Parameter Store (read/write own stack's params), S3 (read/write tenant workspace bucket), ECR (pull images), ECS (run/stop tasks), DynamoDB (read/write enterprise table), EventBridge Scheduler.

**AgentCore Execution Role**: ECR (pull image), Bedrock (invoke models), SSM (own stack params, read/write), S3 (tenant workspace), DynamoDB (enterprise table), CloudWatch (write logs).

**ECS Task Role**: Same as AgentCore Execution Role + EFS (if mounted).

**Lambda roles** (when scheduled task infrastructure is added): invoke AgentCore, SSM read, DynamoDB read/write, EventBridge Scheduler manage.

### 14.4 Audit Trail

Every significant event is written to DynamoDB `AUDIT#{timestamp}`:

| Event | When |
|-------|------|
| `agent_invocation` | Every agent response (channel, duration, model) |
| `config_change` | SOUL edit, KB assignment change, model override |
| `permission_denied` | Plan E audit: blocked tool in response |
| `im_pairing` | Employee pairs/disconnects IM channel |
| `twin_enabled` / `twin_disabled` | Digital Twin toggle |
| `runtime_assignment` | Position assigned to runtime |
| `approval_resolved` | Skill request approved/denied |

Audit entries are immutable (only writes, no updates). Visible in Admin Console → Audit Center with filtering by employee, event type, and time range.

---

## 15. Data Architecture

### 15.1 DynamoDB Access Patterns

All endpoints use these query patterns:

```python
# List all employees
GSI1PK = "TYPE#employee"  → query GSI1

# Get single employee
PK = "ORG#acme", SK = "EMP#emp-carol"  → get_item

# Usage by date range
GSI1PK = "TYPE#usage", GSI1SK BETWEEN "USAGE#2026-03-01" AND "USAGE#2026-03-31"

# Sessions for employee
GSI1PK = "TYPE#session", filter: employeeId = "emp-carol"

# Conversation turns for session
PK = "ORG#acme", SK BEGINS_WITH "CONV#sess-id"  → query
```

### 15.2 S3 Access Patterns

```python
# Workspace read (session start)
aws s3 cp s3://bucket/emp-carol/workspace/ /root/.openclaw/workspace/ --recursive

# Workspace write (watchdog + post-invocation)
aws s3 sync /workspace/ s3://bucket/emp-carol/workspace/ --size-only --exclude ...

# Shared SOUL read (assembly)
s3.get_object(Bucket=bucket, Key="_shared/soul/global/SOUL.md")
s3.get_object(Bucket=bucket, Key="_shared/soul/positions/pos-fa/SOUL.md")

# KB files read (session start)
s3.list_objects_v2(Bucket=bucket, Prefix="_shared/knowledge/company-policies/")
s3.get_object(Bucket=bucket, Key="_shared/knowledge/company-policies/data-handling-policy.md")
```

---

## 16. Cost Model

### 16.1 Current (Serverless Only)

```
27 agents, ~100 conversations/day, Nova 2 Lite:

EC2 (c7g.large)              ~$52/mo    Gateway + Router + Admin Console
DynamoDB (pay-per-request)    ~$1/mo    ~2000 writes/day
S3                            <$1/mo    Workspaces, skills, knowledge (~10GB)
Bedrock (Nova 2 Lite)        ~$5-15/mo  ~100 conv/day × ~1000 tokens avg
AgentCore Runtime          included     Firecracker microVMs (in Bedrock pricing)
ECS (2 always-on agents)    ~$15/mo    Two shared always-on tasks
ECR                          ~$0.5/mo   Image storage
CloudWatch                   ~$2/mo     Logs + metrics
────────────────────────────────────────
Total                      ~$75-85/mo

vs ChatGPT Team: 27 × $25 = $675/mo   → 88% cheaper
vs dedicated EC2 per employee: 27 × $52 = $1,404/mo → 94% cheaper
```

### 16.2 With Personal Always-on (EFS target state)

```
Additional per employee opting into always-on:
  ECS Fargate task: ~$15/mo (512 CPU, 1024MB, 24/7)
  EFS One Zone: ~$0.016/GB × 50MB = ~$0/mo (negligible)
  ─────────────────────────────────────────────
  +~$15/mo per employee

Recommendation: employees who heavily use scheduled tasks or need 0ms
response time opt into always-on. Most employees stay serverless.
```

### 16.3 Cost per Conversation

```
Nova 2 Lite (default):
  Input: ~800 tokens × $0.30/1M = $0.00024
  Output: ~300 tokens × $2.50/1M = $0.00075
  Total: ~$0.001 per conversation

Claude Sonnet 4.6 (executive tier):
  ~$0.05 per conversation (50x more expensive)

At 100 conv/day for standard users: ~$3/mo in model costs
At 10 conv/day for executive users: ~$15/mo in model costs
```

---

## 17. Known Limitations & Design Decisions

### 17.1 Scheduled Tasks Require Always-on

**Limitation**: OpenClaw's HEARTBEAT.md and CronService both require the Gateway daemon to run continuously. AgentCore Firecracker microVMs terminate after each response — no persistent process survives.

**Decision**: Position scheduled tasks as an always-on feature. When a user in serverless mode sets a reminder, the agent acknowledges but informs them reminders fire on next message, not at the scheduled time. Recommend always-on for users who rely on scheduled tasks.

**Why not EventBridge + Lambda**: The additional infrastructure (S3 events, Lambda parsing croner expressions, EventBridge Scheduler rules, delivery Lambda with bot tokens) adds significant operational complexity for moderate benefit. The product-level solution is cleaner.

### 17.2 Daily Session Boundary

The tenant_id hash rotates daily (date-based). This means a new AgentCore session starts each calendar day. Memory persists via S3, so users don't lose context — but OpenClaw's in-session state (active tool calls, streaming context) resets at midnight UTC.

**Implication**: Users may notice a slight cold start at day boundaries. Always-on containers are not affected (no daily rotation; session persists in container memory).

### 17.3 Always-on Multi-Employee Workspace Race

Current shared always-on containers have a workspace collision risk: if two employees are routed to the same container simultaneously, `_ensure_workspace_assembled` may interleave, leaving the workspace in an inconsistent state.

**Mitigation**: The `_assembly_lock` (threading.Lock) in server.py serializes assembly. However, the workspace path (`/root/.openclaw/workspace/`) is singular — assembly for emp-A overwrites the workspace that emp-B was just assembled for.

**Decision for shared agents**: Shared always-on agents are intended to have a single shared identity (e.g., "IT Help Desk"). They should NOT load per-employee SOUL. The workspace is the shared agent's workspace. Per-employee personalization is not supported in shared always-on mode.

**Personal always-on**: 1:1 container-to-employee; no workspace collision.

### 17.4 S3 Sync vs EFS for Always-on

**Current**: always-on ECS containers use S3 sync (watchdog every 120s + SIGTERM flush).

**Problem**: idle always-on containers (e.g., department knowledge agents used infrequently) incur ~1440 S3 LIST API calls/day for no meaningful data change.

**Target**: EFS mounted at `/root/.openclaw/workspace/`. Writes are immediately persistent (NFS write-through). Zero S3 API overhead during idle periods. S3 remains for serverless AgentCore workspace handoff.

**Status**: EFS resources added to CloudFormation. Implementation pending (requires task definition volume config + mount target per employee at RunTask time).

### 17.5 SOUL Update Latency for Always-on

When IT edits the global SOUL.md, the change is written to S3 immediately. Serverless agents pick it up on next session start. Always-on containers cache the assembled workspace in `_assembled_tenants` — they do not re-read S3 mid-lifecycle.

**Current workaround**: restart the always-on ECS task after significant SOUL changes (Admin Console → Agent Factory → restart).

**Planned**: periodic DynamoDB config version check in server.py. If `CONFIG#global-version` incremented, clear `_assembled_tenants` → next message triggers re-assembly.

### 17.6 AgentCore Runtime Update After Image Push

After pushing a new Docker image to ECR with `docker push ... :latest`, the AgentCore Runtime does not automatically pick up the new image digest. A Runtime update is required:

```bash
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id $RUNTIME_ID \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}\"}}" \
  --role-arn $EXECUTION_ROLE_ARN \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --environment-variables "{...}"  ← MUST include all env vars; omitting clears them
```

**Critical**: `--environment-variables` must be passed on every update. Omitting it causes AgentCore to clear all env vars on the runtime, breaking the container.

### 17.7 Region Topology

The platform uses two AWS regions intentionally:

| Service | Region | Reason |
|---------|--------|--------|
| EC2, ECR, ECS, SSM, AgentCore | `us-east-1` (deployment region) | AgentCore only in us-east-1 / us-west-2 |
| DynamoDB | `us-east-2` | Organizational choice; lower latency to US East users |
| S3 | Global (bucket in deployment region) | S3 is globally accessible |
| CloudWatch Logs | Deployment region | Co-located with compute |

All code reads region from `AWS_REGION` env var (deployment region) or `DYNAMODB_REGION` for DynamoDB explicitly. No hardcoded region strings in production code.

### 17.8 OpenClaw Upgrade Path

Because we make zero modifications to OpenClaw source code:

1. `npm install -g openclaw-agentcore@latest` in Dockerfile
2. Rebuild Docker image
3. Push to ECR
4. Update AgentCore Runtime (see 17.6)
5. Redeploy always-on ECS tasks

The workspace file format (SOUL.md, MEMORY.md, etc.) is stable across OpenClaw versions. SOUL.md is plain Markdown — no format dependency. The only coupling point is the OpenClaw CLI invocation in server.py:

```python
["openclaw", "agent", "--session-id", tenant_id, "--message", message, "--json"]
```

This interface has been stable across all OpenClaw versions we've tested.
