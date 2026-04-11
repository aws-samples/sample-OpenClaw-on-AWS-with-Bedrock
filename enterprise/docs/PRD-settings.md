# PRD: Settings & Admin Assistant Module

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Priority:** P0 — Platform management hub + Admin AI assistant rewrite

---

## 1. Problem Statement

### Admin Assistant is a black box
Current implementation runs OpenClaw CLI via subprocess on EC2 (PATH B). Admin cannot control the assistant's behavior — it reads a generic SOUL.md from EC2 filesystem. The `systemPromptExtra` config in DynamoDB is never injected. Conversation history lives in browser localStorage (lost on device change).

### Settings lacks platform operations
Admin has no way to:
- Access Gateway UI (needs SSM port forward, command not provided)
- View platform logs (journalctl for 4 services)
- See pre-built SSM commands for EC2 access
- Audit config changes (model/agent config modifications have no AUDIT# trail)

### Two separate things on one EC2
```
OpenClaw CLI + Gateway (port 18789):
  → Platform maintenance tool for IM bot configuration
  → Admin accesses via SSM port forward
  → Independent from Admin Assistant

Admin Assistant (floating bot):
  → AI operations helper for admin
  → Should use direct Bedrock Converse API
  → System prompt managed in DynamoDB
  → NOT dependent on OpenClaw CLI
```

---

## 2. Solutions

### 2.1 Admin Assistant: OpenClaw CLI → Direct Bedrock Converse (P0)

**Replace `_admin_assistant_direct()` in playground.py:**

```python
def _admin_assistant_direct(message: str) -> dict:
    """Admin Assistant — direct Bedrock Converse API.
    System prompt from DynamoDB CONFIG#admin-assistant.
    Conversation history in DynamoDB CONV#admin-assistant."""

    # Load config
    cfg = db.get_config("admin-assistant") or {}
    model_id = cfg.get("model", os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0"))
    system_prompt = cfg.get("systemPrompt",
        "You are the IT Admin Assistant for OpenClaw Enterprise platform. "
        "You help the admin manage agents, monitor system health, "
        "troubleshoot issues, and configure the platform. "
        "Be concise, technical, and actionable.")
    extra = cfg.get("systemPromptExtra", "")
    if extra:
        system_prompt += "\n\n" + extra

    # Load conversation history from DynamoDB
    history = _load_admin_conversation()

    # Add user message
    messages = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in history[-10:]]
    messages.append({"role": "user", "content": [{"text": message}]})

    # Call Bedrock Converse
    bedrock = boto3.client("bedrock-runtime", region_name=GATEWAY_REGION)
    response = bedrock.converse(
        modelId=model_id,
        system=[{"text": system_prompt}],
        messages=messages,
        inferenceConfig={"maxTokens": 4096},
    )
    reply = response["output"]["message"]["content"][0]["text"]

    # Save conversation to DynamoDB
    _save_admin_conversation(message, reply)

    return {"response": reply, "tenant_id": "admin", "source": "bedrock-direct",
            "model": model_id}
```

**Conversation history in DynamoDB (not localStorage):**
```python
def _load_admin_conversation() -> list:
    """Load admin assistant conversation from DynamoDB."""
    records = db.get_session_conversation("admin-assistant")
    return [{"role": r.get("role",""), "content": r.get("content","")} for r in records[-20:]]

def _save_admin_conversation(user_msg: str, assistant_msg: str):
    """Save admin conversation turn to DynamoDB."""
    import time as _t
    ts = datetime.now(timezone.utc).isoformat()
    ddb = boto3.resource("dynamodb", region_name=db.AWS_REGION)
    table = ddb.Table(db.TABLE_NAME)
    base_ts = int(_t.time() * 1000)
    table.put_item(Item={
        "PK": "ORG#acme", "SK": f"CONV#admin-assistant#user#{base_ts}",
        "sessionId": "admin-assistant", "role": "user", "content": user_msg, "ts": ts,
    })
    table.put_item(Item={
        "PK": "ORG#acme", "SK": f"CONV#admin-assistant#assistant#{base_ts+1}",
        "sessionId": "admin-assistant", "role": "assistant", "content": assistant_msg, "ts": ts,
    })
```

**Frontend AdminAssistant.tsx changes:**
- Remove localStorage for conversation history
- Load from API on mount: `GET /api/v1/settings/admin-assistant/history`
- Send saves to DynamoDB via existing playground/send endpoint
- Add [Clear History] button: `DELETE /api/v1/settings/admin-assistant/history`

### 2.2 Admin Assistant Config — Enhanced (P0)

**Current config (DynamoDB CONFIG#admin-assistant):**
```json
{
    "model": "global.amazon.nova-2-lite-v1:0",
    "allowedCommands": ["list_employees", ...],
    "systemPromptExtra": ""
}
```

**New config:**
```json
{
    "model": "global.amazon.nova-2-lite-v1:0",
    "systemPrompt": "You are the IT Admin Assistant for OpenClaw Enterprise...",
    "systemPromptExtra": "",
    "maxHistoryTurns": 20,
    "maxTokens": 4096
}
```

`allowedCommands` removed — the assistant is now pure Bedrock Converse, no CLI commands. It answers questions based on its system prompt and conversation context. If admin wants the assistant to have data access, they can describe what data the assistant should reference in the system prompt.

### 2.3 Platform Access (P0)

**New endpoint: pre-built SSM commands**
```python
@router.get("/api/v1/settings/platform-access")
def get_platform_access():
    """Pre-built commands for EC2 access and Gateway UI."""
    instance_id = GATEWAY_INSTANCE_ID
    region = GATEWAY_REGION
    return {
        "instanceId": instance_id,
        "region": region,
        "ssmSession": f"aws ssm start-session --target {instance_id} --region {region}",
        "gatewayPortForward": (
            f"aws ssm start-session --target {instance_id} --region {region} "
            f"--document-name AWS-StartPortForwardingSession "
            f"--parameters portNumber=18789,localPortNumber=18789"
        ),
        "gatewayUrl": "http://localhost:18789",
        "adminConsolePortForward": (
            f"aws ssm start-session --target {instance_id} --region {region} "
            f"--document-name AWS-StartPortForwardingSession "
            f"--parameters portNumber=8099,localPortNumber=8099"
        ),
    }
```

### 2.4 Platform Logs (P1)

```python
@router.get("/api/v1/settings/platform-logs")
def get_platform_logs(service: str = "openclaw-admin", lines: int = 50, authorization: str = Header(default="")):
    """Read recent journalctl logs for a platform service."""
    require_role(authorization, roles=["admin"])
    allowed_services = ["openclaw-admin", "tenant-router", "bedrock-proxy-h2", "openclaw-gateway"]
    if service not in allowed_services:
        raise HTTPException(400, f"Service must be one of: {allowed_services}")
    try:
        import subprocess
        output = subprocess.check_output(
            ["journalctl", "-u", service, "--no-pager", "-n", str(min(lines, 200))],
            text=True, timeout=10
        )
        return {"service": service, "lines": output.strip().split("\n"), "count": len(output.strip().split("\n"))}
    except Exception as e:
        return {"service": service, "lines": [], "error": str(e)}
```

### 2.5 Config Change Audit + Force Refresh (P0)

**Model config changes:**
```python
@router.put("/api/v1/settings/model/default")
def set_default_model(body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    config = _get_model_config()
    config["default"] = body
    db.set_config("model", config)
    bump_config_version()
    # Audit
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "model", "targetId": "default",
        "detail": f"Default model changed to {body.get('modelId','')}",
        "status": "success",
    })
    return config["default"]
```

Same pattern for: `set_fallback_model`, `set_position_model`, `set_employee_model`, `set_position_agent_config`, `set_employee_agent_config`.

**Force refresh after model change:**
```python
# After model config change, refresh agents in affected position
if pos_id:
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            threading.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
```

### 2.6 Admin Assistant History API

```python
@router.get("/api/v1/settings/admin-assistant/history")
def get_admin_history(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    records = db.get_session_conversation("admin-assistant")
    return [{"role": r.get("role",""), "content": r.get("content",""), "ts": r.get("ts","")}
            for r in records[-50:]]

@router.delete("/api/v1/settings/admin-assistant/history")
def clear_admin_history(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    # Delete all CONV#admin-assistant records
    try:
        ddb = boto3.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        from boto3.dynamodb.conditions import Key
        resp = table.query(
            KeyConditionExpression=Key("PK").eq("ORG#acme") & Key("SK").begins_with("CONV#admin-assistant"),
        )
        with table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        return {"cleared": True, "count": len(resp.get("Items", []))}
    except Exception as e:
        raise HTTPException(500, str(e))
```

---

## 3. Implementation Plan

### Phase 1: Admin Assistant rewrite (P0)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `playground.py` | Rewrite `_admin_assistant_direct()`: OpenClaw CLI → Bedrock Converse |
| 1.2 | `playground.py` | Add `_load_admin_conversation()` / `_save_admin_conversation()` |
| 1.3 | `settings.py` | Enhanced admin-assistant config (systemPrompt, maxHistoryTurns) |
| 1.4 | `settings.py` | New: GET/DELETE /settings/admin-assistant/history |
| 1.5 | `settings.py` | New: GET /settings/platform-access (SSM commands) |

### Phase 2: Config audit + refresh (P0)

| Task | File | Description |
|------|------|-------------|
| 2.1 | `settings.py` | All model config endpoints: add audit + bump_config_version |
| 2.2 | `settings.py` | All agent config endpoints: add audit |
| 2.3 | `settings.py` | Position model change: force refresh affected employees |

### Phase 3: Platform operations (P1)

| Task | File | Description |
|------|------|-------------|
| 3.1 | `settings.py` | New: GET /settings/platform-logs (journalctl) |
| 3.2 | `settings.py` | Services endpoint: add region to response (for frontend dynamic URLs) |

### Phase 4: Frontend (P1)

| Task | File | Description |
|------|------|-------------|
| 4.1 | `AdminAssistant.tsx` | Remove localStorage, load from API |
| 4.2 | `AdminAssistant.tsx` | Add [Clear History] button |
| 4.3 | `Settings.tsx` | Platform Access tab: SSM commands + Gateway link |
| 4.4 | `Settings.tsx` | Admin Assistant: system prompt editor |
| 4.5 | `Settings.tsx` | Platform Logs tab: journalctl viewer |

---

## 4. TODO

### Must-Do
- [ ] 1.1-1.5: Admin Assistant rewrite (Bedrock Converse + DynamoDB history)
- [ ] 2.1-2.3: Config change audit + force refresh
- [ ] 3.1-3.2: Platform logs + region in services
- [ ] 4.1-4.5: Frontend updates
- [ ] Unit tests
- [ ] Update ui-guide.html Settings findings

### Additional issues found during full scan

**Auth gaps (auth middleware covers JWT, but no role check):**
- [ ] `get_model_config_endpoint()` (line 68): no require_role — any employee can read model config
- [ ] `set_default_model()` (line 73): no require_role — any employee can change default model
- [ ] `set_fallback_model()` (line 81): no require_role
- [ ] `set_position_model()` (line 89): no require_role
- [ ] `remove_position_model()` (line 97): no require_role
- [ ] `get_security_config_endpoint()` (line 225): no require_role
- [ ] `update_security_config()` (line 230): no require_role — any employee can change security settings

**Audit gaps (config changes not logged):**
- [ ] set_default_model: no AUDIT# entry
- [ ] set_fallback_model: no AUDIT#
- [ ] set_position_model: no AUDIT#
- [ ] remove_position_model: no AUDIT#
- [ ] set_position_agent_config: no AUDIT#
- [ ] set_employee_agent_config: no AUDIT#
- [ ] delete_position/employee_agent_config: no AUDIT#
- [ ] update_security_config: no AUDIT# — security policy changes completely invisible

**Config version bump gaps:**
- [ ] set_default_model: no bump_config_version — agents keep old model until natural timeout
- [ ] set_fallback_model: no bump
- [ ] set_position_model: no bump
- [ ] Agent config changes: no bump — compaction/language changes not propagated

**Operational gaps:**
- [ ] Admin Assistant operation audit: each bot conversation should write AUDIT# eventType="admin_assistant_query"
- [ ] Services endpoint dedup: settings/services overlaps with monitor/system-status
- [ ] Service restart: POST /api/v1/settings/restart-service (systemctl restart, admin only, with audit)
- [ ] Org sync apply: `_auto_provision_employee` called incorrectly (line 335) — it expects emp dict with positionId, remote data may not have the right format. No error handling. No audit of individual provisions.
- [ ] Org sync: Feishu/DingTalk API calls have no retry, 10s timeout. Network blip = sync fails entirely.
- [ ] Password change: writes SSM but doesn't validate old password first
- [ ] Admin assistant `allowedCommands` field is obsolete with Bedrock Converse rewrite — remove

### Design Decisions
- OpenClaw CLI (Gateway) remains as platform maintenance tool, accessed via SSM port forward
- Admin Assistant is independent: direct Bedrock Converse, no OpenClaw dependency
- Conversation history in DynamoDB CONV#admin-assistant (not localStorage)
- Config changes audit: all model/agent config writes create AUDIT# entries
- Admin Assistant queries logged to AUDIT# for operational audit trail
- Platform logs: read-only journalctl, 3 services, max 200 lines
- Service restart: admin can restart services from Settings (with audit + confirmation)
