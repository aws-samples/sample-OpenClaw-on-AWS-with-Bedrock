"""
Organization — departments, positions, employees, activity.

Endpoints: /api/v1/org/*
"""

import os
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header

import db
from shared import (
    require_auth, require_role, get_dept_scope,
    ssm_client, STACK_NAME, GATEWAY_REGION,
)

router = APIRouter(prefix="/api/v1/org", tags=["org"])


# ── Email 校验(用于员工 CRUD,SSO 登录匹配键) ────────────────────────────────
import re as _re
_EMAIL_RE = _re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _validate_email_field(body: dict, exclude_emp_id: str | None = None) -> None:
    """
    Email 是可选的;但如果 body 里有 email 字段,必须合法且在组织内唯一。
    成功时将 email 规范化为小写写回 body。

    exclude_emp_id: 更新场景下,传入当前被更新员工的 id,允许其保留自己的 email
                    不被判为重复。
    """
    if "email" not in body:
        return
    email_raw = body.get("email")
    if email_raw is None:
        return
    email = str(email_raw).strip().lower()
    if not email:
        # 允许 PUT 清空 email(员工不再做 SSO 登录)
        body["email"] = ""
        return
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, f"email format is invalid: {email}")
    # 唯一性检查
    existing = db.get_employee_by_email(email)
    if existing and existing.get("id") != exclude_emp_id:
        raise HTTPException(
            409,
            f"email '{email}' is already used by employee {existing.get('id')}",
        )
    body["email"] = email  # 规范化为小写


def _get_current_user(authorization: str):
    """Extract current user, returns None if not authenticated."""
    try:
        return require_auth(authorization)
    except Exception:
        return None


# ── Departments ──────────────────────────────────────────────────────────

@router.get("/departments")
def get_departments(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    depts = db.get_departments()
    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            depts = [d for d in depts if d["id"] in scope]
    return depts


@router.post("/departments")
def create_department(body: dict, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    return db.create_department(body)


@router.put("/departments/{dept_id}")
def update_department(dept_id: str, body: dict, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    body.pop("id", None)
    result = db.update_department(dept_id, body)
    if not result:
        raise HTTPException(404, f"Department {dept_id} not found")
    return result


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: str, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
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
    positions = db.get_positions()
    dept_positions = [p for p in positions if p.get("departmentId") == dept_id]
    if dept_positions:
        raise HTTPException(409, {
            "error": "department_has_positions",
            "count": len(dept_positions),
            "names": [p["name"] for p in dept_positions[:5]],
            "message": f"{len(dept_positions)} position(s) belong to this department. Reassign them first.",
        })
    db.delete_department(dept_id)
    return {"ok": True, "deleted": dept_id}


# ── Positions ────────────────────────────────────────────────────────────

@router.get("/positions")
def get_positions(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    positions = db.get_positions()
    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            positions = [p for p in positions if p.get("departmentId") in scope]
    return positions


@router.post("/positions")
def create_position(body: dict):
    return db.create_position(body)


@router.put("/positions/{pos_id}")
def update_position(pos_id: str, body: dict):
    body.pop("id", None)
    result = db.update_position(pos_id, body)
    if result:
        return result
    body["id"] = pos_id
    return db.create_position(body)


@router.delete("/positions/{pos_id}")
def delete_position(pos_id: str, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
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


# ── Employees ────────────────────────────────────────────────────────────

@router.get("/employees")
def get_employees(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    employees = db.get_employees()
    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            employees = [e for e in employees if e.get("departmentId") in scope]
    return employees


@router.post("/employees")
def create_employee(body: dict):
    """Create or update an employee. Auto-provisions agent + bindings if
    the employee has a positionId but no agentId (new hire flow).

    Email is optional but,if provided,必须合法且在组织内唯一(用于 SSO 登录匹配)。
    """
    _validate_email_field(body, exclude_emp_id=None)
    body.setdefault("mustChangePassword", True)
    result = db.create_employee(body)
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


@router.put("/employees/{emp_id}")
def update_employee(emp_id: str, body: dict, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    body.pop("id", None)
    body.pop("passwordHash", None)
    _validate_email_field(body, exclude_emp_id=emp_id)

    # Detect position change — if employee has always-on, warn about restart needed
    old_emp = db.get_employee(emp_id)
    position_changed = False
    if old_emp and body.get("positionId") and body["positionId"] != old_emp.get("positionId"):
        position_changed = True

    result = db.update_employee(emp_id, body)
    if not result:
        raise HTTPException(404, f"Employee {emp_id} not found")

    warning = None
    if position_changed and old_emp.get("alwaysOnEnabled"):
        warning = (
            f"Position changed. Always-on container needs restart "
            f"(new tier may have different model, guardrail, IAM role, and security group). "
            f"Go to Agent Factory → {emp_id} → Restart."
        )

    resp = dict(result)
    if warning:
        resp["warning"] = warning
    return resp


@router.delete("/employees/{emp_id}")
def delete_employee(emp_id: str, force: bool = False, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
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
    # Get agent info before deletion
    emp = db.get_employee(emp_id)
    agent_id = emp.get("agentId") if emp else None

    if force:
        for b in bindings:
            db.delete_binding(b["id"])
        for m in im_mappings:
            db.delete_user_mapping(m["channel"], m["channelUserId"])
        # Cascade: delete agent + S3 workspace + always-on infrastructure
        if agent_id:
            db.delete_agent(agent_id)
            # S3 workspace cleanup
            try:
                import boto3 as _b3del
                s3_bucket = os.environ.get("S3_BUCKET", "")
                if s3_bucket:
                    s3 = _b3del.client("s3")
                    resp = s3.list_objects_v2(Bucket=s3_bucket, Prefix=f"{emp_id}/workspace/", MaxKeys=200)
                    for obj in resp.get("Contents", []):
                        s3.delete_object(Bucket=s3_bucket, Key=obj["Key"])
            except Exception:
                pass
            # Always-on cleanup: stop ECS Service + delete Access Point + delete EFS dir + delete SSM
            if emp and emp.get("alwaysOnEnabled"):
                try:
                    from routers.admin_always_on import stop_always_on_agent
                    stop_always_on_agent(agent_id, authorization=authorization)
                except Exception as _e_ao:
                    print(f"[delete-emp] ECS stop failed: {_e_ao}")
                # Delete EFS Access Point
                try:
                    ap_id = emp.get("alwaysOnAccessPointId", "")
                    if ap_id:
                        _b3del.client("efs", region_name=os.environ.get("AWS_REGION", "us-east-1")).delete_access_point(AccessPointId=ap_id)
                except Exception:
                    pass
                # Delete EFS workspace directory
                import shutil as _shutil
                efs_path = f"/mnt/efs/{emp_id}"
                if os.path.isdir(efs_path):
                    _shutil.rmtree(efs_path, ignore_errors=True)
                # Delete SSM endpoint + gateway-token
                try:
                    _ssm_del = _b3del.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
                    stack = os.environ.get("STACK_NAME", "openclaw")
                    for suffix in ["/endpoint", "/gateway-token", "/dashboard-token"]:
                        try:
                            _ssm_del.delete_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}{suffix}")
                        except Exception:
                            pass
                except Exception:
                    pass
    db.delete_employee(emp_id)
    # Audit
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "employee_deleted",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "employee",
        "targetId": emp_id,
        "detail": f"Deleted employee {emp_id} (bindings: {len(bindings)}, mappings: {len(im_mappings)}, agent: {agent_id or 'none'})",
        "status": "success",
    })
    return {"ok": True, "deleted": emp_id, "bindingsDeleted": len(bindings), "imMappingsDeleted": len(im_mappings), "agentDeleted": agent_id}


# ── Activity ─────────────────────────────────────────────────────────────

import time as _time_act
_activity_cache = {"data": None, "expires": 0}

@router.get("/employees/activity")
def get_employee_activities(authorization: str = Header(default="")):
    """Get activity data for all employees — seed records + session-derived for gaps.
    Cached for 30 seconds to avoid repeated full-table scans."""
    user = _get_current_user(authorization)

    if _activity_cache["data"] and _time_act.time() < _activity_cache["expires"]:
        activities = _activity_cache["data"]
        if user and user.role == "manager":
            scope = get_dept_scope(user)
            if scope is not None:
                employees = db.get_employees()
                emp_ids = {e["id"] for e in employees if e.get("departmentId") in scope}
                activities = [a for a in activities if a.get("employeeId") in emp_ids]
        return activities

    activities = db.get_activities()

    activity_map: dict = {a["employeeId"]: a for a in activities if a.get("employeeId")}

    try:
        all_sessions = db.get_sessions()
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()

        sessions_by_emp: dict = {}
        for s in all_sessions:
            eid = s.get("employeeId")
            if eid and eid != "unknown":
                sessions_by_emp.setdefault(eid, []).append(s)

        for eid, emp_sessions in sessions_by_emp.items():
            if eid in activity_map and activity_map[eid].get("source") != "seed":
                continue
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
        pass

    activities = list(activity_map.values())
    _activity_cache["data"] = activities
    _activity_cache["expires"] = _time_act.time() + 30

    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            employees = db.get_employees()
            emp_ids = {e["id"] for e in employees if e.get("departmentId") in scope}
            activities = [a for a in activities if a.get("employeeId") in emp_ids]
    return activities


@router.get("/employees/{emp_id}/activity")
def get_employee_activity(emp_id: str):
    """Get activity data for a single employee — derived from real SESSION# records."""
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
    activity = db.get_activity(emp_id)
    if not activity:
        return {"employeeId": emp_id, "messagesThisWeek": 0, "channelStatus": {}}
    return {**activity, "source": "seed"}


# ── Auto-Provision ───────────────────────────────────────────────────────

def _auto_provision_employee(emp: dict) -> dict | None:
    """Auto-create 1:1 agent + binding for a single employee based on position.
    Uses DynamoDB TransactWriteItems — all writes succeed or all fail.
    Returns dict with agentId if provisioned, None if skipped."""
    pos_id = emp.get("positionId", "")
    if not pos_id or emp.get("agentId"):
        return None

    positions = db.get_positions()
    pos = next((p for p in positions if p["id"] == pos_id), None)
    if not pos:
        return None

    now = datetime.now(timezone.utc).isoformat()
    default_channel = pos.get("defaultChannel", "portal")

    agent_id = f"agent-{pos_id.replace('pos-','')}-{emp['id'].replace('emp-','')}"
    agent_name = f"{pos.get('name','')} Agent - {emp['name']}"

    existing = db.get_agent(agent_id)
    if existing:
        return None

    agent_data = {
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

    binding_data = {
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

    emp["agentId"] = agent_id
    emp["agentStatus"] = "active"

    audit_data = {
        "timestamp": now,
        "eventType": "config_change",
        "actorId": "system",
        "actorName": "Auto-Provision",
        "targetType": "binding",
        "targetId": agent_id,
        "detail": f"Auto-provisioned {agent_name} for {emp['name']} ({pos.get('name','')})",
        "status": "success",
    }

    # Atomic write: AGENT# + BIND# + EMP# + AUDIT# + shared bindings
    # All succeed or all fail — no orphaned agents or bindings.
    ok = db.provision_employee_atomic(
        agent_data=agent_data,
        binding_data=binding_data,
        emp_update=emp,
        audit_data=audit_data,
    )
    if not ok:
        # Transaction failed — nothing was written. Clean rollback.
        emp.pop("agentId", None)
        emp.pop("agentStatus", None)
        return None

    return {"agentId": agent_id, "agentName": agent_name}
