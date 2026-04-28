# OpenClaw Enterprise on AgentCore

**Full documentation:** **[README_ENTERPRISE.md](../README_ENTERPRISE.md)**

**[Interactive UI Guide](https://aws-samples.github.io/sample-OpenClaw-on-AWS-with-Bedrock/ui-guide.html)** — 25-page bilingual documentation covering architecture design, every admin and portal page, deployment guide, and platform screenshots. Start here to understand what the platform does and how to use it.

---

## Quick Start

```bash
cd enterprise
cp .env.example .env        # edit: STACK_NAME, REGION, ADMIN_PASSWORD
bash deploy.sh              # ~15 min — infra + Docker build + seed
```

Then follow Steps 4-6 in [README_ENTERPRISE.md](../README_ENTERPRISE.md) to deploy the Admin Console and Gateway services.

## Key Links

| Resource | Path |
|----------|------|
| Interactive UI Guide | [ui-guide.html](https://aws-samples.github.io/sample-OpenClaw-on-AWS-with-Bedrock/ui-guide.html) |
| Full documentation | [README_ENTERPRISE.md](../README_ENTERPRISE.md) |
| CloudFormation template | [clawdbot-bedrock-agentcore-multitenancy.yaml](clawdbot-bedrock-agentcore-multitenancy.yaml) |
| Environment registry | [docs/environments.md](docs/environments.md) |
| Test plan (62+ cases) | [TESTING.md](TESTING.md) |
| Deployment script | [deploy.sh](deploy.sh) |
| Environment config | [.env.example](.env.example) |
| OIDC SSO setup | [docs/OIDC_SSO_SETUP.md](docs/OIDC_SSO_SETUP.md) |

## Architecture

```
Admin Console (React + FastAPI, 30+ pages)
  ├── Admin: Dashboard, Agent Factory, Security Center, Monitor, Audit, Usage
  ├── Portal: Chat, Profile, Skills, Requests, Connect IM, My Agents
  ├── 3-role RBAC (admin / manager / employee)
  └── Dual auth: Azure AD (Microsoft Entra ID) SSO + Employee ID/Password

4-Tier Runtime Architecture:
  Standard    → Nova 2 Lite, scoped IAM, moderate guardrail
  Restricted  → DeepSeek v3.2, dept-scoped IAM, strict guardrail
  Engineering → Claude Sonnet 4.5, engineering IAM, no guardrail
  Executive   → Claude Sonnet 4.6, full IAM, no guardrail

Dual Deployment Modes:
  Serverless  → AgentCore Firecracker microVM (default, pay-per-use)
  Always-On   → ECS Fargate (admin toggle, 24/7, EFS, direct IM)
```

## OpenClaw Version

Enterprise is pinned to **OpenClaw 2026.3.24** in both `agent-container/Dockerfile` and `exec-agent/Dockerfile`. Do not upgrade — newer versions break IM channel integration.
