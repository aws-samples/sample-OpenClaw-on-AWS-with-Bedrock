# OpenClaw Enterprise — Environment Registry

**Last updated:** 2026-04-14

---

## Production

| Stack | Account | Profile | Region | Instance | Type | URL | Password |
|-------|---------|---------|--------|----------|------|-----|----------|
| **openclaw-jiade2** | 651770013524 | jiade2 | ap-northeast-1 | i-0344c501e6bdd0649 | c7g.large | https://openclaw.awspsa.com | wjiad@amazon |

- CloudFront: `E21RJOMTNCOF1N` (default account 263168716248) → origin: Tokyo ALB `openclaw-tokyo-alb-*.ap-northeast-1.elb.amazonaws.com`
- OriginReadTimeout: 60s (updated 2026-04-14, was 30s)
- S3 Bucket: `openclaw-tenants-651770013524`
- DynamoDB: `openclaw-jiade2` (ap-northeast-1)
- ECR: `651770013524.dkr.ecr.ap-northeast-1.amazonaws.com/openclaw-jiade2-multitenancy-agent`
- AgentCore: 4 runtimes (Standard/Restricted/Engineering/Executive), all READY
- ECS Cluster: `openclaw-jiade2-always-on` (Fargate tiers not yet created in prod)
- SSM Agent: Online

**4-Tier Runtime Configuration:**

| Tier | Runtime ID | Model | Guardrail |
|------|-----------|-------|-----------|
| Standard | openclaw_jiade2_runtime-vonFyFBDNw | minimax.minimax-m2.5 | moderate (ztr7izsru5qe) |
| Restricted | openclawJiade2RestrictedRuntime-QtQptkCSrT | deepseek.v3.2 | strict (elk5damd3rvk) |
| Engineering | openclawJiade2EngineeringRuntime-J1UVRfERC1 | claude-sonnet-4-5 | none |
| Executive | openclawJiade2ExecutiveRuntime-V0oNglF1KP | claude-sonnet-4-6 | none |

> **NOTE:** environments.md previously stated production was openclaw-demo in us-west-2.
> That was wrong — CloudFront origin actually points to Tokyo (ap-northeast-1).
> The us-west-2 openclaw-demo stack exists but is NOT the CloudFront origin.

---

## Test / Dev

| Stack | Account | Profile | Region | Instance | Type | URL | Password |
|-------|---------|---------|--------|----------|------|-----|----------|
| **openclaw-e2e-test** | 263168716248 | default | us-east-2 | i-054cb53703d2ba33c | c7g.large | https://dev-openclaw.awspsa.com | e2e-test-2026 |

- CloudFront: `E1KNUZKAIOJVUA` (default account) → origin: EC2:8099 `ec2-3-128-78-89.us-east-2.compute.amazonaws.com`
- OriginReadTimeout: 60s
- S3 Bucket: `openclaw-e2e-test-263168716248`
- DynamoDB: `openclaw-e2e-test` (us-east-2)
- ECR: `263168716248.dkr.ecr.us-east-2.amazonaws.com/openclaw-e2e-test-multitenancy-agent`
- AgentCore: 1 runtime (default, no env vars configured)
- ECS Cluster: `openclaw-e2e-test-always-on`
- **Fargate 4 Tiers: RUNNING** (desiredCount=1, ~$2.4/day)
- SSM Agent: Online
- Test data: 72+ Fargate calls, 179 AUDIT, 68 SESSION, 240 CONV records

**Fargate Tier Configuration:**

| Tier | Service Name | Model | Guardrail |
|------|-------------|-------|-----------|
| Standard | openclaw-e2e-test-tier-standard | minimax.minimax-m2.5 | none |
| Restricted | openclaw-e2e-test-tier-restricted | deepseek.v3.2 | none |
| Engineering | openclaw-e2e-test-tier-engineering | claude-sonnet-4-5 | none |
| Executive | openclaw-e2e-test-tier-executive | claude-sonnet-4-6 | none |

---

## Other Environments (jiade2 account: 651770013524)

| Stack | Region | Instance | Status | Purpose |
|-------|--------|----------|--------|---------|
| openclaw-demo | us-west-2 | i-03dac284e7ea0bb41 | running | **NOT the CloudFront origin** (was previously, now Tokyo is) |
| openclaw-us-east-1 | us-east-1 | i-09af1289811425733 | running | US East test |

---

## Legacy Environments (default account: 263168716248)

| Stack | Region | Instance | Type | Status | Purpose |
|-------|--------|----------|------|--------|---------|
| openclaw-multitenancy | us-east-1 | i-0aa07bd9a04fa2255 | c7g.xlarge | running | Original dev (expensive, consider stopping) |
| openclaw-test-4dot5 | us-west-2 | i-0213514501de78afe | t4g.small | running | Version 4.5 test |
| openclaw-verify-e2e | us-west-2 | i-00f2ee133c77d30a8 | t4g.small | running | E2E verification |
| openclaw-bedrock-Jiade | ap-northeast-1 | i-0e567d6d158c573b6 | r7g.large | running | Tokyo personal dev |

---

## Access Methods

**Browser (preferred):**
```
Production: https://openclaw.awspsa.com        (emp-jiade / wjiad@amazon)
Test/Dev:   https://dev-openclaw.awspsa.com    (emp-jiade / e2e-test-2026)
```

**SSM Port Forward (direct, no CloudFront):**
```bash
# Production (Tokyo)
aws ssm start-session --target i-0344c501e6bdd0649 --region ap-northeast-1 --profile jiade2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=8099,localPortNumber=8099
# → http://localhost:8099

# Test (Ohio)
aws ssm start-session --target i-054cb53703d2ba33c --region us-east-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=8099,localPortNumber=8099
# → http://localhost:8099
```

**Gateway UI (IM bot config):**
```bash
aws ssm start-session --target <INSTANCE_ID> --region <REGION> --profile <PROFILE> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=18789,localPortNumber=18789
# → http://localhost:18789
```

---

## Deployment

**Deploy code to production (Tokyo):**
```bash
# Upload files to S3 → SSM command pulls + rebuilds Docker + restarts services
S3=openclaw-tenants-651770013524
aws s3 sync enterprise/agent-container/ s3://$S3/_deploy/prod-update/agent-container/ --region ap-northeast-1 --profile jiade2
# ... (gateway, admin-console files similarly)
# Then SSM: docker build + ecr push + systemctl restart
# Then: update all 4 AgentCore runtimes
```

**Deploy code to test (Ohio):**
```bash
S3=openclaw-e2e-test-263168716248
aws s3 sync enterprise/agent-container/ s3://$S3/_deploy/fargate-phase1/agent-container/ --region us-east-2
# ... same pattern as production
# Fargate tiers: aws ecs update-service --force-new-deployment
```

**CloudFront cache invalidation:**
```bash
# Production
aws cloudfront create-invalidation --distribution-id E21RJOMTNCOF1N --paths "/*"
# Test
aws cloudfront create-invalidation --distribution-id E1KNUZKAIOJVUA --paths "/*"
```

---

## Code Parity

| Component | Production (Tokyo) | Test (Ohio) | Same? |
|-----------|-------------------|-------------|-------|
| Docker image | Latest (2026-04-14) | Latest (2026-04-14) | ✓ |
| EC2 services | H2 Proxy + Tenant Router + Admin Console | Same | ✓ |
| AgentCore runtimes | 4 tiers, env vars configured | 1 default, no env vars | ✗ |
| Fargate tiers | Not created | 4 tiers RUNNING | ✗ |
| DynamoDB seed data | 20 employees, full org | 20 employees, full org + 72 Fargate test calls | ≈ |
| Guardrails | 2 (strict + moderate), DRAFT | 2 (strict + moderate), DRAFT | ✓ |

---

## Cost Notes

- **Production:** EC2 c7g.large (~$60/mo) + AgentCore usage + DynamoDB/S3
- **Test:** EC2 c7g.large (~$60/mo) + **4 Fargate tiers running (~$73/mo)**
- **Fargate warning:** Test env 4 tiers at desiredCount=1 costs ~$2.4/day. Scale to 0 when not testing.
- **Legacy:** 4 instances in default account still running — consider stopping to save ~$120/mo
