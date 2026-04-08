"""
Tests for routers/admin_eks.py — EKS agent management endpoints.

Covers:
  1. Operator pre-flight — deploy blocked when operator not installed
  2. Deploy — creates CRD, writes SSM, updates DynamoDB
  3. Stop — deletes CRD, cleans SSM
  4. Reload — patches CRD config version
  5. Status — returns CRD + pod info
  6. Logs — returns pod logs
  7. Assign / Unassign — SSM endpoint routing
  8. Operator endpoints — status, install, upgrade
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add server dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock kubernetes_asyncio before any imports pull it in
sys.modules["kubernetes_asyncio"] = MagicMock()
sys.modules["kubernetes_asyncio.config"] = MagicMock()
sys.modules["kubernetes_asyncio.client"] = MagicMock()

# Mock the k8s_client singleton at module level
_mock_k8s = MagicMock()
_mock_k8s.get_operator_status = AsyncMock(return_value={
    "installed": True, "crd_exists": True, "deployment_ready": True,
    "namespace": "openclaw-operator-system", "version": "0.22.2", "pods": [],
})
_mock_k8s.create_openclaw_instance = AsyncMock(return_value={"status": "created", "name": "agt-1"})
_mock_k8s.delete_openclaw_instance = AsyncMock(return_value={"status": "deleted", "name": "agt-1"})
_mock_k8s.patch_openclaw_instance = AsyncMock(return_value={"status": "patched", "name": "agt-1"})
_mock_k8s.get_openclaw_instance = AsyncMock(return_value={"metadata": {"name": "agt-1"}, "status": {"phase": "Running"}})
_mock_k8s.get_pod_status = AsyncMock(return_value={"status": "found", "phase": "Running", "pod_name": "agt-1-0", "node": "ip-10-0-1-42", "containers": [], "conditions": []})
_mock_k8s.get_pod_logs = AsyncMock(return_value={"pod_name": "agt-1-0", "container": "openclaw", "logs": "INFO started", "available_containers": ["openclaw"], "tail_lines": 100})
_mock_k8s.install_operator = AsyncMock(return_value={"status": "installed", "version": "0.22.2", "namespace": "openclaw-operator-system", "output": "ok"})
_mock_k8s.upgrade_operator = AsyncMock(return_value={"status": "upgraded", "version": "0.23.0", "namespace": "openclaw-operator-system", "output": "ok"})
_mock_k8s.upsert_secret = AsyncMock(return_value="created")
_mock_k8s.delete_secret_key = AsyncMock(return_value=True)
_mock_k8s.get_secret_keys = AsyncMock(return_value=[])

# Patch before importing the router
with patch.dict("sys.modules", {
    "services.k8s_client": MagicMock(
        k8s_client=_mock_k8s,
        OPENCLAW_NAMESPACE="openclaw",
        OPERATOR_NAMESPACE="openclaw-operator-system",
    ),
}):
    # Mock shared module dependencies
    _mock_ssm_client = MagicMock()
    _mock_shared = MagicMock()
    _mock_shared.require_role = MagicMock(return_value=MagicMock(role="admin", employee_id="admin-1", name="Admin"))
    _mock_shared.require_auth = _mock_shared.require_role
    _mock_shared.ssm_client = MagicMock(return_value=_mock_ssm_client)
    _mock_shared.STACK_NAME = "test-stack"
    _mock_shared.GATEWAY_REGION = "us-east-1"
    _mock_shared.GATEWAY_ACCOUNT_ID = "123456789012"

    _mock_db = MagicMock()
    _mock_db.get_agent = MagicMock(return_value={
        "id": "agt-carol", "employeeId": "emp-carol", "positionId": "pos-sde",
        "name": "Carol's Agent", "status": "active", "deployMode": "serverless",
    })
    _mock_db.create_audit_entry = MagicMock()

    _mock_s3ops = MagicMock()
    _mock_s3ops.get_soul_layers = MagicMock(return_value={
        "global": {"SOUL.md": "You are a helpful AI assistant.", "AGENTS.md": "", "TOOLS.md": ""},
        "position": {"SOUL.md": "You are an SDE.", "AGENTS.md": ""},
        "personal": {"SOUL.md": "", "USER.md": "Carol, Engineering"},
    })

    with patch.dict("sys.modules", {"shared": _mock_shared, "db": _mock_db, "s3ops": _mock_s3ops, "auth": MagicMock()}):
        from fastapi.testclient import TestClient
        from routers import admin_eks as _admin_eks_module
        from routers.admin_eks import router

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

AUTH_HEADER = {"Authorization": "Bearer test-token"}


# ---------------------------------------------------------------------------
# 1. Operator Pre-flight Check
# ---------------------------------------------------------------------------

class TestOperatorPreflight(unittest.TestCase):

    def test_deploy_blocked_when_operator_not_installed(self):
        _mock_k8s.get_operator_status = AsyncMock(return_value={
            "installed": False, "crd_exists": False, "deployment_ready": False,
            "namespace": "openclaw-operator-system", "version": "", "pods": [],
        })
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 428)
        self.assertIn("not installed", resp.json()["detail"])

    def test_deploy_succeeds_when_operator_installed(self):
        _mock_k8s.get_operator_status = AsyncMock(return_value={
            "installed": True, "crd_exists": True, "deployment_ready": True,
            "namespace": "openclaw-operator-system", "version": "0.22.2", "pods": [],
        })
        _mock_k8s.create_openclaw_instance = AsyncMock(return_value={"status": "created", "name": "agt-carol"})
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deployed"])


# ---------------------------------------------------------------------------
# 2. Deploy
# ---------------------------------------------------------------------------

class TestDeploy(unittest.TestCase):

    def setUp(self):
        _mock_k8s.get_operator_status = AsyncMock(return_value={
            "installed": True, "crd_exists": True, "deployment_ready": True,
            "namespace": "openclaw-operator-system", "version": "0.22.2", "pods": [],
        })
        _mock_k8s.create_openclaw_instance = AsyncMock(return_value={"status": "created", "name": "agt-carol"})

    def test_deploy_success(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["deployed"])
        self.assertEqual(body["agentId"], "agt-carol")
        self.assertEqual(body["namespace"], "openclaw")
        self.assertIn("openclaw.svc:18789", body["endpoint"])
        # Verify workspace files from S3 SOUL layers are passed
        self.assertIn("SOUL.md", body["workspaceFiles"])

    def test_deploy_passes_workspace_files_and_skills(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER,
                           json={"skills": ["jina-reader", "deep-research-pro"]})
        self.assertEqual(resp.status_code, 200)
        call_kwargs = _mock_k8s.create_openclaw_instance.call_args[1]
        # Should pass assembled workspace files from s3ops.get_soul_layers
        self.assertIn("SOUL.md", call_kwargs["workspace_files"])
        self.assertIn("USER.md", call_kwargs["workspace_files"])
        # Should pass skills
        self.assertEqual(call_kwargs["skills"], ["jina-reader", "deep-research-pro"])

    def test_deploy_with_custom_model(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER,
                           json={"model": "bedrock/claude-opus"})
        self.assertEqual(resp.status_code, 200)
        call_kwargs = _mock_k8s.create_openclaw_instance.call_args[1]
        self.assertEqual(call_kwargs["model"], "bedrock/claude-opus")

    def test_deploy_agent_not_found(self):
        _mock_db.get_agent = MagicMock(return_value=None)
        resp = client.post("/api/v1/admin/eks/agt-missing/deploy", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 404)
        # Restore
        _mock_db.get_agent = MagicMock(return_value={
            "id": "agt-carol", "employeeId": "emp-carol", "positionId": "pos-sde",
            "name": "Carol's Agent", "status": "active",
        })

    def test_deploy_conflict_returns_409(self):
        _mock_k8s.create_openclaw_instance = AsyncMock(
            side_effect=ValueError("Agent agt-carol already exists"))
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("already exists", resp.json()["detail"])

    def test_deploy_creates_audit_entry(self):
        _mock_k8s.create_openclaw_instance = AsyncMock(return_value={"status": "created"})
        _mock_db.create_audit_entry.reset_mock()
        client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER, json={})
        _mock_db.create_audit_entry.assert_called_once()
        audit = _mock_db.create_audit_entry.call_args[0][0]
        self.assertEqual(audit["eventType"], "config_change")
        self.assertIn("EKS", audit["detail"])


# ---------------------------------------------------------------------------
# 3. Stop
# ---------------------------------------------------------------------------

class TestStop(unittest.TestCase):

    def setUp(self):
        _mock_k8s.delete_openclaw_instance = AsyncMock(return_value={"status": "deleted", "name": "agt-carol"})

    def test_stop_success(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/stop", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["stopped"])

    def test_stop_agent_not_found(self):
        _mock_db.get_agent = MagicMock(return_value=None)
        resp = client.post("/api/v1/admin/eks/agt-missing/stop", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 404)
        _mock_db.get_agent = MagicMock(return_value={
            "id": "agt-carol", "employeeId": "emp-carol", "positionId": "pos-sde",
            "name": "Carol's Agent", "status": "active",
        })


# ---------------------------------------------------------------------------
# 4. Reload
# ---------------------------------------------------------------------------

class TestReload(unittest.TestCase):

    def setUp(self):
        _mock_k8s.patch_openclaw_instance = AsyncMock(return_value={"status": "patched"})

    def test_reload_success(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/reload", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["reloaded"])
        self.assertIn("configVersion", body)

    def test_reload_with_model_update(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/reload", headers=AUTH_HEADER,
                           json={"model": "bedrock/claude-opus"})
        self.assertEqual(resp.status_code, 200)
        call_kwargs = _mock_k8s.patch_openclaw_instance.call_args[0]
        patch_body = call_kwargs[2]  # third positional arg is the patch dict
        # Model ID should use amazon-bedrock/ prefix (matching openclaw.json provider name)
        self.assertEqual(patch_body["spec"]["config"]["raw"]["agents"]["defaults"]["model"]["primary"],
                         "amazon-bedrock/claude-opus")
        # Should also include full Bedrock provider config with model details
        self.assertIn("models", patch_body["spec"]["config"]["raw"])
        bedrock_models = patch_body["spec"]["config"]["raw"]["models"]["providers"]["amazon-bedrock"]["models"]
        self.assertEqual(bedrock_models[0]["id"], "claude-opus")

    def test_reload_not_found(self):
        _mock_k8s.patch_openclaw_instance = AsyncMock(
            side_effect=ValueError("Agent agt-gone not found"))
        resp = client.post("/api/v1/admin/eks/agt-gone/reload", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 404)

    def test_reload_with_config_override(self):
        """configOverride deep-merges into existing CRD config.raw and patches the CRD."""
        _mock_k8s.patch_openclaw_instance = AsyncMock(return_value={"status": "patched"})
        _mock_k8s.get_openclaw_instance = AsyncMock(return_value={
            "metadata": {"name": "agt-carol"},
            "spec": {"config": {"raw": {
                "models": {"providers": {"amazon-bedrock": {"baseUrl": "https://bedrock.us-west-2.amazonaws.com"}}},
                "agents": {"defaults": {"model": {"primary": "amazon-bedrock/claude"}}},
            }}},
        })
        override = {
            "models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1", "apiKey": "sk-test"}}},
        }
        resp = client.post("/api/v1/admin/eks/agt-carol/reload", headers=AUTH_HEADER,
                           json={"configOverride": override})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["configOverrideApplied"])
        # Verify the patch contains merged config
        patch_body = _mock_k8s.patch_openclaw_instance.call_args[0][2]
        raw = patch_body["spec"]["config"]["raw"]
        # Original bedrock provider should be preserved
        self.assertIn("amazon-bedrock", raw["models"]["providers"])
        # New openai provider should be added
        self.assertIn("openai", raw["models"]["providers"])
        self.assertEqual(raw["models"]["providers"]["openai"]["baseUrl"], "https://api.openai.com/v1")

    def test_reload_config_override_invalid_type(self):
        """configOverride must be a dict, not a string."""
        resp = client.post("/api/v1/admin/eks/agt-carol/reload", headers=AUTH_HEADER,
                           json={"configOverride": "not-a-dict"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("JSON object", resp.json()["detail"])


# ---------------------------------------------------------------------------
# 4b. Config Read
# ---------------------------------------------------------------------------

class TestConfigRead(unittest.TestCase):

    def test_get_config_success(self):
        _mock_k8s.get_openclaw_instance = AsyncMock(return_value={
            "metadata": {"name": "agt-carol"},
            "spec": {"config": {"mergeMode": "merge", "raw": {
                "agents": {"defaults": {"model": {"primary": "amazon-bedrock/claude"}}},
            }}},
        })
        resp = client.get("/api/v1/admin/eks/agt-carol/config", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["agentId"], "agt-carol")
        self.assertEqual(body["mergeMode"], "merge")
        self.assertIn("agents", body["config"])

    def test_get_config_not_found(self):
        _mock_k8s.get_openclaw_instance = AsyncMock(return_value=None)
        resp = client.get("/api/v1/admin/eks/agt-missing/config", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# 4c. Deploy with Config Override
# ---------------------------------------------------------------------------

class TestDeployWithConfigOverride(unittest.TestCase):

    def setUp(self):
        _mock_k8s.get_operator_status = AsyncMock(return_value={
            "installed": True, "crd_exists": True, "deployment_ready": True,
            "namespace": "openclaw-operator-system", "version": "0.22.2", "pods": [],
        })
        _mock_k8s.create_openclaw_instance = AsyncMock(return_value={"status": "created", "name": "agt-carol"})
        _mock_db.get_agent = MagicMock(return_value={
            "id": "agt-carol", "employeeId": "emp-carol", "positionId": "pos-sde",
            "name": "Carol's Agent", "status": "active", "deployMode": "serverless",
        })

    def test_deploy_with_config_override(self):
        override = {"models": {"providers": {"openai": {"baseUrl": "https://api.openai.com/v1"}}}}
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER,
                           json={"configOverride": override})
        self.assertEqual(resp.status_code, 200)
        call_kwargs = _mock_k8s.create_openclaw_instance.call_args[1]
        self.assertEqual(call_kwargs["config_override"], override)

    def test_deploy_config_override_invalid_type(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER,
                           json={"configOverride": "not-a-dict"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("JSON object", resp.json()["detail"])

    def test_deploy_without_config_override_passes_none(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/deploy", headers=AUTH_HEADER, json={})
        self.assertEqual(resp.status_code, 200)
        call_kwargs = _mock_k8s.create_openclaw_instance.call_args[1]
        self.assertIsNone(call_kwargs["config_override"])


# ---------------------------------------------------------------------------
# 5. Status
# ---------------------------------------------------------------------------

class TestStatus(unittest.TestCase):

    def test_status_running(self):
        _mock_k8s.get_openclaw_instance = AsyncMock(
            return_value={"metadata": {"name": "agt-carol"}, "status": {"phase": "Running"}})
        _mock_k8s.get_pod_status = AsyncMock(return_value={
            "status": "found", "phase": "Running", "pod_name": "agt-carol-0",
            "node": "ip-10-0-1-42", "containers": [], "conditions": [],
        })
        resp = client.get("/api/v1/admin/eks/agt-carol/status", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["running"])
        self.assertIn("18789", body["endpoint"])

    def test_status_crd_not_found(self):
        _mock_k8s.get_openclaw_instance = AsyncMock(return_value=None)
        resp = client.get("/api/v1/admin/eks/agt-missing/status", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["running"])
        self.assertEqual(body["crdStatus"], "NOT_FOUND")

    def test_status_pod_pending(self):
        _mock_k8s.get_openclaw_instance = AsyncMock(return_value={"status": {"phase": "Pending"}})
        _mock_k8s.get_pod_status = AsyncMock(return_value={
            "status": "found", "phase": "Pending", "pod_name": "agt-1-0",
        })
        resp = client.get("/api/v1/admin/eks/agt-1/status", headers=AUTH_HEADER)
        body = resp.json()
        self.assertFalse(body["running"])
        self.assertIsNone(body["endpoint"])


# ---------------------------------------------------------------------------
# 6. Logs
# ---------------------------------------------------------------------------

class TestLogs(unittest.TestCase):

    def test_logs_success(self):
        _mock_k8s.get_pod_logs = AsyncMock(return_value={
            "pod_name": "agt-carol-0", "container": "openclaw",
            "logs": "INFO started", "available_containers": ["openclaw"],
            "tail_lines": 100,
        })
        resp = client.get("/api/v1/admin/eks/agt-carol/logs", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("INFO started", resp.json()["logs"])

    def test_logs_not_found(self):
        _mock_k8s.get_pod_logs = AsyncMock(return_value={"error": "Pod not found", "logs": ""})
        resp = client.get("/api/v1/admin/eks/agt-gone/logs", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 404)

    def test_logs_custom_params(self):
        _mock_k8s.get_pod_logs = AsyncMock(return_value={
            "pod_name": "agt-1-0", "container": "metrics-exporter",
            "logs": "metric line", "available_containers": ["openclaw", "metrics-exporter"],
            "tail_lines": 50,
        })
        resp = client.get("/api/v1/admin/eks/agt-1/logs?container=metrics-exporter&tail=50",
                          headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        call_kwargs = _mock_k8s.get_pod_logs.call_args[1]
        self.assertEqual(call_kwargs["container"], "metrics-exporter")
        self.assertEqual(call_kwargs["tail_lines"], 50)


# ---------------------------------------------------------------------------
# 7. Assign / Unassign
# ---------------------------------------------------------------------------

class TestAssign(unittest.TestCase):

    def test_assign_success(self):
        resp = client.put("/api/v1/admin/eks/agt-carol/assign/emp-carol", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["assigned"])
        self.assertIn("18789", body["endpoint"])

    def test_unassign_success(self):
        resp = client.delete("/api/v1/admin/eks/agt-carol/assign/emp-carol", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["unassigned"])


# ---------------------------------------------------------------------------
# 8. Operator Endpoints
# ---------------------------------------------------------------------------

class TestOperatorStatus(unittest.TestCase):

    def test_operator_status(self):
        _mock_k8s.get_operator_status = AsyncMock(return_value={
            "installed": True, "crd_exists": True, "deployment_ready": True,
            "namespace": "openclaw-operator-system", "version": "0.22.2", "pods": [],
        })
        resp = client.get("/api/v1/admin/eks/operator/status", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["installed"])
        self.assertEqual(body["version"], "0.22.2")

    def test_operator_status_k8s_unreachable(self):
        _mock_k8s.get_operator_status = AsyncMock(side_effect=Exception("Connection refused"))
        resp = client.get("/api/v1/admin/eks/operator/status", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 502)


# ---------------------------------------------------------------------------
# Route ordering — "operator" must not match {agent_id}
# ---------------------------------------------------------------------------

class TestRouteOrdering(unittest.TestCase):

    def test_operator_status_not_matched_as_agent_id(self):
        """Verify /admin/eks/operator/status hits the operator endpoint, not {agent_id}/status."""
        _mock_k8s.get_operator_status = AsyncMock(return_value={
            "installed": True, "crd_exists": True, "deployment_ready": True,
            "namespace": "openclaw-operator-system", "version": "0.22.2", "pods": [],
        })
        resp = client.get("/api/v1/admin/eks/operator/status", headers=AUTH_HEADER)
        body = resp.json()
        # Operator endpoint returns "installed" key; agent status returns "running"
        self.assertIn("installed", body)
        self.assertNotIn("running", body)


# ---------------------------------------------------------------------------
# 9. IM Bot Token Management
# ---------------------------------------------------------------------------

class TestSetEksTokens(unittest.TestCase):

    def setUp(self):
        _mock_k8s.upsert_secret = AsyncMock(return_value="created")
        _mock_k8s.delete_secret_key = AsyncMock(return_value=True)
        _mock_k8s.get_secret_keys = AsyncMock(return_value=["TELEGRAM_BOT_TOKEN"])
        _mock_k8s.get_openclaw_instance = AsyncMock(return_value={
            "metadata": {"name": "agt-carol"},
            "spec": {"env": [
                {"name": "EMPLOYEE_ID", "value": "emp-carol"},
                {"name": "STACK_NAME", "value": "test-stack"},
            ]},
            "status": {"phase": "Running"},
        })
        _mock_k8s.patch_openclaw_instance = AsyncMock(return_value={"status": "patched"})

    def test_set_token_creates_secret_and_patches_crd(self):
        resp = client.put("/api/v1/admin/eks/agt-carol/tokens", headers=AUTH_HEADER,
                          json={"telegramBotToken": "123456:ABC-DEF"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["agentId"], "agt-carol")
        self.assertIn("secretName", body)
        # Verify upsert_secret was called with token data
        _mock_k8s.upsert_secret.assert_awaited_once()
        call_kwargs = _mock_k8s.upsert_secret.call_args[1]
        self.assertEqual(call_kwargs["data"]["TELEGRAM_BOT_TOKEN"], "123456:ABC-DEF")
        # Verify CRD patch includes secretKeyRef env vars
        patch_call = _mock_k8s.patch_openclaw_instance.call_args[0]
        patch_body = patch_call[2]
        env = patch_body["spec"]["env"]
        token_env = [e for e in env if e.get("name") == "TELEGRAM_BOT_TOKEN"]
        self.assertEqual(len(token_env), 1)
        self.assertIn("secretKeyRef", token_env[0]["valueFrom"])

    def test_clear_token(self):
        _mock_k8s.get_secret_keys = AsyncMock(return_value=[])
        resp = client.put("/api/v1/admin/eks/agt-carol/tokens", headers=AUTH_HEADER,
                          json={"clearTelegramToken": True})
        self.assertEqual(resp.status_code, 200)
        _mock_k8s.delete_secret_key.assert_awaited_once()
        body = resp.json()
        self.assertFalse(body["saved"]["telegram"])


class TestGetEksTokens(unittest.TestCase):

    def test_get_tokens_status(self):
        _mock_k8s.get_secret_keys = AsyncMock(return_value=["TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN"])
        resp = client.get("/api/v1/admin/eks/agt-carol/tokens", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["telegram"], "configured")
        self.assertEqual(body["discord"], "configured")
        self.assertEqual(body["slack"], "not_configured")
        self.assertEqual(body["feishu"], "not_configured")


# ---------------------------------------------------------------------------
# 10. Image Tag Listing
# ---------------------------------------------------------------------------

class TestListImages(unittest.TestCase):

    def setUp(self):
        _mock_k8s.get_openclaw_instance = AsyncMock(return_value={
            "metadata": {"name": "agt-carol"},
            "spec": {"image": {"repository": "ghcr.io/openclaw/openclaw", "tag": "2026.3.1"}},
            "status": {"phase": "Running"},
        })

    def test_list_images_from_ghcr(self):
        mock_requests = MagicMock()
        # Mock token response
        token_resp = MagicMock()
        token_resp.ok = True
        token_resp.json.return_value = {"token": "ghcr-test-token"}
        # Mock tags response
        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = {"tags": ["2026.3.1", "2026.3.0", "latest"]}
        mock_requests.get.side_effect = [token_resp, tags_resp]

        with patch.object(_admin_eks_module, "_requests", mock_requests):
            resp = client.get("/api/v1/admin/eks/agt-carol/images", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("ghcr", body["sources"])
        self.assertGreater(len(body["images"]), 0)
        self.assertEqual(body["currentImage"]["tag"], "2026.3.1")
        self.assertEqual(body["currentImage"]["repository"], "ghcr.io/openclaw/openclaw")

    def test_list_images_ghcr_failure_graceful(self):
        mock_requests = MagicMock()
        mock_requests.get.side_effect = Exception("Connection timeout")
        with patch.object(_admin_eks_module, "_requests", mock_requests):
            resp = client.get("/api/v1/admin/eks/agt-carol/images", headers=AUTH_HEADER)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["images"], [])
        self.assertEqual(body["sources"], [])
        # Current image info should still be returned from CRD
        self.assertEqual(body["currentImage"]["tag"], "2026.3.1")


# ---------------------------------------------------------------------------
# 11. Image Update via Reload
# ---------------------------------------------------------------------------

class TestReloadImageUpdate(unittest.TestCase):

    def setUp(self):
        _mock_k8s.patch_openclaw_instance = AsyncMock(return_value={"status": "patched"})

    def test_reload_with_image_tag(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/reload", headers=AUTH_HEADER,
                           json={"imageTag": "2026.4.0"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["reloaded"])
        self.assertEqual(body["imageUpdated"], "2026.4.0")
        # Verify the CRD patch contains spec.image.tag
        patch_body = _mock_k8s.patch_openclaw_instance.call_args[0][2]
        self.assertEqual(patch_body["spec"]["image"]["tag"], "2026.4.0")

    def test_reload_with_full_image_uri(self):
        resp = client.post("/api/v1/admin/eks/agt-carol/reload", headers=AUTH_HEADER,
                           json={"image": "ghcr.io/openclaw/openclaw:2026.4.0"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["reloaded"])
        self.assertEqual(body["imageUpdated"], "ghcr.io/openclaw/openclaw:2026.4.0")
        patch_body = _mock_k8s.patch_openclaw_instance.call_args[0][2]
        self.assertEqual(patch_body["spec"]["image"]["repository"], "ghcr.io/openclaw/openclaw")
        self.assertEqual(patch_body["spec"]["image"]["tag"], "2026.4.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
