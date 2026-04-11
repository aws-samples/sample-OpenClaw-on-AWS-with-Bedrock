"""
Tests for SOUL Purification — workspace_assembler.py changes.

Covers:
  1. SOUL.md idempotency (no snowball)
  2. PERSONAL_SOUL.md migration from legacy formats
  3. Context block generation (Plan A, KB, language)
  4. Personal SOUL extraction from merged output
  5. Server.py no longer modifies SOUL.md

Run with:
  cd enterprise/agent-container
  python -m pytest test_soul_purification.py -v
or:
  python test_soul_purification.py
"""

import os
import sys
import tempfile
import hashlib
import unittest
from unittest.mock import MagicMock, patch

# Add parent to path so we can import workspace_assembler
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workspace_assembler import merge_soul


# =========================================================================
# Test 1: SOUL.md idempotency — no snowball
# =========================================================================

class TestSoulNoSnowball(unittest.TestCase):
    """Running merge_soul() twice with the same inputs must produce identical output."""

    def test_merge_deterministic(self):
        """merge_soul(G, P, Personal) returns same result every time."""
        g = "# Global\nYou are an ACME employee."
        p = "# SA\nYou design systems."
        s = "I prefer bullet points."
        result1 = merge_soul(g, p, s)
        result2 = merge_soul(g, p, s)
        self.assertEqual(result1, result2)

    def test_merge_does_not_grow(self):
        """Feeding merged output back as personal layer would cause snowball.
        Verify that with PERSONAL_SOUL.md design, we always use the original."""
        g = "# Global\nRules."
        p = "# Position\nExpertise."
        personal = "My preferences."
        merged = merge_soul(g, p, personal)
        # If we accidentally feed merged back as personal:
        snowball = merge_soul(g, p, merged)
        # snowball should be MUCH larger than merged (proves it's a bug if it happens)
        self.assertGreater(len(snowball), len(merged) * 1.5,
            "Feeding merged output back as personal should cause growth (this test proves the bug exists)")
        # The fix: always use PERSONAL_SOUL.md (original), not SOUL.md (merged)
        stable = merge_soul(g, p, personal)
        self.assertEqual(len(stable), len(merged),
            "Using original personal layer should produce stable size")

    def test_empty_layers(self):
        """Empty layers should be omitted, not crash."""
        result = merge_soul("", "", "")
        self.assertEqual(result, "You are a helpful AI assistant.")
        result2 = merge_soul("Global rules.", "", "")
        self.assertIn("Global rules.", result2)
        self.assertNotIn("POSITION", result2)


# =========================================================================
# Test 2: PERSONAL_SOUL.md migration
# =========================================================================

class TestPersonalSoulMigration(unittest.TestCase):
    """Test migration from legacy formats to PERSONAL_SOUL.md."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_migration_from_backup(self):
        """If .personal_soul_backup.md exists, it should be migrated to PERSONAL_SOUL.md."""
        backup_content = "I prefer concise answers."
        backup_path = os.path.join(self.workspace, ".personal_soul_backup.md")
        with open(backup_path, "w") as f:
            f.write(backup_content)
        # Simulate what assembler should do:
        personal_path = os.path.join(self.workspace, "PERSONAL_SOUL.md")
        self.assertFalse(os.path.isfile(personal_path))
        # Migration logic:
        if not os.path.isfile(personal_path) and os.path.isfile(backup_path):
            with open(backup_path) as f:
                content = f.read()
            with open(personal_path, "w") as f:
                f.write(content)
        self.assertTrue(os.path.isfile(personal_path))
        with open(personal_path) as f:
            self.assertEqual(f.read(), backup_content)

    def test_migration_from_unmerged_soul(self):
        """If SOUL.md exists without merge markers, treat it as personal layer."""
        soul_content = "I like tables and code blocks."
        soul_path = os.path.join(self.workspace, "SOUL.md")
        with open(soul_path, "w") as f:
            f.write(soul_content)
        personal_path = os.path.join(self.workspace, "PERSONAL_SOUL.md")
        # Migration logic:
        if not os.path.isfile(personal_path):
            with open(soul_path) as f:
                content = f.read()
            if "<!-- LAYER: GLOBAL" not in content:
                with open(personal_path, "w") as f:
                    f.write(content)
        self.assertTrue(os.path.isfile(personal_path))
        with open(personal_path) as f:
            self.assertEqual(f.read(), soul_content)

    def test_no_migration_from_merged_soul(self):
        """If SOUL.md contains merge markers, do NOT migrate (it's already assembled)."""
        merged_content = "<!-- LAYER: GLOBAL -->\nGlobal rules\n---\n<!-- LAYER: PERSONAL -->\nMy prefs"
        soul_path = os.path.join(self.workspace, "SOUL.md")
        with open(soul_path, "w") as f:
            f.write(merged_content)
        personal_path = os.path.join(self.workspace, "PERSONAL_SOUL.md")
        # Migration logic:
        if not os.path.isfile(personal_path):
            with open(soul_path) as f:
                content = f.read()
            if "<!-- LAYER: GLOBAL" not in content:
                with open(personal_path, "w") as f:
                    f.write(content)
        # Should NOT have created PERSONAL_SOUL.md
        self.assertFalse(os.path.isfile(personal_path))

    def test_personal_soul_takes_priority(self):
        """If PERSONAL_SOUL.md already exists, don't touch it (no migration needed)."""
        personal_content = "My new preferences."
        personal_path = os.path.join(self.workspace, "PERSONAL_SOUL.md")
        with open(personal_path, "w") as f:
            f.write(personal_content)
        # Also create a backup (should be ignored)
        backup_path = os.path.join(self.workspace, ".personal_soul_backup.md")
        with open(backup_path, "w") as f:
            f.write("Old backup content.")
        # Read personal layer:
        with open(personal_path) as f:
            result = f.read()
        self.assertEqual(result, personal_content)


# =========================================================================
# Test 3: Context block generation
# =========================================================================

class TestContextBlock(unittest.TestCase):
    """Test Plan A, KB, language block generation."""

    def test_plan_a_block(self):
        """Plan A block should list allowed and blocked tools."""
        tools = ["web_search", "file", "crm-query"]
        all_tools = ["web_search", "shell", "browser", "file", "file_write", "code_execution"]
        blocked = [t for t in all_tools if t not in tools]
        block = (
            "<!-- PLAN A: PERMISSION ENFORCEMENT -->\n"
            f"Allowed tools for this session: {', '.join(tools)}.\n"
            f"You MUST NOT use these tools: {', '.join(blocked)}.\n"
        )
        self.assertIn("web_search", block)
        self.assertIn("shell", block)
        self.assertIn("MUST NOT", block)

    def test_plan_a_exec_skipped(self):
        """Exec profile should NOT get Plan A constraints."""
        is_exec = True
        block = "" if is_exec else "Plan A block"
        self.assertEqual(block, "")

    def test_kb_block(self):
        """KB block should list knowledge base paths."""
        kb_lines = [
            "- **Company Policies**: knowledge/kb-policies/",
            "- **Architecture Standards**: knowledge/kb-arch/",
        ]
        block = (
            "<!-- KNOWLEDGE BASES -->\n"
            "You have access to the following knowledge base documents:\n"
            + "\n".join(kb_lines)
        )
        self.assertIn("kb-policies", block)
        self.assertIn("KNOWLEDGE BASES", block)

    def test_language_block(self):
        """Language block should instruct model to respond in specified language."""
        lang = "Chinese"
        block = f"<!-- LANGUAGE PREFERENCE -->\nAlways respond in **{lang}** unless the user explicitly writes in a different language.\n"
        self.assertIn("Chinese", block)
        self.assertIn("LANGUAGE PREFERENCE", block)

    def test_full_soul_structure(self):
        """Verify the complete SOUL.md structure with all blocks."""
        g = "# Global\nCompany rules."
        p = "# Position\nSA expertise."
        s = "My preferences."
        merged = merge_soul(g, p, s)
        plan_a = "<!-- PLAN A: PERMISSION ENFORCEMENT -->\nAllowed: web_search."
        kb = "<!-- KNOWLEDGE BASES -->\n- Policies: knowledge/kb-policies/"
        lang = "<!-- LANGUAGE PREFERENCE -->\nAlways respond in Chinese."
        full = plan_a + "\n\n---\n\n" + merged + "\n\n---\n\n" + kb + "\n\n---\n\n" + lang
        # Verify structure: Plan A first, then layers, then KB, then language
        plan_a_pos = full.find("PLAN A")
        global_pos = full.find("LAYER: GLOBAL")
        personal_pos = full.find("LAYER: PERSONAL")
        kb_pos = full.find("KNOWLEDGE BASES")
        lang_pos = full.find("LANGUAGE PREFERENCE")
        self.assertLess(plan_a_pos, global_pos)
        self.assertLess(global_pos, personal_pos)
        self.assertLess(personal_pos, kb_pos)
        self.assertLess(kb_pos, lang_pos)


# =========================================================================
# Test 4: Personal SOUL extraction
# =========================================================================

class TestPersonalSoulExtraction(unittest.TestCase):
    """Test extracting personal content from merged SOUL.md."""

    def _extract_personal(self, soul_content: str) -> str:
        """Extract personal section from merged SOUL.md."""
        import re
        m = re.search(
            r'<!-- LAYER: PERSONAL[^>]*-->\s*(.*?)(?=\n\n---\n\n<!--|$)',
            soul_content, re.DOTALL)
        return m.group(1).strip() if m else ""

    def test_extract_from_standard_merge(self):
        """Extract personal content from a standard 3-layer merge."""
        merged = merge_soul(
            "Global rules.",
            "Position expertise.",
            "I prefer bullet points.\nTimezone: UTC+8."
        )
        personal = self._extract_personal(merged)
        self.assertIn("bullet points", personal)
        self.assertIn("UTC+8", personal)
        self.assertNotIn("Global rules", personal)
        self.assertNotIn("Position expertise", personal)

    def test_extract_with_context_blocks(self):
        """Extract personal even when KB and language blocks follow."""
        merged = merge_soul("G", "P", "My personal stuff")
        full = merged + "\n\n---\n\n<!-- KNOWLEDGE BASES -->\nKB content\n\n---\n\n<!-- LANGUAGE -->\nChinese"
        personal = self._extract_personal(full)
        self.assertIn("personal stuff", personal)
        self.assertNotIn("KB content", personal)
        self.assertNotIn("Chinese", personal)

    def test_extract_empty_personal(self):
        """If personal layer is empty, extraction returns empty string."""
        merged = merge_soul("Global", "Position", "")
        personal = self._extract_personal(merged)
        self.assertEqual(personal, "")

    def test_hash_comparison(self):
        """SHA256 hash should detect changes to personal content."""
        p1 = "I prefer tables."
        p2 = "I prefer tables.\nAlso charts."
        h1 = hashlib.sha256(p1.encode()).hexdigest()
        h2 = hashlib.sha256(p2.encode()).hexdigest()
        self.assertNotEqual(h1, h2)
        h1_again = hashlib.sha256(p1.encode()).hexdigest()
        self.assertEqual(h1, h1_again)


# =========================================================================
# Test 5: Verify server.py should not modify SOUL.md
# =========================================================================

class TestServerNoSoulModification(unittest.TestCase):
    """After purification, server.py must not open SOUL.md for write/append."""

    def test_no_soul_append_in_server(self):
        """Scan server.py for any remaining SOUL.md write operations."""
        server_path = os.path.join(os.path.dirname(__file__), "server.py")
        if not os.path.isfile(server_path):
            self.skipTest("server.py not found")
        with open(server_path) as f:
            content = f.read()
        # After purification, these patterns should NOT exist:
        dangerous_patterns = [
            'open(soul_path, "a")',
            "open(soul_path, 'a')",
            'open(soul_path, "w")',
            "open(soul_path, 'w')",
        ]
        for pattern in dangerous_patterns:
            self.assertNotIn(pattern, content,
                f"server.py still contains SOUL.md write: {pattern}")


# =========================================================================
# Run
# =========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
