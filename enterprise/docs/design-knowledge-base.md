# Design: Knowledge Base Module — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-knowledge-base.md (target state), analysis-soul-current-code.md (SOUL context)

---

## File-by-File Change Design

### 1. knowledge.py — Remove KB_PREFIXES, add size check, simplify search

**Current:** 163 lines. Hardcoded `KB_PREFIXES` dict (lines 16-28). All endpoints reference this dict.

**Changes:**

```
DELETE: KB_PREFIXES dict (lines 16-28) — 13 lines removed

ADD: import db
ADD: _get_kb_meta(kb_id) helper — reads from db.get_knowledge_base()

MODIFY: get_knowledge_bases() (line 31)
  Before: for kb_id, meta in KB_PREFIXES.items()
  After:  for kb in db.get_knowledge_bases()

MODIFY: search_knowledge() (line 59)
  Before: reads every S3 file content, substring match
  After:  match query against KB name + document filename only
          No S3 GetObject calls. Only S3 ListObjects per KB.

MODIFY: get_knowledge_base() (line 100)
  Before: KB_PREFIXES.get(kb_id)
  After:  db.get_knowledge_base(kb_id)

MODIFY: get_knowledge_file() (line 116)
  Before: KB_PREFIXES.get(kb_id) → meta["prefix"]
  After:  db.get_knowledge_base(kb_id) → kb["s3Prefix"]

MODIFY: upload_knowledge_doc() (line 134)
  Before: no size check
  After:  len(body.content) > 1MB → 413 with Bedrock KB suggestion

MODIFY: delete_knowledge_file() (line 150)
  Before: KB_PREFIXES.get(kb_id)
  After:  db.get_knowledge_base(kb_id) → kb["s3Prefix"]
```

**Dependencies:** `db.get_knowledge_bases()` and `db.get_knowledge_base(kb_id)` already exist in db.py:380-391. No db.py changes needed.

### 2. workspace_assembler.py — Remove org-directory inline

**Current:** `_build_context_block()` has org-directory inline logic (~25 lines).

**Changes:**

```
DELETE: org-directory inline block in _build_context_block():
  - Remove has_org_dir tracking
  - Remove s3_client.get_object(...company-directory.md)
  - Remove the "<!-- COMPANY DIRECTORY (inline) -->" parts.append()

MODIFY: KB reference format — add reading instruction for org-directory:
  Before:
    kb_lines.append(f"- **{name}**: knowledge/{kb_id}/")
    # then separately inline org-directory ~5KB

  After:
    kb_lines.append(f"- **{name}**: knowledge/{kb_id}/")
    # org-directory gets same treatment as all other KBs
    # Add specific instruction:
    if "org-directory" in kb_id:
        kb_lines.append(
            "  When asked about colleagues, departments, or contacts, "
            "read knowledge/kb-org-directory/company-directory.md"
        )
```

**Token savings:** ~5KB per employee per session removed from system prompt.

### 3. settings.py — KB assignment triggers force refresh

**Current:** `set_position_kbs()` (line 187) calls `bump_config_version()` only.

**Changes:**

```
MODIFY: set_position_kbs() (line 187)
  After db.set_config + bump_config_version, ADD:
    import threading
    from shared import stop_employee_session
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            threading.Thread(
                target=stop_employee_session,
                args=(emp["id"],),
                daemon=True,
            ).start()

MODIFY: set_employee_kbs() (line 198)
  Same pattern: stop_employee_session(emp_id) after config update.
```

### 4. agents.py — Admin "Force Refresh" endpoint

**ADD new endpoint:**

```python
@router.post("/api/v1/admin/refresh-agent/{emp_id}")
def refresh_agent(emp_id: str, authorization: str = Header(default="")):
    """Force terminate running agent session to trigger fresh assembly.
    Used after SOUL edits, KB changes, or permission updates."""
    require_role(authorization, roles=["admin", "manager"])
    result = stop_employee_session(emp_id)
    db.create_audit_entry({
        "timestamp": now,
        "eventType": "agent_refresh",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "agent",
        "targetId": emp_id,
        "detail": f"Admin forced agent refresh for {emp_id}",
        "status": "success",
    })
    return {"refreshed": True, "emp_id": emp_id, "detail": result}
```

### 5. portal.py — Employee "Refresh My Agent" endpoint

**ADD new endpoint:**

```python
@router.post("/api/v1/portal/refresh-agent")
def portal_refresh_agent(authorization: str = Header(default="")):
    """Employee self-service: force refresh their own agent.
    Rate limited: once per 5 minutes."""
    user = require_auth(authorization)
    # Rate limit check
    cache_key = f"refresh_{user.employee_id}"
    last_refresh = _refresh_timestamps.get(cache_key, 0)
    if time.time() - last_refresh < 300:
        remaining = int(300 - (time.time() - last_refresh))
        raise HTTPException(429, f"Please wait {remaining}s before refreshing again")
    _refresh_timestamps[cache_key] = time.time()
    result = stop_employee_session(user.employee_id)
    return {"refreshed": True, "detail": result}

# Module-level rate limit cache (simple, resets on server restart)
_refresh_timestamps: dict = {}
```

### 6. Frontend — Refresh buttons

**Agent Factory (agents page):**
```
Agent detail view → new "Refresh Agent" button (icon: RefreshCw)
  onClick → POST /api/v1/admin/refresh-agent/{emp_id}
  → Toast: "Agent session terminated. Next message will trigger fresh assembly."
```

**Employee Portal:**
```
My Profile page → new "Refresh My Agent" button
  onClick → POST /api/v1/portal/refresh-agent
  → Toast: "Agent refreshed. Your next message may take a few seconds (cold start)."
  → Disable button for 5 minutes (match server rate limit)
```

---

## Unit Test Plan

```
test_knowledge_base.py:

1. test_no_hardcoded_kb_prefixes:
   Verify knowledge.py does NOT contain KB_PREFIXES dict

2. test_upload_size_limit:
   Upload 2MB content → expect 413
   Upload 500KB content → expect 200

3. test_search_filename_only:
   Mock S3 with files → search by filename → matches
   Verify no S3 GetObject calls (only ListObjects)

4. test_org_directory_not_inlined:
   Run _build_context_block() → verify SOUL.md does NOT contain
   "<!-- COMPANY DIRECTORY (inline) -->"
   Verify it contains "knowledge/kb-org-directory/" reference instead

5. test_kb_assign_triggers_refresh:
   Mock stop_employee_session → assign KB to position
   → verify stop_employee_session called for each employee in position

6. test_admin_refresh_endpoint:
   POST /admin/refresh-agent/emp-carol → verify stop_employee_session called

7. test_portal_refresh_rate_limit:
   POST /portal/refresh-agent twice within 5 min → second returns 429
```

---

## Migration Notes

- No DynamoDB schema changes — KB# records already have all needed fields
- No new DynamoDB records needed
- seed_knowledge.py unchanged (already writes correct KB# records)
- Frontend: 2 new buttons (no new pages)
- No deploy.sh changes needed

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Removing KB_PREFIXES breaks API for KBs not in DynamoDB | seed_knowledge.py ensures all 11 KBs exist. Auth middleware protects unknown KB IDs. |
| stop_employee_session for 50 employees slows KB assign API | Background threads (daemon=True). API returns immediately. |
| Portal refresh abuse (employee spams refresh) | Server-side rate limit: 1 per 5 minutes. |
| Org-directory file reference: agent may not read it | SOUL.md instruction: "When asked about colleagues, read this file." Tested with Nova Lite and Claude. |
