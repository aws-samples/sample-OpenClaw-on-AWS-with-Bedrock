# PRD: Organization Management Module

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Design principle:** Single admin with full permissions. Remove shared agent design entirely.

---

## 1. Problem Statement

Code audit of org.py (369 lines) identified 12 issues. 5 fixed by previous work (auth middleware, atomic provisioning). 7 remaining backend issues + 4 frontend issues discovered during deeper review.

---

## 2. Solutions — All Findings

### Backend Fixes

#### #3 Employee deletion creates no audit trail

**Problem:** `delete_employee()` cascades bindings + mappings but writes no AUDIT# entry.

**Solution:** Add `db.create_audit_entry()` after deletion with eventType="employee_deleted", recording who was deleted, how many bindings/mappings were cascade-deleted.

#### #5 Department deletion doesn't cascade to positions

**Problem:** Blocks if employees or sub-departments exist, but positions referencing this department become orphaned.

**Solution:** Add position check alongside existing employee/sub-department checks. If positions reference the department, return 409 with "Reassign positions to another department before deleting."

#### #6 Remove shared agent (autoBindAll) design entirely

**Problem:** `_auto_provision_employee()` queries all agents for `autoBindAll=true` and creates N:1 bindings. This design was never used in production (no seed data creates autoBindAll agents).

**Solution:** Remove:
- `org.py:335-350` — shared agent binding loop in `_auto_provision_employee()`
- `db.py:131,157-161` — `shared_bindings` parameter from `provision_employee_atomic()`
- `seed_routing_conversations.py` — `route_to_shared_agent` rules and shared agent sessions
- `demo/server.py` — `autoBindAll` agent records (helpdesk, onboarding)
- `agents.py:115` — "Simple agent creation without binding (rare: shared agent)" comment
- `types/index.ts:83` — `'N:1'` from binding mode type (keep `'1:1'` only)

#### #8 Activity endpoint O(n×m) — no caching

**Problem:** `get_employee_activities()` reads ALL SESSION# records on every request.

**Solution:** 30-second TTL module-level cache (same pattern as skill_keys cache in agents.py).

#### #11 defaultChannel defaults to "slack"

**Problem:** `pos.get("defaultChannel", "slack")` even if only Feishu is configured.

**Solution:** Change default to `"portal"`. Portal always works. Employee connects real IM via self-service pairing later.

#### #12 Force-delete doesn't delete AGENT# record

**Problem:** `delete_employee(force=True)` deletes bindings + mappings but leaves AGENT# orphaned.

**Solution:** If employee has `agentId`, call `db.delete_agent(agentId)` and S3 workspace cleanup (best-effort). Same pattern as Agent Factory's `delete_agent()`.

### Frontend Fixes

#### F1 Default Channel dropdown — remove from Position Edit/Create

**Problem:** Hardcoded 6-channel dropdown in Edit Modal (line 481) and Create Modal (line 536). Field has no runtime value — BIND#.channel is cosmetic, employee pairs via Portal.

**Solution:** Remove Default Channel field from both modals. Backend default changed to "portal".

#### F2 Active Agents stat card shows total, not active

**Problem:** `AGENTS.length` displayed under "Active Agents" label (line 273).

**Solution:** Change to `AGENTS.filter(a => a.status === 'active').length`. Or change label to "Total Agents" if active count is always unreliable.

#### F3 SOUL Configured stat checks wrong field

**Problem:** `!p.soulTemplate?.trim()` checks DynamoDB `soulTemplate` field (line 212). This field may be empty even when S3 has a full SOUL file — they're not the same thing.

**Solution:** Change to check `p.soulWordCount > 0` (add field to API response) or remove stat card and rely on the per-position SOUL status badge in the table (which already works correctly via `usePositionSoul`).

#### F4 Create Position modal Channel dropdown — same as F1

Same fix: remove.

---

## 3. Implementation Plan

### Phase 1: Backend (P0)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `org.py` | `delete_employee()` → add AUDIT# entry |
| 1.2 | `org.py` | `delete_department()` → add position reference check (409) |
| 1.3 | `org.py` | `_auto_provision_employee()` → remove shared agent binding loop |
| 1.4 | `db.py` | `provision_employee_atomic()` → remove `shared_bindings` parameter |
| 1.5 | `org.py` | `_auto_provision_employee()` → change `defaultChannel` default from "slack" to "portal" |
| 1.6 | `org.py` | `delete_employee(force=True)` → delete AGENT# + S3 workspace |
| 1.7 | `org.py` | `get_employee_activities()` → add 30s TTL cache |

### Phase 2: Frontend (P1)

| Task | File | Description |
|------|------|-------------|
| 2.1 | `Positions.tsx` | Remove Default Channel from Edit Modal + Create Modal |
| 2.2 | `Positions.tsx` | Active Agents stat → filter by status or change label |
| 2.3 | `Positions.tsx` | SOUL Configured stat → check actual SOUL content |

### Phase 3: Cleanup (P1)

| Task | File | Description |
|------|------|-------------|
| 3.1 | `seed_routing_conversations.py` | Remove `route_to_shared_agent` rules + shared agent sessions |
| 3.2 | `demo/server.py` | Remove `autoBindAll` agent records |
| 3.3 | `agents.py` | Remove "shared agent" comment at line 115 |

---

## 4. TODO

### Completed (previous work)
- [x] #1 create_position/update_position auth → fixed by auth middleware
- [x] #2 Auto-provision non-transactional → fixed by provision_employee_atomic
- [x] #4 Department isolation UI-only → by design (single admin)
- [x] #7 create_employee auth → fixed by auth middleware
- [x] #9 _get_current_user swallows errors → fixed by auth middleware
- [x] #10 Agent ID predictable → by design (single admin)

### Must-Do
- [ ] 1.1: delete_employee audit trail
- [ ] 1.2: delete_department position check
- [ ] 1.3 + 1.4: remove shared agent / autoBindAll
- [ ] 1.5: defaultChannel "slack" → "portal"
- [ ] 1.6: force-delete cascades to AGENT# + S3
- [ ] 1.7: activity cache
- [ ] 2.1: frontend — remove Default Channel from modals
- [ ] 2.2: frontend — Active Agents stat fix
- [ ] 2.3: frontend — SOUL Configured stat fix
- [ ] 3.1-3.3: cleanup shared agent from seed + demo + comments
- [ ] Update ui-guide.html Organization findings status
