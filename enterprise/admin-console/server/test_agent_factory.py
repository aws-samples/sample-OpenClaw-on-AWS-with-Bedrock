"""
Tests for Agent Factory module fixes.

Covers:
  1. DELETE agent cascade
  2. SOUL save audit trail
  3. No CloudWatch in agents.py
  4. Agent status from DynamoDB
  5. Skill assign no employee loop
  6. Skill keys cache
  7. Employee name sanitized
  8. SOUL save conflict detection

Run with:
  cd enterprise/admin-console/server
  python test_agent_factory.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestDeleteAgentEndpoint(unittest.TestCase):
    """Agent Factory must have a DELETE endpoint."""

    def test_delete_endpoint_exists(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertIn('delete("/api/v1/agents/', content,
            "agents.py should have DELETE /api/v1/agents/{agent_id} endpoint")

    def test_delete_has_audit(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        func_start = content.find("def delete_agent")
        if func_start == -1:
            self.skipTest("delete_agent not found")
        next_func = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("create_audit_entry", func_body,
            "delete_agent should create audit entry")


class TestDeleteAgentInDb(unittest.TestCase):
    """db.py must have delete_agent function."""

    def test_db_has_delete_agent(self):
        db_path = os.path.join(os.path.dirname(__file__), "db.py")
        with open(db_path) as f:
            content = f.read()
        self.assertIn("def delete_agent", content,
            "db.py should have delete_agent function")


class TestSoulSaveAudit(unittest.TestCase):
    """All SOUL save entry points must create audit entries."""

    def test_agents_soul_save_has_audit(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        func_start = content.find("def save_agent_soul")
        if func_start == -1:
            self.skipTest("save_agent_soul not found")
        next_func = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("audit_soul_change", func_body,
            "save_agent_soul should call audit_soul_change")

    def test_security_global_soul_has_audit(self):
        sec_path = os.path.join(os.path.dirname(__file__), "routers", "security.py")
        with open(sec_path) as f:
            content = f.read()
        func_start = content.find("def put_global_soul")
        if func_start == -1:
            self.skipTest("put_global_soul not found")
        next_func = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("audit_soul_change", func_body,
            "put_global_soul should call audit_soul_change")

    def test_security_position_soul_has_audit(self):
        sec_path = os.path.join(os.path.dirname(__file__), "routers", "security.py")
        with open(sec_path) as f:
            content = f.read()
        func_start = content.find("def put_position_soul")
        if func_start == -1:
            self.skipTest("put_position_soul not found")
        next_func = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("audit_soul_change", func_body,
            "put_position_soul should call audit_soul_change")

    def test_shared_has_audit_soul_change(self):
        shared_path = os.path.join(os.path.dirname(__file__), "shared.py")
        with open(shared_path) as f:
            content = f.read()
        self.assertIn("def audit_soul_change", content,
            "shared.py should have audit_soul_change function")


class TestNoCloudWatchInAgents(unittest.TestCase):
    """agents.py should not query CloudWatch for agent status."""

    def test_no_filter_log_events(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertNotIn("filter_log_events", content,
            "agents.py should not call CloudWatch filter_log_events")

    def test_no_describe_log_groups(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertNotIn("describe_log_groups", content,
            "agents.py should not call CloudWatch describe_log_groups")


class TestAgentStatusFromDynamoDB(unittest.TestCase):
    """Agent status derived from lastInvocationAt field."""

    def test_uses_last_invocation_at(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertIn("lastInvocationAt", content,
            "agents.py should use lastInvocationAt for status")


class TestSkillAssignNoEmployeeLoop(unittest.TestCase):
    """assign_skill_to_position should NOT loop through employees."""

    def test_no_employee_loop(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        func_start = content.find("def assign_skill_to_position")
        if func_start == -1:
            self.skipTest("assign_skill_to_position not found")
        next_func = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertNotIn("get_employees", func_body,
            "assign_skill_to_position should not loop through employees")


class TestSkillKeysCache(unittest.TestCase):
    """get_all_skill_keys should have a cache."""

    def test_cache_exists(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertIn("_skill_keys_cache", content,
            "agents.py should have _skill_keys_cache for TTL caching")


class TestEmployeeNameSanitized(unittest.TestCase):
    """Employee name in S3 workspace seed should be sanitized."""

    def test_name_escaped(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertIn("safe_name", content,
            "agents.py should sanitize employee name (safe_name) before S3 seed")


class TestSoulSaveConflict(unittest.TestCase):
    """SOUL save should support conflict detection."""

    def test_expected_version_in_model(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertIn("expectedVersion", content,
            "SoulSaveRequest should have expectedVersion field for conflict detection")

    def test_409_on_conflict(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        with open(agents_path) as f:
            content = f.read()
        self.assertIn("409", content,
            "save_agent_soul should return 409 on version conflict")


if __name__ == "__main__":
    unittest.main(verbosity=2)
