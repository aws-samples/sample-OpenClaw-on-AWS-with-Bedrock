# OpenClaw on AWS with Bedrock

> Your own AI assistant on AWS — connects to WhatsApp, Telegram, Discord, Slack. Powered by Amazon Bedrock. No API keys. One-click deploy. ~$40/month.

English | [简体中文](README_CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![AWS](https://img.shields.io/badge/AWS-Bedrock-orange.svg)](https://aws.amazon.com/bedrock/)
[![CloudFormation](https://img.shields.io/badge/IaC-CloudFormation-blue.svg)](https://aws.amazon.com/cloudformation/)

## Why This Exists

[OpenClaw](https://github.com/openclaw/openclaw) is the fastest-growing open-source AI assistant — it runs on your hardware, connects to your messaging apps, and actually does things: manages email, browses the web, runs commands, schedules tasks.

The problem: setting it up means managing API keys from multiple providers, configuring VPNs, and handling security yourself.

This project solves that. One CloudFormation stack gives you:

- **Amazon Bedrock** for model access — 10 models, one unified API, IAM authentication (no API keys)
- **Graviton ARM instances** — 20-40% cheaper than x86
- **SSM Session Manager** — secure access without opening ports
- **VPC Endpoints** — traffic stays on AWS private network
- **CloudTrail** — every API call audited automatically

Deploy in 8 minutes. Access from your phone.

## Quick Start

### One-Click Deploy

1. Click "Launch Stack" for your region
2. Select an EC2 key pair
3. Wait ~8 minutes
4. Check the Outputs tab

| Region | Launch |
|--------|--------|
| **US West (Oregon)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **US East (Virginia)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **EU (Ireland)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **Asia Pacific (Tokyo)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |

> **Prerequisites**: Enable Bedrock models in the [Bedrock Console](https://console.aws.amazon.com/bedrock/) and create an EC2 key pair in your target region.

### After Deployment

![CloudFormation Outputs](images/20260305-215111.png)

> 🦞 **Just open the Web UI and say hi.** All messaging plugins (WhatsApp, Telegram, Discord, Slack, Feishu) are pre-installed. Tell your OpenClaw which platform you want to connect — it will guide you through the entire setup step by step. No manual configuration needed.

```bash
# 1. Install SSM Session Manager Plugin (one-time)
#    https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

# 2. Start port forwarding (keep terminal open)
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-bedrock \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text --region us-west-2)

aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# 3. Get your token (in a second terminal)
TOKEN=$(aws ssm get-parameter \
  --name /openclaw/openclaw-bedrock/gateway-token \
  --with-decryption \
  --query Parameter.Value \
  --output text --region us-west-2)

# 4. Open in browser
echo "http://localhost:18789/?token=$TOKEN"
```

### CLI Deploy (Alternative)

```bash
aws cloudformation create-stack \
  --stack-name openclaw-bedrock \
  --template-body file://clawdbot-bedrock.yaml \
  --parameters ParameterKey=KeyPairName,ParameterValue=your-keypair \
  --capabilities CAPABILITY_IAM \
  --region us-west-2

aws cloudformation wait stack-create-complete \
  --stack-name openclaw-bedrock --region us-west-2
```

### 🎯 Deploy with Kiro AI

Prefer a guided experience? [Kiro](https://kiro.dev/) walks you through deployment conversationally — just open this repo as a workspace and say "help me deploy OpenClaw".

**[→ Kiro Deployment Guide](QUICK_START_KIRO.md)**

---

## Connect Messaging Platforms

Once deployed, connect your preferred platform in the Web UI under "Channels":

| Platform | Setup | Guide |
|----------|-------|-------|
| **WhatsApp** | Scan QR code from your phone | [docs](https://docs.openclaw.ai/channels/whatsapp) |
| **Telegram** | Create bot via [@BotFather](https://t.me/botfather), paste token | [docs](https://docs.openclaw.ai/channels/telegram) |
| **Discord** | Create app in Developer Portal, paste bot token | [docs](https://docs.openclaw.ai/channels/discord) |
| **Slack** | Create app at api.slack.com, install to workspace | [docs](https://docs.openclaw.ai/channels/slack) |
| **Microsoft Teams** | Requires Azure Bot setup | [docs](https://docs.openclaw.ai/channels/msteams) |
| **Lark / Feishu** | Community plugin: [openclaw-feishu](https://www.npmjs.com/package/openclaw-feishu) | — |

**Full platform docs**: [docs.openclaw.ai](https://docs.openclaw.ai/)

---

## What Can OpenClaw Do?

Once connected, just message it:

```
You: What's the weather in Tokyo?
You: Summarize this PDF [attach file]
You: Remind me every day at 9am to check emails
You: Open google.com and search for "AWS Bedrock pricing"
```

| Command | What it does |
|---------|-------------|
| `/status` | Show model, tokens used, cost |
| `/new` | Start fresh conversation |
| `/think high` | Enable deep reasoning mode |
| `/help` | List all commands |

Voice messages work on WhatsApp and Telegram — OpenClaw transcribes and responds.

---

## Architecture

```
You (WhatsApp/Telegram/Discord)
  │
  ▼
┌─────────────────────────────────────────────┐
│  AWS Cloud                                  │
│                                             │
│  EC2 (OpenClaw)  ──IAM──▶  Bedrock         │
│       │                   (Nova/Claude)     │
│       │                                     │
│  VPC Endpoints        CloudTrail            │
│  (private network)    (audit logs)          │
└─────────────────────────────────────────────┘
  │
  ▼
You (receive response)
```

- **EC2**: Runs OpenClaw gateway (~1GB RAM)
- **Bedrock**: Model inference via IAM (no API keys)
- **SSM**: Secure access, no public ports
- **VPC Endpoints**: Private network to Bedrock (optional, +$22/mo)

---

## Models

Switch models with one CloudFormation parameter — no code changes:

| Model | Input/Output per 1M tokens | Best for |
|-------|---------------------------|----------|
| **Nova 2 Lite** (default) | $0.30 / $2.50 | Everyday tasks, 90% cheaper than Claude |
| Nova Pro | $0.80 / $3.20 | Balanced performance, multimodal |
| Claude Sonnet 4.5 | $3.00 / $15.00 | Complex reasoning, coding |
| Claude Haiku 4.5 | $1.00 / $5.00 | Fast and efficient |
| DeepSeek R1 | $0.55 / $2.19 | Open-source reasoning |
| Llama 3.3 70B | — | Open-source alternative |
| Kimi K2.5 | $0.60 / $3.00 | Multimodal agentic, 262K context |

> Uses [Global CRIS profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) — deploy in any region, requests auto-route to optimal locations.

---

## Cost

### Typical Monthly Cost (Light Usage)

| Component | Cost |
|-----------|------|
| EC2 (t4g.medium, Graviton) | $24 |
| EBS (30GB gp3) | $2.40 |
| VPC Endpoints (optional) | $22 |
| Bedrock (Nova 2 Lite, ~100 conv/day) | $5-8 |
| **Total** | **$31-56** |

### Save Money

- Use Nova 2 Lite instead of Claude → 90% cheaper
- Use Graviton (ARM) instead of x86 → 20-40% cheaper
- Skip VPC Endpoints → save $22/mo (less secure)
- AWS Savings Plans → 30-40% off EC2

### vs. Alternatives

| Option | Cost | What you get |
|--------|------|-------------|
| ChatGPT Plus | $20/person/month | Single user, no integrations |
| This project (5 users) | ~$10/person/month | Multi-user, WhatsApp/Telegram/Discord, full control |
| Local Mac Mini | $0 server + $20-30 API | Hardware cost, manage yourself |

---

## Configuration

### Instance Types

| Type | Monthly | RAM | Architecture | Use case |
|------|---------|-----|-------------|----------|
| t4g.small | $12 | 2GB | Graviton ARM | Personal |
| **t4g.medium** | **$24** | **4GB** | **Graviton ARM** | **Small teams (default)** |
| t4g.large | $48 | 8GB | Graviton ARM | Medium teams |
| c7g.xlarge | $108 | 8GB | Graviton ARM | High performance |
| t3.medium | $30 | 4GB | x86 | x86 compatibility |

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OpenClawModel` | Nova 2 Lite | Bedrock model ID |
| `InstanceType` | c7g.large | EC2 instance type |
| `CreateVPCEndpoints` | true | Private networking (+$22/mo) |
| `EnableSandbox` | true | Docker isolation for code execution |
| `CreateS3Bucket` | true | S3 bucket for file sharing skill |
| `InstallS3FilesSkill` | true | Auto-install S3 file sharing |
| `KeyPairName` | none | EC2 key pair (optional, for emergency SSH) |

---

## Deployment Options

### Standard (EC2) — This README

Best for most users. Fixed cost, full control, 24/7 availability.

### Multi-Tenant Platform (AgentCore Runtime) — [README_AGENTCORE.md](README_AGENTCORE.md)

> ⚠️ In development — targeting v1.0 by June 2026. [Roadmap →](ROADMAP.md)

Turn OpenClaw from a single-user tool into an enterprise platform: every employee gets an AI assistant, every team gets an AI assistant, every department gets an AI assistant — with clear boundaries, shared skills, and centralized governance.

| What you get | How |
|---|---|
| Tenant isolation | Firecracker microVM per user (AgentCore Runtime) |
| Shared model access | One Bedrock account, per-tenant metering (~$1-2/person/month) |
| Shared skills with bundled SaaS keys | Install once, authorize per tenant, credentials never exposed |
| Per-tenant permission profiles | SSM-based rules, hot-reload, Plan A + E enforcement |
| Controlled info sharing | Cross-tenant data policies, audited, opt-in |
| Human approval workflow | Auth Agent → admin notification → approve/reject via messaging |
| Elastic compute | Auto-scaling microVMs, burst capacity, pay-per-use |

**[→ Full Multi-Tenant Guide](README_AGENTCORE.md)** · **[→ Roadmap](ROADMAP.md)**

### macOS (Apple Silicon) — For iOS/macOS Development

| Type | Chip | RAM | Monthly |
|------|------|-----|---------|
| mac2.metal | M1 | 16GB | $468 |
| mac2-m2.metal | M2 | 24GB | $632 |
| mac2-m2pro.metal | M2 Pro | 32GB | $792 |

> 24-hour minimum allocation. Only use for Apple development workflows — Linux is 12x cheaper for general use.

| Region | Launch |
|--------|--------|
| **US West (Oregon)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) |
| **US East (Virginia)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) |

### 🇨🇳 AWS China (Beijing/Ningxia)

Uses SiliconFlow (DeepSeek, Qwen, GLM) instead of Bedrock. Requires a SiliconFlow API key.

| Region | Launch |
|--------|--------|
| **cn-north-1 (Beijing)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://cn-north-1.console.amazonaws.cn/cloudformation/home?region=cn-north-1#/stacks/create/review?stackName=openclaw-china&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-china.yaml) |
| **cn-northwest-1 (Ningxia)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://cn-northwest-1.console.amazonaws.cn/cloudformation/home?region=cn-northwest-1#/stacks/create/review?stackName=openclaw-china&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-china.yaml) |

**[→ China Deployment Guide (中国区部署指南)](DEPLOYMENT_CN.md)**

---

### Data Protection

```yaml
EnableDataProtection: false  # Default: data deleted with stack
  # When true:
  # - Data volume retained on stack delete
  # - Preserves chat history, configs, credentials
  # - Allows instance replacement without data loss
  
  # When false:
  # - Data volume deleted with stack (clean removal)
```

**Storage layout:**
- Root volume (30GB): OS, applications, and OpenClaw data (default)
- Data volume (30GB): `/data` → symlinked to `~/.openclaw` (when EnableDataProtection=true)

## Security

| Layer | What it does |
|-------|-------------|
| **IAM Roles** | No API keys — automatic credential rotation |
| **SSM Session Manager** | No public ports, session logging |
| **VPC Endpoints** | Bedrock traffic stays on private network |
| **SSM Parameter Store** | Gateway token stored as SecureString, never on disk |
| **Supply-chain protection** | Docker via GPG-signed repos, NVM via download-then-execute (no `curl \| sh`) |
| **Docker Sandbox** | Isolates code execution in group chats |
| **CloudTrail** | Every Bedrock API call audited |

**[→ Full Security Guide](SECURITY.md)**

---

## Community Skills

Optional extensions for OpenClaw:

- [S3 Files Skill](skills/s3-files-skill/) — Upload and share files via S3 with pre-signed URLs (auto-installed by default)
- [Kiro CLI Skill](skills/openclaw-kirocli-skill/) — AI-powered coding via Kiro CLI
- [AWS Backup Skill](https://github.com/genedragon/openclaw-aws-backup-skill) — S3 backup/restore with optional KMS encryption

---

## SSH-like Access via SSM

```bash
# Start interactive session
aws ssm start-session --target i-xxxxxxxxx --region us-east-1

# Switch to ubuntu user
sudo su - ubuntu

# Run OpenClaw commands
openclaw --version
openclaw gateway status
```

---

## Troubleshooting

Common issues and fixes: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

Step-by-step deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md)

---

## Contributing

We're building the enterprise OpenClaw platform in the open — from single-user deployment to multi-tenant SaaS. Whether you're an enterprise architect, a skill developer, a security researcher, or just someone who wants a better AI assistant, there's a place for you.

Areas where we need help most:
- End-to-end multi-tenant testing
- Skills with bundled SaaS credentials (Jira, Salesforce, SAP)
- Agent-to-agent orchestration
- Cost benchmarking (AgentCore vs EC2)
- Security audits and penetration testing

**[→ Roadmap](ROADMAP.md)** · **[→ Contributing Guide](CONTRIBUTING.md)** · **[→ GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)**

## Resources

- [OpenClaw Docs](https://docs.openclaw.ai/) · [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [Amazon Bedrock Docs](https://docs.aws.amazon.com/bedrock/) · [SSM Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)
- [OpenClaw on Lightsail](https://aws.amazon.com/blogs/aws/introducing-openclaw-on-amazon-lightsail-to-run-your-autonomous-private-ai-agents/) (official AWS blog)

## Support

- **This Project**: [GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)
- **OpenClaw**: [GitHub Issues](https://github.com/openclaw/openclaw/issues) · [Discord](https://discord.gg/openclaw)
- **AWS Bedrock**: [AWS re:Post](https://repost.aws/tags/bedrock)

---

**Built with Kiro** 🦞
