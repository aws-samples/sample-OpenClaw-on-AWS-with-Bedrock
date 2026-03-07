# Roadmap

Target: **v1.0 by June 2026** — a production-ready multi-tenant OpenClaw platform.

---

## ✅ Done (March 2026)

### Standard Deployment (EC2) — Production Ready

- One-click CloudFormation deploy (Linux/Mac/China regions)
- 10 Bedrock models, Graviton ARM, VPC Endpoints
- SSM Session Manager (no public ports)
- Gateway token in SSM SecureString (never on disk)
- Supply-chain hardened (no `curl | sh`)
- S3 Files Skill auto-installed
- Docker sandbox for code isolation
- Kiro conversational deployment guide
- Bedrock Mantle regional support (conditional VPC endpoint)

### Multi-Tenant Core Components

- Agent Container: Plan A (system prompt injection) + Plan E (response audit)
- Auth Agent: risk assessment, 30-min auto-reject, ApprovalToken issuance (max 24h)
- Safety module: 13 memory poisoning patterns, input validation, path traversal checks
- Identity module: token lifecycle (issue/validate/revoke)
- Observability: structured CloudWatch JSON per tenant
- CloudFormation: EC2 + ECR + SSM + CloudWatch (one stack)
- Gateway Tenant Router: tenant_id derivation, AgentCore Runtime invocation
- Auth Agent input validation: 7 prompt injection patterns on approval messages
- Admin Console: visual management UI (dashboard, tenant permissions, approvals, audit log, live demo)

---

## 🔨 March — April 2026: Wire It Up

The critical path to a working end-to-end multi-tenant flow.

### End-to-End Integration

- [ ] Integration test suite: WhatsApp message → Tenant Router → AgentCore → Container → response
- [ ] Tenant Router systemd service (auto-start on EC2 boot)
- [ ] OpenClaw webhook configuration to forward messages to Tenant Router (port 8090)
- [ ] Verify Firecracker microVM isolation per tenant (filesystem, memory, network)
- [ ] Load test: 50 concurrent tenants, measure latency and cost

### Auth Agent Channel Delivery

- [ ] Send approval notifications to admin's WhatsApp via OpenClaw Gateway API
- [ ] Send approval notifications to admin's Telegram via Bot API
- [ ] Parse admin replies: "approve", "reject", "approve temporary 2h", "approve persistent"
- [ ] Handle edge cases: admin offline, message delivery failure, duplicate replies

### Cost Validation

- [ ] Benchmark AgentCore cold start latency (first message per tenant)
- [ ] Measure cost at 10, 100, 1000 conversations/day
- [ ] Document break-even point: when AgentCore becomes cheaper than dedicated EC2
- [ ] Per-tenant cost metering and chargeback reporting

---

## 🎯 April — May 2026: Enterprise Features

### Shared Skills with Bundled Credentials

- [ ] Skill packaging format: manifest declaring required permissions, bundled SaaS keys
- [ ] Skill installation API: install once, authorize per tenant profile
- [ ] Credential isolation: SaaS keys stored in SSM SecureString, injected at runtime, never exposed to tenants
- [ ] Example skills: Jira (ticket management), Slack (cross-channel messaging), S3 (file sharing)

### Per-Tenant Enterprise Rules

- [ ] Rule templates: "finance-readonly", "engineering-full", "intern-basic"
- [ ] SSM-based rule hot-reload (no redeployment)
- [ ] Rule inheritance: department rules → team rules → individual overrides
- [ ] Compliance presets: HIPAA, SOC2, PCI-DSS (restrict tools + enable audit)

### Controlled Information Sharing

- [ ] Cross-tenant data sharing policies in SSM
- [ ] Team → Department aggregation: team agent outputs readable by department agent
- [ ] Shared knowledge base: company policies, product docs (read-only across all tenants)
- [ ] Audit trail for every cross-boundary data access

### Agent Orchestration

- [ ] Agent-to-agent invocation: agent A triggers agent B via AgentCore session
- [ ] Workflow chains: Finance agent → Compliance agent → Executive agent
- [ ] Scheduled orchestration: Monday 8am, all team agents generate weekly summaries → department agent aggregates
- [ ] Event-driven triggers: PR merged → Engineering agent notifies QA agent

---

## 🚀 May — June 2026: Platform & Ecosystem

### Skills Marketplace

- [ ] Skill catalog API: list, search, install, uninstall
- [ ] Permission declaration: each skill declares what tools/data/APIs it needs
- [ ] Security review workflow: submitted → reviewed → approved/rejected
- [ ] Community skill submissions via GitHub PR
- [ ] Skill versioning and rollback

### Agent Hierarchy

- [ ] Organization → Department → Team → Individual agent tree
- [ ] Hierarchical permission inheritance with override
- [ ] Cross-level communication channels (controlled, audited)
- [ ] Dashboard: org-wide agent activity, cost, permission usage

### Hard Enforcement (AgentCore Gateway MCP Mode)

- [ ] Evaluate AgentCore Gateway MCP mode for tool-call interception
- [ ] Implement MCP-based permission checks (replace Plan A soft enforcement)
- [ ] Keep Plan E audit as defense-in-depth
- [ ] Benchmark latency impact of MCP interception

### Observability Dashboard

- [ ] CloudWatch dashboard CloudFormation template (per-tenant metrics)
- [ ] Cost anomaly detection (alert on unusual Bedrock spend per tenant)
- [ ] Permission denial trends (identify misconfigured tenants)
- [ ] Agent health monitoring (response latency, error rates)

### Production Hardening

- [ ] Multi-region deployment support
- [ ] Disaster recovery: tenant config backup/restore via SSM export
- [ ] Rate limiting per tenant (prevent single tenant from consuming all capacity)
- [ ] Tenant onboarding automation: new employee → auto-create agent with role-based profile

---

## Beyond June 2026

The platform foundation enables:

- **OpenClaw SaaS**: hosted multi-tenant OpenClaw as a service
- **Enterprise MSP**: managed OpenClaw platform for organizations (deploy, operate, optimize)
- **Lightsail integration**: simpler infrastructure for smaller deployments
- **Permissions Vending Machine**: temporary IAM elevation with approval workflow ([Issue #29](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues/29))
- **AgentCore Memory**: persistent cross-session memory with poisoning detection on load
- **Federation**: connect OpenClaw platforms across organizations for B2B agent collaboration

---

## How to Help

We're building this in the open and moving fast. Pick what interests you:

| What | Why it matters | How to start |
|------|---------------|-------------|
| Integration testing | Validates the core flow works | Run the deployment, report issues |
| Auth Agent delivery | Makes approval workflow real | Implement WhatsApp/Telegram sending in `handler.py` |
| Skill packaging | Enables the shared skills vision | Design the manifest format, open a PR |
| Agent orchestration | Enables agent hierarchy | Prototype agent-to-agent invocation |
| Cost benchmarking | Proves the economics | Deploy, measure, share data |
| Security review | Builds trust | Audit the code, file issues |
| Documentation | Lowers the barrier | Write guides, improve READMEs |

**[→ Contributing Guide](CONTRIBUTING.md)** · **[→ GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)** · **[→ Discussions](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/discussions)**
