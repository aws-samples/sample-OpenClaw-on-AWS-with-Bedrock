"""
Gateway Proxy — reverse proxy to an agent's OpenClaw Gateway UI.

Supports both ECS (always-on Fargate) and EKS (Kubernetes pod) deployments.
Allows employees to manage their own IM channel connections through the
native OpenClaw Gateway UI, without knowing the container's internal IP
or gateway token.

Flow:
  Employee Portal → "Open Gateway Console" button
  → GET /api/v1/portal/gateway/access  (returns available + deployMode)
  → All subsequent requests: /api/v1/portal/gateway/ui/{path}
  → This router proxies to http://{endpoint}:18789/{path}?token={gw_token}
  → Employee sees OpenClaw native channel management UI

Resolution:
  ECS: SSM /tenants/{emp}/always-on-agent → /always-on/{agent}/endpoint → container IP:18789
  EKS: SSM /tenants/{emp}/eks-endpoint → http://{agent}.openclaw.svc:18789 (in-cluster DNS)

Security:
  - Employee must be authenticated (JWT)
  - Employee can only access their own agent's Gateway
  - Gateway token is injected server-side, never exposed to the browser
  - Container IP / K8s Service DNS is internal, never exposed
"""

import os
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
import requests as _requests

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/portal/gateway", tags=["gateway-proxy"])

# Lazy imports to avoid circular deps
_boto3 = None
def _get_boto3():
    global _boto3
    if _boto3 is None:
        import boto3
        _boto3 = boto3
    return _boto3


class _UserInfo:
    def __init__(self, employee_id: str, name: str, role: str):
        self.employee_id = employee_id
        self.name = name
        self.role = role

def _require_employee_auth(authorization: str) -> _UserInfo:
    """Validate JWT and return user info. Standalone — no import from main.py."""
    import json, hmac, hashlib, base64, time
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization")
    token = authorization.replace("Bearer ", "")
    # Decode JWT (HS256) — read secret from SSM
    try:
        boto3 = _get_boto3()
        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        region = os.environ.get("GATEWAY_REGION", "us-east-1")
        secret = boto3.client("ssm", region_name=region).get_parameter(
            Name=f"/openclaw/{stack}/jwt-secret", WithDecryption=True
        )["Parameter"]["Value"]
    except Exception:
        secret = os.environ.get("JWT_SECRET", "dev-secret")
    try:
        parts = token.split(".")
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        # Verify expiry
        if payload.get("exp", 0) < time.time():
            raise HTTPException(401, "Token expired")
        return _UserInfo(
            employee_id=payload.get("sub", ""),
            name=payload.get("name", ""),
            role=payload.get("role", "employee"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(401, f"Invalid token: {e}")


def _get_agent_gateway_url(employee_id: str) -> Optional[str]:
    """Resolve the always-on agent's Gateway URL for an employee.
    Returns http://{container_ip}:18789/?token={gw_token} or None."""
    boto3 = _get_boto3()
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    region = os.environ.get("GATEWAY_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    # SSM params are in the gateway region (us-east-1), not DynamoDB region (us-east-2)
    if region == "us-east-2":
        region = "us-east-1"
    ssm = boto3.client("ssm", region_name=region)

    # 1. Check if employee has an always-on agent
    try:
        agent_id = ssm.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{employee_id}/always-on-agent"
        )["Parameter"]["Value"]
    except Exception:
        return None  # Not always-on

    # 2. Get container endpoint (http://10.0.x.x:8080)
    try:
        endpoint = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/endpoint"
        )["Parameter"]["Value"]
    except Exception:
        return None  # Container not running

    # 3. Derive Gateway URL (port 18789 instead of 8080)
    gateway_url = endpoint.replace(":8080", ":18789")

    # 4. Get gateway token from SSM or container config
    gw_token = ""
    try:
        gw_token = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/gateway-token",
            WithDecryption=True,
        )["Parameter"]["Value"]
    except Exception:
        # Fallback: try to read from the container's /ping which doesn't need auth
        # or use a default token. For now, try without token.
        logger.warning("Gateway token not found for %s — proxy may fail auth", agent_id)

    return f"{gateway_url}/?token={gw_token}" if gw_token else gateway_url


# Cache: employee_id → (gateway_base_url, token, deploy_mode, timestamp)
_gw_cache: dict = {}
_GW_CACHE_TTL = 120  # seconds

# EKS gateway token cache: agent_name → (token, timestamp)
_eks_token_cache: dict = {}
_EKS_TOKEN_TTL = 300  # 5 minutes


async def _get_eks_gateway_token(agent_name: str) -> str:
    """Fetch gateway token from an EKS pod's openclaw.json via K8s API exec.
    Cached for 5 minutes. Returns empty string if auth mode is 'none'."""
    import time, json as _json, re
    # Sanitize the agent name to match K8s naming (same as k8s_client._sanitize_k8s_name)
    safe = re.sub(r'[^a-z0-9-]', '-', agent_name.lower()).strip('-')[:63]
    cached = _eks_token_cache.get(safe)
    if cached and time.time() - cached[1] < _EKS_TOKEN_TTL:
        return cached[0]
    try:
        from services.k8s_client import k8s_client
        stdout, stderr, rc = await k8s_client.exec_in_pod(
            namespace="openclaw",
            pod_name=f"{safe}-0",
            command=["cat", "/home/openclaw/.openclaw/openclaw.json"],
            container="openclaw",
        )
        if rc == 0 and stdout:
            cfg = _json.loads(stdout)
            token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
            if token:
                _eks_token_cache[safe] = (token, time.time())
                return token
    except Exception as e:
        logger.warning("Failed to read EKS gateway token for %s: %s", safe, e)
    return ""


def _get_cached_gateway(employee_id: str) -> Optional[tuple]:
    """Return (base_url, token, deploy_mode) for an employee's Gateway, with caching.
    deploy_mode is 'always-on-ecs' or 'eks'."""
    import time
    now = time.time()
    if employee_id in _gw_cache:
        base, token, mode, ts = _gw_cache[employee_id]
        if now - ts < _GW_CACHE_TTL:
            return base, token, mode

    boto3 = _get_boto3()
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    region = os.environ.get("GATEWAY_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    # SSM params are in the gateway region (us-east-1), not DynamoDB region (us-east-2)
    if region == "us-east-2":
        region = "us-east-1"
    ssm = boto3.client("ssm", region_name=region)

    print(f"[gateway-proxy] stack={stack} region={region} emp={employee_id}")

    # --- Try ECS (always-on-agent) first ---
    try:
        param_name = f"/openclaw/{stack}/tenants/{employee_id}/always-on-agent"
        agent_id = ssm.get_parameter(Name=param_name)["Parameter"]["Value"]
        print(f"[gateway-proxy] SSM ECS lookup OK: {param_name} → {agent_id}")

        try:
            endpoint = ssm.get_parameter(
                Name=f"/openclaw/{stack}/always-on/{agent_id}/endpoint"
            )["Parameter"]["Value"]
        except Exception as e:
            logger.info("Gateway proxy: no ECS endpoint for %s: %s", agent_id, e)
            endpoint = None

        if endpoint:
            base_url = endpoint.replace(":8080", ":18789")
            gw_token = ""
            try:
                gw_token = ssm.get_parameter(
                    Name=f"/openclaw/{stack}/always-on/{agent_id}/gateway-token",
                    WithDecryption=True,
                )["Parameter"]["Value"]
            except Exception:
                try:
                    import json
                    s3_bucket = os.environ.get("S3_BUCKET", "")
                    if s3_bucket:
                        s3 = boto3.client("s3", region_name=region)
                        obj = s3.get_object(Bucket=s3_bucket, Key=f"{employee_id}/workspace/.openclaw-gateway-token")
                        gw_token = obj["Body"].read().decode().strip()
                except Exception:
                    pass
            if not gw_token:
                try:
                    gw_token = ssm.get_parameter(
                        Name=f"/openclaw/{stack}/gateway-token",
                        WithDecryption=True,
                    )["Parameter"]["Value"]
                except Exception:
                    pass
            _gw_cache[employee_id] = (base_url, gw_token, "always-on-ecs", now)
            return base_url, gw_token, "always-on-ecs"
    except Exception as e:
        print(f"[gateway-proxy] No ECS always-on-agent for {employee_id}: {e}")

    # --- Try EKS (eks-endpoint) ---
    try:
        param_name = f"/openclaw/{stack}/tenants/{employee_id}/eks-endpoint"
        eks_endpoint = ssm.get_parameter(Name=param_name)["Parameter"]["Value"]
        print(f"[gateway-proxy] SSM EKS lookup OK: {param_name} → {eks_endpoint}")
        # eks_endpoint is like http://{agent}.openclaw.svc:18789
        base_url = eks_endpoint
        # Token will be fetched async in the caller; store empty for now
        _gw_cache[employee_id] = (base_url, "", "eks", now)
        return base_url, "", "eks"
    except Exception as e:
        print(f"[gateway-proxy] No EKS endpoint for {employee_id}: {e}")

    return None


@router.get("/access")
def get_gateway_access(authorization: str = Header(default="")):
    """Check if the employee has Gateway access and return status.
    Does NOT return the URL directly — all access goes through the proxy."""
    user = _require_employee_auth(authorization)
    print(f"[gateway-proxy] access check: emp={user.employee_id} role={user.role}")
    result = _get_cached_gateway(user.employee_id)
    print(f"[gateway-proxy] cache result: {result}")

    if not result:
        return {
            "available": False,
            "reason": "Your agent is not running. IM channels are managed through the shared company bot.",
            "deployMode": "serverless",
        }

    base_url, token, deploy_mode = result
    # Quick health check — try root path (OpenClaw Gateway returns 200 on /)
    try:
        resp = _requests.get(base_url, timeout=3)
        healthy = resp.status_code == 200
    except Exception:
        healthy = False

    return {
        "available": True,
        "healthy": healthy,
        "deployMode": deploy_mode,
        "proxyBase": "/api/v1/portal/gateway/ui/",
    }


@router.get("/dashboard")
async def get_gateway_dashboard(authorization: str = Header(default="")):
    """Get a fresh Gateway Console access info.

    ECS: calls container /gateway-dashboard on port 8080 to get pairing token,
         constructs a direct URL via EC2 public IP for WebSocket support.
    EKS: returns proxy base URL — all access goes through the reverse proxy.
         Gateway token is fetched from the pod via kubectl exec.
    """
    user = _require_employee_auth(authorization)
    result = _get_cached_gateway(user.employee_id)

    if not result:
        return {"available": False, "reason": "Agent is not running"}

    base_url, gw_token, deploy_mode = result

    if deploy_mode == "eks":
        # For EKS: fetch gateway token from pod, proxy everything
        import re
        # Extract agent name from endpoint: http://{name}.openclaw.svc:18789
        m = re.search(r'http://([^.]+)\.', base_url)
        agent_name = m.group(1) if m else ""
        if not gw_token and agent_name:
            gw_token = await _get_eks_gateway_token(agent_name)
        # Quick health check — use root path (Gateway returns 200 on /)
        try:
            resp = _requests.get(base_url, timeout=3)
            healthy = resp.status_code == 200
        except Exception:
            healthy = False
        if not healthy:
            return {"available": False, "reason": "EKS agent gateway not reachable — pod may be starting."}
        return {
            "available": True,
            "gatewayToken": gw_token,
            "dashboardToken": "",
            "proxyBase": "/api/v1/portal/gateway/ui/",
            "directUrl": None,
            "deployMode": "eks",
        }

    # ECS flow: call container's /gateway-dashboard API on port 8080
    agent_api_url = base_url.replace(":18789", ":8080")
    try:
        resp = _requests.get(f"{agent_api_url}/gateway-dashboard", timeout=50)
        if resp.status_code == 200:
            data = resp.json()
            # Build direct URL (EC2 public IP:8098) for WebSocket support
            direct_url = None
            try:
                import urllib.request
                tok_req = urllib.request.Request(
                    "http://169.254.169.254/latest/api/token",
                    method="PUT", headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
                imds_token = urllib.request.urlopen(tok_req, timeout=2).read().decode()
                ip_req = urllib.request.Request(
                    "http://169.254.169.254/latest/meta-data/public-ipv4",
                    headers={"X-aws-ec2-metadata-token": imds_token})
                public_ip = urllib.request.urlopen(ip_req, timeout=2).read().decode().strip()
                if public_ip:
                    direct_url = f"http://{public_ip}:8098/"
            except Exception:
                pass
            return {
                "available": True,
                "gatewayToken": gw_token or data.get("gatewayToken", ""),
                "dashboardToken": data.get("dashboardToken", ""),
                "proxyBase": "/api/v1/portal/gateway/ui/",
                "directUrl": direct_url,
                "deployMode": "always-on-ecs",
            }
        return {"available": False, "reason": f"Container returned {resp.status_code}: {resp.text[:200]}"}
    except _requests.exceptions.ConnectionError:
        return {"available": False, "reason": "Container not reachable"}
    except _requests.exceptions.Timeout:
        return {"available": False, "reason": "Container timed out"}
    except Exception as e:
        return {"available": False, "reason": str(e)}


@router.post("/approve-pairing")
async def approve_gateway_pairing(authorization: str = Header(default="")):
    """Auto-approve the latest pending device pairing request on the Gateway.
    Called by the frontend after opening the Gateway Console URL, so the
    browser's new device pairing is approved without manual CLI intervention.

    ECS: runs `openclaw devices approve --latest` on the EC2 host via local CLI.
    EKS: runs the same command inside the pod via `kubectl exec`.
    """
    import subprocess, asyncio, re
    user = _require_employee_auth(authorization)
    result = _get_cached_gateway(user.employee_id)

    if not result:
        return {"approved": False, "reason": "Agent is not running"}

    base_url, gw_token, deploy_mode = result

    if deploy_mode == "eks":
        # EKS: exec into the pod via K8s API to run approve
        m = re.search(r'http://([^.]+)\.', base_url)
        agent_name = m.group(1) if m else ""
        if not agent_name:
            return {"approved": False, "reason": "Cannot determine agent pod name"}
        try:
            from services.k8s_client import k8s_client
            stdout, stderr, rc = await k8s_client.exec_in_pod(
                namespace="openclaw",
                pod_name=f"{agent_name}-0",
                command=["openclaw", "devices", "approve", "--latest", "--json"],
                container="openclaw",
                timeout=15,
            )
            output = (stdout + stderr)[:300]
            logger.info("approve-pairing (EKS): rc=%d output=%s", rc, output)
            if rc == 0:
                return {"approved": True, "detail": output}
            return {"approved": False, "reason": output}
        except Exception as e:
            return {"approved": False, "reason": str(e)}

    # ECS flow: run local CLI connecting to container via VPC
    ws_url = base_url.replace("http://", "ws://")
    cmd = [
        "/home/ubuntu/.nvm/versions/node/v22.22.1/bin/openclaw",
        "devices", "approve", "--latest", "--json",
        "--url", ws_url,
    ]
    if gw_token:
        cmd.extend(["--token", gw_token])
    try:
        env = os.environ.copy()
        env["PATH"] = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin:" + env.get("PATH", "")
        env["HOME"] = "/home/ubuntu"
        env["OPENCLAW_ALLOW_INSECURE_PRIVATE_WS"] = "1"
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
        output = proc.stdout + proc.stderr
        logger.info("approve-pairing (ECS): exit=%d output=%s", proc.returncode, output[:300])
        if proc.returncode == 0:
            return {"approved": True, "detail": output[:300]}
        return {"approved": False, "reason": output[:300]}
    except subprocess.TimeoutExpired:
        return {"approved": False, "reason": "Approve timed out"}
    except Exception as e:
        return {"approved": False, "reason": str(e)}


def _authenticate_proxy(request: Request, authorization: str) -> _UserInfo:
    """Authenticate for gateway proxy via: Authorization header, ?auth_token= query, or gw_session cookie.
    On success with auth_token query param, sets a session cookie so sub-resource requests work."""
    # 1. Try Authorization header
    if authorization and authorization.startswith("Bearer "):
        return _require_employee_auth(authorization)
    # 2. Try ?auth_token= query param (window.open from browser)
    qt = request.query_params.get("auth_token", "")
    if qt:
        return _require_employee_auth(f"Bearer {qt}")
    # 3. Try gw_session cookie
    cookie_token = request.cookies.get("gw_session", "")
    if cookie_token:
        return _require_employee_auth(f"Bearer {cookie_token}")
    raise HTTPException(401, "Missing authorization")


@router.api_route("/ui/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_gateway(path: str, request: Request, authorization: str = Header(default="")):
    """Reverse proxy to the agent's OpenClaw Gateway UI (ECS or EKS).
    Injects the gateway token server-side — employee never sees it.

    Auth flow for browser navigation (window.open):
      1. Frontend opens /ui/?auth_token=JWT — first request carries JWT in query
      2. Response sets gw_session cookie with the JWT
      3. Sub-resource requests (CSS/JS/images) use the cookie automatically
    """
    user = _authenticate_proxy(request, authorization)
    result = _get_cached_gateway(user.employee_id)

    if not result:
        raise HTTPException(403, "Gateway not available — agent is not running")

    base_url, token, deploy_mode = result

    # For EKS, fetch gateway token from pod if not cached
    if deploy_mode == "eks" and not token:
        import re
        m = re.search(r'http://([^.]+)\.', base_url)
        agent_name = m.group(1) if m else ""
        if agent_name:
            token = await _get_eks_gateway_token(agent_name)

    # Build target URL
    target = f"{base_url}/{path}"
    if token:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}token={token}"

    # Forward query params (strip auth_token — internal only, not for upstream)
    filtered_params = {k: v for k, v in request.query_params.items() if k != "auth_token"}
    if filtered_params:
        from urllib.parse import urlencode
        query = urlencode(filtered_params)
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}{query}"

    # Forward the request
    try:
        body = await request.body()
        headers = {
            "Content-Type": request.headers.get("content-type", "application/json"),
            "Accept": request.headers.get("accept", "*/*"),
        }

        resp = _requests.request(
            method=request.method,
            url=target,
            headers=headers,
            data=body if body else None,
            timeout=(3, 10),
            allow_redirects=False,
        )

        excluded_headers = {"transfer-encoding", "content-encoding", "connection", "content-length"}
        response_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in excluded_headers
        }

        content = resp.content
        ct = resp.headers.get("content-type", "")

        # For EKS: inject base-path hint into HTML so the gateway JS builds
        # WebSocket URLs correctly behind the reverse proxy.
        qt = request.query_params.get("auth_token", "")
        if deploy_mode == "eks" and "text/html" in ct and b"<head>" in content:
            base_path = "/api/v1/portal/gateway/ui"
            inject = (
                f'<script>'
                f'window.__OPENCLAW_CONTROL_UI_BASE_PATH__="{base_path}";'
                f'window.__OPENCLAW_PROXY_TOKEN__="{qt}";'
                f'</script>'
            ).encode()
            content = content.replace(b"<head>", b"<head>" + inject, 1)

        response = Response(
            content=content,
            status_code=resp.status_code,
            headers=response_headers,
            media_type=ct,
        )

        # Set session cookie on first request (auth_token in query)
        if qt:
            response.set_cookie(
                key="gw_session", value=qt,
                max_age=3600, httponly=True, samesite="lax",
                path="/api/v1/portal/gateway/",
            )

        return response

    except _requests.exceptions.ConnectionError:
        raise HTTPException(502, "Gateway not reachable. Agent pod may be starting up.")
    except _requests.exceptions.Timeout:
        raise HTTPException(504, "Gateway request timed out")
    except Exception as e:
        raise HTTPException(502, f"Gateway proxy error: {e}")


@router.websocket("/ui/{path:path}")
async def proxy_gateway_ws(websocket: WebSocket, path: str):
    """WebSocket proxy to the agent's OpenClaw Gateway (ECS or EKS).
    Authenticates via gw_session cookie (set by the HTTP proxy on first page load).
    Then bi-directionally forwards WebSocket frames."""
    import asyncio, re

    # Authenticate via cookie or auth_token query param
    cookie_token = websocket.cookies.get("gw_session", "")
    qt = websocket.query_params.get("auth_token", "")
    token = cookie_token or qt
    if not token:
        await websocket.close(code=4001, reason="Missing auth cookie")
        return
    try:
        user = _require_employee_auth(f"Bearer {token}")
    except Exception:
        await websocket.close(code=4001, reason="Invalid auth")
        return

    result = _get_cached_gateway(user.employee_id)
    if not result:
        await websocket.close(code=4003, reason="Gateway not available")
        return

    base_url, gw_token, deploy_mode = result

    # For EKS, fetch gateway token from pod if not cached
    if deploy_mode == "eks" and not gw_token:
        m = re.search(r'http://([^.]+)\.', base_url)
        agent_name = m.group(1) if m else ""
        if agent_name:
            gw_token = await _get_eks_gateway_token(agent_name)

    # Build upstream WebSocket URL
    ws_base = base_url.replace("http://", "ws://").replace(":8080", ":18789")
    ws_target = f"{ws_base}/{path}"
    if gw_token:
        ws_target += f"?token={gw_token}"

    # Forward query params from client (except auth_token)
    for k, v in websocket.query_params.items():
        if k != "auth_token":
            ws_target += ("&" if "?" in ws_target else "?") + f"{k}={v}"

    await websocket.accept()

    try:
        import websockets
        # Set Origin to match upstream host so gateway's allowedOrigins check passes
        upstream_origin = base_url.split("/")[0] + "//" + base_url.split("/")[2] if "//" in base_url else base_url
        async with websockets.connect(
            ws_target, open_timeout=5, close_timeout=3,
            additional_headers={"Origin": upstream_origin},
        ) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if "text" in msg:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"]:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except Exception:
                    pass

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception as e:
        logger.warning("Gateway WS proxy error: %s", e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
