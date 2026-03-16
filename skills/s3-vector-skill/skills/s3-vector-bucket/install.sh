#!/usr/bin/env bash
# install.sh — 一键部署 S3 Vector Skill Router
#
# 完整流程：前置检查 → 建库 → 安装 Hook → 注入环境变量 → 重启 Gateway
#
# 用法:
#   ./install.sh                           # 交互式，逐步提示
#   ./install.sh --bucket my-router        # 指定桶名
#   ./install.sh --bucket my-router --yes  # 跳过确认，全自动
#
# 环境变量（可替代命令行参数）:
#   SKILL_ROUTER_BUCKET       向量桶名称（必须）
#   SKILL_ROUTER_INDEX_PREFIX 索引前缀（默认 skills）
#   SKILL_ROUTER_REGION       S3 Vectors Region（默认 ap-northeast-1）
#   AWS_BEDROCK_REGION        Bedrock Region（默认跟 SKILL_ROUTER_REGION 一致）

set -euo pipefail

# ── 颜色 ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}ℹ️  $*${NC}"; }
ok()    { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}"; }
fail()  { echo -e "${RED}❌ $*${NC}"; exit 1; }

# ── 参数解析 ──────────────────────────────────────────────────────────
BUCKET="${SKILL_ROUTER_BUCKET:-}"
INDEX_PREFIX="${SKILL_ROUTER_INDEX_PREFIX:-skills}"
REGION="${SKILL_ROUTER_REGION:-ap-northeast-1}"
EMBED_REGION="${AWS_BEDROCK_REGION:-}"
YES=false
SKIP_BUILD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)       BUCKET="$2"; shift 2 ;;
    --prefix)       INDEX_PREFIX="$2"; shift 2 ;;
    --region)       REGION="$2"; shift 2 ;;
    --embed-region) EMBED_REGION="$2"; shift 2 ;;
    --yes|-y)       YES=true; shift ;;
    --skip-build)   SKIP_BUILD=true; shift ;;
    --help|-h)
      echo "用法: ./install.sh [--bucket NAME] [--prefix PREFIX] [--region REGION] [--yes]"
      echo ""
      echo "选项:"
      echo "  --bucket NAME       S3 向量桶名称（必须）"
      echo "  --prefix PREFIX     索引前缀（默认 skills）"
      echo "  --region REGION     S3 Vectors Region（默认 ap-northeast-1）"
      echo "  --embed-region REG  Bedrock Embedding Region（默认跟 --region 一致）"
      echo "  --yes, -y           跳过确认提示"
      echo "  --skip-build        跳过建库步骤（已有索引时使用）"
      exit 0
      ;;
    *) fail "未知参数: $1（使用 --help 查看用法）" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EMBED_REGION="${EMBED_REGION:-$REGION}"

# ── 交互式输入 ────────────────────────────────────────────────────────
if [[ -z "$BUCKET" ]]; then
  echo ""
  read -rp "请输入 S3 向量桶名称（如 my-skill-router）: " BUCKET
  [[ -z "$BUCKET" ]] && fail "桶名不能为空"
fi

# ── 平台检测 ──────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Linux)  PLATFORM="linux" ;;
  Darwin) PLATFORM="macos" ;;
  *)      fail "不支持的操作系统: $OS（仅支持 Linux 和 macOS）" ;;
esac

echo ""
echo "======================================================"
echo "  S3 Vector Skill Router — 一键部署"
echo "======================================================"
echo "  平台         : $PLATFORM ($OS)"
echo "  向量桶       : $BUCKET"
echo "  索引前缀     : $INDEX_PREFIX"
echo "  S3 Region    : $REGION"
echo "  Embed Region : $EMBED_REGION"
echo "======================================================"
echo ""

if [[ "$YES" != true ]]; then
  read -rp "确认开始部署？(y/N) " confirm
  [[ "$confirm" =~ ^[yY] ]] || { echo "已取消"; exit 0; }
fi

# ══════════════════════════════════════════════════════════════════════
# Step 1: 前置检查
# ══════════════════════════════════════════════════════════════════════
echo ""
info "Step 1/5: 前置检查"
echo "------------------------------------------------------"

# Python3
if ! command -v python3 &>/dev/null; then
  fail "python3 未安装"
fi
ok "python3 $(python3 --version 2>&1 | awk '{print $2}')"

# boto3
if ! python3 -c "import boto3" 2>/dev/null; then
  warn "boto3 未安装，正在安装..."
  pip3 install boto3 --upgrade --quiet || fail "boto3 安装失败"
fi
ok "boto3 $(python3 -c 'import boto3; print(boto3.__version__)')"

# AWS 凭证
if ! aws sts get-caller-identity &>/dev/null; then
  fail "AWS 凭证无效（运行 aws sts get-caller-identity 检查）"
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ok "AWS 凭证有效（账号: $ACCOUNT_ID）"

# S3 Vectors 可用性
if ! aws s3vectors list-vector-buckets --region "$REGION" &>/dev/null; then
  fail "S3 Vectors 在 $REGION 不可用（运行 aws s3vectors list-vector-buckets --region $REGION 检查）"
fi
ok "S3 Vectors 在 $REGION 可用"

# Bedrock Titan Embeddings 可用性
TITAN_CHECK=$(aws bedrock list-foundation-models --region "$EMBED_REGION" \
  --query "modelSummaries[?contains(modelId,'titan-embed-text-v2')].modelId" \
  --output text 2>/dev/null || echo "")
if [[ -z "$TITAN_CHECK" ]]; then
  warn "Titan Text Embeddings V2 在 $EMBED_REGION 不可用"
  if [[ "$EMBED_REGION" != "us-east-1" ]]; then
    info "尝试 us-east-1 作为 fallback..."
    EMBED_REGION="us-east-1"
    TITAN_CHECK=$(aws bedrock list-foundation-models --region "$EMBED_REGION" \
      --query "modelSummaries[?contains(modelId,'titan-embed-text-v2')].modelId" \
      --output text 2>/dev/null || echo "")
    [[ -z "$TITAN_CHECK" ]] && fail "Titan Text Embeddings V2 在 us-east-1 也不可用"
    ok "Titan Text Embeddings V2 在 $EMBED_REGION 可用（fallback）"
  else
    fail "Titan Text Embeddings V2 不可用"
  fi
else
  ok "Titan Text Embeddings V2 在 $EMBED_REGION 可用"
fi

# OpenClaw
if ! command -v openclaw &>/dev/null; then
  fail "openclaw CLI 未安装"
fi
ok "openclaw $(openclaw --version 2>/dev/null || echo '(版本未知)')"

# ══════════════════════════════════════════════════════════════════════
# Step 2: 建库
# ══════════════════════════════════════════════════════════════════════
echo ""
info "Step 2/5: 建库（扫描 Skill → Embedding → S3 Vectors）"
echo "------------------------------------------------------"

if [[ "$SKIP_BUILD" == true ]]; then
  warn "跳过建库步骤（--skip-build）"
else
  # 检查 build_all.sh 是否存在
  BUILD_ALL="$SCRIPT_DIR/scripts/build_all.sh"
  BUILD_SINGLE="$SCRIPT_DIR/scripts/build_skill_index.py"

  if [[ -x "$BUILD_ALL" ]]; then
    info "使用 build_all.sh 并行建库（多 Agent）..."
    SKILL_ROUTER_BUCKET="$BUCKET" \
    INDEX_PREFIX="$INDEX_PREFIX" \
    S3_REGION="$REGION" \
    EMBED_REGION="$EMBED_REGION" \
      bash "$BUILD_ALL"
  elif [[ -f "$BUILD_SINGLE" ]]; then
    info "使用 build_skill_index.py 单索引建库..."
    python3 "$BUILD_SINGLE" \
      --bucket "$BUCKET" \
      --index "${INDEX_PREFIX}-main" \
      --region "$REGION" \
      --embed-region "$EMBED_REGION" \
      --sync
  else
    fail "未找到建库脚本（scripts/build_all.sh 或 scripts/build_skill_index.py）"
  fi
  ok "建库完成"
fi

# ══════════════════════════════════════════════════════════════════════
# Step 3: 安装 Hook
# ══════════════════════════════════════════════════════════════════════
echo ""
info "Step 3/5: 安装 Hook"
echo "------------------------------------------------------"

HOOK_SRC="$SCRIPT_DIR/hooks/skill-router-hook"
HOOK_DST="$HOME/.openclaw/hooks/skill-router-hook"

if [[ ! -d "$HOOK_SRC" ]]; then
  fail "未找到 hook 源目录: $HOOK_SRC"
fi

if [[ -d "$HOOK_DST" ]]; then
  warn "Hook 目录已存在，更新覆盖..."
fi

mkdir -p "$(dirname "$HOOK_DST")"
cp -r "$HOOK_SRC" "$HOOK_DST"
ok "Hook 已复制到 $HOOK_DST"

# 启用 Hook
if openclaw hooks enable skill-router-hook 2>/dev/null; then
  ok "Hook 已启用"
else
  warn "openclaw hooks enable 失败（可能需要手动启用，或 Gateway 未运行）"
fi

# ══════════════════════════════════════════════════════════════════════
# Step 4: 注入环境变量
# ══════════════════════════════════════════════════════════════════════
echo ""
info "Step 4/5: 注入环境变量（平台: $PLATFORM）"
echo "------------------------------------------------------"

ENV_VARS=(
  "SKILL_ROUTER_BUCKET=$BUCKET"
  "SKILL_ROUTER_INDEX_PREFIX=$INDEX_PREFIX"
  "SKILL_ROUTER_REGION=$REGION"
  "SKILL_ROUTER_SCRIPT=$SCRIPT_DIR/scripts/skill_router.py"
)
# 只有 Embed Region 与 S3 Region 不同时才注入
if [[ "$EMBED_REGION" != "$REGION" ]]; then
  ENV_VARS+=("AWS_BEDROCK_REGION=$EMBED_REGION")
fi

if [[ "$PLATFORM" == "linux" ]]; then
  # ── Linux: systemd user service override ──
  SYSTEMD_DIR="$HOME/.config/systemd/user"
  
  # 检测 OpenClaw Gateway 的 service 名称
  SERVICE_NAME=""
  for candidate in "openclaw-gateway" "openclaw" "clawd-gateway" "clawd"; do
    if systemctl --user cat "$candidate.service" &>/dev/null; then
      SERVICE_NAME="$candidate"
      break
    fi
  done

  if [[ -z "$SERVICE_NAME" ]]; then
    warn "未检测到 OpenClaw Gateway systemd service"
    warn "将环境变量写入 ~/.bashrc（手动启动 Gateway 时生效）"
    for var in "${ENV_VARS[@]}"; do
      # 避免重复写入
      key="${var%%=*}"
      grep -q "^export $key=" "$HOME/.bashrc" 2>/dev/null && \
        sed -i "/^export $key=/d" "$HOME/.bashrc"
      echo "export $var" >> "$HOME/.bashrc"
    done
    ok "环境变量已写入 ~/.bashrc"
  else
    OVERRIDE_DIR="$SYSTEMD_DIR/${SERVICE_NAME}.service.d"
    OVERRIDE_FILE="$OVERRIDE_DIR/skill-router-env.conf"
    mkdir -p "$OVERRIDE_DIR"

    {
      echo "[Service]"
      for var in "${ENV_VARS[@]}"; do
        echo "Environment=\"$var\""
      done
    } > "$OVERRIDE_FILE"

    systemctl --user daemon-reload
    ok "systemd override 已写入: $OVERRIDE_FILE"
  fi

elif [[ "$PLATFORM" == "macos" ]]; then
  # ── macOS: launchd plist ──
  PLIST=""
  for candidate in "$HOME/Library/LaunchAgents"/ai.openclaw.gateway*.plist \
                    "$HOME/Library/LaunchAgents"/com.openclaw*.plist; do
    if [[ -f "$candidate" ]]; then
      PLIST="$candidate"
      break
    fi
  done

  if [[ -z "$PLIST" ]]; then
    warn "未找到 OpenClaw Gateway launchd plist"
    warn "将环境变量写入 ~/.zshrc（手动启动 Gateway 时生效）"
    RCFILE="$HOME/.zshrc"
    [[ -f "$HOME/.bash_profile" ]] && ! [[ -f "$HOME/.zshrc" ]] && RCFILE="$HOME/.bash_profile"
    for var in "${ENV_VARS[@]}"; do
      key="${var%%=*}"
      grep -q "^export $key=" "$RCFILE" 2>/dev/null && \
        sed -i '' "/^export $key=/d" "$RCFILE"
      echo "export $var" >> "$RCFILE"
    done
    ok "环境变量已写入 $RCFILE"
  else
    # 确保 EnvironmentVariables dict 存在
    /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables dict" "$PLIST" 2>/dev/null || true
    for var in "${ENV_VARS[@]}"; do
      key="${var%%=*}"
      value="${var#*=}"
      /usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:$key" "$PLIST" 2>/dev/null || true
      /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:$key string $value" "$PLIST"
    done
    ok "launchd plist 已更新: $PLIST"
  fi
fi

# ══════════════════════════════════════════════════════════════════════
# Step 5: 重启 Gateway
# ══════════════════════════════════════════════════════════════════════
echo ""
info "Step 5/5: 重启 Gateway"
echo "------------------------------------------------------"

if openclaw gateway restart 2>/dev/null; then
  ok "Gateway 已重启"
else
  warn "Gateway 重启失败（可能未以 service 方式运行）"
  info "请手动重启: openclaw gateway restart"
fi

# ══════════════════════════════════════════════════════════════════════
# 完成
# ══════════════════════════════════════════════════════════════════════
echo ""
echo "======================================================"
echo -e "  ${GREEN}🎉 部署完成！${NC}"
echo "======================================================"
echo ""
echo "  已完成:"
echo "    ✅ 前置检查通过"
[[ "$SKIP_BUILD" != true ]] && echo "    ✅ 向量索引已建立"
echo "    ✅ Hook 已安装并启用"
echo "    ✅ 环境变量已注入（$PLATFORM）"
echo "    ✅ Gateway 已重启"
echo ""
echo "  验证方式:"
echo "    发一条消息给 OpenClaw，查看 BOOTSTRAP.md 是否包含 Skill 路由结果"
echo ""
echo "  日常维护:"
echo "    Skill 安装/更新/卸载后，运行:"
echo "    SKILL_ROUTER_BUCKET=$BUCKET ./scripts/build_all.sh"
echo ""
echo "======================================================"
