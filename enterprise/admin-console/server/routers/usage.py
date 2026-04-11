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

def _get_agent_usage_recent() -> dict:
    """Aggregate recent usage per agent from DynamoDB USAGE# records (last 7 days)."""
    today = date.today().isoformat()
    all_usage = db.get_usage_by_date(today)
    for offset in range(1, 7):
        past = (date.today() - timedelta(days=offset)).isoformat()
        past_usage = db.get_usage_by_date(past)
        for u in past_usage:
            aid = u.get("agentId", "")
            if aid and aid not in {uu.get("agentId") for uu in all_usage}:
                all_usage.append(u)
    result = {}
    for u in all_usage:
        aid = u.get("agentId", "")
        if not aid:
            continue
        if aid in result:
            result[aid]["inputTokens"] += u.get("inputTokens", 0)
            result[aid]["outputTokens"] += u.get("outputTokens", 0)
            result[aid]["requests"] += u.get("requests", 0)
            result[aid]["cost"] += float(u.get("cost", 0))
        else:
            result[aid] = {
                "inputTokens": u.get("inputTokens", 0),
                "outputTokens": u.get("outputTokens", 0),
                "requests": u.get("requests", 0),
                "cost": float(u.get("cost", 0)),
                "model": u.get("model", ""),
            }
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

@router.get("/api/v1/usage/summary")
def usage_summary():
    usage_map = _get_agent_usage_recent()
    total_input = sum(u["inputTokens"] for u in usage_map.values())
    total_output = sum(u["outputTokens"] for u in usage_map.values())
    total_cost = sum(u["cost"] for u in usage_map.values())
    total_requests = sum(u["requests"] for u in usage_map.values())
    employees = db.get_employees()
    return {
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCost": round(total_cost, 2),
        "totalRequests": total_requests,
        "tenantCount": len([e for e in employees if e.get("agentId")]),
    }


@router.get("/api/v1/usage/by-department")
def usage_by_department():
    agents = db.get_agents()
    positions = db.get_positions()
    usage_map = _get_agent_usage_recent()
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


@router.get("/api/v1/usage/by-agent")
def usage_by_agent():
    agents = db.get_agents()
    usage_map = _get_agent_usage_recent()
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
            **usage,
        })
    return sorted(result, key=lambda x: x["cost"], reverse=True)


_model_usage_cache = {"data": None, "expires": 0}

@router.get("/api/v1/usage/by-model")
def usage_by_model():
    """Aggregate usage by model (7-day window, cached 5 min)."""
    if _model_usage_cache["data"] and _time_usage.time() < _model_usage_cache["expires"]:
        return _model_usage_cache["data"]

    model_usage: dict = {}
    for offset in range(7):
        d = (date.today() - timedelta(days=offset)).isoformat()
        records = db.get_usage_by_date(d)
        for u in records:
            model = u.get("model", "") or "unknown"
            if model not in model_usage:
                model_usage[model] = {"model": model, "inputTokens": 0, "outputTokens": 0, "requests": 0, "cost": 0}
            model_usage[model]["inputTokens"] += u.get("inputTokens", 0)
            model_usage[model]["outputTokens"] += u.get("outputTokens", 0)
            model_usage[model]["requests"] += u.get("requests", 0)
            model_usage[model]["cost"] += float(u.get("cost", 0))
    result = sorted(model_usage.values(), key=lambda x: x["cost"], reverse=True)
    for r in result:
        r["cost"] = round(r["cost"], 4)
    _model_usage_cache["data"] = result
    _model_usage_cache["expires"] = _time_usage.time() + 300
    return result


@router.get("/api/v1/usage/agent/{agent_id}")
def usage_for_agent(agent_id: str):
    records = db.get_usage_for_agent(agent_id)
    records.sort(key=lambda x: x.get("date", ""))
    return [{
        "date": r.get("date"),
        "inputTokens": r.get("inputTokens", 0),
        "outputTokens": r.get("outputTokens", 0),
        "requests": r.get("requests", 0),
        "cost": float(r.get("cost", 0)),
    } for r in records]


@router.get("/api/v1/usage/trend")
def usage_trend():
    """7-day cost trend from DynamoDB USAGE# records."""
    try:
        import boto3 as _b3tr
        ddb = _b3tr.resource("dynamodb", region_name=db.AWS_REGION)
        table = ddb.Table(db.TABLE_NAME)
        now = datetime.now(timezone.utc)
        daily_costs: dict = {}
        daily_requests: dict = {}
        for i in range(7):
            date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_costs[date_str] = 0.0
            daily_requests[date_str] = 0

        from boto3.dynamodb.conditions import Key
        resp = table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq("TYPE#usage") & Key("GSI1SK").begins_with("USAGE#"),
            Limit=500)
        for item in resp.get("Items", []):
            d = item.get("date", "")
            if d in daily_costs:
                daily_costs[d] += float(item.get("cost", 0))
                daily_requests[d] += int(item.get("requests", 0))

        trend = []
        for i in range(6, -1, -1):
            date_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            trend.append({
                "date": date_str,
                "cost": round(daily_costs.get(date_str, 0), 4),
                "requests": daily_requests.get(date_str, 0),
                "source": "real" if any(v > 0 for v in daily_costs.values()) else "empty",
            })
        return trend
    except Exception:
        return []


# =========================================================================
# Budgets — hierarchical
# =========================================================================

@router.get("/api/v1/usage/budgets")
def usage_budgets():
    """Department budget tracking with 7-day average projection."""
    dept_usage = usage_by_department()
    agents = db.get_agents()
    positions = db.get_positions()
    pos_to_dept = {p["id"]: p.get("departmentName", "Unknown") for p in positions}

    result = []
    for dept in dept_usage:
        dept_name = dept["department"]
        budget = resolve_budget("", dept_name)

        # 7-day average projection
        dept_agents = {a["id"] for a in agents
                       if pos_to_dept.get(a.get("positionId", ""), "") == dept_name}
        daily_costs = []
        for offset in range(7):
            d = (date.today() - timedelta(days=offset)).isoformat()
            day_records = db.get_usage_by_date(d)
            day_cost = sum(float(u.get("cost", 0)) for u in day_records if u.get("agentId") in dept_agents)
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


@router.put("/api/v1/usage/budgets")
def update_budgets(body: dict, authorization: str = Header(default="")):
    """Save hierarchical budget config. Admin only."""
    user = require_role(authorization, roles=["admin"])
    if "global" in body or "departments" in body or "employees" in body:
        config = {
            "global": float(body.get("global", 20.0)),
            "departments": {k: float(v) for k, v in body.get("departments", {}).items()},
            "employees": {k: float(v) for k, v in body.get("employees", {}).items()},
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
