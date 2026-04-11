"""
Tests for IM Channels module.

Run with:
  cd enterprise/admin-console/server
  python test_im_channels.py
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


class TestNoHardcodedOrgAcme(unittest.TestCase):
    def test_no_org_acme_in_admin_im(self):
        content = _read("routers/admin_im.py")
        self.assertNotIn('"ORG#acme"', content)
        self.assertNotIn("'ORG#acme'", content)


class TestGetImChannelsNoSsmInline(unittest.TestCase):
    def test_no_ssm_pagination_bug(self):
        content = _read("routers/admin_im.py")
        body = _read_func(content, "get_im_channels")
        self.assertIsNotNone(body)
        self.assertNotIn("get_parameters_by_path", body)


class TestDeduplicatedListUserMappings(unittest.TestCase):
    def test_no_list_user_mappings_in_admin_im(self):
        content = _read("routers/admin_im.py")
        self.assertNotIn("def _list_user_mappings", content)

    def test_no_list_user_mappings_in_bindings(self):
        content = _read("routers/bindings.py")
        self.assertNotIn("def _list_user_mappings", content)


class TestBotInfoHasAudit(unittest.TestCase):
    def test_audit_on_set(self):
        content = _read("routers/admin_im.py")
        body = _read_func(content, "set_im_bot_info")
        self.assertIsNotNone(body)
        self.assertIn("create_audit_entry", body)


class TestBotInfoHasPydantic(unittest.TestCase):
    def test_model_exists(self):
        content = _read("routers/admin_im.py")
        self.assertIn("class IMBotInfoUpdate", content)


class TestResolveRouteNoSharedAgent(unittest.TestCase):
    def test_no_shared_agent(self):
        content = _read("routers/bindings.py")
        body = _read_func(content, "resolve_route")
        self.assertIsNotNone(body)
        self.assertNotIn("route_to_shared_agent", body)
        self.assertNotIn("shared_agent", body)


class TestBindingsCrudAuth(unittest.TestCase):
    def test_create_user_mapping_auth(self):
        content = _read("routers/bindings.py")
        body = _read_func(content, "create_user_mapping")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)

    def test_delete_user_mapping_auth(self):
        content = _read("routers/bindings.py")
        body = _read_func(content, "delete_user_mapping")
        self.assertIsNotNone(body)
        self.assertIn("require_role", body)


class TestFindChannelNoSsm(unittest.TestCase):
    def test_find_channel_user_id_no_ssm(self):
        content = _read("routers/portal.py")
        body = _read_func(content, "_find_channel_user_id")
        self.assertIsNotNone(body)
        self.assertNotIn("ssm", body.lower())

    def test_list_user_mappings_for_employee_no_ssm(self):
        content = _read("routers/portal.py")
        body = _read_func(content, "_list_user_mappings_for_employee")
        self.assertIsNotNone(body)
        self.assertNotIn("ssm", body.lower())


class TestHealthEndpointExists(unittest.TestCase):
    def test_exists(self):
        content = _read("routers/admin_im.py")
        self.assertIn("def get_im_channel_health", content)


class TestEnrollmentEndpointExists(unittest.TestCase):
    def test_exists(self):
        content = _read("routers/admin_im.py")
        self.assertIn("def get_im_enrollment_stats", content)


class TestBatchUnbindExists(unittest.TestCase):
    def test_exists(self):
        content = _read("routers/admin_im.py")
        self.assertIn("def batch_unbind_channel", content)

    def test_has_audit(self):
        content = _read("routers/admin_im.py")
        body = _read_func(content, "batch_unbind_channel")
        self.assertIsNotNone(body)
        self.assertIn("create_audit_entry", body)


class TestPairingApproveHasAudit(unittest.TestCase):
    def test_has_audit(self):
        content = _read("routers/bindings.py")
        body = _read_func(content, "approve_pairing")
        self.assertIsNotNone(body)
        self.assertIn("create_audit_entry", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
