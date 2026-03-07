# Quick Start with Kiro AI

> Deploy OpenClaw on AWS by chatting with Kiro — no commands to remember.

## Prerequisites

- AWS account with credentials configured (`aws configure`)
- [Kiro](https://kiro.dev/) installed
- Bedrock models enabled in [Bedrock Console](https://console.aws.amazon.com/bedrock/)

## How to Use

### Step 1: Clone and Open

```bash
git clone https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock.git
```

Open the `sample-OpenClaw-on-AWS-with-Bedrock` folder as a workspace in Kiro (File → Open Folder). The steering file at `.kiro/steering/` loads automatically.

### Step 2: Start Chatting

In the Kiro chat panel, say:

```
"Help me deploy OpenClaw on AWS"
```

### Step 3: Answer 4 Questions

Kiro asks about:

1. **AWS Region** (default: us-west-2)
2. **AI Model** (default: Nova 2 Lite)
3. **Instance Size** (default: c7g.large)
4. **VPC Endpoints** (default: yes)

Say "default" to skip all questions and deploy with recommended settings.

### Step 4: Wait ~8 Minutes

Kiro will:
- Validate AWS credentials
- Create EC2 key pair if needed
- Deploy CloudFormation stack
- Monitor progress
- Retrieve your access token from SSM Parameter Store

### Step 5: Connect a Messaging Platform

Kiro asks which platform to connect and walks you through setup:

1. WhatsApp — scan QR code
2. Telegram — create bot via @BotFather
3. Discord — create app in Developer Portal
4. Slack — create app at api.slack.com
5. Microsoft Teams — requires Azure Bot setup

---

## Example Conversation

```
You: "Help me deploy OpenClaw"

Kiro: "Which AWS region? (1-4 or 'default')"
You: "default"

Kiro: "Which AI model? (1-4 or 'default')"
You: "default"

Kiro: "Instance size? (1-4 or 'default')"
You: "default"

Kiro: "VPC endpoints? (yes/no or 'default')"
You: "default"

Kiro: "Configuration:
       Region: us-west-2, Model: Nova 2 Lite, Instance: c7g.large
       Estimated cost: ~$55-65/month. Proceed?"
You: "yes"

Kiro: "🚀 Deploying... ✅ Complete!
       Run this to get your token:
       aws ssm get-parameter --name /openclaw/.../gateway-token --with-decryption ...
       Which platform to connect? (1-5)"
You: "1"

Kiro: "📱 WhatsApp: Channels → Add → WhatsApp → Scan QR from phone. Done?"
You: "yes"

Kiro: "🎉 Your OpenClaw is live on WhatsApp!"
```

---

## Without Kiro

### Option 1: One-Click CloudFormation

Click "Launch Stack" in the [main README](README.md#quick-start) for your region.

### Option 2: CLI

```bash
aws cloudformation create-stack \
  --stack-name openclaw-bedrock \
  --template-body file://clawdbot-bedrock.yaml \
  --parameters ParameterKey=KeyPairName,ParameterValue=your-key \
  --capabilities CAPABILITY_IAM \
  --region us-west-2
```

---

## Troubleshooting

**Kiro doesn't respond to "deploy OpenClaw"**:
- Make sure you opened the folder as a workspace (File → Open Folder)
- Check that `.kiro/steering/` exists in the file tree
- Try: "Kiro, I need help deploying OpenClaw on AWS"

**AWS credentials not configured**:
Kiro will detect this and guide you through `aws configure`.

**Deployment failed**:
Kiro checks CloudFormation events, explains the error, and offers to retry.

---

## Learn More

- [Kiro](https://kiro.dev/) · [Kiro Docs](https://kiro.dev/docs/)
- [OpenClaw Docs](https://docs.openclaw.ai/)
- [Full Deployment Guide](DEPLOYMENT.md) · [Troubleshooting](TROUBLESHOOTING.md)
