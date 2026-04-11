# Design: Monitor Center — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-monitor-center.md
**Scope:** Full rewrite of monitor.py. Remove CloudWatch dependency (project-wide unique to this file). Migrate takeover from SSM to DynamoDB.

---

## Dependency Removal Scope

### CloudWatch — DELETE (only exists in monitor.py)

```
DELETE: _get_all_agentcore_log_groups()     lines 50-61
DELETE: _query_cloudwatch_sessions()        lines 63-110
DELETE: CloudWatch merge in get_sessions()  lines 170-203
DELETE: get_runtime_events() CW section     lines 391-509 (rewrite as AUDIT# query)
```

Zero impact on other files. monitor.py is the only CloudWatch consumer in the project.

### SSM — DELETE takeover only

```
DELETE from monitor.py:
  takeover_session()    SSM put_parameter   line 229
  return_session()      SSM delete_parameter line 252
  admin_send_message()  SSM get_parameter   line 278
  get_takeover_status() SSM get_parameter   line 306

DELETE from agent-container/server.py:
  SSM takeover check    line ~1078

KEEP (other modules, correct SSM usage):
  admin_always_on.py  — ECS Fargate task management (dynamic, cross-service)
  portal.py/bindings.py — IM user-mapping dual-write (transition period)
  settings.py/skill_loader.py — API keys (SSM SecureString, correct choice)
  shared.py — gateway-instance-id
  tenant_router.py — default runtime-id (startup load)
```

---

## File-by-File Changes

### 1. monitor.py — Full rewrite

**DELETE entirely (CloudWatch + SSM):**
- `_get_all_agentcore_log_groups()` (lines 50-61)
- `_query_cloudwatch_sessions()` (lines 63-110)
- `_check_gateway_status()` (lines 560-568) — replaced by system_status
- `_measure_bedrock_latency()` (lines 571-579) — replaced by system_status

**REWRITE: get_sessions() — DynamoDB only**

```python
@router.get("/api/v1/monitor/sessions")
def get_sessions(authorization: str = Header(default="")):
    """Sessions from DynamoDB only. Status from AGENT#.lastInvocationAt."""
    user = _get_current_user(authorization)
    db_sessions = db.get_sessions()
    employees = db.get_employees()
    agents_list = db.get_agents()
    emp_map = {e["id"]: e for e in employees}
    agent_by_emp = {a.get("employeeId", ""): a for a in agents_list if a.get("employeeId")}

    enriched = []
    for s in db_sessions:
        eid = s.get("employeeId", "")
        if not eid or eid == "unknown":
            continue
        # Resolve names
        emp = emp_map.get(eid)
        if not emp:
            continue
        agent = agent_by_emp.get(emp["id"])
        s["employeeName"] = emp["name"]
        s["agentId"] = agent["id"] if agent else s.get("agentId", "")
        s["agentName"] = agent["name"] if agent else ""
        if not s.get("channel") or s["channel"] == "unknown":
            s["channel"] = (emp.get("channels") or ["portal"])[0]
        # Status from lastActive timestamp
        last_active = s.get("lastActive", s.get("startedAt", ""))
        if last_active:
            try:
                la_time = datetime.fromisoformat(last_active.replace("Z","+00:00"))
                age_min = (datetime.now(timezone.utc) - la_time).total_seconds() / 60
                s["status"] = "active" if age_min < 15 else "idle" if age_min < 60 else "completed"
            except:
                s["status"] = "completed"
        else:
            s["status"] = "completed"
        enriched.append(s)

    # NO CloudWatch merge. DynamoDB is the single source.
    enriched.sort(key=lambda s: ({"active":0,"idle":1,"completed":2}.get(s.get("status","completed"),3), -(s.get("turns",0))))
    return enriched
```

**REWRITE: takeover → DynamoDB with TTL**

```python
@router.post("/api/v1/monitor/sessions/{session_id}/takeover")
def takeover_session(session_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    try:
        import boto3 as _b3tk
        ddb = _b3tk.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        table.update_item(
            Key={"PK": "ORG#acme", "SK": f"SESSION#{session_id}"},
            UpdateExpression="SET takeover = :admin, takeoverBy = :name, "
                "takeoverExpiresAt = :exp, takeoverTTL = :ttl",
            ExpressionAttributeValues={
                ":admin": user.employee_id,
                ":name": user.name,
                ":exp": expires.isoformat(),
                ":ttl": int(expires.timestamp()),
            },
        )
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "session_takeover",
            "actorId": user.employee_id, "actorName": user.name,
            "targetType": "session", "targetId": session_id,
            "detail": f"Admin {user.name} took over session {session_id} (expires {expires.isoformat()})",
            "status": "success",
        })
    except Exception as e:
        raise HTTPException(500, f"Takeover failed: {e}")
    return {"taken_over": True, "sessionId": session_id,
            "adminId": user.employee_id, "expiresAt": expires.isoformat()}

@router.delete("/api/v1/monitor/sessions/{session_id}/takeover")
def return_session(session_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3rt
        ddb = _b3rt.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        table.update_item(
            Key={"PK": "ORG#acme", "SK": f"SESSION#{session_id}"},
            UpdateExpression="REMOVE takeover, takeoverBy, takeoverExpiresAt, takeoverTTL",
        )
        db.create_audit_entry({...eventType: "session_returned"...})
    except Exception as e:
        raise HTTPException(500, f"Return failed: {e}")
    return {"returned": True, "sessionId": session_id}

@router.get("/api/v1/monitor/sessions/{session_id}/takeover")
def get_takeover_status(session_id: str, authorization: str = Header(default="")):
    require_auth(authorization)
    session = db.get_session(session_id)
    if not session or not session.get("takeover"):
        return {"active": False, "sessionId": session_id}
    expires = session.get("takeoverExpiresAt", "")
    if expires:
        try:
            if datetime.fromisoformat(expires.replace("Z","+00:00")) < datetime.now(timezone.utc):
                return {"active": False, "sessionId": session_id, "expired": True}
        except: pass
    return {"active": True, "adminId": session["takeover"],
            "adminName": session.get("takeoverBy", ""),
            "expiresAt": expires, "sessionId": session_id}
```

**REWRITE: admin_send_message — role "assistant" → "admin"**

```python
# Line 291: change "role": "assistant" to "role": "admin"
table.put_item(Item={
    "PK": "ORG#acme", "SK": f"CONV#{session_id}#admin#{int(time.time())}",
    "sessionId": session_id, "role": "admin", "content": message,  # was "assistant"
    "ts": ts, "source": "human_admin", "adminId": user.employee_id,
})
```

**NEW: get_event_stream() — replaces get_runtime_events()**

```python
@router.get("/api/v1/monitor/events")
def get_event_stream(minutes: int = 60, limit: int = 50):
    """Unified event stream from DynamoDB AUDIT#."""
    entries = db.get_audit_entries(limit=max(limit, 200))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    events = [e for e in entries if e.get("timestamp", "") >= cutoff]
    # Categorize for frontend
    category_map = {
        "agent_invocation": ("invocation", "message"),
        "permission_denied": ("security", "shield"),
        "guardrail_block": ("security", "alert"),
        "config_change": ("config", "settings"),
        "soul_change": ("config", "edit"),
        "tool_permission_change": ("config", "wrench"),
        "runtime_config_change": ("config", "cpu"),
        "agent_refresh": ("config", "refresh"),
        "personal_soul_change": ("review", "eye"),
        "kb_upload": ("review", "upload"),
        "session_takeover": ("takeover", "radio"),
        "session_returned": ("takeover", "radio"),
        "employee_deleted": ("lifecycle", "trash"),
        "agent_deleted": ("lifecycle", "trash"),
    }
    for e in events:
        cat, icon = category_map.get(e.get("eventType", ""), ("other", "info"))
        e["category"] = cat
        e["icon"] = icon
    return {"events": events[:limit],
            "summary": {"total": len(events),
                        "security": len([e for e in events if e.get("category") == "security"]),
                        "config": len([e for e in events if e.get("category") == "config"])}}
```

**NEW: get_action_items()**

```python
@router.get("/api/v1/monitor/action-items")
def get_action_items():
    """Aggregated pending items across all modules."""
    entries = db.get_audit_entries(limit=200)
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(hours=24)).isoformat()
    items = []
    # Pending SOUL/KB reviews
    pending = [e for e in entries if e.get("status") == "pending_review"
               or (e.get("eventType") == "personal_soul_change" and e.get("reviewStatus") == "pending")]
    if pending:
        items.append({"type": "review", "severity": "warning",
                       "message": f"{len(pending)} changes pending review",
                       "count": len(pending)})
    # Permission denials (24h)
    denials = [e for e in entries if e.get("eventType") == "permission_denied"
               and e.get("timestamp", "") >= day_ago]
    if denials:
        items.append({"type": "security", "severity": "warning" if len(denials) > 10 else "info",
                       "message": f"{len(denials)} permission denials in last 24h",
                       "count": len(denials)})
    # Guardrail blocks (24h)
    blocks = [e for e in entries if e.get("eventType") == "guardrail_block"
              and e.get("timestamp", "") >= day_ago]
    if blocks:
        items.append({"type": "security", "severity": "warning",
                       "message": f"{len(blocks)} guardrail blocks in last 24h",
                       "count": len(blocks)})
    # Budget
    from routers.usage import usage_budgets
    budgets = usage_budgets()
    over = [b for b in budgets if b["status"] in ("over", "warning")]
    if over:
        items.append({"type": "budget", "severity": "warning",
                       "message": f"{len(over)} departments over/near budget",
                       "count": len(over)})
    # Unbound employees
    unbound = [e for e in db.get_employees() if not e.get("agentId")]
    if unbound:
        items.append({"type": "lifecycle", "severity": "info",
                       "message": f"{len(unbound)} employees without agents",
                       "count": len(unbound)})
    return items
```

**NEW: get_system_status()**

```python
import threading

_system_status_cache = {"data": {}, "expires": 0}

def _check_services():
    """Background: check all 4 services every 30s."""
    import urllib.request
    services = {}
    for name, port in [("admin-console", 8099), ("tenant-router", 8090),
                        ("h2-proxy", 8091), ("gateway", 18789)]:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/", timeout=2)
            services[name] = "healthy"
        except:
            services[name] = "unreachable"
    # Bedrock connectivity
    try:
        boto3.client("bedrock", region_name=os.environ.get("AWS_REGION","us-east-1")).list_foundation_models(maxResults=1)
        services["bedrock"] = "connected"
    except:
        services["bedrock"] = "unreachable"
    services["uptime"] = _format_uptime(time.time() - _SERVER_START_TIME)
    _system_status_cache["data"] = services
    _system_status_cache["expires"] = time.time() + 30

threading.Thread(target=lambda: [(_check_services(), __import__('time').sleep(30)) for _ in iter(int,1)],
                 daemon=True).start()

@router.get("/api/v1/monitor/system-status")
def get_system_status():
    if not _system_status_cache["data"]:
        _check_services()  # first call: sync
    return _system_status_cache["data"]
```

**NEW: get_agent_activity()**

```python
@router.get("/api/v1/monitor/agent-activity")
def get_agent_activity():
    """Agent activity based on DynamoDB AGENT#.lastInvocationAt."""
    agents = db.get_agents()
    now = datetime.now(timezone.utc)
    active, idle, offline = [], [], []
    for a in agents:
        last = a.get("lastInvocationAt", "")
        if not last:
            offline.append({"id": a["id"], "name": a["name"], "employeeName": a.get("employeeName",""), "lastActive": None})
            continue
        try:
            ts = datetime.fromisoformat(last.replace("Z","+00:00"))
            age = (now - ts).total_seconds()
            entry = {"id": a["id"], "name": a["name"], "employeeName": a.get("employeeName",""),
                     "lastActive": last, "ageSec": int(age)}
            if age < 900: active.append(entry)
            elif age < 3600: idle.append(entry)
            else: offline.append(entry)
        except:
            offline.append({"id": a["id"], "name": a["name"], "employeeName": a.get("employeeName",""), "lastActive": None})
    return {"active": active, "idle": idle, "offline": offline,
            "summary": {"active": len(active), "idle": len(idle), "offline": len(offline), "total": len(agents)}}
```

**NEW: refresh_all_agents()**

```python
@router.post("/api/v1/admin/refresh-all")
def refresh_all_agents(authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    agents = db.get_agents()
    refreshed = []
    for a in agents:
        emp_id = a.get("employeeId")
        if emp_id and a.get("lastInvocationAt"):
            threading.Thread(target=stop_employee_session, args=(emp_id,), daemon=True).start()
            refreshed.append(emp_id)
    return {"refreshed": len(refreshed), "employees": refreshed}
```

**REWRITE: get_alert_rules() — real data only**

```python
@router.get("/api/v1/monitor/alerts")
def get_alert_rules():
    entries = db.get_audit_entries(limit=200)
    agents = db.get_agents()
    employees = db.get_employees()
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(hours=24)).isoformat()

    # Budget — real
    from routers.usage import usage_budgets
    budgets = usage_budgets()
    over = [b for b in budgets if b["status"] in ("over", "warning")]

    # Unbound — real
    unbound = [e for e in employees if not e.get("agentId")]

    # Permission denials — real (from new DynamoDB writes)
    denials = [e for e in entries if e.get("eventType") == "permission_denied"
               and e.get("timestamp","") >= day_ago]

    # Pending reviews — real
    pending = [e for e in entries if e.get("reviewStatus") == "pending"]

    # SOUL drift — real
    pos_versions = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv
    drifted = [a for a in agents if (a.get("soulVersions") or {}).get("position", 1) < pos_versions.get(a.get("positionId",""), 1)]

    return [
        {"id": "alert-budget", "type": "Budget overrun", "status": "warning" if over else "ok",
         "detail": f"{len(over)} departments over/near budget" if over else "All within budget"},
        {"id": "alert-unbound", "type": "Unbound employees", "status": "warning" if unbound else "ok",
         "detail": f"{len(unbound)} employees without agents" if unbound else "All bound"},
        {"id": "alert-denials", "type": "Permission denials (24h)", "status": "warning" if len(denials) > 5 else "ok",
         "detail": f"{len(denials)} denials" if denials else "No denials"},
        {"id": "alert-reviews", "type": "Pending reviews", "status": "warning" if pending else "ok",
         "detail": f"{len(pending)} changes pending" if pending else "All reviewed"},
        {"id": "alert-drift", "type": "SOUL version drift", "status": "warning" if drifted else "ok",
         "detail": f"{len(drifted)} agents behind" if drifted else "All current"},
    ]
```

**REWRITE: get_session_detail() quality — real data**

```python
# Replace formula with real calculation
from routers.audit import _calculate_agent_quality
quality_data = _calculate_agent_quality(agent_id)
if quality_data.get("score") is not None:
    quality = quality_data["breakdown"]
    quality["overallScore"] = quality_data["score"]
else:
    quality = {"satisfaction": None, "toolSuccess": None, "responseTime": None,
               "compliance": None, "completionRate": None, "overallScore": None,
               "note": "No feedback data yet — quality scores will appear after real usage"}
```

**REWRITE: Plan E — real PII patterns**

```python
import re
_PII_PATTERNS = [
    (re.compile(r'\d{3}-\d{2}-\d{4}'), "SSN pattern"),
    (re.compile(r'\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}'), "Credit card pattern"),
    (re.compile(r'(?i)password\s*[:=]\s*\S+'), "Credential exposure"),
    (re.compile(r'(?i)api[_-]?key\s*[:=]\s*\S+'), "API key exposure"),
    (re.compile(r'(?i)(secret|token)\s*[:=]\s*["\']?\S{8,}'), "Secret/token exposure"),
]

def _scan_response(content: str) -> list:
    findings = []
    for pattern, label in _PII_PATTERNS:
        if pattern.search(content):
            findings.append({"result": "flag", "detail": label})
    return findings or [{"result": "pass", "detail": "No sensitive data detected"}]
```

### 2. agent-container/server.py — takeover check SSM → DynamoDB

```
FIND: SSM takeover check (~line 1078)
  admin_param = ssm_tk.get_parameter(
      Name=f"/openclaw/{STACK_NAME}/sessions/{tenant_id}/takeover")

REPLACE with DynamoDB check:
  try:
      session_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"SESSION#{tenant_id}"})
      session_item = session_resp.get("Item", {})
      if session_item.get("takeover"):
          expires = session_item.get("takeoverExpiresAt", "")
          if expires:
              from datetime import datetime as _dtk, timezone as _tzk
              if _dtk.fromisoformat(expires.replace("Z","+00:00")) > _dtk.now(_tzk.utc):
                  # Takeover active — return admin message
                  ...
  except Exception:
      pass
```

### 3. DynamoDB TTL configuration

```bash
# Enable TTL on takeoverTTL attribute (one-time setup)
aws dynamodb update-time-to-live \
  --table-name openclaw-demo \
  --time-to-live-specification Enabled=true,AttributeName=takeoverTTL \
  --region us-west-2 --profile jiade2
```

Add to deploy.sh after table creation step.

### 4. monitor.py imports cleanup

```python
# REMOVE:
from botocore.exceptions import ClientError  # only used by CloudWatch
import boto3  # still needed for DynamoDB takeover, but remove CW-specific usage

# KEEP:
import db
from shared import require_auth, require_role, get_dept_scope, stop_employee_session, GATEWAY_REGION, STACK_NAME
from routers.usage import usage_budgets, _get_agent_usage_today
```

---

## Unit Test Plan

```
test_monitor_center.py:

1. test_no_cloudwatch_in_monitor:
   Scan monitor.py for "filter_log_events", "describe_log_groups" → must NOT exist

2. test_no_ssm_in_monitor:
   Scan monitor.py for "ssm.put_parameter", "ssm.get_parameter", "ssm.delete_parameter" → must NOT exist

3. test_takeover_uses_dynamodb:
   Scan takeover_session for "table.update_item" → must exist

4. test_takeover_has_ttl:
   Scan takeover_session for "takeoverTTL" or "takeoverExpiresAt" → must exist

5. test_admin_message_role_is_admin:
   Scan admin_send_message for '"role": "admin"' → must exist
   Scan for '"role": "assistant"' in send_message context → must NOT exist

6. test_sessions_no_cloudwatch_merge:
   Scan get_sessions for "_query_cloudwatch_sessions" → must NOT exist

7. test_event_stream_exists:
   Scan for "def get_event_stream" → must exist

8. test_action_items_exists:
   Scan for "def get_action_items" → must exist

9. test_system_status_exists:
   Scan for "def get_system_status" → must exist

10. test_agent_activity_exists:
    Scan for "def get_agent_activity" → must exist

11. test_alert_rules_no_placeholders:
    Scan get_alert_rules for "crash loop", "Channel auth expired", "Memory bloat" → must NOT exist

12. test_quality_uses_real_calculation:
    Scan get_session_detail for "_calculate_agent_quality" → must exist
    Scan for "3.5 + turns" → must NOT exist

13. test_plan_e_real_patterns:
    Scan for "PII_PATTERNS" or "SSN pattern" → must exist
    Scan for '"$" in msg' → must NOT exist

14. test_refresh_all_exists:
    Scan for "def refresh_all_agents" → must exist
```

---

## Migration Notes

- DynamoDB TTL: one-time `update-time-to-live` command per environment
- server.py takeover change: requires Docker rebuild
- Frontend: full rewrite of Monitor page (separate task)
- Old CloudWatch-based runtime events tab: removed, replaced by AUDIT# event stream
- Existing SESSION# records with no `takeover` field: compatible (treated as no takeover)
