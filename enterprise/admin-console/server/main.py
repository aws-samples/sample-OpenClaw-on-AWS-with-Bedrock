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
_ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://openclaw.awspsa.com,https://dev-openclaw.awspsa.com,http://localhost:5173,http://localhost:8099").split(",")
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

    # Block users who must change password (local JWT only) from all endpoints
    # except change-password and auth/me
    if user.must_change_password and path not in (
        "/api/v1/auth/change-password",
        "/api/v1/auth/me",
    ):
        return StarletteJSONResponse(
            status_code=403,
            content={"detail": "Password change required"},
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
from routers.auth_public import router as _auth_public_router

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
app.include_router(_auth_public_router)

# =========================================================================
# Auth — Login + change password + current user
# Supports both Azure AD (RS256) and local password (HS256) auth.
# =========================================================================

from fastapi import HTTPException, Header
from pydantic import BaseModel


class LoginRequest(BaseModel):
    employeeId: str
    password: str = ""


@app.post("/api/v1/auth/login")
def login(body: LoginRequest):
    """Authenticate employee with ID + password and return a local HS256 JWT."""
    employees_public = db.get_employees()
    emp_public = next((e for e in employees_public if e["id"] == body.employeeId or e.get("employeeNo") == body.employeeId), None)
    if not emp_public:
        raise HTTPException(401, "Employee not found")

    emp_full = db.get_employee_with_password(emp_public["id"])
    if not emp_full:
        raise HTTPException(401, "Employee not found")

    import hmac as _hmac
    from password import verify_password
    password_hash = emp_full.get("passwordHash", "")
    if password_hash:
        if not verify_password(body.password, password_hash):
            raise HTTPException(401, "Invalid password")
    else:
        expected_password = os.environ.get("ADMIN_PASSWORD", "")
        if not expected_password:
            raise HTTPException(500, "ADMIN_PASSWORD environment variable not set")
        if not _hmac.compare_digest(body.password, expected_password):
            raise HTTPException(401, "Invalid password")

    must_change = emp_full.get("mustChangePassword", True)
    token = authmod.create_token(emp_full, must_change_password=must_change)
    return {
        "token": token,
        "mustChangePassword": must_change,
        "employee": {
            "id": emp_full["id"],
            "name": emp_full["name"],
            "role": emp_full.get("role", "employee"),
            "departmentId": emp_full.get("departmentId", ""),
            "departmentName": emp_full.get("departmentName", ""),
            "positionId": emp_full.get("positionId", ""),
            "positionName": emp_full.get("positionName", ""),
        },
    }


class ChangePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str


@app.post("/api/v1/auth/change-password")
def change_password(body: ChangePasswordRequest, authorization: str = Header(default="")):
    """Change the authenticated employee's password (local auth only)."""
    import hmac as _hmac
    from shared import require_auth
    from password import hash_password, verify_password, validate_complexity

    user = require_auth(authorization)
    emp_full = db.get_employee_with_password(user.employee_id)
    if not emp_full:
        raise HTTPException(404, "Employee not found")

    password_hash = emp_full.get("passwordHash", "")
    if password_hash:
        if not verify_password(body.currentPassword, password_hash):
            raise HTTPException(401, "Current password is incorrect")
    else:
        expected_password = os.environ.get("ADMIN_PASSWORD", "")
        if not _hmac.compare_digest(body.currentPassword, expected_password):
            raise HTTPException(401, "Current password is incorrect")

    if body.newPassword == body.currentPassword:
        raise HTTPException(400, "New password must be different from current password")

    error = validate_complexity(body.newPassword)
    if error:
        raise HTTPException(400, error)

    hashed = hash_password(body.newPassword)
    db.update_employee(user.employee_id, {
        "passwordHash": hashed,
        "mustChangePassword": False,
    })

    emp_updated = db.get_employee(user.employee_id)
    new_token = authmod.create_token(emp_updated, must_change_password=False)
    return {"token": new_token, "changed": True}


@app.get("/api/v1/auth/me")
def get_me(authorization: str = Header(default="")):
    """Get current authenticated user info (works with both Azure AD and local JWT)."""
    from shared import require_auth
    user = require_auth(authorization)
    emp = db.get_employee(user.employee_id)
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
        "email": user.email,
        "mustChangePassword": user.must_change_password,
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
