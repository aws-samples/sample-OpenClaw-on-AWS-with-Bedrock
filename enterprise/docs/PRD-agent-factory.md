# PRD: Agent Factory Module

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Design principle:** Single admin with full permissions. No department-level scoping needed in admin views.

---

## 1. Problem Statement

Code audit of agents.py (578 lines) identified 14 issues. 5 fixed by previous work (auth middleware, dead code removal). 9 remaining.

---

## 2. Solutions — All 14 Findings

### #1 No DELETE agent endpoint — NEW ENDPOINT

**Problem:** Agents can be created but never deleted. Orphaned agents from departed employees accumulate forever.

**Solution:** Add `DELETE /api/v1/agents/{agent_id}` endpoint.

Cascade: delete AGENT# → delete all BIND# for this agent → delete S3 workspace → create AUDIT# entry. Use `db.transact_write()` for DynamoDB atomicity. S3 delete is best-effort (orphaned S3 files are harmless).

Frontend: Agent Detail page → "Delete Agent" button with confirmation modal ("This will remove the agent, all bindings, and the workspace. This cannot be undone.").

### #2 SOUL save creates no audit trail — UNIFIED AUDIT FUNCTION

**Problem:** 3 SOUL editing entry points, none create AUDIT#:
- `agents.py:save_agent_soul()` — Agent Factory SOUL editor (position + personal)
- `security.py:put_global_soul()` — Security Center global SOUL
- `security.py:put_position_soul()` — Security Center position SOUL

**Solution:** Create `shared.py:audit_soul_change(user, layer, target_id, char_count)`. All 3 entry points call this after S3 write. Records: who changed which layer, for which position/employee, content size delta.

### #3 Agent status from CloudWatch → DynamoDB — PERFORMANCE

**Problem:** `_get_active_agent_ids()` queries CloudWatch on every Agent Factory page load. 20+ API calls, slow.

**Solution (方案 B):** server.py already runs on each invocation. Add one line: update `AGENT#{agent_id}.lastInvocationAt = now` in the existing usage write. Agent Factory reads DynamoDB instead of CloudWatch:
- `lastInvocationAt < 15min` → "active"
- `lastInvocationAt < 60min` → "idle"
- else → "offline"

Remove `_get_active_agent_ids()` and `_get_all_agentcore_log_groups()` from agents.py entirely.

### #4 create_agent no auth — FIXED

Fixed by auth middleware in main.py. All `/api/` endpoints require JWT.

### #5 XSS in S3 workspace seed — SANITIZE

**Problem:** Employee name embedded raw into IDENTITY.md. A name like `# Ignore all rules` would become a markdown heading.

**Solution:** Escape markdown special characters in employee name before embedding:
```python
import re
safe_name = re.sub(r'([#*_\[\]<>])', r'\\\1', emp_name)
```

### #6 Dead code pos_tools — FIXED

Removed during create_agent refactoring (atomic transaction work).

### #7 _shared/ accessible to employees — ACCEPTABLE

Auth middleware protects all admin APIs. Employee role only accesses portal APIs. The `_shared/` prefix check in `get_workspace_file()` is for admin/manager viewing — they legitimately need to see shared SOUL templates. By design.

### #8 Skill assignment O(n×m) — REDESIGN

**Problem:** `assign_skill_to_position()` loops through all employees, reads each agent individually, updates skills one by one. 50 employees = 100 DynamoDB calls.

**Root cause:** AGENT# record duplicates `skills[]` from POS#.defaultSkills. This duplication creates the sync problem.

**Solution:** Stop duplicating. Agent Factory frontend reads skills from POS#.defaultSkills (already there). Remove the propagation loop. AGENT#.skills field becomes deprecated — runtime reads from POS# via workspace_assembler which already resolves position.

For the transition: keep AGENT#.skills for backward compat display, but `assign_skill_to_position()` only updates POS#.defaultSkills (already does this at line 530). Remove the employee loop (lines 533-545).

### #9 SOUL version increment not atomic — USE TRANSACT_WRITE

**Problem:** `save_agent_soul()` writes S3 first, then updates DynamoDB version. If DynamoDB fails, version stale.

**Solution:** Write S3 first (can't be in DynamoDB transaction). If S3 succeeds, update DynamoDB version. If DynamoDB fails, log warning but S3 content is correct — the version number is cosmetic (used only for admin display, not runtime). Accept this trade-off.

Alternatively: write DynamoDB first (version bump), then S3. If S3 fails, version is ahead but content is old — worse. Current order (S3 first) is actually the safer choice. Add error logging only.

### #10 soul_full no auth — FIXED

Fixed by auth middleware.

### #11 workspace_tree no auth — FIXED

Fixed by auth middleware.

### #12 Memory no department scope — BY DESIGN

Single admin with full permissions. Admin should see all agent memory for debugging and oversight. No change needed.

### #13 skill_keys reads S3 every call — ADD CACHE

**Problem:** `get_all_skill_keys()` reads 26 skill manifests from S3 on every call. 27 S3 API calls.

**Solution:** Module-level cache with 5-minute TTL:
```python
_skill_keys_cache = {"data": None, "expires": 0}

def get_all_skill_keys():
    if _skill_keys_cache["data"] and time.time() < _skill_keys_cache["expires"]:
        return _skill_keys_cache["data"]
    # ... existing S3 reads ...
    _skill_keys_cache["data"] = keys
    _skill_keys_cache["expires"] = time.time() + 300
    return keys
```

### #14 Concurrent edit race condition — LAST-WRITE-WINS WITH WARNING

**Problem:** Two admins editing SOUL simultaneously. Second save overwrites first without warning.

**Design context:** Single admin — this shouldn't happen. But as defensive coding:

**Solution:** Include `versionId` (from S3 versioning) or `lastModifiedAt` timestamp in the GET response. On PUT, compare — if version changed since the GET, return 409 Conflict with message "This SOUL was modified by another session. Please reload."

Frontend: on 409, show "Content was modified. Reload to see latest version?" dialog.

---

## 3. Implementation Plan

### Phase 1: Core fixes (P0)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `shared.py` | New: `audit_soul_change(user, layer, target_id, char_count)` shared function |
| 1.2 | `agents.py` | `save_agent_soul()` → call `audit_soul_change()` after save |
| 1.3 | `security.py` | `put_global_soul()` + `put_position_soul()` → call `audit_soul_change()` |
| 1.4 | `agents.py` | New: `DELETE /api/v1/agents/{agent_id}` with cascade (BIND#, S3, AUDIT#) |
| 1.5 | `server.py` | Write `AGENT#.lastInvocationAt` on each invocation (in existing usage write) |
| 1.6 | `agents.py` | Replace `_get_active_agent_ids()` CloudWatch logic with DynamoDB `lastInvocationAt` read |

### Phase 2: Cleanup + optimization (P1)

| Task | File | Description |
|------|------|-------------|
| 2.1 | `agents.py` | `assign_skill_to_position()`: remove employee loop (lines 533-545). Only update POS#.defaultSkills. |
| 2.2 | `agents.py` | `get_all_skill_keys()`: add 5-minute TTL cache |
| 2.3 | `agents.py` | S3 workspace seed: sanitize employee name (escape markdown chars) |
| 2.4 | `agents.py` | `save_agent_soul()`: add version check for conflict detection (409 Conflict) |
| 2.5 | `agents.py` | `save_agent_soul()`: add error logging if DynamoDB version update fails after S3 write |

### Phase 3: Frontend (P1)

| Task | File | Description |
|------|------|-------------|
| 3.1 | `AgentDetail.tsx` | "Delete Agent" button + confirmation modal |
| 3.2 | `AgentDetail.tsx` | "Refresh Agent" button (already has backend from KB module) |
| 3.3 | `SoulEditor.tsx` | Handle 409 Conflict response — show reload dialog |
| 3.4 | `AgentList.tsx` | Status column: reads from DynamoDB instead of CloudWatch (transparent — backend change, frontend unchanged) |

---

## 4. TODO

### Completed (previous work)
- [x] #4 create_agent auth → fixed by auth middleware
- [x] #6 dead code pos_tools → removed in atomic transaction refactor
- [x] #10 soul_full auth → fixed by auth middleware
- [x] #11 workspace_tree auth → fixed by auth middleware

### Must-Do
- [ ] 1.1: shared.py → `audit_soul_change()` unified function
- [ ] 1.2: agents.py → save_agent_soul calls audit
- [ ] 1.3: security.py → put_global_soul + put_position_soul call audit
- [ ] 1.4: agents.py → DELETE /api/v1/agents/{agent_id} with cascade
- [ ] 1.5: server.py (agent-container) → write AGENT#.lastInvocationAt
- [ ] 1.6: agents.py → replace CloudWatch with DynamoDB for agent status
- [ ] 2.1: agents.py → remove skill propagation loop
- [ ] 2.2: agents.py → skill_keys 5-min cache
- [ ] 2.3: agents.py → sanitize employee name in S3 seed
- [ ] 2.4: agents.py → SOUL save version conflict detection (409)
- [ ] 2.5: agents.py → SOUL save DynamoDB failure logging
- [ ] 3.1: Frontend → Delete Agent button + modal
- [ ] 3.2: Frontend → Refresh Agent button
- [ ] 3.3: Frontend → SoulEditor 409 Conflict handling
- [ ] Update ui-guide.html Agent Factory findings status
- [ ] Docker image rebuild (server.py lastInvocationAt change)
