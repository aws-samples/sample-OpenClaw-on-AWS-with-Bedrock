"""
Tests for Usage & Cost module.

Run with:
  cd enterprise/admin-console/server
  python test_usage_cost.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _read_usage():
    path = os.path.join(os.path.dirname(__file__), "routers", "usage.py")
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


class TestNoChatGPT(unittest.TestCase):
    def test_no_chatgpt_reference(self):
        content = _read_usage()
        self.assertNotIn("chatgpt", content.lower())

    def test_no_083(self):
        content = _read_usage()
        self.assertNotIn("0.83", content)


class TestUnknownModelStaysUnknown(unittest.TestCase):
    def test_no_default_to_nova(self):
        content = _read_usage()
        body = _read_func(content, "usage_by_model")
        self.assertIsNotNone(body)
        # Should NOT replace unknown with nova-2-lite
        self.assertNotIn('model = "global.amazon.nova-2-lite', body)


class TestNoSeedDateFallback(unittest.TestCase):
    def test_no_hardcoded_date(self):
        content = _read_usage()
        self.assertNotIn("2026-03-20", content)


class TestBudgetProjection7Day(unittest.TestCase):
    def test_not_simple_30x(self):
        content = _read_usage()
        body = _read_func(content, "usage_budgets")
        self.assertIsNotNone(body)
        # Should NOT have simple "used * 30" as the sole projection
        # Should have 7-day average logic
        self.assertNotIn("used * 30", body)

    def test_has_average_logic(self):
        content = _read_usage()
        body = _read_func(content, "usage_budgets")
        self.assertIsNotNone(body)
        self.assertTrue("7" in body or "daily" in body.lower() or "average" in body.lower() or "avg" in body.lower())


class TestFunctionRenamed(unittest.TestCase):
    def test_new_name_exists(self):
        content = _read_usage()
        self.assertIn("def _get_agent_usage_recent", content)

    def test_old_name_gone(self):
        content = _read_usage()
        self.assertNotIn("def _get_agent_usage_today", content)


class TestHierarchicalBudget(unittest.TestCase):
    def test_resolve_budget_exists(self):
        content = _read_usage()
        self.assertIn("def resolve_budget", content)

    def test_has_three_levels(self):
        content = _read_usage()
        body = _read_func(content, "resolve_budget")
        self.assertIsNotNone(body)
        self.assertIn("employees", body)
        self.assertIn("departments", body)
        self.assertIn("global", body)


class TestMyBudgetEndpoint(unittest.TestCase):
    def test_exists(self):
        content = _read_usage()
        self.assertIn("def my_budget", content)


class TestDepartmentBudgetEndpoint(unittest.TestCase):
    def test_exists(self):
        content = _read_usage()
        self.assertIn("def department_budget", content)


class TestBudgetUpdateAudit(unittest.TestCase):
    def test_has_audit(self):
        content = _read_usage()
        body = _read_func(content, "update_budgets")
        self.assertIsNotNone(body)
        self.assertIn("create_audit_entry", body)


class TestModelCache(unittest.TestCase):
    def test_cache_exists(self):
        content = _read_usage()
        self.assertIn("_model_usage_cache", content)


class TestModelPricingInServer(unittest.TestCase):
    def test_pricing_table(self):
        server_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "agent-container", "server.py")
        server_path = os.path.normpath(server_path)
        if not os.path.isfile(server_path):
            self.skipTest("server.py not found")
        with open(server_path) as f:
            content = f.read()
        self.assertIn("MODEL_PRICING", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
