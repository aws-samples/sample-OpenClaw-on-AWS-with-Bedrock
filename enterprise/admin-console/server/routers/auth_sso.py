"""
BFF OIDC SSO 登录 — 后端处理 OAuth Authorization Code Flow 全流程。

设计要点:
  - 后端作为 Confidential Client,携带 client_secret 向 IdP 换 token
  - state + PKCE code_verifier 存 HttpOnly Cookie (10 分钟 TTL)
  - 验签 id_token 后按 email 查员工 (支持 Auto-Provisioning)
  - 登录成功签发本地 HS256 JWT,通过 URL hash 传给前端

端点:
  GET /api/v1/auth/sso/login       发起 OAuth,302 到 IdP authorize
  GET /api/v1/auth/sso/callback    IdP 回调,code → token → JWT → 302 回前端
"""
import base64
import hashlib
import json
import logging
import secrets
import urllib.parse
from typing import Optional

import httpx
import jwt
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse
from jwt import PyJWKClient

import db
import auth as authmod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth/sso", tags=["auth-sso"])

# Cookie 名称 (HttpOnly,Secure,SameSite=Lax — 允许跨域回调带回)
_COOKIE_STATE = "openclaw_sso_state"
_COOKIE_VERIFIER = "openclaw_sso_verifier"
_COOKIE_ORIGIN = "openclaw_sso_origin"
_COOKIE_TTL = 600  # 10 分钟

# OIDC discovery 元数据缓存 (1 小时)
_disco_cache: dict = {}
_DISCO_TTL = 3600


def _get_discovery(issuer: str) -> Optional[dict]:
    """拉并缓存 IdP 的 .well-known/openid-configuration。"""
    import time as _t
    cached = _disco_cache.get(issuer)
    if cached and _t.time() - cached["ts"] < _DISCO_TTL:
        return cached["doc"]
    try:
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        resp = httpx.get(url, timeout=10.0)
        resp.raise_for_status()
        doc = resp.json()
        _disco_cache[issuer] = {"ts": _t.time(), "doc": doc}
        return doc
    except Exception as e:
        logger.warning("Fetch discovery failed issuer=%s: %s", issuer, e)
        return None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_challenge(verifier: str) -> str:
    """S256: BASE64URL(SHA256(verifier))"""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def _origin(request: Request) -> str:
    """返回前端 Portal 的 public origin,用于拼 redirect_uri / 最终跳转。

    优先级:
      1. Query param ?origin=  (浏览器显式传入,最可靠)    — 推荐
      2. CONFIG#sso.portalUrl (管理员显式配置)           — 备选
      3. X-Forwarded-Host + X-Forwarded-Proto           — ALB/CloudFront 标准转发
      4. Host header + request scheme                    — 直连场景

    为什么需要多层:当 EC2 在 CloudFront/ALB 后面时,Host header 可能是内部域名,
    而 IdP 要求 redirect_uri 和注册的完全一致(通常是客户面向公网的域名)。
    最可靠的做法是浏览器 (window.location.origin) 告诉后端自己在哪。
    """
    # 1. 浏览器显式传入的 origin (查询参数)
    origin_param = request.query_params.get("origin", "").strip().rstrip("/")
    if origin_param and (origin_param.startswith("https://") or origin_param.startswith("http://localhost")):
        return origin_param

    # 2. 显式配置
    cfg = db.get_config("sso") or {}
    portal_url = (cfg.get("portalUrl") or "").strip().rstrip("/")
    if portal_url:
        return portal_url

    # 3. X-Forwarded-* headers (CloudFront 和 ALB 都会设置)
    xf_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    xf_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if xf_host:
        scheme = xf_proto or "https"
        return f"{scheme}://{xf_host}"

    # 4. 直连场景兜底
    host = request.headers.get("host", "")
    scheme = request.url.scheme or "https"
    if host:
        return f"{scheme}://{host}"
    return str(request.base_url).rstrip("/")


@router.get("/login")
def sso_login(request: Request):
    """发起 OAuth Authorization Code Flow。

    1. 读 CONFIG#sso 拿 issuer/clientId
    2. 生成 state + code_verifier, 写 HttpOnly Cookie
    3. 302 到 IdP authorize_endpoint
    """
    cfg = db.get_config("sso") or {}
    if not cfg.get("enabled"):
        return _redirect_with_error(request, "sso_disabled")

    issuer = (cfg.get("issuer") or "").strip()
    client_id = (cfg.get("clientId") or "").strip()
    scopes = cfg.get("scopes") or "openid profile email"
    if not issuer or not client_id:
        return _redirect_with_error(request, "sso_misconfigured")

    disco = _get_discovery(issuer)
    if not disco or not disco.get("authorization_endpoint"):
        return _redirect_with_error(request, "idp_unreachable")

    # 生成 state + PKCE verifier
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(verifier)

    origin = _origin(request)
    redirect_uri = f"{origin}/api/v1/auth/sso/callback"

    # 构建 authorize URL
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = disco["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)

    resp = RedirectResponse(url=auth_url, status_code=302)
    # SameSite=Lax 允许 IdP 跨域回调时把 Cookie 带回来
    resp.set_cookie(_COOKIE_STATE, state,
                    httponly=True, secure=True, samesite="lax",
                    max_age=_COOKIE_TTL, path="/")
    resp.set_cookie(_COOKIE_VERIFIER, verifier,
                    httponly=True, secure=True, samesite="lax",
                    max_age=_COOKIE_TTL, path="/")
    # 存 origin 供 callback 时复用 (保证 redirect_uri 在两次请求里完全一致)
    resp.set_cookie(_COOKIE_ORIGIN, origin,
                    httponly=True, secure=True, samesite="lax",
                    max_age=_COOKIE_TTL, path="/")
    logger.info(
        "SSO login: redirect_uri=%s state=%s headers(host=%s, x-forwarded-host=%s, x-forwarded-proto=%s)",
        redirect_uri, state[:8],
        request.headers.get("host", ""),
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("x-forwarded-proto", ""),
    )
    return resp


@router.get("/callback")
def sso_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """IdP 回调处理。

    1. IdP 错误 → 跳回 login 显示错误
    2. 校验 state (CSRF 防护)
    3. 用 code + client_secret + code_verifier 向 IdP 换 id_token
    4. 验签 id_token (JWKS + aud + iss + exp)
    5. 按 email 查员工 (或 Auto-Provisioning)
    6. 签本地 JWT 302 到前端 /login/sso-success#token=xxx
    """
    if error:
        logger.warning("IdP returned error: %s", error)
        return _redirect_with_error(request, "idp_error", extra={"idp_error": error})

    if not code or not state:
        return _redirect_with_error(request, "missing_params")

    # 从 Cookie 取回 state + verifier
    cookie_state = request.cookies.get(_COOKIE_STATE, "")
    cookie_verifier = request.cookies.get(_COOKIE_VERIFIER, "")

    if not cookie_state or not cookie_verifier:
        logger.warning("SSO callback: missing state/verifier cookie")
        return _redirect_with_error(request, "session_expired")

    if not secrets.compare_digest(state, cookie_state):
        logger.warning("SSO callback: state mismatch")
        return _redirect_with_error(request, "state_mismatch")

    # 读配置
    cfg = db.get_config("sso") or {}
    issuer = (cfg.get("issuer") or "").strip()
    client_id = (cfg.get("clientId") or "").strip()
    client_secret = (cfg.get("clientSecret") or "").strip()

    if not issuer or not client_id or not client_secret:
        logger.warning("SSO callback: CONFIG#sso missing issuer/clientId/clientSecret")
        return _redirect_with_error(request, "sso_misconfigured")

    disco = _get_discovery(issuer)
    if not disco or not disco.get("token_endpoint"):
        return _redirect_with_error(request, "idp_unreachable")

    # 从 cookie 读 origin (必须和 /login 时用的一致,不然换 token 会失败)
    origin = request.cookies.get(_COOKIE_ORIGIN, "") or _origin(request)
    redirect_uri = f"{origin}/api/v1/auth/sso/callback"
    token_resp = _exchange_code_for_token(
        disco["token_endpoint"], client_id, client_secret,
        code, redirect_uri, cookie_verifier,
    )
    if not token_resp:
        return _redirect_with_error(request, "token_exchange_failed")

    id_token = token_resp.get("id_token")
    if not id_token:
        logger.warning("SSO callback: no id_token in token response")
        return _redirect_with_error(request, "no_id_token")

    # Step: 验签 id_token
    claims = _verify_id_token(id_token, issuer, client_id, disco.get("jwks_uri"))
    if not claims:
        return _redirect_with_error(request, "id_token_invalid")

    # Step: 按 email 查员工 (复用 auth.py 里已有逻辑,含 Auto-Provisioning)
    user_ctx = authmod._user_from_oidc_claims(claims)
    if not user_ctx:
        email = claims.get("email") or claims.get("preferred_username") or ""
        return _redirect_with_error(request, "email_not_found", extra={"email": email})

    # Step: 签本地 JWT
    # SSO 用户的凭证由 IdP 管理,本地 mustChangePassword 标记对其无意义,强制设为 False
    emp = db.get_employee(user_ctx.employee_id) or {}
    local_jwt = authmod.create_token(emp, must_change_password=False)

    # Step: 302 到前端,用 URL hash 传 token (不进 access log)
    success_url = f"{origin}/login/sso-success#token={local_jwt}"
    resp = RedirectResponse(url=success_url, status_code=302)
    # 清理 state/verifier/origin cookie
    resp.delete_cookie(_COOKIE_STATE, path="/")
    resp.delete_cookie(_COOKIE_VERIFIER, path="/")
    resp.delete_cookie(_COOKIE_ORIGIN, path="/")
    logger.info("SSO login success, emp_id=%s", user_ctx.employee_id)
    return resp


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _exchange_code_for_token(
    token_endpoint: str, client_id: str, client_secret: str,
    code: str, redirect_uri: str, code_verifier: str,
) -> Optional[dict]:
    """POST 到 IdP 的 token_endpoint,用 client_secret_basic 鉴权 + PKCE code_verifier。"""
    try:
        basic_auth = base64.b64encode(
            f"{client_id}:{client_secret}".encode()
        ).decode()
        resp = httpx.post(
            token_endpoint,
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.warning(
                "Token exchange failed status=%d body=%s",
                resp.status_code, resp.text[:500],
            )
            return None
        return resp.json()
    except Exception as e:
        logger.warning("Token exchange error: %s", e)
        return None


def _verify_id_token(
    id_token: str, issuer: str, client_id: str, jwks_uri: Optional[str],
) -> Optional[dict]:
    """用 JWKS 验 RS256 签名 + audience + issuer + exp。"""
    if not jwks_uri:
        logger.warning("No jwks_uri for issuer=%s", issuer)
        return None
    try:
        jwks_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        return claims
    except jwt.ExpiredSignatureError:
        logger.warning("id_token expired")
    except jwt.InvalidTokenError as e:
        logger.warning("id_token invalid: %s", e)
    except Exception as e:
        logger.warning("id_token verification error: %s", e)
    return None


def _redirect_with_error(request: Request, code: str, extra: Optional[dict] = None) -> RedirectResponse:
    """统一错误跳转: /login?error=xxx[&email=xxx]

    优先使用 cookie 里存的 origin (login 阶段写入),保证错误跳转也能回到
    正确的公网 URL 而不是 ALB 内部地址。
    """
    origin = request.cookies.get(_COOKIE_ORIGIN, "") or _origin(request)
    params = {"error": code}
    if extra:
        for k, v in extra.items():
            if v:
                params[k] = v
    url = f"{origin}/login?" + urllib.parse.urlencode(params)
    resp = RedirectResponse(url=url, status_code=302)
    resp.delete_cookie(_COOKIE_STATE, path="/")
    resp.delete_cookie(_COOKIE_VERIFIER, path="/")
    resp.delete_cookie(_COOKIE_ORIGIN, path="/")
    return resp


# ── 清除 discovery 缓存(Settings 保存后调用) ─────────────────────────────────

def clear_discovery_cache():
    _disco_cache.clear()
