"""
Tests for Organization Management module fixes.

Run with:
  cd enterprise/admin-console/server
  python test_organization.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestDeleteEmployeeAudit(unittest.TestCase):
    def test_has_audit(self):
        path = os.path.join(os.path.dirname(__file__), "routers", "org.py")
        with open(path) as f:
            content = f.read()
        func_start = content.find("def delete_employee")
        next_func = content.find("\ndef ", func_start + 1)
        body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("create_audit_entry", body,
            "delete_employee should create audit entry")


class TestDeleteDepartmentChecksPositions(unittest.TestCase):
    def test_checks_positions(self):
        path = os.path.join(os.path.dirname(__file__), "routers", "org.py")
        with open(path) as f:
            content = f.read()
        func_start = content.find("def delete_department")
        next_func = content.find("\ndef ", func_start + 1)
        body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("department_has_positions", body,
            "delete_department should check for positions referencing this department")


class TestNoAutoBindAll(unittest.TestCase):
    def test_no_auto_bind_all_in_org(self):
        path = os.path.join(os.path.dirname(__file__), "routers", "org.py")
        with open(path) as f:
            content = f.read()
        self.assertNotIn("autoBindAll", content,
            "org.py should not contain autoBindAll (shared agent design removed)")

    def test_no_shared_bindings_in_db(self):
        path = os.path.join(os.path.dirname(__file__), "db.py")
        with open(path) as f:
            content = f.read()
        self.assertNotIn("shared_bindings", content,
            "db.py should not contain shared_bindings parameter")


class TestDefaultChannelPortal(unittest.TestCase):
    def test_default_is_portal(self):
        path = os.path.join(os.path.dirname(__file__), "routers", "org.py")
        with open(path) as f:
            content = f.read()
        self.assertIn('"portal"', content,
            "org.py should use 'portal' as default channel")
        # Check that "slack" is not used as a default
        func_start = content.find("def _auto_provision_employee")
        if func_start != -1:
            next_func = content.find("\ndef ", func_start + 1)
            body = content[func_start:next_func] if next_func != -1 else content[func_start:]
            self.assertNotIn('defaultChannel", "slack"', body,
                "auto_provision should not default to slack")


class TestForceDeleteCascadesAgent(unittest.TestCase):
    def test_deletes_agent(self):
        path = os.path.join(os.path.dirname(__file__), "routers", "org.py")
        with open(path) as f:
            content = f.read()
        func_start = content.find("def delete_employee")
        next_func = content.find("\ndef ", func_start + 1)
        body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("delete_agent", body,
            "delete_employee should cascade to delete_agent")


class TestActivityCache(unittest.TestCase):
    def test_cache_exists(self):
        path = os.path.join(os.path.dirname(__file__), "routers", "org.py")
        with open(path) as f:
            content = f.read()
        self.assertIn("_activity_cache", content,
            "org.py should have _activity_cache for TTL caching")


if __name__ == "__main__":
    unittest.main(verbosity=2)
