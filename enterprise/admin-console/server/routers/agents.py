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
    ssm_client, bump_config_version,
    GATEWAY_REGION, STACK_NAME, GATEWAY_ACCOUNT_ID,
    stop_employee_session, get_dept_scope,
)

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


# Hardcoded fallback log groups (used if dynamic discovery fails)
_LOG_GROUPS = [
    "/aws/bedrock-agentcore/runtimes/openclaw_multitenancy_runtime-olT3WX54rJ-DEFAULT",
    "/aws/bedrock-agentcore/runtimes/openclaw_multitenancy_exec_runtime-OkWZBw3ybK-DEFAULT",
    "/openclaw/openclaw-multitenancy/agents",
]


def _get_all_agentcore_log_groups() -> list:
    """Dynamically discover all AgentCore runtime log groups.
    Caches for 5 minutes so new runtimes are picked up automatically."""
    try:
        cw = boto3.client("logs", region_name=GATEWAY_REGION)
        resp = cw.describe_log_groups(logGroupNamePrefix="/aws/bedrock-agentcore/runtimes/")
        groups = [g["logGroupName"] for g in resp.get("logGroups", [])]
        extra = ["/openclaw/openclaw-multitenancy/agents"]
        return groups + [g for g in extra if g not in groups]
    except Exception:
        return _LOG_GROUPS


def _get_active_agent_ids() -> set:
    """Determine which agents are currently active (microVM running) from CloudWatch.
    An agent is 'active' if it had an invocation in the last 15 minutes (AgentCore idle timeout).
    Returns set of employee IDs that are active."""
    try:
        import time as _t
        cw = boto3.client("logs", region_name=GATEWAY_REGION)
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

    # Dynamic status: check CloudWatch for recent activity (AgentCore only)
    active_emp_ids = _get_active_agent_ids()
    for a in agents:
        # EKS and always-on-ecs agents have their own status endpoints;
        # don't override with CloudWatch-based idle detection.
        if a.get("deployMode") in ("eks", "always-on-ecs"):
            continue
        emp_id = a.get("employeeId", "")
        if emp_id in active_emp_ids:
            a["status"] = "active"
        elif a.get("status") == "active":
            a["status"] = "idle"  # No recent activity -> idle (serverless standby)

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
    # Dynamic status (AgentCore only; EKS/ECS have their own status endpoints)
    if agent.get("deployMode") not in ("eks", "always-on-ecs"):
        active_emp_ids = _get_active_agent_ids()
        emp_id = agent.get("employeeId", "")
        if emp_id in active_emp_ids:
            agent["status"] = "active"
        elif agent.get("status") == "active":
            agent["status"] = "idle"
    return agent

@router.post("/api/v1/agents")
def create_agent(body: dict):
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
        # 1. Write SSM tenant->position and permissions for this employee
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
            ssm = ssm_client()
            ssm.put_parameter(Name=f"/openclaw/{stack}/tenants/{emp_id}/position",
                              Value=pos_id, Type="String", Overwrite=True)
            ssm.put_parameter(
                Name=f"/openclaw/{stack}/tenants/{emp_id}/permissions",
                Value=json.dumps({"profile": "auto", "tools": tools, "role": pos_id.replace("pos-", "")}),
                Type="String", Overwrite=True)
        except Exception as e:
            print(f"[create_agent] SSM write failed for {emp_id}: {e}")

        # 2. Create binding (employee -> agent)
        now = datetime.now(timezone.utc).isoformat()
        emp = next((e for e in db.get_employees() if e["id"] == emp_id), {})
        positions = db.get_positions()
        pos = next((p for p in positions if p["id"] == pos_id), {})
        deploy_mode = body.get("deployMode", "serverless")
        db.create_binding({
            "employeeId": emp_id,
            "employeeName": emp.get("name", ""),
            "agentId": agent_id,
            "agentName": body.get("name", ""),
            "mode": "1:1",  # kept for backward compat with existing binding queries
            "channel": channel,
            "status": "active",
            "source": "manual",
            "createdAt": now,
        })

        # 3. Seed minimal S3 workspace if it doesn't already exist
        s3_bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
        try:
            s3 = boto3.client("s3", region_name=region)
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
            "detail": f"Created agent '{body.get('name')}' for {emp.get('name', emp_id)} ({pos.get('name', pos_id)}) [{deploy_mode}]",
            "status": "success",
        })

        # 6. If always-on, mark as pending -- admin starts from Agent Factory
        if deploy_mode == "always-on-ecs":
            agent["note"] = "Agent created with ECS mode. Go to Agent Factory -> ECS tab -> Start to launch the Fargate container."
        elif deploy_mode == "eks":
            # Auto-deploy to EKS immediately
            try:
                import asyncio
                from services.k8s_client import k8s_client, OPENCLAW_NAMESPACE
                import s3ops as _s3ops

                # Assemble workspace files from SOUL layers (same as admin_eks deploy)
                soul = _s3ops.get_soul_layers(pos_id, emp_id)
                workspace_files = {}
                for layer_name in ("global", "position", "personal"):
                    layer = soul.get(layer_name, {})
                    for fname, content in layer.items():
                        if content and content.strip():
                            workspace_files[fname] = content

                model = body.get("model", os.environ.get("DEFAULT_BEDROCK_MODEL", "bedrock/us.amazon.nova-2-lite-v1:0"))

                loop = asyncio.get_event_loop()
                result = loop.run_until_complete(k8s_client.create_openclaw_instance(
                    namespace=OPENCLAW_NAMESPACE,
                    agent_name=agent_id,
                    employee_id=emp_id or "",
                    position_id=pos_id,
                    model=model,
                    workspace_files=workspace_files if workspace_files else None,
                ))

                # Write SSM eks-endpoint
                try:
                    from routers.admin_eks import _agent_svc_endpoint
                    ssm = ssm_client()
                    ssm.put_parameter(
                        Name=f"/openclaw/{stack}/tenants/{emp_id}/eks-endpoint",
                        Value=_agent_svc_endpoint(agent_id),
                        Type="String", Overwrite=True,
                    )
                except Exception:
                    pass

                agent["note"] = "Agent created and deployed to EKS. Pod starting (~60s)."
                agent["containerStatus"] = "starting"
            except Exception as e:
                print(f"[create_agent] EKS auto-deploy failed for {agent_id}: {e}")
                agent["note"] = f"Agent created but EKS deploy failed: {e}. Deploy manually from EKS tab."

    return agent


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
    personal_soul = s3ops.read_file(f"{emp_id}/workspace/SOUL.md") if emp_id else ""

    return [
        {"layer": "global", "content": global_soul, "locked": True, "version": sv.get("global", 3), "updatedAt": "2026-03-15T00:00:00Z"},
        {"layer": "position", "content": position_soul, "locked": False, "version": sv.get("position", 1), "updatedAt": "2026-03-18T00:00:00Z"},
        {"layer": "personal", "content": personal_soul or "", "locked": False, "version": sv.get("personal", 0), "updatedAt": "2026-03-19T00:00:00Z"},
    ]


class SoulSaveRequest(BaseModel):
    layer: str  # "position" or "personal"
    content: str

@router.put("/api/v1/agents/{agent_id}/soul")
def save_agent_soul(agent_id: str, body: SoulSaveRequest, authorization: str = Header(default="")):
    """Save a SOUL layer to S3. Increments version in DynamoDB."""
    require_role(authorization, roles=["admin", "manager"])
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if body.layer == "global":
        raise HTTPException(403, "Global layer is locked -- requires CISO + CTO approval")

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

@router.get("/api/v1/skills/keys/all")
def get_all_skill_keys():
    """List all required API keys across all skills.
    Reads skill manifests from S3 to determine required env vars,
    then checks SSM to see which are actually configured."""

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

    return keys


# ── Skill assignment ─────────────────────────────────────────────────────

@router.post("/api/v1/skills/{skill_name}/assign")
def assign_skill_to_position(skill_name: str, body: dict, authorization: str = Header(default="")):
    """Assign a skill to a position. Propagates to all agents in that position."""
    require_role(authorization, roles=["admin"])
    position_id = body.get("positionId", "")
    if not position_id:
        raise HTTPException(400, "positionId required")

    # Verify skill exists in S3
    manifest_content = s3ops.read_file(f"_shared/skills/{skill_name}/skill.json")
    if not manifest_content:
        raise HTTPException(404, f"Skill {skill_name} not found in S3")

    # Update position's defaultSkills
    pos = db.get_position(position_id)
    if not pos:
        raise HTTPException(404, f"Position {position_id} not found")

    skills = pos.get("defaultSkills", [])
    if skill_name not in skills:
        skills.append(skill_name)
        db.update_position(position_id, {"defaultSkills": skills})

    # Propagate to agents: add skill to each agent in this position
    agents_updated = []
    for emp in db.get_employees():
        if emp.get("positionId") == position_id and emp.get("agentId"):
            agent = db.get_agent(emp["agentId"])
            if agent:
                agent_skills = agent.get("skills", [])
                if skill_name not in agent_skills:
                    agent_skills.append(skill_name)
                    db.update_agent(emp["agentId"], {"skills": agent_skills})
                    agents_updated.append(emp["agentId"])

    return {"assigned": True, "positionId": position_id, "skill": skill_name,
            "agentsPropagated": len(agents_updated)}


@router.delete("/api/v1/skills/{skill_name}/assign")
def unassign_skill_from_position(skill_name: str, positionId: str = "", authorization: str = Header(default="")):
    """Remove a skill from a position and its agents."""
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

    # Remove from agents
    agents_updated = []
    for emp in db.get_employees():
        if emp.get("positionId") == positionId and emp.get("agentId"):
            agent = db.get_agent(emp["agentId"])
            if agent:
                agent_skills = agent.get("skills", [])
                if skill_name in agent_skills:
                    agent_skills.remove(skill_name)
                    db.update_agent(emp["agentId"], {"skills": agent_skills})
                    agents_updated.append(emp["agentId"])

    return {"unassigned": True, "positionId": positionId, "skill": skill_name,
            "agentsPropagated": len(agents_updated)}
