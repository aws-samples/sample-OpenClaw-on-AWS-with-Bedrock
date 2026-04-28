"""
统一认证模块。

支持两类 token:
  1. 通用 OIDC id_token (RS256) —— 任意符合 OIDC 标准的 IdP（阿里云 IDaaS /
     Azure AD / Okta / Keycloak 等），配置存 DynamoDB `CONFIG#sso`。
  2. 本地 HS256 JWT —— employeeId + password 登录后自签，存 localStorage。

中间件通过 JWT header 的 `alg` 字段自动分流。
"""
import os
import time
import hashlib
import hmac
import json
import base64
import logging
import threading
from typing import Optional, Dict, Tuple
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

# ── 本地 JWT 配置 ────────────────────────────────────────────────────────────
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    JWT_SECRET = "dev-only-" + hashlib.sha256(os.urandom(16)).hexdigest()[:32]
JWT_EXPIRY_HOURS = 24

# ── OIDC 配置缓存 (60 秒内存缓存,Settings 保存后主动清除) ──────────────────────
_SSO_CONFIG_TTL = 60
_sso_config_cache: Dict[str, object] = {"ts": 0.0, "value": None}
_sso_cache_lock = threading.Lock()

# ── JWKS 客户端缓存 (按 issuer 隔离,1 小时由 PyJWKClient 内置管理) ──────────────
_jwks_clients: Dict[str, PyJWKClient] = {}
_jwks_lock = threading.Lock()


@dataclass
class UserContext:
    employee_id: str
    name: str
    role: str  # admin | manager | employee
    department_id: str
    position_id: str
    email: str = ""
    must_change_password: bool = False


# ── 通用工具 ─────────────────────────────────────────────────────────────────

def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _peek_alg(token: str) -> str:
    """不验签读取 JWT header 里的 alg 字段,用于分流。"""
    try:
        header_b64 = token.split(".")[0]
        header = json.loads(_b64decode(header_b64))
        return header.get("alg", "")
    except Exception:
        return ""


# ── OIDC 配置读取 ────────────────────────────────────────────────────────────

def _get_sso_config() -> Optional[dict]:
    """从 DynamoDB 读 CONFIG#sso,60 秒内存缓存。"""
    now = time.time()
    with _sso_cache_lock:
        ts = _sso_config_cache["ts"]
        if isinstance(ts, (int, float)) and now - ts < _SSO_CONFIG_TTL:
            return _sso_config_cache["value"]  # type: ignore[return-value]

    try:
        import db
        cfg = db.get_config("sso")
    except Exception as e:
        logger.warning("Read CONFIG#sso failed: %s", e)
        cfg = None

    with _sso_cache_lock:
        _sso_config_cache["ts"] = now
        _sso_config_cache["value"] = cfg
    return cfg


def clear_sso_config_cache() -> None:
    """Settings PUT 成功后调用,强制下次请求重新读取。"""
    with _sso_cache_lock:
        _sso_config_cache["ts"] = 0.0
        _sso_config_cache["value"] = None
    with _jwks_lock:
        _jwks_clients.clear()
    logger.info("SSO config and JWKS caches cleared")


def _get_oidc_jwks_client(issuer: str) -> Optional[PyJWKClient]:
    """按 issuer 缓存 PyJWKClient。首次调用会拉 .well-known 发现 jwks_uri。"""
    with _jwks_lock:
        cached = _jwks_clients.get(issuer)
        if cached is not None:
            return cached

    try:
        disco_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        resp = httpx.get(disco_url, timeout=5.0)
        resp.raise_for_status()
        jwks_uri = resp.json().get("jwks_uri")
        if not jwks_uri:
            logger.warning("OIDC discovery has no jwks_uri: %s", disco_url)
            return None
        client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)
    except Exception as e:
        logger.warning("Fetch OIDC discovery failed issuer=%s: %s", issuer, e)
        return None

    with _jwks_lock:
        _jwks_clients[issuer] = client
    return client


# ── OIDC token 验证 ──────────────────────────────────────────────────────────

def _verify_oidc_token(token: str) -> Optional[dict]:
    """用 DynamoDB 里配置的 issuer/clientId 验 RS256 id_token。"""
    cfg = _get_sso_config()
    if not cfg or not cfg.get("enabled"):
        return None

    issuer = cfg.get("issuer", "")
    client_id = cfg.get("clientId", "")
    if not issuer or not client_id:
        logger.warning("CONFIG#sso missing issuer or clientId")
        return None

    jwks = _get_oidc_jwks_client(issuer)
    if not jwks:
        return None

    try:
        signing_key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        return claims
    except jwt.ExpiredSignatureError:
        logger.info("OIDC token expired")
    except jwt.InvalidTokenError as e:
        logger.warning("OIDC token invalid: %s", e)
    except Exception as e:
        logger.warning("OIDC token verification failed: %s", e)
    return None


def _user_from_oidc_claims(claims: dict) -> Optional[UserContext]:
    """按 email claim 在 DynamoDB 查找员工。"""
    email = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
        or ""
    ).strip()
    if not email:
        logger.warning("OIDC token has no email/preferred_username/upn claim")
        return None

    import db
    emp = db.get_employee_by_email(email)
    if not emp:
        logger.warning("No employee matches OIDC email: %s", email)
        return None

    return UserContext(
        employee_id=emp["id"],
        name=emp.get("name", claims.get("name", "")),
        role=emp.get("role", "employee"),
        department_id=emp.get("departmentId", ""),
        position_id=emp.get("positionId", ""),
        email=email,
    )


# ── 本地 JWT 签发与验证 ──────────────────────────────────────────────────────

def create_token(employee: dict, must_change_password: bool = False) -> str:
    """employeeId + password 登录成功后签发本地 HS256 JWT。"""
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_data = {
        "sub": employee.get("id", ""),
        "name": employee.get("name", ""),
        "role": employee.get("role", "employee"),
        "departmentId": employee.get("departmentId", ""),
        "positionId": employee.get("positionId", ""),
        "mustChangePassword": must_change_password,
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }
    payload = _b64encode(json.dumps(payload_data).encode())
    signature = hmac.new(
        JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).digest()
    sig = _b64encode(signature)
    return f"{header}.{payload}.{sig}"


def _verify_local_token(token: str) -> Optional[UserContext]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, sig = parts

        expected = hmac.new(
            JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
        ).digest()
        actual = _b64decode(sig)
        if not hmac.compare_digest(expected, actual):
            return None

        data = json.loads(_b64decode(payload))
        if data.get("exp", 0) < time.time():
            return None

        return UserContext(
            employee_id=data.get("sub", ""),
            name=data.get("name", ""),
            role=data.get("role", "employee"),
            department_id=data.get("departmentId", ""),
            position_id=data.get("positionId", ""),
            must_change_password=data.get("mustChangePassword", False),
        )
    except Exception:
        return None


# ── 对外入口 ─────────────────────────────────────────────────────────────────

def get_user_from_request(authorization: str = "") -> Optional[UserContext]:
    """从 Authorization header 提取用户上下文。按 alg 自动分流: RS256 → OIDC,HS256 → 本地。"""
    if not authorization:
        return None
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        return None

    alg = _peek_alg(token)

    if alg == "RS256":
        claims = _verify_oidc_token(token)
        if not claims:
            return None
        return _user_from_oidc_claims(claims)

    # 其余情况(HS256 或未知)尝试作为本地 token
    return _verify_local_token(token)
