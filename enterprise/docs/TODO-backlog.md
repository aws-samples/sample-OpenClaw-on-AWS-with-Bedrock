# OpenClaw Enterprise — TODO Backlog

> Consolidated from all PRD, design, and worklog documents.
> Last updated: 2026-04-14

---

## P0: Infrastructure Blockers — ALL COMPLETED (2026-04-13)

### ~~P0.1 Docker Image Rebuild + ECR Push~~ DONE
Rebuilt on production EC2, pushed to ECR. All 4 runtimes updated. (worklog-2026-04-13-full)

### ~~P0.2 DynamoDB TTL Enable~~ DONE
Enabled on production table openclaw-jiade2 ap-northeast-1. (worklog-2026-04-13-full)

### ~~P0.3 Guardrail Binding to Runtimes~~ DONE
Bound via GUARDRAIL_ID environment variable on runtime update. Standard→moderate, Restricted→strict. (worklog-2026-04-13-full)

### ~~P0.4 EC2 Instance Role IAM Fix~~ DONE
Added AgentCore CRUD + PassRole + ListRoles + Guardrail permissions to CloudFormation template + deployed stack. (worklog-2026-04-13-full)

---

## P0.5: NEW Infrastructure Blockers (discovered 2026-04-13)

### ~~P0.5.1 server.py ThreadingMixIn~~ DONE (2026-04-14)
Docker 镜像已重建并推送 ECR，包含 ThreadingMixIn 修复。us-east-2 Fargate 72 次测试无 502。
AgentCore runtime 也需要 update-agent-runtime 指向新镜像（ap-northeast-1 生产环境待做）。

### P0.5.2 Fargate 替代 AgentCore — IN PROGRESS
**Status:** 4 tier 容器已运行，72 次测试通过，13 个缺口已识别。详见 worklog-2026-04-14.md。

---

## P0.6: 单线程 / 超时 / 自动重启问题追踪

> 这 3 个问题在 AgentCore 和 Fargate 下的影响不同，统一记录。

### P0.6.1 server.py 单线程 HTTPServer（容器内 Python）

**问题：** `http.server.HTTPServer` 单线程。Agent 处理 Bedrock 调用（10-60s）时无法同时响应 `/ping` 健康检查。
**AgentCore 影响：** 健康检查超时 → AgentCore 判定不健康 → **杀掉 microVM** → 用户 502。这是最严重的问题。
**Fargate 影响：** 新 Docker 镜像已含 `ThreadingMixIn` 修复 ✓。72 次测试无复现。但**生产环境需要配 ECS container health check**，届时需要验证 ThreadingMixIn 在 ECS health check 下正常工作。
**修复：** `server.py:1230` — `class ThreadedServer(ThreadingMixIn, HTTPServer)`
**状态：**
- Fargate (us-east-2): ✓ 新镜像已部署，已验证
- AgentCore (ap-northeast-1 生产): **TODO** — 需要 `update-agent-runtime` 指向新镜像
- ECS health check 配置: **TODO** — 生产环境需要配置并验证

### P0.6.2 Tenant Router 单线程（EC2 Python）

**问题：** `tenant_router.py` 同样用 `HTTPServer` 单线程。一个请求等 AgentCore 响应时（25-60s），其他请求排队。
**AgentCore 影响：** 多个员工同时发消息 → 后面的排队等待 → CloudFront 超时 → BrokenPipeError 洪水。
**Fargate 影响：** Fargate 模式下 H2 Proxy 直连容器（`forwardToFargateContainer()`），**绕过 Tenant Router**。但 deployMode=serverless 的 position 仍走 Tenant Router，所以修复仍重要。
**修复：** `tenant_router.py:620` — `class ThreadedHTTPServer(ThreadingMixIn, HTTPServer)` — 已在 4/13 部署到 EC2。
**状态：** ✓ EC2 已部署修复版。

### P0.6.3 OpenClaw Gateway 启动 30s+（容器内 Node.js）

**问题：** Gateway 是 Node.js 进程，启动需解析 config → 发现工具 → 注册插件 → 建立 WebSocket server ≈ 30s。启动期间 `openclaw agent` 回退到 embedded mode — **没有工具定义**（shell、web_search、browser 全不可用）。
**AgentCore 影响：** 每次冷启动（idle 15-60 分钟后 microVM 被销毁）→ Gateway 重新启动 → **前 30s 工具不可用**。员工每天首次使用或间隔较长时一定会遇到。
**Fargate 影响：** **解决了。** 容器启动一次后 Gateway 永远运行。只有第一次启动有 30s 延迟（ECS Service 创建或 rolling deploy 时），之后工具永远可用。72 次测试中工具全程可用。
**缓解措施（已实施）：** V8 compile cache 预热（`entrypoint.sh:37-40`）、IPv4 优先（`entrypoint.sh:44`）。将 45s 降到约 30s。
**状态：**
- Fargate: ✓ 结构性解决（永远在线）
- AgentCore: **无法根治** — 只要 microVM 被销毁就会冷启动。这是 Fargate 替代 AgentCore 的核心理由之一。

### 三个问题的交叉影响

```
员工发消息
  │
  ├── AgentCore 路径（问题集中爆发）
  │   ├── P0.6.3: Gateway 30s 启动 → 工具不可用
  │   ├── P0.6.1: 健康检查阻塞 → 502 + VM 被杀
  │   └── P0.6.2: Tenant Router 排队 → 后续请求超时
  │   结果: 复杂任务 502、工具不可用、多用户并发卡死
  │
  └── Fargate 路径（问题基本消除）
      ├── P0.6.3: ✓ Gateway 永远在线
      ├── P0.6.1: ✓ ThreadingMixIn 已修复（需验证 ECS health check）
      └── P0.6.2: ✓ 绕过 Tenant Router（直连容器）
      结果: 72 次测试 0 个 502、工具全程可用
```

---

## P1: Deployment Verification (after P0)

| # | Task | Status | Source |
|---|------|--------|--------|
| ~~1.1~~ | ~~Docker rebuild: verify workspace_assembler SOUL assembly~~ | DONE (4/13) | PRD-knowledge:293 |
| 1.2 | Auth middleware live test (login, pairing, API require JWT) | **TODO** | PRD-knowledge:294 |
| 1.3 | DynamoDB transact_write test (provision_employee_atomic) | **TODO** | PRD-knowledge:295 |
| ~~1.4~~ | ~~AgentCore IAM: execution role has dynamodb:GetItem+Query~~ | DONE (4/13) | PRD-soul-review:393 |
| 1.5 | SOUL.md KB file reference test with Nova Lite + Sonnet | **TODO** | PRD-soul-review:394 |
| 1.6 | Existing deployment migration: old .personal_soul_backup.md | **TODO** | PRD-soul-review:395 |
| ~~1.7~~ | ~~Runtime assignment E2E: Security Center → DDB → Router~~ | DONE (4/13, 4-tier routing works) | PRD-security:246 |

---

## P2: Frontend Fixes (10 items)

| # | Page | Task | Status | Source |
|---|------|------|--------|--------|
| 2.1 | Organization | Remove Default Channel from create/edit modals | **TODO** | PRD-org:137 |
| 2.2 | Organization | Active Agents stat card fix | **TODO** | PRD-org:138 |
| 2.3 | Organization | SOUL Configured stat card fix | **TODO** | PRD-org:139 |
| 2.4 | Agent Factory | Delete Agent button + confirmation modal | **TODO** | PRD-agent-factory:183 |
| 2.5 | Agent Factory | Refresh Agent button | **TODO** | PRD-agent-factory:184 |
| 2.6 | Agent Factory | SoulEditor 409 Conflict handling | **TODO** | PRD-agent-factory:185 |
| 2.7 | Portal | "My Agent Identity" page (edit PERSONAL_SOUL.md) | **TODO** | PRD-soul-review:396 |
| 2.8 | Knowledge | Search results format adaptation (new API shape) | **TODO** | PRD-knowledge:279 |
| 2.9 | Knowledge | Upload 413 error friendly message | **TODO** | PRD-knowledge:281 |
| 2.10 | Security Center | Runtime card show guardrail + assigned positions | **TODO** | PRD-monitor:355 |

---

## P3: Completed Modules (2026-04-13)

### ~~P3.1 Tools & Skills Market~~ — DONE (Phase 1-3)
All 3 phases completed in 4/13 session:
- Phase 1: skill_loader DynamoDB fix, assign audit+bump, API key endpoint, output paths, workspace budget, seed cleanup, S3 uploads, skill assignments
- Phase 2: ToolsSkills/index.tsx card grid, Detail.tsx, MCP Registry style, sidebar rename
- Phase 3: Portal submit/request, admin review/approve, code viewer, personal skills loading
- Source: worklog-2026-04-13-full:28-58

### ~~P3.1.1 Tools & Skills PRD items completed but unchecked in PRD~~ — DONE
These PRD-tools-skills-market.md items were completed 4/13 but `- [ ]` not updated:
- Line 748-756: Phase 1 backend fixes (all 9 items)
- Line 759-761: Seed cleanup (all 3 items)
- Line 764-774: Skill assignment + demo (all 11 items)
- Line 779-781: Phase 2 backend (3 items)
- Line 784-788: Phase 2 frontend admin (5 items)
- Line 798-812: Phase 3 (most items — submit, request, review, approve-install, code viewer, personal skills)

---

## P3: New Modules (remaining)

| # | Module | Description | Status | Source |
|---|--------|-------------|--------|--------|
| 3.2 | **Portal / Employee Module** | My Agent Identity, My Usage, IM pairing, request approval | **TODO** | worklog:131 |
| 3.3 | **Fargate Always-On** | Full architecture, deploy.sh integration, per-tier ECS services | **IN PROGRESS** (this session) | design-always-on-fargate.md |
| 3.4 | **SOUL Review Engine** | personal_soul_extractor, tool_usage_collector, review_engine, scheduled scan, auto-revert | **TODO** (8 sub-tasks) | PRD-soul-review:402-409 |
| 3.5 | **Audit Review Engine** | Personal SOUL review, KB upload review, tool usage anomaly, auto-approve/revert | **TODO** | PRD-audit:400-404 |
| 3.6 | **IM Security Hardening** | SSM detection→DDB, public IP removal, JWT validation consolidation | **TODO** | PRD-im:196-198 |

---

## P3.X: Backend Fixes from PRDs (not yet addressed)

### PRD-agent-factory (remaining items)
| # | Task | Source |
|---|------|--------|
| AF.1 | shared.py → audit_soul_change() unified function | PRD-agent-factory:172 |
| AF.2 | agents.py → save_agent_soul calls audit | PRD-agent-factory:173 |
| AF.3 | security.py → put_global_soul/put_position_soul call audit | PRD-agent-factory:174 |
| AF.4 | agents.py → DELETE /api/v1/agents/{agent_id} with cascade | PRD-agent-factory:175 |
| AF.5 | agents.py → replace CloudWatch with DynamoDB for agent status | PRD-agent-factory:177 |
| AF.6 | agents.py → remove skill propagation loop | PRD-agent-factory:178 |
| AF.7 | agents.py → skill_keys 5-min cache | PRD-agent-factory:179 |
| AF.8 | agents.py → SOUL save version conflict detection (409) | PRD-agent-factory:181 |

### PRD-organization (remaining items)
| # | Task | Source |
|---|------|--------|
| ORG.1 | delete_employee audit trail | PRD-org:131 |
| ORG.2 | delete_department position check | PRD-org:132 |
| ORG.3 | remove shared agent / autoBindAll | PRD-org:133 |
| ORG.4 | defaultChannel "slack" → "portal" | PRD-org:134 |
| ORG.5 | force-delete cascades to AGENT# + S3 | PRD-org:135 |
| ORG.6 | activity cache | PRD-org:136 |
| ORG.7 | cleanup shared agent from seed + demo + comments | PRD-org:140 |

### PRD-playground (remaining items)
| # | Task | Source |
|---|------|--------|
| PG.1 | Remove _POS_TOOLS, use DynamoDB | PRD-playground:213 |
| PG.2 | Pipeline config API | PRD-playground:214 |
| PG.3 | Playground events API | PRD-playground:215 |
| PG.4 | Simulate → Bedrock Converse with real SOUL | PRD-playground:216 |
| PG.5 | Frontend updates 2.1-2.4 | PRD-playground:217 |

### PRD-usage-cost (remaining items)
| # | Task | Source |
|---|------|--------|
| UC.1 | Remove ChatGPT comparison | PRD-usage:216 |
| UC.2 | Fix unknown model default | PRD-usage:217 |
| UC.3 | Remove seed date fallback | PRD-usage:218 |
| UC.4 | Budget projection 7-day average | PRD-usage:219 |
| UC.5 | Hierarchical budget system 2.1-2.5 | PRD-usage:220 |
| UC.6 | Cleanup + cache + audit 3.1-3.3 | PRD-usage:221 |
| UC.7 | Frontend updates 4.1-4.4 | PRD-usage:222 |
| UC.8 | Update seed CONFIG#budgets with new schema | PRD-usage:225 |

### PRD-security-center (remaining items)
| # | Task | Source |
|---|------|--------|
| SC.1 | Runtime routing fix 1.1-1.4 (SSM → DynamoDB) | PRD-security:238 |
| SC.2 | Tool permission audit + force refresh | PRD-security:239 |
| SC.3 | Runtime assignment audit + force refresh | PRD-security:240 |
| SC.4 | Runtime config change → force refresh | PRD-security:241 |
| SC.5 | Permission denied → DynamoDB AUDIT# | PRD-security:242 |
| SC.6 | Guardrail block events in Security Center | PRD-security:250 |

### PRD-settings (remaining items — security-critical)
| # | Task | Priority | Source |
|---|------|----------|--------|
| ST.1 | **require_role missing** on 7 settings endpoints (model, security config) | **HIGH** | PRD-settings:305-311 |
| ST.2 | **AUDIT# missing** on 8 config change endpoints | **HIGH** | PRD-settings:314-321 |
| ST.3 | **bump_config_version missing** on model/agent config changes | **MEDIUM** | PRD-settings:324-327 |
| ST.4 | Admin Assistant rewrite (Bedrock Converse + DDB history) | MEDIUM | PRD-settings:295 |
| ST.5 | Config change audit + force refresh | MEDIUM | PRD-settings:296 |
| ST.6 | Platform logs + region in services | LOW | PRD-settings:297 |
| ST.7 | Password change validate old password | LOW | PRD-settings:335 |

### PRD-monitor-center (remaining items)
| # | Task | Source |
|---|------|--------|
| MC.1 | Backend rewrite 1.1-1.13 | PRD-monitor:340 |
| MC.2 | Frontend rewrite 2.1-2.7 | PRD-monitor:341 |
| MC.3 | server.py takeover SSM → DynamoDB | PRD-monitor:342 |

### PRD-audit-center (remaining items)
| # | Task | Source |
|---|------|--------|
| AC.1 | Backend fixes 1.1-1.5 (scope, time-range, threshold, ORG#acme) | PRD-audit:392 |
| AC.2 | Review Engine endpoints + AI analyze + compliance | PRD-audit:393 |
| AC.3 | Frontend updates 3.1-3.7 | PRD-audit:394 |

### PRD-knowledge-base (remaining items)
| # | Task | Source |
|---|------|--------|
| KB.1 | Frontend search results format (new API shape) | PRD-knowledge:279 |
| KB.2 | Frontend Refresh Agent button | PRD-knowledge:280 |
| KB.3 | Frontend Upload 413 handling | PRD-knowledge:281 |
| KB.4 | Frontend Assignment Modal size warning | PRD-knowledge:282 |
| KB.5 | seed_knowledge.py cleanup (fake counts) | PRD-knowledge:284 |
| KB.6 | KB upload triggers pending_review + injection detection | PRD-knowledge:288-289 |

### PRD-soul-review-engine (remaining items)
| # | Task | Source |
|---|------|--------|
| SR.1 | review_engine.py → scheduled AI review + auto-revert | PRD-soul-review:404 |
| SR.2 | Security Center → Review tab frontend | PRD-soul-review:405 |
| SR.3 | server.py cold start → check AUDIT# for critical auto-revert | PRD-soul-review:406 |
| SR.4 | audit.py → review-type scanning | PRD-soul-review:407 |
| SR.5 | monitor.py → review alert rules | PRD-soul-review:408 |
| SR.6 | seed_dynamodb.py → USAGE_PATTERN# + AUDIT# review data | PRD-soul-review:409 |
| SR.7 | Update PRD: CONTEXT.md abandoned → context block in SOUL.md | PRD-soul-review:421 |

### PRD-im-channels (remaining items)
| # | Task | Source |
|---|------|--------|
| IM.1 | Frontend updates for IM Channels page | PRD-im:204 |
| IM.2 | Fargate always-on IM binding redesign | PRD-im:205 |

---

## P4: Enhancements (future)

| # | Area | Task | Source |
|---|------|------|--------|
| 4.1 | Infrastructure | CloudFront → ALB → EC2 (stable endpoint) | PRD-monitor:349 |
| 4.2 | Playground | Multi-turn simulate mode | PRD-playground:222 |
| 4.3 | Playground | SOUL content inline expansion | PRD-playground:223 |
| 4.4 | Playground | A/B comparison (same message, two positions) | PRD-playground:224 |
| 4.5 | Playground | Record test sessions for regression | PRD-playground:225 |
| 4.6 | Usage | Budget enforcement (block agent when exceeded) | PRD-usage:228 |
| 4.7 | Usage | Monthly cost report export (PDF/CSV) | PRD-usage:229 |
| 4.8 | Usage | Model cost comparison tool ("what if") | PRD-usage:230 |
| 4.9 | Usage | Real-time Bedrock Cost Explorer reconciliation | PRD-usage:231 |
| 4.10 | Settings | Admin AI operation audit trail | PRD-settings:330 |
| 4.11 | Settings | Services endpoint dedup (settings vs monitor) | PRD-settings:331 |
| 4.12 | Settings | Org sync retry + error handling | PRD-settings:334 |
| 4.13 | Settings | Password change validate old password | PRD-settings:335 |
| 4.14 | Audit | Scheduled auto-scan (cron 30 min) | PRD-audit:407 |
| 4.15 | Audit | Review Engine batch mode (10 per Bedrock call) | PRD-audit:408 |
| 4.16 | Audit | Compliance report export PDF | PRD-audit:409 |
| 4.17 | Audit | Alert integration with IM channels | PRD-audit:410 |
| 4.18 | Audit | Immutable audit log (S3 Object Lock) | PRD-audit:411 |
| 4.19 | Review Engine | S3 versioning for PERSONAL_SOUL.md | PRD-soul-review:413 |
| 4.20 | Review Engine | Tool usage heatmap visualization | PRD-soul-review:415 |
| 4.21 | Review Engine | Employee notification on auto-revert via IM | PRD-soul-review:416 |
| 4.22 | Knowledge | KB upload → pending_review + injection detection | PRD-knowledge:288-289 |
| 4.23 | Knowledge | KB seed data cleanup (fake counts vs real S3) | PRD-knowledge:284 |
| 4.24 | Security | Permission denied → AI anomaly detection | PRD-security:251 |
| 4.25 | IM Channels | Remove SSM dual-write (after migration verified) | PRD-im:201 |
| 4.26 | IM Channels | DM policy validation/warning in admin UI | PRD-im:202 |
| 4.27 | IM Channels | Expand IM notification to more channels | PRD-im:203 |
| 4.28 | Fargate | ECS Service auto-scaling per agent | design-always-on-fargate.md |
| 4.29 | Fargate | Dedicated Bot allowFrom management | design-always-on-fargate.md:OD2 |
| 4.30 | Fargate | Personal always-on approval flow | design-always-on-fargate.md:OD3 |

---

## Summary

| Priority | Count | Status |
|----------|-------|--------|
| **P0** | ~~4 items~~ | ALL DONE (4/13) |
| **P0.5** | 2 items | NEW blockers: ThreadingMixIn rebuild + Fargate design |
| **P1** | 7 items (4 remaining) | 3 done, 4 need verification |
| **P2** | 10 items | Frontend fixes, not started |
| **P3** | 5 modules (4 remaining) | Tools & Skills DONE; Fargate IN PROGRESS |
| **P3.X** | ~55 items | Backend fixes from 9 PRDs |
| **P4** | 30 items | Long-term roadmap |
| **Total** | ~106 items | P0 cleared, Fargate is current focus |

---

## Current Execution Order (2026-04-14)

```
P0.5.2 Fargate Architecture Design (THIS SESSION)
  ├── AgentCore Issues Analysis doc
  ├── Fargate Full Architecture Design doc
  ├── feature/fargate-first branch implementation
  └── E2E testing on us-east-2
      │
      v
P0.5.1 Docker Rebuild (ThreadingMixIn)
      │
      v
P1.2-P1.6 Remaining Deployment Verification
      │
      v
ST.1-ST.3 Settings Security (require_role + AUDIT# — HIGH priority)
      │
      v
P2.1-P2.10 Frontend Fixes (can batch)
      │
      v
P3.X Backend fixes (batch by PRD)
      │
      v
P3.2-P3.6 New Modules
```
