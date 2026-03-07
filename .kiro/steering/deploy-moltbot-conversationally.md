---
inclusion: always
---

# Deploy OpenClaw Conversationally

Guide users through deploying OpenClaw on AWS via conversation. Ask questions, execute commands, explain what's happening.

## Activation

Respond to: "deploy OpenClaw", "setup OpenClaw", "install OpenClaw", "help me deploy", or similar.

## Step 1: Gather Requirements

Ask these 4 questions one at a time. Accept "default" to skip.

**Question 1 — AWS Region** (default: us-west-2):
1. us-west-2 (Oregon)
2. us-east-1 (Virginia)
3. eu-west-1 (Ireland)
4. ap-northeast-1 (Tokyo)

**Question 2 — AI Model** (default: Nova 2 Lite):
1. Nova 2 Lite — $0.30/$2.50 per 1M tokens, 90% cheaper than Claude
2. Claude Sonnet 4.5 — $3/$15, most capable for reasoning/coding
3. Nova Pro — $0.80/$3.20, balanced, multimodal
4. Kimi K2.5 — $0.60/$3.00, multimodal agentic, 262K context

Model IDs:
- Nova 2 Lite: `global.amazon.nova-2-lite-v1:0`
- Claude Sonnet 4.5: `global.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Nova Pro: `us.amazon.nova-pro-v1:0`
- Kimi K2.5: `moonshotai.kimi-k2.5`

Additional models (offer if asked): Claude Opus 4.6 (`global.anthropic.claude-opus-4-6-v1`), Claude Opus 4.5 (`global.anthropic.claude-opus-4-5-20251101-v1:0`), Claude Haiku 4.5 (`global.anthropic.claude-haiku-4-5-20251001-v1:0`), Claude Sonnet 4 (`global.anthropic.claude-sonnet-4-20250514-v1:0`), DeepSeek R1 (`us.deepseek.r1-v1:0`), Llama 3.3 70B (`us.meta.llama3-3-70b-instruct-v1:0`).

**Question 3 — Instance Type** (default: c7g.large):

Linux (Graviton ARM — recommended):
1. t4g.small — $12/mo, 2GB RAM (personal)
2. t4g.medium — $24/mo, 4GB RAM (small teams)
3. t4g.large — $48/mo, 8GB RAM (medium teams)
4. c7g.large — $52/mo, 4GB RAM (default)
5. c7g.xlarge — $108/mo, 8GB RAM (high performance)

Linux (x86): t3.medium ($30/mo), c5.xlarge ($122/mo)

macOS (separate template `clawdbot-bedrock-mac.yaml`): mac2.metal ($468/mo), mac2-m2.metal ($632/mo), mac2-m2pro.metal ($792/mo). Warn: 24-hour minimum allocation.

**Question 4 — VPC Endpoints** (default: true):
- Yes: traffic stays in AWS private network, +~$29/mo (5 endpoints)
- No: traffic goes through public internet, saves ~$29/mo

## Step 2: Confirm Configuration

Show summary with cost estimate before deploying:

```
Region: <REGION>
Model: <MODEL>
Instance: <INSTANCE_TYPE> ($X/mo)
VPC Endpoints: <YES/NO> ($0 or ~$29/mo)
S3 Files Skill: auto-installed (S3 bucket created, <$1/mo)
Docker Sandbox: enabled by default

Estimated monthly cost: $XX-XX
```

Ask: "Ready to deploy? (yes/no)"

## Step 3: Validate Prerequisites

**Check 1: AWS credentials**
```bash
aws sts get-caller-identity
```
If fails, guide user through `aws configure`.

**Check 2: EC2 Key Pair**
```bash
aws ec2 describe-key-pairs --region <REGION> --query 'KeyPairs[*].KeyName' --output table
```
- If key pairs exist: ask user to pick one, or create new
- If none exist: offer to create one
- To create:
```bash
KEY_NAME="openclaw-key-$(date +%Y%m%d-%H%M%S)"
aws ec2 create-key-pair --key-name $KEY_NAME --region <REGION> --query 'KeyMaterial' --output text > ~/.ssh/$KEY_NAME.pem
chmod 400 ~/.ssh/$KEY_NAME.pem
```
Note: key pair is optional (default "none"). SSM Session Manager is the primary access method.

**Check 3: SSM Session Manager Plugin**
```bash
session-manager-plugin --version
```
If not installed:
- macOS ARM: `curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac_arm64/session-manager-plugin.pkg" -o "session-manager-plugin.pkg" && sudo installer -pkg session-manager-plugin.pkg -target /`
- macOS x86: `curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac/session-manager-plugin.pkg" -o "session-manager-plugin.pkg" && sudo installer -pkg session-manager-plugin.pkg -target /`
- Linux: `curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb" && sudo dpkg -i session-manager-plugin.deb`

## Step 4: Deploy

```bash
STACK_NAME="openclaw-bedrock-$(date +%s)"

aws cloudformation create-stack \
  --stack-name $STACK_NAME \
  --template-body file://clawdbot-bedrock.yaml \
  --parameters \
    ParameterKey=KeyPairName,ParameterValue=<KEY_NAME_OR_none> \
    ParameterKey=OpenClawModel,ParameterValue=<MODEL_ID> \
    ParameterKey=InstanceType,ParameterValue=<INSTANCE_TYPE> \
    ParameterKey=CreateVPCEndpoints,ParameterValue=<true/false> \
  --capabilities CAPABILITY_IAM \
  --region <REGION>
```

For macOS deployments, use `clawdbot-bedrock-mac.yaml` instead.

**Monitor progress** (explain what's happening at each stage):
```bash
aws cloudformation describe-stack-events --stack-name $STACK_NAME --region <REGION> --max-items 5
```

Timeline:
- 0-2 min: VPC, subnets, route tables, internet gateway
- 2-4 min: VPC endpoints (if enabled), security groups
- 4-5 min: IAM role, instance profile
- 5-8 min: EC2 launch, Node.js, Docker, OpenClaw install, Bedrock config
- ~8 min: CloudFormation signal, stack complete

**Wait for completion**:
```bash
aws cloudformation wait stack-create-complete --stack-name $STACK_NAME --region <REGION>
```

## Step 5: Provide Access Information

Retrieve instance ID and token:
```bash
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text --region <REGION>)

TOKEN=$(aws ssm get-parameter \
  --name "/openclaw/$STACK_NAME/gateway-token" \
  --with-decryption \
  --query Parameter.Value \
  --output text --region <REGION>)
```

Give user these instructions:

1. Start port forwarding (keep terminal open):
```bash
aws ssm start-session \
  --target $INSTANCE_ID \
  --region <REGION> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
```

2. Open in browser: `http://localhost:18789/?token=<TOKEN>`

Then ask: "Which messaging platform do you want to connect?"

## Step 6: Platform Configuration

### WhatsApp
1. In Web UI: Channels → Add Channel → WhatsApp
2. On phone: WhatsApp → Settings → Linked Devices → Link a Device → Scan QR
3. Send a test message

Tip: use dedicated number or enable `selfChatMode` for personal number.
Docs: https://docs.openclaw.ai/channels/whatsapp

### Telegram
1. Message @BotFather on Telegram → `/newbot` → get token
2. In Web UI: Channels → Add Channel → Telegram → paste token → Save & Reload
3. Send `/start` to your bot
4. If pairing code appears, approve on EC2:
```bash
aws ssm start-session --target $INSTANCE_ID --region <REGION>
sudo su - ubuntu
openclaw pairing approve telegram <PAIRING_CODE>
```
Docs: https://docs.openclaw.ai/channels/telegram

### Discord
1. Create app at https://discord.com/developers/applications → Bot → copy token
2. Enable intents: Message Content, Server Members
3. Generate invite URL (OAuth2 → bot scope → Administrator) → invite to server
4. In Web UI: Channels → Add Channel → Discord → paste token → Save & Reload
5. @YourBot in a channel to test

Docs: https://docs.openclaw.ai/channels/discord

### Slack
1. Create app at https://api.slack.com/apps
2. Add scopes: chat:write, channels:history, groups:history, im:history
3. Install to workspace, copy Bot User OAuth Token (xoxb-)
4. In Web UI: Channels → Add Channel → Slack → paste token → Save & Reload
5. /invite @YourBot in a channel, then mention to test

Docs: https://docs.openclaw.ai/channels/slack

### Microsoft Teams
Complex setup requiring Azure Bot Channels Registration. Recommend WhatsApp or Telegram first.
Docs: https://docs.openclaw.ai/channels/msteams

## Step 7: Celebrate and Offer Next Steps

After platform is connected:
- Suggest trying: "What's the weather?", `/status`, `/help`
- Offer: connect another platform, change model, set up cost alerts, install skills
- Link to docs: https://docs.openclaw.ai/

## Error Handling

### Deployment fails
```bash
aws cloudformation describe-stack-events \
  --stack-name $STACK_NAME --region <REGION> \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`]'
```
Common causes:
- "Key pair not found" → create key pair or use `none`
- "Service not found" for bedrock-mantle → region doesn't support Mantle, redeploy without or in supported region
- "Insufficient permissions" → check IAM
- WaitCondition timeout → SSH in via SSM, check `/var/log/openclaw-setup.log`

Offer to clean up failed stack and retry:
```bash
aws cloudformation delete-stack --stack-name $STACK_NAME --region <REGION>
```

### Port forwarding fails
- Check SSM plugin installed: `session-manager-plugin --version`
- Check instance running: `aws ec2 describe-instances --instance-ids $INSTANCE_ID`
- Check gateway running (on EC2): `ss -tlnp | grep 18789`

### Platform connection fails
- WhatsApp: QR expired (refresh UI), or 5 device limit reached
- Telegram: bot token wrong, or pairing not approved
- Discord: intents not enabled, or bot not invited to server

## Cost Reference

| Instance | EC2/mo | +EBS | +VPC Endpoints | +Bedrock (est.) | Total |
|----------|--------|------|----------------|-----------------|-------|
| t4g.small | $12 | $2.40 | $29 | $5-8 | $19-51 |
| t4g.medium | $24 | $2.40 | $29 | $5-8 | $31-63 |
| c7g.large | $52 | $2.40 | $29 | $5-8 | $59-91 |
| c7g.xlarge | $108 | $2.40 | $29 | $5-8 | $115-147 |

Without VPC endpoints: subtract ~$29. Bedrock estimate assumes Nova 2 Lite, ~100 conversations/day.

## Guidelines

- Ask one question at a time
- Accept "default" for any question
- Show costs before deploying
- Explain what's happening during deployment
- Gateway token is retrieved from SSM Parameter Store — never expose it in logs or files
