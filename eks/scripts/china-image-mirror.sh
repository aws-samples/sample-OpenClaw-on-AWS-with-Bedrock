#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Mirror all container images and Helm charts to ECR
#
# Required BEFORE terraform apply in China regions (ghcr.io, quay.io, Docker
# Hub, registry.k8s.io are all blocked). Also useful for air-gapped global
# deployments.
#
# Usage:
#   # China region
#   bash china-image-mirror.sh --region cn-northwest-1 --name openclaw-cn --profile china
#
#   # Force re-mirror (even if images already exist in ECR)
#   bash china-image-mirror.sh --region cn-northwest-1 --name openclaw-cn --profile china --force
#
#   # Global region air-gapped deployment
#   bash china-image-mirror.sh --region us-west-2 --name openclaw-prod
#
#   # Cross-architecture (e.g. build on x86 for Graviton nodes)
#   bash china-image-mirror.sh --region cn-northwest-1 --name openclaw-cn --profile china --platform linux/arm64
#
# Flags:
#   --region      AWS region (default: us-west-2)
#   --name        Resource name prefix (default: openclaw-eks)
#   --profile     AWS CLI profile (required for China)
#   --force       Re-mirror all images even if they already exist in ECR
#   --platform    Target platform (e.g. linux/arm64) for cross-arch pulls
#
# Prerequisites:
#   - Docker running locally (with buildx)
#   - Helm >= 3.12 installed
#   - AWS CLI configured (with --profile for China)
#   - jq installed (for manifest platform inspection)
#   - Internet access to ghcr.io, quay.io, Docker Hub, registry.k8s.io
# =============================================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[info]${NC}  $*"; }
success() { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
error()   { echo -e "${RED}[error]${NC} $*"; exit 1; }

REGION="us-west-2"
NAME="openclaw-eks"
AWS_PROFILE_ARG=""
FORCE=false
PLATFORM=""

# Operator version — keep in sync with eks/terraform/modules/operator/variables.tf
OPERATOR_VERSION="0.26.2"
# OpenClaw version — pin to a known stable release (latest may have regressions)
# Override via env: OPENCLAW_VERSION=2026.4.5 bash china-image-mirror.sh ...
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.4.14}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --region)    REGION="$2"; shift 2 ;;
    --name)      NAME="$2"; shift 2 ;;
    --profile)   AWS_PROFILE_ARG="--profile $2"; export AWS_PROFILE="$2"; shift 2 ;;
    --force)     FORCE=true; shift ;;
    --platform)  PLATFORM="$2"; shift 2 ;;
    *) error "Unknown flag: $1" ;;
  esac
done

IS_CHINA=false
[[ "$REGION" == cn-* ]] && IS_CHINA=true

ACCOUNT_ID=$(aws sts get-caller-identity $AWS_PROFILE_ARG --query Account --output text --region "$REGION")
if $IS_CHINA; then
  ECR_HOST="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com.cn"
else
  ECR_HOST="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Mirror Images & Charts — ${NAME} (${REGION})"
echo "  Account: ${ACCOUNT_ID}"
echo "  ECR Host: ${ECR_HOST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── ECR Login (Docker + Helm) ─────────────────────────────────
info "Logging in to ECR..."
ECR_PASSWORD=$(aws ecr get-login-password $AWS_PROFILE_ARG --region "$REGION")
echo "$ECR_PASSWORD" | docker login --username AWS --password-stdin "$ECR_HOST" 2>/dev/null
echo "$ECR_PASSWORD" | helm registry login "$ECR_HOST" --username AWS --password-stdin 2>/dev/null
unset ECR_PASSWORD
success "ECR login"

# ── Container images ──────────────────────────────────────────
# ALL images from registries blocked in China. ECR path preserves the
# upstream org/repo structure so spec.registry CRD rewriting works.

MIRROR_IMAGES=(
  # ── OpenClaw Operator workload images ──
  # Core — always needed for OpenClawInstance pods
  "ghcr.io/openclaw/openclaw:${OPENCLAW_VERSION}|openclaw/openclaw:${OPENCLAW_VERSION}"
  "ghcr.io/astral-sh/uv:0.6-bookworm-slim|astral-sh/uv:0.6-bookworm-slim"
  "busybox:1.37|busybox:1.37"
  "nginx:1.27-alpine|nginx:1.27-alpine"
  "otel/opentelemetry-collector:0.120.0|otel/opentelemetry-collector:0.120.0"
  # Sidecars — needed when enabled in CRD spec
  "chromedp/headless-shell:stable|chromedp/headless-shell:stable"
  "ghcr.io/tailscale/tailscale:latest|tailscale/tailscale:latest"
  "tsl0922/ttyd:latest|tsl0922/ttyd:latest"
  # Backup/restore — needed when spec.backup is configured
  "rclone/rclone:1.68|rclone/rclone:1.68"

  # ── Operator controller ──
  "ghcr.io/openclaw-rocks/openclaw-operator:v${OPERATOR_VERSION}|openclaw-rocks/openclaw-operator:v${OPERATOR_VERSION}"

  # ── Kata Containers (optional: enable_kata) ──
  "quay.io/kata-containers/kata-deploy:3.27.0|kata-containers/kata-deploy:3.27.0"

  # ── Agent Sandbox (optional: enable_agent_sandbox) ──
  "registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.3.10|agent-sandbox/agent-sandbox-controller:v0.3.10"

  # ── LiteLLM (optional: enable_litellm) ──
  "ghcr.io/berriai/litellm:main-latest|berriai/litellm:main-latest"              # enable_db=false (default)
  "ghcr.io/berriai/litellm-database:main-latest|berriai/litellm-database:main-latest"  # enable_db=true
  "docker.io/bitnami/postgresql:latest|bitnami/postgresql:latest"               # enable_db=true only

  # ── Monitoring stack (optional: enable_monitoring) ──
  # Grafana
  "grafana/grafana:11.2.1|grafana/grafana:11.2.1"
  "quay.io/kiwigrid/k8s-sidecar:1.27.4|kiwigrid/k8s-sidecar:1.27.4"
  # kube-prometheus-stack
  "quay.io/prometheus/prometheus:v2.54.1|prometheus/prometheus:v2.54.1"
  "quay.io/prometheus-operator/prometheus-operator:v0.77.1|prometheus-operator/prometheus-operator:v0.77.1"
  "quay.io/prometheus-operator/prometheus-config-reloader:v0.77.1|prometheus-operator/prometheus-config-reloader:v0.77.1"
  "registry.k8s.io/ingress-nginx/kube-webhook-certgen:v20221220-controller-v1.5.1-58-g787ea74b6|ingress-nginx/kube-webhook-certgen:v20221220-controller-v1.5.1-58-g787ea74b6"
  "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.13.0|kube-state-metrics/kube-state-metrics:v2.13.0"
  "quay.io/prometheus/node-exporter:v1.8.2|prometheus/node-exporter:v1.8.2"
)

# ── Platform resolution ──────────────────────────────────────
# When --platform is specified, inspect the image manifest to find the best
# matching platform. Handles variant mismatches, e.g.:
#   requested: linux/arm64  →  manifest has: linux/arm64/v8  →  use linux/arm64/v8
#   requested: linux/arm64  →  manifest has: linux/amd64 only →  FAIL (no compatible arch)
#
# Usage: RESOLVED=$(resolve_platform "image:tag" "linux/arm64") || handle_error
# Returns: the resolved platform string on stdout, exit 0 on match, exit 1 on no match
resolve_platform() {
  local src="$1" requested="$2"
  [[ -z "$requested" ]] && return 0

  local req_os req_arch req_variant
  IFS='/' read -r req_os req_arch req_variant <<< "$requested"

  # Inspect the manifest index
  local manifest
  manifest=$(docker buildx imagetools inspect --raw "$src" 2>/dev/null) || {
    # Cannot inspect (auth, single-arch, etc.) — use requested as-is
    echo "$requested"
    return 0
  }

  # Extract available platforms as "os/arch" or "os/arch/variant"
  local platforms
  platforms=$(echo "$manifest" | jq -r '
    if .manifests then
      .manifests[] |
      select(.platform.os != null and .platform.architecture != null) |
      "\(.platform.os)/\(.platform.architecture)" +
      (if .platform.variant and (.platform.variant | length) > 0
       then "/\(.platform.variant)" else "" end)
    elif .mediaType then
      # Single-arch image (not a manifest list)
      empty
    else empty end
  ' 2>/dev/null)

  # If no platform list (single-arch image), use requested as-is
  if [[ -z "$platforms" ]]; then
    echo "$requested"
    return 0
  fi

  # 1) Exact match
  if echo "$platforms" | grep -qxF "$requested"; then
    echo "$requested"
    return 0
  fi

  # 2) Same os + arch, any variant (e.g. linux/arm64 matches linux/arm64/v8)
  local match
  match=$(echo "$platforms" | grep -E "^${req_os}/${req_arch}(/|$)" | head -1)
  if [[ -n "$match" ]]; then
    echo "$match"
    return 0
  fi

  # 3) No compatible platform found
  return 1
}

info "Mirroring ${#MIRROR_IMAGES[@]} container images to ECR..."
echo ""

MIRROR_FAIL=0
MIRROR_SKIP=0
MIRROR_PUSH=0
for entry in "${MIRROR_IMAGES[@]}"; do
  SRC="${entry%%|*}"
  DST_PATH="${entry##*|}"
  DST="${ECR_HOST}/${DST_PATH}"
  DST_REPO="${DST_PATH%%:*}"
  DST_TAG="${DST_PATH##*:}"

  printf "  %-55s → " "$SRC"

  # Create repo (idempotent)
  aws ecr create-repository $AWS_PROFILE_ARG \
    --repository-name "$DST_REPO" \
    --region "$REGION" 2>/dev/null || true

  # Check if image already exists (skip unless --force)
  if ! $FORCE; then
    EXISTING=$(aws ecr describe-images $AWS_PROFILE_ARG \
      --repository-name "$DST_REPO" \
      --image-ids imageTag="$DST_TAG" \
      --region "$REGION" --query 'imageDetails[0].imagePushedAt' --output text 2>/dev/null || echo "")
    if [[ -n "$EXISTING" && "$EXISTING" != "None" ]]; then
      echo -e "${CYAN}EXISTS${NC} (pushed ${EXISTING})"
      MIRROR_SKIP=$((MIRROR_SKIP + 1))
      continue
    fi
  fi

  # Multi-arch: mirror only linux/amd64 + linux/arm64 (skip s390x, ppc64le, etc.)
  PUSH_OK=false
  MANIFEST_RAW=$(docker buildx imagetools inspect --raw "$SRC" 2>/dev/null || true)
  if [[ -n "$MANIFEST_RAW" ]] && echo "$MANIFEST_RAW" | jq -e '.manifests' > /dev/null 2>&1; then
    # Source has a manifest list — filter to amd64 + arm64 only
    AMEND_ARGS=""
    # amd64: exact match
    DIGEST=$(echo "$MANIFEST_RAW" | jq -r '.manifests[] | select(.platform.os=="linux" and .platform.architecture=="amd64") | .digest' 2>/dev/null | head -1)
    [[ -n "$DIGEST" && "$DIGEST" != "null" ]] && AMEND_ARGS="$AMEND_ARGS ${SRC}@${DIGEST}"
    # arm64: match any variant (arm64, arm64/v8, etc.)
    DIGEST=$(echo "$MANIFEST_RAW" | jq -r '.manifests[] | select(.platform.os=="linux" and .platform.architecture=="arm64") | .digest' 2>/dev/null | head -1)
    [[ -n "$DIGEST" && "$DIGEST" != "null" ]] && AMEND_ARGS="$AMEND_ARGS ${SRC}@${DIGEST}"
    if [[ -n "$AMEND_ARGS" ]]; then
      if docker buildx imagetools create --tag "$DST" $AMEND_ARGS > /dev/null 2>&1; then
        echo -e "${GREEN}PUSHED (amd64+arm64)${NC}"
        MIRROR_PUSH=$((MIRROR_PUSH + 1))
        PUSH_OK=true
      fi
    fi
  fi

  if ! $PUSH_OK; then
    # Fallback: single-arch pull+tag+push (for images without manifest list)
    PULL_ARGS=""
    [[ -n "$PLATFORM" ]] && PULL_ARGS="--platform $PLATFORM"
    if ! docker pull $PULL_ARGS "$SRC" > /dev/null 2>&1; then
      echo -e "${RED}PULL FAILED${NC}"
      MIRROR_FAIL=$((MIRROR_FAIL + 1))
      continue
    fi
    docker tag "$SRC" "$DST"
    if docker push "$DST" > /dev/null 2>&1; then
      echo -e "${GREEN}PUSHED${NC}"
      MIRROR_PUSH=$((MIRROR_PUSH + 1))
    else
      echo -e "${RED}PUSH FAILED${NC}"
      MIRROR_FAIL=$((MIRROR_FAIL + 1))
    fi
  fi
done

echo ""
if [[ $MIRROR_FAIL -eq 0 ]]; then
  success "Image mirror: ${MIRROR_PUSH} pushed, ${MIRROR_SKIP} skipped, ${MIRROR_FAIL} failed"
else
  warn "Image mirror: ${MIRROR_PUSH} pushed, ${MIRROR_SKIP} skipped, ${MIRROR_FAIL} FAILED"
fi

# ── Helm charts (OCI artifacts) ───────────────────────────────
# Terraform helm_release pulls charts from registries blocked in China.

MIRROR_OCI_CHARTS=(
  # Required — OpenClaw Operator (always deployed)
  "oci://ghcr.io/openclaw-rocks/charts|openclaw-operator|${OPERATOR_VERSION}"
  # Kata Containers (optional: enable_kata)
  "oci://ghcr.io/kata-containers/kata-deploy-charts|kata-deploy|3.27.0"
  # LiteLLM (optional: enable_litellm)
  "oci://ghcr.io/berriai|litellm-helm|0.1.812"
  # Bitnami PostgreSQL (LiteLLM subchart, only needed when enable_db=true)
  "oci://registry-1.docker.io/bitnamicharts|postgresql|18.5.10"
)

# HTTPS-repo charts (monitoring, grafana) — uncomment if needed:
MIRROR_HTTPS_CHARTS=(
  "eks|https://aws.github.io/eks-charts|aws-load-balancer-controller"
  # "prometheus-community|https://prometheus-community.github.io/helm-charts|kube-prometheus-stack|65.1.0"
  # "grafana|https://grafana.github.io/helm-charts|grafana|"
)

info "Mirroring Helm charts to ECR..."
echo ""
CHART_DIR=$(mktemp -d)
CHART_FAIL=0
CHART_PUSH=0

for entry in "${MIRROR_OCI_CHARTS[@]}"; do
  [[ "$entry" == \#* ]] && continue
  IFS='|' read -r REPO CHART VERSION <<< "$entry"
  printf "  %-55s → " "${REPO}/${CHART}:${VERSION}"

  if ! helm pull "${REPO}/${CHART}" --version "$VERSION" --destination "$CHART_DIR" 2>/dev/null; then
    echo -e "${RED}PULL FAILED${NC}"
    CHART_FAIL=$((CHART_FAIL + 1))
    continue
  fi

  aws ecr create-repository $AWS_PROFILE_ARG \
    --repository-name "charts/${CHART}" \
    --region "$REGION" 2>/dev/null || true

  CHART_FILE=$(ls "$CHART_DIR/${CHART}"-*.tgz 2>/dev/null | sort -V | tail -1)
  if [[ -n "$CHART_FILE" ]] && helm push "$CHART_FILE" "oci://${ECR_HOST}/charts" 2>/dev/null; then
    echo -e "${GREEN}PUSHED${NC}"
    CHART_PUSH=$((CHART_PUSH + 1))
  else
    echo -e "${RED}PUSH FAILED${NC}"
    CHART_FAIL=$((CHART_FAIL + 1))
  fi
  rm -f "$CHART_FILE"
done

for entry in "${MIRROR_HTTPS_CHARTS[@]}"; do
  [[ "$entry" == \#* ]] && continue
  IFS='|' read -r REPO_NAME REPO_URL CHART VERSION <<< "$entry"
  printf "  %-55s → " "${REPO_NAME}/${CHART}:${VERSION:-latest}"
  CHART_DIR2=$(mktemp -d)
  helm repo add "$REPO_NAME" "$REPO_URL" --force-update > /dev/null 2>&1
  PULL_CMD="$REPO_NAME/$CHART"
  [[ -n "$VERSION" ]] && PULL_CMD="$PULL_CMD --version $VERSION"
  if ! helm pull $PULL_CMD --destination "$CHART_DIR2" 2>/dev/null; then
    echo -e "${RED}PULL FAILED${NC}"
    rm -rf "$CHART_DIR2"
    continue
  fi
  aws ecr create-repository $AWS_PROFILE_ARG \
    --repository-name "charts/${CHART}" \
    --region "$REGION" 2>/dev/null || true
  CHART_FILE=$(ls "$CHART_DIR2/${CHART}"-*.tgz 2>/dev/null | sort -V | tail -1)
  if [[ -n "$CHART_FILE" ]] && helm push "$CHART_FILE" "oci://${ECR_HOST}/charts" 2>/dev/null; then
    echo -e "${GREEN}PUSHED${NC}"
    CHART_PUSH=$((CHART_PUSH + 1))
  else
    echo -e "${RED}PUSH FAILED${NC}"
    CHART_FAIL=$((CHART_FAIL + 1))
  fi
  rm -rf "$CHART_DIR2"
done

rm -rf "$CHART_DIR"
echo ""
if [[ $CHART_FAIL -eq 0 ]]; then
  success "Chart mirror: ${CHART_PUSH} pushed, ${CHART_FAIL} failed"
else
  warn "Chart mirror: ${CHART_PUSH} pushed, ${CHART_FAIL} FAILED"
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Done!${NC}"
echo ""
echo "  ECR Host:   ${ECR_HOST}"
echo "  Chart Repo: oci://${ECR_HOST}/charts"
echo ""
echo "  When deploying OpenClaw instances, set:"
echo "    globalRegistry: ${ECR_HOST}"
echo ""
echo "  Next: run terraform apply"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
