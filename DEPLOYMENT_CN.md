# OpenClaw AWS 中国区部署指南

## 简介

[OpenClaw](https://github.com/openclaw/openclaw)（前身为 Clawdbot）是一个开源、自托管的个人 AI 助手框架。与 ChatGPT 等云端 AI 助手不同，OpenClaw 运行在你自己控制的基础设施上（本地电脑、云服务器甚至树莓派），并能连接你日常使用的消息平台——WhatsApp、Telegram、Discord、Slack、iMessage 等。它不仅能生成文本，还能作为自主 Agent 代你执行实际任务、自动化工作流，同时保证数据隐私完全掌握在自己手中。

本文档是 OpenClaw 在 AWS 中国区（北京/宁夏）的完整部署指南。由于 AWS 中国区不支持 Amazon Bedrock 服务，本方案采用 SiliconFlow 等 OpenAI 兼容 API 作为 LLM 后端，结合 Graviton ARM 实例实现高性价比部署。文档涵盖前置条件准备、一键 CloudFormation 部署、部署后访问配置、常用运维操作及故障排查，帮助你在约 10 分钟内完成从零到可用的全流程搭建。

> 在 AWS 中国区（北京/宁夏）部署 [openclaw](https://github.com/openclaw/openclaw) 个人 AI 助手。使用 SiliconFlow 等 OpenAI 兼容 API，Graviton ARM 处理器，一键 CloudFormation 部署。

[English](README.md) | [简体中文（全球区）](README_CN.md) | **中国区部署**

## 为什么选择 AWS 中国区部署 OpenClaw？

AWS 中国区为在中国境内部署 AI 助手提供了独特优势——全球级安全标准与本地合规并行、零端口暴露的安全访问、一键基础设施编排，以及全球一致的操作体验。以下从核心维度展示 AWS 中国区方案的特点：

| 维度 | AWS 中国区方案 | 国内其他云厂商 | 本地自建方案 |
|------|---------------|---------------|-------------|
| **安全合规** | 等保三级 + ISO 27001/27017/27018 + SOC 1/2/3，继承 AWS 全球安全体系 | 安全认证覆盖度参差不齐 | 完全自负，无专业安全服务 |
| **零端口暴露访问** | SSM Session Manager，无需开放 SSH/公网端口 | 通常需开放 SSH 或配置 VPN | 需自行配置内网穿透 |
| **负责任 AI** | AWS 负责任 AI 框架 + SiliconFlow 算法备案，CloudTrail 全量审计 | AI 治理框架成熟度不一 | 无平台级保障，合规风险自担 |
| **数据主权** | 光环新网/西云数据本地运营，数据不出境 | 数据留在国内 | 数据完全本地 |
| **一键部署** | CloudFormation 模板，约 10 分钟 | 需额外适配 OpenClaw 部署脚本 | 手动安装配置 |
| **全球一致体验** | 与 AWS 全球区操作一致，零学习成本 | 独立操作逻辑，迁移成本高 | N/A |
| **性价比** | Graviton ARM 实例，低至 ~80 CNY/月 | 价格各异 | 取决于自有硬件 |
| **可靠性** | 企业级 SLA，自动化运维（CloudWatch + SSM） | 企业级 SLA | 依赖本地硬件和网络 |

> **总结**：AWS 中国区的核心优势在于——**全球级安全标准本地化落地**、**SSM 零端口暴露的安全访问方式**、**CloudFormation 一键可重复部署**，以及与 AWS 全球区一致的操作体验。如果你或你的团队已有 AWS 使用经验，这是最高效、最安全的选择。

本模板（`clawdbot-china.yaml`）专为 AWS 中国区设计，默认使用 [SiliconFlow](https://siliconflow.cn/) 作为 LLM 提供商，内置国内镜像加速（Docker 国内镜像源、npmmirror），确保部署过程流畅。

## 支持的 LLM 模型

默认使用 SiliconFlow，也兼容任何 OpenAI API 格式的服务商。以下为部署时可直接在下拉菜单中选择的模型：

### DeepSeek Pro 系列（推荐 Marketplace 订阅用户使用）

| 模型 ID | 特点 | 定价 |
|---------|------|------|
| `Pro/deepseek-ai/DeepSeek-V3`（默认） | 综合能力强，性价比高 | 输入 ¥2/M tokens，输出 ¥8/M tokens |
| `Pro/deepseek-ai/DeepSeek-R1` | 推理能力突出 | 输入 ¥4/M tokens，输出 ¥16/M tokens |
| `Pro/deepseek-ai/DeepSeek-V3.2` | DeepSeek 最新版本 | 参见 SiliconFlow 官网 |
| `Pro/deepseek-ai/DeepSeek-V3.1-Terminus` | 终端优化版 | 参见 SiliconFlow 官网 |

### DeepSeek 标准系列（免费额度，较低速率限制）

| 模型 ID | 特点 |
|---------|------|
| `deepseek-ai/DeepSeek-V3` | 综合能力强 |
| `deepseek-ai/DeepSeek-R1` | 推理能力突出 |
| `deepseek-ai/DeepSeek-V3.2` | 最新版本 |

### Qwen 系列（阿里千问）

| 模型 ID | 特点 |
|---------|------|
| `Qwen/Qwen3-32B` | Qwen3 大模型，中文能力强 |
| `Qwen/Qwen3-14B` | Qwen3 中等规模 |
| `Qwen/Qwen3-8B` | Qwen3 轻量级 |
| `Qwen/Qwen2.5-72B-Instruct` | Qwen2.5 大模型 |

### 其他模型

| 模型 ID | 提供商 | 特点 |
|---------|--------|------|
| `Pro/zai-org/GLM-4.7` | 智谱 AI | GLM 最新版本 |
| `zai-org/GLM-4.6` | 智谱 AI | GLM 通用版 |
| `tencent/Hunyuan-A13B-Instruct` | 腾讯 | 混元大模型 |

### 经济型蒸馏模型

| 模型 ID | 定价 |
|---------|------|
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` | ¥1.26/M tokens |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` | ¥0.7/M tokens |

> **完整模型列表**：https://docs.siliconflow.cn/cn/userguide/models
>
> 你也可以使用任何 OpenAI 兼容 API（如智谱 AI、百川、Moonshot 等），修改模板中的 `LLMApiBaseUrl` 和 `LLMApiKey` 即可。

## 支持的区域

| 区域 | 区域代码 | 运营商 |
|------|----------|--------|
| 北京 | cn-north-1 | 光环新网 |
| 宁夏 | cn-northwest-1 | 西云数据 |

## 前置条件

### 1. AWS 中国区账号

中国区账号需要单独申请：[AWS 中国区注册](https://www.amazonaws.cn/sign-up/)

### 2. 安装 AWS CLI

```bash
# macOS
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /

# Linux (x86_64)
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip && sudo ./aws/install

# Linux (ARM)
curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"
unzip awscliv2.zip && sudo ./aws/install
```

### 3. 配置中国区 AWS CLI

```bash
aws configure --profile china
# AWS Access Key ID: <你的中国区 Access Key>
# AWS Secret Access Key: <你的中国区 Secret Key>
# Default region name: cn-north-1
# Default output format: json
```

验证配置：
```bash
aws --profile china --region cn-north-1 sts get-caller-identity
```

### 4. 安装 SSM Session Manager 插件（必须）

> **重要**：SSM Session Manager 插件是连接 OpenClaw 实例的必要组件。如果未安装，运行端口转发命令时会报错：`SessionManagerPlugin is not found`。请务必在**本地电脑**上安装此插件。

SSM 是访问 EC2 实例的推荐方式（无需开放 SSH 端口，无需公网 IP）。

**macOS (Apple Silicon / M1/M2/M3/M4):**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac_arm64/session-manager-plugin.pkg" -o "session-manager-plugin.pkg"
sudo installer -pkg session-manager-plugin.pkg -target /
```

**macOS (Intel):**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac/session-manager-plugin.pkg" -o "session-manager-plugin.pkg"
sudo installer -pkg session-manager-plugin.pkg -target /
```

**macOS (Homebrew):**
```bash
brew install --cask session-manager-plugin
```

**Linux (Debian/Ubuntu 64-bit):**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
sudo dpkg -i session-manager-plugin.deb
```

**Linux (RHEL/CentOS/Amazon Linux):**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/64bit/session-manager-plugin.rpm" -o "session-manager-plugin.rpm"
sudo yum install -y session-manager-plugin.rpm
```

**Windows:**

下载安装包：https://s3.amazonaws.com/session-manager-downloads/plugin/latest/windows/SessionManagerPluginSetup.exe

或参考文档：https://docs.amazonaws.cn/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html

**验证安装：**
```bash
session-manager-plugin
# 应输出版本信息，如 "The Session Manager plugin was installed successfully."
```

> **常见问题**：如果安装后仍报 `SessionManagerPlugin is not found`，请确认插件路径已添加到系统 PATH 中，或重启终端后重试。

### 5. 获取 LLM API Key（通过 AWS Marketplace 订阅 SiliconFlow）

推荐通过 AWS Marketplace 订阅 SiliconFlow，享受以下优势：
- 只需亚马逊云科技中国区账户，开箱即用
- 先用后付，按需计费
- 与亚马逊云科技账单集成，按月出账
- 供应商为西云数据，无需另加供应商

#### 步骤 1：访问 Marketplace 产品页面

登录 AWS 中国区账户后，访问 SiliconCloud 产品页面：

https://awsmarketplace.amazonaws.cn/marketplace/pp/prodview-65lo53ldx6wda

点击 **继续订阅**，阅读最终用户协议后完成订阅。

![Marketplace 订阅页面](images/china-marketplace-subscribe.png)

![点击继续订阅](images/china-marketplace-continue.png)

#### 步骤 2：跳转到 SiliconCloud 平台

完成订阅后，点击右上角 **设置您的账户**，即可跳转至 SiliconCloud MaaS 平台。

> 你也可以在 AWS 控制台内搜索 "Marketplace" 对产品进行管理。

![设置账户](images/china-marketplace-setup-account.png)

![Marketplace 管理](images/china-marketplace-manage.png)

#### 步骤 3：注册 SiliconCloud 账户

在 SiliconCloud 控制台，输入手机号或邮箱与验证码进行注册登录。

> 通过 AWS Marketplace 订阅的用户无需进行法人信息审核（已在 AWS 侧完成），注册完账户后即可使用 Pro 版本模型。

![SiliconCloud 注册](images/china-siliconflow-register.png)

#### 步骤 4：获取 API Key

登录后，在左侧菜单点击 **API 密钥**，即可创建和获取 API Key（以 `sk-` 开头）。

![获取 API 密钥](images/china-siliconflow-apikey.png)

> 详细的 API 使用文档：https://docs.siliconflow.cn/cn/userguide/introduction

![API 文档](images/china-siliconflow-docs.png)

#### 步骤 5（可选）：在 Playground 中测试模型

在 SiliconCloud 控制台的 **Playground** 中，可通过下拉菜单选择模型并直接测试效果。

![Playground 选择模型](images/china-siliconflow-playground.png)

![模型详情与定价](images/china-siliconflow-model-detail.png)

#### 步骤 6（可选）：查看账单

点击 **费用账单** 可查看具体的模型及 Token 消耗量。费用会自动集成到 AWS 账单中。

![费用账单](images/china-siliconflow-billing.png)

---

**不使用 Marketplace？** 也可直接前往 [SiliconFlow 官网](https://cloud.siliconflow.cn/) 注册并获取 API Key。

## 快速开始

### 一键部署（控制台 - 推荐，约 10 分钟）

1. 下载 CloudFormation 模板文件：[**clawdbot-china.yaml**](https://raw.githubusercontent.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/main/clawdbot-china.yaml)（右键 → 另存为）
2. 登录 [AWS 中国区控制台](https://console.amazonaws.cn/)
3. 进入 CloudFormation 服务
4. 点击 **创建堆栈** → **上传模板文件** → 选择下载的 `clawdbot-china.yaml`
5. 填写参数：

| 参数 | 说明 | 建议值 |
|------|------|--------|
| **Stack name** | 堆栈名称 | `openclaw-china` |
| **LLM API Key** | SiliconFlow API 密钥 | `sk-xxxxxxxx`（必填） |
| **LLM API Base URL** | API 地址 | 默认即可（SiliconFlow） |
| **LLM Model ID** | 模型名称（下拉选择） | 默认 `Pro/deepseek-ai/DeepSeek-V3` |
| **EC2 Instance Type** | 实例类型 | `c6g.large`（推荐 ARM） |
| **Use Existing VPC?** | 使用已有 VPC | `false`（新建）或 `true`（使用已有） |
| **Existing VPC ID** | 已有 VPC（下拉选择） | 使用已有 VPC 时选择 |
| **Existing Public Subnet ID** | 已有公有子网（下拉选择） | 使用已有 VPC 时选择 |
| **Existing Private Subnet ID** | 已有私有子网（下拉选择） | 使用已有 VPC 时选择 |
| **EC2 Key Pair Name** | SSH 密钥对（可选） | 输入 `none` 跳过 |
| **Create S3 Bucket?** | 创建 S3 存储桶 | `true`（推荐） |

> **VPC/子网下拉选择器**：当 `Use Existing VPC?` 设为 `true` 时，VPC 和子网参数会以下拉菜单形式展示你账号中的已有资源，方便直接选择。设为 `false` 时（新建 VPC），随意选择一个值即可——它会被忽略。

6. 勾选 **我确认，AWS CloudFormation 可能会创建 IAM 资源**
7. 点击 **创建堆栈**
8. 等待约 10 分钟，状态变为 `CREATE_COMPLETE`

### CLI 部署

#### 新建 VPC 部署（推荐新手）

```bash
aws --profile china --region cn-north-1 cloudformation create-stack \
  --stack-name openclaw-china \
  --template-body file://clawdbot-china.yaml \
  --parameters \
    ParameterKey=LLMApiKey,ParameterValue=sk-你的API密钥 \
  --capabilities CAPABILITY_IAM

# 等待完成（约 10 分钟）
aws --profile china --region cn-north-1 cloudformation wait stack-create-complete \
  --stack-name openclaw-china
```

#### 使用已有 VPC 部署

```bash
aws --profile china --region cn-north-1 cloudformation create-stack \
  --stack-name openclaw-china \
  --template-body file://clawdbot-china.yaml \
  --parameters \
    ParameterKey=LLMApiKey,ParameterValue=sk-你的API密钥 \
    ParameterKey=UseExistingVPC,ParameterValue=true \
    ParameterKey=ExistingVPCId,ParameterValue=vpc-xxxxxxxxx \
    ParameterKey=ExistingPublicSubnetId,ParameterValue=subnet-xxxxxxxxx \
    ParameterKey=ExistingPrivateSubnetId,ParameterValue=subnet-xxxxxxxxx \
  --capabilities CAPABILITY_IAM

aws --profile china --region cn-north-1 cloudformation wait stack-create-complete \
  --stack-name openclaw-china
```

> **使用已有 VPC 的要求**：
> - VPC 必须启用 **DNS 主机名**和 **DNS 解析**
> - 公有子网必须启用**自动分配公有 IP**
> - 公有子网必须有通往 Internet Gateway 的路由

## 部署后访问

### 第 1 步：查看 CloudFormation 输出

```bash
aws --profile china --region cn-north-1 cloudformation describe-stacks \
  --stack-name openclaw-china \
  --query 'Stacks[0].Outputs' --output table
```

你会看到 5 个关键输出：
- `Step1InstallSSMPlugin` — SSM 插件安装链接
- `Step2PortForwarding` — 端口转发命令
- `Step3GetToken` — 从 SSM Parameter Store 获取 token 的命令
- `Step4AccessURL` — 浏览器访问 URL
- `Step5StartChatting` — 开始使用

### 第 2 步：启动端口转发

复制 `Step2PortForwarding` 输出的命令，在**本地电脑**运行：

```bash
aws --profile china ssm start-session \
  --target i-xxxxxxxxxxxxxxxxx \
  --region cn-north-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["18789"],"localPortNumber":["18789"]}'
```

> 保持这个终端窗口打开！关闭会断开连接。

### 第 3 步：获取 Token

运行 `Step3GetToken` 输出的命令，从 SSM Parameter Store 获取 token：

```bash
aws --profile china ssm get-parameter \
  --name /openclaw/openclaw-china/gateway-token \
  --with-decryption \
  --query Parameter.Value \
  --output text \
  --region cn-north-1
```

### 第 4 步：打开浏览器

在浏览器中打开（用上一步获取的 token 替换 `<token>`）：

```
http://localhost:18789/?token=<token>
```

### 第 5 步：连接消息平台

在 Web UI 中连接 WhatsApp、Telegram、Discord 或 Slack：

| 平台 | 操作 |
|------|------|
| **WhatsApp** | Channels → Add → WhatsApp → 手机扫码 |
| **Telegram** | 先用 @BotFather 创建 Bot → 获取 token → 配置 |
| **Discord** | Developer Portal 创建 Bot → 获取 token → 配置 |
| **Slack** | Slack API 创建 App → Bot Token → 配置 |

详细指南：https://docs.openclaw.ai/channels/

## 参数详解

### LLM 配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `LLMApiBaseUrl` | String | `https://api.siliconflow.cn/v1` | OpenAI 兼容 API 地址 |
| `LLMModel` | String | `Pro/deepseek-ai/DeepSeek-V3` | 模型 ID（下拉选择） |
| `LLMApiKey` | String | （必填） | API 密钥，存储在 SSM Parameter Store |

### 计算配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `InstanceType` | String | `c6g.large` | EC2 实例类型 |
| `VolumeSize` | Number | `30` | EBS 卷大小（GB），范围 20-500 |
| `VolumeType` | String | `gp3` | EBS 卷类型 |
| `KeyPairName` | String | `none` | SSH 密钥对，`none` 表示不使用 |

**实例类型推荐：**

| 类型 | 架构 | 月费（约） | 适用场景 |
|------|------|------------|----------|
| `t4g.small` | ARM (Graviton) | ~80 CNY | 个人轻度使用 |
| `t4g.medium` | ARM (Graviton) | ~160 CNY | 个人日常使用 |
| `c6g.large`（默认） | ARM (Graviton) | ~230 CNY | 推荐，稳定性能 |
| `c7g.large` | ARM (Graviton) | ~250 CNY | 最新 Graviton |
| `t3.medium` | x86 | ~200 CNY | x86 兼容需求 |
| `c5.large` | x86 | ~280 CNY | x86 稳定性能 |

> 推荐使用 Graviton（ARM）实例：性价比比 x86 高 20-40%。

### 网络配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `UseExistingVPC` | String | `false` | 是否使用已有 VPC |
| `ExistingVPCId` | AWS::EC2::VPC::Id | （控制台下拉选择） | 已有 VPC ID |
| `ExistingPublicSubnetId` | AWS::EC2::Subnet::Id | （控制台下拉选择） | 已有公有子网 ID |
| `ExistingPrivateSubnetId` | AWS::EC2::Subnet::Id | （控制台下拉选择） | 已有私有子网 ID |
| `VpcCidr` | String | `10.0.0.0/16` | 新建 VPC 的 CIDR |
| `CreateVPCEndpoints` | String | `true` | 创建 SSM VPC 端点 |
| `AllowedSSHCIDR` | String | （空） | SSH 访问 CIDR，留空不开放 |

### S3 配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `CreateS3Bucket` | String | `true` | 创建 S3 存储桶 |
| `InstallS3FilesSkill` | String | `true` | 安装 S3 Files Skill |

## 架构

```
你的电脑
    │ AWS CLI + SSM Plugin
    ▼
SSM Service（AWS 中国区内网）
    │ 端口转发（localhost:18789）
    ▼
EC2 实例（Ubuntu 24.04, Graviton ARM）
    ├── openclaw（Node.js 应用）
    ├── Gateway Web UI（端口 18789，仅本地）
    ├── Docker（沙箱隔离）
    └── SSM Agent（安全远程访问）
    │
    ▼
SiliconFlow API（或其他 OpenAI 兼容 API）
    ├── DeepSeek-V3（默认）
    ├── DeepSeek-R1
    ├── Qwen2.5-72B
    └── 其他模型...
```

**创建的 AWS 资源：**

| 类别 | 资源 |
|------|------|
| 网络 | VPC、Internet Gateway、公有/私有子网、路由表（新建 VPC 时） |
| VPC 端点 | SSM、SSM Messages、EC2 Messages（可选） |
| IAM | 角色（SSM + CloudWatch + SSM 参数存储）、实例配置文件 |
| 安全 | EC2 安全组、VPC 端点安全组（可选） |
| 计算 | EC2 实例（Ubuntu 24.04） |
| 存储 | S3 存储桶（可选，用于 Files Skill） |
| 编排 | CloudFormation WaitCondition（20 分钟超时） |

## 月度成本估算（CNY）

| 服务 | 配置 | 月费（约） |
|------|------|------------|
| EC2 (c6g.large, Graviton) | 2 vCPU, 4GB RAM | 150-230 CNY |
| EBS (gp3) | 30GB | ~18 CNY |
| VPC 端点（可选） | 3 个端点 | ~150 CNY |
| S3 存储桶 | < 1GB 通常 | < 1 CNY |
| **基础设施小计** | | **170-400 CNY** |
| LLM API（SiliconFlow） | 按量付费 | 视使用量而定 |

### 成本优化建议

- **关闭 VPC 端点**：设 `CreateVPCEndpoints=false` 节省 ~150 CNY/月（SSM 仍可通过公网使用）
- **使用 Graviton 实例**：比 x86 便宜 20-40%
- **使用小实例**：`t4g.small`（~80 CNY/月）足够个人使用
- **选择经济模型**：DeepSeek-V3 性价比最高

## 常用操作

### 查看安装日志

```bash
# 通过 SSM 连接实例
aws --profile china ssm start-session --target i-xxxxxxxxx --region cn-north-1

# 切换到 ubuntu 用户
sudo su - ubuntu

# 查看安装日志
tail -100 /var/log/openclaw-setup.log

# 查看安装状态
cat ~/.openclaw/setup_status.txt
```

### 查看 Gateway 服务状态

```bash
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status openclaw-gateway
```

### 重启 Gateway

```bash
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway
```

### 切换模型

```bash
# 编辑配置
nano ~/.openclaw/openclaw.json

# 修改 models.providers.maas.models[0].id 为新模型 ID
# 修改 agents.defaults.model.primary 为 "maas/新模型ID"

# 重启服务
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway
```

### 切换 LLM 提供商

编辑 `~/.openclaw/openclaw.json`，修改：
- `models.providers.maas.baseUrl` — 新的 API 地址
- `models.providers.maas.apiKey` — 新的 API 密钥
- `models.providers.maas.models[0].id` — 新的模型 ID

### 更新 openclaw

```bash
sudo su - ubuntu
npm update -g openclaw
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway
openclaw --version
```

### 更新堆栈

```bash
aws --profile china --region cn-north-1 cloudformation update-stack \
  --stack-name openclaw-china \
  --template-body file://clawdbot-china.yaml \
  --parameters \
    ParameterKey=LLMApiKey,UsePreviousValue=true \
    ParameterKey=UseExistingVPC,UsePreviousValue=true \
    ParameterKey=ExistingVPCId,UsePreviousValue=true \
    ParameterKey=ExistingPublicSubnetId,UsePreviousValue=true \
    ParameterKey=ExistingPrivateSubnetId,UsePreviousValue=true \
  --capabilities CAPABILITY_IAM
```

## 故障排查

### WaitCondition timed out

安装过程超时（20 分钟）。常见原因：

1. **网络不通**：子网没有 Internet Gateway 或路由配置错误
   ```bash
   # 检查实例是否能访问外网
   curl -s --max-time 5 https://api.siliconflow.cn/v1/models
   ```

2. **包下载失败**：国内镜像不可用
   ```bash
   # 查看安装日志中的错误
   sudo grep -i "error\|fail" /var/log/openclaw-setup.log
   ```

3. **实例启动失败**：检查 EC2 控制台系统日志

### SSM 无法连接

```bash
# 确认实例正在运行
aws --profile china --region cn-north-1 ec2 describe-instances \
  --instance-ids i-xxxxxxxxx \
  --query 'Reservations[0].Instances[0].State.Name'

# 确认 SSM Agent 已注册
aws --profile china --region cn-north-1 ssm describe-instance-information \
  --filters "Key=InstanceIds,Values=i-xxxxxxxxx"
```

如果 SSM Agent 未注册：
- 检查实例 IAM 角色是否包含 `AmazonSSMManagedInstanceCore` 策略
- 检查 VPC 端点或公网访问是否可用

### LLM API 错误

```bash
# 测试 API 连接
curl -s https://api.siliconflow.cn/v1/models \
  -H "Authorization: Bearer sk-你的密钥" | head -20

# 测试模型调用
curl -s https://api.siliconflow.cn/v1/chat/completions \
  -H "Authorization: Bearer sk-你的密钥" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-ai/DeepSeek-V3","messages":[{"role":"user","content":"你好"}],"max_tokens":50}'
```

### Gateway 未启动

```bash
# 检查端口是否监听
ss -tlnp | grep 18789

# 查看 Gateway 日志
XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u openclaw-gateway --no-pager -n 50

# 手动启动
XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus openclaw gateway
```

## 清理资源

### 删除堆栈

```bash
aws --profile china --region cn-north-1 cloudformation delete-stack \
  --stack-name openclaw-china

aws --profile china --region cn-north-1 cloudformation wait stack-delete-complete \
  --stack-name openclaw-china
```

> 删除堆栈会移除所有创建的资源（VPC、EC2、IAM 角色、S3 存储桶等）。如果使用的是已有 VPC，VPC 和子网不会被删除。

## 安全特性

- **SSM Session Manager**：无需公网 SSH 端口，自动会话日志
- **API 密钥安全存储**：LLM API 密钥存储在 SSM Parameter Store（SecureString 加密），配置文件权限 600
- **Gateway Token**：部署时自动生成（`openssl rand -hex 24`），仅存储在 SSM Parameter Store（不写入磁盘文件或 CloudFormation 输出）
- **供应链保护**：NVM 采用下载后执行模式（非 `curl | bash`），Docker 通过 GPG 签名的 apt 源安装
- **VPC 端点**：SSM 流量通过 AWS 内网，不经过公网
- **最小权限 IAM**：仅授予 SSM、CloudWatch 和 SSM 参数存储权限
- **S3 存储桶安全**：阻止所有公共访问、启用版本控制、AES256 加密

## 与全球区版本对比

| | 全球区（Bedrock） | 中国区（SiliconFlow） |
|--|-------------------|----------------------|
| **LLM 服务** | Amazon Bedrock（内置） | SiliconFlow 等第三方 API |
| **认证方式** | IAM Role（无密钥） | API Key（SSM 加密存储） |
| **可选模型** | Nova、Claude、DeepSeek、Llama、Kimi | DeepSeek、Qwen 等 |
| **模板文件** | `clawdbot-bedrock.yaml` | `clawdbot-china.yaml` |
| **部署区域** | us-east-1、us-west-2、eu-west-1、ap-northeast-1 | cn-north-1、cn-northwest-1 |
| **网络加速** | 不需要 | Docker 国内镜像源、npmmirror |
| **月费（基础设施）** | ~$84 (USD) | ~170-400 CNY |

## 资源链接

- [openclaw 文档](https://docs.openclaw.ai/)
- [openclaw GitHub](https://github.com/openclaw/openclaw)
- [SiliconFlow 控制台](https://cloud.siliconflow.cn/)
- [AWS 中国区文档](https://docs.amazonaws.cn/)
- [SSM Session Manager（中国区）](https://docs.amazonaws.cn/systems-manager/latest/userguide/session-manager.html)
- [本项目 GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)

---

**Built by builder + Claude** 🦞

在你控制的 AWS 中国区基础设施上部署个人 AI 助手 🦞
