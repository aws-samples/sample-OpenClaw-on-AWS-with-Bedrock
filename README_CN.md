# openclaw AWS Bedrock 部署方案

> 在 AWS 上使用 Amazon Bedrock 部署 [openclaw](https://github.com/openclaw/openclaw)（原 Clawdbot/moltbot）。无需管理 Anthropic/OpenAI/DeepSeek API 密钥，使用 Graviton ARM 处理器，企业级、安全、一键部署。

[English](README.md) | 简体中文

## 这是什么？

[openclaw](https://github.com/openclaw/openclaw)（原 Clawdbot）是一个开源的个人 AI 助手，可以连接 WhatsApp、Slack、Discord 等平台。本项目提供 **AWS 原生部署方案**，使用 Amazon Bedrock 统一 API，无需管理多个 AI 提供商的 API 密钥。

## 为什么选择 AWS 原生版？

| 原版 openclaw | 本项目 |
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
- **Nova 2 Lite 默认**：$0.30/$2.50 每百万 tokens，比 Claude 便宜 90%
- **8 个模型可选**：一个参数切换 Nova、Claude、DeepSeek、Llama
- **无供应商锁定**：切换模型无需改代码或重新部署

**2. Graviton ARM 优势（推荐）**
- **推荐 Graviton**：性价比比 x86 高 20-40%
- **成本示例**：t4g.medium（$24/月）vs t3.medium（$30/月）- 相同配置，节省 20%
- **节能环保**：Graviton 能耗比 x86 低 70%

**3. 企业级安全与合规**
- **零密钥管理**：一个 IAM 角色替代多个供应商密钥
- **完整审计追踪**：CloudTrail 记录每次 Bedrock API 调用
- **私有网络**：VPC Endpoints 保证流量在 AWS 内网
- **安全访问**：SSM Session Manager，无需公网端口

**4. 云原生自动化**
- **一键部署**：CloudFormation 自动化 VPC、IAM、EC2、Bedrock 配置
- **基础设施即代码**：可重复、版本控制的部署
- **多区域支持**：在多个区域使用相同配置部署

## 核心优势

- 🔐 **零密钥管理** - 一个 IAM 角色替代多个 API 密钥
- 🤖 **多模型支持** - 一个参数切换 Claude、Nova、DeepSeek
- 🏢 **企业级** - 完整的 CloudTrail 审计日志和合规支持
- ⚡ **一键部署** - CloudFormation 约 8 分钟自动化所有配置
- 🔒 **安全访问** - SSM Session Manager，无需暴露公网端口
- 💰 **成本可见** - AWS 原生成本追踪和优化

## 部署选项

### 🚀 无服务器部署（AgentCore Runtime）- 生产环境推荐

**[→ 使用 AgentCore Runtime 部署](README_AGENTCORE.md)**

> ⚠️ **开发中**：AgentCore Runtime 需要一个自定义 Docker 镜像，目前仓库中尚未提供。部署前需要自行构建，详见 [README_AGENTCORE.md](README_AGENTCORE.md)。

| 特性 | AgentCore Runtime | 传统 EC2 |
|------|-------------------|----------|
| **扩展性** | ✅ 根据需求自动扩展 | ❌ 固定容量 |
| **成本模式** | ✅ 按使用付费（无空闲成本） | ❌ 24/7 付费（即使空闲） |
| **可用性** | ✅ 跨 microVM 分布式 | ⚠️ 单实例 |
| **容器隔离** | ✅ 每次执行隔离的 microVM | ⚠️ 共享实例 |
| **管理** | ✅ 完全托管运行时 | ⚠️ 手动扩展 |

典型使用场景节省 40-70%。**[→ 完整 AgentCore 文档](README_AGENTCORE.md)**

---

### 💻 标准部署（EC2）

适合需要固定成本、完全控制实例、24/7 可用的场景。

## 快速开始

### ⚡ 一键部署（推荐 - 约 8 分钟）

**只需 3 步**：
1. ✅ 点击下方"部署"按钮
2. ✅ 在表单中选择 EC2 密钥对
3. ✅ 等待约 8 分钟 → 查看"输出"标签 → 复制 URL → 开始使用！

**Linux（Graviton/x86）- 推荐**

| 区域 | 部署 |
|------|------|
| **美国西部（俄勒冈）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **美国东部（弗吉尼亚）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **欧洲（爱尔兰）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **亚太（东京）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |

**macOS (EC2 Mac) - 适合 Apple 开发**

| 区域 | 部署 | 月度成本 |
|------|------|----------|
| **美国西部（俄勒冈）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) | $468-792 |
| **美国东部（弗吉尼亚）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) | $468-792 |

> **Mac 实例**：24 小时最低分配期，适合 iOS/macOS 开发团队。

---

### 🎯 想要更有趣的部署方式？

**和 Kiro AI 聊天部署！** Kiro 会引导你完成部署和手机配置——无需记命令。

### 👉 **[试试 Kiro 部署 →](QUICK_START_KIRO.md)** 👈

---

部署后查看 CloudFormation 输出标签，按步骤操作：

1. **安装 SSM 插件**：点击 `Step1InstallSSMPlugin` 链接（一次性）
2. **端口转发**：复制 `Step2PortForwarding` 命令，在本地运行（保持终端打开）
3. **获取 Token**：运行 `Step3GetToken` 中的命令，从 SSM Parameter Store 获取 token
4. **打开 URL**：在浏览器打开 `http://localhost:18789/?token=<你的token>`
5. **开始使用**：在 Web UI 连接 WhatsApp/Telegram/Discord

![CloudFormation 输出](images/20260128-105244.jpeg)
![openclaw Web UI](images/20260128-105059.jpg)

> **部署前**：在 [Bedrock Console](https://console.aws.amazon.com/bedrock/) 启用所需模型

### 手动部署（CLI）

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

### 访问 openclaw

```bash
# 获取实例 ID（或从 CloudFormation 输出标签查看）
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-bedrock \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text)

# 启动端口转发（保持终端打开）
aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# 从 SSM Parameter Store 获取 token
TOKEN=$(aws ssm get-parameter \
  --name /openclaw/openclaw-bedrock/gateway-token \
  --with-decryption \
  --query Parameter.Value \
  --output text \
  --region us-west-2)

# 在浏览器打开
http://localhost:18789/?token=$TOKEN
```

## 如何使用 openclaw

### 连接消息平台

#### WhatsApp（推荐）

1. 在 Web UI 点击 "Channels" → "Add Channel" → "WhatsApp"
2. 用手机 WhatsApp 扫描二维码
   - 打开 WhatsApp → 设置 → 已关联的设备 → 点击"关联设备"
3. 发送测试消息

📖 **完整指南**：https://docs.openclaw.ai/channels/whatsapp

#### Telegram

1. 与 [@BotFather](https://t.me/botfather) 对话创建 Bot，获取 token
2. 在 Web UI 配置 Telegram channel
3. 向你的 bot 发送 `/start` 测试

📖 **完整指南**：https://docs.openclaw.ai/channels/telegram

#### Discord

1. 访问 [Discord Developer Portal](https://discord.com/developers/applications) 创建 Bot，复制 token
2. 在 Web UI 配置 Discord channel
3. 在频道中 @你的 bot 测试

📖 **完整指南**：https://docs.openclaw.ai/channels/discord

#### Slack

1. 访问 [Slack API](https://api.slack.com/apps) 创建 App
2. 配置 Bot Token Scopes，安装到工作区
3. 在 Web UI 配置 Slack channel

📖 **完整指南**：https://docs.openclaw.ai/channels/slack

### 使用 openclaw

**通过 WhatsApp/Telegram/Discord**：直接发送消息！

```
你：今天天气怎么样？
openclaw：让我帮你查一下...
```

常用聊天命令：

| 命令 | 说明 |
|------|------|
| `/status` | 显示会话状态（模型、tokens、成本） |
| `/new` 或 `/reset` | 开始新对话 |
| `/think high` | 启用深度思考模式 |
| `/help` | 显示可用命令 |

**语音消息**：WhatsApp/Telegram 直接发送语音，openclaw 会转录并回复。

### 自定义提示词

在实例上创建 `~/openclaw/system.md`：

```markdown
你是我的个人助手。请简洁且有帮助。
始终以友好的语气回复。
```

详细指南请访问 [openclaw 文档](https://docs.openclaw.ai/)。

## 架构

```
你的电脑
     │ AWS CLI + SSM Plugin
     ▼
SSM Service（AWS 私有网络）
     │ 端口转发
     ▼
EC2 实例（openclaw）
     │ IAM Role 认证
     ▼
Amazon Bedrock（Nova/Claude）
```

**核心组件**：
- **EC2 实例**：运行 openclaw gateway
- **IAM Role**：与 Bedrock 认证（无需 API Key）
- **SSM Session Manager**：安全访问，无需公网端口
- **VPC 端点**：私有网络访问 Bedrock

## 成本

### 月度基础设施成本

| 服务 | 配置 | 月度成本 |
|------|------|----------|
| EC2 (c7g.large, Graviton) | 2 vCPU, 4GB RAM | $52.60 |
| EBS (gp3) | 30GB | $2.40 |
| VPC 端点 | 5 个端点 | $29 |
| **小计** | | **~$84** |

### Bedrock 使用成本（按量付费）

| 模型 | 输入 | 输出 |
|------|------|------|
| Nova 2 Lite（默认） | $0.30/百万 tokens | $2.50/百万 tokens |
| Nova Pro | $0.80/百万 tokens | $3.20/百万 tokens |
| Claude Sonnet 4.5 | $3/百万 tokens | $15/百万 tokens |
| Claude Haiku 4.5 | $1/百万 tokens | $5/百万 tokens |
| DeepSeek R1 | $0.55/百万 tokens | $2.19/百万 tokens |
| Kimi K2.5 | $0.60/百万 tokens | $3.00/百万 tokens |

**示例**：每天 100 次对话（Nova 2 Lite）≈ $5-8/月

### 成本优化

- 使用 Nova 2 Lite 替代 Claude：便宜 90%
- 使用 Graviton 实例：比 x86 便宜 20-40%
- 禁用 VPC 端点：节省 $29/月（安全性降低）
- 使用 Savings Plans：EC2 节省 30-40%

## 配置

### 支持的模型

```yaml
OpenClawModel:
  - global.amazon.nova-2-lite-v1:0              # 默认，最经济
  - global.anthropic.claude-sonnet-4-5-20250929-v1:0  # 最强能力
  - us.amazon.nova-pro-v1:0                     # 性能与成本平衡
  - global.anthropic.claude-haiku-4-5-20251001-v1:0   # 快速经济
  - us.deepseek.r1-v1:0                         # 开源推理
  - us.meta.llama3-3-70b-instruct-v1:0          # 开源备选
  - moonshotai.kimi-k2.5                        # 多模态，262K 上下文
```

### 实例类型

```yaml
InstanceType:
  # Graviton（ARM）- 推荐，性价比最高
  - t4g.small   # $12/月，2GB RAM
  - t4g.medium  # $24/月，4GB RAM
  - c7g.large   # $52/月，4GB RAM（默认）
  - c7g.xlarge  # $108/月，8GB RAM
  # x86 - 兼容性备选
  - t3.medium   # $30/月，4GB RAM
  - c5.xlarge   # $122/月，8GB RAM
```

### VPC 端点

```yaml
CreateVPCEndpoints: true   # 生产环境推荐，流量保持在 AWS 内网
CreateVPCEndpoints: false  # 节省 $29/月，流量经过公网
```

## 安全特性

- **IAM Role 认证**：无需 API Key，EC2 实例通过 IAM 角色与 Bedrock 认证
- **SSM Session Manager**：无需公网端口，自动会话日志，CloudTrail 审计
- **VPC 端点**：Bedrock API 调用不经过公网，更低延迟，符合合规要求

**完整安全文档**：[SECURITY.md](SECURITY.md)

## 故障排查

### 通过 SSM 命令行登录实例

```bash
# 启动交互式会话（类似 SSH）
aws ssm start-session --target i-xxxxxxxxxxxxxxxxx --region us-east-1

# 切换到 ubuntu 用户
sudo su - ubuntu

# 执行 openclaw 命令
openclaw --version
openclaw gateway status
```

### 查看安装日志

```bash
# 最近 100 行
sudo tail -100 /var/log/openclaw-setup.log

# 实时跟踪（安装中）
sudo tail -f /var/log/openclaw-setup.log

# 完整 cloud-init 日志
sudo cat /var/log/cloud-init-output.log
```

### 常见问题

**WaitCondition timed out**：安装过程中出错导致 cfn-signal 未发出。登录实例查看 `/var/log/openclaw-setup.log` 确认具体报错。

**SSM 无法连接**：
```bash
aws ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=$INSTANCE_ID"
```

**Bedrock API 错误**：确认已在 [Bedrock Console](https://console.aws.amazon.com/bedrock/) 启用对应模型。

**完整排错指南**：[TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## macOS 部署

**仅适合 iOS/macOS 开发团队。** Mac 实例成本 $468-792/月，24 小时最低分配期。

### 何时使用

- ✅ iOS/macOS 应用开发和 CI/CD
- ✅ Xcode 构建自动化
- ✅ Apple 生态集成（iCloud、APNs）
- ❌ 一般 openclaw 使用（Linux 便宜 12 倍）

### Mac 实例选项

| 类型 | 芯片 | 内存 | 月度成本 | 适用场景 |
|------|------|------|----------|----------|
| mac2.metal | M1 | 16GB | $468 | 标准构建 |
| mac2-m2.metal | M2 | 24GB | $632 | 最新芯片 |
| mac2-m2pro.metal | M2 Pro | 32GB | $792 | 高性能 |

部署时必须指定支持 Mac 实例的可用区（先在 AWS 控制台确认）。访问方式与 Linux 相同（SSM + 端口转发）。

## 与原版对比

| | 原版（本地部署） | 本项目（AWS） |
|--|----------------|--------------|
| **配置** | 手动安装，配置 API Key，Tailscale VPN | CloudFormation 一键，8 分钟就绪 |
| **成本** | $20-30/月 API 费（不含硬件） | $36-50/月全包 |
| **模型** | 单一供应商，手动切换 | 8 个模型，一个参数切换 |
| **安全** | API Key 存配置文件，无审计 | IAM 认证，CloudTrail 全审计 |
| **可用性** | 依赖本地硬件和网络 | 99.99% SLA |

## 贡献

欢迎贡献！Fork 仓库 → 创建 feature 分支 → 提交 Pull Request。

## 许可证

MIT License。openclaw 本身有独立许可证，详见 [openclaw GitHub](https://github.com/openclaw/openclaw)。

## 资源

- [openclaw 文档](https://docs.openclaw.ai/)
- [openclaw GitHub](https://github.com/openclaw/openclaw)
- [Amazon Bedrock](https://aws.amazon.com/bedrock/)
- [SSM Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)

## 支持

- **openclaw**：[GitHub Issues](https://github.com/openclaw/openclaw/issues)
- **AWS Bedrock**：[AWS re:Post](https://repost.aws/tags/bedrock)
- **本项目**：[GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)

---

**Built by builder + Kiro** 🦞

*本项目 90% 的代码由 Kiro AI 通过对话生成。*

在你控制的 AWS 基础设施上部署个人 AI 助手 🦞
