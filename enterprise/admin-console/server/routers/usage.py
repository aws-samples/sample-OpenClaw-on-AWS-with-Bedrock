"""
Usage & Cost — Multi-dimension analytics with hierarchical budgets.

Budget inheritance: individual > department > global.
Model-aware pricing: cost calculated from actual model used (not hardcoded).

Endpoints: /api/v1/usage/*, /api/v1/dashboard
"""

import time as _time_usage
from datetime import datetime, timezone, date, timedelta
from fastapi import APIRouter, HTTPException, Header

import db
import s3ops
from shared import require_auth, require_role, get_dept_scope, DYNAMODB_REGION

router = APIRouter(tags=["usage"])


# =========================================================================
# Budget resolution — 3-level hierarchy
# =========================================================================

def _get_budgets() -> dict:
    """Load budget config from DynamoDB CONFIG#budgets.
    Supports both old flat format and new hierarchical format."""
    stored = db.get_config("budgets")
    if not stored:
        return {"global": 20.0, "departments": {}, "employees": {}}
    # New format has "global" key
    if "global" in stored:
        return {
            "global": float(stored.get("global", 20.0)),
            "departments": {k: float(v) for k, v in stored.get("departments", {}).items()},
            "employees": {k: float(v) for k, v in stored.get("employees", {}).items()},
        }
    # Old flat format: {"Engineering": 50, "Sales": 30, ...}
    return {
        "global": 20.0,
        "departments": {k: float(v) for k, v in stored.items() if k not in ("id", "global", "departments", "employees") and not k.startswith("_")},
        "employees": {},
    }


def resolve_budget(emp_id: str, department_name: str) -> float:
    """Resolve budget: individual > department > global."""
    budgets = _get_budgets()
    # Level 1: individual
    if emp_id and emp_id in budgets.get("employees", {}):
        return float(budgets["employees"][emp_id])
    # Level 2: department
    if department_name and department_name in budgets.get("departments", {}):
        return float(budgets["departments"][department_name])
    # Level 3: global
    return float(budgets.get("global", 20.0))


# =========================================================================
# Usage aggregation
# =========================================================================

def _range_dates(range_name: str) -> list[str]:
    """Return inclusive UTC calendar dates for a supported reporting range."""
    today = datetime.now(timezone.utc).date()
    if range_name == "1d":
        days = 1
    elif range_name == "30d":
        days = 30
    elif range_name == "mtd":
        days = today.day
    else:
        days = 7
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days)]


def _usage_records(range_name: str = "7d") -> list[dict]:
    """Read usage records for the selected UTC date range in one paginated query."""
    selected_dates = set(_range_dates(range_name))
    return [record for record in db.get_usage_by_date() if record.get("date") in selected_dates]


def _canonical_usage_id(stored_id: str, employee_agents: dict[str, str]) -> str:
    """Map legacy employee-keyed records to their canonical Agent ID."""
    return employee_agents.get(stored_id, stored_id)


def _employee_agent_map() -> dict[str, str]:
    return {
        employee["id"]: employee.get("agentId", "")
        for employee in db.get_employees()
        if employee.get("id") and employee.get("agentId")
    }


def _scope_agent_ids(user) -> set[str] | None:
    """Return visible Agent IDs, or None for unrestricted admins."""
    department_ids = get_dept_scope(user)
    if department_ids is None:
        return None
    employees = db.get_employees()
    employee_ids = {
        employee["id"] for employee in employees
        if employee.get("departmentId") in department_ids
    }
    position_ids = {
        position["id"] for position in db.get_positions()
        if position.get("departmentId") in department_ids
    }
    return {
        agent["id"] for agent in db.get_agents()
        if agent.get("id") and (
            agent.get("employeeId") in employee_ids
            if agent.get("employeeId")
            else agent.get("positionId") in position_ids
        )
    }


def _get_agent_usage_recent(
    range_name: str = "7d",
    allowed_agent_ids: set[str] | None = None,
) -> dict:
    """Aggregate usage per Agent for the selected reporting range."""
    employee_agents = _employee_agent_map()
    result = {}
    for record in _usage_records(range_name):
        raw_id = record.get("agentId", "")
        agent_id = _canonical_usage_id(raw_id, employee_agents)
        if not agent_id or (allowed_agent_ids is not None and agent_id not in allowed_agent_ids):
            continue
        usage = result.setdefault(agent_id, {
            "inputTokens": 0,
            "outputTokens": 0,
            "requests": 0,
            "cost": 0.0,
            "model": record.get("model", ""),
        })
        usage["inputTokens"] += record.get("inputTokens", 0)
        usage["outputTokens"] += record.get("outputTokens", 0)
        usage["requests"] += record.get("requests", 0)
        usage["cost"] += float(record.get("cost", 0))
        if record.get("model"):
            usage["model"] = record["model"]
    return result


# =========================================================================
# Dashboard
# =========================================================================

@router.get("/api/v1/dashboard")
def dashboard(authorization: str = Header(default="")):
    user = require_auth(authorization)
    scope = get_dept_scope(user)
    depts = db.get_departments()
    agents = db.get_agents()
    bindings = db.get_bindings()
    employees = db.get_employees()
    sessions = db.get_sessions()
    if scope is not None:
        depts = [d for d in depts if d["id"] in scope]
        employees = [e for e in employees if e.get("departmentId") in scope]
        emp_ids = {e["id"] for e in employees}
        positions = db.get_positions()
        pos_in_scope = {p["id"] for p in positions if p.get("departmentId") in scope}
        agents = [a for a in agents if a.get("positionId") in pos_in_scope or not a.get("employeeId")]
        bindings = [b for b in bindings if b.get("employeeId") in emp_ids]
        sessions = [s for s in sessions if s.get("employeeId") in emp_ids]
    return {
        "departments": len([d for d in depts if not d.get("parentId")]),
        "positions": len(db.get_positions() if scope is None else [p for p in db.get_positions() if p.get("departmentId") in scope]),
        "employees": len(employees),
        "agents": len(agents),
        "activeAgents": sum(1 for a in agents if a.get("status") == "active"),
        "bindings": sum(1 for b in bindings if b.get("status") == "active"),
        "sessions": len(sessions),
        "totalTurns": sum(s.get("turns", 0) for s in sessions),
        "unboundEmployees": sum(1 for e in employees if not e.get("agentId")),
    }


# =========================================================================
# Usage endpoints
# =========================================================================

def _usage_summary(report_range: str = "7d", allowed_agent_ids: set[str] | None = None):
    usage_map = _get_agent_usage_recent(report_range, allowed_agent_ids)
    total_input = sum(u["inputTokens"] for u in usage_map.values())
    total_output = sum(u["outputTokens"] for u in usage_map.values())
    total_cost = sum(u["cost"] for u in usage_map.values())
    total_requests = sum(u["requests"] for u in usage_map.values())
    return {
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCost": round(total_cost, 2),
        "totalRequests": total_requests,
        "tenantCount": sum(1 for usage in usage_map.values() if usage["requests"] > 0),
    }


@router.get("/api/v1/usage/summary")
def usage_summary(report_range: str = "7d", authorization: str = Header(default="")):
    user = require_auth(authorization)
    return _usage_summary(report_range, _scope_agent_ids(user))


def _usage_by_department(report_range: str = "7d", allowed_agent_ids: set[str] | None = None):
    agents = db.get_agents()
    if allowed_agent_ids is not None:
        agents = [agent for agent in agents if agent.get("id") in allowed_agent_ids]
    positions = db.get_positions()
    usage_map = _get_agent_usage_recent(report_range, allowed_agent_ids)
    pos_to_dept = {p["id"]: p.get("departmentName", "Unknown") for p in positions}
    dept_usage: dict = {}
    for agent in agents:
        dept = pos_to_dept.get(agent.get("positionId", ""), "Unknown")
        usage = usage_map.get(agent["id"], {"inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0})
        if dept not in dept_usage:
            dept_usage[dept] = {"department": dept, "inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0, "agents": 0}
        dept_usage[dept]["inputTokens"] += usage["inputTokens"]
        dept_usage[dept]["outputTokens"] += usage["outputTokens"]
        dept_usage[dept]["requests"] += usage["requests"]
        dept_usage[dept]["cost"] += usage["cost"]
        dept_usage[dept]["agents"] += 1
    result = sorted(dept_usage.values(), key=lambda x: x["cost"], reverse=True)
    for r in result:
        r["cost"] = round(r["cost"], 2)
    return result


@router.get("/api/v1/usage/by-department")
def usage_by_department(report_range: str = "7d", authorization: str = Header(default="")):
    user = require_auth(authorization)
    return _usage_by_department(report_range, _scope_agent_ids(user))


def _usage_by_agent(report_range: str = "7d", allowed_agent_ids: set[str] | None = None):
    agents = db.get_agents()
    if allowed_agent_ids is not None:
        agents = [agent for agent in agents if agent.get("id") in allowed_agent_ids]
    usage_map = _get_agent_usage_recent(report_range, allowed_agent_ids)
    result = []
    for agent in agents:
        if not agent.get("id"):
            continue
        usage = usage_map.get(agent["id"], {"inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0, "model": ""})
        result.append({
            "agentId": agent["id"],
            "agentName": agent.get("name", agent["id"]),
            "employeeName": agent.get("employeeName", ""),
            "positionName": agent.get("positionName", ""),
            "deployMode": agent.get("deployMode", "serverless"),
            **usage,
        })
    return sorted(result, key=lambda x: x["cost"], reverse=True)


@router.get("/api/v1/usage/by-agent")
def usage_by_agent(report_range: str = "7d", authorization: str = Header(default="")):
    user = require_auth(authorization)
    return _usage_by_agent(report_range, _scope_agent_ids(user))


_model_usage_cache: dict[str, dict] = {}


def _usage_by_model(report_range: str = "7d", allowed_agent_ids: set[str] | None = None):
    """Aggregate usage by model for the selected reporting range."""
    cache_range = report_range if report_range in {"1d", "7d", "30d", "mtd"} else "7d"
    cache_key = f"{cache_range}:{','.join(sorted(allowed_agent_ids)) if allowed_agent_ids is not None else '*'}"
    cached = _model_usage_cache.get(cache_key)
    if cached and _time_usage.time() < cached["expires"]:
        return cached["data"]

    employee_agents = _employee_agent_map()
    model_usage: dict = {}
    for record in _usage_records(cache_range):
        agent_id = _canonical_usage_id(record.get("agentId", ""), employee_agents)
        if allowed_agent_ids is not None and agent_id not in allowed_agent_ids:
            continue
        model = record.get("model", "") or "unknown"
        if model not in model_usage:
            model_usage[model] = {"model": model, "inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0}
        model_usage[model]["inputTokens"] += record.get("inputTokens", 0)
        model_usage[model]["outputTokens"] += record.get("outputTokens", 0)
        model_usage[model]["requests"] += record.get("requests", 0)
        model_usage[model]["cost"] += float(record.get("cost", 0))
    result = sorted(model_usage.values(), key=lambda x: x["cost"], reverse=True)
    for item in result:
        item["cost"] = round(item["cost"], 4)
    _model_usage_cache[cache_key] = {"data": result, "expires": _time_usage.time() + 300}
    return result


@router.get("/api/v1/usage/by-model")
def usage_by_model(report_range: str = "7d", authorization: str = Header(default="")):
    user = require_auth(authorization)
    return _usage_by_model(report_range, _scope_agent_ids(user))


@router.get("/api/v1/usage/agent/{agent_id}")
def usage_for_agent(agent_id: str, authorization: str = Header(default="")):
    user = require_auth(authorization)
    allowed_agent_ids = _scope_agent_ids(user)
    if allowed_agent_ids is not None and agent_id not in allowed_agent_ids:
        raise HTTPException(403, "Agent is outside your department scope")
    records = db.get_usage_for_agent(agent_id)
    records.sort(key=lambda x: x.get("date", ""))
    return [{
        "date": r.get("date"),
        "inputTokens": r.get("inputTokens", 0),
        "outputTokens": r.get("outputTokens", 0),
        "requests": r.get("requests", 0),
        "cost": float(r.get("cost", 0)),
    } for r in records]


def _usage_trend(report_range: str = "7d", allowed_agent_ids: set[str] | None = None):
    """Daily cost and request trend for the selected reporting range."""
    try:
        dates = list(reversed(_range_dates(report_range)))
        daily_costs = {day: 0.0 for day in dates}
        daily_requests = {day: 0 for day in dates}
        employee_agents = _employee_agent_map()
        for item in _usage_records(report_range):
            agent_id = _canonical_usage_id(item.get("agentId", ""), employee_agents)
            if allowed_agent_ids is not None and agent_id not in allowed_agent_ids:
                continue
            record_date = item.get("date", "")
            if record_date in daily_costs:
                daily_costs[record_date] += float(item.get("cost", 0))
                daily_requests[record_date] += int(item.get("requests", 0))
        return [{
            "date": day,
            "openclawCost": round(daily_costs[day], 4),
            "totalRequests": daily_requests[day],
        } for day in dates]
    except Exception:
        return []


@router.get("/api/v1/usage/trend")
def usage_trend(report_range: str = "7d", authorization: str = Header(default="")):
    user = require_auth(authorization)
    return _usage_trend(report_range, _scope_agent_ids(user))


def _skill_usage_records(range_name: str = "7d") -> list[dict]:
    selected_dates = set(_range_dates(range_name))
    return [record for record in db.get_skill_usage() if record.get("date") in selected_dates]


def _skill_record_calls(record: dict) -> int:
    """Combine disjoint legacy and current counters during rolling upgrades."""
    return int(record.get("calls", 0)) + int(record.get("activations", 0))


def _current_skill_ids() -> set[str]:
    try:
        return {
            item.get("name", "").split("/")[0]
            for item in s3ops.list_files("_shared/skills/")
            if item.get("name") and "/" in item["name"]
        }
    except Exception:
        return set()


def _usage_by_skill(report_range: str = "7d", allowed_agent_ids: set[str] | None = None):
    """Aggregate per-turn Skill calls; no static Skill list is required."""
    current_skills = _current_skill_ids()

    def empty_item(skill_id: str) -> dict:
        return {
            "skillId": skill_id,
            "status": "installed" if skill_id in current_skills else "removed",
            "calls": 0,
            "activeUsers": set(),
            "serverlessCalls": 0,
            "alwaysOnCalls": 0,
            "lastUsedAt": "",
        }

    aggregated: dict[str, dict] = {skill_id: empty_item(skill_id) for skill_id in current_skills}
    for record in _skill_usage_records(report_range):
        agent_id = record.get("agentId", "")
        if allowed_agent_ids is not None and agent_id not in allowed_agent_ids:
            continue
        skill_id = record.get("skillId", "")
        if not skill_id:
            continue
        item = aggregated.setdefault(skill_id, empty_item(skill_id))
        calls = _skill_record_calls(record)
        item["calls"] += calls
        employee_id = record.get("employeeId")
        if employee_id:
            item["activeUsers"].add(employee_id)
        if record.get("agentType") == "always-on":
            item["alwaysOnCalls"] += calls
        else:
            item["serverlessCalls"] += calls
        used_at = record.get("lastUsedAt", record.get("lastActivatedAt", ""))
        item["lastUsedAt"] = max(item["lastUsedAt"], used_at)

    result = []
    for item in aggregated.values():
        item["activeUsers"] = len(item["activeUsers"])
        # Temporary aliases keep older console clients compatible.
        item["activations"] = item["calls"]
        item["serverlessActivations"] = item["serverlessCalls"]
        item["alwaysOnActivations"] = item["alwaysOnCalls"]
        item["lastActivatedAt"] = item["lastUsedAt"]
        result.append(item)
    return sorted(result, key=lambda value: value["calls"], reverse=True)


@router.get("/api/v1/usage/by-skill")
def usage_by_skill(report_range: str = "7d", authorization: str = Header(default="")):
    user = require_role(authorization, roles=["admin", "manager"])
    return _usage_by_skill(report_range, _scope_agent_ids(user))


@router.get("/api/v1/usage/skill/{skill_id}/users")
def usage_skill_users(
    skill_id: str,
    report_range: str = "7d",
    authorization: str = Header(default=""),
):
    """Return per-user call counts for one dynamically discovered Skill."""
    user = require_role(authorization, roles=["admin", "manager"])
    allowed_agent_ids = _scope_agent_ids(user)
    employees = {item["id"]: item for item in db.get_employees() if item.get("id")}
    totals: dict[str, dict] = {}
    for record in _skill_usage_records(report_range):
        if record.get("skillId") != skill_id:
            continue
        agent_id = record.get("agentId", "")
        if allowed_agent_ids is not None and agent_id not in allowed_agent_ids:
            continue
        employee_id = record.get("employeeId", "")
        if not employee_id:
            continue
        employee = employees.get(employee_id, {})
        item = totals.setdefault(employee_id, {
            "employeeId": employee_id,
            "employeeName": employee.get("name", employee_id),
            "departmentName": employee.get("departmentName", ""),
            "calls": 0,
            "serverlessCalls": 0,
            "alwaysOnCalls": 0,
            "lastUsedAt": "",
        })
        calls = _skill_record_calls(record)
        item["calls"] += calls
        mode_key = "alwaysOnCalls" if record.get("agentType") == "always-on" else "serverlessCalls"
        item[mode_key] += calls
        used_at = record.get("lastUsedAt", record.get("lastActivatedAt", ""))
        item["lastUsedAt"] = max(item["lastUsedAt"], used_at)
    result = []
    for item in totals.values():
        item["activations"] = item["calls"]
        item["serverlessActivations"] = item["serverlessCalls"]
        item["alwaysOnActivations"] = item["alwaysOnCalls"]
        item["lastActivatedAt"] = item["lastUsedAt"]
        result.append(item)
    return sorted(result, key=lambda value: value["calls"], reverse=True)


# =========================================================================
# Budgets — hierarchical
# =========================================================================

def _usage_budgets(report_range: str = "7d", allowed_agent_ids: set[str] | None = None):
    """Department budget tracking with an active-day monthly projection."""
    dept_usage = _usage_by_department(report_range, allowed_agent_ids)
    agents = db.get_agents()
    if allowed_agent_ids is not None:
        agents = [agent for agent in agents if agent.get("id") in allowed_agent_ids]
    positions = db.get_positions()
    pos_to_dept = {p["id"]: p.get("departmentName", "Unknown") for p in positions}
    employee_agents = _employee_agent_map()
    records = _usage_records(report_range)

    result = []
    for dept in dept_usage:
        dept_name = dept["department"]
        budget = resolve_budget("", dept_name)

        # Active-day average projection based on the selected range.
        dept_agents = {a["id"] for a in agents
                       if pos_to_dept.get(a.get("positionId", ""), "") == dept_name}
        daily_costs = []
        for day in _range_dates(report_range):
            day_cost = sum(
                float(record.get("cost", 0))
                for record in records
                if record.get("date") == day
                and _canonical_usage_id(record.get("agentId", ""), employee_agents) in dept_agents
            )
            daily_costs.append(day_cost)
        active_days = len([c for c in daily_costs if c > 0]) or 1
        avg_daily = sum(daily_costs) / active_days
        projected = round(avg_daily * 30, 2)

        result.append({
            "department": dept_name,
            "budget": budget,
            "used": round(dept["cost"], 2),
            "projected": projected,
            "status": "over" if projected > budget else "warning" if projected > budget * 0.8 else "ok",
            "agents": dept["agents"],
        })
    return result


@router.get("/api/v1/usage/budgets")
def usage_budgets(report_range: str = "7d", authorization: str = Header(default="")):
    """Return scoped budgets using the selected range's active-day average."""
    user = require_auth(authorization)
    return _usage_budgets(report_range, _scope_agent_ids(user))


@router.put("/api/v1/usage/budgets")
def update_budgets(body: dict, authorization: str = Header(default="")):
    """Save hierarchical budget config. Admin only."""
    user = require_role(authorization, roles=["admin"])
    if "global" in body or "departments" in body or "employees" in body:
        existing = _get_budgets()
        config = {
            "global": float(body.get("global", existing["global"])),
            "departments": {
                **existing.get("departments", {}),
                **{k: float(v) for k, v in body.get("departments", {}).items()},
            },
            "employees": {
                **existing.get("employees", {}),
                **{k: float(v) for k, v in body.get("employees", {}).items()},
            },
        }
    else:
        config = {
            "global": 20.0,
            "departments": {k: float(v) for k, v in body.items() if k not in ("global", "employees", "departments") and not k.startswith("_")},
            "employees": {},
        }
    db.set_config("budgets", config)
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "budget",
        "targetId": "all",
        "detail": f"Budget updated: global=${config['global']}, {len(config['departments'])} depts, {len(config['employees'])} individuals",
        "status": "success",
    })
    return config


# =========================================================================
# Employee / Manager budget views
# =========================================================================

@router.get("/api/v1/usage/my-budget")
def my_budget(authorization: str = Header(default="")):
    """Employee sees own budget + usage."""
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    if not emp:
        raise HTTPException(404, "Employee not found")
    dept_name = emp.get("departmentName", "")
    budget = resolve_budget(user.employee_id, dept_name)
    agent_id = emp.get("agentId", "")
    usage_records = db.get_usage_for_agent(agent_id) if agent_id else []
    total_cost = sum(float(u.get("cost", 0)) for u in usage_records)
    total_requests = sum(u.get("requests", 0) for u in usage_records)
    budgets = _get_budgets()
    source = ("individual" if user.employee_id in budgets.get("employees", {})
              else "department" if dept_name in budgets.get("departments", {})
              else "global")
    return {
        "budget": budget,
        "budgetSource": source,
        "used": round(total_cost, 4),
        "remaining": round(max(0, budget - total_cost), 4),
        "requests": total_requests,
        "percentUsed": round(total_cost / max(budget, 0.01) * 100, 1),
    }


@router.get("/api/v1/usage/department-budget")
def department_budget(authorization: str = Header(default="")):
    """Manager sees own department budget + member usage."""
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    if not emp:
        raise HTTPException(404)
    dept_name = emp.get("departmentName", "")
    dept_budget = resolve_budget("", dept_name)
    employees = db.get_employees()
    dept_emps = [e for e in employees if e.get("departmentName") == dept_name]
    usage_map = _get_agent_usage_recent()
    members = []
    total_dept_cost = 0
    for e in dept_emps:
        agent_id = e.get("agentId", "")
        usage = usage_map.get(agent_id, {"cost": 0, "requests": 0})
        cost = float(usage.get("cost", 0))
        total_dept_cost += cost
        individual_budget = resolve_budget(e["id"], dept_name)
        members.append({
            "employeeId": e["id"],
            "name": e["name"],
            "positionName": e.get("positionName", ""),
            "cost": round(cost, 4),
            "requests": usage.get("requests", 0),
            "budget": individual_budget,
            "percentUsed": round(cost / max(individual_budget, 0.01) * 100, 1),
        })
    members.sort(key=lambda x: x["cost"], reverse=True)
    return {
        "department": dept_name,
        "budget": dept_budget,
        "totalUsed": round(total_dept_cost, 2),
        "memberCount": len(dept_emps),
        "members": members,
    }


# ── Fargate cost estimation ─────────────────────────────────────────────

# Fargate ARM64 pricing (us-east-2 / ap-northeast-1, approximate)
_FARGATE_HOURLY = {
    "256/512": 0.01011,    # 0.25 vCPU / 512 MB
    "512/1024": 0.02228,   # 0.5 vCPU / 1 GB
    "1024/2048": 0.04456,  # 1 vCPU / 2 GB
}

@router.get("/api/v1/usage/fargate-cost")
def usage_fargate_cost(authorization=Header(default="")):
    """Estimated Fargate infrastructure cost for all always-on employees."""
    from shared import require_role
    require_role(authorization, roles=["admin"])

    employees = db.get_employees()
    agents = db.get_agents()
    agent_map = {a["id"]: a for a in agents}

    items = []
    total = 0.0
    for emp in employees:
        if not emp.get("alwaysOnEnabled"):
            continue
        tier = emp.get("alwaysOnTier", "standard")
        # Estimate based on tier default resources
        if tier in ("standard", "restricted"):
            hourly = _FARGATE_HOURLY.get("256/512", 0.01011)
            resource = "0.25 vCPU / 512 MB"
        else:
            hourly = _FARGATE_HOURLY.get("512/1024", 0.02228)
            resource = "0.5 vCPU / 1 GB"

        monthly = round(hourly * 24 * 30, 2)
        total += monthly
        items.append({
            "employeeId": emp["id"],
            "employeeName": emp.get("name", ""),
            "tier": tier,
            "resource": resource,
            "monthlyEstimate": monthly,
        })

    return {
        "items": items,
        "totalMonthly": round(total, 2),
        "count": len(items),
    }
