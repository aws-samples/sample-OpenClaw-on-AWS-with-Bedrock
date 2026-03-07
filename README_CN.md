# OpenClaw AWS Bedrock 部署方案

> 在 AWS 上部署你自己的 AI 助手 — 连接 WhatsApp、Telegram、Discord、Slack。基于 Amazon Bedrock，无需 API 密钥，一键部署，约 $40/月。

[English](README.md) | 简体中文

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![AWS](https://img.shields.io/badge/AWS-Bedrock-orange.svg)](https://aws.amazon.com/bedrock/)
[![CloudFormation](https://img.shields.io/badge/IaC-CloudFormation-blue.svg)](https://aws.amazon.com/cloudformation/)

## 为什么做这个项目

[OpenClaw](https://github.com/openclaw/openclaw) 是增长最快的开源 AI 助手 — 运行在你自己的基础设施上，连接消息应用，能真正执行任务：管理邮件、浏览网页、运行命令、定时提醒。

问题是：自己搭建意味着管理多个供应商的 API 密钥、配置 VPN、自行处理安全。

本项目解决这些问题。一个 CloudFormation 堆栈搞定：

- **Amazon Bedrock** 提供模型访问 — 10 个模型，统一 API，IAM 认证（无需 API 密钥）
- **Graviton ARM 实例** — 比 x86 便宜 20-40%
- **SSM Session Manager** — 安全访问，无需开放端口
- **VPC 端点** — 流量保持在 AWS 私有网络内
- **CloudTrail** — 每次 API 调用自动审计

8 分钟部署完成，手机即可访问。

## 快速开始

### 一键部署

1. 点击对应区域的"部署"按钮
2. 选择 EC2 密钥对
3. 等待约 8 分钟
4. 查看输出（Outputs）标签

| 区域 | 部署 |
|------|------|
| **美国西部（俄勒冈）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **美国东部（弗吉尼亚）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **欧洲（爱尔兰）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=eu-west-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |
| **亚太（东京）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=ap-northeast-1#/stacks/create/review?stackName=openclaw-bedrock&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock.yaml) |

> **前提条件**：在 [Bedrock 控制台](https://console.aws.amazon.com/bedrock/) 启用所需模型，并在目标区域创建 EC2 密钥对。

### 部署后操作

> 🦞 **打开 Web UI，直接和 AI 说话就行。** 所有消息平台插件（WhatsApp、Telegram、Discord、Slack、飞书）已预装。告诉你的 OpenClaw 你想用什么方式连接，它会一步步指导你完成全部配置，无需手动操作。

```bash
# 1. 安装 SSM Session Manager 插件（一次性）
#    https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

# 2. 启动端口转发（保持终端打开）
INSTANCE_ID=$(aws cloudformation describe-stacks \
  --stack-name openclaw-bedrock \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
  --output text --region us-west-2)

aws ssm start-session \
  --target $INSTANCE_ID \
  --region us-west-2 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'

# 3. 获取 Token（在第二个终端）
TOKEN=$(aws ssm get-parameter \
  --name /openclaw/openclaw-bedrock/gateway-token \
  --with-decryption \
  --query Parameter.Value \
  --output text --region us-west-2)

# 4. 在浏览器打开
echo "http://localhost:18789/?token=$TOKEN"
```

### CLI 部署（替代方式）

```bash
aws cloudformation create-stack \
  --stack-name openclaw-bedrock \
  --template-body file://clawdbot-bedrock.yaml \
  --parameters ParameterKey=KeyPairName,ParameterValue=your-keypair \
  --capabilities CAPABILITY_IAM \
  --region us-west-2

aws cloudformation wait stack-create-complete \
  --stack-name openclaw-bedrock --region us-west-2
```

### 🎯 用 Kiro AI 部署

更轻松的方式？[Kiro](https://kiro.dev/) 通过对话引导你完成部署 — 打开本仓库作为工作区，说"帮我部署 OpenClaw"即可。

**[→ Kiro 部署指南](QUICK_START_KIRO.md)**

---

## 连接消息平台

部署完成后，在 Web UI 的 "Channels" 中连接：

| 平台 | 配置方式 | 文档 |
|------|---------|------|
| **WhatsApp** | 手机扫描二维码 | [指南](https://docs.openclaw.ai/channels/whatsapp) |
| **Telegram** | 通过 [@BotFather](https://t.me/botfather) 创建 Bot，粘贴 token | [指南](https://docs.openclaw.ai/channels/telegram) |
| **Discord** | 在 Developer Portal 创建应用，粘贴 bot token | [指南](https://docs.openclaw.ai/channels/discord) |
| **Slack** | 在 api.slack.com 创建应用，安装到工作区 | [指南](https://docs.openclaw.ai/channels/slack) |
| **Microsoft Teams** | 需要 Azure Bot 配置 | [指南](https://docs.openclaw.ai/channels/msteams) |
| **飞书 / Lark** | 社区插件：[openclaw-feishu](https://www.npmjs.com/package/openclaw-feishu) | — |

**完整平台文档**：[docs.openclaw.ai](https://docs.openclaw.ai/)

---

## OpenClaw 能做什么？

连接后直接发消息：

```
你：东京今天天气怎么样？
你：帮我总结这个 PDF [附件]
你：每天早上 9 点提醒我查邮件
你：打开 google.com 搜索 "AWS Bedrock 定价"
```

| 命令 | 功能 |
|------|------|
| `/status` | 查看模型、token 用量、成本 |
| `/new` | 开始新对话 |
| `/think high` | 启用深度推理模式 |
| `/help` | 列出所有命令 |

WhatsApp 和 Telegram 支持语音消息 — OpenClaw 会转录并回复。

---

## 架构

```
你（WhatsApp/Telegram/Discord）
  │
  ▼
┌─────────────────────────────────────────────┐
│  AWS 云                                     │
│                                             │
│  EC2（OpenClaw）──IAM──▶  Bedrock          │
│       │                  （Nova/Claude）    │
│       │                                     │
│  VPC 端点              CloudTrail           │
│  （私有网络）          （审计日志）          │
└─────────────────────────────────────────────┘
  │
  ▼
你（收到回复）
```

- **EC2**：运行 OpenClaw 网关（约 1GB 内存）
- **Bedrock**：通过 IAM 进行模型推理（无需 API 密钥）
- **SSM**：安全访问，无公网端口
- **VPC 端点**：到 Bedrock 的私有网络（可选，+$22/月）

---

## 模型

通过一个 CloudFormation 参数切换模型，无需改代码：

| 模型 | 输入/输出（每百万 tokens） | 适用场景 |
|------|--------------------------|---------|
| **Nova 2 Lite**（默认） | $0.30 / $2.50 | 日常任务，比 Claude 便宜 90% |
| Nova Pro | $0.80 / $3.20 | 性能与成本平衡，支持多模态 |
| Claude Sonnet 4.5 | $3.00 / $15.00 | 复杂推理、编程 |
| Claude Haiku 4.5 | $1.00 / $5.00 | 快速高效 |
| DeepSeek R1 | $0.55 / $2.19 | 开源推理模型 |
| Llama 3.3 70B | — | 开源替代方案 |
| Kimi K2.5 | $0.60 / $3.00 | 多模态智能体，262K 上下文 |

> 使用 [Global CRIS](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) — 在任意区域部署，请求自动路由到最优位置。

---

## 成本

### 典型月度成本（轻度使用）

| 组件 | 成本 |
|------|------|
| EC2（t4g.medium，Graviton） | $24 |
| EBS（30GB gp3） | $2.40 |
| VPC 端点（可选） | $22 |
| Bedrock（Nova 2 Lite，约 100 次对话/天） | $5-8 |
| **合计** | **$31-56** |

### 省钱技巧

- 用 Nova 2 Lite 替代 Claude → 便宜 90%
- 用 Graviton（ARM）替代 x86 → 便宜 20-40%
- 不开 VPC 端点 → 省 $22/月（安全性降低）
- AWS Savings Plans → EC2 省 30-40%

### 对比其他方案

| 方案 | 成本 | 特点 |
|------|------|------|
| ChatGPT Plus | $20/人/月 | 单用户，无集成 |
| 本项目（5 人） | 约 $10/人/月 | 多用户，WhatsApp/Telegram/Discord，完全控制 |
| 本地 Mac Mini | $0 服务器 + $20-30 API | 需要硬件，自行维护 |

---

## 配置

### 实例类型

| 类型 | 月费 | 内存 | 架构 | 适用场景 |
|------|------|------|------|---------|
| t4g.small | $12 | 2GB | Graviton ARM | 个人使用 |
| **t4g.medium** | **$24** | **4GB** | **Graviton ARM** | **小团队（推荐）** |
| t4g.large | $48 | 8GB | Graviton ARM | 中型团队 |
| c7g.xlarge | $108 | 8GB | Graviton ARM | 高性能 |
| t3.medium | $30 | 4GB | x86 | x86 兼容 |

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `OpenClawModel` | Nova 2 Lite | Bedrock 模型 ID |
| `InstanceType` | c7g.large | EC2 实例类型 |
| `CreateVPCEndpoints` | true | 私有网络（+$22/月） |
| `EnableSandbox` | true | Docker 隔离代码执行 |
| `CreateS3Bucket` | true | S3 文件分享功能 |
| `InstallS3FilesSkill` | true | 自动安装 S3 文件分享技能 |
| `KeyPairName` | none | EC2 密钥对（可选，紧急 SSH 用） |

---

## 部署选项

### 标准部署（EC2）— 本文档

适合大多数用户。固定成本，完全控制，24/7 可用。

### 多租户平台（AgentCore Runtime）— [README_AGENTCORE.md](README_AGENTCORE.md)

> ⚠️ 开发中 — 目标 2026 年 6 月 v1.0。[路线图 →](ROADMAP.md)

将 OpenClaw 从单用户工具变成企业平台：每位员工一个 AI 助手，每个团队一个 AI 助手，每个部门一个 AI 助手 — 边界清晰，能力共享，集中治理。

| 能力 | 实现方式 |
|------|---------|
| 租户隔离 | 每用户独立 Firecracker microVM（AgentCore Runtime） |
| 统一模型访问 | 一个 Bedrock 账户，按租户计量（约 $1-2/人/月） |
| 共享 Skills + SaaS 密钥打包 | 安装一次，按租户授权，凭证不暴露 |
| 定制化权限规则 | SSM 存储，热更新，Plan A + E 双层防御 |
| 受控信息共享 | 跨租户数据策略，审计，显式授权 |
| 人工审批流程 | Auth Agent → 管理员消息通知 → 批准/拒绝 |
| 弹性计算 | 自动扩展 microVM，按需突发，按使用付费 |

**本地 Demo**：`python3 demo/console.py` → 打开 http://localhost:8099 查看管理控制台

**[→ 多租户完整文档](README_AGENTCORE.md)** · **[→ 路线图](ROADMAP.md)**

### macOS（Apple Silicon）— iOS/macOS 开发

| 类型 | 芯片 | 内存 | 月费 |
|------|------|------|------|
| mac2.metal | M1 | 16GB | $468 |
| mac2-m2.metal | M2 | 24GB | $632 |
| mac2-m2pro.metal | M2 Pro | 32GB | $792 |

> 24 小时最低分配期。仅适合 Apple 开发 — 一般用途 Linux 便宜 12 倍。

| 区域 | 部署 |
|------|------|
| **美国西部（俄勒冈）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) |
| **美国东部（弗吉尼亚）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/create/review?stackName=openclaw-mac&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-bedrock-mac.yaml) |

### 🇨🇳 AWS 中国区（北京/宁夏）

使用 SiliconFlow（DeepSeek、Qwen、GLM）替代 Bedrock。需要 SiliconFlow API 密钥。

| 区域 | 部署 |
|------|------|
| **cn-north-1（北京）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://cn-north-1.console.amazonaws.cn/cloudformation/home?region=cn-north-1#/stacks/create/review?stackName=openclaw-china&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-china.yaml) |
| **cn-northwest-1（宁夏）** | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://cn-northwest-1.console.amazonaws.cn/cloudformation/home?region=cn-northwest-1#/stacks/create/review?stackName=openclaw-china&templateURL=https://sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn/clawdbot-china.yaml) |

**[→ 中国区部署指南](DEPLOYMENT_CN.md)**

---

## 安全

| 层级 | 功能 |
|------|------|
| **IAM Role** | 无需 API 密钥，自动凭证轮换 |
| **SSM Session Manager** | 无公网端口，会话日志 |
| **VPC 端点** | Bedrock 流量保持在私有网络 |
| **SSM Parameter Store** | Gateway token 以 SecureString 存储，不写入磁盘 |
| **供应链保护** | Docker 通过 GPG 签名仓库安装，NVM 先下载后执行（无 `curl \| sh`） |
| **Docker 沙箱** | 隔离群聊中的代码执行 |
| **CloudTrail** | 每次 Bedrock API 调用均被审计 |

**[→ 完整安全文档](SECURITY.md)**

---

## 社区技能

可选的 OpenClaw 扩展：

- [S3 文件分享](skills/s3-files-skill/) — 通过 S3 pre-signed URL 上传和分享文件（默认自动安装）
- [Kiro CLI 技能](skills/openclaw-kirocli-skill/) — 通过 Kiro CLI 进行 AI 编程
- [AWS 备份技能](https://github.com/genedragon/openclaw-aws-backup-skill) — S3 备份/恢复，支持 KMS 加密

---

## 通过 SSM 命令行访问

```bash
# 启动交互式会话
aws ssm start-session --target i-xxxxxxxxx --region us-east-1

# 切换到 ubuntu 用户
sudo su - ubuntu

# 运行 OpenClaw 命令
openclaw --version
openclaw gateway status
```

---

## 故障排查

常见问题和解决方案：[TROUBLESHOOTING.md](TROUBLESHOOTING.md)

分步部署指南：[DEPLOYMENT.md](DEPLOYMENT.md)

---

## 贡献

我们正在开放构建企业级 OpenClaw 平台 — 从单用户部署到多租户 SaaS。无论你是企业架构师、技能开发者、安全研究员，还是想要更好 AI 助手的用户，都欢迎参与。

当前最需要帮助的方向：
- 端到端多租户集成测试
- 带 SaaS 凭证打包的 Skills（Jira、Salesforce、SAP）
- Agent 间编排协议
- 成本对比测试（AgentCore vs EC2）
- 安全审计和渗透测试

**[→ 路线图](ROADMAP.md)** · **[→ 贡献指南](CONTRIBUTING.md)** · **[→ GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)**

## 资源

- [OpenClaw 文档](https://docs.openclaw.ai/) · [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [Amazon Bedrock 文档](https://docs.aws.amazon.com/bedrock/) · [SSM Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html)
- [OpenClaw on Lightsail](https://aws.amazon.com/blogs/aws/introducing-openclaw-on-amazon-lightsail-to-run-your-autonomous-private-ai-agents/)（AWS 官方博客）

## 支持

- **本项目**：[GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)
- **OpenClaw**：[GitHub Issues](https://github.com/openclaw/openclaw/issues) · [Discord](https://discord.gg/openclaw)
- **AWS Bedrock**：[AWS re:Post](https://repost.aws/tags/bedrock)

---

**Built with Kiro** 🦞
