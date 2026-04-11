"""
Tests for Monitor Center rewrite.

Run with:
  cd enterprise/admin-console/server
  python test_monitor_center.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _read_file(relpath):
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


class TestNoCloudWatch(unittest.TestCase):
    def test_no_filter_log_events(self):
        content = _read_file("routers/monitor.py")
        self.assertNotIn("filter_log_events", content)

    def test_no_describe_log_groups(self):
        content = _read_file("routers/monitor.py")
        self.assertNotIn("describe_log_groups", content)

    def test_no_cloudwatch_client(self):
        content = _read_file("routers/monitor.py")
        self.assertNotIn('client("logs"', content)


class TestNoSSMInMonitor(unittest.TestCase):
    def test_no_ssm_put(self):
        content = _read_file("routers/monitor.py")
        self.assertNotIn("ssm.put_parameter", content)
        self.assertNotIn("ssm_client", content.split("from shared import")[0] if "from shared import" in content else "")

    def test_no_ssm_get(self):
        content = _read_file("routers/monitor.py")
        self.assertNotIn("ssm.get_parameter", content)

    def test_no_ssm_delete(self):
        content = _read_file("routers/monitor.py")
        self.assertNotIn("ssm.delete_parameter", content)


class TestTakeoverDynamoDB(unittest.TestCase):
    def test_takeover_uses_dynamodb(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "takeover_session")
        self.assertIsNotNone(body)
        self.assertIn("update_item", body)

    def test_takeover_has_ttl(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "takeover_session")
        self.assertIsNotNone(body)
        self.assertTrue("takeoverTTL" in body or "takeoverExpiresAt" in body)


class TestAdminMessageRole(unittest.TestCase):
    def test_role_is_admin(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "admin_send_message")
        self.assertIsNotNone(body)
        self.assertIn('"role": "admin"', body)
        self.assertNotIn('"role": "assistant"', body)


class TestSessionsNoCloudWatch(unittest.TestCase):
    def test_no_cw_merge(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "get_sessions")
        self.assertIsNotNone(body)
        self.assertNotIn("_query_cloudwatch", body)


class TestNewEndpoints(unittest.TestCase):
    def test_event_stream(self):
        content = _read_file("routers/monitor.py")
        self.assertIn("def get_event_stream", content)

    def test_action_items(self):
        content = _read_file("routers/monitor.py")
        self.assertIn("def get_action_items", content)

    def test_system_status(self):
        content = _read_file("routers/monitor.py")
        self.assertIn("def get_system_status", content)

    def test_agent_activity(self):
        content = _read_file("routers/monitor.py")
        self.assertIn("def get_agent_activity", content)

    def test_refresh_all(self):
        content = _read_file("routers/monitor.py")
        self.assertIn("def refresh_all_agents", content)


class TestAlertRulesNoPlaceholders(unittest.TestCase):
    def test_no_crash_loop(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "get_alert_rules")
        self.assertIsNotNone(body)
        self.assertNotIn("crash loop", body.lower())

    def test_no_channel_auth(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "get_alert_rules")
        self.assertNotIn("Channel auth", body)

    def test_no_memory_bloat(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "get_alert_rules")
        self.assertNotIn("Memory bloat", body)


class TestQualityReal(unittest.TestCase):
    def test_uses_real_calculation(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "get_session_detail")
        self.assertIsNotNone(body)
        self.assertIn("_calculate_agent_quality", body)

    def test_no_formula(self):
        content = _read_file("routers/monitor.py")
        body = _read_func(content, "get_session_detail")
        self.assertIsNotNone(body)
        self.assertNotIn("3.5 + turns", body)


class TestPlanERealPatterns(unittest.TestCase):
    def test_has_pii_patterns(self):
        content = _read_file("routers/monitor.py")
        self.assertTrue("PII_PATTERNS" in content or "SSN pattern" in content)

    def test_no_dollar_check(self):
        content = _read_file("routers/monitor.py")
        self.assertNotIn('"$" in msg', content)
        self.assertNotIn("'$' in msg", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
