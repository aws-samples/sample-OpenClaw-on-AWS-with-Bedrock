"""
Knowledge Base — S3-backed Markdown document management.

Endpoints: /api/v1/knowledge/*

All KB metadata comes from DynamoDB KB# records (single source of truth).
No hardcoded KB list — admin can add/remove KBs via seed or API.
"""

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
import s3ops
from shared import require_role

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

# Max document size: 1MB. Larger documents should use Bedrock KB (RAG) skill instead.
MAX_KB_DOC_SIZE = 1_000_000


def _get_kb_meta(kb_id: str) -> dict:
    """Get KB metadata from DynamoDB. Single source of truth."""
    kb = db.get_knowledge_base(kb_id)
    if not kb:
        raise HTTPException(404, f"Knowledge base '{kb_id}' not found")
    return kb


@router.get("")
def get_knowledge_bases():
    """List all knowledge bases from DynamoDB with real document counts from S3."""
    kbs = db.get_knowledge_bases()
    results = []
    for kb in kbs:
        s3_prefix = kb.get("s3Prefix", "")
        if not s3_prefix:
            results.append({**kb, "docCount": 0, "sizeBytes": 0, "status": "empty", "files": []})
            continue
        files = s3ops.list_files(s3_prefix)
        md_files = [f for f in files if f["name"].endswith(".md")]
        total_size = sum(f["size"] for f in md_files)
        last_modified = max((f["lastModified"] for f in md_files), default="") if md_files else ""
        results.append({
            "id": kb.get("id", ""),
            "name": kb.get("name", kb.get("id", "")),
            "scope": kb.get("scope", "global"),
            "scopeName": kb.get("scopeName", ""),
            "docCount": len(md_files),
            "sizeMB": round(total_size / 1024 / 1024, 2) if total_size > 0 else 0,
            "sizeBytes": total_size,
            "status": "indexed" if md_files else "empty",
            "lastUpdated": last_modified,
            "accessibleBy": kb.get("accessibleBy", ""),
            "s3Prefix": s3_prefix,
            "files": [{"name": f["name"], "size": f["size"], "key": f["key"]} for f in md_files],
        })
    return results


# IMPORTANT: /search must be defined BEFORE /{kb_id} to avoid route conflict
@router.get("/search")
def search_knowledge(query: str = "", kb_id: str = ""):
    """Search knowledge bases and documents by name (not full-text content).
    Full-text search is an Agent capability via Bedrock KB skill (RAG)."""
    if not query:
        return []
    query_lower = query.lower()
    results = []
    for kb in db.get_knowledge_bases():
        kid = kb.get("id", "")
        if kb_id and kid != kb_id:
            continue
        kb_name = kb.get("name", "")
        # Match KB name
        if query_lower in kb_name.lower():
            results.append({"type": "kb", "id": kid, "name": kb_name, "score": 0.95})
        # Match document filenames
        s3_prefix = kb.get("s3Prefix", "")
        if s3_prefix:
            for f in s3ops.list_files(s3_prefix):
                if f["name"].endswith(".md") and query_lower in f["name"].lower():
                    results.append({"type": "doc", "kb": kid, "kbName": kb_name,
                                    "name": f["name"], "score": 0.85, "key": f["key"]})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:20]


@router.get("/{kb_id}")
def get_knowledge_base(kb_id: str):
    kb = _get_kb_meta(kb_id)
    s3_prefix = kb.get("s3Prefix", "")
    files = s3ops.list_files(s3_prefix) if s3_prefix else []
    md_files = [f for f in files if f["name"].endswith(".md")]
    return {
        **kb,
        "docCount": len(md_files),
        "files": [{"name": f["name"], "size": f["size"], "key": f["key"],
                    "lastModified": f["lastModified"]} for f in md_files],
    }


@router.get("/{kb_id}/file")
def get_knowledge_file(kb_id: str, filename: str):
    """Read a specific knowledge document."""
    kb = _get_kb_meta(kb_id)
    content = s3ops.read_file(f"{kb['s3Prefix']}{filename}")
    if content is None:
        raise HTTPException(404, f"File not found: {filename}")
    return {"filename": filename, "content": content, "size": len(content)}


class KBUploadRequest(BaseModel):
    kbId: str
    filename: str
    content: str


@router.post("/upload")
def upload_knowledge_doc(body: KBUploadRequest, authorization: str = Header(default="")):
    """Upload a Markdown document to a knowledge base.
    Max 1MB per document. For larger documents, use Bedrock Knowledge Base skill (RAG)."""
    require_role(authorization, roles=["admin", "manager"])
    kb = _get_kb_meta(body.kbId)
    if len(body.content) > MAX_KB_DOC_SIZE:
        raise HTTPException(413,
            f"Document too large ({len(body.content):,} bytes). "
            f"Max {MAX_KB_DOC_SIZE:,} bytes (1MB). "
            "For larger documents, use Bedrock Knowledge Base skill with RAG.")
    if not body.filename.endswith(".md"):
        body.filename += ".md"
    key = f"{kb['s3Prefix']}{body.filename}"
    success = s3ops.write_file(key, body.content)
    if not success:
        raise HTTPException(500, "Failed to upload")
    return {"key": key, "saved": True, "size": len(body.content)}


@router.delete("/{kb_id}/file")
def delete_knowledge_file(kb_id: str, filename: str, authorization: str = Header(default="")):
    """Delete a knowledge document."""
    require_role(authorization, roles=["admin"])
    kb = _get_kb_meta(kb_id)
    key = f"{kb['s3Prefix']}{filename}"
    try:
        s3ops._client().delete_object(Bucket=s3ops.bucket(), Key=key)
        return {"deleted": True, "key": key}
    except Exception as e:
        raise HTTPException(500, str(e))
