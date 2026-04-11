"""
Security — SOUL policies, tool permissions, runtimes, guardrails, infrastructure.

Endpoints: /api/v1/security/*, /api/v1/audit/guardrail-events
"""

import os
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
import s3ops
from shared import (
    require_role, ssm_client, bump_config_version, audit_soul_change,
    GATEWAY_REGION, STACK_NAME, DYNAMODB_REGION, DYNAMODB_TABLE,
)

router = APIRouter(tags=["security"])


# ── SOUL Management ──────────────────────────────────────────────────────

@router.get("/api/v1/security/global-soul")
def get_global_soul(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        bucket = s3ops.bucket()
        key = "_shared/soul/global/SOUL.md"
        body = s3ops._client().get_object(Bucket=bucket, Key=key)["Body"].read().decode()
        return {"content": body, "key": key}
    except Exception as e:
        return {"content": "", "key": "_shared/soul/global/SOUL.md", "error": str(e)}


@router.put("/api/v1/security/global-soul")
def put_global_soul(body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    bucket = s3ops.bucket()
    content = body.get("content", "")
    s3ops._client().put_object(Bucket=bucket, Key="_shared/soul/global/SOUL.md",
                               Body=content.encode(), ContentType="text/markdown")
    bump_config_version()
    audit_soul_change(user, "global", "global", len(content))
    return {"saved": True}


@router.get("/api/v1/security/positions/{pos_id}/soul")
def get_position_soul(pos_id: str, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        bucket = s3ops.bucket()
        key = f"_shared/soul/positions/{pos_id}/SOUL.md"
        body = s3ops._client().get_object(Bucket=bucket, Key=key)["Body"].read().decode()
        return {"content": body, "key": key}
    except Exception as e:
        return {"content": "", "key": f"_shared/soul/positions/{pos_id}/SOUL.md", "error": str(e)}


@router.put("/api/v1/security/positions/{pos_id}/soul")
def put_position_soul(pos_id: str, body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    bucket = s3ops.bucket()
    content = body.get("content", "")
    s3ops._client().put_object(Bucket=bucket, Key=f"_shared/soul/positions/{pos_id}/SOUL.md",
                               Body=content.encode(), ContentType="text/markdown")
    bump_config_version()
    audit_soul_change(user, "position", pos_id, len(content))
    return {"saved": True}


# ── Tool Permissions ─────────────────────────────────────────────────────

@router.get("/api/v1/security/positions/{pos_id}/tools")
def get_position_tools(pos_id: str, authorization: str = Header(default="")):
    """Read tool permissions for a position from DynamoDB POS# record."""
    require_role(authorization, roles=["admin"])
    positions = db.get_positions()
    pos = next((p for p in positions if p["id"] == pos_id), None)
    if pos:
        tools = pos.get("toolAllowlist", ["web_search"])
        return {"profile": pos_id.replace("pos-", ""), "tools": tools}
    return {"profile": "basic", "tools": ["web_search"]}


@router.put("/api/v1/security/positions/{pos_id}/tools")
def put_position_tools(pos_id: str, body: dict, authorization: str = Header(default="")):
    """Write tool permissions for a position to DynamoDB POS# record."""
    require_role(authorization, roles=["admin"])
    tools = body.get("tools", [])
    try:
        import boto3 as _b3_sec
        ddb = _b3_sec.resource("dynamodb", region_name=GATEWAY_REGION)
        table = ddb.Table(os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw")))
        table.update_item(
            Key={"PK": "ORG#acme", "SK": f"POS#{pos_id}"},
            UpdateExpression="SET toolAllowlist = :tools",
            ExpressionAttributeValues={":tools": tools},
        )
    except Exception as e:
        print(f"[security] position tools write failed: {e}")
    emps = db.get_employees()
    return {"saved": True, "propagated": len([e for e in emps if e.get("positionId") == pos_id])}


# ── Runtime Assignment ───────────────────────────────────────────────────

@router.get("/api/v1/security/positions/{pos_id}/runtime")
def get_position_runtime(pos_id: str, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3pr
        ssm = _b3pr.client("ssm", region_name=GATEWAY_REGION)
        resp = ssm.get_parameter(Name=f"/openclaw/{STACK_NAME}/positions/{pos_id}/runtime-id")
        return {"posId": pos_id, "runtimeId": resp["Parameter"]["Value"]}
    except Exception:
        return {"posId": pos_id, "runtimeId": None}


@router.put("/api/v1/security/positions/{pos_id}/runtime")
def put_position_runtime(pos_id: str, body: dict, authorization: str = Header(default="")):
    """Assign a runtime to a position. Propagates to all employees."""
    require_role(authorization, roles=["admin"])
    runtime_id = body.get("runtimeId", "")
    if not runtime_id:
        raise HTTPException(400, "runtimeId required")
    import boto3 as _b3pr2
    ssm = _b3pr2.client("ssm", region_name=GATEWAY_REGION)
    ssm.put_parameter(
        Name=f"/openclaw/{STACK_NAME}/positions/{pos_id}/runtime-id",
        Value=runtime_id, Type="String", Overwrite=True,
    )
    emps = db.get_employees()
    propagated = []
    for emp in emps:
        if emp.get("positionId") == pos_id:
            try:
                ssm.put_parameter(
                    Name=f"/openclaw/{STACK_NAME}/tenants/{emp['id']}/runtime-id",
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


@router.delete("/api/v1/security/positions/{pos_id}/runtime")
def delete_position_runtime(pos_id: str, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    import boto3 as _b3pr3
    ssm = _b3pr3.client("ssm", region_name=GATEWAY_REGION)
    try:
        ssm.delete_parameter(Name=f"/openclaw/{STACK_NAME}/positions/{pos_id}/runtime-id")
    except Exception:
        pass
    return {"deleted": True, "posId": pos_id}


@router.get("/api/v1/security/position-runtime-map")
def get_position_runtime_map(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    import boto3 as _b3prm
    ssm = _b3prm.client("ssm", region_name=GATEWAY_REGION)
    result = {}
    try:
        prefix = f"/openclaw/{STACK_NAME}/positions/"
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


# ── Runtimes (AgentCore) ─────────────────────────────────────────────────

@router.get("/api/v1/security/runtimes")
def get_security_runtimes(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3r
        ac = _b3r.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)
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


@router.put("/api/v1/security/runtimes/{runtime_id}/lifecycle")
def update_runtime_lifecycle(runtime_id: str, body: dict, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3r2
        ac = _b3r2.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)
        detail = ac.get_agent_runtime(agentRuntimeId=runtime_id)
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


@router.put("/api/v1/security/runtimes/{runtime_id}/config")
def update_runtime_config(runtime_id: str, body: dict, authorization: str = Header(default="")):
    """Full runtime config update: image, roleArn, security groups, model, lifecycle."""
    require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3rc
        ac = _b3rc.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)
        detail = ac.get_agent_runtime(agentRuntimeId=runtime_id)

        container_uri = body.get("containerUri") or detail["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"]
        artifact = {"containerConfiguration": {"containerUri": container_uri}}

        network_mode = body.get("networkMode", detail.get("networkConfiguration", {}).get("networkMode", "PUBLIC"))
        network_cfg: dict = {"networkMode": network_mode}
        if network_mode == "VPC":
            sg_ids = body.get("securityGroupIds", [])
            subnet_ids = body.get("subnetIds", [])
            if sg_ids and subnet_ids:
                network_cfg["networkModeConfig"] = {"securityGroups": sg_ids, "subnets": subnet_ids}

        existing_env = detail.get("environmentVariables") or {}
        new_env = dict(existing_env)
        if body.get("modelId"):
            new_env["BEDROCK_MODEL_ID"] = body["modelId"]

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


@router.post("/api/v1/security/runtimes/create")
def create_runtime(body: CreateRuntimeRequest, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3cr
        ac = _b3cr.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)

        network_cfg: dict = {"networkMode": body.networkMode}
        if body.networkMode == "VPC" and body.securityGroupIds and body.subnetIds:
            network_cfg["networkModeConfig"] = {
                "securityGroups": body.securityGroupIds,
                "subnets": body.subnetIds,
            }

        stack = STACK_NAME
        from shared import GATEWAY_ACCOUNT_ID
        bucket = os.environ.get("S3_BUCKET", f"openclaw-tenants-{GATEWAY_ACCOUNT_ID}")
        region = os.environ.get("AWS_REGION", "us-east-1")
        ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))

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


# ── Guardrails ───────────────────────────────────────────────────────────

@router.get("/api/v1/security/guardrails")
def list_guardrails(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        import boto3 as _b3gr
        bedrock = _b3gr.client("bedrock", region_name=GATEWAY_REGION)
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


@router.get("/api/v1/audit/guardrail-events")
def get_guardrail_events(authorization: str = Header(default=""), limit: int = 50):
    """Fetch guardrail_block audit events from DynamoDB."""
    require_role(authorization, roles=["admin", "manager"])
    try:
        import boto3 as _b3ge
        from boto3.dynamodb.conditions import Key
        table = _b3ge.resource("dynamodb", region_name=DYNAMODB_REGION).Table(DYNAMODB_TABLE)
        resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq("TYPE#audit"),
            ScanIndexForward=False,
            Limit=limit * 5,
        )
        events = [item for item in resp.get("Items", []) if item.get("eventType") == "guardrail_block"]
        events = events[:limit]
        for e in events:
            e.pop("PK", None); e.pop("SK", None)
            e.pop("GSI1PK", None); e.pop("GSI1SK", None)
        return {"events": events}
    except Exception as e:
        return {"events": [], "error": str(e)}


# ── Infrastructure Resources ─────────────────────────────────────────────

@router.get("/api/v1/security/ecr-images")
def list_ecr_images(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    import boto3 as _b3ecr
    ecr = _b3ecr.client("ecr", region_name=GATEWAY_REGION)
    result = []
    try:
        repos = ecr.describe_repositories().get("repositories", [])
        for repo in repos:
            try:
                imgs = ecr.describe_images(
                    repositoryName=repo["repositoryName"],
                    filter={"tagStatus": "TAGGED"}
                ).get("imageDetails", [])
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


@router.get("/api/v1/security/iam-roles")
def list_iam_roles(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    import boto3 as _b3iam
    iam = _b3iam.client("iam")
    result = []
    try:
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


@router.get("/api/v1/security/vpc-resources")
def list_vpc_resources(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    import boto3 as _b3vpc
    ec2 = _b3vpc.client("ec2", region_name=GATEWAY_REGION)
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


@router.get("/api/v1/security/infrastructure")
def get_infrastructure(authorization: str = Header(default="")):
    """Aggregate view: ECR + IAM + VPC — run in parallel for speed."""
    require_role(authorization, roles=["admin"])

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
            except Exception:
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
