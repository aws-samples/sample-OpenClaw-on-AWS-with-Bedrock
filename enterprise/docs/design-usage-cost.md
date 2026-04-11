# Design: Usage & Cost — Code Changes

**Date:** 2026-04-12
**Prereq:** PRD-usage-cost.md, usage.py (312 lines), server.py (agent container)

---

## File-by-File Change Design

### 1. usage.py — 10 changes

#### 1.1 Remove ChatGPT comparison

```
DELETE: line 115 — chatgpt_daily = len([...]) * 0.83
DELETE: line 122 — "chatgptEquivalent" from usage_summary return
DELETE: lines 226-227 — chatgpt_daily in usage_trend
DELETE: line 261 — "chatgptEquivalent": chatgpt_daily in trend
DELETE: line 274 — chatgptEquivalent in seed trend fallback
```

#### 1.2 Fix unknown model

```
MODIFY: usage_by_model() lines 182-183

Before:
    if model == "unknown" or not model:
        model = "global.amazon.nova-2-lite-v1:0"

After:
    if not model:
        model = "unknown"
```

#### 1.3 Remove seed date fallback

```
DELETE: usage_by_model() lines 190-199

    if not model_usage:
        records = db.get_usage_by_date("2026-03-20")
        ...
```

#### 1.4 Budget projection: 7-day average

```
MODIFY: usage_budgets() line 289

Before:
    projected = used * 30

After:
    from datetime import date as _date_b, timedelta as _td_b
    daily_costs = []
    for offset in range(7):
        d = (_date_b.today() - _td_b(days=offset)).isoformat()
        day_records = db.get_usage_by_date(d)
        dept_day_cost = sum(
            float(u.get("cost", 0)) for u in day_records
            if u.get("agentId") in dept_agent_ids
        )
        daily_costs.append(dept_day_cost)
    active_days = len([c for c in daily_costs if c > 0]) or 1
    avg_daily = sum(daily_costs) / active_days
    projected = round(avg_daily * 30, 2)
```

Note: need to compute `dept_agent_ids` before the loop — set of agent IDs in this department.

#### 1.5 Rename function

```
RENAME: _get_agent_usage_today → _get_agent_usage_recent
UPDATE all callers: usage_summary, usage_by_department, usage_by_agent, monitor.py import
```

#### 1.6 Hierarchical budget resolution

```python
def resolve_budget(emp_id: str, department_name: str) -> float:
    """Resolve budget for an employee: individual > department > global."""
    budgets = _get_budgets()
    # Level 1: individual
    emp_budgets = budgets.get("employees", {})
    if emp_id in emp_budgets:
        return float(emp_budgets[emp_id])
    # Level 2: department
    dept_budgets = budgets.get("departments", budgets)  # backward compat: old format is flat
    if department_name in dept_budgets:
        return float(dept_budgets[department_name])
    # Level 3: global
    return float(budgets.get("global", 20.0))
```

#### 1.7 Enhanced usage_budgets() with hierarchical resolution

```python
@router.get("/api/v1/usage/budgets")
def usage_budgets():
    dept_usage = usage_by_department()
    agents = db.get_agents()
    employees = db.get_employees()
    positions = db.get_positions()
    pos_to_dept = {p["id"]: p.get("departmentName", "Unknown") for p in positions}

    result = []
    for dept in dept_usage:
        dept_name = dept["department"]
        budget = resolve_budget("", dept_name)  # department-level

        # 7-day average projection
        from datetime import date as _date_b, timedelta as _td_b
        dept_agents = {a["id"] for a in agents
                       if pos_to_dept.get(a.get("positionId",""),"") == dept_name}
        daily_costs = []
        for offset in range(7):
            d = (_date_b.today() - _td_b(days=offset)).isoformat()
            day_records = db.get_usage_by_date(d)
            day_cost = sum(float(u.get("cost",0)) for u in day_records if u.get("agentId") in dept_agents)
            daily_costs.append(day_cost)
        active_days = len([c for c in daily_costs if c > 0]) or 1
        projected = round(sum(daily_costs) / active_days * 30, 2)

        result.append({
            "department": dept_name,
            "budget": budget,
            "used": round(dept["cost"], 2),
            "projected": projected,
            "status": "over" if projected > budget else "warning" if projected > budget * 0.8 else "ok",
            "agents": dept["agents"],
        })
    return result
```

#### 1.8 Enhanced update_budgets() for new schema

```python
@router.put("/api/v1/usage/budgets")
def update_budgets(body: dict, authorization: str = Header(default="")):
    """Save hierarchical budget config. Admin only."""
    user = require_role(authorization, roles=["admin"])
    # Accept both old format {"Engineering": 50} and new format {"global":20, "departments":{}, "employees":{}}
    if "global" in body or "departments" in body or "employees" in body:
        config = {
            "global": float(body.get("global", 20.0)),
            "departments": {k: float(v) for k, v in body.get("departments", {}).items()},
            "employees": {k: float(v) for k, v in body.get("employees", {}).items()},
        }
    else:
        # Old flat format — treat as departments
        config = {
            "global": 20.0,
            "departments": {k: float(v) for k, v in body.items() if k not in ("global","employees","departments")},
            "employees": {},
        }
    db.set_config("budgets", config)
    # Audit
    db.create_audit_entry({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "config_change",
        "actorId": user.employee_id,
        "actorName": user.name,
        "targetType": "budget",
        "targetId": "all",
        "detail": f"Budget config updated: global=${config['global']}, {len(config['departments'])} depts, {len(config['employees'])} individuals",
        "status": "success",
    })
    return config
```

#### 1.9 New: my-budget endpoint (employee portal)

```python
@router.get("/api/v1/usage/my-budget")
def my_budget(authorization: str = Header(default="")):
    """Employee sees own budget + usage."""
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    if not emp:
        raise HTTPException(404, "Employee not found")

    dept_name = emp.get("departmentName", "")
    budget = resolve_budget(user.employee_id, dept_name)

    # Get personal usage
    agent_id = emp.get("agentId", "")
    usage_records = db.get_usage_for_agent(agent_id) if agent_id else []
    total_cost = sum(float(u.get("cost", 0)) for u in usage_records)
    total_requests = sum(u.get("requests", 0) for u in usage_records)

    return {
        "budget": budget,
        "budgetSource": "individual" if user.employee_id in (_get_budgets().get("employees", {}))
                        else "department" if dept_name in (_get_budgets().get("departments", _get_budgets()))
                        else "global",
        "used": round(total_cost, 4),
        "remaining": round(max(0, budget - total_cost), 4),
        "requests": total_requests,
        "percentUsed": round(total_cost / max(budget, 0.01) * 100, 1),
    }
```

#### 1.10 New: department-budget endpoint (manager view)

```python
@router.get("/api/v1/usage/department-budget")
def department_budget(authorization: str = Header(default="")):
    """Manager sees own department budget + member usage."""
    user = require_auth(authorization)
    emp = next((e for e in db.get_employees() if e["id"] == user.employee_id), None)
    if not emp:
        raise HTTPException(404)

    dept_name = emp.get("departmentName", "")
    dept_budget = resolve_budget("", dept_name)

    # Get all employees in department
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
```

#### 1.11 by-model cache

```python
import time as _time_usage
_model_usage_cache = {"data": None, "expires": 0}

# In usage_by_model():
if _model_usage_cache["data"] and _time_usage.time() < _model_usage_cache["expires"]:
    return _model_usage_cache["data"]
# ... existing logic ...
_model_usage_cache["data"] = result
_model_usage_cache["expires"] = _time_usage.time() + 300
return result
```

### 2. server.py (agent container) — model-aware pricing

```
FIND: _write_usage_to_dynamodb() or cost calculation section

ADD at module level:
MODEL_PRICING = {
    "global.amazon.nova-2-lite-v1:0":           {"input": 0.30, "output": 2.50},
    "us.amazon.nova-pro-v1:0":                  {"input": 0.80, "output": 3.20},
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0": {"input": 3.00, "output": 15.00},
    "global.anthropic.claude-sonnet-4-6":        {"input": 3.00, "output": 15.00},
    "global.anthropic.claude-opus-4-6-v1":       {"input": 15.00, "output": 75.00},
    "global.anthropic.claude-opus-4-5-20251101-v1:0": {"input": 15.00, "output": 75.00},
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 0.80, "output": 4.00},
    "us.deepseek.r1-v1:0":                      {"input": 1.35, "output": 5.40},
    "us.meta.llama3-3-70b-instruct-v1:0":       {"input": 0.72, "output": 0.72},
    "moonshotai.kimi-k2.5":                      {"input": 0.60, "output": 3.00},
}

MODIFY cost calculation:
    model_id = os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
    pricing = MODEL_PRICING.get(model_id, {"input": 0.30, "output": 2.50})
    cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
```

### 3. monitor.py — update import

```
MODIFY: from routers.usage import usage_budgets, _get_agent_usage_today
TO:     from routers.usage import usage_budgets, _get_agent_usage_recent
```

---

## Unit Test Plan

```
test_usage_cost.py:

1. test_no_chatgpt:
   Scan usage.py for "chatgpt" (case insensitive) → must NOT exist
   Scan for "0.83" → must NOT exist

2. test_unknown_model_stays_unknown:
   Scan usage_by_model for default to "nova-2-lite" when unknown → must NOT exist

3. test_no_seed_date_fallback:
   Scan usage.py for "2026-03-20" → must NOT exist

4. test_budget_projection_7day:
   Scan usage_budgets for "* 30" simple multiplication → must NOT exist as sole projection
   Scan for "7" or "daily" or "average" in budget context → must exist

5. test_function_renamed:
   Scan for "def _get_agent_usage_recent" → must exist
   Scan for "def _get_agent_usage_today" → must NOT exist

6. test_hierarchical_budget:
   Scan for "def resolve_budget" → must exist
   Scan for "global" and "employees" in resolve_budget → must exist

7. test_my_budget_exists:
   Scan for "def my_budget" → must exist

8. test_department_budget_exists:
   Scan for "def department_budget" → must exist

9. test_budget_update_has_audit:
   Scan update_budgets for "create_audit_entry" → must exist

10. test_model_cache:
    Scan for "_model_usage_cache" → must exist

11. test_model_pricing_in_server:
    server_path = agent-container/server.py
    Scan for "MODEL_PRICING" → must exist
```

---

## Migration Notes

- CONFIG#budgets schema change: old flat format `{"Engineering": 50}` still works via backward compat in resolve_budget and update_budgets
- server.py MODEL_PRICING: requires Docker rebuild
- monitor.py import rename: already deployed together
- No DynamoDB schema changes
- Seed data: update seed_settings.py to write new budget format if needed
