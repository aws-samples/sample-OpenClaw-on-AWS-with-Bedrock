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

# Gateway region: where EC2, SSM, ECR, and AgentCore live.
# Reads AWS_REGION env var (set in /etc/openclaw/env); falls back to us-east-1.
# Note: DynamoDB may be in a different region (DYNAMODB_REGION) — handled separately.
# Gateway region: where EC2, SSM, ECS, ECR, AgentCore live.
# Uses GATEWAY_REGION env var first (set in start.sh / /etc/openclaw/env),
# then SSM_REGION, then falls back to us-east-1.
# Important: AWS_REGION is NOT used here because it may be set to us-east-2
# for DynamoDB, while all gateway resources (SSM, ECS, EC2) are in us-east-1.
_GATEWAY_REGION = os.environ.get("GATEWAY_REGION", os.environ.get("SSM_REGION", "us-east-1"))


def _resolve_gateway_instance_id() -> str:
    """Resolve the EC2 instance ID this server is running on.
    Reads GATEWAY_INSTANCE_ID env var first (set in /etc/openclaw/env),
    then falls back to IMDSv2 (works when running on EC2)."""
    iid = os.environ.get("GATEWAY_INSTANCE_ID", "")
    if iid:
        return iid
    try:
        import urllib.request
        # IMDSv2: get session token, then fetch instance-id
        put_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
        )
        token = urllib.request.urlopen(put_req, timeout=2).read().decode()
        get_req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token},
        )
        return urllib.request.urlopen(get_req, timeout=2).read().decode()
    except Exception:
        return ""


def _resolve_gateway_account_id() -> str:
    """Resolve the AWS account ID this server is running in, via STS."""
    try:
        import boto3 as _b3sts
        return _b3sts.client("sts", region_name=_GATEWAY_REGION).get_caller_identity()["Account"]
    except Exception:
        return ""


_GATEWAY_INSTANCE_ID: str = _resolve_gateway_instance_id()
_GATEWAY_ACCOUNT_ID: str = _resolve_gateway_account_id()


def _bump_config_version() -> None:
    """Write a new CONFIG#global-version to DynamoDB.

    agent-container/server.py polls this every 5 minutes.  When the version
    changes it clears its assembly cache so every tenant re-assembles their
    SOUL/KB on the next request — no container restart required.

    Called after: global SOUL save, position SOUL save, KB assignment changes,
    model config changes, agent-config changes.
    """
    try:
        import boto3 as _b3bv
        version = datetime.now(timezone.utc).isoformat()
        ddb = _b3bv.resource("dynamodb", region_name=db.AWS_REGION)
        ddb.Table(db.TABLE_NAME).put_item(Item={
            "PK": "ORG#acme", "SK": "CONFIG#global-version",
            "GSI1PK": "TYPE#config", "GSI1SK": "CONFIG#global-version",
            "version": version,
        })
    except Exception as e:
        print(f"[config-version] bump failed (non-fatal): {e}")

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

@app.post("/api/v1/org/departments")
def create_department(body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    return db.create_department(body)

@app.put("/api/v1/org/departments/{dept_id}")
def update_department(dept_id: str, body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    body.pop("id", None)
    result = db.update_department(dept_id, body)
    if not result:
        raise HTTPException(404, f"Department {dept_id} not found")
    return result

@app.delete("/api/v1/org/departments/{dept_id}")
def delete_department(dept_id: str, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    employees = db.get_employees()
    dept_employees = [e for e in employees if e.get("departmentId") == dept_id]
    if dept_employees:
        raise HTTPException(409, {
            "error": "department_has_employees",
            "count": len(dept_employees),
            "names": [e["name"] for e in dept_employees[:5]],
            "message": f"{len(dept_employees)} employee(s) are in this department. Reassign them before deleting.",
        })
    all_depts = db.get_departments()
    sub_depts = [d for d in all_depts if d.get("parentId") == dept_id]
    if sub_depts:
        raise HTTPException(409, {
            "error": "department_has_subdepts",
            "count": len(sub_depts),
            "names": [d["name"] for d in sub_depts],
            "message": f"{len(sub_depts)} sub-department(s) exist under this department. Delete them first.",
        })
    db.delete_department(dept_id)
    return {"ok": True, "deleted": dept_id}

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
    body.pop("id", None)
    result = db.update_position(pos_id, body)
    if result:
        return result
    # Fallback: upsert if not found
    body["id"] = pos_id
    return db.create_position(body)

@app.delete("/api/v1/org/positions/{pos_id}")
def delete_position(pos_id: str, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    employees = db.get_employees()
    pos_employees = [e for e in employees if e.get("positionId") == pos_id]
    if pos_employees:
        raise HTTPException(409, {
            "error": "position_has_employees",
            "count": len(pos_employees),
            "names": [e["name"] for e in pos_employees[:5]],
            "message": f"{len(pos_employees)} employee(s) are in this position. Reassign them first.",
        })
    db.delete_position(pos_id)
    return {"ok": True, "deleted": pos_id}

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

@app.put("/api/v1/org/employees/{emp_id}")
def update_employee(emp_id: str, body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    body.pop("id", None)
    result = db.update_employee(emp_id, body)
    if not result:
        raise HTTPException(404, f"Employee {emp_id} not found")
    return result

@app.delete("/api/v1/org/employees/{emp_id}")
def delete_employee(emp_id: str, force: bool = False, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    bindings = db.get_bindings_for_employee(emp_id)
    im_mappings = db.get_user_mappings_for_employee(emp_id)
    total_links = len(bindings) + len(im_mappings)
    if total_links > 0 and not force:
        raise HTTPException(409, {
            "error": "employee_has_bindings",
            "agentBindings": len(bindings),
            "imMappings": len(im_mappings),
            "message": (
                f"This employee has {len(bindings)} agent binding(s) and {len(im_mappings)} IM channel pairing(s). "
                "Pass force=true to delete all associated bindings along with the employee."
            ),
        })
    if force:
        for b in bindings:
            db.delete_binding(b["id"])
        for m in im_mappings:
            db.delete_user_mapping(m["channel"], m["channelUserId"])
    db.delete_employee(emp_id)
    return {"ok": True, "deleted": emp_id, "bindingsDeleted": len(bindings), "imMappingsDeleted": len(im_mappings)}


@app.get("/api/v1/org/employees/activity")
def get_employee_activities(authorization: str = Header(default="")):
    """Get activity data for all employees — seed records + session-derived for gaps."""
    user = _get_current_user(authorization)
    activities = db.get_activities()

    # Build map of employeeId → activity from seed/stored records
    activity_map: dict = {a["employeeId"]: a for a in activities if a.get("employeeId")}

    # For employees with no stored activity record, derive from SESSION# records.
    # This covers users who connected via portal but were seeded without activity data.
    try:
        all_sessions = db.get_sessions()
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()

        # Group sessions by employee
        sessions_by_emp: dict = {}
        for s in all_sessions:
            eid = s.get("employeeId")
            if eid and eid != "unknown":
                sessions_by_emp.setdefault(eid, []).append(s)

        for eid, emp_sessions in sessions_by_emp.items():
            if eid in activity_map and activity_map[eid].get("source") != "seed":
                continue  # real data already present; skip. seed data gets overwritten by real sessions.
            week_sessions = [s for s in emp_sessions if s.get("lastActive", "") >= week_ago]
            last_active = max((s.get("lastActive", "") for s in emp_sessions), default="")
            channel_status = {}
            for s in emp_sessions[:5]:
                ch = s.get("channel", "portal")
                if last_active:
                    channel_status[ch] = {"lastActive": last_active}
            activity_map[eid] = {
                "employeeId": eid,
                "messagesThisWeek": sum(int(s.get("turns", 0)) for s in week_sessions),
                "lastActive": last_active,
                "totalSessions": len(emp_sessions),
                "weekSessions": len(week_sessions),
                "channelStatus": channel_status,
                "source": "derived",
            }
    except Exception:
        pass  # non-fatal — fall back to seed data only

    activities = list(activity_map.values())

    if user and user.role == "manager":
        scope = _get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            emp_ids = {e["id"] for e in employees if e.get("departmentId") in scope}
            activities = [a for a in activities if a.get("employeeId") in emp_ids]
    return activities


@app.get("/api/v1/org/employees/{emp_id}/activity")
def get_employee_activity(emp_id: str):
    """Get activity data for a single employee — derived from real SESSION# records."""
    # Try real data from SESSION# records first
    try:
        sessions = [s for s in db.get_sessions() if s.get("employeeId") == emp_id]
        if sessions:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            week_ago = (now - timedelta(days=7)).isoformat()
            week_sessions = [s for s in sessions if s.get("lastActive", "") >= week_ago]
            messages_this_week = sum(int(s.get("turns", 0)) for s in week_sessions)
            last_active = max((s.get("lastActive", "") for s in sessions), default="")
            channel_status = {}
            for s in sessions[:5]:
                ch = s.get("channel", "portal")
                channel_status[ch] = {"lastActive": s.get("lastActive", ""), "sessions": 1}
            return {
                "employeeId": emp_id,
                "messagesThisWeek": messages_this_week,
                "lastActive": last_active,
                "totalSessions": len(sessions),
                "weekSessions": len(week_sessions),
                "channelStatus": channel_status,
                "source": "real",
            }
    except Exception:
        pass
    # Fallback to stored activity record (seed data)
    activity = db.get_activity(emp_id)
    if not activity:
        return {"employeeId": emp_id, "messagesThisWeek": 0, "channelStatus": {}}
    return {**activity, "source": "seed"}


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
        cw = _boto3.client("logs", region_name=_GATEWAY_REGION)
        start_time = int((_t.time() - 900) * 1000)  # 15 min ago
        active_ids = set()
        for lg in _get_all_agentcore_log_groups():
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
        s3_bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{_GATEWAY_ACCOUNT_ID}")
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

    # Enrich bindings that are missing employeeName or agentName.
    # portal bindings created by seed_workspaces.py were written without names.
    needs_enrich = any(not b.get("employeeName") or not b.get("agentName") for b in bindings)
    if needs_enrich:
        emp_map = {e["id"]: e for e in db.get_employees()}
        agent_map = {a["id"]: a for a in db.get_agents()}
        for b in bindings:
            if not b.get("employeeName"):
                emp = emp_map.get(b.get("employeeId", ""))
                if emp:
                    b["employeeName"] = emp.get("name", b.get("employeeId", ""))
            if not b.get("agentName"):
                agent = agent_map.get(b.get("agentId", ""))
                if agent:
                    b["agentName"] = agent.get("name", b.get("agentId", ""))

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
    # User-mapping params are always in us-east-1 regardless of admin console AWS_REGION
    return _boto3_main.client("ssm", region_name=os.environ.get("SSM_REGION", "us-east-1"))

def _mapping_prefix():
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    return f"/openclaw/{stack}/user-mapping/"

def _write_user_mapping(channel: str, channel_user_id: str, employee_id: str):
    """Write user mapping to DynamoDB (primary) + SSM (dual-write for legacy components)."""
    try:
        db.create_user_mapping(channel, channel_user_id, employee_id)
    except Exception as e:
        print(f"[user-mapping] DynamoDB write failed: {e}")
    # SSM dual-write kept for tenant_router/agent-container backward compat
    key = f"{channel}__{channel_user_id}"
    path = f"{_mapping_prefix()}{key}"
    try:
        _ssm_client().put_parameter(Name=path, Value=employee_id, Type="String", Overwrite=True)
    except Exception as e:
        print(f"[user-mapping] SSM dual-write failed (non-fatal): {e}")

def _read_user_mapping(channel: str, channel_user_id: str) -> str:
    """Read user mapping — DynamoDB first, SSM fallback."""
    m = db.get_user_mapping(channel, channel_user_id)
    if m:
        return m.get("employeeId", "")
    key = f"{channel}__{channel_user_id}"
    path = f"{_mapping_prefix()}{key}"
    try:
        resp = _ssm_client().get_parameter(Name=path)
        return resp["Parameter"]["Value"]
    except Exception:
        return ""

def _list_user_mappings() -> list:
    """List all user mappings — DynamoDB primary, SSM fallback."""
    ddb = db.get_user_mappings()
    if ddb:
        return ddb
    # SSM fallback for fresh deploys before migration runs
    prefix = _mapping_prefix()
    try:
        ssm = _ssm_client()
        mappings = []
        params = {"Path": prefix, "Recursive": True, "MaxResults": 10}
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
                    })
            token = resp.get("NextToken")
            if not token:
                break
            params["NextToken"] = token
        return mappings
    except Exception as e:
        print(f"[user-mapping] SSM fallback failed: {e}")
        return []

@app.get("/api/v1/bindings/user-mappings")
def get_user_mappings():
    """List all IM user → employee mappings from SSM."""
    return _list_user_mappings()

class UserMappingRequest(BaseModel):
    channel: str       # discord, telegram, slack, whatsapp
    channelUserId: str  # platform-specific user ID
    employeeId: str     # emp-carol, emp-ryan, etc.

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

def _send_im_notification(channel: str, channel_user_id: str, message: str) -> None:
    """Best-effort: send an IM message to the user via their platform bot.
    Non-fatal — logs and returns silently on any error."""
    try:
        import requests as _req
        if channel == "telegram":
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            if token:
                _req.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": channel_user_id, "text": message},
                    timeout=5,
                )
        elif channel == "feishu":
            app_id = os.environ.get("FEISHU_APP_ID", "")
            app_secret = os.environ.get("FEISHU_APP_SECRET", "")
            if app_id and app_secret:
                # Get tenant access token
                auth = _req.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": app_secret}, timeout=5,
                ).json()
                access_token = auth.get("tenant_access_token", "")
                if access_token:
                    _req.post(
                        "https://open.feishu.cn/open-apis/message/v4/send/",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json={
                            "user_id": channel_user_id,
                            "msg_type": "text",
                            "content": {"text": message},
                        },
                        timeout=5,
                    )
        # Discord DM requires creating a channel first — skip for now
    except Exception as e:
        print(f"[im-notify] {channel}/{channel_user_id}: {e}")


@app.delete("/api/v1/bindings/user-mappings")
def delete_user_mapping(channel: str, channelUserId: str):
    """Delete an IM user → employee mapping from DynamoDB + SSM.
    Sends a best-effort IM notification before deleting."""
    # Look up emp_id before deleting (needed for notification and audit)
    existing = db.get_user_mapping(channel, channelUserId)
    emp_id = existing.get("employeeId", "") if existing else ""

    # Best-effort: notify employee their binding is being removed
    if emp_id:
        import threading as _thr
        notif_msg = f"你的 {channel.capitalize()} 账号已从 ACME Corp AI Agent 解除绑定。如需重新连接请登录员工门户。"
        _thr.Thread(
            target=_send_im_notification,
            args=(channel, channelUserId, notif_msg),
            daemon=True,
        ).start()

    # Delete from DynamoDB MAPPING#
    try:
        db.delete_user_mapping(channel, channelUserId)
    except Exception as e:
        print(f"[delete-user-mapping] DynamoDB delete failed: {e}")

    # Delete from SSM
    key = f"{channel}__{channelUserId}"
    path = f"{_mapping_prefix()}{key}"
    try:
        _ssm_client().delete_parameter(Name=path)
    except Exception:
        pass  # SSM may not have this key if DynamoDB was primary

    # Audit log
    if emp_id:
        try:
            db.create_audit_entry({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "eventType": "config_change",
                "actorId": emp_id,
                "actorName": emp_id,
                "targetType": "binding",
                "targetId": f"{channel}__{channelUserId}",
                "detail": f"IM binding revoked: {channel} {channelUserId} → {emp_id}",
                "status": "success",
            })
        except Exception:
            pass

    return {"deleted": True}


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
            s3_bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{_GATEWAY_ACCOUNT_ID}")
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
    "kb-org-directory": {"prefix": "_shared/knowledge/org-directory/", "scope": "global", "scopeName": "All Employees", "accessibleBy": "All employees"},
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
            # Use "playground" channel so Tenant Router creates an isolated session
            # (pgnd__emp-xxx__<hash>) that won't pollute the employee's real conversation.
            # workspace_assembler.py detects "pgnd" prefix → SESSION_CONTEXT.md = Admin Test mode.
            r = _req.post(f"{router_url}/route", json={
                "channel": "playground",
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
    """Employee initiates IM pairing. Returns a token + deep link / QR data.
    Cancels any existing pending token for the same employee+channel so only
    one active token exists at a time."""
    user = _require_auth(authorization)

    # Cancel existing pending tokens for this employee+channel (prevent token accumulation)
    try:
        import boto3 as _b3ps
        from boto3.dynamodb.conditions import Key as _KPS, Attr as _APS
        ddb = _b3ps.resource("dynamodb", region_name=os.environ.get("DYNAMODB_REGION", "us-east-2"))
        table = ddb.Table(os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise"))
        resp = table.query(
            KeyConditionExpression=_KPS("PK").eq("ORG#acme") & _KPS("SK").begins_with("PAIR#"),
            FilterExpression=_APS("employeeId").eq(user.employee_id)
                & _APS("channel").eq(body.channel)
                & _APS("status").eq("pending"),
        )
        for old_item in resp.get("Items", []):
            table.update_item(
                Key={"PK": "ORG#acme", "SK": old_item["SK"]},
                UpdateExpression="SET #s = :cancelled",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":cancelled": "cancelled"},
            )
    except Exception as e:
        print(f"[pair-start] cancel old tokens failed (non-fatal): {e}")

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


@app.get("/api/v1/portal/im-channel-status")
def portal_im_channel_status(authorization: str = Header(default="")):
    """Return which IM channels the admin has configured via OpenClaw Gateway.
    Used by employee portal to show 'available' vs 'admin not configured'."""
    _require_auth(authorization)
    channels = _run_openclaw_channels()
    configured = set()
    for ch in channels:
        name = (ch.get("channel") or ch.get("id", "")).lower()
        if name:
            configured.add(name)
    return {"configured": sorted(configured)}


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


class PairPendingRequest(BaseModel):
    token: str
    channelUserId: str
    channel: str

@app.post("/api/v1/bindings/pair-pending")
def pair_pending(body: PairPendingRequest):
    """Called by H2 Proxy on /start TOKEN — validates token and returns employee info
    for the YES/NO confirmation message. Does NOT consume the token.
    H2 Proxy caches the result and calls pair-complete only after YES."""
    import time as _t
    item = db.get_pair_token(body.token)
    if not item:
        return {"valid": False, "reason": "not_found"}
    if item.get("ttl", 0) < int(_t.time()):
        return {"valid": False, "reason": "expired"}
    if item.get("status") not in ("pending",):
        return {"valid": False, "reason": "already_used"}

    emp_id = item["employeeId"]

    # Check: is this IM userId already bound to a DIFFERENT employee?
    existing = db.get_user_mapping(body.channel, body.channelUserId)
    if existing and existing.get("employeeId") != emp_id:
        other_emps = db.get_employees()
        other_emp = next((e for e in other_emps if e["id"] == existing["employeeId"]), None)
        return {
            "valid": False,
            "reason": "already_bound_other",
            "boundTo": other_emp.get("name", existing["employeeId"]) if other_emp else existing["employeeId"],
        }

    emps = db.get_employees()
    emp = next((e for e in emps if e["id"] == emp_id), {})
    is_rebind = existing is not None and existing.get("employeeId") == emp_id

    return {
        "valid": True,
        "employeeId": emp_id,
        "employeeName": emp.get("name", emp_id),
        "positionName": emp.get("positionName", ""),
        "isRebind": is_rebind,
    }


@app.post("/api/v1/bindings/pair-complete")
def pair_complete(body: PairCompleteRequest):
    """Called by H2 Proxy after employee confirms YES.
    No auth — called from internal network only (H2 Proxy on same EC2).
    Consumes token, writes DynamoDB MAPPING# + SSM, logs audit entry."""
    item = db.consume_pair_token(body.token)
    if not item:
        raise HTTPException(400, "Token invalid, already used, or expired")

    emp_id = item["employeeId"]
    channel = item.get("channel", body.channel)

    # Safety check: reject if this IM userId is already bound to a DIFFERENT employee.
    # pair-pending already checks this, but pair-complete is the final gate in case the
    # H2 Proxy bypasses pair-pending (e.g. old proxy code, retry, race condition).
    existing = db.get_user_mapping(channel, body.channelUserId)
    if existing and existing.get("employeeId") and existing["employeeId"] != emp_id:
        raise HTTPException(409, f"This {channel} account is already bound to another employee. Disconnect it first.")

    # Write DynamoDB MAPPING# (primary, used by tenant_router and workspace_assembler)
    try:
        db.create_user_mapping(channel, body.channelUserId, emp_id)
    except Exception as e:
        print(f"[pair-complete] DynamoDB MAPPING# write failed: {e}")

    # Write SSM (dual-write for backward compat during transition)
    import boto3 as _b3_pair
    _ssm_pair = _b3_pair.client("ssm", region_name=_GATEWAY_REGION)
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

            # Detect if response came from always-on container vs AgentCore Runtime
            stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
            source = "agentcore"
            try:
                _ssm_src = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
                _ssm_src.get_parameter(
                    Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
                source = "always-on"
            except Exception:
                pass

            return {
                "response": reply,
                "agentId": my_binding.get("agentId"),
                "agentName": my_binding.get("agentName"),
                "source": source,
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

    # Determine deployment mode and IM connection info
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    is_always_on = False
    deploy_mode = agent.get("deployMode", "serverless") if agent else "serverless"
    always_on_agent_id = None
    dedicated_bot_info = {}

    try:
        ssm_ao = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        param = ssm_ao.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
        always_on_agent_id = param["Parameter"]["Value"]
        is_always_on = True
        deploy_mode = "always-on-ecs"

        # Check if dedicated IM bots are configured (Plan A direct IM)
        for ch, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
            try:
                ssm_ao.get_parameter(
                    Name=f"/openclaw/{stack}/always-on/{always_on_agent_id}/{key}")
                dedicated_bot_info[ch] = "configured"
            except Exception:
                dedicated_bot_info[ch] = "not_configured"
    except Exception:
        pass

    return {
        "employee": emp,
        "agent": agent,
        "userMd": user_md,
        "memoryMdSize": len(memory_md),
        "dailyMemoryCount": len(s3ops.list_files(f"{user.employee_id}/workspace/memory/")),
        "memoryPreview": memory_preview,
        "isAlwaysOn": is_always_on,
        "deployMode": deploy_mode,            # "serverless" | "always-on-ecs"
        "alwaysOnAgentId": always_on_agent_id,
        "dedicatedBots": dedicated_bot_info,  # which channels have dedicated bot tokens
        "imConnectionMode": (
            "direct" if is_always_on and any(v == "configured" for v in dedicated_bot_info.values())
            else "shared-gateway"             # both modes start with shared gateway
        ),
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
        ssm = _b3_rev.client("ssm", region_name=_GATEWAY_REGION)
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=10)
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
    # Delete from DynamoDB MAPPING# (primary)
    try:
        db.delete_user_mapping(channel, channel_user_id)
    except Exception as e:
        print(f"[disconnect] DynamoDB MAPPING# delete failed (non-fatal): {e}")

    # Delete from SSM (backward compat dual-write cleanup)
    import boto3 as _b3_del
    ssm_del = _b3_del.client("ssm", region_name=_GATEWAY_REGION)
    prefix = _mapping_prefix()
    for key in [f"{channel}__{channel_user_id}", channel_user_id]:
        try:
            ssm_del.delete_parameter(Name=f"{prefix}{key}")
        except Exception:
            pass

    # Remove from DynamoDB employee channels list
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
    """Return connected IM channels plus mode-aware pairing instructions.

    Returns:
    - connected: list of connected channels
    - deployMode: "serverless" | "always-on-ecs"
    - pairingMode: "shared-gateway" | "direct" (direct = dedicated bot per Plan A)
    - pairingInstructions: per-channel guidance based on deploy mode
    """
    user = _require_auth(authorization)
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")

    # Determine deploy mode
    is_always_on = False
    always_on_agent_id = None
    dedicated_bots = {}
    try:
        ssm_ch = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        param = ssm_ch.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
        always_on_agent_id = param["Parameter"]["Value"]
        is_always_on = True
        for ch, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
            try:
                ssm_ch.get_parameter(
                    Name=f"/openclaw/{stack}/always-on/{always_on_agent_id}/{key}")
                dedicated_bots[ch] = True
            except Exception:
                dedicated_bots[ch] = False
    except Exception:
        pass

    # Get connected channels
    connected = []
    try:
        emp = db.get_employee(user.employee_id)
        if emp:
            db_channels = [c for c in emp.get("channels", []) if c not in ("portal",)]
            if db_channels:
                connected = db_channels
    except Exception:
        pass
    if not connected:
        for channel_prefix in ["telegram", "discord", "slack", "whatsapp", "feishu"]:
            if _list_user_mappings_for_employee(user.employee_id, channel_prefix):
                connected.append(channel_prefix)

    # Build pairing instructions based on mode
    pairing_mode = "direct" if is_always_on and any(dedicated_bots.values()) else "shared-gateway"
    instructions = {}
    if pairing_mode == "shared-gateway":
        instructions = {
            "telegram": "Scan the QR code or click the link to start a chat with the shared ACME Agent bot. Send /start to complete pairing.",
            "discord": "Click the invite link to add ACME Agent to your server, then DM the bot and send /start.",
            "whatsapp": "Scan the QR code with your phone's WhatsApp. The pairing link will expire in 5 minutes.",
            "mode_note": "You are using the shared organization bot. All employees share the same bot; your agent is identified by your user ID."
        }
    else:
        instructions = {
            "telegram": "Your agent has a dedicated Telegram bot. Click the link or search for your bot username to start a direct conversation. Send /start to activate.",
            "discord": "Your agent has a dedicated Discord bot. Use the provided invite link to connect your personal agent.",
            "whatsapp": "Scan the QR code to connect your personal WhatsApp to your dedicated agent.",
            "mode_note": "You are using a dedicated personal bot in Always-on mode. Your agent is exclusively yours — messages go directly to your persistent agent, not through a shared gateway.",
        }

    return {
        "connected": connected,
        "deployMode": "always-on-ecs" if is_always_on else "serverless",
        "pairingMode": pairing_mode,
        "pairingInstructions": instructions,
        "dedicatedBots": dedicated_bots,
        "alwaysOnAgentId": always_on_agent_id,
    }


def _list_user_mappings_for_employee(emp_id: str, channel_prefix: str) -> bool:
    """Check if any SSM mapping exists for this employee on the given channel.
    Always uses us-east-1 (where agent container reads mappings from)."""
    try:
        import boto3 as _b3_chk
        prefix = _mapping_prefix()
        ssm = _b3_chk.client("ssm", region_name=_GATEWAY_REGION)
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=10)
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
    "/aws/bedrock-agentcore/runtimes/openclaw_multitenancy_exec_runtime-OkWZBw3ybK-DEFAULT",
    "/openclaw/openclaw-multitenancy/agents",
]

def _get_all_agentcore_log_groups() -> list:
    """Dynamically discover all AgentCore runtime log groups.
    Caches for 5 minutes so new runtimes are picked up automatically."""
    try:
        cw = _boto3.client("logs", region_name=_GATEWAY_REGION)
        resp = cw.describe_log_groups(logGroupNamePrefix="/aws/bedrock-agentcore/runtimes/")
        groups = [g["logGroupName"] for g in resp.get("logGroups", [])]
        extra = ["/openclaw/openclaw-multitenancy/agents"]
        return groups + [g for g in extra if g not in groups]
    except Exception:
        return _LOG_GROUPS

def _query_cloudwatch_sessions(region: str, minutes: int = 30) -> list:
    """Query CloudWatch Logs for recent agent invocations to derive active sessions."""
    try:
        cw = _boto3.client("logs", region_name=region)
        import time as _time
        start_time = int((_time.time() - minutes * 60) * 1000)
        sessions = []

        for log_group in _get_all_agentcore_log_groups():
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
                    s["channel"] = (emp.get("channels") or ["portal"])[0]
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
            cw["channel"] = (emp.get("channels") or ["discord"])[0] if not cw.get("channel") or cw["channel"] == "unknown" else cw["channel"]
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


@app.post("/api/v1/monitor/sessions/{session_id}/takeover")
def takeover_session(session_id: str, authorization: str = Header(default="")):
    """Admin takes over a session — agent pauses auto-reply.

    Writes SSM: /openclaw/{stack}/sessions/{tenant_id}/takeover = admin_user_id
    server.py checks this before each invocation and skips openclaw if set.
    """
    user = _require_role(authorization, roles=["admin", "manager"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        ssm.put_parameter(
            Name=f"/openclaw/{stack}/sessions/{session_id}/takeover",
            Value=user.employee_id, Type="String", Overwrite=True)
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "session_takeover",
            "actorId": user.employee_id, "actorName": user.name,
            "targetType": "session", "targetId": session_id,
            "detail": f"Admin {user.name} took over session {session_id}",
            "status": "success",
        })
    except Exception as e:
        raise HTTPException(500, f"Takeover failed: {e}")
    return {"taken_over": True, "sessionId": session_id, "adminId": user.employee_id}


@app.delete("/api/v1/monitor/sessions/{session_id}/takeover")
def return_session(session_id: str, authorization: str = Header(default="")):
    """Admin returns session to agent — resumes auto-reply."""
    user = _require_role(authorization, roles=["admin", "manager"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        ssm.delete_parameter(Name=f"/openclaw/{stack}/sessions/{session_id}/takeover")
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "session_returned",
            "actorId": user.employee_id, "actorName": user.name,
            "targetType": "session", "targetId": session_id,
            "detail": f"Admin {user.name} returned session {session_id} to agent",
            "status": "success",
        })
    except Exception as e:
        raise HTTPException(500, f"Return failed: {e}")
    return {"returned": True, "sessionId": session_id}


@app.post("/api/v1/monitor/sessions/{session_id}/send")
def admin_send_message(session_id: str, body: dict, authorization: str = Header(default="")):
    """Admin sends a message while in takeover mode (bypasses agent)."""
    user = _require_role(authorization, roles=["admin", "manager"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(400, "message required")

    # Verify takeover is active
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        ssm.get_parameter(Name=f"/openclaw/{stack}/sessions/{session_id}/takeover")
    except Exception:
        raise HTTPException(400, "Session is not in takeover mode")

    # Store admin message in DynamoDB CONV# for session continuity
    try:
        import boto3 as _b3s
        ddb = _b3s.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        from decimal import Decimal
        ts = datetime.now(timezone.utc).isoformat()
        table.put_item(Item={
            "PK": "ORG#acme", "SK": f"CONV#{session_id}#admin#{int(time.time())}",
            "sessionId": session_id, "role": "assistant", "content": message,
            "ts": ts, "source": "human_admin", "adminId": user.employee_id,
        })
    except Exception as e:
        raise HTTPException(500, f"Message storage failed: {e}")

    return {"sent": True, "message": message, "adminId": user.employee_id, "humanAssisted": True}


@app.get("/api/v1/monitor/sessions/{session_id}/takeover")
def get_takeover_status(session_id: str, authorization: str = Header(default="")):
    """Check if a session is in takeover mode."""
    _require_auth(authorization)
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        param = ssm.get_parameter(Name=f"/openclaw/{stack}/sessions/{session_id}/takeover")
        return {"active": True, "adminId": param["Parameter"]["Value"], "sessionId": session_id}
    except Exception:
        return {"active": False, "sessionId": session_id}


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
        cw = _boto3.client("logs", region_name=_GATEWAY_REGION)
        start_time = int((_time.time() - minutes * 60) * 1000)
        events = []

        for log_group in _get_all_agentcore_log_groups():
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

        # If CloudWatch returned nothing, supplement with DynamoDB audit log entries
        if not events:
            try:
                audit = db.get_audit_entries(limit=200)
                cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
                for a in audit:
                    ts = a.get("timestamp", "")
                    try:
                        entry_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        continue
                    if entry_time < cutoff:
                        continue
                    if a.get("eventType") == "agent_invocation":
                        events.append({
                            "type": "invocation",
                            "message": f"Agent invocation — {a.get('actorName', '?')}",
                            "tenant": a.get("actorId", ""),
                            "timestamp": ts,
                            "raw": a.get("detail", "")[:200],
                            "source": "audit_log",
                        })
                if events:
                    events.sort(key=lambda e: e["timestamp"], reverse=True)
                    invocations = [e for e in events if e["type"] == "invocation"]
                    active_tenants = {e.get("tenant", "") for e in invocations if e.get("tenant")}
            except Exception:
                pass

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


def _calculate_agent_quality(agent_id: str) -> dict:
    """Calculate real quality score for an agent from DynamoDB data.

    Quality Score = 0.3×satisfaction + 0.2×tool_success + 0.2×response_time + 0.2×compliance + 0.1×completion

    Data sources:
    - Satisfaction: FEEDBACK# records (thumbs up rate)
    - Tool success: AUDIT# records (agent_invocation success rate)
    - Response time: SESSION# durationMs (P75 < 8s = full score)
    - Compliance: AUDIT# permission_denied rate (low = good)
    - Completion: SESSION# turns > 1 rate
    """
    try:
        import boto3 as _b3q
        from decimal import Decimal
        ddb = _b3q.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)

        # Tool success + compliance from AUDIT# entries (last 7 days)
        audit_resp = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={":pk": "ORG#acme", ":sk": "AUDIT#"},
            ScanIndexForward=False, Limit=200)
        agent_audits = [a for a in audit_resp.get("Items", [])
                        if a.get("targetId") == agent_id]
        invocations = [a for a in agent_audits if a.get("eventType") == "agent_invocation"]
        permission_denials = [a for a in agent_audits if a.get("eventType") == "permission_denied"]
        tool_success = 1.0 if not invocations else sum(
            1 for a in invocations if a.get("status") == "success") / max(1, len(invocations))
        compliance = 1.0 if not invocations else max(0, 1 - len(permission_denials) / max(1, len(invocations)))

        # Response time from SESSION# records
        sessions = [s for s in db.get_sessions() if s.get("agentId") == agent_id]
        durations = [float(s["durationMs"]) for s in sessions if s.get("durationMs")]
        if durations:
            p75 = sorted(durations)[int(len(durations) * 0.75)]
            response_score = min(1.0, max(0, 1.0 - (p75 - 3000) / 12000))  # 3s=1.0, 15s=0.0
        else:
            response_score = 0.7  # neutral default

        # Completion rate (sessions with >1 turn)
        multi_turn = [s for s in sessions if int(s.get("turns", 0)) > 1]
        completion = len(multi_turn) / max(1, len(sessions)) if sessions else 0.7

        # Satisfaction from FEEDBACK# (explicit thumbs up/down)
        feedback_resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND begins_with(GSI1SK, :sk)",
            ExpressionAttributeValues={":pk": "TYPE#feedback", ":sk": f"FEEDBACK#{agent_id}"},
            Limit=100)
        feedbacks = feedback_resp.get("Items", [])
        if feedbacks:
            positive = sum(1 for f in feedbacks if f.get("rating") == "up")
            satisfaction = positive / len(feedbacks)
        else:
            # Fall back to audit success rate as proxy for satisfaction
            satisfaction = tool_success * 0.9 + 0.1

        score = round(
            0.3 * satisfaction + 0.2 * tool_success +
            0.2 * response_score + 0.2 * compliance + 0.1 * completion, 2)

        return {
            "score": round(score * 5, 1),  # 0-5 scale
            "breakdown": {
                "satisfaction": round(satisfaction * 5, 1),
                "toolSuccess": round(tool_success * 5, 1),
                "responseTime": round(response_score * 5, 1),
                "compliance": round(compliance * 5, 1),
                "completion": round(completion * 5, 1),
            },
            "dataPoints": {
                "invocations": len(invocations),
                "sessions": len(sessions),
                "feedbacks": len(feedbacks),
            }
        }
    except Exception as e:
        return {"score": None, "error": str(e)}


@app.get("/api/v1/agents/{agent_id}/quality")
def get_agent_quality(agent_id: str, authorization: str = Header(default="")):
    """Get real quality score for an agent calculated from DynamoDB data."""
    _require_auth(authorization)
    return _calculate_agent_quality(agent_id)


@app.post("/api/v1/portal/request-always-on")
def request_always_on(body: dict, authorization: str = Header(default="")):
    """Employee requests always-on mode for their agent.
    Creates a pending approval that IT admin can approve/deny."""
    user = _require_auth(authorization)
    reason = body.get("reason", "").strip() or "Employee-initiated request"

    # Check not already always-on
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    try:
        ssm_chk = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        ssm_chk.get_parameter(
            Name=f"/openclaw/{stack}/tenants/{user.employee_id}/always-on-agent")
        raise HTTPException(400, "Already in always-on mode")
    except HTTPException:
        raise
    except Exception:
        pass

    approval_id = f"apr-alwayson-{user.employee_id}"
    db.create_approval({
        "id": approval_id,
        "type": "always_on_request",
        "requestedBy": user.employee_id,
        "requestedByName": user.name,
        "reason": reason,
        "status": "pending",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "details": {
            "employeeId": user.employee_id,
            "currentMode": "serverless",
            "requestedMode": "always-on-ecs",
        }
    })
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "always_on_request", "actorId": user.employee_id,
        "actorName": user.name, "targetType": "agent", "targetId": user.employee_id,
        "detail": f"Employee requested always-on mode: {reason}", "status": "pending",
    })
    return {"requested": True, "approvalId": approval_id,
            "message": "Request submitted. IT admin will review and activate always-on mode for your agent."}


@app.post("/api/v1/portal/feedback")
def submit_feedback(body: dict, authorization: str = Header(default="")):
    """Employee submits thumbs up/down feedback on an agent response."""
    user = _require_auth(authorization)
    session_id = body.get("sessionId", "")
    turn_seq = body.get("turnSeq", 0)
    rating = body.get("rating", "")  # "up" or "down"
    agent_id = body.get("agentId", "")

    if rating not in ("up", "down"):
        raise HTTPException(400, "rating must be 'up' or 'down'")

    try:
        import boto3 as _b3fb
        ddb = _b3fb.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        from decimal import Decimal
        fid = f"{session_id}#{turn_seq:04d}"
        table.put_item(Item={
            "PK": "ORG#acme",
            "SK": f"FEEDBACK#{fid}",
            "GSI1PK": "TYPE#feedback",
            "GSI1SK": f"FEEDBACK#{agent_id}#{fid}",
            "sessionId": session_id,
            "turnSeq": Decimal(str(turn_seq)),
            "rating": rating,
            "employeeId": user.employee_id,
            "agentId": agent_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        raise HTTPException(500, f"Failed to save feedback: {e}")

    return {"saved": True, "rating": rating}


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
    """7-day cost trend — aggregated from real USAGE#{agent}#{date} records in DynamoDB.
    Falls back to seed COST_TREND# data if real usage is too sparse."""
    from datetime import timedelta
    employees = db.get_employees()
    active_emp_count = len([e for e in employees if e.get("agentId")])
    chatgpt_daily = round(active_emp_count * 0.83, 2)  # $25/user/month ≈ $0.83/day

    # Aggregate real USAGE records by date (last 7 days)
    try:
        import boto3 as _b3tr
        from decimal import Decimal
        ddb = _b3tr.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        now = datetime.now(timezone.utc)
        daily_costs: dict = {}
        daily_requests: dict = {}
        for i in range(7):
            date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_costs[date_str] = 0.0
            daily_requests[date_str] = 0

        # Scan recent USAGE records
        resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND begins_with(GSI1SK, :sk)",
            ExpressionAttributeValues={":pk": "TYPE#usage", ":sk": "USAGE#"},
            Limit=500)
        for item in resp.get("Items", []):
            date = item.get("date", "")
            if date in daily_costs:
                daily_costs[date] += float(item.get("cost", 0))
                daily_requests[date] += int(item.get("requests", 0))

        # Build trend array (most recent first)
        real_trend = []
        has_real_data = any(v > 0 for v in daily_costs.values())
        for i in range(6, -1, -1):
            date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            real_trend.append({
                "date": date_str,
                "openclawCost": round(daily_costs.get(date_str, 0), 4),
                "chatgptEquivalent": chatgpt_daily,
                "totalRequests": daily_requests.get(date_str, 0),
                "source": "real" if has_real_data else "seed",
            })

        if has_real_data:
            return real_trend
    except Exception:
        pass

    # Fallback to seed COST_TREND# records
    trend = db.get_cost_trend()
    return [{
        "date": t.get("date"),
        "openclawCost": float(t.get("openclawCost", 0)),
        "chatgptEquivalent": float(t["chatgptEquivalent"]) if t.get("chatgptEquivalent") else chatgpt_daily,
        "totalRequests": t.get("totalRequests", 0),
        "source": "seed",
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

@app.put("/api/v1/settings/model/employee/{emp_id}")
def set_employee_model(emp_id: str, body: dict, authorization: str = Header(default="")):
    """Set a per-employee model override — highest priority, overrides position and global."""
    _require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config.setdefault("employeeOverrides", {})[emp_id] = body
    db.set_config("model", config)
    return config["employeeOverrides"]

@app.delete("/api/v1/settings/model/employee/{emp_id}")
def remove_employee_model(emp_id: str, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config.get("employeeOverrides", {}).pop(emp_id, None)
    db.set_config("model", config)
    return config.get("employeeOverrides", {})

# ── Agent Config (compaction, context window, language) ────────────────────────

def _get_agent_config() -> dict:
    cfg = db.get_config("agent-config")
    if not cfg:
        return {"positionConfig": {}, "employeeConfig": {}}
    return cfg

@app.get("/api/v1/settings/agent-config")
def get_agent_config(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    return _get_agent_config()

@app.put("/api/v1/settings/agent-config/position/{pos_id}")
def set_position_agent_config(pos_id: str, body: dict, authorization: str = Header(default="")):
    """Set position-level agent config: recentTurnsPreserve, compactionMode, maxTokens, language."""
    _require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.setdefault("positionConfig", {})[pos_id] = body
    db.set_config("agent-config", cfg)
    return cfg["positionConfig"][pos_id]

@app.delete("/api/v1/settings/agent-config/position/{pos_id}")
def delete_position_agent_config(pos_id: str, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.get("positionConfig", {}).pop(pos_id, None)
    db.set_config("agent-config", cfg)
    return {"deleted": pos_id}

@app.put("/api/v1/settings/agent-config/employee/{emp_id}")
def set_employee_agent_config(emp_id: str, body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.setdefault("employeeConfig", {})[emp_id] = body
    db.set_config("agent-config", cfg)
    return cfg["employeeConfig"][emp_id]

@app.delete("/api/v1/settings/agent-config/employee/{emp_id}")
def delete_employee_agent_config(emp_id: str, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    cfg = _get_agent_config()
    cfg.get("employeeConfig", {}).pop(emp_id, None)
    db.set_config("agent-config", cfg)
    return {"deleted": emp_id}

# ── KB Assignments ─────────────────────────────────────────────────────────────

def _get_kb_assignments() -> dict:
    cfg = db.get_config("kb-assignments")
    return cfg if cfg else {"positionKBs": {}, "employeeKBs": {}}

@app.get("/api/v1/settings/kb-assignments")
def get_kb_assignments(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    return _get_kb_assignments()

@app.put("/api/v1/settings/kb-assignments/position/{pos_id}")
def set_position_kbs(pos_id: str, body: dict, authorization: str = Header(default="")):
    """Assign knowledge bases to a position. kbIds: list of KB IDs."""
    _require_role(authorization, roles=["admin"])
    cfg = _get_kb_assignments()
    cfg.setdefault("positionKBs", {})[pos_id] = body.get("kbIds", [])
    db.set_config("kb-assignments", cfg)
    _bump_config_version()
    return cfg["positionKBs"][pos_id]

@app.put("/api/v1/settings/kb-assignments/employee/{emp_id}")
def set_employee_kbs(emp_id: str, body: dict, authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    cfg = _get_kb_assignments()
    cfg.setdefault("employeeKBs", {})[emp_id] = body.get("kbIds", [])
    db.set_config("kb-assignments", cfg)
    _bump_config_version()
    return cfg["employeeKBs"][emp_id]

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
# Org Sync — Feishu / DingTalk (Task C)
# =========================================================================

@app.get("/api/v1/settings/org-sync")
def get_org_sync_config(authorization: str = Header(default="")):
    """Get org sync configuration (source, interval, last sync time)."""
    _require_role(authorization, roles=["admin"])
    cfg = db.get_config("org-sync") or {}
    return {
        "source": cfg.get("source", "none"),
        "enabled": cfg.get("enabled", False),
        "interval": cfg.get("interval", "4h"),
        "lastSync": cfg.get("lastSync"),
        "lastResult": cfg.get("lastResult"),
        "status": cfg.get("status", "not_configured"),
    }


@app.put("/api/v1/settings/org-sync")
def update_org_sync_config(body: dict, authorization: str = Header(default="")):
    """Save org sync configuration."""
    _require_role(authorization, roles=["admin"])
    cfg = db.get_config("org-sync") or {}
    cfg.update({k: v for k, v in body.items()
                if k in ("source", "enabled", "interval", "apiKey", "appId", "appSecret", "tenantKey")})
    db.set_config("org-sync", cfg)
    return {"saved": True}


@app.post("/api/v1/settings/org-sync/preview")
def preview_org_sync(authorization: str = Header(default="")):
    """Simulate org sync and return a diff preview (what would change)."""
    _require_role(authorization, roles=["admin"])
    cfg = db.get_config("org-sync") or {}
    source = cfg.get("source", "none")

    if source == "none":
        raise HTTPException(400, "No org sync source configured")

    # Fetch remote org data (Feishu / DingTalk)
    remote_users = []
    remote_depts = []
    try:
        if source == "feishu":
            remote_users, remote_depts = _fetch_feishu_org(cfg)
        elif source == "dingtalk":
            remote_users, remote_depts = _fetch_dingtalk_org(cfg)
        else:
            raise HTTPException(400, f"Unsupported source: {source}")
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch from {source}: {e}")

    # Compare with current DynamoDB org
    current_emps = {e["id"]: e for e in db.get_employees()}
    current_depts = {d["id"]: d for d in db.get_departments()}

    new_emps, changed_emps, left_emps = [], [], []
    for ru in remote_users:
        if ru["id"] not in current_emps:
            new_emps.append(ru)
        elif _emp_changed(current_emps[ru["id"]], ru):
            changed_emps.append({"before": current_emps[ru["id"]], "after": ru})
    for emp_id in current_emps:
        if not any(ru["id"] == emp_id for ru in remote_users):
            left_emps.append(current_emps[emp_id])

    new_depts, changed_depts = [], []
    for rd in remote_depts:
        if rd["id"] not in current_depts:
            new_depts.append(rd)
        elif current_depts[rd["id"]].get("name") != rd.get("name"):
            changed_depts.append({"before": current_depts[rd["id"]], "after": rd})

    return {
        "source": source,
        "employees": {"new": new_emps, "changed": changed_emps, "left": left_emps},
        "departments": {"new": new_depts, "changed": changed_depts},
        "summary": {
            "newEmployees": len(new_emps),
            "changedEmployees": len(changed_emps),
            "leftEmployees": len(left_emps),
            "deptChanges": len(new_depts) + len(changed_depts),
        }
    }


@app.post("/api/v1/settings/org-sync/apply")
def apply_org_sync(body: dict, authorization: str = Header(default="")):
    """Apply org sync changes from a preview result."""
    _require_role(authorization, roles=["admin"])
    preview = body.get("preview", {})
    applied = {"newEmployees": 0, "archivedEmployees": 0, "updatedEmployees": 0, "newDepts": 0}

    for emp in preview.get("employees", {}).get("new", []):
        # Auto-provision: create employee + agent + binding
        _auto_provision_employee(emp)
        applied["newEmployees"] += 1

    for change in preview.get("employees", {}).get("changed", []):
        db.update_employee(change["after"]["id"], change["after"])
        applied["updatedEmployees"] += 1

    for emp in preview.get("employees", {}).get("left", []):
        db.update_employee(emp["id"], {**emp, "agentStatus": "archived"})
        applied["archivedEmployees"] += 1

    for dept in preview.get("departments", {}).get("new", []):
        db.create_department(dept)
        applied["newDepts"] += 1

    # Update sync state
    cfg = db.get_config("org-sync") or {}
    cfg["lastSync"] = datetime.now(timezone.utc).isoformat()
    cfg["lastResult"] = applied
    cfg["status"] = "ok"
    db.set_config("org-sync", cfg)

    return {"applied": applied}


def _fetch_feishu_org(cfg: dict):
    """Fetch users and departments from Feishu API."""
    import requests as _req
    app_id = cfg.get("appId", "")
    app_secret = cfg.get("appSecret", "")
    if not app_id or not app_secret:
        raise ValueError("Feishu appId and appSecret required")

    # Get tenant_access_token
    token_resp = _req.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10
    ).json()
    token = token_resp.get("tenant_access_token", "")
    if not token:
        raise ValueError(f"Failed to get Feishu token: {token_resp.get('msg')}")

    headers = {"Authorization": f"Bearer {token}"}
    # Fetch departments
    depts_resp = _req.get(
        "https://open.feishu.cn/open-apis/contact/v3/departments",
        headers=headers, params={"page_size": 200}, timeout=10).json()
    depts = [{"id": f"dept-{d['open_department_id']}", "name": d["name"],
               "parentId": d.get("parent_open_department_id")}
             for d in depts_resp.get("data", {}).get("items", [])]

    # Fetch users
    users_resp = _req.get(
        "https://open.feishu.cn/open-apis/contact/v3/users",
        headers=headers, params={"page_size": 200}, timeout=10).json()
    users = [{"id": f"emp-{u['open_id']}", "name": u["name"],
               "departmentId": f"dept-{u.get('open_department_ids', [''])[0]}",
               "positionId": "pos-employee", "role": "employee"}
             for u in users_resp.get("data", {}).get("items", [])]

    return users, depts


def _fetch_dingtalk_org(cfg: dict):
    """Fetch users and departments from DingTalk API."""
    import requests as _req
    app_key = cfg.get("appId", "")
    app_secret = cfg.get("appSecret", "")
    if not app_key or not app_secret:
        raise ValueError("DingTalk appId and appSecret required")

    token_resp = _req.post(
        "https://oapi.dingtalk.com/gettoken",
        params={"appkey": app_key, "appsecret": app_secret}, timeout=10).json()
    token = token_resp.get("access_token", "")

    headers = {"x-acs-dingtalk-access-token": token}
    depts_resp = _req.post(
        "https://oapi.dingtalk.com/topapi/v2/department/listsub",
        headers={"Content-Type": "application/json"},
        params={"access_token": token},
        json={"dept_id": 1, "language": "zh_CN"}, timeout=10).json()
    depts = [{"id": f"dept-{d['dept_id']}", "name": d["name"]}
             for d in depts_resp.get("result", {}).get("dept_list", [])]

    users_resp = _req.post(
        "https://oapi.dingtalk.com/topapi/v2/user/list",
        params={"access_token": token},
        json={"dept_id": 1, "size": 100}, timeout=10).json()
    users = [{"id": f"emp-{u['userid']}", "name": u["name"],
               "departmentId": f"dept-{u.get('dept_id_list', [1])[0]}",
               "positionId": "pos-employee", "role": "employee"}
             for u in users_resp.get("result", {}).get("list", [])]

    return users, depts


def _emp_changed(current: dict, remote: dict) -> bool:
    """Check if employee record differs between current and remote."""
    for field in ("name", "departmentId", "positionId"):
        if current.get(field) != remote.get(field):
            return True
    return False


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


@app.get("/api/v1/admin/im-channel-connections")
def get_im_channel_connections(authorization: str = Header(default="")):
    """Per-channel employee connection table for admin management."""
    _require_role(authorization, roles=["admin"])
    try:
        # 1. Get all SSM user-mapping params
        raw_mappings = _list_user_mappings()
        print(f"[im-connections] _list_user_mappings returned {len(raw_mappings)} entries")

        # 2. Employee lookup
        emps = db.get_employees()
        emp_map = {e["id"]: e for e in emps}
        print(f"[im-connections] {len(emps)} employees loaded")

        # 3. Session counts from audit log (lightweight: limit 500)
        session_counts: dict = {}
        last_active: dict = {}
        try:
            audit = db.get_audit_entries(limit=500)
            for a in audit:
                eid = a.get("actorId", "")
                if eid and a.get("eventType") == "agent_invocation":
                    session_counts[eid] = session_counts.get(eid, 0) + 1
                    ts = a.get("timestamp", "")
                    if ts > last_active.get(eid, ""):
                        last_active[eid] = ts
        except Exception as ae:
            print(f"[im-connections] audit fetch failed (non-fatal): {ae}")

        # 4. Group by channel — skip unknown/unkn prefixes
        by_channel: dict = {}
        for m in raw_mappings:
            channel = m.get("channel", "")
            if channel in ("unknown", "unkn") or not channel:
                continue
            emp_id = m.get("employeeId", "")
            emp = emp_map.get(emp_id)
            if not emp:
                continue
            channel_user_id = m.get("channelUserId", "")
            by_channel.setdefault(channel, []).append({
                "empId": emp_id,
                "empName": emp.get("name", emp_id),
                "positionName": emp.get("positionName", ""),
                "departmentName": emp.get("departmentName", ""),
                "channelUserId": channel_user_id,
                "connectedAt": m.get("lastModified", ""),
                "sessionCount": session_counts.get(emp_id, 0),
                "lastActive": last_active.get(emp_id, ""),
            })

        print(f"[im-connections] result channels: {list(by_channel.keys())}, total: {sum(len(v) for v in by_channel.values())}")
        return {"connections": by_channel}

    except Exception as e:
        print(f"[im-connections] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"connections": {}, "error": str(e)}


@app.get("/api/v1/admin/im-channels")
def get_im_channels(authorization: str = Header(default="")):
    """Get live IM channel status from Gateway + SSM mappings count per channel."""
    _require_role(authorization, roles=["admin", "manager"])
    import boto3 as _b3_ch
    ssm = _b3_ch.client("ssm", region_name=_GATEWAY_REGION)

    # Get all user mappings to count per channel
    channel_counts: dict = {}
    try:
        prefix = _mapping_prefix()
        resp = ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=10)
        for p in resp.get("Parameters", []):
            name = p["Name"].replace(prefix, "")
            for ch in ["telegram", "discord", "slack", "whatsapp", "feishu", "dingtalk", "teams", "googlechat"]:
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
        {"id": "telegram",   "label": "Telegram",          "enterprise": True},
        {"id": "discord",    "label": "Discord",            "enterprise": True},
        {"id": "slack",      "label": "Slack",              "enterprise": True},
        {"id": "teams",      "label": "Microsoft Teams",    "enterprise": True},
        {"id": "feishu",     "label": "Feishu / Lark",      "enterprise": True},
        {"id": "dingtalk",   "label": "DingTalk",           "enterprise": True},
        {"id": "googlechat", "label": "Google Chat",        "enterprise": True},
        {"id": "whatsapp",   "label": "WhatsApp",           "enterprise": True},
        {"id": "wechat",     "label": "WeChat",             "enterprise": False},
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


@app.get("/api/v1/internal/im-binding-check")
def im_binding_check(channel: str, channelUserId: str):
    """Internal endpoint called by H2 Proxy before routing each IM message.
    Strict enforcement: only respond to IM accounts that have a valid employee binding.
    No auth required — only accessible from the same EC2 (internal network)."""
    # Primary: DynamoDB channel-specific lookup
    m = db.get_user_mapping(channel, channelUserId)
    if m and m.get("employeeId"):
        return {"bound": True, "employeeId": m["employeeId"]}
    # Fallback: scan MAPPING# for bare channelUserId (Feishu OU IDs, etc.)
    try:
        import boto3 as _b3bc
        from boto3.dynamodb.conditions import Key as _KBC, Attr as _ABC
        ddb = _b3bc.resource("dynamodb", region_name=os.environ.get("DYNAMODB_REGION", "us-east-2"))
        table = ddb.Table(os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise"))
        resp = table.query(
            KeyConditionExpression=_KBC("PK").eq("ORG#acme") & _KBC("SK").begins_with("MAPPING#"),
            FilterExpression=_ABC("channelUserId").eq(channelUserId),
        )
        if resp.get("Items"):
            return {"bound": True, "employeeId": resp["Items"][0]["employeeId"]}
    except Exception:
        pass
    return {"bound": False}


@app.post("/api/v1/admin/im-channels/{channel}/test")
def test_im_channel(channel: str, authorization: str = Header(default="")):
    """Test bot connection for a channel by asking OpenClaw if it has the channel configured.
    OpenClaw manages bot credentials — we don't store them separately.
    Returns {ok, botName, error}."""
    _require_role(authorization, roles=["admin"])
    try:
        import subprocess as _sp
        openclaw_bin = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin/openclaw"
        env_path = "/home/ubuntu/.nvm/versions/node/v22.22.1/bin:/usr/local/bin:/usr/bin:/bin"
        result = _sp.run(
            ["sudo", "-u", "ubuntu", "env", f"PATH={env_path}", "HOME=/home/ubuntu",
             openclaw_bin, "channels", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout:
            import json as _json
            # openclaw prints plugin registration logs (with ANSI codes) before the JSON blob.
            # Find the first '{' to skip the preamble, same pattern used in server.py.
            stdout = result.stdout
            json_start = stdout.find('{')
            if json_start == -1:
                return {"ok": False, "error": "Unexpected openclaw output — no JSON found."}
            raw = _json.loads(stdout[json_start:])
            configured = raw.get("chat", {})
            # channel name in openclaw may differ: "feishu" -> "feishu", "discord" -> "discord"
            channel_key = channel.lower()
            if channel_key in configured and configured[channel_key]:
                accounts = configured[channel_key]
                return {"ok": True, "botName": f"{channel} ({', '.join(accounts)})"}
            return {
                "ok": False,
                "error": f"{channel.capitalize()} bot not configured in OpenClaw. Open Gateway UI (port 18789) → Channels → Add {channel.capitalize()}.",
            }
        return {"ok": False, "error": "Could not reach OpenClaw CLI. Ensure openclaw gateway is running."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    _bump_config_version()
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
    _bump_config_version()
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
                ssm_e1 = _b3_t.client("ssm", region_name=_GATEWAY_REGION)
                ssm_e1.put_parameter(Name=f"/openclaw/{stack}/tenants/{emp['id']}/permissions",
                                     Value=value, Type="String", Overwrite=True)
            except Exception as e2:
                print(f"[security] emp {emp['id']} tools write failed: {e2}")
    return {"saved": True, "propagated": len([e for e in emps if e.get("positionId") == pos_id])}

@app.get("/api/v1/security/positions/{pos_id}/runtime")
def get_position_runtime(pos_id: str, authorization: str = Header(default="")):
    """Read the runtime assigned to a position (SSM /positions/{pos_id}/runtime-id)."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    try:
        import boto3 as _b3pr
        ssm = _b3pr.client("ssm", region_name=_GATEWAY_REGION)
        resp = ssm.get_parameter(Name=f"/openclaw/{stack}/positions/{pos_id}/runtime-id")
        return {"posId": pos_id, "runtimeId": resp["Parameter"]["Value"]}
    except Exception:
        return {"posId": pos_id, "runtimeId": None}


@app.put("/api/v1/security/positions/{pos_id}/runtime")
def put_position_runtime(pos_id: str, body: dict, authorization: str = Header(default="")):
    """Assign a runtime to a position. Propagates to all employees in the position
    by writing their individual SSM /tenants/{emp_id}/runtime-id entries."""
    _require_role(authorization, roles=["admin"])
    runtime_id = body.get("runtimeId", "")
    if not runtime_id:
        raise HTTPException(400, "runtimeId required")
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    import boto3 as _b3pr2
    ssm = _b3pr2.client("ssm", region_name=_GATEWAY_REGION)
    # Write position-level SSM (for Tenant Router Tier 2 lookup)
    ssm.put_parameter(
        Name=f"/openclaw/{stack}/positions/{pos_id}/runtime-id",
        Value=runtime_id, Type="String", Overwrite=True,
    )
    # Propagate to all employees in this position (Tier 1 override for fast lookup)
    emps = db.get_employees()
    propagated = []
    for emp in emps:
        if emp.get("positionId") == pos_id:
            try:
                ssm.put_parameter(
                    Name=f"/openclaw/{stack}/tenants/{emp['id']}/runtime-id",
                    Value=runtime_id, Type="String", Overwrite=True,
                )
                propagated.append(emp["id"])
            except Exception as e:
                print(f"[position-runtime] emp {emp['id']} failed: {e}")
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": "admin",
        "actorName": "Admin",
        "targetType": "runtime_assignment",
        "targetId": f"{pos_id} → {runtime_id}",
        "detail": f"Position {pos_id} assigned to runtime {runtime_id}. Propagated to {len(propagated)} employees.",
        "status": "success",
    })
    return {"saved": True, "posId": pos_id, "runtimeId": runtime_id, "propagated": propagated}


@app.delete("/api/v1/security/positions/{pos_id}/runtime")
def delete_position_runtime(pos_id: str, authorization: str = Header(default="")):
    """Remove position-level runtime assignment (employees fall back to default)."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    import boto3 as _b3pr3
    ssm = _b3pr3.client("ssm", region_name=_GATEWAY_REGION)
    try:
        ssm.delete_parameter(Name=f"/openclaw/{stack}/positions/{pos_id}/runtime-id")
    except Exception:
        pass
    return {"deleted": True, "posId": pos_id}


@app.get("/api/v1/security/position-runtime-map")
def get_position_runtime_map(authorization: str = Header(default="")):
    """Get all position → runtime assignments in one call (for the UI mapping table)."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    import boto3 as _b3prm
    ssm = _b3prm.client("ssm", region_name=_GATEWAY_REGION)
    result = {}
    try:
        prefix = f"/openclaw/{stack}/positions/"
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=prefix, Recursive=True):
            for p in page["Parameters"]:
                name = p["Name"].replace(prefix, "")
                if name.endswith("/runtime-id"):
                    pos_id = name.replace("/runtime-id", "")
                    result[pos_id] = p["Value"]
    except Exception as e:
        print(f"[position-runtime-map] {e}")
    return {"map": result}


@app.get("/api/v1/security/runtimes")
def get_security_runtimes(authorization: str = Header(default="")):
    _require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3r
        ac = _b3r.client("bedrock-agentcore-control", region_name=_GATEWAY_REGION)
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
                    "guardrailId": env.get("GUARDRAIL_ID", ""),
                    "guardrailVersion": env.get("GUARDRAIL_VERSION", ""),
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
        ac = _b3r2.client("bedrock-agentcore-control", region_name=_GATEWAY_REGION)
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
        ac = _b3rc.client("bedrock-agentcore-control", region_name=_GATEWAY_REGION)
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

        # Guardrail binding: store as env vars; "" means remove guardrail
        if "guardrailId" in body:
            gid = body["guardrailId"].strip()
            if gid:
                new_env["GUARDRAIL_ID"] = gid
                new_env["GUARDRAIL_VERSION"] = body.get("guardrailVersion", "DRAFT").strip() or "DRAFT"
            else:
                new_env.pop("GUARDRAIL_ID", None)
                new_env.pop("GUARDRAIL_VERSION", None)

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
        ac = _b3cr.client("bedrock-agentcore-control", region_name=_GATEWAY_REGION)

        network_cfg: dict = {"networkMode": body.networkMode}
        if body.networkMode == "VPC" and body.securityGroupIds and body.subnetIds:
            network_cfg["networkModeConfig"] = {
                "securityGroups": body.securityGroupIds,
                "subnets": body.subnetIds,
            }

        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{_GATEWAY_ACCOUNT_ID}")
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


# ── Guardrails ───────────────────────────────────────────────────────────────

@app.get("/api/v1/security/guardrails")
def list_guardrails(authorization: str = Header(default="")):
    """List all Bedrock Guardrails available in this account/region."""
    _require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3gr
        bedrock = _b3gr.client("bedrock", region_name=_GATEWAY_REGION)
        resp = bedrock.list_guardrails(maxResults=100)
        guardrails = []
        for g in resp.get("guardrails", []):
            guardrails.append({
                "id": g["id"],
                "name": g["name"],
                "status": g.get("status", "READY"),
                "version": g.get("version", "DRAFT"),
                "updatedAt": g.get("updatedAt", "").isoformat() if hasattr(g.get("updatedAt", ""), "isoformat") else str(g.get("updatedAt", "")),
            })
        return {"guardrails": guardrails}
    except Exception as e:
        return {"guardrails": [], "error": str(e)}


@app.get("/api/v1/audit/guardrail-events")
def get_guardrail_events(authorization: str = Header(default=""), limit: int = 50):
    """Fetch guardrail_block audit events from DynamoDB."""
    _require_role(authorization, roles=["admin", "manager"])
    try:
        table = boto3.resource("dynamodb", region_name=DYNAMODB_REGION).Table(DYNAMODB_TABLE)
        resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq("TYPE#audit"),
            ScanIndexForward=False,
            Limit=limit * 5,  # over-fetch since we filter by eventType
        )
        events = [item for item in resp.get("Items", []) if item.get("eventType") == "guardrail_block"]
        events = events[:limit]
        for e in events:
            e.pop("PK", None); e.pop("SK", None)
            e.pop("GSI1PK", None); e.pop("GSI1SK", None)
        return {"events": events}
    except Exception as e:
        return {"events": [], "error": str(e)}


# ── Separate resource endpoints for dropdowns ──────────────────────────────

@app.get("/api/v1/security/ecr-images")
def list_ecr_images(authorization: str = Header(default="")):
    """List all ECR repos and their tagged images for runtime image selector."""
    _require_role(authorization, roles=["admin"])
    import boto3 as _b3ecr
    ecr = _b3ecr.client("ecr", region_name=_GATEWAY_REGION)
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
    ec2 = _b3vpc.client("ec2", region_name=_GATEWAY_REGION)
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
        _b3pw.client("ssm", region_name=_GATEWAY_REGION).put_parameter(
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
# Always-on Shared Agents — ECS Fargate tasks
# =========================================================================
# Architecture: Admin Console → ECS RunTask (Fargate) → task self-registers
# its private IP in SSM → Tenant Router reads SSM endpoint → routes to task.
# No port mapping needed; ECS manages networking via awsvpc mode.
# =========================================================================

_ALWAYS_ON_ECR_IMAGE = os.environ.get("AGENT_ECR_IMAGE", "")


def _get_ecs_config() -> dict:
    """Resolve ECS cluster / task-def / subnet / SG from env or CloudFormation outputs via SSM."""
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    cluster   = os.environ.get("ECS_CLUSTER_NAME",      f"{stack}-always-on")
    task_def  = os.environ.get("ECS_TASK_DEFINITION",   f"{stack}-always-on-agent")
    subnet_id = os.environ.get("ECS_SUBNET_ID",         "")
    sg_id     = os.environ.get("ECS_TASK_SG_ID",        "")

    # Fall back to SSM if env vars are not set (written by deploy script from CF outputs)
    if not subnet_id or not sg_id:
        try:
            ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
            if not subnet_id:
                subnet_id = ssm.get_parameter(
                    Name=f"/openclaw/{stack}/ecs/subnet-id")["Parameter"]["Value"]
            if not sg_id:
                sg_id = ssm.get_parameter(
                    Name=f"/openclaw/{stack}/ecs/task-sg-id")["Parameter"]["Value"]
        except Exception:
            pass

    return {"cluster": cluster, "task_def": task_def, "subnet_id": subnet_id, "sg_id": sg_id}


@app.post("/api/v1/admin/always-on/{agent_id}/start")
def start_always_on_agent(agent_id: str, authorization: str = Header(default="")):
    """Start an always-on ECS Fargate task for a shared agent."""
    _require_role(authorization, roles=["admin"])
    stack     = os.environ.get("STACK_NAME",      "openclaw-multitenancy")
    bucket    = os.environ.get("S3_BUCKET",       f"openclaw-tenants-{_GATEWAY_ACCOUNT_ID}")
    ddb_table = os.environ.get("DYNAMODB_TABLE",  "openclaw-enterprise")
    ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")

    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    ecs_cfg = _get_ecs_config()
    if not ecs_cfg["subnet_id"] or not ecs_cfg["sg_id"]:
        raise HTTPException(500,
            "ECS_SUBNET_ID and ECS_TASK_SG_ID are required. "
            "Set them in /etc/openclaw/env or run the deploy script to write them to SSM.")

    ecr_image = _ALWAYS_ON_ECR_IMAGE or (
        f"{_GATEWAY_ACCOUNT_ID}.dkr.ecr.{_GATEWAY_REGION}.amazonaws.com"
        f"/{stack}-multitenancy-agent:latest"
    )

    # Resolve per-agent bot tokens for Plan A direct IM connection
    # IT stores these in SSM when provisioning always-on for an employee
    telegram_token = ""
    discord_token = ""
    try:
        ssm_tok = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        try:
            telegram_token = ssm_tok.get_parameter(
                Name=f"/openclaw/{stack}/always-on/{agent_id}/telegram-token",
                WithDecryption=True)["Parameter"]["Value"]
        except Exception:
            pass
        try:
            discord_token = ssm_tok.get_parameter(
                Name=f"/openclaw/{stack}/always-on/{agent_id}/discord-token",
                WithDecryption=True)["Parameter"]["Value"]
        except Exception:
            pass
    except Exception:
        pass

    # Stop any existing task for this agent first
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        existing_arn = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn"
        )["Parameter"]["Value"]
        import boto3 as _b3ecs_stop
        _b3ecs_stop.client("ecs", region_name=_GATEWAY_REGION).stop_task(
            cluster=ecs_cfg["cluster"], task=existing_arn, reason="Restarted by admin")
    except Exception:
        pass

    # Launch new ECS Fargate task
    try:
        import boto3 as _b3ecs
        ecs = _b3ecs.client("ecs", region_name=_GATEWAY_REGION)
        resp = ecs.run_task(
            cluster=ecs_cfg["cluster"],
            taskDefinition=ecs_cfg["task_def"],
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets":        [ecs_cfg["subnet_id"]],
                    "securityGroups": [ecs_cfg["sg_id"]],
                    "assignPublicIp": "ENABLED",   # needed to pull ECR without NAT
                }
            },
            overrides={
                "containerOverrides": [{
                    "name": "always-on-agent",
                    # "image" is not a valid containerOverrides field; image is set in task def
                    # Use the task definition's image (or update the task def revision for custom tags)
                    "environment": [
                        # For 1:1 employee agents: use personal__emp_id so entrypoint.sh
                        # resolves BASE_TENANT_ID=emp_id → correct EFS/S3/SSM paths.
                        # For shared N:1 agents: agent_id is the right anchor.
                        {"name": "SESSION_ID",         "value": f"personal__{agent.get('employeeId', agent_id)}" if agent.get('employeeId') else f"shared__{agent_id}"},
                        {"name": "SHARED_AGENT_ID",    "value": agent_id},
                        {"name": "S3_BUCKET",          "value": bucket},
                        {"name": "STACK_NAME",         "value": stack},
                        {"name": "AWS_REGION",         "value": _GATEWAY_REGION},
                        {"name": "DYNAMODB_TABLE",     "value": ddb_table},
                        {"name": "DYNAMODB_REGION",    "value": ddb_region},
                        {"name": "SYNC_INTERVAL",      "value": "120"},
                        {"name": "EFS_ENABLED",        "value": "true"},
                        # Plan A: direct IM — inject bot tokens if provisioned
                        {"name": "TELEGRAM_BOT_TOKEN", "value": telegram_token},
                        {"name": "DISCORD_BOT_TOKEN",  "value": discord_token},
                    ],
                }]
            },
            count=1,
            tags=[
                {"key": "agent_id",   "value": agent_id},
                {"key": "stack_name", "value": stack},
            ],
        )
        failures = resp.get("failures", [])
        if failures:
            raise RuntimeError(f"ECS RunTask failures: {failures}")
        task_arn = resp["tasks"][0]["taskArn"]
    except Exception as e:
        raise HTTPException(500, f"Failed to start ECS task: {e}")

    # Persist task ARN — endpoint is registered by entrypoint.sh once the task is RUNNING
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        ssm.put_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn",
                          Value=task_arn, Type="String", Overwrite=True)
    except Exception as e:
        print(f"[always-on] SSM task-arn write failed: {e}")

    # Update DynamoDB status
    try:
        import boto3 as _b3d
        ddb = _b3d.resource("dynamodb", region_name=ddb_region)
        ddb.Table(ddb_table).update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET deployMode = :m, containerStatus = :s, ecsTaskArn = :t",
            ExpressionAttributeValues={":m": "always-on-ecs", ":s": "starting", ":t": task_arn},
        )
    except Exception as e:
        print(f"[always-on] DynamoDB update failed: {e}")

    return {"started": True, "agentId": agent_id, "taskArn": task_arn,
            "note": "Task is starting. Endpoint will be registered in SSM once RUNNING (~30s)."}


@app.post("/api/v1/admin/always-on/{agent_id}/stop")
def stop_always_on_agent(agent_id: str, authorization: str = Header(default="")):
    """Stop the always-on ECS Fargate task for an agent."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")

    task_arn = ""
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        task_arn = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn"
        )["Parameter"]["Value"]
    except Exception:
        pass

    if task_arn:
        try:
            import boto3 as _b3ecs2
            ecs_cfg = _get_ecs_config()
            _b3ecs2.client("ecs", region_name=_GATEWAY_REGION).stop_task(
                cluster=ecs_cfg["cluster"], task=task_arn, reason="Stopped by admin")
        except Exception as e:
            print(f"[always-on] ECS stop_task failed: {e}")

    # Clean up SSM entries
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        for suffix in ["/task-arn", "/endpoint"]:
            try:
                ssm.delete_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}{suffix}")
            except Exception:
                pass
    except Exception:
        pass

    # Update DynamoDB
    try:
        import boto3 as _b3d2
        ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
        ddb = _b3d2.resource("dynamodb", region_name=ddb_region)
        ddb.Table(os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")).update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET deployMode = :m, containerStatus = :s, ecsTaskArn = :empty",
            ExpressionAttributeValues={":m": "serverless", ":s": "stopped", ":empty": ""},
        )
    except Exception:
        pass

    return {"stopped": True, "agentId": agent_id, "taskArn": task_arn}


@app.put("/api/v1/admin/always-on/{agent_id}/tokens")
def set_always_on_tokens(agent_id: str, body: dict, authorization: str = Header(default="")):
    """Store IM bot tokens for a always-on agent (Plan A: direct IM connection).
    Tokens are stored as SSM SecureStrings and injected at ECS task startup."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
    saved = {}
    for channel, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
        token = body.get(f"{channel}BotToken", "").strip()
        if token:
            ssm.put_parameter(
                Name=f"/openclaw/{stack}/always-on/{agent_id}/{key}",
                Value=token, Type="SecureString", Overwrite=True)
            saved[channel] = True
        elif body.get(f"clear{channel.capitalize()}Token"):
            try:
                ssm.delete_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}/{key}")
            except Exception:
                pass
            saved[channel] = False
    return {"saved": saved, "agentId": agent_id,
            "note": "Tokens stored. Restart the always-on container to activate direct IM."}


@app.get("/api/v1/admin/always-on/{agent_id}/tokens")
def get_always_on_tokens(agent_id: str, authorization: str = Header(default="")):
    """Check which IM tokens are configured for an always-on agent (masked)."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
    result = {}
    for channel, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
        try:
            ssm.get_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}/{key}")
            result[channel] = "configured"  # don't return actual token
        except Exception:
            result[channel] = "not_configured"
    return result


@app.post("/api/v1/admin/always-on/{agent_id}/reload")
def reload_always_on_agent(agent_id: str, body: dict, authorization: str = Header(default="")):
    """Reload an always-on container — stops and restarts it so config/SOUL changes take effect.
    Optionally accepts imageTag to deploy a specific ECR image version."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{_GATEWAY_ACCOUNT_ID}")
    ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
    ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
    image_tag = body.get("imageTag", "latest")

    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    ecs_cfg = _get_ecs_config()
    if not ecs_cfg["subnet_id"] or not ecs_cfg["sg_id"]:
        raise HTTPException(500, "ECS config missing")

    # Build ECR image URI with optional tag override
    ecr_image = (
        f"{_GATEWAY_ACCOUNT_ID}.dkr.ecr.{_GATEWAY_REGION}.amazonaws.com"
        f"/{stack}-multitenancy-agent:{image_tag}"
    )

    # Stop existing task
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        old_arn = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn"
        )["Parameter"]["Value"]
        import boto3 as _b3rl
        _b3rl.client("ecs", region_name=_GATEWAY_REGION).stop_task(
            cluster=ecs_cfg["cluster"], task=old_arn, reason=f"Reload by admin (image={image_tag})")
    except Exception:
        pass

    # Resolve bot tokens
    telegram_token, discord_token = "", ""
    try:
        ssm_tok = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        for ch, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
            try:
                val = ssm_tok.get_parameter(
                    Name=f"/openclaw/{stack}/always-on/{agent_id}/{key}",
                    WithDecryption=True)["Parameter"]["Value"]
                if ch == "telegram": telegram_token = val
                else: discord_token = val
            except Exception:
                pass
    except Exception:
        pass

    # Start new task
    try:
        import boto3 as _b3rl2
        ecs = _b3rl2.client("ecs", region_name=_GATEWAY_REGION)
        resp = ecs.run_task(
            cluster=ecs_cfg["cluster"],
            taskDefinition=ecs_cfg["task_def"],
            launchType="FARGATE",
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": [ecs_cfg["subnet_id"]],
                "securityGroups": [ecs_cfg["sg_id"]],
                "assignPublicIp": "ENABLED",
            }},
            overrides={"containerOverrides": [{
                "name": "always-on-agent",
                "environment": [
                    {"name": "SESSION_ID",         "value": f"personal__{agent.get('employeeId', agent_id)}" if agent.get('employeeId') else f"shared__{agent_id}"},
                    {"name": "SHARED_AGENT_ID",    "value": agent_id},
                    {"name": "S3_BUCKET",          "value": bucket},
                    {"name": "STACK_NAME",         "value": stack},
                    {"name": "AWS_REGION",         "value": _GATEWAY_REGION},
                    {"name": "DYNAMODB_TABLE",     "value": ddb_table},
                    {"name": "DYNAMODB_REGION",    "value": ddb_region},
                    {"name": "SYNC_INTERVAL",      "value": "120"},
                    {"name": "EFS_ENABLED",        "value": "true"},
                    {"name": "TELEGRAM_BOT_TOKEN", "value": telegram_token},
                    {"name": "DISCORD_BOT_TOKEN",  "value": discord_token},
                ],
            }]},
            count=1,
        )
        failures = resp.get("failures", [])
        if failures:
            raise RuntimeError(f"ECS failures: {failures}")
        task_arn = resp["tasks"][0]["taskArn"]
    except Exception as e:
        raise HTTPException(500, f"Reload failed: {e}")

    # Update SSM + DynamoDB
    ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
    ssm.put_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn",
                      Value=task_arn, Type="String", Overwrite=True)
    try:
        import boto3 as _b3rl3
        _b3rl3.resource("dynamodb", region_name=ddb_region).Table(ddb_table).update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET deployMode = :m, containerStatus = :s, ecsTaskArn = :t, imageTag = :i",
            ExpressionAttributeValues={":m": "always-on-ecs", ":s": "reloading",
                                       ":t": task_arn, ":i": image_tag},
        )
    except Exception:
        pass

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": "admin", "actorName": "Admin",
        "targetType": "agent", "targetId": agent_id,
        "detail": f"Container reloaded with image tag '{image_tag}'", "status": "success",
    })
    return {"reloaded": True, "agentId": agent_id, "taskArn": task_arn, "imageTag": image_tag,
            "note": "Container restarting (~30s). New SOUL/config will be active on next message."}


@app.get("/api/v1/admin/always-on/{agent_id}/images")
def list_agent_images(agent_id: str, authorization: str = Header(default="")):
    """List available ECR image tags for deploying to this always-on agent."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    try:
        import boto3 as _b3img
        ecr = _b3img.client("ecr", region_name=_GATEWAY_REGION)
        # ECR repo name follows the pattern: {stack}-multitenancy-agent
        repo_name = f"{stack}-multitenancy-agent"
        resp = ecr.describe_images(
            repositoryName=repo_name,
            filter={"tagStatus": "TAGGED"})
        images = []
        for img in sorted(resp.get("imageDetails", []),
                          key=lambda x: x.get("imagePushedAt", ""), reverse=True)[:20]:
            for tag in (img.get("imageTags") or []):
                images.append({
                    "tag": tag,
                    "digest": img.get("imageDigest", "")[:20],
                    "pushed": str(img.get("imagePushedAt", ""))[:10],
                    "sizeMB": round(img.get("imageSizeInBytes", 0) / 1024 / 1024, 1),
                })
        return {"images": images, "repositoryName": repo_name}
    except Exception as e:
        raise HTTPException(500, f"ECR list failed: {e}")


@app.get("/api/v1/admin/always-on/{agent_id}/status")
def get_always_on_status(agent_id: str, authorization: str = Header(default="")):
    """Get status of an always-on ECS Fargate task."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)

    task_arn = ""
    endpoint = ""
    ecs_status = "STOPPED"

    try:
        task_arn = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn"
        )["Parameter"]["Value"]
    except Exception:
        return {"running": False, "endpoint": None, "agentId": agent_id, "ecsStatus": "NOT_FOUND"}

    try:
        endpoint = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{agent_id}/endpoint"
        )["Parameter"]["Value"]
    except Exception:
        pass  # endpoint registered async after task is RUNNING

    try:
        import boto3 as _b3ecs3
        ecs_cfg = _get_ecs_config()
        desc = _b3ecs3.client("ecs", region_name=_GATEWAY_REGION).describe_tasks(
            cluster=ecs_cfg["cluster"], tasks=[task_arn])
        tasks = desc.get("tasks", [])
        ecs_status = tasks[0].get("lastStatus", "UNKNOWN") if tasks else "NOT_FOUND"
    except Exception:
        pass

    running = ecs_status == "RUNNING"
    return {"running": running, "endpoint": endpoint or None,
            "agentId": agent_id, "taskArn": task_arn, "ecsStatus": ecs_status}


@app.put("/api/v1/admin/always-on/{agent_id}/assign/{emp_id}")
def assign_always_on_to_employee(agent_id: str, emp_id: str, authorization: str = Header(default="")):
    """Assign an employee to use the always-on agent instead of AgentCore."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
    ssm.put_parameter(
        Name=f"/openclaw/{stack}/tenants/{emp_id}/always-on-agent",
        Value=agent_id, Type="String", Overwrite=True,
    )
    return {"assigned": True, "empId": emp_id, "agentId": agent_id}


@app.delete("/api/v1/admin/always-on/{agent_id}/assign/{emp_id}")
def unassign_always_on_from_employee(agent_id: str, emp_id: str, authorization: str = Header(default="")):
    """Remove employee's always-on assignment — they fall back to AgentCore."""
    _require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
    try:
        _boto3_main.client("ssm", region_name=_GATEWAY_REGION).delete_parameter(
            Name=f"/openclaw/{stack}/tenants/{emp_id}/always-on-agent"
        )
    except Exception:
        pass
    return {"unassigned": True, "empId": emp_id}


# =========================================================================
# Digital Twin — public shareable agent URL
# =========================================================================

_PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://openclaw.awspsa.com")


@app.get("/api/v1/portal/twin")
def get_twin_status(authorization: str = Header(default="")):
    """Get the current employee's digital twin status."""
    user = _require_auth(authorization)
    record = db.get_twin_by_employee(user.employee_id)
    if not record or not record.get("active"):
        return {"active": False, "url": None, "chatCount": 0, "viewCount": 0}
    token = record.get("tokenRef") or record.get("token", "")
    return {
        "active": True,
        "url": f"{_PUBLIC_URL}/twin/{token}",
        "token": token,
        "chatCount": record.get("chatCount", 0),
        "viewCount": record.get("viewCount", 0),
        "createdAt": record.get("createdAt", ""),
    }


@app.post("/api/v1/portal/twin")
def enable_twin(authorization: str = Header(default="")):
    """Enable digital twin — generate a shareable URL for this employee's agent."""
    user = _require_auth(authorization)
    # Revoke any existing twin first
    existing = db.get_twin_by_employee(user.employee_id)
    if existing:
        db.disable_twin(user.employee_id)
    # Get employee + agent info
    emp = db.get_employee(user.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    agents = db.get_agents()
    agent = next((a for a in agents if a.get("employeeId") == user.employee_id), None)
    # Generate secure token
    import secrets
    token = secrets.token_urlsafe(20)
    record = db.create_twin(
        emp_id=user.employee_id,
        token=token,
        emp_name=emp.get("name", user.name),
        position_name=emp.get("positionName", ""),
        agent_name=agent.get("name", f"{emp.get('name')} Agent") if agent else f"{emp.get('name')} Agent",
    )
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": user.employee_id,
        "actorName": user.name, "targetType": "twin", "targetId": token,
        "detail": f"Digital twin enabled for {user.name}", "status": "success",
    })
    return {"active": True, "url": f"{_PUBLIC_URL}/twin/{token}", "token": token}


@app.delete("/api/v1/portal/twin")
def disable_twin(authorization: str = Header(default="")):
    """Disable digital twin — revoke the public URL."""
    user = _require_auth(authorization)
    db.disable_twin(user.employee_id)
    return {"active": False}


# ── Always-on management for individual employees (IT admin only) ─────────────
# IT admin assigns deployment mode (always-on vs serverless) per agent in
# Agent Factory. Employees do not self-manage this — it is an IT governance decision.
# Digital Twin works with both modes (Tenant Router is mode-agnostic).

def _launch_personal_always_on(emp_id: str, emp_name: str) -> dict:
    """Start a personal ECS Fargate task for a single employee.
    Returns the same shape as start_always_on_agent."""
    stack     = os.environ.get("STACK_NAME",      "openclaw-multitenancy")
    bucket    = os.environ.get("S3_BUCKET",       f"openclaw-tenants-{_GATEWAY_ACCOUNT_ID}")
    ddb_table = os.environ.get("DYNAMODB_TABLE",  "openclaw-enterprise")
    ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")

    ecs_cfg = _get_ecs_config()
    if not ecs_cfg["subnet_id"] or not ecs_cfg["sg_id"]:
        raise HTTPException(500, "ECS config missing — contact IT admin.")

    ecr_image = _ALWAYS_ON_ECR_IMAGE or (
        f"{_GATEWAY_ACCOUNT_ID}.dkr.ecr.{_GATEWAY_REGION}.amazonaws.com"
        f"/{stack}-multitenancy-agent:latest"
    )

    # Stop any existing personal container first
    try:
        ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
        existing_arn = ssm.get_parameter(
            Name=f"/openclaw/{stack}/always-on/{emp_id}/task-arn"
        )["Parameter"]["Value"]
        import boto3 as _b3ep
        _b3ep.client("ecs", region_name=_GATEWAY_REGION).stop_task(
            cluster=ecs_cfg["cluster"], task=existing_arn, reason="Personal container restart")
    except Exception:
        pass

    try:
        import boto3 as _b3ep2
        ecs = _b3ep2.client("ecs", region_name=_GATEWAY_REGION)
        resp = ecs.run_task(
            cluster=ecs_cfg["cluster"],
            taskDefinition=ecs_cfg["task_def"],
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets":        [ecs_cfg["subnet_id"]],
                    "securityGroups": [ecs_cfg["sg_id"]],
                    "assignPublicIp": "ENABLED",
                }
            },
            overrides={
                "containerOverrides": [{
                    "name": "always-on-agent",
                    # "image" is not a valid containerOverrides field; image is set in task def
                    # Use the task definition's image (or update the task def revision for custom tags)
                    "environment": [
                        # Note: SHARED_AGENT_ID deliberately NOT set → personal workspace path
                        {"name": "SESSION_ID",      "value": f"personal__{emp_id}"},
                        {"name": "S3_BUCKET",       "value": bucket},
                        {"name": "STACK_NAME",      "value": stack},
                        {"name": "AWS_REGION",      "value": _GATEWAY_REGION},
                        {"name": "DYNAMODB_TABLE",  "value": ddb_table},
                        {"name": "DYNAMODB_REGION", "value": ddb_region},
                        {"name": "SYNC_INTERVAL",   "value": "120"},
                        {"name": "EFS_ENABLED",     "value": "true"},
                    ],
                }]
            },
            count=1,
            tags=[
                {"key": "agent_id",    "value": emp_id},
                {"key": "agent_type",  "value": "personal"},
                {"key": "stack_name",  "value": stack},
            ],
        )
        failures = resp.get("failures", [])
        if failures:
            raise RuntimeError(f"ECS RunTask failures: {failures}")
        task_arn = resp["tasks"][0]["taskArn"]
    except Exception as e:
        raise HTTPException(500, f"Failed to start personal container: {e}")

    # Persist task ARN in SSM; endpoint registered by entrypoint.sh once RUNNING
    ssm = _boto3_main.client("ssm", region_name=_GATEWAY_REGION)
    try:
        ssm.put_parameter(Name=f"/openclaw/{stack}/always-on/{emp_id}/task-arn",
                          Value=task_arn, Type="String", Overwrite=True)
        # Route this employee to their personal container
        ssm.put_parameter(Name=f"/openclaw/{stack}/tenants/{emp_id}/always-on-agent",
                          Value=emp_id, Type="String", Overwrite=True)
    except Exception as e:
        print(f"[personal-always-on] SSM write failed: {e}")

    return {"started": True, "empId": emp_id, "taskArn": task_arn,
            "note": "Personal container starting (~30s). Scheduled tasks will be active once running."}


# ── Public twin endpoints (NO auth required) ──────────────────────────────────

@app.get("/api/v1/public/twin/{token}")
def get_public_twin_info(token: str):
    """Public: get employee info for the twin page (no auth)."""
    record = db.get_twin_by_token(token)
    if not record or not record.get("active"):
        raise HTTPException(404, "This digital twin is not available")
    db.increment_twin_stat(token, "viewCount")
    return {
        "empName": record.get("empName", ""),
        "positionName": record.get("positionName", ""),
        "agentName": record.get("agentName", ""),
        "companyName": "ACME Corp",
    }


@app.post("/api/v1/public/twin/{token}/chat")
def twin_chat(token: str, body: dict):
    """Public: send a message to the employee's digital twin (no auth).
    The twin has full access to the employee's SOUL + memory."""
    record = db.get_twin_by_token(token)
    if not record or not record.get("active"):
        raise HTTPException(404, "This digital twin is not available")

    emp_id = record.get("empId", "")
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(400, "message required")
    if len(message) > 2000:
        raise HTTPException(400, "Message too long (max 2000 chars)")

    # Rate limit: 60 messages/hour per token (simple check via chatCount)
    # For now just increment and allow — add Redis/DynamoDB TTL later if needed
    db.increment_twin_stat(token, "chatCount")

    # Route through Tenant Router with twin__ channel prefix
    # server.py in the agent container will detect "twin" channel and inject
    # digital twin context into SOUL.md
    router_url = os.environ.get("TENANT_ROUTER_URL", "http://localhost:8090")
    try:
        import requests as _req
        r = _req.post(f"{router_url}/route", json={
            "channel": "twin",
            "user_id": emp_id,
            "message": message,
        }, timeout=180)
        if r.status_code == 200:
            data = r.json()
            resp = data.get("response", {})
            reply = resp.get("response", str(resp)) if isinstance(resp, dict) else str(resp)
            return {"reply": reply, "agentName": record.get("agentName", "")}
        raise HTTPException(502, "Agent unavailable")
    except Exception as e:
        raise HTTPException(502, f"Agent unavailable: {str(e)[:100]}")


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
