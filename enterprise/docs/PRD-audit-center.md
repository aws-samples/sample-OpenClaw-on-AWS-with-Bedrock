# PRD: Audit Center + Review Engine

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Priority:** P0 — Compliance hub + AI-powered security scanning
**Related:** PRD-soul-review-engine.md (Phase 3 design, integrated here)

---

## 1. Problem Statement

Audit Center is a passive log viewer with 5 pattern-matching rules pretending to be "AI Insights". It lacks:
- Real AI analysis (Bedrock-powered anomaly detection)
- Connection to Review Engine (Personal SOUL review, KB injection scan, tool usage anomaly)
- Actionable operations (approve/revert/adjust from within audit)
- Complete event coverage (8 old event types, missing 8+ new types from recent work)
- Time-range filtering and pagination (hard cap at 200 entries)
- Compliance trending (no historical view of policy enforcement)

### Design intent: Three layers

```
Layer 1 — See:    What happened? (complete event history + advanced filtering)
Layer 2 — Understand: What's wrong? (AI analysis + pattern detection + Review Engine)
Layer 3 — Act:    How to fix it? (approve/revert/adjust/refresh from within audit)
```

---

## 2. Backend Issues (from code audit)

| # | Issue | Fix |
|---|-------|-----|
| 1 | Manager scope filters by actorName (string) not actorId | Change to actorId matching |
| 2 | API max 200 entries, no pagination | Add cursor-based pagination (lastEvaluatedKey) |
| 3 | Hardcoded ORG#acme in _calculate_agent_quality | Use db.ORG_PK constant |
| 4 | Insights cache in memory, lost on restart | Accept (low priority, scan is fast) |
| 5 | No scheduled scanning | Add optional auto-scan interval |
| 6 | Scan threshold inconsistency (>=2 vs documented 3) | Fix to >=3 |
| 7 | New event types not in frontend filter | Add all new types to filter + breakdown |
| 8 | "Analyze Memories" button is placeholder | Implement as Review Engine AI analysis |
| 9 | Review Engine not integrated | Full integration in this PRD |
| 10 | request_always_on + feedback in audit.py | Move to portal.py (cleanup) |

---

## 3. Solutions

### 3.1 Event System Enhancement

**Time-range filtering:**
```python
@router.get("/api/v1/audit/entries")
def get_audit_entries(
    limit: int = 50,
    eventType: Optional[str] = None,
    since: Optional[str] = None,     # ISO timestamp: only events after this
    before: Optional[str] = None,    # ISO timestamp: only events before this
    cursor: Optional[str] = None,    # pagination cursor (lastEvaluatedKey)
    authorization: str = Header(default=""),
):
```

**New event types in filter:**
```python
EVENT_TYPES = [
    "agent_invocation", "permission_denied", "guardrail_block",
    "config_change", "soul_change", "tool_permission_change",
    "runtime_config_change", "agent_refresh", "agent_deleted",
    "employee_deleted", "personal_soul_change", "kb_upload",
    "session_takeover", "session_returned", "always_on_request",
]
```

**Manager scope fix:**
```python
# Before: names_in_scope = {e["name"] for e in employees if ...}
# After:
ids_in_scope = {e["id"] for e in employees if e.get("departmentId") in scope}
ids_in_scope.add("system")
entries = [e for e in entries if e.get("actorId") in ids_in_scope]
```

### 3.2 AI-Powered Analysis (Review Engine Integration)

**Replace the 5 pattern-matching rules with real AI analysis:**

```python
@router.post("/api/v1/audit/ai-analyze")
def ai_analyze(authorization: str = Header(default="")):
    """Bedrock AI analysis of recent audit events.
    Reads last 200 AUDIT# entries, sends to Bedrock for anomaly detection."""
    require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=200)

    # Format events for AI
    event_summary = _format_events_for_ai(entries)

    # Call Bedrock
    bedrock = boto3.client("bedrock-runtime", region_name=GATEWAY_REGION)
    prompt = f"""Analyze the following AI agent platform audit events for security anomalies,
compliance issues, and optimization opportunities.

Events (last 200):
{event_summary}

For each finding, provide:
1. severity: critical/high/medium/low
2. category: security/compliance/optimization/anomaly
3. title: one-line summary
4. description: what was detected and why it matters
5. recommendation: specific action to take
6. affectedUsers: list of employee IDs involved

Respond in JSON format: {{"findings": [...]}}"""

    response = bedrock.invoke_model(
        modelId=os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0"),
        body=json.dumps({"messages": [{"role": "user", "content": prompt}]}),
    )
    # Parse and store findings as AUDIT# entries
    ...
```

**Keep existing pattern scan as fast pre-check (no Bedrock cost):**
```python
@router.post("/api/v1/audit/run-scan")  # existing, enhanced
def run_audit_scan():
    """Fast pattern scan (no LLM). Runs 7 checks against DynamoDB data."""
    # Enhanced with new checks:
    # 6. Pending SOUL/KB reviews not addressed within 24h
    # 7. Permission denial rate spike (>200% of 7-day average)
```

### 3.3 Review Queue

**Central place for all pending reviews:**

```python
@router.get("/api/v1/audit/review-queue")
def get_review_queue(authorization: str = Header(default="")):
    """Pending reviews: Personal SOUL changes, KB uploads, tool usage anomalies."""
    require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=200)

    pending = []
    for e in entries:
        if e.get("reviewStatus") == "pending" or \
           (e.get("eventType") in ("personal_soul_change", "kb_upload") and e.get("status") == "pending_review"):
            pending.append({
                **e,
                "reviewType": _classify_review(e),
                "age": _calculate_age(e.get("timestamp", "")),
            })

    pending.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"items": pending, "count": len(pending)}


@router.post("/api/v1/audit/review/{entry_id}/approve")
def approve_review(entry_id: str, authorization: str = Header(default="")):
    """Approve a pending review item."""
    user = require_role(authorization, roles=["admin"])
    # Update the AUDIT# entry
    _update_review_status(entry_id, "approved", user)
    return {"approved": True}


@router.post("/api/v1/audit/review/{entry_id}/reject")
def reject_review(entry_id: str, body: dict, authorization: str = Header(default="")):
    """Reject and optionally revert a pending review item."""
    user = require_role(authorization, roles=["admin"])
    reason = body.get("reason", "")
    revert = body.get("revert", False)

    _update_review_status(entry_id, "rejected", user, reason)

    if revert:
        # Revert Personal SOUL to last approved version (S3 versioning)
        _revert_to_last_approved(entry_id)

    return {"rejected": True, "reverted": revert}
```

### 3.4 Compliance Dashboard

```python
@router.get("/api/v1/audit/compliance-stats")
def get_compliance_stats(days: int = 7, authorization: str = Header(default="")):
    """Compliance trending over time."""
    require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=500)
    agents = db.get_agents()

    # Daily breakdown
    daily = {}
    for e in entries:
        date = e.get("timestamp", "")[:10]
        if date not in daily:
            daily[date] = {"total": 0, "blocked": 0, "success": 0}
        daily[date]["total"] += 1
        if e.get("status") == "blocked":
            daily[date]["blocked"] += 1
        else:
            daily[date]["success"] += 1

    # SOUL version compliance
    pos_versions = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv

    total_agents = len(agents)
    compliant = sum(1 for a in agents
                    if (a.get("soulVersions") or {}).get("position", 1) >= pos_versions.get(a.get("positionId", ""), 1))

    return {
        "daily": daily,
        "soulCompliance": {
            "compliant": compliant,
            "total": total_agents,
            "rate": round(compliant / max(1, total_agents) * 100, 1),
        },
        "enforcementRate": {
            "total": sum(d["total"] for d in daily.values()),
            "blocked": sum(d["blocked"] for d in daily.values()),
            "rate": round((1 - sum(d["blocked"] for d in daily.values()) / max(1, sum(d["total"] for d in daily.values()))) * 100, 1),
        },
    }
```

### 3.5 Action Buttons

```python
# These already exist in other modules, Audit Center calls them:

# From Monitor:
POST /api/v1/admin/refresh-all          → stop all agent sessions

# From audit.py (existing):
POST /api/v1/audit/run-scan             → fast pattern scan

# New in audit.py:
POST /api/v1/audit/ai-analyze           → Bedrock AI analysis
GET  /api/v1/audit/review-queue         → pending reviews
POST /api/v1/audit/review/{id}/approve  → approve
POST /api/v1/audit/review/{id}/reject   → reject + optional revert

# From settings (config sync check):
GET  /api/v1/monitor/agent-activity     → which agents are outdated
```

### 3.6 Code Cleanup

**Move misplaced endpoints out of audit.py:**
- `request_always_on()` → already duplicated concept in portal.py, remove from audit.py or keep as-is (low priority)
- `submit_feedback()` → belongs in portal.py conceptually, but moving it breaks frontend API paths. Keep with TODO comment.

---

## 4. Frontend Design

### Tab 1: AI Insights (Enhanced)

```
┌─────────────────────────────────────────────────────────┐
│ AI Security Scanner                                      │
│ Last scan: 2026-04-12 14:30  Sources: audit_log, agents │
│                                                         │
│ [Run Pattern Scan]  [AI Deep Analyze]  [Force Refresh]  │
│                     (Bedrock Nova)      (All Agents)     │
└─────────────────────────────────────────────────────────┘

Insight cards (from both pattern scan AND Bedrock AI):
  🔴 HIGH: 15 permission denials from Sarah (Finance)
     → [Adjust Permission] [View Details] [Dismiss]
  🟡 MEDIUM: Personal SOUL change for Mike pending 48h
     → [Approve] [Reject + Revert] [View Diff]
  🔵 LOW: 3 agents with no sessions this week
     → [Send Onboarding Nudge] [View Agents]
```

### Tab 2: Event Timeline (Enhanced)

```
Filters: [Event Type ▾] [Time Range ▾] [Search...] [Export CSV]
         Today | 7 days | 30 days | Custom

Event list with ALL new types:
  14:32  soul_change      Admin edited pos-sa SOUL (286 chars)
  14:28  tool_permission_change  shell added to pos-sde
  14:25  agent_refresh    Admin forced refresh for emp-mike
  ...

Pagination: [Previous] Page 1 of 12 [Next]
```

### Tab 3: Review Queue (NEW)

```
┌─────────────────────────────────────────────────────────┐
│ 3 items pending review                                   │
│                                                         │
│ 🟡 Personal SOUL change — Mike Johnson                   │
│    Changed 2h ago · +3 lines · "Added pricing promo"    │
│    AI Review: Low risk                                   │
│    [Approve] [Reject + Revert] [View Diff]              │
│                                                         │
│ 🔴 KB Upload — financial-report-q2.md                    │
│    Uploaded 1d ago · 45KB · Finance KB                   │
│    AI Review: Pending                                    │
│    [Run AI Scan] [Approve] [Delete]                     │
│                                                         │
│ 🟡 Tool Usage Anomaly — Sarah Chen                       │
│    15 shell attempts blocked in 24h                      │
│    AI Review: Medium risk — possible permission gap      │
│    [Adjust Permission] [Dismiss]                        │
└─────────────────────────────────────────────────────────┘
```

### Tab 4: Compliance (replaces Breakdown)

```
Enforcement Rate (7d trend chart)
  98.5% → 97.2% → 99.1% → ...

SOUL Version Compliance: 18/20 agents on latest (90%)
  [Force Refresh Drifted Agents]

Permission Denial Trend (7d bar chart)
  Mon: 3 | Tue: 5 | Wed: 15 ← spike | Thu: 2 | ...

Top Actors (existing, keep)
Event Type Distribution (existing, keep)
```

### Tab 5: Security (merge Guardrail into this)

```
Merge existing "Security Alerts" + "Guardrail Events" into one tab:
  - Permission denials (with action: [Adjust Permission])
  - Guardrail blocks (with detail: policy name, source INPUT/OUTPUT)
  - SOUL injection attempts (from Review Engine)
  - Policy enforcement summary stats
```

---

## 5. Implementation Plan

### Phase 1: Backend fixes (P0)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `audit.py` | Fix manager scope: actorName → actorId |
| 1.2 | `audit.py` | Add time-range params (since/before) to get_audit_entries |
| 1.3 | `audit.py` | Fix scan threshold >=2 → >=3 |
| 1.4 | `audit.py` | Fix hardcoded ORG#acme → db.ORG_PK |
| 1.5 | `audit.py` | Add new checks to _run_audit_scan: pending reviews >24h, permission spike |

### Phase 2: Review Engine + AI Analysis (P0)

| Task | File | Description |
|------|------|-------------|
| 2.1 | `audit.py` | New: GET /audit/review-queue |
| 2.2 | `audit.py` | New: POST /audit/review/{id}/approve |
| 2.3 | `audit.py` | New: POST /audit/review/{id}/reject (with optional revert) |
| 2.4 | `audit.py` | New: POST /audit/ai-analyze (Bedrock AI analysis) |
| 2.5 | `audit.py` | New: GET /audit/compliance-stats |
| 2.6 | `db.py` | New: update_audit_entry() for review status updates |

### Phase 3: Frontend (P1)

| Task | File | Description |
|------|------|-------------|
| 3.1 | `AuditLog.tsx` | Add all new eventTypes to filter dropdown |
| 3.2 | `AuditLog.tsx` | Add time-range selector (Today/7d/30d/Custom) |
| 3.3 | `AuditLog.tsx` | New: Review Queue tab |
| 3.4 | `AuditLog.tsx` | New: Compliance Dashboard tab (replaces Breakdown) |
| 3.5 | `AuditLog.tsx` | Merge Security + Guardrail into one tab |
| 3.6 | `AuditLog.tsx` | AI Insights: add "AI Deep Analyze" button calling /audit/ai-analyze |
| 3.7 | `AuditLog.tsx` | Action buttons on insights: Approve/Reject/Adjust/Refresh |

---

## 6. TODO

### Must-Do
- [ ] 1.1-1.5: Backend fixes (scope, time-range, threshold, ORG#acme, new scan checks)
- [ ] 2.1-2.6: Review Engine endpoints + AI analyze + compliance stats
- [ ] 3.1-3.7: Frontend updates (all 7 tasks)
- [ ] Unit tests for all new endpoints
- [ ] Update ui-guide.html Audit Center findings
- [ ] Add new eventTypes to seed data for demo

### Review Engine specific
- [ ] Personal SOUL review: reads PERSONAL_SOUL.md from S3, sends to Bedrock for injection scan
- [ ] KB upload review: reads uploaded .md content, scans for prompt injection patterns
- [ ] Tool usage anomaly: compares employee usage against position average
- [ ] Auto-approve threshold: risk_level <= "low" auto-approved (configurable)
- [ ] Auto-revert threshold: risk_level == "critical" auto-reverted

### Future
- [ ] Scheduled auto-scan (cron-style, every 30 min)
- [ ] Review Engine batch mode (10 reviews per Bedrock call)
- [ ] Compliance report export (PDF)
- [ ] Alert integration with IM channels (notify admin of critical findings)
- [ ] Immutable audit log (S3 Object Lock for compliance)
