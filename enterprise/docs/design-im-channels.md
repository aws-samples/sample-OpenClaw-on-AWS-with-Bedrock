# Design: IM Channels — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-im-channels.md
**Scope:** admin_im.py (299 lines), bindings.py (469 lines), portal.py (relevant sections)
**Out of scope:** gateway_proxy.py (P2), Fargate always-on redesign (future session)

---

## File-by-File Change Design

### 1. admin_im.py — 5 changes

#### 1.1 Fix `get_im_channels()` — DynamoDB instead of SSM inline query

```
REPLACE: lines 170-187 (SSM inline channel_counts block)

WITH:
    # Count per channel from DynamoDB MAPPING# (replaces SSM scan)
    channel_counts: dict = {}
    try:
        all_mappings = db.get_user_mappings()
        for m in all_mappings:
            ch = m.get("channel", "")
            if ch:
                channel_counts[ch] = channel_counts.get(ch, 0) + 1
    except Exception:
        pass
```

Remove `import boto3` at top if no other usage. Remove `ssm = boto3.client(...)` line.

#### 1.2 Fix hardcoded `ORG#acme`

```
REPLACE line 236:
  Before: KeyConditionExpression=_KBC("PK").eq("ORG#acme") & _KBC("SK").begins_with("MAPPING#"),
  After:  KeyConditionExpression=_KBC("PK").eq(db.ORG_PK) & _KBC("SK").begins_with("MAPPING#"),
```

#### 1.3 Add audit trail to `set_im_bot_info()`

```python
# After db.set_config("im-bot-info", config):
db.create_audit_entry({
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "eventType": "config_change",
    "actorId": "admin",
    "actorName": "IT Admin",
    "targetType": "im-bot-info",
    "targetId": channel,
    "detail": f"Updated IM bot info for {channel}: {list(body.keys())}",
    "status": "success",
})
```

Add `from datetime import datetime, timezone` import.

#### 1.4 Deduplicate `_list_user_mappings()`

```
DELETE: lines 73-102 (_list_user_mappings function)

REPLACE usage at line 111:
  Before: raw_mappings = _list_user_mappings()
  After:  raw_mappings = db.get_user_mappings()
```

#### 1.5 Validate bot info body with Pydantic

```python
class IMBotInfoUpdate(BaseModel):
    botUsername: str = ""
    feishuAppId: str = ""
    deepLinkTemplate: str = ""
    webhookUrl: str = ""

# Change set_im_bot_info signature:
def set_im_bot_info(channel: str, body: IMBotInfoUpdate, ...)
# Use body.model_dump(exclude_unset=True) instead of raw dict
```

Add `from pydantic import BaseModel` import.

#### 1.6 New: IM channel health API

```python
@router.get("/api/v1/admin/im-channels/health")
def get_im_channel_health(authorization: str = Header(default="")):
    """Last message timestamp per channel from AUDIT# entries."""
    require_role(authorization, roles=["admin", "manager"])
    entries = db.get_audit_entries(limit=200)
    last_by_channel: dict = {}
    count_24h: dict = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for e in entries:
        if e.get("eventType") != "agent_invocation":
            continue
        # Determine channel from targetId or detail
        detail = e.get("detail", "")
        ts = e.get("timestamp", "")
        for ch in ["telegram", "discord", "feishu", "slack", "whatsapp", "dingtalk", "teams", "googlechat", "portal"]:
            if ch in detail.lower():
                if ts > last_by_channel.get(ch, ""):
                    last_by_channel[ch] = ts
                if ts >= cutoff:
                    count_24h[ch] = count_24h.get(ch, 0) + 1
                break
    return {"lastActivity": last_by_channel, "messagesLast24h": count_24h}
```

Add `from datetime import timedelta` import.

#### 1.7 New: Enrollment stats API

```python
@router.get("/api/v1/admin/im-channels/enrollment")
def get_im_enrollment_stats(authorization: str = Header(default="")):
    """Which employees are bound/unbound to IM channels."""
    require_role(authorization, roles=["admin", "manager"])
    emps = db.get_employees()
    mappings = db.get_user_mappings()

    # Build emp_id → set of channels
    emp_channels: dict = {}
    for m in mappings:
        eid = m.get("employeeId", "")
        ch = m.get("channel", "")
        if eid and ch:
            emp_channels.setdefault(eid, set()).add(ch)

    bound = []
    unbound = []
    for emp in emps:
        if not emp.get("agentId"):
            continue  # no agent = not relevant
        eid = emp["id"]
        channels = emp_channels.get(eid, set())
        entry = {
            "id": eid,
            "name": emp.get("name", eid),
            "position": emp.get("positionName", ""),
            "department": emp.get("departmentName", ""),
            "channels": sorted(channels),
        }
        if channels:
            bound.append(entry)
        else:
            unbound.append(entry)

    return {
        "totalWithAgent": len(bound) + len(unbound),
        "bound": len(bound),
        "unbound": len(unbound),
        "unboundEmployees": unbound,
        "multiChannel": [e for e in bound if len(e["channels"]) > 1],
    }
```

#### 1.8 New: Batch unbind per channel

```python
@router.delete("/api/v1/admin/im-channels/{channel}/unbind-all")
def batch_unbind_channel(channel: str, authorization: str = Header(default="")):
    """Disconnect all employees from a specific IM channel.
    Use case: bot token rotation — unbind all, rotate token, employees re-pair."""
    require_role(authorization, roles=["admin"])
    mappings = db.get_user_mappings()
    channel_mappings = [m for m in mappings if m.get("channel") == channel]
    deleted = 0
    for m in channel_mappings:
        try:
            db.delete_user_mapping(channel, m["channelUserId"])
            deleted += 1
        except Exception:
            pass
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": "admin",
        "actorName": "IT Admin",
        "targetType": "im-channel",
        "targetId": channel,
        "detail": f"Batch unbind: removed {deleted} bindings from {channel}",
        "status": "success",
    })
    return {"channel": channel, "deleted": deleted}
```

---

### 2. bindings.py — 3 changes

#### 2.1 Remove shared agent routing from `resolve_route()`

```
REPLACE: lines 396-429 (entire resolve_route function)

WITH simplified version:
@router.get("/api/v1/routing/resolve")
def resolve_route(channel: str = "", user_id: str = "", message: str = ""):
    """Simulate routing resolution — shows which employee binding matches."""
    bindings = db.get_bindings()
    binding = next(
        (b for b in bindings
         if b.get("employeeId") == user_id and b.get("mode") == "1:1"),
        None)
    if binding:
        return {
            "matched": True,
            "action": "route_to_personal_agent",
            "agent_id": binding.get("agentId"),
            "agent_name": binding.get("agentName"),
        }
    return {"matched": False, "action": "no_binding", "description": "No 1:1 binding for this user"}
```

Remove references to `route_to_shared_agent`.

#### 2.2 Remove duplicated `_list_user_mappings()`

```
DELETE: lines 72-101 (_list_user_mappings function)

UPDATE get_user_mappings endpoint (line 211):
  Before: return _list_user_mappings()
  After:  return db.get_user_mappings()
```

#### 2.3 Verify/add auth on bindings CRUD

Check which endpoints are in the auth middleware whitelist. Add `require_role()` where missing:

```python
# get_bindings already has _get_current_user() — OK (handles manager scope)
# create_binding — needs require_role
# get_user_mappings — needs require_role
# create_user_mapping — needs require_role
# delete_user_mapping — needs require_role
```

---

### 3. portal.py — 2 changes

#### 3.1 Replace `_find_channel_user_id()` SSM scan

```
REPLACE: lines 111-124

WITH:
def _find_channel_user_id(emp_id: str, channel_prefix: str) -> str:
    """Reverse lookup: given emp_id + channel, return the IM user_id."""
    mappings = db.get_user_mappings_for_employee(emp_id)
    for m in mappings:
        if m.get("channel", "").startswith(channel_prefix):
            return m.get("channelUserId", "")
    return ""
```

#### 3.2 Replace `_list_user_mappings_for_employee()` SSM scan

```
REPLACE: lines 127-139

WITH:
def _list_user_mappings_for_employee(emp_id: str, channel_prefix: str) -> bool:
    """Check if any DynamoDB mapping exists for this employee on the given channel."""
    mappings = db.get_user_mappings_for_employee(emp_id)
    return any(m.get("channel", "").startswith(channel_prefix) for m in mappings)
```

---

## Unit Test Plan

```
test_im_channels.py:

1. test_no_hardcoded_org_acme:
   Scan admin_im.py for "ORG#acme" → must NOT exist

2. test_get_im_channels_no_ssm_inline:
   Scan get_im_channels for "get_parameters_by_path" → must NOT exist

3. test_deduplicated_list_user_mappings:
   Scan admin_im.py for "def _list_user_mappings" → must NOT exist
   Scan bindings.py for "def _list_user_mappings" → must NOT exist

4. test_bot_info_has_audit:
   Scan set_im_bot_info for "create_audit_entry" → must exist

5. test_bot_info_has_pydantic:
   Scan admin_im.py for "class IMBotInfoUpdate" → must exist

6. test_resolve_route_no_shared_agent:
   Scan resolve_route for "route_to_shared_agent" → must NOT exist

7. test_bindings_crud_auth:
   Scan create_user_mapping (bindings.py) for "require_role" → must exist
   Scan delete_user_mapping (bindings.py) for "require_role" → must exist

8. test_find_channel_no_ssm:
   Scan _find_channel_user_id for "ssm" → must NOT exist (case insensitive)
   Scan _list_user_mappings_for_employee for "ssm" → must NOT exist

9. test_health_endpoint_exists:
   Scan admin_im.py for "def get_im_channel_health" → must exist

10. test_enrollment_endpoint_exists:
    Scan admin_im.py for "def get_im_enrollment_stats" → must exist

11. test_batch_unbind_exists:
    Scan admin_im.py for "def batch_unbind_channel" → must exist

12. test_batch_unbind_has_audit:
    Scan batch_unbind_channel for "create_audit_entry" → must exist

13. test_im_binding_check_exists:
    Scan admin_im.py for "def im_binding_check" → must exist

14. test_pairing_approve_has_audit:
    Scan approve_pairing for "create_audit_entry" → must exist
```

---

## Migration Notes

- No DynamoDB schema changes
- No new tables
- SSM reads reduced but SSM dual-write preserved (backward compat)
- `db.get_user_mappings()` and `db.get_user_mappings_for_employee()` already exist in db.py
- Portal SSM changes only affect portal.py helper functions (pairing flow unchanged)
- gateway_proxy.py changes deferred to Phase 5 (P2)
- Fargate always-on IM redesign deferred to future session
