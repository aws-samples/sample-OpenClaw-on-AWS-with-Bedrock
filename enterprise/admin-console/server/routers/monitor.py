"""
Monitor Center — Pure DynamoDB. No CloudWatch, no SSM.

Provides: action items, system status, event stream, agent activity,
session management, takeover (DynamoDB TTL), alerts (real data only).

Endpoints: /api/v1/monitor/*
"""

import os
import re
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3

from fastapi import APIRouter, HTTPException, Header

import db
from shared import (
    require_auth,
    require_role,
    get_dept_scope,
    stop_employee_session,
    GATEWAY_REGION,
    STACK_NAME,
)
from routers.usage import usage_budgets, _get_agent_usage_recent

router = APIRouter(tags=["monitor"])


# ── Auth helper ────────────────────────────────────────────────────────
import auth as _authmod


def _get_current_user(authorization: str) -> _authmod.UserContext | None:
    return _authmod.get_user_from_request(authorization)


# ── Server start time ─────────────────────────────────────────────────
_SERVER_START_TIME = time.time()


def _format_uptime(seconds: float) -> str:
    secs = int(seconds)
    days, remainder = divmod(secs, 86400)
    hours, remainder = divmod(remainder, 3600)
    mins = remainder // 60
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


# =========================================================================
# System Status — background health check (cached 30s)
# =========================================================================

_system_status_cache = {"data": {}, "expires": 0}


def _check_services():
    """Check all 4 services + Bedrock connectivity."""
    import urllib.request
    services = {}
    for name, port in [("admin-console", 8099), ("tenant-router", 8090),
                        ("h2-proxy", 8091), ("gateway", 18789)]:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/", timeout=2)
            services[name] = "healthy"
        except Exception:
            services[name] = "unreachable"
    try:
        boto3.client("bedrock", region_name=os.environ.get("AWS_REGION", "us-east-1")).list_foundation_models(maxResults=1)
        services["bedrock"] = "connected"
    except Exception:
        services["bedrock"] = "unreachable"
    try:
        ac = boto3.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)
        runtimes = ac.list_agent_runtimes().get("agentRuntimes", [])
        services["agentcore"] = f"{len(runtimes)} runtimes"
    except Exception:
        services["agentcore"] = "unknown"
    services["uptime"] = _format_uptime(time.time() - _SERVER_START_TIME)
    _system_status_cache["data"] = services
    _system_status_cache["expires"] = time.time() + 30


def _health_worker():
    while True:
        try:
            _check_services()
        except Exception:
            pass
        time.sleep(30)

threading.Thread(target=_health_worker, daemon=True).start()


@router.get("/api/v1/monitor/system-status")
def get_system_status():
    """Service health for all platform components."""
    if not _system_status_cache["data"]:
        _check_services()
    return _system_status_cache["data"]


# =========================================================================
# Action Items — aggregated pending items
# =========================================================================

@router.get("/api/v1/monitor/action-items")
def get_action_items():
    """Aggregated pending items across all modules."""
    entries = db.get_audit_entries(limit=200)
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(hours=24)).isoformat()
    items = []

    # Pending reviews (SOUL/KB)
    pending = [e for e in entries if e.get("reviewStatus") == "pending"
               or (e.get("eventType") == "personal_soul_change" and e.get("status") == "pending")]
    if pending:
        items.append({"type": "review", "severity": "warning",
                       "message": f"{len(pending)} changes pending review", "count": len(pending)})

    # Permission denials (24h)
    denials = [e for e in entries if e.get("eventType") == "permission_denied"
               and e.get("timestamp", "") >= day_ago]
    if denials:
        items.append({"type": "security", "severity": "warning" if len(denials) > 10 else "info",
                       "message": f"{len(denials)} permission denials in last 24h", "count": len(denials)})

    # Guardrail blocks (24h)
    blocks = [e for e in entries if e.get("eventType") == "guardrail_block"
              and e.get("timestamp", "") >= day_ago]
    if blocks:
        items.append({"type": "security", "severity": "warning",
                       "message": f"{len(blocks)} guardrail blocks in last 24h", "count": len(blocks)})

    # Budget
    try:
        budgets = usage_budgets()
        over = [b for b in budgets if b["status"] in ("over", "warning")]
        if over:
            items.append({"type": "budget", "severity": "warning",
                           "message": f"{len(over)} departments over/near budget", "count": len(over)})
    except Exception:
        pass

    # Unbound employees
    unbound = [e for e in db.get_employees() if not e.get("agentId")]
    if unbound:
        items.append({"type": "lifecycle", "severity": "info",
                       "message": f"{len(unbound)} employees without agents", "count": len(unbound)})

    return items


# =========================================================================
# Sessions — DynamoDB only, no CloudWatch
# =========================================================================

@router.get("/api/v1/monitor/sessions")
def get_sessions(authorization: str = Header(default="")):
    """Sessions from DynamoDB only. Status from lastActive timestamp."""
    user = _get_current_user(authorization)
    db_sessions = db.get_sessions()
    employees = db.get_employees()
    agents_list = db.get_agents()
    emp_map = {e["id"]: e for e in employees}
    agent_by_emp = {a.get("employeeId", ""): a for a in agents_list if a.get("employeeId")}
    now = datetime.now(timezone.utc)

    enriched = []
    for s in db_sessions:
        eid = s.get("employeeId", "")
        if not eid or eid == "unknown":
            continue
        emp = emp_map.get(eid)
        if not emp:
            continue
        agent = agent_by_emp.get(emp["id"])
        s["employeeName"] = emp["name"]
        s["agentId"] = agent["id"] if agent else s.get("agentId", "")
        s["agentName"] = agent["name"] if agent else ""
        if not s.get("channel") or s["channel"] == "unknown":
            s["channel"] = (emp.get("channels") or ["portal"])[0]

        last_active = s.get("lastActive", s.get("startedAt", ""))
        if last_active:
            try:
                la_time = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
                age_min = (now - la_time).total_seconds() / 60
                s["status"] = "active" if age_min < 15 else "idle" if age_min < 60 else "completed"
            except Exception:
                s["status"] = "completed"
        else:
            s["status"] = "completed"

        if not s.get("startedAt"):
            s["startedAt"] = last_active or ""
        enriched.append(s)

    status_order = {"active": 0, "idle": 1, "completed": 2}
    enriched.sort(key=lambda s: (status_order.get(s.get("status", "completed"), 3), -(s.get("turns", 0))))

    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            emp_ids = {e["id"] for e in employees if e.get("departmentId") in scope}
            enriched = [s for s in enriched if s.get("employeeId") in emp_ids]
    return enriched


# =========================================================================
# Takeover — DynamoDB with TTL (no SSM)
# =========================================================================

@router.post("/api/v1/monitor/sessions/{session_id}/takeover")
def takeover_session(session_id: str, authorization: str = Header(default="")):
    """Admin takes over a session. DynamoDB with 30-min TTL auto-expiration."""
    user = require_role(authorization, roles=["admin"])
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    try:
        ddb = boto3.resource("dynamodb", region_name=db.AWS_REGION)
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
    """Admin returns session to agent."""
    user = require_role(authorization, roles=["admin"])
    try:
        ddb = boto3.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        table.update_item(
            Key={"PK": "ORG#acme", "SK": f"SESSION#{session_id}"},
            UpdateExpression="REMOVE takeover, takeoverBy, takeoverExpiresAt, takeoverTTL",
        )
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "session_returned",
            "actorId": user.employee_id, "actorName": user.name,
            "targetType": "session", "targetId": session_id,
            "detail": f"Admin {user.name} returned session {session_id} to agent",
            "status": "success",
        })
    except Exception as e:
        raise HTTPException(500, f"Return failed: {e}")
    return {"returned": True, "sessionId": session_id}


@router.post("/api/v1/monitor/sessions/{session_id}/send")
def admin_send_message(session_id: str, body: dict, authorization: str = Header(default="")):
    """Admin sends a message while in takeover mode."""
    user = require_role(authorization, roles=["admin"])
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(400, "message required")

    # Verify takeover is active via DynamoDB
    session = db.get_session(session_id)
    if not session or not session.get("takeover"):
        raise HTTPException(400, "Session is not in takeover mode")
    expires = session.get("takeoverExpiresAt", "")
    if expires:
        try:
            if datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                raise HTTPException(400, "Takeover expired")
        except HTTPException:
            raise
        except Exception:
            pass

    try:
        ddb = boto3.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        ts = datetime.now(timezone.utc).isoformat()
        table.put_item(Item={
            "PK": "ORG#acme", "SK": f"CONV#{session_id}#admin#{int(time.time())}",
            "sessionId": session_id, "role": "admin", "content": message,
            "ts": ts, "source": "human_admin", "adminId": user.employee_id,
        })
    except Exception as e:
        raise HTTPException(500, f"Message storage failed: {e}")

    return {"sent": True, "message": message, "adminId": user.employee_id, "humanAssisted": True}


@router.get("/api/v1/monitor/sessions/{session_id}/takeover")
def get_takeover_status(session_id: str, authorization: str = Header(default="")):
    """Check if a session is in takeover mode (DynamoDB)."""
    require_auth(authorization)
    session = db.get_session(session_id)
    if not session or not session.get("takeover"):
        return {"active": False, "sessionId": session_id}
    expires = session.get("takeoverExpiresAt", "")
    if expires:
        try:
            if datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return {"active": False, "sessionId": session_id, "expired": True}
        except Exception:
            pass
    return {"active": True, "adminId": session["takeover"],
            "adminName": session.get("takeoverBy", ""),
            "expiresAt": expires, "sessionId": session_id}


# =========================================================================
# Session Detail — real quality from _calculate_agent_quality
# =========================================================================

@router.get("/api/v1/monitor/sessions/{session_id}")
def get_session_detail(session_id: str, authorization: str = Header(default="")):
    """Session detail with real quality metrics."""
    require_auth(authorization)
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    conv_records = db.get_session_conversation(session_id)
    conv = []
    for r in conv_records:
        msg = {"role": r.get("role", ""), "content": r.get("content", ""), "ts": r.get("ts", "")}
        if r.get("toolName"):
            msg["toolCall"] = {"tool": r["toolName"], "status": r.get("toolStatus", "success"),
                               "duration": r.get("toolDuration", "")}
        conv.append(msg)

    # Quality: use real calculation from audit.py
    agent_id = session.get("agentId", "")
    try:
        from routers.audit import _calculate_agent_quality
        quality_data = _calculate_agent_quality(agent_id)
        if quality_data.get("score") is not None:
            quality = quality_data.get("breakdown", {})
            quality["overallScore"] = quality_data["score"]
        else:
            quality = {"overallScore": None, "note": "No feedback data yet"}
    except Exception:
        quality = {"overallScore": None, "note": "Quality calculation unavailable"}

    # Plan E: real PII pattern scanning
    plan_e = []
    if conv:
        for i, msg in enumerate(conv):
            if msg["role"] == "assistant" or msg["role"] == "admin":
                findings = _scan_response(msg["content"])
                plan_e.extend([{**f, "turn": i + 1} for f in findings])
    if not plan_e:
        plan_e = [{"turn": 0, "result": "pass", "detail": "No sensitive data detected"}]

    return {"session": session, "conversation": conv, "quality": quality, "planE": plan_e}


# ── Plan E: PII pattern scanning ──────────────────────────────────────

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


# =========================================================================
# Event Stream — unified AUDIT# timeline (replaces CloudWatch runtime-events)
# =========================================================================

_EVENT_CATEGORIES = {
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


@router.get("/api/v1/monitor/events")
def get_event_stream(minutes: int = 60, limit: int = 50):
    """Unified event stream from DynamoDB AUDIT#."""
    entries = db.get_audit_entries(limit=max(limit, 200))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    events = [e for e in entries if e.get("timestamp", "") >= cutoff]
    for e in events:
        cat, icon = _EVENT_CATEGORIES.get(e.get("eventType", ""), ("other", "info"))
        e["category"] = cat
        e["icon"] = icon
    return {"events": events[:limit],
            "summary": {"total": len(events),
                        "security": len([e for e in events if e.get("category") == "security"]),
                        "config": len([e for e in events if e.get("category") == "config"]),
                        "invocations": len([e for e in events if e.get("category") == "invocation"])}}


# =========================================================================
# Agent Activity — based on AGENT#.lastInvocationAt
# =========================================================================

@router.get("/api/v1/monitor/agent-activity")
def get_agent_activity():
    """Agent activity from DynamoDB AGENT#.lastInvocationAt."""
    agents = db.get_agents()
    now = datetime.now(timezone.utc)
    active, idle, offline = [], [], []
    for a in agents:
        last = a.get("lastInvocationAt", "")
        entry = {"id": a["id"], "name": a["name"],
                 "employeeName": a.get("employeeName", ""),
                 "positionName": a.get("positionName", ""),
                 "lastActive": last}
        if not last:
            offline.append(entry)
            continue
        try:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age = (now - ts).total_seconds()
            entry["ageSec"] = int(age)
            if age < 900:
                active.append(entry)
            elif age < 3600:
                idle.append(entry)
            else:
                offline.append(entry)
        except Exception:
            offline.append(entry)
    return {"active": active, "idle": idle, "offline": offline,
            "summary": {"active": len(active), "idle": len(idle),
                        "offline": len(offline), "total": len(agents)}}


# =========================================================================
# Alerts — real data only (no placeholders)
# =========================================================================

@router.get("/api/v1/monitor/alerts")
def get_alert_rules():
    """Alert rules backed by real data only."""
    entries = db.get_audit_entries(limit=200)
    agents = db.get_agents()
    employees = db.get_employees()
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(hours=24)).isoformat()

    # Budget — real
    try:
        budgets = usage_budgets()
        over = [b for b in budgets if b["status"] in ("over", "warning")]
    except Exception:
        over = []

    # Unbound — real
    unbound = [e for e in employees if not e.get("agentId")]

    # Permission denials — real
    denials = [e for e in entries if e.get("eventType") == "permission_denied"
               and e.get("timestamp", "") >= day_ago]

    # Pending reviews — real
    pending = [e for e in entries if e.get("reviewStatus") == "pending"
               or (e.get("eventType") in ("personal_soul_change", "kb_upload")
                   and e.get("status") == "pending")]

    # SOUL drift — real
    pos_versions = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv
    drifted = [a for a in agents
               if (a.get("soulVersions") or {}).get("position", 1) < pos_versions.get(a.get("positionId", ""), 1)]

    now_str = now.isoformat()
    return [
        {"id": "alert-budget", "type": "Budget overrun", "condition": "Dept budget > 80%",
         "status": "warning" if over else "ok", "lastChecked": now_str,
         "detail": f"{len(over)} over/near limit" if over else "All within budget"},
        {"id": "alert-unbound", "type": "Unbound employees", "condition": "Employee without agent",
         "status": "warning" if unbound else "ok", "lastChecked": now_str,
         "detail": f"{len(unbound)} without agents" if unbound else "All bound"},
        {"id": "alert-denials", "type": "Permission denials (24h)", "condition": "> 5 denials/day",
         "status": "warning" if len(denials) > 5 else "ok", "lastChecked": now_str,
         "detail": f"{len(denials)} denials" if denials else "No denials"},
        {"id": "alert-reviews", "type": "Pending reviews", "condition": "Unreviewed changes",
         "status": "warning" if pending else "ok", "lastChecked": now_str,
         "detail": f"{len(pending)} pending" if pending else "All reviewed"},
        {"id": "alert-drift", "type": "SOUL version drift", "condition": "Agent behind position version",
         "status": "warning" if drifted else "ok", "lastChecked": now_str,
         "detail": f"{len(drifted)} agents behind" if drifted else "All current"},
    ]


# =========================================================================
# Health — comprehensive (replaces old CloudWatch-based health)
# =========================================================================

@router.get("/api/v1/monitor/health")
def get_monitor_health():
    """Agent health metrics from DynamoDB."""
    agents = db.get_agents()
    employees = db.get_employees()
    usage_map = _get_agent_usage_recent()

    agent_health = []
    for agent in agents:
        usage = usage_map.get(agent["id"], {})
        last_active = agent.get("lastInvocationAt") or agent.get("updatedAt") or ""
        # Status from lastInvocationAt
        status = "offline"
        if last_active:
            try:
                ts = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                status = "active" if age < 900 else "idle" if age < 3600 else "offline"
            except Exception:
                pass

        agent_health.append({
            "agentId": agent["id"],
            "agentName": agent["name"],
            "employeeName": agent.get("employeeName", ""),
            "positionName": agent.get("positionName", ""),
            "status": status,
            "qualityScore": agent.get("qualityScore"),
            "channels": agent.get("channels", []),
            "skillCount": len(agent.get("skills", [])),
            "requestsToday": usage.get("requests", 0),
            "costToday": round(usage.get("cost", 0), 4),
            "lastActive": last_active,
            "uptime": _format_uptime(time.time() - _SERVER_START_TIME),
        })

    system = {
        "totalAgents": len(agents),
        "activeAgents": sum(1 for a in agent_health if a["status"] == "active"),
        "totalRequestsToday": sum(usage_map.get(a["id"], {}).get("requests", 0) for a in agents),
        "totalCostToday": round(sum(usage_map.get(a["id"], {}).get("cost", 0) for a in agents), 2),
    }
    # Merge system status from cache
    system.update(_system_status_cache.get("data", {}))

    return {"agents": agent_health, "system": system}


# =========================================================================
# Refresh All Agents
# =========================================================================

@router.post("/api/v1/admin/refresh-all")
def refresh_all_agents(authorization: str = Header(default="")):
    """Force terminate all active agent sessions."""
    user = require_role(authorization, roles=["admin"])
    agents = db.get_agents()
    refreshed = []
    for a in agents:
        emp_id = a.get("employeeId")
        if emp_id:
            threading.Thread(target=stop_employee_session, args=(emp_id,), daemon=True).start()
            refreshed.append(emp_id)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "agent_refresh",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "system",
        "targetId": "all",
        "detail": f"Admin refreshed all {len(refreshed)} agents",
        "status": "success",
    })
    return {"refreshed": len(refreshed), "employees": refreshed}


# =========================================================================
# Backward Compatibility — old frontend endpoint names
# Remove after frontend rewrite
# =========================================================================

@router.get("/api/v1/monitor/runtime-events")
def get_runtime_events_compat(minutes: int = 60):
    """Backward compat: old frontend calls this. Redirects to event stream."""
    return get_event_stream(minutes=minutes)
