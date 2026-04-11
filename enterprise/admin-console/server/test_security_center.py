"""
Tests for Security Center module fixes.

Run with:
  cd enterprise/admin-console/server
  python test_security_center.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _read_func(filepath, func_name):
    """Read a function body from a Python file."""
    with open(filepath) as f:
        content = f.read()
    start = content.find(f"def {func_name}")
    if start == -1:
        return None
    next_func = content.find("\ndef ", start + 1)
    # Also check for @router or class at same indent
    next_decorator = content.find("\n@router", start + 1)
    ends = [e for e in [next_func, next_decorator] if e != -1]
    end = min(ends) if ends else len(content)
    return content[start:end]


class TestRuntimeAssignmentDynamoDB(unittest.TestCase):
    """Runtime assignment must write DynamoDB CONFIG#routing."""

    def test_put_runtime_writes_dynamodb(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "put_position_runtime")
        self.assertIsNotNone(body, "put_position_runtime not found")
        self.assertIn("db.set_position_runtime", body,
            "put_position_runtime should call db.set_position_runtime for DynamoDB write")

    def test_put_runtime_no_employee_ssm_loop(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "put_position_runtime")
        self.assertIsNotNone(body)
        # Should NOT have per-employee SSM write
        self.assertNotIn('tenants/{emp', body,
            "put_position_runtime should not SSM-write per-employee runtime-id")
        self.assertNotIn("tenants/{emp", body)


class TestRuntimeMapReadsDynamoDB(unittest.TestCase):
    """Runtime map should read from DynamoDB, not SSM paginator."""

    def test_uses_dynamodb(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "get_position_runtime_map")
        self.assertIsNotNone(body, "get_position_runtime_map not found")
        self.assertIn("db.get_routing_config", body,
            "get_position_runtime_map should use db.get_routing_config()")

    def test_no_ssm_paginator(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "get_position_runtime_map")
        self.assertIsNotNone(body)
        self.assertNotIn("get_paginator", body,
            "get_position_runtime_map should not use SSM paginator")


class TestToolPermissionAudit(unittest.TestCase):
    """Tool permission changes need audit + force refresh + config bump."""

    def test_has_audit(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "put_position_tools")
        self.assertIsNotNone(body, "put_position_tools not found")
        self.assertIn("create_audit_entry", body,
            "put_position_tools should create audit entry")

    def test_has_force_refresh(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "put_position_tools")
        self.assertIsNotNone(body)
        self.assertIn("stop_employee_session", body,
            "put_position_tools should force refresh affected employees")

    def test_bumps_config_version(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "put_position_tools")
        self.assertIsNotNone(body)
        self.assertIn("bump_config_version", body,
            "put_position_tools should bump config version")


class TestRuntimeConfigForceRefresh(unittest.TestCase):
    """Runtime config update should force refresh affected agents."""

    def test_has_force_refresh(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "update_runtime_config")
        self.assertIsNotNone(body, "update_runtime_config not found")
        self.assertIn("stop_employee_session", body,
            "update_runtime_config should force refresh affected agents")


class TestPermissionDeniedDynamoDB(unittest.TestCase):
    """Permission denied events should write to DynamoDB AUDIT#."""

    def test_writes_dynamodb(self):
        perm_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "agent-container", "permissions.py")
        perm_path = os.path.normpath(perm_path)
        if not os.path.isfile(perm_path):
            self.skipTest("permissions.py not found")
        body = _read_func(perm_path, "_log_permission_denied")
        self.assertIsNotNone(body, "_log_permission_denied not found")
        self.assertIn("put_item", body,
            "permissions.py _log_permission_denied should write DynamoDB AUDIT#")


class TestRuntimeAuditUsesUserContext(unittest.TestCase):
    """Runtime assignment audit should use real user context, not hardcoded."""

    def test_uses_user_id(self):
        body = _read_func(
            os.path.join(os.path.dirname(__file__), "routers", "security.py"),
            "put_position_runtime")
        self.assertIsNotNone(body)
        self.assertIn("user.employee_id", body,
            "put_position_runtime audit should use user.employee_id")
        self.assertNotIn('"actorId": "admin"', body,
            "put_position_runtime should not hardcode actorId as 'admin'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
