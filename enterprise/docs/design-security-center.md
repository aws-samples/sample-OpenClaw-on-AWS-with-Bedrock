# Design: Security Center — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-security-center.md, security.py (555 lines), permissions.py (208 lines), SecurityCenter.tsx (1186 lines)

---

## Architecture Context

```
Security Center manages 5 defense layers across 3 tabs:

┌─── Security Policies tab ──────────────────────────┐
│  L1 SOUL Rules: Global + Position SOUL editors      │
│  L2 Tool Permissions: POS#.toolAllowlist per pos    │
└─────────────────────────────────────────────────────┘
        ↓ Position → Runtime mapping (Runtimes tab)
┌─── Agent Runtimes tab ──────────────────────────────┐
│  Runtime CRUD: Docker image, IAM role, lifecycle     │
│  Position → Runtime assignment table                 │
│  Defense in Depth visualization                      │
└─────────────────────────────────────────────────────┘
        ↓ Each Runtime carries L3/L4/L5 config
┌─── Infrastructure tab ──────────────────────────────┐
│  L3 IAM: roles list + attached policies              │
│  L4 Network: VPC/Subnet/SG read-only view           │
│  L5 Guardrails: Bedrock guardrails list              │
│  Docker images: ECR registry                         │
└─────────────────────────────────────────────────────┘

Data flow for routing:
  Employee message → Tenant Router
    → _get_runtime_id_for_tenant(emp_id)
      → Tier 1: DynamoDB CONFIG#routing.employee_override[emp_id]
      → Tier 2: DynamoDB CONFIG#routing.position_runtime[pos_id]
      → Tier 3: env var AGENTCORE_RUNTIME_ID (loaded from SSM at startup)
    → invoke_agent_runtime(runtime_arn, session_id, payload)
```

---

## File-by-File Change Design

### 1. security.py — 7 changes

#### 1a. put_position_runtime() → DynamoDB dual-write + remove employee loop

```
CURRENT (lines 123-158):
  ssm.put_parameter(Name=f"/positions/{pos_id}/runtime-id", Value=runtime_id)
  for emp in emps:
      ssm.put_parameter(Name=f"/tenants/{emp['id']}/runtime-id", Value=runtime_id)
  db.create_audit_entry(...)

CHANGE:
  # DynamoDB dual-write (Tenant Router reads this for tier 2 resolution)
  db.set_position_runtime(pos_id, runtime_id)
  # Keep SSM position-level write for backward compat
  ssm.put_parameter(Name=f"/positions/{pos_id}/runtime-id", Value=runtime_id)
  # REMOVE: per-employee SSM loop (lines 136-147)
  # Tenant Router resolves position→runtime via DynamoDB directly
  # Force refresh affected employees
  for emp in emps:
      if emp.get("positionId") == pos_id and emp.get("agentId"):
          threading.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
  db.create_audit_entry(...)  # existing audit (keep)
```

#### 1b. delete_position_runtime() → DynamoDB + SSM dual-delete

```
CURRENT (lines 161-170):
  ssm.delete_parameter(Name=f"/positions/{pos_id}/runtime-id")

CHANGE:
  db.remove_position_runtime(pos_id)  # DynamoDB
  ssm.delete_parameter(...)            # SSM cleanup (keep for compat)
```

#### 1c. get_position_runtime() → read DynamoDB, SSM fallback

```
CURRENT (lines 111-120):
  ssm.get_parameter(Name=f"/positions/{pos_id}/runtime-id")

CHANGE:
  cfg = db.get_routing_config()
  runtime_id = cfg.get("position_runtime", {}).get(pos_id)
  if not runtime_id:
      # SSM fallback for pre-migration data
      try:
          resp = ssm.get_parameter(Name=f"/positions/{pos_id}/runtime-id")
          runtime_id = resp["Parameter"]["Value"]
      except: pass
  return {"posId": pos_id, "runtimeId": runtime_id}
```

#### 1d. get_position_runtime_map() → single DynamoDB read

```
CURRENT (lines 173-190):
  SSM paginator over /openclaw/{stack}/positions/ → 11 API calls

CHANGE:
  cfg = db.get_routing_config()
  return {"map": cfg.get("position_runtime", {})}
  # One DynamoDB GetItem instead of SSM pagination
```

#### 1e. put_position_tools() → add audit + config version + force refresh

```
CURRENT (lines 89-106):
  table.update_item(POS#{pos_id}, SET toolAllowlist = :tools)
  return {"saved": True, "propagated": count}

CHANGE:
  # ... existing DynamoDB write ...

  # Audit trail
  db.create_audit_entry({
      "timestamp": now,
      "eventType": "tool_permission_change",
      "actorId": user.employee_id,
      "actorName": user.name,
      "targetType": "position",
      "targetId": pos_id,
      "detail": f"Tool allowlist changed for {pos_id}: {tools}",
      "status": "success",
  })

  # Config version bump → workspace_assembler regenerates context block with new Plan A
  bump_config_version()

  # Force refresh affected employees
  import threading
  refreshed = []
  for emp in db.get_employees():
      if emp.get("positionId") == pos_id and emp.get("agentId"):
          threading.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
          refreshed.append(emp["id"])

  return {"saved": True, "tools": tools, "refreshed": refreshed}
```

Import additions: `from shared import stop_employee_session, bump_config_version`

#### 1f. update_runtime_config() → force refresh agents in affected runtime

```
CURRENT (lines 260-313):
  ac.update_agent_runtime(**kwargs)
  return {"saved": True}

CHANGE:
  ac.update_agent_runtime(**kwargs)

  # Force refresh all agents using this runtime
  import threading
  routing = db.get_routing_config()
  affected_positions = [pid for pid, rid in routing.get("position_runtime", {}).items()
                        if rid == runtime_id]
  refreshed = []
  for emp in db.get_employees():
      if emp.get("positionId") in affected_positions and emp.get("agentId"):
          threading.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
          refreshed.append(emp["id"])

  # Audit
  db.create_audit_entry({
      "timestamp": now,
      "eventType": "runtime_config_change",
      "actorId": user.employee_id,
      "actorName": user.name,
      "targetType": "runtime",
      "targetId": runtime_id,
      "detail": f"Runtime config updated. Refreshed {len(refreshed)} agents.",
      "status": "success",
  })

  return {"saved": True, "runtimeId": runtime_id, "refreshed": refreshed}
```

Same pattern for `update_runtime_lifecycle()`.

#### 1g. put_position_runtime audit → use actual user context

```
CURRENT (lines 148-157):
  "actorId": "admin", "actorName": "Admin"  # hardcoded

CHANGE:
  "actorId": user.employee_id, "actorName": user.name  # from require_role() return
```

(The `user` variable is already available from `require_role()` at line 126.)

### 2. permissions.py — permission denied → DynamoDB AUDIT#

#### 2a. _log_permission_denied() → add DynamoDB write

```
CURRENT (lines 92-100):
  logger.warning("AUDIT %s", json.dumps({...}))  # CloudWatch only

CHANGE:
  logger.warning("AUDIT %s", json.dumps({...}))  # Keep CloudWatch

  # Write to DynamoDB for Audit Center visibility
  try:
      import time as _time_perm
      ddb = boto3.resource("dynamodb", region_name=DYNAMODB_REGION)
      table = ddb.Table(DYNAMODB_TABLE)
      ts = datetime.now(timezone.utc).isoformat()
      table.put_item(Item={
          "PK": "ORG#acme",
          "SK": f"AUDIT#perm-{int(_time_perm.time()*1000)}",
          "GSI1PK": "TYPE#audit",
          "GSI1SK": f"AUDIT#perm-{int(_time_perm.time()*1000)}",
          "eventType": "permission_denied",
          "actorId": _base_tenant_id(tenant_id),
          "actorName": _base_tenant_id(tenant_id),
          "targetType": "tool",
          "targetId": tool_name,
          "detail": f"Tool '{tool_name}' denied for {_base_tenant_id(tenant_id)}"
                    + (f" (resource: {resource})" if resource else ""),
          "status": "blocked",
          "timestamp": ts,
      })
  except Exception:
      pass  # non-fatal — CloudWatch log is primary
```

Note: This runs inside the agent container (Firecracker VM), so it needs DynamoDB write permission in the AgentCore execution role. The role already has `dynamodb:PutItem` for USAGE# and SESSION# writes — same table, so no IAM change needed.

### 3. SecurityCenter.tsx — 4 frontend changes

#### 3a. Dynamic AWS region in console URLs

```
ADD to SecurityCenter component:
  const [awsRegion, setAwsRegion] = useState('us-east-1');
  useEffect(() => {
    fetch('/api/v1/settings/services', {
      headers: { Authorization: `Bearer ${localStorage.getItem('openclaw_token')}` }
    }).then(r => r.json()).then(d => {
      if (d.region) setAwsRegion(d.region);
    }).catch(() => {});
  }, []);

REPLACE all instances of:
  region=us-east-1
WITH:
  region=${awsRegion}

Affected locations (6):
  Line 221: VPC SG console link (RuntimeEditModal)
  Line 242: VPC subnet console link (RuntimeEditModal)
  Line 1025: ECR console link
  Line 1044: ECR repo link
  Line 1118: VPC SG console link (Infrastructure)
  Line 1119: VPC console link (Infrastructure)
  Line 1157: VPC SG detail link (Infrastructure)
```

#### 3b. Runtime card → show Guardrail + associated positions

```
MODIFY RuntimeCard component:
  Current: shows model, region, idle timeout, max lifetime
  ADD:
    - Guardrail name (cross-reference guardrailId with guardrails list)
    - Network mode badge (PUBLIC / VPC)
    - Associated positions list (from positionRuntimeMap)

Example card footer:
  Guardrail: content-policy-v2 | Network: VPC | Positions: SA, SDE, DevOps
```

#### 3c. Runtime card → show associated positions count

```
In RuntimeCard, add:
  const associatedPositions = Object.entries(runtimeMap)
    .filter(([_, rid]) => rid === rt.id)
    .map(([pid]) => positions.find(p => p.id === pid)?.name || pid);

Display: <Badge>{associatedPositions.length} positions</Badge>
If expanded: list position names
```

#### 3d. Guardrail block summary in Runtimes tab

```
ADD below Defense in Depth card:
  <Card>
    <h3>Recent Guardrail Blocks (24h)</h3>
    // Fetch from /api/v1/audit/guardrail-events?limit=5
    // Show: timestamp, employee, policy, source (INPUT/OUTPUT)
    // Link to Audit Center for full view
  </Card>
```

### 4. settings.py — verify region is returned

```
CHECK: /api/v1/settings/services endpoint returns { region: "us-west-2" }
If not, ADD region to response:
  return { ..., "region": os.environ.get("AWS_REGION", GATEWAY_REGION) }
```

### 5. ui-guide.html — documentation updates

#### 5a. 4 layers → 5 layers

```
Security Center section:
  CHANGE: "4 independent security layers" → "5 independent security layers"
  CHANGE: "4 层独立安全防护" → "5 层独立安全防护"

  UPDATE layer descriptions to match frontend:
    L1 SOUL Rules (soft, prompt-level)
    L2 Tool Permissions (soft, application-level)
    L3 IAM Role (hard, infrastructure)
    L4 Network/VPC (hard, infrastructure)
    L5 Bedrock Guardrail (hard, AWS-managed)
```

#### 5b. Plan A code reference update

```
CHANGE: "server.py line 420-438" → "workspace_assembler.py _build_context_block()"
CHANGE: "server.py prepends constraint block to SOUL.md"
     → "workspace_assembler.py includes Plan A in the context block during assembly"
```

#### 5c. Update Permission Resolution Chain

```
The deep() section still says:
  "read_permission_profile(tenant_id) → ... → POS#{pos}.toolAllowlist"
This is still correct (permissions.py unchanged). But add note:
  "Plan A injection now happens in workspace_assembler.py, not server.py"
```

---

## Unit Test Plan

```
test_security_center.py:

1. test_put_position_runtime_writes_dynamodb:
   Scan put_position_runtime for "db.set_position_runtime" → must exist

2. test_put_position_runtime_no_employee_ssm_loop:
   Scan put_position_runtime for per-employee SSM write pattern → must NOT exist
   (Check for "tenants/{emp" SSM write inside the function)

3. test_get_position_runtime_map_uses_dynamodb:
   Scan get_position_runtime_map for "db.get_routing_config" → must exist
   Scan for "get_paginator" → must NOT exist

4. test_put_tools_has_audit:
   Scan put_position_tools for "create_audit_entry" → must exist

5. test_put_tools_has_force_refresh:
   Scan put_position_tools for "stop_employee_session" → must exist

6. test_put_tools_bumps_config_version:
   Scan put_position_tools for "bump_config_version" → must exist

7. test_update_runtime_config_has_force_refresh:
   Scan update_runtime_config for "stop_employee_session" → must exist

8. test_permission_denied_writes_dynamodb:
   Scan permissions.py _log_permission_denied for "put_item" → must exist

9. test_no_hardcoded_region_in_frontend:
   Scan SecurityCenter.tsx for "region=us-east-1" → must NOT exist

10. test_runtime_audit_uses_user_context:
    Scan put_position_runtime for "user.employee_id" or "user.name" → must exist
    Scan for '"actorId": "admin"' → must NOT exist
```

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| DynamoDB dual-write + SSM write = 2 writes per runtime assign | Low | SSM write is backward compat, can remove later |
| force refresh on tool change: 50 employees = 50 stop_session calls | Low | Background threads, admin API returns immediately |
| permissions.py DynamoDB write adds latency to permission checks | Medium | Fire-and-forget try/except, non-fatal. CloudWatch log is primary. |
| Frontend region fetch adds one API call on page load | Low | Cached in state, called once per mount |
| Removing per-employee SSM propagation breaks SSM-only deployments | Low | Position-level SSM retained. DynamoDB is the primary path since migration commit 2332328. |

---

## Migration Notes

- No new DynamoDB tables or indexes
- db.py already has set_position_runtime(), remove_position_runtime(), get_routing_config()
- permissions.py DynamoDB write uses existing table (ORG#acme, AUDIT# prefix)
- AgentCore execution role already has dynamodb:PutItem on the table
- Frontend: requires npm run build + deploy
- Agent container: requires Docker rebuild for permissions.py change
