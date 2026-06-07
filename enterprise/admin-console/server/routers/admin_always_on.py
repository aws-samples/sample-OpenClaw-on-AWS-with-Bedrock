"""
Always-on Shared Agents — ECS Fargate tasks + Agent Refresh.

Architecture: Admin Console -> ECS RunTask (Fargate) -> task self-registers
its private IP in SSM -> Tenant Router reads SSM endpoint -> routes to task.
No port mapping needed; ECS manages networking via awsvpc mode.

Endpoints:
  /api/v1/admin/always-on/{agent_id}/*
  /api/v1/agents/{emp_id}/refresh
"""

import os
import time
from datetime import datetime, timezone

import boto3

from fastapi import APIRouter, HTTPException, Header

import db
from shared import (
    require_auth, require_role, ssm_client,
    GATEWAY_REGION, STACK_NAME, GATEWAY_ACCOUNT_ID,
    stop_employee_session,
)

router = APIRouter(tags=["admin-always-on"])

_ALWAYS_ON_ECR_IMAGE = os.environ.get("AGENT_ECR_IMAGE", "")


def _get_ecs_config() -> dict:
    """Resolve ECS cluster / task-def / subnet / SG from env or CloudFormation outputs via SSM."""
    stack = os.environ.get("STACK_NAME", "openclaw")
    cluster   = os.environ.get("ECS_CLUSTER_NAME",      f"{stack}-always-on")
    task_def  = os.environ.get("ECS_TASK_DEFINITION",   f"{stack}-always-on-agent")
    subnet_id = os.environ.get("ECS_SUBNET_ID",         "")
    sg_id     = os.environ.get("ECS_TASK_SG_ID",        "")

    # Fall back to SSM if env vars are not set (written by deploy script from CF outputs)
    if not subnet_id or not sg_id:
        try:
            ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
            if not subnet_id:
                subnet_id = ssm.get_parameter(
                    Name=f"/openclaw/{stack}/ecs/subnet-id")["Parameter"]["Value"]
            if not sg_id:
                sg_id = ssm.get_parameter(
                    Name=f"/openclaw/{stack}/ecs/task-sg-id")["Parameter"]["Value"]
        except Exception:
            pass

    return {"cluster": cluster, "task_def": task_def, "subnet_id": subnet_id, "sg_id": sg_id}


def _resolve_bot_tokens(stack: str, agent_id: str) -> tuple:
    """Resolve Telegram/Discord bot tokens from SSM for Plan A direct IM."""
    telegram_token = ""
    discord_token = ""
    try:
        ssm_tok = boto3.client("ssm", region_name=GATEWAY_REGION)
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
    return telegram_token, discord_token


def _build_agent_env(agent: dict, agent_id: str, stack: str, bucket: str,
                     ddb_table: str, ddb_region: str,
                     telegram_token: str, discord_token: str,
                     tier: str = "standard") -> list:
    """Build environment variable list for ECS container."""
    emp_id = agent.get("employeeId", agent_id)
    session_id = f"personal__{emp_id}" if agent.get("employeeId") else f"shared__{agent_id}"

    model, guardrail, _, _ = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["standard"])

    # Pre-authorized Discord user IDs (skip interactive DM pairing). Stored on the
    # agent record as discordAllowFrom (list or comma-separated string).
    allow_from = agent.get("discordAllowFrom", "")
    if isinstance(allow_from, list):
        allow_from = ",".join(str(x) for x in allow_from)

    return [
        {"name": "SESSION_ID",         "value": session_id},
        {"name": "SHARED_AGENT_ID",    "value": agent_id},
        {"name": "S3_BUCKET",          "value": bucket},
        {"name": "STACK_NAME",         "value": stack},
        {"name": "AWS_REGION",         "value": GATEWAY_REGION},
        {"name": "DYNAMODB_TABLE",     "value": ddb_table},
        {"name": "DYNAMODB_REGION",    "value": ddb_region},
        {"name": "SYNC_INTERVAL",      "value": "120"},
        {"name": "EFS_ENABLED",        "value": "true"},
        {"name": "BEDROCK_MODEL_ID",   "value": model},
        {"name": "GUARDRAIL_ID",       "value": guardrail},
        {"name": "FARGATE_TIER",       "value": tier},
        {"name": "TELEGRAM_BOT_TOKEN", "value": telegram_token},
        {"name": "DISCORD_BOT_TOKEN",  "value": discord_token},
        {"name": "DISCORD_ALLOW_FROM", "value": allow_from},
    ]


def _ecs_service_name(agent_id: str) -> str:
    """Derive ECS service name from agent_id (must be DNS-compatible)."""
    import re as _re_svc
    return _re_svc.sub(r"[^a-zA-Z0-9-]", "-", agent_id)[:32]


# ── Per-tier config resolution ────────────────────────────────────────────

# Tier → (model, guardrail_id, cpu, memory)
_TIER_DEFAULTS = {
    "standard":    ("minimax.minimax-m2.5",  "", "256", "512"),
    "restricted":  ("deepseek.v3.2",         "", "256", "512"),
    "engineering": ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", "", "512", "1024"),
    "executive":   ("global.anthropic.claude-sonnet-4-6", "", "512", "1024"),
}


def _resolve_tier(emp_id: str) -> str:
    """Resolve employee's tier from position → routing config."""
    emp = db.get_employee(emp_id)
    if not emp:
        return "standard"
    pos_id = emp.get("positionId", "")
    if not pos_id:
        return "standard"
    routing = db.get_routing_config()
    runtime_name = routing.get("position_runtime", {}).get(pos_id, "")
    if runtime_name:
        rn = runtime_name.lower()
        for t in ("restricted", "engineering", "executive"):
            if t in rn:
                return t
    return "standard"


def _get_tier_role_arn(stack: str, tier: str) -> str:
    """Get per-tier Task Role ARN from CloudFormation outputs."""
    output_key = f"AlwaysOnRole{tier.capitalize()}Arn"
    try:
        resp = boto3.client("cloudformation", region_name=GATEWAY_REGION).describe_stacks(
            StackName=stack)
        for out in resp["Stacks"][0].get("Outputs", []):
            if out["OutputKey"] == output_key:
                return out["OutputValue"]
    except Exception:
        pass
    # Fallback to naming convention
    return f"arn:aws:iam::{GATEWAY_ACCOUNT_ID}:role/{stack}-ecs-role-{tier}"


def _get_tier_sg(stack: str, tier: str) -> str:
    """Get per-tier Security Group ID from CloudFormation outputs."""
    output_key = f"AlwaysOnSG{tier.capitalize()}Id"
    try:
        resp = boto3.client("cloudformation", region_name=GATEWAY_REGION).describe_stacks(
            StackName=stack)
        for out in resp["Stacks"][0].get("Outputs", []):
            if out["OutputKey"] == output_key:
                return out["OutputValue"]
    except Exception:
        pass
    # Fallback: use shared SG
    ecs_cfg = _get_ecs_config()
    return ecs_cfg.get("sg_id", "")


def _create_access_point(efs_id: str, emp_id: str) -> str:
    """Create EFS Access Point for an employee (idempotent — returns existing if found)."""
    efs = boto3.client("efs", region_name=GATEWAY_REGION)

    # Check if Access Point already exists for this employee
    try:
        existing = efs.describe_access_points(FileSystemId=efs_id)
        for ap in existing.get("AccessPoints", []):
            root = ap.get("RootDirectory", {}).get("Path", "")
            if root == f"/{emp_id}":
                print(f"[always-on] Access Point already exists for {emp_id}: {ap['AccessPointId']}")
                return ap["AccessPointId"]
    except Exception:
        pass

    # Create new Access Point
    resp = efs.create_access_point(
        FileSystemId=efs_id,
        PosixUser={"Uid": 1000, "Gid": 1000},
        RootDirectory={
            "Path": f"/{emp_id}",
            "CreationInfo": {
                "OwnerUid": 1000,
                "OwnerGid": 1000,
                "Permissions": "755",
            },
        },
        Tags=[
            {"Key": "employee", "Value": emp_id},
            {"Key": "stack", "Value": os.environ.get("STACK_NAME", "openclaw")},
        ],
    )
    ap_id = resp["AccessPointId"]
    print(f"[always-on] Created Access Point for {emp_id}: {ap_id}")
    return ap_id


@router.post("/api/v1/admin/always-on/{agent_id}/start")
def start_always_on_agent(agent_id: str, authorization: str = Header(default="")):
    """Start an always-on agent as an ECS Fargate Service (auto-restart on crash).
    If the service already exists with desiredCount=0, scales it to 1.
    If no service exists, creates one. Also stops any legacy RunTask tasks."""
    require_role(authorization, roles=["admin"])
    stack     = os.environ.get("STACK_NAME",      "openclaw")
    bucket    = os.environ.get("S3_BUCKET",       f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
    ddb_table = os.environ.get("DYNAMODB_TABLE",  "openclaw")
    ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))

    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    ecs_cfg = _get_ecs_config()
    if not ecs_cfg["subnet_id"] or not ecs_cfg["sg_id"]:
        raise HTTPException(500,
            "ECS_SUBNET_ID and ECS_TASK_SG_ID are required. "
            "Set them in /etc/openclaw/env or run the deploy script to write them to SSM.")

    # Resolve per-tier config
    emp_id = agent.get("employeeId", agent_id)
    tier = _resolve_tier(emp_id)
    _, _, tier_cpu, tier_memory = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["standard"])
    tier_role_arn = _get_tier_role_arn(stack, tier)
    tier_sg = _get_tier_sg(stack, tier)

    telegram_token, discord_token = _resolve_bot_tokens(stack, agent_id)
    env_vars = _build_agent_env(agent, agent_id, stack, bucket,
                                ddb_table, ddb_region, telegram_token, discord_token,
                                tier=tier)
    service_name = _ecs_service_name(agent_id)

    try:
        ecs = boto3.client("ecs", region_name=GATEWAY_REGION)

        # Stop any legacy RunTask task (pre-ECS-Service migration)
        try:
            ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
            existing_arn = ssm.get_parameter(
                Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn"
            )["Parameter"]["Value"]
            ecs.stop_task(cluster=ecs_cfg["cluster"], task=existing_arn,
                         reason="Migrated to ECS Service")
            ssm.delete_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn")
        except Exception:
            pass

        # Create EFS Access Point for this employee (idempotent)
        efs_id = ""
        access_point_id = ""
        try:
            efs_id_resp = boto3.client("cloudformation", region_name=GATEWAY_REGION).describe_stacks(
                StackName=stack)
            for out in efs_id_resp["Stacks"][0].get("Outputs", []):
                if out["OutputKey"] == "AlwaysOnEFSId":
                    efs_id = out["OutputValue"]
                    break
            if efs_id:
                access_point_id = _create_access_point(efs_id, emp_id)
        except Exception as e:
            print(f"[always-on] Access Point creation failed (non-fatal): {e}")

        # Tell the container whether EFS is mounted via an access point. The access
        # point root is already scoped to /{employee}, so the entrypoint must NOT
        # append the employee id again (avoids /emp-x/emp-x/workspace double-prefix
        # that diverges from the admin console's root-mounted /emp-x/workspace view).
        env_vars = [e for e in env_vars if e.get("name") != "EFS_ACCESS_POINT"]
        env_vars.append({"name": "EFS_ACCESS_POINT",
                         "value": "true" if access_point_id else "false"})

        # Check if service already exists
        service_exists = False
        try:
            desc = ecs.describe_services(cluster=ecs_cfg["cluster"], services=[service_name])
            active = [s for s in desc.get("services", []) if s["status"] == "ACTIVE"]
            if active:
                service_exists = True
        except Exception:
            pass

        # Register a new Task Definition revision with per-employee config.
        base_td = ecs.describe_task_definition(taskDefinition=ecs_cfg["task_def"])["taskDefinition"]

        # Clean container definitions: remove fields that register_task_definition rejects
        clean_containers = []
        for cd in base_td.get("containerDefinitions", []):
            clean_cd = {k: v for k, v in cd.items()
                        if k not in ("cpu", "status", "taskDefinitionArn", "containerInstanceArn",
                                     "networkBindings", "requiredAttributes")}
            if clean_cd["name"] == "always-on-agent":
                clean_cd["environment"] = env_vars
            clean_containers.append(clean_cd)

        # Build volumes — use Access Point if created
        volumes = base_td.get("volumes", [])
        if access_point_id:
            volumes = []
            for v in base_td.get("volumes", []):
                if v.get("name") == "always-on-workspace":
                    v = {
                        "name": "always-on-workspace",
                        "efsVolumeConfiguration": {
                            "fileSystemId": efs_id,
                            "rootDirectory": "/",
                            "transitEncryption": "ENABLED",
                            "authorizationConfig": {
                                "accessPointId": access_point_id,
                                "iam": "ENABLED",
                            },
                        },
                    }
                volumes.append(v)

        agent_family = f"{stack}-ao-{service_name}"
        agent_td = ecs.register_task_definition(
            family=agent_family,
            taskRoleArn=tier_role_arn,
            executionRoleArn=base_td["executionRoleArn"],
            networkMode="awsvpc",
            containerDefinitions=clean_containers,
            volumes=volumes,
            requiresCompatibilities=["FARGATE"],
            cpu=tier_cpu,
            memory=tier_memory,
            runtimePlatform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        )
        agent_td_arn = agent_td["taskDefinition"]["taskDefinitionArn"]
        print(f"[always-on] Registered task def: {agent_td_arn}")

        if service_exists:
            # Update existing service: new task def + scale to 1
            ecs.update_service(
                cluster=ecs_cfg["cluster"],
                service=service_name,
                taskDefinition=agent_td_arn,
                desiredCount=1,
                forceNewDeployment=True,
            )
            print(f"[always-on] Updated service {service_name} to desiredCount=1")
        else:
            # Create new service with per-tier Security Group
            sg_to_use = tier_sg or ecs_cfg["sg_id"]
            ecs.create_service(
                cluster=ecs_cfg["cluster"],
                serviceName=service_name,
                taskDefinition=agent_td_arn,
                desiredCount=1,
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets":        [ecs_cfg["subnet_id"]],
                        "securityGroups": [sg_to_use],
                        "assignPublicIp": "ENABLED",
                    }
                },
                tags=[
                    {"key": "agent_id",   "value": agent_id},
                    {"key": "employee_id", "value": emp_id},
                    {"key": "tier",       "value": tier},
                    {"key": "stack_name", "value": stack},
                ],
            )
            print(f"[always-on] Created service {service_name}")

    except Exception as e:
        raise HTTPException(500, f"Failed to start ECS service: {e}")

    # Update DynamoDB — AGENT# + EMP# records
    try:
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        table = ddb.Table(ddb_table)
        table.update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET deployMode = :m, containerStatus = :s, ecsServiceName = :sn, alwaysOnTier = :t",
            ExpressionAttributeValues={
                ":m": "always-on-ecs", ":s": "starting", ":sn": service_name, ":t": tier,
            },
        )
        # Also update EMP# record for quick lookup
        if emp_id:
            table.update_item(
                Key={"PK": "ORG#acme", "SK": f"EMP#{emp_id}"},
                UpdateExpression="SET alwaysOnEnabled = :e, alwaysOnTier = :t, alwaysOnServiceName = :sn, alwaysOnAccessPointId = :ap",
                ExpressionAttributeValues={
                    ":e": True, ":t": tier, ":sn": service_name,
                    ":ap": access_point_id or "",
                },
            )
    except Exception as e:
        print(f"[always-on] DynamoDB update failed: {e}")

    return {
        "started": True, "agentId": agent_id, "employeeId": emp_id,
        "serviceName": service_name, "tier": tier,
        "taskRole": tier_role_arn.split("/")[-1],
        "securityGroup": tier_sg or ecs_cfg["sg_id"],
        "accessPoint": access_point_id,
        "cpu": tier_cpu, "memory": tier_memory,
        "note": f"ECS Service created ({tier} tier, {tier_cpu} CPU / {tier_memory} MB). Container starts in ~30s.",
    }


@router.post("/api/v1/admin/always-on/{agent_id}/stop")
def stop_always_on_agent(agent_id: str, authorization: str = Header(default="")):
    """Stop the always-on agent by scaling its ECS Service to 0.
    Service definition is preserved — start will scale back to 1."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    service_name = _ecs_service_name(agent_id)

    try:
        ecs_cfg = _get_ecs_config()
        ecs = boto3.client("ecs", region_name=GATEWAY_REGION)

        # Scale ECS Service to 0 (preferred — keeps service definition)
        try:
            ecs.update_service(
                cluster=ecs_cfg["cluster"],
                service=service_name,
                desiredCount=0,
            )
            print(f"[always-on] Scaled service {service_name} to 0")
        except ecs.exceptions.ServiceNotFoundException:
            # No service — try legacy RunTask stop
            try:
                ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
                task_arn = ssm.get_parameter(
                    Name=f"/openclaw/{stack}/always-on/{agent_id}/task-arn"
                )["Parameter"]["Value"]
                ecs.stop_task(cluster=ecs_cfg["cluster"], task=task_arn,
                             reason="Stopped by admin")
            except Exception:
                pass
    except Exception as e:
        print(f"[always-on] ECS stop failed: {e}")

    # Clean up SSM endpoint (task will deregister on SIGTERM, but clean up just in case)
    try:
        ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
        for suffix in ["/task-arn", "/endpoint"]:
            try:
                ssm.delete_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}{suffix}")
            except Exception:
                pass
    except Exception:
        pass

    # Update DynamoDB
    try:
        ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        ddb.Table(os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))).update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET deployMode = :m, containerStatus = :s",
            ExpressionAttributeValues={":m": "serverless", ":s": "stopped"},
        )
    except Exception:
        pass

    return {"stopped": True, "agentId": agent_id, "serviceName": service_name}


@router.put("/api/v1/admin/always-on/{agent_id}/tokens")
def set_always_on_tokens(agent_id: str, body: dict, authorization: str = Header(default="")):
    """Store IM bot tokens for a always-on agent (Plan A: direct IM connection).
    Tokens are stored as SSM SecureStrings and injected at ECS task startup."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
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


@router.get("/api/v1/admin/always-on/{agent_id}/tokens")
def get_always_on_tokens(agent_id: str, authorization: str = Header(default="")):
    """Check which IM tokens are configured for an always-on agent (masked)."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
    result = {}
    for channel, key in [("telegram", "telegram-token"), ("discord", "discord-token")]:
        try:
            ssm.get_parameter(Name=f"/openclaw/{stack}/always-on/{agent_id}/{key}")
            result[channel] = "configured"  # don't return actual token
        except Exception:
            result[channel] = "not_configured"
    return result


@router.post("/api/v1/admin/always-on/{agent_id}/reload")
def reload_always_on_agent(agent_id: str, body: dict, authorization: str = Header(default="")):
    """Reload an always-on container via ECS Service force-new-deployment.
    ECS gracefully replaces the running task with a fresh one using the latest image.
    If env vars changed (bot tokens, config), re-registers the task definition first."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
    ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))

    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    ecs_cfg = _get_ecs_config()
    service_name = _ecs_service_name(agent_id)

    # Resolve per-tier config so reload preserves the employee's tier (model, CPU,
    # memory, task role) instead of silently regressing to base/standard defaults.
    emp_id = agent.get("employeeId", agent_id)
    tier = _resolve_tier(emp_id)
    _, _, tier_cpu, tier_memory = _TIER_DEFAULTS.get(tier, _TIER_DEFAULTS["standard"])
    tier_role_arn = _get_tier_role_arn(stack, tier)

    telegram_token, discord_token = _resolve_bot_tokens(stack, agent_id)
    env_vars = _build_agent_env(agent, agent_id, stack, bucket,
                                ddb_table, ddb_region, telegram_token, discord_token,
                                tier=tier)

    try:
        ecs = boto3.client("ecs", region_name=GATEWAY_REGION)

        # Re-register task definition with latest env vars
        base_td = ecs.describe_task_definition(taskDefinition=ecs_cfg["task_def"])["taskDefinition"]
        clean_containers = []
        for cd in base_td.get("containerDefinitions", []):
            clean_cd = {k: v for k, v in cd.items()
                        if k not in ("cpu", "status", "taskDefinitionArn", "containerInstanceArn",
                                     "networkBindings", "requiredAttributes")}
            if clean_cd["name"] == "always-on-agent":
                clean_cd["environment"] = env_vars
            clean_containers.append(clean_cd)

        agent_family = f"{stack}-ao-{service_name}"
        agent_td = ecs.register_task_definition(
            family=agent_family,
            taskRoleArn=tier_role_arn,
            executionRoleArn=base_td["executionRoleArn"],
            networkMode="awsvpc",
            containerDefinitions=clean_containers,
            volumes=base_td.get("volumes", []),
            requiresCompatibilities=["FARGATE"],
            cpu=tier_cpu,
            memory=tier_memory,
            runtimePlatform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        )
        agent_td_arn = agent_td["taskDefinition"]["taskDefinitionArn"]

        # Force new deployment — ECS replaces running task with new one
        ecs.update_service(
            cluster=ecs_cfg["cluster"],
            service=service_name,
            taskDefinition=agent_td_arn,
            forceNewDeployment=True,
        )
    except Exception as e:
        raise HTTPException(500, f"Reload failed: {e}")

    try:
        boto3.resource("dynamodb", region_name=ddb_region).Table(ddb_table).update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET containerStatus = :s",
            ExpressionAttributeValues={":s": "reloading"},
        )
    except Exception:
        pass

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": "admin", "actorName": "Admin",
        "targetType": "agent", "targetId": agent_id,
        "detail": f"Container reloaded via ECS Service force-new-deployment", "status": "success",
    })
    return {"reloaded": True, "agentId": agent_id, "serviceName": service_name,
            "note": "ECS replacing container (~30s). New config active on next message."}


@router.get("/api/v1/admin/always-on/{agent_id}/images")
def list_agent_images(agent_id: str, authorization: str = Header(default="")):
    """List available ECR image tags for deploying to this always-on agent."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    try:
        ecr = boto3.client("ecr", region_name=GATEWAY_REGION)
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


@router.get("/api/v1/admin/always-on/{agent_id}/status")
def get_always_on_status(agent_id: str, authorization: str = Header(default="")):
    """Get status of an always-on ECS Fargate task."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    ssm = boto3.client("ssm", region_name=GATEWAY_REGION)

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
        ecs_cfg = _get_ecs_config()
        desc = boto3.client("ecs", region_name=GATEWAY_REGION).describe_tasks(
            cluster=ecs_cfg["cluster"], tasks=[task_arn])
        tasks = desc.get("tasks", [])
        ecs_status = tasks[0].get("lastStatus", "UNKNOWN") if tasks else "NOT_FOUND"
    except Exception:
        pass

    running = ecs_status == "RUNNING"
    return {"running": running, "endpoint": endpoint or None,
            "agentId": agent_id, "taskArn": task_arn, "ecsStatus": ecs_status}


@router.put("/api/v1/admin/always-on/{agent_id}/assign/{emp_id}")
def assign_always_on_to_employee(agent_id: str, emp_id: str, authorization: str = Header(default="")):
    """Assign an employee to use the always-on agent instead of AgentCore."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    ssm = boto3.client("ssm", region_name=GATEWAY_REGION)
    ssm.put_parameter(
        Name=f"/openclaw/{stack}/tenants/{emp_id}/always-on-agent",
        Value=agent_id, Type="String", Overwrite=True,
    )
    return {"assigned": True, "empId": emp_id, "agentId": agent_id}


@router.delete("/api/v1/admin/always-on/{agent_id}/assign/{emp_id}")
def unassign_always_on_from_employee(agent_id: str, emp_id: str, authorization: str = Header(default="")):
    """Remove employee's always-on assignment — they fall back to AgentCore."""
    require_role(authorization, roles=["admin"])
    stack = os.environ.get("STACK_NAME", "openclaw")
    try:
        boto3.client("ssm", region_name=GATEWAY_REGION).delete_parameter(
            Name=f"/openclaw/{stack}/tenants/{emp_id}/always-on-agent"
        )
    except Exception:
        pass
    return {"unassigned": True, "empId": emp_id}


# =========================================================================
# Agent Refresh — force workspace reload via StopRuntimeSession
# =========================================================================

@router.post("/api/v1/agents/{emp_id}/refresh")
def refresh_agent(emp_id: str, authorization: str = Header(default="")):
    """Force an agent to reload its workspace on next invocation.
    Calls StopRuntimeSession for all session types (emp, twin, pgnd).
    Used after config changes that need immediate propagation."""
    require_role(authorization, roles=["admin", "manager"])
    result = stop_employee_session(emp_id)
    # Audit trail
    user = require_auth(authorization)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "agent_refresh",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "agent",
        "targetId": emp_id,
        "detail": f"Agent refresh triggered for {emp_id} by {user.name}",
        "status": "success",
    })
    return {"refreshed": True, "empId": emp_id, "result": result,
            "note": "Agent will reload workspace on next message."}
