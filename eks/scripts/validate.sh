#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASS=0
FAIL=0

check() {
  local desc="$1"
  shift
  if "$@" &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} $desc"
    ((PASS++))
  else
    echo -e "  ${RED}✗${NC} $desc"
    ((FAIL++))
  fi
}

echo "=== OpenClaw EKS Validation ==="
echo ""

echo "Cluster:"
check "Nodes are Ready" kubectl get nodes -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' | grep -q True
check "CoreDNS running" kubectl get pods -n kube-system -l k8s-app=kube-dns --field-selector=status.phase=Running -o name | head -1

echo ""
echo "Storage:"
check "EBS StorageClass exists" kubectl get sc ebs-sc
check "EFS StorageClass exists" kubectl get sc efs-sc 2>/dev/null || true

echo ""
echo "OpenClaw Operator:"
check "Operator namespace exists" kubectl get ns openclaw-operator-system
check "Operator pod running" kubectl get pods -n openclaw-operator-system --field-selector=status.phase=Running -o name | head -1
check "OpenClawInstance CRD exists" kubectl get crd openclawinstances.openclaw.rocks

echo ""
echo "Bedrock IAM:"
check "OpenClaw namespace exists" kubectl get ns openclaw
check "ServiceAccount exists" kubectl get sa openclaw-sandbox -n openclaw

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]] && echo -e "${GREEN}All checks passed!${NC}" || echo -e "${RED}Some checks failed.${NC}"
exit $FAIL
