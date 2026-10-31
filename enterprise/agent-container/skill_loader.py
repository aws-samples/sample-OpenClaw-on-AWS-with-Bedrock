"""
Enterprise Skill Loader for OpenClaw Multi-Tenant Platform.

Loads skills from S3 based on tenant permissions and injects API keys
from SSM Parameter Store as environment variables.

Three-layer skill architecture (all zero-invasion to OpenClaw):
  Layer 1: Built-in skills (Docker image, always available)
  Layer 2: S3 hot-load skills (scripts, loaded at microVM startup)
  Layer 3: Pre-built skill bundles (tar.gz from S3, loaded at startup)

Usage:
  python skill_loader.py --tenant TENANT_ID --workspace /tmp/workspace \
    --bucket openclaw-tenants-xxx --stack openclaw-multitenancy --region us-east-1

Output:
  - Skills copied to {workspace}/skills/
  - /tmp/skill_env.sh with export KEY=VALUE lines for API key injection
"""

import argparse
import json
import logging
import os
import re
import shlex
import subprocess
import sys

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _base_tenant_id(tenant_id):
    """Extract an employee ID from a tenant-router session ID."""
    parts = tenant_id.split("__")
    return parts[1] if len(parts) >= 2 else tenant_id


def get_tenant_roles(stack_name, tenant_id, region=None):
    """Get tenant roles from the employee's DynamoDB record."""
    ddb_region = region or os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
    base_id = _base_tenant_id(tenant_id)

    try:
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        table = ddb.Table(ddb_table)
        emp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{base_id}"}).get("Item", {})
        if not emp:
            logger.info("Employee %s not found, using default roles", base_id)
            return ["employee"]

        dept_name = emp.get("departmentName", "")
        role = emp.get("role", "employee")
        roles = [role, "employee"]
        if dept_name:
            roles.append(dept_name.lower().replace(" & ", "_").replace(" ", "_"))
            aliases = {
                "engineering": ["engineering", "devops", "qa"],
                "platform team": ["engineering", "devops"],
                "frontend team": ["engineering"],
                "backend team": ["engineering"],
                "qa team": ["engineering", "qa"],
                "enterprise sales": ["sales"],
                "smb sales": ["sales"],
                "customer success": ["csm"],
                "hr & admin": ["hr"],
                "legal & compliance": ["legal"],
                "product": ["product"],
                "finance": ["finance"],
            }
            roles.extend(aliases.get(dept_name.lower(), []))
        if role == "admin":
            roles.append("management")
        return list(set(roles))
    except Exception as e:
        logger.warning("DynamoDB role resolution failed: %s — using default", e)
        return ["employee"]


def get_tenant_assigned_skills(tenant_id, region=None):
    """Return skills explicitly assigned to the employee's position."""
    ddb_region = region or os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
    try:
        table = boto3.resource("dynamodb", region_name=ddb_region).Table(ddb_table)
        emp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{_base_tenant_id(tenant_id)}"}).get("Item", {})
        pos_id = emp.get("positionId", "")
        if not pos_id:
            return set()
        position = table.get_item(Key={"PK": "ORG#acme", "SK": f"POS#{pos_id}"}).get("Item", {})
        return set(position.get("defaultSkills", []))
    except Exception as e:
        logger.warning("Failed to read position skill assignments: %s", e)
        return set()


def get_tenant_personal_skills(tenant_id, region=None):
    """Return employee-specific Skills approved by an administrator."""
    ddb_region = region or os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
    try:
        table = boto3.resource("dynamodb", region_name=ddb_region).Table(ddb_table)
        employee = table.get_item(
            Key={"PK": "ORG#acme", "SK": f"EMP#{_base_tenant_id(tenant_id)}"}
        ).get("Item", {})
        return set(employee.get("personalSkills", []))
    except Exception as e:
        logger.warning("Failed to read personal Skill assignments: %s", e)
        return set()


def load_skill_manifest(skill_dir):
    """Read skill.json manifest from a skill directory."""
    manifest_path = os.path.join(skill_dir, "skill.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Failed to read manifest %s: %s", manifest_path, e)
        return None


def is_skill_allowed(manifest, tenant_roles):
    """Check if a skill is allowed for the tenant based on role permissions."""
    if not manifest:
        return True  # No manifest = no restrictions (backward compat)

    permissions = manifest.get("permissions", {})
    allowed_roles = permissions.get("allowedRoles", ["*"])
    blocked_roles = permissions.get("blockedRoles", [])

    # Check blocked first
    for role in tenant_roles:
        if role in blocked_roles:
            logger.info("Skill %s blocked for role %s", manifest.get("name"), role)
            return False

    # Check allowed
    if "*" in allowed_roles:
        return True
    for role in tenant_roles:
        if role in allowed_roles:
            return True

    logger.info("Skill %s not in allowedRoles for %s", manifest.get("name"), tenant_roles)
    return False


def load_layer2_skills(s3, bucket, stack_name, tenant_id, tenant_roles, workspace):
    """Load Layer 2 skills allowed by manifest role or explicit position assignment."""
    skills_dir = os.path.join(workspace, "skills")
    os.makedirs(skills_dir, exist_ok=True)
    loaded = []
    assigned_skills = get_tenant_assigned_skills(tenant_id)

    try:
        subprocess.run(
            ["aws", "s3", "sync", f"s3://{bucket}/_shared/skills/", f"{skills_dir}/_shared_tmp/", "--quiet"],
            capture_output=True, text=True, timeout=30, check=False)
    except Exception as e:
        logger.warning("Failed to sync shared skills: %s", e)
        return loaded

    shared_tmp = os.path.join(skills_dir, "_shared_tmp")
    if os.path.isdir(shared_tmp):
        for skill_name in os.listdir(shared_tmp):
            skill_path = os.path.join(shared_tmp, skill_name)
            if not os.path.isdir(skill_path):
                continue
            manifest = load_skill_manifest(skill_path)
            if skill_name in assigned_skills or is_skill_allowed(manifest, tenant_roles):
                dest = os.path.join(skills_dir, skill_name)
                if not os.path.exists(dest):
                    os.rename(skill_path, dest)
                    loaded.append(skill_name)
                    logger.info("Layer 2 skill loaded: %s", skill_name)
            else:
                logger.info("Layer 2 skill filtered: %s", skill_name)
        subprocess.run(["rm", "-rf", shared_tmp], capture_output=True)

    try:
        subprocess.run(
            ["aws", "s3", "sync", f"s3://{bucket}/{tenant_id}/skills/", f"{skills_dir}/", "--quiet"],
            capture_output=True, text=True, timeout=30, check=False)
    except Exception as e:
        logger.warning("Failed to sync tenant skills: %s", e)
    return loaded


def load_personal_skills(s3, bucket, tenant_id, workspace, region=None):
    """Load employee's personalSkills from DynamoDB → pull from S3.
    These bypass role permission checks — admin already approved them."""
    ddb_region = region or os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))

    base_id = tenant_id
    parts = base_id.split("__")
    if len(parts) >= 2:
        base_id = parts[1]

    try:
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        table = ddb.Table(ddb_table)
        resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{base_id}"})
        emp = resp.get("Item", {})
        personal = emp.get("personalSkills", [])
        if not personal:
            return []
    except Exception as e:
        logger.warning("Failed to read personalSkills: %s", e)
        return []

    skills_dir = os.path.join(workspace, "skills")
    os.makedirs(skills_dir, exist_ok=True)
    loaded = []

    for skill_name in personal:
        dest = os.path.join(skills_dir, skill_name)
        if os.path.exists(dest):
            continue
        try:
            subprocess.run(
                ["aws", "s3", "sync",
                 f"s3://{bucket}/_shared/skills/{skill_name}/", f"{dest}/",
                 "--quiet"],
                capture_output=True, text=True, timeout=15
            )
            if os.path.isdir(dest):
                loaded.append(skill_name)
                logger.info("Personal skill loaded: %s", skill_name)
        except Exception as e:
            logger.warning("Failed to load personal skill %s: %s", skill_name, e)

    return loaded


def load_layer3_bundles(s3_client, ssm, bucket, stack_name, workspace):
    """Load Layer 3 pre-built skill bundles from S3."""
    skills_dir = os.path.join(workspace, "skills")
    os.makedirs(skills_dir, exist_ok=True)
    loaded = []

    # Read skill catalog from SSM
    catalog_path = f"/openclaw/{stack_name}/skill-catalog/"
    try:
        resp = ssm.get_parameters_by_path(Path=catalog_path, Recursive=False)
        params = resp.get("Parameters", [])
    except ClientError:
        logger.info("No skill catalog found at %s", catalog_path)
        return loaded

    for param in params:
        skill_name = param["Name"].split("/")[-1]
        version = param["Value"]
        bundle_key = f"_shared/skill-bundles/skill-{skill_name}-{version}.tar.gz"

        local_tar = f"/tmp/skill-{skill_name}.tar.gz"
        try:
            s3_client.download_file(bucket, bundle_key, local_tar)
            subprocess.run(
                ["tar", "xzf", local_tar, "-C", skills_dir],
                capture_output=True, text=True, timeout=30, check=True
            )
            os.remove(local_tar)
            loaded.append(f"{skill_name}@{version}")
            logger.info("Layer 3 bundle loaded: %s@%s", skill_name, version)
        except ClientError:
            logger.warning("Bundle not found in S3: %s", bundle_key)
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to extract bundle %s: %s", skill_name, e)
        except Exception as e:
            logger.warning("Failed to load bundle %s: %s", skill_name, e)

    return loaded


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SKILL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_TELEMETRY_MARKER = "<!-- OPENCLAW_ENTERPRISE_SKILL_TELEMETRY_V1 -->"


def get_builtin_skill_ids():
    """Return image-pinned Skills intentionally available to every tenant."""
    result = set()
    for root in ("/root/.openclaw/skills", "/home/ubuntu/.openclaw/skills"):
        if not os.path.isdir(root):
            continue
        for skill_name in os.listdir(root):
            if _SKILL_ID.fullmatch(skill_name) and os.path.isdir(os.path.join(root, skill_name)):
                result.add(skill_name)
    return result


def instrument_authorized_skills(workspace, authorized_skills):
    """Idempotently add an implementation-neutral usage receipt contract."""
    roots = [os.path.join(workspace, "skills"), "/root/.openclaw/skills", "/home/ubuntu/.openclaw/skills"]
    instrumented = 0
    for root in roots:
        root_real = os.path.realpath(root)
        if not os.path.isdir(root_real):
            continue
        for skill_name in sorted(authorized_skills):
            if not _SKILL_ID.fullmatch(skill_name):
                continue
            skill_md = os.path.realpath(os.path.join(root_real, skill_name, "SKILL.md"))
            if not skill_md.startswith(root_real + os.sep) or not os.path.isfile(skill_md):
                continue
            try:
                with open(skill_md, encoding="utf-8") as handle:
                    content = handle.read()
                if _TELEMETRY_MARKER in content:
                    continue
                block = (
                    f"\n\n{_TELEMETRY_MARKER}\n"
                    "## Enterprise usage receipt\n\n"
                    "When you actually apply this Skill in a turn, follow the request-scoped "
                    "enterprise telemetry instruction and append its hidden receipt for Skill ID "
                    f"`{skill_name}`. Do this on every turn where the Skill is applied, even when "
                    "this file was not read again. Reading or remembering this file alone is not "
                    "usage. Do not emit a receipt when this Skill did not contribute to the answer, "
                    "and never show or explain the receipt to the user.\n"
                )
                with open(skill_md, "a", encoding="utf-8") as handle:
                    handle.write(block)
                instrumented += 1
            except OSError as exc:
                logger.warning("Could not instrument Skill %s: %s", skill_name, exc)
    return instrumented


def write_authorized_skill_ids(skill_ids, path="/tmp/authorized_skill_ids.json"):
    """Publish only validated tenant-authorized IDs for runtime receipt validation."""
    values = sorted(skill for skill in skill_ids if _SKILL_ID.fullmatch(skill))
    temp_path = f"{path}.{os.getpid()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(values, handle, separators=(",", ":"))
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def inject_skill_keys(
    ssm,
    s3_client,
    bucket,
    stack_name,
    workspace,
    authorized_skills=None,
    env_file="/tmp/skill_env.sh",
):
    """Export keys only for Skills authorized for this tenant."""
    skills_dir = os.path.join(workspace, "skills")
    manifests: dict[str, dict] = {}
    authorized = set(authorized_skills or [])

    if os.path.isdir(skills_dir):
        for skill_name in sorted(os.listdir(skills_dir)):
            skill_path = os.path.join(skills_dir, skill_name)
            if not os.path.isdir(skill_path):
                continue
            manifest = load_skill_manifest(skill_path)
            if manifest:
                manifests[skill_name] = manifest

    # An authorized Skill may already exist locally from a previous tenant in a
    # shared Fargate workspace. Refresh its manifest from the trusted S3 catalog.
    for skill_name in sorted(authorized - set(manifests)):
        try:
            body = s3_client.get_object(
                Bucket=bucket,
                Key=f"_shared/skills/{skill_name}/skill.json",
            )["Body"].read()
            manifest = json.loads(body)
            if isinstance(manifest, dict):
                manifests[skill_name] = manifest
        except (ClientError, json.JSONDecodeError, UnicodeDecodeError):
            continue

    values: dict[str, str] = {}
    injected = []
    for skill_name, manifest in manifests.items():
        if authorized_skills is not None and skill_name not in authorized:
            continue
        required_env = manifest.get("requires", {}).get("env", [])
        if not isinstance(required_env, list):
            continue
        for env_var in required_env:
            if not isinstance(env_var, str) or not _ENV_NAME.fullmatch(env_var):
                logger.warning("Invalid environment variable declaration in Skill %s", skill_name)
                continue
            ssm_path = f"/openclaw/{stack_name}/skill-keys/{skill_name}/{env_var}"
            try:
                value = ssm.get_parameter(Name=ssm_path, WithDecryption=True)["Parameter"]["Value"]
                if env_var in values and values[env_var] != value:
                    logger.warning("Conflicting value for %s; keeping first loaded Skill mapping", env_var)
                    continue
                values[env_var] = value
                injected.append(f"{skill_name}/{env_var}")
                logger.info("Injected key: %s/%s", skill_name, env_var)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "unknown")
                if code == "ParameterNotFound":
                    logger.warning("Key not found in SSM: %s", ssm_path)
                else:
                    logger.warning("Could not read Skill key %s/%s: %s", skill_name, env_var, code)

    global_path = f"/openclaw/{stack_name}/skill-keys/_global/"
    try:
        token = None
        while True:
            kwargs = {"Path": global_path, "Recursive": False, "WithDecryption": True}
            if token:
                kwargs["NextToken"] = token
            response = ssm.get_parameters_by_path(**kwargs)
            for param in response.get("Parameters", []):
                env_var = param["Name"].split("/")[-1]
                if _ENV_NAME.fullmatch(env_var):
                    values.setdefault(env_var, param["Value"])
                    injected.append(f"_global/{env_var}")
            token = response.get("NextToken")
            if not token:
                break
    except ClientError:
        pass

    with open(env_file, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\n# Auto-generated by skill_loader.py — do not edit\n")
        for env_var, value in sorted(values.items()):
            handle.write(f"export {env_var}={shlex.quote(value)}\n")
    os.chmod(env_file, 0o600)

    json_file = f"{env_file}.json"
    with open(json_file, "w", encoding="utf-8") as handle:
        json.dump(values, handle)
    os.chmod(json_file, 0o600)

    keys_file = f"{env_file}.keys"
    with open(keys_file, "w", encoding="utf-8") as handle:
        for env_var in sorted(values):
            handle.write(env_var + "\n")
    os.chmod(keys_file, 0o600)

    logger.info("Wrote %d env vars to protected environment files", len(values))
    return injected


def main():
    parser = argparse.ArgumentParser(description="Enterprise Skill Loader")
    parser.add_argument("--tenant", required=True, help="Tenant ID")
    parser.add_argument("--workspace", required=True, help="Workspace directory")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--stack", required=True, help="Stack name")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    args = parser.parse_args()

    ssm = boto3.client("ssm", region_name=args.region)
    s3 = boto3.client("s3", region_name=args.region)

    logger.info("=== Skill Loader START tenant=%s ===", args.tenant)

    # Get tenant roles for permission filtering (reads DynamoDB, not SSM)
    roles = get_tenant_roles(args.stack, args.tenant, region=args.region)

    # Layer 2: S3 hot-load skills
    l2 = load_layer2_skills(s3, args.bucket, args.stack, args.tenant, roles, args.workspace)
    logger.info("Layer 2 loaded: %s", l2 if l2 else "none")

    # Personal skills (employee-specific, admin-approved)
    personal = load_personal_skills(s3, args.bucket, args.tenant, args.workspace, region=args.region)
    logger.info("Personal skills loaded: %s", personal if personal else "none")

    # Layer 3: Pre-built skill bundles
    l3 = load_layer3_bundles(s3, ssm, args.bucket, args.stack, args.workspace)
    logger.info("Layer 3 loaded: %s", l3 if l3 else "none")

    # Inject API keys only for role-, position-, or personally-authorized Skills.
    assigned = get_tenant_assigned_skills(args.tenant, region=args.region)
    personal_assigned = get_tenant_personal_skills(args.tenant, region=args.region)
    role_allowed = set()
    skills_dir = os.path.join(args.workspace, "skills")
    if _base_tenant_id(args.tenant) != "unknown" and os.path.isdir(skills_dir):
        for skill_name in os.listdir(skills_dir):
            manifest = load_skill_manifest(os.path.join(skills_dir, skill_name))
            if manifest and is_skill_allowed(manifest, roles):
                role_allowed.add(skill_name)
    authorized_skills = assigned | personal_assigned | role_allowed | get_builtin_skill_ids()
    write_authorized_skill_ids(authorized_skills)
    instrumented = instrument_authorized_skills(args.workspace, authorized_skills)
    logger.info("Skill telemetry instrumented: %d", instrumented)
    keys = inject_skill_keys(
        ssm,
        s3,
        args.bucket,
        args.stack,
        args.workspace,
        authorized_skills=authorized_skills,
    )
    logger.info("Keys injected: %d", len(keys))

    # Summary
    skills_dir = os.path.join(args.workspace, "skills")
    total = 0
    if os.path.isdir(skills_dir):
        total = len([d for d in os.listdir(skills_dir) if os.path.isdir(os.path.join(skills_dir, d))])
    logger.info("=== Skill Loader DONE: %d skills available ===", total)


if __name__ == "__main__":
    main()
