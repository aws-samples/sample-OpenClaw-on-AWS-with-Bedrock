# 冷启动优化设计文档

日期: 2026-03-19
状态: 设计评审

---

## 1. 现状分析

### 当前 E2E 冷启动时间分解 (~30s)

```
Phase 0: IM 消息到达 EC2 Gateway                          ~0s (常驻)
Phase 1: Gateway → AWS SDK Bedrock call (HTTP/2)           ~0.1s
Phase 2: H2 Proxy 拦截 → 提取消息 → 转发 Tenant Router    ~0.1s
Phase 3: Tenant Router → AgentCore invoke_agent_runtime    ~0.5s
Phase 4: AgentCore 调度 + ECR image pull + microVM 启动    ~10-15s  ← 不可控
Phase 5: entrypoint.sh → server.py 启动 → /ping 通过      ~2-3s
Phase 6: /invocations → openclaw agent CLI 子进程          ~8-12s   ← OpenClaw 自身
Phase 7: Bedrock 推理                                      ~2-4s    ← 模型推理
Phase 8: 响应原路返回                                      ~0.5s
                                                    总计: ~25-33s
```

### 不可控因素

| 因素 | 耗时 | 原因 |
|------|------|------|
| AgentCore microVM 调度 | ~3-5s | AWS 内部调度，无 API 可优化 |
| ECR image pull | ~5-8s | 取决于镜像大小和网络 |
| OpenClaw CLI 初始化 | ~5-8s | Node.js 模块加载 + OpenClaw 内部初始化 |
| Bedrock 推理 | ~2-4s | 模型推理时间，取决于 prompt 长度和模型 |

### 可控因素

| 因素 | 当前耗时 | 优化空间 |
|------|---------|---------|
| Docker 镜像大小 | ~1.2GB (估) | multi-stage 可减到 ~600-800MB |
| Node.js 模块加载 | ~2-3s | V8 Compile Cache 可省 ~1-2s |
| entrypoint.sh 初始化 | ~1-2s | 已经很精简 |
| S3 workspace pull | 后台非阻塞 | 已优化 |
| IPv6 DNS 超时 | 偶发 0.5-2s | 强制 IPv4 可消除 |

---

## 2. 优化方案设计

### 总体原则

1. **零侵入**: 不修改 OpenClaw 代码、配置格式、运行方式
2. **向后兼容**: 每个优化独立开关，可单独回滚
3. **渐进实施**: 按风险从低到高排序，每步验证后再进下一步
4. **不影响热路径**: 优化冷启动不能让热请求（microVM 已运行）变慢

---

### 优化 A: Multi-stage Docker Build (镜像瘦身)

**目标**: 减小镜像体积 → ECR pull 更快 → Phase 4 缩短

**改动文件**: `agent-container/Dockerfile`

**设计**:
```
Stage 1 (builder):
  - python:3.12-slim
  - 安装 curl, unzip, git, nodejs
  - 安装 AWS CLI v2
  - npm install -g openclaw@latest
  - pip install boto3 requests
  - 创建 templates symlink

Stage 2 (runtime):
  - python:3.12-slim (干净基础)
  - 只 COPY 需要的:
    - AWS CLI 二进制 (/usr/local/aws-cli, /usr/local/bin/aws)
    - Node.js 运行时 (/usr/bin/node, /usr/local/lib/node_modules)
    - OpenClaw 全局模块 + symlink
    - Python 依赖 (boto3, requests)
    - 应用代码 (/app/*)
  - 不含: git, curl, unzip, npm cache, pip cache, apt cache
```

**预期收益**: 镜像从 ~1.2GB → ~600-800MB，ECR pull 省 ~2-3s

**风险评估**:
- 低风险: 只改构建过程，运行时行为不变
- 验证点: 构建后在 EC2 上测试 `openclaw agent --help` 和 `/ping` + `/invocations`
- 回滚: 恢复原 Dockerfile 即可

**对现有架构的影响**: 无。server.py、entrypoint.sh、openclaw.json 不变。

---

### 优化 B: V8 Compile Cache (Node.js 字节码缓存)

**目标**: 预编译 OpenClaw 的 Node.js 模块 → CLI 启动更快 → Phase 6 缩短

**改动文件**: `agent-container/Dockerfile`, `agent-container/entrypoint.sh`

**设计**:

Dockerfile (builder stage):
```dockerfile
# 预热 V8 compile cache
RUN mkdir -p /app/.compile-cache && \
    NODE_COMPILE_CACHE=/app/.compile-cache \
    openclaw agent --help > /dev/null 2>&1 || true
```

entrypoint.sh (新增 2 行):
```bash
# V8 Compile Cache (Node.js 22+)
if [ -d /app/.compile-cache ]; then
    export NODE_COMPILE_CACHE=/app/.compile-cache
fi
```

**原理**: Node.js 22 的 `NODE_COMPILE_CACHE` 在首次加载模块时缓存编译后的字节码。
Docker build 时运行一次 `openclaw agent --help` 触发所有模块编译，缓存写入 `/app/.compile-cache`。
运行时设置环境变量，Node.js 直接加载字节码跳过编译。

**预期收益**: openclaw agent CLI 启动从 ~5-8s → ~3-5s，省 ~2s

**风险评估**:
- 极低风险: `NODE_COMPILE_CACHE` 是 Node.js 22 官方特性，cache miss 时自动 fallback 到正常编译
- 验证点: 对比有无 cache 的 `time openclaw agent --help` 执行时间
- 回滚: 删除 entrypoint.sh 里的 2 行 + Dockerfile 里的 cache 步骤

**对现有架构的影响**: 无。只影响 Node.js 模块加载速度。

---

### 优化 C: 强制 IPv4 (防止 VPC IPv6 超时)

**目标**: 消除 Node.js 22 在 VPC 中的 IPv6 DNS 超时 → 防止偶发延迟

**改动文件**: `agent-container/entrypoint.sh`

**设计**:

entrypoint.sh (新增):
```bash
# Force IPv4 for Node.js 22 VPC compatibility
# Node.js 22 Happy Eyeballs tries IPv6 first, fails in VPC without IPv6
export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--dns-result-order=ipv4first"
```

**预期收益**: 消除偶发的 0.5-2s IPv6 DNS 超时

**风险评估**:
- 极低风险: `--dns-result-order=ipv4first` 是 Node.js 官方选项
- 不需要额外的 `force-ipv4.js` 文件（他的方案用了一个 JS patch，但 CLI flag 就够了）
- 验证点: 在 VPC 环境中测试 Bedrock API 调用是否正常

**对现有架构的影响**: 无。只影响 Node.js DNS 解析顺序。

---

### 优化 D: openclaw agent 子进程重试 (容错)

**目标**: openclaw CLI 偶发失败时自动重试 → 提高可靠性

**改动文件**: `agent-container/server.py`

**设计**:

在 `invoke_openclaw()` 函数中加重试逻辑:
```python
def invoke_openclaw(tenant_id, message, timeout=300, max_retries=2):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return _invoke_openclaw_once(tenant_id, message, timeout)
        except RuntimeError as e:
            last_error = e
            if attempt < max_retries:
                wait = (attempt + 1) * 2  # 2s, 4s 线性退避
                logger.warning("openclaw retry %d/%d after %ds: %s",
                               attempt + 1, max_retries, wait, e)
                time.sleep(wait)
    raise last_error
```

**重试条件**: 只重试 RuntimeError（空输出、JSON 解析失败、超时）。不重试正常的错误响应。

**预期收益**: 偶发 CLI 失败时自动恢复，不需要用户重发消息

**风险评估**:
- 低风险: 最坏情况是多等 6 秒（2+4）后返回原始错误
- 重试不会产生副作用（openclaw agent 是无状态的 CLI 调用）
- 验证点: 模拟 CLI 失败（kill 进程），验证重试行为

**对现有架构的影响**: 无。只影响 server.py 内部的错误处理逻辑。

---

### 优化 E: H2 Proxy Fast-Path (用户感知秒级响应)

**目标**: 冷启动时用户 2-3 秒收到回复 → 用户感知秒级

**改动文件**: `src/gateway/bedrock_proxy_h2.js`

**这是最复杂的优化，需要仔细设计。**

#### 核心思路

H2 Proxy 维护一个 tenant 状态表。当请求到达时：

```
if (tenant 的 microVM 已经热了):
    正常转发到 Tenant Router → AgentCore (热路径, ~10s)
else:
    并行执行:
      1. 直接调 Bedrock Converse API → 2-3s 返回给用户 (fast-path)
      2. 异步转发到 Tenant Router → 触发 microVM 启动 (预热)
```

#### 状态管理

```javascript
// tenant 状态表 (内存, proxy 重启后重建)
const tenantState = new Map();
// key: `${channel}__${userId}`
// value: { status: 'cold' | 'warming' | 'warm', lastSeen: timestamp }

// 状态转换:
// cold → warming: 首次请求，触发 AgentCore 预热
// warming → warm: Tenant Router 返回成功响应
// warm → cold: 超过 20 分钟无请求 (AgentCore idle timeout 是 15 分钟)
```

#### Fast-Path 调用设计

```javascript
async function fastPathBedrock(userText) {
    // 直接调 Bedrock Converse API (不经过 Tenant Router / AgentCore)
    // 用 AWS SDK，走 EC2 的 IAM Role
    // 简单的 system prompt + user message，无 SOUL.md / memory / skills
    const response = await bedrockClient.converse({
        modelId: process.env.BEDROCK_MODEL_ID || 'global.amazon.nova-2-lite-v1:0',
        messages: [{ role: 'user', content: [{ text: userText }] }],
        system: [{ text: 'You are a helpful AI assistant. Be concise.' }],
    });
    return response.output.message.content[0].text;
}
```

#### 请求流程 (详细)

```
请求到达 H2 Proxy:
  │
  ├─ 提取 channel, userId, userText
  │
  ├─ 查 tenantState:
  │
  ├─ case 'warm':
  │   └─ 正常转发 Tenant Router (现有逻辑不变)
  │      └─ 更新 lastSeen
  │
  ├─ case 'warming':
  │   └─ 尝试转发 Tenant Router (microVM 可能已就绪)
  │      ├─ 成功 → 标记 warm, 返回响应
  │      └─ 超时/失败 → fast-path Bedrock 直接回复
  │
  └─ case 'cold' (或不存在):
      ├─ 标记 warming
      ├─ 异步: 转发 Tenant Router (触发 microVM 启动, 不等结果)
      └─ 同步: fast-path Bedrock → 2-3s 返回给用户
```

#### 关键设计决策

**Q: fast-path 回复和 OpenClaw 回复会不会冲突？**

不会。fast-path 只在冷启动的第一条消息使用。响应直接通过 H2 Proxy → Gateway → IM 返回。
异步预热只是触发 microVM 启动，不返回结果给用户。第二条消息开始走正常链路。

**Q: fast-path 没有 SOUL.md 人格，回复风格会不一致？**

是的。fast-path 是裸 Bedrock 调用，没有 SOUL.md 的人格设定。
但第一条消息通常是 "hi" / "你好" / 简单问题，裸回复可以接受。
可以在 fast-path 的 system prompt 里加一句通用的友好提示。

**Q: 如果用户连续快速发多条消息怎么办？**

warming 状态下，后续消息先尝试 Tenant Router（可能 microVM 已就绪），
超时才 fallback 到 fast-path。避免所有消息都走裸 Bedrock。

**Q: fast-path 需要额外的 IAM 权限吗？**

不需要。EC2 Gateway 的 IAM Role 已经有 Bedrock InvokeModel 权限
（Gateway OpenClaw 本身就在调 Bedrock）。fast-path 复用同一个 Role。

**Q: 这会增加 Bedrock 调用成本吗？**

会，但很少。只有冷启动的第一条消息会多一次 Bedrock 调用。
Nova 2 Lite 的成本是 $0.30/1M input tokens，一次简单对话 < $0.001。

#### 风险评估

- 中等风险: 这是最大的逻辑改动，引入了状态管理和并行执行路径
- 需要 `@aws-sdk/client-bedrock-runtime` npm 包（EC2 上安装）
- 验证点:
  1. 冷启动: 第一条消息 < 5s 返回
  2. 热路径: 正常消息不受影响（tenantState = warm 时走原逻辑）
  3. 并发: 多个用户同时冷启动不互相干扰
  4. 状态过期: 20 分钟无请求后重新变 cold
- 回滚: 删除 fast-path 逻辑，恢复原来的直接转发

**对现有架构的影响**:
- H2 Proxy 增加了 AWS SDK 依赖（需要在 EC2 上 `npm install`）
- H2 Proxy 从无状态变为有状态（内存中的 tenantState Map）
- Tenant Router 和 server.py 完全不变
- Gateway OpenClaw 完全不变

---

### 优化 F: STS Scoped Credentials (安全增强)

**目标**: 每个租户的 microVM 只能访问自己的 S3 namespace

**改动文件**: `agent-container/entrypoint.sh`, `agent-container/server.py`

**设计**: 排入 Week 2，独立于冷启动优化。此处只记录设计方向。

entrypoint.sh 启动时:
```bash
# 用 STS AssumeRole 生成 scoped session
# Policy: s3:* 限制到 s3://${S3_BUCKET}/${TENANT_ID}/*
aws sts assume-role \
  --role-arn $EXECUTION_ROLE_ARN \
  --role-session-name "tenant-${TENANT_ID}" \
  --policy '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"s3:*","Resource":["arn:aws:s3:::'${S3_BUCKET}'/'${TENANT_ID}'/*"]}]}' \
  > /tmp/scoped-creds.json

# 导出 scoped credentials 给 openclaw 子进程
export AWS_ACCESS_KEY_ID=$(jq -r .Credentials.AccessKeyId /tmp/scoped-creds.json)
export AWS_SECRET_ACCESS_KEY=$(jq -r .Credentials.SecretAccessKey /tmp/scoped-creds.json)
export AWS_SESSION_TOKEN=$(jq -r .Credentials.SessionToken /tmp/scoped-creds.json)
```

**注意**: 这个优化需要 IAM Role 有 `sts:AssumeRole` 权限，且 scoped credentials
不能影响 Bedrock 调用（openclaw agent 需要 Bedrock 权限）。需要仔细设计 policy。

---

## 3. 实施顺序

```
Phase 1 (低风险, 立即可做):
  ├─ 优化 A: Multi-stage Docker Build
  ├─ 优化 B: V8 Compile Cache
  └─ 优化 C: 强制 IPv4
  
  改动: Dockerfile + entrypoint.sh
  验证: EC2 上 rebuild 镜像, 测试 /ping + /invocations
  预期: 冷启动 30s → ~22-25s

Phase 2 (低风险, 紧跟 Phase 1):
  └─ 优化 D: openclaw agent 子进程重试
  
  改动: server.py
  验证: 模拟 CLI 失败, 验证重试 + 正常请求不受影响
  预期: 可靠性提升, 偶发失败自动恢复

Phase 3 (中等风险, Phase 1 验证通过后):
  └─ 优化 E: H2 Proxy Fast-Path
  
  改动: bedrock_proxy_h2.js
  验证: 
    1. 冷启动第一条消息 < 5s
    2. 热路径不受影响
    3. 多用户并发
    4. 状态过期后重新冷启动
  预期: 用户感知 2-3s 响应

Phase 4 (Week 2):
  └─ 优化 F: STS Scoped Credentials
  
  改动: entrypoint.sh + IAM policy
  验证: 租户 A 不能访问租户 B 的 S3 路径
  预期: 安全隔离增强
```

---

## 4. 优化后的目标架构

```
用户发消息 → Gateway → H2 Proxy
                         │
                         ├─ 查 tenantState
                         │
                         ├─ [warm] → Tenant Router → AgentCore → microVM → ~10s 响应
                         │
                         └─ [cold] → 并行:
                              ├─ fast-path Bedrock → 2-3s 响应给用户 ✨
                              └─ async Tenant Router → 触发 microVM 预热
                                   (下次消息走热路径)
```

### 优化后的时间指标

| 场景 | 当前 | 优化后 | 改善 |
|------|------|--------|------|
| 冷启动 (真实) | ~30s | ~22-25s | -5~8s |
| 冷启动 (用户感知) | ~30s | ~2-3s | -27s ✨ |
| 热请求 | ~10s | ~10s | 不变 |
| 偶发 CLI 失败 | 返回 500 | 自动重试 | 可靠性 ↑ |

---

## 5. 回滚计划

每个优化独立，可单独回滚:

| 优化 | 回滚方式 |
|------|---------|
| A: Multi-stage build | 恢复原 Dockerfile, rebuild |
| B: V8 Cache | 删除 entrypoint.sh 的 2 行 + Dockerfile cache 步骤 |
| C: IPv4 | 删除 entrypoint.sh 的 NODE_OPTIONS 行 |
| D: 子进程重试 | 恢复 server.py 的 invoke_openclaw 函数 |
| E: Fast-path | 删除 bedrock_proxy_h2.js 的 fast-path 逻辑 |
| F: STS Scoped | 删除 entrypoint.sh 的 STS 逻辑 |

---

## 6. 不做的事情 (明确排除)

| 方案 | 为什么不做 |
|------|-----------|
| OpenAI proxy 替代原生 Bedrock | 增加中间层, 侵入 OpenClaw 配置 |
| WebSocket bridge | 依赖 OpenClaw 内部协议, 版本耦合 |
| Lightweight agent shim (17 tools) | 本质是重写 OpenClaw, 维护成本高 |
| 修改 OpenClaw 源码 | 违反零侵入原则 |
| Lambda webhook 替代 EC2 Gateway | 不支持 WhatsApp/Discord 长连接 |
| OpenClaw 版本锁定 | 限制升级能力, 不如保持 @latest |
