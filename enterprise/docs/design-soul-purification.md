# Design: SOUL Purification — Code Changes

**Date:** 2026-04-11
**Prereq:** analysis-soul-current-code.md (current state), PRD-soul-review-engine.md (target state)

---

## Decision: Option B — SOUL.md includes context block (assembled once)

**Reason:** OpenClaw reads ONLY `SOUL.md` as system prompt. A separate `CONTEXT.md` would require the model to proactively read it via `file` tool — unreliable for security-critical Plan A permissions. The model might skip it.

**Revised approach:**
- SOUL.md = 3-layer merge + context block (Plan A + KB refs + language + org-directory)
- **All written by workspace_assembler.py in ONE pass** (no server.py append)
- server.py touches SOUL.md **zero times**
- PERSONAL_SOUL.md = independent file for employee personal layer (synced to S3)

```
SOUL.md (assembled by workspace_assembler.py, single write):

  <!-- PLAN A: PERMISSION ENFORCEMENT -->
  Allowed tools: web_search, file. MUST NOT: shell, browser...

  ---

  <!-- LAYER: GLOBAL -->
  {global SOUL}

  ---

  <!-- LAYER: POSITION -->
  {position SOUL}

  ---

  <!-- LAYER: PERSONAL (from PERSONAL_SOUL.md) -->
  {personal SOUL}

  ---

  <!-- KNOWLEDGE BASES -->
  You have access to:
  - Company Policies: knowledge/kb-policies/
  ...

  <!-- COMPANY DIRECTORY (inline) -->
  {org-directory content}

  <!-- LANGUAGE PREFERENCE -->
  Always respond in Chinese...
```

**Key difference from current state:**
- Current: assembler writes SOUL.md, then server.py appends 4 times (`"w"` + `"a"` + `"a"` + `"a"`)
- New: assembler writes SOUL.md once with everything (`"w"` only)
- No server.py SOUL.md modification at all
- Idempotent: running assembler twice produces identical output

---

## File-by-File Change Design

### 1. workspace_assembler.py

#### New function: `build_context_block()`

```python
def build_context_block(
    s3_client, bucket: str, stack_name: str,
    tenant_id: str, base_id: str, pos_id: str, workspace: str,
) -> str:
    """Build the runtime context block (Plan A + KB + language + org-directory).
    Returns a string to append after the 3-layer merge in SOUL.md."""

    ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))

    import boto3
    ddb = boto3.resource("dynamodb", region_name=ddb_region)
    table = ddb.Table(ddb_table)
    parts = []

    # 1. Plan A — tool permissions from POS#.toolAllowlist
    try:
        from permissions import read_permission_profile
        profile = read_permission_profile(tenant_id)
        is_exec = profile.get("role") == "exec" or profile.get("profile") == "exec"
        is_twin = tenant_id.startswith("twin__")
        if not is_exec and not is_twin:
            tools = profile.get("tools", [])
            all_tools = ["web_search", "shell", "browser", "file", "file_write", "code_execution"]
            blocked = [t for t in all_tools if t not in tools]
            if tools:
                constraint = (
                    "<!-- PLAN A: PERMISSION ENFORCEMENT -->\n"
                    f"Allowed tools for this session: {', '.join(tools)}.\n"
                )
                if blocked:
                    constraint += (
                        f"You MUST NOT use these tools: {', '.join(blocked)}.\n"
                        "If the user requests an action requiring a blocked tool, "
                        "explain that you don't have permission and suggest alternatives.\n"
                    )
                parts.append(constraint)
    except Exception as e:
        logger.warning("Plan A context build failed: %s", e)

    # 2. Digital Twin context
    if tenant_id.startswith("twin__"):
        parts.append(
            "<!-- DIGITAL TWIN MODE -->\n"
            "You are this employee's AI digital representative.\n"
            "- Introduce yourself as their AI assistant standing in\n"
            "- Answer based on their expertise, SOUL profile, and memory\n"
            "- Be warm, professional, helpful — represent them well\n"
            "- Do NOT reveal private/sensitive internal data\n"
        )

    # 3. KB references + org-directory inline
    try:
        kb_cfg_resp = table.get_item(Key={"PK": "ORG#acme", "SK": "CONFIG#kb-assignments"})
        if "Item" in kb_cfg_resp:
            kb_cfg = kb_cfg_resp["Item"]
            kb_ids = set()
            if pos_id:
                kb_ids.update(kb_cfg.get("positionKBs", {}).get(pos_id, []))
            kb_ids.update(kb_cfg.get("employeeKBs", {}).get(base_id, []))

            if kb_ids:
                kb_lines = []
                has_org_dir = False
                for kb_id in kb_ids:
                    kb_item = table.get_item(
                        Key={"PK": "ORG#acme", "SK": f"KB#{kb_id}"}
                    ).get("Item")
                    if kb_item:
                        kb_lines.append(
                            f"- **{kb_item.get('name', kb_id)}**: knowledge/{kb_id}/"
                        )
                        if "org-directory" in kb_id:
                            has_org_dir = True

                if kb_lines:
                    parts.append(
                        "<!-- KNOWLEDGE BASES -->\n"
                        "You have access to the following knowledge base documents:\n"
                        + "\n".join(kb_lines)
                        + "\nUse the `file` tool to read these when relevant.\n"
                    )

                # Inline org-directory
                if has_org_dir:
                    try:
                        org_obj = s3_client.get_object(
                            Bucket=bucket,
                            Key="_shared/knowledge/org-directory/company-directory.md"
                        )
                        dir_content = org_obj["Body"].read().decode("utf-8")
                        parts.append(
                            "<!-- COMPANY DIRECTORY (inline) -->\n"
                            "The following is the complete employee directory. "
                            "Use this to answer questions about colleagues:\n\n"
                            + dir_content + "\n"
                        )
                    except Exception:
                        pass
    except Exception as e:
        logger.warning("KB context build failed: %s", e)

    # 4. Language preference
    try:
        agent_cfg_resp = table.get_item(
            Key={"PK": "ORG#acme", "SK": "CONFIG#agent-config"})
        if "Item" in agent_cfg_resp:
            cfg = agent_cfg_resp["Item"]
            emp_cfg = cfg.get("employeeConfig", {}).get(base_id, {})
            pos_cfg = cfg.get("positionConfig", {}).get(pos_id, {}) if pos_id else {}
            lang = emp_cfg.get("language") or pos_cfg.get("language", "")
            if lang:
                parts.append(
                    f"<!-- LANGUAGE PREFERENCE -->\n"
                    f"Always respond in **{lang}** unless the user "
                    f"explicitly writes in a different language.\n"
                )
    except Exception as e:
        logger.warning("Language context build failed: %s", e)

    return "\n---\n\n".join(parts) if parts else ""
```

#### Modified: `assemble_workspace()`

Changes to existing function:

```python
def assemble_workspace(...):
    # ... existing steps 1-3 (get position, read global, read position) ...

    # Step 4: Read personal layer — NEW: from PERSONAL_SOUL.md
    personal_soul_path = os.path.join(workspace, "PERSONAL_SOUL.md")
    personal_soul = ""
    if os.path.isfile(personal_soul_path):
        with open(personal_soul_path) as f:
            personal_soul = f.read()
        logger.info("Personal layer (PERSONAL_SOUL.md): %d chars", len(personal_soul))
    else:
        # Migration: if PERSONAL_SOUL.md doesn't exist, check old SOUL.md
        # This handles existing deployments that haven't been migrated yet.
        old_soul_path = os.path.join(workspace, "SOUL.md")
        backup_path = os.path.join(workspace, ".personal_soul_backup.md")
        if os.path.isfile(backup_path):
            with open(backup_path) as f:
                personal_soul = f.read()
            # Migrate: write PERSONAL_SOUL.md from backup
            with open(personal_soul_path, "w") as f:
                f.write(personal_soul)
            logger.info("Migrated .personal_soul_backup.md → PERSONAL_SOUL.md (%d chars)", len(personal_soul))
        elif os.path.isfile(old_soul_path):
            with open(old_soul_path) as f:
                content = f.read()
            # Only use if it doesn't contain merge markers (not yet assembled)
            if "<!-- LAYER: GLOBAL" not in content:
                personal_soul = content
                with open(personal_soul_path, "w") as f:
                    f.write(personal_soul)
                logger.info("Migrated SOUL.md → PERSONAL_SOUL.md (%d chars)", len(personal_soul))

    # REMOVED: .personal_soul_backup.md creation logic

    # Step 5: Merge 3 layers
    merged_soul = merge_soul(global_soul, position_soul, personal_soul)

    # Step 6: Build context block (Plan A + KB + language + org-directory)
    context_block = build_context_block(
        s3_client, bucket, stack_name, tenant_id, base_id, pos_id, workspace)
    if context_block:
        merged_soul += "\n\n---\n\n" + context_block

    # Step 7: Write SOUL.md (single write, complete)
    with open(os.path.join(workspace, "SOUL.md"), "w") as f:
        f.write(merged_soul)

    # ... rest unchanged (AGENTS.md, TOOLS.md, knowledge sync, IDENTITY.md, etc.) ...
```

#### KB file download — stays in assembler (moved from server.py)

```python
    # Step 6.5: Download KB files to workspace/knowledge/
    # (moved from server.py to assembler so it's part of the single assembly pass)
    if kb_ids:  # from build_context_block scope
        kb_dir = os.path.join(workspace, "knowledge")
        os.makedirs(kb_dir, exist_ok=True)
        for kb_id in kb_ids:
            # ... same download logic currently in server.py:657-669 ...
```

**Decision: Move KB download to assembler** rather than keeping in server.py. Reason: assembler already has the S3 client and KB assignments from build_context_block(). No need to read DynamoDB twice.

### 2. server.py — Remove SOUL.md modifications

**Lines to remove/refactor:**

| Lines | Current | New |
|-------|---------|-----|
| 420-466 | Plan A prepend + Twin context append | **DELETE** (moved to assembler) |
| 609-617 | Language preference append | **DELETE** (moved to assembler) |
| 619-708 | KB assignments + file download + SOUL append | **DELETE** (moved to assembler) |
| 530-607 | Model override + agent config | **KEEP** (writes openclaw.json, not SOUL.md) |

**Refactored `_ensure_workspace_assembled()`:**

```python
def _ensure_workspace_assembled(tenant_id: str) -> None:
    # ... existing session storage check (lines 288-303) ...
    # ... existing base_id extraction (lines 314-363) ...
    # ... existing S3 workspace cp (lines 381-396) ...

    # 2. Run workspace_assembler.py (now does EVERYTHING: merge + context + KB download)
    # ... existing assembler call (lines 398-418) ...

    # 3. REMOVED: Plan A injection (was lines 420-466)
    # 4. Re-source skill env vars (keep, lines 468-480)
    # 5. Write tenant_id files (keep, lines 482-490)
    # 6. MEMORY.md synthesis (keep, lines 492-528)
    # 7. Model + agent config (keep, lines 530-607)
    #    REMOVED: language injection (was lines 609-617)
    #    REMOVED: KB injection (was lines 619-708)

    _assembled_tenants.add(tenant_id)
```

### 3. entrypoint.sh — Watchdog + SIGTERM changes

**Watchdog exclude list (line 302-308):**
```bash
# CHANGED: remove .personal_soul_backup.md (no longer exists)
# ADD: PERSONAL_SOUL.md must NOT be excluded (it's synced back!)
aws s3 sync "$WORKSPACE/" "$SYNC_TARGET" \
    --exclude "node_modules/*" --exclude "skills/_shared/*" --exclude "skills/*" \
    --exclude "SOUL.md" --exclude "AGENTS.md" --exclude "TOOLS.md" \
    --exclude "IDENTITY.md" --exclude "SESSION_CONTEXT.md" --exclude "CHANNELS.md" \
    --exclude "knowledge/*" \
    --size-only --region "$AWS_REGION" \
    --quiet 2>/dev/null
```

Note: `PERSONAL_SOUL.md` is NOT in the exclude list → it gets synced back to S3.
Also added `SESSION_CONTEXT.md` and `CHANNELS.md` to exclude (they're regenerated, were missing).

**SIGTERM handler (cleanup function, line 326-391):**
```bash
cleanup() {
    echo "[entrypoint] SIGTERM — flushing workspace"

    # ... existing: deregister SSM, stop server, stop Gateway (lines 330-349) ...

    # NEW: Extract personal SOUL delta before final sync
    if [ -f "/app/personal_soul_extractor.py" ] && [ -f "$WORKSPACE/SOUL.md" ]; then
        timeout 3 python3 /app/personal_soul_extractor.py \
            --workspace "$WORKSPACE" \
            --bucket "$S3_BUCKET" \
            --base-id "$(cat /tmp/base_tenant_id 2>/dev/null || echo unknown)" \
            --region "$AWS_REGION" \
            --stack "$STACK_NAME" 2>&1 || true
    fi

    # ... existing: stop bg worker, final S3 sync (lines 352-388) ...
}
```

### 4. agents.py — SOUL editor

**`save_agent_soul()` (line 273-297):**
```python
@router.put("/api/v1/agents/{agent_id}/soul")
def save_agent_soul(agent_id: str, body: SoulSaveRequest, ...):
    # ... existing auth + agent lookup ...

    if body.layer == "personal":
        # NEW: write to PERSONAL_SOUL.md instead of position SOUL path
        s3_key = f"{emp_id}/workspace/PERSONAL_SOUL.md"
        s3ops.write_file(s3_key, body.content)
        # ... version increment + audit entry ...
    elif body.layer == "position":
        # existing: write to _shared/soul/positions/{pos_id}/SOUL.md
        result = s3ops.save_soul_layer(body.layer, pos_id, emp_id, "SOUL.md", body.content)
        # ...
```

**`create_agent()` S3 seed (revised line ~196-216):**
```python
# Add PERSONAL_SOUL.md to workspace seed
s3.put_object(Bucket=s3_bucket, Key=f"{prefix}PERSONAL_SOUL.md",
    Body=b"# Personal Preferences\n\n(Edit this to customize your AI agent's behavior.)\n")
```

**`get_agent_soul()` (line 246-266):**
```python
# Read personal layer from PERSONAL_SOUL.md instead of SOUL.md
personal_soul = s3ops.read_file(f"{emp_id}/workspace/PERSONAL_SOUL.md") if emp_id else ""
```

### 5. New file: personal_soul_extractor.py (Phase 2)

```python
"""Extract personal SOUL delta from merged SOUL.md at session end.
Called by entrypoint.sh SIGTERM handler. Must complete in <3 seconds."""

def extract_personal(soul_content: str) -> str:
    """Strip known layers from merged SOUL.md, return personal content only."""
    # Split by <!-- LAYER: markers
    # Content between <!-- LAYER: PERSONAL --> and next <!-- or end = personal
    import re
    m = re.search(
        r'<!-- LAYER: PERSONAL[^>]*-->\s*(.*?)(?=\n---\n\n<!--|\Z)',
        soul_content, re.DOTALL)
    return m.group(1).strip() if m else ""

def main():
    # 1. Read workspace/SOUL.md
    # 2. extract_personal() → personal_content
    # 3. SHA256 compare with PERSONAL_SOUL.md
    # 4. If different + S3 not newer → save + write AUDIT#
```

### 6. DynamoDB — No new tables needed

All records use existing single-table design:
- `AUDIT# personal_soul_change` — new eventType, same table
- `USAGE_PATTERN#{emp}#{date}` — new SK pattern, same table (Phase 3)

No CloudFormation changes required.

---

## Migration Strategy

For existing deployments with employees who have `.personal_soul_backup.md` or SOUL.md containing personal content:

```
workspace_assembler.py handles this automatically:
  1. Check PERSONAL_SOUL.md → if exists, use it (new format)
  2. Else check .personal_soul_backup.md → migrate to PERSONAL_SOUL.md
  3. Else check SOUL.md → if no merge markers, migrate to PERSONAL_SOUL.md
  4. If all empty → personal layer is empty (fine)
```

For deploy.sh seed:
```
Step 5 (S3 upload) needs to create per-employee PERSONAL_SOUL.md:
  For each position template, copy to:
    {emp_id}/workspace/PERSONAL_SOUL.md = empty template
```

---

## Unit Test Plan

```
test_soul_purification.py:

1. test_soul_no_growth:
   Run assembler twice with same inputs → SOUL.md identical both times
   (Proves no snowball)

2. test_personal_soul_migration:
   Start with .personal_soul_backup.md → assembler creates PERSONAL_SOUL.md
   Next run uses PERSONAL_SOUL.md (backup ignored)

3. test_context_block_plan_a:
   Mock DynamoDB POS#.toolAllowlist → verify Plan A block in SOUL.md

4. test_context_block_kb:
   Mock KB assignments → verify KB paths in SOUL.md

5. test_context_block_language:
   Mock CONFIG#agent-config with language → verify language block

6. test_personal_soul_extraction:
   Given merged SOUL.md → extract_personal() returns only personal content

7. test_server_no_soul_append:
   After purification, verify server.py never opens SOUL.md for write/append
```
