# OpenClaw AWS Bedrock - Single-User Optimization

This branch optimizes the project for single-user deployments with comprehensive documentation and maintenance tools.

## 🎯 Key Changes

### 1. Documentation
- **README.md** - Refactored to emphasize single-user deployment
- **SINGLE_USER_GUIDE.md** - Complete bilingual deployment guide (EN/CN)
- **docs/BEDROCK_MODELS_GUIDE.md** - Comprehensive Bedrock model configuration
- **docs/KIRO_INSTALLATION.md** - Kiro CLI installation guide

### 2. Maintenance Scripts
- **scripts/backup.sh** - Automated backup for config and workspace
- **scripts/health-check.sh** - System health monitoring
- **scripts/install-kiro.sh** - Kiro CLI installation automation

### 3. CloudFormation Updates
- **clawdbot-bedrock.yaml** - Optimized UserData (removed Kiro auto-install due to 16KB limit)
- **clawdbot-bedrock-mac.yaml** - Same optimization for macOS

## 📊 Benefits

**For Single Users:**
- Clear deployment path with step-by-step guide
- Maintenance tools for backup and health checks
- Detailed model selection guidance (15+ Bedrock models)
- Cost optimization recommendations

**For Enterprise:**
- Multi-tenant options still available
- AgentCore integration documented
- Clear comparison table in README

## 🚀 Quick Start

```bash
# Deploy single-user stack
aws cloudformation create-stack \
  --stack-name openclaw-production \
  --template-body file://clawdbot-bedrock.yaml \
  --parameters \
    ParameterKey=OpenClawModel,ParameterValue=global.anthropic.claude-sonnet-4-5-20250929-v1:0 \
    ParameterKey=InstanceType,ParameterValue=t4g.large \
  --capabilities CAPABILITY_IAM

# After deployment, install Kiro CLI (optional)
./scripts/install-kiro.sh
```

## 📝 File Structure

```
OpenClaw-AWS-Bedrock/
├── README.md                           # Main readme (refactored)
├── SINGLE_USER_GUIDE.md                # Single-user deployment guide
├── clawdbot-bedrock.yaml               # Main CloudFormation template
├── clawdbot-bedrock-mac.yaml          # macOS variant
├── docs/
│   ├── BEDROCK_MODELS_GUIDE.md        # Model configuration guide
│   └── KIRO_INSTALLATION.md           # Kiro CLI guide
└── scripts/
    ├── backup.sh                       # Backup automation
    ├── health-check.sh                 # Health monitoring
    └── install-kiro.sh                 # Kiro installation
```

## ⚠️ Known Issues & Solutions

### GuardDuty VPC Endpoint Conflict

**Issue:** When deleting stacks in accounts with GuardDuty enabled, VPC deletion may fail due to GuardDuty-managed resources.

**Solution:**
```bash
# Before deleting a stack, manually clean up GuardDuty resources:
VPC_ID=$(aws cloudformation describe-stack-resource \
  --stack-name YOUR_STACK \
  --logical-resource-id OpenClawVPC \
  --query 'StackResourceDetail.PhysicalResourceId' \
  --output text)

# Delete GuardDuty VPC Endpoints
aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=service-name,Values=*guardduty*" \
  --query 'VpcEndpoints[].VpcEndpointId' \
  --output text | \
  xargs -I {} aws ec2 delete-vpc-endpoints --vpc-endpoint-ids {}

# Delete GuardDuty Security Groups
aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=GuardDutyManagedSecurityGroup-*" \
  --query 'SecurityGroups[].GroupId' \
  --output text | \
  xargs -I {} aws ec2 delete-security-group --group-id {}

# Wait 30 seconds, then delete stack
sleep 30
aws cloudformation delete-stack --stack-name YOUR_STACK
```

### UserData Size Limit

**Issue:** CloudFormation UserData is limited to 16KB. Adding too many setup scripts can exceed this limit.

**Solution:** Kiro CLI installation moved to post-deployment manual step. See `docs/KIRO_INSTALLATION.md` for instructions.

## 🎓 Best Practices

1. **Model Selection:** Start with `claude-sonnet-4-5` for best balance of cost and performance
2. **Backup:** Run `scripts/backup.sh` regularly
3. **Monitoring:** Use `scripts/health-check.sh` for system health
4. **Cost Control:** Set CloudWatch billing alarms
5. **Security:** Follow security audit recommendations from health-check

## 📚 Related Documentation

- [AWS Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [OpenClaw Documentation](https://docs.openclaw.ai)
- [Kiro CLI](https://kiro.dev)

## 🤝 Contributing

This branch focuses on single-user optimizations. For enterprise/multi-tenant features, see the main branch or create a separate feature branch.

---

**Branch:** optimize-single-user  
**Status:** Ready for review  
**Target:** main (via PR)
