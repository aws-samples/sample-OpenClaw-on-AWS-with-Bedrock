"""
Settings — LLM Provider config, agent-config, KB assignments, security,
org-sync, admin account, admin assistant, system stats, services.

Endpoints: /api/v1/settings/*
"""

import os
import time
import json
from datetime import datetime, timezone

import boto3

from fastapi import APIRouter, HTTPException, Header

import db
import s3ops
from shared import (
    require_auth, require_role, ssm_client, bump_config_version,
    stop_employee_session,
    GATEWAY_REGION, STACK_NAME, GATEWAY_INSTANCE_ID, GATEWAY_ACCOUNT_ID,
)

router = APIRouter(tags=["settings"])

# Import cross-router helpers lazily to avoid circular imports at module level
# _auto_provision_employee is used in apply_org_sync
# _get_agent_usage_today / usage_by_department are used in dashboard-related settings


# Server start time — used to compute uptime for /settings/services
_SERVER_START_TIME = time.time()

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


# =========================================================================
# Settings — persisted in DynamoDB
# =========================================================================

def _get_model_config():
    config = db.get_config("model")
    if not config:
        return {"default": {"modelId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "modelName": "Claude Sonnet 4.5", "inputRate": 3.00, "outputRate": 15.00}, "fallback": {}, "positionOverrides": {}, "availableModels": []}
    def fix_rates(d):
        if isinstance(d, dict):
            for k in ("inputRate", "outputRate"):
                if k in d and isinstance(d[k], str):
                    d[k] = float(d[k])
            for v in d.values():
                if isinstance(v, dict): fix_rates(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict): fix_rates(item)
    fix_rates(config)
    return config


def _get_security_config():
    config = db.get_config("security")
    if not config:
        return {"alwaysBlocked": ["install_skill", "load_extension", "eval"], "piiDetection": {"enabled": True, "mode": "redact"}, "dataSovereignty": {"enabled": True, "region": "us-east-1"}, "conversationRetention": {"days": 180}, "dockerSandbox": True, "fastPathRouting": True, "verboseAudit": False}
    return config


import threading as _threading_settings


def _audit_config(user, target_type: str, target_id: str, detail: str):
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": target_type, "targetId": target_id,
        "detail": detail, "status": "success",
    })


@router.get("/api/v1/settings/model")
def get_model_config_endpoint(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    return _get_model_config()


@router.put("/api/v1/settings/model/default")
def set_default_model(body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config["default"] = body
    db.set_config("model", config)
    bump_config_version()
    _audit_config(user, "model", "default", f"Default model → {body.get('modelId','')}")
    return config["default"]


@router.put("/api/v1/settings/model/fallback")
def set_fallback_model(body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config["fallback"] = body
    db.set_config("model", config)
    bump_config_version()
    _audit_config(user, "model", "fallback", f"Fallback model → {body.get('modelId','')}")
    return config["fallback"]


@router.put("/api/v1/settings/model/position/{pos_id}")
def set_position_model(pos_id: str, body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config.setdefault("positionOverrides", {})[pos_id] = body
    db.set_config("model", config)
    bump_config_version()
    _audit_config(user, "model", pos_id, f"Position model override → {body.get('modelId','')}")
    # Force refresh affected employees
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            _threading_settings.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
    return config["positionOverrides"]


@router.delete("/api/v1/settings/model/position/{pos_id}")
def remove_position_model(pos_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config.get("positionOverrides", {}).pop(pos_id, None)
    db.set_config("model", config)
    bump_config_version()
    _audit_config(user, "model", pos_id, f"Position model override removed")
    return config["positionOverrides"]


@router.put("/api/v1/settings/model/employee/{emp_id}")
def set_employee_model(emp_id: str, body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config.setdefault("employeeOverrides", {})[emp_id] = body
    db.set_config("model", config)
    bump_config_version()
    _audit_config(user, "model", emp_id, f"Employee model override → {body.get('modelId','')}")
    _threading_settings.Thread(target=stop_employee_session, args=(emp_id,), daemon=True).start()
    return config["employeeOverrides"]


@router.delete("/api/v1/settings/model/employee/{emp_id}")
def remove_employee_model(emp_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config.get("employeeOverrides", {}).pop(emp_id, None)
    db.set_config("model", config)
    bump_config_version()
    _audit_config(user, "model", emp_id, "Employee model override removed")
    return config.get("employeeOverrides", {})


# -- Agent Config (compaction, context window, language) ---------------------

def _get_agent_config() -> dict:
    cfg = db.get_config("agent-config")
    if not cfg:
        return {"positionConfig": {}, "employeeConfig": {}}
    return cfg


@router.get("/api/v1/settings/agent-config")
def get_agent_config(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    return _get_agent_config()


@router.put("/api/v1/settings/agent-config/position/{pos_id}")
def set_position_agent_config(pos_id: str, body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.setdefault("positionConfig", {})[pos_id] = body
    db.set_config("agent-config", cfg)
    bump_config_version()
    _audit_config(user, "agent-config", pos_id, f"Position agent config: {list(body.keys())}")
    return cfg["positionConfig"][pos_id]


@router.delete("/api/v1/settings/agent-config/position/{pos_id}")
def delete_position_agent_config(pos_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.get("positionConfig", {}).pop(pos_id, None)
    db.set_config("agent-config", cfg)
    bump_config_version()
    _audit_config(user, "agent-config", pos_id, "Position agent config removed")
    return {"deleted": pos_id}


@router.put("/api/v1/settings/agent-config/employee/{emp_id}")
def set_employee_agent_config(emp_id: str, body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.setdefault("employeeConfig", {})[emp_id] = body
    db.set_config("agent-config", cfg)
    bump_config_version()
    _audit_config(user, "agent-config", emp_id, f"Employee agent config: {list(body.keys())}")
    return cfg["employeeConfig"][emp_id]


@router.delete("/api/v1/settings/agent-config/employee/{emp_id}")
def delete_employee_agent_config(emp_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.get("employeeConfig", {}).pop(emp_id, None)
    db.set_config("agent-config", cfg)
    _audit_config(user, "agent-config", emp_id, "Employee agent config removed")
    return {"deleted": emp_id}


# -- KB Assignments ----------------------------------------------------------

def _get_kb_assignments() -> dict:
    cfg = db.get_config("kb-assignments")
    return cfg if cfg else {"positionKBs": {}, "employeeKBs": {}}


@router.get("/api/v1/settings/kb-assignments")
def get_kb_assignments(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    return _get_kb_assignments()


@router.put("/api/v1/settings/kb-assignments/position/{pos_id}")
def set_position_kbs(pos_id: str, body: dict, authorization: str = Header(default="")):
    """Assign knowledge bases to a position. kbIds: list of KB IDs.
    Triggers force refresh for all affected employees so they get new KB on next message."""
    require_role(authorization, roles=["admin"])
    cfg = _get_kb_assignments()
    cfg.setdefault("positionKBs", {})[pos_id] = body.get("kbIds", [])
    db.set_config("kb-assignments", cfg)
    bump_config_version()
    # Force refresh: terminate running sessions for affected employees
    import threading
    refreshed = []
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            threading.Thread(
                target=stop_employee_session, args=(emp["id"],), daemon=True
            ).start()
            refreshed.append(emp["id"])
    return {"kbIds": cfg["positionKBs"][pos_id], "refreshed": refreshed}


@router.put("/api/v1/settings/kb-assignments/employee/{emp_id}")
def set_employee_kbs(emp_id: str, body: dict, authorization: str = Header(default="")):
    """Assign knowledge bases to an individual employee. Triggers force refresh."""
    require_role(authorization, roles=["admin"])
    cfg = _get_kb_assignments()
    cfg.setdefault("employeeKBs", {})[emp_id] = body.get("kbIds", [])
    db.set_config("kb-assignments", cfg)
    bump_config_version()
    import threading
    threading.Thread(
        target=stop_employee_session, args=(emp_id,), daemon=True
    ).start()
    return {"kbIds": cfg["employeeKBs"][emp_id], "refreshed": [emp_id]}


@router.get("/api/v1/settings/security")
def get_security_config_endpoint(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    return _get_security_config()


@router.put("/api/v1/settings/security")
def update_security_config(body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_security_config()
    config.update(body)
    db.set_config("security", config)
    _audit_config(user, "security", "global", f"Security config updated: {list(body.keys())}")
    return config


# =========================================================================
# Org Sync — Feishu / DingTalk (Task C)
# =========================================================================

@router.get("/api/v1/settings/org-sync")
def get_org_sync_config(authorization: str = Header(default="")):
    """Get org sync configuration (source, interval, last sync time)."""
    require_role(authorization, roles=["admin"])
    cfg = db.get_config("org-sync") or {}
    return {
        "source": cfg.get("source", "none"),
        "enabled": cfg.get("enabled", False),
        "interval": cfg.get("interval", "4h"),
        "lastSync": cfg.get("lastSync"),
        "lastResult": cfg.get("lastResult"),
        "status": cfg.get("status", "not_configured"),
    }


@router.put("/api/v1/settings/org-sync")
def update_org_sync_config(body: dict, authorization: str = Header(default="")):
    """Save org sync configuration."""
    require_role(authorization, roles=["admin"])
    cfg = db.get_config("org-sync") or {}
    cfg.update({k: v for k, v in body.items()
                if k in ("source", "enabled", "interval", "apiKey", "appId", "appSecret", "tenantKey")})
    db.set_config("org-sync", cfg)
    return {"saved": True}


@router.post("/api/v1/settings/org-sync/preview")
def preview_org_sync(authorization: str = Header(default="")):
    """Simulate org sync and return a diff preview (what would change)."""
    require_role(authorization, roles=["admin"])
    cfg = db.get_config("org-sync") or {}
    source = cfg.get("source", "none")

    if source == "none":
        raise HTTPException(400, "No org sync source configured")

    # Fetch remote org data (Feishu / DingTalk)
    remote_users = []
    remote_depts = []
    try:
        if source == "feishu":
            remote_users, remote_depts = _fetch_feishu_org(cfg)
        elif source == "dingtalk":
            remote_users, remote_depts = _fetch_dingtalk_org(cfg)
        else:
            raise HTTPException(400, f"Unsupported source: {source}")
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch from {source}: {e}")

    # Compare with current DynamoDB org
    current_emps = {e["id"]: e for e in db.get_employees()}
    current_depts = {d["id"]: d for d in db.get_departments()}

    new_emps, changed_emps, left_emps = [], [], []
    for ru in remote_users:
        if ru["id"] not in current_emps:
            new_emps.append(ru)
        elif _emp_changed(current_emps[ru["id"]], ru):
            changed_emps.append({"before": current_emps[ru["id"]], "after": ru})
    for emp_id in current_emps:
        if not any(ru["id"] == emp_id for ru in remote_users):
            left_emps.append(current_emps[emp_id])

    new_depts, changed_depts = [], []
    for rd in remote_depts:
        if rd["id"] not in current_depts:
            new_depts.append(rd)
        elif current_depts[rd["id"]].get("name") != rd.get("name"):
            changed_depts.append({"before": current_depts[rd["id"]], "after": rd})

    return {
        "source": source,
        "employees": {"new": new_emps, "changed": changed_emps, "left": left_emps},
        "departments": {"new": new_depts, "changed": changed_depts},
        "summary": {
            "newEmployees": len(new_emps),
            "changedEmployees": len(changed_emps),
            "leftEmployees": len(left_emps),
            "deptChanges": len(new_depts) + len(changed_depts),
        }
    }


@router.post("/api/v1/settings/org-sync/apply")
def apply_org_sync(body: dict, authorization: str = Header(default="")):
    """Apply org sync changes from a preview result."""
    require_role(authorization, roles=["admin"])
    from routers.org import _auto_provision_employee

    preview = body.get("preview", {})
    applied = {"newEmployees": 0, "archivedEmployees": 0, "updatedEmployees": 0, "newDepts": 0}

    for emp in preview.get("employees", {}).get("new", []):
        # Auto-provision: create employee + agent + binding
        _auto_provision_employee(emp)
        applied["newEmployees"] += 1

    for change in preview.get("employees", {}).get("changed", []):
        db.update_employee(change["after"]["id"], change["after"])
        applied["updatedEmployees"] += 1

    for emp in preview.get("employees", {}).get("left", []):
        db.update_employee(emp["id"], {**emp, "agentStatus": "archived"})
        applied["archivedEmployees"] += 1

    for dept in preview.get("departments", {}).get("new", []):
        db.create_department(dept)
        applied["newDepts"] += 1

    # Update sync state
    cfg = db.get_config("org-sync") or {}
    cfg["lastSync"] = datetime.now(timezone.utc).isoformat()
    cfg["lastResult"] = applied
    cfg["status"] = "ok"
    db.set_config("org-sync", cfg)

    return {"applied": applied}


def _fetch_feishu_org(cfg: dict):
    """Fetch users and departments from Feishu API."""
    import requests as _req
    app_id = cfg.get("appId", "")
    app_secret = cfg.get("appSecret", "")
    if not app_id or not app_secret:
        raise ValueError("Feishu appId and appSecret required")

    # Get tenant_access_token
    token_resp = _req.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10
    ).json()
    token = token_resp.get("tenant_access_token", "")
    if not token:
        raise ValueError(f"Failed to get Feishu token: {token_resp.get('msg')}")

    headers = {"Authorization": f"Bearer {token}"}
    # Fetch departments
    depts_resp = _req.get(
        "https://open.feishu.cn/open-apis/contact/v3/departments",
        headers=headers, params={"page_size": 200}, timeout=10).json()
    depts = [{"id": f"dept-{d['open_department_id']}", "name": d["name"],
               "parentId": d.get("parent_open_department_id")}
             for d in depts_resp.get("data", {}).get("items", [])]

    # Fetch users
    users_resp = _req.get(
        "https://open.feishu.cn/open-apis/contact/v3/users",
        headers=headers, params={"page_size": 200}, timeout=10).json()
    users = [{"id": f"emp-{u['open_id']}", "name": u["name"],
               "departmentId": f"dept-{u.get('open_department_ids', [''])[0]}",
               "positionId": "pos-employee", "role": "employee"}
             for u in users_resp.get("data", {}).get("items", [])]

    return users, depts


def _fetch_dingtalk_org(cfg: dict):
    """Fetch users and departments from DingTalk API."""
    import requests as _req
    app_key = cfg.get("appId", "")
    app_secret = cfg.get("appSecret", "")
    if not app_key or not app_secret:
        raise ValueError("DingTalk appId and appSecret required")

    token_resp = _req.post(
        "https://oapi.dingtalk.com/gettoken",
        params={"appkey": app_key, "appsecret": app_secret}, timeout=10).json()
    token = token_resp.get("access_token", "")

    headers = {"x-acs-dingtalk-access-token": token}
    depts_resp = _req.post(
        "https://oapi.dingtalk.com/topapi/v2/department/listsub",
        headers={"Content-Type": "application/json"},
        params={"access_token": token},
        json={"dept_id": 1, "language": "zh_CN"}, timeout=10).json()
    depts = [{"id": f"dept-{d['dept_id']}", "name": d["name"]}
             for d in depts_resp.get("result", {}).get("dept_list", [])]

    users_resp = _req.post(
        "https://oapi.dingtalk.com/topapi/v2/user/list",
        params={"access_token": token},
        json={"dept_id": 1, "size": 100}, timeout=10).json()
    users = [{"id": f"emp-{u['userid']}", "name": u["name"],
               "departmentId": f"dept-{u.get('dept_id_list', [1])[0]}",
               "positionId": "pos-employee", "role": "employee"}
             for u in users_resp.get("result", {}).get("list", [])]

    return users, depts


def _emp_changed(current: dict, remote: dict) -> bool:
    """Check if employee record differs between current and remote."""
    for field in ("name", "departmentId", "positionId"):
        if current.get(field) != remote.get(field):
            return True
    return False


# =========================================================================
# Settings — Services health / status
# =========================================================================

def _format_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    secs = int(seconds)
    days, remainder = divmod(secs, 86400)
    hours, remainder = divmod(remainder, 3600)
    mins = remainder // 60
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _check_gateway_status() -> str:
    """Try to hit the OpenClaw Gateway /health endpoint on localhost:18789."""
    try:
        import urllib.request as _ur
        req = _ur.Request("http://localhost:18789/health", method="GET")
        with _ur.urlopen(req, timeout=2) as resp:
            return "healthy" if resp.status == 200 else "degraded"
    except Exception:
        return "unreachable"


def _measure_bedrock_latency() -> int:
    """Measure round-trip latency to Bedrock by timing a lightweight ListFoundationModels call."""
    try:
        t0 = time.time()
        boto3.client("bedrock", region_name=AWS_REGION).list_foundation_models(maxResults=1)
        return int((time.time() - t0) * 1000)
    except Exception:
        return 0


@router.get("/api/v1/settings/services")
def get_services():
    uptime_str = _format_uptime(time.time() - _SERVER_START_TIME)

    # Gateway: try to ping, measure latency
    gw_status = _check_gateway_status()

    # Requests today: count agent_invocation audit entries from today
    from datetime import date as _date
    today_str = _date.today().isoformat()
    audit_entries = db.get_audit_entries(limit=500)
    requests_today = sum(
        1 for e in audit_entries
        if e.get("eventType") == "agent_invocation"
        and e.get("timestamp", "").startswith(today_str)
    )

    # Approvals processed: count all non-pending approvals
    approvals = db.get_approvals()
    approvals_processed = sum(1 for a in approvals if a.get("status") in ("approved", "denied"))

    # Bedrock: measure real latency
    bedrock_ms = _measure_bedrock_latency()
    bedrock_status = "connected" if bedrock_ms > 0 else "unreachable"

    # DynamoDB: get real item count via a lightweight describe (scan is expensive; use table meta)
    ddb_item_count = 0
    ddb_status = "unknown"
    try:
        table_meta = boto3.resource("dynamodb", region_name=db.AWS_REGION).Table(db.TABLE_NAME)
        table_meta.load()
        ddb_item_count = table_meta.item_count or 0
        ddb_status = "active"
    except Exception:
        ddb_status = "unreachable"

    # S3: quick head-bucket check
    s3_status = "unknown"
    try:
        boto3.client("s3").head_bucket(Bucket=s3ops.bucket())
        s3_status = "active"
    except Exception:
        s3_status = "unreachable"

    return {
        "gateway": {
            "status": gw_status,
            "port": 18789,
            "uptime": uptime_str,
            "requestsToday": requests_today,
        },
        "auth_agent": {
            "status": "healthy",
            "uptime": uptime_str,
            "approvalsProcessed": approvals_processed,
        },
        "bedrock": {
            "status": bedrock_status,
            "region": AWS_REGION,
            "latencyMs": bedrock_ms if bedrock_ms else None,
            "vpcEndpoint": True,
        },
        "dynamodb": {
            "status": ddb_status,
            "table": db.TABLE_NAME,
            "itemCount": ddb_item_count,
        },
        "s3": {"status": s3_status, "bucket": s3ops.bucket()},
        "platform": {
            "instanceId": GATEWAY_INSTANCE_ID,
            "region": GATEWAY_REGION,
            "awsRegion": AWS_REGION,
            "stackName": STACK_NAME,
        },
    }


# =========================================================================
# Settings — Admin Account, Admin Assistant, System Stats
# =========================================================================

@router.put("/api/v1/settings/admin-password")
def change_admin_password(body: dict, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    new_pw = body.get("newPassword", "")
    if len(new_pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        stack = os.environ.get("STACK_NAME", "openclaw")
        boto3.client("ssm", region_name=GATEWAY_REGION).put_parameter(
            Name=f"/openclaw/{stack}/admin-password",
            Value=new_pw, Type="SecureString", Overwrite=True)
        os.environ["ADMIN_PASSWORD"] = new_pw
        return {"saved": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/v1/settings/admin-assistant")
def get_admin_assistant(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        cfg = db.get_config("admin-assistant") or {}
    except Exception:
        cfg = {}
    return {
        "model": cfg.get("model", os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")),
        "systemPrompt": cfg.get("systemPrompt",
            "You are the IT Admin Assistant for OpenClaw Enterprise platform. "
            "You help the admin manage AI agents, monitor system health, "
            "troubleshoot issues, and configure the platform. "
            "Be concise, technical, and actionable."),
        "systemPromptExtra": cfg.get("systemPromptExtra", ""),
        "maxHistoryTurns": int(cfg.get("maxHistoryTurns", 20)),
        "maxTokens": int(cfg.get("maxTokens", 4096)),
    }


@router.put("/api/v1/settings/admin-assistant")
def put_admin_assistant(body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    cfg = {
        "model": body.get("model", ""),
        "systemPrompt": body.get("systemPrompt", ""),
        "systemPromptExtra": body.get("systemPromptExtra", ""),
        "maxHistoryTurns": int(body.get("maxHistoryTurns", 20)),
        "maxTokens": int(body.get("maxTokens", 4096)),
    }
    db.set_config("admin-assistant", cfg)
    _audit_config(user, "admin-assistant", "config", "Admin assistant config updated")
    return {"saved": True}


@router.get("/api/v1/settings/admin-assistant/history")
def get_admin_history(authorization: str = Header(default="")):
    """Get admin assistant conversation history from DynamoDB."""
    require_role(authorization, roles=["admin"])
    records = db.get_session_conversation("admin-assistant")
    return [{"role": r.get("role", ""), "content": r.get("content", ""), "ts": r.get("ts", "")}
            for r in records[-50:]]


@router.delete("/api/v1/settings/admin-assistant/history")
def clear_admin_history(authorization: str = Header(default="")):
    """Clear admin assistant conversation history."""
    require_role(authorization, roles=["admin"])
    try:
        from boto3.dynamodb.conditions import Key
        ddb = boto3.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        resp = table.query(
            KeyConditionExpression=Key("PK").eq("ORG#acme") & Key("SK").begins_with("CONV#admin-assistant"),
        )
        with table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        return {"cleared": True, "count": len(resp.get("Items", []))}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/v1/settings/system-stats")
def get_system_stats(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    import shutil, subprocess
    result = {}
    # Disk
    try:
        disk = shutil.disk_usage("/")
        result["disk"] = {"total": disk.total, "used": disk.used, "free": disk.free,
                          "pct": round(disk.used / disk.total * 100, 1)}
    except Exception:
        result["disk"] = {}
    # CPU / Memory via /proc (no psutil needed)
    try:
        with open("/proc/meminfo") as f:
            mem_lines = {l.split(":")[0]: int(l.split(":")[1].strip().split()[0])
                         for l in f if ":" in l}
        mem_total = mem_lines.get("MemTotal", 0) * 1024
        mem_free = (mem_lines.get("MemAvailable", 0)) * 1024
        result["memory"] = {"total": mem_total, "used": mem_total - mem_free, "free": mem_free,
                             "pct": round((mem_total - mem_free) / max(mem_total, 1) * 100, 1)}
    except Exception:
        result["memory"] = {}
    try:
        cpu_out = subprocess.check_output(["top", "-bn1"], text=True, timeout=5)
        for line in cpu_out.splitlines():
            if "Cpu" in line or "cpu" in line:
                parts = line.replace(",", " ").split()
                for i, p in enumerate(parts):
                    if "id" in p.lower() and i > 0:
                        try:
                            idle = float(parts[i - 1].replace("%", ""))
                            result["cpu"] = {"pct": round(100 - idle, 1)}
                            break
                        except Exception:
                            pass
                break
    except Exception:
        result["cpu"] = {"pct": 0}
    # Port status
    try:
        ports_out = subprocess.check_output(["ss", "-tlnp"], text=True, timeout=5)
        listening = set()
        for line in ports_out.splitlines():
            if "LISTEN" in line:
                m = __import__("re").search(r":(\d+)\s", line)
                if m:
                    listening.add(int(m.group(1)))
        key_ports = [
            {"port": 8099, "name": "Admin Console", "expected": True},
            {"port": 8090, "name": "Tenant Router", "expected": True},
            {"port": 8091, "name": "H2 Proxy", "expected": True},
            {"port": 18789, "name": "OpenClaw Gateway", "expected": False},
        ]
        result["ports"] = [{"port": p["port"], "name": p["name"], "listening": p["port"] in listening} for p in key_ports]
    except Exception:
        result["ports"] = []
    return result


# =========================================================================
# Platform Access — SSM commands, Gateway UI
# =========================================================================

@router.get("/api/v1/settings/platform-access")
def get_platform_access(authorization: str = Header(default="")):
    """Pre-built SSM commands for EC2 access and Gateway UI."""
    require_role(authorization, roles=["admin"])
    return {
        "instanceId": GATEWAY_INSTANCE_ID,
        "region": GATEWAY_REGION,
        "stackName": STACK_NAME,
        "ssmSession": f"aws ssm start-session --target {GATEWAY_INSTANCE_ID} --region {GATEWAY_REGION}",
        "gatewayPortForward": (
            f"aws ssm start-session --target {GATEWAY_INSTANCE_ID} --region {GATEWAY_REGION} "
            f"--document-name AWS-StartPortForwardingSession "
            f"--parameters portNumber=18789,localPortNumber=18789"),
        "gatewayUrl": "http://localhost:18789",
        "note": "Run the port forward command in terminal, then open Gateway URL in browser.",
    }


# =========================================================================
# Platform Logs — journalctl for systemd services
# =========================================================================

@router.get("/api/v1/settings/platform-logs")
def get_platform_logs(service: str = "openclaw-admin", lines: int = 50, authorization: str = Header(default="")):
    """Read recent journalctl logs for a platform service."""
    require_role(authorization, roles=["admin"])
    allowed = {"openclaw-admin", "tenant-router", "bedrock-proxy-h2"}
    if service not in allowed:
        raise HTTPException(400, f"Service must be one of: {sorted(allowed)}")
    lines = min(lines, 200)
    try:
        import subprocess
        output = subprocess.check_output(
            ["journalctl", "-u", service, "--no-pager", "-n", str(lines)],
            text=True, timeout=10)
        log_lines = output.strip().split("\n") if output.strip() else []
        return {"service": service, "lines": log_lines, "count": len(log_lines)}
    except Exception as e:
        return {"service": service, "lines": [], "error": str(e)}


# =========================================================================
# Service Restart
# =========================================================================

@router.post("/api/v1/settings/restart-service")
def restart_service(body: dict, authorization: str = Header(default="")):
    """Restart a platform systemd service. Admin only."""
    user = require_role(authorization, roles=["admin"])
    service = body.get("service", "")
    allowed = {"openclaw-admin", "tenant-router", "bedrock-proxy-h2"}
    if service not in allowed:
        raise HTTPException(400, f"Service must be one of: {sorted(allowed)}")
    try:
        import subprocess
        subprocess.check_output(["sudo", "systemctl", "restart", service], text=True, timeout=15)
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "service_restart",
            "actorId": user.employee_id, "actorName": user.name,
            "targetType": "service", "targetId": service,
            "detail": f"Admin restarted {service}", "status": "success",
        })
        return {"restarted": True, "service": service}
    except Exception as e:
        raise HTTPException(500, str(e))

# =========================================================================
# SSO (通用 OIDC) 配置
# =========================================================================
# 管理员在 Settings → SSO tab 填写 IdP 应用信息,存 DynamoDB CONFIG#sso。
# 前端不经此端点拉取 (那是 /public/sso/config),此处仅供管理员管理。

@router.get("/api/v1/settings/sso")
def get_sso_config(authorization: str = Header(default="")):
    """读取当前 SSO 配置。clientSecret 脱敏返回 (已设置则返回 '***',未设置返回空)。"""
    require_role(authorization, roles=["admin"])
    cfg = db.get_config("sso") or {}
    has_secret = bool(cfg.get("clientSecret"))
    return {
        "enabled": bool(cfg.get("enabled")),
        "issuer": cfg.get("issuer", ""),
        "clientId": cfg.get("clientId", ""),
        "clientSecret": "***" if has_secret else "",
        "scopes": cfg.get("scopes", "openid profile email"),
        "autoCreateEnabled": bool(cfg.get("autoCreateEnabled", True)),
        "defaultPositionId": cfg.get("defaultPositionId", ""),
        "defaultRole": cfg.get("defaultRole", "employee"),
        "updatedAt": cfg.get("updatedAt", ""),
        "updatedBy": cfg.get("updatedBy", ""),
    }


@router.put("/api/v1/settings/sso")
def update_sso_config(body: dict, authorization: str = Header(default="")):
    """保存 SSO 配置,同时清除后端验证缓存。

    clientSecret 的特殊处理: 如果前端传入 '***' 或空串,保留现有值不动。
    只有传入非 '***' 的具体值才更新。
    """
    user = require_role(authorization, roles=["admin"])

    issuer = (body.get("issuer") or "").strip()
    client_id = (body.get("clientId") or "").strip()
    client_secret_input = body.get("clientSecret", "")
    scopes = (body.get("scopes") or "openid profile email").strip()
    enabled = bool(body.get("enabled"))
    auto_create_enabled = bool(body.get("autoCreateEnabled", True))
    default_position_id = (body.get("defaultPositionId") or "").strip()
    default_role = (body.get("defaultRole") or "employee").strip()

    # 读现有配置 (用于保留 clientSecret 的情况)
    existing = db.get_config("sso") or {}
    existing_secret = existing.get("clientSecret", "")

    # clientSecret 保留策略: 空串或 '***' → 保留旧值(存量不丢);否则采用新值
    if not client_secret_input or client_secret_input == "***":
        client_secret = existing_secret
    else:
        client_secret = client_secret_input.strip()

    # 基础校验: 启用状态下 issuer/clientId/clientSecret 不能空
    if enabled:
        if not issuer or not client_id:
            raise HTTPException(400, "issuer and clientId are required when SSO is enabled")
        if not client_secret:
            raise HTTPException(400, "clientSecret is required when SSO is enabled")
        if not issuer.startswith("https://") and not issuer.startswith("http://localhost"):
            raise HTTPException(400, "issuer must be HTTPS (only localhost allowed for HTTP)")

    # Auto-create 启用时需要合法的 defaultPositionId 和 defaultRole
    if enabled and auto_create_enabled:
        if not default_position_id:
            raise HTTPException(400, "defaultPositionId is required when auto-create is enabled")
        if not db.get_position(default_position_id):
            raise HTTPException(400, f"defaultPositionId '{default_position_id}' not found")
        if default_role not in ("employee", "manager", "admin"):
            raise HTTPException(400, "defaultRole must be one of: employee, manager, admin")

    cfg = {
        "enabled": enabled,
        "issuer": issuer,
        "clientId": client_id,
        "clientSecret": client_secret,
        "scopes": scopes,
        "autoCreateEnabled": auto_create_enabled,
        "defaultPositionId": default_position_id,
        "defaultRole": default_role,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "updatedBy": user.employee_id,
    }
    db.set_config("sso", cfg)

    # 保存后主动清除 auth.py 的 OIDC 配置和 JWKS 缓存 + auth_sso 的 discovery 缓存
    try:
        import auth as authmod
        authmod.clear_sso_config_cache()
    except Exception:
        pass
    try:
        from routers.auth_sso import clear_discovery_cache
        clear_discovery_cache()
    except Exception:
        pass

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "sso_config", "targetId": "CONFIG#sso",
        "detail": f"Updated SSO config: enabled={enabled}, issuer={issuer[:60]}",
        "status": "success",
    })
    return cfg


@router.post("/api/v1/settings/sso/test")
def test_sso_connection(body: dict, authorization: str = Header(default="")):
    """测试 Issuer 是否可达 (拉 .well-known/openid-configuration)。"""
    require_role(authorization, roles=["admin"])
    issuer = (body.get("issuer") or "").strip().rstrip("/")
    if not issuer:
        raise HTTPException(400, "issuer is required")

    import httpx
    try:
        resp = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=5.0)
        resp.raise_for_status()
        doc = resp.json()
        return {
            "ok": True,
            "authorizationEndpoint": doc.get("authorization_endpoint", ""),
            "tokenEndpoint": doc.get("token_endpoint", ""),
            "jwksUri": doc.get("jwks_uri", ""),
            "scopesSupported": doc.get("scopes_supported", []),
        }
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"HTTP error: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
