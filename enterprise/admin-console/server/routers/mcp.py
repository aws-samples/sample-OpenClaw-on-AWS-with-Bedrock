"""
MCP Connections — Validate & connect GitHub / ADO / Jira MCP servers.

Secrets are stored in AWS Secrets Manager.
The backend validates the secret exists and starts the MCP subprocess.

Endpoints: /api/v1/mcp/*
"""

import os
import subprocess
import threading
import time
import logging
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from shared import require_role

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp"])

# ── Config ────────────────────────────────────────────────────────────────────

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
STACK_NAME = os.environ.get("STACK_NAME", "openclaw")

# MCP server commands: provider_id → (command_args, required_env_keys)
MCP_SERVER_COMMANDS: Dict[str, dict] = {
    "github": {
        "cmd": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "required_env": ["GITHUB_TOKEN"],
    },
    "ado": {
        "cmd": ["npx", "-y", "@azure-devops/mcp-server"],
        "required_env": ["ADO_TOKEN", "ADO_ORG_URL"],
    },
    "jira": {
        "cmd": ["uvx", "mcp-atlassian"],
        "required_env": ["JIRA_URL", "JIRA_USER_EMAIL", "JIRA_API_TOKEN"],
    },
}

# In-memory process registry: provider_id → subprocess.Popen
_mcp_processes: Dict[str, subprocess.Popen] = {}
_lock = threading.Lock()

# ── Pydantic models ───────────────────────────────────────────────────────────

class SecretMapping(BaseModel):
    key: str          # env var name, e.g. GITHUB_TOKEN
    secretName: str   # secret path in AWS Secrets Manager, e.g. /openclaw/mcp/GITHUB_TOKEN


class ConnectRequest(BaseModel):
    provider: str
    secretNames: List[SecretMapping]


class DisconnectRequest(BaseModel):
    provider: str


class TestRequest(BaseModel):
    provider: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_secret(secret_name: str) -> str:
    """Fetch a secret value from AWS Secrets Manager."""
    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    try:
        resp = client.get_secret_value(SecretId=secret_name)
        return resp.get("SecretString", "")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "NoSuchEntityException"):
            raise HTTPException(
                status_code=404,
                detail=f"Secret not found: {secret_name}. Create it in AWS Secrets Manager first."
            )
        if code == "AccessDeniedException":
            raise HTTPException(
                status_code=403,
                detail=f"Access denied to secret {secret_name}. Check IAM permissions for secretsmanager:GetSecretValue."
            )
        raise HTTPException(status_code=500, detail=f"AWS error: {e.response['Error']['Message']}")


def _resolve_secrets(secret_mappings: List[SecretMapping]) -> Dict[str, str]:
    """Resolve all secret names → actual values from Secrets Manager."""
    resolved: Dict[str, str] = {}
    for mapping in secret_mappings:
        if not mapping.secretName.strip():
            raise HTTPException(
                status_code=400,
                detail=f"Secret name for {mapping.key} cannot be empty."
            )
        value = _get_secret(mapping.secretName.strip())
        if not value:
            raise HTTPException(
                status_code=400,
                detail=f"Secret {mapping.secretName} exists but is empty."
            )
        resolved[mapping.key] = value
    return resolved


def _kill_process(provider: str) -> None:
    """Stop a running MCP subprocess."""
    with _lock:
        proc = _mcp_processes.pop(provider, None)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info("MCP process for %s terminated", provider)


def _spawn_mcp_process(provider: str, env: Dict[str, str]) -> subprocess.Popen:
    """Start the MCP server subprocess with resolved env vars injected."""
    config = MCP_SERVER_COMMANDS.get(provider)
    if not config:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Build environment: inherit current + inject secrets
    child_env = os.environ.copy()
    child_env.update(env)

    logger.info("Spawning MCP server for provider=%s cmd=%s", provider, config["cmd"])
    proc = subprocess.Popen(
        config["cmd"],
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # MCP servers communicate over stdio; we leave stdin open
    )
    # Brief check to catch immediate startup failures
    time.sleep(0.8)
    if proc.poll() is not None:
        stderr_out = proc.stderr.read(512).decode(errors="replace")
        raise HTTPException(
            status_code=500,
            detail=f"MCP server for {provider} exited immediately. stderr: {stderr_out}"
        )
    return proc


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/mcp/connect")
async def mcp_connect(
    body: ConnectRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Validate secrets in AWS Secrets Manager and start the MCP server subprocess.
    Returns 200 if connected successfully.
    """
    require_role(authorization, ["admin", "manager"])

    provider = body.provider.lower()
    if provider not in MCP_SERVER_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    # Kill any existing process for this provider
    _kill_process(provider)

    # Resolve secrets from Secrets Manager (will raise HTTPException on failure)
    resolved_env = _resolve_secrets(body.secretNames)

    # Verify all required env keys are present
    required = MCP_SERVER_COMMANDS[provider]["required_env"]
    missing = [k for k in required if k not in resolved_env]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required secret mappings for {provider}: {missing}"
        )

    # Spawn the MCP server
    proc = _spawn_mcp_process(provider, resolved_env)

    with _lock:
        _mcp_processes[provider] = proc

    logger.info("MCP connected: provider=%s pid=%d", provider, proc.pid)
    return {
        "status": "connected",
        "provider": provider,
        "pid": proc.pid,
        "message": f"{provider.upper()} MCP server started (PID {proc.pid})",
    }


@router.post("/mcp/disconnect")
async def mcp_disconnect(
    body: DisconnectRequest,
    authorization: Optional[str] = Header(None),
):
    """Stop a running MCP server subprocess."""
    require_role(authorization, ["admin", "manager"])

    provider = body.provider.lower()
    was_running = provider in _mcp_processes
    _kill_process(provider)

    return {
        "status": "disconnected",
        "provider": provider,
        "wasRunning": was_running,
    }


@router.post("/mcp/test")
async def mcp_test(
    body: TestRequest,
    authorization: Optional[str] = Header(None),
):
    """Send a lightweight list_tools ping to verify the MCP process is healthy."""
    require_role(authorization, ["admin", "manager"])

    provider = body.provider.lower()
    with _lock:
        proc = _mcp_processes.get(provider)

    if not proc:
        raise HTTPException(
            status_code=409,
            detail=f"No MCP server running for {provider}. Connect first."
        )

    if proc.poll() is not None:
        with _lock:
            _mcp_processes.pop(provider, None)
        raise HTTPException(
            status_code=500,
            detail=f"MCP server for {provider} has stopped unexpectedly (exit code {proc.returncode})."
        )

    return {
        "status": "ok",
        "provider": provider,
        "pid": proc.pid,
        "message": f"{provider.upper()} MCP server is running (PID {proc.pid}) ✓",
    }


@router.get("/mcp/status")
async def mcp_status(authorization: Optional[str] = Header(None)):
    """Return the live status of all MCP server processes."""
    require_role(authorization, ["admin", "manager"])

    result = {}
    stale = []

    with _lock:
        snapshot = list(_mcp_processes.items())

    for provider, proc in snapshot:
        alive = proc.poll() is None
        if not alive:
            stale.append(provider)
        result[provider] = {
            "connected": alive,
            "pid": proc.pid if alive else None,
        }

    # Clean up stale entries outside the snapshot loop
    if stale:
        with _lock:
            for provider in stale:
                _mcp_processes.pop(provider, None)

    # Fill in not-connected providers
    for provider in MCP_SERVER_COMMANDS:
        if provider not in result:
            result[provider] = {"connected": False, "pid": None}

    return result
