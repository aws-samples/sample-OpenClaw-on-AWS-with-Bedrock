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
  fastapi uvicorn boto3 requests python-multipart anthropic \
  "PyJWT[crypto]>=2.8.0" "bcrypt>=4.0.0"

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
# The Gateway Chat UI and `openclaw tui` need template files (AGENTS.md, TOOLS.md, etc.)
# in $HOME/docs/reference/templates/. These ship inside the npm package.
OPENCLAW_DOCS=$(ls -d /home/ubuntu/.nvm/versions/node/*/lib/node_modules/openclaw/docs 2>/dev/null | head -1)
if [ -n "$OPENCLAW_DOCS" ] && [ -d "$OPENCLAW_DOCS" ]; then
  cp -r "$OPENCLAW_DOCS" /home/ubuntu/docs
  chown -R ubuntu:ubuntu /home/ubuntu/docs
  echo "  Workspace templates copied from $OPENCLAW_DOCS"
else
  echo "  WARN: Could not find openclaw docs — Gateway Chat may show template errors"
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

# Configure OpenClaw Gateway to route Bedrock calls through H2 Proxy.
# This is critical for enterprise multi-tenant routing: Gateway → H2 Proxy → Tenant Router → AgentCore.
# Two mechanisms ensure this works regardless of openclaw version:
# 1. openclaw.json baseUrl → http://localhost:8091 (read by some versions)
# 2. AWS_ENDPOINT_URL_BEDROCK_RUNTIME env var (forces AWS SDK to route to H2 Proxy)
GATEWAY_SERVICE="/home/ubuntu/.config/systemd/user/openclaw-gateway.service"
if [ -f "$GATEWAY_SERVICE" ]; then
  if ! grep -q "AWS_ENDPOINT_URL_BEDROCK_RUNTIME" "$GATEWAY_SERVICE"; then
    sed -i '/\[Service\]/a Environment=AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://localhost:8091' "$GATEWAY_SERVICE"
    echo "  Gateway: AWS_ENDPOINT_URL_BEDROCK_RUNTIME injected"
  fi
  # Also set EnvironmentFile so Gateway reads /etc/openclaw/env
  if ! grep -q "EnvironmentFile" "$GATEWAY_SERVICE"; then
    sed -i '/\[Service\]/a EnvironmentFile=-/etc/openclaw/env' "$GATEWAY_SERVICE"
    echo "  Gateway: EnvironmentFile added"
  fi
fi

# Set baseUrl in openclaw.json to H2 Proxy
python3 -c "
import json, os
cfg = '/home/ubuntu/.openclaw/openclaw.json'
if os.path.isfile(cfg):
    c = json.load(open(cfg))
    changed = False
    providers = c.get('models', {}).get('providers', {}).get('amazon-bedrock', {})
    if providers.get('baseUrl', '').startswith('https://bedrock-runtime'):
        providers['baseUrl'] = 'http://localhost:8091'
        changed = True
    if changed:
        json.dump(c, open(cfg, 'w'), indent=2)
        print('  Gateway: baseUrl set to http://localhost:8091')
" 2>/dev/null || true

# Reload and restart Gateway
sudo -H -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user daemon-reload 2>/dev/null || true
sudo -H -u ubuntu XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway 2>/dev/null || true
echo "  openclaw-gateway restarted"

# Configure Slack from env when both Socket Mode tokens are present.
# This keeps Slack setup reproducible across redeploys instead of relying on
# one-time UI clicks on the gateway.
if [ -n "${SLACK_BOT_TOKEN:-}" ] && [ -n "${SLACK_APP_TOKEN:-}" ]; then
  echo "  Configuring Slack channel from env"
  sudo -H -u ubuntu bash -lc '
    export HOME=/home/ubuntu
    export XDG_RUNTIME_DIR=/run/user/1000
    source /home/ubuntu/.nvm/nvm.sh
    set -o allexport
    . /etc/openclaw/env
    set +o allexport
    openclaw channels add --channel slack --bot-token "$SLACK_BOT_TOKEN" --app-token "$SLACK_APP_TOKEN"
  ' || echo "  WARN: Slack channel configuration failed"
fi

# ── Phase 6: Mount EFS for Admin Console access to always-on workspaces ────

echo ">>> Phase 6: Mounting EFS..."
STACK_NAME="${STACK_NAME:-openclaw}"
REGION="${AWS_REGION:-us-east-1}"

# Get EFS ID from CloudFormation outputs
EFS_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AlwaysOnEFSId'].OutputValue" --output text 2>/dev/null || echo "")

if [ -n "$EFS_ID" ] && [ "$EFS_ID" != "None" ]; then
  # Install EFS mount helper (amazon-efs-utils)
  apt-get install -y amazon-efs-utils 2>/dev/null || pip install botocore 2>/dev/null || true

  mkdir -p /mnt/efs
  # Check if already mounted
  if ! mountpoint -q /mnt/efs 2>/dev/null; then
    mount -t efs -o tls "$EFS_ID":/ /mnt/efs 2>/dev/null || \
    mount -t nfs4 -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 \
      "$EFS_ID.efs.$REGION.amazonaws.com":/ /mnt/efs 2>/dev/null || \
    echo "  WARN: EFS mount failed (non-fatal, admin workspace browsing unavailable)"
  fi
  # Add to fstab for persistence across reboots
  if ! grep -q "$EFS_ID" /etc/fstab 2>/dev/null; then
    echo "$EFS_ID:/ /mnt/efs efs _netdev,tls 0 0" >> /etc/fstab 2>/dev/null || \
    echo "$EFS_ID.efs.$REGION.amazonaws.com:/ /mnt/efs nfs4 nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,_netdev 0 0" >> /etc/fstab
  fi
  echo "  EFS $EFS_ID mounted at /mnt/efs"
else
  echo "  WARN: EFS ID not found in stack outputs (always-on workspace browsing unavailable)"
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "  EC2 Setup Complete!"
echo "  Services: openclaw-admin, tenant-router, bedrock-proxy-h2"
echo "  EFS: ${EFS_ID:-not mounted}"
echo "══════════════════════════════════════════════════"
echo "EC2_SETUP_COMPLETE"
