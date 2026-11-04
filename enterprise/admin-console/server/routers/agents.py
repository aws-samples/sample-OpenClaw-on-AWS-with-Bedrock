"""
Agents — CRUD, SOUL (3-layer), Workspace, Memory, Skills.

Endpoints span multiple prefixes:
  /api/v1/agents/*
  /api/v1/workspace/*
  /api/v1/skills/*
"""

import os
import json
import threading
from datetime import datetime, timezone

import boto3
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
import s3ops
import auth as authmod
from shared import (
    require_auth, require_role,
    ssm_client, bump_config_version, audit_soul_change,
    GATEWAY_REGION, STACK_NAME, GATEWAY_ACCOUNT_ID,
    stop_employee_session, get_dept_scope,
)
import re as _re_agents
import time as _time_agents

router = APIRouter(tags=["agents"])


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

def _get_current_user(authorization: str):
    """Extract current user, returns None if not authenticated."""
    try:
        return require_auth(authorization)
    except Exception:
        return None


def _propagate_skill_key_change(skill_name: str) -> None:
    """Restart only running environments assigned the changed Skill credential."""
    positions = {position["id"]: position for position in db.get_positions() if position.get("id")}
    affected = [
        employee for employee in db.get_employees()
        if skill_name in employee.get("personalSkills", [])
        or skill_name in positions.get(employee.get("positionId", ""), {}).get("defaultSkills", [])
    ]
    stack = os.environ.get("STACK_NAME", "openclaw")
    cluster = os.environ.get("ECS_CLUSTER_NAME", f"{stack}-always-on")
    services: set[str] = set()

    for employee in affected:
        position = positions.get(employee.get("positionId", ""), {})
        agent = db.get_agent(employee.get("agentId", "")) or {}
        dedicated = employee.get("alwaysOnServiceName") or agent.get("ecsServiceName")
        is_dedicated = bool(employee.get("alwaysOnEnabled") or agent.get("deployMode") == "always-on-ecs")
        if is_dedicated and dedicated:
            services.add(dedicated)
            continue
        if position.get("deployMode") == "fargate":
            tier = position.get("fargateTier") or employee.get("alwaysOnTier") or "standard"
            services.add(f"{stack}-tier-{tier}")
            continue
        stop_employee_session(employee["id"])

    if services:
        ecs = boto3.client("ecs", region_name=GATEWAY_REGION)
        for service in sorted(services):
            try:
                response = ecs.describe_services(cluster=cluster, services=[service])
                active = [item for item in response.get("services", []) if item.get("status") == "ACTIVE"]
                if active and int(active[0].get("desiredCount", 0)) > 0:
                    ecs.update_service(cluster=cluster, service=service, forceNewDeployment=True)
            except Exception as exc:
                print(f"[skill-key] Could not restart ECS service {service}: {exc}")


def _resolve_agent_status(agent: dict) -> str:
    """Derive agent status from DynamoDB lastInvocationAt field.
    Replaces CloudWatch-based status detection (was 20+ API calls per page load)."""
    last = agent.get("lastInvocationAt", "")
    if not last:
        return agent.get("status", "idle")
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age < 900:
            return "active"
        elif age < 3600:
            return "idle"
        else:
            return "offline"
    except Exception:
        return agent.get("status", "idle")


# =========================================================================
# Agents CRUD
# =========================================================================

@router.get("/api/v1/agents")
def get_agents(authorization: str = Header(default="")):
    user = _get_current_user(authorization)
    agents = db.get_agents()
    positions = {p["id"]: p for p in db.get_positions()}
    employees = {e["id"]: e for e in db.get_employees()}

    # Skills are resolved dynamically from the authoritative assignment records.
    for agent in agents:
        agent.setdefault("channels", [])
        agent.setdefault("soulVersions", {})
        position_skills = positions.get(agent.get("positionId", ""), {}).get("defaultSkills", [])
        personal_skills = employees.get(agent.get("employeeId", ""), {}).get("personalSkills", [])
        agent["skills"] = list(dict.fromkeys([*position_skills, *personal_skills]))
        agent["status"] = _resolve_agent_status(agent)

    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            pos_in_scope = {p["id"] for p in positions.values() if p.get("departmentId") in scope}
            agents = [a for a in agents if a.get("positionId") in pos_in_scope or not a.get("employeeId")]
    return agents

@router.get("/api/v1/agents/{agent_id}")
def get_agent(agent_id: str):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    agent.setdefault("channels", [])
    agent.setdefault("skills", [])
    agent.setdefault("soulVersions", {})
    agent["status"] = _resolve_agent_status(agent)
    return agent

@router.post("/api/v1/agents")
def create_agent(body: dict):
    body.setdefault("status", "active")
    body.setdefault("soulVersions", {"global": 3, "position": 1, "personal": 0})
    body.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
    body.setdefault("updatedAt", body["createdAt"])

    emp_id = body.get("employeeId")
    pos_id = body.get("positionId", "")
    channel = body.get("defaultChannel", "discord")

    if not emp_id or not pos_id:
        # Simple agent creation without binding (rare: shared agent)
        agent = db.create_agent(body)
        return agent

    # Full provisioning: agent + binding + employee update + audit — atomic
    now = datetime.now(timezone.utc).isoformat()
    emp = next((e for e in db.get_employees() if e["id"] == emp_id), {})
    positions = db.get_positions()
    pos = next((p for p in positions if p["id"] == pos_id), {})
    deploy_mode = body.get("deployMode", "serverless")
    agent_id = body.get("id", f"agent-{int(__import__('time').time())}")
    body["id"] = agent_id

    binding_data = {
        "employeeId": emp_id,
        "employeeName": emp.get("name", ""),
        "agentId": agent_id,
        "agentName": body.get("name", ""),
        "mode": "1:1",
        "channel": channel,
        "status": "active",
        "source": "manual",
        "createdAt": now,
    }

    emp["agentId"] = agent_id
    emp["agentStatus"] = "active"

    audit_data = {
        "timestamp": now,
        "eventType": "config_change", "actorId": "admin", "actorName": "IT Admin",
        "targetType": "agent", "targetId": agent_id,
        "detail": f"Created agent '{body.get('name')}' for {emp.get('name', emp_id)} ({pos.get('name', pos_id)}) [{deploy_mode}]",
        "status": "success",
    }

    # Atomic write: AGENT# + BIND# + EMP# + AUDIT# in one DynamoDB transaction
    ok = db.provision_employee_atomic(
        agent_data=body,
        binding_data=binding_data,
        emp_update=emp,
        audit_data=audit_data,
    )
    if not ok:
        raise HTTPException(500, "Agent creation failed — transaction rolled back, no partial state.")

    # S3 workspace seeding (non-transactional — but DynamoDB state is consistent)
    # If S3 fails, agent exists in DynamoDB but workspace will be created on first invocation
    # by workspace_assembler.py (which seeds missing files automatically).
    region = os.environ.get("AWS_REGION", "us-east-1")
    s3_bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
    try:
        s3 = boto3.client("s3", region_name=region)
        prefix = f"{emp_id}/workspace/"
        resp = s3.list_objects_v2(Bucket=s3_bucket, Prefix=prefix, MaxKeys=5)
        if not resp.get("Contents"):
            emp_name = emp.get("name", emp_id)
            safe_name = _re_agents.sub(r'([#*_\[\]<>])', r'\\\1', emp_name)
            pos_name = pos.get("name", pos_id)
            dept = emp.get("departmentName", "")
            s3.put_object(Bucket=s3_bucket, Key=f"{prefix}IDENTITY.md",
                Body=f"# Agent Identity\n\n- **Name**: {safe_name} AI Assistant\n- **Position**: {pos_name}\n- **Department**: {dept}\n- **Company**: ACME Corp\n- **Platform**: OpenClaw Enterprise\n".encode())
            s3.put_object(Bucket=s3_bucket, Key=f"{prefix}MEMORY.md",
                Body=f"# Memory\nNo previous conversations recorded.\n".encode())
            s3.put_object(Bucket=s3_bucket, Key=f"{prefix}USER.md",
                Body=f"# User Profile\n\n- **Name**: {emp_name}\n- **Position**: {pos_name}\n- **Language**: English\n".encode())
            s3.put_object(Bucket=s3_bucket, Key=f"{prefix}PERSONAL_SOUL.md",
                Body=b"# Personal Preferences\n\n(Edit this to customize your AI agent behavior.)\n")
    except Exception as e:
        print(f"[create_agent] S3 seed failed (non-fatal, workspace_assembler will retry): {e}")

    if deploy_mode == "always-on-ecs":
        body["note"] = "Agent created with Always-on mode. Go to Agent Factory -> Always-on tab -> Start to launch the ECS container."

    return body


# =========================================================================
# SOUL -- Three-layer read/write with S3 versioning
# =========================================================================

@router.get("/api/v1/agents/{agent_id}/soul")
def get_agent_soul(agent_id: str, authorization: str = Header(default="")):
    """Get three-layer SOUL for an agent. Reads from S3."""
    require_auth(authorization)
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    pos_id = agent.get("positionId", "")
    emp_id = agent.get("employeeId")
    sv = agent.get("soulVersions", {})

    global_soul = s3ops.read_file("_shared/soul/global/SOUL.md") or ""
    position_soul = s3ops.read_file(f"_shared/soul/positions/{pos_id}/SOUL.md") or ""
    # Read personal layer from PERSONAL_SOUL.md (new purified design).
    # Falls back to SOUL.md for pre-migration workspaces.
    personal_soul = ""
    if emp_id:
        personal_soul = s3ops.read_file(f"{emp_id}/workspace/PERSONAL_SOUL.md") or ""
        if not personal_soul:
            # Fallback: try old SOUL.md (pre-purification workspace)
            old = s3ops.read_file(f"{emp_id}/workspace/SOUL.md") or ""
            if old and "<!-- LAYER: GLOBAL" not in old:
                personal_soul = old

    return [
        {"layer": "global", "content": global_soul, "locked": True, "version": sv.get("global", 3), "updatedAt": "2026-03-15T00:00:00Z"},
        {"layer": "position", "content": position_soul, "locked": False, "version": sv.get("position", 1), "updatedAt": "2026-03-18T00:00:00Z"},
        {"layer": "personal", "content": personal_soul or "", "locked": False, "version": sv.get("personal", 0), "updatedAt": "2026-03-19T00:00:00Z"},
    ]


class SoulSaveRequest(BaseModel):
    layer: str  # "position" or "personal"
    content: str
    expectedVersion: int | None = None  # for conflict detection

@router.put("/api/v1/agents/{agent_id}/soul")
def save_agent_soul(agent_id: str, body: SoulSaveRequest, authorization: str = Header(default="")):
    """Save a SOUL layer to S3. Increments version in DynamoDB. Audits the change."""
    user = require_role(authorization, roles=["admin", "manager"])
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if body.layer == "global":
        raise HTTPException(403, "Global layer is locked -- requires CISO + CTO approval")

    # Conflict detection: if client sends expectedVersion, verify it matches
    sv = agent.get("soulVersions", {})
    current_version = sv.get(body.layer, 0)
    if body.expectedVersion is not None and body.expectedVersion != current_version:
        raise HTTPException(409, "SOUL was modified by another session. Reload to see latest.")

    pos_id = agent.get("positionId", "")
    emp_id = agent.get("employeeId")

    if body.layer == "personal" and emp_id:
        s3ops.write_file(f"{emp_id}/workspace/PERSONAL_SOUL.md", body.content)
        result = {"key": f"{emp_id}/workspace/PERSONAL_SOUL.md"}
    else:
        result = s3ops.save_soul_layer(body.layer, pos_id, emp_id, "SOUL.md", body.content)
        if result.get("error"):
            raise HTTPException(400, result["error"])

    # Increment version
    sv[body.layer] = current_version + 1
    agent["soulVersions"] = sv
    agent["updatedAt"] = datetime.now(timezone.utc).isoformat()
    db.create_agent(agent)

    # Audit
    audit_soul_change(user, body.layer, agent_id, len(body.content))

    return {"saved": True, "layer": body.layer, "version": sv[body.layer], "s3Key": result.get("key")}


@router.get("/api/v1/agents/{agent_id}/soul/full")
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
# Workspace -- Full file tree with S3 read/write
# =========================================================================

@router.get("/api/v1/workspace/tree")
def get_workspace_tree(agent_id: str = ""):
    """Get the full workspace file tree for an agent."""
    agent = db.get_agent(agent_id) if agent_id else None
    pos_id = agent.get("positionId", "") if agent else ""
    emp_id = agent.get("employeeId") if agent else None
    return s3ops.get_workspace_tree(pos_id, emp_id)

@router.get("/api/v1/workspace/file")
def get_workspace_file(key: str, authorization: str = Header(default="")):
    """Read a single workspace file from S3. Admin/manager can read any key; employees only their own."""
    user = require_auth(authorization)
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

@router.put("/api/v1/workspace/file")
def save_workspace_file(body: FileWriteRequest, authorization: str = Header(default="")):
    """Write a workspace file to S3. Global layer locked; employees can only write their own files."""
    user = require_auth(authorization)
    if body.key.startswith("_shared/soul/global/"):
        raise HTTPException(403, "Global layer is locked")
    # Employees can only modify their own workspace files
    if user.role == "employee":
        if not body.key.startswith(f"{user.employee_id}/workspace/"):
            raise HTTPException(403, "Access denied: you can only modify your own workspace files")
    success = s3ops.write_file(body.key, body.content)
    if not success:
        raise HTTPException(500, "Failed to write file")

    # Auto-trigger session refresh when employee personal files change.
    # This ensures USER.md edits take effect immediately (not waiting for config_version poll).
    if "/workspace/USER.md" in body.key or "/workspace/SOUL.md" in body.key:
        import re as _re_ws
        m = _re_ws.match(r"(emp-[^/]+)/workspace/", body.key)
        if m:
            threading.Thread(target=stop_employee_session, args=(m.group(1),), daemon=True).start()

    return {"key": body.key, "saved": True, "size": len(body.content)}

@router.get("/api/v1/workspace/file/versions")
def get_file_versions(key: str):
    """List all versions of a workspace file."""
    return s3ops.list_versions(key)

@router.get("/api/v1/workspace/file/version")
def get_file_version(key: str, versionId: str):
    """Read a specific version of a workspace file."""
    content = s3ops.read_version(key, versionId)
    if content is None:
        raise HTTPException(404, "Version not found")
    return {"key": key, "versionId": versionId, "content": content}


# =========================================================================
# Memory -- Agent memory management
# =========================================================================

@router.get("/api/v1/agents/{agent_id}/memory")
def get_agent_memory(agent_id: str, authorization: str = Header(default="")):
    """Get memory overview for an agent."""
    require_auth(authorization)
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    emp_id = agent.get("employeeId")
    if not emp_id:
        return {"memoryMd": "", "memoryMdSize": 0, "dailyFiles": [], "totalDailyFiles": 0, "totalSize": 0, "note": "Shared agents don't have personal memory"}
    return s3ops.get_agent_memory(emp_id)

@router.get("/api/v1/agents/{agent_id}/memory/{date}")
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
# Skills -- reads from S3 _shared/skills/
# =========================================================================

@router.get("/api/v1/skills")
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
            try:
                manifest = json.loads(manifest_content)
                manifest.setdefault("status", "installed")
                manifest["id"] = f"sk-{name}"
                skills.append(manifest)
            except json.JSONDecodeError:
                pass
    return skills

@router.get("/api/v1/skills/{skill_name}")
def get_skill(skill_name: str):
    """Get a single skill manifest."""
    content = s3ops.read_file(f"_shared/skills/{skill_name}/skill.json")
    if not content:
        raise HTTPException(404, f"Skill {skill_name} not found")
    return json.loads(content)

_skill_keys_cache = {"data": None, "expires": 0}

@router.get("/api/v1/skills/keys/all")
def get_all_skill_keys(authorization: str = Header(default="")):
    """List required skill environment variables and their real SSM status."""
    require_role(authorization, roles=["admin"])
    if _skill_keys_cache["data"] is not None and _time_agents.time() < _skill_keys_cache["expires"]:
        return _skill_keys_cache["data"]

    files = s3ops.list_files("_shared/skills/")
    skill_names = {f["name"].split("/")[0] for f in files if f.get("name")}
    stack = os.environ.get("STACK_NAME", "openclaw")
    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    keys = []

    for name in sorted(skill_names):
        content = s3ops.read_file(f"_shared/skills/{name}/skill.json")
        if not content:
            continue
        try:
            manifest = json.loads(content)
        except json.JSONDecodeError:
            continue

        aws_service = manifest.get("awsService", "")
        for env_var in manifest.get("requires", {}).get("env", []):
            ssm_path = f"/openclaw/{stack}/skill-keys/{name}/{env_var}"
            last_rotated = ""
            if aws_service:
                status = "iam-role"
                note = f"Provided by IAM role ({aws_service})"
            else:
                try:
                    parameter = ssm.get_parameter(Name=ssm_path)["Parameter"]
                    status = "active"
                    note = "Stored securely in SSM Parameter Store"
                    modified = parameter.get("LastModifiedDate")
                    last_rotated = modified.isoformat() if modified else ""
                except ssm.exceptions.ParameterNotFound:
                    status = "not-configured"
                    note = "Needs configuration in API Key Vault"

            keys.append({
                "id": f"key-{len(keys) + 1}",
                "skillName": name,
                "envVar": env_var,
                "ssmPath": ssm_path,
                "status": status,
                "lastRotated": last_rotated,
                "createdBy": "",
                "awsService": aws_service,
                "note": note,
            })

    _skill_keys_cache["data"] = keys
    _skill_keys_cache["expires"] = _time_agents.time() + 60
    return keys


# ── Skill assignment ─────────────────────────────────────────────────────

@router.post("/api/v1/skills/{skill_name}/assign")
def assign_skill_to_position(skill_name: str, body: dict, authorization: str = Header(default="")):
    """Assign a skill to a position. Triggers config bump + audit + force refresh."""
    user = require_role(authorization, roles=["admin"])
    position_id = body.get("positionId", "")
    if not position_id:
        raise HTTPException(400, "positionId required")

    manifest_content = s3ops.read_file(f"_shared/skills/{skill_name}/skill.json")
    if not manifest_content:
        raise HTTPException(404, f"Skill {skill_name} not found in S3")

    pos = db.get_position(position_id)
    if not pos:
        raise HTTPException(404, f"Position {position_id} not found")

    skills = pos.get("defaultSkills", [])
    if skill_name not in skills:
        skills.append(skill_name)
        db.update_position(position_id, {"defaultSkills": skills})

    bump_config_version()
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "skill_assignment",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "position", "targetId": position_id,
        "detail": f"Assigned skill '{skill_name}' to {pos.get('name', position_id)}",
        "status": "success",
    })
    # Force refresh affected employees in background
    emps = [e for e in db.get_employees() if e.get("positionId") == position_id]
    for e in emps:
        threading.Thread(target=stop_employee_session, args=(e["id"],), daemon=True).start()

    return {"assigned": True, "positionId": position_id, "skill": skill_name,
            "agentsAffected": len(emps)}


@router.delete("/api/v1/skills/{skill_name}/assign")
def unassign_skill_from_position(skill_name: str, positionId: str = "", authorization: str = Header(default="")):
    """Remove a skill from a position. Triggers config bump + audit + force refresh."""
    user = require_role(authorization, roles=["admin"])
    if not positionId:
        raise HTTPException(400, "positionId query param required")

    pos = db.get_position(positionId)
    if not pos:
        raise HTTPException(404, f"Position {positionId} not found")

    skills = pos.get("defaultSkills", [])
    if skill_name in skills:
        skills.remove(skill_name)
        db.update_position(positionId, {"defaultSkills": skills})

    bump_config_version()
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "skill_assignment",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "position", "targetId": positionId,
        "detail": f"Removed skill '{skill_name}' from {pos.get('name', positionId)}",
        "status": "success",
    })
    emps = [e for e in db.get_employees() if e.get("positionId") == positionId]
    for e in emps:
        threading.Thread(target=stop_employee_session, args=(e["id"],), daemon=True).start()

    return {"unassigned": True, "positionId": positionId, "skill": skill_name,
            "agentsAffected": len(emps)}


@router.put("/api/v1/skills/keys/{skill_name}/{env_var}")
def set_skill_key(skill_name: str, env_var: str, body: dict, authorization: str = Header(default="")):
    """Store a manifest-declared skill credential in SSM SecureString."""
    user = require_role(authorization, roles=["admin"])
    value = body.get("value", "")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, "value is required")
    if len(value) > 4096:
        raise HTTPException(400, "value exceeds the 4096 character limit")

    manifest_content = s3ops.read_file(f"_shared/skills/{skill_name}/skill.json")
    if not manifest_content:
        raise HTTPException(404, f"Skill {skill_name} not found")
    try:
        manifest = json.loads(manifest_content)
    except json.JSONDecodeError:
        raise HTTPException(500, f"Skill {skill_name} has an invalid manifest")
    if env_var not in manifest.get("requires", {}).get("env", []):
        raise HTTPException(400, f"{env_var} is not declared by skill {skill_name}")
    if manifest.get("awsService"):
        raise HTTPException(400, "AWS-native skills use IAM roles and do not accept API keys")

    stack = os.environ.get("STACK_NAME", "openclaw")
    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    ssm.put_parameter(
        Name=f"/openclaw/{stack}/skill-keys/{skill_name}/{env_var}",
        Value=value, Type="SecureString", Overwrite=True)
    _skill_keys_cache["data"] = None
    _skill_keys_cache["expires"] = 0

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "skill_key", "targetId": f"{skill_name}/{env_var}",
        "detail": f"Configured API key {env_var} for skill {skill_name}",
        "status": "success",
    })
    threading.Thread(
        target=_propagate_skill_key_change,
        args=(skill_name,),
        daemon=True,
    ).start()
    return {
        "configured": True,
        "skill": skill_name,
        "envVar": env_var,
        "propagation": "Affected Serverless sessions and running Always-On services are restarting",
    }


# =========================================================================
# DELETE Agent — cascade: BIND#, S3 workspace, AUDIT#
# =========================================================================

@router.delete("/api/v1/agents/{agent_id}")
def delete_agent(agent_id: str, authorization: str = Header(default="")):
    """Delete an agent and cascade: remove bindings, clear employee link, delete S3 workspace."""
    user = require_role(authorization, roles=["admin"])
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    emp_id = agent.get("employeeId", "")

    # 1. Delete all bindings for this agent
    bindings = [b for b in db.get_bindings() if b.get("agentId") == agent_id]
    for b in bindings:
        db.delete_binding(b["id"])

    # 2. Delete agent record
    db.delete_agent(agent_id)

    # 3. Clear agentId from employee record
    if emp_id:
        emp = db.get_employee(emp_id)
        if emp:
            emp.pop("agentId", None)
            emp.pop("agentStatus", None)
            db.create_employee(emp)

    # 4. Delete S3 workspace (best-effort)
    if emp_id:
        try:
            s3 = boto3.client("s3")
            bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"{emp_id}/workspace/", MaxKeys=200)
            for obj in resp.get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
        except Exception as e:
            print(f"[delete_agent] S3 cleanup failed (non-fatal): {e}")

    # 5. Audit
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "agent_deleted",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "agent",
        "targetId": agent_id,
        "detail": f"Deleted agent {agent.get('name', agent_id)} (employee: {emp_id})",
        "status": "success",
    })

    return {"deleted": True, "agentId": agent_id, "bindingsDeleted": len(bindings)}


# =========================================================================
# Force Refresh — terminate agent session to trigger fresh assembly
# =========================================================================

@router.post("/api/v1/admin/refresh-agent/{emp_id}")
def refresh_agent(emp_id: str, authorization: str = Header(default="")):
    """Force terminate running agent session to trigger fresh workspace assembly.
    Used after SOUL edits, KB changes, or permission updates."""
    user = require_role(authorization, roles=["admin", "manager"])
    from datetime import datetime, timezone
    result = stop_employee_session(emp_id)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "agent_refresh",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "agent",
        "targetId": emp_id,
        "detail": f"Admin forced agent refresh for {emp_id}",
        "status": "success",
    })
    return {"refreshed": True, "emp_id": emp_id, "detail": result}


# =========================================================================
# Skill Submission + Review (Phase 3)
# =========================================================================

@router.post("/api/v1/portal/skills/submit")
def portal_submit_skill(body: dict, authorization: str = Header(default="")):
    """Employee submits a custom skill for review.
    Body: { name, description, author, category, skillJson (string), toolJs (string) }
    Files stored to S3 _pending/skills/{name}/, APPROVAL# created."""
    user = require_auth(authorization)
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    if not body.get("toolJs"):
        raise HTTPException(400, "toolJs (tool implementation code) is required")

    # Build manifest
    manifest = {
        "name": name,
        "version": body.get("version", "1.0.0"),
        "description": body.get("description", ""),
        "author": body.get("author", user.name),
        "layer": 2,
        "category": body.get("category", "custom"),
        "scope": "department",
        "requires": {"env": body.get("requiredEnv", []), "tools": body.get("requiredTools", [])},
        "permissions": {"allowedRoles": body.get("allowedRoles", ["*"]), "blockedRoles": []},
        "submittedBy": user.employee_id,
        "submittedAt": datetime.now(timezone.utc).isoformat(),
        "status": "under_review",
    }

    # Write to _pending/ in S3 (isolation area)
    s3ops.write_file(f"_pending/skills/{name}/skill.json", json.dumps(manifest, indent=2))
    s3ops.write_file(f"_pending/skills/{name}/tool.js", body["toolJs"])
    if body.get("setupGuide"):
        s3ops.write_file(f"_pending/skills/{name}/setup-guide.md", body["setupGuide"])

    # Create approval record
    approval_id = f"skill-submit-{name}-{int(_time_agents.time())}"
    db.create_approval({
        "id": approval_id,
        "type": "skill_submit",
        "status": "pending",
        "skillName": name,
        "submittedBy": user.employee_id,
        "submittedByName": user.name,
        "description": manifest["description"],
        "category": manifest["category"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "skill_submit",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "skill", "targetId": name,
        "detail": f"Submitted skill '{name}' for review",
        "status": "success",
    })

    return {"submitted": True, "skillName": name, "approvalId": approval_id}


@router.post("/api/v1/portal/skills/{skill_name}/request")
def portal_request_skill(skill_name: str, body: dict = {}, authorization: str = Header(default="")):
    """Employee requests access to an approved skill (adds to personalSkills)."""
    user = require_auth(authorization)
    reason = body.get("reason", f"Requesting access to skill: {skill_name}")

    # Verify skill exists in catalog
    manifest = s3ops.read_file(f"_shared/skills/{skill_name}/skill.json")
    if not manifest:
        raise HTTPException(404, f"Skill '{skill_name}' not found")

    approval_id = f"skill-req-{user.employee_id}-{skill_name}-{int(_time_agents.time())}"
    db.create_approval({
        "id": approval_id,
        "type": "skill_access_request",
        "status": "pending",
        "skillName": skill_name,
        "employeeId": user.employee_id,
        "employeeName": user.name,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "skill_access_request",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "skill", "targetId": skill_name,
        "detail": f"Requested access to skill '{skill_name}': {reason}",
        "status": "success",
    })

    return {"requested": True, "approvalId": approval_id, "skill": skill_name}


@router.get("/api/v1/tools-skills/pending")
def get_pending_skills(authorization: str = Header(default="")):
    """Admin: list skills pending review from _pending/ in S3."""
    require_role(authorization, roles=["admin"])
    files = s3ops.list_files("_pending/skills/")
    skill_names = set()
    for f in files:
        parts = f["name"].split("/")
        if parts[0]:
            skill_names.add(parts[0])

    pending = []
    for name in sorted(skill_names):
        manifest_content = s3ops.read_file(f"_pending/skills/{name}/skill.json")
        if manifest_content:
            try:
                manifest = json.loads(manifest_content)
                manifest["id"] = f"pending-{name}"
                manifest["hasTool"] = bool(s3ops.read_file(f"_pending/skills/{name}/tool.js"))
                pending.append(manifest)
            except json.JSONDecodeError:
                pass
    return pending


@router.post("/api/v1/tools-skills/{skill_name}/review")
def review_skill(skill_name: str, body: dict, authorization: str = Header(default="")):
    """Admin approves or rejects a submitted skill.
    Body: { action: "approve" | "reject", reason?: string }"""
    user = require_role(authorization, roles=["admin"])
    action = body.get("action", "")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")

    # Read pending manifest
    manifest_content = s3ops.read_file(f"_pending/skills/{skill_name}/skill.json")
    if not manifest_content:
        raise HTTPException(404, f"Pending skill '{skill_name}' not found")

    manifest = json.loads(manifest_content)

    if action == "approve":
        # Move from _pending/ to _shared/
        manifest["status"] = "approved"
        manifest["reviewedBy"] = user.employee_id
        manifest["reviewedAt"] = datetime.now(timezone.utc).isoformat()
        manifest["securityScan"] = {"passed": True, "scanner": "manual", "date": datetime.now(timezone.utc).isoformat()}

        s3ops.write_file(f"_shared/skills/{skill_name}/skill.json", json.dumps(manifest, indent=2))
        # Copy tool.js
        tool_content = s3ops.read_file(f"_pending/skills/{skill_name}/tool.js")
        if tool_content:
            s3ops.write_file(f"_shared/skills/{skill_name}/tool.js", tool_content)
        # Copy setup-guide.md if exists
        guide_content = s3ops.read_file(f"_pending/skills/{skill_name}/setup-guide.md")
        if guide_content:
            s3ops.write_file(f"_shared/skills/{skill_name}/setup-guide.md", guide_content)

        # Clean up _pending/
        _s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        try:
            objs = _s3.list_objects_v2(Bucket=s3ops.bucket(), Prefix=f"_pending/skills/{skill_name}/")
            for obj in objs.get("Contents", []):
                _s3.delete_object(Bucket=s3ops.bucket(), Key=obj["Key"])
        except Exception:
            pass

    # Update approval record
    approvals = db.get_approvals()
    for a in approvals:
        if a.get("skillName") == skill_name and a.get("type") == "skill_submit" and a.get("status") == "pending":
            db.update_approval(a["id"], {
                "status": "approved" if action == "approve" else "rejected",
                "reviewedBy": user.employee_id,
                "reviewedByName": user.name,
                "reviewedAt": datetime.now(timezone.utc).isoformat(),
                "reviewReason": body.get("reason", ""),
            })
            break

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "skill_review",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "skill", "targetId": skill_name,
        "detail": f"{'Approved' if action == 'approve' else 'Rejected'} skill '{skill_name}'" + (f": {body.get('reason','')}" if body.get('reason') else ""),
        "status": "success",
    })

    return {"action": action, "skill": skill_name}


@router.post("/api/v1/tools-skills/{skill_name}/approve-install")
def approve_skill_install(skill_name: str, body: dict, authorization: str = Header(default="")):
    """Admin approves employee's skill access request → adds to EMP#.personalSkills."""
    user = require_role(authorization, roles=["admin"])
    approval_id = body.get("approvalId", "")
    if not approval_id:
        raise HTTPException(400, "approvalId required")

    approval = db.get_approval(approval_id)
    if not approval:
        raise HTTPException(404, "Approval not found")

    emp_id = approval.get("employeeId", "")
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(404, f"Employee {emp_id} not found")

    # Add to personalSkills
    personal = emp.get("personalSkills", [])
    if skill_name not in personal:
        personal.append(skill_name)
        db.update_employee(emp_id, {"personalSkills": personal})

    # Update approval
    db.update_approval(approval_id, {
        "status": "approved",
        "reviewedBy": user.employee_id,
        "reviewedByName": user.name,
        "reviewedAt": datetime.now(timezone.utc).isoformat(),
    })

    # Force refresh employee agent
    threading.Thread(target=stop_employee_session, args=(emp_id,), daemon=True).start()

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "skill_install_approved",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "employee_skill", "targetId": f"{emp_id}/{skill_name}",
        "detail": f"Approved skill '{skill_name}' for {emp.get('name', emp_id)}",
        "status": "success",
    })

    return {"approved": True, "employee": emp_id, "skill": skill_name}


@router.get("/api/v1/tools-skills/{skill_name}/code")
def get_skill_code(skill_name: str, source: str = "shared", authorization: str = Header(default="")):
    """Read skill tool.js code for review. source=shared or pending."""
    require_role(authorization, roles=["admin"])
    prefix = "_shared" if source == "shared" else "_pending"
    content = s3ops.read_file(f"{prefix}/skills/{skill_name}/tool.js")
    manifest = s3ops.read_file(f"{prefix}/skills/{skill_name}/skill.json")
    guide = s3ops.read_file(f"{prefix}/skills/{skill_name}/setup-guide.md")
    return {
        "skill": skill_name,
        "source": source,
        "toolJs": content or "",
        "manifest": json.loads(manifest) if manifest else None,
        "setupGuide": guide or "",
    }


# =========================================================================
# Always-On Agent Management
# =========================================================================

@router.put("/api/v1/agents/{emp_id}/always-on")
def enable_always_on(emp_id: str, body: dict, authorization: str = Header(default="")):
    """Enable or disable always-on for an employee.
    enable=true: creates ECS Service. enable=false: stops ECS Service."""
    user = require_role(authorization, roles=["admin"])
    enable = body.get("enable", True)

    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    agent_id = emp.get("agentId")
    if not agent_id:
        raise HTTPException(400, "Employee has no agent. Create one first in Agent Factory.")

    if enable:
        # Delegate to admin_always_on.py start
        from routers.admin_always_on import start_always_on_agent
        # Inject a fake authorization header since we already validated
        return start_always_on_agent(agent_id, authorization=authorization)
    else:
        from routers.admin_always_on import stop_always_on_agent
        result = stop_always_on_agent(agent_id, authorization=authorization)
        # Clear EMP# always-on fields
        db.update_employee(emp_id, {"alwaysOnEnabled": False, "alwaysOnStatus": "stopped"})
        return result


@router.get("/api/v1/agents/{emp_id}/always-on/status")
def get_always_on_status(emp_id: str, authorization: str = Header(default="")):
    """Get always-on container status for an employee."""
    require_role(authorization, roles=["admin", "manager"])
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    agent_id = emp.get("agentId", "")
    if not emp.get("alwaysOnEnabled"):
        return {"enabled": False, "status": "not_configured", "employeeId": emp_id}

    # Get container status from admin_always_on
    from routers.admin_always_on import get_always_on_status as _get_status
    try:
        container = _get_status(agent_id, authorization=authorization)
    except Exception:
        container = {"running": False, "ecsStatus": "UNKNOWN"}

    return {
        "enabled": True,
        "employeeId": emp_id,
        "agentId": agent_id,
        "tier": emp.get("alwaysOnTier", "standard"),
        "serviceName": emp.get("alwaysOnServiceName", ""),
        "running": container.get("running", False),
        "ecsStatus": container.get("ecsStatus", "UNKNOWN"),
        "endpoint": container.get("endpoint"),
    }


@router.get("/api/v1/agents/{emp_id}/always-on/channels")
def get_always_on_channels(emp_id: str, authorization: str = Header(default="")):
    """Get IM channel status for an employee's always-on container."""
    require_role(authorization, roles=["admin", "manager"])
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    if not emp.get("alwaysOnEnabled"):
        return {"channels": [], "reason": "always-on not enabled"}

    # Read IM credentials from DynamoDB (masked)
    im_creds = emp.get("imCredentials", {}) or {}
    channels = []
    for ch, data in im_creds.items():
        if not data or not isinstance(data, dict):
            continue
        channels.append({
            "channel": ch,
            "connected": True,
            "connectedAt": data.get("connectedAt", ""),
            "masked": True,  # never show actual tokens
        })

    # Also try to get live status from container
    agent_id = emp.get("agentId", "")
    try:
        ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
        endpoint = ssm.get_parameter(
            Name=f"/openclaw/{STACK_NAME}/always-on/{agent_id}/endpoint"
        )["Parameter"]["Value"]
        import requests as _req_ch
        r = _req_ch.get(f"{endpoint}/admin/channels/list", timeout=5)
        if r.status_code == 200:
            return {"channels": channels, "liveStatus": r.json().get("output", "")}
    except Exception:
        pass

    return {"channels": channels}


@router.delete("/api/v1/agents/{emp_id}/always-on/channels/{channel}")
def disconnect_always_on_channel(emp_id: str, channel: str, authorization: str = Header(default="")):
    """Disconnect an IM channel from an employee's always-on container."""
    user = require_role(authorization, roles=["admin"])
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    agent_id = emp.get("agentId", "")

    # Call container to remove channel
    try:
        ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
        endpoint = ssm.get_parameter(
            Name=f"/openclaw/{STACK_NAME}/always-on/{agent_id}/endpoint"
        )["Parameter"]["Value"]
        import requests as _req_dc
        r = _req_dc.post(f"{endpoint}/admin/channels/remove",
                        json={"channel": channel}, timeout=15)
    except Exception as e:
        print(f"[disconnect-channel] Container call failed: {e}")

    # Remove credential from DynamoDB
    im_creds = emp.get("imCredentials", {}) or {}
    if channel in im_creds:
        im_creds.pop(channel)
        db.update_employee(emp_id, {"imCredentials": im_creds})

    # Audit
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "im_channel",
        "targetId": f"{emp_id}/{channel}",
        "detail": f"Admin disconnected {channel} for {emp_id}",
        "status": "success",
    })
    return {"disconnected": True, "channel": channel, "employeeId": emp_id}


@router.get("/api/v1/workspace/{emp_id}/files")
def get_workspace_files(emp_id: str, agent_type: str = "serverless",
                        authorization: str = Header(default="")):
    """List workspace files. agent_type=serverless reads S3, agent_type=always-on reads EFS."""
    require_role(authorization, roles=["admin", "manager"])

    if agent_type == "always-on":
        # Read from EFS (mounted on EC2 at /mnt/efs)
        efs_path = f"/mnt/efs/{emp_id}/workspace"
        if not os.path.isdir(efs_path):
            return {"files": [], "source": "efs", "error": "EFS workspace not found"}
        files = []
        for root, dirs, fnames in os.walk(efs_path):
            for fname in fnames:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, efs_path)
                try:
                    stat = os.stat(fpath)
                    files.append({
                        "path": rel,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    })
                except OSError:
                    pass
        return {"files": files, "source": "efs", "basePath": efs_path}
    else:
        # Read from S3 (existing logic)
        bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
        prefix = f"{emp_id}/workspace/"
        try:
            s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=200)
            files = []
            for obj in resp.get("Contents", []):
                rel = obj["Key"].replace(prefix, "")
                if not rel:
                    continue
                files.append({
                    "path": rel,
                    "size": obj["Size"],
                    "modified": obj["LastModified"].isoformat(),
                })
            return {"files": files, "source": "s3", "bucket": bucket, "prefix": prefix}
        except Exception as e:
            return {"files": [], "source": "s3", "error": str(e)}
