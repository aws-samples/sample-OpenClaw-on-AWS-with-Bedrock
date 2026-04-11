"""
Tests for Playground module.

Run with:
  cd enterprise/admin-console/server
  python test_playground.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _read():
    path = os.path.join(os.path.dirname(__file__), "routers", "playground.py")
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


class TestNoPosToolsHardcode(unittest.TestCase):
    def test_no_hardcode(self):
        content = _read()
        self.assertNotIn("_POS_TOOLS", content)


class TestProfilesUseDynamoDB(unittest.TestCase):
    def test_uses_tool_allowlist(self):
        content = _read()
        body = _read_func(content, "get_playground_profiles")
        self.assertIsNotNone(body)
        self.assertIn("toolAllowlist", body)


class TestPipelineConfigExists(unittest.TestCase):
    def test_exists(self):
        content = _read()
        self.assertIn("def get_pipeline_config", content)

    def test_has_soul_summary(self):
        content = _read()
        body = _read_func(content, "get_pipeline_config")
        self.assertIsNotNone(body)
        self.assertIn("globalWords", body)
        self.assertIn("personalWords", body)


class TestPlaygroundEventsExists(unittest.TestCase):
    def test_exists(self):
        content = _read()
        self.assertIn("def get_playground_events", content)


class TestSimulateUsesBedrock(unittest.TestCase):
    def test_simulate_function_exists(self):
        content = _read()
        self.assertIn("def _simulate_agent", content)

    def test_simulate_calls_bedrock(self):
        content = _read()
        body = _read_func(content, "_simulate_agent")
        self.assertIsNotNone(body)
        self.assertIn("converse", body.lower())


class TestNoKeywordMatching(unittest.TestCase):
    def test_no_is_shell(self):
        content = _read()
        self.assertNotIn("is_shell", content)

    def test_no_is_jira(self):
        content = _read()
        self.assertNotIn("is_jira", content)


class TestAdminDelegatesToAdminAI(unittest.TestCase):
    def test_delegates(self):
        content = _read()
        body = _read_func(content, "_admin_assistant_direct")
        self.assertIsNotNone(body)
        self.assertIn("admin_ai", body)

    def test_no_subprocess(self):
        content = _read()
        body = _read_func(content, "_admin_assistant_direct")
        self.assertIsNotNone(body)
        self.assertNotIn("subprocess", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
