# Troubleshooting Guide

## Quick Reference: Common Commands

### Connect to EC2 Instance

```bash
# Get instance ID from CloudFormation
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-bedrock \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text \
  --region us-west-2)

# Connect via SSM
aws ssm start-session --target $INSTANCE_ID --region us-west-2

# Switch to ubuntu user
sudo su - ubuntu
```

### openclaw Common Commands

```bash
# Check gateway status
openclaw gateway status

# Restart gateway service
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway.service

# Check service status
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status openclaw-gateway.service

# View gateway logs
XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u openclaw-gateway.service -n 100 -f

# Check configuration
cat ~/.openclaw/openclaw.json | python3 -m json.tool

# Get gateway token from SSM Parameter Store
bash ~/ssm-portforward.sh

# Test Bedrock connection
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
aws bedrock-runtime invoke-model \
  --model-id global.amazon.nova-2-lite-v1:0 \
  --body '{"messages":[{"role":"user","content":[{"text":"Hello"}]}],"inferenceConfig":{"maxTokens":100}}' \
  --region $REGION \
  /tmp/test.json && cat /tmp/test.json
```

### View Setup Logs

If deployment fails or openclaw isn't working, check the setup logs on the instance:

```bash
# Last 100 lines of setup log
sudo tail -100 /var/log/openclaw-setup.log

# Follow setup log in real time (if still running)
sudo tail -f /var/log/openclaw-setup.log

# Full cloud-init log
sudo cat /var/log/cloud-init-output.log
```

---

## Common Issues

### 1. Cannot Connect via SSM

**Symptom**: `TargetNotConnected` or timeout when running `aws ssm start-session`

**Causes**:
- SSM agent not running
- IAM role missing permissions
- Security group blocking traffic

**Solutions**:

```bash
# Check SSM agent status
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
  --region us-west-2

# Check IAM role
aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].IamInstanceProfile' \
  --region us-west-2

# Restart SSM agent (if you have SSH access)
ssh -i openclaw-key.pem ubuntu@<instance-ip>
sudo snap restart amazon-ssm-agent
```

### 2. Web UI Shows "Disconnected" or Token Mismatch

**Symptom**: Browser shows "Disconnected from gateway" or "unauthorized: gateway token mismatch"

**Causes**:
- Port forwarding not running
- Wrong token in URL
- Browser cached old token
- Gateway service not running

**Solutions**:

```bash
# 1. Verify port forwarding is running (on local computer)
ps aux | grep "start-session.*18789"

# 2. Restart port forwarding if needed
aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# 3. Get correct token (on EC2, via SSM Parameter Store)
bash ~/ssm-portforward.sh

# 4. Clear browser cache
# Chrome: Cmd+Shift+Delete (Mac) or Ctrl+Shift+Delete (Windows)
# Firefox: Cmd+Shift+P (Mac) or Ctrl+Shift+P (Windows) for private window

# 5. Check if Gateway is running (on EC2)
ps aux | grep clawdbot
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status clawdbot-gateway

# 6. Restart Gateway if needed
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart clawdbot-gateway
```

### 3. Model Returns Empty Response

**Symptom**: Send message in Web Chat, no response or empty response

**Causes**:
- Model not enabled in Bedrock
- Wrong model ID in configuration
- IAM permissions missing
- Model ID without CRIS prefix (us./global./etc.)

**Solutions**:

```bash
# 1. Check current model configuration
cat ~/.clawdbot/clawdbot.json | grep -A 5 '"id"'

# 2. Verify model is available
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
aws bedrock list-foundation-models \
  --by-provider Amazon \
  --region $REGION \
  --query 'modelSummaries[?modelId==`global.amazon.nova-2-lite-v1:0`].[modelId,modelLifecycle.status]' \
  --output table

# 3. Test model directly
aws bedrock-runtime invoke-model \
  --model-id global.amazon.nova-2-lite-v1:0 \
  --body '{"messages":[{"role":"user","content":[{"text":"Hello"}]}],"inferenceConfig":{"maxTokens":100}}' \
  --region $REGION \
  /tmp/test.json

cat /tmp/test.json

# 4. Check Gateway logs for errors
XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u clawdbot-gateway -n 50 | grep -i error

# 5. Verify model ID has CRIS prefix
# ✅ Good: global.amazon.nova-2-lite-v1:0
# ✅ Good: us.amazon.nova-pro-v1:0
# ❌ Bad: amazon.nova-2-lite-v1:0 (no prefix)
```

### 4. Bedrock API Errors

**Symptom**: `AccessDeniedException`, `ThrottlingException`, or `ModelNotFound`

**Solutions**:

```bash
# Check IAM permissions
aws sts get-caller-identity

# Test Bedrock access
aws bedrock list-foundation-models --region us-west-2

# Check if model is enabled
aws bedrock list-foundation-models \
  --by-provider Amazon \
  --region us-west-2 \
  --query 'modelSummaries[?contains(modelId, `nova`)].[modelId,modelLifecycle.status]' \
  --output table

# View Clawdbot logs
tail -f /tmp/clawdbot/clawdbot-$(date +%Y-%m-%d).log
```

### 5. High Costs / Unexpected Bills

**Symptom**: Bedrock costs higher than expected

**Solutions**:

```bash
# 1. Check current usage
aws ce get-cost-and-usage \
  --time-period Start=2026-01-01,End=2026-01-31 \
  --granularity DAILY \
  --metrics BlendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Bedrock"]}}' \
  --region us-west-2

# 2. Switch to cheaper model
# Edit ~/.clawdbot/clawdbot.json
# Change model to: global.amazon.nova-2-lite-v1:0 (90% cheaper than Claude)

# 3. Set up cost alerts
aws cloudwatch put-metric-alarm \
  --alarm-name bedrock-cost-alert \
  --alarm-description "Alert when Bedrock costs exceed $50" \
  --metric-name EstimatedCharges \
  --namespace AWS/Billing \
  --statistic Maximum \
  --period 86400 \
  --evaluation-periods 1 \
  --threshold 50 \
  --comparison-operator GreaterThanThreshold

# 4. Check for runaway loops
XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u clawdbot-gateway | grep -i "invoke"
```

### 6. Gateway Won't Start

**Symptom**: `systemctl --user status clawdbot-gateway` shows failed

**Solutions**:

```bash
# 1. Check logs
XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u clawdbot-gateway -n 100

# 2. Verify configuration is valid JSON
python3 -m json.tool ~/.clawdbot/clawdbot.json

# 3. Check if port is already in use
netstat -tlnp | grep 18789
# or
ss -tlnp | grep 18789

# 4. Kill existing process
pkill -f clawdbot

# 5. Restart service
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart clawdbot-gateway

# 6. If systemctl doesn't work, start manually
nohup clawdbot gateway start > /tmp/gateway.log 2>&1 &
tail -f /tmp/gateway.log
```

### 7. Port Forwarding Fails

**Symptom**: `Connection to destination port failed`

**Causes**:
- Gateway not running on EC2
- Port 18789 not listening
- SSM agent issues

**Solutions**:

```bash
# 1. Verify Gateway is running (on EC2)
ps aux | grep clawdbot
netstat -tlnp | grep 18789

# 2. Check Gateway logs
tail -f /tmp/clawdbot/clawdbot-$(date +%Y-%m-%d).log

# 3. Restart Gateway
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart clawdbot-gateway
sleep 5

# 4. Verify port is listening
ss -tlnp | grep 18789

# 5. Restart port forwarding (on local computer)
# Kill old session
pkill -f "start-session.*18789"

# Start new session
aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
```

### 8. Slow Response Times

**Symptom**: openclaw takes long time to respond

**Solutions**:

```bash
# 1. Check instance CPU/memory
top
htop

# 2. Upgrade instance type
# Update CloudFormation with larger instance (c7g.xlarge)

# 3. Switch to faster model
# Nova 2 Lite → Nova Pro (faster, slightly more expensive)
# Edit ~/.clawdbot/clawdbot.json

# 4. Check network latency
ping bedrock-runtime.$REGION.amazonaws.com

# 5. Enable VPC endpoints if not already
# Reduces latency by keeping traffic in AWS network
```

### 9. Cannot Install Channels (WhatsApp/Telegram)

**Symptom**: Error when adding channel in Web UI

**Solutions**:

```bash
# 1. Check openclaw version
clawdbot --version

# 2. Update to latest
npm update -g clawdbot
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart clawdbot-gateway

# 3. Check logs for specific error
XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u clawdbot-gateway -f

# 4. For WhatsApp, ensure you're using correct pairing mode
# Check ~/.clawdbot/clawdbot.json for "dmPolicy": "pairing"
```

### 10. AWS CLI "text contents could not be decoded" Error

**Symptom**: When running `aws cloudformation create-stack --template-body file://clawdbot-bedrock.yaml`, you get:
```
An error occurred (ValidationError) when calling the CreateStack operation: 
Template format error: YAML not well-formed. text contents could not be decoded
```

**Cause**: The YAML file is not encoded in UTF-8, or contains a BOM (Byte Order Mark) that AWS CLI cannot decode.

**Solutions**:

```bash
# Option 1: Convert file to UTF-8 (Linux/Mac)
iconv -f UTF-16 -t UTF-8 clawdbot-bedrock.yaml > clawdbot-bedrock-utf8.yaml
aws cloudformation create-stack --template-body file://clawdbot-bedrock-utf8.yaml ...

# Option 2: Remove BOM if present
sed '1s/^\xEF\xBB\xBF//' clawdbot-bedrock.yaml > clawdbot-bedrock-clean.yaml

# Option 3: Re-download from GitHub (ensures UTF-8)
curl -O https://raw.githubusercontent.com/aws-samples/sample-Moltbot-on-AWS-with-Bedrock/main/clawdbot-bedrock.yaml

# Option 4: Use text editor to save as UTF-8
# - VS Code: Click encoding in status bar → "Save with Encoding" → UTF-8
# - Notepad++: Encoding → Convert to UTF-8 (without BOM)
# - Sublime: File → Save with Encoding → UTF-8

# Verify encoding
file -I clawdbot-bedrock.yaml
# Should show: text/plain; charset=utf-8
```

**Prevention**: Always clone the repository using Git, which preserves file encoding:
```bash
git clone https://github.com/aws-samples/sample-Moltbot-on-AWS-with-Bedrock.git
```

### 11. CloudFormation Stack Fails

**Symptom**: Stack creation fails or rolls back

**Common Causes**:

**a) Key pair not found**
```bash
# Create key pair in the region
aws ec2 create-key-pair \
  --key-name openclaw-key \
  --region us-west-2 \
  --query 'KeyMaterial' \
  --output text > openclaw-key.pem
```

**b) Insufficient permissions**
```bash
# Verify you have CloudFormation permissions
aws cloudformation describe-stacks --region us-west-2
```

**c) Resource limits**
```bash
# Check VPC limits
aws ec2 describe-account-attributes \
  --attribute-names max-instances \
  --region us-west-2
```

**View failure reason**:
```bash
aws cloudformation describe-stack-events \
  --stack-name openclaw-bedrock \
  --region us-west-2 \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`]'
```

## Diagnostic Scripts

### Complete Health Check

```bash
#!/bin/bash
# Save as diagnose.sh and run on EC2 instance

echo "=== openclaw Health Check ==="
echo ""

echo "1. Service Status:"
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status clawdbot-gateway --no-pager
echo ""

echo "2. Process:"
ps aux | grep clawdbot | grep -v grep
echo ""

echo "3. Port Listening:"
ss -tlnp | grep 18789
echo ""

echo "4. Configuration:"
cat ~/.clawdbot/clawdbot.json | python3 -m json.tool | grep -A 10 '"gateway"'
echo ""

echo "5. Model Configuration:"
cat ~/.clawdbot/clawdbot.json | python3 -m json.tool | grep -A 5 '"models"'
echo ""

echo "6. Recent Logs:"
XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u clawdbot-gateway -n 20 --no-pager
echo ""

echo "7. Bedrock Test:"
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
aws bedrock-runtime invoke-model \
  --model-id global.amazon.nova-2-lite-v1:0 \
  --body '{"messages":[{"role":"user","content":[{"text":"test"}]}],"inferenceConfig":{"maxTokens":10}}' \
  --region $REGION \
  /tmp/test.json && echo "✓ Bedrock OK" || echo "✗ Bedrock Failed"
```

### Reset Configuration

```bash
#!/bin/bash
# Complete reset if configuration is corrupted

echo "=== Resetting openclaw Configuration ==="

# Stop service
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user stop clawdbot-gateway

# Backup old config
cp ~/.clawdbot/clawdbot.json ~/.clawdbot/clawdbot.json.backup.$(date +%s)

# Get current values (token from SSM Parameter Store)
IMDS_TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
STACK_NAME=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$INSTANCE_ID" "Name=key,Values=aws:cloudformation:stack-name" --query "Tags[0].Value" --output text --region $REGION)
TOKEN=$(aws ssm get-parameter --name "/openclaw/$STACK_NAME/gateway-token" --with-decryption --query Parameter.Value --output text --region $REGION)
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)

# Recreate config
cat > ~/.clawdbot/clawdbot.json << EOF
{
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "loopback",
    "controlUi": {
      "enabled": true,
      "allowInsecureAuth": true
    },
    "auth": {
      "mode": "token",
      "token": "$TOKEN"
    }
  },
  "models": {
    "providers": {
      "amazon-bedrock": {
        "baseUrl": "https://bedrock-runtime.$REGION.amazonaws.com",
        "api": "bedrock-converse-stream",
        "auth": "aws-sdk",
        "models": [
          {
            "id": "global.amazon.nova-2-lite-v1:0",
            "name": "Nova 2 Lite",
            "input": ["text", "image", "video"],
            "contextWindow": 1000000,
            "maxTokens": 8192
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "amazon-bedrock/global.amazon.nova-2-lite-v1:0"
      }
    }
  }
}
EOF

# Restart service
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart clawdbot-gateway

echo "✓ Configuration reset complete"
echo "Access URL: http://localhost:18789/?token=$TOKEN"
```

## Performance Optimization

### Upgrade to Faster Model

```bash
# Edit config
nano ~/.clawdbot/clawdbot.json

# Change model ID to:
# - us.amazon.nova-pro-v1:0 (faster, multimodal)
# - global.anthropic.claude-sonnet-4-5-20250929-v1:0 (fastest, most capable)

# Also update agents.defaults.model.primary

# Restart
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart clawdbot-gateway
```

### Upgrade Instance Type

```bash
# Update CloudFormation stack
aws cloudformation update-stack \
  --stack-name openclaw-bedrock \
  --use-previous-template \
  --parameters \
    ParameterKey=InstanceType,ParameterValue=c7g.xlarge \
    ParameterKey=KeyPairName,UsePreviousValue=true \
    ParameterKey=OpenClawModel,UsePreviousValue=true \
  --capabilities CAPABILITY_IAM \
  --region us-west-2
```

## Monitoring

### CloudWatch Metrics

```bash
# View EC2 CPU utilization
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=$INSTANCE_ID \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average \
  --region us-west-2
```

### Cost Monitoring

```bash
# View Bedrock costs (last 7 days)
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '7 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY \
  --metrics BlendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Bedrock"]}}' \
  --region us-west-2
```

## Getting Help

### Collect Diagnostic Information

```bash
# Run on EC2 instance
cat > /tmp/diagnostic-info.txt << 'EOF'
=== openclaw Diagnostic Information ===
Date: $(date)
Region: $(curl -s http://169.254.169.254/latest/meta-data/placement/region)
Instance ID: $(curl -s http://169.254.169.254/latest/meta-data/instance-id)

=== openclaw Version ===
$(clawdbot --version)

=== Service Status ===
$(XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status clawdbot-gateway --no-pager)

=== Configuration ===
$(cat ~/.clawdbot/clawdbot.json | python3 -m json.tool)

=== Recent Logs ===
$(XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u clawdbot-gateway -n 50 --no-pager)

=== Bedrock Models ===
$(aws bedrock list-foundation-models --by-provider Amazon --region $(curl -s http://169.254.169.254/latest/meta-data/placement/region) --output table)
EOF

cat /tmp/diagnostic-info.txt
```

### Support Resources

- **openclaw Issues**: https://github.com/openclaw/openclaw/issues
- **openclaw Discord**: https://discord.gg/openclaw
- **AWS Bedrock**: https://repost.aws/tags/bedrock
- **This Project**: https://github.com/aws-samples/sample-openclaw-on-AWS-with-Bedrock/issues

## Reference

### Useful File Locations

```
/home/ubuntu/.clawdbot/clawdbot.json          # Main configuration
# Gateway token: stored in SSM Parameter Store (not on disk)
/home/ubuntu/.clawdbot/setup_status.txt       # Setup completion status
/var/log/clawdbot-setup.log                   # Installation log
/tmp/clawdbot/clawdbot-YYYY-MM-DD.log        # Daily logs
```

### Environment Variables

```bash
export AWS_REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
export XDG_RUNTIME_DIR=/run/user/1000
```

### Quick Commands Reference

```bash
# Status
clawdbot status

# Logs
clawdbot logs -f

# Restart
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart clawdbot-gateway

# Config
cat ~/.clawdbot/clawdbot.json

# Token (from SSM Parameter Store)
bash ~/ssm-portforward.sh

# Test Bedrock
aws bedrock-runtime invoke-model \
  --model-id global.amazon.nova-2-lite-v1:0 \
  --body '{"messages":[{"role":"user","content":[{"text":"test"}]}],"inferenceConfig":{"maxTokens":10}}' \
  --region $(curl -s http://169.254.169.254/latest/meta-data/placement/region) \
  /tmp/test.json
```

For deployment instructions, see [DEPLOYMENT.md](DEPLOYMENT.md).
For security best practices, see [SECURITY.md](SECURITY.md).
