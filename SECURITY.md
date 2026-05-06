# Security Best Practices

## Quick Reference: Secure Access Commands

### Connect to EC2 Instance (Secure Method)

```bash
# Get instance ID
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-bedrock \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text \
  --region us-west-2)

# Connect via SSM (no SSH keys needed)
aws ssm start-session --target $INSTANCE_ID --region us-west-2

# Switch to ubuntu user
sudo su - ubuntu
```

### Port Forwarding (Secure Access to Web UI)

```bash
# Start port forwarding (keep terminal open)
aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# Access Web UI at: http://localhost:18789/?token=<your-token>
```

---

## Overview

This deployment follows AWS security best practices and provides multiple layers of protection.

## Security Architecture Summary

The CloudFormation template implements defense-in-depth with the following layers:

### Private Access (SSM Session Manager Only)
When `EnablePublicAccess=false` (default):
1. **EC2 in private subnet** (no public IP)
2. **NAT Gateway** for controlled outbound internet access
3. **SSM Session Manager** access only (no SSH required)
4. **VPC endpoints** (optional, keeps AWS API traffic private)
5. **IMDSv2 enforcement** (prevents SSRF attacks)
6. **EBS encryption** at rest (AWS managed keys)
7. **S3 encryption** at rest (AES256)
8. **IAM role-based authentication** (no API keys)
9. **Docker sandbox** for exec isolation

### Public Access (CloudFront + ALB)
When `EnablePublicAccess=true`, adds:
1. **AWS Shield Standard** (automatic DDoS protection - Layer 3/4)
2. **AWS WAF** (only in us-east-1) - SQL injection, XSS, rate limiting, bad inputs
3. **CloudFront managed prefix list** (only CloudFront IPs can reach ALB)
4. **Custom header validation** (CloudFront → ALB, secret AWS::StackId)
5. **Direct ALB access blocked** (returns 403 without header)
6. **HTTPS enforcement** (CloudFront viewer side)
7. **Geographic restrictions** (optional, via `AllowedCountries` parameter)
8. ⚠️ **HTTP-only origin** (CloudFront → ALB uses unencrypted HTTP)

## Security Features

### 1. IAM Role-Based Authentication

**No API Keys**: The EC2 instance uses an IAM role to authenticate with Bedrock.

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
    "bedrock:ListFoundationModels"
  ],
  "Resource": "*"
}
```

**Benefits**:
- ✅ Automatic credential rotation
- ✅ No secrets in code or config files
- ✅ Centralized access control
- ✅ CloudTrail audit logs

### 2. SSM Session Manager

**No SSH Keys Needed**: Access instances through AWS Systems Manager.

**Benefits**:
- ✅ No public SSH port (22) required
- ✅ Automatic session logging
- ✅ CloudTrail audit trail
- ✅ Session timeout controls
- ✅ No key management

**Enable SSM-only access**:
```yaml
AllowedSSHCIDR: 127.0.0.1/32  # Disables SSH
```

### 3. VPC Endpoints

**Private Network**: Bedrock API calls stay within AWS network.

**Benefits**:
- ✅ Traffic doesn't traverse internet
- ✅ Lower latency
- ✅ Compliance-friendly (HIPAA, SOC2)
- ✅ Reduced attack surface

**Cost**: ~$22/month for 3 endpoints

### 4. Docker Sandbox

**Isolated Execution**: Non-main sessions run in Docker containers.

```json
{
  "sandbox": {
    "mode": "non-main",
    "allowlist": ["bash", "read", "write", "edit"],
    "denylist": ["browser", "canvas", "nodes", "gateway"]
  }
}
```

**Benefits**:
- ✅ Limits blast radius
- ✅ Protects host system
- ✅ Safe for group chats

### 5. Compute Isolation (Enterprise Multi-Tenant)

When running agents for multiple users, compute isolation prevents one agent from observing or interfering with another. AgentCore and ECS Fargate provide hardware-level isolation via Firecracker microVMs (same technology as AWS Lambda). EKS deployments can enable Kata Containers for equivalent isolation.

For detailed runtime comparison, see [docs/DEPLOYMENT_EKS.md](docs/DEPLOYMENT_EKS.md).

### 6. Public Access Security (CloudFront + ALB)

When `EnablePublicAccess=true`, the deployment exposes OpenClaw via CloudFront + ALB:

**Security Layers**:
- ✅ CloudFront HTTPS enforcement (viewer → CloudFront encrypted)
- ✅ AWS WAF protection (SQL injection, XSS, rate limiting)
- ✅ Custom header validation (CloudFront → ALB, secret AWS::StackId)
- ✅ CloudFront managed prefix list (only CloudFront IPs can reach ALB)
- ✅ EC2 in private subnet (no direct internet access)
- ⚠️  **HTTP-only origin** (CloudFront → ALB uses unencrypted HTTP)

**Known Limitation**: The CloudFront → ALB connection uses HTTP (port 80), not HTTPS. This means traffic between CloudFront edge locations and the ALB traverses the AWS network **unencrypted**.

**Risk Assessment**:
- Traffic mostly stays on AWS backbone (private network between CloudFront PoPs and ALB)
- Custom header secret prevents direct ALB access (bypassing CloudFront)
- EC2 instance is in private subnet (no public IP)
- This is an **accepted tradeoff** for deployment simplicity (no domain/ACM certificate required)

**Mitigation** (if end-to-end encryption is required):
1. Obtain a domain name (e.g., via Route 53)
2. Create ACM certificate for the domain
3. Add HTTPS listener on ALB (port 443)
4. Update CloudFront origin to use HTTPS protocol policy
5. Validate certificate on ALB

**Traffic Flow**:
```
User (HTTPS encrypted)
  → CloudFront Edge Location (TLS termination)
  → [AWS Backbone - HTTP plaintext]
  → ALB in VPC (port 80)
  → EC2 in private subnet (port 18789)
```

For personal AI assistant deployments, the HTTP-only origin is acceptable. For production/enterprise use cases requiring full end-to-end encryption, implement the HTTPS ALB listener mitigation above.

## CloudFormation Security Best Practices

This template implements the following AWS security best practices:

### 1. Least Privilege IAM Policies
- ✅ Instance role grants **only** required Bedrock actions (`InvokeModel`, `InvokeModelWithResponseStream`, `ListFoundationModels`)
- ✅ SSM parameter access scoped to `/openclaw/${StackName}/*` only
- ✅ S3 bucket access scoped to specific bucket ARN only
- ✅ CloudWatch logs scoped to `/openclaw/*` log groups only
- ✅ No wildcard `Resource: "*"` except where AWS service requires it (e.g., `ec2:DescribeTags`)

### 2. Encryption at Rest
- ✅ **EBS volumes**: Encrypted with AWS managed keys (`Encrypted: true`)
- ✅ **S3 bucket**: Server-side encryption with AES256 (`SSEAlgorithm: AES256`)
- ✅ **SSM parameters**: Gateway token stored as `SecureString` type (encrypted with KMS)
- ✅ **CloudWatch Logs**: Automatically encrypted by AWS

### 3. Encryption in Transit
- ✅ **Bedrock API**: HTTPS only (enforced by AWS SDK)
- ✅ **SSM Session Manager**: TLS encrypted session tunnel
- ✅ **VPC endpoints**: HTTPS only (TLS 1.2+)
- ✅ **CloudFront viewer**: HTTPS enforced (`redirect-to-https` policy)
- ⚠️ **CloudFront origin**: HTTP only (see section 6 above for details)

### 4. Network Isolation
- ✅ **Private subnet**: EC2 instance has no public IP (`AssociatePublicIpAddress: false`)
- ✅ **Security groups**: Deny-by-default, allow only required ports
- ✅ **Network ACLs**: Stateless firewall on private subnet (HTTPS, DNS, ephemeral ports only)
- ✅ **NAT Gateway**: Single controlled egress point for internet access
- ✅ **VPC endpoints** (optional): Keep AWS API traffic off public internet

### 5. Instance Metadata Security (IMDSv2)
- ✅ **IMDSv2 enforced**: `HttpTokens: required` prevents SSRF attacks
- ✅ **Hop limit**: `HttpPutResponseHopLimit: 2` allows Docker containers to access metadata
- ✅ **Token-based authentication**: Prevents IP spoofing and replay attacks

### 6. S3 Bucket Security
- ✅ **Block public access**: All four settings enabled (`BlockPublicAcls`, `BlockPublicPolicy`, `IgnorePublicAcls`, `RestrictPublicBuckets`)
- ✅ **Versioning enabled**: Protects against accidental deletion/overwrite
- ✅ **Lifecycle policy**: Auto-delete old versions after 30 days (cost optimization)
- ✅ **Encryption**: AES256 server-side encryption
- ✅ **Deletion policy**: Configurable via `EnableDataProtection` parameter

### 7. Secrets Management
- ✅ **Gateway token**: Generated with `openssl rand -hex 24` (192 bits entropy)
- ✅ **Token storage**: SSM Parameter Store SecureString (KMS encrypted)
- ✅ **Custom header**: Uses AWS::StackId (UUID, unpredictable)
- ✅ **No hardcoded secrets**: All secrets generated at stack creation time

### 8. Monitoring & Logging
- ✅ **CloudTrail**: Bedrock API calls automatically logged (account-level)
- ✅ **CloudWatch Logs**: Setup logs, health check logs shipped to CloudWatch
- ✅ **CloudWatch Alarms**: Auto-recovery on system status check failure, reboot on instance check failure
- ✅ **Health checks**: Cron job checks gateway port + HTTP response every 5 minutes

### 9. Defense in Depth (CloudFront + ALB)
- ✅ **AWS Shield Standard**: Automatic DDoS protection (Layer 3/4)
- ✅ **AWS WAF** (us-east-1 only): SQL injection, XSS, rate limiting (1000 req/5min/IP), bad inputs
- ✅ **Custom header validation**: CloudFront sends `X-Custom-Header: ${AWS::StackId}`, ALB validates
- ✅ **CloudFront managed prefix list**: ALB security group only allows CloudFront IP ranges
- ✅ **Default deny**: ALB listener returns 403 for requests without custom header
- ✅ **Geographic restrictions** (optional): `AllowedCountries` parameter restricts CloudFront access by country

### 10. Patch Management
- ✅ **Latest Ubuntu 24.04 LTS**: Resolved dynamically via SSM Parameter Store
- ✅ **Unattended security upgrades**: Automatically configured during setup
- ✅ **Package updates**: `apt-get upgrade -y` during initial setup
- ✅ **OpenClaw updates**: Version pinned via `OpenClawVersion` parameter (controlled upgrades)

### 11. Secure Defaults
- ✅ **VPC endpoints disabled by default**: Saves cost, users opt-in for production
- ✅ **Public access disabled by default**: SSM Session Manager only, users opt-in for internet exposure
- ✅ **WAF enabled by default** (when in us-east-1): Protection-first approach
- ✅ **Data protection disabled by default**: Prevents orphaned resources during testing
- ✅ **Monitoring enabled by default**: CloudWatch alarms + logs shipped automatically

### 12. Resource Tagging
- ✅ **All resources tagged**: `Name` tags for easy identification
- ✅ **Stack name prefix**: All resources include `${AWS::StackName}` for multi-stack support
- ✅ **Cost allocation**: Tags enable cost tracking per stack

## Security Checklist

### Deployment

- [ ] Enable VPC endpoints for production
- [ ] Set `AllowedSSHCIDR` to your IP or disable SSH
- [ ] Enable Docker sandbox
- [ ] Use latest AMI
- [ ] Enable CloudTrail in your account

### Post-Deployment

- [ ] Rotate gateway token regularly
- [ ] Review CloudTrail logs weekly
- [ ] Monitor Bedrock usage
- [ ] Set up cost alerts
- [ ] Enable CloudWatch alarms

### Ongoing

- [ ] Update Clawdbot monthly
- [ ] Review IAM policies quarterly
- [ ] Audit session logs
- [ ] Test disaster recovery
- [ ] Review security group rules

## Audit & Compliance

### CloudTrail Logs

All Bedrock API calls are logged:

```bash
# View recent Bedrock calls
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=InvokeModel \
  --max-items 50 \
  --region us-west-2
```

### SSM Session Logs

All SSM sessions are logged:

```bash
# View session logs
aws logs tail /aws/ssm/session-logs --follow --region us-west-2
```

### Cost Tracking

```bash
# View Bedrock costs
aws ce get-cost-and-usage \
  --time-period Start=2026-01-01,End=2026-01-31 \
  --granularity DAILY \
  --metrics BlendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Bedrock"]}}'
```

## Compliance Certifications

Amazon Bedrock supports:
- SOC 1, 2, 3
- ISO 27001, 27017, 27018, 27701
- PCI DSS
- HIPAA eligible
- FedRAMP Moderate (in supported regions)

## Incident Response

### Compromised Instance

```bash
# 1. Isolate instance
aws ec2 modify-instance-attribute \
  --instance-id $INSTANCE_ID \
  --groups sg-isolated

# 2. Create forensic snapshot
aws ec2 create-snapshot \
  --volume-id $VOLUME_ID \
  --description "Forensic snapshot"

# 3. Terminate instance
aws ec2 terminate-instances --instance-ids $INSTANCE_ID

# 4. Review CloudTrail logs
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue=$INSTANCE_ID
```

### Leaked Gateway Token

```bash
# 1. Connect to instance
aws ssm start-session --target $INSTANCE_ID

# 2. Regenerate token
sudo su - ubuntu
NEW_TOKEN=$(openssl rand -hex 24)

# 3. Update config
python3 << EOF
import json
with open('/home/ubuntu/.clawdbot/clawdbot.json') as f:
    config = json.load(f)
config['gateway']['auth']['token'] = '$NEW_TOKEN'
with open('/home/ubuntu/.clawdbot/clawdbot.json', 'w') as f:
    json.dump(config, f, indent=2)
EOF

# 4. Restart service
systemctl --user restart clawdbot-gateway
```

## Security Recommendations

### For Development

- Use `t4g.small` instance (Graviton, cost-effective)
- Use Nova 2 Lite model (cheapest)
- Disable VPC endpoints (save $22/month)
- Allow SSH from your IP only
- Enable sandbox mode

### For Production

- Use `t4g.medium` or larger (Graviton recommended)
- Use Nova Pro or Claude models (better performance)
- **Enable VPC endpoints** (required for security)
- **Disable SSH** (`AllowedSSHCIDR: 127.0.0.1/32`)
- Enable sandbox mode
- Set up CloudWatch alarms
- Enable AWS Config rules
- Regular security audits

### For Compliance (HIPAA, PCI-DSS)

- **Must use Graviton or x86 instances in compliant regions**
- **Must enable VPC endpoints**
- **Must disable SSH**
- Enable CloudTrail
- Enable VPC Flow Logs
- Encrypt EBS volumes (enabled by default)
- Use AWS Secrets Manager for tokens
- Regular penetration testing
- Document security controls

## References

- [AWS Security Best Practices](https://aws.amazon.com/architecture/security-identity-compliance/)
- [Bedrock Security](https://docs.aws.amazon.com/bedrock/latest/userguide/security.html)
- [SSM Security](https://docs.aws.amazon.com/systems-manager/latest/userguide/security.html)
