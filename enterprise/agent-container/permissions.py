"""
Permission profile management.

Reads per-tenant permission profiles from DynamoDB (position toolAllowlist).
Profiles are injected into openclaw's system prompt (Plan A enforcement).
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

STACK_NAME = os.environ.get("STACK_NAME", "dev")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
DYNAMODB_REGION = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))

DEFAULT_PROFILE = {
    "profile": "basic",
    "tools": ["web_search"],
    "data_permissions": {"file_paths": [], "api_endpoints": []},
}

# Always blocked for standard agents — arbitrary code execution risk.
# Exec profile bypasses Plan A entirely so this only applies to non-exec.
ALWAYS_BLOCKED_TOOLS = {"load_extension", "eval"}


class PermissionDeniedError(Exception):
    def __init__(self, tenant_id: str, tool: str, resource: Optional[str] = None):
        self.tenant_id = tenant_id
        self.tool = tool
        self.resource = resource
        super().__init__(f"Permission denied: tenant={tenant_id} tool={tool}")


def _base_tenant_id(tenant_id: str) -> str:
    """Extract base employee ID from session tenant_id.

    Tenant IDs can be:
      channel__emp_id__hash  → emp_id   (e.g. tg__emp-wjd__abc123)
      channel__emp_id        → emp_id   (e.g. port__emp-wjd)
      emp-wjd                → emp-wjd  (direct)
    Permissions are stored per position in DynamoDB, resolved via employee record.
    """
    parts = tenant_id.split("__")
    if len(parts) >= 2:
        return parts[1]
    return tenant_id


def read_permission_profile(tenant_id: str) -> dict:
    """Read tenant's permission profile from DynamoDB.

    Resolution: tenant_id → emp_id → EMP#{emp_id}.positionId → POS#{pos}.toolAllowlist
    Falls back to basic profile if lookup fails.
    """
    base_id = _base_tenant_id(tenant_id)
    try:
        ddb = boto3.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)

        # Get employee's position
        emp_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{base_id}"})
        emp_item = emp_resp.get("Item", {})
        pos_id = emp_item.get("positionId", "")

        if pos_id:
            # Get position's tool allowlist
            pos_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"POS#{pos_id}"})
            pos_item = pos_resp.get("Item", {})
            tools = pos_item.get("toolAllowlist", ["web_search"])
            role = pos_id.replace("pos-", "")
            return {
                "profile": role,
                "role": role,
                "tools": tools,
                "data_permissions": {"file_paths": [], "api_endpoints": []},
            }
    except Exception as e:
        logger.warning("DynamoDB permission lookup failed for %s: %s", base_id, e)

    return dict(DEFAULT_PROFILE)


def _log_permission_denied(tenant_id: str, tool_name: str, resource: Optional[str]) -> None:
    logger.warning("AUDIT %s", json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "log_stream": f"tenant_{tenant_id}",
        "tenant_id": tenant_id,
        "event_type": "permission_denied",
        "tool_name": tool_name,
        "resource": resource,
    }))
    # Write to DynamoDB AUDIT# for Audit Center visibility
    try:
        import time as _time_perm
        ddb = boto3.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        ts = datetime.now(timezone.utc).isoformat()
        base_id = _base_tenant_id(tenant_id)
        table.put_item(Item={
            "PK": "ORG#acme",
            "SK": f"AUDIT#perm-{int(_time_perm.time()*1000)}",
            "GSI1PK": "TYPE#audit",
            "GSI1SK": f"AUDIT#perm-{int(_time_perm.time()*1000)}",
            "eventType": "permission_denied",
            "actorId": base_id,
            "actorName": base_id,
            "targetType": "tool",
            "targetId": tool_name,
            "detail": f"Tool '{tool_name}' denied for {base_id}"
                      + (f" (resource: {resource})" if resource else ""),
            "status": "blocked",
            "timestamp": ts,
        })
    except Exception:
        pass  # non-fatal — CloudWatch log is primary record


def check_tool_permission(
    tenant_id: str, tool_name: str, resource: Optional[str] = None
) -> bool:
    """Check tool permission against SSM profile. Raises PermissionDeniedError if denied."""
    if tool_name in ALWAYS_BLOCKED_TOOLS:
        _log_permission_denied(tenant_id, tool_name, resource)
        raise PermissionDeniedError(tenant_id=tenant_id, tool=tool_name, resource=resource)

    profile = read_permission_profile(tenant_id)
    if tool_name not in profile.get("tools", []):
        _log_permission_denied(tenant_id, tool_name, resource)
        raise PermissionDeniedError(tenant_id=tenant_id, tool=tool_name, resource=resource)
    return True


def check_data_permission(tenant_id: str, data_path: str) -> bool:
    """Check data path permission against SSM profile. Raises PermissionDeniedError if denied."""
    profile = read_permission_profile(tenant_id)
    allowed_paths = profile.get("data_permissions", {}).get("file_paths", [])

    def _normalise(p: str) -> str:
        return p.rstrip("*").rstrip("/") + "/"

    for allowed in allowed_paths:
        if data_path.startswith(_normalise(allowed)):
            return True

    _log_permission_denied(tenant_id, "data_access", data_path)
    raise PermissionDeniedError(tenant_id=tenant_id, tool="data_access", resource=data_path)


# ---------------------------------------------------------------------------
# Authorization Agent integration
# ---------------------------------------------------------------------------

AUTH_AGENT_RUNTIME_ID = os.environ.get("AUTH_AGENT_RUNTIME_ID", "")

_auth_agent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth-agent")
if _auth_agent_path not in sys.path:
    sys.path.insert(0, _auth_agent_path)

try:
    from permission_request import PermissionRequest  # noqa: E402
except ImportError:
    PermissionRequest = None  # type: ignore


def _agentcore_client():
    return boto3.client(
        "bedrock-agentcore-runtime",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def send_permission_request(
    tenant_id: str,
    tool_name: str,
    resource: Optional[str] = None,
    reason: str = "Permission required",
    duration_type: str = "temporary",
    suggested_duration_hours: Optional[int] = 1,
):
    """Send a PermissionRequest to the Authorization Agent."""
    now = datetime.now(timezone.utc)
    request = PermissionRequest(
        request_id=str(uuid4()),
        tenant_id=tenant_id,
        resource_type="tool",
        resource=resource or tool_name,
        reason=reason,
        duration_type=duration_type,
        suggested_duration_hours=suggested_duration_hours,
        requested_at=now,
        expires_at=now + timedelta(minutes=30),
        status="pending",
    )

    session_id = f"auth-agent-{STACK_NAME}"
    payload = {
        "request_id": request.request_id,
        "tenant_id": request.tenant_id,
        "resource_type": request.resource_type,
        "resource": request.resource,
        "reason": request.reason,
        "duration_type": request.duration_type,
        "suggested_duration_hours": request.suggested_duration_hours,
        "requested_at": request.requested_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
        "status": request.status,
    }

    try:
        _agentcore_client().invoke_agent_runtime(
            agentRuntimeId=AUTH_AGENT_RUNTIME_ID,
            sessionId=session_id,
            payload=json.dumps(payload),
        )
        logger.info(
            "PermissionRequest sent request_id=%s tenant_id=%s session_id=%s",
            request.request_id, tenant_id, session_id,
        )
    except Exception as e:
        logger.error("Failed to send PermissionRequest request_id=%s error=%s", request.request_id, e)

    return request
