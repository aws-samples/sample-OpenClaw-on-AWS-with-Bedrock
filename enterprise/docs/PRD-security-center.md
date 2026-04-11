# PRD: Security Center Module

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Priority:** P0 — Core platform security module

---

## 1. Design Intent

Security Center implements a **5-layer defense-in-depth** model for AI agent security:

```
                    Soft constraints (position/dept level)
┌─────────────────────────────────────────────────────────────┐
│  L1 SOUL Rules        — behavioral instructions in SOUL.md  │ Security Policies tab
│  L2 Tool Permissions  — Plan A allowlist per position        │
├─────────────────────────────────────────────────────────────┤
│  L3 IAM Role          — AWS permissions, cannot be bypassed  │
│  L4 Network           — VPC isolation between Runtimes       │ Infrastructure tab
│  L5 Bedrock Guardrail — content filtering on I/O             │
└─────────────────────────────────────────────────────────────┘
                    Hard constraints (runtime/AWS level)
```

**Core architecture:** Different Runtimes can have different Docker images, IAM roles, guardrails, and network configs. Positions map to Runtimes. This means:
- Engineering positions → Standard Runtime (limited IAM, VPC isolated)
- Executive positions → Exec Runtime (broader IAM, different guardrail)
- Finance positions → Finance Runtime (SAP connector IAM, strict guardrail)

**Three tabs:**
- **Agent Runtimes** — Create/manage runtimes, map positions → runtimes
- **Security Policies** — L1 (Global/Position SOUL editor) + L2 (tool allowlist per position)
- **Infrastructure** — L3 (IAM roles), L4 (VPC/SG), L5 (Guardrails), Docker images

---

## 2. Problem Statement

Code audit of security.py (555 lines), permissions.py (208 lines), and SecurityCenter.tsx (1186 lines) found:

### 2.1 Runtime assignment writes SSM but should also write DynamoDB

Per commit `2332328` (SSM→DynamoDB migration), SSM is intentionally kept for runtime-id (default runtime, secrets, always-on endpoints). The Tenant Router has a 3-tier resolution:
1. Employee override → DynamoDB `CONFIG#routing.employee_override[emp_id]`
2. Position rule → DynamoDB `CONFIG#routing.position_runtime[pos_id]`
3. Default → SSM `/openclaw/{stack}/runtime-id` or env var `AGENTCORE_RUNTIME_ID`

`security.py:put_position_runtime()` writes SSM per-position + propagates SSM per-employee. This works for the default runtime fallback (tier 3). But for position-level override (tier 2), it should **also** write DynamoDB `CONFIG#routing` using the existing `db.set_position_runtime()`. Currently it does not — so position-level runtime overrides set from Security Center only work via SSM fallback, not the faster DynamoDB path.

**Fix:** Dual-write: SSM (for backward compat + default fallback) AND DynamoDB (for Tenant Router fast path). Eventually deprecate SSM reads.

### 2.2 Tool permission change has no audit trail and no force refresh

`put_position_tools()` (line 89-106) updates DynamoDB `POS#.toolAllowlist` but:
- No AUDIT# entry created (who changed what permissions when)
- No `stop_employee_session()` — running agents keep old permissions until idle timeout
- No `bump_config_version()` — workspace_assembler won't regenerate CONTEXT block

### 2.3 Runtime config change has no force refresh

`update_runtime_lifecycle()` and `update_runtime_config()` call AgentCore API to update runtime settings (model, guardrail, idle timeout). But running microVMs in that runtime are not affected until naturally released. No mechanism to force all agents in the affected runtime to restart.

### 2.4 AWS Console URLs hardcoded to us-east-1

SecurityCenter.tsx has multiple AWS Console links hardcoded to `us-east-1`:
- Line 221, 242: VPC console in RuntimeEditModal
- Line 1025, 1044: ECR console
- Line 1118, 1119, 1157: VPC/SG console in Infrastructure tab

Production is `us-west-2`. These links open the wrong region.

### 2.5 ui-guide says 4 layers, should be 5

The ui-guide SOUL editor section and Security Center section reference a "4-layer" defense model. The frontend correctly shows 5 layers (L1 Prompt, L2 Application, L3 IAM, L4 Network, L5 Guardrail). Documentation needs alignment.

### 2.6 ui-guide references outdated code locations

Plan A injection is no longer in `server.py line 420-438`. It's now in `workspace_assembler.py _build_context_block()`. Documentation references need updating.

### 2.7 SSM per-employee runtime propagation is wasteful

`put_position_runtime()` loops through all employees in a position and writes SSM per-employee (line 138-147). With DynamoDB `CONFIG#routing`, this SSM propagation is unnecessary. Tenant Router resolves position → runtime via DynamoDB directly.

### 2.8 Permission denied events not written to DynamoDB

`permissions.py:_log_permission_denied()` (line 92-100) only logs to CloudWatch (via logger.warning). No DynamoDB AUDIT# entry. Admin can't see permission denials in the Audit Center without CloudWatch access.

---

## 3. Solutions

### 3.1 Runtime assignment → dual-write SSM + DynamoDB

**Add DynamoDB write alongside existing SSM writes:**

```python
# security.py put_position_runtime() — add:
db.set_position_runtime(pos_id, runtime_id)  # DynamoDB CONFIG#routing (Tenant Router fast path)
# Keep existing SSM write for backward compat / default fallback
# Remove per-employee SSM loop — Tenant Router resolves position→runtime via DynamoDB directly

# security.py delete_position_runtime() — add:
db.remove_position_runtime(pos_id)  # DynamoDB
# Keep SSM delete for cleanup
```

Read path: `get_position_runtime()` → read from `db.get_routing_config()` (DynamoDB, fast). SSM read as fallback.
`get_position_runtime_map()` → read from `db.get_routing_config()` (single DynamoDB call instead of SSM paginator).

### 3.2 Tool permission change → audit + force refresh + config version bump

```python
@router.put("/api/v1/security/positions/{pos_id}/tools")
def put_position_tools(pos_id, body, authorization):
    user = require_role(authorization, roles=["admin"])
    tools = body.get("tools", [])
    # ... existing DynamoDB write ...

    # NEW: audit
    db.create_audit_entry({
        "eventType": "tool_permission_change",
        "actorId": user.employee_id,
        "targetId": pos_id,
        "detail": f"Tools changed for {pos_id}: {tools}",
    })

    # NEW: force refresh affected employees
    bump_config_version()
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            threading.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
```

### 3.3 Runtime config change → force refresh all agents in runtime

After `update_runtime_config()` or `update_runtime_lifecycle()`:
```python
# Find all positions using this runtime
routing = db.get_routing_config()
affected_positions = [pid for pid, rid in routing["position_runtime"].items() if rid == runtime_id]

# Force refresh all employees in affected positions
for emp in db.get_employees():
    if emp.get("positionId") in affected_positions and emp.get("agentId"):
        threading.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
```

### 3.4 AWS Console URLs → dynamic region

Replace all hardcoded `us-east-1` in SecurityCenter.tsx with the actual region from settings:

```tsx
// Add to SecurityCenter component
const { data: services } = useServiceStatus();  // existing hook
const awsRegion = services?.region || 'us-east-1';

// Then use: `https://console.aws.amazon.com/ecr/repositories?region=${awsRegion}`
```

Need to ensure `/api/v1/settings/services` returns the region (check settings.py).

### 3.5 ui-guide → update to 5 layers

Update the Security Center page in ui-guide.html:
- Change "4 independent security layers" to "5 independent security layers"
- Update the layer descriptions to match frontend (L1-L5)
- Update Plan A code references to workspace_assembler.py

### 3.6 Remove per-employee SSM propagation loop

Delete the per-employee SSM loop in `put_position_runtime()` (lines 138-147). Tenant Router resolves position→runtime via DynamoDB `CONFIG#routing` directly — no need to propagate per-employee SSM keys. Keep the position-level SSM write (`/positions/{pos_id}/runtime-id`) for backward compat.

### 3.7 Permission denied → DynamoDB audit

Add DynamoDB AUDIT# write in `permissions.py:_log_permission_denied()`:

```python
def _log_permission_denied(tenant_id, tool_name, resource):
    logger.warning(...)  # existing CloudWatch log
    # NEW: write to DynamoDB
    try:
        ddb = boto3.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        table.put_item(Item={
            "PK": "ORG#acme",
            "SK": f"AUDIT#perm-{int(time.time())}",
            "GSI1PK": "TYPE#audit",
            "GSI1SK": f"AUDIT#perm-{int(time.time())}",
            "eventType": "permission_denied",
            "actorId": _base_tenant_id(tenant_id),
            "targetType": "tool",
            "targetId": tool_name,
            "detail": f"Tool {tool_name} denied for {tenant_id}",
            "status": "blocked",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass  # non-fatal, CloudWatch log is the primary record
```

---

## 4. Implementation Plan

### Phase 1: Critical fix — Runtime routing (P0)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `security.py` | `put_position_runtime()` → use `db.set_position_runtime()` instead of SSM. Remove per-employee SSM loop. |
| 1.2 | `security.py` | `delete_position_runtime()` → use `db.remove_position_runtime()` instead of SSM. |
| 1.3 | `security.py` | `get_position_runtime()` → read from `db.get_routing_config()` instead of SSM. |
| 1.4 | `security.py` | `get_position_runtime_map()` → read from `db.get_routing_config()` instead of SSM paginator. |

### Phase 2: Audit + force refresh (P0)

| Task | File | Description |
|------|------|-------------|
| 2.1 | `security.py` | `put_position_tools()` → add audit + bump_config_version + stop_employee_session |
| 2.2 | `security.py` | `put_position_runtime()` → add audit + stop_employee_session for affected employees |
| 2.3 | `security.py` | `update_runtime_config()` → stop_employee_session for all agents in affected runtime |
| 2.4 | `permissions.py` | `_log_permission_denied()` → add DynamoDB AUDIT# write |

### Phase 3: Frontend + documentation (P1)

| Task | File | Description |
|------|------|-------------|
| 3.1 | `SecurityCenter.tsx` | Replace hardcoded `us-east-1` with dynamic region from settings API |
| 3.2 | `ui-guide.html` | Update "4 layers" → "5 layers" throughout Security Center section |
| 3.3 | `ui-guide.html` | Update Plan A code references (server.py → workspace_assembler.py) |

---

## 5. TODO

### Must-Do
- [ ] 1.1-1.4: Runtime routing fix (SSM → DynamoDB)
- [ ] 2.1: Tool permission audit + force refresh
- [ ] 2.2: Runtime assignment audit + force refresh
- [ ] 2.3: Runtime config change → force refresh affected agents
- [ ] 2.4: Permission denied → DynamoDB AUDIT#
- [ ] 3.1: Frontend → dynamic AWS region in console URLs
- [ ] 3.2-3.3: ui-guide → 5 layers + correct code references
- [ ] Docker image rebuild for permissions.py change (AUDIT# write)
- [ ] Test runtime assignment end-to-end: Security Center → DynamoDB → Tenant Router

### Future — Review Engine integration
- [ ] Tool permission changes feed into Review Engine (PRD-soul-review-engine.md)
- [ ] Guardrail block events visible in Security Center Review tab
- [ ] Permission denied patterns → AI anomaly detection
