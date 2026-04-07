#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="${SCRIPT_DIR}/../terraform"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}=== OpenClaw EKS Cleanup ===${NC}"
echo ""
echo "This will destroy ALL infrastructure managed by Terraform."
echo ""

read -rp "Are you sure? Type 'yes' to confirm: " confirm
if [[ "$confirm" != "yes" ]]; then
  echo "Cancelled."
  exit 0
fi

cd "$TF_DIR"

# Delete any OpenClaw instances first to clean up PVCs
echo -e "${YELLOW}Deleting OpenClaw instances...${NC}"
kubectl delete openclawinstances --all -A --ignore-not-found 2>/dev/null || true
sleep 10

echo -e "${YELLOW}Destroying infrastructure...${NC}"
terraform destroy -auto-approve

echo ""
echo -e "${GREEN}Cleanup complete.${NC}"
