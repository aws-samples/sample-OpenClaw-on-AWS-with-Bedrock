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

## Demo 3: AWS Demo (Real Bedrock)

Runs on an EC2 instance with real Bedrock model calls. Three tenants with different permissions send messages through the full pipeline.

### Setup

```bash
# 1. Connect to your EC2 instance
aws ssm start-session --target <INSTANCE_ID> --region <REGION>
sudo su - ubuntu

# 2. Clone repo (if not already present)
git clone https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock.git
cd sample-OpenClaw-on-AWS-with-Bedrock

# 3. Run setup (installs dependencies, verifies Bedrock access)
bash demo/setup_aws_demo.sh

# 4. Run demo
python3 demo/aws_demo.py
```

Or with a specific stack name:
```bash
STACK_NAME=openclaw-bedrock python3 demo/aws_demo.py
```

### What it does

1. Creates 3 tenant permission profiles in SSM Parameter Store
2. Starts Agent Container (`server.py`) on port 8080 — same code that runs in AgentCore microVMs
3. Starts Tenant Router on port 8090 — routes messages by tenant_id
4. Sends 3 messages as different tenants (intern/engineer/admin)
5. Each message goes through: input validation → permission injection (Plan A) → Bedrock inference → response audit (Plan E)
6. Shows real Bedrock responses with per-tenant constraints

### What's real vs simulated

| Component | Demo 1 (Local) | Demo 2 (AWS) |
|-----------|----------------|--------------|
| Tenant Router | Real code | Real code |
| Permission profiles | In-memory mock | Real SSM Parameter Store |
| Input validation | Real code | Real code |
| Plan A (system prompt) | Real logic | Real code |
| Plan E (response audit) | Real code | Real code |
| LLM responses | Simulated | Real Bedrock (Nova 2 Lite) |
| Agent Container | Not running | Real server.py process |
| AgentCore microVM | Not present | Not present (runs on EC2 directly) |

In production, AgentCore Runtime provides Firecracker microVM isolation per tenant. The demo runs all tenants on the same EC2 instance to keep things simple.

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
