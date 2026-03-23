#!/bin/bash
# setup-integrations.sh
# Post-deploy setup for GitHub App, Slack, Claude Code, and security hardening.
# Called from CloudFormation UserData after core OpenClaw is installed.
# Expects environment variables: AWS_REGION, GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID,
# GITHUB_APP_KEY_SSM_PATH, GITHUB_REPO_URL, SLACK_BOT_TOKEN_SSM_PATH,
# SLACK_APP_TOKEN_SSM_PATH, ENABLE_CLAUDE_CODE, OPENCLAW_MODEL

set -e
export HOME=/home/ubuntu

echo "=== Integration Setup: $(date) ==="

# ── GitHub App credentials ──────────────────────────────────────────────
if [ -n "$GITHUB_APP_ID" ]; then
  echo "[integrations] Configuring GitHub App credentials..."

  # Verify SSM parameter is accessible
  aws ssm get-parameter --name "$GITHUB_APP_KEY_SSM_PATH" --with-decryption --region "$AWS_REGION" > /dev/null 2>&1 \
    || { echo "ERROR: Cannot read GitHub App key from SSM: $GITHUB_APP_KEY_SSM_PATH"; exit 1; }

  # Write credential helper script (fetches key from SSM at runtime)
  cat > /home/ubuntu/git-credential-github-app.py << 'GHCREDEOF'
#!/usr/bin/env python3
import json, time, urllib.request, subprocess, base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

APP_ID = "PLACEHOLDER_APP_ID"
INSTALLATION_ID = "PLACEHOLDER_INSTALLATION_ID"
SSM_KEY_PATH = "PLACEHOLDER_SSM_KEY_PATH"
AWS_REGION = "PLACEHOLDER_REGION"

def get_pem():
    out = subprocess.check_output([
        "aws", "ssm", "get-parameter",
        "--name", SSM_KEY_PATH,
        "--with-decryption",
        "--query", "Parameter.Value",
        "--output", "text",
        "--region", AWS_REGION
    ])
    return out.decode().strip()

def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")

def create_jwt(pem_str):
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": APP_ID}).encode())
    key = serialization.load_pem_private_key(pem_str.encode(), password=None)
    sig = key.sign(header + b"." + payload, padding.PKCS1v15(), hashes.SHA256())
    return (header + b"." + payload + b"." + b64url(sig)).decode()

def get_token():
    pem = get_pem()
    jwt = create_jwt(pem)
    url = f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
    req = urllib.request.Request(url, data=b"{}", headers={
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json"
    })
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())["token"]

if __name__ == "__main__":
    token = get_token()
    print("username=x-access-token")
    print(f"password={token}")
GHCREDEOF

  # Replace placeholders
  sed -i "s/PLACEHOLDER_APP_ID/$GITHUB_APP_ID/" /home/ubuntu/git-credential-github-app.py
  sed -i "s/PLACEHOLDER_INSTALLATION_ID/$GITHUB_APP_INSTALLATION_ID/" /home/ubuntu/git-credential-github-app.py
  sed -i "s|PLACEHOLDER_SSM_KEY_PATH|$GITHUB_APP_KEY_SSM_PATH|" /home/ubuntu/git-credential-github-app.py
  sed -i "s/PLACEHOLDER_REGION/$AWS_REGION/" /home/ubuntu/git-credential-github-app.py

  chmod 700 /home/ubuntu/git-credential-github-app.py
  chown ubuntu:ubuntu /home/ubuntu/git-credential-github-app.py

  # Install cryptography library
  pip3 install --break-system-packages cryptography 2>/dev/null || true

  # Configure git
  cat > /home/ubuntu/.gitconfig << 'GITCFGEOF'
[credential]
	helper = /home/ubuntu/git-credential-github-app.py
[user]
	name = OpenClaw Dev Bot[bot]
	email = openclaw-dev-bot[bot]@users.noreply.github.com
GITCFGEOF
  chmod 600 /home/ubuntu/.gitconfig
  chown ubuntu:ubuntu /home/ubuntu/.gitconfig

  # Clone the repo if URL provided
  if [ -n "$GITHUB_REPO_URL" ]; then
    sudo -u ubuntu git clone "$GITHUB_REPO_URL" /home/ubuntu/repo || echo "Git clone failed"
  fi

  echo "[integrations] GitHub App configured"
fi

# ── Slack ───────────────────────────────────────────────────────────────
if [ -n "$SLACK_BOT_TOKEN_SSM_PATH" ]; then
  echo "[integrations] Configuring Slack..."

  SLACK_BOT=$(aws ssm get-parameter --name "$SLACK_BOT_TOKEN_SSM_PATH" --with-decryption --query Parameter.Value --output text --region "$AWS_REGION")
  SLACK_APP=$(aws ssm get-parameter --name "$SLACK_APP_TOKEN_SSM_PATH" --with-decryption --query Parameter.Value --output text --region "$AWS_REGION")

  sudo -u ubuntu python3 << SLACKPY
import json
with open("/home/ubuntu/.openclaw/openclaw.json") as f:
    c = json.load(f)
c.setdefault("channels", {})
c["channels"]["slack"] = {
    "mode": "socket",
    "enabled": True,
    "botToken": "$SLACK_BOT",
    "appToken": "$SLACK_APP",
    "groupPolicy": "allowlist",
    "dmPolicy": "open",
    "allowFrom": ["*"],
    "streaming": "partial",
    "nativeStreaming": True
}
with open("/home/ubuntu/.openclaw/openclaw.json", "w") as f:
    json.dump(c, f, indent=2)
print("Slack configured")
SLACKPY

  unset SLACK_BOT SLACK_APP
  echo "[integrations] Slack configured"
fi

# ── Claude Code + ACPX ─────────────────────────────────────────────────
if [ "$ENABLE_CLAUDE_CODE" = "true" ]; then
  echo "[integrations] Installing Claude Code + ACPX..."

  # Install Claude Code
  sudo -u ubuntu bash << 'CCINSTALL'
export HOME=/home/ubuntu
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
npm install -g @anthropic-ai/claude-code@latest --timeout=300000 || echo "Claude Code install failed"
CCINSTALL

  # Upgrade ACPX in plugin extensions
  OPENCLAW_MJS=$(find /home/ubuntu/.nvm -path "*/node_modules/openclaw/openclaw.mjs" 2>/dev/null | head -1)
  OPENCLAW_DIR=$(dirname "$OPENCLAW_MJS" 2>/dev/null)
  if [ -n "$OPENCLAW_DIR" ] && [ -d "$OPENCLAW_DIR/extensions/acpx" ]; then
    sudo -u ubuntu bash << ACPXINSTALL
export HOME=/home/ubuntu
export NVM_DIR="\$HOME/.nvm"
[ -s "\$NVM_DIR/nvm.sh" ] && . "\$NVM_DIR/nvm.sh"
cd "$OPENCLAW_DIR/extensions/acpx"
npm install acpx@latest --omit=dev 2>&1 || echo "ACPX upgrade failed"
echo "ACPX version: \$(node_modules/.bin/acpx --version 2>&1)"
ACPXINSTALL
  fi

  # Install acpx globally
  sudo -u ubuntu bash << 'ACPXGLOBAL'
export HOME=/home/ubuntu
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
npm install -g acpx@latest 2>&1 || echo "Global acpx install failed"
ACPXGLOBAL

  # ACPX config
  sudo -u ubuntu mkdir -p /home/ubuntu/.acpx
  cat > /home/ubuntu/.acpx/config.json << 'ACPXCFG'
{"defaultAgent":"claude","defaultPermissions":"approve-all","nonInteractivePermissions":"deny","authPolicy":"skip","ttl":300}
ACPXCFG
  chown ubuntu:ubuntu /home/ubuntu/.acpx/config.json
  chmod 600 /home/ubuntu/.acpx/config.json

  # Claude Code workspace trust
  sudo -u ubuntu mkdir -p /home/ubuntu/.claude
  cat > /home/ubuntu/.claude/settings.json << 'CCSETTINGS'
{"trustedDirectories":["/home/ubuntu/repo"]}
CCSETTINGS
  chown ubuntu:ubuntu /home/ubuntu/.claude/settings.json

  # Update OpenClaw config with ACPX plugin
  sudo -u ubuntu python3 << 'ACPXPY'
import json
with open("/home/ubuntu/.openclaw/openclaw.json") as f:
    c = json.load(f)
c.setdefault("plugins", {})
c["plugins"]["allow"] = ["acpx", "telegram", "whatsapp", "discord", "googlechat", "slack", "imessage"]
c["plugins"].setdefault("entries", {})
c["plugins"]["entries"]["acpx"] = {
    "enabled": True,
    "config": {
        "permissionMode": "approve-all",
        "nonInteractivePermissions": "deny"
    }
}
with open("/home/ubuntu/.openclaw/openclaw.json", "w") as f:
    json.dump(c, f, indent=2)
print("ACPX plugin configured")
ACPXPY

  echo "[integrations] Claude Code + ACPX configured (run 'claude' interactively to authenticate)"
fi

# ── Harden permissions ──────────────────────────────────────────────────
echo "[integrations] Hardening file permissions..."
chmod 600 /home/ubuntu/.openclaw/openclaw.json 2>/dev/null || true
chmod 600 /home/ubuntu/.gitconfig 2>/dev/null || true
chmod 700 /home/ubuntu/.openclaw 2>/dev/null || true
chmod 700 /home/ubuntu/.claude 2>/dev/null || true
chmod 700 /home/ubuntu/.acpx 2>/dev/null || true

# Add systemd env vars to bashrc for SSM sessions
if ! grep -q XDG_RUNTIME_DIR /home/ubuntu/.bashrc 2>/dev/null; then
  cat >> /home/ubuntu/.bashrc << 'BASHRCEOF'

# Systemd user session env vars
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
BASHRCEOF
fi

echo "=== Integration Setup Complete: $(date) ==="
