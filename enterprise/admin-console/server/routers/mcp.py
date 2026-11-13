"""
MCP Connections — Validate & record GitHub / ADO / Jira MCP server bindings.

Credentials are kept in AWS Secrets Manager under openclaw/mcp/*.
The admin console validates secret existence and records bindings in DynamoDB.
At container start / reload, agent containers read bindings and resolve tokens
directly into their OpenClaw configuration (mcpServers).

Endpoints: /api/v1/mcp/*
"""

import os
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
from shared import require_role, bump_config_version

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp"])

# ── Config & Definitions ──────────────────────────────────────────────────────

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
STACK_NAME = os.environ.get("STACK_NAME", "openclaw")
SECRET_PREFIX = "openclaw/mcp/"

# Supported MCP server specs with pinned packages and strictly allowed env vars
MCP_PROVIDERS: Dict[str, dict] = {
    "github": {
        "name": "GitHub",
        "cmd": ["npx", "-y", "@modelcontextprotocol/server-github@2025.4.8"],
        "required_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    },
    "ado": {
        "name": "Azure DevOps",
        "cmd": ["npx", "-y", "@azure-devops/mcp@2.10.0"],
        "required_env": ["AZURE_DEVOPS_TOKEN", "AZURE_DEVOPS_ORG_URL"],
    },
    "azure-devops": {
        "name": "Azure DevOps",
        "cmd": ["npx", "-y", "@azure-devops/mcp@2.10.0"],
        "required_env": ["AZURE_DEVOPS_TOKEN", "AZURE_DEVOPS_ORG_URL"],
    },
    "jira": {
        "name": "Jira",
        "cmd": ["uvx", "--from", "mcp-atlassian==0.23.1", "mcp-atlassian"],
        "required_env": ["JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"],
    },
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class SecretMapping(BaseModel):
    key: str          # env var name, restricted to provider's required_env
    secretName: str   # secret name in AWS Secrets Manager, must start with openclaw/mcp/


class ConnectRequest(BaseModel):
    provider: str
    secretNames: List[SecretMapping]


class DisconnectRequest(BaseModel):
    provider: str


class TestRequest(BaseModel):
    provider: str


# ── Security & Validation Helpers ─────────────────────────────────────────────

def _validate_and_normalize_secret_name(secret_name: str) -> str:
    """Enforce fixed secret prefix openclaw/mcp/ without leading slash."""
    s = secret_name.strip()
    if s.startswith("/"):
        s = s.lstrip("/")
    if not s.startswith(SECRET_PREFIX):
        raise HTTPException(
            status_code=400,
            detail=f"Secret name must start with '{SECRET_PREFIX}' (e.g. openclaw/mcp/github_token)"
        )
    if ".." in s or "\\" in s or any(c.isspace() for c in s):
        raise HTTPException(
            status_code=400,
            detail="Invalid secret name format."
        )
    return s


def _check_secret_exists(secret_name: str) -> None:
    """Validate that the secret exists and is accessible in AWS Secrets Manager.
    Logs errors server-side and returns generic sanitized client messages."""
    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    try:
        # describe_secret checks existence and permissions without pulling the secret payload
        client.describe_secret(SecretId=secret_name)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "NoSuchEntityException"):
            logger.warning("Secret not found: %s", secret_name)
            raise HTTPException(
                status_code=404,
                detail=f"Secret not found in AWS Secrets Manager: {secret_name}. Create it first."
            )
        if code == "AccessDeniedException":
            logger.warning("Access denied to secret: %s", secret_name)
            raise HTTPException(
                status_code=403,
                detail="Access denied to secret in AWS Secrets Manager. Check IAM permissions."
            )
        logger.error("AWS Secrets Manager error for %s: %s", secret_name, code)
        raise HTTPException(
            status_code=500,
            detail="Failed to validate secret in AWS Secrets Manager."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error validating secret %s: %s", secret_name, e)
        raise HTTPException(
            status_code=500,
            detail="Failed to validate secret."
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/v1/mcp/connect")
async def mcp_connect(
    body: ConnectRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Validate secrets in AWS Secrets Manager and record binding in DynamoDB.
    Restricted to admin role only.
    Enforces fixed secret prefix and strictly whitelisted provider env keys.
    Triggers config version bump to notify running agent containers.
    """
    user = require_role(authorization, ["admin"])

    provider = body.provider.lower()
    if provider not in MCP_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    spec = MCP_PROVIDERS[provider]
    allowed_keys = set(spec["required_env"])

    # Validate secret mappings against strict provider env keys
    secret_map: Dict[str, str] = {}
    for mapping in body.secretNames:
        key = mapping.key.strip()
        if key not in allowed_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid env key '{key}' for provider '{provider}'. Allowed keys: {sorted(allowed_keys)}"
            )
        sec_name = _validate_and_normalize_secret_name(mapping.secretName)
        _check_secret_exists(sec_name)
        secret_map[key] = sec_name

    # Ensure all required keys for the provider are supplied
    missing = [k for k in spec["required_env"] if k not in secret_map]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required secret mappings for {provider}: {missing}"
        )

    # Record binding in DynamoDB CONFIG#mcp-servers
    current_config = db.get_config("mcp-servers") or {}
    servers = current_config.get("servers", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    servers[provider] = {
        "enabled": True,
        "provider": provider,
        "secrets": secret_map,
        "updatedAt": now_iso,
        "updatedBy": user.employee_id,
    }
    # Keep ado and azure-devops keys in sync
    if provider == "ado":
        servers["azure-devops"] = {
            "enabled": True,
            "provider": "azure-devops",
            "secrets": secret_map,
            "updatedAt": now_iso,
            "updatedBy": user.employee_id,
        }
    elif provider == "azure-devops":
        servers["ado"] = {
            "enabled": True,
            "provider": "ado",
            "secrets": secret_map,
            "updatedAt": now_iso,
            "updatedBy": user.employee_id,
        }

    db.set_config("mcp-servers", {"servers": servers, "updatedAt": now_iso})

    # Propagate config change to agent containers
    bump_config_version()

    # Record audit entry
    db.create_audit_entry({
        "timestamp": now_iso,
        "eventType": "mcp_connect",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "mcp_server",
        "targetId": provider,
        "detail": f"Connected MCP server for {provider} with secret keys: {sorted(secret_map.keys())}",
        "status": "success",
    })

    logger.info("MCP server configured: provider=%s by=%s", provider, user.employee_id)
    return {
        "status": "connected",
        "provider": provider,
        "message": f"{provider.upper()} MCP server binding recorded and configured ✓",
    }


@router.post("/api/v1/mcp/disconnect")
async def mcp_disconnect(
    body: DisconnectRequest,
    authorization: Optional[str] = Header(None),
):
    """Disable MCP server binding and notify running agent containers."""
    user = require_role(authorization, ["admin"])

    provider = body.provider.lower()
    current_config = db.get_config("mcp-servers") or {}
    servers = current_config.get("servers", {})

    was_connected = False
    providers_to_update = [provider]
    if provider == "ado":
        providers_to_update.append("azure-devops")
    elif provider == "azure-devops":
        providers_to_update.append("ado")

    for p in providers_to_update:
        if p in servers and servers[p].get("enabled"):
            was_connected = True
            servers[p]["enabled"] = False
            servers[p]["updatedAt"] = datetime.now(timezone.utc).isoformat()
            servers[p]["updatedBy"] = user.employee_id

    now_iso = datetime.now(timezone.utc).isoformat()
    db.set_config("mcp-servers", {"servers": servers, "updatedAt": now_iso})

    bump_config_version()

    # Record audit entry
    db.create_audit_entry({
        "timestamp": now_iso,
        "eventType": "mcp_disconnect",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "mcp_server",
        "targetId": provider,
        "detail": f"Disconnected MCP server for {provider}",
        "status": "success",
    })

    logger.info("MCP server disconnected: provider=%s by=%s", provider, user.employee_id)
    return {
        "status": "disconnected",
        "provider": provider,
        "wasRunning": was_connected,
    }


@router.post("/api/v1/mcp/test")
async def mcp_test(
    body: TestRequest,
    authorization: Optional[str] = Header(None),
):
    """Validate that the configured secret mappings for the provider still exist and are accessible."""
    user = require_role(authorization, ["admin"])

    provider = body.provider.lower()
    if provider not in MCP_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    current_config = db.get_config("mcp-servers") or {}
    servers = current_config.get("servers", {})
    entry = servers.get(provider)

    if not entry or not entry.get("enabled"):
        raise HTTPException(
            status_code=409,
            detail=f"No active MCP server binding for {provider}. Connect first."
        )

    secrets = entry.get("secrets", {})
    for _, sec_name in secrets.items():
        _check_secret_exists(sec_name)

    return {
        "status": "ok",
        "provider": provider,
        "message": f"{provider.upper()} MCP configuration and secrets verified ✓",
    }


@router.get("/api/v1/mcp/status")
async def mcp_status(authorization: Optional[str] = Header(None)):
    """Return configured status of all MCP server providers."""
    user = require_role(authorization, ["admin"])

    current_config = db.get_config("mcp-servers") or {}
    servers = current_config.get("servers", {})

    result = {}
    for p in ["github", "ado", "azure-devops", "jira"]:
        entry = servers.get(p, {})
        connected = bool(entry.get("enabled", False))
        result[p] = {
            "connected": connected,
            "secrets": entry.get("secrets", {}),
            "updatedAt": entry.get("updatedAt", ""),
            "updatedBy": entry.get("updatedBy", ""),
        }

    return result
