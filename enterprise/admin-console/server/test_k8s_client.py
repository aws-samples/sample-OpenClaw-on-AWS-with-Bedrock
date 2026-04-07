"""
Tests for services/k8s_client.py — K8s client for OpenClawInstance CRDs.

Covers:
  1. Initialization — kube_config vs in-cluster
  2. CRD CRUD — create, get, patch, delete OpenClawInstance
  3. Pod status — label-selector lookup, container parsing
  4. Pod logs — log retrieval, missing container handling
  5. Operator status — CRD existence, deployment readiness, pod health
  6. Operator install/upgrade — helm subprocess calls
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _make_api_exception(status: int, reason: str = ""):
    """Build a mock kubernetes ApiException."""
    from unittest.mock import MagicMock as _MagicMock
    exc = _MagicMock()
    exc.status = status
    exc.reason = reason
    return type("ApiException", (Exception,), {"status": status, "reason": reason})()


# We need to mock the kubernetes_asyncio module before importing k8s_client,
# since it imports at module level.
_mock_k8s_config = MagicMock()
_mock_k8s_config.load_kube_config = AsyncMock()
_mock_k8s_config.load_incluster_config = MagicMock()

_mock_k8s_client_module = MagicMock()


# Build a realistic ApiException class that matches the real one
class _FakeApiException(Exception):
    def __init__(self, status=500, reason=""):
        self.status = status
        self.reason = reason
        super().__init__(f"({status}) Reason: {reason}")


_mock_k8s_client_module.exceptions.ApiException = _FakeApiException

sys.modules["kubernetes_asyncio"] = MagicMock()
sys.modules["kubernetes_asyncio"].config = _mock_k8s_config
sys.modules["kubernetes_asyncio"].client = _mock_k8s_client_module
sys.modules["kubernetes_asyncio.client"] = _mock_k8s_client_module

# Now import the module under test
from services.k8s_client import K8sClient, CRD_GROUP, CRD_VERSION, CRD_PLURAL, CRD_FULL_NAME


class _BaseK8sTest(unittest.IsolatedAsyncioTestCase):
    """Base class: creates a K8sClient with mocked K8s APIs."""

    def setUp(self):
        self.client = K8sClient()
        self.client._initialized = True
        self.client._api_client = MagicMock()
        self.client._core_v1 = AsyncMock()
        self.client._apps_v1 = AsyncMock()
        self.client._api_ext = AsyncMock()
        self.client._custom_objects = AsyncMock()


# ---------------------------------------------------------------------------
# 1. Initialization
# ---------------------------------------------------------------------------

class TestInitialization(unittest.IsolatedAsyncioTestCase):

    async def test_load_kube_config_default(self):
        """Verify that initialize() sets _initialized=True and creates API clients."""
        import services.k8s_client as _mod
        orig_in_cluster = _mod.K8S_IN_CLUSTER
        _mod.K8S_IN_CLUSTER = False
        try:
            c = K8sClient()
            self.assertFalse(c._initialized)
            await c.initialize()
            self.assertTrue(c._initialized)
            self.assertIsNotNone(c._core_v1)
            self.assertIsNotNone(c._custom_objects)
        finally:
            _mod.K8S_IN_CLUSTER = orig_in_cluster

    async def test_idempotent_initialize(self):
        c = K8sClient()
        c._initialized = True
        await c.initialize()
        # No exception = success; it skipped re-init

    async def test_close(self):
        c = K8sClient()
        c._initialized = True
        c._api_client = AsyncMock()
        await c.close()
        c._api_client.close.assert_awaited_once()
        self.assertFalse(c._initialized)


# ---------------------------------------------------------------------------
# 2. CRD CRUD
# ---------------------------------------------------------------------------

class TestCreateOpenClawInstance(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.client = K8sClient()
        self.client._initialized = True
        self.client._api_client = MagicMock()
        self.client._core_v1 = AsyncMock()
        self.client._apps_v1 = AsyncMock()
        self.client._api_ext = AsyncMock()
        self.client._custom_objects = AsyncMock()

    async def test_create_success(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock(
            return_value={"metadata": {"name": "agt-carol"}})

        result = await self.client.create_openclaw_instance(
            namespace="openclaw",
            agent_name="agt-carol",
            employee_id="emp-carol",
            position_id="pos-sde",
            model="bedrock/claude-sonnet",
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["name"], "agt-carol")

        # Verify CRD body was correct
        call_kwargs = self.client._custom_objects.create_namespaced_custom_object.call_args[1]
        self.assertEqual(call_kwargs["group"], CRD_GROUP)
        self.assertEqual(call_kwargs["version"], CRD_VERSION)
        self.assertEqual(call_kwargs["namespace"], "openclaw")
        body = call_kwargs["body"]
        self.assertEqual(body["metadata"]["name"], "agt-carol")
        self.assertEqual(body["metadata"]["labels"]["openclaw.rocks/employee"], "emp-carol")
        # Model should be in amazon-bedrock/model-id format (bedrock/ prefix stripped)
        self.assertEqual(body["spec"]["config"]["raw"]["agents"]["defaults"]["model"]["primary"],
                         "amazon-bedrock/claude-sonnet")
        # Full openclaw.json config should include Bedrock provider
        self.assertIn("models", body["spec"]["config"]["raw"])
        self.assertIn("amazon-bedrock", body["spec"]["config"]["raw"]["models"]["providers"])
        bedrock_provider = body["spec"]["config"]["raw"]["models"]["providers"]["amazon-bedrock"]
        self.assertEqual(bedrock_provider["models"][0]["id"], "claude-sonnet")
        self.assertEqual(bedrock_provider["auth"], "aws-sdk")
        # Gateway should be enabled
        self.assertTrue(body["spec"]["gateway"]["enabled"])
        # Env vars should include shared agent-container vars
        env_names = [e["name"] for e in body["spec"]["env"]]
        for required in ["EMPLOYEE_ID", "STACK_NAME", "S3_BUCKET", "DYNAMODB_TABLE",
                         "AWS_REGION", "BEDROCK_MODEL_ID", "SHARED_AGENT_ID"]:
            self.assertIn(required, env_names)

    async def test_create_with_registry(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock()

        await self.client.create_openclaw_instance(
            namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
            position_id="pos-sde", model="m",
            image="834204282212.dkr.ecr.cn-northwest-1.amazonaws.com.cn/agent:v2",
        )
        body = self.client._custom_objects.create_namespaced_custom_object.call_args[1]["body"]
        self.assertEqual(body["spec"]["image"]["repository"],
                         "834204282212.dkr.ecr.cn-northwest-1.amazonaws.com.cn/agent")
        self.assertEqual(body["spec"]["image"]["tag"], "v2")

    async def test_create_with_registry_no_tag(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock()

        await self.client.create_openclaw_instance(
            namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
            position_id="pos-sde", model="m",
            image="834204282212.dkr.ecr.cn-northwest-1.amazonaws.com.cn/agent",
        )
        body = self.client._custom_objects.create_namespaced_custom_object.call_args[1]["body"]
        self.assertEqual(body["spec"]["image"]["repository"],
                         "834204282212.dkr.ecr.cn-northwest-1.amazonaws.com.cn/agent")
        self.assertEqual(body["spec"]["image"]["tag"], "latest")

    async def test_create_without_registry_uses_env(self):
        """When no registry arg, falls back to AGENT_ECR_IMAGE env var."""
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock()

        with patch.dict(os.environ, {"AGENT_ECR_IMAGE": ""}):
            await self.client.create_openclaw_instance(
                namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
                position_id="pos-sde", model="m",
            )
        body = self.client._custom_objects.create_namespaced_custom_object.call_args[1]["body"]
        self.assertNotIn("image", body["spec"])

    async def test_create_conflict_raises_valueerror(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock(
            side_effect=_FakeApiException(status=409, reason="Conflict"))

        with self.assertRaises(ValueError) as ctx:
            await self.client.create_openclaw_instance(
                namespace="openclaw", agent_name="agt-dup", employee_id="emp-1",
                position_id="pos-sde", model="m",
            )
        self.assertIn("already exists", str(ctx.exception))

    async def test_create_other_api_error_raised(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock(
            side_effect=_FakeApiException(status=500, reason="Internal"))

        with self.assertRaises(_FakeApiException):
            await self.client.create_openclaw_instance(
                namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
                position_id="pos-sde", model="m",
            )

    async def test_create_includes_bedrock_role_annotation_and_irsa(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock()

        await self.client.create_openclaw_instance(
            namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
            position_id="pos-sde", model="m",
            bedrock_role_arn="arn:aws-cn:iam::834204282212:role/bedrock-role",
        )
        body = self.client._custom_objects.create_namespaced_custom_object.call_args[1]["body"]
        # Should have annotation for reference
        self.assertEqual(
            body["metadata"]["annotations"]["openclaw.rocks/bedrock-role-arn"],
            "arn:aws-cn:iam::834204282212:role/bedrock-role")
        # Should have IRSA annotation in security.rbac for the operator
        self.assertEqual(
            body["spec"]["security"]["rbac"]["serviceAccountAnnotations"]["eks.amazonaws.com/role-arn"],
            "arn:aws-cn:iam::834204282212:role/bedrock-role")

    async def test_create_with_workspace_files(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock()

        workspace = {"SOUL.md": "You are helpful.", "USER.md": "Carol, SDE"}
        await self.client.create_openclaw_instance(
            namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
            position_id="pos-sde", model="m",
            workspace_files=workspace,
        )
        body = self.client._custom_objects.create_namespaced_custom_object.call_args[1]["body"]
        self.assertEqual(body["spec"]["workspace"]["initialFiles"]["SOUL.md"], "You are helpful.")
        self.assertEqual(body["spec"]["workspace"]["initialFiles"]["USER.md"], "Carol, SDE")

    async def test_create_with_skills(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock()

        await self.client.create_openclaw_instance(
            namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
            position_id="pos-sde", model="m",
            skills=["jina-reader", "deep-research-pro"],
        )
        body = self.client._custom_objects.create_namespaced_custom_object.call_args[1]["body"]
        self.assertEqual(body["spec"]["skills"], ["jina-reader", "deep-research-pro"])

    async def test_create_without_workspace_omits_workspace(self):
        self.client._custom_objects.create_namespaced_custom_object = AsyncMock()

        await self.client.create_openclaw_instance(
            namespace="openclaw", agent_name="agt-1", employee_id="emp-1",
            position_id="pos-sde", model="m",
        )
        body = self.client._custom_objects.create_namespaced_custom_object.call_args[1]["body"]
        self.assertNotIn("workspace", body["spec"])
        self.assertNotIn("skills", body["spec"])


class TestDeleteOpenClawInstance(_BaseK8sTest):

    async def test_delete_success(self):
        self.client._custom_objects.delete_namespaced_custom_object = AsyncMock()
        result = await self.client.delete_openclaw_instance("openclaw", "agt-1")
        self.assertEqual(result["status"], "deleted")

    async def test_delete_not_found(self):
        self.client._custom_objects.delete_namespaced_custom_object = AsyncMock(
            side_effect=_FakeApiException(status=404))
        result = await self.client.delete_openclaw_instance("openclaw", "agt-gone")
        self.assertEqual(result["status"], "not_found")


class TestPatchOpenClawInstance(_BaseK8sTest):

    async def test_patch_success(self):
        self.client._custom_objects.patch_namespaced_custom_object = AsyncMock()
        result = await self.client.patch_openclaw_instance(
            "openclaw", "agt-1", {"metadata": {"annotations": {"version": "2"}}})
        self.assertEqual(result["status"], "patched")

    async def test_patch_not_found(self):
        self.client._custom_objects.patch_namespaced_custom_object = AsyncMock(
            side_effect=_FakeApiException(status=404))
        with self.assertRaises(ValueError):
            await self.client.patch_openclaw_instance("openclaw", "agt-gone", {})


class TestGetOpenClawInstance(_BaseK8sTest):

    async def test_get_found(self):
        crd = {"metadata": {"name": "agt-1"}, "status": {"phase": "Running"}}
        self.client._custom_objects.get_namespaced_custom_object = AsyncMock(return_value=crd)
        result = await self.client.get_openclaw_instance("openclaw", "agt-1")
        self.assertEqual(result["metadata"]["name"], "agt-1")

    async def test_get_not_found(self):
        self.client._custom_objects.get_namespaced_custom_object = AsyncMock(
            side_effect=_FakeApiException(status=404))
        result = await self.client.get_openclaw_instance("openclaw", "agt-gone")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 3. Pod Status
# ---------------------------------------------------------------------------

class TestGetPodStatus(_BaseK8sTest):

    def _make_pod(self, name="agt-1-0", phase="Running", ready=True, restarts=0):
        """Build a mock pod object mimicking kubernetes_asyncio structure."""
        cs = MagicMock()
        cs.name = "openclaw"
        cs.ready = ready
        cs.restart_count = restarts
        cs.state = MagicMock()
        cs.state.running = True if phase == "Running" else None
        cs.state.waiting = True if phase == "Pending" else None
        cs.state.terminated = None

        cond = MagicMock()
        cond.type = "Ready"
        cond.status = "True" if ready else "False"

        pod = MagicMock()
        pod.metadata.name = name
        pod.status.phase = phase
        pod.spec.node_name = "ip-10-0-1-42"
        pod.status.start_time = None
        pod.status.container_statuses = [cs]
        pod.status.conditions = [cond]
        return pod

    async def test_pod_found_running(self):
        pod = self._make_pod(phase="Running")
        pods_list = MagicMock()
        pods_list.items = [pod]
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)

        result = await self.client.get_pod_status("openclaw", "agt-1")
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["phase"], "Running")
        self.assertEqual(result["containers"][0]["name"], "openclaw")
        self.assertTrue(result["containers"][0]["ready"])

    async def test_pod_not_found(self):
        pods_list = MagicMock()
        pods_list.items = []
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)

        result = await self.client.get_pod_status("openclaw", "agt-missing")
        self.assertEqual(result["status"], "not_found")
        self.assertIsNone(result["phase"])

    async def test_pod_pending(self):
        pod = self._make_pod(phase="Pending", ready=False)
        pods_list = MagicMock()
        pods_list.items = [pod]
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)

        result = await self.client.get_pod_status("openclaw", "agt-1")
        self.assertEqual(result["phase"], "Pending")
        self.assertFalse(result["containers"][0]["ready"])

    async def test_label_selector_used(self):
        pods_list = MagicMock()
        pods_list.items = []
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)

        await self.client.get_pod_status("openclaw", "agt-carol")
        call_kwargs = self.client._core_v1.list_namespaced_pod.call_args[1]
        self.assertIn("app.kubernetes.io/instance=agt-carol", call_kwargs["label_selector"])
        self.assertIn("app.kubernetes.io/name=openclaw", call_kwargs["label_selector"])


# ---------------------------------------------------------------------------
# 4. Pod Logs
# ---------------------------------------------------------------------------

class TestGetPodLogs(_BaseK8sTest):

    def _make_pod_with_containers(self, containers=None):
        containers = containers or ["openclaw"]
        statuses = []
        for name in containers:
            cs = MagicMock()
            cs.name = name
            statuses.append(cs)
        pod = MagicMock()
        pod.metadata.name = "agt-1-0"
        pod.status.container_statuses = statuses
        return pod

    async def test_logs_success(self):
        pod = self._make_pod_with_containers(["openclaw", "metrics-exporter"])
        pods_list = MagicMock()
        pods_list.items = [pod]
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)
        self.client._core_v1.read_namespaced_pod_log = AsyncMock(
            return_value="2026-04-07 INFO Agent started\n2026-04-07 INFO Ready")

        result = await self.client.get_pod_logs("openclaw", "agt-1")
        self.assertIn("Agent started", result["logs"])
        self.assertEqual(result["container"], "openclaw")
        self.assertEqual(result["available_containers"], ["openclaw", "metrics-exporter"])

    async def test_logs_pod_not_found(self):
        pods_list = MagicMock()
        pods_list.items = []
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)

        result = await self.client.get_pod_logs("openclaw", "agt-gone")
        self.assertEqual(result["error"], "Pod not found")
        self.assertEqual(result["logs"], "")

    async def test_logs_container_not_found(self):
        pod = self._make_pod_with_containers(["openclaw"])
        pods_list = MagicMock()
        pods_list.items = [pod]
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)

        result = await self.client.get_pod_logs("openclaw", "agt-1", container="nonexistent")
        self.assertIn("not found", result["error"])
        self.assertEqual(result["available_containers"], ["openclaw"])

    async def test_logs_custom_tail_lines(self):
        pod = self._make_pod_with_containers(["openclaw"])
        pods_list = MagicMock()
        pods_list.items = [pod]
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)
        self.client._core_v1.read_namespaced_pod_log = AsyncMock(return_value="log line")

        await self.client.get_pod_logs("openclaw", "agt-1", tail_lines=50)
        call_kwargs = self.client._core_v1.read_namespaced_pod_log.call_args[1]
        self.assertEqual(call_kwargs["tail_lines"], 50)


# ---------------------------------------------------------------------------
# 5. Operator Status
# ---------------------------------------------------------------------------

class TestCheckCrdExists(_BaseK8sTest):

    async def test_crd_exists(self):
        self.client._api_ext.read_custom_resource_definition = AsyncMock(
            return_value={"metadata": {"name": CRD_FULL_NAME}})
        self.assertTrue(await self.client.check_crd_exists())

    async def test_crd_not_found(self):
        self.client._api_ext.read_custom_resource_definition = AsyncMock(
            side_effect=_FakeApiException(status=404))
        self.assertFalse(await self.client.check_crd_exists())


class TestGetOperatorStatus(_BaseK8sTest):

    def _make_operator_pod(self, phase="Running", ready=True):
        cs = MagicMock()
        cs.ready = ready
        cs.restart_count = 0
        pod = MagicMock()
        pod.metadata.name = "openclaw-operator-controller-manager-abc123"
        pod.status.phase = phase
        pod.status.container_statuses = [cs]
        return pod

    def _make_operator_deployment(self, ready_replicas=1, image="ghcr.io/openclaw-rocks/openclaw-operator:v0.22.2"):
        dep = MagicMock()
        dep.status.ready_replicas = ready_replicas
        container = MagicMock()
        container.image = image
        dep.spec.template.spec.containers = [container]
        return dep

    async def test_operator_fully_installed(self):
        # CRD exists
        self.client._api_ext.read_custom_resource_definition = AsyncMock()
        # Pod running
        pods_list = MagicMock()
        pods_list.items = [self._make_operator_pod()]
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)
        # Deployment ready
        self.client._apps_v1.read_namespaced_deployment = AsyncMock(
            return_value=self._make_operator_deployment())

        status = await self.client.get_operator_status()
        self.assertTrue(status["installed"])
        self.assertTrue(status["crd_exists"])
        self.assertTrue(status["deployment_ready"])
        self.assertEqual(status["version"], "0.22.2")
        self.assertEqual(len(status["pods"]), 1)
        self.assertTrue(status["pods"][0]["ready"])

    async def test_operator_not_installed(self):
        # CRD missing
        self.client._api_ext.read_custom_resource_definition = AsyncMock(
            side_effect=_FakeApiException(status=404))
        # No pods
        pods_list = MagicMock()
        pods_list.items = []
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)
        # No deployment
        self.client._apps_v1.read_namespaced_deployment = AsyncMock(
            side_effect=_FakeApiException(status=404))

        status = await self.client.get_operator_status()
        self.assertFalse(status["installed"])
        self.assertFalse(status["crd_exists"])
        self.assertFalse(status["deployment_ready"])
        self.assertEqual(status["pods"], [])

    async def test_crd_exists_but_deployment_not_ready(self):
        self.client._api_ext.read_custom_resource_definition = AsyncMock()
        pods_list = MagicMock()
        pods_list.items = []
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)
        dep = self._make_operator_deployment(ready_replicas=0)
        dep.status.ready_replicas = None
        self.client._apps_v1.read_namespaced_deployment = AsyncMock(return_value=dep)

        status = await self.client.get_operator_status()
        self.assertFalse(status["installed"])
        self.assertTrue(status["crd_exists"])
        self.assertFalse(status["deployment_ready"])

    async def test_version_extracted_from_ecr_mirror_tag(self):
        self.client._api_ext.read_custom_resource_definition = AsyncMock()
        pods_list = MagicMock()
        pods_list.items = []
        self.client._core_v1.list_namespaced_pod = AsyncMock(return_value=pods_list)
        dep = self._make_operator_deployment(
            image="public.ecr.aws/t6v6o5d5/kube-prometheus:openclaw-operator-v0.22.2")
        self.client._apps_v1.read_namespaced_deployment = AsyncMock(return_value=dep)

        status = await self.client.get_operator_status()
        self.assertEqual(status["version"], "openclaw-operator-v0.22.2")


# ---------------------------------------------------------------------------
# 6. Operator Install / Upgrade (Helm subprocess)
# ---------------------------------------------------------------------------

class TestInstallOperator(_BaseK8sTest):

    async def test_install_success(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"release installed", b""))

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await self.client.install_operator(version="0.22.2")

        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["version"], "0.22.2")
        # Verify helm command
        cmd = mock_exec.call_args[0]
        self.assertEqual(cmd[0], "helm")
        self.assertEqual(cmd[1], "install")
        self.assertIn("--version", cmd)
        self.assertIn("0.22.2", cmd)

    async def test_install_china_region(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await self.client.install_operator(version="0.22.2", china_region=True)

        cmd = mock_exec.call_args[0]
        # Should include ECR mirror image overrides
        cmd_str = " ".join(cmd)
        self.assertIn("image.repository=public.ecr.aws", cmd_str)
        self.assertIn("openclaw-operator-v0.22.2", cmd_str)

    async def test_install_already_exists(self):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(
            return_value=(b"", b"cannot re-use a name that is still in use"))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await self.client.install_operator()

        self.assertEqual(result["status"], "already_installed")

    async def test_install_failure_raises(self):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"helm: command not found"))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with self.assertRaises(RuntimeError) as ctx:
                await self.client.install_operator()
        self.assertIn("helm install failed", str(ctx.exception))

    async def test_install_uses_default_version(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch.dict(os.environ, {"OPERATOR_VERSION": "0.23.0"}):
            # Re-read module-level default
            import services.k8s_client as _mod
            orig = _mod.OPERATOR_VERSION
            _mod.OPERATOR_VERSION = "0.23.0"
            try:
                with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
                    await self.client.install_operator()
                cmd = mock_exec.call_args[0]
                self.assertIn("0.23.0", cmd)
            finally:
                _mod.OPERATOR_VERSION = orig


class TestUpgradeOperator(_BaseK8sTest):

    async def test_upgrade_success(self):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"upgraded", b""))

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await self.client.upgrade_operator(version="0.23.0")

        self.assertEqual(result["status"], "upgraded")
        cmd = mock_exec.call_args[0]
        self.assertEqual(cmd[1], "upgrade")
        self.assertIn("0.23.0", cmd)

    async def test_upgrade_failure_raises(self):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"release not found"))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with self.assertRaises(RuntimeError) as ctx:
                await self.client.upgrade_operator()
        self.assertIn("helm upgrade failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
