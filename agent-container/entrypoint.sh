#!/bin/bash
# =============================================================================
# Agent Container Entrypoint
# Design: server.py starts immediately (health check ready in seconds).
# OpenClaw is invoked per-request via CLI subprocess — no long-running process.
# S3 sync happens in background after server is up.
# =============================================================================
set -eo pipefail

TENANT_ID="${SESSION_ID:-${sessionId:-unknown}}"
S3_BUCKET="${S3_BUCKET:-openclaw-tenants-000000000000}"
S3_BASE="s3://${S3_BUCKET}/${TENANT_ID}"
WORKSPACE="/tmp/workspace"
SYNC_INTERVAL="${SYNC_INTERVAL:-60}"
STACK_NAME="${STACK_NAME:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"

echo "[entrypoint] START tenant=${TENANT_ID} bucket=${S3_BUCKET}"

# =============================================================================
# Step 0: Node.js runtime optimizations (before any openclaw invocation)
# =============================================================================

# V8 Compile Cache (Node.js 22+) — pre-warmed at Docker build time
if [ -d /app/.compile-cache ]; then
    export NODE_COMPILE_CACHE=/app/.compile-cache
    echo "[entrypoint] V8 compile cache enabled"
fi

# Force IPv4 for Node.js 22 VPC compatibility
# Node.js 22 Happy Eyeballs tries IPv6 first, times out in VPC without IPv6
export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--dns-result-order=ipv4first"

# Prepare workspace
mkdir -p "$WORKSPACE" "$WORKSPACE/memory" "$WORKSPACE/skills"
echo "$TENANT_ID" > /tmp/tenant_id

# =============================================================================
# Step 0.5: Write openclaw.json config (substitute env vars)
# =============================================================================
OPENCLAW_CONFIG_DIR="$HOME/.openclaw"
mkdir -p "$OPENCLAW_CONFIG_DIR"
sed -e "s|\${AWS_REGION}|${AWS_REGION}|g" \
    -e "s|\${BEDROCK_MODEL_ID}|${BEDROCK_MODEL_ID:-global.amazon.nova-2-lite-v1:0}|g" \
    /app/openclaw.json > "$OPENCLAW_CONFIG_DIR/openclaw.json"
echo "[entrypoint] openclaw.json written to $OPENCLAW_CONFIG_DIR/openclaw.json"

# =============================================================================
# Step 1: Start server.py IMMEDIATELY — health check must respond in seconds
# =============================================================================
export OPENCLAW_WORKSPACE="$WORKSPACE"
export OPENCLAW_SKIP_ONBOARDING=1

python /app/server.py &
SERVER_PID=$!
echo "[entrypoint] server.py PID=${SERVER_PID}"

# =============================================================================
# Step 2: S3 sync in background (non-blocking)
# =============================================================================
(
    echo "[bg] Pulling workspace from S3..."
    aws s3 sync "${S3_BASE}/workspace/" "$WORKSPACE/" --quiet 2>/dev/null || true
    aws s3 sync "s3://${S3_BUCKET}/_shared/skills/" "$WORKSPACE/skills/_shared/" --quiet 2>/dev/null || true

    # Initialize SOUL.md for new tenants
    if [ ! -f "$WORKSPACE/SOUL.md" ]; then
        ROLE=$(aws ssm get-parameter \
            --name "/openclaw/${STACK_NAME}/tenants/${TENANT_ID}/soul-template" \
            --query Parameter.Value --output text --region "$AWS_REGION" 2>/dev/null || echo "default")
        aws s3 cp "s3://${S3_BUCKET}/_shared/templates/${ROLE}.md" "$WORKSPACE/SOUL.md" \
            --quiet 2>/dev/null || echo "You are a helpful AI assistant." > "$WORKSPACE/SOUL.md"
    fi

    echo "[bg] Workspace ready"
    echo "WORKSPACE_READY" > /tmp/workspace_status

    # Watchdog: sync back every SYNC_INTERVAL seconds
    while true; do
        sleep "$SYNC_INTERVAL"
        aws s3 sync "$WORKSPACE/" "${S3_BASE}/workspace/" \
            --exclude "node_modules/*" --exclude "skills/_shared/*" \
            --quiet 2>/dev/null || true
    done
) &
BG_PID=$!
echo "[entrypoint] Background sync PID=${BG_PID}"

# =============================================================================
# Step 3: Graceful shutdown
# =============================================================================
cleanup() {
    echo "[entrypoint] SIGTERM — flushing workspace"
    kill "$BG_PID" 2>/dev/null || true
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    aws s3 sync "$WORKSPACE/" "${S3_BASE}/workspace/" \
        --exclude "node_modules/*" --exclude "skills/_shared/*" \
        --quiet 2>/dev/null || true
    echo "[entrypoint] Done"
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "[entrypoint] Waiting..."
wait "$SERVER_PID" || true
