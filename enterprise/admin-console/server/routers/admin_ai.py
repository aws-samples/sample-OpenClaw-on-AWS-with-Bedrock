"""
Admin AI Assistant — Claude via Bedrock Converse API + whitelist tools.
No shell, no subprocess, no OpenClaw. Bounded read/write via Python fns.

Endpoints: /api/v1/admin-ai/*
"""

import os
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import db
import s3ops
from shared import require_role, GATEWAY_REGION

router = APIRouter(tags=["admin-ai"])

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_ADMIN_AI_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

_ADMIN_AI_SYSTEM = """You are the IT Admin Assistant for OpenClaw Enterprise — the AI Ops expert for this platform.
You understand every aspect of how this platform is built, deployed, configured, and operated.

# Platform Architecture

OpenClaw Enterprise is a multi-tenant AI agent platform deployed on AWS:
- **EC2 Gateway** — runs OpenClaw Gateway (port 18789), Admin Console (port 8099), Tenant Router (port 8090), Bedrock H2 Proxy (port 8091)
- **Amazon Bedrock** — model inference (Nova 2 Lite default, Claude Sonnet/Opus for exec tier)
- **DynamoDB** — org data, sessions, usage, audit logs (table name = STACK_NAME)
- **S3** — SOUL templates, workspaces, knowledge docs, skills
- **ECS Fargate** — always-on agent containers (exec tier, 24/7 IM connectivity)
- **AgentCore** — serverless agent runtime for standard employees

# Key Concepts

**Three-Layer SOUL**: Global (locked by IT, affects ALL agents) → Position (per-role, e.g. "Finance Agent") → Personal (per-employee). Merged at session start.

**Agent Deployment Modes**:
- **Serverless (AgentCore)** — default, starts on-demand, lower cost, uses shared Gateway for IM
- **Always-on (ECS Fargate)** — 24/7 container, dedicated IM channels, for executives

**IM Channel Flow**:
1. Admin configures bot tokens in Gateway UI (one-time): SSM port-forward to localhost:18789 → Channels → add Telegram/Discord/Feishu etc.
2. After admin configures, employees pair from Portal → Connect IM → scan QR or send /start
3. All employees share the same bot; tenant_router maps IM user → employee → agent

**Employee Permissions** (Cedar-based):
- `employee` — web_search only
- `manager` — web_search + file read
- `exec` — full tools (shell, browser, code_execution, file_write)

# Configuration & Operations

**Config files on EC2**:
- `/etc/openclaw/env` — all environment variables (STACK_NAME, AWS_REGION, DYNAMODB_TABLE, ECS config, etc.)
- `~/.openclaw/openclaw.json` — OpenClaw Gateway config (model, auth, channels)
- `~/.openclaw/.env` — gateway service env vars (AWS_PROFILE=default for Bedrock IAM auth)

**Services** (systemd):
- `openclaw-gateway.service` (user) — OpenClaw Gateway on port 18789
- `openclaw-admin.service` — Admin Console on port 8099
- `openclaw-router.service` — Tenant Router on port 8090
- `openclaw-proxy.service` — Bedrock H2 Proxy on port 8091

**Common troubleshooting**:
- "No API key found for amazon-bedrock" after upgrade → write `AWS_PROFILE=default` to `~/.openclaw/.env`, restart gateway
- Agent offline / AgentCore unavailable → check tenant-router and openclaw-gateway services
- DynamoDB connection fails after stack name change → ensure DYNAMODB_TABLE in /etc/openclaw/env matches stack name
- IM pairing not working → admin must configure channel in Gateway UI first

# How To Use Tools

- Always use tools for data queries — never guess employee counts, usage, or config values.
- For SOUL edits: read the current template first (get_soul_template), then show the diff before writing.
- For health checks: use get_service_health, then give actionable next steps based on what's down.
- For IM channel questions: use list_bindings to check actual connection status.
- Respond in the same language the user writes in.
- Be concise. Use tables for data, bullet points for steps.
- You cannot execute shell commands directly. Guide the admin with exact commands they can run via SSM."""

# Per-admin conversation history (in-memory, resets on server restart)
_admin_ai_history: dict[str, list] = {}

_ADMIN_AI_TOOLS = [
    {
        "name": "list_employees",
        "description": "List employees, optionally filtered by department_id or position_id. Returns id, name, position, department, agent status, channels.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "department_id": {"type": "string"},
            "position_id": {"type": "string"},
        }}}
    },
    {
        "name": "get_employee_detail",
        "description": "Get full details for one employee: profile, agent config, bindings, recent usage.",
        "inputSchema": {"json": {"type": "object", "required": ["employee_id"], "properties": {
            "employee_id": {"type": "string", "description": "e.g. emp-carol"},
        }}}
    },
    {
        "name": "get_soul_template",
        "description": "Read a SOUL template from S3. scope=global reads the locked global SOUL. scope=position reads a position template (requires position_id). scope=personal reads an employee's personal SOUL (requires employee_id).",
        "inputSchema": {"json": {"type": "object", "required": ["scope"], "properties": {
            "scope": {"type": "string", "enum": ["global", "position", "personal"]},
            "position_id": {"type": "string"},
            "employee_id": {"type": "string"},
        }}}
    },
    {
        "name": "update_soul_template",
        "description": "Write a SOUL template to S3. Only position and personal scope are writable. Global is locked. Creates an audit log entry automatically.",
        "inputSchema": {"json": {"type": "object", "required": ["scope", "content"], "properties": {
            "scope": {"type": "string", "enum": ["position", "personal"]},
            "position_id": {"type": "string"},
            "employee_id": {"type": "string"},
            "content": {"type": "string"},
        }}}
    },
    {
        "name": "list_departments_and_positions",
        "description": "List all departments and positions with member counts and default channels.",
        "inputSchema": {"json": {"type": "object", "properties": {}}}
    },
    {
        "name": "get_agent_detail",
        "description": "Get agent configuration, SOUL versions, skills, channels, and today's usage.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "agent_id": {"type": "string"},
            "employee_id": {"type": "string", "description": "Alternative lookup by employee"},
        }}}
    },
    {
        "name": "get_usage_report",
        "description": "Get token usage and cost data. scope=summary gives org total, scope=by_department breaks down by dept, scope=by_agent lists per-agent stats.",
        "inputSchema": {"json": {"type": "object", "required": ["scope"], "properties": {
            "scope": {"type": "string", "enum": ["summary", "by_department", "by_agent"]},
        }}}
    },
    {
        "name": "get_service_health",
        "description": "Check health of all platform services: Gateway, Admin Console, Tenant Router, Bedrock, DynamoDB, S3.",
        "inputSchema": {"json": {"type": "object", "properties": {}}}
    },
    {
        "name": "get_audit_log",
        "description": "Query recent audit log entries. Optionally filter by employee_id or event_type.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "employee_id": {"type": "string"},
            "event_type": {"type": "string", "enum": ["agent_invocation", "permission_denied", "config_change", "approval_decision"]},
            "limit": {"type": "integer", "default": 20},
        }}}
    },
    {
        "name": "list_bindings",
        "description": "List IM channel bindings. Optionally filter by employee_id or channel name.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "employee_id": {"type": "string"},
            "channel": {"type": "string"},
        }}}
    },
]


def _execute_admin_tool(name: str, inputs: dict, actor_id: str, actor_name: str) -> str:
    """Execute one whitelisted admin tool. Returns result as string for Claude."""
    from routers.usage import _get_agent_usage_recent, usage_summary, usage_by_department, usage_by_agent
    try:
        if name == "list_employees":
            emps = db.get_employees()
            if inputs.get("department_id"):
                emps = [e for e in emps if e.get("departmentId") == inputs["department_id"]]
            if inputs.get("position_id"):
                emps = [e for e in emps if e.get("positionId") == inputs["position_id"]]
            rows = [f"- {e['id']} | {e['name']} | {e.get('positionName','')} | {e.get('departmentName','')} | agent={'yes' if e.get('agentId') else 'no'} | channels={','.join(e.get('channels',[]))}"
                    for e in emps]
            return f"{len(emps)} employees:\n" + "\n".join(rows)

        elif name == "get_employee_detail":
            emp_id = inputs["employee_id"]
            emps = db.get_employees()
            emp = next((e for e in emps if e["id"] == emp_id or e.get("name") == emp_id), None)
            if not emp:
                return f"Employee '{emp_id}' not found."
            agent = db.get_agent(emp.get("agentId", "")) if emp.get("agentId") else None
            bindings = [b for b in db.get_bindings() if b.get("employeeId") == emp["id"]]
            usage = _get_agent_usage_recent().get(emp.get("agentId", ""), {})
            lines = [
                f"**{emp['name']}** ({emp['id']})",
                f"Position: {emp.get('positionName','')} | Dept: {emp.get('departmentName','')} | Role: {emp.get('role','')}",
                f"Channels: {', '.join(emp.get('channels', []))}",
                f"Agent: {emp.get('agentId', 'none')} | Status: {agent.get('status','') if agent else 'no agent'}",
                f"Skills: {', '.join(agent.get('skills',[]) if agent else [])}",
                f"Bindings: {len(bindings)} ({', '.join(b.get('channel','') for b in bindings)})",
                f"Today usage: {usage.get('requests',0)} requests, ${usage.get('cost',0):.4f}",
            ]
            if agent:
                sv = agent.get("soulVersions") or {}
                lines.append(f"SOUL versions: global=v{sv.get('global',0)} position=v{sv.get('position',0)} personal=v{sv.get('personal',0)}")
            return "\n".join(lines)

        elif name == "get_soul_template":
            scope = inputs["scope"]
            if scope == "global":
                content = s3ops.read_file("_shared/soul/global/SOUL.md") or "(empty)"
                return f"**Global SOUL** (locked):\n\n{content[:3000]}"
            elif scope == "position":
                pos_id = inputs.get("position_id", "")
                if not pos_id:
                    return "position_id required for scope=position"
                content = s3ops.read_file(f"_shared/soul/positions/{pos_id}/SOUL.md") or "(not set)"
                return f"**Position SOUL [{pos_id}]**:\n\n{content[:3000]}"
            elif scope == "personal":
                emp_id = inputs.get("employee_id", "")
                if not emp_id:
                    return "employee_id required for scope=personal"
                content = s3ops.read_file(f"{emp_id}/workspace/SOUL.md") or "(not set)"
                return f"**Personal SOUL [{emp_id}]**:\n\n{content[:3000]}"

        elif name == "update_soul_template":
            scope = inputs["scope"]
            content = inputs["content"]
            if scope == "global":
                return "❌ Global SOUL is locked and cannot be modified."
            elif scope == "position":
                pos_id = inputs.get("position_id", "")
                if not pos_id:
                    return "position_id required for scope=position"
                s3ops.write_file(f"_shared/soul/positions/{pos_id}/SOUL.md", content)
                db.create_audit_entry({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "eventType": "config_change", "actorId": actor_id, "actorName": actor_name,
                    "targetType": "soul", "targetId": pos_id,
                    "detail": f"Admin AI updated position SOUL for {pos_id} ({len(content)} chars)",
                    "status": "success",
                })
                return f"✅ Position SOUL for {pos_id} updated ({len(content)} chars). Agents will get it on next workspace assembly."
            elif scope == "personal":
                emp_id = inputs.get("employee_id", "")
                if not emp_id:
                    return "employee_id required for scope=personal"
                s3ops.write_file(f"{emp_id}/workspace/SOUL.md", content)
                db.create_audit_entry({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "eventType": "config_change", "actorId": actor_id, "actorName": actor_name,
                    "targetType": "soul", "targetId": emp_id,
                    "detail": f"Admin AI updated personal SOUL for {emp_id} ({len(content)} chars)",
                    "status": "success",
                })
                return f"✅ Personal SOUL for {emp_id} updated ({len(content)} chars)."

        elif name == "list_departments_and_positions":
            depts = db.get_departments()
            positions = db.get_positions()
            lines = ["**Departments:**"]
            for d in depts:
                lines.append(f"  {d['id']} | {d['name']} | head: {d.get('headName','')} | members: {d.get('headCount',0)}")
            lines.append("\n**Positions:**")
            for p in positions:
                lines.append(f"  {p['id']} | {p['name']} | dept: {p.get('departmentName','')} | channel: {p.get('defaultChannel','')} | members: {p.get('employeeCount',0)}")
            return "\n".join(lines)

        elif name == "get_agent_detail":
            agents = db.get_agents()
            agent = None
            if inputs.get("agent_id"):
                agent = next((a for a in agents if a["id"] == inputs["agent_id"]), None)
            elif inputs.get("employee_id"):
                agent = next((a for a in agents if a.get("employeeId") == inputs["employee_id"]), None)
            if not agent:
                return "Agent not found."
            usage = _get_agent_usage_recent().get(agent["id"], {})
            sv = agent.get("soulVersions") or {}
            lines = [
                f"**{agent['name']}** ({agent['id']})",
                f"Employee: {agent.get('employeeName','')} | Position: {agent.get('positionName','')}",
                f"Status: {agent.get('status','')} | Quality: {agent.get('qualityScore','—')}",
                f"Channels: {', '.join(agent.get('channels',[]))}",
                f"Skills: {', '.join(agent.get('skills',[]))}",
                f"SOUL: global=v{sv.get('global',0)} position=v{sv.get('position',0)} personal=v{sv.get('personal',0)}",
                f"Today: {usage.get('requests',0)} reqs, ${usage.get('cost',0):.4f}, model={usage.get('model','')}",
            ]
            return "\n".join(lines)

        elif name == "get_usage_report":
            scope = inputs["scope"]
            if scope == "summary":
                s = usage_summary()
                return (f"Input: {s['totalInputTokens']:,} tokens\n"
                        f"Output: {s['totalOutputTokens']:,} tokens\n"
                        f"Cost (7-day): ${s['totalCost']:.4f}\n"
                        f"Requests: {s['totalRequests']}\n"
                        f"Active tenants: {s['tenantCount']}")
            elif scope == "by_department":
                rows = usage_by_department()
                lines = [f"{'Dept':<25} {'Agents':>6} {'Reqs':>6} {'Tokens':>8} {'Cost':>8}"]
                lines.append("-" * 58)
                for r in rows:
                    lines.append(f"{r['department']:<25} {r['agents']:>6} {r['requests']:>6} {(r['inputTokens']+r['outputTokens'])//1000:>7}k ${r['cost']:>6.2f}")
                return "\n".join(lines)
            elif scope == "by_agent":
                rows = usage_by_agent()
                lines = [f"{'Agent':<30} {'Reqs':>5} {'Cost':>7}"]
                for r in rows[:20]:
                    lines.append(f"{r['agentName'][:30]:<30} {r['requests']:>5} ${r['cost']:>5.2f}")
                return "\n".join(lines)

        elif name == "get_service_health":
            from routers.settings import get_services
            svc = get_services()
            lines = []
            for name_svc, info in svc.items():
                status = info.get("status", "unknown")
                icon = "✅" if status in ("healthy", "active", "running", "connected") else "⚠️"
                extra = ""
                if name_svc == "gateway":
                    extra = f" | port {info.get('port','')} | {info.get('requestsToday',0)} reqs today"
                elif name_svc == "bedrock":
                    ms = info.get("latencyMs")
                    extra = f" | {info.get('region','')} | {ms}ms" if ms else f" | {info.get('region','')}"
                elif name_svc == "dynamodb":
                    extra = f" | {info.get('table','')} | {info.get('itemCount',0)} items"
                elif name_svc == "s3":
                    extra = f" | {info.get('bucket','')}"
                lines.append(f"{icon} **{name_svc}**: {status}{extra}")
            return "\n".join(lines)

        elif name == "get_audit_log":
            limit = min(int(inputs.get("limit", 20)), 50)
            entries = db.get_audit_entries(limit=limit)
            if inputs.get("employee_id"):
                entries = [e for e in entries if e.get("actorId") == inputs["employee_id"]]
            if inputs.get("event_type"):
                entries = [e for e in entries if e.get("eventType") == inputs["event_type"]]
            entries = entries[:limit]
            lines = []
            for e in entries:
                ts = e.get("timestamp", "")[:16].replace("T", " ")
                lines.append(f"{ts} | {e.get('eventType','')} | {e.get('actorName','')} | {e.get('detail','')[:60]}")
            return f"{len(lines)} entries:\n" + "\n".join(lines) if lines else "No entries found."

        elif name == "list_bindings":
            bindings = db.get_bindings()
            if inputs.get("employee_id"):
                bindings = [b for b in bindings if b.get("employeeId") == inputs["employee_id"]]
            if inputs.get("channel"):
                bindings = [b for b in bindings if b.get("channel") == inputs["channel"]]
            lines = [f"- {b.get('employeeName','')} ({b.get('employeeId','')}) | {b.get('channel','')} | {b.get('status','')} | agent: {b.get('agentId','')}"
                     for b in bindings]
            return f"{len(bindings)} bindings:\n" + "\n".join(lines)

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error ({name}): {e}"


def _admin_ai_loop(history: list, user) -> str:
    """Agentic loop: call Claude with tools, execute tool_use responses, repeat until text."""
    import boto3 as _b3_ai
    client = _b3_ai.client("bedrock-runtime", region_name=AWS_REGION)

    messages = list(history)

    for _ in range(8):
        try:
            resp = client.converse(
                modelId=_ADMIN_AI_MODEL,
                system=[{"text": _ADMIN_AI_SYSTEM}],
                messages=messages,
                toolConfig={"tools": [{"toolSpec": t} for t in _ADMIN_AI_TOOLS]},
                inferenceConfig={"maxTokens": 2048, "temperature": 0.2},
            )
        except Exception as e:
            return f"⚠️ AI error: {e}"

        stop_reason = resp.get("stopReason", "")
        output_msg = resp.get("output", {}).get("message", {})
        content_blocks = output_msg.get("content", [])

        text_parts = []
        tool_uses = []
        for block in content_blocks:
            if block.get("text"):
                text_parts.append(block["text"])
            if block.get("toolUse"):
                tool_uses.append(block["toolUse"])

        if stop_reason == "end_turn" or not tool_uses:
            return " ".join(text_parts) or "(no response)"

        messages.append({"role": "assistant", "content": content_blocks})
        tool_results = []
        for tu in tool_uses:
            result = _execute_admin_tool(tu["name"], tu.get("input", {}), user.employee_id, user.name)
            tool_results.append({
                "toolResult": {
                    "toolUseId": tu["toolUseId"],
                    "content": [{"text": result}],
                }
            })
        messages.append({"role": "user", "content": tool_results})

    return "⚠️ Reached maximum tool-use rounds."


class AdminAiMessage(BaseModel):
    message: str


@router.post("/api/v1/admin-ai/chat")
def admin_ai_chat(body: AdminAiMessage, authorization: str = Header(default="")):
    """Admin AI assistant — Claude via Bedrock + whitelist tools. Admin only."""
    user = require_role(authorization, roles=["admin"])

    history = _admin_ai_history.setdefault(user.employee_id, [])
    history.append({"role": "user", "content": [{"text": body.message}]})

    if len(history) > 20:
        _admin_ai_history[user.employee_id] = history[-20:]
        history = _admin_ai_history[user.employee_id]

    response_text = _admin_ai_loop(history, user)
    history.append({"role": "assistant", "content": [{"text": response_text}]})

    # Audit trail
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "admin_assistant_query",
        "actorId": user.employee_id, "actorName": user.name,
        "targetType": "assistant", "targetId": "admin-ai",
        "detail": f"Admin query: {body.message[:80]}", "status": "success",
    })

    return {"response": response_text}


@router.delete("/api/v1/admin-ai/chat")
def admin_ai_clear(authorization: str = Header(default="")):
    """Clear conversation history for the current admin."""
    user = require_role(authorization, roles=["admin"])
    _admin_ai_history.pop(user.employee_id, None)
    return {"cleared": True}
