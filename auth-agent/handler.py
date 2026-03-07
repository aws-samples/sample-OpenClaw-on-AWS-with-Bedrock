"""
Authorization_Agent handler — approval notification and Human-in-the-Loop flow.

Requirements: 9.3, 9.4, 9.7, 9.9
"""

import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Optional

import boto3

try:
    from .permission_request import PermissionRequest
except ImportError:
    from permission_request import PermissionRequest  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input validation for approval messages
# ---------------------------------------------------------------------------

# Patterns that indicate prompt injection in approval responses
_APPROVAL_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"new\s+system\s+prompt", re.IGNORECASE),
    re.compile(r"approve\s+all\s+(pending|future)", re.IGNORECASE),
    re.compile(r"grant\s+(all|unlimited|full)\s+(access|permissions?)", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
]

MAX_APPROVAL_MESSAGE_LENGTH = 2000
MAX_REASON_LENGTH = 500


def validate_approval_input(message: str) -> str:
    """Validate an approval response from Human_Approver.

    Checks for:
    - Message length (max 2000 chars)
    - Prompt injection patterns
    - Returns sanitized message

    Raises ValueError if injection detected.
    """
    if len(message) > MAX_APPROVAL_MESSAGE_LENGTH:
        logger.warning("Approval message truncated: %d > %d", len(message), MAX_APPROVAL_MESSAGE_LENGTH)
        message = message[:MAX_APPROVAL_MESSAGE_LENGTH]

    for pattern in _APPROVAL_INJECTION_PATTERNS:
        match = pattern.search(message)
        if match:
            logger.warning(
                "[auth-agent] INJECTION_BLOCKED pattern=%r matched=%r",
                pattern.pattern, match.group(0)[:60],
            )
            raise ValueError(f"Approval message rejected: suspicious pattern detected")

    return message


def validate_permission_request_fields(payload: dict) -> dict:
    """Validate fields in an incoming PermissionRequest payload.

    Checks:
    - tenant_id: alphanumeric + underscore/hyphen/dot, max 128 chars
    - resource: no null bytes, no path traversal, max 512 chars
    - reason: max 500 chars, no injection patterns
    - resource_type: must be one of allowed values
    """
    import re as _re

    tenant_id = payload.get("tenant_id", "")
    if not _re.match(r"^[a-zA-Z0-9_.\-]{1,128}$", tenant_id):
        raise ValueError(f"Invalid tenant_id: {tenant_id!r}")

    resource = payload.get("resource", "")
    if len(resource) > 512:
        raise ValueError("Resource too long")
    if "\x00" in resource:
        raise ValueError("Null byte in resource")
    if ".." in resource.split("/"):
        raise ValueError("Path traversal in resource")

    reason = payload.get("reason", "")
    if len(reason) > MAX_REASON_LENGTH:
        payload["reason"] = reason[:MAX_REASON_LENGTH]

    allowed_types = {"tool", "data_path", "api_endpoint"}
    if payload.get("resource_type") not in allowed_types:
        raise ValueError(f"Invalid resource_type: {payload.get('resource_type')}")

    return payload


# ---------------------------------------------------------------------------
# SSM system prompt (Requirement 9.9)
# ---------------------------------------------------------------------------

STACK_NAME = os.environ.get("STACK_NAME", "dev")
_SYSTEM_PROMPT_SSM_PATH = f"/openclaw/{STACK_NAME}/auth-agent/system-prompt"
_DEFAULT_SYSTEM_PROMPT = (
    "You are the Authorization Agent. Review permission requests carefully."
)


def _ssm_client():
    """Factory for the SSM boto3 client — mockable in tests."""
    return boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def load_system_prompt() -> str:
    """Read the system prompt from SSM Parameter Store.

    Falls back to the hardcoded default if SSM is unavailable or the
    parameter does not exist, so the agent keeps working without SSM.

    Requirement: 9.9
    """
    path = _SYSTEM_PROMPT_SSM_PATH
    try:
        ssm = _ssm_client()
        response = ssm.get_parameter(Name=path)
        return response["Parameter"]["Value"]
    except Exception as e:
        logger.warning(
            "[auth-agent] SSM system prompt unavailable path=%s error=%s — using default",
            path,
            e,
        )
        return _DEFAULT_SYSTEM_PROMPT


def get_system_prompt() -> str:
    """Return the current system prompt, re-reading SSM on every call (hot-reload).

    Requirement: 9.9
    """
    return load_system_prompt()


# ---------------------------------------------------------------------------
# In-memory store for pending requests
# ---------------------------------------------------------------------------
_pending_requests: dict[str, PermissionRequest] = {}
_timers: dict[str, threading.Timer] = {}

# ---------------------------------------------------------------------------
# Risk assessment
# ---------------------------------------------------------------------------

_LOW_RISK_TOOLS = {"web_search"}
_MEDIUM_RISK_TOOLS = {"file_write", "code_execution"}
_HIGH_RISK_TOOLS = {"shell"}

_LOW_RISK_KEYWORDS = {"read", "public", "readonly"}
_HIGH_RISK_KEYWORDS = {"system", "/etc/", "/var/", "/usr/", "/bin/", "/sbin/"}


def assess_risk_level(request: PermissionRequest) -> str:
    """Return '低', '中', or '高' based on the requested resource."""
    resource = request.resource.lower()
    resource_type = request.resource_type

    if resource_type == "tool":
        if resource in _HIGH_RISK_TOOLS:
            return "高"
        if resource in _MEDIUM_RISK_TOOLS:
            return "中"
        if resource in _LOW_RISK_TOOLS:
            return "低"
        # Unknown tool — default to medium
        return "中"

    # data_path or api_endpoint
    if request.duration_type == "persistent":
        return "高"
    if any(kw in resource for kw in _HIGH_RISK_KEYWORDS):
        return "高"
    if any(kw in resource for kw in _LOW_RISK_KEYWORDS):
        return "低"
    return "中"

# ---------------------------------------------------------------------------
# Risk descriptions
# ---------------------------------------------------------------------------

_RISK_DESCRIPTIONS = {
    "低": "该操作属于低风险只读或公开访问，对系统安全影响有限。",
    "中": "该操作涉及文件写入或代码执行，可能对系统状态产生影响，请谨慎审批。",
    "高": "该操作属于高风险操作（如 shell 执行或系统路径访问），可能对系统安全造成严重影响，强烈建议仅授予临时权限。",
}

# ---------------------------------------------------------------------------
# Notification formatting
# ---------------------------------------------------------------------------


def format_approval_notification(request: PermissionRequest) -> str:
    """Return the formatted approval notification string for Human_Approver."""
    risk = assess_risk_level(request)
    risk_desc = _RISK_DESCRIPTIONS[risk]

    if request.duration_type == "temporary" and request.suggested_duration_hours:
        duration_str = f"临时（{request.suggested_duration_hours} 小时）"
        approve_temp_label = f"✅ 批准（临时）- 授权 {request.suggested_duration_hours} 小时"
    elif request.duration_type == "temporary":
        duration_str = "临时（1 小时）"
        approve_temp_label = "✅ 批准（临时）- 授权 1 小时"
    else:
        duration_str = "持久"
        approve_temp_label = "✅ 批准（临时）- 授权 1 小时"

    resource_type_label = {
        "tool": "工具",
        "data_path": "数据路径",
        "api_endpoint": "API 端点",
    }.get(request.resource_type, request.resource_type)

    lines = [
        "🔐 **权限申请通知**",
        "",
        f"**申请人**：{request.tenant_id}",
        f"**申请资源**：{request.resource}（{resource_type_label}）",
        f"**申请原因**：{request.reason}",
        f"**建议时效**：{duration_str}",
        f"**风险等级**：{risk}",
        "",
        f"**风险说明**：{risk_desc}",
        "",
        "**请回复以下选项之一**：",
        approve_temp_label,
        "✅ 批准（持久）- 永久加入白名单",
        "⚠️ 部分批准 - 请说明限制条件",
        "❌ 拒绝 - 请说明原因（可选）",
        "",
        "⏰ 30 分钟内未回复将自动拒绝。",
    ]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Notification sending (abstracted — actual channel integration out of scope)
# ---------------------------------------------------------------------------


def _send_notification(message: str, tenant_id: str) -> None:
    """Send a notification message to the Human_Approver channel.

    The actual WhatsApp/Telegram integration is out of scope; we log the
    message so it is visible in CloudWatch Logs.
    """
    logger.info(
        "[auth-agent] NOTIFICATION tenant_id=%s message=%s",
        tenant_id,
        message,
    )


# ---------------------------------------------------------------------------
# Agent Container notification
# ---------------------------------------------------------------------------


def _notify_agent_container(request_id: str, status: str, reason: Optional[str] = None) -> None:
    """Notify the originating Agent Container of the approval outcome."""
    logger.info(
        "[auth-agent] AGENT_NOTIFY request_id=%s status=%s reason=%s",
        request_id,
        status,
        reason or "",
    )


# ---------------------------------------------------------------------------
# Auto-reject on timeout
# ---------------------------------------------------------------------------


def auto_reject(request_id: str) -> None:
    """Called by the 30-minute timer when Human_Approver has not replied."""
    request = _pending_requests.pop(request_id, None)
    _timers.pop(request_id, None)

    if request is None:
        # Already handled (approved/rejected) before timeout fired
        return

    request.status = "timeout"

    logger.warning(
        "[auth-agent] AUTO_REJECT request_id=%s tenant_id=%s resource=%s reason=timeout",
        request_id,
        request.tenant_id,
        request.resource,
    )

    _notify_agent_container(request_id, "timeout", reason="30 分钟内未收到审批回复，已自动拒绝。")

    # Optionally notify the Human_Approver that the request timed out
    timeout_msg = (
        f"⏰ 权限申请已超时自动拒绝。\n"
        f"申请人：{request.tenant_id}\n"
        f"申请资源：{request.resource}\n"
        f"申请 ID：{request_id}"
    )
    _send_notification(timeout_msg, request.tenant_id)

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def handle_permission_request(request: PermissionRequest) -> dict:
    """Process an incoming PermissionRequest.

    1. Load the system prompt (hot-reload from SSM on each call).
    2. Format the approval notification.
    3. Store the request in the pending dict.
    4. Send the notification to the Human_Approver channel.
    5. Start a 30-minute timer that calls auto_reject on expiry.

    Returns a dict with the request_id, notification message, and SSM prompt path.
    """
    # Hot-reload system prompt on every request (Requirement 9.9)
    get_system_prompt()

    notification = format_approval_notification(request)

    # Store in pending store
    _pending_requests[request.request_id] = request
    request.status = "pending"

    logger.info(
        "[auth-agent] PENDING request_id=%s tenant_id=%s resource=%s",
        request.request_id,
        request.tenant_id,
        request.resource,
    )

    # Send notification to Human_Approver
    _send_notification(notification, request.tenant_id)

    # Start 30-minute auto-reject timer
    timer = threading.Timer(TIMEOUT_SECONDS, auto_reject, args=(request.request_id,))
    timer.daemon = True
    timer.start()
    _timers[request.request_id] = timer

    return {
        "request_id": request.request_id,
        "status": "pending",
        "notification": notification,
        "expires_at": request.expires_at.isoformat(),
        "system_prompt_path": _SYSTEM_PROMPT_SSM_PATH,
    }


# ---------------------------------------------------------------------------
# Pending list query (for /pending approvals)
# ---------------------------------------------------------------------------


def list_pending_requests() -> list[dict]:
    """Return a summary of all pending requests for Human_Approver queries."""
    now = datetime.now(timezone.utc)
    result = []
    for idx, (rid, req) in enumerate(_pending_requests.items(), start=1):
        # Make expires_at timezone-aware if it isn't already
        expires_at = req.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        requested_at = req.requested_at
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=timezone.utc)

        waited = now - requested_at
        remaining = expires_at - now
        result.append(
            {
                "index": idx,
                "request_id": rid,
                "tenant_id": req.tenant_id,
                "resource": req.resource,
                "waited_seconds": max(0, int(waited.total_seconds())),
                "remaining_seconds": max(0, int(remaining.total_seconds())),
            }
        )
    return result


def format_pending_list(requests: list) -> str:
    """Format a list of pending request dicts as a human-readable string.

    Each item is expected to have the keys returned by list_pending_requests():
    index, tenant_id, resource, waited_seconds, remaining_seconds.

    Returns a Chinese-language summary suitable for sending via a message channel.

    Requirement: 9.8
    """
    if not requests:
        return "当前没有待审批的权限申请"

    lines = [f"待审批列表（共 {len(requests)} 项）："]
    for item in requests:
        waited_min = item["waited_seconds"] // 60
        remaining_min = item["remaining_seconds"] // 60
        lines.append(
            f"{item['index']}. 申请人：{item['tenant_id']} | "
            f"资源：{item['resource']} | "
            f"等待：{waited_min}分钟 | "
            f"剩余：{remaining_min}分钟"
        )
    return "\n".join(lines)


def handle_pending_approvals_command() -> str:
    """Handle the '/pending approvals' command from Human_Approver.

    Queries the current pending list and returns a formatted string.

    Requirement: 9.8
    """
    requests = list_pending_requests()
    return format_pending_list(requests)
