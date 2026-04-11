# PRD: Monitor Center — Full Redesign

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Priority:** P0 — Currently the weakest module, needs fundamental rework

---

## 1. Problem Statement

Monitor Center is performing monitoring rather than doing monitoring. The page shows fake quality scores, placeholder alert rules, and CloudWatch queries that often return empty. Admin sees green lights everywhere but nothing is actually being monitored.

### Core issues:
- **Fake data**: Quality scores are formula-based (3.5 + turns * 0.15), 6/8 alert rules always show "ok"
- **Wrong data source**: CloudWatch queries for session status when DynamoDB already has the data
- **Missing integrations**: No connection to Review Engine, permission denied events, guardrail blocks, audit scan
- **Unnecessary AWS dependencies**: CloudWatch and SSM used where DynamoDB suffices
- **No actionable operations**: No refresh buttons, no audit scan trigger, no config sync check

---

## 2. Architecture Decision: Pure DynamoDB

### Remove CloudWatch dependency

| Current CloudWatch usage | Replacement |
|--------------------------|-------------|
| `_query_cloudwatch_sessions()` — active session detection | `AGENT#.lastInvocationAt` (already implemented in Agent Factory) |
| `get_runtime_events()` — microVM lifecycle events | DynamoDB AUDIT# events (invocation, permission_denied, guardrail_block, config_change) |
| `_get_all_agentcore_log_groups()` — log group discovery | Not needed — DynamoDB is the data source |

CloudWatch remains available via AWS Console for developer debugging. It does not belong in the management UI.

### Remove SSM dependency (takeover)

| Current SSM usage | Replacement |
|-------------------|-------------|
| Write `/sessions/{id}/takeover` | DynamoDB `SESSION#{id}` add fields: `takeover`, `takeoverBy`, `takeoverExpiresAt` |
| Read takeover status (server.py) | server.py reads DynamoDB (already has connection for EMP#, POS#, CONFIG#) |
| No TTL → permanent silencing risk | DynamoDB TTL feature → automatic expiration after 30 minutes |

### Result: Monitor Center is pure DynamoDB

All data from: AUDIT#, SESSION#, USAGE#, AGENT#, CONFIG#. One data source, consistent with all other modules.

---

## 3. New Page Design

### 3.1 Action Items (top of page)

Aggregates pending items from across all modules into one panel:

```
Sources:
  - AUDIT# eventType="personal_soul_change" status="pending" → "N SOUL changes pending review"
  - AUDIT# eventType="permission_denied" (last 24h) → "N permission denials today"
  - AUDIT# eventType="guardrail_block" (last 24h) → "N guardrail blocks today"
  - AUDIT# eventType="kb_upload" status="pending_review" → "N KB uploads pending review"
  - Usage budgets (existing usage_budgets()) → "N departments over/near budget"
  - Employees without agents → "N unbound employees"

Buttons:
  [Run Audit Scan] → POST /api/v1/audit/run-scan (existing)
  [Refresh All Agents] → POST /api/v1/admin/refresh-all (new: loops stop_employee_session for all)
  [Check Config Version] → GET config version + show last bump time
```

### 3.2 System Status

Real service health checks (not just Gateway):

```python
def get_system_status():
    """Check all 4 services + Bedrock connectivity."""
    services = {}
    for name, port in [("admin-console", 8099), ("tenant-router", 8090),
                        ("h2-proxy", 8091), ("gateway", 18789)]:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/", timeout=2)
            services[name] = "healthy"
        except:
            services[name] = "unreachable"
    # Bedrock connectivity (lightweight check)
    try:
        boto3.client("bedrock", region_name=AWS_REGION).list_foundation_models(maxResults=1)
        services["bedrock"] = "connected"
    except:
        services["bedrock"] = "unreachable"
    # AgentCore runtimes count
    try:
        ac = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)
        runtimes = ac.list_agent_runtimes().get("agentRuntimes", [])
        services["agentcore"] = f"{len(runtimes)} runtimes"
    except:
        services["agentcore"] = "unknown"
    return services
```

Cached 30 seconds (background thread, same pattern as gateway health worker).

### 3.3 Event Stream (unified timeline)

Single chronological view merging all DynamoDB AUDIT# events:

```python
def get_event_stream(minutes: int = 60, limit: int = 50):
    """Unified event stream from DynamoDB AUDIT# — all event types."""
    entries = db.get_audit_entries(limit=limit)
    # Filter to requested time window
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    events = [e for e in entries if e.get("timestamp", "") >= cutoff]
    # Enrich with icons/categories for frontend
    for e in events:
        et = e.get("eventType", "")
        if et == "agent_invocation":
            e["category"] = "invocation"
            e["icon"] = "message"
        elif et == "permission_denied":
            e["category"] = "security"
            e["icon"] = "shield"
        elif et == "guardrail_block":
            e["category"] = "security"
            e["icon"] = "alert"
        elif et in ("config_change", "soul_change", "tool_permission_change",
                     "runtime_config_change", "agent_refresh"):
            e["category"] = "config"
            e["icon"] = "settings"
        elif et in ("personal_soul_change", "kb_upload"):
            e["category"] = "review"
            e["icon"] = "eye"
        elif et in ("employee_deleted", "agent_deleted"):
            e["category"] = "lifecycle"
            e["icon"] = "trash"
        else:
            e["category"] = "other"
            e["icon"] = "info"
    return events
```

No CloudWatch. No string splitting. Just DynamoDB query + categorization.

### 3.4 Agent Activity

Based on `AGENT#.lastInvocationAt` (DynamoDB):

```python
def get_agent_activity():
    agents = db.get_agents()
    now = datetime.now(timezone.utc)
    active, idle, offline = [], [], []
    for a in agents:
        last = a.get("lastInvocationAt", "")
        if not last:
            offline.append(a)
            continue
        try:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age = (now - ts).total_seconds()
            a["lastActiveAgo"] = age
            if age < 900: active.append(a)
            elif age < 3600: idle.append(a)
            else: offline.append(a)
        except:
            offline.append(a)
    return {"active": active, "idle": idle, "offline": offline,
            "summary": {"active": len(active), "idle": len(idle), "offline": len(offline)}}
```

Each active agent shows [Refresh ↻] button (calls existing `/admin/refresh-agent/{emp_id}`).

### 3.5 Takeover — DynamoDB with TTL

```python
@router.post("/api/v1/monitor/sessions/{session_id}/takeover")
def takeover_session(session_id, authorization):
    user = require_role(authorization, roles=["admin"])
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    expires_epoch = int(expires.timestamp())
    # Write to DynamoDB SESSION# record
    table.update_item(
        Key={"PK": "ORG#acme", "SK": f"SESSION#{session_id}"},
        UpdateExpression="SET takeover = :admin, takeoverBy = :name, takeoverExpiresAt = :exp, takeoverTTL = :ttl",
        ExpressionAttributeValues={
            ":admin": user.employee_id,
            ":name": user.name,
            ":exp": expires.isoformat(),
            ":ttl": expires_epoch,  # DynamoDB TTL attribute
        },
    )
    db.create_audit_entry({...eventType: "session_takeover"...})
    return {"taken_over": True, "expiresAt": expires.isoformat(), "remainingMin": 30}
```

DynamoDB TTL configured on `takeoverTTL` attribute → AWS auto-deletes after 30 min.

server.py check (in agent container):
```python
# Replace SSM check with DynamoDB check
session_record = table.get_item(Key={"PK": "ORG#acme", "SK": f"SESSION#{tenant_id}"}).get("Item", {})
if session_record.get("takeover"):
    expires = session_record.get("takeoverExpiresAt", "")
    if expires and datetime.fromisoformat(expires) > datetime.now(timezone.utc):
        # Takeover active — skip agent processing
        return takeover_response(session_record["takeoverBy"])
```

### 3.6 Quality Scores — honest

Replace formula with real data or "N/A":

```python
# Before: satisfaction = round(min(5.0, 3.5 + turns * 0.15), 1)
# After:
from routers.audit import _calculate_agent_quality
quality_data = _calculate_agent_quality(agent_id)
if quality_data.get("score") is not None:
    satisfaction = quality_data["score"]
else:
    satisfaction = None  # Frontend shows "N/A" instead of fake 4.5
```

### 3.7 Alert Rules — real only

Remove 6 placeholder rules. Keep only rules backed by real data:

```python
def get_alert_rules():
    return [
        # Budget — real data from usage_budgets()
        {"id": "alert-budget", "type": "Budget overrun", ...},
        # Unbound employees — real data from db.get_employees()
        {"id": "alert-unbound", "type": "Unbound employees", ...},
        # Permission denials — real from AUDIT# (new, from Security Center work)
        {"id": "alert-perm-denied", "type": "Repeated permission denials",
         "status": "warning" if denial_count > 5 else "ok",
         "detail": f"{denial_count} denials in last 24h"},
        # Pending reviews — real from AUDIT# (Review Engine)
        {"id": "alert-pending-review", "type": "Unreviewed changes",
         "status": "warning" if pending_count > 0 else "ok",
         "detail": f"{pending_count} SOUL/KB changes pending review"},
        # SOUL version drift — real from AGENT# soulVersions comparison
        {"id": "alert-soul-drift", ...},
    ]
```

### 3.8 Plan E — meaningful patterns

Replace `"$" in content` with actual security patterns:

```python
import re
PII_PATTERNS = [
    (r'\d{3}-\d{2}-\d{4}', "SSN pattern"),
    (r'\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}', "Credit card pattern"),
    (r'(?i)password\s*[:=]\s*\S+', "Credential exposure"),
    (r'(?i)api[_-]?key\s*[:=]\s*\S+', "API key exposure"),
]

def scan_response(content):
    findings = []
    for pattern, label in PII_PATTERNS:
        if re.search(pattern, content):
            findings.append({"result": "flag", "detail": label})
    return findings or [{"result": "pass", "detail": "No sensitive data detected"}]
```

---

## 4. Files to Delete/Rewrite

### Delete entirely:
- `_get_all_agentcore_log_groups()` — CloudWatch log group discovery
- `_query_cloudwatch_sessions()` — CloudWatch session queries
- `_check_gateway_status()` — replaced by comprehensive system status
- `_measure_bedrock_latency()` — misleading metric, replaced by connectivity check
- 6 placeholder alert rules (crash loop, channel auth, memory bloat, context window, PII, always "ok")

### Rewrite:
- `get_sessions()` — remove CloudWatch merge logic, DynamoDB only
- `get_session_detail()` — use `_calculate_agent_quality()` instead of formula
- `takeover_session()` / `return_session()` / `get_takeover_status()` — SSM → DynamoDB
- `admin_send_message()` — change role from "assistant" to "admin"
- `get_runtime_events()` → rename to `get_event_stream()`, source from AUDIT# only
- `get_alert_rules()` — keep only real data-backed rules
- `get_monitor_health()` — comprehensive system status + real agent activity

### New endpoints:
- `POST /api/v1/admin/refresh-all` — stop all active agent sessions
- `GET /api/v1/monitor/action-items` — aggregated pending items
- `GET /api/v1/monitor/system-status` — 4 services + Bedrock + AgentCore

---

## 5. Implementation Plan

### Phase 1: Backend rewrite (P0)

| Task | Description |
|------|-------------|
| 1.1 | Remove all CloudWatch code (3 functions + calls) |
| 1.2 | Takeover: SSM → DynamoDB with TTL |
| 1.3 | get_sessions(): DynamoDB only, no CloudWatch merge |
| 1.4 | get_event_stream(): unified AUDIT# timeline (replaces runtime-events) |
| 1.5 | get_agent_activity(): based on lastInvocationAt |
| 1.6 | get_system_status(): 4 services + Bedrock + AgentCore (cached 30s) |
| 1.7 | get_action_items(): aggregate pending reviews + denials + budget |
| 1.8 | get_alert_rules(): remove 6 placeholders, add real rules |
| 1.9 | get_session_detail(): real quality from _calculate_agent_quality() |
| 1.10 | admin_send_message(): role "assistant" → "admin" |
| 1.11 | Plan E: real PII pattern matching |
| 1.12 | OverallScore: unified 0-1 normalization |
| 1.13 | POST /admin/refresh-all: batch stop all active sessions |

### Phase 2: Frontend rewrite (P1)

| Task | Description |
|------|-------------|
| 2.1 | Action Items panel (top of page) |
| 2.2 | System Status card (4 services + badges) |
| 2.3 | Event Stream timeline (replacing Runtime Events tab) |
| 2.4 | Agent Activity view (active/idle/offline groups with refresh buttons) |
| 2.5 | Takeover panel: show expiration countdown |
| 2.6 | Quality scores: show "N/A" when no real data |
| 2.7 | Alert rules: only show rules with real data |

### Phase 3: Agent container (requires Docker rebuild)

| Task | Description |
|------|-------------|
| 3.1 | server.py: replace SSM takeover check with DynamoDB check |
| 3.2 | DynamoDB table: enable TTL on `takeoverTTL` attribute |

---

## 6. TODO

### Must-Do
- [ ] 1.1-1.13: Backend rewrite (all 13 tasks)
- [ ] 2.1-2.7: Frontend rewrite (all 7 tasks)
- [ ] 3.1: server.py takeover SSM → DynamoDB
- [ ] 3.2: Enable DynamoDB TTL attribute (CloudFormation or aws cli)
- [ ] Unit tests for all new endpoints
- [ ] Update ui-guide.html Monitor Center findings
- [ ] Docker rebuild for server.py takeover change

### Infrastructure TODO
- [ ] **CloudFront → ALB → EC2**: Current setup CloudFront → EC2 public IP breaks on instance restart (no Elastic IP). Add ALB in front of EC2:8099. CloudFront origin points to ALB DNS (stable). Update CloudFormation template and deploy.sh.
- [ ] **DynamoDB TTL**: Enable TTL on `takeoverTTL` attribute for each environment
- [ ] **Elastic IP or ALB**: Production environment needs stable endpoint. ALB preferred (health checks, future multi-AZ).

### Frontend TODO from other modules (include in this build)
- [ ] Security Center: dynamic AWS region in console URLs
- [ ] Security Center: Runtime card show guardrail + positions
- [ ] Knowledge: upload 413 error handling (already done)
- [ ] Agent Factory: Refresh Agent button (already done)
- [ ] Organization: Default Channel removed (already done), stat card fixes (already done)
