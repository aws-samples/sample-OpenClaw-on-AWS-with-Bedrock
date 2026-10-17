"""
Unit tests for MCP Connections backend router and agent container injection.

Tests verify:
1. Route prefix: /api/v1/mcp/* matches frontend client
2. Security: No subprocess spawned on admin host
3. Security: Role enforcement is strictly admin
4. Security: Fixed secret prefix openclaw/mcp/ enforced; rejects arbitrary paths
5. Security: Allowed env keys restricted to provider's required_env; rejects arbitrary env vars
6. Packages & env: @azure-devops/mcp, GITHUB_PERSONAL_ACCESS_TOKEN, JIRA_USERNAME
7. Audit & propagation: Audit entry recorded and bump_config_version called on connect/disconnect
8. Agent container injection: server.py contains mcpServers injection logic
9. IAM: CloudFormation template grants secretsmanager permissions on openclaw/*

Run with:
  cd enterprise/admin-console/server
  python3 test_mcp.py
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Ensure mock modules exist if running in an environment without external dependencies
if "boto3" not in sys.modules:
    mock_boto3 = MagicMock()
    sys.modules["boto3"] = mock_boto3

if "botocore" not in sys.modules:
    sys.modules["botocore"] = MagicMock()

if "botocore.exceptions" not in sys.modules:
    class MockClientError(Exception):
        def __init__(self, response):
            self.response = response
            super().__init__(str(response))
    mock_botocore_exc = MagicMock()
    mock_botocore_exc.ClientError = MockClientError
    sys.modules["botocore.exceptions"] = mock_botocore_exc

if "fastapi" not in sys.modules:
    class MockHTTPException(Exception):
        def __init__(self, status_code: int, detail: str = ""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(f"HTTP {status_code}: {detail}")
    def mock_decorator(*args, **kwargs):
        def wrapper(fn):
            return fn
        return wrapper
    mock_fastapi = MagicMock()
    mock_fastapi.HTTPException = MockHTTPException
    mock_fastapi.APIRouter = MagicMock(return_value=MagicMock(
        post=mock_decorator,
        get=mock_decorator,
        put=mock_decorator,
        delete=mock_decorator,
    ))
    mock_fastapi.Header = MagicMock(return_value=None)
    sys.modules["fastapi"] = mock_fastapi

if "pydantic" not in sys.modules:
    class MockBaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    mock_pydantic = MagicMock()
    mock_pydantic.BaseModel = MockBaseModel
    sys.modules["pydantic"] = mock_pydantic

if "db" not in sys.modules:
    sys.modules["db"] = MagicMock()

if "shared" not in sys.modules:
    sys.modules["shared"] = MagicMock()


def _read(relpath):
    path = os.path.join(os.path.dirname(__file__), relpath)
    with open(path) as f:
        return f.read()


class TestMCPSecurityAndDesign(unittest.TestCase):
    """Verify that routers/mcp.py complies with all architectural and security constraints."""

    def setUp(self):
        self.content = _read("routers/mcp.py")

    def test_no_subprocess_in_mcp_router(self):
        """Admin console MUST NOT spawn subprocesses on the admin host."""
        self.assertNotIn("subprocess.Popen", self.content)
        self.assertNotIn("subprocess.run", self.content)
        self.assertNotIn("import subprocess", self.content)

    def test_routes_use_api_v1_prefix(self):
        """All routes must use /api/v1/mcp/* prefix to match frontend client."""
        self.assertIn('@router.post("/api/v1/mcp/connect")', self.content)
        self.assertIn('@router.post("/api/v1/mcp/disconnect")', self.content)
        self.assertIn('@router.post("/api/v1/mcp/test")', self.content)
        self.assertIn('@router.get("/api/v1/mcp/status")', self.content)

    def test_role_enforcement_admin_only(self):
        """MCP endpoints must be restricted to admin role only, not manager or employee."""
        self.assertIn('require_role(authorization, ["admin"])', self.content)
        self.assertNotIn('["admin", "manager"]', self.content)

    def test_secret_prefix_enforcement(self):
        """Secret names must enforce openclaw/mcp/ prefix."""
        self.assertIn('SECRET_PREFIX = "openclaw/mcp/"', self.content)

    def test_correct_package_and_env_names(self):
        """Validate package names and environment variable names."""
        # Azure DevOps: @azure-devops/mcp (not @azure-devops/mcp-server)
        self.assertIn("@azure-devops/mcp@2.10.0", self.content)
        self.assertNotIn("@azure-devops/mcp-server", self.content)

        # GitHub: reads GITHUB_PERSONAL_ACCESS_TOKEN (not GITHUB_TOKEN)
        self.assertIn("GITHUB_PERSONAL_ACCESS_TOKEN", self.content)

        # Jira: reads JIRA_USERNAME (not JIRA_USER_EMAIL)
        self.assertIn("JIRA_USERNAME", self.content)
        self.assertNotIn("JIRA_USER_EMAIL", self.content)

    def test_audit_logging_present(self):
        """Audit logging must be invoked for both connect and disconnect."""
        self.assertIn('db.create_audit_entry(', self.content)
        self.assertIn('"eventType": "mcp_connect"', self.content)
        self.assertIn('"eventType": "mcp_disconnect"', self.content)

    def test_config_bump_present(self):
        """Config version must be bumped to trigger agent container refresh."""
        self.assertIn("bump_config_version()", self.content)


class TestSecretValidationLogic(unittest.TestCase):
    """Test secret name normalization and validation."""

    def test_validate_and_normalize_secret_name(self):
        from routers.mcp import _validate_and_normalize_secret_name
        from fastapi import HTTPException

        # Valid with leading slash (normalized)
        normalized = _validate_and_normalize_secret_name("/openclaw/mcp/github_token")
        self.assertEqual(normalized, "openclaw/mcp/github_token")

        # Valid without leading slash
        self.assertEqual(
            _validate_and_normalize_secret_name("openclaw/mcp/jira_token"),
            "openclaw/mcp/jira_token",
        )

        # Invalid: arbitrary secret outside openclaw/mcp/
        with self.assertRaises(HTTPException) as ctx:
            _validate_and_normalize_secret_name("my_secrets/admin_pw")
        self.assertEqual(ctx.exception.status_code, 400)

        # Invalid: path traversal attempt
        with self.assertRaises(HTTPException) as ctx:
            _validate_and_normalize_secret_name("openclaw/mcp/../other/secret")
        self.assertEqual(ctx.exception.status_code, 400)


class TestMCPEndpointsFunctional(unittest.TestCase):
    """Test functional behavior of MCP router endpoints."""

    def test_connect_success_and_audit(self):
        import asyncio
        from routers.mcp import mcp_connect, ConnectRequest, SecretMapping
        import db
        import shared

        mock_user = MagicMock(employee_id="emp-admin", name="Admin User", role="admin")
        shared.require_role.return_value = mock_user
        db.get_config.return_value = {"servers": {}}

        # Mock secretsmanager
        import boto3
        mock_sm = MagicMock()
        boto3.client.return_value = mock_sm

        req = ConnectRequest(
            provider="github",
            secretNames=[
                SecretMapping(
                    key="GITHUB_PERSONAL_ACCESS_TOKEN",
                    secretName="openclaw/mcp/github_pat",
                )
            ],
        )

        resp = asyncio.run(mcp_connect(req, authorization="Bearer valid-token"))
        self.assertEqual(resp["status"], "connected")
        self.assertEqual(resp["provider"], "github")

        # Verify DB config was updated
        db.set_config.assert_called()
        saved_data = db.set_config.call_args[0][1]
        self.assertTrue(saved_data["servers"]["github"]["enabled"])
        self.assertEqual(
            saved_data["servers"]["github"]["secrets"]["GITHUB_PERSONAL_ACCESS_TOKEN"],
            "openclaw/mcp/github_pat",
        )

        # Verify audit entry
        db.create_audit_entry.assert_called()
        audit_item = db.create_audit_entry.call_args[0][0]
        self.assertEqual(audit_item["eventType"], "mcp_connect")
        self.assertEqual(audit_item["targetId"], "github")

        # Verify config version bump
        shared.bump_config_version.assert_called()

    def test_connect_arbitrary_env_rejected(self):
        """Attempting to inject arbitrary env variables like NODE_OPTIONS must fail with 400."""
        import asyncio
        from routers.mcp import mcp_connect, ConnectRequest, SecretMapping
        import shared
        from fastapi import HTTPException

        mock_user = MagicMock(employee_id="emp-admin", name="Admin User", role="admin")
        shared.require_role.return_value = mock_user

        # Try to inject NODE_OPTIONS into GitHub
        req = ConnectRequest(
            provider="github",
            secretNames=[
                SecretMapping(
                    key="NODE_OPTIONS",
                    secretName="openclaw/mcp/evil_node_opts",
                )
            ],
        )

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(mcp_connect(req, authorization="Bearer valid-token"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid env key", ctx.exception.detail)

    def test_disconnect_success_and_audit(self):
        import asyncio
        from routers.mcp import mcp_disconnect, DisconnectRequest
        import db
        import shared

        mock_user = MagicMock(employee_id="emp-admin", name="Admin User", role="admin")
        shared.require_role.return_value = mock_user
        db.get_config.return_value = {
            "servers": {
                "github": {"enabled": True, "secrets": {}}
            }
        }

        req = DisconnectRequest(provider="github")
        resp = asyncio.run(mcp_disconnect(req, authorization="Bearer valid-token"))
        self.assertEqual(resp["status"], "disconnected")
        self.assertTrue(resp["wasRunning"])

        shared.bump_config_version.assert_called()
        db.create_audit_entry.assert_called()
        self.assertEqual(db.create_audit_entry.call_args[0][0]["eventType"], "mcp_disconnect")


class TestAgentContainerInjection(unittest.TestCase):
    """Test that agent container server.py contains MCP config injection logic and schema compliance."""

    def test_server_py_has_mcp_injection_and_schema(self):
        content = _read("../../agent-container/server.py")
        self.assertIn("def _sync_mcp_servers_config", content)
        self.assertIn("CONFIG#mcp-servers", content)
        # Verifies dual schema: canonical OpenClaw 2026.3.24 (mcp.servers) and legacy (mcpServers)
        self.assertIn('oc["mcp"]["servers"] = active_servers', content)
        self.assertIn('oc["mcpServers"] = active_servers', content)
        # Packages and commands
        self.assertIn("@modelcontextprotocol/server-github@2025.4.8", content)
        self.assertIn("@azure-devops/mcp@2.10.0", content)
        self.assertIn("mcp-atlassian==0.23.1", content)
        # Binary execution fallbacks
        self.assertIn("mcp-server-github", content)
        self.assertIn("mcp-server-azuredevops", content)
        self.assertIn("mcp-atlassian", content)
        # Azure DevOps org argument and token handling
        self.assertIn("ADO_MCP_AUTH_TOKEN", content)
        self.assertIn("PERSONAL_ACCESS_TOKEN", content)
        self.assertIn("--authentication", content)

    def test_dockerfile_preinstalls_mcp_servers(self):
        """Dockerfile must preinstall all MCP packages so microVMs run offline in restricted VPCs."""
        dockerfile = _read("../../agent-container/Dockerfile")
        self.assertIn("@azure-devops/mcp@2.10.0", dockerfile)
        self.assertIn("@modelcontextprotocol/server-github@2025.4.8", dockerfile)
        self.assertIn("mcp-atlassian==0.23.1", dockerfile)
        # npm and npx must be copied to runtime stage
        self.assertIn("COPY --from=builder /usr/bin/npm /usr/bin/npm", dockerfile)
        self.assertIn("COPY --from=builder /usr/bin/npx /usr/bin/npx", dockerfile)
        # Symlinks for executables
        self.assertIn("mcp-server-azuredevops", dockerfile)
        self.assertIn("mcp-server-github", dockerfile)

    def test_functional_sync_mcp_servers_config(self):
        """Direct functional test of _sync_mcp_servers_config writing both mcp.servers and mcpServers."""
        import tempfile
        import json
        from unittest.mock import patch, MagicMock

        # Create temporary openclaw.json
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tmp:
            tmp_path = tmp.name
            json.dump({"agents": {"defaults": {}}}, tmp)

        try:
            # Mock DynamoDB and Secrets Manager
            mock_boto3 = MagicMock()
            mock_table = MagicMock()
            mock_ddb = MagicMock()
            mock_ddb.Table.return_value = mock_table
            mock_sm = MagicMock()

            # Mock DynamoDB returning enabled GitHub and ADO
            mock_table.get_item.return_value = {
                "Item": {
                    "servers": {
                        "github": {
                            "enabled": True,
                            "secrets": {"GITHUB_PERSONAL_ACCESS_TOKEN": "openclaw/mcp/gh_token"},
                        },
                        "azure-devops": {
                            "enabled": True,
                            "secrets": {
                                "AZURE_DEVOPS_TOKEN": "openclaw/mcp/ado_tok",
                                "AZURE_DEVOPS_ORG_URL": "openclaw/mcp/ado_url",
                            },
                        },
                    }
                }
            }

            # Mock Secrets Manager returning secret strings
            def fake_get_secret(SecretId):
                if SecretId == "openclaw/mcp/gh_token":
                    return {"SecretString": "ghp_supersecret123"}
                elif SecretId == "openclaw/mcp/ado_tok":
                    return {"SecretString": "adopat456"}
                elif SecretId == "openclaw/mcp/ado_url":
                    return {"SecretString": "https://dev.azure.com/acmecorp"}
                return {"SecretString": ""}

            mock_sm.get_secret_value.side_effect = fake_get_secret

            def fake_resource(name, **kwargs):
                if name == "dynamodb":
                    return mock_ddb
                return MagicMock()

            def fake_client(name, **kwargs):
                if name == "secretsmanager":
                    return mock_sm
                return MagicMock()

            mock_boto3.resource = fake_resource
            mock_boto3.client = fake_client

            import importlib.util
            agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../agent-container"))
            if agent_dir not in sys.path:
                sys.path.insert(0, agent_dir)

            spec = importlib.util.spec_from_file_location("agent_server", os.path.join(agent_dir, "server.py"))
            agent_server = importlib.util.module_from_spec(spec)

            with patch.dict("sys.modules", {"boto3": mock_boto3}):
                spec.loader.exec_module(agent_server)
                # Run the sync function
                agent_server._sync_mcp_servers_config(oc_config_path=tmp_path)

            # Read back generated openclaw.json
            with open(tmp_path) as f:
                saved = json.load(f)

            # Assert canonical OpenClaw 2026.3.24 schema
            self.assertIn("mcp", saved)
            self.assertIn("servers", saved["mcp"])
            self.assertIn("github", saved["mcp"]["servers"])
            self.assertIn("azure-devops", saved["mcp"]["servers"])

            # Assert legacy mcpServers schema
            self.assertIn("mcpServers", saved)
            self.assertIn("github", saved["mcpServers"])
            self.assertIn("azure-devops", saved["mcpServers"])

            # Check GitHub configuration
            gh = saved["mcp"]["servers"]["github"]
            self.assertEqual(gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "ghp_supersecret123")
            self.assertEqual(gh["env"]["GITHUB_TOKEN"], "ghp_supersecret123")

            # Check Azure DevOps configuration
            ado = saved["mcp"]["servers"]["azure-devops"]
            self.assertEqual(ado["env"]["AZURE_DEVOPS_TOKEN"], "adopat456")
            self.assertEqual(ado["env"]["ADO_MCP_AUTH_TOKEN"], "adopat456")
            self.assertIn("PERSONAL_ACCESS_TOKEN", ado["env"])
            self.assertIn("acmecorp", ado["args"])
            self.assertIn("--authentication", ado["args"])
            self.assertIn("envvar", ado["args"])
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestCloudFormationIAMPolicies(unittest.TestCase):
    """Test that CloudFormation template includes strict least-privilege SecretsManager permissions."""

    def setUp(self):
        self.cfn = _read("../../clawdbot-bedrock-agentcore-multitenancy.yaml")

    def test_openclaw_instance_role_has_describe_only(self):
        """Admin console host must ONLY have DescribeSecret (no privilege escalation via GetSecretValue)."""
        self.assertIn("SecretsManagerDescribePolicy", self.cfn)
        self.assertIn("secretsmanager:DescribeSecret", self.cfn)
        # OpenClawInstanceRole section must not allow GetSecretValue
        inst_role_start = self.cfn.find("OpenClawInstanceRole:")
        inst_role_end = self.cfn.find("OpenClawInstanceProfile:", inst_role_start)
        inst_role_snippet = self.cfn[inst_role_start:inst_role_end]
        self.assertIn("secretsmanager:DescribeSecret", inst_role_snippet)
        self.assertNotIn("secretsmanager:GetSecretValue", inst_role_snippet)
        self.assertIn("arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:openclaw/mcp/*", inst_role_snippet)

    def test_container_roles_have_scoped_get_secret_value(self):
        """Container roles must have GetSecretValue strictly scoped to openclaw/mcp/*."""
        exec_role_start = self.cfn.find("AgentContainerExecutionRole:")
        exec_role_end = self.cfn.find("OpenClawSecurityGroup:", exec_role_start)
        exec_snippet = self.cfn[exec_role_start:exec_role_end]
        self.assertIn("secretsmanager:GetSecretValue", exec_snippet)
        self.assertIn("arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:openclaw/mcp/*", exec_snippet)

    def test_restricted_tier_has_no_secrets_access(self):
        """Restricted tier must NOT have any secretsmanager permissions."""
        restr_start = self.cfn.find("AlwaysOnTaskRoleRestricted:")
        restr_end = self.cfn.find("AlwaysOnTaskRoleEngineering:", restr_start)
        restr_snippet = self.cfn[restr_start:restr_end]
        self.assertNotIn("secretsmanager", restr_snippet)


if __name__ == "__main__":
    unittest.main()

