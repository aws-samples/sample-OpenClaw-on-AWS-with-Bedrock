# OpenClaw Enterprise — 核心代码架构图

> 整个项目 ~9,000 行核心代码 + 1,300 行 CloudFormation + 800 行部署脚本
> 两种运行模式：AgentCore (Serverless) 和 Fargate (Always-On)

---
### 0413 思考：
1- 共享OpenClaw Gateway 当初的设计考虑是简化员工的 IM 接入，而现在看造成了 H2 Proxy 和Tenant Router 严重的过度设计，更重要的是它屏蔽了 IM 端实现的权限划分功能，所以人用一个飞书机器人是不可接受的，它让 openclaw 的主动推送也失效了。我们必须让每个员工使用自己的 gateway，消息入口需要重新设计，员工的 IM 接入需要在保障可管理可流畅使用的前提下思考实现方案或者简单的旁听方案，bingjiao 说的也对（为什么 IM 接入这部分不是飞书的管理员角色去做）。
2- 第二层：Agent 执行引擎 我觉得没什么大问题， 要细致检查每个组件对 openclaw 原生代码的侵入性影响，要想清楚每个代码文件是不是真的重要， 在执行层和管理后端协作下，要做到 1）openclaw 开放的配置文件和 openclaw Soul/记忆/skill/身份 等成长文件的监管控； 2） openclaw 运行环境的监管控，网络，Role，运行时长，防火墙等；3）openclaw 行为的监管控，Guardrail/Plan A 等行为日志监管控； 4）在具备监管控的条件下，去优化监管控时效性，例如 注入拉起时效性，配置变更生效时间，S3 回写效率，S3同步等

## 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        EC2 Instance (永远运行)                          │
│                                                                         │
│  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────────────┐  │
│  │ OpenClaw Gateway │  │  H2 Proxy (Node) │  │  Admin Console (Py)   │  │
│  │  :18789          │→→│  :8091            │→→│  :8099                │  │
│  │  IM 客户端       │  │  消息总线         │  │  管理后端 + Portal     │  │
│  └─────────────────┘  └──────────────────┘  └───────────────────────┘  │
│           │                    │                        │               │
│           │            ┌──────┴──────┐                 │               │
│           │            │Tenant Router│                 │               │
│           │            │  :8090      │                 │               │
│           │            └──────┬──────┘                 │               │
└───────────┼───────────────────┼─────────────────────────┼───────────────┘
            │                   │                         │
     IM 消息入站         ┌──────┴──────┐           管理 API
            │            │             │                  │
            v            v             v                  v
┌────────────────┐ ┌──────────────────────────────────────────────────┐
│ AgentCore 模式  │ │               Fargate 模式                      │
│ (Firecracker)  │ │         (ECS Fargate + EFS)                     │
│                │ │                                                  │
│ 每次请求启动    │ │  ┌─────────┐┌──────────┐┌──────────┐┌────────┐ │
│ microVM →      │ │  │Standard ││Restricted││Engineer. ││Execut. │ │
│ 15-60min 后销毁│ │  │Nova Lite││DeepSeek  ││Sonnet4.5 ││Sonn4.6 │ │
│                │ │  │+moderate││+strict   ││no guard  ││no guard│ │
│ 冷启动 25s     │ │  └─────────┘└──────────┘└──────────┘└────────┘ │
│ 工具延迟 30s   │ │  容器永远运行，工具立即可用，EFS 持久存储        │
└────────────────┘ └──────────────────────────────────────────────────┘
                              │
                              v
                    ┌──────────────────┐
                    │  Amazon Bedrock   │
                    │  (模型推理)       │
                    └──────────────────┘
```

---

## 核心代码清单

### 第一层：消息入口（EC2 上运行）

| 文件 | 语言 | 行数 | 端口 | 主要价值 | AgentCore 模式 | Fargate 模式 |
|------|------|------|------|---------|---------------|-------------|
| **bedrock_proxy_h2.js** | Node.js | 957 | :8091 | **消息总线。** 拦截 EC2 Gateway 的 Bedrock API 调用，提取 channel + userId + message。执行 IM 绑定验证、自助配对流程、冷启动优化（cold/warming/warm 状态机）、Admin 会话旁路。 | 所有 IM 消息经过此处 → `forwardToTenantRouter()` → AgentCore | `resolveFargateEndpoint()` 检测 Fargate 模式 → `forwardToFargateContainer()` 直连容器，跳过 Tenant Router |
| **tenant_router.py** | Python | 719 | :8090 | **路由器。** 解析 emp_id（DynamoDB MAPPING# + SSM fallback），3 层路由选择（employee override → position rule → default runtime）。调用 AgentCore Runtime API。 | 核心路由层：emp_id → runtime → `invoke_agent_runtime()` | Fargate 模式被 H2 Proxy 绕过。仅 serverless 兼容模式使用。also有 Fargate tier 路由代码（`_get_fargate_tier_endpoint`）作为备选路径。 |

**关联的功能模块：**
- H2 Proxy → IM Channels（绑定验证）、Portal（自助配对）、Playground（Admin 旁路）、Security Center（routing config）
- Tenant Router → Security Center（position→runtime 映射）、Monitor（stop-session）、所有需要 force-refresh 的模块

### 第二层：Agent 执行引擎（容器内运行 — AgentCore microVM 或 Fargate）

| 文件 | 行数 | 主要价值 | 实现逻辑 |
|------|------|---------|---------|
| **entrypoint.sh** | 466 | **容器启动编排。** 决定了 Agent 从零到可用的全过程。 | 1. 解析 tenant_id → 2. EFS/S3 workspace 设置 → 3. 写 openclaw.json → 4. 同步 workspace assembly（Fargate 专用）→ 5. 注入 IM bot token → 6. 启动 Gateway(:18789) → 7. 启动 server.py(:8080) → 8. 后台 S3 sync + skill loading → 9. SSM 端点注册（Fargate 专用）→ 10. SIGTERM cleanup(EFS→S3 snapshot) |
| **server.py** | 1292 | **管控核心。** 在 OpenClaw 之上包装了所有 Admin 管控能力。 | `/invocations` 入口：config_version 检查 → Guardrail INPUT → session takeover 检查 → workspace assembly（首次）→ `openclaw agent` CLI 调用 → Guardrail OUTPUT → Plan E 审计 → DynamoDB 写入（USAGE#, AUDIT#, SESSION#, CONV#, memory）。新增 `/admin/refresh` 清缓存不杀容器。 |
| **workspace_assembler.py** | 663 | **配置注入核心。** 决定了 Agent "看到"什么。 | 合并 3 层 SOUL（Global + Position + Personal）→ 注入 Plan A 工具白名单 → 注入 IDENTITY.md → 注入 CHANNELS.md → 下载 knowledge/ 文件 → 100MB workspace 预算执行 |
| **skill_loader.py** | 377 | **技能加载。** Layer 2 S3 热加载 + Layer 3 预构建包 + API key 注入。 | 按职位权限过滤 S3 skills → 加载个人 skills（EMP#.personalSkills）→ SSM 读取 API key → 写 /tmp/skill_env.sh |
| **permissions.py** | 231 | **Plan A 权限执行。** | tenant_id → emp_id → positionId → POS#.toolAllowlist → 允许/拒绝工具调用 → 写 AUDIT# permission_denied |
| **safety.py** | 142 | **输入安全。** 防 prompt injection + memory poisoning。 | 11 个正则模式检测注入攻击。消息长度限制 32K。工具名 + 资源路径验证。 |
| **observability.py** | 137 | **结构化日志。** CloudWatch 可检索格式。 | `STRUCTURED_LOG {json}` 前缀。agent_invocation / permission_denied / approval_decision 事件。 |
| **identity.py** | 123 | **审批令牌。** 受保护工具的临时授权。 | 内存令牌存储。shell/file_write/code_execution 需要审批令牌。24h 最大 TTL，不自动续期。 |
| **memory.py** | 142 | **记忆管理。** | 会话摘要安全检查（防 memory poisoning）。（大部分记忆逻辑在 server.py 内联实现） |

**AgentCore vs Fargate 的差异：**
- 代码完全一样，跑在不同的计算平台上
- AgentCore：每次冷启动全部重新执行（entrypoint.sh → server.py → 全流程）
- Fargate：启动一次后常驻。server.py 的 `_assembled_tenants` 缓存避免重复组装。`/admin/refresh` 可按需清缓存。

### 第三层：管理后端（EC2 上的 FastAPI）

| 文件 | 行数 | 主要价值 | 关联前端页面 |
|------|------|---------|-------------|
| **main.py** | 227 | FastAPI 应用入口。JWT auth 中间件，路由注册，CORS。 | 所有页面 |
| **db.py** | 718 | DynamoDB CRUD 封装。单表设计 ORG#acme + PK/SK 模式。 | 所有页面 |
| **shared.py** | 267 | 跨模块共享：auth helpers, `bump_config_version()`, `stop_employee_session()`（Fargate-aware）, SSM client | 所有需要刷新/审计的模块 |
| **auth.py** | 100 | JWT 生成/验证。HS256，SSM 存 secret。 | Login 页 |
| **s3ops.py** | 246 | S3 文件读写封装（SOUL, KB, workspace）。 | Agent Factory, Knowledge, Security Center |

#### 业务路由器（18 个模块）

| Router 文件 | 行数 | 功能模块 | AgentCore 依赖 | Fargate 影响 |
|-------------|------|---------|---------------|-------------|
| **agents.py** | 914 | Agent CRUD, SOUL 3层编辑, Workspace, Memory, Skills 分配 | `stop_employee_session` 杀 microVM | → 调 `/admin/refresh` 清缓存 |
| **portal.py** | 843 | 员工自助：IM 配对、My Agent、导出、workspace 树 | 无直接依赖 | 不变 |
| **settings.py** | 811 | 模型配置、Agent 配置、平台日志、服务管理 | `bump_config_version` → 5min 轮询 | → 秒级传播（refresh-all） |
| **security.py** | 730 | SOUL 策略、工具白名单、Runtime 管理、**Fargate tier 管理** | Runtime CRUD 调 AgentCore API | 新增：deploy-mode toggle, tier activate/deactivate |
| **monitor.py** | 629 | Action items, 系统状态, Event stream, Session 管理, Takeover | `list_agent_runtimes` 查 AgentCore | 需增加 ECS `DescribeServices` |
| **audit.py** | 625 | 审计日志、AI 分析、Review Queue、合规统计 | 读 AUDIT#（compute 无关） | 不变 |
| **admin_always_on.py** | 537 | **ECS Fargate 生命周期**：start/stop/reload/assign/status/images | ECS API 操作 | 核心 Fargate 管理 API |
| **gateway_proxy.py** | 499 | 反向代理到 Fargate 容器的 OpenClaw Gateway UI | SSM 解析容器 IP + token | 不变（已是 Fargate 功能） |
| **admin_ai.py** | 446 | Admin AI 助手（Bedrock Converse 直调） | 无 | 不变 |
| **admin_im.py** | 443 | IM 频道管理、绑定检查、**Fargate resolve-fargate API** | OpenClaw CLI 查频道状态 | 新增：`/internal/resolve-fargate` |
| **bindings.py** | 423 | IM 绑定 CRUD、用户映射、配对审批、Approval 工作流 | SSM 双写（兼容） | 不变 |
| **org.py** | 416 | 组织管理：部门/职位/员工 CRUD、自动 provision | `stop_employee_session` | → Fargate-aware refresh |
| **usage.py** | 411 | 用量追踪、预算管理、模型成本 | 读 USAGE#（compute 无关） | 不变 |
| **playground.py** | 346 | 交互测试：Live(AgentCore/Fargate) + Simulate(Bedrock直调) | Live 调 Tenant Router | 新增：`_resolve_fargate_for_playground()` 直调容器 |
| **knowledge.py** | 150 | KB 上传、分配、搜索 | 无直接依赖 | 不变 |
| **twin.py** | 124 | Digital Twin（员工 AI 分身的访客视角） | 无直接依赖 | 不变 |
| **openclaw_cli.py** | 64 | OpenClaw CLI 路径发现 + 环境变量 | EC2 上的 openclaw 二进制 | 不变 |

### 第四层：基础设施

| 文件 | 行数 | 主要价值 |
|------|------|---------|
| **clawdbot-bedrock-agentcore-multitenancy.yaml** | 1335 | CloudFormation 模板。VPC, EC2, IAM, S3, ECR, **ECS Cluster + EFS + Task Definition + Security Groups** |
| **deploy.sh** | 768 | 一键部署脚本。8 步：验证→CFn→Docker→AgentCore Runtime→**Fargate Tiers**→S3→DynamoDB→EC2 服务 |
| **ec2-setup.sh** | 179 | EC2 服务安装。systemd 配置 4 个服务 + Gateway baseUrl 劫持 |
| **Dockerfile** | 117 | Agent 容器镜像。多阶段构建：Node.js + OpenClaw + Python + Skills |

---

## 数据流对比：AgentCore vs Fargate

### AgentCore 模式（一条消息的完整旅程）

```
1. Telegram 消息到达
2. EC2 OpenClaw Gateway (:18789) 接收 webhook
3. Gateway 构建 Bedrock Converse API 请求
4. AWS SDK 被劫持 → H2 Proxy (:8091)
5. H2 Proxy: 提取 channel/userId/message
6. H2 Proxy: checkImBinding → admin_im.py (绑定验证)
7. H2 Proxy: routeRequest() → cold/warming/warm 状态机
8. H2 Proxy: forwardToTenantRouter(:8090/route)
9. Tenant Router: _resolve_emp_id (DynamoDB MAPPING#)
10. Tenant Router: _get_runtime_id_for_tenant (3层路由)
11. Tenant Router: invoke_agent_runtime (AgentCore API)
    ── 跨越 AWS 边界 → Firecracker microVM 启动 ──
12. entrypoint.sh: S3 sync → workspace assembly → Gateway 启动(30s)
13. server.py: /invocations 接收
14. server.py: _ensure_workspace_assembled (首次6s)
15. server.py: _apply_guardrail (INPUT)
16. server.py: invoke_openclaw → openclaw agent CLI → Gateway
17. Bedrock Converse API 调用 (10-60s)
18. server.py: _apply_guardrail (OUTPUT)
19. server.py: _audit_response (Plan E)
20. server.py: 后台写 DynamoDB (USAGE#, AUDIT#, SESSION#, CONV#)
21. server.py: 后台 S3 sync (memory, HEARTBEAT)
    ── 返回 ──
22. 回复沿 21→11 原路返回
23. EC2 Gateway → Telegram 发送回复

总延迟: 25s(冷启动) + 10-60s(推理) = 35-85s
后续消息(warm): 10-60s(推理)
idle 15-60min 后 microVM 被销毁 → 下次又冷启动
```

### Fargate 模式（一条消息的完整旅程）

```
1. Telegram 消息到达
2. EC2 OpenClaw Gateway (:18789) 接收 webhook
3. Gateway 构建 Bedrock Converse API 请求
4. AWS SDK 被劫持 → H2 Proxy (:8091)
5. H2 Proxy: 提取 channel/userId/message
6. H2 Proxy: checkImBinding (不变)
7. H2 Proxy: resolveFargateEndpoint → admin_im.py /internal/resolve-fargate
8. H2 Proxy: emp→position→deployMode=fargate→tier endpoint
9. H2 Proxy: forwardToFargateContainer(endpoint, ...)
    ── VPC 内部 HTTP，无 AWS 边界 ──
10. server.py: /invocations 接收 (容器已运行，Gateway 已就绪)
11. server.py: _ensure_workspace_assembled (首次6s，后续0s — 缓存)
12. server.py: _apply_guardrail (INPUT)
13. server.py: invoke_openclaw → openclaw agent CLI → Gateway (工具立即可用)
14. Bedrock Converse API 调用 (10-60s)
15. server.py: _apply_guardrail (OUTPUT)
16. server.py: _audit_response (Plan E)
17. server.py: 后台写 DynamoDB (USAGE#, AUDIT#, SESSION#, CONV#)
18. server.py: workspace 在 EFS 上 (无需 S3 sync)
    ── 返回 ──
19. 回复沿 9→1 原路返回
20. EC2 Gateway → Telegram 发送回复

总延迟: 0s(无冷启动) + 10-60s(推理) = 10-60s
容器永远运行，工具永远可用
Admin 改配置 → bump_config_version → 秒级传播到容器
```

---

## Admin 管控能力 × 代码位置

| 管控能力 | 执行位置 | 核心代码 | AgentCore / Fargate 差异 |
|---------|---------|---------|------------------------|
| SOUL 3 层注入 | 容器内 | workspace_assembler.py | 无差异 |
| Plan A 工具白名单 | 容器内 | permissions.py + workspace_assembler.py | 无差异 |
| Guardrail (L5) | 容器内 | server.py `_apply_guardrail()` | 无差异（GUARDRAIL_ID env var） |
| IAM 权限隔离 (L3) | AWS 平台 | CloudFormation IAM Role | AgentCore: execution role / Fargate: task role |
| 调用审计 | 容器内 | server.py `_write_usage_to_dynamodb()` | 无差异 |
| Session Takeover | 容器内 | server.py `_handle_invocation()` | 无差异（读 DDB SESSION#.takeover） |
| IM 绑定验证 | EC2 | H2 Proxy `checkImBinding()` + admin_im.py | 无差异（Fargate 仍经过 H2 Proxy） |
| 配置变更传播 | EC2 + 容器 | shared.py `bump_config_version()` | AgentCore: 5min轮询 / **Fargate: 秒级 refresh-all** |
| 强制刷新 | EC2 + 容器 | shared.py `stop_employee_session()` | AgentCore: 杀 microVM / **Fargate: /admin/refresh** |
| Skills 审批 | EC2 + 容器 | agents.py + skill_loader.py | 无差异 |
| KB 管理 | EC2 + 容器 | knowledge.py + workspace_assembler.py | 无差异 |
| Memory 安全 | 容器内 | safety.py `check_memory_safety()` | 无差异 |
| 预算追踪 | 容器内 | server.py MODEL_PRICING | 无差异 |
