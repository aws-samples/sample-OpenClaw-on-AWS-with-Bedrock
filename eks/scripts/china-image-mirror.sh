#!/usr/bin/env bash
set -euo pipefail

#############################################################################
# China Image Mirror Script
#
# Mirrors all container images required by the OpenClaw operator from
# Docker Hub / ghcr.io to a private China region ECR. This is needed
# because China EKS nodes cannot reach docker.io or ghcr.io.
#
# How it works:
#   1. Creates ECR repos matching the upstream image path structure
#   2. Pulls images locally (this machine must have internet access)
#   3. Tags and pushes to China ECR
#
# After running this script, set `spec.registry` in OpenClawInstance to
# your China ECR endpoint. The operator replaces the registry prefix for
# all images (init, sidecar, main), so the paths must match.
#
# Usage:
#   AWS_PROFILE=zhy ./china-image-mirror.sh
#   AWS_PROFILE=zhy ./china-image-mirror.sh --skip-push  # ECR repos only
#
# Example OpenClawInstance with registry override:
#   spec:
#     registry: "123456789.dkr.ecr.cn-northwest-1.amazonaws.com.cn"
#############################################################################

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

CN_REGION="${AWS_DEFAULT_REGION:-cn-northwest-1}"
CN_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
CN_ECR="${CN_ACCOUNT}.dkr.ecr.${CN_REGION}.amazonaws.com.cn"
PLATFORM="linux/arm64"
SKIP_PUSH=false
[[ "${1:-}" == "--skip-push" ]] && SKIP_PUSH=true

# Each entry: "upstream_image|ecr_path"
# The ECR path must match what the operator produces after spec.registry replacement
IMAGES=(
  "busybox:1.37|busybox:1.37"
  "nginx:1.27-alpine|nginx:1.27-alpine"
  "ghcr.io/astral-sh/uv:0.6-bookworm-slim|astral-sh/uv:0.6-bookworm-slim"
  "ghcr.io/openclaw/openclaw:latest|openclaw/openclaw:latest"
  "otel/opentelemetry-collector:0.120.0|otel/opentelemetry-collector:0.120.0"
)

echo -e "${GREEN}=== China Image Mirror ===${NC}"
echo "ECR: ${CN_ECR}"
echo "Region: ${CN_REGION}"
echo ""

# Step 1: Create ECR repositories
echo -e "${YELLOW}[1/3] Creating ECR repositories...${NC}"
for entry in "${IMAGES[@]}"; do
  ecr_path="${entry##*|}"
  repo="${ecr_path%%:*}"
  aws ecr create-repository --repository-name "$repo" --region "$CN_REGION" 2>/dev/null \
    && echo "  + $repo" || echo "  . $repo (exists)"
done
echo ""

if [ "$SKIP_PUSH" = true ]; then
  echo -e "${YELLOW}Skipping push (--skip-push)${NC}"
  echo ""
  echo "Registry for OpenClawInstance: ${CN_ECR}"
  exit 0
fi

# Step 2: Login to ECR
echo -e "${YELLOW}[2/3] Logging into China ECR...${NC}"
aws ecr get-login-password --region "$CN_REGION" | \
  docker login --username AWS --password-stdin "$CN_ECR" 2>&1 | grep -v WARNING || true
echo ""

# Step 3: Pull, tag, push
echo -e "${YELLOW}[3/3] Mirroring images...${NC}"
for entry in "${IMAGES[@]}"; do
  src="${entry%%|*}"
  ecr_path="${entry##*|}"
  dst="${CN_ECR}/${ecr_path}"
  echo -n "  ${src} -> ${dst}: "
  docker pull --platform "$PLATFORM" "$src" >/dev/null 2>&1
  docker tag "$src" "$dst"
  docker push "$dst" >/dev/null 2>&1
  echo -e "${GREEN}done${NC}"
done

echo ""
echo -e "${GREEN}=== Mirror complete ===${NC}"
echo ""
echo "Use this registry in your OpenClawInstance:"
echo ""
echo "  spec:"
echo "    registry: \"${CN_ECR}\""
echo ""
echo "Or set it globally via the operator Helm values (future)."
