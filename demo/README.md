# Multi-Tenant Platform Demos

Three demos, from simple to visual:

| Demo | What it is | Requirements |
|------|-----------|-------------|
| `run_demo.py` | Terminal-based, 7 scenarios | Python 3.10+, boto3 |
| `aws_demo.py` | Real Bedrock inference on EC2 | EC2 instance + Bedrock |
| `console.py` | Visual admin console in browser | Python 3.10+ |

---

## Demo 1: Admin Console (Visual — Recommended)

A full management console with dashboard, tenant management, approval queue, audit log, and live chat demo.

```bash
python3 demo/console.py
```

Open http://localhost:8099 in your browser.

No AWS account needed — runs with demo data locally.

### What you can do

- **Dashboard**: See tenant count, active agents, requests, violations, pending approvals
- **Tenants**: View all tenants, click to edit permissions (toggle tools on/off)
- **Approvals**: Review pending permission requests, approve or reject with one click
- **Audit Log**: See structured event stream — invocations, denials, approval decisions
- **Live Demo**: Send messages as different tenants, see Plan A (system prompt injection) and Plan E (response audit) in real time

### Story walkthrough

1. Open Dashboard — see 5 tenants, 2 pending approvals, 2 violations
2. Go to Tenants — Sarah (intern) has only `web_search`; Alex (engineer) has full access
3. Go to Live Demo — send "Run ls -la" as Sarah → blocked. Send same as Alex → allowed
4. Send "Install a skill" as Jordan (admin) → always blocked (supply-chain protection)
5. Go to Approvals — approve Sarah's shell request
6. Back to Live Demo — send "Run ls -la" as Sarah again → now allowed
7. Check Audit Log — see the full trail: denial → approval → success

---

## Demo 2: Terminal Demo (No AWS)

Demonstrates the permission, audit, and approval logic with mocked AWS services in the terminal.

```bash
python3 demo/run_demo.py
```

Requirements: Python 3.10+ with `boto3` (`pip install boto3`). No AWS credentials needed.

---

## Demo 3: AWS Demo (Real Bedrock + AgentCore microVM)

Runs the full multi-tenant pipeline: Gateway OpenClaw → H2 Proxy → Tenant Router → AgentCore Firecracker microVM → OpenClaw CLI → Bedrock.

### Architecture

```
Your laptop                          EC2 Gateway                    AgentCore (Serverless)
─────────                            ───────────                    ──────────────────────
SSM port forward ──────────────────→ OpenClaw Gateway (18789)
                                       │ AWS SDK Bedrock call
                                       ▼
                                     H2 Proxy (8091) ──────────→ Tenant Router (8090)
                                       intercepts HTTP/2            │ derive tenant_id
                                       extracts message             │ invoke AgentCore
                                                                    ▼
                                                              Firecracker microVM
                                                                │ entrypoint.sh
                                                                │ server.py
                                                                │ openclaw agent CLI
                                                                │ → Bedrock Nova 2 Lite
                                                                ▼
                                                              "Hello! How can I help?"
                                                              ← response returns ←
```

### Value Delivered

| Metric | Value |
|--------|-------|
| Cost per user (50 users) | ~$1.30-2.20/month vs $20/month ChatGPT Plus |
| Tenant isolation | Firecracker microVM (hardware-level) |
| Cold start latency | ~30s (first request per tenant) |
| Warm request latency | ~10s (microVM already running) |
| OpenClaw code changes | Zero (all management via external layers) |
| IM channel setup | Same as standard OpenClaw deployment |

### Setup

```bash
# 1. Connect to EC2 via SSM
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-multitenancy --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text)

aws ssm start-session --target $INSTANCE_ID --region us-east-1

# 2. On EC2: verify all services are running
sudo systemctl status openclaw-gateway openclaw-proxy
ss -tlnp | grep -E '(18789|8090|8091)'

# 3. If services are not running:
sudo systemctl start openclaw-proxy
sudo systemctl start openclaw-gateway
# Tenant Router (if not running):
sudo -u ubuntu bash -c 'AWS_REGION=us-east-1 STACK_NAME=openclaw-multitenancy python3 /home/ubuntu/tenant_router.py >> /tmp/tenant_router.log 2>&1 &'
```

### Demo Script: End-to-End Multi-Tenant Test

```bash
# On EC2 (via SSM session):

# === Step 1: Verify services ===
echo "=== Services ==="
ss -tlnp | grep -E '(18789|8090|8091|8092)'
# Expected: 4 ports listening (18789, 8090, 8091, 8092)

# === Step 2: Test Bedrock Proxy directly (HTTP/1.1) ===
echo "=== Direct proxy test ==="
curl -s --max-time 120 -X POST http://localhost:8091/model/test/converse \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":[{"text":"say hello"}]}],"system":[{"text":"channel: demo sender: employee-alice"}]}'
# Expected: {"output":{"message":{"role":"assistant","content":[{"text":"Hello! How can I help you today?"}]}}}

# === Step 3: Test via OpenClaw Gateway (full chain) ===
echo "=== Full chain test (Employee A) ==="
sudo -u ubuntu bash -c 'export PATH=/home/ubuntu/.nvm/versions/node/v22.22.1/bin:$PATH HOME=/home/ubuntu AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://localhost:8091 && openclaw agent --session-id employee-alice-001 --message "What can you help me with?" --json' | head -20

echo "=== Full chain test (Employee B - different tenant) ==="
sudo -u ubuntu bash -c 'export PATH=/home/ubuntu/.nvm/versions/node/v22.22.1/bin:$PATH HOME=/home/ubuntu AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://localhost:8091 && openclaw agent --session-id employee-bob-001 --message "Hello, who are you?" --json' | head -20

# === Step 4: Verify tenant isolation in logs ===
echo "=== Tenant Router log (different tenant_ids) ==="
tail -10 /tmp/tenant_router.log | grep tenant_id

# === Step 5: Check AgentCore microVM logs ===
echo "=== AgentCore container logs ==="
aws logs filter-log-events \
  --log-group-name '/aws/bedrock-agentcore/runtimes/openclaw_multitenancy_runtime-olT3WX54rJ-DEFAULT' \
  --start-time $(python3 -c 'import time; print(int((time.time()-300)*1000))') \
  --filter-pattern "Invocation" --limit 5 --region us-east-1 \
  --query 'events[*].message' --output text
```

### Demo Script: IM Channel Test (Telegram)

```bash
# On your laptop:

# 1. Port forward to EC2
aws ssm start-session --target $INSTANCE_ID --region us-east-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# 2. Get gateway token
TOKEN=$(aws ssm get-parameter \
  --name "/openclaw/openclaw-multitenancy/gateway-token" \
  --with-decryption --query Parameter.Value --output text --region us-east-1)

# 3. Open Web UI: http://localhost:18789/?token=$TOKEN
# 4. Go to Channels → Add Channel → Telegram
# 5. Paste your Telegram bot token → Save & Reload
# 6. Send a message to your Telegram bot
# 7. The response comes from a Firecracker microVM via AgentCore!

# Verify in proxy log (on EC2):
tail -5 /tmp/bedrock_proxy_h2.log
# You should see: Request: /model/global.amazon.nova-2-lite-v1%3A0/converse-stream
#                 channel=... user=... msg=<your message>
#                 Response: <AI response>
```

---

### What's Real vs Simulated

| Component | Demo 1 (Console) | Demo 2 (Terminal) | Demo 3 (AWS E2E) |
|-----------|------------------|-------------------|-------------------|
| Tenant Router | In-memory mock | In-memory mock | Real (port 8090) |
| Permission profiles | In-memory | In-memory | Real SSM Parameter Store |
| Plan A + Plan E | Real logic | Real logic | Real code |
| LLM responses | Simulated | Simulated | Real Bedrock Nova 2 Lite |
| Bedrock H2 Proxy | N/A | N/A | Real (port 8091, HTTP/2) |
| Agent Container | N/A | N/A | Real (in Firecracker microVM) |
| AgentCore microVM | N/A | N/A | **Real Firecracker isolation** |
| IM channels | N/A | N/A | Real (Telegram/WhatsApp/Discord) |
| S3 workspace sync | N/A | N/A | Real (SOUL.md, MEMORY.md) |

---

## Three Tenants

| Tenant | Channel | Profile | Allowed Tools |
|--------|---------|---------|--------------|
| Intern (Sarah) | WhatsApp | basic | web_search only |
| Engineer (Alex) | Telegram | advanced | web_search, shell, browser, file, file_write, code_execution |
| Admin (Jordan) | Discord | advanced | Same as engineer (install_skill still blocked) |

---

## Next Steps

- Deploy the full platform: [README_AGENTCORE.md](../README_AGENTCORE.md)
- Roadmap: [ROADMAP.md](../ROADMAP.md)
- Contribute: [CONTRIBUTING.md](../CONTRIBUTING.md)
