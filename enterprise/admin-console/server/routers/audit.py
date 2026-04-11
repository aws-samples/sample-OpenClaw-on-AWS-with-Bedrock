"""
Audit Center + Review Engine.

Three layers: See (event history) → Understand (AI analysis) → Act (approve/reject/revert).

Endpoints:
  /api/v1/audit/entries          — event timeline with filtering
  /api/v1/audit/insights         — cached scan results
  /api/v1/audit/run-scan         — fast pattern scan (no LLM)
  /api/v1/audit/ai-analyze       — Bedrock AI deep analysis
  /api/v1/audit/review-queue     — pending reviews
  /api/v1/audit/review/{id}/*    — approve/reject
  /api/v1/audit/compliance-stats — compliance trending
  /api/v1/agents/{id}/quality    — agent quality score
  /api/v1/audit/guardrail-events — Bedrock guardrail blocks
  /api/v1/portal/request-always-on
  /api/v1/portal/feedback
"""

import os
import re
import json
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

import boto3 as _boto3_audit
from fastapi import APIRouter, HTTPException, Header

import db
from shared import require_auth, require_role, get_dept_scope, GATEWAY_REGION, STACK_NAME

router = APIRouter(tags=["audit"])


# =========================================================================
# Event Timeline — with time-range filtering
# =========================================================================

@router.get("/api/v1/audit/entries")
def get_audit_entries(
    limit: int = 50,
    eventType: Optional[str] = None,
    since: Optional[str] = None,
    before: Optional[str] = None,
    authorization: str = Header(default=""),
):
    """Audit event timeline with filtering by type, time range, and department scope."""
    user = require_auth(authorization)
    limit = min(limit, 500)
    entries = db.get_audit_entries(limit=limit)

    if eventType:
        entries = [e for e in entries if e.get("eventType") == eventType]
    if since:
        entries = [e for e in entries if e.get("timestamp", "") >= since]
    if before:
        entries = [e for e in entries if e.get("timestamp", "") <= before]

    # Scope for managers — filter by actorId (not actorName)
    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            ids_in_scope = {e["id"] for e in employees if e.get("departmentId") in scope}
            ids_in_scope.update({"system", "Auto-Provision"})
            entries = [e for e in entries if e.get("actorId") in ids_in_scope]

    return entries


# =========================================================================
# Pattern Scan — fast, no LLM cost
# =========================================================================

def _run_audit_scan() -> dict:
    """Generate insights from live DynamoDB data. Pattern-based (no LLM)."""
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    entries = db.get_audit_entries(limit=200)
    agents = db.get_agents()
    employees = db.get_employees()
    sessions = db.get_sessions()
    insights = []
    idx = 0

    # 1. Repeated permission denials
    blocked = [e for e in entries if e.get("status") == "blocked"
               or e.get("eventType") == "permission_denied"]
    if blocked:
        by_actor: dict = {}
        for e in blocked:
            actor = e.get("actorId", e.get("actorName", "unknown"))
            by_actor.setdefault(actor, []).append(e)
        repeat_blockers = {k: v for k, v in by_actor.items() if len(v) >= 3}
        if repeat_blockers:
            actor_ids = list(repeat_blockers.keys())[:5]
            total = sum(len(v) for v in repeat_blockers.values())
            top_tool = ""
            for e in blocked:
                m = re.search(r'(shell|browser|code_execution|file_write)', e.get("detail", ""), re.I)
                if m:
                    top_tool = m.group(1)
                    break
            insights.append({
                "id": f"ins-{idx:03d}", "severity": "high", "category": "access_pattern",
                "title": f"{total} permission denials — {len(repeat_blockers)} repeat offenders",
                "description": f"Detected {total} blocked operations across {len(repeat_blockers)} actors. Top blocked tool: {top_tool or 'various'}.",
                "recommendation": "Review tool permissions for affected positions. Consider permission escalation workflow.",
                "affectedUsers": actor_ids,
                "detectedAt": now_str, "source": "audit_log_scan",
            })
            idx += 1

    # 2. SOUL version drift
    pos_versions: dict = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv
    drifted = []
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos in pos_versions and sv < pos_versions[pos]:
            emp = next((e for e in employees if e.get("id") == a.get("employeeId")), {})
            drifted.append(emp.get("name", a.get("employeeName", a["id"])))
    if drifted:
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "high", "category": "compliance",
            "title": f"SOUL version drift — {len(drifted)} agent(s) behind",
            "description": f"{len(drifted)} agent(s) running outdated position SOUL templates.",
            "recommendation": "Use Force Refresh to trigger workspace reassembly for affected agents.",
            "affectedUsers": drifted[:5],
            "detectedAt": now_str, "source": "version_drift_check",
        })
        idx += 1

    # 3. Zero-turn agents
    agents_with_sessions = {s.get("agentId") for s in sessions}
    unengaged = [a for a in agents if a.get("id") not in agents_with_sessions and a.get("employeeId")]
    if unengaged:
        names = [next((e.get("name", "") for e in employees if e.get("id") == a.get("employeeId")), "") for a in unengaged[:5]]
        names = [n for n in names if n]
        if names:
            insights.append({
                "id": f"ins-{idx:03d}", "severity": "low", "category": "optimization",
                "title": f"{len(unengaged)} agent(s) with no sessions",
                "description": f"{len(unengaged)} agents have never been used. Low engagement reduces ROI.",
                "recommendation": "Verify IM channel bindings and send onboarding nudge to affected employees.",
                "affectedUsers": names,
                "detectedAt": now_str, "source": "engagement_analysis",
            })
            idx += 1

    # 4. Config change spike
    config_changes = [e for e in entries if e.get("eventType") in
                      ("config_change", "soul_change", "tool_permission_change", "runtime_config_change")]
    if len(config_changes) >= 5:
        changers = list({e.get("actorId", "") for e in config_changes})[:3]
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "compliance",
            "title": f"{len(config_changes)} configuration changes detected",
            "description": "High change velocity may introduce policy inconsistencies.",
            "recommendation": "Review recent changes in Event Timeline filtered by config events.",
            "affectedUsers": changers,
            "detectedAt": now_str, "source": "audit_log_scan",
        })
        idx += 1

    # 5. Unbound agents
    bound_agent_ids = {b.get("agentId") for b in (db.get_bindings() if hasattr(db, "get_bindings") else [])}
    unbound_agents = [a for a in agents if a.get("employeeId") and a.get("id") not in bound_agent_ids]
    if unbound_agents:
        names_ub = [a.get("employeeName", a.get("id", "")) for a in unbound_agents[:3]]
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "optimization",
            "title": f"{len(unbound_agents)} agent(s) without IM binding",
            "description": f"{len(unbound_agents)} agents have no IM channel binding.",
            "recommendation": "Create bindings or use Bulk Provision by Position.",
            "affectedUsers": names_ub,
            "detectedAt": now_str, "source": "binding_scan",
        })
        idx += 1

    # 6. Pending reviews not addressed within 24h
    day_ago = (now - timedelta(hours=24)).isoformat()
    stale_reviews = [e for e in entries
                     if (e.get("reviewStatus") == "pending"
                         or (e.get("eventType") in ("personal_soul_change", "kb_upload")
                             and e.get("status") in ("pending", "pending_review")))
                     and e.get("timestamp", "") < day_ago]
    if stale_reviews:
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "compliance",
            "title": f"{len(stale_reviews)} review(s) pending over 24h",
            "description": "SOUL changes or KB uploads awaiting admin review for over 24 hours.",
            "recommendation": "Go to Review Queue to approve or reject pending items.",
            "affectedUsers": list({e.get("actorId", "") for e in stale_reviews})[:5],
            "detectedAt": now_str, "source": "review_queue_scan",
        })
        idx += 1

    # 7. Permission denial spike
    recent_denials = [e for e in entries if e.get("eventType") == "permission_denied"]
    if len(recent_denials) > 10:
        top_denied: dict = {}
        for e in recent_denials:
            for t in ["shell", "browser", "code_execution", "file_write"]:
                if t in e.get("detail", "").lower():
                    top_denied[t] = top_denied.get(t, 0) + 1
                    break
        top_tool = max(top_denied, key=top_denied.get) if top_denied else "various"
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "high", "category": "security",
            "title": f"{len(recent_denials)} permission denials — possible misconfiguration",
            "description": f"High denial volume. Most denied: {top_tool}. May indicate permission gap or unauthorized attempts.",
            "recommendation": f"Review {top_tool} permissions for affected positions or investigate access attempts.",
            "affectedUsers": list({e.get("actorId", "") for e in recent_denials})[:5],
            "detectedAt": now_str, "source": "denial_spike_scan",
        })
        idx += 1

    return {
        "insights": insights,
        "summary": {
            "totalInsights": len(insights),
            "high": len([i for i in insights if i["severity"] == "high"]),
            "medium": len([i for i in insights if i["severity"] == "medium"]),
            "low": len([i for i in insights if i["severity"] == "low"]),
            "lastScanAt": now_str,
            "scanSources": ["audit_log", "agent_versions", "sessions", "bindings", "review_queue", "denial_rate"],
        }
    }


_audit_scan_cache: dict = {}


@router.get("/api/v1/audit/insights")
def get_audit_insights():
    global _audit_scan_cache
    if not _audit_scan_cache:
        _audit_scan_cache = _run_audit_scan()
    return _audit_scan_cache


@router.post("/api/v1/audit/run-scan")
def run_audit_scan():
    global _audit_scan_cache
    _audit_scan_cache = _run_audit_scan()
    return _audit_scan_cache


# =========================================================================
# AI Deep Analysis — Bedrock-powered
# =========================================================================

@router.post("/api/v1/audit/ai-analyze")
def ai_analyze(authorization: str = Header(default="")):
    """Bedrock AI analysis of recent audit events."""
    user = require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=200)

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

        json_start = ai_text.find("{")
        json_end = ai_text.rfind("}") + 1
        ai_findings = json.loads(ai_text[json_start:json_end]) if json_start >= 0 and json_end > json_start else {"findings": []}

        global _audit_scan_cache
        pattern_insights = _audit_scan_cache.get("insights", []) if _audit_scan_cache else _run_audit_scan().get("insights", [])
        ai_insights = []
        for i, f in enumerate(ai_findings.get("findings", [])):
            ai_insights.append({
                "id": f"ai-{i:03d}", "severity": f.get("severity", "medium"),
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


# =========================================================================
# Review Queue — pending SOUL/KB/anomaly reviews
# =========================================================================

@router.get("/api/v1/audit/review-queue")
def get_review_queue(authorization: str = Header(default="")):
    """Pending reviews: Personal SOUL changes, KB uploads, anomalies."""
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
                    age_hours = (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 3600
                except Exception:
                    pass
            pending.append({**e, "ageHours": round(age_hours, 1)})
    pending.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"items": pending, "count": len(pending)}


def _update_review_status(entry_id: str, status: str, user, reason: str = ""):
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


@router.post("/api/v1/audit/review/{entry_id}/approve")
def approve_review(entry_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    _update_review_status(entry_id, "approved", user)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "review_decision", "actorId": user.employee_id,
        "actorName": user.name, "targetType": "review", "targetId": entry_id,
        "detail": f"Admin approved review {entry_id}", "status": "success",
    })
    return {"approved": True, "entryId": entry_id}


@router.post("/api/v1/audit/review/{entry_id}/reject")
def reject_review(entry_id: str, body: dict = {}, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    reason = body.get("reason", "")
    revert = body.get("revert", False)
    _update_review_status(entry_id, "rejected", user, reason)
    reverted = False
    if revert:
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "soul_reverted", "actorId": "system",
            "actorName": "Review Engine", "targetType": "soul", "targetId": entry_id,
            "detail": f"Revert requested for {entry_id} (pending S3 versioning rollback)",
            "status": "pending",
        })
    return {"rejected": True, "entryId": entry_id, "reverted": reverted, "reason": reason}


# =========================================================================
# Compliance Stats
# =========================================================================

@router.get("/api/v1/audit/compliance-stats")
def get_compliance_stats(days: int = 7, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    entries = db.get_audit_entries(limit=500)
    agents = db.get_agents()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()

    daily: dict = {}
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
    pos_versions: dict = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv
    compliant = sum(1 for a in agents
        if (a.get("soulVersions") or {}).get("position", 1) >= pos_versions.get(a.get("positionId", ""), 1))

    total_events = sum(d["total"] for d in daily.values())
    total_blocked = sum(d["blocked"] for d in daily.values())
    pending_reviews = len([e for e in entries if e.get("reviewStatus") == "pending"
                           or (e.get("eventType") in ("personal_soul_change", "kb_upload")
                               and e.get("status") in ("pending", "pending_review"))])

    return {
        "daily": daily,
        "soulCompliance": {"compliant": compliant, "total": len(agents),
            "rate": round(compliant / max(1, len(agents)) * 100, 1)},
        "enforcementRate": {"total": total_events, "blocked": total_blocked,
            "rate": round((1 - total_blocked / max(1, total_events)) * 100, 1)},
        "pendingReviews": pending_reviews,
    }


# =========================================================================
# Guardrail Events
# =========================================================================

@router.get("/api/v1/audit/guardrail-events")
def get_guardrail_events(authorization: str = Header(default=""), limit: int = 50):
    """Fetch guardrail_block audit events from DynamoDB."""
    require_role(authorization, roles=["admin", "manager"])
    try:
        from boto3.dynamodb.conditions import Key
        table = _boto3_audit.resource("dynamodb", region_name=db.AWS_REGION).Table(db.TABLE_NAME)
        resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq("TYPE#audit"),
            ScanIndexForward=False, Limit=limit * 5,
        )
        events = [item for item in resp.get("Items", []) if item.get("eventType") == "guardrail_block"]
        events = events[:limit]
        for e in events:
            e.pop("PK", None); e.pop("SK", None)
            e.pop("GSI1PK", None); e.pop("GSI1SK", None)
        return {"events": events}
    except Exception as e:
        return {"events": [], "error": str(e)}


# =========================================================================
# Agent Quality Score — real data from DynamoDB
# =========================================================================

def _calculate_agent_quality(agent_id: str) -> dict:
    """Calculate quality score from AUDIT#, SESSION#, FEEDBACK# data."""
    try:
        ddb = _boto3_audit.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)

        audit_resp = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": db.ORG_PK, ":sk": "AUDIT#"},
            ScanIndexForward=False, Limit=200)
        agent_audits = [a for a in audit_resp.get("Items", []) if a.get("targetId") == agent_id]
        invocations = [a for a in agent_audits if a.get("eventType") == "agent_invocation"]
        permission_denials = [a for a in agent_audits if a.get("eventType") == "permission_denied"]
        tool_success = 1.0 if not invocations else sum(
            1 for a in invocations if a.get("status") == "success") / max(1, len(invocations))
        compliance = 1.0 if not invocations else max(0, 1 - len(permission_denials) / max(1, len(invocations)))

        sessions = [s for s in db.get_sessions() if s.get("agentId") == agent_id]
        durations = [float(s["durationMs"]) for s in sessions if s.get("durationMs")]
        if durations:
            p75 = sorted(durations)[int(len(durations) * 0.75)]
            response_score = min(1.0, max(0, 1.0 - (p75 - 3000) / 12000))
        else:
            response_score = 0.7

        multi_turn = [s for s in sessions if int(s.get("turns", 0)) > 1]
        completion = len(multi_turn) / max(1, len(sessions)) if sessions else 0.7

        feedback_resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND begins_with(GSI1SK, :sk)",
            ExpressionAttributeValues={":pk": "TYPE#feedback", ":sk": f"FEEDBACK#{agent_id}"},
            Limit=100)
        feedbacks = feedback_resp.get("Items", [])
        if feedbacks:
            positive = sum(1 for f in feedbacks if f.get("rating") == "up")
            satisfaction = positive / len(feedbacks)
        else:
            satisfaction = tool_success * 0.9 + 0.1

        score = round(
            0.3 * satisfaction + 0.2 * tool_success +
            0.2 * response_score + 0.2 * compliance + 0.1 * completion, 2)

        return {
            "score": round(score * 5, 1),
            "breakdown": {
                "satisfaction": round(satisfaction * 5, 1),
                "toolSuccess": round(tool_success * 5, 1),
                "responseTime": round(response_score * 5, 1),
                "compliance": round(compliance * 5, 1),
                "completion": round(completion * 5, 1),
            },
            "dataPoints": {"invocations": len(invocations), "sessions": len(sessions), "feedbacks": len(feedbacks)},
        }
    except Exception as e:
        return {"score": None, "error": str(e)}


@router.get("/api/v1/agents/{agent_id}/quality")
def get_agent_quality(agent_id: str, authorization: str = Header(default="")):
    require_auth(authorization)
    return _calculate_agent_quality(agent_id)


# =========================================================================
# Portal endpoints (kept here for backward compat)
# =========================================================================

@router.post("/api/v1/portal/request-always-on")
def request_always_on(body: dict, authorization: str = Header(default="")):
    """Employee requests always-on mode."""
    user = require_auth(authorization)
    reason = body.get("reason", "").strip() or "Employee-initiated request"
    stack = os.environ.get("STACK_NAME", "openclaw")
    try:
        ssm_chk = _boto3_audit.client("ssm", region_name=GATEWAY_REGION)
        ssm_chk.get_parameter(Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
        raise HTTPException(400, "Already in always-on mode")
    except HTTPException:
        raise
    except Exception:
        pass
    approval_id = f"apr-alwayson-{user.employee_id}"
    db.create_approval({
        "id": approval_id, "type": "always_on_request",
        "requestedBy": user.employee_id, "requestedByName": user.name,
        "reason": reason, "status": "pending",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "details": {"employeeId": user.employee_id, "currentMode": "serverless", "requestedMode": "always-on-ecs"},
    })
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "always_on_request", "actorId": user.employee_id,
        "actorName": user.name, "targetType": "agent", "targetId": user.employee_id,
        "detail": f"Employee requested always-on mode: {reason}", "status": "pending",
    })
    return {"requested": True, "approvalId": approval_id}


@router.post("/api/v1/portal/feedback")
def submit_feedback(body: dict, authorization: str = Header(default="")):
    """Employee thumbs up/down feedback."""
    user = require_auth(authorization)
    session_id = body.get("sessionId", "")
    turn_seq = body.get("turnSeq", 0)
    rating = body.get("rating", "")
    agent_id = body.get("agentId", "")
    if rating not in ("up", "down"):
        raise HTTPException(400, "rating must be 'up' or 'down'")
    try:
        ddb = _boto3_audit.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        fid = f"{session_id}#{turn_seq:04d}"
        table.put_item(Item={
            "PK": db.ORG_PK, "SK": f"FEEDBACK#{fid}",
            "GSI1PK": "TYPE#feedback", "GSI1SK": f"FEEDBACK#{agent_id}#{fid}",
            "sessionId": session_id, "turnSeq": Decimal(str(turn_seq)),
            "rating": rating, "employeeId": user.employee_id,
            "agentId": agent_id, "ts": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        raise HTTPException(500, f"Failed to save feedback: {e}")
    return {"saved": True, "rating": rating}
