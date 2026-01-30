# Moltbot AWS Bedrock 部署方案

> 在 AWS 上使用 Amazon Bedrock 部署 [Moltbot](https://github.com/moltbot/moltbot)（原 Clawdbot）。无需管理 Anthropic/OpenAI/DeepSeek API 密钥，使用 Graviton ARM 处理器，企业级、安全、一键部署。

[English](README.md) | 简体中文

## 这是什么？

[Moltbot](https://github.com/moltbot/moltbot)（原 Clawdbot）是一个开源的个人 AI 助手，可以连接 WhatsApp、Slack、Discord 等平台。本项目提供 **AWS 原生部署方案**，使用 Amazon Bedrock 统一 API，无需管理多个 AI 提供商的 API 密钥。

## 为什么选择 AWS 原生版？

| 原版 Moltbot | 本项目 |
|---------------|--------|
| 多个 API 密钥（Anthropic/OpenAI 等） | **Amazon Bedrock 统一 API + IAM** |
| 单一模型，固定成本 | **8 个模型可选，Nova 2 Lite（对比 Anthropic 便宜 90%）** |
| x86 硬件，固定规格 | **x86/ARM/Mac 灵活配置，推荐 Graviton ARM（省 20-40%）** |
| Tailscale VPN | **SSM Session Manager** |
| 手动配置 | **CloudFormation 一键部署** |
| 无审计日志 | **CloudTrail 自动审计** |
| 公网访问 | **VPC 端点（私有网络）** |

### 核心优势

**1. 多模型灵活性与成本优势**
- **Nova Pro 默认**：$0.80/$3.20 每百万 tokens，比 Claude 的 $3/$15 便宜 73%
- **8 个模型可选**：一个参数切换 Nova、Claude、DeepSeek、Llama
- **智能路由**：简单任务用 Nova Lite，复杂推理用 Claude Sonnet
- **无供应商锁定**：切换模型无需改代码或重新部署

**2. 灵活的实例配置与 Graviton 优势（推荐）**
- **x86 和 ARM 双支持**：可选择 t3/c5（x86）或 t4g/c7g（Graviton ARM）
- **推荐 Graviton**：性价比比 x86 高 20-40%
- **成本示例**：t4g.medium（$24/月）vs t3.medium（$30/月）- 相同配置，节省 20%
- **灵活配置**：从 t4g.small（$12/月）到 c7g.xlarge（$108/月）按需选择
- **节能环保**：Graviton 能耗比 x86 低 70%

**3. 企业级安全与合规**
- **零密钥管理**：一个 IAM 角色替代多个供应商密钥
- **完整审计追踪**：CloudTrail 记录每次 Bedrock API 调用
- **私有网络**：VPC Endpoints 保证流量在 AWS 内网
- **安全访问**：SSM Session Manager，无需公网端口

**4. 云原生自动化**
- **一键部署**：CloudFormation 自动化 VPC、IAM、EC2、Bedrock 配置
- **基础设施即代码**：可重复、版本控制的部署
- **多区域支持**：在 4 个区域使用相同配置部署

## 核心优势

- 🚀 **Graviton ARM 处理器**：性价比比 x86 高 20-40%
- 💰 **Nova 2 Lite 默认**：比 Claude 便宜 90%，日常任务表现出色
- 🔐 **零密钥管理** - 一个 IAM 角色替代多个 API 密钥（Anthropic、OpenAI、DeepSeek）
- 🤖 **8 个模型即用** - 无需改代码即可切换 Nova、Claude、DeepSeek、Llama
- 🏢 **企业级** - 完整的 CloudTrail 审计日志和合规支持
- ⚡ **一键部署** - CloudFormation 8 分钟自动化所有配置
- 🔒 **安全访问** - SSM Session Manager，无需暴露公网端口

## 快速开始

### ⚡ 一键部署（强烈推荐 - 8 分钟即可使用！）

> **为什么选择 CloudFormation？** 全自动配置，无需手动操作。点击按钮，等待 8 分钟，获取 URL 即可使用！

**只需 3 步**：
1. ✅ 点击下方"部署"按钮
2. ✅ 在表单中选择 EC2 密钥对
3. ✅ 等待约 8 分钟 → 查看"输出"标签 → 复制 URL → 开始使用！

**自动完成的操作**：
- 创建 VPC、子网、安全组
- 启动 EC2 实例
- 安装 Node.js、Docker、Clawdbot
- 配置 Bedrock 集成
- 生成安全网关令牌
- 输出可直接使用的 URL

点击部署：

| 区域 | 部署 |
|------|------|
| **美国西部（俄勒冈）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=clawdbot-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **美国东部（弗吉尼亚）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=clawdbot-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **欧洲（爱尔兰）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/create/review?stackName=clawdbot-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **亚太（东京）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-1#/stacks/create/review?stackName=clawdbot-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |

> **说明**：使用 Global CRIS 配置文件 - 在全球 30+ 区域可用。可在任意区域部署，请求会自动路由到最优位置。

> **💡 想要更有趣的方式？** 和 Kiro AI 聊天部署！Kiro 会引导你完成部署和手机配置。[试试 Kiro 部署 →](QUICK_START_KIRO.md)

> **注意**：在目标区域创建 EC2 密钥对

### 手动部署

**下载并上传**：

1. 下载模板：[clawdbot-bedrock.yaml](clawdbot-bedrock.yaml)
2. 访问 [CloudFormation Console](https://console.aws.amazon.com/cloudformation/)
3. 上传模板并部署

**使用 AWS CLI**：

```bash
aws cloudformation create-stack \
  --stack-name clawdbot-bedrock \
  --template-body file://clawdbot-bedrock.yaml \
  --parameters ParameterKey=KeyPairName,ParameterValue=your-keypair \
  --capabilities CAPABILITY_IAM \
  --region us-west-2

# 等待完成
aws cloudformation wait stack-create-complete \
  --stack-name clawdbot-bedrock \
  --region us-west-2
```

### 访问 Clawdbot

![CloudFormation 输出](images/20260128-105244.jpeg)

![Clawdbot Web UI](images/20260128-105059.jpg)

```bash
# 获取实例 ID
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name clawdbot-bedrock \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text)

# 启动端口转发（保持终端打开）
aws ssm start-session \
  --target $INSTANCE_ID \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# 获取 token（新终端）
aws ssm start-session --target $INSTANCE_ID
sudo su - ubuntu
cat ~/.clawdbot/gateway_token.txt

# 在浏览器打开
http://localhost:18789/?token=<你的token>
```

## 如何使用 Clawdbot

### 连接消息平台

#### WhatsApp（推荐）

1. 在 Web UI 点击 "Channels" → "Add Channel" → "WhatsApp"
2. 用手机 WhatsApp 扫描二维码
   - 打开 WhatsApp → 设置 → 已关联的设备
   - 点击"关联设备"
   - 扫描二维码
3. 发送测试消息

**提示**：建议使用专用手机号，或启用 `selfChatMode`。

#### Telegram

1. 创建 Bot：与 [@BotFather](https://t.me/botfather) 对话
   ```
   /newbot
   选择名称：My Clawdbot
   选择用户名：my_clawdbot_bot
   ```
2. 复制 Bot Token（格式：`123456:ABC-DEF...`）
3. 在 Web UI 配置 Telegram channel
4. 测试：向你的 bot 发送 `/start`

#### Discord

1. 创建 Bot：访问 [Discord Developer Portal](https://discord.com/developers/applications)
   - 点击 "New Application"
   - 进入 "Bot" → "Add Bot"
   - 复制 bot token
   - 启用 intents：Message Content、Server Members
2. 邀请 Bot：生成邀请链接
   ```
   https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=8&scope=bot
   ```
3. 在 Web UI 配置 Discord channel
4. 测试：在 Discord 频道中 @你的bot

#### Slack

1. 创建 App：访问 [Slack API](https://api.slack.com/apps)
2. 配置 Bot Token Scopes（chat:write、channels:history）
3. 安装 App 到工作区
4. 在 Web UI 配置 Slack channel
5. 测试：邀请 bot 到频道并 @它



### 使用 Clawdbot

#### 发送消息

**通过 WhatsApp/Telegram/Discord**：直接发送消息！

```
你：今天天气怎么样？
Clawdbot：让我帮你查一下...
```

**通过 CLI**：
```bash
# SSH/SSM 连接到实例后
clawdbot message send --to +1234567890 --message "你好"
```

#### 聊天命令

在任何连接的频道发送：

| 命令 | 说明 |
|------|------|
| `/status` | 显示会话状态（模型、tokens、成本） |
| `/new` 或 `/reset` | 开始新对话 |
| `/think high` | 启用深度思考模式 |
| `/help` | 显示可用命令 |

#### 语音消息

**WhatsApp/Telegram**：直接发送语音消息 - Clawdbot 会转录并回复！

#### 浏览器控制

```
你：打开 google.com 搜索"AWS Bedrock"
Clawdbot：*打开浏览器，执行搜索，返回结果*
```

#### 定时任务

```
你：每天早上9点提醒我查看邮件
Clawdbot：*创建 cron 任务*
```

### 高级功能

#### 安装技能

```bash
# 列出可用技能
clawdbot skills list

# 安装技能
clawdbot skills install voice-generation

# 查看已安装技能
clawdbot skills installed
```

#### 自定义提示词

在实例上创建 `~/clawd/system.md`：

```markdown
你是我的个人助手。请简洁且有帮助。
始终以友好的语气回复。
```

详细指南请访问 [molt 文档](https://docs.molt.bot/)。

## 架构

```
你的电脑
     │
     │ AWS CLI + SSM Plugin
     ▼
SSM Service（AWS 私有网络）
     │
     │ 端口转发
     ▼
EC2 实例（Moltbot）
     │
     │ IAM Role 认证
     ▼
Amazon Bedrock（Claude Sonnet 4）
```

**核心组件**：
- **EC2 实例**：运行 Moltbot gateway 和浏览器控制
- **IAM Role**：与 Bedrock 认证（无需 API Key）
- **SSM Session Manager**：安全访问，无需公网端口
- **VPC 端点**：私有网络访问 Bedrock
- **Lambda 预检查**：部署前验证环境

## 成本

### 月度基础设施成本

| 服务 | 成本 |
|------|------|
| EC2 (t3.medium) | $30 |
| EBS (30GB) | $2.40 |
| VPC 端点 (3个) | $22 |
| 数据传输 | $5-10 |
| **小计** | **$60-65** |

### Bedrock 使用成本（按量付费）

| 模型 | 输入 | 输出 |
|------|------|------|
| Claude Sonnet 4 | $3/百万 tokens | $15/百万 tokens |
| Claude 3.5 Sonnet v2 | $3/百万 tokens | $15/百万 tokens |
| Claude 3.5 Haiku | $1/百万 tokens | $5/百万 tokens |
| Claude 3 Haiku | $0.25/百万 tokens | $1.25/百万 tokens |
| Claude 3 Opus | $15/百万 tokens | $75/百万 tokens |

**示例**：每天 100 次对话（Sonnet 4）≈ $10-15/月

**总计**：轻度使用约 $70-80/月

### 成本优化

- 使用 Nova 或 Haiku：比 Claude 便宜 70%+
- 禁用 VPC 端点：节省 $22/月（安全性降低）
- 使用 Savings Plans：EC2 节省 30-40%

## 架构与数据流

### 简单聊天示例
```
你（WhatsApp）："AWS Bedrock 是什么？"
     ↓
EC2（Clawdbot）：接收 → 调用 Bedrock → 获取回复
     ↓
你：2-3 秒内收到答案

成本：约 $0.001 | 安全：IAM 认证，私有网络 | 数据：留在 AWS
```

### 高级功能：视频创建示例
```
你："创建一个 10 秒关于 AWS 的视频"
     ↓
EC2（Clawdbot）：
  1. Bedrock 生成脚本（$0.01）
  2. 调用 Runway API（$0.50，需要你的 API key）
  3. 下载视频到 /tmp（约 50MB）
  4. 可选：ffmpeg 处理
  5. 发送到 WhatsApp
     ↓
你：收到视频

总计：$0.51 | 时间：30-60 秒 | 资源：1GB 内存，50MB 磁盘
```

### 为什么 EC2 安全、可靠、经济？

**🔒 安全性**：
- 所有数据在你的 AWS 账号（不经过第三方服务器）
- IAM 认证（无需担心 API Key 泄露）
- VPC 端点（流量保持在 AWS 私有网络）
- CloudTrail 审计日志（每次 API 调用都被记录）

**🛡️ 可靠性**：
- 24/7 可用（即使你的电脑关机）
- 自动重启（systemd 守护）
- 多平台同时支持（WhatsApp、Telegram、Discord）

**💰 经济性**：
- 基础设施：约 $60-65/月（EC2 + VPC 端点）
- AI 使用：约 $10-50/月（按量付费）
- 总计：约 $70-115/月
- 对比：5 人 × ChatGPT Plus（$100/月）vs 1 个 Clawdbot（$70/月）

## 配置

### 支持的模型

```yaml
ClawdbotModel:
  - anthropic.claude-sonnet-4-5-20250929-v1:0  # 默认，最强能力
  - anthropic.claude-3-5-sonnet-20241022-v2:0  # 稳定备选
  - anthropic.claude-3-5-haiku-20241022-v1:0   # 更快，更便宜
  - anthropic.claude-3-haiku-20240307-v1:0     # 最快/最便宜
```

**模型选择指南**：
- **Claude Sonnet 4.5**（默认）：最佳性能、编码和复杂推理能力。在全球 30+ 个区域可用。
- **Nova v2**：性能和可用性的最佳平衡。
- **Claude 3.5 Haiku**：快速且经济，适合简单任务。

### 实例类型

```yaml
InstanceType:
  - t3.small   # $15/月 - 轻度使用
  - t3.medium  # $30/月 - 推荐
  - c5.xlarge  # $120/月 - 高性能
```

### VPC 端点

```yaml
CreateVPCEndpoints: true   # 生产环境推荐
  # 优点：私有网络，安全，合规
  # 缺点：+$22/月

CreateVPCEndpoints: false  # 成本优化
  # 优点：节省 $22/月
  # 缺点：流量经过公网
```

## 资源

- [Moltbot 文档](https://docs.molt.bot/)
- [Moltbot GitHub](https://github.com/moltbot/moltbot)
- [Amazon Bedrock](https://aws.amazon.com/bedrock/)
- [SSM Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)

## 支持

### 1. IAM Role 认证

无需 API Key。EC2 实例使用 IAM 角色与 Bedrock 认证：

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream"
  ],
  "Resource": "*"
}
```

### 2. SSM Session Manager

无需 SSH 密钥。通过 AWS Systems Manager 访问：

- ✅ 无需公网端口（除可选的 SSH 备用）
- ✅ 自动会话日志
- ✅ CloudTrail 审计追踪
- ✅ 会话超时控制

### 3. VPC 端点

流量保持在 AWS 网络内：

- ✅ Bedrock API 调用不经过公网
- ✅ 更低延迟
- ✅ 符合合规要求

### 4. Docker 沙箱

非主会话在隔离的 Docker 容器中运行：

```json
{
  "sandbox": {
    "mode": "non-main",
    "allowlist": ["bash", "read", "write"],
    "denylist": ["browser", "gateway"]
  }
}
```

## 监控和审计

### CloudTrail 日志

所有 Bedrock API 调用自动记录：

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=InvokeModel \
  --region us-west-2
```

### CloudWatch 日志

```bash
# 查看安装日志
aws logs tail /var/log/clawdbot-setup.log --follow

# 查看 SSM 会话日志
aws logs tail /aws/ssm/session-logs --follow
```

### 成本监控

```bash
# 查看 Bedrock 成本
aws ce get-cost-and-usage \
  --time-period Start=2026-01-01,End=2026-01-31 \
  --granularity DAILY \
  --metrics BlendedCost \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Bedrock"]}}'
```

## 故障排查

### 预检查失败

```bash
# 查看 Lambda 日志
aws logs tail /aws/lambda/clawdbot-bedrock-bedrock-precheck --follow

# 常见问题：
# 1. 模型未启用 → 在 Bedrock Console 启用
# 2. 区域不支持 → 使用 us-east-1 或 us-west-2
# 3. 权限不足 → 检查 IAM 权限
```

### 无法通过 SSM 连接

```bash
# 检查 SSM agent 状态
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID"

# 检查 IAM 角色
aws ec2 describe-instances \
  --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].IamInstanceProfile'
```

### Bedrock API 错误

```bash
# 测试 Bedrock 访问
aws bedrock-runtime invoke-model \
  --model-id us.anthropic.claude-sonnet-4-20250514-v1:0 \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[{"role":"user","content":"Hi"}]}' \
  --region us-west-2 \
  output.txt

# 查看 Clawdbot 日志
journalctl --user -u clawdbot-gateway -n 100
```

## 与原版对比

### 原版（Anthropic API + Tailscale）

```bash
# 需要：
- Anthropic API key（手动管理）
- Tailscale 账号（第三方服务）
- 手动安全配置

# 成本：~$40/月 + API 费用
```

### 本项目（Bedrock + SSM）

```bash
# 需要：
- 仅需 AWS 账号
- IAM 认证（自动）
- AWS 原生服务

# 成本：~$65/月 + Bedrock 费用
# 额外的 $25/月 换来：
# - 无需管理 API Key
# - 多模型支持（Claude、Nova、DeepSeek）
# - 完整审计日志
# - 企业合规支持
# - AWS 技术支持覆盖
```

## 贡献

欢迎贡献！请：

1. Fork 仓库
2. 创建 feature 分支
3. 提交 Pull Request

## 许可证

MIT License。详见 [LICENSE](LICENSE)。

Clawdbot 本身有独立的许可证。参见 [Clawdbot License](https://github.com/clawdbot/clawdbot)。

## 资源

- [Moltbot 文档](https://docs.molt.bot/)
- [Moltbot GitHub](https://github.com/moltbot/moltbot)
- [Amazon Bedrock](https://aws.amazon.com/bedrock/)
- [SSM Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)

## 支持

- **Moltbot**：[GitHub Issues](https://github.com/moltbot/moltbot/issues) | [Discord](https://discord.gg/moltbot)
- **AWS Bedrock**：[AWS re:Post](https://repost.aws/tags/bedrock)
- **本项目**：[GitHub Issues](https://github.com/JiaDe-Wu/clawdbot-aws-bedrock/issues)

---

**Built by builder + Kiro** 🦞

*本项目 90% 的代码由 Kiro AI 通过对话生成。*

在你控制的 AWS 基础设施上部署个人 AI 助手 🦞
