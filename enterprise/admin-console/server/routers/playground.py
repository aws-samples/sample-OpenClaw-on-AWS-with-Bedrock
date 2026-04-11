"""
Playground — Interactive audit replay for admin testing.

Left: conversation with real AgentCore (Live) or Bedrock Converse (Simulate)
Right: pipeline showing actual runtime config + AUDIT# events from interaction

Endpoints: /api/v1/playground/*
"""

import os
import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
import s3ops
from shared import require_role, GATEWAY_REGION

router = APIRouter(tags=["playground"])


class PlaygroundMessage(BaseModel):
    tenant_id: str
    message: str
    mode: str = "simulate"  # "simulate" or "live"


# =========================================================================
# Profiles — from DynamoDB (dynamic toolAllowlist)
# =========================================================================

@router.get("/api/v1/playground/profiles")
def get_playground_profiles():
    """Dynamically generate profiles from DynamoDB positions."""
    emps = db.get_employees()
    positions = db.get_positions()
    pos_map = {p["id"]: p for p in positions}
    profiles = {}
    for emp in emps:
        if not emp.get("agentId"):
            continue
        pos_id = emp.get("positionId", "")
        pos = pos_map.get(pos_id, {})
        tenant_id = f"port__{emp['id']}"
        role = pos.get("name", "unknown").lower().replace(" ", "_")
        tools = pos.get("toolAllowlist", ["web_search"])
        blocked = [t for t in ["shell", "browser", "file_write", "code_execution"] if t not in tools]
        plan_a = f"ALLOW: {', '.join(tools)}."
        if blocked:
            plan_a += f"\nDENY: {', '.join(blocked)}."
        plan_e = "Block PII (SSN, credit cards, phone numbers). Block credential exposure."
        profiles[tenant_id] = {"role": role, "tools": tools, "planA": plan_a, "planE": plan_e}
    profiles["port__admin"] = {
        "role": "it_admin",
        "tools": ["web_search", "shell", "browser", "file", "file_write", "code_execution"],
        "planA": "ALLOW: all tools. Full IT Admin access.",
        "planE": "Block credential exposure in responses.",
    }
    return profiles


# =========================================================================
# Pipeline Config — complete runtime configuration for an employee
# =========================================================================

@router.get("/api/v1/playground/pipeline/{emp_id}")
def get_pipeline_config(emp_id: str, authorization: str = Header(default="")):
    """Complete runtime configuration for testing/verification."""
    require_role(authorization, roles=["admin", "manager"])
    emp = db.get_employee(emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    pos_id = emp.get("positionId", "")
    pos = db.get_position(pos_id) if pos_id else {}

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
        "employee": {"id": emp_id, "name": emp.get("name", ""),
                      "position": pos.get("name", ""), "department": emp.get("departmentName", "")},
        "soul": {
            "globalWords": len(global_soul.split()),
            "positionWords": len(position_soul.split()),
            "personalWords": len(personal_soul.split()),
            "totalChars": len(global_soul) + len(position_soul) + len(personal_soul),
        },
        "planA": {"tools": tools, "blocked": [t for t in all_tools if t not in tools]},
        "kbs": kb_ids,
        "model": model,
        "modelSource": ("employee" if mc.get("employeeOverrides", {}).get(emp_id)
                        else "position" if mc.get("positionOverrides", {}).get(pos_id)
                        else "default"),
        "runtime": runtime_id,
    }


# =========================================================================
# Interaction Events — AUDIT# events from a specific interaction
# =========================================================================

@router.get("/api/v1/playground/events")
def get_playground_events(
    tenant_id: str = "", seconds: int = 60,
    authorization: str = Header(default=""),
):
    """AUDIT# events for a tenant from the last N seconds."""
    require_role(authorization, roles=["admin", "manager"])
    entries = db.get_audit_entries(limit=50)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    base_id = tenant_id.replace("port__", "").replace("pgnd__", "").split("__")[0] if tenant_id else ""
    events = []
    for e in entries:
        if e.get("timestamp", "") < cutoff:
            continue
        if base_id and (base_id in e.get("actorId", "") or base_id in e.get("targetId", "")):
            icon = "✅" if e.get("status") == "success" else "⛔" if e.get("status") == "blocked" else "🟡"
            events.append({**e, "icon": icon})
    return {"events": events, "count": len(events)}


# =========================================================================
# Admin Assistant — delegates to admin_ai.py
# =========================================================================

def _admin_assistant_direct(message: str) -> dict:
    """Playground admin path — delegates to admin_ai.py."""
    from routers.admin_ai import _admin_ai_history, _admin_ai_loop

    class _FakeUser:
        employee_id = "admin"
        name = "Admin"
        role = "admin"
    user = _FakeUser()

    history = _admin_ai_history.setdefault("admin", [])
    history.append({"role": "user", "content": [{"text": message}]})
    if len(history) > 20:
        _admin_ai_history["admin"] = history[-20:]
        history = _admin_ai_history["admin"]

    response_text = _admin_ai_loop(history, user)
    history.append({"role": "assistant", "content": [{"text": response_text}]})

    return {"response": response_text, "tenant_id": "admin",
            "profile": {"role": "it_admin", "tools": [], "planA": "Admin AI (Bedrock + tools)", "planE": ""},
            "source": "admin-ai"}


# =========================================================================
# Simulate — Bedrock Converse with real SOUL + Plan A
# =========================================================================

def _simulate_agent(emp_id: str, message: str, profile: dict) -> dict:
    """Simulate agent using Bedrock Converse with employee's real SOUL configuration."""
    import boto3 as _b3sim

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
    blocked = [t for t in ["shell", "browser", "file_write", "code_execution"] if t not in tools]
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


# =========================================================================
# Send — route to Live (AgentCore) or Simulate (Bedrock direct)
# =========================================================================

@router.post("/api/v1/playground/send")
def playground_send(body: PlaygroundMessage, authorization: str = Header(default="")):
    """Send message. Live = real AgentCore. Simulate = Bedrock Converse with real SOUL."""
    require_role(authorization, roles=["admin", "manager"])
    profiles = get_playground_profiles()
    profile = profiles.get(body.tenant_id, {"role": "unknown", "tools": ["web_search"], "planA": "Default", "planE": "Default"})
    emp_id = body.tenant_id.replace("port__", "")

    if body.mode == "live":
        if emp_id == "admin":
            return _admin_assistant_direct(body.message)

        router_url = os.environ.get("TENANT_ROUTER_URL", "http://localhost:8090")
        try:
            import requests as _req
            r = _req.post(f"{router_url}/route", json={
                "channel": "playground", "user_id": emp_id, "message": body.message,
            }, timeout=180)
            if r.status_code == 200:
                data = r.json()
                agent_response = data.get("response", {})
                resp_text = agent_response.get("response", str(data)) if isinstance(agent_response, dict) else str(agent_response)
                return {
                    "response": resp_text,
                    "tenant_id": data.get("tenant_id", body.tenant_id),
                    "profile": profile,
                    "plan_a": profile.get("planA", ""),
                    "plan_e": "✅ PASS — Real agent response via AgentCore.",
                    "source": "agentcore",
                }
            else:
                return {
                    "response": f"AgentCore returned {r.status_code}: {r.text[:200]}",
                    "tenant_id": body.tenant_id, "profile": profile,
                    "plan_a": profile.get("planA", ""),
                    "plan_e": f"ERROR — Status {r.status_code}", "source": "error",
                }
        except Exception as e:
            return {
                "response": f"AgentCore call failed: {e}\n\nFalling back to simulation.",
                "tenant_id": body.tenant_id, "profile": profile,
                "plan_a": profile.get("planA", ""),
                "plan_e": "ERROR — AgentCore unreachable.", "source": "error",
            }

    # Simulate mode: Bedrock Converse with real SOUL
    result = _simulate_agent(emp_id, body.message, profile)
    return {
        "response": result["response"],
        "tenant_id": body.tenant_id,
        "profile": profile,
        "plan_a": profile["planA"],
        "plan_e": result.get("plan_e", ""),
        "source": result["source"],
    }
