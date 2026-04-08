"""
Kubernetes client for managing OpenClawInstance CRDs on EKS.

Simplified from openclaw-saas-gcr/platform/api/services/k8s_client.py
for the enterprise admin console. Operates on a single namespace
(OPENCLAW_NAMESPACE env var) instead of per-tenant namespaces.
"""

import asyncio
import os
import re
from typing import Optional

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client import ApiClient


def _sanitize_k8s_name(name: str) -> str:
    """Sanitize a string into a valid RFC 1123 subdomain for K8s resource names."""
    name = re.sub(r'[^a-z0-9.\-]', '-', name.lower())
    name = re.sub(r'-{2,}', '-', name).strip('-.')
    return name


# CRD coordinates
CRD_GROUP = "openclaw.rocks"
CRD_VERSION = "v1alpha1"
CRD_PLURAL = "openclawinstances"
CRD_FULL_NAME = f"{CRD_PLURAL}.{CRD_GROUP}"

# Operator namespace (for status checks)
OPERATOR_NAMESPACE = os.environ.get("OPERATOR_NAMESPACE", "openclaw-operator-system")

# Env-based config
OPENCLAW_NAMESPACE = os.environ.get("OPENCLAW_NAMESPACE", "openclaw")

# Auto-detect in-cluster: K8S_IN_CLUSTER env var OR Kubernetes service account token exists
_k8s_env = os.environ.get("K8S_IN_CLUSTER", "").lower()
K8S_IN_CLUSTER = (
    _k8s_env in ("true", "1", "yes")
    or os.path.isfile("/var/run/secrets/kubernetes.io/serviceaccount/token")
)


class K8sClient:
    """Async Kubernetes client for OpenClawInstance CRD lifecycle."""

    def __init__(self):
        self._initialized = False
        self._api_client: Optional[ApiClient] = None
        self._core_v1: Optional[client.CoreV1Api] = None
        self._apps_v1: Optional[client.AppsV1Api] = None
        self._api_ext: Optional[client.ApiextensionsV1Api] = None
        self._custom_objects: Optional[client.CustomObjectsApi] = None

    async def initialize(self):
        if self._initialized:
            return
        if K8S_IN_CLUSTER:
            config.load_incluster_config()
        else:
            await config.load_kube_config()
        self._api_client = ApiClient()
        self._core_v1 = client.CoreV1Api(self._api_client)
        self._apps_v1 = client.AppsV1Api(self._api_client)
        self._api_ext = client.ApiextensionsV1Api(self._api_client)
        self._custom_objects = client.CustomObjectsApi(self._api_client)
        self._initialized = True

    async def close(self):
        if self._api_client:
            await self._api_client.close()
            self._initialized = False

    # ─── Pod exec ───

    async def exec_in_pod(
        self,
        namespace: str,
        pod_name: str,
        command: list,
        container: str = "openclaw",
        timeout: int = 10,
    ) -> tuple:
        """Execute a command inside a pod via the Kubernetes API.

        Returns (stdout: str, stderr: str, exit_code: int).
        Uses kubernetes_asyncio's WebSocket-based exec stream.
        """
        await self.initialize()
        from kubernetes_asyncio.stream import WsApiClient

        async with WsApiClient() as ws_client:
            core_ws = client.CoreV1Api(ws_client)
            try:
                resp = await asyncio.wait_for(
                    core_ws.connect_get_namespaced_pod_exec(
                        name=pod_name,
                        namespace=namespace,
                        command=command,
                        container=container,
                        stderr=True,
                        stdin=False,
                        stdout=True,
                        tty=False,
                    ),
                    timeout=timeout,
                )
                # resp is the raw output string when using connect_get
                return (resp or "", "", 0)
            except asyncio.TimeoutError:
                return ("", "exec timed out", 1)
            except client.exceptions.ApiException as e:
                return ("", f"K8s API error: {e.reason}", e.status or 1)
            except Exception as e:
                return ("", str(e), 1)

    # ─── Secrets ───

    async def upsert_secret(
        self,
        namespace: str,
        name: str,
        data: dict,
        labels: Optional[dict] = None,
    ) -> str:
        """Create or update an Opaque Secret. Values are plain strings (base64 handled by API).
        Returns 'created' or 'updated'."""
        await self.initialize()
        import base64
        encoded = {k: base64.b64encode(v.encode()).decode() for k, v in data.items() if v}
        body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": namespace, "labels": labels or {}},
            "type": "Opaque",
            "data": encoded,
        }
        try:
            await self._core_v1.read_namespaced_secret(name=name, namespace=namespace)
            await self._core_v1.patch_namespaced_secret(name=name, namespace=namespace, body=body)
            return "updated"
        except client.exceptions.ApiException as e:
            if e.status == 404:
                await self._core_v1.create_namespaced_secret(namespace=namespace, body=body)
                return "created"
            raise

    async def delete_secret_key(self, namespace: str, name: str, key: str) -> bool:
        """Remove a single key from a Secret. Returns True if deleted, False if not found."""
        await self.initialize()
        try:
            secret = await self._core_v1.read_namespaced_secret(name=name, namespace=namespace)
            if secret.data and key in secret.data:
                del secret.data[key]
                # replace (not patch) to actually remove the key
                await self._core_v1.replace_namespaced_secret(
                    name=name, namespace=namespace, body=secret)
                return True
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return False
            raise
        return False

    async def get_secret_keys(self, namespace: str, name: str) -> list:
        """Return the list of keys in a Secret (no values). Empty list if not found."""
        await self.initialize()
        try:
            secret = await self._core_v1.read_namespaced_secret(name=name, namespace=namespace)
            return list(secret.data.keys()) if secret.data else []
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return []
            raise

    # ─── OpenClawInstance CRD ───

    async def create_openclaw_instance(
        self,
        namespace: str,
        agent_name: str,
        employee_id: str,
        position_id: str,
        model: str,
        image: str = "",
        global_registry: str = "",
        bedrock_role_arn: str = "",
        workspace_files: Optional[dict] = None,
        skills: Optional[list] = None,
        storage_class: str = "",
        storage_size: str = "10Gi",
        cpu_request: str = "500m",
        cpu_limit: str = "2",
        memory_request: str = "2Gi",
        memory_limit: str = "4Gi",
        runtime_class: str = "",
        node_selector: Optional[dict] = None,
        tolerations: Optional[list] = None,
        chromium: bool = False,
        backup_schedule: str = "",
        service_type: str = "",
    ) -> dict:
        """Create an OpenClawInstance CRD for an enterprise agent.

        Uses the same agent-container image as ECS Fargate so the entrypoint.sh,
        server.py, and workspace_assembler.py code paths are shared across runtimes.

        The operator reconciles this CRD into a StatefulSet + Service + PVC + ConfigMap.
        Config is written to the PVC via init-config container; workspace files are
        seeded once via init-workspace container (never overwritten on restart).

        Args:
            image: Container image URI for the main openclaw container (e.g. ECR URI).
                   Overrides spec.image.repository/tag.
            global_registry: Global registry override (spec.registry). Rewrites the registry
                   portion of ALL container images (main, sidecars, init containers).
                   Required for China regions where ghcr.io is inaccessible.
                   Example: "834204282212.dkr.ecr.cn-northwest-1.amazonaws.com.cn"
            workspace_files: Dict of filename->content to seed into workspace.
            skills: List of ClawHub skill identifiers to install via init container.
            cpu_request/cpu_limit/memory_request/memory_limit: Compute resources.
            runtime_class: RuntimeClassName (e.g. "kata-qemu" for Firecracker isolation).
            node_selector: K8s nodeSelector labels (e.g. {"katacontainers.io/kata-runtime": "true"}).
            tolerations: K8s tolerations (e.g. [{"key": "kata", "value": "true", "effect": "NoSchedule"}]).
            chromium: Enable headless Chromium sidecar for browser automation.
            backup_schedule: Cron schedule for S3 backups (e.g. "0 2 * * *").
            service_type: K8s Service type (ClusterIP, LoadBalancer, NodePort).
        """
        await self.initialize()
        agent_name = _sanitize_k8s_name(agent_name)
        if not agent_name:
            raise ValueError("agent_name produces an empty K8s name after sanitization")

        stack = os.environ.get("STACK_NAME", "openclaw-multitenancy")
        s3_bucket = os.environ.get("S3_BUCKET", "")
        ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
        ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
        aws_region = os.environ.get("AWS_REGION", "us-west-2")

        # Extract Bedrock model ID from the model string.
        # Admin console passes "bedrock/us.anthropic.claude-sonnet-4-5..." format;
        # the openclaw.json template expects just the model ID for ${BEDROCK_MODEL_ID}.
        bedrock_model_id = model
        if "/" in bedrock_model_id:
            bedrock_model_id = bedrock_model_id.split("/", 1)[1]

        # Build openclaw.json config — same structure as agent-container/openclaw.json
        # so it merges cleanly with the template baked into the image.
        raw_config = {
            "models": {
                "providers": {
                    "amazon-bedrock": {
                        "baseUrl": f"https://bedrock-runtime.{aws_region}.amazonaws.com",
                        "auth": "aws-sdk",
                        "api": "bedrock-converse-stream",
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
                    "workspace": "/home/openclaw/.openclaw/workspace",
                    "skipBootstrap": True,
                    "compaction": {"mode": "default", "recentTurnsPreserve": 5},
                },
            },
            "gateway": {
                "port": 18789,
                "mode": "local",
                "bind": "lan",
                "auth": {"mode": "none"},
                "controlUi": {"allowedOrigins": ["*"], "allowInsecureAuth": True},
            },
            "tools": {"exec": {"security": "full", "ask": "off"}},
        }

        # Environment variables — same as entrypoint.sh expects so the shared
        # agent-container code (workspace_assembler, server.py, skill_loader) works.
        env_vars = [
            {"name": "EMPLOYEE_ID", "value": employee_id},
            {"name": "POSITION_ID", "value": position_id},
            {"name": "STACK_NAME", "value": stack},
            {"name": "S3_BUCKET", "value": s3_bucket},
            {"name": "DYNAMODB_TABLE", "value": ddb_table},
            {"name": "DYNAMODB_REGION", "value": ddb_region},
            {"name": "AWS_REGION", "value": aws_region},
            {"name": "BEDROCK_MODEL_ID", "value": bedrock_model_id},
            {"name": "SESSION_ID", "value": employee_id},
            {"name": "SHARED_AGENT_ID", "value": agent_name},
            {"name": "OPENCLAW_SKIP_ONBOARDING", "value": "1"},
        ]

        # Image: use agent-container ECR image if available, otherwise default
        # to the standard openclaw image (operator default: ghcr.io/openclaw/openclaw).
        agent_ecr_image = image or os.environ.get("AGENT_ECR_IMAGE", "")
        image_spec = {}
        if agent_ecr_image:
            # ECR URI may include tag (e.g. 123456.dkr.ecr.us-west-2.amazonaws.com/repo:tag)
            if ":" in agent_ecr_image.split("/")[-1]:
                repo, tag = agent_ecr_image.rsplit(":", 1)
            else:
                repo, tag = agent_ecr_image, "latest"
            image_spec = {
                "repository": repo,
                "tag": tag,
                "pullPolicy": "Always",
            }

        # Global registry override — rewrites the registry portion of ALL images
        # (main container, sidecars, init containers). Required for China regions
        # where ghcr.io/docker.io are inaccessible. Images must be pre-mirrored.
        registry_override = global_registry or os.environ.get("OPENCLAW_REGISTRY", "")

        # IRSA annotation for Bedrock access
        rbac_spec = {}
        if bedrock_role_arn:
            rbac_spec = {
                "serviceAccountAnnotations": {
                    "eks.amazonaws.com/role-arn": bedrock_role_arn,
                },
            }

        # Workspace files: seed SOUL.md, USER.md etc. via operator init-workspace container.
        # These are written once on first boot and never overwritten, so agent
        # modifications survive pod restarts.
        workspace_spec = None
        if workspace_files:
            workspace_spec = {"initialFiles": workspace_files}

        # Availability: runtimeClassName, nodeSelector, tolerations
        availability_spec = {}
        if runtime_class:
            availability_spec["runtimeClassName"] = runtime_class
        if node_selector:
            availability_spec["nodeSelector"] = node_selector
        if tolerations:
            availability_spec["tolerations"] = tolerations

        # Networking: service type override
        networking_spec = {}
        if service_type:
            networking_spec = {"service": {"type": service_type}}

        body = {
            "apiVersion": f"{CRD_GROUP}/{CRD_VERSION}",
            "kind": "OpenClawInstance",
            "metadata": {
                "name": agent_name,
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "admin-console",
                    "openclaw.rocks/employee": employee_id,
                    "openclaw.rocks/position": position_id,
                },
                "annotations": {
                    **({"openclaw.rocks/bedrock-role-arn": bedrock_role_arn} if bedrock_role_arn else {}),
                },
            },
            "spec": {
                **({"registry": registry_override} if registry_override else {}),
                **({"image": image_spec} if image_spec else {}),
                "env": env_vars,
                "config": {
                    "mergeMode": "merge",
                    "raw": raw_config,
                },
                **({"workspace": workspace_spec} if workspace_spec else {}),
                **({"skills": skills} if skills else {}),
                **({"security": {"rbac": rbac_spec}} if rbac_spec else {}),
                "gateway": {"enabled": True},
                "storage": {"persistence": {
                    "enabled": True,
                    "size": storage_size,
                    **({"storageClass": storage_class} if storage_class else {}),
                }},
                "resources": {
                    "requests": {"cpu": cpu_request, "memory": memory_request},
                    "limits": {"cpu": cpu_limit, "memory": memory_limit},
                },
                "chromium": {"enabled": chromium},
                **({"availability": availability_spec} if availability_spec else {}),
                **({"backup": {"schedule": backup_schedule}} if backup_schedule else {}),
                **({"networking": networking_spec} if networking_spec else {}),
            },
        }

        try:
            await self._custom_objects.create_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural=CRD_PLURAL,
                body=body,
            )
            return {"status": "created", "name": agent_name}
        except client.exceptions.ApiException as e:
            if e.status == 409:
                raise ValueError(f"Agent {agent_name} already exists in namespace {namespace}")
            raise

    async def delete_openclaw_instance(self, namespace: str, agent_name: str) -> dict:
        """Delete an OpenClawInstance CRD."""
        await self.initialize()
        agent_name = _sanitize_k8s_name(agent_name)
        try:
            await self._custom_objects.delete_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural=CRD_PLURAL,
                name=agent_name,
            )
            return {"status": "deleted", "name": agent_name}
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return {"status": "not_found", "name": agent_name}
            raise

    async def patch_openclaw_instance(self, namespace: str, agent_name: str, patch: dict) -> dict:
        """Merge-patch an OpenClawInstance CRD (e.g. config update)."""
        await self.initialize()
        agent_name = _sanitize_k8s_name(agent_name)
        try:
            await self._custom_objects.patch_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural=CRD_PLURAL,
                name=agent_name,
                body=patch,
                _content_type="application/merge-patch+json",
            )
            return {"status": "patched", "name": agent_name}
        except client.exceptions.ApiException as e:
            if e.status == 404:
                raise ValueError(f"Agent {agent_name} not found in namespace {namespace}")
            raise

    async def get_openclaw_instance(self, namespace: str, agent_name: str) -> Optional[dict]:
        """Get an OpenClawInstance CRD."""
        await self.initialize()
        agent_name = _sanitize_k8s_name(agent_name)
        try:
            return await self._custom_objects.get_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural=CRD_PLURAL,
                name=agent_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return None
            raise

    async def list_openclaw_instances(self, namespace: str) -> list:
        """List all OpenClawInstance CRDs in a namespace."""
        await self.initialize()
        try:
            result = await self._custom_objects.list_namespaced_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                namespace=namespace,
                plural=CRD_PLURAL,
            )
            instances = []
            for item in result.get("items", []):
                meta = item.get("metadata", {})
                status = item.get("status", {})
                spec = item.get("spec", {})
                instances.append({
                    "name": meta.get("name", ""),
                    "namespace": meta.get("namespace", ""),
                    "employee": meta.get("labels", {}).get("openclaw.rocks/employee", ""),
                    "position": meta.get("labels", {}).get("openclaw.rocks/position", ""),
                    "phase": status.get("phase", "Unknown"),
                    "ready": status.get("ready", False),
                    "model": spec.get("config", {}).get("raw", {}).get("agents", {}).get("defaults", {}).get("model", {}).get("primary", ""),
                    "created": meta.get("creationTimestamp", ""),
                    "configVersion": meta.get("annotations", {}).get("openclaw.rocks/config-version", ""),
                })
            return instances
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return []
            raise

    # ─── Pod Status ───

    async def get_pod_status(self, namespace: str, agent_name: str) -> dict:
        """Get pod status for an agent by label selector."""
        await self.initialize()
        agent_name = _sanitize_k8s_name(agent_name)
        try:
            pods = await self._core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app.kubernetes.io/instance={agent_name},app.kubernetes.io/name=openclaw",
            )
            if not pods.items:
                return {"status": "not_found", "phase": None}

            pod = pods.items[0]
            container_statuses = []
            for cs in pod.status.container_statuses or []:
                container_statuses.append({
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": "running" if cs.state.running else "waiting" if cs.state.waiting else "terminated",
                })

            return {
                "status": "found",
                "phase": pod.status.phase,
                "pod_name": pod.metadata.name,
                "node": pod.spec.node_name,
                "start_time": pod.status.start_time.isoformat() if pod.status.start_time else None,
                "containers": container_statuses,
                "conditions": [
                    {"type": c.type, "status": c.status}
                    for c in pod.status.conditions or []
                ],
            }
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return {"status": "not_found", "phase": None}
            raise

    async def get_pod_logs(
        self,
        namespace: str,
        agent_name: str,
        container: str = "openclaw",
        tail_lines: int = 100,
    ) -> dict:
        """Get pod logs for an agent container."""
        await self.initialize()
        agent_name = _sanitize_k8s_name(agent_name)
        try:
            pods = await self._core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app.kubernetes.io/instance={agent_name},app.kubernetes.io/name=openclaw",
            )
            if not pods.items:
                return {"error": "Pod not found", "logs": ""}

            pod = pods.items[0]
            containers = [cs.name for cs in (pod.status.container_statuses or [])]

            if container not in containers:
                return {
                    "error": f"Container '{container}' not found",
                    "available_containers": containers,
                    "logs": "",
                }

            logs = await self._core_v1.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
            )
            return {
                "pod_name": pod.metadata.name,
                "container": container,
                "available_containers": containers,
                "tail_lines": tail_lines,
                "logs": logs,
            }
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return {"error": "Pod not found", "logs": ""}
            raise

    # ─── Operator Status & Install ───

    async def check_crd_exists(self) -> bool:
        """Check if the OpenClawInstance CRD is registered in the cluster."""
        await self.initialize()
        try:
            await self._api_ext.read_custom_resource_definition(name=CRD_FULL_NAME)
            return True
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return False
            raise

    async def get_operator_status(self) -> dict:
        """Check if the OpenClaw operator is installed and running.

        Returns:
            dict with keys: installed, crd_exists, namespace, pods, version
        """
        await self.initialize()

        # 1. Check CRD
        crd_exists = await self.check_crd_exists()

        # 2. Check operator pods
        operator_pods = []
        try:
            pods = await self._core_v1.list_namespaced_pod(
                namespace=OPERATOR_NAMESPACE,
                label_selector="app.kubernetes.io/name=openclaw-operator",
            )
            for pod in pods.items or []:
                operator_pods.append({
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "ready": all(
                        cs.ready for cs in (pod.status.container_statuses or [])
                    ),
                    "restart_count": sum(
                        cs.restart_count for cs in (pod.status.container_statuses or [])
                    ),
                })
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

        # 3. Check deployment (try both naming conventions: Helm chart uses
        #    "openclaw-operator", older OLM installs use "openclaw-operator-controller-manager")
        operator_version = ""
        deployment_ready = False
        for dep_name in ["openclaw-operator", "openclaw-operator-controller-manager"]:
            try:
                dep = await self._apps_v1.read_namespaced_deployment(
                    name=dep_name,
                    namespace=OPERATOR_NAMESPACE,
                )
                deployment_ready = (
                    dep.status.ready_replicas is not None
                    and dep.status.ready_replicas >= 1
                )
                for c in dep.spec.template.spec.containers or []:
                    if "openclaw-operator" in (c.image or ""):
                        tag = (c.image or "").rsplit(":", 1)[-1]
                        operator_version = tag.lstrip("v")
                        break
                break  # found a deployment, stop searching
            except client.exceptions.ApiException as e:
                if e.status != 404:
                    raise

        installed = crd_exists and deployment_ready
        return {
            "installed": installed,
            "crd_exists": crd_exists,
            "deployment_ready": deployment_ready,
            "namespace": OPERATOR_NAMESPACE,
            "version": operator_version,
            "pods": operator_pods,
        }

    # Operator install/upgrade removed — use install.sh or Terraform instead.
    # The get_operator_status() method above is kept for read-only health checks.


# Global singleton
k8s_client = K8sClient()
