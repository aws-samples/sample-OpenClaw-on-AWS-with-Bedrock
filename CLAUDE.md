# CLAUDE.md

## Project Overview

This repository provides **AWS-native CloudFormation templates** for deploying [OpenClaw](https://www.npmjs.com/package/openclaw) (formerly Clawdbot/Moltbot), an open-source personal AI assistant. It integrates with Amazon Bedrock for model access and supports messaging platforms including WhatsApp, Telegram, Discord, Slack, Microsoft Teams, iMessage, and Google Chat.

This is **not** an application codebase -- it is infrastructure-as-code (CloudFormation YAML) plus documentation. There are no Lambda functions, no package.json, no build system, and no automated tests.

## Repository Structure

```
.
├── clawdbot-bedrock.yaml          # Main CloudFormation template (Linux/Graviton)
├── clawdbot-bedrock-mac.yaml      # macOS CloudFormation template (Apple Silicon/Intel)
├── .kiro/
│   └── steering/
│       └── deploy-moltbot-conversationally.md  # Kiro AI deployment guide (~1300 lines)
├── eks/                           # EKS deployment (Terraform + scripts)
│   ├── terraform/                 # Terraform modules (VPC, EKS, operator, admin-console, etc.)
│   ├── manifests/                 # K8s manifest examples
│   └── scripts/                   # Install, cleanup, validate scripts
├── enterprise/                    # Enterprise platform (Admin Console, Agent Container, Gateway)
│   ├── admin-console/             # React + FastAPI admin console
│   │   ├── server/                # FastAPI backend (routers, services, db, s3ops)
│   │   ├── src/                   # React frontend (pages, hooks, components)
│   │   ├── Dockerfile             # Multi-stage build (Node + Python)
│   │   └── deploy-eks.sh          # Standalone EKS deploy script
│   ├── agent-container/           # OpenClaw agent Docker image
│   └── gateway/                   # Tenant Router, Bedrock H2 Proxy
├── images/                        # Screenshots for documentation
├── docs/
│   └── DEPLOYMENT_EKS.md          # EKS deployment guide
├── README.md                      # Primary documentation (English)
├── README_CN.md                   # Chinese documentation
├── DEPLOYMENT.md                  # Step-by-step deployment guide (EC2)
├── SECURITY.md                    # Security architecture and best practices
├── TROUBLESHOOTING.md             # Common issues and resolution steps
├── QUICK_START_KIRO.md            # Kiro AI-guided deployment quickstart
├── CONTRIBUTING.md                # Contribution guidelines
├── CODE_OF_CONDUCT.md             # Amazon Open Source Code of Conduct
└── LICENSE                        # MIT No Attribution
```

## Key Files

### CloudFormation Templates

- **`clawdbot-bedrock.yaml`** (~660 lines): The primary deployment template for Linux (Ubuntu 24.04 on Graviton ARM or x86). Creates a full VPC, IAM roles, security groups, optional VPC endpoints, and an EC2 instance that bootstraps OpenClaw via a UserData script.

- **`clawdbot-bedrock-mac.yaml`** (~760 lines): macOS variant requiring a Dedicated Host. Supports mac1.metal (Intel), mac2.metal (M1), mac2-m2.metal (M2), and mac2-m2pro.metal (M2 Pro). Includes region-specific AMI mappings.

### Template Parameters

Both templates share these key parameters:
- `OpenClawModel` -- Bedrock model ID (default: `global.amazon.nova-2-lite-v1:0`; 9 models available)
- `InstanceType` -- EC2 instance type (default: `c7g.large` Graviton)
- `KeyPairName` -- EC2 key pair for emergency SSH
- `AllowedSSHCIDR` -- SSH access CIDR (set to `127.0.0.1/32` to disable)
- `CreateVPCEndpoints` -- Private networking toggle (adds ~$22/month)
- `EnableSandbox` -- Docker sandbox isolation for non-main sessions

### Template Conditions

- `CreateEndpoints` -- Controls VPC endpoint creation
- `AllowSSH` -- Controls SSH security group rules
- `UseGraviton` -- Detects ARM instance types (t4g, c7g, m7g) for AMI selection

### Resources Created (Linux Template)

| Category | Resources |
|----------|-----------|
| Networking | VPC, Internet Gateway, public/private subnets, route tables |
| VPC Endpoints | Bedrock Runtime, Bedrock Mantle, SSM, SSM Messages, EC2 Messages (conditional) |
| IAM | Role (SSM + CloudWatch + Bedrock + SSM Parameter Store), Instance Profile |
| Security | EC2 security group, VPC endpoint security group (conditional) |
| Compute | EC2 instance with ~200-line UserData bootstrap script |
| Orchestration | CloudFormation WaitCondition with 900s timeout |

### UserData Bootstrap Sequence

The EC2 UserData script installs and configures:
1. System packages (apt-get update/upgrade)
2. AWS CLI v2
3. SSM Agent
4. Docker engine
5. Node.js 22 via NVM (with retry logic)
6. OpenClaw npm package (global install with retry logic)
7. AWS region detection and configuration
8. OpenClaw config (`~/.openclaw/openclaw.json`) with generated gateway token
9. systemd user service registration

## Supported Models

| Model | ID |
|-------|----|
| Nova 2 Lite (default) | `global.amazon.nova-2-lite-v1:0` |
| Claude Sonnet 4.5 | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| Nova Pro | `us.amazon.nova-pro-v1:0` |
| Claude Opus 4.6 | `global.anthropic.claude-opus-4-6-v1` |
| Claude Opus 4.5 | `global.anthropic.claude-opus-4-5-20251101-v1:0` |
| Claude Haiku 4.5 | `global.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Claude Sonnet 4 | `global.anthropic.claude-sonnet-4-20250514-v1:0` |
| DeepSeek R1 | `us.deepseek.r1-v1:0` |
| Llama 3.3 70B | `us.meta.llama3-3-70b-instruct-v1:0` |
| Kimi K2.5 | `moonshotai.kimi-k2.5` |

**Note on Project Mantle models:** Kimi K2.5 is a Project Mantle model that uses the `bedrock-mantle` endpoint (`https://bedrock-mantle.REGION.api.aws/v1`) for OpenAI-compatible API access. While it also works through the standard Converse API (`bedrock-runtime`), there are known issues with tool-call parsing. The templates include both `bedrock-runtime` and `bedrock-mantle` VPC endpoints to support all models.

## Supported Regions

Linux template: us-east-1, us-west-2, eu-west-1, ap-northeast-1

Mac template: us-east-1, us-east-2, us-west-2, eu-west-1, eu-central-1, ap-southeast-1, ap-southeast-2

## Development Guidelines

### Working with CloudFormation Templates

- Templates use YAML format with AWS CloudFormation syntax
- The Linux template uses SSM Parameter Store to resolve the latest Ubuntu 24.04 AMI dynamically
- The Mac template uses hardcoded AMI mappings per region (must be updated when new AMIs are released)
- Conditional resources (VPC endpoints, SSH rules) are controlled via `Conditions` blocks
- The UserData script is embedded as a `Fn::Base64`/`Fn::Sub` block within the EC2 instance resource

### Naming Conventions

- Template files: `clawdbot-bedrock*.yaml` (legacy naming from Clawdbot era)
- The project has been renamed multiple times: Moltbot -> Clawdbot -> OpenClaw
- Current branding is **OpenClaw** in all user-facing text
- Internal resource logical IDs use `OpenClaw` prefix (e.g., `OpenClawInstance`, `OpenClawRole`)

### Making Changes

When modifying CloudFormation templates:
1. Validate YAML syntax (no tabs, correct indentation)
2. Ensure `Fn::Sub` variable references (`${Variable}`) match defined parameters/resources
3. Keep Linux and Mac templates in sync for shared features
4. Update the `Description` field if the template's purpose changes
5. Test parameter `AllowedValues` lists match between both templates when applicable
6. Update documentation (README.md, DEPLOYMENT.md, etc.) if user-visible behavior changes

### CloudFormation Validation

There are no automated tests. Validate templates manually:

```bash
# Validate template syntax
aws cloudformation validate-template --template-body file://clawdbot-bedrock.yaml

# Validate Mac template
aws cloudformation validate-template --template-body file://clawdbot-bedrock-mac.yaml
```

### Security Considerations

- IAM policies follow least-privilege: only `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:ListFoundationModels`, and SSM parameter access
- SSM Session Manager is the primary access method (no public SSH needed)
- VPC endpoints keep Bedrock API traffic on the AWS private network
- Gateway tokens are generated at deploy time using `openssl rand -hex 24`
- Docker sandbox is enabled by default for session isolation
- Never commit or hardcode API keys, tokens, or credentials in templates

### Documentation Standards

- README.md is the primary entry point; keep it comprehensive
- README_CN.md mirrors the English README in Chinese
- DEPLOYMENT.md contains step-by-step operational procedures
- SECURITY.md documents the security architecture
- TROUBLESHOOTING.md covers common issues with specific commands
- All docs reference SSM Session Manager as the recommended access method

### Commit History Conventions

Based on the repository history, commit messages follow a concise descriptive style:
- `Add Claude Opus 4.6 support and consolidate IMDS detection`
- `Enable WhatsApp, Telegram, Discord, Slack, iMessage, Google Chat`
- `rename openclaw`
- `Security fix: remove reference to unclaimed npm package`

Use clear, descriptive messages that explain what changed and why.

## Architecture Summary

```
User Device
  |
  v
Messaging Platform (WhatsApp/Telegram/Discord/Slack/Teams)
  |
  v
EC2 Instance (Ubuntu 24.04, Graviton ARM or x86)
  ├── OpenClaw (Node.js application, npm package)
  ├── Gateway Web UI (port 18789, loopback only)
  ├── Docker (sandbox isolation)
  └── SSM Agent (secure remote access)
  |
  v
Amazon Bedrock (model inference via VPC endpoint or public endpoint)
  |
  v
CloudTrail (API audit logging)
```

Access flow: Local machine -> SSM Session Manager port forwarding -> EC2 localhost:18789 -> OpenClaw Gateway UI

## License

MIT No Attribution (MIT-0) -- see LICENSE file.
