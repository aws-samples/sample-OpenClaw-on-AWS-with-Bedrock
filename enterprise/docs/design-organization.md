# Design: Organization Management — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-organization.md

---

## File-by-File Change Design

### 1. org.py — 6 changes

**Change 1.1: delete_employee audit trail**

```
MODIFY: delete_employee() — after db.delete_employee(emp_id), ADD:

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "employee_deleted",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "employee",
        "targetId": emp_id,
        "detail": f"Deleted employee {emp_id} (bindings: {len(bindings)}, mappings: {len(im_mappings)})",
        "status": "success",
    })
```

**Change 1.2: delete_department position check**

```
MODIFY: delete_department() — after sub-department check, ADD:

    positions = db.get_positions()
    dept_positions = [p for p in positions if p.get("departmentId") == dept_id]
    if dept_positions:
        raise HTTPException(409, {
            "error": "department_has_positions",
            "count": len(dept_positions),
            "names": [p["name"] for p in dept_positions[:5]],
            "message": f"{len(dept_positions)} position(s) belong to this department. Reassign them first.",
        })
```

**Change 1.3: remove shared agent binding from _auto_provision_employee**

```
DELETE lines 335-350:
    agents = db.get_agents()
    shared_agents = [a for a in agents if ...]
    shared_bindings = []
    for sa in shared_agents: ...

MODIFY provision_employee_atomic call:
    Remove shared_bindings= parameter
```

**Change 1.5: defaultChannel "slack" → "portal"**

```
MODIFY line 298:
  Before: default_channel = pos.get("defaultChannel", "slack")
  After:  default_channel = pos.get("defaultChannel", "portal")
```

**Change 1.6: force-delete cascades to AGENT#**

```
MODIFY: delete_employee(force=True) — after binding/mapping deletion, ADD:

    agent_id = None
    emp = db.get_employee(emp_id)
    if emp:
        agent_id = emp.get("agentId")

    ... existing db.delete_employee(emp_id) ...

    # Cascade: delete agent + S3 workspace
    if agent_id:
        db.delete_agent(agent_id)
        try:
            import boto3 as _b3del
            s3 = _b3del.client("s3")
            bucket = os.environ.get("S3_BUCKET", "")
            if bucket:
                resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"{emp_id}/workspace/", MaxKeys=200)
                for obj in resp.get("Contents", []):
                    s3.delete_object(Bucket=bucket, Key=obj["Key"])
        except Exception:
            pass
```

**Change 1.7: activity cache**

```
ADD module-level:
    _activity_cache = {"data": None, "expires": 0}

MODIFY: get_employee_activities() — at top:
    import time as _time_act
    if _activity_cache["data"] and _time_act.time() < _activity_cache["expires"]:
        cached = _activity_cache["data"]
        # Still apply manager scope filter on cached data
        if user and user.role == "manager":
            ...filter cached...
        return cached

At bottom before return:
    _activity_cache["data"] = activities
    _activity_cache["expires"] = _time_act.time() + 30
    return activities
```

### 2. db.py — remove shared_bindings

**MODIFY: provision_employee_atomic()**

```
DELETE parameter: shared_bindings: list[dict] = None
DELETE lines 157-161: shared binding loop
```

### 3. Cleanup files

**seed_routing_conversations.py:**
```
DELETE: rule-02 (route_to_shared_agent helpdesk)
DELETE: rule-05 (route_to_shared_agent onboarding)
DELETE: sess-005 (shared agent session)
```

**demo/server.py:**
```
DELETE: agent-helpdesk and agent-onboarding entries with autoBindAll
```

**agents.py:**
```
DELETE line 115 comment: "Simple agent creation without binding (rare: shared agent)"
```

---

## Unit Test Plan

```
test_organization.py:

1. test_delete_employee_has_audit:
   Scan delete_employee for create_audit_entry → must exist

2. test_delete_department_checks_positions:
   Scan delete_department for "department_has_positions" → must exist

3. test_no_auto_bind_all:
   Scan org.py for "autoBindAll" → must NOT exist

4. test_no_shared_bindings_in_db:
   Scan db.py provision_employee_atomic for "shared_bindings" → must NOT exist

5. test_default_channel_portal:
   Scan org.py for '"portal"' in defaultChannel context → must exist
   Scan for '"slack"' as default → must NOT exist

6. test_force_delete_cascades_agent:
   Scan delete_employee for "delete_agent" → must exist

7. test_activity_cache:
   Scan org.py for "_activity_cache" → must exist
```

---

## Migration Notes

- No DynamoDB schema changes
- No new records
- Shared agent removal: existing BIND# records with mode="N:1" become orphaned but harmless (never queried after removal)
- seed_routing_conversations.py: only affects fresh deploys, not existing environments
