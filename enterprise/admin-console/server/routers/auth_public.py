"""
公开 (无鉴权) 认证相关端点。

前端在用户尚未登录时需要拉取 SSO 配置以决定是否显示 "Sign in with SSO" 按钮
或触发自动跳转,因此此端点不能放在需要 Bearer token 的中间件白名单之外。

路径前缀 `/api/v1/public/` 在 main.py 的 _AUTH_PUBLIC_PREFIXES 中已豁免。
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

import db

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.get("/sso/config")
def get_sso_public_config():
    """返回前端所需的 SSO 公开配置 (不含任何机密字段)。"""
    cfg = db.get_config("sso") or {}
    payload = {
        "enabled": bool(cfg.get("enabled")),
        "issuer": cfg.get("issuer", ""),
        "clientId": cfg.get("clientId", ""),
        "scopes": cfg.get("scopes", "openid profile email"),
        "autoRedirect": bool(cfg.get("autoRedirect")),
    }
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=60"},
    )
