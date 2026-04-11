# Design: Settings & Admin Assistant — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-settings.md, settings.py (659 lines), playground.py (264 lines)

---

## File-by-File Change Design

### 1. playground.py — Admin Assistant: CLI → Bedrock Converse

**REWRITE: `_admin_assistant_direct()`**

```
DELETE: entire function (lines 73-136) — subprocess + OpenClaw CLI

REPLACE with direct Bedrock Converse:

def _admin_assistant_direct(message: str) -> dict:
    """Admin Assistant — direct Bedrock Converse API.
    System prompt from DynamoDB. History in DynamoDB CONV#admin-assistant."""
    import boto3 as _b3aa
    from shared import GATEWAY_REGION

    # Load config
    cfg = db.get_config("admin-assistant") or {}
    model_id = cfg.get("model", os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0"))
    system_prompt = cfg.get("systemPrompt",
        "You are the IT Admin Assistant for OpenClaw Enterprise platform. "
        "You help the admin manage AI agents, monitor system health, "
        "troubleshoot issues, and configure the platform. "
        "Be concise, technical, and actionable. "
        "When asked about platform status, refer to the monitoring data. "
        "When asked about employees or agents, refer to the organization data.")
    extra = cfg.get("systemPromptExtra", "")
    if extra:
        system_prompt += "\n\n" + extra

    max_history = int(cfg.get("maxHistoryTurns", 20))
    max_tokens = int(cfg.get("maxTokens", 4096))

    # Load conversation history from DynamoDB
    history_records = db.get_session_conversation("admin-assistant")
    messages = []
    for r in history_records[-max_history:]:
        role = r.get("role", "")
        content = r.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": [{"text": content}]})
    messages.append({"role": "user", "content": [{"text": message}]})

    try:
        bedrock = _b3aa.client("bedrock-runtime", region_name=GATEWAY_REGION)
        response = bedrock.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=messages,
            inferenceConfig={"maxTokens": max_tokens},
        )
        reply = response["output"]["message"]["content"][0]["text"]

        # Save conversation to DynamoDB
        import time as _taa
        from datetime import datetime as _dtaa, timezone as _tzaa
        ts = _dtaa.now(_tzaa.utc).isoformat()
        ddb = _b3aa.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        base_ts = int(_taa.time() * 1000)
        table.put_item(Item={
            "PK": "ORG#acme", "SK": f"CONV#admin-assistant#user#{base_ts}",
            "sessionId": "admin-assistant", "role": "user", "content": message, "ts": ts,
        })
        table.put_item(Item={
            "PK": "ORG#acme", "SK": f"CONV#admin-assistant#assistant#{base_ts+1}",
            "sessionId": "admin-assistant", "role": "assistant", "content": reply, "ts": ts,
        })

        return {"response": reply, "tenant_id": "admin",
                "profile": {"role": "it_admin", "tools": [], "planA": "Bedrock Converse direct", "planE": ""},
                "source": "bedrock-direct", "model": model_id}

    except Exception as e:
        return {"response": f"Error: {e}", "tenant_id": "admin",
                "profile": {"role": "it_admin"}, "source": "error"}
```

**CLEANUP: remove OpenClaw CLI imports**
```
DELETE from playground.py:
  from routers.openclaw_cli import find_openclaw_bin, openclaw_env_path
  (only if no other function uses them — check playground_send live mode)
```

Check: `playground_send` live mode for employee still uses `requests.post` to Tenant Router, not OpenClaw CLI. So the CLI imports are only used by admin assistant. But keep them if Playground admin detection still needs them — actually no, admin path is `_admin_assistant_direct()` which we're rewriting. Safe to remove.

Wait — check if `find_openclaw_bin` is used elsewhere:

```
grep: only in _admin_assistant_direct (line 93-94)
→ Safe to remove the import after rewrite
```

### 2. settings.py — 8 changes

#### 2.1 All model config endpoints: add auth + audit + bump

```
MODIFY: set_default_model (line 72)
  ADD: authorization parameter + require_role
  ADD: bump_config_version()
  ADD: db.create_audit_entry({eventType: "config_change", targetType: "model", targetId: "default"})

Same for: set_fallback_model, set_position_model, remove_position_model
(set_employee_model and remove_employee_model already have require_role)
```

#### 2.2 All agent config endpoints: add audit

```
MODIFY: set_position_agent_config (line 138)
  ADD: db.create_audit_entry({eventType: "config_change", targetType: "agent-config"})
  ADD: bump_config_version()

Same for: set_employee_agent_config, delete_position_agent_config, delete_employee_agent_config
```

#### 2.3 Position model change: force refresh

```
MODIFY: set_position_model (line 88)
  AFTER db.set_config:
    import threading
    for emp in db.get_employees():
        if emp.get("positionId") == pos_id and emp.get("agentId"):
            threading.Thread(target=stop_employee_session, args=(emp["id"],), daemon=True).start()
```

#### 2.4 Platform Access endpoint

```
ADD:
@router.get("/api/v1/settings/platform-access")
def get_platform_access(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    return {
        "instanceId": GATEWAY_INSTANCE_ID,
        "region": GATEWAY_REGION,
        "stackName": STACK_NAME,
        "ssmSession": f"aws ssm start-session --target {GATEWAY_INSTANCE_ID} --region {GATEWAY_REGION}",
        "gatewayPortForward": (
            f"aws ssm start-session --target {GATEWAY_INSTANCE_ID} --region {GATEWAY_REGION} "
            f"--document-name AWS-StartPortForwardingSession "
            f"--parameters portNumber=18789,localPortNumber=18789"),
        "gatewayUrl": "http://localhost:18789",
        "note": "Run the port forward command in your terminal, then open the Gateway URL in browser.",
    }
```

#### 2.5 Admin Assistant history API

```
ADD:
@router.get("/api/v1/settings/admin-assistant/history")
def get_admin_history(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    records = db.get_session_conversation("admin-assistant")
    return [{"role": r.get("role",""), "content": r.get("content",""), "ts": r.get("ts","")}
            for r in records[-50:]]


@router.delete("/api/v1/settings/admin-assistant/history")
def clear_admin_history(authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    try:
        from boto3.dynamodb.conditions import Key
        ddb = boto3.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
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

#### 2.6 Platform Logs endpoint

```
ADD:
@router.get("/api/v1/settings/platform-logs")
def get_platform_logs(service: str = "openclaw-admin", lines: int = 50, authorization: str = Header(default="")):
    require_role(authorization, roles=["admin"])
    allowed = {"openclaw-admin", "tenant-router", "bedrock-proxy-h2"}
    if service not in allowed:
        raise HTTPException(400, f"Service must be one of: {sorted(allowed)}")
    lines = min(lines, 200)
    try:
        import subprocess
        output = subprocess.check_output(
            ["journalctl", "-u", service, "--no-pager", "-n", str(lines)],
            text=True, timeout=10)
        log_lines = output.strip().split("\n") if output.strip() else []
        return {"service": service, "lines": log_lines, "count": len(log_lines)}
    except Exception as e:
        return {"service": service, "lines": [], "error": str(e)}
```

#### 2.7 Enhanced admin-assistant config

```
MODIFY: get_admin_assistant (line 575)

Before:
    return {
        "model": cfg.get("model", ...),
        "allowedCommands": cfg.get("allowedCommands", [...]),
        "systemPromptExtra": cfg.get("systemPromptExtra", ""),
    }

After:
    return {
        "model": cfg.get("model", os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")),
        "systemPrompt": cfg.get("systemPrompt",
            "You are the IT Admin Assistant for OpenClaw Enterprise platform. "
            "You help the admin manage AI agents, monitor system health, "
            "troubleshoot issues, and configure the platform. "
            "Be concise, technical, and actionable."),
        "systemPromptExtra": cfg.get("systemPromptExtra", ""),
        "maxHistoryTurns": int(cfg.get("maxHistoryTurns", 20)),
        "maxTokens": int(cfg.get("maxTokens", 4096)),
    }


MODIFY: put_admin_assistant (line 589)

    cfg = {
        "model": body.get("model", ""),
        "systemPrompt": body.get("systemPrompt", ""),
        "systemPromptExtra": body.get("systemPromptExtra", ""),
        "maxHistoryTurns": int(body.get("maxHistoryTurns", 20)),
        "maxTokens": int(body.get("maxTokens", 4096)),
    }
```

#### 2.8 Services: add region to response

```
MODIFY: get_services() return value — add "region" to platform section

    "platform": {
        "instanceId": GATEWAY_INSTANCE_ID,
        "region": GATEWAY_REGION,        # already there
        "stackName": STACK_NAME,
        "awsRegion": AWS_REGION,         # ADD: actual AWS region for console URLs
    },
```

---

### 3. settings.py — Additional fixes

#### 3.1 Add require_role to all unprotected endpoints

```
MODIFY these endpoints — add authorization parameter + require_role:

  get_model_config_endpoint()   → require_role(authorization, roles=["admin"])
  set_default_model()           → require_role(authorization, roles=["admin"])
  set_fallback_model()          → require_role(authorization, roles=["admin"])
  set_position_model()          → require_role(authorization, roles=["admin"])
  remove_position_model()       → require_role(authorization, roles=["admin"])
  get_security_config_endpoint()→ require_role(authorization, roles=["admin"])
  update_security_config()      → require_role(authorization, roles=["admin"])
```

#### 3.2 Add AUDIT# to all config change endpoints

```
ADD to each PUT/DELETE config endpoint:
  db.create_audit_entry({
      "timestamp": now, "eventType": "config_change",
      "actorId": user.employee_id, "actorName": user.name,
      "targetType": "<model|agent-config|security>",
      "targetId": "<pos_id|emp_id|default|fallback>",
      "detail": "...", "status": "success",
  })

Affected: set_default_model, set_fallback_model, set_position_model,
  remove_position_model, set_position_agent_config, delete_position_agent_config,
  set_employee_agent_config, delete_employee_agent_config, update_security_config
```

#### 3.3 Add bump_config_version to model + agent config changes

```
ADD bump_config_version() after db.set_config() in:
  set_default_model, set_fallback_model, set_position_model, remove_position_model,
  set_position_agent_config, set_employee_agent_config
```

#### 3.4 Service restart endpoint

```
ADD:
@router.post("/api/v1/settings/restart-service")
def restart_service(body: dict, authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin"])
    service = body.get("service", "")
    allowed = {"openclaw-admin", "tenant-router", "bedrock-proxy-h2"}
    if service not in allowed:
        raise HTTPException(400, f"Service must be one of: {sorted(allowed)}")
    try:
        import subprocess
        subprocess.check_output(
            ["sudo", "systemctl", "restart", service], text=True, timeout=15)
        db.create_audit_entry({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eventType": "service_restart",
            "actorId": user.employee_id, "actorName": user.name,
            "targetType": "service", "targetId": service,
            "detail": f"Admin restarted {service}", "status": "success",
        })
        return {"restarted": True, "service": service}
    except Exception as e:
        raise HTTPException(500, str(e))
```

#### 3.5 Admin assistant query audit

```
In playground.py _admin_assistant_direct(), after saving conversation:
  db.create_audit_entry({
      "timestamp": ts,
      "eventType": "admin_assistant_query",
      "actorId": "admin", "actorName": "Admin",
      "targetType": "assistant", "targetId": "admin-assistant",
      "detail": f"Admin query: {message[:80]}",
      "status": "success",
  })
```

---

## Unit Test Plan

```
test_settings.py:

1. test_admin_assistant_no_subprocess:
   Scan playground.py _admin_assistant_direct for "subprocess" → must NOT exist
   Scan for "bedrock" or "converse" → must exist

2. test_admin_assistant_dynamodb_history:
   Scan playground.py for "CONV#admin-assistant" → must exist

3. test_platform_access_endpoint:
   Scan settings.py for "def get_platform_access" → must exist
   Scan for "portNumber=18789" → must exist

4. test_admin_history_endpoints:
   Scan settings.py for "def get_admin_history" → must exist
   Scan for "def clear_admin_history" → must exist

5. test_platform_logs_endpoint:
   Scan settings.py for "def get_platform_logs" → must exist
   Scan for "journalctl" → must exist

6. test_model_config_has_audit:
   Scan set_default_model for "create_audit_entry" → must exist

7. test_model_config_has_bump:
   Scan set_default_model for "bump_config_version" → must exist

8. test_position_model_has_refresh:
   Scan set_position_model for "stop_employee_session" → must exist

9. test_admin_assistant_config_enhanced:
   Scan get_admin_assistant for "systemPrompt" and "maxHistoryTurns" → must exist
   Scan for "allowedCommands" → must NOT exist

10. test_services_has_aws_region:
    Scan get_services for "awsRegion" → must exist

11. test_all_model_endpoints_have_role_check:
    Scan set_default_model, set_fallback_model, set_position_model, remove_position_model
      for "require_role" → must exist

12. test_security_config_has_role_check:
    Scan get_security_config_endpoint, update_security_config for "require_role" → must exist

13. test_security_config_has_audit:
    Scan update_security_config for "create_audit_entry" → must exist

14. test_restart_service_exists:
    Scan for "def restart_service" → must exist

15. test_admin_query_audit:
    Scan playground.py _admin_assistant_direct for "admin_assistant_query" → must exist

16. test_no_allowed_commands:
    Scan get_admin_assistant for "allowedCommands" → must NOT exist
```

---

## Migration Notes

- Admin Assistant conversation: old localStorage history will not migrate to DynamoDB. Fresh start. Frontend should handle empty history gracefully (already does — shows welcome message).
- CONFIG#admin-assistant schema: new fields (systemPrompt, maxHistoryTurns, maxTokens) are backward compatible — old config missing these fields gets defaults.
- No new DynamoDB tables (uses existing CONV# prefix for admin history).
- Platform logs: requires `journalctl` on EC2 (Ubuntu has it by default).
- OpenClaw CLI remains on EC2 for Gateway management — no removal needed.
