# Session Storage Integration Design

## Status: ABANDONED (2026-04-14)

> **Decision: Session Storage is no longer used.** The optimization (skip workspace
> assembly if Session Storage has files from a previous session) caused critical bugs:
> - Employee identity lost (cached generic SOUL from tenant=unknown boot)
> - Stale KB files (cached old versions, os.path.isfile skipped re-download)
> - 1GB space limit risk (output files + skills accumulated over sessions)
> - 3-way state complexity (microVM local + Session Storage + S3 divergence)
>
> Architecture now uses 2-way state: microVM local workspace + S3 (source of truth).
> Every cold start runs full workspace assembly from S3 (~6s). Acceptable trade-off.
> Fargate mode uses EFS (persistent, unlimited) and has 0s cold start.
>
> The design below is kept for historical reference only.

---

## Original Design (no longer implemented)

## Problem Statement

AgentCore microVM is stateless. We built ~400 lines of S3 sync + memory hack code to work around this.
Session Storage can persist the filesystem across stop/resume cycles, but it's a black box (no external API).
S3 remains the communication channel between Admin Console and agent runtime.

We need to design: how do Session Storage and S3 coexist?

---

## Key Insight: StopRuntimeSession as the Control Plane

`StopRuntimeSession` API solves the config propagation and force-refresh problems:

```
Admin/Employee changes a file → S3 updated
  → Admin Console calls StopRuntimeSession(sessionId)
  → microVM graceful shutdown → Session Storage flushed to durable storage
  → next invoke_agent_runtime with same sessionId
  → new microVM starts → Session Storage restored
  → _assembled_tenants is empty (new process) → _ensure_workspace_assembled() runs
  → pulls latest from S3 → overwrites stale files in Session Storage
```

This means:
- **No polling needed** for immediate config propagation — Stop + next invoke = guaranteed fresh
- **`_config_version` still handles background refresh** for non-urgent changes (5 min TTL)
- **Admin gets a "Force Refresh" button** = `StopRuntimeSession`
- **Employee edits (USER.md) propagate immediately** = save to S3 + `StopRuntimeSession`

---

## Architecture: Three Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                     Admin Console                               │
│  SOUL Editor, KB Manager, Workspace Viewer, Monitor, Portal     │
└────┬──────────────────┬──────────────────┬──────────────────────┘
     │ write (config)   │ read (visibility) │ control
     ▼                  ▲                   ▼
┌────────────────────────────────┐    ┌──────────────────────────┐
│       S3 (source of truth)     │    │  AgentCore Control Plane │
│  _shared/soul/    (admin SOUL) │    │                          │
│  {emp}/workspace/ (personal)   │    │  StopRuntimeSession()    │
│  _shared/skills/  (skills)     │    │  invoke_agent_runtime()  │
└────┬──────────────────┬────────┘    └──────────────────────────┘
     │ pull (refresh)   │ push (writeback)
     ▼                  ▲
┌─────────────────────────────────────────────────────────────────┐
│              Session Storage (/mnt/workspace)                   │
│  Hot workspace — persists across microVM stop/resume            │
│  SOUL.md, memory/*.md, MEMORY.md, knowledge/, etc.             │
│  Only accessible from inside the microVM                        │
│  Limit: 1 GB per session (our workspaces are <1 MB)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Session Types

Prerequisite: remove date from `derive_tenant_id` hash → stable session IDs.

| Session type | runtimeSessionId | Session Storage | Writeback to S3 | CONV# to DDB | Usage/Audit |
|-------------|-----------------|----------------|----------------|-------------|-------------|
| **Main** (Portal + IM) | `emp__emp-carol__<hash>` | Primary workspace | Yes | Yes | Yes |
| **Twin** (digital twin) | `twin__emp-carol__<hash>` | Isolated workspace | DDB only (OQ5) | Yes | Yes |
| **Playground** (admin test) | `pgnd__emp-carol__<hash>` | Isolated workspace | No | No | Yes |

All 3 session types load the same employee's SOUL/memory from S3 at first assembly,
but Twin and Playground get isolated Session Storage so their conversations don't pollute the main session.

Twin and Playground prefix fix already implemented (tenant_router.py + main.py).

---

## Data Flow Matrix

### Admin Console → S3 → Agent (config injection)

| File | Written by | S3 path | Propagation mechanism |
|------|-----------|---------|----------------------|
| Global SOUL.md | SOUL Editor (admin) | `_shared/soul/global/SOUL.md` | `_bump_config_version()` → 5 min poll → re-assembly |
| Position SOUL.md | SOUL Editor (admin/mgr) | `_shared/soul/positions/{pos}/SOUL.md` | `_bump_config_version()` → 5 min poll → re-assembly |
| Personal SOUL.md | SOUL Editor (employee) | `{emp}/workspace/SOUL.md` | `_bump_config_version()` → 5 min poll → re-assembly |
| USER.md | Portal Profile editor | `{emp}/workspace/USER.md` | **StopRuntimeSession** → immediate on next invoke |
| KB documents | KB Manager | `_shared/knowledge/{kb}/` | `_bump_config_version()` → 5 min poll → re-assembly |
| Skills | Skill Platform | `_shared/skills/{name}/` | skill_loader at startup / `_bump_config_version()` |
| Permission profile | Security Center | SSM / DynamoDB | **StopRuntimeSession** → immediate (security-critical) |

### Agent → S3 (visibility writeback)

| File | Written by | Purpose | Writeback timing |
|------|-----------|---------|-----------------|
| `memory/*.md` | server.py per-turn checkpoint | Daily conversation log | Fire-and-forget after each invocation |
| `MEMORY.md` | OpenClaw Gateway compaction | Compressed cross-session memory | Fire-and-forget after each invocation |
| `HEARTBEAT.md` | OpenClaw (user sets reminder) | Scheduled task triggers | Fire-and-forget after each invocation |

### Generated inside microVM (no writeback)

| File | Generated by | Regenerated on |
|------|-------------|----------------|
| `SOUL.md` (merged) | workspace_assembler | Every re-assembly (config change or StopRuntimeSession) |
| `AGENTS.md` (merged) | workspace_assembler | Every re-assembly |
| `TOOLS.md` | workspace_assembler | Every re-assembly |
| `IDENTITY.md` | workspace_assembler | Every re-assembly |
| `SESSION_CONTEXT.md` | workspace_assembler | Every re-assembly |
| `CHANNELS.md` | workspace_assembler | Every re-assembly |
| `knowledge/{kb}/` | server.py KB injection | Every re-assembly |

---

## Design Decisions (Resolved)

### D1: When does the microVM write back to S3?

**Decision: Keep current fire-and-forget after each invocation.**

- S3 API cost is negligible ($0.005/1000 PUTs)
- Admin visibility is more valuable than saving API calls
- Remove the 60s watchdog in entrypoint.sh (Session Storage handles persistence, fire-and-forget handles visibility)
- Keep SIGTERM cleanup for EFS cross-mode snapshot (always-on containers)

### D2: How does admin-edited config reach Session Storage?

**Decision: Two mechanisms, choose based on urgency.**

| Urgency | Mechanism | Latency | Who triggers |
|---------|-----------|---------|-------------|
| Background (default) | `_config_version` poll (5 min) | 0–5 min | Automatic |
| Immediate | `StopRuntimeSession` | Next invocation | Admin Console / Portal code |

Immediate triggers (call `StopRuntimeSession` after S3 write):
- Employee edits USER.md in Portal
- Admin changes permission profile (security-critical)
- Admin clicks "Force Refresh" button
- Admin changes agent model override for specific employee

Background triggers (rely on `_config_version`):
- Admin edits Global/Position SOUL (affects all agents, 5 min acceptable)
- Admin changes KB assignments
- Admin changes global model config

### D3: How does admin verify agent has correct files?

**Decision: SOUL hash + config_version in DynamoDB SESSION#, plus CloudWatch logs.**

After workspace_assembler runs, write to DynamoDB:
```python
SESSION#{tenant_id}.soulHash = sha256(merged_soul_md)[:16]
SESSION#{tenant_id}.configVersion = current_config_version
SESSION#{tenant_id}.assembledAt = timestamp
```

Admin Console Session Detail page displays:
- SOUL hash (compare against expected)
- Config version (compare against current global version)
- Last assembled timestamp
- "Force Refresh" button

CloudWatch logs already capture assembly events with character counts.

### D4: What if Session Storage and S3 diverge?

**Decision: S3 always wins on re-assembly. Session Storage wins between assemblies.**

| Scenario | What happens |
|----------|-------------|
| Admin changed SOUL | `_config_version` or `StopRuntimeSession` triggers re-assembly → S3 wins |
| Employee edited USER.md | `StopRuntimeSession` triggers re-assembly → S3 wins |
| Agent wrote new memory | Session Storage has latest; S3 gets fire-and-forget copy |
| Runtime update wiped Session Storage | Bootstrap from S3 (existing behavior) |
| 14-day expiry wiped Session Storage | Bootstrap from S3 (existing behavior) |
| Admin clicks "Force Refresh" | `StopRuntimeSession` → re-assembly → S3 wins |

**Invariant:** After any re-assembly, Session Storage matches S3 for admin-managed files.
Between assemblies, Session Storage may have newer agent-written files (memory) — that's correct.

### D5: Playground and Twin writeback policy

| Session type | Memory → S3 | CONV# → DDB | Usage/Audit → DDB | Reason |
|-------------|-------------|-------------|-------------------|--------|
| `emp__` (main) | Yes | Yes | Yes | Primary session |
| `twin__` | No | Yes | Yes | Conversations visible to employee via DDB; memory files isolated |
| `pgnd__` (playground) | No | No | Yes | Read-only test; only audit trail |

Twin rationale: twin conversations are recorded in DynamoDB CONV# (employee can review in Portal).
But twin memory files (memory/*.md, MEMORY.md) are NOT written back to the employee's S3 path
to avoid polluting the employee's personal memory with external visitors' questions.

Playground rationale: admin testing should leave zero trace in the employee's data.
Only usage/audit records are kept for admin accountability.

---

## Open Questions (Remaining)

### OQ3: What monitoring data does Admin Console need from inside Session Storage?

Currently visible to admin via S3:
- memory/*.md (daily conversation logs)
- MEMORY.md (compressed memory)
- USER.md (personal preferences)
- HEARTBEAT.md (scheduled reminders)

Currently visible via DynamoDB:
- CONV# (individual conversation turns — full message text)
- SESSION# (session metadata: turns, tokens, lastActive)
- USAGE# (per-agent daily usage)
- AUDIT# (all events)

**Question:** Is DynamoDB data sufficient for admin monitoring, or does admin need to see the actual files?

Current assessment:
- DynamoDB CONV# already captures full conversation text — more reliable than memory/*.md
- SOUL hash in SESSION# (D3) lets admin verify correct config
- memory/*.md in S3 is useful for Workspace Viewer file browsing
- Fire-and-forget writeback provides "eventually consistent" S3 view (seconds delay)

**Likely answer:** DynamoDB for real-time monitoring, S3 for file browsing (acceptable delay).
No need for synchronous writeback or introspection endpoint.

### OQ5: Twin memory isolation (minor)

Decided: twin conversations go to DDB CONV# only, not to S3 memory files.
But should the twin session's local memory/*.md files (inside Session Storage) be written at all?

Options:
- A. Don't write local memory files for twin sessions (skip `_append_conversation_turn` local write)
- B. Write locally (Session Storage) but don't sync to S3 — twin gets cross-session context within its own Session Storage

**Leaning:** B — twin benefits from remembering previous twin conversations across sessions.

---

## StopRuntimeSession Integration Points

### New API: Tenant Router `/stop-session`

```
POST /stop-session
Body: { "emp_id": "emp-carol" }

→ derive session IDs for all 3 session types (emp__, twin__, pgnd__)
→ call StopRuntimeSession for each active session
→ return { "stopped": ["emp__emp-carol__...", ...] }
```

### New API: Admin Console `/api/v1/agents/{emp_id}/refresh`

```
POST /api/v1/agents/{emp_id}/refresh
Auth: admin or manager (department-scoped)

→ call Tenant Router /stop-session
→ audit log: "Agent refresh triggered by {admin_name} for {emp_name}"
→ return { "status": "Agent will reload on next message" }
```

### Auto-trigger points in Admin Console backend (main.py)

| Endpoint | Current behavior | Add StopRuntimeSession? |
|----------|-----------------|------------------------|
| `PUT /api/v1/workspace/file` (USER.md) | S3 write | Yes — `_stop_employee_session(emp_id)` |
| `PUT /api/v1/agents/{id}/soul` (personal layer) | S3 write + `_bump_config_version()` | Optional — config_version handles it |
| `PUT /api/v1/security/global-soul` | S3 write + `_bump_config_version()` | No — affects all agents, config_version is appropriate |
| `PUT /api/v1/security/positions/{pos}/soul` | S3 write + `_bump_config_version()` | No — affects multiple agents |
| `POST /api/v1/settings/security/permissions` | DDB write | Yes — security-critical, immediate |
| Agent model override (per-employee) | DDB write | Yes — employee should get new model immediately |
| KB assignment change | DDB write + `_bump_config_version()` | No — config_version handles it |

---

## Implementation Plan

### Phase 0: Stable Session ID
- Remove date from `derive_tenant_id` hash (1 line in tenant_router.py)
- Update test_routing.py to remove date-dependent assertions
- **Prerequisite for all subsequent phases**

### Phase 1: Enable Session Storage
- CFn template: add `filesystemConfigurations` with `sessionStorage.mountPath: /mnt/workspace`
- CFn template: update S3 Gateway Endpoint policy to include `acr-storage-*` bucket access
- entrypoint.sh: detect Session Storage mount (`/mnt/workspace` non-empty at invocation time)
- server.py `_ensure_workspace_assembled()`: if Session Storage has `SOUL.md` + `_config_version` unchanged → skip S3 cp and assembly

### Phase 2: StopRuntimeSession integration
- tenant_router.py: add `POST /stop-session` endpoint
- main.py: add `POST /api/v1/agents/{emp_id}/refresh` (admin)
- main.py: auto-trigger `StopRuntimeSession` after USER.md save, permission change, model override
- server.py: after assembly, write SOUL hash + config_version to DynamoDB SESSION#

### Phase 3: Admin Console UI
- Session Detail page: show SOUL hash, config version, last assembled timestamp
- Agent Detail page: "Force Refresh" button → calls `/api/v1/agents/{emp_id}/refresh`
- Workspace Viewer: unchanged (reads S3, fire-and-forget writeback provides eventual consistency)

### Phase 4: Simplify codebase
- Remove MEMORY.md synthesis hack (server.py:472-508) — Gateway compaction works with persistent sessions
- Remove daily memory file local write from `_append_conversation_turn` (server.py:143-163) — DDB CONV# is the primary record, Gateway handles MEMORY.md
- Remove entrypoint.sh 60s watchdog loop — fire-and-forget after invocation + SIGTERM cleanup is sufficient
- Estimated: ~130 lines removed

---

## Session Storage Limits (confirmed)

| Limit | Value | Impact |
|-------|-------|--------|
| Max storage per session | 1 GB | Our workspaces are <1 MB — 1000x headroom |
| Max files | ~100,000-200,000 | Not a concern |
| Max directory depth | 200 | Not a concern |
| Inactivity expiry | 14 days | S3 bootstrap fallback handles this |
| Runtime update | Wipes all sessions | S3 bootstrap fallback handles this |
| Init phase availability | Not mounted until invocation | Current design already assembles at invocation time |
| External API | None | S3 writeback provides admin visibility |
| SQLite / file locking | Advisory locks don't persist | Needs testing with OpenClaw's SQLite session store |
