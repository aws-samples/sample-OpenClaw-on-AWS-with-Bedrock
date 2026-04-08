"""
EKS Agents — Deploy, manage, and route agents on Kubernetes.

Architecture: Admin Console -> K8s API -> OpenClawInstance CRD ->
Operator reconciles -> StatefulSet + Service + PVC.
Tenant Router reads SSM eks-endpoint -> routes to K8s Service.

Endpoints:
  /api/v1/admin/eks/operator/*    (operator management — must be before {agent_id})
  /api/v1/admin/eks/{agent_id}/*  (agent lifecycle)
"""

import asyncio
import copy
import logging
import os
from datetime import datetime, timezone

import boto3
import requests as _requests

from fastapi import APIRouter, HTTPException, Header, Request, Response, WebSocket, WebSocketDisconnect

import db
import s3ops
from shared import require_role, ssm_client, STACK_NAME, GATEWAY_REGION
from services.k8s_client import k8s_client, OPENCLAW_NAMESPACE, OPERATOR_NAMESPACE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin-eks"])

IS_CHINA_REGION = os.environ.get("AWS_REGION", "").startswith("cn-")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*.

    - dict values are merged recursively
    - list values in *override* replace the base (no append)
    - scalar values in *override* replace the base
    """
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _agent_svc_endpoint(agent_name: str) -> str:
    """Derive the in-cluster Service endpoint for an EKS agent."""
    return f"http://{agent_name}.{OPENCLAW_NAMESPACE}.svc:18789"


# Cache gateway tokens per agent (TTL 5 min)
_gw_token_cache: dict = {}  # agent_name -> (token, timestamp)

async def _get_gateway_token(agent_name: str) -> str:
    """Fetch the gateway token from the running pod's openclaw.json.
    Cached for 5 minutes to avoid repeated exec calls."""
    import time, json as _json
    from services.k8s_client import _sanitize_k8s_name
    safe = _sanitize_k8s_name(agent_name)
    cached = _gw_token_cache.get(safe)
    if cached and time.time() - cached[1] < 300:
        return cached[0]
    try:
        # Read config from the pod via HTTP — the gateway exposes /api/health
        resp = _requests.get(
            f"http://{safe}.{OPENCLAW_NAMESPACE}.svc:18789/api/health",
            timeout=3,
        )
        # If health works without token, try fetching the token from pod exec
    except Exception:
        pass
    # Fallback: exec into pod to read the config
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl", "exec", "-n", OPENCLAW_NAMESPACE,
            f"{safe}-0", "-c", "openclaw", "--",
            "cat", "/home/openclaw/.openclaw/openclaw.json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        cfg = _json.loads(stdout)
        token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
        if token:
            _gw_token_cache[safe] = (token, time.time())
            return token
    except Exception as e:
        logger.warning("Failed to read gateway token for %s: %s", safe, e)
    return ""


# =========================================================================
# Cluster Discovery & Association (fixed-path routes — before {agent_id})
# =========================================================================

@router.get("/api/v1/admin/eks/cluster")
async def get_cluster_config(authorization: str = Header(default="")):
    """Get the currently associated EKS cluster configuration.

    Returns:
      - configured: True if a cluster is associated (SSM) or running in-cluster
      - in_cluster: True if admin console is running inside K8s (auto-detected)
      - operator: operator status (when K8s API is reachable)
    """
    require_role(authorization, roles=["admin"])

    from services.k8s_client import K8S_IN_CLUSTER

    # Check SSM for explicitly associated cluster
    ssm = ssm_client()
    cluster = {}
    for key in ["cluster-name", "cluster-endpoint", "cluster-region", "cluster-version"]:
        try:
            r = ssm.get_parameter(Name=f"/openclaw/{STACK_NAME}/eks/{key}")
            cluster[key.replace("-", "_")] = r["Parameter"]["Value"]
        except Exception:
            pass

    configured = bool(cluster.get("cluster_name")) or K8S_IN_CLUSTER

    if not configured:
        return {"configured": False, "in_cluster": False}

    # Get operator status if cluster is reachable
    operator = {}
    try:
        operator = await k8s_client.get_operator_status()
    except Exception:
        operator = {"installed": False, "error": "Cannot reach K8s API"}

    return {
        "configured": configured,
        "in_cluster": K8S_IN_CLUSTER,
        **cluster,
        "operator": operator,
    }


@router.get("/api/v1/admin/eks/clusters/discover")
async def discover_clusters(authorization: str = Header(default="")):
    """List available EKS clusters in the current AWS region."""
    require_role(authorization, roles=["admin"])

    region = os.environ.get("EKS_REGION", GATEWAY_REGION)
    try:
        eks = boto3.client("eks", region_name=region)
        names = eks.list_clusters()["clusters"]
    except Exception as e:
        raise HTTPException(502, f"Failed to list EKS clusters: {e}")

    clusters = []
    for name in names:
        try:
            info = eks.describe_cluster(name=name)["cluster"]
            clusters.append({
                "name": info["name"],
                "status": info["status"],
                "endpoint": info["endpoint"],
                "version": info.get("version", ""),
                "region": region,
                "arn": info["arn"],
                "platformVersion": info.get("platformVersion", ""),
                "vpcId": info.get("resourcesVpcConfig", {}).get("vpcId", ""),
            })
        except Exception:
            clusters.append({"name": name, "status": "UNKNOWN", "region": region})

    return {"clusters": clusters, "region": region}


@router.post("/api/v1/admin/eks/cluster")
async def associate_cluster(body: dict, authorization: str = Header(default="")):
    """Associate an EKS cluster with this admin console.

    Saves the cluster config to SSM and updates kubeconfig so
    the k8s_client can reach the cluster's API server.

    Body: { name: str, region?: str }
    """
    require_role(authorization, roles=["admin"])

    cluster_name = body.get("name", "").strip()
    if not cluster_name:
        raise HTTPException(400, "Cluster name required")

    region = body.get("region", os.environ.get("EKS_REGION", GATEWAY_REGION))

    # Describe the cluster to validate and get endpoint
    try:
        eks = boto3.client("eks", region_name=region)
        info = eks.describe_cluster(name=cluster_name)["cluster"]
    except Exception as e:
        raise HTTPException(404, f"Cluster '{cluster_name}' not found in {region}: {e}")

    if info["status"] != "ACTIVE":
        raise HTTPException(400, f"Cluster is {info['status']}, must be ACTIVE to associate")

    # Save to SSM
    ssm = ssm_client()
    params = {
        "cluster-name": cluster_name,
        "cluster-endpoint": info["endpoint"],
        "cluster-region": region,
        "cluster-version": info.get("version", ""),
    }
    for key, value in params.items():
        ssm.put_parameter(
            Name=f"/openclaw/{STACK_NAME}/eks/{key}",
            Value=value, Type="String", Overwrite=True,
        )

    # Update kubeconfig so k8s_client can connect
    proc = await asyncio.create_subprocess_exec(
        "aws", "eks", "update-kubeconfig",
        "--name", cluster_name,
        "--region", region,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    kubeconfig_ok = proc.returncode == 0

    # Reset k8s_client so it re-initializes with new kubeconfig
    try:
        await k8s_client.close()
    except Exception:
        pass

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": "admin", "actorName": "Admin",
        "targetType": "eks-cluster", "targetId": cluster_name,
        "detail": f"Associated EKS cluster {cluster_name} ({region}, K8s {info.get('version', '?')})",
        "status": "success",
    })

    return {
        "associated": True,
        "cluster_name": cluster_name,
        "cluster_endpoint": info["endpoint"],
        "cluster_region": region,
        "cluster_version": info.get("version", ""),
        "kubeconfig_updated": kubeconfig_ok,
        "kubeconfig_error": stderr.decode().strip() if not kubeconfig_ok else None,
    }


@router.get("/api/v1/admin/eks/instances")
async def list_eks_instances(authorization: str = Header(default="")):
    """List all OpenClawInstance CRDs in the EKS namespace."""
    require_role(authorization, roles=["admin"])
    try:
        instances = await k8s_client.list_openclaw_instances(OPENCLAW_NAMESPACE)
    except Exception as e:
        raise HTTPException(502, f"Cannot list instances: {e}")
    return {"instances": instances, "namespace": OPENCLAW_NAMESPACE}


@router.delete("/api/v1/admin/eks/cluster")
async def disassociate_cluster(authorization: str = Header(default="")):
    """Remove the EKS cluster association."""
    require_role(authorization, roles=["admin"])

    ssm = ssm_client()
    for key in ["cluster-name", "cluster-endpoint", "cluster-region", "cluster-version"]:
        try:
            ssm.delete_parameter(Name=f"/openclaw/{STACK_NAME}/eks/{key}")
        except Exception:
            pass

    try:
        await k8s_client.close()
    except Exception:
        pass

    return {"disassociated": True}


# =========================================================================
# Operator Management (MUST be registered before {agent_id} routes
# so FastAPI doesn't match "operator" as an agent_id)
# =========================================================================

@router.get("/api/v1/admin/eks/operator/status")
async def get_operator_status(authorization: str = Header(default="")):
    """Check if the OpenClaw operator is installed and running on the EKS cluster.

    Returns CRD registration, deployment status, pod health, and version."""
    require_role(authorization, roles=["admin"])

    try:
        status = await k8s_client.get_operator_status()
    except Exception as e:
        raise HTTPException(502, f"Cannot reach K8s API: {e}")

    return status


    # Operator install/upgrade removed — use install.sh or Terraform to deploy
    # the operator before deploying agents. The status endpoint above is kept
    # so the admin UI can display whether the operator is healthy.


# =========================================================================
# Agent Config (read current CRD spec.config.raw for UI editing)
# =========================================================================

@router.get("/api/v1/admin/eks/{agent_id}/config")
async def get_eks_agent_config(agent_id: str, authorization: str = Header(default="")):
    """Return the current spec.config.raw from the OpenClawInstance CRD.

    Used by the UI config editor so admins can view and override the full
    openclaw.json config — custom model providers, tool settings, agent
    defaults, etc. — without us building specific UI for each field.
    """
    require_role(authorization, roles=["admin"])

    crd = await k8s_client.get_openclaw_instance(OPENCLAW_NAMESPACE, agent_id)
    if not crd:
        raise HTTPException(404, f"Agent {agent_id} not found in EKS")

    raw = crd.get("spec", {}).get("config", {}).get("raw", {})
    merge_mode = crd.get("spec", {}).get("config", {}).get("mergeMode", "merge")
    return {"agentId": agent_id, "config": raw, "mergeMode": merge_mode}


# =========================================================================
# Agent Lifecycle (EKS)
# =========================================================================

def _build_workspace_files(pos_id: str, employee_id: str) -> dict:
    """Fetch SOUL layers from S3 and build workspace files for the CRD.

    Reuses the same s3ops module that the admin console UI uses for SOUL
    editing, so the three-layer SOUL (global + position + personal) is
    consistent across all runtimes.

    Returns a dict of filename -> content suitable for spec.workspace.initialFiles.
    """
    files = {}
    try:
        layers = s3ops.get_soul_layers(pos_id, employee_id)

        # Assemble merged SOUL.md (same order as workspace_assembler.py)
        soul_parts = []
        if layers["global"].get("SOUL.md"):
            soul_parts.append(layers["global"]["SOUL.md"])
        if layers["position"].get("SOUL.md"):
            soul_parts.append(layers["position"]["SOUL.md"])
        if layers["personal"].get("SOUL.md"):
            soul_parts.append(layers["personal"]["SOUL.md"])
        if soul_parts:
            files["SOUL.md"] = "\n\n---\n\n".join(soul_parts)

        # Optional workspace files
        for key in ["AGENTS.md", "TOOLS.md"]:
            content = layers["global"].get(key, "")
            if content:
                files[key] = content
        if layers["position"].get("AGENTS.md"):
            # Append position-level agents to global
            existing = files.get("AGENTS.md", "")
            files["AGENTS.md"] = existing + "\n\n" + layers["position"]["AGENTS.md"] if existing else layers["position"]["AGENTS.md"]
        if layers["personal"].get("USER.md"):
            files["USER.md"] = layers["personal"]["USER.md"]
    except Exception as e:
        print(f"[eks] Failed to fetch SOUL layers for {employee_id}: {e}")

    return files


@router.post("/api/v1/admin/eks/{agent_id}/deploy")
async def deploy_eks_agent(agent_id: str, body: dict = {}, authorization: str = Header(default="")):
    """Deploy an agent to EKS by creating an OpenClawInstance CRD.

    Uses the same agent-container image as ECS Fargate, sharing entrypoint.sh,
    server.py, and workspace_assembler.py. The operator handles StatefulSet,
    Service, PVC, and ConfigMap creation from the CRD spec.

    Body (all optional):
      - model: Bedrock model (default: env DEFAULT_MODEL)
      - image: Container image URI for openclaw (default: env AGENT_ECR_IMAGE)
      - globalRegistry: Global registry override for ALL images (default: env OPENCLAW_REGISTRY).
            Required for China where ghcr.io is inaccessible. Images must be pre-mirrored.
      - bedrockRoleArn: IAM role for Bedrock IRSA
      - skills: list of ClawHub skill identifiers to install
      - storageClass: K8s StorageClass name (default: cluster default)
      - storageSize: PVC size (default: "10Gi")
      - cpuRequest/cpuLimit/memoryRequest/memoryLimit: compute resources
      - runtimeClass: RuntimeClassName for VM-level isolation (e.g. "kata-qemu")
      - nodeSelector: dict of K8s node labels for scheduling
      - tolerations: list of K8s tolerations
      - chromium: bool, enable headless browser sidecar
      - backupSchedule: cron expression for S3 backups (e.g. "0 2 * * *")
      - serviceType: K8s Service type (ClusterIP, LoadBalancer, NodePort)
      - configOverride: dict — deep-merged into spec.config.raw to override
            any openclaw.json config (custom model providers, tool settings, etc.)
    """
    require_role(authorization, roles=["admin"])

    # Pre-flight: ensure the OpenClaw operator is installed
    operator_status = await k8s_client.get_operator_status()
    if not operator_status["installed"]:
        raise HTTPException(
            428,
            "OpenClaw operator is not installed on the EKS cluster. "
            "Call POST /api/v1/admin/eks/operator/install first, or install via "
            f"helm install openclaw-operator oci://ghcr.io/openclaw-rocks/charts/openclaw-operator "
            f"--namespace {OPERATOR_NAMESPACE} --create-namespace")

    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    emp_id = agent.get("employeeId", agent_id)
    pos_id = agent.get("positionId", "")
    model = body.get("model", os.environ.get(
        "DEFAULT_MODEL", "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"))
    image = body.get("image", os.environ.get("AGENT_ECR_IMAGE", ""))
    global_registry = body.get("globalRegistry", os.environ.get("OPENCLAW_REGISTRY", ""))
    bedrock_role_arn = body.get("bedrockRoleArn", os.environ.get("BEDROCK_ROLE_ARN", ""))
    skills = body.get("skills", [])
    storage_class = body.get("storageClass", os.environ.get("EKS_STORAGE_CLASS", ""))
    storage_size = body.get("storageSize", os.environ.get("EKS_STORAGE_SIZE", "10Gi"))
    cpu_request = body.get("cpuRequest", "500m")
    cpu_limit = body.get("cpuLimit", "2")
    memory_request = body.get("memoryRequest", "2Gi")
    memory_limit = body.get("memoryLimit", "4Gi")
    runtime_class = body.get("runtimeClass", "")
    node_selector = body.get("nodeSelector")
    tolerations = body.get("tolerations")
    chromium = body.get("chromium", False)
    backup_schedule = body.get("backupSchedule", "")
    service_type = body.get("serviceType", "")
    config_override = body.get("configOverride")

    # Validate configOverride is a dict if provided
    if config_override is not None and not isinstance(config_override, dict):
        raise HTTPException(400, "configOverride must be a JSON object")

    # Fetch SOUL layers from S3 and assemble workspace files
    workspace_files = _build_workspace_files(pos_id, emp_id)

    try:
        await k8s_client.create_openclaw_instance(
            namespace=OPENCLAW_NAMESPACE,
            agent_name=agent_id,
            employee_id=emp_id,
            position_id=pos_id,
            model=model,
            image=image,
            global_registry=global_registry,
            bedrock_role_arn=bedrock_role_arn,
            workspace_files=workspace_files or None,
            skills=skills or None,
            storage_class=storage_class,
            storage_size=storage_size,
            cpu_request=cpu_request,
            cpu_limit=cpu_limit,
            memory_request=memory_request,
            memory_limit=memory_limit,
            runtime_class=runtime_class,
            node_selector=node_selector,
            tolerations=tolerations,
            chromium=chromium,
            backup_schedule=backup_schedule,
            service_type=service_type,
            config_override=config_override,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to create OpenClawInstance: {e}")

    # Write SSM eks-endpoint so tenant_router can find this agent
    try:
        ssm = ssm_client()
        ssm.put_parameter(
            Name=f"/openclaw/{STACK_NAME}/tenants/{emp_id}/eks-endpoint",
            Value=_agent_svc_endpoint(agent_id),
            Type="String", Overwrite=True,
        )
    except Exception as e:
        print(f"[eks] SSM eks-endpoint write failed for {emp_id}: {e}")

    # Update DynamoDB
    ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
    ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
    try:
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        ddb.Table(ddb_table).update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET deployMode = :m, containerStatus = :s",
            ExpressionAttributeValues={":m": "eks", ":s": "starting"},
        )
    except Exception as e:
        print(f"[eks] DynamoDB update failed: {e}")

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": "admin", "actorName": "Admin",
        "targetType": "agent", "targetId": agent_id,
        "detail": f"Deployed agent to EKS (namespace={OPENCLAW_NAMESPACE}, model={model}, "
                  f"workspace_files={len(workspace_files)}, skills={len(skills)})",
        "status": "success",
    })

    return {"deployed": True, "agentId": agent_id, "namespace": OPENCLAW_NAMESPACE,
            "endpoint": _agent_svc_endpoint(agent_id),
            "workspaceFiles": list(workspace_files.keys()),
            "note": "OpenClawInstance CRD created. Pod starting (~60s)."}


@router.post("/api/v1/admin/eks/{agent_id}/stop")
async def stop_eks_agent(agent_id: str, authorization: str = Header(default="")):
    """Stop an EKS agent by deleting the OpenClawInstance CRD.
    The operator cleans up the StatefulSet, Service, and PVC."""
    require_role(authorization, roles=["admin"])

    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    emp_id = agent.get("employeeId", agent_id)

    try:
        result = await k8s_client.delete_openclaw_instance(OPENCLAW_NAMESPACE, agent_id)
    except Exception as e:
        raise HTTPException(500, f"Failed to delete OpenClawInstance: {e}")

    # Clean up SSM eks-endpoint
    try:
        ssm = ssm_client()
        ssm.delete_parameter(
            Name=f"/openclaw/{STACK_NAME}/tenants/{emp_id}/eks-endpoint")
    except Exception:
        pass

    # Update DynamoDB
    ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
    ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
    try:
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        ddb.Table(ddb_table).update_item(
            Key={"PK": "ORG#acme", "SK": f"AGENT#{agent_id}"},
            UpdateExpression="SET deployMode = :m, containerStatus = :s",
            ExpressionAttributeValues={":m": "serverless", ":s": "stopped"},
        )
    except Exception:
        pass

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": "admin", "actorName": "Admin",
        "targetType": "agent", "targetId": agent_id,
        "detail": f"Stopped EKS agent (deleted CRD from {OPENCLAW_NAMESPACE})",
        "status": "success",
    })

    return {"stopped": True, "agentId": agent_id, "result": result}


@router.post("/api/v1/admin/eks/{agent_id}/reload")
async def reload_eks_agent(agent_id: str, body: dict = {}, authorization: str = Header(default="")):
    """Reload an EKS agent by patching the CRD config version.
    The operator detects the change and restarts the pod with new config.

    Body (all optional):
      - model: update the Bedrock model
      - imageTag / image: update the container image
      - configOverride: dict — deep-merged into current spec.config.raw
    """
    require_role(authorization, roles=["admin"])

    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    # Bump configVersion annotation to trigger operator reconcile
    config_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    patch = {
        "metadata": {
            "annotations": {
                "openclaw.rocks/config-version": config_version,
            },
        },
    }

    # Optionally update image tag (or full repository:tag)
    image_updated = ""
    if body.get("imageTag") or body.get("image"):
        image_patch = {}
        if body.get("image"):
            # Full image URI: "repo:tag" or just "repo" (keeps current tag)
            parts = body["image"].rsplit(":", 1)
            image_patch["repository"] = parts[0]
            if len(parts) > 1:
                image_patch["tag"] = parts[1]
        elif body.get("imageTag"):
            image_patch["tag"] = body["imageTag"]
        patch.setdefault("spec", {})["image"] = image_patch
        image_updated = body.get("image") or body.get("imageTag")

    # Optionally update model — update both config.raw and BEDROCK_MODEL_ID env var
    if body.get("model"):
        new_model = body["model"]
        bedrock_model_id = new_model.split("/", 1)[1] if "/" in new_model else new_model
        aws_region = os.environ.get("AWS_REGION", "us-west-2")
        patch["spec"] = {
            "config": {
                "raw": {
                    "models": {
                        "providers": {
                            "amazon-bedrock": {
                                "baseUrl": f"https://bedrock-runtime.{aws_region}.amazonaws.com",
                                "models": [{
                                    "id": bedrock_model_id,
                                    "name": "Bedrock Model",
                                    "reasoning": False,
                                    "input": ["text"],
                                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                                    "contextWindow": 200000,
                                    "maxTokens": 8192,
                                }],
                            },
                        },
                    },
                    "agents": {
                        "defaults": {
                            "model": {"primary": f"amazon-bedrock/{bedrock_model_id}"},
                        },
                    },
                },
            },
        }

    # Apply custom config override — deep-merge user overrides into existing
    # CRD config.raw so admins can inject custom model providers, tool settings, etc.
    config_override = body.get("configOverride")
    if config_override:
        if not isinstance(config_override, dict):
            raise HTTPException(400, "configOverride must be a JSON object")
        # Read current config from CRD, merge override on top, then set as patch
        crd = await k8s_client.get_openclaw_instance(OPENCLAW_NAMESPACE, agent_id)
        current_raw = crd.get("spec", {}).get("config", {}).get("raw", {}) if crd else {}
        merged_raw = _deep_merge(current_raw, config_override)
        # If model patch already set spec.config.raw, merge that too
        existing_raw = patch.get("spec", {}).get("config", {}).get("raw", {})
        if existing_raw:
            merged_raw = _deep_merge(merged_raw, existing_raw)
        patch.setdefault("spec", {}).setdefault("config", {})["raw"] = merged_raw

    try:
        await k8s_client.patch_openclaw_instance(OPENCLAW_NAMESPACE, agent_id, patch)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to patch OpenClawInstance: {e}")

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": "admin", "actorName": "Admin",
        "targetType": "agent", "targetId": agent_id,
        "detail": f"Reloaded EKS agent (config-version={config_version}"
                  f"{', image=' + image_updated if image_updated else ''}"
                  f"{', configOverride=yes' if config_override else ''})",
        "status": "success",
    })

    return {"reloaded": True, "agentId": agent_id, "configVersion": config_version,
            "imageUpdated": image_updated or None,
            "configOverrideApplied": bool(config_override),
            "note": "CRD patched. Operator will restart pod with new config (~30s)."}


@router.get("/api/v1/admin/eks/{agent_id}/images")
async def list_eks_agent_images(agent_id: str, authorization: str = Header(default="")):
    """List available container image tags for an EKS agent.

    Checks two sources:
    1. ECR mirror (if globalRegistry is set or China region) — for air-gapped/China clusters
    2. ghcr.io/openclaw/openclaw (public, no auth) — default upstream registry

    Returns the current CRD image as reference plus available tags.
    """
    require_role(authorization, roles=["admin"])

    # Get current image from CRD
    crd = await k8s_client.get_openclaw_instance(OPENCLAW_NAMESPACE, agent_id)
    current_image = crd.get("spec", {}).get("image", {}) if crd else {}
    current_repo = current_image.get("repository", "ghcr.io/openclaw/openclaw")
    current_tag = current_image.get("tag", "latest")

    images = []
    sources = []

    # 1. Try ECR mirror
    global_registry = os.environ.get("OPENCLAW_REGISTRY", "")
    ecr_repo = "openclaw/openclaw"
    if global_registry or IS_CHINA_REGION:
        try:
            region = os.environ.get("AWS_REGION", GATEWAY_REGION)
            ecr = boto3.client("ecr", region_name=region)
            resp = ecr.describe_images(
                repositoryName=ecr_repo,
                filter={"tagStatus": "TAGGED"})
            for img in sorted(resp.get("imageDetails", []),
                              key=lambda x: x.get("imagePushedAt", ""), reverse=True)[:20]:
                for tag in (img.get("imageTags") or []):
                    images.append({
                        "tag": tag,
                        "repository": f"{global_registry}/{ecr_repo}" if global_registry else ecr_repo,
                        "source": "ecr",
                        "digest": img.get("imageDigest", "")[:20],
                        "pushed": str(img.get("imagePushedAt", ""))[:19],
                        "sizeMB": round(img.get("imageSizeInBytes", 0) / 1024 / 1024, 1),
                    })
            sources.append("ecr")
        except Exception as e:
            logger.info("ECR image list failed for %s: %s", ecr_repo, e)

    # 2. ghcr.io (public, anonymous bearer token required)
    try:
        tok_resp = _requests.get(
            "https://ghcr.io/token?service=ghcr.io&scope=repository:openclaw/openclaw:pull",
            timeout=5)
        ghcr_token = tok_resp.json().get("token", "") if tok_resp.ok else ""
        if ghcr_token:
            ghcr_resp = _requests.get(
                "https://ghcr.io/v2/openclaw/openclaw/tags/list",
                headers={"Authorization": f"Bearer {ghcr_token}"},
                timeout=5,
            )
            if ghcr_resp.status_code == 200:
                tags = ghcr_resp.json().get("tags", [])
                # Filter to release tags (e.g. "2026.3.1"), skip arch-specific
                release_tags = [t for t in tags
                                if not t.endswith(("-amd64", "-arm64"))]
                for tag in reversed(release_tags[-20:]):
                    images.append({
                        "tag": tag,
                        "repository": "ghcr.io/openclaw/openclaw",
                        "source": "ghcr",
                    })
                sources.append("ghcr")
    except Exception as e:
        logger.info("ghcr.io tag list failed: %s", e)

    return {
        "images": images,
        "sources": sources,
        "currentImage": {"repository": current_repo, "tag": current_tag},
    }


@router.get("/api/v1/admin/eks/{agent_id}/status")
async def get_eks_agent_status(agent_id: str, authorization: str = Header(default="")):
    """Get pod status for an EKS agent."""
    require_role(authorization, roles=["admin"])

    # Get CRD status
    crd = await k8s_client.get_openclaw_instance(OPENCLAW_NAMESPACE, agent_id)
    if not crd:
        return {"running": False, "agentId": agent_id, "crdStatus": "NOT_FOUND"}

    # Get pod status
    pod_status = await k8s_client.get_pod_status(OPENCLAW_NAMESPACE, agent_id)

    running = pod_status.get("phase") == "Running"
    return {
        "running": running,
        "agentId": agent_id,
        "namespace": OPENCLAW_NAMESPACE,
        "endpoint": _agent_svc_endpoint(agent_id) if running else None,
        "crdStatus": crd.get("status", {}).get("phase", "Unknown"),
        "pod": pod_status,
    }


@router.get("/api/v1/admin/eks/{agent_id}/logs")
async def get_eks_agent_logs(
    agent_id: str,
    container: str = "openclaw",
    tail: int = 100,
    authorization: str = Header(default=""),
):
    """Get pod logs for an EKS agent."""
    require_role(authorization, roles=["admin"])

    result = await k8s_client.get_pod_logs(
        OPENCLAW_NAMESPACE, agent_id, container=container, tail_lines=tail)

    if result.get("error"):
        raise HTTPException(404, result["error"])
    return result


@router.put("/api/v1/admin/eks/{agent_id}/assign/{emp_id}")
async def assign_eks_to_employee(agent_id: str, emp_id: str, authorization: str = Header(default="")):
    """Assign an employee to route through the EKS agent instead of AgentCore."""
    require_role(authorization, roles=["admin"])

    try:
        ssm = ssm_client()
        ssm.put_parameter(
            Name=f"/openclaw/{STACK_NAME}/tenants/{emp_id}/eks-endpoint",
            Value=_agent_svc_endpoint(agent_id),
            Type="String", Overwrite=True,
        )
    except Exception as e:
        raise HTTPException(500, f"SSM write failed: {e}")

    return {"assigned": True, "empId": emp_id, "agentId": agent_id,
            "endpoint": _agent_svc_endpoint(agent_id)}


@router.delete("/api/v1/admin/eks/{agent_id}/assign/{emp_id}")
async def unassign_eks_from_employee(agent_id: str, emp_id: str, authorization: str = Header(default="")):
    """Remove employee's EKS assignment -- they fall back to AgentCore."""
    require_role(authorization, roles=["admin"])

    try:
        ssm = ssm_client()
        ssm.delete_parameter(
            Name=f"/openclaw/{STACK_NAME}/tenants/{emp_id}/eks-endpoint")
    except Exception:
        pass

    return {"unassigned": True, "empId": emp_id}


# =========================================================================
# IM Bot Tokens (EKS) — stored in K8s Secret, injected via CRD env
# =========================================================================

_TOKEN_CHANNELS = [
    ("telegram", "TELEGRAM_BOT_TOKEN"),
    ("discord", "DISCORD_BOT_TOKEN"),
    ("slack", "SLACK_BOT_TOKEN"),
    ("feishu", "LARK_APP_SECRET"),
]


def _token_secret_name(agent_id: str) -> str:
    from services.k8s_client import _sanitize_k8s_name
    return f"{_sanitize_k8s_name(agent_id)}-im-tokens"


@router.put("/api/v1/admin/eks/{agent_id}/tokens")
async def set_eks_tokens(agent_id: str, body: dict, authorization: str = Header(default="")):
    """Store IM bot tokens for an EKS agent.

    Tokens are stored in a K8s Secret and injected into the pod via
    CRD spec.env[].valueFrom.secretKeyRef. A config-version bump
    triggers the operator to restart the pod with the new tokens.

    Body: { telegramBotToken?, discordBotToken?, slackBotToken?, feishuBotToken?,
            clearTelegramToken?, clearDiscordToken?, clearSlackToken?, clearFeishuToken? }
    """
    require_role(authorization, roles=["admin"])

    secret_name = _token_secret_name(agent_id)
    from services.k8s_client import _sanitize_k8s_name
    safe_name = _sanitize_k8s_name(agent_id)

    # Build secret data from request
    secret_data = {}
    cleared = []
    for channel, env_key in _TOKEN_CHANNELS:
        token = body.get(f"{channel}BotToken", "").strip()
        if token:
            secret_data[env_key] = token
        elif body.get(f"clear{channel.capitalize()}Token"):
            await k8s_client.delete_secret_key(OPENCLAW_NAMESPACE, secret_name, env_key)
            cleared.append(channel)

    # Create/update secret with new tokens
    if secret_data:
        await k8s_client.upsert_secret(
            namespace=OPENCLAW_NAMESPACE,
            name=secret_name,
            data=secret_data,
            labels={"app.kubernetes.io/managed-by": "admin-console",
                    "openclaw.rocks/agent": safe_name},
        )

    # Read current secret keys to build env patch
    existing_keys = await k8s_client.get_secret_keys(OPENCLAW_NAMESPACE, secret_name)

    # Build CRD env patch: add valueFrom.secretKeyRef for each token key
    # Preserve existing non-token env vars from the CRD
    crd = await k8s_client.get_openclaw_instance(OPENCLAW_NAMESPACE, agent_id)
    current_env = crd.get("spec", {}).get("env", []) if crd else []
    # Filter out old token env vars (we'll re-add from secret)
    token_env_names = {ek for _, ek in _TOKEN_CHANNELS}
    base_env = [e for e in current_env if e.get("name") not in token_env_names]
    # Add secretKeyRef entries for each key in the secret
    for env_key in existing_keys:
        base_env.append({
            "name": env_key,
            "valueFrom": {"secretKeyRef": {"name": secret_name, "key": env_key}},
        })

    # Patch CRD: update env + bump config-version to trigger pod restart
    config_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    patch = {
        "metadata": {"annotations": {"openclaw.rocks/config-version": config_version}},
        "spec": {"env": base_env},
    }
    try:
        await k8s_client.patch_openclaw_instance(OPENCLAW_NAMESPACE, agent_id, patch)
    except ValueError as e:
        raise HTTPException(404, str(e))

    saved = {ch: (ek in existing_keys or ek in secret_data)
             for ch, ek in _TOKEN_CHANNELS}
    for ch in cleared:
        saved[ch] = False

    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change", "actorId": "admin", "actorName": "Admin",
        "targetType": "agent", "targetId": agent_id,
        "detail": f"Updated IM bot tokens for EKS agent (secret={secret_name})",
        "status": "success",
    })

    return {"saved": saved, "agentId": agent_id, "secretName": secret_name,
            "note": "Tokens stored in K8s Secret. Pod restarting to activate direct IM."}


@router.get("/api/v1/admin/eks/{agent_id}/tokens")
async def get_eks_tokens(agent_id: str, authorization: str = Header(default="")):
    """Check which IM tokens are configured for an EKS agent (masked)."""
    require_role(authorization, roles=["admin"])

    secret_name = _token_secret_name(agent_id)
    existing_keys = await k8s_client.get_secret_keys(OPENCLAW_NAMESPACE, secret_name)

    result = {}
    for channel, env_key in _TOKEN_CHANNELS:
        result[channel] = "configured" if env_key in existing_keys else "not_configured"
    return result


# =========================================================================
# Gateway Proxy — reverse proxy to EKS agent's OpenClaw Gateway UI
# =========================================================================

@router.api_route(
    "/api/v1/admin/eks/{agent_id}/gateway/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy_eks_gateway(agent_id: str, path: str, request: Request,
                            authorization: str = Header(default="")):
    """Reverse proxy HTTP to an EKS agent's Gateway UI (port 18789).

    Auth: admin JWT via header or auth_token query param.  On first request
    a cookie is set so sub-resource loads (CSS/JS/images) authenticate
    automatically.

    The admin console pod reaches the agent via in-cluster Service DNS:
      http://{agent_name}.openclaw.svc:18789/{path}
    Gateway auth mode is "none" for EKS instances (set in CRD config).
    """
    # Accept JWT from header, query param, or cookie
    qt = request.query_params.get("auth_token", "")
    cookie_token = request.cookies.get("eks_gw_session", "")
    effective_auth = authorization or (f"Bearer {qt}" if qt else "") or (f"Bearer {cookie_token}" if cookie_token else "")
    require_role(effective_auth, roles=["admin"])

    from services.k8s_client import _sanitize_k8s_name
    safe_name = _sanitize_k8s_name(agent_id)
    target_base = f"http://{safe_name}.{OPENCLAW_NAMESPACE}.svc:18789"
    target = f"{target_base}/{path}"

    # Inject gateway token for upstream auth
    gw_token = await _get_gateway_token(agent_id)
    if gw_token:
        target += ("&" if "?" in target else "?") + f"token={gw_token}"

    # Forward query params (strip auth_token)
    filtered = {k: v for k, v in request.query_params.items() if k != "auth_token"}
    if filtered:
        from urllib.parse import urlencode
        target += ("&" if "?" in target else "?") + urlencode(filtered)

    try:
        body = await request.body()
        headers = {
            "Content-Type": request.headers.get("content-type", "application/json"),
            "Accept": request.headers.get("accept", "*/*"),
        }
        resp = _requests.request(
            method=request.method, url=target, headers=headers,
            data=body if body else None, timeout=(3, 15), allow_redirects=False,
        )

        excluded = {"transfer-encoding", "content-encoding", "connection", "content-length"}
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}

        content = resp.content
        ct = resp.headers.get("content-type", "")

        # Inject base-path hint + auth token into HTML so the gateway JS
        # can build WebSocket URLs correctly even behind a reverse proxy.
        if "text/html" in ct and qt and b"<head>" in content:
            base_path = f"/api/v1/admin/eks/{agent_id}/gateway"
            inject = (
                f'<script>'
                f'window.__OPENCLAW_CONTROL_UI_BASE_PATH__="{base_path}";'
                f'window.__OPENCLAW_PROXY_TOKEN__="{qt}";'
                f'</script>'
            ).encode()
            content = content.replace(b"<head>", b"<head>" + inject, 1)

        response = Response(
            content=content, status_code=resp.status_code,
            headers=resp_headers, media_type=ct,
        )

        # Set cookie on first request so sub-resources authenticate
        if qt:
            response.set_cookie(
                key="eks_gw_session", value=qt,
                max_age=3600, httponly=True, samesite="lax",
                path=f"/api/v1/admin/eks/{agent_id}/gateway",
            )
        return response

    except _requests.exceptions.ConnectionError:
        raise HTTPException(502, "Agent gateway not reachable — pod may be starting.")
    except _requests.exceptions.Timeout:
        raise HTTPException(504, "Agent gateway timed out")
    except Exception as e:
        raise HTTPException(502, f"Gateway proxy error: {e}")


async def _proxy_eks_ws(websocket: WebSocket, agent_id: str, path: str = ""):
    """Shared WebSocket proxy logic for EKS gateway.
    Authenticates via eks_gw_session cookie or auth_token query param."""
    token = (websocket.cookies.get("eks_gw_session", "")
             or websocket.query_params.get("auth_token", ""))
    if not token:
        await websocket.close(code=4001, reason="Missing auth")
        return
    try:
        require_role(f"Bearer {token}", roles=["admin"])
    except Exception:
        await websocket.close(code=4001, reason="Invalid auth")
        return

    from services.k8s_client import _sanitize_k8s_name
    safe_name = _sanitize_k8s_name(agent_id)
    ws_target = f"ws://{safe_name}.{OPENCLAW_NAMESPACE}.svc:18789/{path}"

    # Inject gateway token for upstream auth
    gw_token = await _get_gateway_token(agent_id)
    if gw_token:
        ws_target += ("&" if "?" in ws_target else "?") + f"token={gw_token}"

    # Forward query params (strip auth_token — internal only)
    for k, v in websocket.query_params.items():
        if k != "auth_token":
            ws_target += ("&" if "?" in ws_target else "?") + f"{k}={v}"

    await websocket.accept()
    try:
        import websockets
        # Set Origin to match the upstream host so the gateway's allowedOrigins check passes
        upstream_origin = f"http://{safe_name}.{OPENCLAW_NAMESPACE}.svc:18789"
        async with websockets.connect(
            ws_target, open_timeout=5, close_timeout=3,
            additional_headers={"Origin": upstream_origin},
        ) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if "text" in msg:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"]:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except Exception:
                    pass

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception as e:
        logger.warning("EKS gateway WS proxy error for %s: %s", agent_id, e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# Two routes needed: the gateway UI connects to the root path (no trailing segment)
@router.websocket("/api/v1/admin/eks/{agent_id}/gateway")
async def proxy_eks_gateway_ws_root(websocket: WebSocket, agent_id: str):
    await _proxy_eks_ws(websocket, agent_id, "")


@router.websocket("/api/v1/admin/eks/{agent_id}/gateway/{path:path}")
async def proxy_eks_gateway_ws(websocket: WebSocket, agent_id: str, path: str):
    await _proxy_eks_ws(websocket, agent_id, path)
