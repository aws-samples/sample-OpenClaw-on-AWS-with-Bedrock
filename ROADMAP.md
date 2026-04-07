# Roadmap

Target: **v1.0 by April 30, 2026** — production-ready multi-tenant OpenClaw platform.

---

## ✅ Done (as of March 17, 2026)

### Standard Deployment — Production Ready
- One-click CloudFormation (Linux/Mac/China), 10 Bedrock models, Graviton ARM
- SSM Session Manager, VPC Endpoints, CloudTrail, Docker sandbox
- S3 Files Skill, Kiro conversational deploy guide

### Multi-Tenant E2E Pipeline — Verified ✅
- Agent Container: `openclaw agent` CLI subprocess, Plan A + Plan E
- Bedrock H2 Proxy: Node.js HTTP/2, intercepts AWS SDK calls via `AWS_ENDPOINT_URL`
- Tenant Router: tenant_id derivation, AgentCore invoke (300s timeout)
- AgentCore Runtime: Firecracker microVM per tenant, ECR image
- IM bridging: zero OpenClaw code changes, IM config same as single-user
- systemd services, CloudFormation one-stack, Admin Console UI

| Metric | Current |
|--------|---------|
| Cold start (real) | ~22-25s (optimized from ~30s) |
| Cold start (user-perceived) | ~2-3s (fast-path) |
| Warm request | ~10s |
| Cost (50 users) | ~$1.30-2.20/person/month |

---

## Week 1: Mar 17-23 — Optimize & Stabilize

### Cold Start Optimization (30s → ~2-3s user-perceived) ✅
- [x] `NODE_COMPILE_CACHE` + V8 bytecode pre-warm in Dockerfile
- [x] Multi-stage Docker build (slim runtime image, no build tools)
- [x] Force IPv4 (`--dns-result-order=ipv4first`) for VPC compatibility
- [x] H2 Proxy fast-path: direct Bedrock call for cold tenants (~2-3s)
- [x] Tenant state tracking (cold/warming/warm) with TTL expiry
- [x] Async microVM prewarming (fire-and-forget on first request)
- [x] openclaw agent CLI subprocess retry with linear backoff
- [ ] Benchmark each phase, validate improvements on EC2

### Production Reliability
- [ ] Tenant Router as systemd service (auto-start on boot)
- [ ] Health check endpoint for all 3 services
- [ ] Log rotation, crash recovery
- [ ] Automated E2E smoke test script

### IM End-to-End Validation
- [ ] Configure Telegram bot, send real message through full pipeline
- [ ] Two different users → verify different tenant_ids and microVM isolation
- [ ] WhatsApp QR pairing through multi-tenant gateway

---

## Week 2: Mar 24-30 — Permission & Cost

### Permission Enforcement
- [ ] Test Plan A bypass attempts, measure and fix gaps
- [ ] Plan E real-time blocking option (not just audit)
- [ ] Tool allowlist in openclaw.json per tenant
- [ ] Permission hot-reload from SSM (no microVM restart)
- [ ] Cedar policy engine evaluation

### Per-Tenant Cost Metering
- [ ] Track Bedrock tokens per tenant_id (from server.py response)
- [ ] CloudWatch metric: `BedrockTokens` by tenant_id
- [ ] Monthly cost report (S3 CSV)
- [ ] Budget alerts when tenant exceeds threshold

### Auth Agent Channel Delivery
- [ ] Send approval notifications via WhatsApp/Telegram
- [ ] Parse admin replies: approve/reject/temporary
- [ ] Handle offline admin, delivery failure

---

## Week 3: Mar 31 - Apr 6 — Shared Skills & Rules

### Shared Skills with Bundled Credentials
- [ ] Skill packaging format: manifest + bundled SaaS keys
- [ ] Install once, authorize per tenant profile
- [ ] Credential isolation: SSM SecureString, injected at runtime
- [ ] Example: Jira skill, S3 file sharing skill

### Per-Tenant Enterprise Rules
- [ ] Rule templates: finance-readonly, engineering-full, intern-basic
- [ ] Rule inheritance: department → team → individual
- [ ] Compliance presets: HIPAA, SOC2
- [ ] Admin Console: visual rule editor

### Controlled Information Sharing
- [ ] Cross-tenant data sharing policies
- [ ] Shared knowledge base (read-only across tenants)
- [ ] Audit trail for cross-boundary access

---

## Week 4: Apr 7-13 — EKS Runtime + Agent Orchestration

### EKS as Third Runtime Backend ✅
- [x] K8s client service (`kubernetes-asyncio`): CRD CRUD, pod status/logs, operator management
- [x] Admin EKS router: deploy/stop/reload/status/logs, cluster discover/associate, operator install/upgrade
- [x] 3-tier tenant routing: ECS → EKS → AgentCore (serverless fallback)
- [x] Frontend: EKS tab in Settings (cluster/operator) and Agent Factory (instances)
- [x] Terraform module: DynamoDB, S3, ECR, IAM with Pod Identity
- [x] Standalone `deploy-eks.sh` script (no Terraform required)
- [x] Dockerfile for containerized deployment
- [x] 73 unit tests (k8s_client, admin_eks router, routing)
- [x] Deployed to dev-cluster (us-west-2)

### Agent Orchestration
- [ ] Agent-to-agent invocation via AgentCore session
- [ ] Workflow chains: Finance → Compliance → Executive
- [ ] Scheduled orchestration: weekly summaries
- [ ] Event-driven triggers

### Agent Hierarchy
- [ ] Org → Department → Team → Individual agent tree
- [ ] Hierarchical permission inheritance
- [ ] Cross-level communication (controlled, audited)

---

## Week 5: Apr 14-20 — Platform & Marketplace

### Skills Marketplace
- [ ] Skill catalog API: list, search, install
- [ ] Permission declaration per skill
- [ ] Security review workflow
- [ ] Community submissions via GitHub PR

### Hard Enforcement (MCP Mode)
- [ ] Evaluate AgentCore Gateway MCP for tool-call interception
- [ ] MCP-based permission checks (replace Plan A soft enforcement)
- [ ] Benchmark latency impact

### Observability Dashboard
- [ ] CloudWatch dashboard CFN template (per-tenant metrics)
- [ ] Cost anomaly detection
- [ ] Permission denial trends
- [ ] Agent health monitoring

---

## Week 6: Apr 21-27 — Hardening & Documentation

### Production Hardening
- [ ] Multi-region deployment support
- [ ] Disaster recovery: tenant config backup/restore
- [ ] Rate limiting per tenant
- [ ] Tenant onboarding automation: new employee → auto-create agent

### Documentation & Launch Prep
- [ ] Deployment guide: step-by-step for enterprise IT
- [ ] Security whitepaper: isolation model, threat analysis
- [ ] Cost calculator: interactive tool for enterprise sizing
- [ ] Video demo: 5-min walkthrough
- [ ] Blog post draft

---

## Apr 28-30 — Final Testing & v1.0 Release

- [ ] Full regression: single-user + multi-tenant
- [ ] Load test: 50 concurrent tenants
- [ ] Security audit: penetration test on Plan A/E
- [ ] Tag v1.0, publish release notes

---

## Post v1.0 (May+)

- **OpenClaw SaaS**: hosted multi-tenant as a service
- **Enterprise MSP**: managed platform for organizations
- **Permissions Vending Machine**: temporary IAM elevation
- **AgentCore Memory**: persistent cross-session memory
- **Federation**: B2B agent collaboration across organizations

---

## How to Help

| What | Deadline | How to start |
|------|----------|-------------|
| Cold start optimization | Mar 23 | Profile container startup, submit PR |
| Permission bypass testing | Mar 30 | Try to bypass Plan A, file issues |
| Cost benchmarking | Mar 30 | Deploy, measure, share data |
| Skill packaging format | Apr 6 | Design manifest, open PR |
| Agent orchestration | Apr 13 | Prototype agent-to-agent invocation |
| Security audit | Apr 27 | Audit code, file issues |

**[→ Contributing Guide](CONTRIBUTING.md)** · **[→ GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)**
