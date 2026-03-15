#!/bin/bash
# =============================================================================
# Agent Container Entrypoint — S3 Workspace Sync + OpenClaw Lifecycle
#
# This script manages the full lifecycle of a tenant's OpenClaw instance
# inside a Firecracker microVM (AgentCore Runtime).
#
# Phases: PULL → RUN (with watchdog) → SHUTDOWN (graceful flush)
#
# Environment variables (set by AgentCore Runtime):
#   SESSION_ID     — tenant_id (e.g. wa__8613800138000)
#   AWS_REGION     — AWS region
#   STACK_NAME     — CloudFormation stack name
#   S3_BUCKET      — S3 bucket for tenant workspaces (e.g. openclaw-tenants-XXXX)
#   BEDROCK_MODEL_ID — Bedrock model ID
# =============================================================================
set -eo pipefail
# Note: not using set -u because AgentCore may not set all expected env vars

TENANT_ID="${SESSION_ID:-${sessionId:-unknown}}"
S3_BUCKET="${S3_BUCKET:-openclaw-tenants-${AWS_ACCOUNT_ID:-000000000000}}"
S3_BASE="s3://${S3_BUCKET}/${TENANT_ID}"
WORKSPACE="/tmp/workspace"
SYNC_INTERVAL="${SYNC_INTERVAL:-60}"
STACK_NAME="${STACK_NAME:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"

echo "[entrypoint] tenant=${TENANT_ID} bucket=${S3_BUCKET} region=${AWS_REGION}"
echo "[entrypoint] Phase: PULL"

# =============================================================================
# Phase 1.5: Start HTTP server IMMEDIATELY for health check
# AgentCore health check must pass before S3 sync completes
# =============================================================================
echo "[entrypoint] Starting server.py FIRST for health check"
export OPENCLAW_WORKSPACE="$WORKSPACE"
export OPENCLAW_SKIP_ONBOARDING=1

python /app/server.py &
SERVER_PID=$!
echo "[entrypoint] server.py started (PID=${SERVER_PID}, health check ready)"
sleep 1

# =============================================================================
# Phase 1: PULL — Download tenant workspace from S3
# =============================================================================
mkdir -p "$WORKSPACE" "$WORKSPACE/memory" "$WORKSPACE/skills"

# Pull tenant workspace (if exists)
echo "[entrypoint] Pulling tenant workspace from ${S3_BASE}/workspace/"
aws s3 sync "${S3_BASE}/workspace/" "$WORKSPACE/" --quiet 2>/dev/null || true

# Pull shared skills from organization
echo "[entrypoint] Pulling shared skills"
aws s3 sync "s3://${S3_BUCKET}/_shared/skills/" "$WORKSPACE/skills/_shared/" --quiet 2>/dev/null || true

# If new tenant — initialize SOUL.md from role template
if [ ! -f "$WORKSPACE/SOUL.md" ]; then
    echo "[entrypoint] New tenant — initializing SOUL.md from template"
    ROLE_TEMPLATE=$(aws ssm get-parameter \
        --name "/openclaw/${STACK_NAME}/tenants/${TENANT_ID}/soul-template" \
        --query Parameter.Value --output text \
        --region "$AWS_REGION" 2>/dev/null || echo "default")

    aws s3 cp "s3://${S3_BUCKET}/_shared/templates/${ROLE_TEMPLATE}.md" "$WORKSPACE/SOUL.md" \
        --quiet 2>/dev/null || echo "You are a helpful AI assistant." > "$WORKSPACE/SOUL.md"
    echo "[entrypoint] SOUL.md initialized (template=${ROLE_TEMPLATE})"
fi

# Pull SQLite vector index (avoid expensive rebuild)
if aws s3 cp "${S3_BASE}/index/memory.sqlite" "$WORKSPACE/.memory-index.sqlite" --quiet 2>/dev/null; then
    echo "[entrypoint] Loaded cached vector index"
else
    echo "[entrypoint] No cached vector index — will rebuild on first query"
fi

echo "[entrypoint] Workspace ready: $(ls -1 $WORKSPACE | tr '\n' ' ')"

# Symlink OpenClaw templates so it can find them from workspace cwd
TEMPLATE_SRC=$(find /usr/local/lib/node_modules -path "*/openclaw/docs/reference/templates" -type d 2>/dev/null | head -1)
if [ -n "$TEMPLATE_SRC" ]; then
    mkdir -p "$WORKSPACE/docs/reference"
    ln -sf "$TEMPLATE_SRC" "$WORKSPACE/docs/reference/templates"
    echo "[entrypoint] Templates linked from $TEMPLATE_SRC"
fi

# =============================================================================
# Phase 2: RUN — Start OpenClaw with watchdog
# =============================================================================
echo "[entrypoint] Phase: RUN"

# S3 sync function
sync_to_s3() {
    aws s3 sync "$WORKSPACE/" "${S3_BASE}/workspace/" \
        --exclude "node_modules/*" \
        --exclude ".memory-index.sqlite-*" \
        --exclude "skills/_shared/*" \
        --quiet 2>/dev/null || true

    # Sync vector index separately
    if [ -f "$WORKSPACE/.memory-index.sqlite" ]; then
        aws s3 cp "$WORKSPACE/.memory-index.sqlite" "${S3_BASE}/index/memory.sqlite" \
            --quiet 2>/dev/null || true
    fi
}

# Background watchdog: sync every N seconds
(
    while true; do
        sleep "$SYNC_INTERVAL"
        echo "[watchdog] Syncing workspace to S3"
        sync_to_s3
    done
) &
WATCHDOG_PID=$!
echo "[entrypoint] Watchdog started (PID=${WATCHDOG_PID}, interval=${SYNC_INTERVAL}s)"

# =============================================================================
# Phase 3: SHUTDOWN — Graceful flush on SIGTERM
# =============================================================================
cleanup() {
    echo "[entrypoint] Phase: SHUTDOWN (SIGTERM received)"

    # Stop watchdog
    kill "$WATCHDOG_PID" 2>/dev/null || true

    # Stop server.py (which stops OpenClaw subprocess)
    if [ -n "${SERVER_PID:-}" ]; then
        echo "[entrypoint] Stopping server.py (PID=${SERVER_PID})"
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi

    # Final sync
    echo "[entrypoint] Final S3 sync"
    sync_to_s3

    echo "[entrypoint] Shutdown complete"
    exit 0
}
trap cleanup SIGTERM SIGINT

# =============================================================================
# Wait for server to exit (or SIGTERM)
# =============================================================================
echo "[entrypoint] All phases complete. Waiting for server.py..."
# Note: wait returns non-zero when process is killed by signal, so we || true
wait "$SERVER_PID" || true
