"""
Admin — IM Channels Management.

Endpoints:
  /api/v1/admin/im-channel-connections
  /api/v1/admin/im-channels
  /api/v1/admin/im-channels/health
  /api/v1/admin/im-channels/enrollment
  /api/v1/admin/im-channels/{channel}/unbind-all
  /api/v1/internal/im-binding-check
  /api/v1/admin/im-channels/{channel}/test
  /api/v1/admin/im-bot-info
"""

import os
import json
from datetime import datetime, timezone, timedelta

import boto3

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
from shared import require_role, GATEWAY_REGION, STACK_NAME

router = APIRouter(tags=["admin-im"])


def _run_openclaw_channels() -> list:
    """Get live channel status from openclaw channels list CLI.

    Uses text format (primary) because it includes per-channel
    'configured' / 'not configured' status that the JSON format lacks.
    """
    import re
    import subprocess as _sp
    from routers.openclaw_cli import find_openclaw_bin, openclaw_env_path
    openclaw_bin = find_openclaw_bin()
    env_path = openclaw_env_path()
    _ansi_re = re.compile(r'\x1b\[[0-9;]*m')
    try:
        result = _sp.run(
            ["sudo", "-u", "ubuntu", "env", f"PATH={env_path}", "HOME=/home/ubuntu",
             openclaw_bin, "channels", "list"],
            capture_output=True, text=True, timeout=15,
        )
        channels = []
        for line in result.stdout.splitlines():
            line = _ansi_re.sub('', line).strip()
            if not line.startswith("- "):
                continue
            parts = line[2:].split()
            ch_type = parts[0].lower() if parts else "unknown"
            configured = "not configured" not in line and "configured" in line
            linked = "not linked" not in line
            channels.append({
                "channel": ch_type,
                "account": "default",
                "configured": configured,
                "linked": linked,
            })
        if channels:
            return channels
    except Exception:
        pass
    return []


# =========================================================================
# Connections — per-channel employee connection table
# =========================================================================

@router.get("/api/v1/admin/im-channel-connections")
def get_im_channel_connections(authorization: str = Header(default="")):
    """Per-channel employee connection table for admin management."""
    require_role(authorization, roles=["admin"])
    try:
        raw_mappings = db.get_user_mappings()
        print(f"[im-connections] db.get_user_mappings returned {len(raw_mappings)} entries")

        emps = db.get_employees()
        emp_map = {e["id"]: e for e in emps}
        print(f"[im-connections] {len(emps)} employees loaded")

        session_counts: dict = {}
        last_active: dict = {}
        try:
            audit = db.get_audit_entries(limit=500)
            for a in audit:
                eid = a.get("actorId", "")
                if eid and a.get("eventType") == "agent_invocation":
                    session_counts[eid] = session_counts.get(eid, 0) + 1
                    ts = a.get("timestamp", "")
                    if ts > last_active.get(eid, ""):
                        last_active[eid] = ts
        except Exception as ae:
            print(f"[im-connections] audit fetch failed (non-fatal): {ae}")

        by_channel: dict = {}
        for m in raw_mappings:
            channel = m.get("channel", "")
            if channel in ("unknown", "unkn") or not channel:
                continue
            emp_id = m.get("employeeId", "")
            emp = emp_map.get(emp_id)
            if not emp:
                continue
            channel_user_id = m.get("channelUserId", "")
            by_channel.setdefault(channel, []).append({
                "empId": emp_id,
                "empName": emp.get("name", emp_id),
                "positionName": emp.get("positionName", ""),
                "departmentName": emp.get("departmentName", ""),
                "channelUserId": channel_user_id,
                "connectedAt": m.get("lastModified", ""),
                "sessionCount": session_counts.get(emp_id, 0),
                "lastActive": last_active.get(emp_id, ""),
            })

        print(f"[im-connections] result channels: {list(by_channel.keys())}, total: {sum(len(v) for v in by_channel.values())}")
        return {"connections": by_channel}

    except Exception as e:
        print(f"[im-connections] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"connections": {}, "error": str(e)}


# =========================================================================
# Channel List — live status + DynamoDB mapping counts
# =========================================================================

@router.get("/api/v1/admin/im-channels")
def get_im_channels(authorization: str = Header(default="")):
    """Get live IM channel status from Gateway + DynamoDB mapping counts."""
    require_role(authorization, roles=["admin", "manager"])

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

    # Get live Gateway channel status
    gateway_channels = _run_openclaw_channels()

    all_channels = [
        {"id": "telegram",   "label": "Telegram",          "enterprise": True},
        {"id": "discord",    "label": "Discord",            "enterprise": True},
        {"id": "slack",      "label": "Slack",              "enterprise": True},
        {"id": "teams",      "label": "Microsoft Teams",    "enterprise": True},
        {"id": "feishu",     "label": "Feishu / Lark",      "enterprise": True},
        {"id": "dingtalk",   "label": "DingTalk",           "enterprise": True},
        {"id": "googlechat", "label": "Google Chat",        "enterprise": True},
        {"id": "whatsapp",   "label": "WhatsApp",           "enterprise": True},
        {"id": "wechat",     "label": "WeChat",             "enterprise": False},
    ]

    gw_by_channel = {ch["channel"]: ch for ch in gateway_channels}
    result = []
    for ch in all_channels:
        gw = gw_by_channel.get(ch["id"], {})
        configured = bool(gw) and gw.get("configured", False)
        linked = bool(gw) and gw.get("linked", False)
        status = "connected" if (configured and linked) else \
                 "configured" if configured else "not_connected"
        result.append({
            **ch,
            "status": status,
            "connectedEmployees": channel_counts.get(ch["id"], 0),
        })
    return result


# =========================================================================
# Binding Check — internal endpoint for H2 Proxy
# =========================================================================

@router.get("/api/v1/internal/im-binding-check")
def im_binding_check(channel: str, channelUserId: str):
    """Internal endpoint called by H2 Proxy before routing each IM message.
    Strict enforcement: only respond to IM accounts that have a valid employee binding.
    No auth required — only accessible from the same EC2 (internal network)."""
    m = db.get_user_mapping(channel, channelUserId)
    if m and m.get("employeeId"):
        return {"bound": True, "employeeId": m["employeeId"]}
    # Fallback: scan MAPPING# for bare channelUserId (Feishu OU IDs, etc.)
    try:
        from boto3.dynamodb.conditions import Key as _KBC, Attr as _ABC
        ddb = boto3.resource("dynamodb", region_name=os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1")))
        table = ddb.Table(os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw")))
        resp = table.query(
            KeyConditionExpression=_KBC("PK").eq(db.ORG_PK) & _KBC("SK").begins_with("MAPPING#"),
            FilterExpression=_ABC("channelUserId").eq(channelUserId),
        )
        if resp.get("Items"):
            return {"bound": True, "employeeId": resp["Items"][0]["employeeId"]}
    except Exception:
        pass
    return {"bound": False}


# =========================================================================
# Test Connection
# =========================================================================

@router.post("/api/v1/admin/im-channels/{channel}/test")
def test_im_channel(channel: str, authorization: str = Header(default="")):
    """Test bot connection for a channel by asking OpenClaw if it has the channel configured."""
    require_role(authorization, roles=["admin"])
    try:
        channels = _run_openclaw_channels()
        if not channels:
            return {"ok": False, "error": "Could not reach OpenClaw CLI. Ensure openclaw gateway is running."}
        channel_key = channel.lower()
        for ch in channels:
            if ch.get("channel") == channel_key:
                if ch.get("configured"):
                    return {"ok": True, "botName": f"{channel} ({ch.get('account', 'default')})"}
                return {
                    "ok": False,
                    "error": f"{channel.capitalize()} bot not configured in OpenClaw. Open Gateway UI (port 18789) → Channels → Add {channel.capitalize()}.",
                }
        return {
            "ok": False,
            "error": f"{channel.capitalize()} plugin not enabled. Enable it in openclaw.json plugins → entries.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =========================================================================
# IM Bot Info Configuration
# =========================================================================

class IMBotInfoUpdate(BaseModel):
    botUsername: str = ""
    feishuAppId: str = ""
    deepLinkTemplate: str = ""
    webhookUrl: str = ""


@router.get("/api/v1/admin/im-bot-info")
def get_im_bot_info(authorization: str = Header(default="")):
    """Return IM bot info config (appIds, bot usernames, deep link templates)."""
    require_role(authorization, roles=["admin", "manager"])
    config = db.get_config("im-bot-info")
    if not config:
        return {"channels": {}}
    return {"channels": config.get("channels", {})}


@router.put("/api/v1/admin/im-bot-info/{channel}")
def set_im_bot_info(channel: str, body: IMBotInfoUpdate, authorization: str = Header(default="")):
    """Update bot info for a specific IM channel (feishuAppId, botUsername, etc.)."""
    require_role(authorization, roles=["admin"])
    config = db.get_config("im-bot-info") or {"channels": {}}
    channels = config.get("channels", {})
    existing = channels.get(channel, {})
    updates = body.model_dump(exclude_unset=True)
    existing.update(updates)
    channels[channel] = existing
    config["channels"] = channels
    db.set_config("im-bot-info", config)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": "admin",
        "actorName": "IT Admin",
        "targetType": "im-bot-info",
        "targetId": channel,
        "detail": f"Updated IM bot info for {channel}: {list(updates.keys())}",
        "status": "success",
    })
    return {"ok": True, "channel": channel, "config": existing}


# =========================================================================
# Channel Health — last activity per channel
# =========================================================================

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
        detail = e.get("detail", "").lower()
        ts = e.get("timestamp", "")
        for ch in ["telegram", "discord", "feishu", "slack", "whatsapp", "dingtalk", "teams", "googlechat", "portal"]:
            if ch in detail:
                if ts > last_by_channel.get(ch, ""):
                    last_by_channel[ch] = ts
                if ts >= cutoff:
                    count_24h[ch] = count_24h.get(ch, 0) + 1
                break
    return {"lastActivity": last_by_channel, "messagesLast24h": count_24h}


# =========================================================================
# Enrollment Stats — which employees are bound/unbound
# =========================================================================

@router.get("/api/v1/admin/im-channels/enrollment")
def get_im_enrollment_stats(authorization: str = Header(default="")):
    """Which employees are bound/unbound to IM channels."""
    require_role(authorization, roles=["admin", "manager"])
    emps = db.get_employees()
    mappings = db.get_user_mappings()

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
            continue
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


# =========================================================================
# Batch Unbind — disconnect all employees from a channel
# =========================================================================

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
