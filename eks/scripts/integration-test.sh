#!/bin/bash
# =============================================================================
# EKS Integration Test — validates admin console + OpenClaw instance deployment
#
# Usage:
#   bash integration-test.sh --cluster openclaw-test --region us-west-2 --password TestPass123!
#   bash integration-test.sh --cluster openclaw-cn --region cn-northwest-1 --password TestPass123! \
#     --registry 834204282212.dkr.ecr.cn-northwest-1.amazonaws.com.cn
#
# Prerequisites:
#   - kubectl configured for the target cluster
#   - Admin console deployed and accessible via port-forward on localhost:8099
#   - DynamoDB seeded with org data (deploy-eks.sh --skip-build)
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${CYAN}[INFO]${NC} $*"; }

CLUSTER=""
REGION="us-west-2"
PASSWORD="admin123"
GLOBAL_REGISTRY=""
PORT=8099
FAILURES=0

while [[ $# -gt 0 ]]; do
  case $1 in
    --cluster)   CLUSTER="$2"; shift 2 ;;
    --region)    REGION="$2"; shift 2 ;;
    --password)  PASSWORD="$2"; shift 2 ;;
    --registry)  GLOBAL_REGISTRY="$2"; shift 2 ;;
    --port)      PORT="$2"; shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

[[ -z "$CLUSTER" ]] && { echo "Usage: $0 --cluster NAME [--region REGION] [--password PW] [--registry ECR_URI]"; exit 1; }

BASE="http://localhost:${PORT}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Integration Test: ${CLUSTER} (${REGION})"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Login ────────────────────────────────────────────────
info "1. Login..."
TOKEN=$(curl -sf -X POST "${BASE}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"employeeId\":\"emp-jiade\",\"password\":\"${PASSWORD}\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('token',''))")
if [[ -n "$TOKEN" ]]; then pass "Login OK"; else fail "Login failed"; exit 1; fi
AUTH="Authorization: Bearer ${TOKEN}"

# ── 2. Operator status ─────────────────────────────────────
info "2. Operator status..."
OP=$(curl -sf -H "$AUTH" "${BASE}/api/v1/admin/eks/operator/status")
INSTALLED=$(echo "$OP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('installed',False))")
VERSION=$(echo "$OP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('version',''))")
if [[ "$INSTALLED" == "True" ]]; then pass "Operator v${VERSION} installed"; else fail "Operator not installed"; fi

# ── 3. Associate cluster ───────────────────────────────────
info "3. Associate cluster..."
curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"name\":\"${CLUSTER}\",\"region\":\"${REGION}\"}" \
  "${BASE}/api/v1/admin/eks/cluster" > /dev/null
pass "Cluster associated"

# ── 4. Deploy OpenClaw instance ─────────────────────────────
info "4. Deploy agent-helpdesk..."
DEPLOY_BODY="{\"model\":\"bedrock/us.amazon.nova-2-lite-v1:0\""
[[ -n "$GLOBAL_REGISTRY" ]] && DEPLOY_BODY="${DEPLOY_BODY},\"globalRegistry\":\"${GLOBAL_REGISTRY}\""
DEPLOY_BODY="${DEPLOY_BODY}}"

DEPLOY_RESP=$(curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "$DEPLOY_BODY" "${BASE}/api/v1/admin/eks/agent-helpdesk/deploy")
DEPLOYED=$(echo "$DEPLOY_RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('deployed',False))")
if [[ "$DEPLOYED" == "True" ]]; then pass "Deploy accepted"; else fail "Deploy failed: $DEPLOY_RESP"; fi

# ── 5. Wait for pod Running ────────────────────────────────
info "5. Waiting for pod (up to 5 min)..."
RUNNING=false
for i in $(seq 1 20); do
  sleep 15
  STATUS=$(curl -sf -H "$AUTH" "${BASE}/api/v1/admin/eks/agent-helpdesk/status")
  POD_RUNNING=$(echo "$STATUS" | python3 -c "import sys,json;print(json.load(sys.stdin).get('running',False))")
  CRD_STATUS=$(echo "$STATUS" | python3 -c "import sys,json;print(json.load(sys.stdin).get('crdStatus',''))")
  POD_PHASE=$(echo "$STATUS" | python3 -c "import sys,json;print(json.load(sys.stdin).get('pod',{}).get('phase','N/A'))")
  echo "  [${i}] crd=${CRD_STATUS} pod=${POD_PHASE}"
  if [[ "$POD_RUNNING" == "True" ]]; then RUNNING=true; break; fi
done
if $RUNNING; then pass "Pod is Running"; else fail "Pod failed to start within 5 min"; fi

if $RUNNING; then
  # ── 6. Verify PVC storage class ─────────────────────────
  info "6. PVC storage class..."
  PVC_CLASS=$(kubectl -n openclaw get pvc -o jsonpath='{.items[0].spec.storageClassName}' 2>/dev/null || echo "unknown")
  if [[ "$PVC_CLASS" == "efs-sc" ]]; then pass "StorageClass: efs-sc"; else fail "StorageClass: $PVC_CLASS (expected efs-sc)"; fi

  # ── 7. Verify registry override (China) ──────────────────
  if [[ -n "$GLOBAL_REGISTRY" ]]; then
    info "7. Registry override..."
    CRD_REG=$(kubectl -n openclaw get openclawinstance agent-helpdesk -o jsonpath='{.spec.registry}' 2>/dev/null)
    if [[ "$CRD_REG" == "$GLOBAL_REGISTRY" ]]; then pass "Registry: $CRD_REG"; else fail "Registry: $CRD_REG (expected $GLOBAL_REGISTRY)"; fi
  fi

  # ── 8. Reload ──────────────────────────────────────────────
  info "8. Reload with model change..."
  RELOAD=$(curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"model":"bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"}' \
    "${BASE}/api/v1/admin/eks/agent-helpdesk/reload")
  RELOADED=$(echo "$RELOAD" | python3 -c "import sys,json;print(json.load(sys.stdin).get('reloaded',False))")
  if [[ "$RELOADED" == "True" ]]; then pass "Reload OK"; else fail "Reload failed"; fi

  # ── 9. Duplicate deploy → 409 ──────────────────────────────
  info "9. Duplicate deploy (expect 409)..."
  DUP_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"model":"bedrock/x"}' "${BASE}/api/v1/admin/eks/agent-helpdesk/deploy")
  if [[ "$DUP_CODE" == "409" ]]; then pass "Duplicate blocked (409)"; else fail "Duplicate: $DUP_CODE (expected 409)"; fi

  # ── 10. Stop ───────────────────────────────────────────────
  info "10. Stop agent..."
  STOP=$(curl -sf -X POST -H "$AUTH" "${BASE}/api/v1/admin/eks/agent-helpdesk/stop")
  STOPPED=$(echo "$STOP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('stopped',False))")
  if [[ "$STOPPED" == "True" ]]; then pass "Stop OK"; else fail "Stop failed"; fi
  sleep 3
  kubectl -n openclaw patch openclawinstance agent-helpdesk --type=json \
    -p='[{"op":"remove","path":"/metadata/finalizers"}]' 2>/dev/null || true
fi

# ── 11. UI deploy modal ─────────────────────────────────────
info "11. UI deploy modal..."
JS_FILE=$(curl -sf "${BASE}/" | grep -o 'assets/index-[^"]*')
JS=$(curl -sf "${BASE}/${JS_FILE}" -o /tmp/integration-test-js.tmp && cat /tmp/integration-test-js.tmp)
UI_OK=true
for text in "Deploy Agent to EKS" "Global Registry" "Container Image" "Compute Resources"; do
  if echo "$JS" | grep -q "$text"; then
    pass "UI: '$text' present"
  else
    fail "UI: '$text' missing"
    UI_OK=false
  fi
done

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ $FAILURES -eq 0 ]]; then
  echo -e "  ${GREEN}ALL TESTS PASSED${NC} — ${CLUSTER} (${REGION})"
else
  echo -e "  ${RED}${FAILURES} TEST(S) FAILED${NC} — ${CLUSTER} (${REGION})"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
exit $FAILURES
