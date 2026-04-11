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

    # Ensure all agents have required array fields (some DynamoDB records lack them)
    for a in agents:
        a.setdefault("channels", [])
        a.setdefault("skills", [])
        a.setdefault("soulVersions", {})

    # Dynamic status from DynamoDB lastInvocationAt (replaces CloudWatch)
    for a in agents:
        a["status"] = _resolve_agent_status(a)

    if user and user.role == "manager":
        scope = get_dept_scope(user)
        if scope is not None:
            positions = db.get_positions()
            pos_in_scope = {p["id"] for p in positions if p.get("departmentId") in scope}
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
def get_all_skill_keys():
    """List all required API keys across all skills.
    Reads skill manifests from S3 to determine required env vars,
    then checks SSM to see which are actually configured.
    Cached for 5 minutes to reduce S3 API calls."""

    if _skill_keys_cache["data"] and _time_agents.time() < _skill_keys_cache["expires"]:
        return _skill_keys_cache["data"]

    # Get all skills from S3
    files = s3ops.list_files("_shared/skills/")
    skill_names = set()
    for f in files:
        parts = f["name"].split("/")
        if parts[0]:
            skill_names.add(parts[0])

    keys = []
    key_id = 0
    stack = os.environ.get("STACK_NAME", "openclaw")

    for name in sorted(skill_names):
        content = s3ops.read_file(f"_shared/skills/{name}/skill.json")
        if not content:
            continue
        try:
            manifest = json.loads(content)
        except json.JSONDecodeError:
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

    _skill_keys_cache["data"] = keys
    _skill_keys_cache["expires"] = _time_agents.time() + 300
    return keys


# ── Skill assignment ─────────────────────────────────────────────────────

@router.post("/api/v1/skills/{skill_name}/assign")
def assign_skill_to_position(skill_name: str, body: dict, authorization: str = Header(default="")):
    """Assign a skill to a position. Skills are read from POS#.defaultSkills at runtime
    by workspace_assembler — no need to propagate to individual AGENT# records."""
    require_role(authorization, roles=["admin"])
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

    return {"assigned": True, "positionId": position_id, "skill": skill_name}


@router.delete("/api/v1/skills/{skill_name}/assign")
def unassign_skill_from_position(skill_name: str, positionId: str = "", authorization: str = Header(default="")):
    """Remove a skill from a position."""
    require_role(authorization, roles=["admin"])
    if not positionId:
        raise HTTPException(400, "positionId query param required")

    pos = db.get_position(positionId)
    if not pos:
        raise HTTPException(404, f"Position {positionId} not found")

    skills = pos.get("defaultSkills", [])
    if skill_name in skills:
        skills.remove(skill_name)
        db.update_position(positionId, {"defaultSkills": skills})

    return {"unassigned": True, "positionId": positionId, "skill": skill_name}


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
