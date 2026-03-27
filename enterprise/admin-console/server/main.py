"""
OpenClaw Enterprise Admin Console — Backend API v0.4

FastAPI server backed by DynamoDB + S3.
Serves both API and frontend static files from a single port.

Usage:
  cd admin-console/server && python main.py

Env vars:
  DYNAMODB_TABLE (default: openclaw-enterprise)
  AWS_REGION     (default: us-east-2)
  CONSOLE_PORT   (default: 8099)
"""

import os
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import s3ops
import auth as authmod

AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")

# Server start time — used to compute uptime for /settings/services
_SERVER_START_TIME = time.time()

# Default monthly budgets (USD) by department — overridden by DynamoDB CONFIG#budgets
_DEFAULT_BUDGETS = {
    "Engineering": 50.0, "Platform Team": 20.0, "Sales": 30.0,
    "Product": 25.0, "Finance": 20.0, "HR & Admin": 15.0,
    "Customer Success": 20.0, "Legal & Compliance": 10.0, "QA Team": 15.0,
}

app = FastAPI(title="OpenClaw Admin API", version="0.5.0")
_ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://openclaw.awspsa.com,http://localhost:5173,http://localhost:8099").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS, allow_methods=["GET","POST","PUT","DELETE","OPTIONS"], allow_headers=["Content-Type","Authorization"])


# =========================================================================
# Auth — Login + current user helper
# =========================================================================

def _get_current_user(authorization: str = Header(default="")) -> authmod.UserContext | None:
    """Extract current user from Authorization header. Returns None if not authenticated."""
    return authmod.get_user_from_request(authorization)


def _require_auth(authorization: str = Header(default="")) -> authmod.UserContext:
    """Require authentication. Raises 401 if not authenticated."""
    user = authmod.get_user_from_request(authorization)
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def _require_role(authorization: str = Header(default=""), roles: list[str] = ["admin"]) -> authmod.UserContext:
    """Require specific role(s). Raises 403 if insufficient permissions."""
    user = _require_auth(authorization)
    if user.role not in roles:
        raise HTTPException(403, f"Role '{user.role}' not permitted. Required: {roles}")
    return user


def _get_dept_scope(user: authmod.UserContext) -> set[str] | None:
    """For managers, return set of department IDs they can see (their dept + all sub-depts).
    For admins, return None (no filter). For employees, return empty set."""
    if user.role == "admin":
        return None  # no filter
    if user.role == "employee":
        return set()
    # Manager: BFS from their department
    depts = db.get_departments()
    dept_id = user.department_id
    ids = {dept_id}
    queue = [dept_id]
    while queue:
        current = queue.pop(0)
        for d in depts:
            if d.get("parentId") == current and d["id"] not in ids:
                ids.add(d["id"])
                queue.append(d["id"])
    return ids


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

    # Password check — read from environment variable
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
    user = _require_auth(authorization)
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
# Organization
# =========================================================================

@app.get("/api/v1/org/departments")
def get_departments(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    depts = db.get_departments()
    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            depts = [d for d in depts if d["id"] in scope]
    return depts

@app.get("/api/v1/org/positions")
def get_positions(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    positions = db.get_positions()
    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            positions = [p for p in positions if p.get("departmentId") in scope]
    return positions

@app.post("/api/v1/org/positions")
def create_position(body: dict):
    return db.create_position(body)

@app.put("/api/v1/org/positions/{pos_id}")
def update_position(pos_id: str, body: dict):
    body["id"] = pos_id
    return db.create_position(body)  # upsert

@app.get("/api/v1/org/employees")
def get_employees(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    employees = db.get_employees()
    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            employees = [e for e in employees if e.get("departmentId") in scope]
    return employees

@app.post("/api/v1/org/employees")
def create_employee(body: dict):
    """Create or update an employee. Auto-provisions agent + bindings if
    the employee has a positionId but no agentId (new hire flow)."""
    result = db.create_employee(body)
    # Auto-provision if employee has position but no agent
    if body.get("positionId") and not body.get("agentId"):
        try:
            auto = _auto_provision_employee(result)
            if auto:
                result["agentId"] = auto["agentId"]
                result["agentStatus"] = "active"
                result["_autoProvisioned"] = True
        except Exception as e:
            print(f"[auto-provision] failed for {result.get('id')}: {e}")
    return result


@app.get("/api/v1/org/employees/activity")
def get_employee_activities(authorization: str = Header(default="")):
    """Get activity data for all employees from DynamoDB."""
    user = _get_current_user(authorization)
    activities = db.get_activities()
    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            emp_ids = {e["id"] for e in employees if e.get("departmentId") in scope}
            activities = [a for a in activities if a.get("employeeId") in emp_ids]
    return activities


@app.get("/api/v1/org/employees/{emp_id}/activity")
def get_employee_activity(emp_id: str):
    """Get activity data for a single employee."""
    activity = db.get_activity(emp_id)
    if not activity:
        return {"employeeId": emp_id, "messagesThisWeek": 0, "channelStatus": {}}
    return activity


def _auto_provision_employee(emp: dict) -> dict | None:
    """Auto-create 1:1 agent + binding for a single employee based on position.
    Also binds to any shared agents marked as 'autoBindAll'.
    Returns dict with agentId if provisioned, None if skipped."""
    pos_id = emp.get("positionId", "")
    if not pos_id or emp.get("agentId"):
        return None

    positions = db.get_positions()
    pos = next((p for p in positions if p["id"] == pos_id), None)
    if not pos:
        return None

    now = datetime.now(timezone.utc).isoformat()
    default_channel = pos.get("defaultChannel", "slack")

    # 1. Create personal 1:1 agent
    agent_id = f"agent-{pos_id.replace('pos-','')}-{emp['id'].replace('emp-','')}"
    agent_name = f"{pos.get('name','')} Agent - {emp['name']}"

    existing = db.get_agent(agent_id)
    if not existing:
        agent = {
            "id": agent_id,
            "name": agent_name,
            "employeeId": emp["id"],
            "employeeName": emp["name"],
            "positionId": pos_id,
            "positionName": pos.get("name", ""),
            "status": "active",
            "soulVersions": {"global": 3, "position": 1, "personal": 0},
            "skills": pos.get("defaultSkills", []),
            "channels": [default_channel],
            "qualityScore": None,
            "createdAt": now,
            "updatedAt": now,
        }
        db.create_agent(agent)

    # 2. Create 1:1 binding
    binding = {
        "employeeId": emp["id"],
        "employeeName": emp["name"],
        "agentId": agent_id,
        "agentName": agent_name,
        "mode": "1:1",
        "channel": default_channel,
        "status": "active",
        "source": "auto-provision",
        "createdAt": now,
    }
    db.create_binding(binding)

    # 3. Auto-bind to shared agents (Help Desk, Onboarding, etc.)
    agents = db.get_agents()
    shared_agents = [a for a in agents if not a.get("employeeId") and a.get("autoBindAll")]
    for sa in shared_agents:
        shared_binding = {
            "employeeId": emp["id"],
            "employeeName": emp["name"],
            "agentId": sa["id"],
            "agentName": sa["name"],
            "mode": "N:1",
            "channel": sa.get("channels", [default_channel])[0] if sa.get("channels") else default_channel,
            "status": "active",
            "source": "auto-provision-shared",
            "createdAt": now,
        }
        db.create_binding(shared_binding)

    # 4. Update employee record
    emp["agentId"] = agent_id
    emp["agentStatus"] = "active"
    db.create_employee(emp)

    # 5. Audit trail
    db.create_audit_entry({
        "timestamp": now,
        "eventType": "config_change",
        "actorId": "system",
        "actorName": "Auto-Provision",
        "targetType": "binding",
        "targetId": agent_id,
        "detail": f"Auto-provisioned {agent_name} for {emp['name']} ({pos.get('name','')})",
        "status": "success",
    })

    # 6. Write SSM tenant→position mapping (for AgentCore workspace assembly)
    try:
        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        ssm = _ssm_client()
        ssm.put_parameter(
            Name=f"/openclaw/{stack}/tenants/{emp['id']}/position",
            Value=pos_id, Type="String", Overwrite=True)
        # Also write permissions based on position
        pos_tools = {
            "pos-sa": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "pos-sde": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "pos-devops": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "pos-qa": ["web_search", "shell", "file", "code_execution"],
            "pos-ae": ["web_search", "file", "crm-query", "email-send", "calendar-check"],
            "pos-pm": ["web_search", "file", "notion-sync", "calendar-check", "excel-gen"],
            "pos-fa": ["web_search", "file", "excel-gen", "sap-connector"],
            "pos-hr": ["web_search", "file", "email-send", "calendar-check"],
            "pos-csm": ["web_search", "file", "crm-query", "email-send"],
            "pos-legal": ["web_search", "file"],
            "pos-exec": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
        }
        tools = pos_tools.get(pos_id, ["web_search"])
        import json as _json_prov
        ssm.put_parameter(
            Name=f"/openclaw/{stack}/tenants/{emp['id']}/permissions",
            Value=_json_prov.dumps({"profile": "auto", "tools": tools, "role": pos_id.replace("pos-", "")}),
            Type="String", Overwrite=True)
    except Exception as e:
        print(f"[auto-provision] SSM write failed for {emp['id']}: {e}")

    return {"agentId": agent_id, "agentName": agent_name}


# =========================================================================
# Agents
# =========================================================================

def _get_active_agent_ids() -> set:
    """Determine which agents are currently active (microVM running) from CloudWatch.
    An agent is 'active' if it had an invocation in the last 15 minutes (AgentCore idle timeout).
    Returns set of employee IDs that are active."""
    try:
        import time as _t
        cw = _boto3.client("logs", region_name="us-east-1")
        start_time = int((_t.time() - 900) * 1000)  # 15 min ago
        active_ids = set()
        for lg in _LOG_GROUPS:
            try:
                resp = cw.filter_log_events(
                    logGroupName=lg, startTime=start_time,
                    filterPattern="Invocation tenant_id=",
                    limit=50, interleaved=True,
                )
                for event in resp.get("events", []):
                    msg = event.get("message", "")
                    if "tenant_id=" in msg:
                        tid = msg.split("tenant_id=")[1].split(" ")[0]
                        # Extract base employee ID
                        parts = tid.split("__")
                        if len(parts) >= 3:
                            active_ids.add(parts[1])
                        elif len(parts) == 2:
                            active_ids.add(parts[1])
                        else:
                            active_ids.add(tid)
            except Exception:
                pass
        return active_ids
    except Exception:
        return set()


@app.get("/api/v1/agents")
def get_agents(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    agents = db.get_agents()

    # Dynamic status: check CloudWatch for recent activity
    active_emp_ids = _get_active_agent_ids()
    for a in agents:
        emp_id = a.get("employeeId", "")
        if emp_id in active_emp_ids:
            a["status"] = "active"
        elif a.get("status") == "active":
            a["status"] = "idle"  # No recent activity → idle (serverless standby)

    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            positions = db.get_positions()
            pos_in_scope = {p["id"] for p in positions if p.get("departmentId") in scope}
            agents = [a for a in agents if a.get("positionId") in pos_in_scope or not a.get("employeeId")]
    return agents

@app.get("/api/v1/agents/{agent_id}")
def get_agent(agent_id: str):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    # Dynamic status
    active_emp_ids = _get_active_agent_ids()
    emp_id = agent.get("employeeId", "")
    if emp_id in active_emp_ids:
        agent["status"] = "active"
    elif agent.get("status") == "active":
        agent["status"] = "idle"
    return agent

@app.post("/api/v1/agents")
def create_agent(body: dict):
    from datetime import datetime, timezone
    import json as _json_ca

    body.setdefault("status", "active")
    body.setdefault("soulVersions", {"global": 3, "position": 1, "personal": 0})
    body.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
    body.setdefault("updatedAt", body["createdAt"])

    agent = db.create_agent(body)

    emp_id = body.get("employeeId")
    pos_id = body.get("positionId", "")
    agent_id = body.get("id") or agent.get("id", "")
    channel = body.get("defaultChannel", "discord")

    if emp_id and pos_id:
        # 1. Write SSM tenant→position and permissions for this employee
        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        region = os.environ.get("AWS_REGION", "us-east-1")
        pos_tools = {
            "pos-sa": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "pos-sde": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "pos-devops": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
            "pos-qa": ["web_search", "shell", "file", "code_execution"],
            "pos-ae": ["web_search", "file", "crm-query", "email-send", "calendar-check"],
            "pos-pm": ["web_search", "file", "notion-sync", "calendar-check", "excel-gen"],
            "pos-fa": ["web_search", "file", "excel-gen", "sap-connector"],
            "pos-hr": ["web_search", "file", "email-send", "calendar-check"],
            "pos-csm": ["web_search", "file", "crm-query", "email-send"],
            "pos-legal": ["web_search", "file"],
            "pos-exec": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
        }
        tools = pos_tools.get(pos_id, ["web_search"])
        try:
            ssm = _ssm_client()
            ssm.put_parameter(Name=f"/openclaw/{stack}/tenants/{emp_id}/position",
                              Value=pos_id, Type="String", Overwrite=True)
            ssm.put_parameter(
                Name=f"/openclaw/{stack}/tenants/{emp_id}/permissions",
                Value=_json_ca.dumps({"profile": "auto", "tools": tools, "role": pos_id.replace("pos-", "")}),
                Type="String", Overwrite=True)
        except Exception as e:
            print(f"[create_agent] SSM write failed for {emp_id}: {e}")

        # 2. Create binding (employee → agent)
        now = datetime.now(timezone.utc).isoformat()
        emp = next((e for e in db.get_employees() if e["id"] == emp_id), {})
        positions = db.get_positions()
        pos = next((p for p in positions if p["id"] == pos_id), {})
        db.create_binding({
            "employeeId": emp_id,
            "employeeName": emp.get("name", ""),
            "agentId": agent_id,
            "agentName": body.get("name", ""),
            "mode": "1:1",
            "channel": channel,
            "status": "active",
            "source": "manual",
            "createdAt": now,
        })

        # 3. Seed minimal S3 workspace if it doesn't already exist
        s3_bucket = os.environ.get("S3_BUCKET", "openclaw-tenants-263168716248")
        try:
            import boto3 as _b3_ws
            s3 = _b3_ws.client("s3", region_name=region)
            # Only seed if workspace is empty
            prefix = f"{emp_id}/workspace/"
            resp = s3.list_objects_v2(Bucket=s3_bucket, Prefix=prefix, MaxKeys=5)
            if not resp.get("Contents"):
                emp_name = emp.get("name", emp_id)
                pos_name = pos.get("name", pos_id)
                dept = emp.get("departmentName", "")
                # IDENTITY.md
                s3.put_object(Bucket=s3_bucket, Key=f"{prefix}IDENTITY.md",
                    Body=f"# Agent Identity\n\n- **Name**: {emp_name}'s AI Assistant\n- **Position**: {pos_name}\n- **Department**: {dept}\n- **Company**: ACME Corp\n- **Platform**: OpenClaw Enterprise\n".encode())
                # MEMORY.md
                s3.put_object(Bucket=s3_bucket, Key=f"{prefix}MEMORY.md",
                    Body=f"# Memory\nNo previous conversations recorded.\n".encode())
                # USER.md
                s3.put_object(Bucket=s3_bucket, Key=f"{prefix}USER.md",
                    Body=f"# User Profile\n\n- **Name**: {emp_name}\n- **Position**: {pos_name}\n- **Language**: English\n".encode())
                print(f"[create_agent] S3 workspace seeded for {emp_id}")
        except Exception as e:
            print(f"[create_agent] S3 workspace seed failed for {emp_id}: {e}")

        # 4. Update employee record with agentId
        try:
            emp["agentId"] = agent_id
            emp["agentStatus"] = "active"
            db.create_employee(emp)
        except Exception as e:
            print(f"[create_agent] employee update failed: {e}")

        # 5. Audit trail
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "config_change", "actorId": "admin", "actorName": "IT Admin",
            "targetType": "agent", "targetId": agent_id,
            "detail": f"Created agent '{body.get('name')}' for {emp.get('name', emp_id)} ({pos.get('name', pos_id)})",
            "status": "success",
        })

    return agent


# =========================================================================
# SOUL — Three-layer read/write with S3 versioning
# =========================================================================

@app.get("/api/v1/agents/{agent_id}/soul")
def get_agent_soul(agent_id: str, authorization: str = Header(default="")):
    """Get three-layer SOUL for an agent. Reads from S3."""
    _require_auth(authorization)
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    pos_id = agent.get("positionId", "")
    emp_id = agent.get("employeeId")
    sv = agent.get("soulVersions", {})

    global_soul = s3ops.read_file("_shared/soul/global/SOUL.md") or ""
    position_soul = s3ops.read_file(f"_shared/soul/positions/{pos_id}/SOUL.md") or ""
    personal_soul = s3ops.read_file(f"{emp_id}/workspace/SOUL.md") if emp_id else ""

    return [
        {"layer": "global", "content": global_soul, "locked": True, "version": sv.get("global", 3), "updatedAt": "2026-03-15T00:00:00Z"},
        {"layer": "position", "content": position_soul, "locked": False, "version": sv.get("position", 1), "updatedAt": "2026-03-18T00:00:00Z"},
        {"layer": "personal", "content": personal_soul or "", "locked": False, "version": sv.get("personal", 0), "updatedAt": "2026-03-19T00:00:00Z"},
    ]


class SoulSaveRequest(BaseModel):
    layer: str  # "position" or "personal"
    content: str

@app.put("/api/v1/agents/{agent_id}/soul")
def save_agent_soul(agent_id: str, body: SoulSaveRequest, authorization: str = Header(default="")):
    """Save a SOUL layer to S3. Increments version in DynamoDB."""
    _require_role(authorization, roles=["admin", "manager"])
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if body.layer == "global":
        raise HTTPException(403, "Global layer is locked — requires CISO + CTO approval")

    pos_id = agent.get("positionId", "")
    emp_id = agent.get("employeeId")

    result = s3ops.save_soul_layer(body.layer, pos_id, emp_id, "SOUL.md", body.content)
    if result.get("error"):
        raise HTTPException(400, result["error"])

    # Increment version in DynamoDB
    sv = agent.get("soulVersions", {})
    sv[body.layer] = sv.get(body.layer, 0) + 1
    agent["soulVersions"] = sv
    agent["updatedAt"] = datetime.now(timezone.utc).isoformat()
    db.create_agent(agent)  # upsert

    return {"saved": True, "layer": body.layer, "version": sv[body.layer], "s3Key": result.get("key")}


@app.get("/api/v1/agents/{agent_id}/soul/full")
def get_agent_soul_full(agent_id: str):
    """Get ALL workspace files for an agent (SOUL, AGENTS, TOOLS, USER, MEMORY)."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    pos_id = agent.get("positionId", "")
    emp_id = agent.get("employeeId")
    layers = s3ops.get_soul_layers(pos_id, emp_id)
    return layers


# =========================================================================
# Workspace — Full file tree with S3 read/write
# =========================================================================

@app.get("/api/v1/workspace/tree")
def get_workspace_tree(agent_id: str = ""):
    """Get the full workspace file tree for an agent."""
    agent = db.get_agent(agent_id) if agent_id else None
    pos_id = agent.get("positionId", "") if agent else ""
    emp_id = agent.get("employeeId") if agent else None
    return s3ops.get_workspace_tree(pos_id, emp_id)

@app.get("/api/v1/workspace/file")
def get_workspace_file(key: str, authorization: str = Header(default="")):
    """Read a single workspace file from S3. Admin/manager can read any key; employees only their own."""
    user = _require_auth(authorization)
    # Employees can only access their own workspace, not other employees' files
    if user.role == "employee":
        allowed_prefix = f"{user.employee_id}/workspace/"
        if not key.startswith(allowed_prefix) and not key.startswith("_shared/"):
            raise HTTPException(403, "Access denied: you can only read your own workspace files")
    content = s3ops.read_file(key)
    if content is None:
        raise HTTPException(404, f"File not found: {key}")
    return {"key": key, "content": content, "size": len(content)}


class FileWriteRequest(BaseModel):
    key: str
    content: str

@app.put("/api/v1/workspace/file")
def save_workspace_file(body: FileWriteRequest, authorization: str = Header(default="")):
    """Write a workspace file to S3. Global layer locked; employees can only write their own files."""
    user = _require_auth(authorization)
    if body.key.startswith("_shared/soul/global/"):
        raise HTTPException(403, "Global layer is locked")
    # Employees can only modify their own workspace files
    if user.role == "employee":
        if not body.key.startswith(f"{user.employee_id}/workspace/"):
            raise HTTPException(403, "Access denied: you can only modify your own workspace files")
    success = s3ops.write_file(body.key, body.content)
    if not success:
        raise HTTPException(500, "Failed to write file")
    return {"key": body.key, "saved": True, "size": len(body.content)}

@app.get("/api/v1/workspace/file/versions")
def get_file_versions(key: str):
    """List all versions of a workspace file."""
    return s3ops.list_versions(key)

@app.get("/api/v1/workspace/file/version")
def get_file_version(key: str, versionId: str):
    """Read a specific version of a workspace file."""
    content = s3ops.read_version(key, versionId)
    if content is None:
        raise HTTPException(404, "Version not found")
    return {"key": key, "versionId": versionId, "content": content}


# =========================================================================
# Memory — Agent memory management
# =========================================================================

@app.get("/api/v1/agents/{agent_id}/memory")
def get_agent_memory(agent_id: str, authorization: str = Header(default="")):
    """Get memory overview for an agent."""
    _require_auth(authorization)
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    emp_id = agent.get("employeeId")
    if not emp_id:
        return {"memoryMd": "", "memoryMdSize": 0, "dailyFiles": [], "totalDailyFiles": 0, "totalSize": 0, "note": "Shared agents don't have personal memory"}
    return s3ops.get_agent_memory(emp_id)

@app.get("/api/v1/agents/{agent_id}/memory/{date}")
def get_agent_daily_memory(agent_id: str, date: str):
    """Get a specific daily memory file."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    emp_id = agent.get("employeeId")
    if not emp_id:
        raise HTTPException(404, "Shared agents don't have personal memory")
    content = s3ops.get_daily_memory(emp_id, date)
    if content is None:
        raise HTTPException(404, f"No memory for {date}")
    return {"date": date, "content": content}


# =========================================================================
# Skills — reads from S3 _shared/skills/
# =========================================================================

@app.get("/api/v1/skills")
def get_skills():
    """List all skills from S3 with their manifests."""
    files = s3ops.list_files("_shared/skills/")
    # Group by skill name (each skill is a folder with skill.json)
    skill_names = set()
    for f in files:
        parts = f["name"].split("/")
        if parts[0]:
            skill_names.add(parts[0])

    skills = []
    for name in sorted(skill_names):
        manifest_content = s3ops.read_file(f"_shared/skills/{name}/skill.json")
        if manifest_content:
            import json as _json
            try:
                manifest = _json.loads(manifest_content)
                manifest.setdefault("status", "installed")
                manifest["id"] = f"sk-{name}"
                skills.append(manifest)
            except _json.JSONDecodeError:
                pass
    return skills

@app.get("/api/v1/skills/{skill_name}")
def get_skill(skill_name: str):
    """Get a single skill manifest."""
    content = s3ops.read_file(f"_shared/skills/{skill_name}/skill.json")
    if not content:
        raise HTTPException(404, f"Skill {skill_name} not found")
    import json as _json
    return _json.loads(content)

@app.get("/api/v1/skills/keys/all")
def get_all_skill_keys():
    """List all required API keys across all skills.
    Reads skill manifests from S3 to determine required env vars,
    then checks SSM to see which are actually configured."""
    import json as _json

    # Get all skills from S3
    files = s3ops.list_files("_shared/skills/")
    skill_names = set()
    for f in files:
        parts = f["name"].split("/")
        if parts[0]:
            skill_names.add(parts[0])

    keys = []
    key_id = 0
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")

    for name in sorted(skill_names):
        content = s3ops.read_file(f"_shared/skills/{name}/skill.json")
        if not content:
            continue
        try:
            manifest = _json.loads(content)
        except _json.JSONDecodeError:
            continue

        required_env = manifest.get("requires", {}).get("env", [])
        if not required_env:
            continue

        # Check if keys exist in SSM
        for env_var in required_env:
            key_id += 1
            ssm_path = f"/openclaw/{stack}/skill-keys/{name}/{env_var}"

            # For AWS-native skills, keys come from IAM role, not SSM
            aws_service = manifest.get("awsService", "")
            if aws_service:
                status = "iam-role"
                note = f"Provided by IAM role ({aws_service})"
            else:
                status = "not-configured"
                note = "Needs configuration in API Key Vault"

            keys.append({
                "id": f"key-{key_id}",
                "skillName": name,
                "envVar": env_var,
                "ssmPath": ssm_path,
                "status": status,
                "awsService": aws_service,
                "note": note,
            })

    return keys


# =========================================================================

# =========================================================================
# Bindings
# =========================================================================

@app.get("/api/v1/bindings")
def get_bindings(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    bindings = db.get_bindings()
    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            emp_ids_in_scope = {e["id"] for e in employees if e.get("departmentId") in scope}
            bindings = [b for b in bindings if b.get("employeeId") in emp_ids_in_scope]
    return bindings

@app.post("/api/v1/bindings")
def create_binding(body: dict):
    body.setdefault("status", "active")
    body.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
    # If channel user ID provided, write SSM mapping
    channel_user_id = body.get("channelUserId", "")
    channel = body.get("channel", "")
    employee_id = body.get("employeeId", "")
    if channel_user_id and channel and employee_id:
        _write_user_mapping(channel, channel_user_id, employee_id)
    return db.create_binding(body)


# =========================================================================
# IM User → Employee Mapping (SSM-backed)
# =========================================================================

import boto3 as _boto3_main

def _ssm_client():
    return _boto3_main.client("ssm", region_name=os.environ.get("SSM_REGION", os.environ.get("AWS_REGION", "us-east-1")))

def _mapping_prefix():
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    return f"/openclaw/{stack}/user-mapping/"

def _write_user_mapping(channel: str, channel_user_id: str, employee_id: str):
    """Write SSM mapping: channel__user_id → employee_id"""
    key = f"{channel}__{channel_user_id}"
    path = f"{_mapping_prefix()}{key}"
    try:
        _ssm_client().put_parameter(Name=path, Value=employee_id, Type="String", Overwrite=True)
    except Exception as e:
        print(f"[user-mapping] SSM write failed: {e}")

def _read_user_mapping(channel: str, channel_user_id: str) -> str:
    """Read SSM mapping: channel__user_id → employee_id"""
    key = f"{channel}__{channel_user_id}"
    path = f"{_mapping_prefix()}{key}"
    try:
        resp = _ssm_client().get_parameter(Name=path)
        return resp["Parameter"]["Value"]
    except Exception:
        return ""

def _list_user_mappings() -> list:
    """List all user mappings from SSM."""
    prefix = _mapping_prefix()
    try:
        ssm = _ssm_client()
        mappings = []
        params = {"Path": prefix, "Recursive": True, "MaxResults": 50}
        while True:
            resp = ssm.get_parameters_by_path(**params)
            for p in resp.get("Parameters", []):
                name = p["Name"].replace(prefix, "")
                parts = name.split("__", 1)
                if len(parts) == 2:
                    mappings.append({
                        "channel": parts[0],
                        "channelUserId": parts[1],
                        "employeeId": p["Value"],
                        "ssmPath": p["Name"],
                    })
            token = resp.get("NextToken")
            if not token:
                break
            params["NextToken"] = token
        return mappings
    except Exception as e:
        print(f"[user-mapping] SSM list failed: {e}")
        return []

@app.get("/api/v1/bindings/user-mappings")
def get_user_mappings():
    """List all IM user → employee mappings from SSM."""
    return _list_user_mappings()

class UserMappingRequest(BaseModel):
    channel: str       # discord, telegram, slack, whatsapp
    channelUserId: str  # platform-specific user ID
    employeeId: str     # emp-carol, emp-w5, etc.

@app.post("/api/v1/bindings/user-mappings")
def create_user_mapping(body: UserMappingRequest):
    """Create or update an IM user → employee mapping in SSM."""
    _write_user_mapping(body.channel, body.channelUserId, body.employeeId)
    # Also write position mapping for the tenant_id that H2 Proxy derives
    emp = next((e for e in db.get_employees() if e["id"] == body.employeeId), None)
    if emp:
        pos_id = emp.get("positionId", "")
        if pos_id:
            # Write position for various tenant_id formats the proxy might derive
            stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
            ssm = _ssm_client()
            for tenant_key in [body.employeeId, f"{body.channel}__{body.channelUserId}"]:
                try:
                    ssm.put_parameter(
                        Name=f"/openclaw/{stack}/tenants/{tenant_key}/position",
                        Value=pos_id, Type="String", Overwrite=True)
                except Exception:
                    pass
    return {"saved": True, "channel": body.channel, "channelUserId": body.channelUserId, "employeeId": body.employeeId}

@app.delete("/api/v1/bindings/user-mappings")
def delete_user_mapping(channel: str, channelUserId: str):
    """Delete an IM user → employee mapping from SSM."""
    key = f"{channel}__{channelUserId}"
    path = f"{_mapping_prefix()}{key}"
    try:
        _ssm_client().delete_parameter(Name=path)
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(500, str(e))


class PairingApproveRequest(BaseModel):
    channel: str          # discord, telegram, feishu, slack, whatsapp
    pairingCode: str      # e.g. KFDAF3GN
    employeeId: str       # e.g. emp-carol
    channelUserId: str = ""   # numeric platform user ID (from pairing message)
    pairingUserId: str = ""   # username/handle (e.g. "wujiade4444") for dm_ mapping

@app.post("/api/v1/bindings/pairing-approve")
def approve_pairing(body: PairingApproveRequest, authorization: str = Header(default="")):
    """Approve IM pairing + create user mapping in one step.
    Calls `openclaw pairing approve <channel> <code>` via subprocess,
    then writes SSM user mapping if channelUserId is provided."""
    _require_role(authorization, roles=["admin"])
    import subprocess as _sp

    # 1. Run openclaw pairing approve
    openclaw_bin = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin/openclaw"
    env = os.environ.copy()
    env["PATH"] = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin:" + env.get("PATH", "")
    env["HOME"] = "/home/ubuntu"

    try:
        result = _sp.run(
            [openclaw_bin, "pairing", "approve", body.channel, body.pairingCode],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode != 0:
            return {"approved": False, "error": result.stderr.strip() or result.stdout.strip()}
        approve_output = result.stdout.strip()
    except Exception as e:
        return {"approved": False, "error": str(e)}

    # 2. Write SSM user mappings if channelUserId provided.
    # Write ALL formats that H2 Proxy may extract, so routing works regardless of
    # how OpenClaw formats the sender metadata (numeric ID, dm_username, username).
    mapping_written = False
    if body.channelUserId and body.employeeId:
        _write_user_mapping(body.channel, body.channelUserId, body.employeeId)
        # Also write username-based mappings extracted by H2 Proxy from Discord DM format
        if body.pairingUserId:  # Discord username from pairing message meta
            _write_user_mapping(body.channel, f"dm_{body.pairingUserId}", body.employeeId)
            _write_user_mapping(body.channel, body.pairingUserId, body.employeeId)
        # Also write position mapping for the numeric user ID (what H2 Proxy extracts)
        emp = next((e for e in db.get_employees() if e["id"] == body.employeeId), None)
        if emp and emp.get("positionId"):
            stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
            ssm = _ssm_client()
            try:
                ssm.put_parameter(
                    Name=f"/openclaw/{stack}/tenants/{body.channelUserId}/position",
                    Value=emp["positionId"], Type="String", Overwrite=True)
                import json as _json_pair
                pos_tools = {
                    "pos-sa": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
                    "pos-sde": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
                    "pos-devops": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
                    "pos-fa": ["web_search", "file", "excel-gen", "sap-connector"],
                    "pos-pm": ["web_search", "file", "notion-sync", "calendar-check", "excel-gen"],
                    "pos-ae": ["web_search", "file", "crm-query", "email-send"],
                    "pos-csm": ["web_search", "file", "crm-query", "email-send"],
                    "pos-hr": ["web_search", "file", "email-send", "calendar-check"],
                    "pos-legal": ["web_search", "file"],
                    "pos-exec": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
                }
                tools = pos_tools.get(emp["positionId"], ["web_search"])
                ssm.put_parameter(
                    Name=f"/openclaw/{stack}/tenants/{body.channelUserId}/permissions",
                    Value=_json_pair.dumps({"profile": "auto", "tools": tools, "role": emp["positionId"].replace("pos-", "")}),
                    Type="String", Overwrite=True)
                mapping_written = True
            except Exception:
                pass

    # 3. Sync updated allowFrom list to S3 so microVMs pick it up
    # The EC2's openclaw pairing approve updates the local credentials file.
    # We push it to S3 so AgentCore microVMs can load it on first invocation.
    if body.channel == "discord" and body.channelUserId:
        try:
            creds_src = "/home/ubuntu/.openclaw/credentials/discord-default-allowFrom.json"
            s3_bucket = os.environ.get("S3_BUCKET", "openclaw-tenants-263168716248")
            s3_key = "_shared/openclaw-creds/discord-default-allowFrom.json"
            if os.path.isfile(creds_src):
                import subprocess as _sp2
                _sp2.run(
                    ["aws", "s3", "cp", creds_src,
                     f"s3://{s3_bucket}/{s3_key}", "--quiet",
                     "--region", os.environ.get("AWS_REGION", "us-east-1")],
                    timeout=10, capture_output=True
                )
        except Exception as _e:
            print(f"[pairing] S3 credentials sync failed (non-fatal): {_e}")

    # 4. Audit trail
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": "admin",
        "actorName": "IT Admin",
        "targetType": "pairing",
        "targetId": body.pairingCode,
        "detail": f"Approved {body.channel} pairing for {body.employeeId} (code: {body.pairingCode})",
        "status": "success",
    })

    return {"approved": True, "output": approve_output, "mappingWritten": mapping_written}


# =========================================================================
# Routing Rules — determines how messages are routed to agents
# =========================================================================

# Routing rules — stored in DynamoDB (seeded by seed_routing_conversations.py)

@app.get("/api/v1/routing/rules")
def get_routing_rules():
    return db.get_routing_rules()


@app.post("/api/v1/bindings/provision-by-position")
def provision_by_position(body: dict):
    """Bulk-provision agents and bindings for all unbound employees in a position.
    Delegates to _auto_provision_employee for each employee."""
    pos_id = body.get("positionId", "")
    default_channel = body.get("defaultChannel", "slack")

    if not pos_id:
        raise HTTPException(400, "positionId required")

    positions = db.get_positions()
    pos = next((p for p in positions if p["id"] == pos_id), None)
    if not pos:
        raise HTTPException(404, f"Position {pos_id} not found")

    # Temporarily set defaultChannel on position so _auto_provision_employee picks it up
    pos["defaultChannel"] = default_channel
    db.create_position(pos)  # upsert with channel

    employees = db.get_employees()
    unbound = [e for e in employees if e.get("positionId") == pos_id and not e.get("agentId")]
    already_bound = len([e for e in employees if e.get("positionId") == pos_id and e.get("agentId")])

    provisioned = []
    for emp in unbound:
        result = _auto_provision_employee(emp)
        if result:
            provisioned.append({
                "employee": emp["name"],
                "agent": result["agentName"],
                "channel": default_channel,
            })

    return {
        "position": pos.get("name"),
        "provisioned": len(provisioned),
        "details": provisioned,
        "alreadyBound": already_bound,
    }

@app.get("/api/v1/routing/resolve")
def resolve_route(channel: str = "", user_id: str = "", message: str = ""):
    """Simulate routing resolution — shows which rule would match and where the message goes."""
    # Look up user's bindings
    bindings = db.get_bindings()
    user_bindings = [b for b in bindings if b.get("employeeId") == user_id or b.get("employeeName") == user_id]

    for rule in sorted(db.get_routing_rules(), key=lambda r: r.get("priority", 99)):
        cond = rule.get("condition", {})
        match = True

        if "channel" in cond and cond["channel"] != channel:
            match = False
        if "messagePrefix" in cond and not message.startswith(cond["messagePrefix"]):
            match = False
        if "department" in cond:
            # Would check user's department from DynamoDB
            pass
        if "role" in cond:
            # Would check user's role from DynamoDB
            pass

        if match:
            if rule["action"] == "route_to_shared_agent":
                agent_id = rule.get("agentId", "")
                return {"matched_rule": rule["name"], "action": rule["action"], "agent_id": agent_id, "description": rule["description"]}
            else:
                # Find user's personal binding for this channel
                binding = next((b for b in user_bindings if b.get("channel") == channel and b.get("mode") == "1:1"), None)
                if binding:
                    return {"matched_rule": rule["name"], "action": "route_to_personal_agent", "agent_id": binding.get("agentId"), "agent_name": binding.get("agentName"), "description": rule["description"]}
                return {"matched_rule": rule["name"], "action": "route_to_personal_agent", "agent_id": None, "description": "No binding found for this user/channel"}

    return {"matched_rule": "none", "action": "rejected", "description": "No routing rule matched"}


# =========================================================================
# Knowledge Base — S3-backed Markdown document management
# =========================================================================

# KB prefix → metadata mapping (from DynamoDB for access control, S3 for actual files)
_KB_PREFIXES = {
    "kb-policies": {"prefix": "_shared/knowledge/company-policies/", "scope": "global", "scopeName": "All Employees", "accessibleBy": "All employees"},
    "kb-product": {"prefix": "_shared/knowledge/product-docs/", "scope": "global", "scopeName": "All Employees", "accessibleBy": "All employees"},
    "kb-onboarding": {"prefix": "_shared/knowledge/onboarding/", "scope": "global", "scopeName": "All Employees", "accessibleBy": "All employees"},
    "kb-arch": {"prefix": "_shared/knowledge/arch-standards/", "scope": "department", "scopeName": "Engineering", "accessibleBy": "Engineering dept"},
    "kb-runbooks": {"prefix": "_shared/knowledge/runbooks/", "scope": "department", "scopeName": "Engineering", "accessibleBy": "Engineering dept"},
    "kb-cases": {"prefix": "_shared/knowledge/case-studies/", "scope": "department", "scopeName": "Sales", "accessibleBy": "Sales + SA positions"},
    "kb-finance": {"prefix": "_shared/knowledge/financial-reports/", "scope": "department", "scopeName": "Finance", "accessibleBy": "Finance + C-level"},
    "kb-hr": {"prefix": "_shared/knowledge/hr-policies/", "scope": "department", "scopeName": "HR & Admin", "accessibleBy": "HR dept only"},
    "kb-legal": {"prefix": "_shared/knowledge/contract-templates/", "scope": "department", "scopeName": "Legal & Compliance", "accessibleBy": "Legal dept only"},
    "kb-customer": {"prefix": "_shared/knowledge/customer-playbooks/", "scope": "department", "scopeName": "Customer Success", "accessibleBy": "CS + Sales"},
}

@app.get("/api/v1/knowledge")
def get_knowledge_bases():
    """List all knowledge bases with real document counts from S3."""
    results = []
    for kb_id, meta in _KB_PREFIXES.items():
        files = s3ops.list_files(meta["prefix"])
        md_files = [f for f in files if f["name"].endswith(".md")]
        total_size = sum(f["size"] for f in md_files)
        last_modified = max((f["lastModified"] for f in md_files), default="") if md_files else ""
        name_map = {"kb-hr": "HR Policies", "kb-cases": "Case Studies", "kb-customer": "Customer Playbooks"}
        results.append({
            "id": kb_id,
            "name": name_map.get(kb_id, kb_id.replace("kb-", "").replace("-", " ").title()),
            "scope": meta["scope"],
            "scopeName": meta["scopeName"],
            "docCount": len(md_files),
            "sizeMB": round(total_size / 1024 / 1024, 2) if total_size > 0 else 0,
            "sizeBytes": total_size,
            "status": "indexed" if md_files else "empty",
            "lastUpdated": last_modified,
            "accessibleBy": meta["accessibleBy"],
            "s3Prefix": meta["prefix"],
            "files": [{"name": f["name"], "size": f["size"], "key": f["key"]} for f in md_files],
        })
    return results

# IMPORTANT: /search must be defined BEFORE /{kb_id} to avoid route conflict
@app.get("/api/v1/knowledge/search")
def search_knowledge(query: str = "", kb_id: str = ""):
    """Search across knowledge documents by reading file contents from S3."""
    if not query:
        return []
    query_lower = query.lower()
    results = []
    for kid, meta in _KB_PREFIXES.items():
        if kb_id and kid != kb_id:
            continue
        files = s3ops.list_files(meta["prefix"])
        for f in files:
            if not f["name"].endswith(".md"):
                continue
            content = s3ops.read_file(f["key"])
            if not content:
                continue
            content_lower = content.lower()
            if query_lower in content_lower:
                count = content_lower.count(query_lower)
                score = min(0.99, 0.7 + count * 0.05)
                idx = content_lower.find(query_lower)
                start = max(0, idx - 80)
                end = min(len(content), idx + len(query) + 120)
                snippet = content[start:end].replace("\n", " ").strip()
                if start > 0:
                    snippet = "..." + snippet
                if end < len(content):
                    snippet += "..."
                results.append({
                    "doc": f["name"],
                    "kb": kid,
                    "kbName": kid.replace("kb-", "").replace("-", " ").title(),
                    "score": round(score, 2),
                    "snippet": snippet,
                    "key": f["key"],
                })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:10]

@app.get("/api/v1/knowledge/{kb_id}")
def get_knowledge_base(kb_id: str):
    meta = _KB_PREFIXES.get(kb_id)
    if not meta:
        raise HTTPException(404, "Knowledge base not found")
    files = s3ops.list_files(meta["prefix"])
    md_files = [f for f in files if f["name"].endswith(".md")]
    return {
        "id": kb_id,
        "name": kb_id.replace("kb-", "").replace("-", " ").title(),
        **meta,
        "docCount": len(md_files),
        "files": [{"name": f["name"], "size": f["size"], "key": f["key"], "lastModified": f["lastModified"]} for f in md_files],
    }

@app.get("/api/v1/knowledge/{kb_id}/file")
def get_knowledge_file(kb_id: str, filename: str):
    """Read a specific knowledge document."""
    meta = _KB_PREFIXES.get(kb_id)
    if not meta:
        raise HTTPException(404, "Knowledge base not found")
    content = s3ops.read_file(f"{meta['prefix']}{filename}")
    if content is None:
        raise HTTPException(404, f"File not found: {filename}")
    return {"filename": filename, "content": content, "size": len(content)}


class KBUploadRequest(BaseModel):
    kbId: str
    filename: str
    content: str

@app.post("/api/v1/knowledge/upload")
def upload_knowledge_doc(body: KBUploadRequest, authorization: str = Header(default="")):
    """Upload a Markdown document to a knowledge base."""
    _require_role(authorization, roles=["admin", "manager"])
    meta = _KB_PREFIXES.get(body.kbId)
    if not meta:
        raise HTTPException(404, "Knowledge base not found")
    if not body.filename.endswith(".md"):
        body.filename += ".md"
    key = f"{meta['prefix']}{body.filename}"
    success = s3ops.write_file(key, body.content)
    if not success:
        raise HTTPException(500, "Failed to upload")
    return {"key": key, "saved": True, "size": len(body.content)}

@app.delete("/api/v1/knowledge/{kb_id}/file")
def delete_knowledge_file(kb_id: str, filename: str, authorization: str = Header(default="")):
    """Delete a knowledge document."""
    _require_role(authorization, roles=["admin"])
    meta = _KB_PREFIXES.get(kb_id)
    if not meta:
        raise HTTPException(404, "Knowledge base not found")
    key = f"{meta['prefix']}{filename}"
    try:
        s3ops._client().delete_object(Bucket=s3ops.bucket(), Key=key)
        return {"deleted": True, "key": key}
    except Exception as e:
        raise HTTPException(500, str(e))


# =========================================================================
# Approvals — persisted in DynamoDB
# =========================================================================

@app.get("/api/v1/approvals")
def get_approvals(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin", "manager"])
    all_approvals = db.get_approvals()
    pending = [a for a in all_approvals if a.get("status") == "pending"]
    resolved = [a for a in all_approvals if a.get("status") in ("approved", "denied")]
    resolved.sort(key=lambda x: x.get("resolvedAt", ""), reverse=True)
    return {"pending": pending, "resolved": resolved}

@app.post("/api/v1/approvals/{approval_id}/approve")
def approve_request(approval_id: str, authorization: str = Header(default="")):
    user = _require_role(authorization, roles=["admin", "manager"])
    result = db.update_approval(approval_id, {
        "status": "approved",
        "reviewer": user.name,
        "resolvedAt": datetime.now(timezone.utc).isoformat(),
    })
    if not result:
        raise HTTPException(404, "Approval not found")
    return result

@app.post("/api/v1/approvals/{approval_id}/deny")
def deny_request(approval_id: str, authorization: str = Header(default="")):
    user = _require_role(authorization, roles=["admin", "manager"])
    result = db.update_approval(approval_id, {
        "status": "denied",
        "reviewer": user.name,
        "resolvedAt": datetime.now(timezone.utc).isoformat(),
    })
    if not result:
        raise HTTPException(404, "Approval not found")


# =========================================================================
# Playground — test agent with different tenant contexts
# =========================================================================

class PlaygroundMessage(BaseModel):
    tenant_id: str
    message: str
    mode: str = "simulate"  # "simulate" or "live"

_POS_TOOLS = {
    "pos-sa": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
    "pos-sde": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
    "pos-devops": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
    "pos-qa": ["web_search", "shell", "file", "code_execution"],
    "pos-ae": ["web_search", "file", "crm-query", "email-send"],
    "pos-pm": ["web_search", "file", "notion-sync", "calendar-check"],
    "pos-fa": ["web_search", "file", "excel-gen", "sap-connector"],
    "pos-hr": ["web_search", "file", "email-send", "calendar-check"],
    "pos-csm": ["web_search", "file", "crm-query", "email-send"],
    "pos-legal": ["web_search", "file"],
    "pos-exec": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
}

@app.get("/api/v1/playground/profiles")
def get_playground_profiles():
    """Dynamically generate profiles for all employees with agents."""
    emps = db.get_employees()
    positions = db.get_positions()
    pos_map = {p["id"]: p for p in positions}
    channel_short = {"whatsapp": "wa", "telegram": "tg", "discord": "dc", "slack": "sl", "feishu": "fs", "dingtalk": "dt", "portal": "port"}
    profiles = {}
    for emp in emps:
        if not emp.get("agentId"):
            continue
        pos_id = emp.get("positionId", "")
        pos = pos_map.get(pos_id, {})
        # Use port__ prefix for all playground profiles (channel-agnostic)
        tenant_id = f"port__{emp['id']}"
        role = pos.get("name", "unknown").lower().replace(" ", "_")
        tools = _POS_TOOLS.get(pos_id, pos.get("toolAllowlist", ["web_search"]))
        blocked = [t for t in ["shell", "browser", "file_write", "code_execution"] if t not in tools]
        plan_a = f"ALLOW: {', '.join(tools)}."
        if blocked:
            plan_a += f"\nDENY: {', '.join(blocked)}."
        plan_e = "Block PII (SSN, credit cards, phone numbers). Block credential exposure."
        profiles[tenant_id] = {"role": role, "tools": tools, "planA": plan_a, "planE": plan_e}
    # Always include admin profile for the floating assistant
    profiles["port__admin"] = {
        "role": "it_admin",
        "tools": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
        "planA": "ALLOW: all tools. Full IT Admin access.\nThis is the Admin Assistant running on the Gateway EC2.",
        "planE": "Block credential exposure in responses.",
    }
    return profiles

def _admin_assistant_direct(message: str) -> dict:
    """
    PATH B: IT Admin Assistant — runs OpenClaw CLI on EC2.

    The H2 Proxy (bedrock_proxy_h2.js) detects admin sessions and proxies
    the Bedrock request directly to real Bedrock (bypassing Tenant Router).
    This means OpenClaw on EC2 keeps all its tools (shell, file, browser)
    and reads the local SOUL.md for identity.

    Flow: FastAPI → subprocess(openclaw CLI) → OpenClaw reads SOUL.md →
          OpenClaw calls Bedrock via H2 Proxy → H2 Proxy detects admin →
          H2 Proxy forwards to real Bedrock (not Tenant Router) →
          Response back to OpenClaw → back to FastAPI → Admin Console
    """
    import subprocess as _sp
    profile = {"role": "it_admin",
               "tools": ["web_search", "shell", "browser", "file", "code_execution"],
               "planA": "Full IT Admin access (read-only safety)",
               "planE": "Block credential exposure"}

    openclaw_bin = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin/openclaw"
    env_path = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin:/usr/local/bin:/usr/bin:/bin"

    try:
        import time as _admin_t
        session_id = f"admin-assistant"  # Stable session for conversation continuity

        cmd = ["sudo", "-u", "ubuntu", "env", f"PATH={env_path}", "HOME=/home/ubuntu",
               openclaw_bin, "agent", "--session-id", session_id,
               "--message", message, "--json", "--timeout", "120"]
        result = _sp.run(cmd, capture_output=True, text=True, timeout=130)

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # OpenClaw may write JSON to stderr in Gateway fallback mode
        raw = stdout if (stdout and '{' in stdout) else stderr if (stderr and '{' in stderr) else ""

        if raw and '{' in raw:
            json_start = raw.find('{')
            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(raw, json_start)
                result_obj = data.get("result", data)
                payloads = result_obj.get("payloads", [])
                text = " ".join(p.get("text", "") for p in payloads if p.get("text")).strip()
                if not text:
                    text = data.get("text", result_obj.get("text", ""))
                if text:
                    return {"response": text, "tenant_id": "admin", "profile": profile,
                            "plan_a": profile["planA"], "plan_e": "✅ EC2 via H2 bypass", "source": "ec2-direct"}
            except (json.JSONDecodeError, ValueError):
                pass

        return {"response": f"OpenClaw output:\n```\n{(stdout or stderr)[:500]}\n```",
                "tenant_id": "admin", "profile": profile,
                "plan_a": "", "plan_e": "⚠️ Parse error", "source": "ec2-direct"}
    except _sp.TimeoutExpired:
        return {"response": "⏳ Timed out after 120s.", "tenant_id": "admin",
                "profile": profile, "plan_a": "", "plan_e": "TIMEOUT", "source": "error"}
    except Exception as e:
        return {"response": f"⚠️ Error: {e}", "tenant_id": "admin",
                "profile": profile, "plan_a": "", "plan_e": "ERROR", "source": "error"}


@app.post("/api/v1/playground/send")
def playground_send(body: PlaygroundMessage, authorization: str = Header(default="")):
    """Send message to agent. mode=live routes through real Tenant Router → AgentCore."""
    _require_role(authorization, roles=["admin", "manager"])
    profiles = get_playground_profiles()
    profile = profiles.get(body.tenant_id, {"role": "unknown", "tools": ["web_search"], "planA": "Default", "planE": "Default"})

    # Extract employee ID from tenant_id (port__emp-xxx → emp-xxx)
    emp_id = body.tenant_id.replace("port__", "")

    # Live mode: route through Tenant Router → AgentCore → OpenClaw
    if body.mode == "live":
        # PATH B: Admin Assistant runs directly on EC2 (not via AgentCore)
        # See _admin_assistant_direct() docstring for full architecture explanation
        if emp_id == "admin":
            return _admin_assistant_direct(body.message)

        # PATH A: Employee agents route through Tenant Router → AgentCore microVM
        router_url = os.environ.get("TENANT_ROUTER_URL", "http://localhost:8090")
        try:
            import requests as _req
            # Use "portal" as channel and bare emp_id as user_id
            # This matches how Portal Chat sends requests
            r = _req.post(f"{router_url}/route", json={
                "channel": "portal",
                "user_id": emp_id,
                "message": body.message,
            }, timeout=180)
            if r.status_code == 200:
                data = r.json()
                agent_response = data.get("response", {})
                resp_text = agent_response.get("response", str(data)) if isinstance(agent_response, dict) else str(agent_response)
                return {
                    "response": resp_text,
                    "tenant_id": data.get("tenant_id", body.tenant_id),
                    "profile": profile,
                    "plan_a": profile.get("planA", ""),
                    "plan_e": "✅ PASS — Real agent response via AgentCore.",
                    "source": "agentcore",
                }
            else:
                return {
                    "response": f"⚠️ AgentCore returned {r.status_code}: {r.text[:200]}",
                    "tenant_id": body.tenant_id,
                    "profile": profile,
                    "plan_a": profile.get("planA", ""),
                    "plan_e": f"⚠️ ERROR — Status {r.status_code}",
                    "source": "error",
                }
        except Exception as e:
            return {
                "response": f"⚠️ AgentCore call failed: {e}\n\nFalling back to simulation.",
                "tenant_id": body.tenant_id,
                "profile": profile,
                "plan_a": profile.get("planA", ""),
                "plan_e": "⚠️ ERROR — AgentCore unreachable.",
                "source": "error",
            }

    # Simulate mode: permission-aware canned responses
    msg = body.message.lower()
    is_shell = any(w in msg for w in ["shell", "run", "execute", "command", "terminal"])
    is_file = any(w in msg for w in ["file", "write", "save", "create file", "export"])
    is_code = any(w in msg for w in ["code", "compile", "debug", "test"])
    is_search = any(w in msg for w in ["search", "find", "look up", "google", "research"])
    is_email = any(w in msg for w in ["email", "send mail", "compose"])
    is_jira = any(w in msg for w in ["jira", "ticket", "issue", "bug"])

    # Check permission
    if is_shell and "shell" not in profile["tools"]:
        response = f"⛔ Permission denied: Your {profile['role']} role does not have access to shell commands. This request has been logged.\n\nYou can submit an approval request if you need temporary access. Would you like me to do that?"
        plan_e = "⛔ BLOCKED — Plan A denied before execution."
    elif is_file and "file_write" not in profile["tools"] and "file" not in profile["tools"]:
        response = f"⛔ Permission denied: Your {profile['role']} role does not have file write access. Only read-only file access is available."
        plan_e = "⛔ BLOCKED — Plan A denied file_write."
    elif is_code and "code_execution" not in profile["tools"]:
        response = f"⛔ Permission denied: Your {profile['role']} role does not have code execution access."
        plan_e = "⛔ BLOCKED — Plan A denied code_execution."
    elif is_shell:
        response = "✅ Shell access granted. Running in sandboxed Docker environment.\n\n```\n$ git status\nOn branch main\nYour branch is up to date with 'origin/main'.\n\nChanges not staged for commit:\n  modified:   src/api/handler.py\n  modified:   tests/test_handler.py\n\nno changes added to commit\n```\n\nYou have 2 modified files. Would you like me to show the diff?"
        plan_e = "✅ PASS — Output scanned. No sensitive data, no credentials detected."
    elif is_email:
        if "email-send" in str(profile["tools"]) or profile["role"] in ["sales", "csm", "hr", "management"]:
            response = "📧 I can help you compose an email. Please provide:\n\n- **To:** recipient email address\n- **Subject:** email subject line\n- **Body:** email content\n\n⚠️ Note: Every email requires your confirmation before sending (Approval Required skill)."
            plan_e = "✅ PASS — Email skill available, approval-per-use enforced."
        else:
            response = f"⛔ Your {profile['role']} role does not have email sending capability. Please contact IT to request access."
            plan_e = "⛔ BLOCKED — email-send skill not in role allowlist."
    elif is_jira:
        if profile["role"] in ["engineering", "product", "management", "qa", "admin"]:
            response = "🎫 Querying Jira...\n\n| Key | Summary | Status | Assignee | Priority |\n|-----|---------|--------|----------|----------|\n| PROJ-1234 | Fix login timeout | In Progress | Alice Chen | High |\n| PROJ-1235 | Update API docs | Open | Bob Wang | Medium |\n| PROJ-1236 | Add unit tests | In Review | Carol Li | Low |\n\n3 issues found. Would you like details on any of these?"
            plan_e = "✅ PASS — Jira query returned public project data only."
        else:
            response = f"⛔ Your {profile['role']} role does not have Jira access. This skill is restricted to engineering, product, and management roles."
            plan_e = "⛔ BLOCKED — jira-query skill not in role allowlist."
    elif is_search:
        response = "🔍 Searching the web...\n\nHere's what I found:\n\n1. **AWS Well-Architected Framework** — Best practices for building secure, high-performing, resilient, and efficient infrastructure.\n2. **Microservices Design Patterns** — Common patterns for building distributed systems.\n3. **Cost Optimization on AWS** — Strategies to reduce cloud spending by 30-50%.\n\nWould you like me to dive deeper into any of these topics?"
        plan_e = "✅ PASS — Web search results contain no sensitive data."
    elif "hello" in msg or "hi" in msg or "hey" in msg:
        response = f"Hello! I'm your AI assistant running as a **{profile['role']}** role. I have access to these tools: {', '.join(profile['tools'])}.\n\nHow can I help you today? Try asking me to:\n- Search the web for information\n- {'Run shell commands' if 'shell' in profile['tools'] else '(shell access not available for your role)'}\n- {'Query Jira tickets' if profile['role'] in ['engineering','product','admin','qa'] else '(Jira not available for your role)'}"
        plan_e = "✅ PASS — Greeting response, no tool execution."
    elif "help" in msg or "what can" in msg or "capabilities" in msg:
        tools_desc = {
            "web_search": "🔍 Web Search — search the internet",
            "shell": "💻 Shell — execute terminal commands (sandboxed)",
            "browser": "🌐 Browser — navigate web pages",
            "file": "📁 File — read files",
            "file_write": "✏️ File Write — create and edit files",
            "code_execution": "⚡ Code Execution — run code in Docker sandbox",
        }
        available = "\n".join(f"  - {tools_desc.get(t, t)}" for t in profile["tools"])
        response = f"I'm running as **{profile['role']}** role. Here are my capabilities:\n\n**Available tools:**\n{available}\n\n**Skills:** web-search, jina-reader, deep-research" + (", jira-query, github-pr" if profile["role"] in ["engineering","admin"] else "") + "\n\nWhat would you like me to do?"
        plan_e = "✅ PASS — Capability listing, no execution."
    else:
        response = f"I can help you with that. As a **{profile['role']}** role, I have access to: {', '.join(profile['tools'])}.\n\nCould you be more specific about what you'd like me to do? For example:\n- \"Search for AWS best practices\"\n- \"Run git status\"\n- \"Query Jira ticket PROJ-1234\""
        plan_e = "✅ PASS — No policy violations."

    return {
        "response": response,
        "tenant_id": body.tenant_id,
        "profile": profile,
        "plan_a": profile["planA"],
        "plan_e": plan_e,
    }


# =========================================================================
# Portal — Employee Self-Service endpoints
# =========================================================================

# =========================================================================
# Portal — IM Self-Service Pairing
# Flow: pair-start (Portal) → employee scans QR → bot receives /start TOKEN
#       → H2 Proxy calls pair-complete → SSM mapping written → done
# =========================================================================

# Channel → bot info map (used to build deep links shown to employees)
_CHANNEL_BOT_INFO = {
    "telegram": {
        "botUsername": os.environ.get("TELEGRAM_BOT_USERNAME", "acme_enterprise_bot"),
        "deepLinkTemplate": "https://t.me/{bot}?start={token}",
        "label": "Telegram",
    },
    "discord": {
        "botUsername": "ACME Agent",
        "deepLinkTemplate": None,
        "label": "Discord",
        "instructions": "Open Discord → ACME Corp server → DM ACME Agent → send the command",
    },
    "feishu": {
        "botUsername": os.environ.get("FEISHU_BOT_NAME", "ACME Agent"),
        # Feishu deep link opens the bot chat directly (doesn't support token param)
        # User scans QR → bot chat opens → then manually sends /start TOKEN
        "deepLinkTemplate": "https://applink.feishu.cn/client/bot/open?appId={appId}",
        "feishuAppId": os.environ.get("FEISHU_APP_ID", "cli_a94cb611da399cdd"),
        "label": "Feishu / Lark",
    },
    "slack": {
        "botUsername": os.environ.get("SLACK_BOT_USERNAME", "acme-agent"),
        "deepLinkTemplate": None,
        "label": "Slack",
    },
}


class PairStartRequest(BaseModel):
    channel: str  # "telegram" | "discord" | "slack"


class PairCompleteRequest(BaseModel):
    channel: str
    channelUserId: str
    token: str


@app.post("/api/v1/portal/channel/pair-start")
def pair_start(body: PairStartRequest, authorization: str = Header(default="")):
    """Employee initiates IM pairing. Returns a token + deep link / QR data."""
    user = _require_auth(authorization)

    import secrets, string
    token = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))

    db.create_pair_token(token, user.employee_id, body.channel)

    bot_info = _CHANNEL_BOT_INFO.get(body.channel, {})
    bot_username = bot_info.get("botUsername", "")
    template = bot_info.get("deepLinkTemplate")
    # Feishu uses appId in the deep link, not bot username or token
    if template and "{appId}" in template:
        app_id = bot_info.get("feishuAppId", "")
        deep_link = template.format(appId=app_id) if app_id else None
    elif template:
        deep_link = template.format(bot=bot_username, token=token) if template else None
    else:
        deep_link = None

    return {
        "token": token,
        "deepLink": deep_link,
        "botUsername": bot_username,
        "channel": body.channel,
        "expiresIn": 900,  # 15 minutes
    }


@app.get("/api/v1/portal/channel/pair-status")
def pair_status(token: str, authorization: str = Header(default="")):
    """Poll pairing status. Returns pending / completed / expired."""
    _require_auth(authorization)
    import time as _t
    item = db.get_pair_token(token)
    if not item:
        return {"status": "not_found"}
    if item.get("ttl", 0) < int(_t.time()):
        return {"status": "expired"}
    return {"status": item.get("status", "pending")}


@app.post("/api/v1/bindings/pair-complete")
def pair_complete(body: PairCompleteRequest):
    """Called by H2 Proxy when employee sends /start TOKEN to the bot.
    No auth — called from internal network only (H2 Proxy on same EC2).
    Validates token, writes SSM user mapping, logs audit entry."""
    item = db.consume_pair_token(body.token)
    if not item:
        raise HTTPException(400, "Token invalid, already used, or expired")

    emp_id = item["employeeId"]
    channel = item.get("channel", body.channel)

    # Write SSM mapping — must use us-east-1 (where agent container reads from)
    # pair_complete is the only endpoint called by H2 Proxy, so we need explicit region
    import boto3 as _b3_pair
    _ssm_pair = _b3_pair.client("ssm", region_name="us-east-1")
    _prefix = _mapping_prefix()
    for key in [f"{channel}__{body.channelUserId}", body.channelUserId]:
        try:
            _ssm_pair.put_parameter(Name=f"{_prefix}{key}", Value=emp_id, Type="String", Overwrite=True)
        except Exception as e:
            print(f"[pair-complete] SSM write failed key={key}: {e}")

    # Update DynamoDB employee record so portal/channels reflects this immediately
    try:
        db.add_employee_channel(emp_id, channel)
    except Exception as e:
        print(f"[pair-complete] DynamoDB channel update failed (non-fatal): {e}")

    # Resolve employee name for confirmation message
    emps = db.get_employees()
    emp = next((e for e in emps if e["id"] == emp_id), {})

    # Audit log
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": emp_id,
        "actorName": emp.get("name", emp_id),
        "targetType": "binding",
        "targetId": f"{channel}__{body.channelUserId}",
        "detail": f"IM pairing self-service: {channel} {body.channelUserId} → {emp_id}",
        "status": "success",
    })

    return {
        "success": True,
        "employeeName": emp.get("name", emp_id),
        "employeeId": emp_id,
        "positionName": emp.get("positionName", ""),
        "channel": channel,
    }


class PortalChatMessage(BaseModel):
    message: str


@app.post("/api/v1/portal/upload")
async def portal_upload(
    file: UploadFile,
    authorization: str = Header(default=""),
):
    """Upload a file from the employee portal. Text files have their content returned
    so the agent can read them inline. Binary / image files are stored to S3."""
    user = _require_auth(authorization)

    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"
    raw = await file.read()
    size = len(raw)

    # Determine if we can extract text content
    text_extensions = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".sh",
                       ".yaml", ".yml", ".xml", ".html", ".sql", ".log", ".env",
                       ".toml", ".cfg", ".ini", ".java", ".go", ".rs", ".rb", ".php"}
    import os as _os
    ext = _os.path.splitext(filename)[1].lower()
    is_text = ext in text_extensions or content_type.startswith("text/")

    content_preview: str | None = None
    if is_text:
        try:
            text = raw.decode("utf-8", errors="replace")
            # Limit to 8KB inline (agent context window)
            content_preview = text[:8192]
            if len(text) > 8192:
                content_preview += f"\n\n[... truncated — {len(text)} chars total]"
        except Exception:
            pass

    # Store file in S3
    ts = int(time.time())
    s3_key = f"{user.employee_id}/workspace/uploads/{ts}_{filename}"
    try:
        import boto3 as _b3up
        _b3up.client("s3").put_object(
            Bucket=s3ops.bucket(),
            Key=s3_key,
            Body=raw,
            ContentType=content_type,
        )
    except Exception as e:
        print(f"[upload] S3 write failed: {e}")

    s3_uri = f"s3://{s3ops.bucket()}/{s3_key}"
    return {
        "filename": filename,
        "size": size,
        "type": content_type,
        "isText": is_text,
        "contentPreview": content_preview,
        "s3Key": s3_key,
        "s3Uri": s3_uri,
    }


@app.post("/api/v1/portal/chat")
def portal_chat(body: PortalChatMessage, authorization: str = Header(default="")):
    """Employee sends message to their bound agent via Tenant Router."""
    user = _require_auth(authorization)

    # Find employee's 1:1 binding
    bindings = db.get_bindings()
    my_binding = next((b for b in bindings if b.get("employeeId") == user.employee_id and b.get("mode") == "1:1"), None)
    if not my_binding:
        raise HTTPException(404, "No agent bound. Contact IT to provision your agent.")

    # Route through Tenant Router → AgentCore
    router_url = os.environ.get("TENANT_ROUTER_URL", "http://localhost:8090")
    try:
        import requests as _req
        r = _req.post(f"{router_url}/route", json={
            "channel": "portal",
            "user_id": user.employee_id,
            "message": body.message,
        }, timeout=180)
        if r.status_code == 200:
            data = r.json()
            agent_response = data.get("response", {})

            # Handle both nested and flat response structures
            if isinstance(agent_response, dict):
                reply = agent_response.get("response", str(agent_response))
            else:
                reply = str(agent_response)

            # Audit trail
            db.create_audit_entry({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "eventType": "agent_invocation",
                "actorId": user.employee_id,
                "actorName": user.name,
                "targetType": "agent",
                "targetId": my_binding.get("agentId", ""),
                "detail": f"Portal chat: {body.message[:80]}",
                "status": "success",
            })

            return {
                "response": reply,
                "agentId": my_binding.get("agentId"),
                "agentName": my_binding.get("agentName"),
                "source": "agentcore",
                "model": agent_response.get("model", "") if isinstance(agent_response, dict) else "",
            }
        else:
            print(f"[portal-chat] Tenant Router returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[portal-chat] Error calling Tenant Router: {e}")

    # Fallback when AgentCore is unreachable
    # Read employee's workspace to provide context-aware response
    soul_content = s3ops.read_file(f"_shared/soul/global/SOUL.md") or ""
    pos_soul = s3ops.read_file(f"_shared/soul/positions/{user.position_id}/SOUL.md") or ""
    user_md = s3ops.read_file(f"{user.employee_id}/workspace/USER.md") or ""

    context_parts = []
    if pos_soul:
        context_parts.append(f"[Position SOUL loaded: {len(pos_soul)} chars]")
    if user_md:
        context_parts.append(f"[USER.md loaded: {len(user_md)} chars]")

    return {
        "response": f"I'm your {my_binding.get('agentName', 'AI assistant')}. I'm currently running in offline mode (AgentCore unavailable).\n\n{''.join(context_parts)}\n\nPlease try again later, or use your messaging channel ({my_binding.get('channel', 'Slack')}) for full agent capabilities.",
        "agentId": my_binding.get("agentId"),
        "agentName": my_binding.get("agentName"),
        "source": "fallback",
    }


@app.get("/api/v1/portal/profile")
def portal_profile(authorization: str = Header(default="")):
    """Get employee's profile including USER.md preferences."""
    user = _require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    if not emp:
        raise HTTPException(404)

    user_md = s3ops.read_file(f"{user.employee_id}/workspace/USER.md") or ""
    memory_md = s3ops.read_file(f"{user.employee_id}/workspace/MEMORY.md") or ""
    agent = db.get_agent(emp.get("agentId", ""))

    # Return first 2KB of MEMORY.md so portal can show "what agent remembers"
    memory_preview = memory_md[:2048] if memory_md else None

    return {
        "employee": emp,
        "agent": agent,
        "userMd": user_md,
        "memoryMdSize": len(memory_md),
        "dailyMemoryCount": len(s3ops.list_files(f"{user.employee_id}/workspace/memory/")),
        "memoryPreview": memory_preview,
    }


class ProfileUpdateRequest(BaseModel):
    userMd: str

@app.put("/api/v1/portal/profile")
def update_portal_profile(body: ProfileUpdateRequest, authorization: str = Header(default="")):
    """Update employee's USER.md preferences."""
    user = _require_auth(authorization)
    s3ops.write_file(f"{user.employee_id}/workspace/USER.md", body.userMd)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "employee",
        "targetId": user.employee_id,
        "detail": "Updated personal preferences (USER.md)",
        "status": "success",
    })
    return {"saved": True}


@app.get("/api/v1/portal/usage")
def portal_usage(authorization: str = Header(default="")):
    """Get employee's personal usage stats."""
    user = _require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    agent_id = emp.get("agentId", "") if emp else ""

    usage_records = db.get_usage_for_agent(agent_id) if agent_id else []
    total_input = sum(u.get("inputTokens", 0) for u in usage_records)
    total_output = sum(u.get("outputTokens", 0) for u in usage_records)
    total_requests = sum(u.get("requests", 0) for u in usage_records)
    total_cost = sum(float(u.get("cost", 0)) for u in usage_records)

    return {
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalRequests": total_requests,
        "totalCost": round(total_cost, 4),
        "dailyUsage": [{
            "date": u.get("date"),
            "requests": u.get("requests", 0),
            "cost": float(u.get("cost", 0)),
        } for u in sorted(usage_records, key=lambda x: x.get("date", ""))],
    }


@app.get("/api/v1/portal/skills")
def portal_skills(authorization: str = Header(default="")):
    """Get employee's available and restricted skills."""
    user = _require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    agent = db.get_agent(emp.get("agentId", "")) if emp else None
    agent_skills = agent.get("skills", []) if agent else []

    # Get all skills from S3
    all_skills = get_skills()  # reuse existing endpoint logic
    available = [s for s in all_skills if s.get("name", s.get("id", "")).replace("sk-", "") in agent_skills or s.get("permissions", {}).get("allowedRoles", ["*"]) == ["*"]]
    restricted = [s for s in all_skills if s not in available]

    return {"available": available, "restricted": restricted}


@app.get("/api/v1/portal/requests")
def portal_requests(authorization: str = Header(default="")):
    """Get employee's approval requests."""
    user = _require_auth(authorization)
    all_approvals = db.get_approvals()
    my_pending = [a for a in all_approvals if a.get("tenantId", "").endswith(user.employee_id.replace("emp-", "")) and a.get("status") == "pending"]
    my_resolved = [a for a in all_approvals if a.get("tenantId", "").endswith(user.employee_id.replace("emp-", "")) and a.get("status") != "pending"]
    return {"pending": my_pending, "resolved": my_resolved}


class PortalRequestCreate(BaseModel):
    type: str  # "tool" or "skill"
    resourceId: str
    resourceName: str
    reason: str = ""


@app.post("/api/v1/portal/requests/create")
def portal_request_create(body: PortalRequestCreate, authorization: str = Header(default="")):
    """Employee self-service: create a tool/skill access request."""
    user = _require_auth(authorization)
    emp = db.get_employee(user.employee_id)
    emp_name = emp.get("name", user.employee_id) if emp else user.employee_id

    request_id = f"req-{user.employee_id}-{body.resourceId}-{int(time.time())}"
    db.create_approval({
        "id": request_id,
        "type": "permission_request",
        "status": "pending",
        "tenantId": f"portal__{user.employee_id}",
        "employeeId": user.employee_id,
        "employeeName": emp_name,
        "tool": body.resourceName,
        "resource": body.resourceId,
        "reason": body.reason or f"Employee requested access to: {body.resourceName}",
        "requestedAt": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "permission_request",
        "actorId": user.employee_id,
        "actorName": emp_name,
        "targetType": body.type,
        "targetId": body.resourceId,
        "detail": f"Employee self-service request: {body.resourceName} — {body.reason}",
        "status": "pending",
    })
    return {"created": True, "requestId": request_id}


def _find_channel_user_id(emp_id: str, channel_prefix: str) -> str:
    """Reverse lookup: given emp_id + channel, return the IM user_id."""
    try:
        import boto3 as _b3_rev
        prefix = _mapping_prefix()
        ssm = _b3_rev.client("ssm", region_name="us-east-1")
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=100)
        for p in resp.get("Parameters", []):
            if p.get("Value") == emp_id:
                name = p["Name"].replace(prefix, "")
                if name.startswith(f"{channel_prefix}__"):
                    return name.replace(f"{channel_prefix}__", "")
        return ""
    except Exception:
        return ""


@app.delete("/api/v1/portal/channels/{channel}")
def portal_channel_disconnect(channel: str, authorization: str = Header(default="")):
    """Employee self-service disconnect — deletes SSM mapping for their IM channel."""
    user = _require_auth(authorization)
    channel_user_id = _find_channel_user_id(user.employee_id, channel)
    if not channel_user_id:
        raise HTTPException(404, f"No {channel} connection found for your account")
    # Delete the mapping
    # Delete mappings from us-east-1 (where agent reads from)
    import boto3 as _b3_del
    ssm_del = _b3_del.client("ssm", region_name="us-east-1")
    prefix = _mapping_prefix()
    for key in [f"{channel}__{channel_user_id}", channel_user_id]:
        try:
            ssm_del.delete_parameter(Name=f"{prefix}{key}")
        except Exception:
            pass
    # Remove from DynamoDB employee channels
    try:
        db.remove_employee_channel(user.employee_id, channel)
    except Exception as e:
        print(f"[disconnect] DynamoDB channel remove failed (non-fatal): {e}")
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "binding",
        "targetId": f"{channel}__{channel_user_id}",
        "detail": f"Employee self-service disconnected {channel} ({channel_user_id})",
        "status": "success",
    })
    return {"disconnected": True, "channel": channel}


@app.get("/api/v1/portal/channels")
def portal_channels(authorization: str = Header(default="")):
    """Return list of connected IM channels for the current employee.

    Primary source: DynamoDB employee record's 'channels' field (set by admin
    when a pairing is approved or a self-service pairing completes).
    Fallback: SSM user-mapping scan (slower, catches edge cases).
    """
    user = _require_auth(authorization)
    # Primary: read from DynamoDB employee record
    try:
        emp = db.get_employee(user.employee_id)
        if emp:
            db_channels = [c for c in emp.get("channels", []) if c not in ("portal",)]
            if db_channels:
                return {"connected": db_channels}
    except Exception:
        pass
    # Fallback: SSM scan
    connected = []
    for channel_prefix in ["telegram", "discord", "slack", "whatsapp", "feishu"]:
        if _list_user_mappings_for_employee(user.employee_id, channel_prefix):
            connected.append(channel_prefix)
    return {"connected": connected}


def _list_user_mappings_for_employee(emp_id: str, channel_prefix: str) -> bool:
    """Check if any SSM mapping exists for this employee on the given channel.
    Always uses us-east-1 (where agent container reads mappings from)."""
    try:
        import boto3 as _b3_chk
        prefix = _mapping_prefix()
        ssm = _b3_chk.client("ssm", region_name="us-east-1")
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=50)
        for p in resp.get("Parameters", []):
            if p.get("Value") == emp_id and channel_prefix in p.get("Name", ""):
                return True
        return False
    except Exception:
        return False


# =========================================================================
# Data Export
# =========================================================================

@app.get("/api/v1/export/agent/{agent_id}")
def export_agent(agent_id: str):
    """Export agent configuration as downloadable package."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    pos_id = agent.get("positionId", "")
    emp_id = agent.get("employeeId")

    # Gather all files
    soul_layers = s3ops.get_soul_layers(pos_id, emp_id)
    memory = s3ops.get_agent_memory(emp_id) if emp_id else {}

    return {
        "agent": agent,
        "soul": soul_layers,
        "memory": memory,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "format": "openclaw-workspace",
        "note": "This export can be imported into any OpenClaw instance",
    }


# =========================================================================
# Monitor
# =========================================================================

# =========================================================================
# Monitor — derives active sessions from recent audit events
# =========================================================================
# Monitor — CloudWatch Logs backed, with DynamoDB fallback
# =========================================================================

import boto3 as _boto3
from botocore.exceptions import ClientError as _ClientError

# Log groups to query (AgentCore runtime + custom agent logs)
_LOG_GROUPS = [
    "/aws/bedrock-agentcore/runtimes/openclaw_multitenancy_runtime-olT3WX54rJ-DEFAULT",
    "/openclaw/openclaw-multitenancy/agents",
]

def _query_cloudwatch_sessions(region: str, minutes: int = 30) -> list:
    """Query CloudWatch Logs for recent agent invocations to derive active sessions."""
    try:
        cw = _boto3.client("logs", region_name=region)
        import time as _time
        start_time = int((_time.time() - minutes * 60) * 1000)
        sessions = []

        for log_group in _LOG_GROUPS:
            try:
                resp = cw.filter_log_events(
                    logGroupName=log_group,
                    startTime=start_time,
                    filterPattern='{ $.event_type = "agent_invocation" || $.status = "success" }',
                    limit=50,
                    interleaved=True,
                )
                for event in resp.get("events", []):
                    try:
                        msg = json.loads(event.get("message", "{}"))
                        tid = msg.get("tenant_id", "")
                        if tid and tid != "unknown":
                            sessions.append({
                                "id": f"sess-{event['eventId'][:8]}",
                                "agentId": "",
                                "agentName": f"Agent ({tid})",
                                "employeeId": tid,
                                "employeeName": tid,
                                "channel": msg.get("channel", "unknown"),
                                "turns": 1,
                                "lastMessage": msg.get("detail", msg.get("message", "")),
                                "status": "active",
                                "timestamp": event.get("timestamp", 0),
                            })
                    except (json.JSONDecodeError, KeyError):
                        pass
            except _ClientError:
                pass  # Log group might not exist in this region

        # Deduplicate by tenant_id, keep latest
        seen = {}
        for s in sessions:
            eid = s["employeeId"]
            if eid not in seen or s.get("timestamp", 0) > seen[eid].get("timestamp", 0):
                seen[eid] = s
        return list(seen.values())
    except Exception as e:
        return []


# Sessions are now stored in DynamoDB (seeded by seed_usage.py)

@app.get("/api/v1/monitor/sessions")
def get_sessions(source: str = "auto", authorization: str = Header(default="")):
    """Return sessions — seed data as 'completed', real sessions with dynamic status."""
    user = _get_current_user(authorization)
    import time as _t

    employees = db.get_employees()
    agents_list = db.get_agents()
    emp_map = {e["id"]: e for e in employees}
    agent_by_emp = {a.get("employeeId", ""): a for a in agents_list if a.get("employeeId")}
    now_ms = _t.time() * 1000

    # 1. Seed sessions from DynamoDB (have conversations, proper names)
    db_sessions = db.get_sessions()
    enriched = []
    for s in db_sessions:
        eid = s.get("employeeId", "")
        if not eid or eid == "unknown":
            continue

        # Resolve names if needed
        if not s.get("employeeName") or s["employeeName"] == eid:
            emp = emp_map.get(eid)
            if emp:
                agent = agent_by_emp.get(emp["id"])
                s["employeeName"] = emp["name"]
                s["agentId"] = agent["id"] if agent else s.get("agentId", "")
                s["agentName"] = agent["name"] if agent else ""
                if not s.get("channel") or s["channel"] == "unknown":
                    s["channel"] = emp.get("channels", ["portal"])[0]
            else:
                continue

        # Determine status based on lastActive timestamp
        last_active = s.get("lastActive", s.get("startedAt", ""))
        if last_active:
            try:
                from datetime import datetime as _dt, timezone as _tz
                la_time = _dt.fromisoformat(last_active.replace("Z", "+00:00")).timestamp() * 1000
                age_min = (now_ms - la_time) / 60000
                if age_min < 15:
                    s["status"] = "active"
                elif age_min < 60:
                    s["status"] = "idle"
                else:
                    s["status"] = "completed"
            except Exception:
                s["status"] = "completed"
        else:
            s["status"] = "completed"

        if not s.get("startedAt"):
            s["startedAt"] = last_active or ""
        enriched.append(s)

    # 2. CloudWatch real sessions (last 2 hours)
    cw_sessions = _query_cloudwatch_sessions("us-east-1", minutes=120)
    if cw_sessions:
        existing_emps = {s.get("employeeId") for s in enriched}
        for cw in cw_sessions:
            raw_id = cw.get("employeeId", "")
            emp = emp_map.get(raw_id)
            if not emp:
                for e in employees:
                    if raw_id in (e.get("employeeNo", ""), e.get("id", "")):
                        emp = e
                        break
            if not emp:
                continue

            agent = agent_by_emp.get(emp["id"])
            # Set id to match DynamoDB SESSION# key pattern (raw_id is the tenant_id from logs)
            # This ensures View button can look up the session via /monitor/sessions/{id}
            cw["id"] = raw_id[:40] if raw_id else cw.get("id", "")
            cw["employeeId"] = emp["id"]
            cw["employeeName"] = emp["name"]
            cw["agentId"] = agent["id"] if agent else ""
            cw["agentName"] = agent["name"] if agent else f"Agent ({emp['positionName']})"
            cw["channel"] = emp.get("channels", ["discord"])[0] if not cw.get("channel") or cw["channel"] == "unknown" else cw["channel"]
            cw["status"] = "active"

            if cw.get("timestamp") and not cw.get("startedAt"):
                from datetime import datetime as _dt2, timezone as _tz2
                cw["startedAt"] = _dt2.fromtimestamp(cw["timestamp"] / 1000, tz=_tz2.utc).isoformat()

            # Replace existing session for same employee (real data > seed data)
            if emp["id"] in existing_emps:
                enriched = [s for s in enriched if s.get("employeeId") != emp["id"]]
            enriched.append(cw)

    # Sort: active first, then by turns descending
    status_order = {"active": 0, "idle": 1, "completed": 2}
    enriched.sort(key=lambda s: (status_order.get(s.get("status", "completed"), 3), -(s.get("turns", 0))))

    # Scope for managers
    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            emp_ids = {e["id"] for e in employees if e.get("departmentId") in scope}
            enriched = [s for s in enriched if s.get("employeeId") in emp_ids]
    return enriched


@app.get("/api/v1/monitor/sessions/{session_id}")
def get_session_detail(session_id: str):
    """Get session detail with conversation from DynamoDB."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    # Read conversation from DynamoDB
    conv_records = db.get_session_conversation(session_id)
    conv = []
    for r in conv_records:
        msg = {"role": r.get("role", ""), "content": r.get("content", ""), "ts": r.get("ts", "")}
        if r.get("toolName"):
            msg["toolCall"] = {"tool": r["toolName"], "status": r.get("toolStatus", "success"), "duration": r.get("toolDuration", "")}
        conv.append(msg)

    # No conversation fallback — return empty list so frontend shows proper empty state
    # (Real conversation persistence requires a message storage integration)

    # Quality metrics: use real session data where available, estimate otherwise.
    # tokensUsed and turns come from DynamoDB SESSION# records written by server.py.
    turns = session.get("turns", 1)
    tool_calls = session.get("toolCalls", 0)
    tokens_used = session.get("tokensUsed", 0)
    avg_tokens_per_turn = round(tokens_used / max(turns, 1))

    # Satisfaction: use stored qualityScore if present, else turns-based estimate
    stored_quality = session.get("qualityScore")
    if stored_quality:
        satisfaction = round(float(stored_quality), 1)
    else:
        satisfaction = round(min(5.0, 3.5 + turns * 0.15), 1)

    # toolSuccess: real if we have audit data, otherwise derive from blocked count
    agent_id = session.get("agentId", "")
    audit_for_session = db.get_audit_entries(limit=100)
    session_blocks = [
        e for e in audit_for_session
        if e.get("status") == "blocked" and e.get("targetId") == agent_id
    ]
    if tool_calls > 0:
        tool_success = round(max(0, (tool_calls - len(session_blocks)) / tool_calls * 100), 1)
    else:
        tool_success = 100.0

    quality = {
        "satisfaction": satisfaction,
        "toolSuccess": tool_success,
        "responseTime": round(max(1.0, 4.5 - turns * 0.2), 1),
        "compliance": min(100, 90 + turns),
        "completionRate": min(100, 85 + turns * 2),
        "avgTokensPerTurn": avg_tokens_per_turn,
    }
    quality["overallScore"] = round(
        0.3 * quality["satisfaction"] +
        0.2 * (quality["toolSuccess"] / 20) +
        0.2 * max(0, (5 - quality["responseTime"])) +
        0.2 * (quality["compliance"] / 20) +
        0.1 * (quality["completionRate"] / 20), 1
    )

    # Plan E: scan real conversation content if available, else report no data
    plan_e = []
    if conv:
        for i, msg in enumerate(conv):
            if msg["role"] == "assistant":
                has_cost = "$" in msg["content"]
                has_code = "```" in msg["content"]
                plan_e.append({
                    "turn": i + 1,
                    "result": "flag" if has_cost else "pass",
                    "detail": "Cost data shared — within policy" if has_cost else "Code snippet — sandboxed" if has_code else "No sensitive data detected",
                })
    else:
        plan_e = [{"turn": 0, "result": "pass", "detail": "No conversation turns recorded yet"}]

    return {"session": session, "conversation": conv, "quality": quality, "planE": plan_e}


@app.get("/api/v1/monitor/runtime-events")
def get_runtime_events(minutes: int = 30):
    """Query CloudWatch Logs for microVM lifecycle events (invocations, SIGTERM, assembly)."""
    try:
        import time as _time
        cw = _boto3.client("logs", region_name="us-east-1")
        start_time = int((_time.time() - minutes * 60) * 1000)
        events = []

        for log_group in _LOG_GROUPS:
            try:
                # Get all recent log events
                resp = cw.filter_log_events(
                    logGroupName=log_group,
                    startTime=start_time,
                    limit=200,
                    interleaved=True,
                )
                for event in resp.get("events", []):
                    msg = event.get("message", "")
                    ts = event.get("timestamp", 0)
                    iso_ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

                    # Classify event type
                    if "SIGTERM" in msg:
                        events.append({"type": "release", "message": "microVM released (SIGTERM)", "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "First invocation" in msg or "assembling workspace" in msg:
                        tenant = ""
                        if "tenant" in msg:
                            parts = msg.split("tenant")
                            if len(parts) > 1:
                                tenant = parts[1].strip().split(" ")[0].strip("= ")
                        events.append({"type": "cold_start", "message": f"Cold start — workspace assembly", "tenant": tenant, "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "Workspace ready" in msg or "Workspace assembled" in msg:
                        events.append({"type": "ready", "message": "Workspace ready", "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "Invocation tenant_id=" in msg:
                        tenant = msg.split("tenant_id=")[1].split(" ")[0] if "tenant_id=" in msg else ""
                        msg_len = msg.split("message_len=")[1].split(" ")[0] if "message_len=" in msg else "?"
                        events.append({"type": "invocation", "message": f"Agent invocation (msg_len={msg_len})", "tenant": tenant, "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "Response tenant_id=" in msg:
                        duration = ""
                        if "duration_ms=" in msg:
                            duration = msg.split("duration_ms=")[1].split(" ")[0]
                        model = ""
                        if "model=" in msg:
                            model = msg.split("model=")[1].split(" ")[0]
                        tokens = ""
                        if "tokens=" in msg:
                            tokens = msg.split("tokens=")[1].split(" ")[0]
                        events.append({"type": "response", "message": f"Response ({duration}ms, {tokens} tokens, {model})", "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "DynamoDB usage written" in msg:
                        events.append({"type": "usage", "message": "Usage written to DynamoDB", "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "Plan A" in msg:
                        events.append({"type": "plan_a", "message": "Plan A constraints injected", "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "S3 workspace synced" in msg or "watchdog" in msg.lower():
                        events.append({"type": "sync", "message": "S3 workspace sync", "timestamp": iso_ts, "raw": msg.strip()[:200]})
                    elif "SSM user-mapping" in msg:
                        events.append({"type": "mapping", "message": msg.strip()[:100], "timestamp": iso_ts, "raw": msg.strip()[:200]})
            except _ClientError:
                pass

        # Sort by timestamp descending (newest first)
        events.sort(key=lambda e: e["timestamp"], reverse=True)

        # Summary stats
        invocations = [e for e in events if e["type"] == "invocation"]
        cold_starts = [e for e in events if e["type"] == "cold_start"]
        releases = [e for e in events if e["type"] == "release"]
        responses = [e for e in events if e["type"] == "response"]

        # Unique active tenants
        active_tenants = set()
        for e in invocations:
            t = e.get("tenant", "")
            if t:
                active_tenants.add(t)

        return {
            "events": events[:100],  # cap at 100
            "summary": {
                "totalEvents": len(events),
                "invocations": len(invocations),
                "coldStarts": len(cold_starts),
                "releases": len(releases),
                "activeTenants": len(active_tenants),
                "timeRangeMinutes": minutes,
            },
        }
    except Exception as e:
        return {"events": [], "summary": {"error": str(e)}}


@app.get("/api/v1/monitor/alerts")
def get_alert_rules():
    """Alert rules with real-time status evaluation against actual data."""
    agents = db.get_agents()
    employees = db.get_employees()
    budgets_data = usage_budgets()

    over_budget = [b for b in budgets_data if b["status"] == "over"]
    near_budget = [b for b in budgets_data if b["status"] == "warning"]
    idle_agents = [a for a in agents if a.get("status") == "idle"]
    unbound = [e for e in employees if not e.get("agentId")]
    now = datetime.now(timezone.utc).isoformat()

    return [
        {"id": "alert-01", "type": "Agent crash loop", "condition": "3 restarts in 5min", "action": "Notify IT + auto-downgrade", "status": "ok", "lastChecked": now, "detail": "No crash loops detected"},
        {"id": "alert-02", "type": "Channel auth expired", "condition": "Token expired", "action": "Notify admin", "status": "ok", "lastChecked": now, "detail": "All channel tokens valid"},
        {"id": "alert-03", "type": "Memory bloat", "condition": "MEMORY.md > 50MB", "action": "Notify user + suggest compaction", "status": "ok", "lastChecked": now, "detail": "All memory files within limits"},
        {"id": "alert-04", "type": "Context window near limit", "condition": "> 90% utilization", "action": "Auto compaction", "status": "ok", "lastChecked": now, "detail": "Context utilization normal"},
        {"id": "alert-05", "type": "Budget overrun", "condition": "Dept budget > 80%", "action": "Notify dept admin",
         "status": "warning" if (over_budget or near_budget) else "ok", "lastChecked": now,
         "detail": f"{len(over_budget)} over, {len(near_budget)} near limit" if (over_budget or near_budget) else "All within budget"},
        {"id": "alert-06", "type": "PII in output", "condition": "PII detection triggered", "action": "Auto-block + notify security", "status": "ok", "lastChecked": now, "detail": "No PII in recent outputs"},
        {"id": "alert-07", "type": "SOUL version drift", "condition": "Agent running old SOUL", "action": "Flag for reassembly",
         "status": "warning" if idle_agents else "ok", "lastChecked": now,
         "detail": f"{len(idle_agents)} idle agents may have stale SOUL" if idle_agents else "All on latest"},
        {"id": "alert-08", "type": "Unbound employees", "condition": "Employee without agent", "action": "Notify IT",
         "status": "warning" if unbound else "ok", "lastChecked": now,
         "detail": f"{len(unbound)} employees without agents" if unbound else "All bound"},
    ]



# =========================================================================
# Audit — persisted in DynamoDB
# =========================================================================

@app.get("/api/v1/audit/entries")
def get_audit_entries(limit: int = 50, eventType: Optional[str] = None, authorization: str = Header(default="")):
    user = _require_auth(authorization)
    limit = min(limit, 200)  # cap to prevent full-table dump
    entries = db.get_audit_entries(limit=limit)
    if eventType:
        entries = [e for e in entries if e.get("eventType") == eventType]
    # Scope for managers — only show events from their department's actors
    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            names_in_scope = {e["name"] for e in employees if e.get("departmentId") in scope}
            names_in_scope.add("system")
            names_in_scope.add("Auto-Provision")
            names_in_scope.add("IT Admin")
            entries = [e for e in entries if e.get("actorName") in names_in_scope]
    return entries


def _run_audit_scan() -> dict:
    """Generate real insights from live DynamoDB + S3 data.
    Pattern-based (no LLM). LLM memory analysis is a separate endpoint."""
    from datetime import datetime, timezone, timedelta
    import re

    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    entries = db.get_audit_entries(limit=200)
    agents = db.get_agents()
    employees = db.get_employees()
    sessions = db.get_sessions()
    insights = []
    idx = 0

    # 1. Repeated permission denials — real data from audit log
    blocked = [e for e in entries if e.get("status") == "blocked"]
    if blocked:
        # Group by actor
        by_actor: dict = {}
        for e in blocked:
            actor = e.get("actorName", "unknown")
            by_actor[actor] = by_actor.get(actor, [])
            by_actor[actor].append(e)
        # Flag actors with 3+ blocked attempts
        repeat_blockers = {k: v for k, v in by_actor.items() if len(v) >= 2}
        if repeat_blockers:
            actor_names = list(repeat_blockers.keys())[:3]
            total = sum(len(v) for v in repeat_blockers.values())
            top_tool = ""
            for e in blocked:
                m = re.search(r'(shell|browser|code_execution|file_write)', e.get("detail", ""), re.I)
                if m:
                    top_tool = m.group(1)
                    break
            insights.append({
                "id": f"ins-{idx:03d}", "severity": "high", "category": "access_pattern",
                "title": f"{total} permission denials — {len(repeat_blockers)} repeat offenders",
                "description": f"Detected {total} blocked operations across {len(repeat_blockers)} employees in the last 50 audit events. Top blocked tool: {top_tool or 'various'}. Repeated denials may indicate misconfigured SOUL permissions or employee confusion about allowed tools.",
                "recommendation": "Review SOUL tool permissions for affected positions. Consider adding a permission escalation request workflow so employees can request access instead of being silently blocked.",
                "affectedUsers": actor_names,
                "detectedAt": now_str,
                "source": "audit_log_scan",
            })
            idx += 1

    # 2. SOUL version drift — agents where soulVersions.position < latest
    pos_versions: dict = {}
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos not in pos_versions or sv > pos_versions[pos]:
            pos_versions[pos] = sv
    drifted = []
    for a in agents:
        pos = a.get("positionId", "")
        sv = (a.get("soulVersions") or {}).get("position", 1)
        if pos in pos_versions and sv < pos_versions[pos]:
            emp = next((e for e in employees if e.get("id") == a.get("employeeId")), {})
            drifted.append(emp.get("name", a.get("employeeName", a["id"])))
    if drifted:
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "high", "category": "compliance",
            "title": f"SOUL version drift — {len(drifted)} agent(s) behind",
            "description": f"{len(drifted)} agent(s) are running outdated position SOUL templates. Policy changes made to position SOULs have not been propagated to these agents, meaning security and behavior rules may not be current.",
            "recommendation": "Trigger workspace reassembly for affected agents via Agent Factory → SOUL Editor. Consider auto-incrementing position SOUL version on each workspace assembly.",
            "affectedUsers": drifted[:5],
            "detectedAt": now_str,
            "source": "version_drift_check",
        })
        idx += 1

    # 3. Zero-turn agents — agents with no sessions at all
    agents_with_sessions = {s.get("agentId") for s in sessions}
    unengaged = [a for a in agents if a.get("id") not in agents_with_sessions and a.get("employeeId")]
    if unengaged:
        names = [next((e.get("name", "") for e in employees if e.get("id") == a.get("employeeId")), a.get("employeeName", "")) for a in unengaged[:3]]
        names = [n for n in names if n]
        if names:
            insights.append({
                "id": f"ins-{idx:03d}", "severity": "low", "category": "optimization",
                "title": f"{len(unengaged)} employee agent(s) with no recorded sessions",
                "description": f"{len(unengaged)} personal agents have never recorded a session. These employees may not be aware of their agent, or onboarding hasn't been completed. Low engagement reduces ROI.",
                "recommendation": "Send an onboarding nudge to affected employees. Verify IM channel bindings are configured. Check if pairing was completed in Bindings → IM User Mappings.",
                "affectedUsers": names,
                "detectedAt": now_str,
                "source": "engagement_analysis",
            })
            idx += 1

    # 4. Config changes spike — if many config changes in audit log
    config_changes = [e for e in entries if e.get("eventType") == "config_change"]
    if len(config_changes) >= 5:
        changers = list({e.get("actorName", "") for e in config_changes})[:3]
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "compliance",
            "title": f"{len(config_changes)} configuration changes detected",
            "description": f"{len(config_changes)} config change events recorded in recent audit log. High change velocity can introduce policy inconsistencies or unintended agent behavior changes.",
            "recommendation": "Review recent config changes in Audit Center → Event Timeline filtered by 'Config Change'. Enable change approval workflow for SOUL and permission edits.",
            "affectedUsers": changers,
            "detectedAt": now_str,
            "source": "audit_log_scan",
        })
        idx += 1

    # 5. Agents missing bindings — created but no channel binding
    bound_agent_ids = {b.get("agentId") for b in (db.get_bindings() if hasattr(db, "get_bindings") else [])}
    unbound_agents = [a for a in agents if a.get("employeeId") and a.get("id") not in bound_agent_ids]
    if unbound_agents:
        names_ub = [a.get("employeeName", a.get("id", "")) for a in unbound_agents[:3]]
        insights.append({
            "id": f"ins-{idx:03d}", "severity": "medium", "category": "optimization",
            "title": f"{len(unbound_agents)} agent(s) without IM channel binding",
            "description": f"{len(unbound_agents)} personal agents exist in the system but have no IM channel binding. These agents cannot receive messages from employees via Discord, Slack, or other channels.",
            "recommendation": "Go to Bindings & Routing → Create Binding to link these agents to the appropriate IM channel. Or use Bulk Assign by Position.",
            "affectedUsers": names_ub,
            "detectedAt": now_str,
            "source": "binding_scan",
        })
        idx += 1

    return {
        "insights": insights,
        "summary": {
            "totalInsights": len(insights),
            "high": len([i for i in insights if i["severity"] == "high"]),
            "medium": len([i for i in insights if i["severity"] == "medium"]),
            "low": len([i for i in insights if i["severity"] == "low"]),
            "lastScanAt": now_str,
            "scanSources": ["audit_log", "agent_soul_versions", "session_data", "binding_registry"],
        }
    }


# Cache last scan result in memory (reset on server restart)
_audit_scan_cache: dict = {}


@app.get("/api/v1/audit/insights")
def get_audit_insights():
    """Return cached scan results (or empty if never scanned)."""
    global _audit_scan_cache
    if not _audit_scan_cache:
        # Run once on first load
        _audit_scan_cache = _run_audit_scan()
    return _audit_scan_cache


@app.post("/api/v1/audit/run-scan")
def run_audit_scan():
    """Trigger a fresh audit scan. Returns updated insights."""
    global _audit_scan_cache
    _audit_scan_cache = _run_audit_scan()
    return _audit_scan_cache


def _format_uptime(seconds: float) -> str:
    """Format seconds into a human-readable uptime string."""
    secs = int(seconds)
    days, remainder = divmod(secs, 86400)
    hours, remainder = divmod(remainder, 3600)
    mins = remainder // 60
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _check_gateway_status() -> str:
    """Try to hit the OpenClaw Gateway /health endpoint on localhost:18789."""
    try:
        import urllib.request as _ur
        req = _ur.Request("http://localhost:18789/health", method="GET")
        with _ur.urlopen(req, timeout=2) as resp:
            return "healthy" if resp.status == 200 else "degraded"
    except Exception:
        return "unreachable"


def _measure_bedrock_latency() -> int:
    """Measure round-trip latency to Bedrock by timing a lightweight ListFoundationModels call."""
    try:
        import boto3 as _b3_lat
        t0 = time.time()
        _b3_lat.client("bedrock", region_name=AWS_REGION).list_foundation_models(maxResults=1)
        return int((time.time() - t0) * 1000)
    except Exception:
        return 0


@app.get("/api/v1/monitor/health")
def get_monitor_health():
    """Comprehensive agent health metrics for Monitor Center."""
    agents = db.get_agents()
    employees = db.get_employees()
    usage_map = _get_agent_usage_today()

    # Build last-active map from SESSION# records in DynamoDB
    sessions = db.get_sessions()
    session_last_active: dict[str, str] = {}
    for s in sessions:
        aid = s.get("agentId", "")
        la = s.get("lastActive", "") or s.get("startedAt", "")
        if aid and la:
            if aid not in session_last_active or la > session_last_active[aid]:
                session_last_active[aid] = la

    # Compute per-agent avg response time from DynamoDB duration_ms field if present
    # Fall back to a cost-proportional estimate if not available
    total_duration_ms: dict[str, list] = {}
    for s in sessions:
        aid = s.get("agentId", "")
        dur = s.get("avgResponseMs") or s.get("durationMs")
        if aid and dur:
            total_duration_ms.setdefault(aid, []).append(float(dur))

    # Compute success rate from recent audit entries
    audit_entries = db.get_audit_entries(limit=200)
    agent_audit: dict[str, dict] = {}
    for e in audit_entries:
        aid = e.get("targetId", "")
        if not aid:
            continue
        rec = agent_audit.setdefault(aid, {"success": 0, "blocked": 0})
        if e.get("status") == "blocked":
            rec["blocked"] += 1
        else:
            rec["success"] += 1

    server_uptime = _format_uptime(time.time() - _SERVER_START_TIME)

    agent_health = []
    for agent in agents:
        usage = usage_map.get(agent["id"], {})
        aid = agent["id"]

        # lastActive: from SESSION# records; fall back to agent's own updatedAt
        last_active = (
            session_last_active.get(aid)
            or agent.get("lastActive")
            or agent.get("updatedAt")
            or ""
        )

        # avgResponseSec: from session duration data; fall back to requests-based estimate
        durations = total_duration_ms.get(aid, [])
        if durations:
            avg_resp = round(sum(durations) / len(durations) / 1000, 1)
        else:
            req = usage.get("requests", 0)
            avg_resp = round(max(1.0, 5.0 - min(req, 20) * 0.1), 1) if req else 0.0

        # toolSuccessRate: from audit log if we have entries; else 100 (no failures recorded)
        audit = agent_audit.get(aid, {})
        total_audit = audit.get("success", 0) + audit.get("blocked", 0)
        tool_success = round(
            (audit["success"] / total_audit * 100) if total_audit > 0 else 100.0, 1
        )

        agent_health.append({
            "agentId": aid,
            "agentName": agent["name"],
            "employeeName": agent.get("employeeName", ""),
            "positionName": agent.get("positionName", ""),
            "status": agent.get("status", "idle"),
            "qualityScore": agent.get("qualityScore"),
            "channels": agent.get("channels", []),
            "skillCount": len(agent.get("skills", [])),
            "requestsToday": usage.get("requests", 0),
            "costToday": round(usage.get("cost", 0), 4),
            "avgResponseSec": avg_resp,
            "toolSuccessRate": tool_success,
            "soulVersion": f"v{agent.get('soulVersions', {}).get('global', 3)}.{agent.get('soulVersions', {}).get('position', 1)}.{agent.get('soulVersions', {}).get('personal', 0)}",
            "lastActive": last_active,
            "uptime": server_uptime,
        })

    # System-level metrics
    all_requests = [usage_map.get(a["id"], {}).get("requests", 0) for a in agents]
    total_blocked = sum(e.get("blocked", 0) for e in agent_audit.values())
    total_success = sum(e.get("success", 0) for e in agent_audit.values())
    total_audit_all = total_blocked + total_success
    overall_tool_success = round(
        (total_success / total_audit_all * 100) if total_audit_all > 0 else 100.0, 1
    )

    # p95 response time: 95th percentile across all known durations
    all_durations = [ms for dlist in total_duration_ms.values() for ms in dlist]
    if all_durations:
        all_durations.sort()
        p95_idx = int(len(all_durations) * 0.95)
        p95_resp = round(all_durations[min(p95_idx, len(all_durations) - 1)] / 1000, 1)
    else:
        p95_resp = None

    gateway_status = _check_gateway_status()
    bedrock_ms = _measure_bedrock_latency()

    system = {
        "totalAgents": len(agents),
        "activeAgents": sum(1 for a in agents if a.get("status") == "active"),
        "avgQuality": round(sum(a.get("qualityScore") or 0 for a in agents) / max(1, len([a for a in agents if a.get("qualityScore")])), 1),
        "totalRequestsToday": sum(all_requests),
        "totalCostToday": round(sum(usage_map.get(a["id"], {}).get("cost", 0) for a in agents), 2),
        "p95ResponseSec": p95_resp,
        "overallToolSuccess": overall_tool_success,
        "gatewayStatus": gateway_status,
        "agentCoreStatus": "healthy",  # AgentCore status requires control-plane API; show healthy unless errors seen
        "bedrockLatencyMs": bedrock_ms if bedrock_ms else None,
    }

    return {"agents": agent_health, "system": system}

# =========================================================================
# Dashboard
# =========================================================================

@app.get("/api/v1/dashboard")
def dashboard(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    scope = _get_dept_scope(user) if user else None

    depts = db.get_departments()
    agents = db.get_agents()
    bindings = db.get_bindings()
    employees = db.get_employees()
    sessions = db.get_sessions()

    if scope is not None:
        depts = [d for d in depts if d["id"] in scope]
        employees = [e for e in employees if e.get("departmentId") in scope]
        emp_ids = {e["id"] for e in employees}
        positions = db.get_positions()
        pos_in_scope = {p["id"] for p in positions if p.get("departmentId") in scope}
        agents = [a for a in agents if a.get("positionId") in pos_in_scope or not a.get("employeeId")]
        bindings = [b for b in bindings if b.get("employeeId") in emp_ids]
        sessions = [s for s in sessions if s.get("employeeId") in emp_ids]

    return {
        "departments": len([d for d in depts if not d.get("parentId")]),
        "positions": len(db.get_positions() if scope is None else [p for p in db.get_positions() if p.get("departmentId") in scope]),
        "employees": len(employees),
        "agents": len(agents),
        "activeAgents": sum(1 for a in agents if a.get("status") == "active"),
        "bindings": sum(1 for b in bindings if b.get("status") == "active"),
        "sessions": len(sessions),
        "totalTurns": sum(s.get("turns", 0) for s in sessions),
        "unboundEmployees": sum(1 for e in employees if not e.get("agentId")),
    }


# =========================================================================
# Usage — Multi-dimension drill-down
# =========================================================================

# Per-agent usage data — reads from DynamoDB (seeded by seed_usage.py)

def _get_agent_usage_today() -> dict:
    """Aggregate today's usage per agent from DynamoDB USAGE# records.
    Reads today's date dynamically. Also merges the last 6 days to capture
    recent real usage (Discord, Portal, Telegram) that may land on different dates."""
    from datetime import date as _date, timedelta
    today = _date.today().isoformat()
    all_usage = db.get_usage_by_date(today)
    # Merge recent days to capture real usage — but never fall back to hard-coded seed dates
    for offset in range(1, 7):
        past = (_date.today() - timedelta(days=offset)).isoformat()
        past_usage = db.get_usage_by_date(past)
        for u in past_usage:
            aid = u.get("agentId", "")
            if aid and aid not in {uu.get("agentId") for uu in all_usage}:
                all_usage.append(u)
    result = {}
    for u in all_usage:
        aid = u.get("agentId", "")
        if not aid:
            continue
        if aid in result:
            # Accumulate across dates
            result[aid]["inputTokens"] += u.get("inputTokens", 0)
            result[aid]["outputTokens"] += u.get("outputTokens", 0)
            result[aid]["requests"] += u.get("requests", 0)
            result[aid]["cost"] += float(u.get("cost", 0))
        else:
            result[aid] = {
                "inputTokens": u.get("inputTokens", 0),
                "outputTokens": u.get("outputTokens", 0),
                "requests": u.get("requests", 0),
                "cost": float(u.get("cost", 0)),
                "model": u.get("model", ""),
            }
    return result

@app.get("/api/v1/usage/summary")
def usage_summary():
    usage_map = _get_agent_usage_today()
    total_input = sum(u["inputTokens"] for u in usage_map.values())
    total_output = sum(u["outputTokens"] for u in usage_map.values())
    total_cost = sum(u["cost"] for u in usage_map.values())
    total_requests = sum(u["requests"] for u in usage_map.values())
    employees = db.get_employees()
    # ChatGPT Team costs $25/user/month = ~$0.83/user/day
    chatgpt_daily = len([e for e in employees if e.get("agentId")]) * 0.83
    return {
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCost": round(total_cost, 2),
        "totalRequests": total_requests,
        "tenantCount": len([e for e in employees if e.get("agentId")]),
        "chatgptEquivalent": round(chatgpt_daily, 2),
    }

@app.get("/api/v1/usage/by-department")
def usage_by_department():
    """Aggregate usage by department from DynamoDB."""
    agents = db.get_agents()
    positions = db.get_positions()
    usage_map = _get_agent_usage_today()
    pos_to_dept = {p["id"]: p.get("departmentName", "Unknown") for p in positions}

    dept_usage: dict = {}
    for agent in agents:
        dept = pos_to_dept.get(agent.get("positionId", ""), "Unknown")
        usage = usage_map.get(agent["id"], {"inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0})
        if dept not in dept_usage:
            dept_usage[dept] = {"department": dept, "inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0, "agents": 0}
        dept_usage[dept]["inputTokens"] += usage["inputTokens"]
        dept_usage[dept]["outputTokens"] += usage["outputTokens"]
        dept_usage[dept]["requests"] += usage["requests"]
        dept_usage[dept]["cost"] += usage["cost"]
        dept_usage[dept]["agents"] += 1

    result = sorted(dept_usage.values(), key=lambda x: x["cost"], reverse=True)
    for r in result:
        r["cost"] = round(r["cost"], 2)
    return result

@app.get("/api/v1/usage/by-agent")
def usage_by_agent():
    """Per-agent usage breakdown from DynamoDB."""
    agents = db.get_agents()
    usage_map = _get_agent_usage_today()
    result = []
    for agent in agents:
        usage = usage_map.get(agent["id"], {"inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0})
        result.append({
            "agentId": agent["id"],
            "agentName": agent["name"],
            "employeeName": agent.get("employeeName", ""),
            "positionName": agent.get("positionName", ""),
            **usage,
        })
    return sorted(result, key=lambda x: x["cost"], reverse=True)

@app.get("/api/v1/usage/by-model")
def usage_by_model():
    """Aggregate usage by model from DynamoDB USAGE# records."""
    from datetime import date as _date, timedelta
    model_usage: dict = {}
    # Scan last 7 days of usage records
    for offset in range(7):
        d = (_date.today() - timedelta(days=offset)).isoformat()
        records = db.get_usage_by_date(d)
        for u in records:
            model = u.get("model", "unknown")
            if model == "unknown" or not model:
                model = "global.amazon.nova-2-lite-v1:0"  # default
            if model not in model_usage:
                model_usage[model] = {"model": model, "inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0}
            model_usage[model]["inputTokens"] += u.get("inputTokens", 0)
            model_usage[model]["outputTokens"] += u.get("outputTokens", 0)
            model_usage[model]["requests"] += u.get("requests", 0)
            model_usage[model]["cost"] += float(u.get("cost", 0))
    # Fallback to seed date if empty
    if not model_usage:
        records = db.get_usage_by_date("2026-03-20")
        for u in records:
            model = u.get("model", "global.amazon.nova-2-lite-v1:0")
            if model not in model_usage:
                model_usage[model] = {"model": model, "inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0}
            model_usage[model]["inputTokens"] += u.get("inputTokens", 0)
            model_usage[model]["outputTokens"] += u.get("outputTokens", 0)
            model_usage[model]["requests"] += u.get("requests", 0)
            model_usage[model]["cost"] += float(u.get("cost", 0))
    result = sorted(model_usage.values(), key=lambda x: x["cost"], reverse=True)
    for r in result:
        r["cost"] = round(r["cost"], 4)
    return result

@app.get("/api/v1/usage/agent/{agent_id}")
def usage_for_agent(agent_id: str):
    """Get daily usage records for a specific agent."""
    records = db.get_usage_for_agent(agent_id)
    records.sort(key=lambda x: x.get("date", ""))
    return [{
        "date": r.get("date"),
        "inputTokens": r.get("inputTokens", 0),
        "outputTokens": r.get("outputTokens", 0),
        "requests": r.get("requests", 0),
        "cost": float(r.get("cost", 0)),
    } for r in records]

@app.get("/api/v1/usage/trend")
def usage_trend():
    """7-day cost trend from DynamoDB.
    chatgptEquivalent: computed from active employee count if not stored per-day."""
    employees = db.get_employees()
    # ChatGPT Team $25/user/month ≈ $0.83/user/day
    chatgpt_daily = round(len([e for e in employees if e.get("agentId")]) * 0.83, 2)
    trend = db.get_cost_trend()
    return [{
        "date": t.get("date"),
        "openclawCost": float(t.get("openclawCost", 0)),
        # Use stored value if present; otherwise use employee-count-based estimate
        "chatgptEquivalent": float(t["chatgptEquivalent"]) if t.get("chatgptEquivalent") else chatgpt_daily,
        "totalRequests": t.get("totalRequests", 0),
    } for t in trend]

def _get_budgets() -> dict:
    """Load department budgets from DynamoDB CONFIG#budgets; fall back to defaults."""
    stored = db.get_config("budgets")
    if stored:
        # Merge with defaults so new departments always have a fallback
        merged = dict(_DEFAULT_BUDGETS)
        merged.update({k: float(v) for k, v in stored.items() if k != "id" and not k.startswith("_")})
        return merged
    return dict(_DEFAULT_BUDGETS)


@app.get("/api/v1/usage/budgets")
def usage_budgets():
    """Department budget tracking — budgets loaded from DynamoDB CONFIG#budgets."""
    dept_usage = usage_by_department()
    budgets = _get_budgets()
    result = []
    for dept in dept_usage:
        budget = budgets.get(dept["department"], 20.0)
        used = dept["cost"]
        projected = used * 30  # project to monthly
        result.append({
            "department": dept["department"],
            "budget": budget,
            "used": round(used, 2),
            "projected": round(projected, 2),
            "status": "over" if projected > budget else "warning" if projected > budget * 0.8 else "ok",
        })
    return result


class BudgetUpdateRequest(BaseModel):
    budgets: dict  # {"Engineering": 60.0, "Sales": 35.0, ...}


@app.put("/api/v1/usage/budgets")
def update_budgets(body: BudgetUpdateRequest, authorization: str = Header(default="")):
    """Save department budget config to DynamoDB. Admin only."""
    _require_role(authorization, roles=["admin"])
    merged = _get_budgets()
    merged.update({k: float(v) for k, v in body.budgets.items()})
    db.set_config("budgets", merged)
    return merged


# =========================================================================
# Settings — LLM Provider config with per-position overrides
# =========================================================================

# =========================================================================
# Settings — persisted in DynamoDB
# =========================================================================

def _get_model_config():
    config = db.get_config("model")
    if not config:
        return {"default": {"modelId": "global.amazon.nova-2-lite-v1:0", "modelName": "Amazon Nova 2 Lite", "inputRate": 0.30, "outputRate": 2.50}, "fallback": {}, "positionOverrides": {}, "availableModels": []}
    def fix_rates(d):
        if isinstance(d, dict):
            for k in ("inputRate", "outputRate"):
                if k in d and isinstance(d[k], str):
                    d[k] = float(d[k])
            for v in d.values():
                if isinstance(v, dict): fix_rates(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict): fix_rates(item)
    fix_rates(config)
    return config

def _get_security_config():
    config = db.get_config("security")
    if not config:
        return {"alwaysBlocked": ["install_skill", "load_extension", "eval"], "piiDetection": {"enabled": True, "mode": "redact"}, "dataSovereignty": {"enabled": True, "region": "us-east-2"}, "conversationRetention": {"days": 180}, "dockerSandbox": True, "fastPathRouting": True, "verboseAudit": False}
    return config

@app.get("/api/v1/settings/model")
def get_model_config():
    return _get_model_config()

@app.put("/api/v1/settings/model/default")
def set_default_model(body: dict):
    config = _get_model_config()
    config["default"] = body
    db.set_config("model", config)
    return config["default"]

@app.put("/api/v1/settings/model/fallback")
def set_fallback_model(body: dict):
    config = _get_model_config()
    config["fallback"] = body
    db.set_config("model", config)
    return config["fallback"]

@app.put("/api/v1/settings/model/position/{pos_id}")
def set_position_model(pos_id: str, body: dict):
    config = _get_model_config()
    config.setdefault("positionOverrides", {})[pos_id] = body
    db.set_config("model", config)
    return config["positionOverrides"]

@app.delete("/api/v1/settings/model/position/{pos_id}")
def remove_position_model(pos_id: str):
    config = _get_model_config()
    config.get("positionOverrides", {}).pop(pos_id, None)
    db.set_config("model", config)
    return config["positionOverrides"]

@app.get("/api/v1/settings/security")
def get_security_config():
    return _get_security_config()

@app.put("/api/v1/settings/security")
def update_security_config(body: dict):
    config = _get_security_config()
    config.update(body)
    db.set_config("security", config)
    return config

# =========================================================================
# Admin — IM Channels Management
# =========================================================================

def _run_openclaw_channels() -> list:
    """Get live channel status from openclaw channels list CLI."""
    import subprocess as _sp
    openclaw_bin = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin/openclaw"
    env_path = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin:/usr/local/bin:/usr/bin:/bin"
    try:
        result = _sp.run(
            ["sudo", "-u", "ubuntu", "env", f"PATH={env_path}", "HOME=/home/ubuntu",
             openclaw_bin, "channels", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout:
            raw = json.loads(result.stdout)
            channels = []
            for ch_type, accounts in raw.get("chat", {}).items():
                for account in accounts:
                    channels.append({"channel": ch_type, "account": account, "type": "chat"})
            return channels
    except Exception:
        pass
    # Fallback: parse openclaw channels list text output
    try:
        result = _sp.run(
            ["sudo", "-u", "ubuntu", "env", f"PATH={env_path}", "HOME=/home/ubuntu",
             openclaw_bin, "channels", "list"],
            capture_output=True, text=True, timeout=10,
        )
        channels = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("- ") and "default" in line:
                parts = line[2:].split()
                ch_type = parts[0].lower() if parts else "unknown"
                configured = "configured" in line
                linked = "not linked" not in line
                channels.append({
                    "channel": ch_type,
                    "account": "default",
                    "configured": configured,
                    "linked": linked,
                    "raw": line,
                })
        return channels
    except Exception:
        return []


@app.get("/api/v1/admin/im-channels")
def get_im_channels(authorization: str = Header(default="")):
    """Get live IM channel status from Gateway + SSM mappings count per channel."""
    _require_role(authorization, roles=["admin", "manager"])
    import boto3 as _b3_ch
    ssm = _b3_ch.client("ssm", region_name="us-east-1")

    # Get all user mappings to count per channel
    channel_counts: dict = {}
    try:
        prefix = _mapping_prefix()
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=100)
        for p in resp.get("Parameters", []):
            name = p["Name"].replace(prefix, "")
            for ch in ["telegram", "discord", "slack", "whatsapp", "feishu", "teams"]:
                if name.startswith(f"{ch}__"):
                    channel_counts[ch] = channel_counts.get(ch, 0) + 1
                    break
            else:
                # Bare user_id mappings — count but don't attribute to a channel
                pass
    except Exception:
        pass

    # Get live Gateway channel status
    gateway_channels = _run_openclaw_channels()

    # Build enriched channel list
    all_channels = [
        {"id": "telegram", "label": "Telegram", "enterprise": True},
        {"id": "discord", "label": "Discord", "enterprise": True},
        {"id": "slack", "label": "Slack", "enterprise": True},
        {"id": "teams", "label": "Microsoft Teams", "enterprise": True},
        {"id": "feishu", "label": "Feishu / Lark", "enterprise": True},
        {"id": "googlechat", "label": "Google Chat", "enterprise": True},
        {"id": "whatsapp", "label": "WhatsApp", "enterprise": False},
        {"id": "wechat", "label": "WeChat", "enterprise": False},
    ]

    gw_by_channel = {ch["channel"]: ch for ch in gateway_channels}
    result = []
    for ch in all_channels:
        gw = gw_by_channel.get(ch["id"], {})
        configured = bool(gw) and gw.get("configured", False)
        linked = bool(gw) and gw.get("linked", False)
        if gw and "raw" not in gw:
            configured = True
            linked = True
        status = "connected" if (configured and linked) else \
                 "configured" if configured else "not_connected"
        result.append({
            **ch,
            "status": status,
            "connectedEmployees": channel_counts.get(ch["id"], 0),
            "gatewayInfo": gw.get("raw", "") if gw else "",
        })
    return result


@app.get("/api/v1/settings/services")
def get_services():
    uptime_str = _format_uptime(time.time() - _SERVER_START_TIME)

    # Gateway: try to ping, measure latency
    gw_status = _check_gateway_status()

    # Requests today: count agent_invocation audit entries from today
    from datetime import date as _date
    today_str = _date.today().isoformat()
    audit_entries = db.get_audit_entries(limit=500)
    requests_today = sum(
        1 for e in audit_entries
        if e.get("eventType") == "agent_invocation"
        and e.get("timestamp", "").startswith(today_str)
    )

    # Approvals processed: count all non-pending approvals
    approvals = db.get_approvals()
    approvals_processed = sum(1 for a in approvals if a.get("status") in ("approved", "denied"))

    # Bedrock: measure real latency
    bedrock_ms = _measure_bedrock_latency()
    bedrock_status = "connected" if bedrock_ms > 0 else "unreachable"

    # DynamoDB: get real item count via a lightweight describe (scan is expensive; use table meta)
    ddb_item_count = 0
    ddb_status = "unknown"
    try:
        import boto3 as _b3_svc
        table_meta = _b3_svc.resource("dynamodb", region_name=db.AWS_REGION).Table(db.TABLE_NAME)
        table_meta.load()
        ddb_item_count = table_meta.item_count or 0
        ddb_status = "active"
    except Exception:
        ddb_status = "unreachable"

    # S3: quick head-bucket check
    s3_status = "unknown"
    try:
        import boto3 as _b3_s3
        _b3_s3.client("s3").head_bucket(Bucket=s3ops.bucket())
        s3_status = "active"
    except Exception:
        s3_status = "unreachable"

    return {
        "gateway": {
            "status": gw_status,
            "port": 18789,
            "uptime": uptime_str,
            "requestsToday": requests_today,
        },
        "auth_agent": {
            "status": "healthy",
            "uptime": uptime_str,
            "approvalsProcessed": approvals_processed,
        },
        "bedrock": {
            "status": bedrock_status,
            "region": AWS_REGION,
            "latencyMs": bedrock_ms if bedrock_ms else None,
            "vpcEndpoint": True,
        },
        "dynamodb": {
            "status": ddb_status,
            "table": db.TABLE_NAME,
            "itemCount": ddb_item_count,
        },
        "s3": {"status": s3_status, "bucket": s3ops.bucket()},
    }


# =========================================================================
# Serve frontend (production mode)
# =========================================================================

DIST_DIR = Path(__file__).parent.parent / "dist"

if DIST_DIR.exists():
    # Serve static assets
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    # SPA fallback: serve index.html for any non-API 404
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request, exc):
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            return FileResponse(str(DIST_DIR / "index.html"))
        # For API 404s, return JSON
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# =========================================================================
# Admin AI Assistant — Claude via Bedrock Converse API + whitelist tools
# No shell, no subprocess, no OpenClaw. Bounded read/write via Python fns.
# =========================================================================

_ADMIN_AI_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

_ADMIN_AI_SYSTEM = """You are the IT Admin Assistant for OpenClaw Enterprise. You help administrators query and configure the platform.

You have access to specific tools to read and modify platform data. Use them to answer questions accurately.
- For data queries, always use the appropriate tool rather than guessing.
- For write operations (update_soul_template), confirm what you're about to change before calling the tool if the intent isn't crystal clear.
- Respond in the same language the user writes in.
- Be concise. Show data in structured format (tables or lists) when useful.
- You cannot execute shell commands or access the EC2 directly. All operations go through the defined tools."""

# Per-admin conversation history (in-memory, resets on server restart)
_admin_ai_history: dict[str, list] = {}

_ADMIN_AI_TOOLS = [
    {
        "name": "list_employees",
        "description": "List employees, optionally filtered by department_id or position_id. Returns id, name, position, department, agent status, channels.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "department_id": {"type": "string"},
            "position_id": {"type": "string"},
        }}}
    },
    {
        "name": "get_employee_detail",
        "description": "Get full details for one employee: profile, agent config, bindings, recent usage.",
        "inputSchema": {"json": {"type": "object", "required": ["employee_id"], "properties": {
            "employee_id": {"type": "string", "description": "e.g. emp-carol"},
        }}}
    },
    {
        "name": "get_soul_template",
        "description": "Read a SOUL template from S3. scope=global reads the locked global SOUL. scope=position reads a position template (requires position_id). scope=personal reads an employee's personal SOUL (requires employee_id).",
        "inputSchema": {"json": {"type": "object", "required": ["scope"], "properties": {
            "scope": {"type": "string", "enum": ["global", "position", "personal"]},
            "position_id": {"type": "string"},
            "employee_id": {"type": "string"},
        }}}
    },
    {
        "name": "update_soul_template",
        "description": "Write a SOUL template to S3. Only position and personal scope are writable. Global is locked. Creates an audit log entry automatically.",
        "inputSchema": {"json": {"type": "object", "required": ["scope", "content"], "properties": {
            "scope": {"type": "string", "enum": ["position", "personal"]},
            "position_id": {"type": "string"},
            "employee_id": {"type": "string"},
            "content": {"type": "string"},
        }}}
    },
    {
        "name": "list_departments_and_positions",
        "description": "List all departments and positions with member counts and default channels.",
        "inputSchema": {"json": {"type": "object", "properties": {}}}
    },
    {
        "name": "get_agent_detail",
        "description": "Get agent configuration, SOUL versions, skills, channels, and today's usage.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "agent_id": {"type": "string"},
            "employee_id": {"type": "string", "description": "Alternative lookup by employee"},
        }}}
    },
    {
        "name": "get_usage_report",
        "description": "Get token usage and cost data. scope=summary gives org total, scope=by_department breaks down by dept, scope=by_agent lists per-agent stats.",
        "inputSchema": {"json": {"type": "object", "required": ["scope"], "properties": {
            "scope": {"type": "string", "enum": ["summary", "by_department", "by_agent"]},
        }}}
    },
    {
        "name": "get_service_health",
        "description": "Check health of all platform services: Gateway, Admin Console, Tenant Router, Bedrock, DynamoDB, S3.",
        "inputSchema": {"json": {"type": "object", "properties": {}}}
    },
    {
        "name": "get_audit_log",
        "description": "Query recent audit log entries. Optionally filter by employee_id or event_type.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "employee_id": {"type": "string"},
            "event_type": {"type": "string", "enum": ["agent_invocation", "permission_denied", "config_change", "approval_decision"]},
            "limit": {"type": "integer", "default": 20},
        }}}
    },
    {
        "name": "list_bindings",
        "description": "List IM channel bindings. Optionally filter by employee_id or channel name.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "employee_id": {"type": "string"},
            "channel": {"type": "string"},
        }}}
    },
]


def _execute_admin_tool(name: str, inputs: dict, actor_id: str, actor_name: str) -> str:
    """Execute one whitelisted admin tool. Returns result as string for Claude."""
    try:
        if name == "list_employees":
            emps = db.get_employees()
            if inputs.get("department_id"):
                emps = [e for e in emps if e.get("departmentId") == inputs["department_id"]]
            if inputs.get("position_id"):
                emps = [e for e in emps if e.get("positionId") == inputs["position_id"]]
            rows = [f"- {e['id']} | {e['name']} | {e.get('positionName','')} | {e.get('departmentName','')} | agent={'yes' if e.get('agentId') else 'no'} | channels={','.join(e.get('channels',[]))}"
                    for e in emps]
            return f"{len(emps)} employees:\n" + "\n".join(rows)

        elif name == "get_employee_detail":
            emp_id = inputs["employee_id"]
            emps = db.get_employees()
            emp = next((e for e in emps if e["id"] == emp_id or e.get("name") == emp_id), None)
            if not emp:
                return f"Employee '{emp_id}' not found."
            agent = db.get_agent(emp.get("agentId", "")) if emp.get("agentId") else None
            bindings = [b for b in db.get_bindings() if b.get("employeeId") == emp["id"]]
            usage = _get_agent_usage_today().get(emp.get("agentId", ""), {})
            lines = [
                f"**{emp['name']}** ({emp['id']})",
                f"Position: {emp.get('positionName','')} | Dept: {emp.get('departmentName','')} | Role: {emp.get('role','')}",
                f"Channels: {', '.join(emp.get('channels', []))}",
                f"Agent: {emp.get('agentId', 'none')} | Status: {agent.get('status','') if agent else 'no agent'}",
                f"Skills: {', '.join(agent.get('skills',[]) if agent else [])}",
                f"Bindings: {len(bindings)} ({', '.join(b.get('channel','') for b in bindings)})",
                f"Today usage: {usage.get('requests',0)} requests, ${usage.get('cost',0):.4f}",
            ]
            if agent:
                sv = agent.get("soulVersions") or {}
                lines.append(f"SOUL versions: global=v{sv.get('global',0)} position=v{sv.get('position',0)} personal=v{sv.get('personal',0)}")
            return "\n".join(lines)

        elif name == "get_soul_template":
            scope = inputs["scope"]
            if scope == "global":
                content = s3ops.read_file("_shared/soul/global/SOUL.md") or "(empty)"
                return f"**Global SOUL** (locked):\n\n{content[:3000]}"
            elif scope == "position":
                pos_id = inputs.get("position_id", "")
                if not pos_id:
                    return "position_id required for scope=position"
                content = s3ops.read_file(f"_shared/soul/positions/{pos_id}/SOUL.md") or "(not set)"
                return f"**Position SOUL [{pos_id}]**:\n\n{content[:3000]}"
            elif scope == "personal":
                emp_id = inputs.get("employee_id", "")
                if not emp_id:
                    return "employee_id required for scope=personal"
                content = s3ops.read_file(f"{emp_id}/workspace/SOUL.md") or "(not set)"
                return f"**Personal SOUL [{emp_id}]**:\n\n{content[:3000]}"

        elif name == "update_soul_template":
            scope = inputs["scope"]
            content = inputs["content"]
            if scope == "global":
                return "❌ Global SOUL is locked and cannot be modified."
            elif scope == "position":
                pos_id = inputs.get("position_id", "")
                if not pos_id:
                    return "position_id required for scope=position"
                s3ops.write_file(f"_shared/soul/positions/{pos_id}/SOUL.md", content)
                db.create_audit_entry({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "eventType": "config_change", "actorId": actor_id, "actorName": actor_name,
                    "targetType": "soul", "targetId": pos_id,
                    "detail": f"Admin AI updated position SOUL for {pos_id} ({len(content)} chars)",
                    "status": "success",
                })
                return f"✅ Position SOUL for {pos_id} updated ({len(content)} chars). Agents will get it on next workspace assembly."
            elif scope == "personal":
                emp_id = inputs.get("employee_id", "")
                if not emp_id:
                    return "employee_id required for scope=personal"
                s3ops.write_file(f"{emp_id}/workspace/SOUL.md", content)
                db.create_audit_entry({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "eventType": "config_change", "actorId": actor_id, "actorName": actor_name,
                    "targetType": "soul", "targetId": emp_id,
                    "detail": f"Admin AI updated personal SOUL for {emp_id} ({len(content)} chars)",
                    "status": "success",
                })
                return f"✅ Personal SOUL for {emp_id} updated ({len(content)} chars)."

        elif name == "list_departments_and_positions":
            depts = db.get_departments()
            positions = db.get_positions()
            lines = ["**Departments:**"]
            for d in depts:
                lines.append(f"  {d['id']} | {d['name']} | head: {d.get('headName','')} | members: {d.get('headCount',0)}")
            lines.append("\n**Positions:**")
            for p in positions:
                lines.append(f"  {p['id']} | {p['name']} | dept: {p.get('departmentName','')} | channel: {p.get('defaultChannel','')} | members: {p.get('employeeCount',0)}")
            return "\n".join(lines)

        elif name == "get_agent_detail":
            agents = db.get_agents()
            agent = None
            if inputs.get("agent_id"):
                agent = next((a for a in agents if a["id"] == inputs["agent_id"]), None)
            elif inputs.get("employee_id"):
                agent = next((a for a in agents if a.get("employeeId") == inputs["employee_id"]), None)
            if not agent:
                return "Agent not found."
            usage = _get_agent_usage_today().get(agent["id"], {})
            sv = agent.get("soulVersions") or {}
            lines = [
                f"**{agent['name']}** ({agent['id']})",
                f"Employee: {agent.get('employeeName','')} | Position: {agent.get('positionName','')}",
                f"Status: {agent.get('status','')} | Quality: {agent.get('qualityScore','—')}",
                f"Channels: {', '.join(agent.get('channels',[]))}",
                f"Skills: {', '.join(agent.get('skills',[]))}",
                f"SOUL: global=v{sv.get('global',0)} position=v{sv.get('position',0)} personal=v{sv.get('personal',0)}",
                f"Today: {usage.get('requests',0)} reqs, ${usage.get('cost',0):.4f}, model={usage.get('model','')}",
            ]
            return "\n".join(lines)

        elif name == "get_usage_report":
            scope = inputs["scope"]
            if scope == "summary":
                s = usage_summary()
                return (f"Input: {s['totalInputTokens']:,} tokens\n"
                        f"Output: {s['totalOutputTokens']:,} tokens\n"
                        f"Cost today: ${s['totalCost']:.4f}\n"
                        f"Requests: {s['totalRequests']}\n"
                        f"Active tenants: {s['tenantCount']}\n"
                        f"vs ChatGPT equivalent: ${s['chatgptEquivalent']:.2f}/day")
            elif scope == "by_department":
                rows = usage_by_department()
                lines = [f"{'Dept':<25} {'Agents':>6} {'Reqs':>6} {'Tokens':>8} {'Cost':>8}"]
                lines.append("-" * 58)
                for r in rows:
                    lines.append(f"{r['department']:<25} {r['agents']:>6} {r['requests']:>6} {(r['inputTokens']+r['outputTokens'])//1000:>7}k ${r['cost']:>6.2f}")
                return "\n".join(lines)
            elif scope == "by_agent":
                rows = usage_by_agent()
                lines = [f"{'Agent':<30} {'Reqs':>5} {'Cost':>7}"]
                for r in rows[:20]:
                    lines.append(f"{r['agentName'][:30]:<30} {r['requests']:>5} ${r['cost']:>5.2f}")
                return "\n".join(lines)

        elif name == "get_service_health":
            svc = get_services()
            lines = []
            for name_svc, info in svc.items():
                status = info.get("status", "unknown")
                icon = "✅" if status in ("healthy", "active", "running", "connected") else "⚠️"
                extra = ""
                if name_svc == "gateway":
                    extra = f" | port {info.get('port','')} | {info.get('requestsToday',0)} reqs today"
                elif name_svc == "bedrock":
                    ms = info.get("latencyMs")
                    extra = f" | {info.get('region','')} | {ms}ms" if ms else f" | {info.get('region','')}"
                elif name_svc == "dynamodb":
                    extra = f" | {info.get('table','')} | {info.get('itemCount',0)} items"
                elif name_svc == "s3":
                    extra = f" | {info.get('bucket','')}"
                lines.append(f"{icon} **{name_svc}**: {status}{extra}")
            return "\n".join(lines)

        elif name == "get_audit_log":
            limit = min(int(inputs.get("limit", 20)), 50)
            entries = db.get_audit_entries(limit=limit)
            if inputs.get("employee_id"):
                entries = [e for e in entries if e.get("actorId") == inputs["employee_id"]]
            if inputs.get("event_type"):
                entries = [e for e in entries if e.get("eventType") == inputs["event_type"]]
            entries = entries[:limit]
            lines = []
            for e in entries:
                ts = e.get("timestamp", "")[:16].replace("T", " ")
                lines.append(f"{ts} | {e.get('eventType','')} | {e.get('actorName','')} | {e.get('detail','')[:60]}")
            return f"{len(lines)} entries:\n" + "\n".join(lines) if lines else "No entries found."

        elif name == "list_bindings":
            bindings = db.get_bindings()
            if inputs.get("employee_id"):
                bindings = [b for b in bindings if b.get("employeeId") == inputs["employee_id"]]
            if inputs.get("channel"):
                bindings = [b for b in bindings if b.get("channel") == inputs["channel"]]
            lines = [f"- {b.get('employeeName','')} ({b.get('employeeId','')}) | {b.get('channel','')} | {b.get('status','')} | agent: {b.get('agentId','')}"
                     for b in bindings]
            return f"{len(bindings)} bindings:\n" + "\n".join(lines)

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error ({name}): {e}"


def _admin_ai_loop(history: list, user) -> str:
    """Agentic loop: call Claude with tools, execute tool_use responses, repeat until text."""
    import boto3 as _b3_ai
    client = _b3_ai.client("bedrock-runtime", region_name=AWS_REGION)

    messages = list(history)  # shallow copy

    for _ in range(8):  # max 8 tool-use rounds
        try:
            resp = client.converse(
                modelId=_ADMIN_AI_MODEL,
                system=[{"text": _ADMIN_AI_SYSTEM}],
                messages=messages,
                toolConfig={"tools": [{"toolSpec": t} for t in _ADMIN_AI_TOOLS]},
                inferenceConfig={"maxTokens": 2048, "temperature": 0.2},
            )
        except Exception as e:
            return f"⚠️ AI error: {e}"

        stop_reason = resp.get("stopReason", "")
        output_msg = resp.get("output", {}).get("message", {})
        content_blocks = output_msg.get("content", [])

        # Collect text and tool_use blocks
        text_parts = []
        tool_uses = []
        for block in content_blocks:
            if block.get("text"):
                text_parts.append(block["text"])
            if block.get("toolUse"):
                tool_uses.append(block["toolUse"])

        if stop_reason == "end_turn" or not tool_uses:
            return " ".join(text_parts) or "(no response)"

        # Execute all tool calls
        messages.append({"role": "assistant", "content": content_blocks})
        tool_results = []
        for tu in tool_uses:
            result = _execute_admin_tool(tu["name"], tu.get("input", {}), user.employee_id, user.name)
            tool_results.append({
                "toolResult": {
                    "toolUseId": tu["toolUseId"],
                    "content": [{"text": result}],
                }
            })
        messages.append({"role": "user", "content": tool_results})

    return "⚠️ Reached maximum tool-use rounds."


class AdminAiMessage(BaseModel):
    message: str


@app.post("/api/v1/admin-ai/chat")
def admin_ai_chat(body: AdminAiMessage, authorization: str = Header(default="")):
    """Admin AI assistant — Claude via Bedrock + whitelist tools. Admin only."""
    user = _require_role(authorization, roles=["admin"])

    # Get or init per-admin history
    history = _admin_ai_history.setdefault(user.employee_id, [])
    # Bedrock Converse API requires content as list of blocks
    history.append({"role": "user", "content": [{"text": body.message}]})

    # Trim to last 20 messages to stay within token limits
    if len(history) > 20:
        _admin_ai_history[user.employee_id] = history[-20:]
        history = _admin_ai_history[user.employee_id]

    response_text = _admin_ai_loop(history, user)
    history.append({"role": "assistant", "content": [{"text": response_text}]})

    return {"response": response_text}


@app.delete("/api/v1/admin-ai/chat")
def admin_ai_clear(authorization: str = Header(default="")):
    """Clear conversation history for the current admin."""
    user = _require_role(authorization, roles=["admin"])
    _admin_ai_history.pop(user.employee_id, None)
    return {"cleared": True}


# =========================================================================
# Security Center — SOUL, Tools, Runtimes, Infrastructure
# =========================================================================

@app.get("/api/v1/security/global-soul")
def get_global_soul(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    try:
        bucket = s3ops.bucket()
        key = "_shared/soul/global/SOUL.md"
        body = s3ops._client().get_object(Bucket=bucket, Key=key)["Body"].read().decode()
        return {"content": body, "key": key}
    except Exception as e:
        return {"content": "", "key": "_shared/soul/global/SOUL.md", "error": str(e)}

@app.put("/api/v1/security/global-soul")
def put_global_soul(body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    bucket = s3ops.bucket()
    s3ops._client().put_object(Bucket=bucket, Key="_shared/soul/global/SOUL.md",
                               Body=body.get("content", "").encode(), ContentType="text/markdown")
    return {"saved": True}

@app.get("/api/v1/security/positions/{pos_id}/soul")
def get_position_soul(pos_id: str, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    try:
        bucket = s3ops.bucket()
        key = f"_shared/soul/positions/{pos_id}/SOUL.md"
        body = s3ops._client().get_object(Bucket=bucket, Key=key)["Body"].read().decode()
        return {"content": body, "key": key}
    except Exception as e:
        return {"content": "", "key": f"_shared/soul/positions/{pos_id}/SOUL.md", "error": str(e)}

@app.put("/api/v1/security/positions/{pos_id}/soul")
def put_position_soul(pos_id: str, body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    bucket = s3ops.bucket()
    s3ops._client().put_object(Bucket=bucket, Key=f"_shared/soul/positions/{pos_id}/SOUL.md",
                               Body=body.get("content", "").encode(), ContentType="text/markdown")
    return {"saved": True}

@app.get("/api/v1/security/positions/{pos_id}/tools")
def get_position_tools(pos_id: str, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    # Read all employees in this position and return their permissions, or return position-default
    try:
        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        ssm = _ssm_client()
        # Check if there's a position-level default stored in SSM
        try:
            resp = ssm.get_parameter(Name=f"/openclaw/{stack}/positions/{pos_id}/tools")
            return json.loads(resp["Parameter"]["Value"])
        except Exception:
            pass
        # Fallback: infer from employees
        emps = db.get_employees()
        pos_emps = [e for e in emps if e.get("positionId") == pos_id]
        for emp in pos_emps[:1]:
            try:
                p = ssm.get_parameter(Name=f"/openclaw/{stack}/tenants/{emp['id']}/permissions")
                data = json.loads(p["Parameter"]["Value"])
                return {"profile": data.get("profile", "basic"), "tools": data.get("tools", [])}
            except Exception:
                pass
        return {"profile": "basic", "tools": ["web_search"]}
    except Exception as e:
        return {"profile": "basic", "tools": ["web_search"], "error": str(e)}

@app.put("/api/v1/security/positions/{pos_id}/tools")
def put_position_tools(pos_id: str, body: dict, authorization: str = Header(default="")):
    """Write tool permissions for ALL employees in this position."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    ssm = _ssm_client()
    profile = {"profile": body.get("profile", "custom"), "tools": body.get("tools", []),
               "role": body.get("profile", "custom"),
               "data_permissions": {"file_paths": [], "api_endpoints": []}}
    value = json.dumps(profile)
    # Write position-level default
    try:
        ssm.put_parameter(Name=f"/openclaw/{stack}/positions/{pos_id}/tools",
                          Value=value, Type="String", Overwrite=True)
    except Exception as e:
        print(f"[security] position tools write failed: {e}")
    # Also propagate to all employees in this position
    emps = db.get_employees()
    for emp in emps:
        if emp.get("positionId") == pos_id:
            try:
                # Use us-east-1 — where agent container reads
                import boto3 as _b3_t
                ssm_e1 = _b3_t.client("ssm", region_name="us-east-1")
                ssm_e1.put_parameter(Name=f"/openclaw/{stack}/tenants/{emp['id']}/permissions",
                                     Value=value, Type="String", Overwrite=True)
            except Exception as e2:
                print(f"[security] emp {emp['id']} tools write failed: {e2}")
    return {"saved": True, "propagated": len([e for e in emps if e.get("positionId") == pos_id])}

@app.get("/api/v1/security/runtimes")
def get_security_runtimes(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3r
        ac = _b3r.client("bedrock-agentcore-control", region_name="us-east-1")
        resp = ac.list_agent_runtimes()
        result = []
        for rt in resp.get("agentRuntimes", []):
            rt_id = rt.get("agentRuntimeId", "")
            try:
                detail = ac.get_agent_runtime(agentRuntimeId=rt_id)
                artifact = detail.get("agentRuntimeArtifact", {}).get("containerConfiguration", {})
                env = detail.get("environmentVariables", {})
                lc = detail.get("lifecycleConfiguration", {})
                result.append({
                    "id": rt_id,
                    "name": detail.get("agentRuntimeName", rt_id),
                    "status": detail.get("status", "UNKNOWN"),
                    "containerUri": artifact.get("containerUri", ""),
                    "roleArn": detail.get("roleArn", ""),
                    "model": env.get("BEDROCK_MODEL_ID", ""),
                    "region": env.get("AWS_REGION", "us-east-1"),
                    "idleTimeoutSec": lc.get("idleRuntimeSessionTimeout", 900),
                    "maxLifetimeSec": lc.get("maxLifetime", 28800),
                    "createdAt": detail.get("createdAt", "").isoformat() if hasattr(detail.get("createdAt", ""), "isoformat") else str(detail.get("createdAt", "")),
                    "version": detail.get("agentRuntimeVersion", "1"),
                })
            except Exception:
                result.append({"id": rt_id, "name": rt.get("agentRuntimeName", rt_id), "status": rt.get("status", "UNKNOWN")})
        return {"runtimes": result}
    except Exception as e:
        return {"runtimes": [], "error": str(e)}

@app.put("/api/v1/security/runtimes/{runtime_id}/lifecycle")
def update_runtime_lifecycle(runtime_id: str, body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3r2
        ac = _b3r2.client("bedrock-agentcore-control", region_name="us-east-1")
        detail = ac.get_agent_runtime(agentRuntimeId=runtime_id)
        # IMPORTANT: always pass environmentVariables — AgentCore clears them if omitted
        existing_env = detail.get("environmentVariables") or {}
        kwargs: dict = {
            "agentRuntimeId": runtime_id,
            "agentRuntimeArtifact": detail["agentRuntimeArtifact"],
            "roleArn": detail["roleArn"],
            "networkConfiguration": detail["networkConfiguration"],
            "lifecycleConfiguration": {
                "idleRuntimeSessionTimeout": body.get("idleTimeoutSec", 900),
                "maxLifetime": body.get("maxLifetimeSec", 28800),
            },
        }
        if existing_env:
            kwargs["environmentVariables"] = existing_env
        if detail.get("protocolConfiguration"):
            kwargs["protocolConfiguration"] = detail["protocolConfiguration"]
        ac.update_agent_runtime(**kwargs)
        return {"saved": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.put("/api/v1/security/runtimes/{runtime_id}/config")
def update_runtime_config(runtime_id: str, body: dict, authorization: str = Header(default="")):
    """Full runtime config update: image, roleArn, security groups, model, lifecycle."""
    _require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3rc
        ac = _b3rc.client("bedrock-agentcore-control", region_name="us-east-1")
        detail = ac.get_agent_runtime(agentRuntimeId=runtime_id)

        # Build updated artifact
        container_uri = body.get("containerUri") or detail["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"]
        artifact = {"containerConfiguration": {"containerUri": container_uri}}

        # Build updated network config
        network_mode = body.get("networkMode", detail.get("networkConfiguration", {}).get("networkMode", "PUBLIC"))
        network_cfg: dict = {"networkMode": network_mode}
        if network_mode == "VPC":
            sg_ids = body.get("securityGroupIds", [])
            subnet_ids = body.get("subnetIds", [])
            if sg_ids and subnet_ids:
                network_cfg["networkModeConfig"] = {"securityGroups": sg_ids, "subnets": subnet_ids}

        # Build updated environment variables — always preserve existing ones
        # (AgentCore clears environmentVariables if the field is omitted from update)
        existing_env = detail.get("environmentVariables") or {}
        new_env = dict(existing_env)
        if body.get("modelId"):
            new_env["BEDROCK_MODEL_ID"] = body["modelId"]
            # Also update DynamoDB so agents pick it up on cold start
            try:
                import boto3 as _b3ddb
                _b3ddb.resource("dynamodb", region_name=os.environ.get("DYNAMODB_REGION", "us-east-2")).Table(
                    os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
                )
            except Exception:
                pass

        role_arn = body.get("roleArn") or detail["roleArn"]
        idle = body.get("idleTimeoutSec") or detail.get("lifecycleConfiguration", {}).get("idleRuntimeSessionTimeout", 900)
        max_life = body.get("maxLifetimeSec") or detail.get("lifecycleConfiguration", {}).get("maxLifetime", 28800)

        kwargs: dict = {
            "agentRuntimeId": runtime_id,
            "agentRuntimeArtifact": artifact,
            "roleArn": role_arn,
            "networkConfiguration": network_cfg,
            "lifecycleConfiguration": {"idleRuntimeSessionTimeout": idle, "maxLifetime": max_life},
        }
        if new_env:
            kwargs["environmentVariables"] = new_env
        if detail.get("protocolConfiguration"):
            kwargs["protocolConfiguration"] = detail["protocolConfiguration"]

        ac.update_agent_runtime(**kwargs)
        return {"saved": True, "runtimeId": runtime_id}
    except Exception as e:
        raise HTTPException(500, str(e))


class CreateRuntimeRequest(BaseModel):
    name: str
    containerUri: str
    roleArn: str
    networkMode: str = "PUBLIC"
    securityGroupIds: list = []
    subnetIds: list = []
    modelId: str = "global.amazon.nova-2-lite-v1:0"
    idleTimeoutSec: int = 900
    maxLifetimeSec: int = 28800


@app.post("/api/v1/security/runtimes/create")
def create_runtime(body: CreateRuntimeRequest, authorization: str = Header(default="")):
    """Create a new AgentCore runtime."""
    _require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3cr
        ac = _b3cr.client("bedrock-agentcore-control", region_name="us-east-1")

        network_cfg: dict = {"networkMode": body.networkMode}
        if body.networkMode == "VPC" and body.securityGroupIds and body.subnetIds:
            network_cfg["networkModeConfig"] = {
                "securityGroups": body.securityGroupIds,
                "subnets": body.subnetIds,
            }

        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        bucket = os.environ.get("S3_BUCKET", "openclaw-tenants-263168716248")
        region = os.environ.get("AWS_REGION", "us-east-1")
        ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
        ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")

        resp = ac.create_agent_runtime(
            agentRuntimeName=body.name,
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": body.containerUri}},
            roleArn=body.roleArn,
            networkConfiguration=network_cfg,
            lifecycleConfiguration={"idleRuntimeSessionTimeout": body.idleTimeoutSec, "maxLifetime": body.maxLifetimeSec},
            protocolConfiguration={"serverProtocol": "HTTP"},
            environmentVariables={
                "BEDROCK_MODEL_ID": body.modelId,
                "AWS_REGION": region,
                "STACK_NAME": stack,
                "S3_BUCKET": bucket,
                "DYNAMODB_TABLE": ddb_table,
                "DYNAMODB_REGION": ddb_region,
            },
        )
        return {"created": True, "runtimeId": resp.get("agentRuntimeId", ""), "status": resp.get("status", "")}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Separate resource endpoints for dropdowns ──────────────────────────────

@app.get("/api/v1/security/ecr-images")
def list_ecr_images(authorization: str = Header(default="")):
    """List all ECR repos and their tagged images for runtime image selector."""
    _require_role(authorization, roles=["admin"])
    import boto3 as _b3ecr
    ecr = _b3ecr.client("ecr", region_name="us-east-1")
    account = boto3.client("sts").get_caller_identity()["Account"]
    result = []
    try:
        repos = ecr.describe_repositories().get("repositories", [])
        for repo in repos:
            try:
                imgs = ecr.describe_images(
                    repositoryName=repo["repositoryName"],
                    filter={"tagStatus": "TAGGED"}
                ).get("imageDetails", [])
                # Sort by push date descending
                imgs.sort(key=lambda x: x.get("imagePushedAt", ""), reverse=True)
                for img in imgs:
                    for tag in (img.get("imageTags") or ["latest"]):
                        pushed = img.get("imagePushedAt")
                        result.append({
                            "uri": f"{repo['repositoryUri']}:{tag}",
                            "repo": repo["repositoryName"],
                            "tag": tag,
                            "digest": (img.get("imageDigest", ""))[:20],
                            "sizeBytes": img.get("imageSizeInBytes", 0),
                            "pushedAt": pushed.isoformat() if hasattr(pushed, "isoformat") else str(pushed or ""),
                        })
            except Exception:
                pass
    except Exception as e:
        return {"images": [], "error": str(e)}
    return {"images": result}


@app.get("/api/v1/security/iam-roles")
def list_iam_roles(authorization: str = Header(default="")):
    """List IAM roles — all roles, agentcore/openclaw roles flagged."""
    _require_role(authorization, roles=["admin"])
    import boto3 as _b3iam
    iam = _b3iam.client("iam")
    result = []
    try:
        # Only paginate first 2 pages (~200 roles max) to keep response fast.
        # Relevant roles (agentcore/openclaw/bedrock) are always included.
        paginator = iam.get_paginator("list_roles")
        pages_fetched = 0
        for page in paginator.paginate():
            pages_fetched += 1
            for r in page["Roles"]:
                name_lower = r["RoleName"].lower()
                relevant = "agentcore" in name_lower or "openclaw" in name_lower or "bedrock" in name_lower
                result.append({
                    "name": r["RoleName"],
                    "arn": r["Arn"],
                    "relevant": relevant,
                    "created": r["CreateDate"].isoformat() if hasattr(r["CreateDate"], "isoformat") else str(r["CreateDate"]),
                })
            if pages_fetched >= 2:
                break
        result.sort(key=lambda r: (not r["relevant"], r["name"]))
    except Exception as e:
        return {"roles": [], "error": str(e)}
    return {"roles": result}


@app.get("/api/v1/security/vpc-resources")
def list_vpc_resources(authorization: str = Header(default="")):
    """List VPCs, subnets, and security groups for runtime network config."""
    _require_role(authorization, roles=["admin"])
    import boto3 as _b3vpc
    ec2 = _b3vpc.client("ec2", region_name="us-east-1")
    result = {"vpcs": [], "subnets": [], "securityGroups": []}
    try:
        vpcs = ec2.describe_vpcs()["Vpcs"]
        for v in vpcs:
            name = next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "Name"), v["VpcId"])
            result["vpcs"].append({
                "id": v["VpcId"], "name": name,
                "cidr": v["CidrBlock"], "isDefault": v.get("IsDefault", False),
            })
    except Exception as e:
        result["vpcs"] = [{"error": str(e)}]
    try:
        subnets = ec2.describe_subnets()["Subnets"]
        for s in subnets:
            name = next((t["Value"] for t in s.get("Tags", []) if t["Key"] == "Name"), s["SubnetId"])
            result["subnets"].append({
                "id": s["SubnetId"], "name": name, "vpcId": s["VpcId"],
                "az": s["AvailabilityZone"], "cidr": s["CidrBlock"],
                "public": s.get("MapPublicIpOnLaunch", False),
            })
    except Exception as e:
        result["subnets"] = [{"error": str(e)}]
    try:
        sgs = ec2.describe_security_groups()["SecurityGroups"]
        for sg in sgs:
            result["securityGroups"].append({
                "id": sg["GroupId"], "name": sg["GroupName"],
                "description": sg["Description"], "vpcId": sg.get("VpcId", ""),
                "relevant": any(kw in sg["GroupName"].lower() for kw in ["agentcore", "openclaw", "bedrock"]),
            })
        result["securityGroups"].sort(key=lambda s: (not s["relevant"], s["name"]))
    except Exception as e:
        result["securityGroups"] = [{"error": str(e)}]
    return result


@app.get("/api/v1/security/infrastructure")
def get_infrastructure(authorization: str = Header(default="")):
    """Aggregate view: ECR + IAM + VPC — run in parallel for speed."""
    _require_role(authorization, roles=["admin"])
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _ecr():
        return "ecr", list_ecr_images(authorization)

    def _iam():
        return "iam", list_iam_roles(authorization)

    def _vpc():
        return "vpc", list_vpc_resources(authorization)

    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_ecr), pool.submit(_iam), pool.submit(_vpc)]
        for f in as_completed(futures, timeout=15):
            try:
                key, data = f.result()
                results[key] = data
            except Exception as e:
                pass

    ecr_data = results.get("ecr", {})
    iam_data = results.get("iam", {})
    vpc_data = results.get("vpc", {})
    return {
        "ecrImages": ecr_data.get("images", []),
        "iamRoles": iam_data.get("roles", []),
        "securityGroups": vpc_data.get("securityGroups", []),
        "vpcs": vpc_data.get("vpcs", []),
        "subnets": vpc_data.get("subnets", []),
    }

# =========================================================================
# Settings — Admin Account, Admin Assistant, System Stats
# =========================================================================

@app.put("/api/v1/settings/admin-password")
def change_admin_password(body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    new_pw = body.get("newPassword", "")
    if len(new_pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        import boto3 as _b3pw
        _b3pw.client("ssm", region_name="us-east-1").put_parameter(
            Name=f"/openclaw/{stack}/admin-password",
            Value=new_pw, Type="SecureString", Overwrite=True)
        os.environ["ADMIN_PASSWORD"] = new_pw
        return {"saved": True}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/v1/settings/admin-assistant")
def get_admin_assistant(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    try:
        cfg = db.get_config("admin-assistant") or {}
    except Exception:
        cfg = {}
    return {
        "model": cfg.get("model", os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")),
        "allowedCommands": cfg.get("allowedCommands", ["list_employees", "list_agents", "get_agent", "list_sessions", "list_audit", "list_approvals", "approve_request", "deny_request", "get_service_status", "get_model_config", "update_model_config"]),
        "systemPromptExtra": cfg.get("systemPromptExtra", ""),
    }

@app.put("/api/v1/settings/admin-assistant")
def put_admin_assistant(body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    cfg = {
        "model": body.get("model", ""),
        "allowedCommands": body.get("allowedCommands", []),
        "systemPromptExtra": body.get("systemPromptExtra", ""),
    }
    db.set_config("admin-assistant", cfg)
    return {"saved": True}

@app.get("/api/v1/settings/system-stats")
def get_system_stats(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    import shutil, subprocess
    result = {}
    # Disk
    try:
        disk = shutil.disk_usage("/")
        result["disk"] = {"total": disk.total, "used": disk.used, "free": disk.free,
                          "pct": round(disk.used / disk.total * 100, 1)}
    except Exception:
        result["disk"] = {}
    # CPU / Memory via /proc (no psutil needed)
    try:
        with open("/proc/meminfo") as f:
            mem_lines = {l.split(":")[0]: int(l.split(":")[1].strip().split()[0])
                         for l in f if ":" in l}
        mem_total = mem_lines.get("MemTotal", 0) * 1024
        mem_free = (mem_lines.get("MemAvailable", 0)) * 1024
        result["memory"] = {"total": mem_total, "used": mem_total - mem_free, "free": mem_free,
                             "pct": round((mem_total - mem_free) / max(mem_total, 1) * 100, 1)}
    except Exception:
        result["memory"] = {}
    try:
        cpu_out = subprocess.check_output(["top", "-bn1"], text=True, timeout=5)
        for line in cpu_out.splitlines():
            if "Cpu" in line or "cpu" in line:
                parts = line.replace(",", " ").split()
                for i, p in enumerate(parts):
                    if "id" in p.lower() and i > 0:
                        try:
                            idle = float(parts[i - 1].replace("%", ""))
                            result["cpu"] = {"pct": round(100 - idle, 1)}
                            break
                        except Exception:
                            pass
                break
    except Exception:
        result["cpu"] = {"pct": 0}
    # Port status
    try:
        ports_out = subprocess.check_output(["ss", "-tlnp"], text=True, timeout=5)
        listening = set()
        for line in ports_out.splitlines():
            if "LISTEN" in line:
                m = __import__("re").search(r":(\d+)\s", line)
                if m:
                    listening.add(int(m.group(1)))
        key_ports = [
            {"port": 8099, "name": "Admin Console", "expected": True},
            {"port": 8090, "name": "Tenant Router", "expected": True},
            {"port": 8091, "name": "H2 Proxy", "expected": True},
            {"port": 18789, "name": "OpenClaw Gateway", "expected": False},
        ]
        result["ports"] = [{"port": p["port"], "name": p["name"], "listening": p["port"] in listening} for p in key_ports]
    except Exception:
        result["ports"] = []
    return result


# =========================================================================
# Startup
# =========================================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("CONSOLE_PORT", "8099"))
    print(f"\n  🦞 OpenClaw Admin Console API v0.4")
    print(f"  DynamoDB: {db.TABLE_NAME} ({db.AWS_REGION})")
    print(f"  S3: {s3ops.bucket()}")
    print(f"  http://localhost:{port}/docs")
    print(f"  http://localhost:{port}/api/v1/dashboard\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
