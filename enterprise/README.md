# OpenClaw Enterprise on AgentCore

Turn [OpenClaw](https://github.com/openclaw/openclaw) from a personal AI assistant into an enterprise-grade digital workforce platform — without modifying a single line of OpenClaw source code.

## 🦞 Live Demo

> **https://openclaw.awspsa.com**
>
> A real running instance with 7 top-level + 6 sub-departments (13 total), 11 positions, 22 employees, 23 AI agents (20 personal + 3 shared), 26 role-filtered skills, and 12 knowledge documents — all backed by DynamoDB + S3 on AWS.
>
> This is not a mockup. Every button works, every chart reads from real data, every agent runs on Bedrock AgentCore in isolated Firecracker microVMs. Discord Bot connected with real SOUL injection and cross-session memory persistence verified.
>
> **Verified features:** 3-layer SOUL injection · Per-role tool permissions (Plan A + Plan E) · Cross-session memory via S3 · OpenClaw Gateway mode in microVM · AgentCore Firecracker isolation · All-channel audit logging (Portal + Discord + Telegram) · IT Admin Assistant (Claude API, 10 whitelisted tools) · Secrets in SSM SecureString · Agent Playground with live file editing
>
> Need a demo account? Contact [wjiad@aws](mailto:wjiad@amazon.com) to get access.

## Screenshots

| Admin Dashboard | Employee Portal Chat |
|:-:|:-:|
| ![Admin Dashboard](demo/images/04-admin-dashboard.jpeg) | ![Portal Chat](demo/images/01-portal-chat-permission-denied.jpeg) |

| Agent Factory | Workspace & SOUL Editor |
|:-:|:-:|
| ![Agent Factory](demo/images/03-agent-factory-list.jpeg) | ![SOUL Editor](demo/images/05-workspace-manager-soul.jpeg) |

| Usage & Cost | Skill Platform |
|:-:|:-:|
| ![Usage & Cost](demo/images/02-usage-cost-dashboard.jpeg) | ![Skill Platform](demo/images/08-skill-platform-catalog.jpeg) |

| Audit & AI Insights | Employee Profile |
|:-:|:-:|
| ![Audit Center](demo/images/07-audit-center-ai-insights.jpeg) | ![Employee Profile](demo/images/06-portal-profile-preferences.jpeg) |

## The Problem

OpenClaw is one of the most capable open-source AI agent platforms (200k+ GitHub stars). It excels at personal productivity: connecting AI to WhatsApp, Telegram, Discord, running browser automation, managing calendars. But enterprise deployments need:

- **Multi-tenant isolation** — each employee gets their own agent with separate identity, memory, and permissions
- **Role-based access control** — interns can't run shell commands, finance can't access engineering data
- **Centralized governance** — IT controls agent behavior, skills, and model selection across the organization
- **Audit & compliance** — every agent action logged, PII detection, data sovereignty
- **Cost management** — per-department budgets, model routing, usage tracking

## The Solution

A management layer that wraps OpenClaw with enterprise controls, deployed on AWS Bedrock AgentCore. No fork, no patch, no vendor lock-in — just configuration files and AWS-native services.

### Design Principles

#### 1. Zero Invasion to OpenClaw

We don't fork, patch, or modify a single line of OpenClaw source code. Instead, we control agent behavior entirely through OpenClaw's native workspace file system:

```
workspace/
├── SOUL.md      ← Agent identity & rules (assembled from 3 layers)
├── AGENTS.md    ← Workflow definitions
├── TOOLS.md     ← Tool permissions
├── USER.md      ← Employee preferences
├── MEMORY.md    ← Persistent memory
├── memory/      ← Daily memory files
├── knowledge/   ← Position-scoped documents
└── skills/      ← Role-filtered skill packages
```

The `workspace_assembler` merges Global + Position + Personal layers into these files before OpenClaw reads them. OpenClaw doesn't know it's running in an enterprise context — it just reads its workspace as usual. This means:

- Upgrade OpenClaw independently without breaking enterprise controls
- No maintenance burden from maintaining a fork
- All OpenClaw community plugins and skills work out of the box
- Enterprise logic is fully decoupled and portable

#### 2. Serverless-First Architecture

Each agent runs in an isolated Firecracker microVM via Bedrock AgentCore. There are no long-running servers per tenant — microVMs cold-start in ~5 seconds, execute the request, and auto-scale to zero when idle.

| Traditional Approach | Our Approach |
|---------------------|-------------|
| 1 container per agent, always running | 1 microVM per request, auto-released |
| 20 agents = 20 containers = $400+/mo | 20 agents = 0 idle cost, pay per invocation |
| Manual scaling, capacity planning | Automatic scaling, zero ops |
| Shared process = blast radius risk | Firecracker isolation = hardware-level security |

State persists between sessions via S3 (workspace files, memory) and DynamoDB (usage, audit). The microVM is stateless and disposable — if it crashes, the next request gets a fresh VM with the same workspace from S3.

**OpenClaw Gateway runs inside each microVM** — enabling native session management, multi-turn memory compaction, and cross-session memory persistence. Zero modification to OpenClaw's source code.

#### 3. Enterprise-Grade Governance

Every aspect of agent behavior is centrally managed and auditable:

- **Identity control** — IT defines who the agent is (SOUL.md), what it can do (TOOLS.md), and what it knows (knowledge/). Employees can customize preferences but cannot override security policies.
- **Permission enforcement** — Plan A (pre-execution): SOUL.md declares allowed/blocked tools. Plan E (post-audit): response scanner detects unauthorized tool usage.
- **Skill governance** — 26 skills with `allowedRoles`/`blockedRoles` in manifests. Finance gets excel-gen but not shell. SDE gets github-pr but not email-send. IT controls the catalog.
- **Data scoping** — Manager APIs return only their department's data (BFS sub-department rollup). Employees see only their own agent, usage, and memory. No data leaks across organizational boundaries.
- **Auto-provisioning** — New employee + position assignment → system auto-creates agent, binding, shared agent connections, and audit trail. Zero manual setup for IT.

#### 4. Security & Audit by Design

Security is not a feature — it's the architecture:

- **No open ports** — Admin Console accessed via SSM port forwarding or CloudFront with origin restricted to CloudFront managed prefix list. No security group rules exposing ports to the internet.
- **No hardcoded credentials** — Login password (`ADMIN_PASSWORD`) and JWT signing secret (`JWT_SECRET`) are stored in AWS SSM Parameter Store as `SecureString` (AES-256 encrypted). The systemd service file contains no secrets — it calls a startup wrapper script that fetches credentials from SSM at boot. No secrets in source code or service files.
- **Tenant isolation** — Each employee's agent runs in a separate Firecracker microVM with its own filesystem, network namespace, and memory space. One compromised agent cannot affect another.
- **IAM least privilege** — AgentCore execution role has only the permissions it needs: DynamoDB read/write, S3 read/write, SSM read, Bedrock invoke. No admin access, no wildcard policies.
- **Comprehensive audit trail** — Every agent invocation, tool execution, permission denial, SOUL change, and admin action is logged to DynamoDB with actor, timestamp, and detail. AI Insights scanner detects anomalies (unusual hours, excessive tool usage, SOUL version drift).
- **Memory privacy** — Employee memory files (MEMORY.md, daily memories) are private. Managers see aggregated usage stats but cannot read memory content. Memory writeback excludes assembled files (SOUL.md, AGENTS.md, TOOLS.md) to prevent employees from overriding IT policies.

#### 5. File-First Knowledge

Following OpenClaw's philosophy, knowledge is stored as Markdown files in S3 — not in a vector database. Agents read documents via the `workspace/knowledge/` directory.

- Zero infrastructure cost (no OpenSearch, no Pinecone, no embedding pipeline)
- Human-readable and auditable (it's just Markdown)
- Scope-controlled (Engineering docs vs. Finance docs vs. HR docs)
- Upload via Admin Console, full-text search across all documents
- Bedrock Knowledge Base integration planned for v1.1 (hybrid retrieval)

#### 6. Three-Layer SOUL Architecture

Agent identity is composed from three layers, each managed by a different stakeholder:

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: GLOBAL (IT locked — CISO + CTO approval)      │
│  Company policies, security red lines, data handling     │
│  "Never share customer PII. Never execute rm -rf."       │
├─────────────────────────────────────────────────────────┤
│  Layer 2: POSITION (Department admin managed)            │
│  Role expertise, tool permissions, knowledge scope       │
│  "You are a Finance Analyst. Use excel-gen, not shell."  │
├─────────────────────────────────────────────────────────┤
│  Layer 3: PERSONAL (Employee self-service)               │
│  Communication preferences, custom instructions          │
│  "I prefer concise answers. Always use TypeScript."      │
└─────────────────────────────────────────────────────────┘
                        ↓ merge
              Final SOUL.md (what OpenClaw reads)
```

The merged SOUL.md is what the agent reads. An SA agent and a Finance agent use the same LLM but have completely different identities, capabilities, and boundaries. The merge order ensures Global rules always take precedence — employees cannot override security policies through personal preferences.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Admin Console (React + FastAPI)                             │
│  ├── 24 pages (19 admin + 5 portal) + M3 Expressive design  │
│  ├── 3-role RBAC (admin / manager / employee)                │
│  ├── Agent Playground — live testing + employee file editor  │
│  ├── IT Admin Assistant — Claude API, 10 whitelisted tools   │
│  ├── Secrets in AWS SSM SecureString (no plaintext in files) │
│  └── All data from DynamoDB + S3 (zero hardcoded values)     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  PATH A: Employee Agents (via AgentCore)                     │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  IM Message (Discord/Telegram/Slack/WhatsApp/Portal)  │    │
│  │       ↓                                               │    │
│  │  OpenClaw Gateway (port 18789)                        │    │
│  │       ↓                                               │    │
│  │  H2 Proxy (port 8091) — intercepts Bedrock SDK call   │    │
│  │       ↓ extracts sender_id from JSON metadata         │    │
│  │  Tenant Router (port 8090) — derives tenant_id        │    │
│  │       ↓                                               │    │
│  │  AgentCore Runtime (Firecracker microVM per tenant)   │    │
│  │       ↓ workspace_assembler.py → 3-layer SOUL merge   │    │
│  │  OpenClaw CLI → Bedrock (in microVM)                  │    │
│  │       ↓                                               │    │
│  │  Response → H2 Proxy → Gateway → IM channel           │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  PATH B: IT Admin Assistant (Claude API, no subprocess)      │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  Admin Console chat bubble (admin role only)          │    │
│  │       ↓ POST /api/v1/admin-ai/chat                    │    │
│  │  FastAPI (require_role: admin)                         │    │
│  │       ↓ boto3 bedrock-runtime.converse()              │    │
│  │  Claude Haiku via Bedrock (10 whitelisted tools)      │    │
│  │  Tools: list_employees, get_soul_template,            │    │
│  │         update_soul_template, get_usage_report,       │    │
│  │         get_service_health, get_audit_log, ...        │    │
│  │  No shell · No subprocess · All ops via Python fns    │    │
│  │       ↓                                               │    │
│  │  Response → FastAPI → Admin Console                   │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│  AWS Services                                                │
│  ├── DynamoDB — org, agents, bindings, audit, usage, config  │
│  ├── S3 — SOUL templates, skills, workspaces, knowledge      │
│  ├── SSM — tenant→position mappings, user-mappings           │
│  ├── Bedrock — LLM inference (Nova 2 Lite default)           │
│  └── CloudWatch — agent invocation logs, runtime events      │
└─────────────────────────────────────────────────────────────┘
```

**Path A** is for all employee agents — messages go through the Gateway → H2 Proxy → Tenant Router → AgentCore microVM pipeline. Each employee gets an isolated Firecracker microVM with their personalized SOUL, skills, and memory.

**Path B** is for the IT Admin Assistant only — a floating chat bubble visible only to admin-role users. It calls `POST /api/v1/admin-ai/chat` which invokes Claude via Bedrock Converse API directly from FastAPI (boto3), with a whitelist of 10 read/write tools implemented as Python functions. No subprocess, no OpenClaw, no Gateway dependency. Write operations (e.g. `update_soul_template`) create audit log entries automatically. Conversation history is maintained server-side per admin user and resets on service restart.

## Gateway Architecture: One Bot, All Employees

A single OpenClaw Gateway on EC2 serves as the unified IM connection layer for the entire organization. IT Admin creates one Bot per IM platform (one Discord Bot, one Telegram Bot, one Slack App), and all employees share it.

```
IT Admin (one-time setup):
  Discord  → Create 1 Bot "ACME Agent" → Connect to Gateway
  Telegram → Create 1 Bot @acme_bot    → Connect to Gateway
  Slack    → Create 1 App              → Connect to Gateway
  WhatsApp → Link 1 number             → Connect to Gateway

All employees use the same Bot, but get different Agents:

  Carol DMs @ACME Agent → Gateway → H2 Proxy extracts user_id → Tenant Router
    → AgentCore microVM (Carol's Finance Analyst SOUL) → Bedrock → reply

  Wang Wu DMs @ACME Agent → Gateway → H2 Proxy extracts user_id → Tenant Router
    → AgentCore microVM (Wang Wu's SDE SOUL) → Bedrock → reply
```

The Gateway doesn't do AI inference — it only manages IM connections. When a message arrives, OpenClaw's AWS SDK call to Bedrock is intercepted by the H2 Proxy, which extracts the sender's platform user ID and forwards to the Tenant Router. The Router derives a unique `tenant_id` and invokes AgentCore, which creates an isolated Firecracker microVM with the employee's personalized SOUL.

### Employee Onboarding Flow

When a new employee joins the company and needs their AI agent:

```
Step 1: Employee joins company Discord/Slack/Telegram
        (or IT sends them an invite link)

Step 2: Employee DMs the company Bot for the first time
        Bot replies: "Pairing code: KFDAF3GN"

Step 3: IT Admin opens Admin Console → Bindings → IM User Mappings
        Clicks "Approve Pairing":
          Pairing Code: KFDAF3GN (from pairing message)
          Platform User ID: 1460888812426363004 (from pairing message)
          Employee: Carol Zhang (Finance Analyst)
        → System approves pairing (updates gateway in-memory state instantly)
        → Writes SSM mappings for ALL userId formats the H2 Proxy may extract:
            /user-mapping/1460888812426363004  → emp-carol  (numeric Discord ID)
            /user-mapping/dm_carol            → emp-carol  (Discord DM username)
            /user-mapping/carol               → emp-carol  (plain username)
        → Writes SSM position + permissions for Carol's role

Step 4: Employee sends another message
        → Gateway allows it (pairing approved)
        → H2 Proxy extracts Discord user_id
        → Tenant Router resolves: user_id → emp-carol → pos-fa
        → AgentCore creates microVM with Finance Analyst SOUL
        → Agent responds as "ACME Corp Finance Analyst"

Step 5: From now on, every DM from Carol goes to her personal Agent
        with her SOUL identity, permissions, memory, and skills.
```

Zero configuration for the employee. They just DM the Bot. IT Admin does a one-click approval + binding in the Admin Console.

For employees who don't use IM tools, the Web Portal provides the same experience — login with employee ID, chat with their bound Agent directly in the browser.

## Key Features

| Feature | How It Works |
|---------|-------------|
| **SOUL Injection** | 3-layer merge (Global + Position + Personal) → OpenClaw reads merged SOUL.md at session start |
| **Permission Control** | SOUL.md defines allowed/blocked tools per role. Plan A (pre-execution) + Plan E (post-audit) |
| **Skill Filtering** | 26 skills with `allowedRoles`/`blockedRoles` in manifest. Finance gets excel-gen, SDE gets github-pr |
| **Memory Persistence** | OpenClaw Gateway (running inside microVM) manages session memory natively. Watchdog syncs workspace to S3 every 60s. Next session loads MEMORY.md from S3 — full cross-session memory. |
| **Real-time Usage** | Every invocation writes tokens/cost to DynamoDB. Admin Console shows per-agent breakdown |
| **Manager Scoping** | API-level filtering — managers see only their department's data (BFS sub-department rollup) |
| **Employee Portal** | Browser-based chat with bound agent. No IM tool dependency |
| **Knowledge Base** | Markdown files in S3. Upload via Admin Console, agents read via workspace/knowledge/ |
| **Audit Trail** | Every action logged to DynamoDB. AI Insights scan for anomalies |
| **Export** | Audit CSV export, usage CSV export from Admin Console |

## Security Model

| Layer | Mechanism | Detail |
|-------|-----------|--------|
| **Network** | No open ports | SSM port forwarding or CloudFront (origin restricted to CloudFront managed prefix list `pl-3b927c52`) |
| **Credentials** | AWS SSM SecureString | `ADMIN_PASSWORD` and `JWT_SECRET` stored encrypted in SSM. Startup script fetches them at boot — no plaintext in service files, source code, or environment |
| **Compute** | Firecracker microVM isolation | Each agent runs in a separate microVM with its own filesystem, network, and memory space |
| **IAM** | Least privilege | AgentCore role: DynamoDB, S3, SSM, Bedrock only. No admin access, no wildcard policies |
| **Data** | Role-based scoping | Admin: all data. Manager: own department (BFS rollup). Employee: own data only. Enforced at API level |
| **Agent** | SOUL-based permission control | Plan A: pre-execution tool allowlist in SOUL.md. Plan E: post-response audit scan for blocked tool usage |
| **Audit** | Comprehensive logging | Every invocation, tool call, permission denial, config change → DynamoDB. AI Insights anomaly detection |
| **Memory** | Privacy by design | Employee memory files are private. Writeback excludes assembled files to prevent policy override |
| **Knowledge** | Scope-controlled access | Department-scoped knowledge bases. Finance docs invisible to Engineering agents |

## Quick Start

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| AWS CLI | v2.27+ | `aws --version` — `bedrock-agentcore-control` requires 2.27+ |
| Docker | Any | Must support `--platform linux/arm64` (Mac M1/M2/M3 natively; Linux needs QEMU: `docker run --privileged --rm tonistiigi/binfmt --install arm64`) |
| Node.js | 18+ | For building the Admin Console frontend |
| Python | 3.10+ | For seed scripts and Admin Console backend |
| SSM Plugin | Latest | [Install guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) — required for EC2 access without SSH |

**AWS requirements:**
- Bedrock model access for **third-party models** — Amazon Nova models (Nova Lite, Nova Pro) are available by default with no approval needed. If you plan to use Claude models (required for the IT Admin Assistant) or other third-party models, go to [AWS Console → Bedrock → Model Access](https://console.aws.amazon.com/bedrock/home#/modelaccess) and enable **Anthropic Claude Haiku** at minimum.
- Bedrock AgentCore available in `us-east-1` and `us-west-2`.
- IAM permissions: `cloudformation:*`, `ec2:*`, `iam:*`, `ecr:*`, `s3:*`, `ssm:*`, `bedrock:*`, `dynamodb:*`

### Step 1: Deploy Infrastructure + AgentCore Runtime

This single script handles everything: CloudFormation stack (EC2 + ECR + S3 + IAM), Docker image build and push to ECR, AgentCore Runtime creation, and Runtime ID stored in SSM.

```bash
cd enterprise   # from repo root
bash deploy-multitenancy.sh openclaw-multitenancy us-east-1
# Takes ~15 minutes. Coffee time ☕
```

What it creates:
- EC2 (c7g.large, Graviton) — OpenClaw Gateway + H2 Proxy + Tenant Router
- ECR repository — Agent Container Docker image
- S3 bucket — tenant workspaces, SOUL templates, skills, knowledge docs
- IAM roles — EC2 instance role, AgentCore execution role (least privilege)
- AgentCore Runtime — Firecracker microVM runtime backed by ECR image
- SSM parameters — gateway token, runtime ID, stack config

After completion, export these variables for subsequent steps:
```bash
STACK_NAME="openclaw-multitenancy"
REGION="us-east-1"
DYNAMODB_REGION="us-east-2"   # DynamoDB can be in a different region

INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text)
S3_BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`TenantWorkspaceBucketName`].OutputValue' --output text)

echo "Instance: $INSTANCE_ID"
echo "S3 Bucket: $S3_BUCKET"
```

### Step 2: Create DynamoDB Table

DynamoDB is in `us-east-2` by default (separate from the gateway EC2 in `us-east-1`) — keeps latency low for the Admin Console. Change both regions to the same value if you prefer.

```bash
aws dynamodb create-table \
  --table-name openclaw-enterprise \
  --attribute-definitions \
    AttributeName=PK,AttributeType=S \
    AttributeName=SK,AttributeType=S \
    AttributeName=GSI1PK,AttributeType=S \
    AttributeName=GSI1SK,AttributeType=S \
  --key-schema \
    AttributeName=PK,KeyType=HASH \
    AttributeName=SK,KeyType=RANGE \
  --global-secondary-indexes '[{
    "IndexName":"GSI1",
    "KeySchema":[
      {"AttributeName":"GSI1PK","KeyType":"HASH"},
      {"AttributeName":"GSI1SK","KeyType":"RANGE"}
    ],
    "Projection":{"ProjectionType":"ALL"}
  }]' \
  --billing-mode PAY_PER_REQUEST \
  --region $DYNAMODB_REGION
```

### Step 3: Seed Sample Organization

Creates ACME Corp — 22 employees, 23 agents, 11 positions, 13 departments. Run from the server directory in order:

```bash
cd enterprise/admin-console/server   # from repo root
pip install boto3 requests

# 1. DynamoDB — org structure must come first (other seeds reference employee IDs)
python3 seed_dynamodb.py         --region $DYNAMODB_REGION   # employees, agents, departments
python3 seed_roles.py            --region $DYNAMODB_REGION   # RBAC (admin/manager/employee)
python3 seed_settings.py         --region $DYNAMODB_REGION   # model config, security policy
python3 seed_audit_approvals.py  --region $DYNAMODB_REGION   # audit log, approval samples
python3 seed_usage.py            --region $DYNAMODB_REGION   # usage metrics, session records
python3 seed_routing_conversations.py --region $DYNAMODB_REGION  # routing rules

# 2. SSM — tenant→position mappings (region must match AgentCore region)
python3 seed_ssm_tenants.py --region $REGION --stack $STACK_NAME

# 3. S3 — workspace files and knowledge docs (auto-detects bucket from env or SSM)
export S3_BUCKET=$S3_BUCKET
python3 seed_skills_final.py                          # 26 skills with role permissions
python3 seed_workspaces.py                            # SOUL.md, USER.md per employee
python3 seed_all_workspaces.py   --bucket $S3_BUCKET  # MEMORY.md, IDENTITY.md
python3 seed_knowledge_docs.py   --bucket $S3_BUCKET  # 12 knowledge documents
```

### Step 4: Deploy Admin Console

```bash
# Build frontend (from repo root)
cd enterprise/admin-console
npm install && npm run build
cd ../..

# Upload dist + server to S3
COPYFILE_DISABLE=1 tar czf /tmp/admin-deploy.tar.gz -C enterprise/admin-console dist server
aws s3 cp /tmp/admin-deploy.tar.gz "s3://${S3_BUCKET}/_deploy/admin-deploy.tar.gz"

# Install on EC2 via SSM
aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters "{\"commands\":[
    \"python3 -m venv /opt/admin-venv\",
    \"/opt/admin-venv/bin/pip install fastapi uvicorn boto3 requests\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/admin-deploy.tar.gz /tmp/admin-deploy.tar.gz --region $REGION\",
    \"mkdir -p /opt/admin-console && tar xzf /tmp/admin-deploy.tar.gz -C /opt/admin-console\",
    \"chown -R ubuntu:ubuntu /opt/admin-console /opt/admin-venv\"
  ]}"
```

Store secrets in SSM (no plaintext in service files):
```bash
# Replace <YOUR_PASSWORD> with your chosen admin password
aws ssm put-parameter \
  --name "/openclaw/${STACK_NAME}/admin-password" \
  --value "<YOUR_PASSWORD>" \
  --type SecureString --overwrite --region $REGION

aws ssm put-parameter \
  --name "/openclaw/${STACK_NAME}/jwt-secret" \
  --value "$(openssl rand -hex 32)" \
  --type SecureString --overwrite --region $REGION
```

Create startup wrapper and systemd service on EC2:
```bash
aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters "{\"commands\":[
    \"cat > /opt/admin-console/start.sh << 'SCRIPT'\n#!/bin/bash\nexport ADMIN_PASSWORD=\$(aws ssm get-parameter --name /openclaw/${STACK_NAME}/admin-password --with-decryption --query Parameter.Value --output text --region ${REGION} 2>/dev/null)\nexport JWT_SECRET=\$(aws ssm get-parameter --name /openclaw/${STACK_NAME}/jwt-secret --with-decryption --query Parameter.Value --output text --region ${REGION} 2>/dev/null)\nexport AWS_REGION=${DYNAMODB_REGION}\nexport CONSOLE_PORT=8099\nexport TENANT_ROUTER_URL=http://localhost:8090\ncd /opt/admin-console/server\nexec /opt/admin-venv/bin/python main.py\nSCRIPT\",
    \"chmod +x /opt/admin-console/start.sh\",
    \"printf '[Unit]\\nDescription=OpenClaw Admin Console\\nAfter=network.target\\n\\n[Service]\\nType=simple\\nUser=ubuntu\\nExecStart=/opt/admin-console/start.sh\\nRestart=always\\n\\n[Install]\\nWantedBy=multi-user.target\\n' > /etc/systemd/system/openclaw-admin.service\",
    \"systemctl daemon-reload && systemctl enable openclaw-admin && systemctl start openclaw-admin\"
  ]}"
```

### Step 5: Start Gateway Services

The H2 Proxy and Tenant Router must run as systemd services. Install the service files:

```bash
aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters "{\"commands\":[
    \"sudo mkdir -p /etc/openclaw\",
    \"printf 'STACK_NAME=${STACK_NAME}\\nAWS_REGION=${REGION}\\nBEDROCK_MODEL_ID=global.amazon.nova-2-lite-v1:0\\n' > /etc/openclaw/env\",
    \"cp /home/ubuntu/sample-OpenClaw-on-AWS-with-Bedrock/enterprise/gateway/bedrock-proxy-h2.service /etc/systemd/system/\",
    \"cp /home/ubuntu/sample-OpenClaw-on-AWS-with-Bedrock/enterprise/gateway/tenant-router.service /etc/systemd/system/\",
    \"systemctl daemon-reload\",
    \"systemctl enable bedrock-proxy-h2 tenant-router\",
    \"systemctl start bedrock-proxy-h2 tenant-router\"
  ]}"
```

Verify all services are running:
```bash
aws ssm send-command --instance-ids $INSTANCE_ID --region $REGION \
  --document-name AWS-RunShellScript \
  --parameters '{"commands":["systemctl is-active openclaw-admin bedrock-proxy-h2 tenant-router openclaw-gateway"]}' \
  --query 'Command.CommandId' --output text
# All four should show "active"
```

### Step 6: Access Admin Console

```bash
# Open an SSM port forwarding tunnel (keep this terminal open)
aws ssm start-session --target $INSTANCE_ID --region $REGION \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8099"],"localPortNumber":["8199"]}'
```

Open **http://localhost:8199** in your browser.

Login with any employee ID from the [Demo Accounts](#demo-accounts) table and the password you set in Step 4. Start with `emp-z3` (admin) to see the full Admin Console.

> **Tip:** To expose publicly, put CloudFront in front. Assign an Elastic IP to the EC2 first so the CloudFront origin doesn't change on reboot: `aws ec2 allocate-address --domain vpc --region $REGION` then associate it.

### Step 7: Connect IM Channels (Optional)

For Discord/Telegram/WhatsApp — access the OpenClaw Gateway UI to connect your bots:

```bash
# Get gateway token
aws ssm get-parameter \
  --name "/openclaw/${STACK_NAME}/gateway-token" \
  --with-decryption --query Parameter.Value --output text --region $REGION

# Open gateway tunnel
aws ssm start-session --target $INSTANCE_ID --region $REGION \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
# Open http://localhost:18789/?token=<token_from_above>
```

Then follow the IM channel setup guides in the OpenClaw documentation.
After connecting, use Admin Console → **Bindings → IM User Mappings → Approve Pairing** to link IM user IDs to employee accounts.

---

### Local Development (no AWS)

The demo server runs the full Admin Console UI with mock data — no AWS account needed.

```bash
# Build frontend
cd enterprise/admin-console && npm install && npm run build
cp -r dist ../demo/dist

# Run demo server
cd ../demo && python3 server.py
# Open http://localhost:8099
# Login: emp-z3 / any password
```

For full backend development with a real DynamoDB:
```bash
cd enterprise/admin-console/server
pip install -r requirements.txt
export ADMIN_PASSWORD="dev-password"
export JWT_SECRET=$(openssl rand -hex 32)
export AWS_REGION=us-east-2
python3 main.py   # API on http://localhost:8099
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ADMIN_PASSWORD` | Yes | Login password for all accounts. In production: store in SSM SecureString at `/openclaw/<STACK>/admin-password` |
| `JWT_SECRET` | Yes | JWT signing secret. Generate with `openssl rand -hex 32`. In production: store in SSM at `/openclaw/<STACK>/jwt-secret` |
| `AWS_REGION` | Yes | DynamoDB region (default: `us-east-2`) |
| `CONSOLE_PORT` | No | Admin Console port (default: `8099`) |
| `TENANT_ROUTER_URL` | No | Tenant Router base URL (default: `http://localhost:8090`) |
| `DYNAMODB_TABLE` | No | DynamoDB table name (default: `openclaw-enterprise`) |
| `DYNAMODB_REGION` | No | Agent container DynamoDB region (default: `us-east-2`) |
| `ALLOWED_ORIGINS` | No | CORS allowed origins, comma-separated (default: `https://your-domain.com,http://localhost:5173`) |

## Sample Organization

The seed scripts create ACME Corp — a B2B SaaS company with:

| | Count | Details |
|-|-------|---------|
| Departments | 13 | 7 top-level + 6 sub-departments |
| Positions | 11 | SA, SDE, DevOps, QA, AE, PM, FA, HR, CSM, Legal, Executive |
| Employees | 22 | Each with workspace files in S3 |
| Agents | 23 | 20 personal (1:1) + 3 shared (Help Desk, Onboarding, Platform) |
| Bindings | 12 | IM channel bindings (Discord, Slack, Telegram) |
| Skills | 26 | 6 global + 20 department-scoped, 3 layers |
| Knowledge Docs | 12 | Company policies, architecture standards, runbooks, etc. |
| SOUL Templates | 11 | 1 global + 10 position-specific |
| RBAC Roles | 3 | Admin (2), Manager (3), Employee (17) |

### Demo Accounts

| Employee ID | Name | Role | What They See |
|-------------|------|------|--------------|
| emp-z3 | Zhang San | Admin | Full Admin Console |
| emp-jiade | JiaDe Wang | Admin | Full Admin Console + Discord → SA Agent (full dev tools, cross-session memory ✨) |
| emp-lin | Lin Xiaoyu | Manager | Product department only |
| emp-mike | Mike Johnson | Manager | Sales department only |
| emp-peter | Peter Wu | Manager | Portal/Discord → Executive Agent (strategic tools, no shell/code, memory ✨) |
| emp-david | David Park | Employee | Portal/Discord → Finance Agent (Excel + SAP, no shell, memory ✨) |
| emp-w5 | Wang Wu | Employee | Portal: SDE Agent (full dev tools) |
| emp-carol | Carol Zhang | Employee | Portal: Finance Agent (Excel + SAP only, no shell) |

> ✨ = Cross-session memory via S3. Agent recalls previous conversations when you return. Discord pairing required for Discord access — see Bindings → IM User Mappings → Approve Pairing.

## What to Test

### 1. SOUL Injection (core differentiator)
Login as Carol (Employee) → Chat → "Who are you?" → **"ACME Corp Finance Analyst"**
Login as Wang Wu (Employee) → Chat → "Who are you?" → **"ACME Corp Software Engineer"**
Same LLM, completely different identities.

### 2. Permission Boundaries
Carol: "Run git status" → **Refused** (Finance role has no shell)
Wang Wu: "Run git status" → **Executed** (SDE role has shell access)
JiaDe (Discord): Full shell/code access (SA role)
Peter (Discord): "Run ls" → **Refused** (Executive role, no shell/code)

### 3. Manager Data Scoping
Login as Lin Xiaoyu (Manager) → Dashboard → **Only Product department data visible**

### 4. Real-time Usage
Send messages in Portal → Usage & Cost page updates with **real token counts from DynamoDB**
By Model tab shows **real model distribution** from DynamoDB usage records

### 5. Memory Persistence (Cross-Session)
Login as Peter Wu or JiaDe Wang (or use Discord) → Chat → come back later →
Agent recalls previous conversations from S3-persisted MEMORY.md.

**How it works:**
- OpenClaw Gateway runs inside each AgentCore microVM (zero invasion to OpenClaw)
- Gateway manages session memory natively — writes to `workspace/MEMORY.md`
- Watchdog syncs workspace to S3 every 60s
- Next session: new microVM loads MEMORY.md from S3 → agent has full context
- Same memory shared across Discord, Telegram, Slack, and Portal Chat (same employee = same S3 path)

### 6. LLM Model Management (Settings)
Settings → LLM Provider → **Change default model** (writes to DynamoDB)
**Add position override** (e.g., SA uses Claude Sonnet, Finance uses Nova Pro)
Changes take effect on next agent cold start — no redeployment needed

### 7. IT Admin Assistant
Click the **floating chat bubble** (bottom-right, admin role only) → Chat with the IT Admin Agent.
Backed by Claude via Bedrock Converse API with 10 whitelisted tools (no shell, no subprocess).
Try: `"How many employees are in Engineering?"` or `"Show me the Finance Analyst SOUL template"` or `"What's today's token usage by department?"`

### 8. Workspace Explorer
Workspace Manager → Select an agent → **M3 collapsible tree** with folders
Click any .md file → **Rendered markdown** (tables, code blocks, lists)
Toggle Raw/Rendered view for source inspection

## Design System

The Admin Console uses a custom design system inspired by **Material Design 3 Expressive** and **Gemini AI Visual Design**:

- **M3 Tonal Surface System** — 6 surface levels (surface-dim → surface-container-highest) for depth without borders
- **Spring Physics Motion** — Overshoot bounce for modals/cards, scale press for buttons, spring toggle for switches
- **Gemini Gradients** — Pulsing gradient animations for loading states, shimmer effects for skeletons
- **Dark/Light Theme** — Toggle in sidebar bottom, persisted to localStorage, smooth 400ms transition
- **Rounded Shapes** — 20px cards, 28px modals, 16px buttons (M3 large radius)
- **Accessible Status Indicators** — Ping animation for active, pulse for pending, color-coded dots

## Project Structure

```
enterprise/
├── README.md                              # This file
├── ROADMAP.md                             # Product roadmap
├── deploy-multitenancy.sh                 # One-click deployment script
├── clawdbot-bedrock-agentcore-multitenancy.yaml  # CloudFormation template
├── docs/                                  # Design documents
│   ├── prd.md                             # Product requirements
│   ├── architecture.md                    # Technical architecture deep-dive
│   ├── rbac-portal-design.md              # RBAC + Portal design
│   ├── skill-platform-design.md           # Skill architecture
│   └── cold-start-optimization-design.md  # Performance optimization
├── admin-console/                         # React frontend + FastAPI backend
│   ├── src/                               # 24 pages (19 admin + 5 portal)
│   │   ├── components/                    # UI components (M3 design system)
│   │   │   ├── ui.tsx                     # Card, Button, Badge, Modal, Table, Tabs, etc.
│   │   │   ├── Layout.tsx                 # Admin sidebar + top bar + search
│   │   │   ├── PortalLayout.tsx           # Employee portal sidebar
│   │   │   ├── AdminAssistant.tsx         # Floating IT Admin chat bubble
│   │   │   └── ClawForgeLogo.tsx          # Animated logo component
│   │   ├── contexts/                      # React contexts
│   │   │   ├── AuthContext.tsx             # JWT auth + role-based routing
│   │   │   └── ThemeContext.tsx            # Dark/light theme toggle
│   │   └── pages/                         # All page components
│   └── server/                            # FastAPI backend + seed scripts
│       ├── main.py                        # 40+ API endpoints
│       ├── db.py                          # DynamoDB single-table operations
│       ├── s3ops.py                       # S3 file operations
│       ├── auth.py                        # JWT authentication
│       └── seed_*.py                      # 10 seed scripts for sample data
├── agent-container/                       # Docker image for AgentCore
│   ├── Dockerfile                         # Multi-stage ARM64 build
│   ├── entrypoint.sh                      # S3 sync + workspace assembly
│   ├── server.py                          # HTTP server + dynamic model config
│   ├── workspace_assembler.py             # 3-layer SOUL merge
│   └── skill_loader.py                    # Role-filtered skill loading
├── gateway/                               # EC2 Gateway components
│   ├── bedrock_proxy_h2.js                # H2 Proxy — intercepts Bedrock SDK
│   └── tenant_router.py                   # Tenant Router — routes to AgentCore
├── auth-agent/                            # Authorization Agent (approval flow)
└── demo/                                  # Interactive demo
    ├── README.md                          # Demo guide with scenarios
    └── images/                            # Screenshots for README
```

## Operational Notes

### H2 Proxy and Tenant Router — systemd Services

The H2 Proxy (`bedrock_proxy_h2.js`) and Tenant Router (`tenant_router.py`) must be managed by systemd to preserve environment variables across restarts. Without systemd, a manual restart loses `STACK_NAME`, `AGENTCORE_RUNTIME_ID`, and other env vars, breaking all routing silently.

Service files are in `gateway/bedrock-proxy-h2.service` and `gateway/tenant-router.service`. Install with:

```bash
# Create env file with secrets (never commit this)
sudo mkdir -p /etc/openclaw
sudo tee /etc/openclaw/env << EOF
STACK_NAME=openclaw-multitenancy
AGENTCORE_RUNTIME_ID=openclaw_multitenancy_runtime-<YOUR_RUNTIME_ID>
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=global.amazon.nova-2-lite-v1:0
EOF

sudo cp gateway/bedrock-proxy-h2.service /etc/systemd/system/
sudo cp gateway/tenant-router.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bedrock-proxy-h2 tenant-router
sudo systemctl start bedrock-proxy-h2 tenant-router
```

### Cold-Start First Message Behavior

When a tenant's microVM is cold (first message after idle > 20 min), the H2 Proxy:
1. Fires a background prewarm to AgentCore (async)
2. Returns a fast-path Bedrock response (~2-3s) without employee SOUL/memory
3. **The second message** (after prewarm completes, ~15-20s) gets the full personalized response

This is expected behavior. The warming timeout is 25s (tuned to AgentCore cold-start time). The first message will always be generic — this is the latency/UX tradeoff of serverless microVMs.

### Discord Pairing File Permissions

The files `/home/ubuntu/.openclaw/credentials/discord-default-allowFrom.json` and `discord-pairing.json` must be owned by `ubuntu` (not `root`). The openclaw-gateway runs as ubuntu and cannot read root-owned files. If permissions break:

```bash
chown ubuntu:ubuntu /home/ubuntu/.openclaw/credentials/discord-*.json
sudo systemctl restart openclaw-gateway
```

Always use the Admin Console "Approve Pairing" button — never run `openclaw pairing approve` as root directly.

## Cost Estimate

| Component | Monthly Cost | Notes |
|-----------|-------------|-------|
| EC2 (c7g.large) | ~$52 | Gateway + Tenant Router + Admin Console |
| DynamoDB | ~$1 | Pay-per-request, ~2000 writes/day |
| S3 | < $1 | Workspace files, skills, knowledge docs |
| Bedrock (Nova 2 Lite) | ~$5-15 | ~100 conversations/day |
| AgentCore | Included | Firecracker microVMs, pay per invocation |
| **Total** | **~$60-70/mo** | For 20 agents, ~100 conversations/day |

Compared to ChatGPT Team ($25/user/month × 20 = $500/month), this is **85% cheaper** with full enterprise controls.

## How It Compares

| Capability | ChatGPT Team | Microsoft Copilot | OpenClaw Enterprise |
|-----------|-------------|-------------------|-------------------|
| Per-employee identity control | ❌ Same for all | ❌ Same for all | ✅ 3-layer SOUL per role |
| Tool permission per role | ❌ | ❌ | ✅ Plan A + Plan E |
| Department data scoping | ❌ | Partial | ✅ API-level BFS rollup |
| Memory persistence | ❌ Session only | ❌ | ✅ S3 writeback, cross-session |
| Self-hosted, no data leaves your VPC | ❌ | ❌ | ✅ Bedrock in your account |
| Open source, no vendor lock-in | ❌ | ❌ | ✅ OpenClaw + AWS native |
| Cost for 20 users | $500/mo | $600/mo | ~$65/mo |

---

Built by [wjiad@aws](mailto:wjiad@amazon.com) · [aws-samples](https://github.com/aws-samples) · Contributions welcome
