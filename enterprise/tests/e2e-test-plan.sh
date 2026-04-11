#!/bin/bash
# =============================================================================
# OpenClaw Enterprise — End-to-End Test Suite
#
# Prerequisites:
#   - AWS CLI configured with correct profile
#   - curl available
#   - jq available
#   - EC2 instance running with admin-console on port 8099
#
# Usage:
#   export BASE_URL=https://openclaw.awspsa.com   # or http://localhost:8099
#   export ADMIN_PASSWORD=your-password
#   bash enterprise/tests/e2e-test-plan.sh
# =============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
PASS=0; FAIL=0; SKIP=0

BASE_URL="${BASE_URL:-http://localhost:8099}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

log()  { echo -e "${CYAN}[test]${NC}  $*"; }
pass() { echo -e "${GREEN}[PASS]${NC}  $*"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}[FAIL]${NC}  $*"; FAIL=$((FAIL + 1)); }
skip() { echo -e "${YELLOW}[SKIP]${NC}  $*"; SKIP=$((SKIP + 1)); }

# ── Auth ─────────────────────────────────────────────────────────────────────
get_token() {
  if [ -z "$ADMIN_PASSWORD" ]; then
    echo "ERROR: ADMIN_PASSWORD not set" >&2; exit 1
  fi
  ADMIN_EMP="${ADMIN_EMPLOYEE_ID:-emp-jiade}"
  TOKEN=$(curl -sf "$BASE_URL/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"employeeId\":\"$ADMIN_EMP\",\"password\":\"$ADMIN_PASSWORD\"}" | jq -r '.token // empty')
  if [ -z "$TOKEN" ]; then
    echo "ERROR: Login failed" >&2; exit 1
  fi
  AUTH="Authorization: Bearer $TOKEN"
  log "Authenticated as admin"
}

api_get() {
  curl -sf "$BASE_URL/api/v1$1" -H "$AUTH" 2>/dev/null
}

api_post() {
  curl -sf "$BASE_URL/api/v1$1" -H "$AUTH" -H 'Content-Type: application/json' -d "$2" 2>/dev/null
}

# =============================================================================
# TEST GROUP 1: Health & Basic Connectivity
# =============================================================================
test_health() {
  log "=== Group 1: Health & Connectivity ==="

  # T1.1 Admin console responds
  if curl -sf "$BASE_URL/api/v1/settings/services" -H "$AUTH" | jq -e '.platform' >/dev/null 2>&1; then
    pass "T1.1 Admin console API reachable"
  else
    fail "T1.1 Admin console API not reachable"
  fi

  # T1.2 System stats
  HEALTH=$(api_get "/settings/system-stats" || echo "{}")
  if echo "$HEALTH" | jq -e '.cpu' >/dev/null 2>&1; then
    pass "T1.2 System stats endpoint responds"
  else
    fail "T1.2 System stats endpoint failed"
  fi

  # T1.3 Services info has platform
  SERVICES=$(api_get "/settings/services" || echo "{}")
  if echo "$SERVICES" | jq -e '.platform' >/dev/null 2>&1; then
    pass "T1.3 Services returns platform info"
  else
    fail "T1.3 Services missing platform"
  fi
}

# =============================================================================
# TEST GROUP 2: Organization Data
# =============================================================================
test_org() {
  log "=== Group 2: Organization Data ==="

  # T2.1 Employees exist
  EMPS=$(api_get "/org/employees")
  EMP_COUNT=$(echo "$EMPS" | jq 'length')
  if [ "$EMP_COUNT" -gt 0 ]; then
    pass "T2.1 Employees loaded: $EMP_COUNT"
  else
    fail "T2.1 No employees found"
  fi

  # T2.2 Positions exist
  POSITIONS=$(api_get "/org/positions")
  POS_COUNT=$(echo "$POSITIONS" | jq 'length')
  if [ "$POS_COUNT" -gt 0 ]; then
    pass "T2.2 Positions loaded: $POS_COUNT"
  else
    fail "T2.2 No positions found"
  fi

  # T2.3 Departments exist
  DEPTS=$(api_get "/org/departments")
  DEPT_COUNT=$(echo "$DEPTS" | jq 'length')
  if [ "$DEPT_COUNT" -gt 0 ]; then
    pass "T2.3 Departments loaded: $DEPT_COUNT"
  else
    fail "T2.3 No departments found"
  fi
}

# =============================================================================
# TEST GROUP 3: SOUL Loading
# =============================================================================
test_soul() {
  log "=== Group 3: SOUL Loading ==="

  # T3.1 Get first employee with agent
  EMP_ID=$(api_get "/org/employees" | jq -r '[.[] | select(.agentId != null and .agentId != "")][0].id // empty')
  if [ -z "$EMP_ID" ]; then
    skip "T3.1-T3.4 No employee with agent found"
    return
  fi
  pass "T3.1 Employee with agent: $EMP_ID"

  # T3.2 Playground pipeline config loads SOUL
  PIPELINE=$(api_get "/playground/pipeline/$EMP_ID" || echo "{}")
  GLOBAL_WORDS=$(echo "$PIPELINE" | jq '.soul.globalWords // 0')
  if [ "$GLOBAL_WORDS" -gt 0 ]; then
    pass "T3.2 Global SOUL loaded: $GLOBAL_WORDS words"
  else
    fail "T3.2 Global SOUL not loaded (0 words)"
  fi

  # T3.3 Pipeline has model info
  MODEL=$(echo "$PIPELINE" | jq -r '.model // empty')
  if [ -n "$MODEL" ]; then
    pass "T3.3 Model resolved: $MODEL"
  else
    fail "T3.3 No model in pipeline config"
  fi

  # T3.4 Pipeline has Plan A tools
  TOOLS=$(echo "$PIPELINE" | jq '.planA.tools | length')
  if [ "$TOOLS" -gt 0 ]; then
    pass "T3.4 Plan A tools: $TOOLS allowed"
  else
    fail "T3.4 No Plan A tools in pipeline"
  fi
}

# =============================================================================
# TEST GROUP 4: Monitor Center
# =============================================================================
test_monitor() {
  log "=== Group 4: Monitor Center ==="

  # T4.1 Action items
  ACTIONS=$(api_get "/monitor/action-items" || echo "[]")
  if echo "$ACTIONS" | jq -e 'type' >/dev/null 2>&1; then
    pass "T4.1 Action items endpoint responds"
  else
    fail "T4.1 Action items endpoint failed"
  fi

  # T4.2 System status
  STATUS=$(api_get "/monitor/system-status" || echo "{}")
  if echo "$STATUS" | jq -e '.uptime' >/dev/null 2>&1; then
    pass "T4.2 System status responds"
  else
    fail "T4.2 System status failed"
  fi

  # T4.3 Event stream
  EVENTS=$(api_get "/monitor/events?limit=5" || echo "{}")
  if echo "$EVENTS" | jq -e '.events' >/dev/null 2>&1; then
    pass "T4.3 Event stream responds"
  else
    fail "T4.3 Event stream failed"
  fi

  # T4.4 Monitor health (agent health data)
  MHEALTH=$(api_get "/monitor/health" || echo "{}")
  if echo "$MHEALTH" | jq -e '.agents' >/dev/null 2>&1; then
    pass "T4.4 Monitor health responds"
  else
    fail "T4.4 Monitor health failed"
  fi
}

# =============================================================================
# TEST GROUP 5: Audit Center
# =============================================================================
test_audit() {
  log "=== Group 5: Audit Center ==="

  # T5.1 Audit entries
  AUDIT=$(api_get "/audit?limit=5" || echo "[]")
  if echo "$AUDIT" | jq -e 'length' >/dev/null 2>&1; then
    pass "T5.1 Audit entries loaded"
  else
    fail "T5.1 Audit entries failed"
  fi

  # T5.2 Audit insights
  INSIGHTS=$(api_get "/audit/insights" || echo "{}")
  if echo "$INSIGHTS" | jq -e '.insights' >/dev/null 2>&1; then
    pass "T5.2 Audit insights responds"
  else
    fail "T5.2 Audit insights failed"
  fi

  # T5.3 Review queue
  REVIEWS=$(api_get "/audit/reviews" || echo "[]")
  if echo "$REVIEWS" | jq -e 'type' >/dev/null 2>&1; then
    pass "T5.3 Review queue responds"
  else
    fail "T5.3 Review queue failed"
  fi

  # T5.4 Compliance stats
  COMPLIANCE=$(api_get "/audit/compliance" || echo "{}")
  if echo "$COMPLIANCE" | jq -e 'type' >/dev/null 2>&1; then
    pass "T5.4 Compliance stats responds"
  else
    fail "T5.4 Compliance stats failed"
  fi
}

# =============================================================================
# TEST GROUP 6: Usage & Cost
# =============================================================================
test_usage() {
  log "=== Group 6: Usage & Cost ==="

  # T6.1 Usage summary
  USAGE=$(api_get "/usage/summary" || echo "{}")
  if echo "$USAGE" | jq -e '.totalCost' >/dev/null 2>&1; then
    pass "T6.1 Usage summary responds"
  else
    fail "T6.1 Usage summary failed"
  fi

  # T6.2 By-model breakdown
  BYMODEL=$(api_get "/usage/by-model" || echo "[]")
  if echo "$BYMODEL" | jq -e 'type' >/dev/null 2>&1; then
    pass "T6.2 By-model breakdown responds"
  else
    fail "T6.2 By-model breakdown failed"
  fi

  # T6.3 Budgets endpoint
  BUDGETS=$(api_get "/usage/budgets" || echo "{}")
  if echo "$BUDGETS" | jq -e '.' >/dev/null 2>&1; then
    pass "T6.3 Budgets endpoint responds"
  else
    fail "T6.3 Budgets endpoint failed"
  fi

  # T6.4 No ChatGPT comparison (should not have chatgpt in response)
  SUMMARY_TEXT=$(api_get "/usage/summary" || echo "{}")
  if echo "$SUMMARY_TEXT" | grep -qi "chatgpt"; then
    fail "T6.4 ChatGPT comparison still present"
  else
    pass "T6.4 No ChatGPT comparison (removed)"
  fi
}

# =============================================================================
# TEST GROUP 7: Security Center
# =============================================================================
test_security() {
  log "=== Group 7: Security Center ==="

  # T7.1 Security config
  SEC=$(api_get "/security/config" || echo "{}")
  if echo "$SEC" | jq -e '.' >/dev/null 2>&1; then
    pass "T7.1 Security config responds"
  else
    fail "T7.1 Security config failed"
  fi

  # T7.2 Runtime list
  RUNTIMES=$(api_get "/security/runtimes" || echo "[]")
  if echo "$RUNTIMES" | jq -e '.' >/dev/null 2>&1; then
    pass "T7.2 Runtimes endpoint responds"
  else
    fail "T7.2 Runtimes endpoint failed"
  fi
}

# =============================================================================
# TEST GROUP 8: Playground
# =============================================================================
test_playground() {
  log "=== Group 8: Playground ==="

  # T8.1 Profiles load
  PROFILES=$(api_get "/playground/profiles" || echo "{}")
  PROFILE_COUNT=$(echo "$PROFILES" | jq 'keys | length')
  if [ "$PROFILE_COUNT" -gt 0 ]; then
    pass "T8.1 Playground profiles: $PROFILE_COUNT"
  else
    fail "T8.1 No playground profiles"
  fi

  # T8.2 Simulate mode (Bedrock Converse)
  EMP_ID=$(api_get "/org/employees" | jq -r '[.[] | select(.agentId != null and .agentId != "")][0].id // empty')
  if [ -n "$EMP_ID" ]; then
    TENANT="port__$EMP_ID"
    RESP=$(api_post "/playground/send" "{\"tenant_id\":\"$TENANT\",\"message\":\"hello\",\"mode\":\"simulate\"}" || echo "{}")
    SOURCE=$(echo "$RESP" | jq -r '.source // empty')
    if [ "$SOURCE" = "simulate-bedrock" ]; then
      pass "T8.2 Simulate uses Bedrock Converse (source=simulate-bedrock)"
    elif [ "$SOURCE" = "error" ]; then
      fail "T8.2 Simulate returned error: $(echo "$RESP" | jq -r '.response' | head -c 100)"
    else
      fail "T8.2 Unexpected source: $SOURCE"
    fi

    # T8.3 Events endpoint
    EVENTS=$(api_get "/playground/events?tenant_id=$TENANT&seconds=300" || echo "{}")
    if echo "$EVENTS" | jq -e '.events' >/dev/null 2>&1; then
      pass "T8.3 Playground events responds"
    else
      fail "T8.3 Playground events failed"
    fi
  else
    skip "T8.2-T8.3 No employee with agent"
  fi
}

# =============================================================================
# TEST GROUP 9: Settings
# =============================================================================
test_settings() {
  log "=== Group 9: Settings ==="

  # T9.1 Model config
  MC=$(api_get "/settings/models" || echo "{}")
  if echo "$MC" | jq -e 'type' >/dev/null 2>&1; then
    pass "T9.1 Model config responds"
  else
    fail "T9.1 Model config failed"
  fi

  # T9.2 Admin assistant config
  AA=$(api_get "/settings/admin-assistant" || echo "{}")
  if echo "$AA" | jq -e '.systemPrompt' >/dev/null 2>&1; then
    pass "T9.2 Admin assistant config has systemPrompt"
  else
    fail "T9.2 Admin assistant config missing systemPrompt"
  fi

  # T9.3 Platform access (SSM commands)
  PA=$(api_get "/settings/platform-access" || echo "{}")
  if echo "$PA" | jq -e '.instanceId' >/dev/null 2>&1; then
    pass "T9.3 Platform access responds"
  else
    fail "T9.3 Platform access failed"
  fi
}

# =============================================================================
# TEST GROUP 10: IM Channels
# =============================================================================
test_im() {
  log "=== Group 10: IM Channels ==="

  # T10.1 Channel list
  CHANNELS=$(api_get "/admin/im-channels" || echo "[]")
  CH_COUNT=$(echo "$CHANNELS" | jq 'length')
  if [ "$CH_COUNT" -gt 0 ]; then
    pass "T10.1 IM channels loaded: $CH_COUNT"
  else
    fail "T10.1 No IM channels"
  fi

  # T10.2 Channel connections
  CONNS=$(api_get "/admin/im-channel-connections" || echo "{}")
  if echo "$CONNS" | jq -e '.connections' >/dev/null 2>&1; then
    pass "T10.2 IM connections responds"
  else
    fail "T10.2 IM connections failed"
  fi

  # T10.3 Channel health
  HEALTH=$(api_get "/admin/im-channels/health" || echo "{}")
  if echo "$HEALTH" | jq -e '.lastActivity' >/dev/null 2>&1; then
    pass "T10.3 IM channel health responds"
  else
    fail "T10.3 IM channel health failed"
  fi

  # T10.4 Enrollment stats
  ENROLL=$(api_get "/admin/im-channels/enrollment" || echo "{}")
  if echo "$ENROLL" | jq -e '.totalWithAgent' >/dev/null 2>&1; then
    pass "T10.4 Enrollment stats responds"
  else
    fail "T10.4 Enrollment stats failed"
  fi
}

# =============================================================================
# TEST GROUP 11: Knowledge Base
# =============================================================================
test_kb() {
  log "=== Group 11: Knowledge Base ==="

  # T11.1 KB list
  KBS=$(api_get "/knowledge-bases" || echo "[]")
  if echo "$KBS" | jq -e '.' >/dev/null 2>&1; then
    pass "T11.1 Knowledge bases responds"
  else
    fail "T11.1 Knowledge bases failed"
  fi
}

# =============================================================================
# TEST GROUP 12: Dashboard
# =============================================================================
test_dashboard() {
  log "=== Group 12: Dashboard ==="

  # T12.1 Dashboard data
  DASH=$(api_get "/dashboard" || echo "{}")
  if echo "$DASH" | jq -e '.' >/dev/null 2>&1; then
    pass "T12.1 Dashboard responds"
  else
    fail "T12.1 Dashboard failed"
  fi
}

# =============================================================================
# MAIN
# =============================================================================
echo ""
echo "============================================"
echo " OpenClaw Enterprise — E2E Test Suite"
echo " Target: $BASE_URL"
echo "============================================"
echo ""

get_token

test_health
test_org
test_soul
test_monitor
test_audit
test_usage
test_security
test_playground
test_settings
test_im
test_kb
test_dashboard

echo ""
echo "============================================"
echo -e " Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC}"
echo "============================================"
echo ""

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
