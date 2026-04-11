# PRD: Usage & Cost Module

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Priority:** P0 — Cost data accuracy is the foundation of budget management

---

## 1. Problem Statement

The entire cost tracking system is based on hardcoded Nova Lite pricing ($0.30/$2.50 per 1M tokens). When admin assigns Claude Sonnet ($3/$15) or Claude Opus ($15/$75) to executive positions, the cost shown is 6-30x too low. Budget tracking, department comparisons, and ROI calculations are all wrong.

Additionally:
- ChatGPT comparison ($0.83/day) is outdated and should be removed
- Budget system is flat (department-only), needs hierarchical budgets (global → department → individual)
- Manager/employee cannot see their own budget context
- Multiple hardcoded values and missing auth on endpoints

---

## 2. Solutions

### 2.1 Model-Aware Pricing (P0)

**Problem:** server.py (agent container) writes USAGE# with `cost = input_tokens * 0.30/1M + output_tokens * 2.50/1M` regardless of actual model.

**Solution:** Model pricing table + server.py reads actual model ID when calculating cost.

```python
# Pricing table — in server.py or loaded from DynamoDB CONFIG#model-pricing
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

# In _write_usage_to_dynamodb():
model_id = os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
pricing = MODEL_PRICING.get(model_id, {"input": 0.30, "output": 2.50})
cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
```

**Change location:** `enterprise/agent-container/server.py` — requires Docker rebuild.

### 2.2 Remove ChatGPT Comparison

**Delete from:**
- `usage.py:115` — `chatgpt_daily = len([...]) * 0.83`
- `usage.py:122` — `"chatgptEquivalent"` in summary response
- `usage.py:227,261,274` — `chatgptEquivalent` in trend response
- Frontend: remove ChatGPT comparison bar in trend chart

### 2.3 Hierarchical Budget System

**Current:** `CONFIG#budgets` = flat dict `{"Engineering": 50.0, ...}`

**New:** Three-level budget with inheritance:

```python
# DynamoDB CONFIG#budgets (new schema)
{
    "global": 20.0,                    # default for everyone
    "departments": {
        "Engineering": 50.0,
        "Platform Team": 20.0,
        "Sales": 30.0,
        "Finance": 20.0,
        # ... departments without entry → use global
    },
    "employees": {
        "emp-peter": 200.0,           # CEO needs more
        "emp-jiade": 100.0,           # SA needs more
        # ... employees without entry → use department budget
    }
}

# Resolution:
def resolve_budget(emp_id, department_name):
    budgets = _get_budgets()
    # Level 1: individual
    if emp_id in budgets.get("employees", {}):
        return budgets["employees"][emp_id]
    # Level 2: department
    if department_name in budgets.get("departments", {}):
        return budgets["departments"][department_name]
    # Level 3: global
    return budgets.get("global", 20.0)
```

**Who sees what:**

| Role | View | Edit |
|------|------|------|
| Admin | All budgets + all usage | Set global, department, individual budgets |
| Manager | Own department total + member usage | — |
| Employee | Own usage + own budget limit | — |

**New endpoints:**
```
GET  /api/v1/usage/my-budget          → employee sees own budget + usage
PUT  /api/v1/usage/budgets            → admin sets all budgets (existing, enhanced schema)
GET  /api/v1/usage/department-budget   → manager sees department budget + members
```

### 2.4 Fix Unknown Model Default

```python
# usage.py:182-183
# Before: if model == "unknown": model = "global.amazon.nova-2-lite-v1:0"
# After: keep "unknown" as-is — don't inflate Nova Lite stats
if not model:
    model = "unknown"
```

### 2.5 Budget Projection: 7-Day Average

```python
# Before: projected = used * 30
# After:
from datetime import date, timedelta
daily_costs = []
for offset in range(7):
    d = (date.today() - timedelta(days=offset)).isoformat()
    day_usage = db.get_usage_by_date(d)
    daily_costs.append(sum(float(u.get("cost", 0)) for u in day_usage))
avg_daily = sum(daily_costs) / max(1, len([c for c in daily_costs if c > 0]))
projected = round(avg_daily * 30, 2)
```

### 2.6 Remove Hardcoded Seed Date Fallback

```python
# usage.py:191 — delete:
# if not model_usage:
#     records = db.get_usage_by_date("2026-03-20")
# If no real data, return empty — don't show fake data
```

### 2.7 Function Rename

```python
# Before: _get_agent_usage_today()  — actually reads 7 days
# After:  _get_agent_usage_recent() — accurate name
```

### 2.8 By-Model Cache

```python
_model_usage_cache = {"data": None, "expires": 0}

@router.get("/api/v1/usage/by-model")
def usage_by_model():
    if _model_usage_cache["data"] and time.time() < _model_usage_cache["expires"]:
        return _model_usage_cache["data"]
    # ... existing logic ...
    _model_usage_cache["data"] = result
    _model_usage_cache["expires"] = time.time() + 300
    return result
```

---

## 3. Implementation Plan

### Phase 1: Data accuracy (P0)

| Task | File | Description |
|------|------|-------------|
| 1.1 | `server.py` (agent container) | Model-aware pricing: MODEL_PRICING dict + read BEDROCK_MODEL_ID |
| 1.2 | `usage.py` | Remove ChatGPT comparison (chatgptEquivalent, $0.83) |
| 1.3 | `usage.py` | Fix unknown model: keep "unknown", don't default to Nova Lite |
| 1.4 | `usage.py` | Remove hardcoded seed date fallback (2026-03-20) |
| 1.5 | `usage.py` | Budget projection: 7-day average × 30 (replace used * 30) |

### Phase 2: Hierarchical budgets (P0)

| Task | File | Description |
|------|------|-------------|
| 2.1 | `usage.py` | New: `resolve_budget(emp_id, dept_name)` with 3-level inheritance |
| 2.2 | `usage.py` | Enhanced: `usage_budgets()` uses hierarchical budget resolution |
| 2.3 | `usage.py` | Enhanced: `update_budgets()` accepts new schema (global + departments + employees) |
| 2.4 | `usage.py` | New: `GET /api/v1/usage/my-budget` for employee portal |
| 2.5 | `usage.py` | New: `GET /api/v1/usage/department-budget` for manager view |

### Phase 3: Cleanup (P1)

| Task | File | Description |
|------|------|-------------|
| 3.1 | `usage.py` | Rename _get_agent_usage_today → _get_agent_usage_recent |
| 3.2 | `usage.py` | by-model 5-min cache |
| 3.3 | `usage.py` | Budget update creates AUDIT# entry |

### Phase 4: Frontend (P1)

| Task | File | Description |
|------|------|-------------|
| 4.1 | `Usage.tsx` | Remove ChatGPT comparison from trend chart |
| 4.2 | `Usage.tsx` | Budget management: global + department + individual |
| 4.3 | `MyUsage.tsx` | Employee portal: show personal budget + usage |
| 4.4 | `Usage.tsx` | Show actual model in by-agent view |

---

## 4. TODO

### Must-Do
- [ ] 1.1: server.py model-aware pricing (Docker rebuild required)
- [ ] 1.2: Remove ChatGPT comparison
- [ ] 1.3: Fix unknown model default
- [ ] 1.4: Remove seed date fallback
- [ ] 1.5: Budget projection 7-day average
- [ ] 2.1-2.5: Hierarchical budget system
- [ ] 3.1-3.3: Cleanup + cache + audit
- [ ] 4.1-4.4: Frontend updates
- [ ] Unit tests
- [ ] Update ui-guide.html Usage findings
- [ ] Seed data: update CONFIG#budgets with new schema

### Future
- [ ] Budget enforcement: block agent when individual budget exceeded (integrate with Review Engine alerts)
- [ ] Monthly cost report export (PDF/CSV)
- [ ] Model cost comparison tool: "what if we switch Engineering from Sonnet to Nova Pro?"
- [ ] Real-time Bedrock cost reconciliation via Cost Explorer API
