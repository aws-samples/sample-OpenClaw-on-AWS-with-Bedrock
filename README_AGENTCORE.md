# OpenClaw Multi-Tenant Platform on AWS

> Every employee gets an AI assistant. Every team gets an AI assistant. Every department gets an AI assistant. They have clear boundaries, shared capabilities, and centralized governance. This is enterprise OpenClaw — the path from personal AI tool to organizational AI platform.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![AWS](https://img.shields.io/badge/AWS-Bedrock-orange.svg)](https://aws.amazon.com/bedrock/)
[![Status](https://img.shields.io/badge/Status-In%20Development-yellow.svg)]()

> ⚠️ **Work in Progress** — Core components implemented. End-to-end integration testing ongoing. We need contributors — see [How to Contribute](#how-to-contribute).

---

## The Problem

OpenClaw is the most capable open-source AI assistant — but it's built for one user on one machine.

Enterprises face a dilemma:

- **500 separate instances?** 500 API keys to manage, 500 unaudited agents, 500 potential security incidents. No shared capabilities. No centralized governance. Costs multiply linearly.
- **One shared instance?** No tenant isolation. No permission control. One person's prompt injection compromises everyone. OpenClaw's own [security policy](https://github.com/openclaw/openclaw/security) explicitly states the gateway is not a multi-tenant security boundary.

Neither works. Enterprises need a platform.

---

## Why Multi-Tenant Matters: 7 Value Propositions

### 1. Unified Model Access — Pool Resources, Cut Costs

Individual deployments mean individual API costs. A multi-tenant platform consolidates model access through a single Amazon Bedrock account with IAM authentication — no API keys to manage, no keys to rotate, no keys to leak.

The economics are transformative:

| Approach | Cost for 50 users |
|----------|-------------------|
| ChatGPT Plus ($20/person) | **$1,000/month** |
| 50 separate OpenClaw instances | **$2,000+/month** (50 × EC2 + 50 × API keys) |
| This platform (shared infrastructure) | **$65-110/month** (~$1.30-2.20/person) |

Tenants contribute to a shared model pool. The platform meters usage per tenant for chargeback. Bulk Bedrock access through a single account means lower per-unit cost than any individual subscription. This is "化零为整" — turning fragmented individual costs into consolidated organizational savings.

### 2. Shared Skills with Bundled SaaS Credentials

Skills are the killer feature of OpenClaw — they extend the agent with real-world capabilities. In a multi-tenant platform, skills become shared organizational assets:

- **IT installs a Jira skill** with the organization's Jira API key baked in. Every authorized employee's agent can create tickets, query sprints, update issues — without any employee ever seeing the Jira API key.
- **Finance installs a SAP skill** with the SAP connector credentials. Finance agents query financial data; the credentials never leave the skill container.
- **HR installs a Workday skill**. Employees ask their agent "how many PTO days do I have?" — the skill handles authentication transparently.

The pattern: **IT manages the skill catalog and credentials. Employees consume capabilities.** Skills are installed once on the platform, authorized per tenant profile, and executed within each tenant's isolated microVM. SaaS keys stay in the skill package — tenants use the capability, never the credential.

### 3. Per-Tenant Enterprise Rules — Customized Governance

Every tenant gets a permission profile stored in SSM Parameter Store. The platform enforces these rules through two complementary mechanisms:

- **Plan A (Soft Enforcement)**: The tenant's allowed tools list is injected into the system prompt before every request. The LLM knows its boundaries.
- **Plan E (Audit)**: After execution, the response is scanned for blocked tool usage. Violations are logged to CloudWatch with tenant ID, tool name, and timestamp.

Real-world examples:

| Role | Allowed Tools | Blocked | Rationale |
|------|--------------|---------|-----------|
| Intern | web_search | Everything else | Minimize risk surface |
| Finance analyst | web_search, file (read-only) | shell, code_execution | Read financial data, no system access |
| Senior engineer | web_search, shell, file, code_execution | install_skill, eval | Full dev capabilities, no supply-chain risk |
| IT admin | All except install_skill, eval | — | Maximum capability with safety rails |

Rules are updated via SSM — no redeployment needed. Change a tenant's profile, and the next request picks up the new rules automatically.

### 4. Controlled Information and Memory Sharing

Tenants are isolated by default — each runs in a separate Firecracker microVM with its own filesystem, memory, and CPU. No cross-tenant data leakage.

But enterprises need controlled sharing. The platform supports explicit data sharing scenarios:

- **Team → Department**: A team agent produces a weekly status report. The department agent is authorized to read team agents' output summaries — but not their raw conversations or tool execution logs.
- **Department → Executive**: Department agents generate quarterly metrics. The executive agent aggregates across departments for board-level summaries.
- **Project → Cross-functional**: A project agent spans engineering, design, and product. It can read each team's project-related outputs, scoped by project ID.
- **Knowledge base sharing**: Certain memory segments (company policies, product documentation, approved procedures) are shared read-only across all tenants. Tenant-specific memory (conversations, personal notes) stays private.

The key principle: **sharing is opt-in, scoped, and audited.** Every cross-boundary data access is logged. No implicit sharing. No "everyone can see everything."

### 5. Skills Marketplace Ecosystem

Beyond internal skills, the platform enables a marketplace model:

- **Third-party developers** publish skills with declared permission requirements and security reviews.
- **Platform operators** curate the catalog — approve, reject, or flag skills based on security audit.
- **Tenants** browse and request skills. Approved skills are available within their permission profile.

Think "app store for AI agents." Each skill declares:
- What tools it needs (shell? file_write? API access?)
- What data it accesses
- What SaaS credentials it bundles
- Its security audit status

This is the foundation of an OpenClaw ecosystem — where the value of the platform grows with every skill published, and every organization benefits from the community's contributions.

### 6. Elastic Compute for Enterprise Workloads

AgentCore Runtime scales from zero to thousands of concurrent Firecracker microVMs. This unlocks enterprise workloads that a single EC2 instance can never handle:

- **Nightly batch processing**: 10,000 customer support tickets analyzed overnight. Spin up 100 microVMs, process in parallel, spin down. Cost: minutes of compute, not 24/7 EC2.
- **Scheduled reports**: Every Monday at 8am, 50 department agents generate weekly summaries simultaneously. No queuing, no bottleneck.
- **Burst capacity**: Product launch day — 10x normal message volume. AgentCore auto-scales. No capacity planning, no over-provisioning.
- **Heavy computation**: Code review agent analyzes a 100-file PR with deep reasoning (Claude Sonnet). Needs 8GB RAM and 5 minutes of compute. Gets its own microVM, doesn't affect other tenants.

Pay only for what you use. No idle costs. No capacity planning.

### 7. Agent Hierarchy — The Organizational Nervous System

```
┌─────────────────────────────────────────────────────────────┐
│  Organization Agent                                         │
│  (company-wide policies, cross-department coordination)     │
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────┐ │
│  │ Engineering Dept │  │ Finance Dept    │  │ Sales Dept │ │
│  │ Agent            │  │ Agent           │  │ Agent      │ │
│  │                  │  │                 │  │            │ │
│  │ ┌──┐ ┌──┐ ┌──┐ │  │ ┌──┐ ┌──┐      │  │ ┌──┐ ┌──┐ │ │
│  │ │A │ │B │ │C │ │  │ │D │ │E │      │  │ │F │ │G │ │ │
│  │ └──┘ └──┘ └──┘ │  │ └──┘ └──┘      │  │ └──┘ └──┘ │ │
│  └─────────────────┘  └─────────────────┘  └────────────┘ │
└─────────────────────────────────────────────────────────────┘

A-G = Individual employee agents
Each box = isolated microVM with its own permissions, memory, and identity
Arrows between boxes = controlled, audited communication channels
```

Each agent has:
- **Its own identity**: tenant_id, permission profile, session history
- **Its own permissions**: what tools, data, and APIs it can access
- **Its own memory**: conversations, notes, learned preferences
- **Controlled communication**: agents talk through explicit channels, not shared state

A team agent can ask its members' agents for status updates. A department agent can aggregate team outputs. An executive agent can summarize across departments. But Alice's agent can never read Bob's private conversations, and the sales agent can never execute engineering's deployment tools.

**This is the future**: not a chatbot, but an organizational nervous system. Not OpenClaw-the-tool, but OpenClaw-the-platform. This is what enterprise OpenClaw SaaS looks like. This is what an OpenClaw MSP (Managed Service Provider) delivers.

![OpenClaw Multi-Tenant Admin Console](images/20260305-214028.jpeg)

### Try It Now

```bash
# Visual admin console (no AWS needed)
python3 demo/console.py
# Open http://localhost:8099

# Terminal demo (7 scenarios)
python3 demo/run_demo.py

# AWS demo (real Bedrock inference, requires EC2)
bash demo/setup_aws_demo.sh
python3 demo/aws_demo.py
```

The admin console lets you manage tenants, edit permissions, approve requests, view audit logs, and send live messages as different tenants — all in the browser. **[→ Demo Guide](demo/README.md)**

---

## How It Works Today

```
Users (WhatsApp / Telegram / Discord / Slack)
  │
  ▼
┌──────────────────────────────────────────────────────┐
│  EC2 Gateway                                         │
│                                                      │
│  OpenClaw Gateway (Node.js, port 18789)              │
│  └── Receives messages, serves Web UI                │
│                                                      │
│  Tenant Router (Python, port 8090)                   │
│  ├── derive_tenant_id(channel, user_id)              │
│  └── invoke AgentCore Runtime (sessionId=tenant_id)  │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  AgentCore Runtime  (serverless)                     │
│  Each tenant → isolated Firecracker microVM          │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │  Agent Container                               │  │
│  │  1. Validate input (safety.py)                 │  │
│  │  2. Inject tenant permissions (Plan A)         │  │
│  │  3. Execute via OpenClaw subprocess            │  │
│  │  4. Audit response for violations (Plan E)     │  │
│  │  5. Log to CloudWatch per tenant               │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────────────┘
                       │ (on permission violation)
                       ▼
┌──────────────────────────────────────────────────────┐
│  Auth Agent  (separate AgentCore session)            │
│  ├── Risk-assessed approval notification             │
│  ├── Send to admin via WhatsApp/Telegram             │
│  ├── 30-minute auto-reject                           │
│  └── Approve → issue token or update SSM profile     │
└──────────────────────────────────────────────────────┘

Supporting Services:
  SSM Parameter Store  → per-tenant permission profiles, gateway token, system prompts
  CloudWatch Logs      → structured JSON per tenant (compliance, forensics)
  ECR                  → Agent Container Docker image
  CloudTrail           → every Bedrock API call audited
```

### Security Model

| Layer | Mechanism | What it prevents |
|-------|-----------|-----------------|
| **VM Isolation** | Firecracker microVM per tenant | Cross-tenant data leakage |
| **Plan A** | System prompt injection (allowed tools list) | Unauthorized tool usage |
| **Plan E** | Post-execution response audit | Undetected policy violations |
| **Always Blocked** | `install_skill`, `load_extension`, `eval` hardcoded | Supply-chain attacks via [ClawHub](https://www.onyx.app/insights/openclaw-enterprise-evaluation-framework) |
| **Input Validation** | Message truncation, path traversal checks, 13 injection patterns | Prompt injection, memory poisoning |
| **Auth Agent Validation** | 7 approval-specific injection patterns | Manipulation of approval flow |
| **Centralized Audit** | CloudWatch structured JSON per tenant | Compliance (SOC2, HIPAA, PCI-DSS) |

> Plan A is soft enforcement — the LLM can theoretically be bypassed via prompt injection. Plan E catches what Plan A misses. For hard enforcement via AgentCore Gateway MCP mode, see [Roadmap](ROADMAP.md).

### What This Adds to OpenClaw

| | OpenClaw alone | This platform |
|---|---|---|
| Users | 1 | Unlimited, isolated |
| Execution | Local process | Serverless microVM per tenant |
| Model access | Individual API keys | Unified Bedrock, per-tenant metering |
| Permissions | None | Per-tenant SSM profiles, Plan A + E |
| Audit | None | CloudWatch + CloudTrail per tenant |
| Approval workflow | None | Human-in-the-loop, 30-min auto-reject |
| Memory safety | None | 13 injection patterns detected |
| Skills | Per-instance, manual | Shared catalog, bundled SaaS credentials |
| Cost model | Fixed per instance | Shared infrastructure, per-tenant metering |
| Scalability | Single machine | Auto-scaling microVMs, burst capacity |

---

## Repository Structure

```
agent-container/           # Docker image for AgentCore Runtime
├── server.py              # HTTP wrapper: Plan A + E enforcement
├── permissions.py         # SSM profile read/write, permission checks
├── safety.py              # Input validation, memory poisoning detection
├── identity.py            # ApprovalToken lifecycle (max 24h TTL)
├── memory.py              # Optional AgentCore Memory persistence
├── observability.py       # Structured CloudWatch JSON logs
├── openclaw.json          # OpenClaw config template
└── Dockerfile             # Multi-stage: OpenClaw + Python 3.12

auth-agent/                # Authorization Agent
├── server.py              # HTTP entry point with input validation
├── handler.py             # Approval flow, risk assessment, injection detection
├── approval_executor.py   # Execute approve/reject, update SSM
└── permission_request.py  # PermissionRequest dataclass

src/gateway/
└── tenant_router.py       # Gateway → AgentCore routing (tenant derivation + invocation)

src/utils/
└── agentcore.ts           # SessionKey derivation, response formatting

clawdbot-bedrock-agentcore-multitenancy.yaml  # CloudFormation: EC2 + ECR + SSM + CloudWatch
```

---

## Deployment

### Prerequisites

- AWS CLI with permissions for CloudFormation, EC2, VPC, IAM, ECR, Bedrock AgentCore, SSM, CloudWatch
- Docker installed locally
- Bedrock model access enabled in [Bedrock Console](https://console.aws.amazon.com/bedrock/)

### Phase 1: Deploy Infrastructure

```bash
aws cloudformation create-stack \
  --stack-name openclaw-multitenancy \
  --template-body file://clawdbot-bedrock-agentcore-multitenancy.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --parameters \
    ParameterKey=KeyPairName,ParameterValue=your-key-pair \
    ParameterKey=OpenClawModel,ParameterValue=global.amazon.nova-2-lite-v1:0

aws cloudformation wait stack-create-complete \
  --stack-name openclaw-multitenancy --region us-east-1
```

### Phase 2: Build and Push Agent Container

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1

ECR_URI=$(aws cloudformation describe-stacks \
  --stack-name openclaw-multitenancy --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`MultitenancyEcrRepositoryUri`].OutputValue' \
  --output text)

aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

docker build --platform linux/arm64 -f agent-container/Dockerfile -t $ECR_URI:latest .
docker push $ECR_URI:latest
```

### Phase 3: Create AgentCore Runtime

```bash
EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name openclaw-multitenancy --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`AgentContainerExecutionRoleArn`].OutputValue' \
  --output text)

RUNTIME_ID=$(aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name "openclaw_multitenancy_runtime" \
  --agent-runtime-artifact '{"containerConfiguration":{"containerUri":"'$ECR_URI':latest"}}' \
  --role-arn "$EXECUTION_ROLE_ARN" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --environment-variables "STACK_NAME=openclaw-multitenancy,AWS_REGION=$REGION" \
  --region $REGION \
  --query 'agentRuntimeId' --output text)

aws ssm put-parameter \
  --name "/openclaw/openclaw-multitenancy/runtime-id" \
  --value "$RUNTIME_ID" --type String --overwrite --region $REGION
```

### Phase 4: Start Tenant Router

```bash
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-multitenancy --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text)

aws ssm start-session --target $INSTANCE_ID --region $REGION
# On EC2:
sudo su - ubuntu
export STACK_NAME=openclaw-multitenancy AWS_REGION=us-east-1
nohup python3 /path/to/tenant_router.py > /tmp/tenant-router.log 2>&1 &
```

### Phase 5: Access Gateway

```bash
aws ssm start-session --target $INSTANCE_ID --region $REGION \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

TOKEN=$(aws ssm get-parameter \
  --name "/openclaw/openclaw-multitenancy/gateway-token" \
  --region $REGION --with-decryption --query 'Parameter.Value' --output text)

echo "http://localhost:18789/?token=$TOKEN"
```

### Phase 6: Enterprise Profiles (Optional)

```bash
STACK_NAME=openclaw-multitenancy REGION=us-east-1 bash setup-enterprise-profiles.sh
```

| Role | Tools | Use case |
|---|---|---|
| `readonly-agent` | web_search | General staff |
| `finance-agent` | web_search, shell (read-only), file | Financial queries |
| `web-agent` | All tools | Web development |
| `erp-agent` | web_search, shell, file, file_write | ERP operations |

---

## Day-2 Operations

```bash
# Update Auth Agent behavior (no redeployment — hot reload from SSM)
aws ssm put-parameter \
  --name "/openclaw/openclaw-multitenancy/auth-agent/system-prompt" \
  --type String --overwrite --value "Your updated instructions..."

# View tenant logs
aws logs filter-log-events \
  --log-group-name "/openclaw/openclaw-multitenancy/agents" \
  --filter-pattern '{ $.tenant_id = "wa__8613800138000" }'

# Update container (AgentCore picks up new image on next invocation)
docker build --platform linux/arm64 -f agent-container/Dockerfile -t $ECR_URI:latest .
docker push $ECR_URI:latest
```

---

## Cost

| Component | Cost |
|---|---|
| EC2 Gateway (c7g.large) | ~$35/mo |
| EBS 30GB | ~$2.40/mo |
| VPC Endpoints (optional) | ~$29/mo |
| AgentCore Runtime | Pay-per-invocation |
| Bedrock Nova 2 Lite | $0.30/$2.50 per 1M tokens |

**For a team of 50**: ~$40-60/mo infrastructure + ~$25-50/mo Bedrock = **~$1.30-2.20/person/month**

| Comparison | 50 users | 500 users |
|---|---|---|
| ChatGPT Plus | $1,000/mo | $10,000/mo |
| Individual OpenClaw instances | $2,000+/mo | $20,000+/mo |
| **This platform** | **$65-110/mo** | **$200-400/mo** |

The more users, the better the economics. This is the MSP model.

---

## Cleanup

```bash
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id $RUNTIME_ID --region us-east-1
aws cloudformation delete-stack --stack-name openclaw-multitenancy --region us-east-1
```

---

## How to Contribute

We're building the enterprise OpenClaw platform in the open. The most impactful areas right now:

| Area | What's needed | Difficulty |
|------|--------------|------------|
| **End-to-end testing** | Validate full message flow: Gateway → Router → AgentCore → Container | Medium |
| **Auth Agent delivery** | Send approval notifications via WhatsApp/Telegram (replace logging stubs) | Medium |
| **Skills marketplace** | Design skill packaging format, permission declaration, catalog API | Hard |
| **Agent orchestration** | Agent-to-agent communication protocol, cross-tenant data sharing policies | Hard |
| **Cost benchmarking** | Real-world AgentCore vs EC2 cost data at 10/100/1000 conversations/day | Easy |
| **Documentation** | Deployment guides, architecture deep-dives, security audit reports | Easy |

Whether you're an enterprise architect evaluating this for your organization, a developer who wants to build skills, or a security researcher who wants to poke holes — we want you here.

**[→ Roadmap](ROADMAP.md)** · **[→ Contributing Guide](CONTRIBUTING.md)** · **[→ GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)**

---

## Resources

- [OpenClaw Docs](https://docs.openclaw.ai/) · [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html) · [Session Isolation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html)
- [Microsoft OpenClaw Security Guidance](https://www.microsoft.com/en-us/security/blog/2026/02/19/running-openclaw-safely-identity-isolation-runtime-risk/)
- [OpenClaw on Lightsail](https://aws.amazon.com/blogs/aws/introducing-openclaw-on-amazon-lightsail-to-run-your-autonomous-private-ai-agents/) (single-user; this project extends to multi-tenant)

---

*This is the path from "personal AI assistant" to "enterprise AI platform." From one user on one machine to an organizational nervous system. Without rewriting OpenClaw, without vendor lock-in, on infrastructure you control. This is OpenClaw SaaS. This is enterprise OpenClaw MSP.*
