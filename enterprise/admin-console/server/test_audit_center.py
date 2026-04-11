"""
Tests for Audit Center + Review Engine.

Run with:
  cd enterprise/admin-console/server
  python test_audit_center.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _read_file():
    path = os.path.join(os.path.dirname(__file__), "routers", "audit.py")
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


class TestScopeUsesActorId(unittest.TestCase):
    def test_uses_actor_id(self):
        content = _read_file()
        body = _read_func(content, "get_audit_entries")
        self.assertIsNotNone(body)
        self.assertIn("actorId", body)
        # Should NOT filter by actorName for scope
        # Check that names_in_scope pattern is gone
        self.assertNotIn("names_in_scope", body)


class TestTimeRangeParams(unittest.TestCase):
    def test_since_param(self):
        content = _read_file()
        body = _read_func(content, "get_audit_entries")
        self.assertIsNotNone(body)
        self.assertIn("since", body)

    def test_before_param(self):
        content = _read_file()
        body = _read_func(content, "get_audit_entries")
        self.assertIsNotNone(body)
        self.assertIn("before", body)


class TestScanThreshold(unittest.TestCase):
    def test_threshold_is_3(self):
        content = _read_file()
        body = _read_func(content, "_run_audit_scan")
        self.assertIsNotNone(body)
        self.assertIn(">= 3", body)


class TestNoHardcodedOrg(unittest.TestCase):
    def test_quality_uses_db_constant(self):
        content = _read_file()
        body = _read_func(content, "_calculate_agent_quality")
        self.assertIsNotNone(body)
        self.assertNotIn('"ORG#acme"', body)
        self.assertIn("db.ORG_PK", body)


class TestReviewQueue(unittest.TestCase):
    def test_queue_exists(self):
        content = _read_file()
        self.assertIn("def get_review_queue", content)

    def test_approve_exists(self):
        content = _read_file()
        self.assertIn("def approve_review", content)

    def test_reject_exists(self):
        content = _read_file()
        self.assertIn("def reject_review", content)


class TestAIAnalyze(unittest.TestCase):
    def test_exists(self):
        content = _read_file()
        self.assertIn("def ai_analyze", content)

    def test_calls_bedrock(self):
        content = _read_file()
        body = _read_func(content, "ai_analyze")
        self.assertIsNotNone(body)
        self.assertTrue("bedrock" in body.lower() or "converse" in body.lower())


class TestComplianceStats(unittest.TestCase):
    def test_exists(self):
        content = _read_file()
        self.assertIn("def get_compliance_stats", content)

    def test_returns_soul_compliance(self):
        content = _read_file()
        body = _read_func(content, "get_compliance_stats")
        self.assertIsNotNone(body)
        self.assertIn("soulCompliance", body)


class TestScanNewChecks(unittest.TestCase):
    def test_pending_review_check(self):
        content = _read_file()
        body = _read_func(content, "_run_audit_scan")
        self.assertIsNotNone(body)
        self.assertIn("pending", body.lower())
        self.assertIn("24h", body.lower().replace(" ", "").replace("hours", "h"))

    def test_denial_spike_check(self):
        content = _read_file()
        body = _read_func(content, "_run_audit_scan")
        self.assertIsNotNone(body)
        self.assertIn("permission_denied", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
