#!/usr/bin/env bash
# build_all.sh — 为所有 OpenClaw Agent 并行建立 S3 Vectors 技能索引
#
# 用法:
#   ./scripts/build_all.sh
#   SKILL_ROUTER_BUCKET=my-bucket ./scripts/build_all.sh
#
# 环境变量:
#   SKILL_ROUTER_BUCKET  向量桶名称（必须，或在下方直接写死）
#   INDEX_PREFIX         索引名称前缀（默认 skills）
#   OPENCLAW_AGENTS_DIR  agents workspace 根目录（默认 ~/.openclaw）
#   GLOBAL_SKILLS_DIR    全局 Skill 目录（默认 OpenClaw 安装路径）
#   EMBED_REGION         Bedrock Region（默认跟 S3_REGION 一致；若该 region 无 Titan v2 可手动指定）
#   S3_REGION            S3 Vectors Region（默认 ap-northeast-1）

set -euo pipefail

BUCKET="${SKILL_ROUTER_BUCKET:-}"
INDEX_PREFIX="${INDEX_PREFIX:-skills}"
OPENCLAW_DIR="${OPENCLAW_AGENTS_DIR:-$HOME/.openclaw}"
GLOBAL_SKILLS="${GLOBAL_SKILLS_DIR:-$(node -e "require.resolve('openclaw')" 2>/dev/null | sed 's|/index.js||' || echo "$HOME/.nvm/versions/node/v22.22.0/lib/node_modules/openclaw")/skills}"
S3_REGION="${S3_REGION:-ap-northeast-1}"
EMBED_REGION="${EMBED_REGION:-${AWS_BEDROCK_REGION:-$S3_REGION}}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_SCRIPT="$SCRIPT_DIR/build_skill_index.py"

# ── 校验 ─────────────────────────────────────────────────────────────
if [[ -z "$BUCKET" ]]; then
  echo "❌ 请设置 SKILL_ROUTER_BUCKET 环境变量或修改 build_all.sh 顶部的 BUCKET 变量"
  exit 1
fi

# ── Agent 列表（自动扫描 workspace-* 目录）───────────────────────────
declare -A AGENT_WORKSPACES=()
for ws in "$OPENCLAW_DIR"/workspace*; do
  [[ -d "$ws" ]] || continue
  dir_name="${ws##*/}"                     # workspace-general-tech
  agent_id="${dir_name#workspace}"         # -general-tech
  agent_id="${agent_id#-}"                 # general-tech
  [[ -z "$agent_id" ]] && agent_id="main" # workspace → main
  AGENT_WORKSPACES[$agent_id]="$ws"
done

if [[ ${#AGENT_WORKSPACES[@]} -eq 0 ]]; then
  echo "❌ 未在 $OPENCLAW_DIR 下发现任何 workspace 目录"
  exit 1
fi

echo "======================================================"
echo "  S3 Vector Skill Router — 多 Agent 全量建库"
echo "======================================================"
echo "  Bucket       : $BUCKET"
echo "  Index Prefix : $INDEX_PREFIX"
echo "  S3 Region    : $S3_REGION"
echo "  Embed Region : $EMBED_REGION"
echo "  Global Skills: $GLOBAL_SKILLS"
echo "======================================================"
echo ""

PIDS=()
LOG_DIR="/tmp/skill-router-build-logs"
mkdir -p "$LOG_DIR"

for agent_id in "${!AGENT_WORKSPACES[@]}"; do
  workspace="${AGENT_WORKSPACES[$agent_id]}"
  local_skills="$workspace/skills"
  index_name="${INDEX_PREFIX}-${agent_id}"
  log_file="$LOG_DIR/${agent_id}.log"

  # 跳过 workspace 不存在的 agent
  if [[ ! -d "$workspace" ]]; then
    echo "⚠️  跳过 $agent_id（workspace 不存在: $workspace）"
    continue
  fi

  echo "🚀 启动 $agent_id → index: $index_name"

  # 构建 --skills-dir 参数（local + global）
  skills_dirs=()
  [[ -d "$local_skills" ]] && skills_dirs+=("$local_skills")
  [[ -d "$GLOBAL_SKILLS" ]] && skills_dirs+=("$GLOBAL_SKILLS")

  (
    python3 "$BUILD_SCRIPT" \
      --bucket  "$BUCKET" \
      --index   "$index_name" \
      --region  "$S3_REGION" \
      --embed-region "$EMBED_REGION" \
      --skills-dir "${skills_dirs[@]}" \
      --sync \
      > "$log_file" 2>&1
    status=$?
    if [[ $status -eq 0 ]]; then
      skill_count=$(grep "发现" "$log_file" | grep -o "[0-9]* 个 Skill" | head -1)
      echo "  ✅ $agent_id 完成（$skill_count）"
    else
      echo "  ❌ $agent_id 失败，查看日志: $log_file"
      tail -5 "$log_file" | sed 's/^/     /'
    fi
  ) &
  PIDS+=($!)
done

echo ""
echo "⏳ 等待所有 Agent 建库完成..."
for pid in "${PIDS[@]}"; do
  wait "$pid" || true
done

echo ""
echo "======================================================"
echo "  建库完成！各 Agent 索引名称："
for agent_id in "${!AGENT_WORKSPACES[@]}"; do
  [[ -d "${AGENT_WORKSPACES[$agent_id]}" ]] && \
    echo "  $agent_id → ${INDEX_PREFIX}-${agent_id}"
done
echo ""
echo "  Hook 配置（设置以下环境变量）："
echo "  export SKILL_ROUTER_BUCKET=$BUCKET"
echo "  export SKILL_ROUTER_INDEX_PREFIX=$INDEX_PREFIX"
echo "======================================================"
