# OpenClaw Single-User Deployment Guide

> Simple, personal AI assistant on AWS. Connect to Telegram, WhatsApp, Discord, Slack. Powered by Amazon Bedrock. One-click deploy.

[English](#english) | [简体中文](#简体中文)

---

## English

### Quick Start (8 minutes)

#### Prerequisites

1. **AWS Account** with Bedrock access
2. **Enable Bedrock Models** in [Bedrock Console](https://console.aws.amazon.com/bedrock/)
   - Recommended: Claude Sonnet 4.5, Nova models
3. **EC2 Key Pair** in your target region

#### One-Click Deploy

Click "Launch Stack" for your region:

| Region | Launch |
|--------|--------|
| **US West (Oregon)** | [![Launch](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **US East (Virginia)** | [![Launch](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **Asia Pacific (Tokyo)** | [![Launch](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |

**Parameters to configure:**
- `KeyName`: Select your EC2 key pair (optional, for SSH access)
- `BedrockModelId`: Default `anthropic.claude-sonnet-4-5-v2:0`
- `InstanceType`: Default `t4g.large` (Graviton ARM64, cheaper)

#### Access Your OpenClaw

After deployment (8 minutes), find these in **CloudFormation Outputs**:

1. **Install SSM Plugin** (one-time)
   ```bash
   # macOS
   brew install --cask session-manager-plugin
   
   # Linux
   curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_arm64/session-manager-plugin.deb" -o "session-manager-plugin.deb"
   sudo dpkg -i session-manager-plugin.deb
   ```

2. **Port Forward** (keep terminal open)
   ```bash
   aws ssm start-session \
     --target i-xxxxxxxxxxxxxxxxx \
     --region ap-northeast-1 \
     --document-name AWS-StartPortForwardingSession \
     --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
   ```

3. **Open Web UI**
   ```
   http://localhost:18789/?token=YOUR_TOKEN_FROM_OUTPUTS
   ```

4. **Connect Messaging Apps**
   - Say: "Connect to Telegram"
   - OpenClaw guides you step-by-step!

### Architecture

```
You (Telegram/WhatsApp/Discord/Slack)
  ↓
OpenClaw Gateway (EC2)
  ↓
Amazon Bedrock (Claude/Nova models)
  ↓
Response back to you
```

**Key Features:**
- ✅ **No API Keys** - Uses AWS IAM for Bedrock
- ✅ **Private Network** - VPC Endpoints, no internet exposure
- ✅ **Secure Access** - SSM Session Manager, no SSH ports open
- ✅ **Cost Efficient** - Graviton ARM64 instances (20-40% cheaper)
- ✅ **Auditable** - CloudTrail logs every API call

### What You Get

**Infrastructure:**
- EC2 instance (t4g.large, Graviton ARM64)
- VPC with private subnet
- 6 VPC Endpoints (Bedrock, SSM, EC2, GuardDuty)
- 30GB EBS volume
- Security group (minimal ingress)

**Software:**
- OpenClaw (latest stable)
- Node.js 22
- Pre-installed channel plugins:
  - Telegram
  - WhatsApp
  - Discord
  - Slack
  - And more!

**AWS Services:**
- Amazon Bedrock (10+ models)
- SSM Session Manager
- CloudWatch Logs
- CloudTrail

### Monthly Cost

| Item | Cost (USD) |
|------|------------|
| EC2 (t4g.large, Graviton ARM64) | $20-40 |
| EBS (30GB) | $2.40 |
| VPC Endpoints (5 × $0.01/hour) | $29 |
| Bedrock | Pay-per-use |
| **Total** | **~$45-65/month** |

💡 **Tip:** Graviton ARM64 instances are 20-40% cheaper than x86 equivalents.

### Connect Your Apps

#### Telegram (Easiest)

1. Open Web UI
2. Say: "Connect to Telegram"
3. OpenClaw guides you through:
   - Create bot via @BotFather
   - Get bot token
   - Configure OpenClaw
4. Start chatting!

#### WhatsApp

1. Say: "Connect to WhatsApp"
2. OpenClaw shows QR code
3. Scan with WhatsApp
4. Done!

#### Discord / Slack

Similar guided setup - just ask OpenClaw!

### Common Tasks

#### Check Status

```bash
# SSH into instance (if you have key pair)
ssh -i your-key.pem ubuntu@INSTANCE_IP

# Or use SSM Session Manager
aws ssm start-session --target i-xxxxxxxxxxxxxxxxx

# Once connected:
openclaw status
```

#### View Logs

```bash
openclaw logs --follow
```

#### Restart OpenClaw

```bash
sudo systemctl restart openclaw-gateway
```

#### Update OpenClaw

```bash
sudo npm install -g openclaw@latest
sudo systemctl restart openclaw-gateway
```

#### Change Bedrock Model

Web UI → Settings → Model → Select new model

Or via CLI:
```bash
openclaw config set model "anthropic.claude-opus-4-6-v2:0"
```

### Security Best Practices

#### Enable Firewall (Recommended)

```bash
# Allow SSH if needed
sudo ufw allow 22/tcp

# Enable firewall
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
```

#### Disable Insecure Auth (Recommended)

```bash
openclaw config set gateway.controlUi.allowInsecureAuth false
openclaw security audit --fix
```

#### Schedule Security Audits

```bash
openclaw cron add \
  --name "security-audit" \
  --schedule "0 9 * * 1" \
  --task "Run weekly security audit"
```

### Troubleshooting

#### Web UI Not Accessible

**Symptom:** Port forwarding fails or Web UI doesn't load

**Fix:**
1. Check OpenClaw is running:
   ```bash
   sudo systemctl status openclaw-gateway
   ```

2. Restart if needed:
   ```bash
   sudo systemctl restart openclaw-gateway
   ```

3. Check SSM agent:
   ```bash
   sudo systemctl status amazon-ssm-agent
   ```

#### Bedrock Access Denied

**Symptom:** "AccessDenied" when using models

**Fix:**
1. Enable models in [Bedrock Console](https://console.aws.amazon.com/bedrock/)
2. Wait 2-3 minutes for propagation
3. Retry

#### Telegram Bot Not Responding

**Symptom:** Bot shows online but doesn't reply

**Fix:**
1. Check OpenClaw logs:
   ```bash
   openclaw logs --follow
   ```

2. Verify bot token:
   ```bash
   openclaw config get channels.telegram.botToken
   ```

3. Re-approve pairing if needed:
   ```bash
   openclaw pairing approve telegram YOUR_PAIRING_CODE
   ```

#### High AWS Costs

**Symptom:** Bill higher than expected

**Optimize:**
1. **Stop instance when not in use:**
   ```bash
   aws ec2 stop-instances --instance-ids i-xxxxxxxxxxxxxxxxx
   ```

2. **Use smaller instance:**
   - Change to `t4g.medium` in CloudFormation
   - Update stack

3. **Monitor Bedrock usage:**
   - Check CloudWatch metrics
   - Set billing alarms

### Backup and Restore

#### Backup Configuration

```bash
# Backup OpenClaw config
cp ~/.openclaw/openclaw.json ~/openclaw-backup-$(date +%Y%m%d).json

# Backup workspace
tar -czf ~/openclaw-workspace-$(date +%Y%m%d).tar.gz ~/.openclaw/workspace/
```

#### Create AMI Snapshot

```bash
aws ec2 create-image \
  --instance-id i-xxxxxxxxxxxxxxxxx \
  --name "openclaw-backup-$(date +%Y%m%d)" \
  --description "OpenClaw backup" \
  --no-reboot
```

#### Restore from Backup

```bash
# Restore config
cp ~/openclaw-backup-YYYYMMDD.json ~/.openclaw/openclaw.json

# Restart
sudo systemctl restart openclaw-gateway
```

### Upgrade Guide

#### Minor Updates (Patch Releases)

```bash
sudo npm install -g openclaw@latest
sudo systemctl restart openclaw-gateway
openclaw status
```

#### Major Updates (Version Upgrades)

1. **Backup first** (see above)
2. Update CloudFormation stack with new template
3. Or manually update:
   ```bash
   sudo npm install -g openclaw@next
   sudo systemctl restart openclaw-gateway
   ```

### Uninstall

#### Delete CloudFormation Stack

```bash
aws cloudformation delete-stack --stack-name openclaw-bedrock
```

This removes:
- EC2 instance
- VPC and subnets
- VPC endpoints
- Security groups
- IAM roles

**Note:** EBS snapshots (if any) are retained for 30 days.

### FAQ

#### Q: Can I use multiple messaging apps?

**A:** Yes! You can connect Telegram, WhatsApp, Discord, Slack simultaneously. Each gets isolated sessions.

#### Q: Does it work in China?

**A:** Yes, but use `clawdbot-china.yaml` template for AWS China regions (cn-north-1, cn-northwest-1).

#### Q: Can I self-host without AWS?

**A:** Yes, see [OpenClaw docs](https://docs.openclaw.ai) for Docker/bare-metal deployment. This project is AWS-specific.

#### Q: How do I add custom skills?

**A:** Place skill directories in `~/.openclaw/workspace/skills/`. OpenClaw auto-loads them. See [Skills Guide](https://docs.openclaw.ai/skills).

#### Q: Can I use OpenAI/Anthropic API instead of Bedrock?

**A:** Yes, change model config to use external providers. But you lose IAM authentication and pay API provider directly.

#### Q: Is multi-user supported?

**A:** This template is single-user. For enterprise multi-tenant, see `clawdbot-bedrock-agentcore-multitenancy.yaml` (advanced).

#### Q: What's the latency?

**A:** Typical response time: 1-3 seconds for simple queries, 3-8 seconds for complex tasks (depends on model and Bedrock region).

### Support

- **Documentation:** [OpenClaw Docs](https://docs.openclaw.ai)
- **Community:** [Discord](https://discord.com/invite/clawd)
- **Issues:** [GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)

---

## 简体中文

### 快速开始（8分钟）

#### 前置要求

1. **AWS账户**，开通Bedrock服务
2. **启用Bedrock模型**：[Bedrock控制台](https://console.aws.amazon.com/bedrock/)
   - 推荐：Claude Sonnet 4.5, Nova系列
3. **EC2密钥对**（可选，用于SSH访问）

#### 一键部署

点击您所在区域的"Launch Stack"：

| 区域 | 启动 |
|------|------|
| **美国西部（俄勒冈）** | [![Launch](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **美国东部（弗吉尼亚）** | [![Launch](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **亚太（东京）** | [![Launch](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |

**配置参数：**
- `KeyName`: 选择EC2密钥对（可选）
- `BedrockModelId`: 默认 `anthropic.claude-sonnet-4-5-v2:0`
- `InstanceType`: 默认 `t4g.large`（Graviton ARM64，更便宜）

#### 访问OpenClaw

部署完成后（约8分钟），在**CloudFormation输出**中找到：

1. **安装SSM插件**（一次性）
   ```bash
   # macOS
   brew install --cask session-manager-plugin
   
   # Linux
   curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_arm64/session-manager-plugin.deb" -o "session-manager-plugin.deb"
   sudo dpkg -i session-manager-plugin.deb
   ```

2. **端口转发**（保持终端打开）
   ```bash
   aws ssm start-session \
     --target i-xxxxxxxxxxxxxxxxx \
     --region ap-northeast-1 \
     --document-name AWS-StartPortForwardingSession \
     --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
   ```

3. **打开Web界面**
   ```
   http://localhost:18789/?token=您的TOKEN
   ```

4. **连接通讯软件**
   - 对话："连接到Telegram"
   - OpenClaw会一步步引导您！

### 架构

```
您（Telegram/微信/Discord/Slack）
  ↓
OpenClaw网关（EC2）
  ↓
Amazon Bedrock（Claude/Nova模型）
  ↓
返回响应
```

**核心特性：**
- ✅ **无需API密钥** - 使用AWS IAM认证Bedrock
- ✅ **私有网络** - VPC端点，不暴露公网
- ✅ **安全访问** - SSM Session Manager，无需开SSH端口
- ✅ **成本优化** - Graviton ARM64实例（便宜20-40%）
- ✅ **可审计** - CloudTrail记录每次API调用

### 月度成本

| 项目 | 费用（美元） |
|------|------------|
| EC2 (t4g.large, Graviton ARM64) | $20-40 |
| EBS (30GB) | $2.40 |
| VPC端点 (5 × $0.01/小时) | $29 |
| Bedrock | 按使用量付费 |
| **合计** | **约$45-65/月** |

💡 **提示：** Graviton ARM64实例比x86便宜20-40%。

### 连接通讯软件

#### Telegram（最简单）

1. 打开Web界面
2. 对话："连接到Telegram"
3. OpenClaw引导您：
   - 通过@BotFather创建bot
   - 获取bot token
   - 配置OpenClaw
4. 开始聊天！

#### WhatsApp

1. 对话："连接到WhatsApp"
2. OpenClaw显示二维码
3. 用WhatsApp扫描
4. 完成！

### 常见任务

详细说明请参考英文版。

### 故障排除

详细说明请参考英文版。

### 支持

- **文档：** [OpenClaw文档](https://docs.openclaw.ai)
- **社区：** [Discord](https://discord.com/invite/clawd)
- **问题：** [GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)
