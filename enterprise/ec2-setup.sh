#!/bin/bash
# =============================================================================
# OpenClaw Enterprise — EC2 Service Setup
#
# Runs ON the EC2 instance (invoked by deploy.sh via SSM).
# Reads config from /etc/openclaw/env (written by deploy.sh step 7 via SSM),
# then builds the admin console, installs gateway files, and starts all services.
#
# Prerequisites:
#   - /etc/openclaw/env must exist (written by deploy.sh before this script runs)
#   - Node.js 22 installed via NVM (done by CloudFormation UserData)
#   - services.tar.gz already extracted in working directory
#
# Idempotent — safe to re-run.
# =============================================================================
set -ex

# ── Load config from /etc/openclaw/env ───────────────────────────────────────

if [ ! -f /etc/openclaw/env ]; then
  echo "ERROR: /etc/openclaw/env not found. deploy.sh step 7 should write this file."
  exit 1
fi

set -o allexport
. /etc/openclaw/env
set +o allexport

echo "══════════════════════════════════════════════════"
echo "  OpenClaw EC2 Setup — $STACK_NAME ($AWS_REGION)"
echo "══════════════════════════════════════════════════"
echo "  S3_BUCKET=$S3_BUCKET"
echo "  DYNAMODB_TABLE=$DYNAMODB_TABLE"
echo "  AGENTCORE_RUNTIME_ID=$AGENTCORE_RUNTIME_ID"
echo "  BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID"
echo "  ECS_CLUSTER=$ECS_CLUSTER"

# ── Phase 1: Install system dependencies ─────────────────────────────────────

echo ">>> Phase 1: Installing system dependencies..."

apt-get update -qq
apt-get install -y python3.12-venv 2>/dev/null || true

# Upgrade boto3/botocore for system python (tenant_router uses system python)
pip3 install --break-system-packages --upgrade boto3 botocore 2>/dev/null || true

# ── Phase 2: Build admin console frontend ────────────────────────────────────

echo ">>> Phase 2: Building admin console frontend..."

# Run npm as the ubuntu user — NVM is installed under /home/ubuntu and npm
# writes cache/config to $HOME. Running as root would pollute /root/.npm and
# create root-owned files that the ubuntu user can't manage later.
ADMIN_CONSOLE_DIR="$(pwd)/enterprise/admin-console"
chown -R ubuntu:ubuntu "$ADMIN_CONSOLE_DIR"
su - ubuntu -c "source /home/ubuntu/.nvm/nvm.sh && cd '$ADMIN_CONSOLE_DIR' && npm install --no-audit --no-fund && npx vite build"

# ── Phase 3: Set up Python venv ──────────────────────────────────────────────

echo ">>> Phase 3: Setting up Python venv..."

python3 -m venv /opt/admin-venv
/opt/admin-venv/bin/pip install --upgrade pip
/opt/admin-venv/bin/pip install \
  fastapi uvicorn boto3 requests python-multipart anthropic

# ── Phase 4: Install files ───────────────────────────────────────────────────

echo ">>> Phase 4: Installing files..."

# Admin console → /opt/admin-console/
mkdir -p /opt/admin-console
rm -rf /opt/admin-console/dist /opt/admin-console/server
cp -r enterprise/admin-console/dist    /opt/admin-console/dist
cp -r enterprise/admin-console/server  /opt/admin-console/server
cp    enterprise/admin-console/start.sh /opt/admin-console/start.sh
chmod +x /opt/admin-console/start.sh
chown -R ubuntu:ubuntu /opt/admin-console /opt/admin-venv

# Gateway files → /home/ubuntu/
cp enterprise/gateway/tenant_router.py    /home/ubuntu/tenant_router.py
cp enterprise/gateway/bedrock_proxy_h2.js /home/ubuntu/bedrock_proxy_h2.js
chown ubuntu:ubuntu /home/ubuntu/tenant_router.py /home/ubuntu/bedrock_proxy_h2.js

# OpenClaw workspace templates → /home/ubuntu/docs/
# OpenClaw expects template files (AGENTS.md, TOOLS.md, etc.) in $HOME/docs/reference/templates/
# These ship inside the npm package but must be copied to $HOME for the gateway to find them.
OPENCLAW_PKG_DIR=$(su - ubuntu -c 'source ~/.nvm/nvm.sh && node -e "console.log(require.resolve(\"openclaw/package.json\").replace(\"/package.json\",\"\"))"' 2>/dev/null || true)
if [ -n "$OPENCLAW_PKG_DIR" ] && [ -d "$OPENCLAW_PKG_DIR/docs" ]; then
  cp -r "$OPENCLAW_PKG_DIR/docs" /home/ubuntu/docs
  chown -R ubuntu:ubuntu /home/ubuntu/docs
  echo "  Copied workspace templates from $OPENCLAW_PKG_DIR/docs"
else
  echo "  WARN: Could not locate openclaw package docs — openclaw tui may fail"
fi

# ── Phase 5: Install and start systemd services ─────────────────────────────

echo ">>> Phase 5: Installing systemd services..."

# Admin console service
cat > /etc/systemd/system/openclaw-admin.service << 'SVCEOF'
[Unit]
Description=OpenClaw Admin Console
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/admin-console/server
EnvironmentFile=-/etc/openclaw/env
ExecStart=/opt/admin-console/start.sh
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

# Gateway services (from repo)
cp enterprise/gateway/tenant-router.service    /etc/systemd/system/tenant-router.service
cp enterprise/gateway/bedrock-proxy-h2.service /etc/systemd/system/bedrock-proxy-h2.service

# Reload and start all services
systemctl daemon-reload
for svc in openclaw-admin tenant-router bedrock-proxy-h2; do
  systemctl enable "$svc"
  systemctl restart "$svc"
  echo "  $svc → $(systemctl is-active "$svc")"
done

# Also restart the main OpenClaw gateway (picks up env changes)
systemctl restart openclaw-gateway 2>/dev/null || true

echo ""
echo "══════════════════════════════════════════════════"
echo "  EC2 Setup Complete!"
echo "  Services: openclaw-admin, tenant-router, bedrock-proxy-h2"
echo "══════════════════════════════════════════════════"
echo "EC2_SETUP_COMPLETE"
