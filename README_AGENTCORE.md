# OpenClaw on AWS with Bedrock AgentCore Runtime

> Deploy [OpenClaw](https://github.com/openclaw/openclaw) (formerly Clawdbot) on AWS using Amazon Bedrock AgentCore Runtime for serverless agent execution. Enterprise-ready, secure, one-click deployment with Graviton ARM processors.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![AWS](https://img.shields.io/badge/AWS-Bedrock-orange.svg)](https://aws.amazon.com/bedrock/)
[![CloudFormation](https://img.shields.io/badge/IaC-CloudFormation-blue.svg)](https://aws.amazon.com/cloudformation/)

## What is This?

[OpenClaw](https://github.com/openclaw/openclaw) is an open-source personal AI assistant that connects to WhatsApp, Slack, Discord, and more. This project provides an **AWS-native deployment** using **Amazon Bedrock AgentCore Runtime** for serverless agent execution, eliminating the need to manage multiple API keys and providing auto-scaling capabilities.

## Why AgentCore Runtime?

| Traditional Deployment | AgentCore Runtime Deployment |
|----------------------|----------------------------|
| Agents run on EC2 (fixed capacity) | **Agents run serverless (auto-scales)** |
| Manual scaling required | **Automatic scaling based on demand** |
| Pay for EC2 even when idle | **Pay-per-use pricing** |
| Single instance bottleneck | **Distributed execution** |
| Manual container management | **Managed container runtime** |

### Key Advantages

**1. Serverless Agent Execution**
- **Auto-scaling**: AgentCore Runtime automatically scales based on demand
- **Pay-per-use**: Only pay when agents are executing
- **No idle costs**: No EC2 costs when agents aren't running
- **High availability**: Distributed execution across multiple microVMs

**2. Multi-Model Flexibility with Better Economics**
- **Nova 2 Lite default**: $0.30/$2.50 per 1M tokens vs Claude's $3/$15 (90% cheaper)
- **8 models available**: Switch between Nova, Claude, DeepSeek, Llama with one parameter
- **Smart routing**: Use Nova Lite for simple tasks, Claude Sonnet for complex reasoning
- **No vendor lock-in**: Change models without code changes or redeployment

**3. Flexible Instance Sizing with Graviton Advantage (Recommended)**
- **x86 and ARM support**: Choose t3/c5 (x86) or t4g/c7g (Graviton ARM)
- **Graviton ARM recommended**: 20-40% better price-performance than x86
- **Cost example**: t4g.medium ($24/mo) vs t3.medium ($30/mo) - same specs, 20% savings
- **Flexible sizing**: Scale from t4g.small ($12/mo) to c7g.xlarge ($108/mo) as needed
- **Energy efficient**: Graviton uses 70% less power than x86

**4. Enterprise Security & Compliance**
- **Zero API key management**: IAM roles replace multiple provider keys
- **Complete audit trail**: CloudTrail logs every Bedrock API call
- **Private networking**: VPC Endpoints keep traffic within AWS
- **Secure access**: SSM Session Manager, no public ports
- **Container isolation**: Each agent execution runs in isolated microVMs

**5. Cloud-Native Automation**
- **One-click deployment**: CloudFormation automates VPC, IAM, EC2, AgentCore, ECR setup
- **Infrastructure as Code**: Reproducible, version-controlled deployments
- **Multi-region support**: Deploy in any AWS region with identical configuration

## Key Benefits

- 🔐 **No API Key Management** - IAM roles handle authentication automatically
- 🤖 **Serverless Agents** - AgentCore Runtime auto-scales based on demand
- 💰 **Pay-Per-Use** - Only pay when agents execute, no idle costs
- 🏢 **Enterprise-Ready** - Full CloudTrail audit logs and compliance support
- 🚀 **One-Click Deploy** - CloudFormation automates everything
- 🔒 **Secure Access** - SSM Session Manager, no public ports exposed
- 📊 **Cost Visibility** - Native AWS cost tracking and optimization
- 🌐 **Auto-Scaling** - Handles traffic spikes automatically

## Quick Start

### ⚡ One-Click Deploy (Recommended - 10-15 minutes to ready!)

**Prerequisites**:
- AWS CLI configured (`aws configure`)
- Docker installed and running
- EC2 Key Pair created in your target region
- AWS account with permissions for CloudFormation, EC2, VPC, IAM, ECR, Bedrock AgentCore Runtime, and Bedrock model access

**Manual deployment**:

```bash
# 1. Build and push container
cd ../openclaw
docker build -f agent/Dockerfile -t openclaw-agentcore-agent:latest .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com
docker tag openclaw-agentcore-agent:latest ${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/openclaw-agentcore-agent:latest
docker push ${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/openclaw-agentcore-agent:latest

# 2. Deploy CloudFormation stack
cd ../OpenClaw-on-AWS-with-Bedrock
aws cloudformation create-stack \
  --stack-name openclaw-agentcore \
  --template-body file://clawdbot-bedrock-agentcore.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1 \
  --parameters \
    ParameterKey=KeyPairName,ParameterValue=your-key-pair \
    ParameterKey=InstanceType,ParameterValue=c7g.large \
    ParameterKey=OpenClawModel,ParameterValue=global.amazon.nova-2-lite-v1:0 \
    ParameterKey=EnableAgentCore,ParameterValue=true \
    ParameterKey=CreateVPCEndpoints,ParameterValue=true
```

### 🔌 Accessing OpenClaw

**Step 1: Port Forwarding**

Open a terminal and run (keep it open):

```bash
# Get instance ID
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-agentcore \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text \
  --region us-east-1)

# Start port forwarding
aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-east-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
```

**Step 2: Get Gateway Token**

```bash
aws ssm get-parameter \
  --name "/openclaw/openclaw-agentcore/gateway-token" \
  --region us-east-1 \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text
```

**Step 3: Open Gateway UI**

Open in your browser:
```
http://localhost:18789/?token=<GATEWAY_TOKEN>
```

## Architecture

```
┌─────────────┐
│   You       │ Send message via WhatsApp/Telegram/Discord
└──────┬──────┘
       │ (Internet)
       ▼
┌─────────────────────────────────────────────────────┐
│  AWS Cloud                                          │
│                                                     │
│  ┌──────────────┐         ┌──────────────┐        │
│  │ EC2 Gateway  │────────▶│ AgentCore    │        │
│  │  (openclaw)   │  IAM    │ Runtime      │        │
│  └──────────────┘  Auth   └──────┬───────┘        │
│         │                         │                  │
│         │ VPC Endpoints          │                  │
│         │ (Private Network)      │                  │
│         ▼                         ▼                  │
│  ┌──────────────┐         ┌──────────────┐        │
│  │ CloudTrail   │         │ Containerized│        │
│  │ (Audit Logs) │         │ OpenClaw Agent│        │
│  └──────────────┘         └──────┬───────┘        │
│                                   │                  │
│                                   ▼                  │
│                            ┌──────────────┐        │
│                            │   Bedrock    │        │
│                            │ (Nova/Claude)│        │
│                            └──────────────┘        │
└─────────────────────────────────────────────────────┘
       │
       ▼ (Internet)
┌──────────────┐
│   You        │ Receive response
└──────────────┘
```

### Component Details

**Gateway (EC2)**
- Handles messaging channels (WhatsApp, Telegram, Discord, Slack)
- Routes agent requests to AgentCore Runtime
- Web UI for configuration
- Runs on EC2 (Graviton ARM recommended)

**AgentCore Runtime (Serverless)**
- Serverless execution environment for OpenClaw agents
- Auto-scales based on demand
- Runs containerized agents in isolated microVMs
- Pay-per-use pricing

**Containerized OpenClaw Agent**
- Docker container with OpenClaw agent
- HTTP server exposing `/ping` and `/invocations` endpoints
- Uses Pi framework internally
- Runs inside AgentCore Runtime

**Amazon Bedrock Models**
- LLM models (Nova, Claude, DeepSeek, Llama)
- Invoked by the agent container
- IAM-based authentication (no API keys)

## How to Use OpenClaw

### Connect Messaging Platforms

**For detailed configuration guides, visit [OpenClaw Official Documentation](https://docs.molt.bot/).**

#### WhatsApp (Recommended)

1. **In Web UI**: Click "Channels" → "Add Channel" → "WhatsApp"
2. **Scan QR Code**: Use WhatsApp on your phone
   - Open WhatsApp → Settings → Linked Devices
   - Tap "Link a Device"
   - Scan the QR code displayed
3. **Verify**: Send a test message to your OpenClaw number

**Tip**: Use a dedicated phone number or enable `selfChatMode` for personal number.

📖 **Full guide**: https://docs.molt.bot/channels/whatsapp

#### Telegram

1. **Create Bot**: Message [@BotFather](https://t.me/botfather)
   ```
   /newbot
   Choose a name: My openclaw
   Choose a username: my_openclaw_bot
   ```
2. **Copy Token**: BotFather will give you a token like `123456:ABC-DEF...`
3. **Configure**: In Web UI, add Telegram channel with your bot token
4. **Test**: Send `/start` to your bot on Telegram

📖 **Full guide**: https://docs.molt.bot/channels/telegram

#### Discord

1. **Create Bot**: Visit [Discord Developer Portal](https://discord.com/developers/applications)
   - Click "New Application"
   - Go to "Bot" → "Add Bot"
   - Copy bot token
   - Enable intents: Message Content, Server Members
2. **Invite Bot**: Generate invite URL with permissions
   ```
   https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=8&scope=bot
   ```
3. **Configure**: In Web UI, add Discord channel with bot token
4. **Test**: Mention your bot in a Discord channel

📖 **Full guide**: https://docs.molt.bot/channels/discord

#### Slack

1. **Create App**: Visit [Slack API](https://api.slack.com/apps)
2. **Configure Bot**: Add bot token scopes (chat:write, channels:history)
3. **Install**: Install app to your workspace
4. **Configure**: In Web UI, add Slack channel
5. **Test**: Invite bot to a channel and mention it

📖 **Full guide**: https://docs.molt.bot/channels/slack

### Using OpenClaw

#### Send Messages

**Via WhatsApp/Telegram/Discord**: Just send a message!

```
You: What's the weather today?
OpenClaw: Let me check that for you...
```

**Via CLI**:
```bash
# SSH/SSM to instance
openclaw agent --message "Hello" --json
```

#### Chat Commands

Send these in any connected channel:

| Command | Description |
|---------|-------------|
| `/status` | Show session status (model, tokens, cost) |
| `/new` or `/reset` | Start a new conversation |
| `/think high` | Enable deep thinking mode |
| `/help` | Show available commands |

#### Voice Messages

**WhatsApp/Telegram**: Send voice notes directly - OpenClaw will transcribe and respond!

#### Browser Control

```
You: Open google.com and search for "AWS Bedrock"
OpenClaw: *Opens browser, performs search, returns results*
```

#### Scheduled Tasks

```
You: Remind me every day at 9am to check emails
OpenClaw: *Creates cron job*
```

### Advanced Features

#### Skills

```bash
# List available skills
openclaw skills list

# Install a skill
openclaw skills install voice-generation

# View installed skills
openclaw skills installed
```

#### Custom Prompts

Create `~/.openclaw/workspace/SOUL.md` on the instance:

```markdown
You are my personal assistant. Be concise and helpful.
Always respond in a friendly tone.
```

#### Multi-Agent Routing

Configure different agents for different channels in Web UI.

For detailed guides, visit [OpenClaw Documentation](https://docs.molt.bot/).

## Cost Breakdown

### Monthly Infrastructure Cost

| Service | Configuration | Monthly Cost |
|---------|--------------|--------------|
| EC2 (c7g.large, Graviton) | 2 vCPU, 4GB RAM | $30-40 |
| EBS (gp3) | 30GB | $2.40 |
| VPC Endpoints | 3 endpoints (optional) | $21.60 |
| Data Transfer | VPC endpoint processing | $5-10 |
| **Subtotal** | | **$33-58** |

### AgentCore Runtime Cost

| Component | Pricing |
|-----------|---------|
| **Runtime Execution** | Pay-per-use (per invocation) |
| **Container Storage** | Included in ECR costs |
| **Network Transfer** | Standard AWS data transfer rates |

**Note**: AgentCore Runtime is serverless - you only pay when agents execute. No idle costs!

### Bedrock Usage Cost

| Model | Input | Output |
|-------|-------|--------|
| Nova 2 Lite | $0.30/1M tokens | $2.50/1M tokens |
| Claude Sonnet 4.5 | $3/1M tokens | $15/1M tokens |
| Claude Haiku 4.5 | $1/1M tokens | $5/1M tokens |
| Nova Pro | $0.80/1M tokens | $3.20/1M tokens |
| DeepSeek R1 | $0.55/1M tokens | $2.19/1M tokens |

**Example**: 100 conversations/day with Nova 2 Lite ≈ $5-8/month

**Total**: ~$38-66/month for light usage (with AgentCore Runtime)

### Cost Optimization

- Use Nova 2 Lite instead of Claude: 90% cheaper
- Use Graviton instances: 20-40% cheaper than x86
- Disable VPC endpoints: Save $22/month (less secure)
- AgentCore Runtime: Pay-per-use, no idle costs
- Use Savings Plans: Save 30-40% on EC2

## Configuration

### Supported Models

```yaml
# In CloudFormation parameters
OpenClawModel:
  - global.amazon.nova-2-lite-v1:0              # Default, most cost-effective
  - global.anthropic.claude-sonnet-4-5-20250929-v1:0  # Most capable
  - us.amazon.nova-pro-v1:0                     # Balanced performance
  - global.anthropic.claude-opus-4-5-20251101-v1:0    # Advanced reasoning
  - global.anthropic.claude-haiku-4-5-20251001-v1:0   # Fast and efficient
  - global.anthropic.claude-sonnet-4-20250514-v1:0
  - us.deepseek.r1-v1:0                         # Open-source reasoning
  - us.meta.llama3-3-70b-instruct-v1:0          # Open-source alternative
```

**Model Selection Guide**:
- **Nova 2 Lite** (default): Most cost-effective, 90% cheaper than Claude, great for everyday tasks
- **Claude Sonnet 4.5**: Most capable for complex reasoning and coding
- **Nova Pro**: Best balance of performance and cost, supports multimodal
- **DeepSeek R1**: Cost-effective open-source reasoning model

### Instance Types

```yaml
# Linux Instances
InstanceType:
  # Graviton (ARM) - Recommended for best price-performance
  - t4g.small   # $12/month, 2GB RAM
  - t4g.medium  # $24/month, 4GB RAM
  - t4g.large   # $48/month, 8GB RAM
  - c7g.large   # $30-40/month, 2 vCPU, 4GB RAM (default)
  - c7g.xlarge  # $108/month, 8GB RAM, compute-optimized
  
  # x86 - Alternative for broader compatibility
  - t3.small    # $15/month, 2GB RAM
  - t3.medium   # $30/month, 4GB RAM
  - t3.large    # $60/month, 8GB RAM
  - c5.xlarge   # $122/month, 8GB RAM
```

**Graviton Benefits**: ARM-based processors offer 20-40% better price-performance than x86.

### VPC Endpoints

```yaml
CreateVPCEndpoints: true   # Recommended for production
  # Pros: Private network, more secure, lower latency
  # Cons: +$22/month

CreateVPCEndpoints: false  # For cost optimization
  # Pros: Save $22/month
  # Cons: Traffic goes through public internet
```

### Enable/Disable AgentCore

```yaml
EnableAgentCore: true   # Use AgentCore Runtime (serverless, recommended)
EnableAgentCore: false # Run agents locally on EC2
```

## Security Features

- **IAM roles**: Eliminate API key risks - no credentials to leak
- **CloudTrail**: Logs every Bedrock API call for compliance
- **VPC Endpoints**: Keep traffic private within AWS network
- **SSM Session Manager**: Secure access without public ports
- **Container isolation**: Each agent execution runs in isolated microVMs
- **No public IPs**: Gateway accessed only via SSM port forwarding

## Troubleshooting

### Port Forwarding Fails

- Ensure SSM Session Manager Plugin is installed
- Check EC2 instance is running
- Verify IAM role has SSM permissions

### Gateway Not Accessible

- Check Gateway service is running: `systemctl --user status openclaw-gateway.service`
- Verify port 18789 is listening: `ss -tlnp | grep 18789`
- Check Gateway logs: `journalctl --user -u openclaw-gateway.service -f`

### AgentCore Not Working

- Verify runtime ID in Gateway config: `cat ~/.openclaw/openclaw.json | jq .agentcore`
- Check IAM permissions for AgentCore Runtime
- View container logs in CloudWatch
- Verify container image is pushed to ECR

### Agent Response Not Received

- Check Gateway logs for errors
- Verify AgentCore Runtime is active
- Check container logs in CloudWatch
- Verify Bedrock model access is enabled

### Config File Permissions

If you see `EACCES: permission denied` errors:
```bash
# Fix permissions
chmod 644 ~/.openclaw/openclaw.json
chown ubuntu:ubuntu ~/.openclaw/openclaw.json
chmod 755 ~/.openclaw
chown ubuntu:ubuntu ~/.openclaw
```

### Missing Templates

If you see "Missing workspace template" errors:
```bash
# Copy templates from npm package
NODE_VERSION=$(node --version | cut -d v -f 2)
TEMPLATE_SRC="/home/ubuntu/.nvm/versions/node/v$NODE_VERSION/lib/node_modules/openclaw-agentcore/docs/reference/templates"
TEMPLATE_DEST="/home/ubuntu/docs/reference/templates"
mkdir -p "$TEMPLATE_DEST"
cp "$TEMPLATE_SRC"/*.md "$TEMPLATE_DEST/"
chown -R ubuntu:ubuntu "$TEMPLATE_DEST"
```

## Comparison with Original OpenClaw

### Local Deployment (Original)

**Setup**: Install on Mac Mini/PC, configure API keys, set up Tailscale VPN  
**Cost**: $20-30/month (API fees only, excludes $599 hardware + electricity)  
**Models**: Single provider (Anthropic/OpenAI), manual switching  
**Security**: API keys in config files, no audit logs  
**Availability**: Depends on your hardware and internet  
**Scalability**: Limited to single machine resources  
**Agent Execution**: Runs on local machine

### Cloud Deployment with AgentCore (This Project)

**Setup**: One-click CloudFormation deployment, 10-15 minutes to ready  
**Cost**: $38-66/month all-inclusive (Graviton + AgentCore + Bedrock)  
**Models**: 8 models via Bedrock, switch with one parameter  
**Security**: IAM roles (no keys), CloudTrail audit, VPC Endpoints  
**Availability**: 99.99% uptime with enterprise SLA  
**Scalability**: Elastic sizing (t4g.small to c7g.xlarge), orchestrate cloud resources  
**Agent Execution**: Serverless via AgentCore Runtime (auto-scales)

**Bottom line**: Cloud deployment with AgentCore provides enterprise-grade security, multi-model flexibility, unlimited scalability, and serverless agent execution. For teams, one cloud instance ($50/mo) serves 10+ people vs individual ChatGPT Plus subscriptions ($200/mo).

## What Gets Deployed

### Infrastructure

- **VPC** with public and private subnets
- **EC2 Instance** (Gateway) - Graviton recommended
- **ECR Repository** for agent container
- **AgentCore Runtime** (serverless)
- **IAM Roles** with proper permissions
- **VPC Endpoints** (optional, for private access)
- **SSM Parameters** for configuration storage

### Configuration

- Gateway automatically configured with AgentCore Runtime
- Bedrock model configured
- Messaging channels enabled
- SSM Session Manager for secure access
- Template directories initialized
- Workspace files created

## Cleanup

```bash
# Delete CloudFormation stack (removes all resources)
aws cloudformation delete-stack --stack-name openclaw-agentcore --region us-east-1

# Wait for deletion
aws cloudformation wait stack-delete-complete --stack-name openclaw-agentcore --region us-east-1
```

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## License

This deployment template is provided as-is. OpenClaw itself is licensed under its original license.

## Resources

- [OpenClaw Official Docs](https://docs.molt.bot/)
- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [Amazon Bedrock Docs](https://docs.aws.amazon.com/bedrock/)
- [AgentCore Runtime Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)
- [SSM Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)

## Support

- **OpenClaw Issues**: [GitHub Issues](https://github.com/openclaw/openclaw/issues)
- **AWS Bedrock**: [AWS re:Post](https://repost.aws/tags/bedrock)
- **This Project**: [GitHub Issues](https://github.com/Vivek0712/OpenClaw-on-AWS-with-Bedrock/issues)

---

**Deploy your personal AI assistant on AWS infrastructure you control with serverless agent execution.**

