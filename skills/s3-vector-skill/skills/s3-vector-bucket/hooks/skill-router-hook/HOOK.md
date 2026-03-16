---
name: skill-router-hook
description: "在 agent:bootstrap 时用 S3 Vectors 动态筛选 Top-K 最相关 Skill，将结果写入 BOOTSTRAP.md 注入 LLM 上下文，减少 Skill Token 消耗。支持多 Agent 独立索引（方案 A）。"
homepage: https://docs.openclaw.ai/automation/hooks
metadata:
  {
    "openclaw":
      {
        "emoji": "🧭",
        "events": ["agent:bootstrap"],
        "requires":
          {
            "config": ["workspace.dir"],
            "bins": ["python3"],
            "env": ["SKILL_ROUTER_BUCKET"],
          },
      },
  }
---

# Skill Router Hook

在 `agent:bootstrap` 时，读取近期 Memory 上下文，通过 **S3 Vectors** 相似度搜索找出
最匹配当前会话意图的 Top-5 Skill，将结果写入工作区 `BOOTSTRAP.md`，
LLM 启动时即可优先聚焦于相关 Skill，降低无效 Token 消耗。

支持**多 Agent 独立索引**：设置 `SKILL_ROUTER_INDEX_PREFIX` 后，Hook 自动从
`event.sessionKey` 提取 agent id，选择对应的 `{prefix}-{agent_id}` 索引。

## 工作流程

```
agent:bootstrap
    ↓
从 sessionKey 提取 agent id（多 Agent 模式）
    ↓
读取 memory/YYYY-MM-DD.md（最近 2 天）
    ↓
提取最近 500 字作为上下文
    ↓
调用 skill_router.py → S3 Vectors 相似度搜索
    ↓
写入 BOOTSTRAP.md（Top-5 Skill 列表）
    ↓
注入 LLM 上下文
```

## 注意事项

- 需先运行 `build_all.sh`（多 Agent）或 `build_skill_index.py`（单 Agent）离线建库
- 需设置环境变量 `SKILL_ROUTER_BUCKET` + `SKILL_ROUTER_INDEX_PREFIX`（多 Agent 推荐）
- `message:received` Hook 的上下文注入机制落地后（[#8807](https://github.com/openclaw/openclaw/issues/8807)）可升级为真正的按消息动态注入

## 环境变量

| 变量 | 必需 | 说明 | 示例 |
|------|:---:|------|------|
| `SKILL_ROUTER_BUCKET` | ✅ | S3 向量桶名称 | `my-skill-router` |
| `SKILL_ROUTER_INDEX_PREFIX` | 多Agent推荐 | 索引前缀，自动拼接 agent id | `skills` → `skills-general-tech` |
| `SKILL_ROUTER_INDEX` | 单Agent | 固定索引名（与 PREFIX 二选一，PREFIX 优先） | `skills-v1` |
| `SKILL_ROUTER_TOP_K` | ❌ | 返回 Top-K 数量（默认 5） | `5` |
| `SKILL_ROUTER_SCRIPT` | ❌ | skill_router.py 路径（自动检测） | `/path/to/skill_router.py` |
| `SKILL_ROUTER_REGION` | ❌ | S3 Vectors Region（默认 ap-northeast-1） | `ap-northeast-1` |
| `AWS_BEDROCK_REGION` | ❌ | Bedrock Region（默认跟 `SKILL_ROUTER_REGION` 一致；若该 region 无 Titan v2 可手动指定） | `us-east-1` |

## 多 Agent 配置（方案 A：每 Agent 独立索引）

```bash
# 1. 建库（所有 Agent 并行，约 2 分钟）
SKILL_ROUTER_BUCKET=my-skill-router \
  ./scripts/build_all.sh

# 2. 安装 Hook
cp -r hooks/skill-router-hook ~/.openclaw/hooks/

# 3. 设置环境变量（加到 ~/.bashrc 或 OpenClaw 配置）
export SKILL_ROUTER_BUCKET=my-skill-router
export SKILL_ROUTER_INDEX_PREFIX=skills

# 4. 启用 Hook
openclaw hooks enable skill-router-hook
```

Hook 自动按 agent id 选择对应索引，无需为每个 Agent 单独配置。
