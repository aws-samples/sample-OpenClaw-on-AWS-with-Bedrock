# openclaw on AWS with Bedrock

> Deploy [openclaw](https://github.com/openclaw/openclaw) (formerly Clawdbot) on AWS using Amazon Bedrock instead of managing Anthropic/OpenAI/DeepSeek API keys. Enterprise-ready, secure, one-click deployment with Graviton ARM processors.

English | [简体中文](README_CN.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![AWS](https://img.shields.io/badge/AWS-Bedrock-orange.svg)](https://aws.amazon.com/bedrock/)
[![CloudFormation](https://img.shields.io/badge/IaC-CloudFormation-blue.svg)](https://aws.amazon.com/cloudformation/)

## What is This?

[openclaw](https://github.com/openclaw/openclaw) (formerly Clawdbot/moltbot) is an open-source personal AI assistant that connects to WhatsApp, Slack, Discord, and more. This project provides an **AWS-native deployment** using Amazon Bedrock's unified API, eliminating the need to manage multiple API keys from different providers.

## Why AWS Native?

| Original openclaw | This Project |
|-------------------|--------------|
| Multiple API keys (Anthropic/OpenAI/etc.) | **Amazon Bedrock unified API + IAM** |
| Single model, fixed cost | **8 models available, Nova 2 Lite (90% cheaper vs Anthropic)** |
| x86 hardware, fixed specs | **x86/ARM/Mac flexible, (Graviton Default, 20-40% savings)** |
| Tailscale VPN | **SSM Session Manager** |
| Manual setup | **CloudFormation (1-click)** |
| No audit logs | **CloudTrail (automatic)** |
| Public internet | **VPC Endpoints (private)** |

### Key Advantages

**1. Multi-Model Flexibility with Better Economics**
- **Nova Pro default**: $0.80/$3.20 per 1M tokens vs Claude's $3/$15 (73% cheaper)
- **8 models available**: Switch between Nova, Claude, DeepSeek, Llama with one parameter
- **Smart routing**: Use Nova Lite for simple tasks, Claude Sonnet for complex reasoning
- **No vendor lock-in**: Change models without code changes or redeployment

**2. Flexible Instance Sizing with Graviton Advantage (Recommended)**
- **x86, ARM, and Mac support**: Choose t3/c5 (x86), t4g/c7g (Graviton ARM), or mac2 (Apple Silicon)
- **Graviton ARM recommended**: 20-40% better price-performance than x86
- **Cost example**: t4g.medium ($24/mo) vs t3.medium ($30/mo) - same specs, 20% savings
- **Mac for Apple development**: mac2.metal ($468/mo) for iOS/macOS workflows
- **Flexible sizing**: Scale from t4g.small ($12/mo) to c7g.xlarge ($108/mo) as needed
- **Energy efficient**: Graviton uses 70% less power than x86

**3. Enterprise Security & Compliance**
- **Zero API key management**: IAM roles replace multiple provider keys
- **Complete audit trail**: CloudTrail logs every Bedrock API call
- **Private networking**: VPC Endpoints keep traffic within AWS
- **Secure access**: SSM Session Manager, no public ports

**4. Cloud-Native Automation**
- **One-click deployment**: CloudFormation automates VPC, IAM, EC2, Bedrock setup
- **Infrastructure as Code**: Reproducible, version-controlled deployments
- **Multi-region support**: Deploy in 4 regions with identical configuration

## Key Benefits

- 🔐 **No API Key Management** - IAM roles handle authentication automatically
- 🤖 **Multi-Model Support** - Switch between Claude, Nova, DeepSeek with one parameter
- 🏢 **Enterprise-Ready** - Full CloudTrail audit logs and compliance support
- 🚀 **One-Click Deploy** - CloudFormation automates everything in ~8 minutes
- 🔒 **Secure Access** - SSM Session Manager, no public ports exposed
- 💰 **Cost Visibility** - Native AWS cost tracking and optimization

## Deployment Options

Choose the deployment that fits your needs:

### 🚀 Serverless Deployment (AgentCore Runtime) - Recommended for Production

**[→ Deploy with AgentCore Runtime](README_AGENTCORE.md)**

> ⚠️ **Work in Progress**: AgentCore Runtime requires a custom Docker image that is not yet provided. You'll need to build it yourself before deploying. See [README_AGENTCORE.md](README_AGENTCORE.md) for details.

Best for variable workloads and cost optimization:

| Feature | AgentCore Runtime | Traditional EC2 |
|---------|-------------------|-----------------|
| **Scaling** | ✅ Auto-scales with demand | ❌ Fixed capacity |
| **Cost Model** | ✅ Pay-per-use (no idle costs) | ❌ Pay 24/7 even when idle |
| **Availability** | ✅ Distributed across microVMs | ⚠️ Single instance |
| **Container Isolation** | ✅ Isolated microVMs per execution | ⚠️ Shared instance |
| **Management** | ✅ Fully managed runtime | ⚠️ Manual scaling |

**Cost Example:**
- Traditional EC2: $50/month (running 24/7)
- AgentCore: $15-30/month (pay only when agents execute)
- **Savings: 40-70% for typical usage**

**[→ Full AgentCore documentation and deployment guide](README_AGENTCORE.md)**

---

### 💻 Standard Deployment (EC2)

Traditional deployment with OpenClaw running on dedicated EC2 instances:
- **Linux (Graviton/x86)**: Best price-performance with Graviton ARM
- **macOS (Apple Silicon)**: For iOS/macOS development workflows

Choose this if you need:
- Predictable fixed costs
- Full control over the instance
- 24/7 availability regardless of usage

## Quick Start

### ⚡ One-Click Deploy (Recommended - 8 minutes to ready!)

> **Why CloudFormation?** Fully automated setup - no manual configuration needed. Just click, wait 8 minutes, and get your ready-to-use URL!

**Just 3 steps**:
1. ✅ Click "Launch Stack" button below
2. ✅ Select your EC2 key pair in the form
3. ✅ Wait ~8 minutes → Check "Outputs" tab → Copy URL → Start using!

**What happens automatically**:
- Creates VPC, subnets, security groups
- Launches EC2 instance
- Installs Node.js, openclaw
- Configures Bedrock integration
- Generates secure gateway token
- Outputs ready-to-use URL with token

Click to deploy:

**Linux (Graviton/x86) - Recommended**

| Region | Launch Stack |
|--------|--------------|
| **US West (Oregon)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **US East (N. Virginia)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **EU (Ireland)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **Asia Pacific (Tokyo)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |

**macOS (EC2 Mac) - For Apple Development**

| Region | Launch Stack | Monthly Cost |
|--------|--------------|--------------|
| **US West (Oregon)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) | $468-792 |
| **US East (N. Virginia)** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) | $468-792 |

> **Mac instances**: 24-hour minimum allocation, best for iOS/macOS development teams. [Learn more →](#macos-deployment)

> **Note**: Using Global CRIS profiles - works in 30+ regions worldwide. Deploy in any region, requests auto-route to optimal locations.

**🇨🇳 AWS China Region (Beijing/Ningxia)**

For AWS China deployments using SiliconFlow (DeepSeek, Qwen, GLM) instead of Bedrock. Download [`clawdbot-china.yaml`](clawdbot-china.yaml) and upload via CloudFormation console.

**[→ Full China deployment guide (中国区部署指南)](DEPLOYMENT_CN.md)**

**After deployment (~8 minutes), check CloudFormation Outputs tab**:

---

### 🎯 Want a More Fun Way to Deploy?

**Chat with Kiro AI!** Kiro will guide you through deployment and help configure your phone—no commands to remember.

### 👉 **[Try Kiro Deployment →](QUICK_START_KIRO.md)** 👈

---

1. **Install SSM Plugin**: Click link in `Step1InstallSSMPlugin` (one-time setup)
2. **Port Forwarding**: Copy command from `Step2PortForwarding`, run on your computer (keep terminal open)
3. **Open URL**: Copy URL from `Step3AccessURL`, open in browser (token included!)
4. **Start Chatting**: Connect WhatsApp/Telegram/Discord in Web UI


![CloudFormation Outputs](images/20260128-105244.jpeg)
![Clawdbot Web UI](images/20260128-105059.jpg)

> **Before deploying**:
> - Before deploying, enable Bedrock models in Bedrock Console
> - Create an EC2 key pair in your target region
> - Lambda will automatically validate Bedrock access during deployment

### Alternative: CLI Deploy

```bash
aws cloudformation create-stack \
  --stack-name openclaw-bedrock \
  --template-body file://clawdbot-bedrock.yaml \
  --parameters ParameterKey=KeyPairName,ParameterValue=your-keypair \
  --capabilities CAPABILITY_IAM \
  --region us-west-2

aws cloudformation wait stack-create-complete \
  --stack-name openclaw-bedrock \
  --region us-west-2
```

### Access openclaw

```bash
# Get instance ID from CloudFormation Outputs, or:
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-bedrock \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text)

# Start port forwarding (keep this terminal open)
aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# Open in browser (token is shown in CloudFormation Outputs > Step3AccessURL)
http://localhost:18789/?token=<your-token>
```

## How to Use openclaw

### Connect Messaging Platforms

**For detailed configuration guides, visit [openclaw Official Documentation](https://docs.openclaw.ai/).**

#### WhatsApp (Recommended)

1. **In Web UI**: Click "Channels" → "Add Channel" → "WhatsApp"
2. **Scan QR Code**: Use WhatsApp on your phone
   - Open WhatsApp → Settings → Linked Devices
   - Tap "Link a Device"
   - Scan the QR code displayed
3. **Verify**: Send a test message to your openclaw number

**Tip**: Use a dedicated phone number or enable `selfChatMode` for personal number.

📖 **Full guide**: https://docs.openclaw.ai/channels/whatsapp

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

📖 **Full guide**: https://docs.openclaw.ai/channels/telegram

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

📖 **Full guide**: https://docs.openclaw.ai/channels/discord

#### Slack

1. **Create App**: Visit [Slack API](https://api.slack.com/apps)
2. **Configure Bot**: Add bot token scopes (chat:write, channels:history)
3. **Install**: Install app to your workspace
4. **Configure**: In Web UI, add Slack channel
5. **Test**: Invite bot to a channel and mention it

📖 **Full guide**: https://docs.openclaw.ai/channels/slack

#### Microsoft Teams

**Microsoft Teams integration requires Azure Bot setup and is more complex.**

📖 **Full guide**: https://docs.openclaw.ai/channels/msteams

**Note**: This integration requires additional configuration beyond the scope of this quick start.

#### Lark / Feishu (飞书) - Community Plugin

openclaw doesn't have official Lark/Feishu support, but the community has created a plugin:

**Community Plugin**: https://www.npmjs.com/package/openclaw-feishu

Install on your EC2 instance to forward messages between Feishu and openclaw via WebSocket. No public IP or domain required.

#### Community Skills

Looking for optional extensions? See [Community Skills](COMMUNITY_SKILLS.md) for skills like [S3 backup/restore](https://github.com/genedragon/openclaw-aws-backup-skill), [Kiro CLI integration](skills/openclaw-kirocli-skill/), and [S3 file sharing](skills/s3-files-skill/).

### Using openclaw

#### Send Messages

**Via WhatsApp/Telegram/Discord**: Just send a message!

```
You: What's the weather today?
openclaw: Let me check that for you...
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

**WhatsApp/Telegram**: Send voice notes directly - openclaw will transcribe and respond!

#### Browser Control

```
You: Open google.com and search for "AWS Bedrock"
openclaw: *Opens browser, performs search, returns results*
```

#### Scheduled Tasks

```
You: Remind me every day at 9am to check emails
openclaw: *Creates cron job*
```

### Advanced Features

#### Custom Prompts

Create `~/openclaw/system.md` on the instance:

```markdown
You are my personal assistant. Be concise and helpful.
Always respond in a friendly tone.
```

#### Multi-Agent Routing

Configure different agents for different channels in Web UI.

For detailed guides, visit [openclaw Documentation](https://docs.openclaw.ai/).

## Architecture

```
Your Phone/Computer → WhatsApp/Telegram → EC2 (openclaw) → Bedrock (Claude)
                                              ↓
                                         Your Data Stays Here
                                         (Secure, Private, Audited)
```

## Architecture

```
┌─────────────┐
│   You       │ Send message via WhatsApp/Telegram
└──────┬──────┘
       │ (Internet)
       ▼
┌─────────────────────────────────────────────────────┐
│  AWS Cloud                                          │
│                                                     │
│  ┌──────────────┐         ┌──────────────┐        │
│  │ EC2 Instance │────────▶│   Bedrock    │        │
│  │  (openclaw)   │  IAM    │ (Nova/Claude)│        │
│  └──────────────┘  Auth   └──────────────┘        │
│         │                        │                  │
│         │ VPC Endpoints          │                  │
│         │ (Private Network)      │                  │
│         ▼                        ▼                  │
│  ┌──────────────┐         ┌──────────────┐        │
│  │ CloudTrail   │         │ Cost Explorer│        │
│  │ (Audit Logs) │         │ (Billing)    │        │
│  └──────────────┘         └──────────────┘        │
└─────────────────────────────────────────────────────┘
       │
       ▼ (Internet)
┌──────────────┐
│   You        │ Receive response
└──────────────┘

Cost: ~$0.01/request | Time: 2-5s | Security: Private network
```

**Key Components**:
- **EC2 Instance**: Runs openclaw gateway (~500MB-1GB RAM)
- **IAM Role**: Authenticates with Bedrock (no API keys)
- **SSM Session Manager**: Secure access without public ports
- **VPC Endpoints**: Private network access to Bedrock

## Cost Breakdown

### Monthly Infrastructure Cost

| Service | Configuration | Monthly Cost |
|---------|--------------|--------------|
| EC2 (c7g.large, Graviton) | 2 vCPU, 4GB RAM | $52.60 |
| EBS (gp3) | 30GB | $2.40 |
| VPC Endpoints | 3 endpoints | $21.60 |
| Data Transfer | VPC endpoint processing | $5-10 |
| **Subtotal** | | **$76-81** |

### Bedrock Usage Cost

| Model | Input | Output |
|-------|-------|--------|
| Nova 2 Lite | $0.30/1M tokens | $2.50/1M tokens |
| Claude Sonnet 4.5 | $3/1M tokens | $15/1M tokens |
| Claude Haiku 4.5 | $1/1M tokens | $5/1M tokens |
| Nova Pro | $0.80/1M tokens | $3.20/1M tokens |
| DeepSeek R1 | $0.55/1M tokens | $2.19/1M tokens |
| Kimi K2.5 | $0.60/1M tokens | $3.00/1M tokens |

**Example**: 100 conversations/day with Nova 2 Lite ≈ $5-8/month

**Total**: ~$58-66/month for light usage

### Cost Optimization

- Use Nova 2 Lite instead of Claude: 90% cheaper
- Use Graviton instances: 20-40% cheaper than x86
- Disable VPC endpoints: Save $22/month (less secure)
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
  - us.deepseek.r1-v1:0                         # Open-source reasoning
  - us.meta.llama3-3-70b-instruct-v1:0          # Open-source alternative
  - moonshotai.kimi-k2.5                        # Multimodal agentic, 262K context
```

**Model Selection Guide**:
- **Nova 2 Lite** (default): Most cost-effective, 90% cheaper than Claude, great for everyday tasks
- **Claude Sonnet 4.5**: Most capable for complex reasoning and coding
- **Nova Pro**: Best balance of performance and cost, supports multimodal
- **DeepSeek R1**: Cost-effective open-source reasoning model
- **Kimi K2.5**: Multimodal agentic model (text + vision), 262K context window, $0.60/$3.00 per 1M tokens

### Instance Types

```yaml
# Linux Instances
InstanceType:
  # Graviton (ARM) - Recommended for best price-performance
  - t4g.small   # $12/month, 2GB RAM
  - t4g.medium  # $24/month, 4GB RAM (default)
  - t4g.large   # $48/month, 8GB RAM
  - c7g.xlarge  # $108/month, 8GB RAM, compute-optimized
  
  # x86 - Alternative for broader compatibility
  - t3.small    # $15/month, 2GB RAM
  - t3.medium   # $30/month, 4GB RAM
  - c5.xlarge   # $122/month, 8GB RAM

# Mac Instances (separate template: clawdbot-bedrock-mac.yaml)
MacInstanceType:
  - mac2.metal        # $468/month, M1, 16GB RAM
  - mac2-m2.metal     # $632/month, M2, 24GB RAM
  - mac2-m2pro.metal  # $792/month, M2 Pro, 32GB RAM
```

**Graviton Benefits**: ARM-based processors offer 20-40% better price-performance than x86.

**Mac Use Cases**: iOS/macOS development, Xcode automation, Apple ecosystem integration. [Learn more →](#macos-deployment)

### VPC Endpoints

```yaml
CreateVPCEndpoints: true   # Recommended for production
  # Pros: Private network, more secure, lower latency
  # Cons: +$22/month

CreateVPCEndpoints: false  # For cost optimization
  # Pros: Save $22/month
  # Cons: Traffic goes through public internet
```

### Sandbox Mode

```yaml
EnableSandbox: true   # Default: Docker installed
  # Provides isolated execution for code in group chats
  # Recommended for shared/team use

EnableSandbox: false  # Skip Docker installation
  # Faster deployment (~2 min saved)
  # Use for personal/single-user deployments
```

### Data Protection

```yaml
EnableDataProtection: false  # Default: data deleted with stack
  # When true:
  # - Data volume retained on stack delete
  # - Preserves chat history, configs, credentials
  # - Allows instance replacement without data loss
  
  # When false:
  # - Data volume deleted with stack (clean removal)
```

**Storage layout:**
- Root volume (30GB): OS, applications, and OpenClaw data (default)
- Data volume (30GB): `/data` → symlinked to `~/.openclaw` (when EnableDataProtection=true)
## Security Features

IAM roles eliminate API key risks. CloudTrail logs every API call. VPC Endpoints keep traffic private. Docker sandbox isolates execution.

**Full details**: [SECURITY.md](SECURITY.md)

## Troubleshooting

Common issues: SSM connection, Web UI token mismatch, model configuration, port forwarding.

**Full guide**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## SSH-like CLI Access via SSM

If you want to access the EC2 instance directly via command line (similar to SSH), use SSM Session Manager:

```bash
# 1. Start an interactive session (replace with your instance ID and region)
aws ssm start-session --target i-xxxxxxxxxxxxxxxxx --region us-east-1

# 2. Switch to the ubuntu user
sudo su - ubuntu

# 3. Now you can run openclaw commands directly
openclaw --version
openclaw gateway status
```

> **Tip**: Your instance ID is shown in the CloudFormation Outputs tab under `InstanceId`.
> Make sure you have the [SSM Session Manager Plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) installed locally.

## Comparison with Original openclaw

### Deployment Options

| Option | Monthly Cost | Best For | Deploy |
|--------|--------------|----------|--------|
| **Linux (Graviton)** | $39-58 | Most users, best value | [Launch →](#quick-start) |
| **macOS (M1/M2)** | $468-792 | iOS/macOS development | [Launch →](#macos-deployment) |

### Local Deployment (Original)

**Setup**: Install on Mac Mini/PC, configure API keys, set up Tailscale VPN
**Cost**: $20-30/month (API fees only, excludes $599 hardware + electricity)
**Models**: Single provider (Anthropic/OpenAI), manual switching
**Security**: API keys in config files, no audit logs
**Availability**: Depends on your hardware and internet
**Scalability**: Limited to single machine resources

### Cloud Deployment (This Project)

**Setup**: One-click CloudFormation deployment, 8 minutes to ready
**Cost**: $36-50/month all-inclusive (Graviton + Nova Pro + VPC)
**Models**: 8 models via Bedrock, switch with one parameter
**Security**: IAM roles (no keys), CloudTrail audit, VPC Endpoints
**Availability**: 99.99% uptime with enterprise SLA
**Scalability**: Elastic sizing (t4g.small to c7g.xlarge), orchestrate cloud resources

**Bottom line**: Cloud deployment costs similar but delivers enterprise-grade security, multi-model flexibility, and unlimited scalability. For teams, one cloud instance ($50/mo) serves 10+ people vs individual ChatGPT Plus subscriptions ($200/mo).

---

## macOS Deployment

**For iOS/macOS development teams only.** Mac instances cost $468-792/month with 24-hour minimum allocation.

### When to Use

- ✅ iOS/macOS app development and CI/CD
- ✅ Xcode build automation
- ✅ Apple ecosystem integration (iCloud, APNs)
- ❌ General openclaw use (Linux is 12x cheaper)

### Mac Instance Options

| Type | Chip | RAM | Cost/Month | Best For |
|------|------|-----|------------|----------|
| mac2.metal | M1 | 16GB | $468 | Standard builds |
| mac2-m2.metal | M2 | 24GB | $632 | Latest Silicon |
| mac2-m2pro.metal | M2 Pro | 32GB | $792 | High performance |

### Deploy Mac Version

Click "Launch Stack" above in the macOS section. **Important**: You must specify an Availability Zone that supports Mac instances (check AWS Console first).

**Access**: Same as Linux (SSM Session Manager + port forwarding)

---

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## License

This deployment template is provided as-is. openclaw itself is licensed under its original license.

## Resources

- [openclaw Official Docs](https://docs.openclaw.ai/)
- [openclaw GitHub](https://github.com/openclaw/openclaw)
- [Amazon Bedrock Docs](https://docs.aws.amazon.com/bedrock/)
- [SSM Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)

## Support

- **openclaw Issues**: [GitHub Issues](https://github.com/openclaw/openclaw/issues)
- **AWS Bedrock**: [AWS re:Post](https://repost.aws/tags/bedrock)
- **This Project**: [GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)

---

**Built by builder + Kiro** 🦞

*90% of this project's code was generated through conversations with Kiro AI.*

Deploy your personal AI assistant on AWS infrastructure you control.
