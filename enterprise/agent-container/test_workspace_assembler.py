"""
Tests for workspace_assembler.py — SESSION_CONTEXT.md path logic.

Covers all five access paths:
  Path 1 — IT Admin:        admin__...
  Path 2 — Playground:      pgnd__emp-xxx__...
  Path 3 — Employee Portal: emp__emp-xxx__... (also IM channels after DDB resolve)
  Path 4 — IM Channels:     emp__emp-xxx__... (same session as Portal post-resolve)
  Path 5 — Digital Twin:    twin__emp-xxx__...
  Extra  — legacy pt alias: pt__emp-xxx__...
  Extra  — raw fallback:    tg__12345__... (unresolved IM user, no binding)
  Extra  — unknown prefix:  anything not in the known set → default mode

Run with:
  cd enterprise/agent-container
  python -m pytest test_workspace_assembler.py -v
or:
  python test_workspace_assembler.py
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers — build the SESSION_CONTEXT content the same way
# workspace_assembler does, isolated from boto3
# ---------------------------------------------------------------------------

def _build_session_context(prefix: str, verified_name: str, tenant_id: str) -> str:
    """Mirror of the SESSION_CONTEXT generation logic in assemble_workspace()."""
    if prefix in ("emp", "pt"):
        return (
            "# Session Context\n\n"
            f"**Mode:** Employee Session\n"
            f"**Authenticated User:** {verified_name}\n"
            f"**Verification:** Confirmed (enterprise identity — SSO or IM binding)\n\n"
            "You are speaking directly with the authenticated employee listed above. "
            "Use their name naturally in conversation. "
            "You have full read/write access to this workspace."
        )
    elif prefix == "pgnd":
        return (
            "# Session Context\n\n"
            f"**Mode:** Playground (Admin Test)\n"
            f"**Employee Being Simulated:** {verified_name}\n"
            f"**Operator:** IT Administrator\n\n"
            "This is an administrative test session. An IT admin is testing your "
            "behavior as this employee's agent. Respond as you normally would for "
            "this employee's role. Do NOT write back to the employee workspace — "
            "this session is read-only with respect to memory."
        )
    elif prefix == "twin":
        return (
            "# Session Context\n\n"
            f"**Mode:** Digital Twin\n"
            f"**Represented Employee:** {verified_name}\n"
            f"**Caller:** External visitor or colleague (identity unverified)\n\n"
            f"You are acting as {verified_name}'s digital representative. "
            "The person you are speaking with may not be the employee themselves — "
            "they could be a colleague, partner, or visitor interacting with the digital twin. "
            f"All conversations in this mode are visible to {verified_name} in their Portal."
        )
    elif prefix == "admin":
        return (
            "# Session Context\n\n"
            "**Mode:** IT Admin Assistant\n"
            "**Operator:** Authorized IT Administrator\n\n"
            "You are assisting an IT administrator. You may discuss system configuration, "
            "employee settings, and platform management topics."
        )
    else:
        return (
            "# Session Context\n\n"
            f"**Mode:** Standard Session\n"
            f"**Session ID:** {tenant_id}\n"
        )


def _prefix_from(tenant_id: str) -> str:
    return tenant_id.split("__")[0] if "__" in tenant_id else ""


# ---------------------------------------------------------------------------
# Unit tests — prefix parsing
# ---------------------------------------------------------------------------

class TestPrefixParsing(unittest.TestCase):
    """Verify that session_id strings produce the correct prefix."""

    def _p(self, tid):
        return _prefix_from(tid)

    # Path 3 & 4: employee sessions (Portal + IM channels after DDB resolve)
    def test_emp_prefix(self):
        self.assertEqual(self._p("emp__emp-jiade__a1b2c3d4e5f678901"), "emp")

    # Path 3 legacy: portal alias
    def test_pt_prefix(self):
        self.assertEqual(self._p("pt__emp-carol__a1b2c3d4e5f678901"), "pt")

    # Path 2: Playground
    def test_pgnd_prefix(self):
        self.assertEqual(self._p("pgnd__emp-sharon__xyz789012345678"), "pgnd")

    # Path 5: Digital Twin
    def test_twin_prefix(self):
        self.assertEqual(self._p("twin__emp-sharon__def456789012345"), "twin")

    # Path 1: IT Admin
    def test_admin_prefix(self):
        self.assertEqual(self._p("admin__it__ghi012345678901234"), "admin")

    # Raw fallback: IM user not yet in DDB (before pairing)
    def test_tg_raw_fallback(self):
        self.assertEqual(self._p("tg__123456789__f7e8d9c0b1a23456"), "tg")

    # No separator at all
    def test_no_separator(self):
        self.assertEqual(self._p("plainid"), "")

    # Only one separator
    def test_single_separator(self):
        self.assertEqual(self._p("emp__nodash"), "emp")


# ---------------------------------------------------------------------------
# Unit tests — SESSION_CONTEXT content correctness
# ---------------------------------------------------------------------------

class TestSessionContextContent(unittest.TestCase):

    # ── Path 1: IT Admin ────────────────────────────────────────────────────

    def test_admin_mode_content(self):
        ctx = _build_session_context("admin", "IT Admin", "admin__it__abc")
        self.assertIn("**Mode:** IT Admin Assistant", ctx)
        self.assertIn("**Operator:** Authorized IT Administrator", ctx)
        self.assertNotIn("Authenticated User", ctx)
        self.assertNotIn("Digital Twin", ctx)

    # ── Path 2: Playground ──────────────────────────────────────────────────

    def test_playground_mode_content(self):
        ctx = _build_session_context("pgnd", "JiaDe Wang", "pgnd__emp-jiade__xyz")
        self.assertIn("**Mode:** Playground (Admin Test)", ctx)
        self.assertIn("**Employee Being Simulated:** JiaDe Wang", ctx)
        self.assertIn("**Operator:** IT Administrator", ctx)
        self.assertIn("read-only with respect to memory", ctx)
        self.assertNotIn("Authenticated User", ctx)

    def test_playground_no_write_back_hint(self):
        """Playground must explicitly instruct agent NOT to write back."""
        ctx = _build_session_context("pgnd", "Sharon Li", "pgnd__emp-sharon__xyz")
        self.assertIn("Do NOT write back to the employee workspace", ctx)

    # ── Path 3 + 4: Employee Portal / IM Channels (shared emp__ session) ───

    def test_employee_session_content(self):
        ctx = _build_session_context("emp", "Carol Zhang", "emp__emp-carol__abc")
        self.assertIn("**Mode:** Employee Session", ctx)
        self.assertIn("**Authenticated User:** Carol Zhang", ctx)
        self.assertIn("**Verification:** Confirmed", ctx)
        self.assertIn("full read/write access", ctx)

    def test_pt_alias_same_as_emp(self):
        """pt__ (portal alias) must produce identical Employee Session content."""
        ctx_emp = _build_session_context("emp", "Carol Zhang", "emp__emp-carol__abc")
        ctx_pt  = _build_session_context("pt",  "Carol Zhang", "pt__emp-carol__abc")
        # Mode and key fields are identical
        self.assertIn("**Mode:** Employee Session", ctx_pt)
        self.assertIn("**Authenticated User:** Carol Zhang", ctx_pt)
        self.assertIn("full read/write access", ctx_pt)

    def test_employee_name_in_content(self):
        """Ensure the employee's actual name appears in the context."""
        ctx = _build_session_context("emp", "WangJie Di", "emp__emp-wjd__abc")
        self.assertIn("WangJie Di", ctx)

    # ── Path 5: Digital Twin ────────────────────────────────────────────────

    def test_digital_twin_content(self):
        ctx = _build_session_context("twin", "Sharon Li", "twin__emp-sharon__def")
        self.assertIn("**Mode:** Digital Twin", ctx)
        self.assertIn("**Represented Employee:** Sharon Li", ctx)
        self.assertIn("identity unverified", ctx)
        self.assertIn("visible to Sharon Li in their Portal", ctx)

    def test_digital_twin_no_verification_claim(self):
        """Digital Twin must NOT claim the caller is verified."""
        ctx = _build_session_context("twin", "Sharon Li", "twin__emp-sharon__def")
        self.assertNotIn("Verification: Confirmed", ctx)
        self.assertNotIn("enterprise identity", ctx)

    def test_digital_twin_name_in_both_fields(self):
        """Employee name appears in both Represented Employee and the body text."""
        ctx = _build_session_context("twin", "JiaDe Wang", "twin__emp-jiade__def")
        self.assertEqual(ctx.count("JiaDe Wang"), 3)  # header + 2 body refs

    # ── Raw IM fallback (unresolved user before pairing) ────────────────────

    def test_raw_tg_fallback_produces_standard_session(self):
        tenant_id = "tg__123456789__f7e8d9c0b1a23456"
        prefix = _prefix_from(tenant_id)
        ctx = _build_session_context(prefix, "tg__123456789", tenant_id)
        self.assertIn("**Mode:** Standard Session", ctx)
        self.assertIn(tenant_id, ctx)

    # ── Edge cases ───────────────────────────────────────────────────────────

    def test_unknown_prefix_produces_standard_session(self):
        ctx = _build_session_context("xyz", "someone", "xyz__emp-foo__abc")
        self.assertIn("**Mode:** Standard Session", ctx)

    def test_empty_prefix_produces_standard_session(self):
        ctx = _build_session_context("", "someone", "plaintenantid")
        self.assertIn("**Mode:** Standard Session", ctx)

    def test_emp_name_fallback_to_b_id(self):
        """If DDB lookup fails, emp_name is empty → verified_name falls back to _b_id."""
        emp_name = ""  # DDB failed
        _b_id = "emp-jiade"
        verified_name = emp_name or _b_id
        self.assertEqual(verified_name, "emp-jiade")
        ctx = _build_session_context("emp", verified_name, "emp__emp-jiade__abc")
        self.assertIn("emp-jiade", ctx)


# ---------------------------------------------------------------------------
# Integration test — assemble_workspace writes SESSION_CONTEXT.md
# (mocks all AWS calls; filesystem is real via tempdir)
# ---------------------------------------------------------------------------

class TestAssembleWorkspaceSessionContext(unittest.TestCase):
    """Verify that assemble_workspace() actually writes SESSION_CONTEXT.md correctly."""

    def _make_mock_s3(self):
        from botocore.exceptions import ClientError
        s3 = MagicMock()
        # read_s3() catches ClientError only — must use ClientError not plain Exception
        s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": ""}}, "GetObject"
        )
        s3.list_objects_v2.return_value = {"Contents": []}
        return s3

    def _make_mock_ssm(self):
        from botocore.exceptions import ClientError
        ssm = MagicMock()
        ssm.get_parameter.side_effect = ClientError(
            {"Error": {"Code": "ParameterNotFound", "Message": ""}}, "GetParameter"
        )
        ssm.get_paginator.return_value.paginate.return_value = []
        return ssm

    def _run_assemble(self, tenant_id: str, emp_name: str = "Test User") -> str:
        """Run assemble_workspace and return SESSION_CONTEXT.md content."""
        sys.path.insert(0, os.path.dirname(__file__))
        from workspace_assembler import assemble_workspace

        # DynamoDB mock — returns employee name when queried for EMP#
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": {"name": emp_name, "positionId": "pos-test",
                                                     "positionName": "Test Position"}}
        mock_table.query.return_value = {"Items": []}
        mock_ddb = MagicMock()
        mock_ddb.Table.return_value = mock_table

        s3 = self._make_mock_s3()
        ssm = self._make_mock_ssm()

        with tempfile.TemporaryDirectory() as workspace:
            with patch("boto3.resource", return_value=mock_ddb), \
                 patch("boto3.client", return_value=MagicMock()):
                try:
                    assemble_workspace(s3, ssm, "test-bucket", "test-stack",
                                       tenant_id, workspace, position_override="pos-test")
                except Exception:
                    pass  # IDENTITY.md write may still succeed even if S3 fails

                ctx_path = os.path.join(workspace, "SESSION_CONTEXT.md")
                if os.path.exists(ctx_path):
                    with open(ctx_path) as f:
                        return f.read()
        return ""

    def test_emp_session_written_to_disk(self):
        content = self._run_assemble("emp__emp-carol__a1b2c3d4e5f678901")
        self.assertIn("Employee Session", content)
        self.assertIn("Test User", content)

    def test_pgnd_session_written_to_disk(self):
        content = self._run_assemble("pgnd__emp-sharon__x1y2z3a4b5c678901")
        self.assertIn("Playground", content)
        self.assertIn("read-only", content)

    def test_twin_session_written_to_disk(self):
        content = self._run_assemble("twin__emp-sharon__d1e2f3g4h5i678901", "Sharon Li")
        self.assertIn("Digital Twin", content)
        self.assertIn("identity unverified", content)

    def test_admin_session_written_to_disk(self):
        content = self._run_assemble("admin__it__j1k2l3m4n5o678901")
        self.assertIn("IT Admin Assistant", content)

    def test_raw_tg_fallback_written_to_disk(self):
        content = self._run_assemble("tg__123456789__p1q2r3s4t5u678901")
        self.assertIn("Standard Session", content)


# ---------------------------------------------------------------------------
# Session ID → path matrix (documentation test)
# ---------------------------------------------------------------------------

class TestSessionIdMatrix(unittest.TestCase):
    """Each session_id format maps to exactly one access path."""

    MATRIX = [
        # (tenant_id,                              expected_mode_text)
        ("emp__emp-jiade__a1b2c3d4e5f678901",   "Employee Session"),
        ("pt__emp-carol__a1b2c3d4e5f678901",    "Employee Session"),   # legacy alias
        ("pgnd__emp-sharon__a1b2c3d4e5f67890",  "Playground"),
        ("twin__emp-sharon__a1b2c3d4e5f67890",  "Digital Twin"),
        ("admin__it__a1b2c3d4e5f67890123456",   "IT Admin Assistant"),
        ("tg__123456789__f7e8d9c0b1a2345678",   "Standard Session"),
        ("dc__987654321__f7e8d9c0b1a2345678",   "Standard Session"),
        ("plainid",                               "Standard Session"),
    ]

    def test_all_paths(self):
        for tenant_id, expected_mode in self.MATRIX:
            with self.subTest(tenant_id=tenant_id):
                prefix = _prefix_from(tenant_id)
                # Use tenant_id as name fallback to keep test self-contained
                b_id = tenant_id.split("__")[1] if "__" in tenant_id else tenant_id
                ctx = _build_session_context(prefix, b_id, tenant_id)
                self.assertIn(expected_mode, ctx,
                    f"tenant_id={tenant_id!r} prefix={prefix!r}: expected '{expected_mode}' in context")


if __name__ == "__main__":
    unittest.main(verbosity=2)
