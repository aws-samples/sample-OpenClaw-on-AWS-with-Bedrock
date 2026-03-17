# OpenClaw 企业多租户平台 — 进度记录

日期: 2026-03-17

---

## 整体架构

### 一句话

EC2 上常驻一只龙虾 (OpenClaw Gateway) 作为 IM 路由器，管理渠道连接和 Web UI；员工的每次消息经 Bedrock H2 Proxy 拦截后，由 Tenant Router 派生 tenant_id，弹性拉起 Serverless 的 Firecracker microVM (Bedrock AgentCore Runtime)，在隔离环境中运行原生 OpenClaw CLI，执行完毕自动释放。OpenClaw 代码零修改。

### 架构图

![Architecture](images/architecture-multitenant.drawio.png)

### 核心流程

```
员工 (WhatsApp/Telegram/Discord/Slack)
  │
  ▼
EC2 Gateway (常驻)
  ├── OpenClaw Gateway (Node.js, port 18789) — 渠道长连接、Web UI
  │     └── 调 Bedrock Converse API (AWS SDK, HTTP/2)
  │           │
  │           │ AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://localhost:8091
  │           ▼
  ├── Bedrock H2 Proxy (Node.js, port 8091) — 拦截 HTTP/2 请求
  │     └── 提取用户消息 + channel/sender → 转发到 Tenant Router
  │
  └── Tenant Router (Python, port 8090) — 派生 tenant_id、调 AgentCore
        │
        │  invoke_agent_runtime(runtimeSessionId=tenant_id, payload=message)
        ▼
AgentCore Runtime (Serverless)
  └── Firecracker microVM (每租户隔离)
        │
        │  entrypoint.sh 启动:
        │  1. 写入 openclaw.json (Bedrock provider 配置)
        │  2. 启动 server.py (立即响应 /ping health check)
        │  3. 从 S3 拉取租户 workspace (SOUL.md, MEMORY.md, Skills)
        │  4. watchdog 每 60s sync workspace 回 S3
        │
        │  请求到达 /invocations:
        │  5. server.py 构建 Plan A system prompt (权限约束)
        │  6. 调用 openclaw agent --session-id <tenant_id> --message <text> --json
        │  7. OpenClaw CLI 调 Bedrock 推理 (子进程，~10s)
        │  8. server.py 用 JSONDecoder.raw_decode 解析响应
        │  9. Plan E 审计 (扫描响应中的违规工具调用)
        │  10. 返回 JSON 响应
        │
        │  关停:
        │  SIGTERM → flush workspace 到 S3 → 释放
        ▼
响应原路返回 → H2 Proxy → Gateway → IM channel → 员工收到回复
```

### 零入侵设计

OpenClaw 在 microVM 里原生运行，不知道自己在企业平台上。所有管控通过外层实现:

| 管控层 | 怎么做 | 用的 OpenClaw 接口 |
|---|---|---|
| entrypoint.sh | S3 拉取/写回 workspace | OpenClaw 只看到本地文件系统 |
| server.py | Plan A 权限注入 + Plan E 审计 | openclaw agent CLI (子进程调用) |
| openclaw.json | 配置 Bedrock 模型 | ~/.openclaw/openclaw.json (标准配置) |
| SOUL.md | 人格/规则/行为边界 | OpenClaw 原生读取 workspace/SOUL.md |
| ECR 镜像 | 版本管理 | npm install -g openclaw@latest |

升级 OpenClaw: rebuild 镜像 push ECR，所有租户下次请求自动用新版本。

### AWS 服务选型

| 数据类型 | AWS 服务 | 理由 |
|---|---|---|
| 灵魂/权限配置 | SSM Parameter Store | 免费、加密、热更新 |
| 租户 workspace | S3 | 便宜、版本控制、增量 sync |
| 容器镜像 | ECR | 版本管理、ARM64 支持 |
| 模型推理 | Amazon Bedrock | IAM 认证、10+ 模型、无 API Key |
| 审计日志 | CloudWatch Logs | 结构化 JSON、按 tenant_id 过滤 |
| API 审计 | CloudTrail | 每次 Bedrock 调用自动记录 |
| 对话历史 (规划) | DynamoDB | 多轮上下文、TTL 自动过期 |
| 资源隔离 | IAM | 每 microVM 最小权限、S3 路径隔离 |

---

## 核心代码文件

### agent-container/entrypoint.sh — microVM 入口

容器启动时的第一个脚本。管理完整生命周期:

```
Phase 0:   写入 openclaw.json (sed 替换 AWS_REGION/BEDROCK_MODEL_ID 环境变量)
Phase 1:   立即启动 server.py → 响应 /ping health check (AgentCore 要求秒级响应)
Phase 2:   从 S3 拉取租户 workspace (SOUL.md, MEMORY.md, memory/*.md, skills)
           如果新租户 → 从 SSM 读角色模板 → 从 S3 拉模板初始化 SOUL.md
Phase 3:   启动 watchdog 后台线程，每 60s aws s3 sync 写回
Phase 4:   trap SIGTERM → 杀 watchdog → 杀 server.py → 最终 sync → exit
```

### agent-container/server.py — HTTP wrapper (Plan A + Plan E)

AgentCore 调用的 HTTP 端点。两个关键路径:

- `GET /ping` → 返回 `{"status":"Healthy"}`，AgentCore 用这个判断容器是否存活
- `POST /invocations` → 收到请求后:
  1. 从 headers/payload 提取 tenant_id (优先 AgentCore session header)
  2. 从 SSM 读权限 profile → 构建 Plan A system prompt
  3. 调用 `openclaw agent --session-id <tenant_id> --message <text> --json` CLI 子进程
  4. 用 `JSONDecoder.raw_decode` 解析 openclaw 的 JSON 输出
  5. Plan E: 扫描响应文本，检测是否使用了被禁止的工具
  6. 记录审计日志到 CloudWatch

容器内以 root 运行时直接调用 `/usr/bin/openclaw`；EC2 上以 root 运行时用 `sudo -u ubuntu env ...` 切换用户。

### agent-container/openclaw.json — OpenClaw 配置模板

Bedrock provider 配置，使用 `${AWS_REGION}` 和 `${BEDROCK_MODEL_ID}` 环境变量替换。server.py 启动时写入 `~/.openclaw/openclaw.json`。

### agent-container/Dockerfile — 容器镜像

```
Python 3.12-slim
  + AWS CLI v2 (架构感知: aarch64/x86_64)
  + Node.js 22 + git
  + OpenClaw (npm install -g openclaw@latest)
  + Python 依赖 (boto3, requests)
  + entrypoint.sh + server.py + openclaw.json + permissions.py + safety.py
  + OpenClaw templates symlink
ENTRYPOINT: /app/entrypoint.sh
```

### src/gateway/tenant_router.py — Gateway 到 AgentCore 的路由

EC2 上运行的 Python HTTP 服务 (port 8090):

- `derive_tenant_id(channel, user_id)` → 生成 33+ 字符的 tenant_id (AgentCore 要求)
- `invoke_agent_runtime(tenant_id, message)` → 调用 `bedrock-agentcore` SDK 的 `invoke_agent_runtime`
- 自动从 STS 获取 account_id 构造 Runtime ARN
- 支持 demo 模式 (直连本地 Agent Container) 和生产模式 (调 AgentCore API)

### clawdbot-bedrock-agentcore-multitenancy.yaml — CloudFormation

一个 YAML 部署全部基础设施:
- VPC + Subnet + Security Group
- EC2 (Graviton ARM) + IAM Role (Bedrock + SSM + ECR + S3 + AgentCore)
- ECR Repository
- S3 Bucket (openclaw-tenants-{AccountId})，版本控制开启
- SSM Parameters (gateway token, 默认权限 profile)
- CloudWatch Log Group

### deploy-multitenancy.sh — 一键部署

5 步: CloudFormation → S3 模板上传 → Docker build+push → AgentCore Runtime 创建 → SSM 存储 Runtime ID

### agent-container/build-on-ec2.sh — 远程构建

当本地 Docker 不可用时 (公司安全基线)，上传代码到 S3，在 EC2 上 build + push ECR。

---

## 已完成

### 1. 设计定稿

- 架构设计: EC2 Gateway (常驻龙虾) + AgentCore Runtime (按需 microVM) + S3 workspace sync
- 零入侵原则: OpenClaw 代码一行不改，所有管控在外层完成
- 文件持久化: SOUL.md/MEMORY.md/Skills 存 S3，每次 microVM 启动拉取、运行中 watchdog sync、关停时 flush
- 权限执法: Plan A (system prompt 注入) + Plan E (响应审计) + IAM (AWS 资源隔离)
- 审批流: Auth Agent 独立会话，30 分钟超时自动拒绝
- Cron: Gateway 龙虾集中调度，到点拉起 microVM 执行

### 2. 基础设施部署 (us-east-1)

| 资源 | 状态 | 标识 |
|---|---|---|
| CloudFormation stack | CREATE_COMPLETE | openclaw-multitenancy |
| EC2 Gateway | 运行中 | i-0aa07bd9a04fa2255 |
| ECR 镜像仓库 | 已创建 | openclaw-multitenancy-multitenancy-agent |
| S3 租户桶 | 已创建 | openclaw-tenants-263168716248 |
| AgentCore Runtime | READY | openclaw_multitenancy_runtime-olT3WX54rJ |
| SOUL.md 模板 | 已上传 | _shared/templates/{default,intern,engineer}.md |
| Docker 镜像 | 已 push | 含 AWS CLI + Node.js 22 + OpenClaw + entrypoint.sh |
| Tenant Router | 运行中 | EC2 port 8090 |
| OpenClaw Gateway | 运行中 | EC2 port 18789 |

### 3. 链路验证

| 链路 | 状态 | 备注 |
|---|---|---|
| IM → EC2 Gateway → Bedrock → 回复 | ✅ 跑通 | 单用户模式，生产可用 |
| Tenant Router → AgentCore invoke | ✅ 跑通 | tenant_id 正确派生 (33+ 字符) |
| AgentCore → Firecracker microVM 启动 | ✅ 跑通 | 容器成功拉起 |
| entrypoint.sh → S3 pull workspace | ✅ 跑通 | SOUL.md 从模板初始化 |
| server.py → /ping health check | ✅ 跑通 | 返回 {"status":"Healthy"} |
| server.py → /invocations → OpenClaw CLI → Bedrock | ✅ 跑通 | 返回 AI 响应，Nova 2 Lite，~12s |
| OpenClaw 容器内完整运行 | ✅ 跑通 | openclaw agent CLI 子进程调用，无需 gateway |
| Tenant Router → AgentCore → microVM → Bedrock → 响应 | ✅ 跑通 | E2E 33s (含冷启动)，2026-03-16 验证 |
| IM → Tenant Router → AgentCore → 回复 | ✅ 跑通 | 通过 H2 Proxy 拦截 Bedrock 请求，2026-03-16 验证 |

### 4. 文档和 Demo

| 产出 | 位置 |
|---|---|
| 两页纸方案文档 | OpenClaw-企业多租户方案一页纸.md |
| 三项目对比 | AgentCore-OpenClaw-对比.md |
| 架构图 (Draw.io) | images/architecture-multitenant.drawio.png |
| 时序图 (Mermaid) | images/sequence-diagrams.md (5 张) |
| Admin Console (CloudFront) | https://d2mv4530orbo0c.cloudfront.net |
| Admin Console (本地) | python3 demo/console.py → localhost:8099 |
| 部署脚本 | deploy-multitenancy.sh |
| 静态站构建 | demo/build_static.py |
| 静态站 CFN | demo/deploy-static-site.yaml |

---

## 核心卡点

### ~~卡点 1: OpenClaw 容器内冷启动超时~~ ✅ 已解决 (2026-03-16)

**解法**: 不再启动 OpenClaw gateway 进程。server.py 改为直接调用 `openclaw agent --session-id <tenant_id> --message <text> --json` CLI 子进程。每次 /invocations 请求启动一个 openclaw 进程，执行完毕自动退出。无需等待 gateway 就绪。

关键修复:
- server.py: HTTP 代理 → CLI 子进程 (`subprocess.run`)
- JSON 解析: `json.loads` → `JSONDecoder.raw_decode` (openclaw 输出含多个 JSON 对象)
- EC2 模式: `sudo -u ubuntu env PATH=... HOME=... openclaw agent ...` (root 切换用户)
- 容器模式: 直接运行 `/usr/bin/openclaw agent ...`
- openclaw.json: 去掉 gateway 配置 (容器里不需要，且新版 OpenClaw 对 gateway.bind 格式校验严格)
- entrypoint.sh: 写入 openclaw.json 后立即启动 server.py，不运行 `openclaw doctor --fix`

### ~~卡点 2: tenant_id 传递~~ ✅ 已解决

**解法**: server.py 从 /invocations 的 HTTP headers 和 payload 中提取 tenant_id，优先级: `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header > payload `runtimeSessionId` > payload `sessionId` > payload `tenant_id` > `/tmp/tenant_id` 文件 > "unknown"。

### ~~卡点 4: AgentCore SDK 超时~~ ✅ 已解决 (2026-03-16)

**现象**: Tenant Router 通过 boto3 SDK 调用 AgentCore 时返回 500 (RuntimeClientError)，但 AWS CLI 直接调用成功。

**根因**: boto3 默认 read_timeout=60s，AgentCore 冷启动 + openclaw 执行需要 30-60s，加上网络延迟超过了默认超时。

**解法**: `_agentcore_client()` 加 `Config(read_timeout=300, connect_timeout=10, retries={"max_attempts": 0})`。

### 卡点 3: IM → 多租户链路桥接 ✅ 已解决 (2026-03-16)

**解法**: Bedrock Converse API HTTP/2 本地代理 (`bedrock_proxy_h2.js`, port 8091)。

通过 `AWS_ENDPOINT_URL_BEDROCK_RUNTIME=http://localhost:8091` 环境变量，让 Gateway OpenClaw 的 AWS SDK 把 Bedrock HTTP/2 请求发到本地 proxy。Proxy 拦截请求，提取用户消息和 channel/sender 信息，转发到 Tenant Router → AgentCore → microVM，响应包装成 Bedrock Converse API 格式返回。零入侵 OpenClaw。

关键发现:
- OpenClaw `auth: "aws-sdk"` 模式下 `baseUrl` 被忽略，AWS SDK 直接调 Bedrock endpoint
- 必须用 `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` 环境变量覆盖 endpoint
- AWS SDK 用 HTTP/2 调 Bedrock，Python `http.server` 不支持 HTTP/2，必须用 Node.js `http2.createServer()`
- `tenant_router.py`: AgentCore SDK 的 response body key 是 `response`（StreamingBody），不是 `body` 或 `payload`
- Gateway 和 Proxy 用 systemd 管理，避免 SSM RunShellScript 的 fd 阻塞问题

---

## 踩过的坑 (经验)

### AWS CLI 版本
- `bedrock-agentcore-control` 需要 AWS CLI >= 2.27
- EC2 上的 boto3 也需要升级 (`pip3 install --upgrade boto3`)
- 服务名是 `bedrock-agentcore` 不是 `bedrock-agentcore-runtime`

### AgentCore API 参数
- `--agent-runtime-name` 只允许 `[a-zA-Z][a-zA-Z0-9_]{0,47}`，不能有连字符
- `runtimeSessionId` 最少 33 字符，短 tenant_id 需要 hash 补长
- `--environment-variables` shorthand 格式是 `Key=Value,Key=Value`
- `agentRuntimeArn` 需要完整 ARN，不是 runtime ID
- Runtime 和 Endpoint 是分开创建的 (create-agent-runtime + create-agent-runtime-endpoint)

### OpenClaw 配置
- 配置文件路径: `~/.openclaw/openclaw.json`，不支持 `--config` CLI 参数
- 配置 schema 变化频繁，`auth.type`/`sessions`/`model` 等旧 key 不再支持
- 需要 `docs/reference/templates/` 目录，否则启动报错 Missing workspace template
- 启动命令是 `openclaw gateway --port 18789`，不是 `openclaw --config xxx`

### Docker 构建
- 本地 Docker Desktop 可能因公司安全基线无法使用 — 在 EC2 上 build 是替代方案
- OpenClaw npm install 需要 `git` (某些依赖从 git clone)
- AWS CLI 安装需要区分 aarch64/x86_64 架构
- `hypothesis` 不应该在生产 requirements.txt 里

### AgentCore 容器要求
- 必须监听 0.0.0.0:8080
- 必须是 ARM64 镜像
- `/ping` 必须返回 `{"status":"Healthy"}` 或 `{"status":"HealthyBusy"}`
- health check 在容器启动后几秒内就会到达，HTTP 服务器必须最先启动
- 容器如果 health check 失败会被反复重启
- AgentCore 日志在 `/aws/bedrock-agentcore/runtimes/<runtime_id>-DEFAULT`，不是自定义 log group
- boto3 SDK 调用 `invoke_agent_runtime` 需要 `read_timeout=300`，默认 60s 在冷启动时不够
- 容器内 openclaw.json 不能有 gateway 配置，新版 OpenClaw 对 `gateway.bind` 格式校验严格会导致 exit=1
- `openclaw doctor --fix` 耗时 5-10s，不能在 server.py 启动前阻塞运行
- 容器内 openclaw 输出可能包含多个 JSON 对象，必须用 `JSONDecoder.raw_decode` 解析第一个

### CloudFormation
- `AWS::EC2::KeyPair::KeyName` 类型会验证 key pair 必须存在，空值会失败 — 改用 String + Condition
- `ecr:GetAuthorizationToken` 的 Resource 必须是 `*`，不能限制到具体 repo ARN
- EC2 Role 需要 ECR push 权限 (PutImage/InitiateLayerUpload 等) 才能在 EC2 上 build

### S3 日志
- S3 访问日志不能投递到自身桶 — 会导致日志静默停止
- Athena 查不到新数据时先检查分区，再检查日志投递配置

### IM 桥接 (Bedrock H2 Proxy)
- OpenClaw `auth: "aws-sdk"` 模式下 `baseUrl` 配置被忽略，AWS SDK 直接用 AWS endpoint
- 必须用 `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` 环境变量覆盖 Bedrock endpoint
- AWS SDK for JS v3 用 HTTP/2 调 Bedrock，Python `http.server` 只支持 HTTP/1.1 → 必须用 Node.js
- OpenClaw 没有消息级别的 hook 事件（只有 `gateway:startup`, `command`, `agent:bootstrap`）
- OpenClaw WebSocket RPC (port 18792) 有 origin 校验，外部连接被拒绝
- `openclaw gateway` 是长运行进程，SSM RunShellScript 的 nohup/setsid/disown 都无法正确后台化 → 用 systemd service

---

## 下一步 (优先级排序)

1. **重启 Gateway 并验证 IM 端到端** — 重启 OpenClaw Gateway（baseUrl 指向 proxy），从 Telegram/WhatsApp 发消息验证完整链路
2. **第二个租户测试** — 验证两个不同 tenant_id 的隔离性（不同员工发消息，各自独立的 microVM）
3. **S3 workspace 写回验证** — 确认 MEMORY.md 更新后能 sync 回 S3
4. **冷启动优化** — 当前 E2E ~25s (含冷启动)，目标 < 15s
5. **Admin Console 集成** — 管理员配置员工角色、权限、查看审计日志

---

## 关键文件清单

| 文件 | 用途 |
|---|---|
| agent-container/entrypoint.sh | microVM 入口: openclaw.json 写入 + server.py 启动 + S3 sync |
| agent-container/server.py | HTTP wrapper: health check + Plan A/E + openclaw agent CLI 子进程 |
| agent-container/openclaw.json | OpenClaw 配置模板 (Bedrock provider, 无 gateway 配置) |
| agent-container/Dockerfile | 容器镜像: Python 3.12 + AWS CLI + Node.js 22 + OpenClaw |
| agent-container/build-on-ec2.sh | EC2 上远程 build Docker 镜像的脚本 |
| agent-container/templates/*.md | SOUL.md 角色模板 (default/intern/engineer) |
| src/gateway/tenant_router.py | Tenant Router: tenant_id 派生 + AgentCore invoke (port 8090) |
| src/gateway/bedrock_proxy_h2.js | Bedrock H2 Proxy: 拦截 HTTP/2 请求转发到 Tenant Router (port 8091) |
| src/gateway/bedrock_proxy.py | Bedrock HTTP/1.1 Proxy (curl 测试用，生产用 H2 版本) |
| clawdbot-bedrock-agentcore-multitenancy.yaml | CloudFormation: EC2 + ECR + S3 + SSM + IAM |
| deploy-multitenancy.sh | 一键部署脚本 |
