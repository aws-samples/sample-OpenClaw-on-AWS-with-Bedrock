# SOUL Module — Current Code Analysis

**Date:** 2026-04-11
**Purpose:** Map every SOUL.md read/write/append across all files before implementing purification.

---

## 1. SOUL.md Lifecycle — Every Touch Point

### Writes (who creates/modifies SOUL.md)

| # | File | Line | Operation | When | Content |
|---|------|------|-----------|------|---------|
| W1 | `entrypoint.sh` | 246-247 | `aws s3 cp ... SOUL.md` | Cold start, bg worker | Default template fallback (only if SOUL.md missing) |
| W2 | `workspace_assembler.py` | 196 | `open(soul_path, "w")` | Assembly (entrypoint.sh bg + server.py first invocation) | **Merged 3-layer** (Global + Position + Personal) |
| W3 | `server.py` | 436-437 | `open(soul_path, "w")` | First invocation | **Plan A prepend**: reads existing, writes Plan A + existing |
| W4 | `server.py` | 460 | `open(soul_path, "a")` | First invocation (twin mode) | **Digital Twin context** appended |
| W5 | `server.py` | 615-616 | `open(soul_path, "a")` | First invocation | **Language preference** appended |
| W6 | `server.py` | 707 | `open(soul_path, "a")` | First invocation | **KB paths + org-directory inline** appended |
| W7 | `agents.py` | 286 (via s3ops) | S3 write | Admin SOUL editor | Position/personal layer S3 write |

### Reads (who reads SOUL.md)

| # | File | Line | Operation | When |
|---|------|------|-----------|------|
| R1 | `workspace_assembler.py` | 174-188 | `open(personal_soul_path)` | Assembly | Reads as personal layer input (or backup) |
| R2 | `server.py` | 433-434 | `open(soul_path, "r")` | Plan A injection | Reads current content to prepend |
| R3 | `server.py` | 441-442 | `open(soul_path, "r")` | Twin mode check | Reads to check if "DIGITAL TWIN MODE" already present |
| R4 | OpenClaw CLI | — | Reads SOUL.md as system prompt | Every conversation turn | **This is the consumer** |

### Sync (S3 ↔ workspace)

| # | File | Line | Direction | Excluded? |
|---|------|------|-----------|-----------|
| S1 | `entrypoint.sh` | 110-111 | S3 → workspace | No (downloads all) |
| S2 | `entrypoint.sh` | 231 | S3 → workspace | No (downloads all) |
| S3 | `server.py` | 389-392 | S3 → workspace | No (cp --recursive) |
| S4 | `entrypoint.sh` | 302-308 | workspace → S3 (watchdog) | **YES: SOUL.md excluded** |
| S5 | `entrypoint.sh` | 379-385 | workspace → S3 (SIGTERM) | **YES: SOUL.md excluded** |

---

## 2. Personal Layer — Current Anti-Snowball Flow

```
First assembly (workspace_assembler.py:174-189):
  if .personal_soul_backup.md exists:
      personal = read(backup)           ← use original, not merged
  elif SOUL.md exists:
      personal = read(SOUL.md)          ← first time: SOUL.md IS the personal layer
      write(backup, personal)           ← save for next time

  merged = merge(global, position, personal)
  write(SOUL.md, merged)                ← overwrites personal with merged
```

**Problem:** After W2, SOUL.md is the merged output. If server.py (W3-W6) appends to it, and then assembly runs again (config version change), the assembler reads backup (correct) but server.py appends AGAIN → KB block duplicated.

**Dedup guard (partial):** W3 checks `"Allowed tools for this session" not in existing` before Plan A injection. But W5 (language) and W6 (KB) have NO dedup check.

---

## 3. Session Storage Optimization

```
server.py:296-303 — _session_storage_has_workspace():
  If workspace already has files AND config_version unchanged:
      Skip S3 download + assembly entirely
      Return immediately (resume from in-VM state)

  This means: W3-W6 appends only run on FIRST invocation.
  But if config_version changes → _assembled_tenants.clear() →
  next request re-runs assembly + W3-W6 → KB/language appended AGAIN.
```

---

## 4. entrypoint.sh — Assembly Runs in TWO Places

```
Place 1: Background worker (line 255-263)
  Runs after S3 sync, in subshell
  tenant_id may still be "unknown" → skipped
  If known: runs assembler

Place 2: Always-on pre-Gateway (line 108-123)
  Runs SYNCHRONOUSLY before Gateway starts
  Only for EFS mode or SHARED_AGENT_ID

Both call workspace_assembler.py with same args.
Neither calls server.py's Plan A/KB injection.
Those injections ONLY happen in server.py W3-W6.
```

---

## 5. Watchdog Exclude List — What Gets Synced Back

```
entrypoint.sh:302-308 (watchdog loop):
  --exclude "SOUL.md"
  --exclude "AGENTS.md"
  --exclude "TOOLS.md"
  --exclude "IDENTITY.md"
  --exclude ".personal_soul_backup.md"
  --exclude "knowledge/*"
  --exclude "node_modules/*"
  --exclude "skills/_shared/*"
  --exclude "skills/*"

entrypoint.sh:379-385 (SIGTERM final sync):
  Same exclude list.

What IS synced back:
  USER.md, MEMORY.md, HEARTBEAT.md, memory/*.md
  SESSION_CONTEXT.md (not excluded — BUG? regenerated each time)
  CHANNELS.md (not excluded — BUG? regenerated each time)
  Any other files agent creates
```

---

## 6. DynamoDB Records Read During Assembly/Injection

| Record | Read By | Purpose |
|--------|---------|---------|
| `EMP#{base_id}` | workspace_assembler.py:88, server.py:541 | positionId, name, employeeNo |
| `MAPPING#{ch}__{uid}` | workspace_assembler.py:72, server.py:332 | IM user → employee resolution |
| `CONFIG#model` | server.py:547 | Model override (employee > position > default) |
| `CONFIG#agent-config` | server.py:576 | Language, compaction, maxTokens |
| `CONFIG#kb-assignments` | server.py:621 | Which KBs assigned to which positions/employees |
| `KB#{kb_id}` | server.py:637 | KB metadata (s3Prefix, files) |
| `POS#{pos_id}` | permissions.py (via server.py:426) | toolAllowlist for Plan A |
| `CONFIG#global-version` | server.py (config version poll) | Cache invalidation trigger |

**Key insight:** workspace_assembler.py reads 2 DynamoDB records. server.py reads 6 more. After purification, assembler needs to read all 8.

---

## 7. Files to Change — Impact Map

### workspace_assembler.py (MAJOR changes)
- **Remove:** .personal_soul_backup.md logic (lines 174-189)
- **Add:** Read PERSONAL_SOUL.md instead of SOUL.md for personal layer
- **Add:** Read DynamoDB: POS#.toolAllowlist, CONFIG#agent-config (language), CONFIG#kb-assignments, KB# items
- **Add:** Generate CONTEXT.md (Plan A + KB refs + language + org-directory)
- **Add:** SOUL.md footer: "Your runtime context is in CONTEXT.md."
- **Keep:** merge_soul() logic unchanged
- **Keep:** IDENTITY.md, SESSION_CONTEXT.md, CHANNELS.md generation

### server.py (REMOVE injections)
- **Remove:** W3 (Plan A prepend, lines 420-466) — moved to assembler CONTEXT.md
- **Remove:** W5 (Language append, lines 609-617) — moved to assembler CONTEXT.md
- **Remove:** W6 (KB paths + org-directory append, lines 619-708) — moved to assembler CONTEXT.md
- **Keep:** KB file download to knowledge/ (lines 657-669) — OR move to assembler
- **Keep:** Model override (lines 546-573) — stays in server.py (writes openclaw.json, not SOUL.md)
- **Keep:** Memory synthesis (lines 492-528)
- **Keep:** Skill env vars (lines 468-480)
- **Keep:** S3 workspace cp (lines 381-396)
- **Keep:** Calling workspace_assembler.py (lines 398-418)

### entrypoint.sh (MINOR changes)
- **Change:** Watchdog exclude list: add CONTEXT.md, remove .personal_soul_backup.md
- **Change:** SIGTERM exclude list: same
- **Add:** In SIGTERM handler (cleanup function): call personal_soul_extractor.py before final sync

### agents.py (MINOR changes)
- **Change:** SOUL editor personal layer → write PERSONAL_SOUL.md (not SOUL.md)
- **Change:** Agent creation S3 seed → create empty PERSONAL_SOUL.md

### portal.py (MINOR changes)
- **No change needed:** Portal profile writes USER.md (not SOUL.md)
- **Future:** Add "My Agent Identity" page to edit PERSONAL_SOUL.md

### New files
- `personal_soul_extractor.py` — SIGTERM extraction (Phase 2)
- `tool_usage_collector.py` — SIGTERM tool stats (Phase 3)
- `review_engine.py` — Admin Console async review (Phase 3)

---

## 8. Risk Analysis

| Risk | Severity | Mitigation |
|------|----------|------------|
| assembler fails to read DynamoDB → no CONTEXT.md | High | Fallback: if DynamoDB read fails, generate minimal CONTEXT.md with just "Read knowledge/ directory" |
| Cold start latency increase (2-3 more DynamoDB reads) | Medium | Use batch_get_item() for POS# + CONFIG# + KB# in single round trip |
| OpenClaw does not read CONTEXT.md automatically | High | **MUST TEST**: add "Read CONTEXT.md" instruction in SOUL.md footer. If insufficient, concatenate CONTEXT.md into SOUL.md at assembly time (less pure but functional) |
| PERSONAL_SOUL.md does not exist for existing employees | Medium | Migration: if PERSONAL_SOUL.md missing, extract from current SOUL.md or create empty |
| KB file download moved to assembler → entrypoint.sh timeout | Medium | Keep KB download in server.py (lazy, on first invocation) rather than assembler (eager) |

---

## 9. Recommended Change Order

```
1. Create PERSONAL_SOUL.md for existing employees (migration)
   → Add to seed scripts + deploy.sh

2. workspace_assembler.py: read PERSONAL_SOUL.md, generate CONTEXT.md
   → New function: build_context(pos_id, base_id, bucket, region)
   → Reads DynamoDB: POS#, CONFIG#agent-config, CONFIG#kb-assignments, KB#
   → Writes CONTEXT.md to workspace

3. server.py: remove W3/W5/W6 (Plan A, language, KB SOUL append)
   → Keep KB file download (lazy, on first invocation)
   → Or move KB download to assembler if latency acceptable

4. entrypoint.sh: update exclude lists
   → watchdog: +CONTEXT.md, -(.personal_soul_backup.md already excluded)
   → SIGTERM: +CONTEXT.md, +personal_soul_extractor.py call

5. agents.py: SOUL editor → write PERSONAL_SOUL.md for personal layer

6. Unit tests: verify SOUL.md stays constant across multiple assemblies
   → No growth (snowball test)
   → CONTEXT.md regenerated correctly
   → PERSONAL_SOUL.md preserved across sessions
```
