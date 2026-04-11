# PRD: Knowledge Base Module Redesign

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Related:** PRD-soul-review-engine.md (Review Engine integration)

---

## 1. Problem Statement

Code audit of knowledge.py (163 lines), server.py KB injection (lines 619-709), and seed_knowledge.py identified 13 issues. After SOUL purification (Phase 1 complete), KB injection moved to workspace_assembler.py, but several structural problems remain.

### 1.1 Dual data source — KB_PREFIXES hardcode vs DynamoDB

`knowledge.py:16-28` has a hardcoded Python dict with 11 KB entries. `seed_knowledge.py` writes the same data to DynamoDB `KB#` records. Two sources of truth:
- Admin adds a new KB via DynamoDB → API returns 404 (not in KB_PREFIXES)
- Developer adds KB to KB_PREFIXES but forgets seed → DynamoDB empty, runtime injection fails

**Decision:** Eliminate KB_PREFIXES. All API endpoints read from `db.get_knowledge_bases()` (DynamoDB KB# records). Single source of truth.

### 1.2 No upload size limit

`knowledge.py:134` upload endpoint has no content size check. A 500MB Markdown file would be accepted, stored in S3, and at runtime, `workspace_assembler.py` would download it into the Firecracker microVM (limited memory), causing OOM.

**Decision:** Add size check at upload time (1MB max per document). For larger documents, guide admin to use Bedrock Knowledge Base skill (RAG, supports PDF/Word/HTML, no size limit). Also validate at KB assignment time — warn if a position's total KB size exceeds threshold.

### 1.3 org-directory inlined into SOUL.md (~5KB per employee)

All 12 positions have `kb-org-directory` assigned. The assembler inlines the full company directory (~5KB) into every SOUL.md. For 500 employees × 10 turns/day, that's 50,000 API calls each carrying 5KB of redundant directory data.

**Decision:** Remove org-directory inline. All KBs (including org-directory) use file path references only. SOUL.md instructs: "When asked about colleagues, read knowledge/kb-org-directory/company-directory.md using the file tool."

### 1.4 KB assignment change does not refresh running agents

Admin reassigns KBs for a position → `bump_config_version()` is called → but running agents poll config version every 5 minutes. Worse: Session Storage caches old KB files — even after config refresh, `if not os.path.isfile(local_path)` skips re-download.

**Decision:** KB assignment change triggers `stop_employee_session()` for all affected employees immediately. This terminates the VM, forcing a full cold start with fresh KB download on next message. Add "Force Refresh" button in Admin Console (Agent Factory) and Employee Portal.

### 1.5 Search scans all S3 file contents

`knowledge.py:66-73` on every search request: iterates 11 KBs × all files → S3 GetObject for each → substring match. ~110 S3 API calls per search.

**Decision:** Search by document name only (not full-text). Admin searches are for managing documents, not semantic retrieval. Full-text search is an Agent capability via Bedrock KB skill (RAG). Simplify to filename/KB name matching against DynamoDB metadata.

### 1.6 KB content may contain prompt injection

Admin uploads a KB document containing "Ignore all previous instructions." Agent reads it via `file` tool → model may follow the injected instructions.

**Decision:** Integrate with Review Engine (from PRD-soul-review-engine.md). On KB upload, write `AUDIT# eventType="kb_upload" status="pending"`. Review Engine async scans for prompt injection patterns. Critical findings → flag to admin. Same Bedrock AI review infrastructure as Personal SOUL review.

### 1.7 Read API had no access control (FIXED)

`knowledge.py:116` `get_knowledge_file()` had no `require_role()`. Any authenticated user could read any KB file.

**Status:** FIXED by auth middleware (main.py). All `/api/` endpoints now require JWT. However, no department-level KB access control exists — any logged-in user can read any KB via the admin API. For department-scoped KBs (Finance, Legal, HR), this is still a gap. Runtime KB injection respects position assignments, but the admin API does not.

---

## 2. Design

### 2.1 Eliminate KB_PREFIXES — DynamoDB single source

**Before:**
```python
# knowledge.py — hardcoded, must update code to add KB
KB_PREFIXES = {
    "kb-policies": {"prefix": "_shared/knowledge/company-policies/", ...},
    ...  # 11 entries
}
```

**After:**
```python
# knowledge.py — reads from DynamoDB, admin can add/remove via seed or API
def _get_kb_meta(kb_id: str) -> dict:
    kb = db.get_knowledge_base(kb_id)
    if not kb:
        raise HTTPException(404, f"Knowledge base '{kb_id}' not found")
    return kb
```

All endpoints (`get_knowledge_bases`, `search`, `get_knowledge_base`, `get_knowledge_file`, `upload`, `delete`) refactored to use `db.get_knowledge_bases()` and `db.get_knowledge_base(kb_id)`.

### 2.2 Upload size check + assignment validation

**Upload (knowledge.py):**
```python
MAX_KB_DOC_SIZE = 1_000_000  # 1MB

@router.post("/upload")
def upload_knowledge_doc(body, authorization):
    if len(body.content) > MAX_KB_DOC_SIZE:
        raise HTTPException(413,
            f"Document too large ({len(body.content):,} bytes, max 1MB). "
            "For larger documents, use Bedrock Knowledge Base skill (RAG).")
    ...
```

**Assignment (settings.py):**
```python
@router.put("/api/v1/settings/kb-assignments/position/{pos_id}")
def set_position_kbs(pos_id, body, authorization):
    # Validate total KB size for this position
    kb_ids = body.get("kbIds", [])
    total_size = sum(
        db.get_knowledge_base(kid).get("sizeBytes", 0)
        for kid in kb_ids if db.get_knowledge_base(kid)
    )
    if total_size > 10_000_000:  # 10MB total warning
        # Don't block — just include warning in response
        warning = f"Total KB size for this position: {total_size/1024/1024:.1f}MB. Consider using Bedrock KB skill for large document sets."
    ...
    # Force refresh affected employees
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            stop_employee_session(emp["id"])
    ...
```

### 2.3 org-directory — file reference only

**workspace_assembler.py `_build_context_block()`:**

Remove the entire org-directory inline block:
```python
# REMOVED:
# if has_org_dir:
#     org_obj = s3_client.get_object(...)
#     dir_content = org_obj["Body"].read().decode("utf-8")
#     parts.append("<!-- COMPANY DIRECTORY (inline) -->\n" + dir_content)
```

All KBs treated equally — file path reference in SOUL.md:
```
<!-- KNOWLEDGE BASES -->
You have access to the following knowledge base documents:
- **Company Policies**: knowledge/kb-policies/
- **Company Directory**: knowledge/kb-org-directory/
  When asked about colleagues, departments, or contacts, read this directory.
- **Architecture Standards**: knowledge/kb-arch/
Use the `file` tool to read these when relevant.
```

### 2.4 Force Refresh on KB assignment change

**settings.py — already has `bump_config_version()`, add `stop_employee_session()`:**
```python
@router.put("/api/v1/settings/kb-assignments/position/{pos_id}")
def set_position_kbs(pos_id, body, authorization):
    ...
    db.set_config("kb-assignments", cfg)
    bump_config_version()
    # Force refresh: terminate running sessions for affected employees
    import threading
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            threading.Thread(
                target=stop_employee_session, args=(emp["id"],), daemon=True
            ).start()
    ...
```

**Admin Console — "Force Refresh" button:**
- Agent Factory → click agent → new "Refresh" button
- Calls `POST /api/v1/admin/refresh-agent/{emp_id}` (new endpoint in agents.py)
- Wrapper for `stop_employee_session(emp_id)` + returns status

**Employee Portal — "Refresh Agent" button:**
- Portal → My Profile → "Refresh My Agent" button
- Calls `POST /api/v1/portal/refresh-agent` (new endpoint in portal.py)
- Uses `request.state.user.employee_id` → `stop_employee_session()`
- Rate limited: 1 refresh per 5 minutes per employee

### 2.5 Search — filename only

**knowledge.py search endpoint:**

```python
@router.get("/search")
def search_knowledge(query: str = ""):
    """Search knowledge bases and documents by name (not full-text content)."""
    if not query:
        return []
    query_lower = query.lower()
    results = []
    for kb in db.get_knowledge_bases():
        kb_name = kb.get("name", "").lower()
        kb_id = kb.get("id", "")
        # Match KB name
        if query_lower in kb_name:
            results.append({"type": "kb", "id": kb_id, "name": kb["name"],
                            "score": 0.95})
        # Match document filenames within this KB
        s3_prefix = kb.get("s3Prefix", "")
        if s3_prefix:
            for f in s3ops.list_files(s3_prefix):
                if query_lower in f["name"].lower():
                    results.append({"type": "doc", "kb": kb_id,
                                    "name": f["name"], "score": 0.85,
                                    "key": f["key"]})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:20]
```

Still uses S3 ListObjects per KB (11 calls), but NO GetObject (no file content read). ~10x faster.

### 2.6 KB upload — Review Engine integration

On upload, trigger async security review:

```python
@router.post("/upload")
def upload_knowledge_doc(body, authorization):
    ...  # size check, write to S3
    # Trigger async review for prompt injection
    db.create_audit_entry({
        "timestamp": now,
        "eventType": "kb_upload",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "knowledge",
        "targetId": body.kbId,
        "detail": f"Uploaded {body.filename} ({len(body.content)} bytes)",
        "status": "pending_review",
    })
    # Review Engine (Phase 3) scans for prompt injection patterns
    ...
```

---

## 3. Implementation Plan

### Phase 1: Structural fixes (P0)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `knowledge.py` | Remove KB_PREFIXES dict. All endpoints use `db.get_knowledge_bases()` / `db.get_knowledge_base()`. |
| 1.2 | `knowledge.py` | Upload: add `MAX_KB_DOC_SIZE = 1MB` check with Bedrock KB suggestion. |
| 1.3 | `knowledge.py` | Search: change to filename/KB name matching (no S3 GetObject). |
| 1.4 | `workspace_assembler.py` | Remove org-directory inline from `_build_context_block()`. All KBs use file path reference. |

### Phase 2: Force refresh (P0)

| Task | File | Description |
|------|------|-------------|
| 2.1 | `settings.py` | KB assignment change → `stop_employee_session()` for affected employees. |
| 2.2 | `agents.py` | New endpoint: `POST /api/v1/admin/refresh-agent/{emp_id}`. |
| 2.3 | `portal.py` | New endpoint: `POST /api/v1/portal/refresh-agent` (rate limited). |
| 2.4 | Frontend | Agent Factory: "Refresh" button. Portal: "Refresh My Agent" button. |

### Phase 3: Review Engine integration (P1, depends on PRD-soul-review-engine Phase 3)

| Task | File | Description |
|------|------|-------------|
| 3.1 | `knowledge.py` | Upload triggers `AUDIT# kb_upload pending_review`. |
| 3.2 | `review_engine.py` | Add KB content review template (prompt injection detection). |

---

## 4. TODO

### Completed

- [x] Phase 1.1: knowledge.py → removed KB_PREFIXES, all endpoints use DynamoDB
- [x] Phase 1.2: knowledge.py → upload size check (MAX_KB_DOC_SIZE = 1MB, 413 response)
- [x] Phase 1.3: knowledge.py → search by KB name + filename only (no S3 GetObject)
- [x] Phase 1.4: workspace_assembler.py → removed org-directory inline, file reference only
- [x] Phase 2.1: settings.py → KB assign triggers stop_employee_session for affected employees
- [x] Phase 2.2: agents.py → POST /admin/refresh-agent/{emp_id} with audit entry
- [x] Phase 2.3: portal.py → POST /portal/refresh-agent (5-min rate limit)
- [x] Unit tests → 11 tests all pass (test_knowledge_base.py)
- [x] ui-guide.html → KB audit findings updated (#1-8 marked FIXED)

### Must-Do Before Production

- [ ] **Docker image rebuild**: workspace_assembler.py org-directory inline removal is in agent container. Must rebuild + push to ECR.
- [ ] **Frontend — Search results format**: Search API response changed from `{doc, kb, score, snippet}` to `{type, id, name, score, key}`. Frontend Knowledge/index.tsx search modal needs to handle new format.
- [ ] **Frontend — Refresh buttons**: Agent Factory → "Refresh Agent" button calling POST /admin/refresh-agent/{emp_id}. Portal → "Refresh My Agent" button calling POST /portal/refresh-agent. (Backend ready, frontend not built.)
- [ ] **Frontend — Upload Modal 413 handling**: Catch 413 response, show user-friendly message: "Document too large (>1MB). For larger documents, use Bedrock Knowledge Base skill (RAG)."
- [ ] **Frontend — Assignment Modal size warning**: When assigning KBs to position, calculate total KB size. If > 10MB, show warning suggesting Bedrock KB skill.
- [ ] **Verify settings.py import**: Confirmed — `stop_employee_session` added to import.
- [ ] **seed_knowledge.py cleanup**: DynamoDB KB# records have hardcoded `docCount`, `vectorCount`, `sizeMB` that may not match S3 reality. The API now reads real counts from S3, but seed data creates misleading initial state. Consider removing fake counts from seed or aligning with actual S3 content.

### Phase 3 (Not Yet Started)

- [ ] Phase 3.1: knowledge.py → upload triggers AUDIT# kb_upload pending_review
- [ ] Phase 3.2: review_engine.py → KB content prompt injection detection (same Bedrock AI review as Personal SOUL)

### Cross-Module: Deployment

- [ ] **All 3 deployed environments** (us-west-2 demo, ap-northeast-1, us-east-1) need: Docker rebuild, admin-console service restart (`systemctl restart openclaw-admin`), and testing.
- [ ] **Auth middleware testing**: main.py auth middleware added but not tested on live. Verify: login still works, pair-pending/pair-complete still work (whitelisted), all admin/portal APIs require JWT.
- [ ] **DynamoDB transact_write testing**: db.py provision_employee_atomic() added but not tested on live. Verify: new employee creation via Admin Console still works.
