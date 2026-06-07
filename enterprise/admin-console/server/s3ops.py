"""
S3 operations for workspace files, SOUL management, and memory.
Centralizes all S3 access with proper error handling and caching.
"""
import os
import json
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_s3 = None
_bucket = None

# === EFS redirection for always-on ECS agents ===
# Always-on ECS containers persist their workspace on EFS (mounted at /mnt/efs on
# this box), NOT S3 — the container's watchdog skips S3 sync in EFS mode. So admin
# edits to an always-on employee's personal workspace must go to EFS to be visible
# to the running container (and vice versa). Shared layers (_shared/...) stay on S3.
EFS_ROOT = os.environ.get("EFS_ROOT", "/mnt/efs")
_always_on_cache: dict = {}   # emp_id -> bool, short-lived
_always_on_cache_ts: dict = {}


def _is_always_on_employee(emp_id: str) -> bool:
    """Return True if this employee runs an always-on ECS agent (workspace on EFS).
    Cached for 30s to avoid a DynamoDB read on every file op."""
    if not emp_id:
        return False
    import time as _t
    now = _t.time()
    ts = _always_on_cache_ts.get(emp_id, 0)
    if now - ts < 30 and emp_id in _always_on_cache:
        return _always_on_cache[emp_id]
    result = False
    try:
        import db as _db
        emp = _db.get_employee(emp_id)
        if emp and emp.get("alwaysOnEnabled"):
            result = True
    except Exception:
        result = False
    _always_on_cache[emp_id] = result
    _always_on_cache_ts[emp_id] = now
    return result


def _efs_path_for_key(key: str) -> Optional[str]:
    """Map an S3 personal-workspace key to its EFS path, IF the owning employee runs
    always-on. Returns None for non-personal keys or serverless employees (→ use S3).

    Personal key format: '{emp_id}/workspace/...'  →  '/mnt/efs/{emp_id}/workspace/...'
    """
    parts = key.split("/", 1)
    if len(parts) != 2:
        return None
    emp_id, rest = parts[0], parts[1]
    # Only personal workspace keys; shared layers (_shared/...) always live on S3.
    if not rest.startswith("workspace/"):
        return None
    if not emp_id.startswith("emp-"):
        return None
    if not _is_always_on_employee(emp_id):
        return None
    if not os.path.isdir(os.path.join(EFS_ROOT, emp_id, "workspace")):
        return None
    return os.path.join(EFS_ROOT, emp_id, rest)


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=AWS_REGION)
    return _s3


def bucket():
    global _bucket
    if _bucket is None:
        # Prefer S3_BUCKET env var (set by /etc/openclaw/env and start.sh)
        _bucket = os.environ.get("S3_BUCKET", "")
        if not _bucket:
            try:
                account = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()["Account"]
                _bucket = f"openclaw-tenants-{account}"
            except Exception:
                _bucket = "openclaw-tenants-000000000000"
    return _bucket


def read_file(key: str) -> Optional[str]:
    """Read a text file. Always-on personal workspace → EFS; otherwise S3."""
    efs = _efs_path_for_key(key)
    if efs is not None:
        try:
            with open(efs, encoding="utf-8") as f:
                return f.read()
        except (OSError, FileNotFoundError):
            return None
    try:
        obj = _client().get_object(Bucket=bucket(), Key=key)
        return obj["Body"].read().decode("utf-8")
    except ClientError:
        return None


def write_file(key: str, content: str, metadata: Optional[dict] = None) -> bool:
    """Write a text file. Always-on personal workspace → EFS (live to container);
    otherwise S3 (versioning handles history)."""
    efs = _efs_path_for_key(key)
    if efs is not None:
        try:
            os.makedirs(os.path.dirname(efs), exist_ok=True)
            with open(efs, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError as e:
            print(f"[s3ops] EFS write error: {e}")
            return False
    try:
        extra = {}
        if metadata:
            extra["Metadata"] = {k: str(v) for k, v in metadata.items()}
        _client().put_object(
            Bucket=bucket(), Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/markdown",
            **extra,
        )
        return True
    except ClientError as e:
        print(f"[s3ops] write error: {e}")
        return False


def list_files(prefix: str) -> list[dict]:
    """List files under a prefix. Always-on personal workspace → EFS; otherwise S3."""
    # EFS redirect: prefix like 'emp-x/workspace/...' for an always-on employee.
    efs_dir = _efs_path_for_key(prefix.rstrip("/") + "/_") if prefix else None
    if efs_dir is not None:
        efs_base = os.path.dirname(efs_dir)  # strip the '_' sentinel
        files = []
        if os.path.isdir(efs_base):
            for root, _dirs, fnames in os.walk(efs_base):
                for fname in fnames:
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, efs_base)
                    # Reconstruct the S3-style key so callers see a consistent shape
                    key = prefix + rel
                    try:
                        st = os.stat(fpath)
                        files.append({
                            "key": key,
                            "name": rel,
                            "size": st.st_size,
                            "lastModified": datetime.fromtimestamp(
                                st.st_mtime, timezone.utc).isoformat(),
                        })
                    except OSError:
                        pass
        return files
    files = []
    try:
        paginator = _client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket(), Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key[len(prefix):]  # relative name
                if name and not name.endswith("/"):
                    files.append({
                        "key": key,
                        "name": name,
                        "size": obj["Size"],
                        "lastModified": obj["LastModified"].isoformat(),
                    })
    except ClientError:
        pass
    return files


def list_versions(key: str) -> list[dict]:
    """List all versions of a file (requires S3 versioning enabled)."""
    versions = []
    try:
        resp = _client().list_object_versions(Bucket=bucket(), Prefix=key)
        for v in resp.get("Versions", []):
            if v["Key"] == key:
                versions.append({
                    "versionId": v["VersionId"],
                    "lastModified": v["LastModified"].isoformat(),
                    "size": v["Size"],
                    "isLatest": v["IsLatest"],
                })
    except ClientError:
        pass
    return versions


def read_version(key: str, version_id: str) -> Optional[str]:
    """Read a specific version of a file."""
    try:
        obj = _client().get_object(Bucket=bucket(), Key=key, VersionId=version_id)
        return obj["Body"].read().decode("utf-8")
    except ClientError:
        return None


# === SOUL-specific operations ===

def get_soul_layers(pos_id: str, employee_id: Optional[str] = None) -> dict:
    """Get all three SOUL layers for an agent."""
    global_soul = read_file("_shared/soul/global/SOUL.md") or ""
    global_agents = read_file("_shared/soul/global/AGENTS.md") or ""
    global_tools = read_file("_shared/soul/global/TOOLS.md") or ""
    position_soul = read_file(f"_shared/soul/positions/{pos_id}/SOUL.md") or ""
    position_agents = read_file(f"_shared/soul/positions/{pos_id}/AGENTS.md") or ""

    personal_soul = ""
    personal_user = ""
    if employee_id:
        personal_soul = read_file(f"{employee_id}/workspace/SOUL.md") or ""
        personal_user = read_file(f"{employee_id}/workspace/USER.md") or ""

    return {
        "global": {"SOUL.md": global_soul, "AGENTS.md": global_agents, "TOOLS.md": global_tools},
        "position": {"SOUL.md": position_soul, "AGENTS.md": position_agents},
        "personal": {"SOUL.md": personal_soul, "USER.md": personal_user},
    }


def save_soul_layer(layer: str, pos_id: str, employee_id: Optional[str], filename: str, content: str) -> dict:
    """Save a SOUL layer file to S3."""
    if layer == "global":
        key = f"_shared/soul/global/{filename}"
    elif layer == "position":
        key = f"_shared/soul/positions/{pos_id}/{filename}"
    elif layer == "personal" and employee_id:
        key = f"{employee_id}/workspace/{filename}"
    else:
        return {"error": "Invalid layer or missing employee_id"}

    now = datetime.now(timezone.utc).isoformat()
    success = write_file(key, content, metadata={"updatedAt": now, "layer": layer})
    return {"key": key, "saved": success, "updatedAt": now}


# === Memory operations ===

def get_agent_memory(employee_id: str) -> dict:
    """Get memory files for an agent's workspace."""
    memory_md = read_file(f"{employee_id}/workspace/MEMORY.md")
    daily_files = list_files(f"{employee_id}/workspace/memory/")
    return {
        "memoryMd": memory_md or "",
        "memoryMdSize": len(memory_md) if memory_md else 0,
        "dailyFiles": daily_files,
        "totalDailyFiles": len(daily_files),
        "totalSize": sum(f["size"] for f in daily_files) + (len(memory_md) if memory_md else 0),
    }


def get_daily_memory(employee_id: str, date: str) -> Optional[str]:
    """Read a specific daily memory file."""
    return read_file(f"{employee_id}/workspace/memory/{date}.md")


# === Workspace listing ===

def get_workspace_tree(pos_id: str, employee_id: Optional[str] = None) -> dict:
    """Get the full workspace file tree for an agent, with role-filtered skills."""
    global_files = list_files("_shared/soul/global/")
    position_files = list_files(f"_shared/soul/positions/{pos_id}/") if pos_id else []
    personal_files = list_files(f"{employee_id}/workspace/") if employee_id else []

    # List all skills and filter by role
    all_skill_files = list_files("_shared/skills/")
    position_skills = list_files(f"_shared/soul/positions/{pos_id}/skills/") if pos_id else []

    # Determine agent's role from position
    pos_to_role = {
        "pos-sa": "engineering", "pos-sde": "engineering", "pos-devops": "devops",
        "pos-qa": "qa", "pos-ae": "sales", "pos-pm": "product",
        "pos-fa": "finance", "pos-hr": "hr", "pos-csm": "csm",
        "pos-legal": "legal",
    }
    agent_role = pos_to_role.get(pos_id, "employee")

    # Read each skill's manifest and filter
    global_skills = []  # allowedRoles: ["*"]
    role_skills = []    # matches agent's role
    skill_names_seen = set()

    for f in all_skill_files:
        # Only look at skill.json files
        if not f["name"].endswith("skill.json"):
            continue
        skill_name = f["name"].split("/")[0]
        if skill_name in skill_names_seen:
            continue
        skill_names_seen.add(skill_name)

        # Read manifest to check permissions
        manifest_content = read_file(f["key"])
        if not manifest_content:
            continue
        try:
            import json as _json
            manifest = _json.loads(manifest_content)
        except Exception:
            continue

        allowed = manifest.get("permissions", {}).get("allowedRoles", ["*"])
        blocked = manifest.get("permissions", {}).get("blockedRoles", [])

        if agent_role in blocked:
            continue

        if "*" in allowed:
            global_skills.append(f)
        elif agent_role in allowed or "management" in allowed:
            role_skills.append(f)

    return {
        "global": {
            "soul": global_files,
            "skills": global_skills,
        },
        "position": {
            "soul": position_files,
            "skills": role_skills + position_skills,
        },
        "personal": {
            "files": personal_files,
        },
        "summary": {
            "globalCount": len(global_files) + len(global_skills),
            "positionCount": len(position_files) + len(role_skills) + len(position_skills),
            "personalCount": len(personal_files),
        },
    }
