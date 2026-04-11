# Design: Audit Center + Review Engine — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-audit-center.md, audit.py (364 lines), AuditLog.tsx (437 lines)

---

## File-by-File Change Design

### 1. audit.py — 12 changes

#### 1.1 Fix manager scope: actorName → actorId

```
MODIFY: get_audit_entries() lines 36-44

Before:
    names_in_scope = {e["name"] for e in employees if e.get("departmentId") in scope}
    names_in_scope.add("system")
    entries = [e for e in entries if e.get("actorName") in names_in_scope]

After:
    ids_in_scope = {e["id"] for e in employees if e.get("departmentId") in scope}
    ids_in_scope.update({"system", "Auto-Provision"})
    entries = [e for e in entries if e.get("actorId") in ids_in_scope]
```

#### 1.2 Add time-range + pagination params

```
MODIFY: get_audit_entries() signature

def get_audit_entries(
    limit: int = 50,
    eventType: Optional[str] = None,
    since: Optional[str] = None,
    before: Optional[str] = None,
    authorization: str = Header(default=""),
):
    ...
    if since:
        entries = [e for e in entries if e.get("timestamp", "") >= since]
    if before:
        entries = [e for e in entries if e.get("timestamp", "") <= before]
    ...
```

#### 1.3 Fix scan threshold

```
MODIFY: _run_audit_scan() line 71

Before: repeat_blockers = {k: v for k, v in by_actor.items() if len(v) >= 2}
After:  repeat_blockers = {k: v for k, v in by_actor.items() if len(v) >= 3}
```

#### 1.4 Fix hardcoded ORG#acme

```
MODIFY: _calculate_agent_quality() line 221

Before: ExpressionAttributeValues={":pk": "ORG#acme", ...}
After:  ExpressionAttributeValues={":pk": db.ORG_PK, ...}
```

#### 1.5 Add new scan checks to _run_audit_scan()

```
ADD after existing 5 checks:

    # 6. Pending reviews not addressed within 24h
    day_ago = (now - timedelta(hours=24)).isoformat()
    stale_reviews = [e for e in entries
                     if e.get("reviewStatus") == "pending"
                     and e.get("timestamp", "") < day_ago]
    if stale_reviews:
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "compliance",
            "title": f"{len(stale_reviews)} review(s) pending over 24h",
            "description": "SOUL changes or KB uploads awaiting admin review for over 24 hours.",
            "recommendation": "Go to Review Queue tab to approve or reject pending items.",
            "affectedUsers": list({e.get("actorId","") for e in stale_reviews})[:5],
            "detectedAt": now_str, "source": "review_queue_scan",
        })
        idx += 1

    # 7. Permission denial rate spike
    recent_denials = [e for e in entries if e.get("eventType") == "permission_denied"]
    if len(recent_denials) > 10:
        top_denied = {}
        for e in recent_denials:
            tool = ""
            detail = e.get("detail", "")
            for t in ["shell", "browser", "code_execution", "file_write"]:
                if t in detail.lower():
                    tool = t; break
            if tool:
                top_denied[tool] = top_denied.get(tool, 0) + 1
        top_tool = max(top_denied, key=top_denied.get) if top_denied else "various"
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "high", "category": "security",
            "title": f"{len(recent_denials)} permission denials detected",
            "description": f"High volume of permission denials. Most denied tool: {top_tool}. May indicate permission misconfiguration or unauthorized access attempts.",
            "recommendation": f"Review tool permissions for affected positions. Consider granting {top_tool} access if legitimate, or investigate unauthorized attempts.",
            "affectedUsers": list({e.get("actorId","") for e in recent_denials})[:5],
            "detectedAt": now_str, "source": "denial_spike_scan",
        })
        idx += 1
```

#### 1.6 NEW: Review Queue endpoints

```python
@router.get("/api/v1/audit/review-queue")
def get_review_queue(authorization: str = Header(default="")):
    """Pending reviews: SOUL changes, KB uploads, tool anomalies."""
    require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=200)
    now = datetime.now(timezone.utc)
    pending = []
    for e in entries:
        is_pending = (
            e.get("reviewStatus") == "pending"
            or (e.get("eventType") in ("personal_soul_change", "kb_upload")
                and e.get("status") in ("pending", "pending_review"))
        )
        if is_pending:
            ts = e.get("timestamp", "")
            age_hours = 0
            if ts:
                try:
                    age_hours = (now - datetime.fromisoformat(ts.replace("Z","+00:00"))).total_seconds() / 3600
                except: pass
            pending.append({**e, "ageHours": round(age_hours, 1)})
    pending.sort(key=lambda x: x.get("timestamp",""), reverse=True)
    return {"items": pending, "count": len(pending)}


@router.post("/api/v1/audit/review/{entry_id}/approve")
def approve_review(entry_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    _update_review_status(entry_id, "approved", user)
    return {"approved": True, "entryId": entry_id}


@router.post("/api/v1/audit/review/{entry_id}/reject")
def reject_review(entry_id: str, body: dict = {}, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    reason = body.get("reason", "")
    revert = body.get("revert", False)
    _update_review_status(entry_id, "rejected", user, reason)
    reverted = False
    if revert:
        reverted = _revert_to_last_approved(entry_id)
    return {"rejected": True, "entryId": entry_id, "reverted": reverted, "reason": reason}


def _update_review_status(entry_id: str, status: str, user, reason: str = ""):
    """Update reviewStatus on an AUDIT# entry."""
    try:
        ddb = _boto3_audit.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        update_expr = "SET reviewStatus = :s, reviewedBy = :by, reviewedAt = :at"
        expr_values = {
            ":s": status,
            ":by": user.employee_id,
            ":at": datetime.now(timezone.utc).isoformat(),
        }
        if reason:
            update_expr += ", reviewReason = :r"
            expr_values[":r"] = reason
        table.update_item(
            Key={"PK": db.ORG_PK, "SK": f"AUDIT#{entry_id}"},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )
    except Exception as e:
        raise HTTPException(500, f"Review update failed: {e}")


def _revert_to_last_approved(entry_id: str) -> bool:
    """Revert Personal SOUL to previous version using S3 versioning."""
    # TODO: implement S3 version rollback when S3 versioning is enabled
    # For now, log the intent
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "soul_reverted",
        "actorId": "system",
        "actorName": "Review Engine",
        "targetType": "soul",
        "targetId": entry_id,
        "detail": f"Revert requested for {entry_id} (S3 versioning rollback pending)",
        "status": "pending",
    })
    return False  # TODO: return True when S3 rollback implemented
```

#### 1.7 NEW: AI Analysis endpoint

```python
@router.post("/api/v1/audit/ai-analyze")
def ai_analyze(authorization: str = Header(default="")):
    """Bedrock AI analysis of recent audit events."""
    user = require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=200)

    # Format for AI
    event_lines = []
    for e in entries[:100]:
        event_lines.append(
            f"[{e.get('timestamp','')}] {e.get('eventType','')} "
            f"actor={e.get('actorName','')} target={e.get('targetId','')} "
            f"status={e.get('status','')} detail={e.get('detail','')[:100]}"
        )
    event_text = "\n".join(event_lines)

    prompt = (
        "Analyze the following AI agent platform audit events for security anomalies, "
        "compliance issues, and optimization opportunities.\n\n"
        f"Events:\n{event_text}\n\n"
        "For each finding, respond with JSON:\n"
        '{"findings": [{"severity":"high/medium/low", "category":"security/compliance/optimization", '
        '"title":"...", "description":"...", "recommendation":"...", "affectedUsers":["emp-..."]}]}\n'
        "Only include genuine findings. If nothing unusual, return empty findings array."
    )

    try:
        bedrock = _boto3_audit.client("bedrock-runtime", region_name=GATEWAY_REGION)
        model_id = os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
        response = bedrock.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 2000},
        )
        ai_text = response["output"]["message"]["content"][0]["text"]
        # Parse JSON from response
        import json as _json_ai
        # Find JSON in response
        json_start = ai_text.find("{")
        json_end = ai_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            ai_findings = _json_ai.loads(ai_text[json_start:json_end])
        else:
            ai_findings = {"findings": []}

        # Store as audit insights
        global _audit_scan_cache
        pattern_insights = _audit_scan_cache.get("insights", []) if _audit_scan_cache else []
        ai_insights = []
        for i, f in enumerate(ai_findings.get("findings", [])):
            ai_insights.append({
                "id": f"ai-{i:03d}",
                "severity": f.get("severity", "medium"),
                "category": f.get("category", "security"),
                "title": f.get("title", "AI Finding"),
                "description": f.get("description", ""),
                "recommendation": f.get("recommendation", ""),
                "affectedUsers": f.get("affectedUsers", []),
                "detectedAt": datetime.now(timezone.utc).isoformat(),
                "source": "bedrock_ai_analysis",
            })

        combined = pattern_insights + ai_insights
        result = {
            "insights": combined,
            "summary": {
                "totalInsights": len(combined),
                "high": len([i for i in combined if i["severity"] == "high"]),
                "medium": len([i for i in combined if i["severity"] == "medium"]),
                "low": len([i for i in combined if i["severity"] == "low"]),
                "lastScanAt": datetime.now(timezone.utc).isoformat(),
                "scanSources": ["pattern_scan", "bedrock_ai"],
                "modelUsed": model_id,
            },
        }
        _audit_scan_cache = result
        return result

    except Exception as e:
        raise HTTPException(500, f"AI analysis failed: {e}")
```

#### 1.8 NEW: Compliance stats

```python
@router.get("/api/v1/audit/compliance-stats")
def get_compliance_stats(days: int = 7, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=500)
    agents = db.get_agents()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()

    # Daily enforcement breakdown
    daily = {}
    for e in entries:
        ts = e.get("timestamp", "")
        if ts < cutoff:
            continue
        date = ts[:10]
        if date not in daily:
            daily[date] = {"total": 0, "blocked": 0, "success": 0, "config": 0}
        daily[date]["total"] += 1
        if e.get("status") == "blocked":
            daily[date]["blocked"] += 1
        elif e.get("status") == "success":
            daily[date]["success"] += 1
        if e.get("eventType") in ("config_change", "soul_change", "tool_permission_change"):
            daily[date]["config"] += 1

    # SOUL version compliance
    pos_versions = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv
    compliant = sum(1 for a in agents
        if (a.get("soulVersions") or {}).get("position", 1) >= pos_versions.get(a.get("positionId",""), 1))

    total_events = sum(d["total"] for d in daily.values())
    total_blocked = sum(d["blocked"] for d in daily.values())

    return {
        "daily": daily,
        "soulCompliance": {"compliant": compliant, "total": len(agents),
            "rate": round(compliant / max(1, len(agents)) * 100, 1)},
        "enforcementRate": {"total": total_events, "blocked": total_blocked,
            "rate": round((1 - total_blocked / max(1, total_events)) * 100, 1)},
        "pendingReviews": len([e for e in entries if e.get("reviewStatus") == "pending"]),
    }
```

### 2. db.py — add helper

```python
def update_audit_entry(entry_id: str, updates: dict) -> bool:
    """Update fields on an AUDIT# entry."""
    item = _get_item(f"AUDIT#{entry_id}")
    if not item:
        return False
    item.update(updates)
    _put_item(f"AUDIT#{entry_id}", item, "TYPE#audit", f"AUDIT#{entry_id}")
    return True
```

---

## Unit Test Plan

```
test_audit_center.py:

1. test_scope_uses_actor_id:
   Scan get_audit_entries for "actorId" in scope filter → must exist
   Scan for "actorName" in scope filter → must NOT exist

2. test_time_range_params:
   Scan get_audit_entries for "since" and "before" parameters → must exist

3. test_scan_threshold_3:
   Scan _run_audit_scan for ">= 3" → must exist
   Scan for ">= 2" in blocker context → must NOT exist

4. test_no_hardcoded_org:
   Scan _calculate_agent_quality for "ORG#acme" → must NOT exist
   Scan for "db.ORG_PK" → must exist

5. test_review_queue_exists:
   Scan for "def get_review_queue" → must exist

6. test_approve_reject_exist:
   Scan for "def approve_review" and "def reject_review" → must exist

7. test_ai_analyze_exists:
   Scan for "def ai_analyze" → must exist
   Scan for "bedrock" in ai_analyze → must exist (calls Bedrock)

8. test_compliance_stats_exists:
   Scan for "def get_compliance_stats" → must exist

9. test_scan_has_new_checks:
   Scan _run_audit_scan for "pending" and "denial" → must exist (new check 6 + 7)

10. test_new_event_types_defined:
    Scan audit.py for "soul_change" and "tool_permission_change" → must exist
```

---

## Migration Notes

- No new DynamoDB tables (uses existing AUDIT# with new fields: reviewStatus, reviewedBy, reviewedAt)
- Bedrock AI analysis requires `bedrock:InvokeModel` / `bedrock:Converse` in EC2 IAM role (already present for agent invocations)
- Frontend changes require npm build + deploy
- No deploy.sh changes
