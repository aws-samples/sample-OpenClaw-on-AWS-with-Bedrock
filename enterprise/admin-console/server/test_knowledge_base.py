"""
Tests for Knowledge Base module redesign.

Covers:
  1. No hardcoded KB_PREFIXES in knowledge.py
  2. Upload size limit (1MB max)
  3. Search by filename only (no S3 GetObject)
  4. org-directory not inlined in SOUL.md
  5. KB assignment triggers force refresh
  6. Admin refresh endpoint
  7. Portal refresh rate limit

Run with:
  cd enterprise/admin-console/server
  python -m pytest test_knowledge_base.py -v
or:
  python test_knowledge_base.py
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# =========================================================================
# Test 1: No hardcoded KB_PREFIXES
# =========================================================================

class TestNoHardcodedKBPrefixes(unittest.TestCase):
    """knowledge.py must not contain hardcoded KB_PREFIXES dict."""

    def test_no_kb_prefixes_dict(self):
        kb_path = os.path.join(os.path.dirname(__file__), "routers", "knowledge.py")
        if not os.path.isfile(kb_path):
            self.skipTest("knowledge.py not found")
        with open(kb_path) as f:
            content = f.read()
        self.assertNotIn("KB_PREFIXES", content,
            "knowledge.py still contains hardcoded KB_PREFIXES dict")

    def test_uses_db_module(self):
        """knowledge.py should import and use db module for KB metadata."""
        kb_path = os.path.join(os.path.dirname(__file__), "routers", "knowledge.py")
        if not os.path.isfile(kb_path):
            self.skipTest("knowledge.py not found")
        with open(kb_path) as f:
            content = f.read()
        self.assertIn("import db", content,
            "knowledge.py should import db module")
        self.assertIn("db.get_knowledge_base", content,
            "knowledge.py should use db.get_knowledge_base()")


# =========================================================================
# Test 2: Upload size limit
# =========================================================================

class TestUploadSizeLimit(unittest.TestCase):
    """Upload endpoint must reject documents over 1MB."""

    def test_size_constant_exists(self):
        kb_path = os.path.join(os.path.dirname(__file__), "routers", "knowledge.py")
        if not os.path.isfile(kb_path):
            self.skipTest("knowledge.py not found")
        with open(kb_path) as f:
            content = f.read()
        self.assertIn("MAX_KB_DOC_SIZE", content,
            "knowledge.py should define MAX_KB_DOC_SIZE constant")

    def test_upload_checks_size(self):
        """Upload function should reference MAX_KB_DOC_SIZE for validation."""
        kb_path = os.path.join(os.path.dirname(__file__), "routers", "knowledge.py")
        if not os.path.isfile(kb_path):
            self.skipTest("knowledge.py not found")
        with open(kb_path) as f:
            content = f.read()
        # The upload function should check content length against the limit
        self.assertIn("413", content,
            "Upload endpoint should return 413 for oversized documents")


# =========================================================================
# Test 3: Search by filename only
# =========================================================================

class TestSearchFilenameOnly(unittest.TestCase):
    """Search should match KB name and document filename, not file content."""

    def test_search_no_read_file(self):
        """Search function should NOT call s3ops.read_file (no content reading)."""
        kb_path = os.path.join(os.path.dirname(__file__), "routers", "knowledge.py")
        if not os.path.isfile(kb_path):
            self.skipTest("knowledge.py not found")
        with open(kb_path) as f:
            content = f.read()
        # Find the search function body
        search_start = content.find("def search_knowledge")
        if search_start == -1:
            self.skipTest("search_knowledge function not found")
        # Find next function definition after search
        next_func = content.find("\ndef ", search_start + 1)
        search_body = content[search_start:next_func] if next_func != -1 else content[search_start:]
        self.assertNotIn("read_file", search_body,
            "search_knowledge should NOT read file contents (filename match only)")


# =========================================================================
# Test 4: org-directory not inlined in SOUL.md
# =========================================================================

class TestOrgDirectoryNotInlined(unittest.TestCase):
    """workspace_assembler should NOT inline org-directory content."""

    def test_no_inline_in_assembler(self):
        """_build_context_block should not contain COMPANY DIRECTORY inline logic."""
        assembler_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "agent-container", "workspace_assembler.py")
        assembler_path = os.path.normpath(assembler_path)
        if not os.path.isfile(assembler_path):
            self.skipTest("workspace_assembler.py not found")
        with open(assembler_path) as f:
            content = f.read()
        self.assertNotIn("COMPANY DIRECTORY (inline)", content,
            "workspace_assembler.py should not inline org-directory into SOUL.md")

    def test_org_directory_file_reference(self):
        """org-directory should be referenced as a file path, same as other KBs."""
        assembler_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "agent-container", "workspace_assembler.py")
        assembler_path = os.path.normpath(assembler_path)
        if not os.path.isfile(assembler_path):
            self.skipTest("workspace_assembler.py not found")
        with open(assembler_path) as f:
            content = f.read()
        # Should have instruction to read org-directory via file tool
        self.assertIn("org-directory", content,
            "assembler should reference org-directory as file path")


# =========================================================================
# Test 5: KB assignment triggers force refresh
# =========================================================================

class TestKBAssignTriggersRefresh(unittest.TestCase):
    """KB assignment change should call stop_employee_session."""

    def test_settings_calls_stop_session(self):
        """set_position_kbs should trigger stop_employee_session."""
        settings_path = os.path.join(os.path.dirname(__file__), "routers", "settings.py")
        if not os.path.isfile(settings_path):
            self.skipTest("settings.py not found")
        with open(settings_path) as f:
            content = f.read()
        # Find the set_position_kbs function
        func_start = content.find("def set_position_kbs")
        if func_start == -1:
            self.skipTest("set_position_kbs not found")
        next_func = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_func] if next_func != -1 else content[func_start:]
        self.assertIn("stop_employee_session", func_body,
            "set_position_kbs should call stop_employee_session for affected employees")


# =========================================================================
# Test 6: Admin refresh endpoint exists
# =========================================================================

class TestAdminRefreshEndpoint(unittest.TestCase):
    """Admin Console should have a force refresh endpoint."""

    def test_refresh_endpoint_exists(self):
        agents_path = os.path.join(os.path.dirname(__file__), "routers", "agents.py")
        if not os.path.isfile(agents_path):
            self.skipTest("agents.py not found")
        with open(agents_path) as f:
            content = f.read()
        self.assertIn("refresh-agent", content,
            "agents.py should have a refresh-agent endpoint")


# =========================================================================
# Test 7: Portal refresh with rate limit
# =========================================================================

class TestPortalRefreshRateLimit(unittest.TestCase):
    """Portal refresh should be rate limited."""

    def test_portal_refresh_endpoint_exists(self):
        portal_path = os.path.join(os.path.dirname(__file__), "routers", "portal.py")
        if not os.path.isfile(portal_path):
            self.skipTest("portal.py not found")
        with open(portal_path) as f:
            content = f.read()
        self.assertIn("refresh-agent", content,
            "portal.py should have a refresh-agent endpoint")

    def test_portal_refresh_has_rate_limit(self):
        portal_path = os.path.join(os.path.dirname(__file__), "routers", "portal.py")
        if not os.path.isfile(portal_path):
            self.skipTest("portal.py not found")
        with open(portal_path) as f:
            content = f.read()
        self.assertIn("429", content,
            "portal.py refresh should return 429 for rate limit")


# =========================================================================
# Run
# =========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
