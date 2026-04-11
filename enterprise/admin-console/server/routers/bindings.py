"""
Bindings — CRUD, IM User Mapping (SSM-backed), pairing-approve,
Routing Rules, provision-by-position, routing/resolve, Approvals.

Extracted from main.py lines 699-1172.
"""

import os
import json
import threading
import subprocess
from datetime import datetime, timezone

import boto3
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
from shared import (
    require_auth, require_role, get_dept_scope,
    ssm_client, GATEWAY_REGION, STACK_NAME, GATEWAY_ACCOUNT_ID,
)
from routers.org import _auto_provision_employee

router = APIRouter(tags=["bindings"])


# ── Local helpers ────────────────────────────────────────────────────────

def _get_current_user(authorization: str):
    """Extract current user, returns None if not authenticated."""
    try:
        return require_auth(authorization)
    except Exception:
        return None


def _mapping_prefix():
    stack = os.environ.get("STACK_NAME", "openclaw")
    return f"/openclaw/{stack}/user-mapping/"


def _write_user_mapping(channel: str, channel_user_id: str, employee_id: str):
    """Write user mapping to DynamoDB (primary) + SSM (dual-write for legacy components)."""
    try:
        db.create_user_mapping(channel, channel_user_id, employee_id)
    except Exception as e:
        print(f"[user-mapping] DynamoDB write failed: {e}")
    # SSM dual-write kept for tenant_router/agent-container backward compat
    key = f"{channel}__{channel_user_id}"
    path = f"{_mapping_prefix()}{key}"
    try:
        ssm_client().put_parameter(Name=path, Value=employee_id, Type="String", Overwrite=True)
    except Exception as e:
        print(f"[user-mapping] SSM dual-write failed (non-fatal): {e}")


def _read_user_mapping(channel: str, channel_user_id: str) -> str:
    """Read user mapping -- DynamoDB first, SSM fallback."""
    m = db.get_user_mapping(channel, channel_user_id)
    if m:
        return m.get("employeeId", "")
    key = f"{channel}__{channel_user_id}"
    path = f"{_mapping_prefix()}{key}"
    try:
        resp = ssm_client().get_parameter(Name=path)
        return resp["Parameter"]["Value"]
    except Exception:
        return ""




def _send_im_notification(channel: str, channel_user_id: str, message: str) -> None:
    """Best-effort: send an IM message to the user via their platform bot.
    Non-fatal -- logs and returns silently on any error."""
    try:
        import requests as _req
        if channel == "telegram":
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            if token:
                _req.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": channel_user_id, "text": message},
                    timeout=5,
                )
        elif channel == "feishu":
            app_id = os.environ.get("FEISHU_APP_ID", "")
            app_secret = os.environ.get("FEISHU_APP_SECRET", "")
            if app_id and app_secret:
                # Get tenant access token
                auth = _req.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": app_secret}, timeout=5,
                ).json()
                access_token = auth.get("tenant_access_token", "")
                if access_token:
                    _req.post(
                        "https://open.feishu.cn/open-apis/message/v4/send/",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json={
                            "user_id": channel_user_id,
                            "msg_type": "text",
                            "content": {"text": message},
                        },
                        timeout=5,
                    )
        # Discord DM requires creating a channel first -- skip for now
    except Exception as e:
        print(f"[im-notify] {channel}/{channel_user_id}: {e}")


# ── Pydantic models ─────────────────────────────────────────────────────

class UserMappingRequest(BaseModel):
    channel: str       # discord, telegram, slack, whatsapp
    channelUserId: str  # platform-specific user ID
    employeeId: str     # emp-carol, emp-ryan, etc.


class PairingApproveRequest(BaseModel):
    channel: str          # discord, telegram, feishu, slack, whatsapp
    pairingCode: str      # e.g. KFDAF3GN
    employeeId: str       # e.g. emp-carol
    channelUserId: str = ""   # numeric platform user ID (from pairing message)
    pairingUserId: str = ""   # username/handle (e.g. "wujiade4444") for dm_ mapping


# =========================================================================
# Bindings CRUD
# =========================================================================

@router.get("/api/v1/bindings")
def get_bindings(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    bindings = db.get_bindings()
    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            emp_ids_in_scope = {e["id"] for e in employees if e.get("departmentId") in scope}
            bindings = [b for b in bindings if b.get("employeeId") in emp_ids_in_scope]

    # Enrich bindings that are missing employeeName or agentName.
    # portal bindings created by seed_workspaces.py were written without names.
    needs_enrich = any(not b.get("employeeName") or not b.get("agentName") for b in bindings)
    if needs_enrich:
        emp_map = {e["id"]: e for e in db.get_employees()}
        agent_map = {a["id"]: a for a in db.get_agents()}
        for b in bindings:
            if not b.get("employeeName"):
                emp = emp_map.get(b.get("employeeId", ""))
                if emp:
                    b["employeeName"] = emp.get("name", b.get("employeeId", ""))
            if not b.get("agentName"):
                agent = agent_map.get(b.get("agentId", ""))
                if agent:
                    b["agentName"] = agent.get("name", b.get("agentId", ""))

    return bindings


@router.post("/api/v1/bindings")
def create_binding(body: dict):
    body.setdefault("status", "active")
    body.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
    # If channel user ID provided, write SSM mapping
    channel_user_id = body.get("channelUserId", "")
    channel = body.get("channel", "")
    employee_id = body.get("employeeId", "")
    if channel_user_id and channel and employee_id:
        _write_user_mapping(channel, channel_user_id, employee_id)
    return db.create_binding(body)


# =========================================================================
# IM User -> Employee Mapping (SSM-backed)
# =========================================================================

@router.get("/api/v1/bindings/user-mappings")
def get_user_mappings(authorization: str = Header(default="")):
    """List all IM user -> employee mappings."""
    require_role(authorization, roles=["admin", "manager"])
    return db.get_user_mappings()


@router.post("/api/v1/bindings/user-mappings")
def create_user_mapping(body: UserMappingRequest, authorization: str = Header(default="")):
    """Create or update an IM user -> employee mapping."""
    require_role(authorization, roles=["admin"])
    _write_user_mapping(body.channel, body.channelUserId, body.employeeId)
    return {"saved": True, "channel": body.channel, "channelUserId": body.channelUserId, "employeeId": body.employeeId}


@router.delete("/api/v1/bindings/user-mappings")
def delete_user_mapping(channel: str, channelUserId: str, authorization: str = Header(default="")):
    """Delete an IM user -> employee mapping from DynamoDB + SSM.
    Sends a best-effort IM notification before deleting."""
    require_role(authorization, roles=["admin"])
    # Look up emp_id before deleting (needed for notification and audit)
    existing = db.get_user_mapping(channel, channelUserId)
    emp_id = existing.get("employeeId", "") if existing else ""

    # Best-effort: notify employee their binding is being removed
    if emp_id:
        notif_msg = f"\u4f60\u7684 {channel.capitalize()} \u8d26\u53f7\u5df2\u4ece ACME Corp AI Agent \u89e3\u9664\u7ed1\u5b9a\u3002\u5982\u9700\u91cd\u65b0\u8fde\u63a5\u8bf7\u767b\u5f55\u5458\u5de5\u95e8\u6237\u3002"
        threading.Thread(
            target=_send_im_notification,
            args=(channel, channelUserId, notif_msg),
            daemon=True,
        ).start()

    # Delete from DynamoDB MAPPING#
    try:
        db.delete_user_mapping(channel, channelUserId)
    except Exception as e:
        print(f"[delete-user-mapping] DynamoDB delete failed: {e}")

    # Delete from SSM
    key = f"{channel}__{channelUserId}"
    path = f"{_mapping_prefix()}{key}"
    try:
        ssm_client().delete_parameter(Name=path)
    except Exception:
        pass  # SSM may not have this key if DynamoDB was primary

    # Audit log
    if emp_id:
        try:
            db.create_audit_entry({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "eventType": "config_change",
                "actorId": emp_id,
                "actorName": emp_id,
                "targetType": "binding",
                "targetId": f"{channel}__{channelUserId}",
                "detail": f"IM binding revoked: {channel} {channelUserId} \u2192 {emp_id}",
                "status": "success",
            })
        except Exception:
            pass

    return {"deleted": True}


# =========================================================================
# Pairing Approve
# =========================================================================

@router.post("/api/v1/bindings/pairing-approve")
def approve_pairing(body: PairingApproveRequest, authorization: str = Header(default="")):
    """Approve IM pairing + create user mapping in one step.
    Calls `openclaw pairing approve <channel> <code>` via subprocess,
    then writes SSM user mapping if channelUserId is provided."""
    require_role(authorization, roles=["admin"])

    # 1. Run openclaw pairing approve
    from routers.openclaw_cli import find_openclaw_bin, openclaw_env
    openclaw_bin = find_openclaw_bin()
    env = openclaw_env()

    try:
        result = subprocess.run(
            [openclaw_bin, "pairing", "approve", body.channel, body.pairingCode],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode != 0:
            return {"approved": False, "error": result.stderr.strip() or result.stdout.strip()}
        approve_output = result.stdout.strip()
    except Exception as e:
        return {"approved": False, "error": str(e)}

    # 2. Write SSM user mappings if channelUserId provided.
    # Write ALL formats that H2 Proxy may extract, so routing works regardless of
    # how OpenClaw formats the sender metadata (numeric ID, dm_username, username).
    mapping_written = False
    if body.channelUserId and body.employeeId:
        _write_user_mapping(body.channel, body.channelUserId, body.employeeId)
        # Also write username-based mappings extracted by H2 Proxy from Discord DM format
        if body.pairingUserId:  # Discord username from pairing message meta
            _write_user_mapping(body.channel, f"dm_{body.pairingUserId}", body.employeeId)
            _write_user_mapping(body.channel, body.pairingUserId, body.employeeId)
        # Position and permissions are now read from DynamoDB (EMP#/POS# records).
        # DynamoDB MAPPING# resolves channelUserId → emp_id → positionId at runtime.

    # 3. Sync updated allowFrom list to S3 so microVMs pick it up
    # The EC2's openclaw pairing approve updates the local credentials file.
    # We push it to S3 so AgentCore microVMs can load it on first invocation.
    if body.channel == "discord" and body.channelUserId:
        try:
            creds_src = "/home/ubuntu/.openclaw/credentials/discord-default-allowFrom.json"
            s3_bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
            s3_key = "_shared/openclaw-creds/discord-default-allowFrom.json"
            if os.path.isfile(creds_src):
                subprocess.run(
                    ["aws", "s3", "cp", creds_src,
                     f"s3://{s3_bucket}/{s3_key}", "--quiet",
                     "--region", os.environ.get("AWS_REGION", "us-east-1")],
                    timeout=10, capture_output=True
                )
        except Exception as _e:
            print(f"[pairing] S3 credentials sync failed (non-fatal): {_e}")

    # 4. Audit trail
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": "admin",
        "actorName": "IT Admin",
        "targetType": "pairing",
        "targetId": body.pairingCode,
        "detail": f"Approved {body.channel} pairing for {body.employeeId} (code: {body.pairingCode})",
        "status": "success",
    })

    return {"approved": True, "output": approve_output, "mappingWritten": mapping_written}


# =========================================================================
# Routing Rules
# =========================================================================

@router.get("/api/v1/routing/rules")
def get_routing_rules():
    return db.get_routing_rules()


@router.post("/api/v1/bindings/provision-by-position")
def provision_by_position(body: dict):
    """Bulk-provision agents and bindings for all unbound employees in a position.
    Delegates to _auto_provision_employee for each employee."""
    pos_id = body.get("positionId", "")
    default_channel = body.get("defaultChannel", "slack")

    if not pos_id:
        raise HTTPException(400, "positionId required")

    positions = db.get_positions()
    pos = next((p for p in positions if p["id"] == pos_id), None)
    if not pos:
        raise HTTPException(404, f"Position {pos_id} not found")

    # Temporarily set defaultChannel on position so _auto_provision_employee picks it up
    pos["defaultChannel"] = default_channel
    db.create_position(pos)  # upsert with channel

    employees = db.get_employees()
    unbound = [e for e in employees if e.get("positionId") == pos_id and not e.get("agentId")]
    already_bound = len([e for e in employees if e.get("positionId") == pos_id and e.get("agentId")])

    provisioned = []
    for emp in unbound:
        result = _auto_provision_employee(emp)
        if result:
            provisioned.append({
                "employee": emp["name"],
                "agent": result["agentName"],
                "channel": default_channel,
            })

    return {
        "position": pos.get("name"),
        "provisioned": len(provisioned),
        "details": provisioned,
        "alreadyBound": already_bound,
    }


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


# =========================================================================
# Approvals
# =========================================================================

@router.get("/api/v1/approvals")
def get_approvals(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin", "manager"])
    all_approvals = db.get_approvals()
    pending = [a for a in all_approvals if a.get("status") == "pending"]
    resolved = [a for a in all_approvals if a.get("status") in ("approved", "denied")]
    resolved.sort(key=lambda x: x.get("resolvedAt", ""), reverse=True)
    return {"pending": pending, "resolved": resolved}


@router.post("/api/v1/approvals/{approval_id}/approve")
def approve_request(approval_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin", "manager"])
    result = db.update_approval(approval_id, {
        "status": "approved",
        "reviewer": user.name,
        "resolvedAt": datetime.now(timezone.utc).isoformat(),
    })
    if not result:
        raise HTTPException(404, "Approval not found")
    return result


@router.post("/api/v1/approvals/{approval_id}/deny")
def deny_request(approval_id: str, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin", "manager"])
    result = db.update_approval(approval_id, {
        "status": "denied",
        "reviewer": user.name,
        "resolvedAt": datetime.now(timezone.utc).isoformat(),
    })
    if not result:
        raise HTTPException(404, "Approval not found")
