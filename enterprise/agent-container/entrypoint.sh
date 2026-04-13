#!/bin/bash
# =============================================================================
# Agent Container Entrypoint
# Design: OpenClaw Gateway starts first (port 18789) for native session management.
# server.py starts immediately for health check. S3 workspace assembled on first request.
# openclaw agent CLI connects to Gateway → proper memory compaction and session state.
# =============================================================================
set -eo pipefail

TENANT_ID="${SESSION_ID:-${sessionId:-unknown}}"
S3_BUCKET="${S3_BUCKET:-openclaw-tenants-000000000000}"
WORKSPACE="/root/.openclaw/workspace"
SYNC_INTERVAL="${SYNC_INTERVAL:-60}"
STACK_NAME="${STACK_NAME:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# Extract base employee ID from Tenant Router's tenant_id format
# Format: channel__employee_id__hash (e.g., port__emp-w5__c60c15e6c2ed12bf585)
# We use the base employee ID for S3 workspace paths so data persists across sessions
BASE_TENANT_ID="$TENANT_ID"
if echo "$TENANT_ID" | grep -q '__'; then
    # Split by __ and take the middle segment (employee ID)
    BASE_TENANT_ID=$(echo "$TENANT_ID" | awk -F'__' '{print $2}')
    if [ -z "$BASE_TENANT_ID" ]; then
        BASE_TENANT_ID="$TENANT_ID"
    fi
fi
S3_BASE="s3://${S3_BUCKET}/${BASE_TENANT_ID}"

echo "[entrypoint] START tenant=${TENANT_ID} base=${BASE_TENANT_ID} bucket=${S3_BUCKET}"

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

# EFS workspace detection:
# If EFS is mounted at /mnt/efs (EFS_ENABLED env var set by task definition),
# use /mnt/efs/{BASE_TENANT_ID}/workspace/ as the persistent workspace.
# On first start (empty EFS dir), bootstrap from S3. No watchdog needed.
EFS_MODE=false
if [ "${EFS_ENABLED:-}" = "true" ] && [ -d "/mnt/efs" ]; then
    EFS_WORKSPACE="/mnt/efs/${BASE_TENANT_ID}/workspace"
    mkdir -p "$EFS_WORKSPACE" "$EFS_WORKSPACE/memory" "$EFS_WORKSPACE/skills"
    WORKSPACE="$EFS_WORKSPACE"
    export OPENCLAW_WORKSPACE="$WORKSPACE"
    EFS_MODE=true
    echo "[entrypoint] EFS mode: workspace=${WORKSPACE}"

    # Bootstrap from S3 if this employee's EFS directory is empty (first start)
    if [ -z "$(ls -A "$EFS_WORKSPACE" 2>/dev/null)" ]; then
        echo "[entrypoint] EFS workspace empty — bootstrapping from S3..."
        aws s3 sync "${S3_BASE}/workspace/" "$EFS_WORKSPACE/" \
            --quiet --region "$AWS_REGION" 2>/dev/null || true
        echo "[entrypoint] EFS bootstrap complete"
    fi
else
    # Standard mode: use default workspace path, watchdog will sync to S3
    mkdir -p "$WORKSPACE" "$WORKSPACE/memory" "$WORKSPACE/skills"
fi

# Clean output/ directory on every cold start.
# Output files are persisted in S3 by the watchdog sync — no need to keep old ones locally.
rm -rf "$WORKSPACE/output" 2>/dev/null
mkdir -p "$WORKSPACE/output"

# Symlink for backward compat (skill_loader, watchdog sync)
ln -sfn "$WORKSPACE" /tmp/workspace
echo "$TENANT_ID" > /tmp/tenant_id
echo "$BASE_TENANT_ID" > /tmp/base_tenant_id

# =============================================================================
# Step 0.5: Write openclaw.json config (substitute env vars)
# =============================================================================
OPENCLAW_CONFIG_DIR="$HOME/.openclaw"
mkdir -p "$OPENCLAW_CONFIG_DIR"

# Generate a random gateway token for this container instance
# This token is stored in SSM so the admin console proxy can inject it
GATEWAY_TOKEN=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
export OPENCLAW_GATEWAY_TOKEN="$GATEWAY_TOKEN"

sed -e "s|\${AWS_REGION}|${AWS_REGION}|g" \
    -e "s|\${BEDROCK_MODEL_ID}|${BEDROCK_MODEL_ID:-global.anthropic.claude-sonnet-4-5-20250929-v1:0}|g" \
    /app/openclaw.json > "$OPENCLAW_CONFIG_DIR/openclaw.json"
echo "[entrypoint] openclaw.json written to $OPENCLAW_CONFIG_DIR/openclaw.json"

# Store gateway token in SSM so admin console proxy can authenticate
if [ -n "${SHARED_AGENT_ID:-}" ]; then
    aws ssm put-parameter \
        --name "/openclaw/${STACK_NAME}/always-on/${SHARED_AGENT_ID}/gateway-token" \
        --value "$GATEWAY_TOKEN" --type "SecureString" --overwrite \
        --region "$AWS_REGION" 2>/dev/null \
        && echo "[entrypoint] Gateway token stored in SSM for ${SHARED_AGENT_ID}" \
        || echo "[entrypoint] WARNING: Gateway token SSM write failed"
fi

# =============================================================================
# Step 0.55: Synchronous workspace assembly for always-on containers
# For ECS always-on containers, run workspace_assembler BEFORE starting the
# Gateway so the Gateway reads a fully assembled SOUL.md from the first session.
# Only runs if SHARED_AGENT_ID or SESSION_ID is set (i.e., we know the employee).
# =============================================================================
if [ "$EFS_MODE" = "true" ] || [ -n "${SHARED_AGENT_ID:-}" ]; then
    # Quick S3 sync to get latest personal SOUL + workspace files before assembly
    aws s3 sync "${S3_BASE}/workspace/" "$WORKSPACE/" \
        --quiet --region "$AWS_REGION" 2>/dev/null || true
    # Run workspace_assembler synchronously (will get SOUL from S3 + assemble)
    if [ -f "/app/workspace_assembler.py" ] && [ "$BASE_TENANT_ID" != "unknown" ]; then
        timeout 25 python3 /app/workspace_assembler.py \
            --tenant "$TENANT_ID" \
            --workspace "$WORKSPACE" \
            --bucket "$S3_BUCKET" \
            --stack "$STACK_NAME" \
            --region "$AWS_REGION" 2>&1 | head -5 \
            && echo "[entrypoint] Pre-Gateway workspace assembly complete" \
            || echo "[entrypoint] Pre-Gateway assembly timed out (non-fatal, Gateway will use available SOUL)"
    fi
fi

# =============================================================================
# Step 0.6: Inject IM bot tokens into openclaw.json (for always-on direct IM)
# When TELEGRAM_BOT_TOKEN or DISCORD_BOT_TOKEN env vars are set, inject them
# into openclaw.json so the Gateway connects directly to IM on startup.
# This enables Plan A: per-employee dedicated bot with direct IM connection.
# =============================================================================
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] || [ -n "${DISCORD_BOT_TOKEN:-}" ]; then
    python3 -c "
import json, os, sys

config_path = os.path.expanduser('~/.openclaw/openclaw.json')
with open(config_path) as f:
    c = json.load(f)

c.setdefault('channels', {})

tg_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
if tg_token:
    c['channels'].setdefault('telegram', {})
    c['channels']['telegram']['botToken'] = tg_token
    c['channels']['telegram'].setdefault('accounts', {}).setdefault('default', {})['botToken'] = tg_token
    print('[entrypoint] Telegram bot token injected')

dc_token = os.environ.get('DISCORD_BOT_TOKEN', '')
if dc_token:
    c['channels'].setdefault('discord', {})
    c['channels']['discord']['token'] = dc_token
    print('[entrypoint] Discord bot token injected')

with open(config_path, 'w') as f:
    json.dump(c, f, indent=2)
" 2>&1 || echo "[entrypoint] Bot token injection failed (non-fatal)"
fi

# =============================================================================
# Step 0.6: Start OpenClaw Gateway — native session management + memory
# Gateway must run BEFORE server.py so OpenClaw agent CLI can connect to it.
# Without Gateway, OpenClaw falls back to embedded mode (no memory compaction).
# With bot tokens injected above, Gateway auto-connects to IM channels.
# =============================================================================
openclaw gateway --port 18789 > /tmp/openclaw-gateway.log 2>&1 &
GATEWAY_PID=$!
echo "[entrypoint] OpenClaw Gateway PID=${GATEWAY_PID}"

# Wait for Gateway to start — but don't block server.py startup.
# AgentCore: wait up to 30s (server.py starts after this, healthcheck needs it fast)
# Fargate:   wait only 5s, then continue (server.py starts immediately for health check,
#            Gateway continues starting in background — tools available within ~25s)
if [ "$EFS_MODE" = "true" ]; then
    GATEWAY_WAIT=5
    echo "[entrypoint] Fargate mode: waiting ${GATEWAY_WAIT}s for Gateway (non-blocking)"
else
    GATEWAY_WAIT=30
    echo "[entrypoint] AgentCore mode: waiting ${GATEWAY_WAIT}s for Gateway"
fi

GATEWAY_READY=false
for i in $(seq 1 $GATEWAY_WAIT); do
    if curl -sf --connect-timeout 1 http://127.0.0.1:18789/__openclaw/control-ui-config.json >/dev/null 2>&1; then
        echo "[entrypoint] Gateway ready on port 18789 (${i}s)"
        GATEWAY_READY=true
        break
    fi
    sleep 1
done
if [ "$GATEWAY_READY" = "false" ]; then
    if [ "$EFS_MODE" = "true" ]; then
        echo "[entrypoint] Gateway still starting (Fargate: will be ready for next request)"
    else
        echo "[entrypoint] WARNING: Gateway not ready after ${GATEWAY_WAIT}s (tools may be unavailable)"
    fi
fi

# Auto-pair Control UI and store the dashboard URL token in SSM.
# `openclaw dashboard --no-open` outputs a URL with #token=xxx which is the
# one-time pairing token. We extract and store it so the admin console can
# construct the full URL for the employee.
# Determine the agent ID for SSM key (shared or personal always-on)
_AGENT_SSM_ID="${SHARED_AGENT_ID:-}"
if [ -z "$_AGENT_SSM_ID" ] && [ "$EFS_MODE" = "true" ]; then
    # Personal always-on: derive from ECS service name or tenant_id
    # The admin console writes SSM key as agent-exec-{name} or agent-{pos}-{emp}
    _AGENT_SSM_ID=$(echo "$TENANT_ID" | sed -n 's/personal__//p')
    # Try to read from SSM (admin console stores: tenants/{emp}/always-on-agent → agent-id)
    if [ -n "$_AGENT_SSM_ID" ]; then
        _LOOKED_UP=$(aws ssm get-parameter --name "/openclaw/${STACK_NAME}/tenants/${_AGENT_SSM_ID}/always-on-agent" \
            --query Parameter.Value --output text --region "$AWS_REGION" 2>/dev/null || true)
        [ -n "$_LOOKED_UP" ] && _AGENT_SSM_ID="$_LOOKED_UP"
    fi
fi
if [ "$GATEWAY_READY" = "true" ] && [ -n "$_AGENT_SSM_ID" ]; then
    DASHBOARD_OUTPUT=$(timeout 10 openclaw dashboard --no-open 2>&1 || true)
    DASHBOARD_TOKEN=$(echo "$DASHBOARD_OUTPUT" | sed -n 's/.*#token=\([a-f0-9]*\).*/\1/p' | head -1)
    if [ -n "$DASHBOARD_TOKEN" ]; then
        aws ssm put-parameter \
            --name "/openclaw/${STACK_NAME}/always-on/${_AGENT_SSM_ID}/dashboard-token" \
            --value "$DASHBOARD_TOKEN" --type "String" --overwrite \
            --region "$AWS_REGION" 2>/dev/null \
            && echo "[entrypoint] Dashboard pairing token stored in SSM" \
            || echo "[entrypoint] WARNING: Dashboard token SSM write failed"
    else
        echo "[entrypoint] No dashboard token extracted (non-fatal)"
    fi
fi

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
    aws s3 sync "${S3_BASE}/workspace/" "$WORKSPACE/" --exclude "output/*" --quiet 2>/dev/null || true

    # Detect shared agent: if tenant_id starts with "shared_" or matches a shared agent pattern
    # The tenant router sets SHARED_AGENT_ID env var for shared agents
    if [ -n "${SHARED_AGENT_ID:-}" ]; then
        echo "$SHARED_AGENT_ID" > "$WORKSPACE/.shared_agent"
        echo "[bg] Shared agent detected: $SHARED_AGENT_ID"
    fi

    # Position is resolved from DynamoDB by workspace_assembler.py below.
    # No SSM reads needed — all tenant data is in DynamoDB.
    TENANT_POSITION=""

    # Initialize SOUL.md for new tenants (workspace_assembler will overwrite with merged SOUL)
    if [ ! -f "$WORKSPACE/SOUL.md" ]; then
        aws s3 cp "s3://${S3_BUCKET}/_shared/templates/default.md" "$WORKSPACE/SOUL.md" \
            --quiet 2>/dev/null || echo "You are a helpful AI assistant." > "$WORKSPACE/SOUL.md"
    fi

    # =========================================================================
    # Workspace Assembler: Merge three-layer SOUL (Global + Position + Personal)
    # NOTE: At startup tenant=unknown, so we only do assembly if tenant is known.
    # The real assembly happens in server.py on first invocation when tenant_id is available.
    # =========================================================================
    if [ "$TENANT_ID" != "unknown" ]; then
        echo "[bg] Assembling three-layer workspace..."
        python /app/workspace_assembler.py \
            --tenant "$TENANT_ID" \
            --workspace "$WORKSPACE" \
            --bucket "$S3_BUCKET" \
            --stack "$STACK_NAME" \
            --region "$AWS_REGION" \
            --position "${TENANT_POSITION:-}" 2>&1 || echo "[bg] workspace_assembler.py failed (non-fatal)"
    else
        echo "[bg] Skipping workspace assembly (tenant=unknown, will assemble on first request)"
    fi

    # =========================================================================
    # Skill Loader: Layer 2 (S3 hot-load) + Layer 3 (pre-built bundles)
    # Layer 1 (built-in) is already in the Docker image at ~/.openclaw/skills/
    # =========================================================================
    echo "[bg] Loading enterprise skills..."
    python /app/skill_loader.py \
        --tenant "$TENANT_ID" \
        --workspace "$WORKSPACE" \
        --bucket "$S3_BUCKET" \
        --stack "$STACK_NAME" \
        --region "$AWS_REGION" 2>&1 || echo "[bg] skill_loader.py failed (non-fatal)"

    # Source skill API keys into environment (for subsequent openclaw invocations)
    if [ -f /tmp/skill_env.sh ]; then
        . /tmp/skill_env.sh
        echo "[bg] Skill API keys loaded"
    fi

    echo "[bg] Workspace + skills ready"
    echo "WORKSPACE_READY" > /tmp/workspace_status

    # Watchdog: sync workspace back to S3
    # EFS mode: EFS handles persistence — skip periodic S3 sync (zero API overhead)
    # S3 mode: sync every SYNC_INTERVAL seconds
    if [ "$EFS_MODE" = "true" ]; then
        echo "[watchdog] EFS mode active — skipping S3 sync loop (writes durable on EFS)"
        # Stay alive (entrypoint needs this subshell to keep running for SIGTERM to work)
        while true; do sleep 3600; done
    else
        while true; do
            sleep "$SYNC_INTERVAL"
            CURRENT_BASE=$(cat /tmp/base_tenant_id 2>/dev/null || echo "$BASE_TENANT_ID")
            if [ "$CURRENT_BASE" != "unknown" ] && [ -n "$CURRENT_BASE" ]; then
                SYNC_TARGET="s3://${S3_BUCKET}/${CURRENT_BASE}/workspace/"
                aws s3 sync "$WORKSPACE/" "$SYNC_TARGET" \
                    --exclude "node_modules/*" --exclude "skills/_shared/*" --exclude "skills/*" \
                    --exclude "SOUL.md" --exclude "AGENTS.md" --exclude "TOOLS.md" \
                    --exclude "IDENTITY.md" --exclude "SESSION_CONTEXT.md" --exclude "CHANNELS.md" \
                    --exclude ".personal_soul_backup.md" \
                    --exclude "knowledge/*" \
                    --size-only --region "$AWS_REGION" \
                    --quiet 2>/dev/null && echo "[watchdog] Synced to ${SYNC_TARGET}" || true
            fi
            if [ -f "$WORKSPACE/.shared_agent" ]; then
                SHARED_ID=$(cat "$WORKSPACE/.shared_agent")
                aws s3 sync "$WORKSPACE/memory/" "s3://${S3_BUCKET}/_shared/memory/${SHARED_ID}/" \
                    --quiet 2>/dev/null || true
                aws s3 cp "$WORKSPACE/MEMORY.md" "s3://${S3_BUCKET}/_shared/memory/${SHARED_ID}/MEMORY.md" \
                    --quiet 2>/dev/null || true
            fi
        done
    fi
) &
BG_PID=$!
echo "[entrypoint] Background sync PID=${BG_PID}"

# =============================================================================
# Step 3: Graceful shutdown
# =============================================================================
cleanup() {
    echo "[entrypoint] SIGTERM — flushing workspace"

    # Step 0: Deregister SSM endpoint so Tenant Router stops routing to this container
    if [ -n "${SHARED_AGENT_ID:-}" ]; then
        aws ssm delete-parameter \
            --name "/openclaw/${STACK_NAME}/always-on/${SHARED_AGENT_ID}/endpoint" \
            --region "$AWS_REGION" 2>/dev/null || true
        echo "[entrypoint] SSM endpoint deregistered for ${SHARED_AGENT_ID}"
    fi
    # Also deregister tier endpoint if FARGATE_TIER is set
    if [ -n "${FARGATE_TIER:-}" ]; then
        aws ssm delete-parameter \
            --name "/openclaw/${STACK_NAME}/fargate/tier-${FARGATE_TIER}/endpoint" \
            --region "$AWS_REGION" 2>/dev/null || true
        echo "[entrypoint] Fargate tier endpoint deregistered: tier-${FARGATE_TIER}"
    fi

    # Step 1: Stop server first — no new requests during shutdown
    kill "$SERVER_PID" 2>/dev/null || true

    # Step 2: Signal Gateway to shut down gracefully and WAIT for it to finish.
    # The Gateway writes session state (MEMORY.md) during graceful shutdown.
    # Without waiting, the final sync below runs before MEMORY.md is updated.
    kill -SIGTERM "$GATEWAY_PID" 2>/dev/null || true
    # Give Gateway up to 15s to write memory and exit cleanly
    for i in $(seq 1 15); do
        kill -0 "$GATEWAY_PID" 2>/dev/null || break
        sleep 1
    done
    kill -9 "$GATEWAY_PID" 2>/dev/null || true  # force-kill if still alive

    # Step 3: Stop background watchdog
    kill "$BG_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true

    # Step 4: Persist workspace to S3
    # EFS mode: EFS is already durable — do a minimal S3 snapshot so that if
    #   the employee later disables always-on, serverless AgentCore finds their
    #   latest memory. (Cross-mode handoff)
    # S3 mode: full sync as before.
    FINAL_BASE=$(cat /tmp/base_tenant_id 2>/dev/null || echo "$BASE_TENANT_ID")
    if [ "$FINAL_BASE" != "unknown" ] && [ -n "$FINAL_BASE" ]; then
        SYNC_TARGET="s3://${S3_BUCKET}/${FINAL_BASE}/workspace/"

        if [ "$EFS_MODE" = "true" ]; then
            # EFS → S3 snapshot: only memory + MEMORY.md (other files already in S3 from bootstrap)
            echo "[entrypoint] EFS → S3 cross-mode snapshot..."
            timeout 15 aws s3 sync "$WORKSPACE/memory/" "${SYNC_TARGET}memory/" \
                --region "$AWS_REGION" --quiet 2>/dev/null || true
            timeout 10 aws s3 cp "$WORKSPACE/MEMORY.md" "${SYNC_TARGET}MEMORY.md" \
                --region "$AWS_REGION" --quiet 2>/dev/null || true
            timeout 10 aws s3 cp "$WORKSPACE/HEARTBEAT.md" "${SYNC_TARGET}HEARTBEAT.md" \
                --region "$AWS_REGION" --quiet 2>/dev/null || true
        else
            # Standard S3 mode: full sync
            aws s3 sync "$WORKSPACE/memory/" "${SYNC_TARGET}memory/" \
                --region "$AWS_REGION" --quiet 2>/dev/null || true
            aws s3 cp "$WORKSPACE/MEMORY.md" "${SYNC_TARGET}MEMORY.md" \
                --region "$AWS_REGION" --quiet 2>/dev/null || true
            aws s3 sync "$WORKSPACE/" "$SYNC_TARGET" \
                --exclude "node_modules/*" --exclude "skills/_shared/*" --exclude "skills/*" \
                --exclude "SOUL.md" --exclude "AGENTS.md" --exclude "TOOLS.md" \
                --exclude "IDENTITY.md" --exclude ".personal_soul_backup.md" \
                --exclude "knowledge/*" \
                --size-only --region "$AWS_REGION" \
                --quiet 2>/dev/null || true
        fi
        echo "[entrypoint] Workspace persisted to ${SYNC_TARGET}"
    fi
    echo "[entrypoint] Done"
    exit 0
}
trap cleanup SIGTERM SIGINT

# Register ECS Fargate task endpoint in SSM once server is healthy.
# Runs in the background so it doesn't block the main process.
# The Tenant Router reads this SSM parameter to route requests to this task.
# Supports both per-agent endpoints (SHARED_AGENT_ID) and per-tier endpoints (FARGATE_TIER).
if [ -n "${SHARED_AGENT_ID:-}" ] && [ -n "${ECS_CONTAINER_METADATA_URI_V4:-}" ]; then
(
    # Wait up to 15s for server.py to be ready
    for i in $(seq 1 15); do
        if curl -sf "http://localhost:8080/ping" >/dev/null 2>&1; then break; fi
        sleep 1
    done
    # Get this task's private IP from ECS metadata v4
    # Try task-level endpoint first (/task), then container-level as fallback.
    # The task endpoint has the ENI details with the private IP.
    TASK_IP=""
    for META_URL in "${ECS_CONTAINER_METADATA_URI_V4}/task" "${ECS_CONTAINER_METADATA_URI_V4}"; do
        TASK_IP=$(curl -sf "$META_URL" 2>/dev/null \
            | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # Task-level: Containers[].Networks[].IPv4Addresses[]
    for c in data.get('Containers', [data]):
        for n in c.get('Networks', []):
            addrs = n.get('IPv4Addresses', [])
            if addrs:
                print(addrs[0])
                sys.exit(0)
    print('')
except Exception:
    print('')
" 2>/dev/null || echo "")
        if [ -n "$TASK_IP" ]; then break; fi
    done
    if [ -n "$TASK_IP" ]; then
        ENDPOINT="http://${TASK_IP}:8080"
        # Register per-agent endpoint (always-on dedicated agents)
        aws ssm put-parameter \
            --name "/openclaw/${STACK_NAME}/always-on/${SHARED_AGENT_ID}/endpoint" \
            --value "$ENDPOINT" --type "String" --overwrite \
            --region "$AWS_REGION" 2>/dev/null \
            && echo "[entrypoint] ECS endpoint registered: $ENDPOINT" \
            || echo "[entrypoint] WARNING: SSM endpoint registration failed"
        # Also register per-tier endpoint if FARGATE_TIER is set (Fargate-first mode)
        if [ -n "${FARGATE_TIER:-}" ]; then
            aws ssm put-parameter \
                --name "/openclaw/${STACK_NAME}/fargate/tier-${FARGATE_TIER}/endpoint" \
                --value "$ENDPOINT" --type "String" --overwrite \
                --region "$AWS_REGION" 2>/dev/null \
                && echo "[entrypoint] Fargate tier endpoint registered: tier-${FARGATE_TIER} → $ENDPOINT" \
                || echo "[entrypoint] WARNING: Fargate tier endpoint registration failed"
        fi
    else
        echo "[entrypoint] WARNING: Could not determine ECS task IP"
    fi
) &
fi

echo "[entrypoint] Waiting..."
wait "$SERVER_PID" || true
