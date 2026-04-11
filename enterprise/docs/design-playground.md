# Design: Playground — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-playground.md, playground.py (226 lines)

---

## File-by-File Change Design

### 1. playground.py — 4 changes

#### 1.1 Remove _POS_TOOLS hardcode

```
DELETE: lines 25-37 (_POS_TOOLS dict)

MODIFY: get_playground_profiles() line 56
  Before: tools = _POS_TOOLS.get(pos_id, pos.get("toolAllowlist", ["web_search"]))
  After:  tools = pos.get("toolAllowlist", ["web_search"])
```

#### 1.2 New: pipeline config API

```python
@router.get("/api/v1/playground/pipeline/{emp_id}")
def get_pipeline_config(emp_id: str, authorization: str = Header(default="")):
    """Complete runtime configuration for an employee's agent."""
    require_role(authorization, roles=["admin", "manager"])
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    pos_id = emp.get("positionId", "")
    pos = db.get_position(pos_id) if pos_id else {}

    import s3ops
    global_soul = s3ops.read_file("_shared/soul/global/SOUL.md") or ""
    position_soul = s3ops.read_file(f"_shared/soul/positions/{pos_id}/SOUL.md") if pos_id else ""
    personal_soul = s3ops.read_file(f"{emp_id}/workspace/PERSONAL_SOUL.md") or ""

    from routers.settings import _get_model_config
    mc = _get_model_config()
    model = (mc.get("employeeOverrides", {}).get(emp_id, {}).get("modelId")
             or mc.get("positionOverrides", {}).get(pos_id, {}).get("modelId")
             or mc.get("default", {}).get("modelId", ""))

    kb_cfg = db.get_config("kb-assignments") or {}
    kb_ids = list(set(
        kb_cfg.get("positionKBs", {}).get(pos_id, [])
        + kb_cfg.get("employeeKBs", {}).get(emp_id, [])))

    routing = db.get_routing_config()
    runtime_id = (routing.get("employee_override", {}).get(emp_id)
                  or routing.get("position_runtime", {}).get(pos_id)
                  or "default")

    tools = pos.get("toolAllowlist", ["web_search"])
    all_tools = ["web_search", "shell", "browser", "file", "file_write", "code_execution"]

    return {
        "employee": {"id": emp_id, "name": emp.get("name", ""), "position": pos.get("name", ""),
                      "department": emp.get("departmentName", "")},
        "soul": {
            "globalWords": len(global_soul.split()),
            "positionWords": len(position_soul.split()),
            "personalWords": len(personal_soul.split()),
            "totalChars": len(global_soul) + len(position_soul) + len(personal_soul),
        },
        "planA": {
            "tools": tools,
            "blocked": [t for t in all_tools if t not in tools],
        },
        "kbs": kb_ids,
        "model": model,
        "modelSource": ("employee" if mc.get("employeeOverrides", {}).get(emp_id)
                        else "position" if mc.get("positionOverrides", {}).get(pos_id)
                        else "default"),
        "runtime": runtime_id,
    }
```

#### 1.3 New: interaction events API

```python
@router.get("/api/v1/playground/events")
def get_playground_events(
    tenant_id: str = "", seconds: int = 60,
    authorization: str = Header(default=""),
):
    """AUDIT# events for a tenant from the last N seconds."""
    require_role(authorization, roles=["admin", "manager"])
    from datetime import datetime, timezone, timedelta
    entries = db.get_audit_entries(limit=50)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    base_id = tenant_id.replace("port__", "").replace("pgnd__", "").split("__")[0] if tenant_id else ""
    events = []
    for e in entries:
        if e.get("timestamp", "") < cutoff:
            continue
        if base_id and (base_id in e.get("actorId", "") or base_id in e.get("targetId", "")):
            et = e.get("eventType", "")
            icon = "✅" if e.get("status") == "success" else "⛔" if e.get("status") == "blocked" else "🟡"
            events.append({**e, "icon": icon})
    return {"events": events, "count": len(events)}
```

#### 1.4 Simulate mode: Bedrock Converse with real SOUL

```
REPLACE: lines 161-225 (entire simulate mode keyword matching block)

WITH:
def _simulate_agent(emp_id: str, message: str, profile: dict) -> dict:
    import boto3 as _b3sim
    import s3ops
    from shared import GATEWAY_REGION

    pos_id = ""
    for e in db.get_employees():
        if e["id"] == emp_id:
            pos_id = e.get("positionId", "")
            break

    global_soul = s3ops.read_file("_shared/soul/global/SOUL.md") or ""
    position_soul = s3ops.read_file(f"_shared/soul/positions/{pos_id}/SOUL.md") if pos_id else ""
    personal_soul = s3ops.read_file(f"{emp_id}/workspace/PERSONAL_SOUL.md") or ""

    system = f"{global_soul}\n\n---\n\n{position_soul}\n\n---\n\n{personal_soul}"

    tools = profile.get("tools", [])
    blocked = [t for t in ["shell","browser","file_write","code_execution"] if t not in tools]
    if blocked:
        plan_a = f"Allowed tools: {', '.join(tools)}.\nYou MUST NOT use: {', '.join(blocked)}."
        system = f"{plan_a}\n\n---\n\n{system}"

    try:
        bedrock = _b3sim.client("bedrock-runtime", region_name=GATEWAY_REGION)
        model_id = os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
        resp = bedrock.converse(
            modelId=model_id,
            system=[{"text": system[:8000]}],
            messages=[{"role": "user", "content": [{"text": message}]}],
            inferenceConfig={"maxTokens": 2048},
        )
        reply = resp["output"]["message"]["content"][0]["text"]
        return {"response": reply, "source": "simulate-bedrock",
                "plan_e": "✅ Simulated — real SOUL + Plan A, Bedrock Converse direct"}
    except Exception as e:
        return {"response": f"Simulation error: {e}", "source": "error", "plan_e": "ERROR"}


# In playground_send(), replace the simulate block:
    if body.mode == "simulate":
        result = _simulate_agent(emp_id, body.message, profile)
        return {
            "response": result["response"],
            "tenant_id": body.tenant_id,
            "profile": profile,
            "plan_a": profile["planA"],
            "plan_e": result.get("plan_e", ""),
            "source": result["source"],
        }
```

#### Import addition

```
ADD: from fastapi import HTTPException  (if not already imported)
ADD: import s3ops  (for pipeline config)
```

---

## Unit Test Plan

```
test_playground.py:

1. test_no_pos_tools_hardcode:
   Scan playground.py for "_POS_TOOLS" → must NOT exist

2. test_profiles_use_dynamodb:
   Scan get_playground_profiles for "toolAllowlist" → must exist

3. test_pipeline_config_exists:
   Scan for "def get_pipeline_config" → must exist

4. test_pipeline_has_soul_summary:
   Scan get_pipeline_config for "globalWords" and "personalWords" → must exist

5. test_playground_events_exists:
   Scan for "def get_playground_events" → must exist

6. test_simulate_uses_bedrock:
   Scan for "def _simulate_agent" → must exist
   Scan _simulate_agent for "converse" → must exist

7. test_no_keyword_matching:
   Scan playground.py for "is_shell" or "is_jira" → must NOT exist

8. test_admin_delegates_to_admin_ai:
   Scan _admin_assistant_direct for "admin_ai" → must exist
   Scan for "subprocess" → must NOT exist
```

---

## Migration Notes

- No DynamoDB changes
- No new tables
- Simulate mode now calls Bedrock → needs bedrock:Converse IAM (already present)
- Pipeline config reads S3 SOUL files → already accessible
- Frontend: requires npm build for Pipeline tab + events display
