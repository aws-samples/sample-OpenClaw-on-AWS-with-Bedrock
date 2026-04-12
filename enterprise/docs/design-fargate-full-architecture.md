# Fargate-First Architecture Design

> Date: 2026-04-14
> Status: DESIGN — implementation follows in feature/fargate-first branch
> Prerequisites: Read agentcore-issues-analysis.md for the "why"

---

## 1. Why Fargate Is Needed

### AgentCore Problems Summary

| # | Problem | Fargate Solves? |
|---|---------|----------------|
| 1 | server.py single-thread (502) | No (our bug, fix pending rebuild) |
| 2 | Gateway startup >30s (no tools) | **YES** — Gateway stays running, tools always ready |
| 3 | Cold start 25s latency | **YES** — container always running, 0ms cold start |
| 4 | Runtime update clears sessions | **YES** — ECS rolling update, no session loss |
| 5 | Session Storage black box | **YES** — EFS is transparent, unlimited, controllable |
| 6 | tenant=unknown at startup | **YES** — container knows its tenant from env vars |
| 7 | Model ID confusion | No (config issue, same in both) |
| 8 | Guardrail only via env var | Same — GUARDRAIL_ID works identically |
| 9 | No microVM logs in console | **Partially** — can tail ECS task logs via API |
| 10 | 3-way state entanglement | **YES** — EFS only, no Session Storage |
| 11 | Docker rebuild no auto-update | **YES** — ECS force-new-deployment auto-pulls |
| 12 | BrokenPipeError noise | No (our bug, same fix) |

**Fargate solves 7 of 12 problems**, including the 3 most impactful (cold start, tools, session invalidation).

### What Fargate Cannot Solve
- Our code bugs (ThreadingMixIn — must fix regardless)
- Model ID naming conventions
- Guardrail granularity (still per-container, not per-request)

---

## 2. Architecture Comparison

### 2.1 Current AgentCore Architecture

```
Employee → IM Platform / Portal
   │
   v
EC2 Instance (Gateway + H2 Proxy)
   │
   ├── OpenClaw Gateway (:18789) — IM connections, session management
   ├── H2 Proxy (:8099) — webhook receiver, auth
   ├── Tenant Router (:8090) — route to correct runtime
   │       │
   │       ├── SSM: /openclaw/{stack}/tenants/{emp}/always-on-agent?
   │       │       ├── YES → route to ECS Fargate task (existing, partial)
   │       │       └── NO → AgentCore Runtime
   │       │
   │       └── DynamoDB CONFIG#routing → 3-tier chain
   │               Employee override → Position rule → Default
   │
   └── Admin Console (:3001) — FastAPI backend
           │
           v
AgentCore Runtime (Firecracker microVM per session)
   ├── entrypoint.sh → S3 sync → workspace assembly → Gateway start
   ├── server.py (:8080) → /invocations → openclaw agent CLI
   └── OpenClaw Gateway (:18789) — per-session, destroyed on idle
           │
           v
Amazon Bedrock (model inference)
```

**Key pain points:**
- MicroVM destroyed after idle → cold start every time
- Gateway must restart in each microVM → 30s tool unavailability
- Session Storage opaque → 3-way sync complexity
- Runtime update → all sessions killed

### 2.2 Fargate-First Architecture

```
Employee → IM Platform / Portal
   │
   v
EC2 Instance (Gateway + H2 Proxy)
   │
   ├── OpenClaw Gateway (:18789) — IM connections, session management
   ├── H2 Proxy (:8099) — webhook receiver, auth
   ├── Tenant Router (:8090) — route by deploy mode
   │       │
   │       ├── DynamoDB: POS#{pos}.deployMode = "fargate"?
   │       │       ├── YES → SSM endpoint → Fargate task IP
   │       │       └── NO → AgentCore Runtime (serverless)
   │       │
   │       └── DynamoDB CONFIG#routing → 3-tier chain
   │
   └── Admin Console (:3001) — FastAPI backend
           │
           v
ECS Fargate Service (per tier, always running)
   ├── entrypoint.sh → EFS workspace → pre-assembled SOUL
   ├── server.py (:8080) → /invocations → openclaw agent CLI
   └── OpenClaw Gateway (:18789) — always running, tools always ready
   │       │
   │       └── EFS (/mnt/efs/{emp_id}/workspace/) — durable, unlimited
   │
   v
Amazon Bedrock (model inference)
```

**Key improvements:**
- Container always running → 0ms cold start
- Gateway always ready → tools available on first message
- EFS persistent storage → no Session Storage, no 3-way sync
- ECS Service → rolling update, auto-restart on crash

### 2.3 Comparison Table

| Dimension | AgentCore (Serverless) | Fargate (Always-On) | Winner |
|-----------|----------------------|---------------------|--------|
| Cold start | 25-35s | 0ms | **Fargate** |
| Tool availability | After 30s (Gateway startup) | Immediate | **Fargate** |
| Storage | 100MB (Session Storage) | Unlimited (EFS) | **Fargate** |
| Storage model | 3-way (local+SessionStorage+S3) | Single (EFS) | **Fargate** |
| Config update | Mass session eviction | Rolling update (0 downtime) | **Fargate** |
| Log access | CloudWatch only | ECS task logs API | **Fargate** |
| Auto-restart | No | Yes (ECS Service) | **Fargate** |
| Cost (idle) | $0 | ~$15-30/month per service | AgentCore |
| Cost (active) | ~$0.001/invocation | Flat rate | Depends on usage |
| Scale to zero | Yes | No (desiredCount=0 stops) | AgentCore |
| Isolation | Firecracker microVM | Container (Docker) | AgentCore |
| Max scale | Auto (AgentCore managed) | Manual (adjust desiredCount) | AgentCore |
| IM direct connection | No (microVM destroyed) | Yes (persistent) | **Fargate** |
| Scheduled tasks | No (no cron) | Yes (HEARTBEAT) | **Fargate** |

---

## 3. Fargate Detailed Design

### 3.1 ECS Service Architecture — One Service per Security Tier

Current: 4 AgentCore Runtimes (Standard, Restricted, Engineering, Executive) with different IAM roles + guardrails.

Fargate equivalent: 4 ECS Services in the same cluster, each with a dedicated Task Definition carrying tier-specific configuration.

```
ECS Cluster: {stack}-always-on
├── Service: {stack}-tier-standard
│   ├── Task Definition: {stack}-tier-standard (BEDROCK_MODEL_ID=nova, GUARDRAIL_ID=moderate)
│   ├── desiredCount: 1 (or 0 when no always-on agents in this tier)
│   └── Positions: AE, CSM, HR, PM
│
├── Service: {stack}-tier-restricted
│   ├── Task Definition: {stack}-tier-restricted (BEDROCK_MODEL_ID=deepseek, GUARDRAIL_ID=strict)
│   ├── desiredCount: 1
│   └── Positions: FA, Legal
│
├── Service: {stack}-tier-engineering
│   ├── Task Definition: {stack}-tier-engineering (BEDROCK_MODEL_ID=sonnet-4.5, no guardrail)
│   ├── desiredCount: 1
│   └── Positions: SDE, DevOps, QA
│
└── Service: {stack}-tier-executive
    ├── Task Definition: {stack}-tier-executive (BEDROCK_MODEL_ID=sonnet-4.6, no guardrail)
    ├── desiredCount: 1
    └── Positions: Exec, SA
```

**Difference from current always-on model:** The existing `admin_always_on.py` creates one ECS Service per _agent_ (agent-helpdesk, agent-exec-dickson). The Fargate-first model creates one ECS Service per _security tier_. Within each service, multiple employees share the same container, with per-employee EFS workspace isolation.

**Migration path:** admin_always_on.py's per-agent services remain for dedicated bot use cases (customer service center). The tier-based services are the default for all employees.

### 3.2 Storage Design

```
EFS FileSystem: {stack}-always-on-workspace
├── /mnt/efs/
│   ├── emp-carol/
│   │   └── workspace/
│   │       ├── SOUL.md (assembled)
│   │       ├── PERSONAL_SOUL.md
│   │       ├── MEMORY.md
│   │       ├── memory/
│   │       │   ├── 2026-04-13.md
│   │       │   └── 2026-04-14.md
│   │       ├── output/
│   │       │   └── Q2-budget.xlsx
│   │       └── skills/ (loaded by skill_loader)
│   ├── emp-ryan/
│   │   └── workspace/ (same structure)
│   └── _shared/
│       └── memory/ (shared agent memory, e.g., helpdesk)
```

**EFS vs S3 sync:**
- EFS is the primary persistent store (writes are durable immediately via NFS)
- S3 is the backup / cross-mode handoff store
- On SIGTERM, `entrypoint.sh:370-378` does EFS→S3 snapshot (memory + MEMORY.md only)
- On container start, `entrypoint.sh:59-64` bootstraps from S3 if EFS dir is empty (first start)
- No watchdog sync needed — `entrypoint.sh:297-300` skips S3 sync loop in EFS mode

**Output files:**
- Written directly to EFS `/mnt/efs/{emp}/workspace/output/`
- Portal reads output files via Admin Console API → EFS mount on EC2 (or S3 fallback)
- No 100MB budget needed — EFS is effectively unlimited (~$0.30/GB/month)

### 3.3 Network Design

```
VPC
├── Public Subnet
│   ├── EC2 Instance (Gateway + Admin Console)
│   │   └── SG: OpenClawSecurityGroup (443 inbound, all outbound)
│   │
│   ├── ECS Fargate Tasks (per tier)
│   │   └── SG: AlwaysOnTaskSecurityGroup
│   │       ├── Inbound: 8080 from OpenClawSecurityGroup only
│   │       └── Outbound: all (Bedrock, S3, DynamoDB, SSM, EFS)
│   │
│   └── EFS Mount Target
│       └── SG: AlwaysOnEFSSecurityGroup
│           ├── Inbound: 2049 from OpenClawSecurityGroup
│           └── Inbound: 2049 from AlwaysOnTaskSecurityGroup
│
└── (Optional) VPC Endpoints for Bedrock, S3, DynamoDB
```

**Routing: EC2 → Fargate Task**
- Tenant Router on EC2 reads SSM `/openclaw/{stack}/always-on/{agent_id}/endpoint` → `http://{task_ip}:8080`
- ECS Fargate tasks in `awsvpc` mode get their own ENI with a private IP
- No ALB needed for internal routing — direct task IP works within VPC
- For future: ALB with target group per service for health checks and load balancing

### 3.4 4-Layer Security in Fargate

| Layer | AgentCore Implementation | Fargate Implementation | Difference |
|-------|------------------------|----------------------|------------|
| **L1 SOUL** | workspace_assembler.py merges 3-layer SOUL | Same — identical code | None |
| **L2 Plan A** | Tool whitelist injected in SOUL.md context block | Same — permissions.py reads DynamoDB | None |
| **L3 IAM** | AgentCore execution role per runtime (4 roles) | **ECS Task Role per tier (4 task definitions)** | Task Role replaces execution role |
| **L4 Network** | Firecracker microVM isolation (strongest) | VPC security group per service | Weaker isolation (shared container) |
| **L5 Guardrail** | GUARDRAIL_ID env var → server.py apply_guardrail() | Same — identical code | None |

**L3 IAM per tier:**
Each tier's Task Definition references a different IAM Task Role:

| Tier | Task Role | Permissions |
|------|-----------|------------|
| Standard | `{stack}-ecs-role-standard` | Bedrock InvokeModel, S3 read/write, DynamoDB read/write, SSM read |
| Restricted | `{stack}-ecs-role-restricted` | Bedrock InvokeModel, S3 read only, DynamoDB read only |
| Engineering | `{stack}-ecs-role-engineering` | Bedrock InvokeModel, S3 full, DynamoDB full (incl. delete), SSM full |
| Executive | `{stack}-ecs-role-executive` | Bedrock full, S3 full, DynamoDB full, SSM full, ECR read |

**L4 Network isolation:**
- Each tier gets its own security group (optional, can share AlwaysOnTaskSecurityGroup initially)
- Restricted tier: SG allows only port 8080 inbound from EC2, outbound only to Bedrock + DynamoDB + S3 (no internet)
- Engineering/Executive: full outbound access

**L4 weakness vs AgentCore:** Fargate containers share the same kernel (vs Firecracker microVM isolation). For enterprise multi-tenant, this is acceptable because all tenants belong to the same organization. For multi-customer hosting, AgentCore's Firecracker isolation is stronger.

### 3.5 Routing Design

**Tenant Router already supports always-on routing** (`tenant_router.py:387-425, 507-510`).

Current flow:
```python
# tenant_router.py:507-510
always_on_url = _get_always_on_endpoint(user_id, channel)
if always_on_url:
    result = _invoke_local_container(always_on_url, tenant_id, message, payload.get("model"))
else:
    result = invoke_agent_runtime(...)
```

For Fargate-first, the change is minimal: instead of checking SSM per-employee always-on assignment, we check the position's deploy mode:

```python
# Proposed change (tenant_router.py)
def _get_deploy_mode_for_position(pos_id: str) -> str:
    """Read position deploy mode from DynamoDB POS#.deployMode"""
    # Returns "fargate" or "serverless" (default)
    ...

# In _handle_route():
if resolved_emp_id:
    pos_id = _get_position_for_emp(resolved_emp_id)
    if _get_deploy_mode_for_position(pos_id) == "fargate":
        tier = _get_tier_for_position(pos_id)  # standard/restricted/engineering/executive
        endpoint = _get_tier_fargate_endpoint(tier)
        if endpoint:
            result = _invoke_local_container(endpoint, tenant_id, message, ...)
        else:
            # Fargate service not running, fall back to AgentCore
            result = invoke_agent_runtime(...)
    else:
        result = invoke_agent_runtime(...)
```

**SSM parameters for tier endpoints:**
```
/openclaw/{stack}/fargate/tier-standard/endpoint = http://{task_ip}:8080
/openclaw/{stack}/fargate/tier-restricted/endpoint = http://{task_ip}:8080
/openclaw/{stack}/fargate/tier-engineering/endpoint = http://{task_ip}:8080
/openclaw/{stack}/fargate/tier-executive/endpoint = http://{task_ip}:8080
```

Each ECS task self-registers its IP in SSM at startup (existing code in `entrypoint.sh:400-445`, using SHARED_AGENT_ID).

### 3.6 Guardrail Implementation

Identical to AgentCore. No changes needed.

- `server.py:66-67` reads `GUARDRAIL_ID` and `GUARDRAIL_VERSION` from environment
- `server.py:851-897` applies guardrail via `bedrock-runtime:ApplyGuardrail` on INPUT/OUTPUT
- Each tier's Task Definition sets the appropriate GUARDRAIL_ID:
  - Standard: `GUARDRAIL_ID=ztr7izsru5qe` (moderate)
  - Restricted: `GUARDRAIL_ID=elk5damd3rvk` (strict)
  - Engineering/Executive: `GUARDRAIL_ID=` (empty, no guardrail)

---

## 4. Cost Model

### Per-Service Monthly Cost (Fargate ARM64)

| Resource | Standard (0.5 vCPU / 1 GB) | Restricted (0.25 vCPU / 0.5 GB) | Engineering (0.5 vCPU / 1 GB) | Executive (1 vCPU / 2 GB) |
|----------|---------------------------|--------------------------------|------------------------------|--------------------------|
| Fargate compute | $14.84/mo | $7.42/mo | $14.84/mo | $29.67/mo |
| EFS storage (est. 5 GB) | $1.50/mo | $1.50/mo | $1.50/mo | $1.50/mo |
| **Total per service** | **$16.34/mo** | **$8.92/mo** | **$16.34/mo** | **$31.17/mo** |

*Fargate ARM64 pricing: vCPU $0.03238/hr, Memory $0.00356/hr/GB (us-east-2)*

### Comparison by Scale

| Scale | AgentCore Only | Fargate Only (4 tiers) | Hybrid (Fargate VIP + AgentCore rest) |
|-------|---------------|----------------------|--------------------------------------|
| **30 employees** | ~$5/mo (pay-per-use) | $72.77/mo (4 services) | $55/mo (2 Fargate tiers + AgentCore) |
| **100 employees** | ~$15/mo | $72.77/mo (same) | $55/mo (same infra, more AgentCore use) |
| **500 employees** | ~$75/mo | $145/mo (8 services for HA) | $90/mo (4 Fargate + AgentCore) |

**Key insight:** Fargate cost is fixed per tier, not per employee. Whether 1 or 100 employees use the Standard tier, it's one ECS Service at $16/mo. AgentCore cost scales linearly with usage.

**Break-even:** For a tier with employees sending >500 messages/day, Fargate is cheaper due to zero cold-start overhead (no wasted Firecracker provisioning cycles).

### Recommended Configuration

| Deployment | Tiers Running | Monthly Cost | Use Case |
|-----------|---------------|-------------|----------|
| **Demo** | 1 (Executive only) | ~$31/mo | Quick demos without cold start |
| **Small team (30)** | 2 (Standard + Engineering) | ~$33/mo | Most employees on AgentCore, VIP on Fargate |
| **Enterprise (100+)** | 4 (all tiers) | ~$73/mo | Full Fargate, AgentCore as overflow |

---

## 5. Hybrid Mode Design

The platform supports both deployment modes simultaneously. Admin switches per position.

### Admin Flow

```
Admin Console → Security Center → Positions tab
  └── Position "Finance Analyst"
      ├── Runtime Assignment: Restricted Runtime
      ├── Deploy Mode: ○ Serverless (AgentCore)  ● Always-On (Fargate)  ← NEW toggle
      └── [Save]
```

**What happens on switch to Fargate:**
1. DynamoDB `POS#{pos_id}` updated: `deployMode: "fargate"`
2. `bump_config_version()` → all agents in this position will re-assemble on next cold start
3. Tenant Router picks up new deployMode on next request (DynamoDB read, cached 5 min)
4. Next message from Finance Analyst routes to Fargate tier-restricted service
5. First request triggers workspace assembly on EFS (lazy, same as AgentCore)

**What happens on switch back to Serverless:**
1. DynamoDB `POS#{pos_id}` updated: `deployMode: "serverless"`
2. Tenant Router falls back to AgentCore on next request
3. Employee's memory is preserved: EFS→S3 snapshot happened on last Fargate SIGTERM
4. AgentCore session starts with S3 sync → workspace restored from Fargate's last state

### Cross-Mode Memory Handoff

```
Fargate running → EFS has latest memory
  │
  v
Admin switches to Serverless → Fargate SIGTERM
  │
  v
entrypoint.sh cleanup():
  EFS → S3 snapshot (memory/ + MEMORY.md + HEARTBEAT.md)
  │
  v
AgentCore cold start → S3 sync → memory restored
  │
  v
Employee continues conversation with full context
```

This handoff is already implemented:
- `entrypoint.sh:370-378` — EFS→S3 cross-mode snapshot on SIGTERM
- `entrypoint.sh:59-64` — S3 bootstrap when EFS is empty (first Fargate start)

---

## 6. Migration Path

### Phase 1: Per-Tier Fargate Services (this session)

1. **deploy.sh Step 4.5:** After creating AgentCore Runtimes, also create 4 ECS Services (one per tier)
   - Each service uses a tier-specific Task Definition with appropriate env vars
   - `desiredCount=0` initially — admin enables via console

2. **Tenant Router:** Add `deployMode` check in routing logic
   - Read `POS#.deployMode` from DynamoDB
   - Route "fargate" positions to tier endpoint, "serverless" to AgentCore

3. **Admin Console:** Add "Deploy Mode" toggle in Security Center → Positions

4. **Test:** Switch one position to Fargate, verify routing + SOUL + memory

### Phase 2: Default Fargate for VIP (future)

5. Default new deployments to Fargate for Executive + Engineering tiers
6. Add Fargate monitoring to Monitor page (ECS task status, uptime, memory usage)
7. Add Fargate logs viewer to Admin Console

### Phase 3: Full Fargate (future)

8. All positions default to Fargate
9. AgentCore retained only as overflow / burst capacity
10. Add auto-scaling: desiredCount based on active employee count per tier

---

## 7. deploy.sh Modification Plan

### Current Step 4: AgentCore Runtime

```bash
# deploy.sh line 295-338
# Creates 1 AgentCore Runtime (Standard)
# Production manually creates 3 more (Restricted, Engineering, Executive)
```

### New Step 4.5: Fargate Tier Services

```bash
# ── Step 4.5: Fargate Tier Services ────────────────────────────────
info "[4.5/8] Creating Fargate tier services..."

# Read ECS config from CloudFormation outputs (already in SSM)
ECS_CLUSTER="${STACK_NAME}-always-on"
TASK_DEF="${STACK_NAME}-always-on-agent"
SUBNET_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='PublicSubnetId'].OutputValue" \
  --output text --region "$REGION")
TASK_SG=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AlwaysOnTaskSecurityGroupId'].OutputValue" \
  --output text --region "$REGION")

# Store in SSM for admin_always_on.py
aws ssm put-parameter --name "/openclaw/${STACK_NAME}/ecs/cluster-name" \
  --value "$ECS_CLUSTER" --type String --overwrite --region "$REGION"
aws ssm put-parameter --name "/openclaw/${STACK_NAME}/ecs/subnet-id" \
  --value "$SUBNET_ID" --type String --overwrite --region "$REGION"
aws ssm put-parameter --name "/openclaw/${STACK_NAME}/ecs/task-sg-id" \
  --value "$TASK_SG" --type String --overwrite --region "$REGION"

# Tier definitions: model, guardrail, desiredCount
TIERS=(
  "standard:${MODEL}:${GUARDRAIL_MODERATE_ID:-}:0"
  "restricted:us.deepseek.r1-v1:0:${GUARDRAIL_STRICT_ID:-}:0"
  "engineering:global.anthropic.claude-sonnet-4-5-20250929-v1:0::0"
  "executive:global.anthropic.claude-sonnet-4-6::0"
)

for tier_config in "${TIERS[@]}"; do
  IFS=: read -r TIER_NAME TIER_MODEL TIER_GUARDRAIL TIER_COUNT <<< "$tier_config"
  SERVICE_NAME="${STACK_NAME}-tier-${TIER_NAME}"

  # Register tier-specific task definition
  # ... (register_task_definition with tier env vars)

  # Create or update ECS service
  # ... (create_service or update_service)

  success "  Fargate tier: $TIER_NAME (model=$TIER_MODEL, guardrail=${TIER_GUARDRAIL:-none})"
done
```

### Key: deploy.sh creates services with desiredCount=0

Services exist but are not running. Admin enables per-position via Security Center.

This means:
- Zero cost until admin activates a tier
- Infrastructure is ready for instant activation
- No breaking change to existing deployments

---

## 8. Tenant Router Modification Plan

### Current routing (tenant_router.py:507-510)

```python
always_on_url = _get_always_on_endpoint(user_id, channel)
if always_on_url:
    result = _invoke_local_container(...)
else:
    result = invoke_agent_runtime(...)
```

### New routing (3-level cascade)

```python
# Level 1: Per-employee always-on assignment (existing — dedicated agents)
always_on_url = _get_always_on_endpoint(resolved_emp_id, channel)
if always_on_url:
    result = _invoke_local_container(always_on_url, ...)

# Level 2: Position-level Fargate tier (NEW — default always-on)
elif _is_fargate_position(pos_id):
    tier = _get_tier_for_position(pos_id)
    endpoint = _get_fargate_tier_endpoint(tier)
    if endpoint:
        result = _invoke_local_container(endpoint, ...)
    else:
        # Fargate not running, fall back to AgentCore
        result = invoke_agent_runtime(...)

# Level 3: AgentCore serverless (existing — default)
else:
    result = invoke_agent_runtime(...)
```

**Cache strategy:**
- Position deploy mode: cached in `_routing_config` (5 min TTL, already loaded from DynamoDB)
- Tier endpoint: cached in `_fargate_tier_cache` (60s TTL, reads SSM)

---

## 9. What Already Exists vs What Needs Building

### Already Implemented (reuse as-is)

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| ECS Cluster + Task Def | CFn template | 976-1222 | Deployed |
| EFS + Mount Target | CFn template | 1016-1035 | Deployed |
| IAM roles | CFn template | 1057-1131 | Deployed |
| Security groups | CFn template | 995-1055 | Deployed |
| EFS mode workspace | entrypoint.sh | 50-65 | Working |
| S3 bootstrap (empty EFS) | entrypoint.sh | 59-64 | Working |
| Pre-Gateway assembly | entrypoint.sh | 113-128 | Working |
| Bot token injection | entrypoint.sh | 136-162 | Working |
| SSM endpoint registration | entrypoint.sh | 400-445 | Working |
| SIGTERM cleanup + EFS→S3 | entrypoint.sh | 332-397 | Working |
| Tenant Router always-on check | tenant_router.py | 387-425, 507-510 | Working |
| _invoke_local_container | tenant_router.py | 273-310 | Working |
| Admin start/stop/reload | admin_always_on.py | Full file | Working |
| Per-agent ECS Service | admin_always_on.py | 107-236 | Working |
| ECS Service force-new-deployment | admin_always_on.py | 340-417 | Working |

### Needs Building (this session)

| Component | Description | Effort |
|-----------|-------------|--------|
| deploy.sh Step 4.5 | Create 4 tier-specific ECS services | Medium |
| Per-tier Task Definitions | Register task defs with tier-specific env vars | Medium |
| Tenant Router position deploy mode | Read POS#.deployMode, route to tier endpoint | Small |
| Fargate tier endpoint SSM registration | entrypoint.sh: register tier endpoint (not just agent-specific) | Small |
| Admin Console deploy mode toggle | Security Center position settings | Small |
| E2E test | Verify full path: Portal → Router → Fargate → Agent → Response | Medium |
