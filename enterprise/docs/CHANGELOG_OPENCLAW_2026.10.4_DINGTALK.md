# OpenClaw Enterprise 2026.10.4 升级说明

- 更新日期：2026-10-20
- 适用范围：`enterprise/` 企业部署
- 对应分支：`contrib/enterprise-enhancements`
- 本文记录从同事 A 的 fork 继续开发后的企业版改动，以及 OpenClaw `2026.10.4` 正式版升级；AWS 部署、模型权限和消息渠道需在各自环境中验证。

## 概览

本次正式发布将企业运行时升级到 OpenClaw `2026.10.4`，并固定 Node.js `24.16.0`、ClawHub `0.23.3` 和 Amazon Bedrock Provider `2026.10.4`。改动同时覆盖 AgentCore/Fargate 容器、EC2 Gateway、CloudFormation、部署脚本、企业管理后台、Skill 授权与用量统计，以及 IM 消息绑定路由。

## 固定版本与默认配置

| 组件 | 版本或默认值 |
|---|---|
| OpenClaw | `2026.10.4` |
| Node.js | `24.16.0` |
| ClawHub | `0.23.3` |
| Amazon Bedrock Provider | `2026.10.4` |
| 默认模型 | `zai.glm-5` |
| 正式镜像 Tag | `openclaw-2026.10.4` |

`.env.example`、部署模板和运行时默认值均已同步。生产环境应从 `.env.example` 创建本地 `.env`，不要提交真实 `.env`、密钥或 Token。

## 运行时与镜像

- Agent 容器和执行容器使用精确版本的 Node.js 与 OpenClaw，不再依赖 `latest` 或发行版 Node 包。
- Agent 容器预装 `@openclaw/amazon-bedrock-provider`，为新版 Provider 显式创建指向全局 OpenClaw 安装的 peer dependency 链接，避免启动时出现 `missing-openclaw-peer-link`。
- Runtime stage 显式重建 `npm`、`npx` 和 `openclaw` 命令链接，避免多阶段 Docker `COPY` 后 CLI 链接失效。
- `gateway.controlUi.allowInsecureAuth` 已移除，因为 OpenClaw `2026.10.4` 不再支持该旧配置字段。
- `agent-container/smoke-test.sh` 校验 Node、OpenClaw、Provider、npm、`openclaw agent --json`、Provider peer link 及 Gateway 日志中的 `[gateway] ready`；启动等待上限为 150 秒，适配新版首次初始化。
- `entrypoint.sh` 在 Gateway 启动前加载已授权 Skill 及其 SSM 凭据；Gateway 和应用进程共享相同的运行环境。
- 支持 Gateway 在 Skill 凭据刷新后受控重启，并在关闭前尽量保留会话状态。
- Gateway 启动诊断会筛选错误类别并脱敏 URL、Token、Secret、Password、Authorization 和 API Key。

## 部署与基础设施

- `deploy.sh` 支持版本化镜像构建、验证和部署；`--build-only` 只构建、检查并推送指定镜像，不切换运行中的基础设施。
- 可通过 `IMAGE_TAG=<tag>` 选择已知镜像版本；`--skip-build --skip-seed` 可复用镜像更新服务，避免重新写入种子数据。
- CloudFormation 新增 OpenClaw、Node 和 Agent 镜像版本参数，AgentCore/Fargate 使用版本化 ECR Tag 而非 `latest`。
- EC2 初始化脚本按指定 Node/OpenClaw 版本升级 Gateway，并重建 systemd user service，确保服务不继续引用旧 NVM 路径。
- AgentCore Runtime 更新仅处理当前 Stack 关联的资源；已有会话可能仍使用旧镜像，应在变更后按需停止旧会话。

## OpenClaw 调用与模型处理

- `server.py` 继续通过官方 `openclaw agent --json` CLI 调用本地 Gateway，保留原生 session、memory compaction 和工具编排。
- 兼容直接返回 payload 与由 `result` 包装的 JSON 响应，避免将 stderr 非空直接判断为模型调用失败。
- 错误日志只保留可安全记录的错误分类、退出码和输出大小，不记录用户消息、模型响应、工具参数或凭据。
- 默认模型配置改为 `zai.glm-5`；管理员或部署配置可按既有机制覆盖模型选择。

## Skill 授权、密钥与调用统计

- Agent 的有效 Skill 列表根据岗位默认 Skill 与员工个人 Skill 动态汇总，不再依赖可能过期的 Agent 静态列表。
- Skill Key API 限制为管理员访问；只接受对应 `skill.json` manifest 中明确声明的环境变量。
- Skill Key 使用 SSM SecureString 保存，管理界面仅显示配置状态，不会回显原始值；AWS 原生 Skill 使用 IAM Role，不接受 API Key。
- 修改 Skill Key 后，会刷新受影响的 Serverless 会话或运行中的 Always-On ECS 服务，使新的环境配置生效。
- 审批通过的 Skill 请求会授予员工个人 Skill，并触发关联会话刷新。
- 新增 `skill_usage.py`，结合本请求的受控回执和结构化运行记录归集实际调用的 Skill 与工具；不会存储 prompt、回答、对话记录或凭据。
- Usage API 与界面支持按时间范围查看 Skill 调用次数、活跃用户、Serverless/Always-On 分类及按用户明细。

## 管理后台与门户

- 用量查询支持时间范围、部门、Agent、模型、部署模式与 Skill 维度；前端查询缓存按范围区分。
- Portal 个人用量兼容以员工 ID 或 Agent ID 写入的历史记录，并可按部署模式过滤。
- Portal 的可用 Skill 根据岗位和个人授权计算；审批列表按当前员工身份过滤。
- 管理端增加 Skill Key 配置和轮换入口，输入框使用密码类型且成功后清空。
- API 客户端能正确解析 FastAPI 的结构化错误详情。
- Always-On 服务的默认模型与部署级模型保持一致，避免不同层使用不一致的默认模型。

## 员工资源与生命周期管理

- 删除员工前检查 Agent binding 和 IM mapping；未指定 `force=true` 时返回明确冲突信息。
- 强制删除会处理关联 binding、IM mapping、Agent、Serverless 会话，以及 Always-On 使用的 ECS 服务、EFS Access Point、SSM 参数和 S3 workspace。
- S3 workspace 清理使用分页，避免只删除首批对象。
- 所有外部资源清理均为尽力执行；失败会记录简要错误并避免阻塞主数据删除流程。

## IM 路由与钉钉绑定

- H2 Proxy 会跳过 OpenClaw 2026.7 追加的内部 runtime context，向后查找最新的真实用户消息。
- 解析全部 JSON metadata block，从可信 inbound metadata 获取 channel 与 sender；兼容 `dingtalk-connector` 等带 `-connector` 后缀的名称。
- 拒绝将内部 `actions`、`unknown` 等值作为真实渠道或身份。
- `/start <TOKEN>` 绑定命令无论 token 是否有效都不会转发到 LLM；无效、过期、服务异常或身份信息缺失会返回绑定相关错误。
- 请求日志不再输出完整 system prompt、用户消息、模型回复或一次性绑定 Token。

## 测试与验证建议

提交前已完成的本地检查：

- 对 `server.py`、`skill_usage.py` 和 `bedrock_proxy_h2.js` 运行编辑器诊断，未发现诊断问题。
- 对 Git 暂存补丁执行格式检查；Windows CRLF 换行按 `cr-at-eol` 规则验证。
- 已确认提交不包含 `enterprise/skills/`、真实 `.env` 或其他本地凭据文件。

部署到测试环境后建议验证：

1. 构建并验证发布镜像：`bash enterprise/deploy.sh --build-only`。
2. 确认容器 smoke test 通过，且 Gateway 日志包含 `[gateway] ready`。
3. 验证 Portal、IM、AgentCore 和 Always-On 的一次正常对话。
4. 配置测试 Skill Key，验证管理员权限、SSM 保存、会话刷新和 Usage 统计。
5. 验证钉钉或其他目标 IM 的 `/start <TOKEN>`、确认绑定和后续消息路由。
6. 在测试 Stack 上验证版本化镜像更新和回滚流程。

## 相关文件

- `enterprise/agent-container/Dockerfile`
- `enterprise/agent-container/entrypoint.sh`
- `enterprise/agent-container/server.py`
- `enterprise/agent-container/skill_loader.py`
- `enterprise/agent-container/skill_usage.py`
- `enterprise/agent-container/smoke-test.sh`
- `enterprise/deploy.sh`
- `enterprise/ec2-setup.sh`
- `enterprise/clawdbot-bedrock-agentcore-multitenancy.yaml`
- `enterprise/gateway/bedrock_proxy_h2.js`
- `enterprise/admin-console/server/routers/agents.py`
- `enterprise/admin-console/server/routers/usage.py`
- `enterprise/admin-console/server/routers/portal.py`
- `enterprise/admin-console/src/hooks/useApi.ts`
- `enterprise/admin-console/src/pages/Usage.tsx`
