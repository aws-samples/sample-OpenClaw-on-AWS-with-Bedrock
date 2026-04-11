# Design: Agent Factory — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-agent-factory.md, agents.py (578 lines), security.py, server.py

---

## File-by-File Change Design

### 1. shared.py — Unified SOUL audit function

**ADD:**

```python
def audit_soul_change(
    user, layer: str, target_id: str,
    content_len: int, action: str = "edit",
):
    """Create audit entry for any SOUL layer change.
    Called by agents.py save_agent_soul, security.py put_global_soul/put_position_soul."""
    import db
    from datetime import datetime, timezone
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "soul_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "soul",
        "targetId": target_id,
        "detail": f"SOUL {layer} layer {action}: {target_id} ({content_len} chars)",
        "status": "success",
    })
```

### 2. agents.py — 6 changes

**Change 2a: save_agent_soul() → add audit**

```
After S3 write + version increment (line ~295):
  ADD: audit_soul_change(user, body.layer, agent_id, len(body.content))
  Import: from shared import audit_soul_change
```

**Change 2b: DELETE /api/v1/agents/{agent_id} — new endpoint**

```python
@router.delete("/api/v1/agents/{agent_id}")
def delete_agent(agent_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    emp_id = agent.get("employeeId", "")

    # 1. Delete all bindings for this agent
    bindings = [b for b in db.get_bindings() if b.get("agentId") == agent_id]
    for b in bindings:
        db.delete_binding(b["id"])

    # 2. Delete agent record
    db.delete_agent(agent_id)

    # 3. Clear agentId from employee record
    if emp_id:
        emp = db.get_employee(emp_id)
        if emp:
            emp.pop("agentId", None)
            emp.pop("agentStatus", None)
            db.create_employee(emp)  # upsert

    # 4. Delete S3 workspace (best-effort)
    if emp_id:
        try:
            s3 = boto3.client("s3")
            bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"{emp_id}/workspace/", MaxKeys=200)
            for obj in resp.get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
        except Exception as e:
            print(f"[delete_agent] S3 cleanup failed (non-fatal): {e}")

    # 5. Audit
    db.create_audit_entry({...eventType: "agent_deleted"...})

    return {"deleted": True, "agentId": agent_id, "bindingsDeleted": len(bindings)}
```

Requires db.py addition:
```python
def delete_agent(agent_id: str) -> bool:
    return _delete_item(f"AGENT#{agent_id}")
```

**Change 2c: Replace CloudWatch with DynamoDB for status**

```
DELETE: _get_all_agentcore_log_groups() (lines 44-55)
DELETE: _get_active_agent_ids() (lines 58-90)

REPLACE in get_agents() and get_agent():
  Before:
    active_emp_ids = _get_active_agent_ids()  # CloudWatch, slow
    if emp_id in active_emp_ids: status = "active"

  After:
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    last = agent.get("lastInvocationAt", "")
    if last:
        try:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age = (now - ts).total_seconds()
            if age < 900:      # 15 min
                agent["status"] = "active"
            elif age < 3600:   # 60 min
                agent["status"] = "idle"
            else:
                agent["status"] = "offline"
        except: pass
```

**Change 2d: Remove skill propagation loop**

```
MODIFY: assign_skill_to_position() (lines 509-545)
  Keep: db.update_position(pos_id, {"defaultSkills": skills})  (line 530)
  REMOVE: lines 533-545 (the for emp in db.get_employees() loop)
  Return: {"assigned": True, "positionId": pos_id, "skill": skill_name}

MODIFY: unassign_skill_from_position() (lines 548-577)
  Keep: db.update_position(positionId, {"defaultSkills": skills})
  REMOVE: lines 565-577 (the for emp in db.get_employees() loop)
```

**Change 2e: skill_keys cache**

```
ADD module-level:
  _skill_keys_cache = {"data": None, "expires": 0}

MODIFY: get_all_skill_keys()
  At top of function:
    import time
    if _skill_keys_cache["data"] and time.time() < _skill_keys_cache["expires"]:
        return _skill_keys_cache["data"]
  At bottom before return:
    _skill_keys_cache["data"] = keys
    _skill_keys_cache["expires"] = time.time() + 300
    return keys
```

**Change 2f: Sanitize employee name in S3 seed**

```
MODIFY: create_agent() S3 seed section
  Before: f"- **Name**: {emp_name} AI Assistant"
  After:
    import re
    safe_name = re.sub(r'([#*_\[\]<>])', r'\\\1', emp_name)
    f"- **Name**: {safe_name} AI Assistant"
```

### 3. security.py — Add audit to SOUL save

**MODIFY: put_global_soul() (line 39)**

```
After s3ops._client().put_object(...) and bump_config_version():
  ADD:
    user = require_role(authorization, roles=["admin"])  # already called above
    audit_soul_change(user, "global", "global", len(body.get("content", "")))
```

**MODIFY: put_position_soul() (line 61)**

```
After s3ops._client().put_object(...) and bump_config_version():
  ADD:
    audit_soul_change(user, "position", pos_id, len(body.get("content", "")))
```

Import: `from shared import audit_soul_change`

### 4. server.py (agent-container) — Write lastInvocationAt

**MODIFY: _write_usage_to_dynamodb() or the invocation handler**

Find where server.py writes USAGE# after each invocation. In the same block, add:

```python
# Update agent status in DynamoDB (replaces CloudWatch-based status detection)
try:
    table.update_item(
        Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
        UpdateExpression="SET lastInvocationAt = :ts",
        ExpressionAttributeValues={":ts": datetime.now(timezone.utc).isoformat()},
    )
except Exception:
    pass  # non-fatal
```

This requires resolving agent_id from emp_id. server.py already reads EMP# to get positionId — agent_id = emp.get("agentId").

### 5. db.py — Add delete_agent

**ADD:**

```python
def delete_agent(agent_id: str) -> bool:
    return _delete_item(f"AGENT#{agent_id}")
```

### 6. save_agent_soul() — Version conflict detection

**MODIFY: save_agent_soul() in agents.py**

```python
@router.put("/api/v1/agents/{agent_id}/soul")
def save_agent_soul(agent_id, body, authorization):
    ...
    # Check for concurrent edit conflict
    current_version = agent.get("soulVersions", {}).get(body.layer, 0)
    if body.expectedVersion is not None and body.expectedVersion != current_version:
        raise HTTPException(409, "SOUL was modified by another session. Reload to see latest.")
    ...
```

Update SoulSaveRequest:
```python
class SoulSaveRequest(BaseModel):
    layer: str
    content: str
    expectedVersion: int | None = None  # optional, for conflict detection
```

---

## Unit Test Plan

```
test_agent_factory.py:

1. test_delete_agent_cascade:
   Create agent + binding → delete → verify both gone from DynamoDB

2. test_soul_audit_on_save:
   Save SOUL → verify AUDIT# entry created with eventType="soul_change"

3. test_no_cloudwatch_in_agents:
   Scan agents.py for "filter_log_events" → should not exist

4. test_agent_status_from_dynamodb:
   Set AGENT#.lastInvocationAt to 5min ago → status = "active"
   Set to 30min ago → "idle"
   Set to 2h ago → "offline"

5. test_skill_assign_no_employee_loop:
   Scan assign_skill_to_position for "get_employees" → should not exist

6. test_skill_keys_cache:
   Call get_all_skill_keys twice → second call should not read S3

7. test_employee_name_sanitized:
   Create agent with name "# Evil <script>" → verify IDENTITY.md has escaped chars

8. test_soul_save_conflict:
   Save with expectedVersion=1 when current is 2 → expect 409
```

---

## Migration Notes

- `AGENT#.lastInvocationAt` — new field. Old agents won't have it → status defaults to "offline" until first invocation. Acceptable.
- `delete_agent` — new endpoint, no migration needed.
- `AGENT#.skills` — still written by create_agent seed, but no longer propagated on skill assign. Frontend may show stale count until agent is recreated. Low impact.
- Docker image rebuild required for server.py lastInvocationAt change.
