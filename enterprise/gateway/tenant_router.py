"""
Gateway Tenant Router — bridges OpenClaw Gateway and AgentCore Runtime.

Runs as an HTTP proxy on EC2 alongside the OpenClaw Gateway process.
OpenClaw's webhook forwards incoming messages here; this module:
  1. Derives a tenant_id from the channel + user identity
  2. Invokes AgentCore Runtime with sessionId=tenant_id
  3. Returns the agent response to OpenClaw for delivery

Design decisions:
  - tenant_id format: {channel}__{user_id} (e.g. "wa__8613800138000")
  - Stateless: all state lives in AgentCore Runtime sessions and SSM
  - Graceful fallback: if AgentCore is unreachable, returns error (no local fallback)
"""

import hashlib
import json
import logging
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STACK_NAME = os.environ.get("STACK_NAME", "dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
DYNAMODB_REGION = os.environ.get("DYNAMODB_REGION", "us-east-2")
RUNTIME_ID = os.environ.get("AGENTCORE_RUNTIME_ID", "")
ROUTER_PORT = int(os.environ.get("ROUTER_PORT", "8090"))

# Per-tenant runtime cache (TTL 5 min)
_runtime_cache: dict = {}
_runtime_cache_ts: dict = {}
_RUNTIME_CACHE_TTL = 300

# Routing config cache (full CONFIG#routing item)
_routing_config: dict = {}
_routing_config_ts: float = 0.0
_ROUTING_CONFIG_TTL = 300


def _get_routing_config() -> dict:
    """Read routing config from DynamoDB CONFIG#routing, cached 5 min."""
    global _routing_config, _routing_config_ts
    now = time.time()
    if _routing_config and now - _routing_config_ts < _ROUTING_CONFIG_TTL:
        return _routing_config
    try:
        ddb = boto3.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        resp = table.get_item(Key={"PK": "ORG#acme", "SK": "CONFIG#routing"})
        item = resp.get("Item", {})
        _routing_config = {
            "position_runtime": item.get("position_runtime", {}),
            "employee_override": item.get("employee_override", {}),
        }
        _routing_config_ts = now
        logger.info("Routing config loaded from DynamoDB: %d positions, %d overrides",
                    len(_routing_config["position_runtime"]),
                    len(_routing_config["employee_override"]))
    except Exception as e:
        logger.warning("DynamoDB routing config load failed, using cached/empty: %s", e)
        if not _routing_config:
            _routing_config = {"position_runtime": {}, "employee_override": {}}
    return _routing_config


def _resolve_emp_id(raw_id: str, channel: str) -> str:
    """Resolve a raw user ID to employee ID via DynamoDB MAPPING# (fallback: SSM)."""
    if raw_id.startswith("emp-"):
        return raw_id
    # Try DynamoDB: MAPPING#{channel}__{raw_id}
    try:
        ddb = boto3.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"MAPPING#{channel}__{raw_id}"})
        item = resp.get("Item")
        if item:
            return item.get("employeeId", "")
        # Try bare userId (no channel prefix)
        resp2 = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq("ORG#acme")
            & boto3.dynamodb.conditions.Key("SK").begins_with("MAPPING#"),
            FilterExpression=boto3.dynamodb.conditions.Attr("channelUserId").eq(raw_id),
        )
        items = resp2.get("Items", [])
        if items:
            return items[0].get("employeeId", "")
    except Exception as e:
        logger.debug("DynamoDB user-mapping lookup failed: %s", e)
    # SSM fallback
    try:
        ssm = boto3.client("ssm", region_name=AWS_REGION)
        for key in [f"{channel}__{raw_id}", raw_id]:
            try:
                resp = ssm.get_parameter(
                    Name=f"/openclaw/{STACK_NAME}/user-mapping/{key}")
                return resp["Parameter"]["Value"]
            except Exception:
                pass
    except Exception:
        pass
    return ""


def _get_position_for_emp(emp_id: str) -> str:
    """Get positionId for an employee from DynamoDB (fallback: SSM)."""
    try:
        ddb = boto3.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{emp_id}"})
        item = resp.get("Item")
        if item and item.get("positionId"):
            return item["positionId"]
    except Exception as e:
        logger.debug("DynamoDB employee lookup failed: %s", e)
    # SSM fallback
    try:
        ssm = boto3.client("ssm", region_name=AWS_REGION)
        resp = ssm.get_parameter(Name=f"/openclaw/{STACK_NAME}/tenants/{emp_id}/position")
        return resp["Parameter"]["Value"]
    except Exception:
        return ""


def _get_runtime_id_for_tenant(base_id: str) -> str:
    """Resolve runtime ID for a tenant — 3-tier chain via DynamoDB (SSM fallback).

    1. Employee override  DynamoDB CONFIG#routing.employee_override[emp_id]
    2. Position rule      DynamoDB CONFIG#routing.position_runtime[positionId]
    3. Default            AGENTCORE_RUNTIME_ID env var
    """
    now = time.time()
    cache_key = f"runtime__{base_id}"
    if cache_key in _runtime_cache and now - _runtime_cache_ts.get(cache_key, 0) < _RUNTIME_CACHE_TTL:
        return _runtime_cache[cache_key]

    cfg = _get_routing_config()

    # Tier 1: employee override
    if base_id in cfg.get("employee_override", {}):
        runtime = cfg["employee_override"][base_id]
        _runtime_cache[cache_key] = runtime
        _runtime_cache_ts[cache_key] = now
        logger.info("Runtime (employee override DDB) %s → %s", base_id, runtime)
        return runtime

    # Tier 2: position-level
    pos_id = _get_position_for_emp(base_id)
    if pos_id and pos_id in cfg.get("position_runtime", {}):
        runtime = cfg["position_runtime"][pos_id]
        _runtime_cache[cache_key] = runtime
        _runtime_cache_ts[cache_key] = now
        logger.info("Runtime (position DDB %s) %s → %s", pos_id, base_id, runtime)
        return runtime

    # SSM fallback (backward compat during transition)
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    try:
        resp = ssm.get_parameter(Name=f"/openclaw/{STACK_NAME}/tenants/{base_id}/runtime-id")
        runtime = resp["Parameter"]["Value"]
        _runtime_cache[cache_key] = runtime
        _runtime_cache_ts[cache_key] = now
        logger.info("Runtime (employee SSM fallback) %s → %s", base_id, runtime)
        return runtime
    except Exception:
        pass
    if pos_id:
        try:
            resp = ssm.get_parameter(Name=f"/openclaw/{STACK_NAME}/positions/{pos_id}/runtime-id")
            runtime = resp["Parameter"]["Value"]
            _runtime_cache[cache_key] = runtime
            _runtime_cache_ts[cache_key] = now
            logger.info("Runtime (position SSM fallback %s) %s → %s", pos_id, base_id, runtime)
            return runtime
        except Exception:
            pass

    # Tier 3: default
    logger.info("Runtime (default) %s → %s", base_id, RUNTIME_ID)
    return RUNTIME_ID

# Tenant ID validation: alphanumeric, underscores, hyphens, dots
_TENANT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")

# Channel name normalization
_CHANNEL_ALIASES = {
    "whatsapp": "wa",
    "telegram": "tg",
    "discord": "dc",
    "slack": "sl",
    "teams": "ms",
    "imessage": "im",
    "googlechat": "gc",
    "webchat": "web",
    "playground": "pgnd",
    "twin": "twin",
}


# ---------------------------------------------------------------------------
# Tenant ID derivation
# ---------------------------------------------------------------------------

def derive_tenant_id(channel: str, user_id: str) -> str:
    """Derive a stable, safe tenant_id from channel and user identity.

    Format: {channel_short}__{sanitized_user_id}__{hash_suffix}
    
    AgentCore requires runtimeSessionId >= 33 chars, so we append a hash
    suffix to guarantee minimum length while keeping the ID human-readable.

    Examples:
      - ("whatsapp", "8613800138000") → "wa__8613800138000__a1b2c3d4e5f6"
      - ("telegram", "123456789")     → "tg__123456789__f7e8d9c0b1a2"
    """
    channel_short = _CHANNEL_ALIASES.get(channel.lower(), channel.lower()[:4])
    sanitized = re.sub(r"[^a-zA-Z0-9_.\-]", "_", user_id.strip())

    # Hash suffix ensures minimum 33 chars for AgentCore runtimeSessionId
    # Stable across days — Session Storage persists across stop/resume cycles
    hash_suffix = hashlib.sha256(f"{channel}:{user_id}".encode()).hexdigest()[:19]
    tenant_id = f"{channel_short}__{sanitized}__{hash_suffix}"

    # Pad to 33 chars minimum if still too short
    while len(tenant_id) < 33:
        tenant_id += "0"

    if len(tenant_id) > 128:
        tenant_id = f"{channel_short}__{hash_suffix}"

    if not _TENANT_ID_PATTERN.match(tenant_id):
        raise ValueError(f"Invalid tenant_id derived: {tenant_id}")

    return tenant_id


# ---------------------------------------------------------------------------
# AgentCore Runtime invocation
# ---------------------------------------------------------------------------

def _agentcore_client():
    from botocore.config import Config
    cfg = Config(
        read_timeout=300,
        connect_timeout=10,
        retries={"max_attempts": 0},
    )
    return boto3.client("bedrock-agentcore", region_name=AWS_REGION, config=cfg)


def invoke_agent_runtime(
    tenant_id: str,
    message: str,
    model: Optional[str] = None,
) -> dict:
    """Invoke AgentCore Runtime with tenant isolation.

    In production: calls AgentCore Runtime API (Firecracker microVM per tenant).
    In demo mode: calls local Agent Container directly (AGENT_CONTAINER_URL env var).

    Args:
        tenant_id: Derived tenant identifier, used as sessionId
        message: User message text
        model: Optional model override

    Returns:
        Agent response dict with 'response' key

    Raises:
        RuntimeError: If invocation fails
    """
    # Demo mode: call local Agent Container directly
    local_url = os.environ.get("AGENT_CONTAINER_URL")
    if local_url:
        return _invoke_local_container(local_url, tenant_id, message, model)

    # Production mode: call AgentCore Runtime API
    # Check for per-tenant runtime override (e.g. Executive Runtime for pos-exec)
    parts = tenant_id.split("__")
    base_id = parts[1] if len(parts) >= 2 else tenant_id
    effective_runtime = _get_runtime_id_for_tenant(base_id) or RUNTIME_ID

    if not effective_runtime:
        raise RuntimeError(
            "AGENTCORE_RUNTIME_ID not configured. "
            "Set it in SSM or environment after creating the AgentCore Runtime."
        )

    return _invoke_agentcore(tenant_id, message, model, runtime_id_override=effective_runtime)


def _invoke_local_container(
    base_url: str, tenant_id: str, message: str, model: Optional[str]
) -> dict:
    """Call a local Agent Container server.py directly (demo/testing mode)."""
    import requests

    payload = {
        "sessionId": tenant_id,
        "tenant_id": tenant_id,
        "message": message,
    }
    if model:
        payload["model"] = model

    start = time.time()
    try:
        resp = requests.post(
            f"{base_url}/invocations",
            json=payload,
            timeout=300,
        )
        duration_ms = int((time.time() - start) * 1000)

        if resp.status_code == 200:
            logger.info(
                "Local container invocation tenant_id=%s duration_ms=%d status=success",
                tenant_id, duration_ms,
            )
            return resp.json()
        else:
            logger.error(
                "Local container invocation failed tenant_id=%s status=%d body=%s",
                tenant_id, resp.status_code, resp.text[:200],
            )
            raise RuntimeError(f"Agent Container returned {resp.status_code}: {resp.text[:200]}")

    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Agent Container not reachable at {base_url}: {e}") from e


def _invoke_agentcore(tenant_id: str, message: str, model: Optional[str],
                      runtime_id_override: Optional[str] = None) -> dict:
    """Call AgentCore Runtime API (production mode).
    runtime_id_override allows routing to a different runtime (e.g. Executive Runtime)."""
    import json as _json

    payload = {
        "sessionId": tenant_id,
        "message": message,
    }
    if model:
        payload["model"] = model

    effective_runtime_id = runtime_id_override or RUNTIME_ID

    # Get the Runtime ARN — construct from known pattern to avoid needing control plane permissions
    runtime_arn = os.environ.get("AGENTCORE_RUNTIME_ARN", "") if not runtime_id_override else ""
    if not runtime_arn:
        # Construct ARN from runtime ID + region + account
        try:
            sts = boto3.client("sts", region_name=AWS_REGION)
            account_id = sts.get_caller_identity()["Account"]
            runtime_arn = f"arn:aws:bedrock-agentcore:{AWS_REGION}:{account_id}:runtime/{effective_runtime_id}"
            logger.info("Constructed runtime ARN: %s", runtime_arn)
        except Exception as e:
            logger.error("Could not construct runtime ARN: %s", e)
            raise RuntimeError(f"Cannot determine runtime ARN: {e}") from e

    start = time.time()
    try:
        client = _agentcore_client()
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=tenant_id,
            contentType="application/json",
            accept="application/json",
            payload=_json.dumps(payload).encode(),
        )

        # Response body key is 'response' (StreamingBody), not 'body' or 'payload'
        result_bytes = response.get("response", response.get("payload", response.get("body", b"")))
        if hasattr(result_bytes, "read"):
            result_bytes = result_bytes.read()
        if isinstance(result_bytes, str):
            result_bytes = result_bytes.encode()
        result = json.loads(result_bytes) if result_bytes else {}
        duration_ms = int((time.time() - start) * 1000)

        logger.info(
            "AgentCore invocation tenant_id=%s duration_ms=%d status=success",
            tenant_id, duration_ms,
        )
        return result

    except ClientError as e:
        duration_ms = int((time.time() - start) * 1000)
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", "")
        logger.error(
            "AgentCore invocation failed tenant_id=%s error=%s msg=%s duration_ms=%d",
            tenant_id, error_code, error_msg, duration_ms,
        )
        raise RuntimeError(f"AgentCore invocation failed: {error_code}: {error_msg}") from e


# ---------------------------------------------------------------------------
# Always-on Docker container routing (Shared / Team Agents)
# ---------------------------------------------------------------------------

# Cache: agent_id → endpoint URL (http://localhost:PORT)
_always_on_cache: dict = {}
_always_on_cache_ts: dict = {}
_ALWAYS_ON_TTL = 60  # seconds

def _get_always_on_endpoint(user_id: str, channel: str) -> str:
    """Return the localhost endpoint of an always-on container for this user/agent,
    or empty string if the user should use AgentCore (normal path).

    Always-on containers are registered in SSM:
      /openclaw/{stack}/always-on/{agent_id}/endpoint = "http://localhost:PORT"
    and linked to employees:
      /openclaw/{stack}/tenants/{emp_id}/always-on-agent = "agent-helpdesk"
    """
    now = time.time()
    cache_key = f"always_on__{user_id}"
    if cache_key in _always_on_cache and now - _always_on_cache_ts.get(cache_key, 0) < _ALWAYS_ON_TTL:
        return _always_on_cache[cache_key]

    try:
        ssm = boto3.client("ssm", region_name=AWS_REGION)
        # Check if this employee is assigned an always-on agent
        try:
            r = ssm.get_parameter(Name=f"/openclaw/{STACK_NAME}/tenants/{user_id}/always-on-agent")
            agent_id = r["Parameter"]["Value"]
        except Exception:
            _always_on_cache[cache_key] = ""
            _always_on_cache_ts[cache_key] = now
            return ""

        # Get the container endpoint for this always-on agent
        try:
            r2 = ssm.get_parameter(Name=f"/openclaw/{STACK_NAME}/always-on/{agent_id}/endpoint")
            endpoint = r2["Parameter"]["Value"]
            _always_on_cache[cache_key] = endpoint
            _always_on_cache_ts[cache_key] = now
            logger.info("Always-on routing: %s → %s (%s)", user_id, agent_id, endpoint)
            return endpoint
        except Exception:
            _always_on_cache[cache_key] = ""
            _always_on_cache_ts[cache_key] = now
            return ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# HTTP server — receives webhooks from OpenClaw Gateway
# ---------------------------------------------------------------------------

class TenantRouterHandler(BaseHTTPRequestHandler):
    """HTTP handler for the tenant routing proxy.

    Endpoints:
      GET  /health          → health check
      POST /route           → route message to AgentCore Runtime
      POST /route/broadcast → (future) broadcast to multiple tenants
    """

    def log_message(self, fmt, *args):
        logger.info(fmt, *args)

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {
                "status": "ok",
                "runtime_id": RUNTIME_ID or "not_configured",
                "stack": STACK_NAME,
            })
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/route":
            self._handle_route()
        else:
            self._respond(404, {"error": "not found"})

    def _handle_route(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid json"})
            return

        # Extract routing fields
        channel = payload.get("channel", "")
        user_id = payload.get("user_id", "")
        message = payload.get("message", "")

        if not channel or not user_id:
            self._respond(400, {"error": "channel and user_id required"})
            return

        if not message:
            self._respond(400, {"error": "message required"})
            return

        # Resolve IM channel user IDs (Feishu OU IDs, Discord numeric IDs, etc.) to emp_id.
        resolved_emp_id = _resolve_emp_id(user_id, channel)

        try:
            if resolved_emp_id:
                # Twin and Playground get isolated sessions so they don't pollute
                # the employee's real conversation history. workspace_assembler.py
                # and server.py detect these prefixes (twin__, pgnd__) to inject
                # mode-specific context (e.g. digital twin persona, read-only notice).
                if channel in ("twin", "playground"):
                    tenant_id = derive_tenant_id(channel, resolved_emp_id)
                else:
                    # Employee-scoped session: all IM channels + Portal share ONE
                    # AgentCore session, preserving cross-channel context.
                    tenant_id = derive_tenant_id("emp", resolved_emp_id)
            else:
                # Fallback for users not yet in DynamoDB user-mapping (e.g. new users
                # before pairing, or admin test accounts).
                tenant_id = derive_tenant_id(channel, user_id)
        except ValueError as e:
            self._respond(400, {"error": str(e)})
            return

        try:
            # Check if this routes to an always-on Docker container
            always_on_url = _get_always_on_endpoint(user_id, channel)
            if always_on_url:
                result = _invoke_local_container(always_on_url, tenant_id, message, payload.get("model"))
            else:
                result = invoke_agent_runtime(
                    tenant_id=tenant_id,
                    message=message,
                    model=payload.get("model"),
                )
            self._respond(200, {
                "tenant_id": tenant_id,
                "response": result,
            })
        except RuntimeError as e:
            self._respond(502, {"error": str(e), "tenant_id": tenant_id})

    def _respond(self, status: int, body: dict):
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _load_runtime_id_from_ssm():
    """Try to load AGENTCORE_RUNTIME_ID from SSM if not set in env."""
    global RUNTIME_ID
    if RUNTIME_ID:
        return

    ssm_path = f"/openclaw/{STACK_NAME}/runtime-id"
    try:
        ssm = boto3.client("ssm", region_name=AWS_REGION)
        resp = ssm.get_parameter(Name=ssm_path)
        RUNTIME_ID = resp["Parameter"]["Value"]
        logger.info("Loaded runtime_id from SSM: %s", RUNTIME_ID)
    except Exception as e:
        logger.warning("Could not load runtime_id from SSM path=%s: %s", ssm_path, e)


def main():
    _load_runtime_id_from_ssm()

    if not RUNTIME_ID:
        logger.warning(
            "AGENTCORE_RUNTIME_ID not set. Router will start but /route calls will fail. "
            "Set AGENTCORE_RUNTIME_ID env var or SSM parameter /openclaw/%s/runtime-id",
            STACK_NAME,
        )

    server = HTTPServer(("0.0.0.0", ROUTER_PORT), TenantRouterHandler)
    logger.info(
        "Tenant Router listening on port %d (stack=%s, runtime=%s)",
        ROUTER_PORT, STACK_NAME, RUNTIME_ID or "NOT_SET",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
