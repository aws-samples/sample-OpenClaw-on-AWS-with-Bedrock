"""
Tests for Settings & Admin Assistant module.

Run with:
  cd enterprise/admin-console/server
  python test_settings.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _read(relpath):
    path = os.path.join(os.path.dirname(__file__), relpath)
    with open(path) as f:
        return f.read()


def _read_func(content, func_name):
    start = content.find(f"def {func_name}")
    if start == -1:
        return None
    next_func = content.find("\ndef ", start + 1)
    next_dec = content.find("\n@router", start + 1)
    ends = [e for e in [next_func, next_dec] if e != -1]
    end = min(ends) if ends else len(content)
    return content[start:end]


class TestAdminAssistantNoSubprocess(unittest.TestCase):
    def test_no_subprocess_in_playground(self):
        content = _read("routers/playground.py")
        body = _read_func(content, "_admin_assistant_direct")
        self.assertIsNotNone(body)
        self.assertNotIn("subprocess", body)

    def test_delegates_to_admin_ai(self):
        content = _read("routers/playground.py")
        body = _read_func(content, "_admin_assistant_direct")
        self.assertIsNotNone(body)
        self.assertIn("admin_ai", body)

    def test_admin_ai_uses_bedrock(self):
        content = _read("routers/admin_ai.py")
        body = _read_func(content, "_admin_ai_loop")
        self.assertIsNotNone(body)
        self.assertIn("converse", body.lower())


class TestAdminAssistantAudit(unittest.TestCase):
    def test_admin_ai_has_audit(self):
        content = _read("routers/admin_ai.py")
        body = _read_func(content, "admin_ai_chat")
        self.assertIsNotNone(body)
        self.assertIn("admin_assistant_query", body)


class TestPlatformAccess(unittest.TestCase):
    def test_endpoint_exists(self):
        content = _read("routers/settings.py")
        self.assertIn("def get_platform_access", content)

    def test_has_ssm_command(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "get_platform_access")
        self.assertIsNotNone(body)
        self.assertIn("portNumber=18789", body)


class TestAdminHistoryEndpoints(unittest.TestCase):
    def test_get_history(self):
        content = _read("routers/settings.py")
        self.assertIn("def get_admin_history", content)

    def test_clear_history(self):
        content = _read("routers/settings.py")
        self.assertIn("def clear_admin_history", content)


class TestPlatformLogs(unittest.TestCase):
    def test_endpoint_exists(self):
        content = _read("routers/settings.py")
        self.assertIn("def get_platform_logs", content)

    def test_uses_journalctl(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "get_platform_logs")
        self.assertIsNotNone(body)
        self.assertIn("journalctl", body)


class TestModelConfigAudit(unittest.TestCase):
    def test_default_model_has_audit(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "set_default_model")
        self.assertIsNotNone(body)
        self.assertTrue("create_audit_entry" in body or "_audit_config" in body)

    def test_default_model_has_bump(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "set_default_model")
        self.assertIsNotNone(body)
        self.assertIn("bump_config_version", body)


class TestPositionModelRefresh(unittest.TestCase):
    def test_has_refresh(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "set_position_model")
        self.assertIsNotNone(body)
        self.assertIn("stop_employee_session", body)


class TestAdminAssistantConfigEnhanced(unittest.TestCase):
    def test_has_system_prompt(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "get_admin_assistant")
        self.assertIsNotNone(body)
        self.assertIn("systemPrompt", body)
        self.assertIn("maxHistoryTurns", body)

    def test_no_allowed_commands(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "get_admin_assistant")
        self.assertIsNotNone(body)
        self.assertNotIn("allowedCommands", body)


class TestServicesHasRegion(unittest.TestCase):
    def test_has_aws_region(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "get_services")
        self.assertIsNotNone(body)
        self.assertIn("awsRegion", body)


class TestModelEndpointsRoleCheck(unittest.TestCase):
    def test_set_default_model(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "set_default_model")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)

    def test_set_fallback_model(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "set_fallback_model")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)

    def test_set_position_model(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "set_position_model")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)

    def test_remove_position_model(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "remove_position_model")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)


class TestSecurityConfigRoleCheck(unittest.TestCase):
    def test_get_has_role(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "get_security_config_endpoint")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)

    def test_update_has_role(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "update_security_config")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)

    def test_update_has_audit(self):
        content = _read("routers/settings.py")
        body = _read_func(content, "update_security_config")
        self.assertIsNotNone(body)
        self.assertTrue("create_audit_entry" in body or "_audit_config" in body)


class TestRestartService(unittest.TestCase):
    def test_exists(self):
        content = _read("routers/settings.py")
        self.assertIn("def restart_service", content)


class TestAdminQueryAudit(unittest.TestCase):
    def test_audit_logged(self):
        """Admin AI chat should write audit entry."""
        content = _read("routers/admin_ai.py")
        body = _read_func(content, "admin_ai_chat")
        self.assertIsNotNone(body)
        self.assertIn("admin_assistant_query", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
