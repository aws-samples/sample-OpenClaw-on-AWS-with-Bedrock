"""
Portal router — Employee Self-Service endpoints + IM Self-Service Pairing + Export.

Extracted from main.py lines ~1420-2179.
"""

import os
import json
import time
from datetime import datetime, timezone

import boto3
from fastapi import APIRouter, HTTPException, Header, UploadFile
from pydantic import BaseModel

import db
import s3ops
from shared import require_auth, require_role, ssm_client, GATEWAY_REGION, STACK_NAME, GATEWAY_ACCOUNT_ID, GATEWAY_INSTANCE_ID
from routers.bindings import _mapping_prefix, _write_user_mapping
from routers.agents import get_skills

router = APIRouter(tags=["portal"])

# =========================================================================
# Portal — IM Self-Service Pairing
# Flow: pair-start (Portal) -> employee scans QR -> bot receives /start TOKEN
#       -> H2 Proxy calls pair-complete -> SSM mapping written -> done
# =========================================================================

# Channel -> bot info map (used to build deep links shown to employees)
_CHANNEL_BOT_INFO = {
    "telegram": {
        "botUsername": os.environ.get("TELEGRAM_BOT_USERNAME", "acme_enterprise_bot"),
        "deepLinkTemplate": "https://t.me/{bot}?start={token}",
        "label": "Telegram",
    },
    "discord": {
        "botUsername": "ACME Agent",
        "deepLinkTemplate": None,
        "label": "Discord",
        "instructions": "Open Discord -> ACME Corp server -> DM ACME Agent -> send the command",
    },
    "feishu": {
        "botUsername": os.environ.get("FEISHU_BOT_NAME", "ACME Agent"),
        # Feishu deep link opens the bot chat directly (doesn't support token param)
        # User scans QR -> bot chat opens -> then manually sends /start TOKEN
        "deepLinkTemplate": "https://applink.feishu.cn/client/bot/open?appId={appId}",
        "feishuAppId": os.environ.get("FEISHU_APP_ID", "cli_a94cb611da399cdd"),
        "label": "Feishu / Lark",
    },
    "slack": {
        "botUsername": os.environ.get("SLACK_BOT_USERNAME", "acme-agent"),
        "deepLinkTemplate": None,
        "label": "Slack",
    },
}


class PairStartRequest(BaseModel):
    channel: str  # "telegram" | "discord" | "slack"


class PairCompleteRequest(BaseModel):
    channel: str
    channelUserId: str
    token: str


class PairPendingRequest(BaseModel):
    token: str
    channelUserId: str
    channel: str


class PortalChatMessage(BaseModel):
    message: str


class ProfileUpdateRequest(BaseModel):
    userMd: str


class PortalRequestCreate(BaseModel):
    type: str  # "tool" or "skill"
    resourceId: str
    resourceName: str
    reason: str = ""


# ── Local helper: run openclaw channels CLI ──────────────────────────────
def _run_openclaw_channels() -> list:
    """Get live channel status from openclaw channels list CLI."""
    import subprocess as _sp
    openclaw_bin = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin/openclaw"
    env_path = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin:/usr/local/bin:/usr/bin:/bin"
    try:
        result = _sp.run(
            ["sudo", "-u", "ubuntu", "env", f"PATH={env_path}", "HOME=/home/ubuntu",
             openclaw_bin, "channels", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout:
            raw = json.loads(result.stdout)
            channels = []
            for ch_type, accounts in raw.get("chat", {}).items():
                for account in accounts:
                    channels.append({"channel": ch_type, "account": account, "type": "chat"})
            return channels
    except Exception:
        pass
    # Fallback: parse openclaw channels list text output
    try:
        result = _sp.run(
            ["sudo", "-u", "ubuntu", "env", f"PATH={env_path}", "HOME=/home/ubuntu",
             openclaw_bin, "channels", "list"],
            capture_output=True, text=True, timeout=10,
        )
        channels = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("- ") and "default" in line:
                parts = line[2:].split()
                ch_type = parts[0].lower() if parts else "unknown"
                configured = "configured" in line
                linked = "not linked" not in line
                channels.append({
                    "channel": ch_type,
                    "account": "default",
                    "configured": configured,
                    "linked": linked,
                    "raw": line,
                })
        return channels
    except Exception:
        return []


# ── Helper: find channel user id (reverse lookup) ───────────────────────
def _find_channel_user_id(emp_id: str, channel_prefix: str) -> str:
    """Reverse lookup: given emp_id + channel, return the IM user_id."""
    try:
        prefix = _mapping_prefix()
        ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=10)
        for p in resp.get("Parameters", []):
            if p.get("Value") == emp_id:
                name = p["Name"].replace(prefix, "")
                if name.startswith(f"{channel_prefix}__"):
                    return name.replace(f"{channel_prefix}__", "")
        return ""
    except Exception:
        return ""


def _list_user_mappings_for_employee(emp_id: str, channel_prefix: str) -> bool:
    """Check if any SSM mapping exists for this employee on the given channel.
    Always uses us-east-1 (where agent container reads mappings from)."""
    try:
        prefix = _mapping_prefix()
        ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=10)
        for p in resp.get("Parameters", []):
            if p.get("Value") == emp_id and channel_prefix in p.get("Name", ""):
                return True
        return False
    except Exception:
        return False


# =========================================================================
# Endpoints
# =========================================================================

@router.post("/api/v1/portal/channel/pair-start")
def pair_start(body: PairStartRequest, authorization: str = Header(default="")):
    """Employee initiates IM pairing. Returns a token + deep link / QR data.
    Cancels any existing pending token for the same employee+channel so only
    one active token exists at a time."""
    user = require_auth(authorization)

    # Cancel existing pending tokens for this employee+channel (prevent token accumulation)
    try:
        from boto3.dynamodb.conditions import Key as _KPS, Attr as _APS
        ddb = boto3.resource("dynamodb", region_name=os.environ.get("DYNAMODB_REGION", "us-east-2"))
        table = ddb.Table(os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise"))
        resp = table.query(
            KeyConditionExpression=_KPS("PK").eq("ORG#acme") & _KPS("SK").begins_with("PAIR#"),
            FilterExpression=_APS("employeeId").eq(user.employee_id)
                & _APS("channel").eq(body.channel)
                & _APS("status").eq("pending"),
        )
        for old_item in resp.get("Items", []):
            table.update_item(
                Key={"PK": "ORG#acme", "SK": old_item["SK"]},
                UpdateExpression="SET #s = :cancelled",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":cancelled": "cancelled"},
            )
    except Exception as e:
        print(f"[pair-start] cancel old tokens failed (non-fatal): {e}")

    import secrets, string
    token = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))

    db.create_pair_token(token, user.employee_id, body.channel)

    bot_info = _CHANNEL_BOT_INFO.get(body.channel, {})
    bot_username = bot_info.get("botUsername", "")
    template = bot_info.get("deepLinkTemplate")
    # Feishu uses appId in the deep link, not bot username or token
    if template and "{appId}" in template:
        app_id = bot_info.get("feishuAppId", "")
        deep_link = template.format(appId=app_id) if app_id else None
    elif template:
        deep_link = template.format(bot=bot_username, token=token) if template else None
    else:
        deep_link = None

    return {
        "token": token,
        "deepLink": deep_link,
        "botUsername": bot_username,
        "channel": body.channel,
        "expiresIn": 900,  # 15 minutes
    }


@router.get("/api/v1/portal/im-channel-status")
def portal_im_channel_status(authorization: str = Header(default="")):
    """Return which IM channels the admin has configured via OpenClaw Gateway.
    Used by employee portal to show 'available' vs 'admin not configured'."""
    require_auth(authorization)
    channels = _run_openclaw_channels()
    configured = set()
    for ch in channels:
        name = (ch.get("channel") or ch.get("id", "")).lower()
        if name:
            configured.add(name)
    return {"configured": sorted(configured)}


@router.get("/api/v1/portal/channel/pair-status")
def pair_status(token: str, authorization: str = Header(default="")):
    """Poll pairing status. Returns pending / completed / expired."""
    require_auth(authorization)
    import time as _t
    item = db.get_pair_token(token)
    if not item:
        return {"status": "not_found"}
    if item.get("ttl", 0) < int(_t.time()):
        return {"status": "expired"}
    return {"status": item.get("status", "pending")}


@router.post("/api/v1/bindings/pair-pending")
def pair_pending(body: PairPendingRequest):
    """Called by H2 Proxy on /start TOKEN -- validates token and returns employee info
    for the YES/NO confirmation message. Does NOT consume the token.
    H2 Proxy caches the result and calls pair-complete only after YES."""
    import time as _t
    item = db.get_pair_token(body.token)
    if not item:
        return {"valid": False, "reason": "not_found"}
    if item.get("ttl", 0) < int(_t.time()):
        return {"valid": False, "reason": "expired"}
    if item.get("status") not in ("pending",):
        return {"valid": False, "reason": "already_used"}

    emp_id = item["employeeId"]

    # Check: is this IM userId already bound to a DIFFERENT employee?
    existing = db.get_user_mapping(body.channel, body.channelUserId)
    if existing and existing.get("employeeId") != emp_id:
        other_emps = db.get_employees()
        other_emp = next((e for e in other_emps if e["id"] == existing["employeeId"]), None)
        return {
            "valid": False,
            "reason": "already_bound_other",
            "boundTo": other_emp.get("name", existing["employeeId"]) if other_emp else existing["employeeId"],
        }

    emps = db.get_employees()
    emp = next((e for e in emps if e["id"] == emp_id), {})
    is_rebind = existing is not None and existing.get("employeeId") == emp_id

    return {
        "valid": True,
        "employeeId": emp_id,
        "employeeName": emp.get("name", emp_id),
        "positionName": emp.get("positionName", ""),
        "isRebind": is_rebind,
    }


@router.post("/api/v1/bindings/pair-complete")
def pair_complete(body: PairCompleteRequest):
    """Called by H2 Proxy after employee confirms YES.
    No auth -- called from internal network only (H2 Proxy on same EC2).
    Consumes token, writes DynamoDB MAPPING# + SSM, logs audit entry."""
    item = db.consume_pair_token(body.token)
    if not item:
        raise HTTPException(400, "Token invalid, already used, or expired")

    emp_id = item["employeeId"]
    channel = item.get("channel", body.channel)

    # Safety check: reject if this IM userId is already bound to a DIFFERENT employee.
    # pair-pending already checks this, but pair-complete is the final gate in case the
    # H2 Proxy bypasses pair-pending (e.g. old proxy code, retry, race condition).
    existing = db.get_user_mapping(channel, body.channelUserId)
    if existing and existing.get("employeeId") and existing["employeeId"] != emp_id:
        raise HTTPException(409, f"This {channel} account is already bound to another employee. Disconnect it first.")

    # Write DynamoDB MAPPING# (primary, used by tenant_router and workspace_assembler)
    try:
        db.create_user_mapping(channel, body.channelUserId, emp_id)
    except Exception as e:
        print(f"[pair-complete] DynamoDB MAPPING# write failed: {e}")

    # Write SSM (dual-write for backward compat during transition)
    _ssm_pair = boto3.client("ssm", region_name=GATEWAY_REGION)
    _prefix = _mapping_prefix()
    for key in [f"{channel}__{body.channelUserId}", body.channelUserId]:
        try:
            _ssm_pair.put_parameter(Name=f"{_prefix}{key}", Value=emp_id, Type="String", Overwrite=True)
        except Exception as e:
            print(f"[pair-complete] SSM write failed key={key}: {e}")

    # Update DynamoDB employee record so portal/channels reflects this immediately
    try:
        db.add_employee_channel(emp_id, channel)
    except Exception as e:
        print(f"[pair-complete] DynamoDB channel update failed (non-fatal): {e}")

    # Resolve employee name for confirmation message
    emps = db.get_employees()
    emp = next((e for e in emps if e["id"] == emp_id), {})

    # Audit log
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": emp_id,
        "actorName": emp.get("name", emp_id),
        "targetType": "binding",
        "targetId": f"{channel}__{body.channelUserId}",
        "detail": f"IM pairing self-service: {channel} {body.channelUserId} -> {emp_id}",
        "status": "success",
    })

    return {
        "success": True,
        "employeeName": emp.get("name", emp_id),
        "employeeId": emp_id,
        "positionName": emp.get("positionName", ""),
        "channel": channel,
    }


@router.post("/api/v1/portal/upload")
async def portal_upload(
    file: UploadFile,
    authorization: str = Header(default=""),
):
    """Upload a file from the employee portal. Text files have their content returned
    so the agent can read them inline. Binary / image files are stored to S3."""
    user = require_auth(authorization)

    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"
    raw = await file.read()
    size = len(raw)

    # Determine if we can extract text content
    text_extensions = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".sh",
                       ".yaml", ".yml", ".xml", ".html", ".sql", ".log", ".env",
                       ".toml", ".cfg", ".ini", ".java", ".go", ".rs", ".rb", ".php"}
    ext = os.path.splitext(filename)[1].lower()
    is_text = ext in text_extensions or content_type.startswith("text/")

    content_preview: str | None = None
    if is_text:
        try:
            text = raw.decode("utf-8", errors="replace")
            # Limit to 8KB inline (agent context window)
            content_preview = text[:8192]
            if len(text) > 8192:
                content_preview += f"\n\n[... truncated — {len(text)} chars total]"
        except Exception:
            pass

    # Store file in S3
    ts = int(time.time())
    s3_key = f"{user.employee_id}/workspace/uploads/{ts}_{filename}"
    try:
        boto3.client("s3").put_object(
            Bucket=s3ops.bucket(),
            Key=s3_key,
            Body=raw,
            ContentType=content_type,
        )
    except Exception as e:
        print(f"[upload] S3 write failed: {e}")

    s3_uri = f"s3://{s3ops.bucket()}/{s3_key}"
    return {
        "filename": filename,
        "size": size,
        "type": content_type,
        "isText": is_text,
        "contentPreview": content_preview,
        "s3Key": s3_key,
        "s3Uri": s3_uri,
    }


@router.post("/api/v1/portal/chat")
def portal_chat(body: PortalChatMessage, authorization: str = Header(default="")):
    """Employee sends message to their bound agent via Tenant Router."""
    user = require_auth(authorization)

    # Find employee's 1:1 binding
    bindings = db.get_bindings()
    my_binding = next((b for b in bindings if b.get("employeeId") == user.employee_id and b.get("mode") == "1:1"), None)
    if not my_binding:
        raise HTTPException(404, "No agent bound. Contact IT to provision your agent.")

    # Route through Tenant Router -> AgentCore
    router_url = os.environ.get("TENANT_ROUTER_URL", "http://localhost:8090")
    try:
        import requests as _req
        r = _req.post(f"{router_url}/route", json={
            "channel": "portal",
            "user_id": user.employee_id,
            "message": body.message,
        }, timeout=180)
        if r.status_code == 200:
            data = r.json()
            agent_response = data.get("response", {})

            # Handle both nested and flat response structures
            if isinstance(agent_response, dict):
                reply = agent_response.get("response", str(agent_response))
            else:
                reply = str(agent_response)

            # Audit trail
            db.create_audit_entry({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "eventType": "agent_invocation",
                "actorId": user.employee_id,
                "actorName": user.name,
                "targetType": "agent",
                "targetId": my_binding.get("agentId", ""),
                "detail": f"Portal chat: {body.message[:80]}",
                "status": "success",
            })

            # Detect if response came from always-on container vs AgentCore Runtime
            stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
            source = "agentcore"
            try:
                _ssm_src = boto3.client("ssm", region_name=GATEWAY_REGION)
                _ssm_src.get_parameter(
                    Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
                source = "always-on"
            except Exception:
                pass

            return {
                "response": reply,
                "agentId": my_binding.get("agentId"),
                "agentName": my_binding.get("agentName"),
                "source": source,
                "model": agent_response.get("model", "") if isinstance(agent_response, dict) else "",
            }
        else:
            print(f"[portal-chat] Tenant Router returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[portal-chat] Error calling Tenant Router: {e}")

    # Fallback when AgentCore is unreachable
    # Read employee's workspace to provide context-aware response
    soul_content = s3ops.read_file(f"_shared/soul/global/SOUL.md") or ""
    pos_soul = s3ops.read_file(f"_shared/soul/positions/{user.position_id}/SOUL.md") or ""
    user_md = s3ops.read_file(f"{user.employee_id}/workspace/USER.md") or ""

    context_parts = []
    if pos_soul:
        context_parts.append(f"[Position SOUL loaded: {len(pos_soul)} chars]")
    if user_md:
        context_parts.append(f"[USER.md loaded: {len(user_md)} chars]")

    return {
        "response": f"I'm your {my_binding.get('agentName', 'AI assistant')}. I'm currently running in offline mode (AgentCore unavailable).\n\n{''.join(context_parts)}\n\nPlease try again later, or use your messaging channel ({my_binding.get('channel', 'Slack')}) for full agent capabilities.",
        "agentId": my_binding.get("agentId"),
        "agentName": my_binding.get("agentName"),
        "source": "fallback",
    }


@router.get("/api/v1/portal/profile")
def portal_profile(authorization: str = Header(default="")):
    """Get employee's profile including USER.md preferences."""
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    if not emp:
        raise HTTPException(404)

    user_md = s3ops.read_file(f"{user.employee_id}/workspace/USER.md") or ""
    memory_md = s3ops.read_file(f"{user.employee_id}/workspace/MEMORY.md") or ""
    agent = db.get_agent(emp.get("agentId", ""))

    # Return first 2KB of MEMORY.md so portal can show "what agent remembers"
    memory_preview = memory_md[:2048] if memory_md else None

    # Determine deployment mode and IM connection info
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    is_always_on = False
    deploy_mode = agent.get("deployMode", "serverless") if agent else "serverless"
    always_on_agent_id = None
    dedicated_bot_info = {}

    try:
        ssm_ao = boto3.client("ssm", region_name=GATEWAY_REGION)
        param = ssm_ao.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
        always_on_agent_id = param["Parameter"]["Value"]
        is_always_on = True
        deploy_mode = "always-on-ecs"

        # Check if dedicated IM bots are configured (Plan A direct IM)
        for ch, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
            try:
                ssm_ao.get_parameter(
                    Name=f"/openclaw/{stack}/always-on/{always_on_agent_id}/{key}")
                dedicated_bot_info[ch] = "configured"
            except Exception:
                dedicated_bot_info[ch] = "not_configured"
    except Exception:
        pass

    # Check for EKS deployment (eks-endpoint SSM param)
    if deploy_mode == "serverless":
        try:
            ssm_eks = boto3.client("ssm", region_name=GATEWAY_REGION)
            ssm_eks.get_parameter(
                Name=f"/openclaw/{stack}/tenants/{user.employee_id}/eks-endpoint")
            deploy_mode = "eks"
            is_always_on = True
        except Exception:
            pass

    return {
        "employee": emp,
        "agent": agent,
        "userMd": user_md,
        "memoryMdSize": len(memory_md),
        "dailyMemoryCount": len(s3ops.list_files(f"{user.employee_id}/workspace/memory/")),
        "memoryPreview": memory_preview,
        "isAlwaysOn": is_always_on,
        "deployMode": deploy_mode,            # "serverless" | "always-on-ecs" | "eks"
        "alwaysOnAgentId": always_on_agent_id,
        "dedicatedBots": dedicated_bot_info,  # which channels have dedicated bot tokens
        "imConnectionMode": (
            "direct" if is_always_on and any(v == "configured" for v in dedicated_bot_info.values())
            else "shared-gateway"             # both modes start with shared gateway
        ),
    }


@router.put("/api/v1/portal/profile")
def update_portal_profile(body: ProfileUpdateRequest, authorization: str = Header(default="")):
    """Update employee's USER.md preferences."""
    user = require_auth(authorization)
    s3ops.write_file(f"{user.employee_id}/workspace/USER.md", body.userMd)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "employee",
        "targetId": user.employee_id,
        "detail": "Updated personal preferences (USER.md)",
        "status": "success",
    })
    return {"saved": True}


@router.get("/api/v1/portal/usage")
def portal_usage(authorization: str = Header(default="")):
    """Get employee's personal usage stats."""
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    agent_id = emp.get("agentId", "") if emp else ""

    usage_records = db.get_usage_for_agent(agent_id) if agent_id else []
    total_input = sum(u.get("inputTokens", 0) for u in usage_records)
    total_output = sum(u.get("outputTokens", 0) for u in usage_records)
    total_requests = sum(u.get("requests", 0) for u in usage_records)
    total_cost = sum(float(u.get("cost", 0)) for u in usage_records)

    return {
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalRequests": total_requests,
        "totalCost": round(total_cost, 4),
        "dailyUsage": [{
            "date": u.get("date"),
            "requests": u.get("requests", 0),
            "cost": float(u.get("cost", 0)),
        } for u in sorted(usage_records, key=lambda x: x.get("date", ""))],
    }


@router.get("/api/v1/portal/skills")
def portal_skills(authorization: str = Header(default="")):
    """Get employee's available and restricted skills."""
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    agent = db.get_agent(emp.get("agentId", "")) if emp else None
    agent_skills = agent.get("skills", []) if agent else []

    # Get all skills from S3
    all_skills = get_skills()  # reuse existing endpoint logic
    available = [s for s in all_skills if s.get("name", s.get("id", "")).replace("sk-", "") in agent_skills or s.get("permissions", {}).get("allowedRoles", ["*"]) == ["*"]]
    restricted = [s for s in all_skills if s not in available]

    return {"available": available, "restricted": restricted}


@router.get("/api/v1/portal/requests")
def portal_requests(authorization: str = Header(default="")):
    """Get employee's approval requests."""
    user = require_auth(authorization)
    all_approvals = db.get_approvals()
    my_pending = [a for a in all_approvals if a.get("tenantId", "").endswith(user.employee_id.replace("emp-", "")) and a.get("status") == "pending"]
    my_resolved = [a for a in all_approvals if a.get("tenantId", "").endswith(user.employee_id.replace("emp-", "")) and a.get("status") != "pending"]
    return {"pending": my_pending, "resolved": my_resolved}


@router.post("/api/v1/portal/requests/create")
def portal_request_create(body: PortalRequestCreate, authorization: str = Header(default="")):
    """Employee self-service: create a tool/skill access request."""
    user = require_auth(authorization)
    emp = db.get_employee(user.employee_id)
    emp_name = emp.get("name", user.employee_id) if emp else user.employee_id

    request_id = f"req-{user.employee_id}-{body.resourceId}-{int(time.time())}"
    db.create_approval({
        "id": request_id,
        "type": "permission_request",
        "status": "pending",
        "tenantId": f"portal__{user.employee_id}",
        "employeeId": user.employee_id,
        "employeeName": emp_name,
        "tool": body.resourceName,
        "resource": body.resourceId,
        "reason": body.reason or f"Employee requested access to: {body.resourceName}",
        "requestedAt": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "permission_request",
        "actorId": user.employee_id,
        "actorName": emp_name,
        "targetType": body.type,
        "targetId": body.resourceId,
        "detail": f"Employee self-service request: {body.resourceName} — {body.reason}",
        "status": "pending",
    })
    return {"created": True, "requestId": request_id}


@router.delete("/api/v1/portal/channels/{channel}")
def portal_channel_disconnect(channel: str, authorization: str = Header(default="")):
    """Employee self-service disconnect -- deletes SSM mapping for their IM channel."""
    user = require_auth(authorization)
    channel_user_id = _find_channel_user_id(user.employee_id, channel)
    if not channel_user_id:
        raise HTTPException(404, f"No {channel} connection found for your account")
    # Delete the mapping
    # Delete from DynamoDB MAPPING# (primary)
    try:
        db.delete_user_mapping(channel, channel_user_id)
    except Exception as e:
        print(f"[disconnect] DynamoDB MAPPING# delete failed (non-fatal): {e}")

    # Delete from SSM (backward compat dual-write cleanup)
    ssm_del = boto3.client("ssm", region_name=GATEWAY_REGION)
    prefix = _mapping_prefix()
    for key in [f"{channel}__{channel_user_id}", channel_user_id]:
        try:
            ssm_del.delete_parameter(Name=f"{prefix}{key}")
        except Exception:
            pass

    # Remove from DynamoDB employee channels list
    try:
        db.remove_employee_channel(user.employee_id, channel)
    except Exception as e:
        print(f"[disconnect] DynamoDB channel remove failed (non-fatal): {e}")
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "binding",
        "targetId": f"{channel}__{channel_user_id}",
        "detail": f"Employee self-service disconnected {channel} ({channel_user_id})",
        "status": "success",
    })
    return {"disconnected": True, "channel": channel}


@router.get("/api/v1/portal/channels")
def portal_channels(authorization: str = Header(default="")):
    """Return connected IM channels plus mode-aware pairing instructions.

    Returns:
    - connected: list of connected channels
    - deployMode: "serverless" | "always-on-ecs"
    - pairingMode: "shared-gateway" | "direct" (direct = dedicated bot per Plan A)
    - pairingInstructions: per-channel guidance based on deploy mode
    """
    user = require_auth(authorization)
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")

    # Determine deploy mode
    is_always_on = False
    is_eks = False
    eks_endpoint = None
    always_on_agent_id = None
    dedicated_bots = {}
    try:
        ssm_ch = boto3.client("ssm", region_name=GATEWAY_REGION)
        param = ssm_ch.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
        always_on_agent_id = param["Parameter"]["Value"]
        is_always_on = True
        for ch, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
            try:
                ssm_ch.get_parameter(
                    Name=f"/openclaw/{stack}/always-on/{always_on_agent_id}/{key}")
                dedicated_bots[ch] = True
            except Exception:
                dedicated_bots[ch] = False
    except Exception:
        pass

    # Check for EKS deployment
    if not is_always_on:
        try:
            ssm_eks = boto3.client("ssm", region_name=GATEWAY_REGION)
            ep = ssm_eks.get_parameter(
                Name=f"/openclaw/{stack}/tenants/{user.employee_id}/eks-endpoint")
            eks_endpoint = ep["Parameter"]["Value"]
            is_eks = True
        except Exception:
            pass

    # Get connected channels
    connected = []
    try:
        emp = db.get_employee(user.employee_id)
        if emp:
            db_channels = [c for c in emp.get("channels", []) if c not in ("portal",)]
            if db_channels:
                connected = db_channels
    except Exception:
        pass
    if not connected:
        for channel_prefix in ["telegram", "discord", "slack", "whatsapp", "feishu"]:
            if _list_user_mappings_for_employee(user.employee_id, channel_prefix):
                connected.append(channel_prefix)

    # Build pairing instructions based on mode
    has_dedicated = any(dedicated_bots.values())
    pairing_mode = "direct" if is_always_on and has_dedicated else "shared-gateway"

    # Channel-specific pairing instructions
    ch_instructions = {
        "telegram": "Scan the QR code or click the link to open a chat with the company bot. Send /start to complete pairing.",
        "discord": "Click the invite link to add the company bot to your server, then DM the bot and send /start.",
        "whatsapp": "Scan the QR code with your phone's WhatsApp. The pairing link expires in 5 minutes.",
        "feishu": "Open the Feishu bot link and send a message to start pairing.",
        "slack": "Open the Slack bot link and send /start in a DM to pair.",
    }

    # Override with dedicated bot instructions for channels that have tokens
    if has_dedicated:
        for ch, has_token in dedicated_bots.items():
            if has_token:
                ch_instructions[ch] = f"Your agent has a dedicated {ch.title()} bot. Messages go directly to your ECS agent — no pairing needed."

    # Mode banner text
    if is_eks:
        ch_instructions["mode_note"] = (
            "Your agent runs on EKS (instant response, no cold start). "
            "Open the Gateway Console below to manage your IM connections directly."
        )
    elif is_always_on and has_dedicated:
        ch_instructions["mode_note"] = (
            "Your agent runs on ECS Fargate with a dedicated IM bot. "
            "Messages go directly to your persistent agent — instant response, no cold start."
        )
    elif is_always_on:
        ch_instructions["mode_note"] = (
            "Your agent runs on ECS Fargate (instant response, no cold start). "
            "Connect via the company bot below — your messages are routed to your dedicated agent container."
        )
    else:
        ch_instructions["mode_note"] = (
            "Your agent starts on demand when you send a message. "
            "Connect via the company bot below — first message may take a few seconds."
        )

    instructions = ch_instructions

    # For always-on: resolve container IP, instance ID, and gateway tokens for SSM port-forward
    agent_ip = None
    gw_token = None
    dashboard_token = None
    instance_id = GATEWAY_INSTANCE_ID if GATEWAY_INSTANCE_ID else None
    if is_always_on and always_on_agent_id:
        try:
            ep = ssm_ch.get_parameter(
                Name=f"/openclaw/{stack}/always-on/{always_on_agent_id}/endpoint"
            )["Parameter"]["Value"]
            import re
            m = re.search(r'(\d+\.\d+\.\d+\.\d+)', ep)
            if m:
                agent_ip = m.group(1)
        except Exception:
            pass
        try:
            gw_token = ssm_ch.get_parameter(
                Name=f"/openclaw/{stack}/always-on/{always_on_agent_id}/gateway-token",
                WithDecryption=True,
            )["Parameter"]["Value"]
        except Exception:
            pass
        try:
            dashboard_token = ssm_ch.get_parameter(
                Name=f"/openclaw/{stack}/always-on/{always_on_agent_id}/dashboard-token",
            )["Parameter"]["Value"]
        except Exception:
            pass

    deploy_mode = "eks" if is_eks else ("always-on-ecs" if is_always_on else "serverless")

    return {
        "connected": connected,
        "deployMode": deploy_mode,
        "pairingMode": pairing_mode,
        "pairingInstructions": instructions,
        "dedicatedBots": dedicated_bots,
        "alwaysOnAgentId": always_on_agent_id,
        "eksEndpoint": eks_endpoint,
        "agentIp": agent_ip,
        "instanceId": instance_id,
        "gatewayToken": gw_token,
        "dashboardToken": dashboard_token,
    }


# =========================================================================
# Data Export
# =========================================================================

@router.get("/api/v1/export/agent/{agent_id}")
def export_agent(agent_id: str):
    """Export agent configuration as downloadable package."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    pos_id = agent.get("positionId", "")
    emp_id = agent.get("employeeId")

    # Gather all files
    soul_layers = s3ops.get_soul_layers(pos_id, emp_id)
    memory = s3ops.get_agent_memory(emp_id) if emp_id else {}

    return {
        "agent": agent,
        "soul": soul_layers,
        "memory": memory,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "format": "openclaw-workspace",
        "note": "This export can be imported into any OpenClaw instance",
    }
