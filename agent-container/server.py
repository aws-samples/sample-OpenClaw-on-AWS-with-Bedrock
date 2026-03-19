"""
Agent Container HTTP server for Amazon Bedrock AgentCore.

Wraps `openclaw agent --session-id <tenant_id> --message <text> --json`
as a subprocess for each /invocations request.

Plan A: inject allowed tools into system prompt via SOUL.md prepend.
Plan E: audit response for blocked tool usage.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from permissions import read_permission_profile
from observability import log_agent_invocation, log_permission_denied
from safety import validate_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Path to openclaw binary (nvm install on EC2, system install in container)
_OPENCLAW_CANDIDATES = [
    "/home/ubuntu/.nvm/versions/node/v22.22.1/bin/openclaw",
    "/usr/local/bin/openclaw",
    "/usr/bin/openclaw",
]

_TOOL_PATTERN = re.compile(
    r'\b(shell|browser|file_write|code_execution|install_skill|load_extension|eval)\b',
    re.IGNORECASE,
)


def _find_openclaw() -> str:
    for p in _OPENCLAW_CANDIDATES:
        if os.path.isfile(p):
            return p
    # fallback: hope it's on PATH
    return "openclaw"


OPENCLAW_BIN = _find_openclaw()
logger.info("openclaw binary: %s", OPENCLAW_BIN)


def _build_system_prompt(tenant_id: str) -> str:
    """Plan A: build constraint text to prepend to SOUL.md."""
    try:
        profile = read_permission_profile(tenant_id)
        allowed = profile.get("tools", ["web_search"])
        blocked = [t for t in ["shell", "browser", "file", "file_write", "code_execution",
                                "install_skill", "load_extension", "eval"]
                   if t not in allowed]
    except Exception:
        allowed = ["web_search"]
        blocked = ["shell", "browser", "file", "file_write", "code_execution",
                   "install_skill", "load_extension", "eval"]

    lines = [f"Allowed tools for this session: {', '.join(allowed)}."]
    if blocked:
        lines.append(
            f"You MUST NOT use these tools: {', '.join(blocked)}. "
            "If the user requests an action requiring a blocked tool, "
            "explain that you don't have permission."
        )
    return " ".join(lines)


def _audit_response(tenant_id: str, response_text: str, allowed_tools: list) -> None:
    """Plan E: scan response for blocked tool usage."""
    matches = _TOOL_PATTERN.findall(response_text)
    if not matches:
        return
    for tool in set(t.lower() for t in matches):
        if tool not in allowed_tools:
            log_permission_denied(
                tenant_id=tenant_id,
                tool_name=tool,
                cedar_decision="RESPONSE_AUDIT",
                request_id=None,
            )
            logger.warning("AUDIT: blocked tool '%s' in response tenant_id=%s", tool, tenant_id)


def invoke_openclaw(tenant_id: str, message: str, timeout: int = 300, max_retries: int = 2) -> dict:
    """
    Run openclaw agent CLI with automatic retry on transient failures.
    Retries on: empty output, JSON parse errors, timeouts.
    Does NOT retry on successful responses (even if the content is an error message).
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return _invoke_openclaw_once(tenant_id, message, timeout)
        except RuntimeError as e:
            last_error = e
            if attempt < max_retries:
                wait = (attempt + 1) * 2  # 2s, 4s linear backoff
                logger.warning(
                    "openclaw retry %d/%d after %ds: %s",
                    attempt + 1, max_retries, wait, e,
                )
                time.sleep(wait)
    raise last_error


def _invoke_openclaw_once(tenant_id: str, message: str, timeout: int = 300) -> dict:
    """
    Run: openclaw agent --session-id <tenant_id> --message <message> --json
    Returns parsed JSON result dict.
    Runs as 'ubuntu' user if we're root (EC2 host) so openclaw config is accessible.
    """
    env = os.environ.copy()
    # Ensure node is on PATH for nvm installs
    nvm_bin = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin"
    if os.path.isdir(nvm_bin):
        env["PATH"] = nvm_bin + ":" + env.get("PATH", "")
        env["HOME"] = "/home/ubuntu"

    openclaw_cmd = [
        OPENCLAW_BIN,
        "agent",
        "--session-id", tenant_id,
        "--message", message,
        "--json",
        "--timeout", str(timeout),
    ]

    # If running as root (EC2 host), sudo to ubuntu so openclaw config is accessible
    # Use 'sudo -u ubuntu env KEY=VAL ...' and do NOT pass env= to subprocess
    # (subprocess env= would override the sudo env vars)
    run_env = None  # None = inherit current process env (used in container as ubuntu)
    if os.geteuid() == 0 and os.path.isdir("/home/ubuntu"):
        path_val = env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        aws_region = env.get("AWS_REGION", "us-east-1")
        cmd = [
            "sudo", "-u", "ubuntu",
            "env",
            f"PATH={path_val}",
            "HOME=/home/ubuntu",
            f"AWS_REGION={aws_region}",
            f"AWS_DEFAULT_REGION={aws_region}",
        ] + openclaw_cmd
        run_env = None  # let sudo handle the environment
    else:
        cmd = openclaw_cmd
        run_env = env  # pass env in container (running as ubuntu already)

    logger.info("Invoking openclaw tenant_id=%s cmd=%s", tenant_id, " ".join(cmd[:5]))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"openclaw timed out after {timeout}s")

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if stderr:
        # openclaw logs info/warnings to stderr — log at WARNING for visibility
        for line in stderr.splitlines():
            logger.warning("[openclaw stderr] %s", line)

    if not stdout:
        raise RuntimeError(f"openclaw returned empty output (exit={result.returncode})")

    # Find the first JSON object in stdout (may have log lines before it)
    json_start = stdout.find('{')
    if json_start == -1:
        raise RuntimeError(f"No JSON in openclaw output: {stdout[:200]}")

    # Use JSONDecoder to parse only the first complete JSON object
    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(stdout, json_start)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse openclaw JSON: {e} — output: {stdout[:200]}")

    return data


class AgentCoreHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # noqa: A002
        logger.info(format, *args)

    def do_GET(self):
        if self.path == "/ping":
            self._respond(200, {"status": "Healthy", "time_of_last_update": int(time.time())})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invocations":
            self._respond(404, {"error": "not found"})
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid json"})
            return

        # Extract tenant_id from headers or payload
        _file_tenant = ""
        try:
            with open("/tmp/tenant_id") as f:
                _file_tenant = f.read().strip()
        except Exception:
            pass

        tenant_id = (
            self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")
            or self.headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
            or payload.get("runtimeSessionId")
            or payload.get("sessionId")
            or payload.get("tenant_id")
            or _file_tenant
            or "unknown"
        )

        message = validate_message(
            payload.get("prompt") or payload.get("message") or str(payload)
        )

        logger.info("Invocation tenant_id=%s message_len=%d", tenant_id, len(message))
        self._handle_invocation(tenant_id, message, payload)

    def _handle_invocation(self, tenant_id: str, message: str, payload: dict):
        start_ms = int(time.time() * 1000)
        try:
            timeout = int(payload.get("timeout", 300))
            data = invoke_openclaw(tenant_id, message, timeout=timeout)
            duration_ms = int(time.time() * 1000) - start_ms

            # Extract text from openclaw JSON response
            # Format: {"payloads": [{"text": "..."}], "meta": {...}}
            payloads = data.get("payloads", [])
            response_text = " ".join(
                p.get("text", "") for p in payloads if p.get("text")
            ).strip()

            if not response_text:
                # Fallback: try top-level text field
                response_text = data.get("text", str(data))

            # Plan E audit
            try:
                profile = read_permission_profile(tenant_id)
                allowed = profile.get("tools", ["web_search"])
            except Exception:
                allowed = ["web_search"]
            _audit_response(tenant_id, response_text, allowed)

            # Extract model usage for observability
            meta = data.get("meta", {})
            agent_meta = meta.get("agentMeta", {})
            model = agent_meta.get("model", "unknown")
            usage = agent_meta.get("usage", {})

            log_agent_invocation(
                tenant_id=tenant_id,
                tools_used=[],
                duration_ms=duration_ms,
                status="success",
            )
            logger.info(
                "Response tenant_id=%s duration_ms=%d model=%s tokens=%s text_len=%d",
                tenant_id, duration_ms, model, usage.get("total", "?"), len(response_text),
            )

            self._respond(200, {
                "response": response_text,
                "status": "success",
                "model": model,
                "usage": usage,
            })

        except Exception as e:
            duration_ms = int(time.time() * 1000) - start_ms
            log_agent_invocation(tenant_id=tenant_id, tools_used=[], duration_ms=duration_ms, status="error")
            logger.error("Invocation failed tenant_id=%s error=%s", tenant_id, e)
            self._respond(500, {"error": str(e)})

    def _respond(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), AgentCoreHandler)
    logger.info("HTTP server listening on port %d", port)
    logger.info("openclaw binary: %s", OPENCLAW_BIN)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
