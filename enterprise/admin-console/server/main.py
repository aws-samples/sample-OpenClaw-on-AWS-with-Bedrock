"""
OpenClaw Enterprise Admin Console — Backend API v0.5

FastAPI server backed by DynamoDB + S3.
Serves both API and frontend static files from a single port.

Usage:
  cd admin-console/server && python main.py

Env vars:
  DYNAMODB_TABLE (default: STACK_NAME or openclaw)
  AWS_REGION     (default: AWS_REGION)
  CONSOLE_PORT   (default: 8099)
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from starlette.requests import Request
from starlette.responses import JSONResponse as StarletteJSONResponse

import db
import s3ops
import auth as authmod

# =========================================================================
# App init
# =========================================================================

app = FastAPI(title="OpenClaw Admin API", version="0.5.0")
_ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://openclaw.awspsa.com,http://localhost:5173,http://localhost:8099").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS, allow_methods=["GET","POST","PUT","DELETE","OPTIONS"], allow_headers=["Content-Type","Authorization"])


# =========================================================================
# Auth Middleware — unified authentication for all API endpoints
# =========================================================================
# Endpoints NOT in this whitelist require a valid JWT in the Authorization
# header. This is the single enforcement point — individual routers no
# longer need to call require_auth() for basic authentication (though they
# may still call require_role() for role-based access control).

_AUTH_PUBLIC_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/bindings/pair-pending",
    "/api/v1/bindings/pair-complete",
}

_AUTH_PUBLIC_PREFIXES = (
    "/api/v1/internal/",
    "/api/v1/public/",
)

# Non-API paths (static files, gateway proxy HTML, /docs, /openapi.json)
# are not subject to auth middleware.


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Skip non-API paths (static files, frontend, gateway proxy HTML pages)
    if not path.startswith("/api/"):
        return await call_next(request)

    # Skip OPTIONS (CORS preflight)
    if request.method == "OPTIONS":
        return await call_next(request)

    # Skip whitelisted public endpoints
    if path in _AUTH_PUBLIC_PATHS:
        return await call_next(request)
    for prefix in _AUTH_PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return await call_next(request)

    # Require valid JWT
    auth_header = request.headers.get("authorization", "")
    user = authmod.get_user_from_request(auth_header)
    if not user:
        return StarletteJSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )

    # Attach user to request state for downstream use
    request.state.user = user
    return await call_next(request)

# =========================================================================
# Modular routers — all endpoint logic lives in routers/
# =========================================================================

from routers.gateway_proxy import router as _gateway_proxy_router
from routers.org import router as _org_router
from routers.agents import router as _agents_router
from routers.bindings import router as _bindings_router
from routers.knowledge import router as _knowledge_router
from routers.playground import router as _playground_router
from routers.portal import router as _portal_router
from routers.monitor import router as _monitor_router
from routers.audit import router as _audit_router
from routers.usage import router as _usage_router
from routers.settings import router as _settings_router
from routers.security import router as _security_router
from routers.admin_im import router as _admin_im_router
from routers.admin_ai import router as _admin_ai_router
from routers.admin_always_on import router as _admin_always_on_router
from routers.twin import router as _twin_router

app.include_router(_gateway_proxy_router)
app.include_router(_org_router)
app.include_router(_agents_router)
app.include_router(_bindings_router)
app.include_router(_knowledge_router)
app.include_router(_playground_router)
app.include_router(_portal_router)
app.include_router(_monitor_router)
app.include_router(_audit_router)
app.include_router(_usage_router)
app.include_router(_settings_router)
app.include_router(_security_router)
app.include_router(_admin_im_router)
app.include_router(_admin_ai_router)
app.include_router(_admin_always_on_router)
app.include_router(_twin_router)

# =========================================================================
# Auth — Login + current user (stays in main.py — needed by app startup)
# =========================================================================

from fastapi import HTTPException, Header
from pydantic import BaseModel


class LoginRequest(BaseModel):
    employeeId: str
    password: str = ""


@app.post("/api/v1/auth/login")
def login(body: LoginRequest):
    """Authenticate employee and return JWT token."""
    employees = db.get_employees()
    emp = next((e for e in employees if e["id"] == body.employeeId or e.get("employeeNo") == body.employeeId), None)
    if not emp:
        raise HTTPException(401, "Employee not found")

    expected_password = os.environ.get("ADMIN_PASSWORD", "")
    if not expected_password:
        raise HTTPException(500, "ADMIN_PASSWORD environment variable not set")
    if body.password != expected_password:
        raise HTTPException(401, "Invalid password")

    token = authmod.create_token(emp)
    return {
        "token": token,
        "employee": {
            "id": emp["id"],
            "name": emp["name"],
            "role": emp.get("role", "employee"),
            "departmentId": emp.get("departmentId", ""),
            "departmentName": emp.get("departmentName", ""),
            "positionId": emp.get("positionId", ""),
            "positionName": emp.get("positionName", ""),
        },
    }


@app.get("/api/v1/auth/me")
def get_me(authorization: str = Header(default="")):
    """Get current authenticated user info."""
    from shared import require_auth
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    if not emp:
        raise HTTPException(404, "Employee not found")
    return {
        "id": emp["id"],
        "name": emp["name"],
        "role": emp.get("role", "employee"),
        "departmentId": emp.get("departmentId", ""),
        "departmentName": emp.get("departmentName", ""),
        "positionId": emp.get("positionId", ""),
        "positionName": emp.get("positionName", ""),
        "agentId": emp.get("agentId"),
        "channels": emp.get("channels", []),
    }


# =========================================================================
# Serve frontend (production mode)
# =========================================================================

DIST_DIR = Path(__file__).parent.parent / "dist"

if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request, exc):
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            return FileResponse(str(DIST_DIR / "index.html"))
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# =========================================================================
# Startup
# =========================================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("CONSOLE_PORT", "8099"))
    print(f"\n  🦞 OpenClaw Admin Console API v0.5")
    print(f"  DynamoDB: {db.TABLE_NAME} ({db.AWS_REGION})")
    print(f"  S3: {s3ops.bucket()}")
    print(f"  http://localhost:{port}/docs")
    print(f"  http://localhost:{port}/api/v1/dashboard\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
