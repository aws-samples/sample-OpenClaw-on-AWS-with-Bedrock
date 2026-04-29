#!/bin/bash
# =============================================================================
# OpenClaw Enterprise — Admin Console 轻量部署
#
# 只更新 admin-console (前端 + 后端),不动 gateway/tenant-router/agent-container。
# 用于迭代管理端功能时的快速发布,耗时 1-2 分钟,IM 消息处理零中断。
#
# 用法:
#   bash deploy-admin-only.sh
#
# 会做什么:
#   1. 打包 enterprise/admin-console → S3
#   2. 通过 SSM 在 EC2 上:
#      - 下载新代码
#      - 前端 npm install + vite build
#      - 把 dist/ 和 server/ 替换到 /opt/admin-console/
#      - 重启 openclaw-admin 服务 (仅此一个)
#
# 不会做什么:
#   - 不重启 tenant-router / bedrock-proxy-h2 / openclaw-gateway
#   - 不重新打包 agent-container Docker 镜像
#   - 不重置 DynamoDB
#   - 不更新 CloudFormation 栈
#
# 首次完整部署请改用 deploy.sh。
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[info]${NC}  $*"; }
success() { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
error()   { echo -e "${RED}[error]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env ─────────────────────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
[ ! -f "$ENV_FILE" ] && error ".env not found at $ENV_FILE (run deploy.sh once first)"
set -o allexport
# shellcheck source=.env
source "$ENV_FILE"
set +o allexport

STACK_NAME="${STACK_NAME:-openclaw}"
REGION="${REGION:-us-east-1}"

# ── Discover stack resources (S3 bucket + EC2 instance) ──────────────────────
info "Discovering stack resources..."

# S3 bucket: 真实 CloudFormation output key 是 TenantWorkspaceBucketName
S3_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='TenantWorkspaceBucketName'].OutputValue" --output text 2>/dev/null || echo "")
# 向后兼容:尝试旧命名 WorkspaceBucket / S3Bucket
if [ -z "$S3_BUCKET" ] || [ "$S3_BUCKET" = "None" ]; then
  S3_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='WorkspaceBucket'].OutputValue" --output text 2>/dev/null || echo "")
fi
if [ -z "$S3_BUCKET" ] || [ "$S3_BUCKET" = "None" ]; then
  S3_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='S3Bucket'].OutputValue" --output text 2>/dev/null || echo "")
fi
[ -z "$S3_BUCKET" ] || [ "$S3_BUCKET" = "None" ] && \
  error "Cannot find S3 bucket from stack $STACK_NAME outputs"

INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text 2>/dev/null || echo "")
[ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "None" ] && \
  error "Cannot find EC2 instance id from stack $STACK_NAME outputs"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Admin Console — Lightweight Redeploy"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Stack:    $STACK_NAME"
echo "  Region:   $REGION"
echo "  EC2:      $INSTANCE_ID"
echo "  S3:       $S3_BUCKET"
echo ""

# ── Step 1: Package admin-console only ───────────────────────────────────────
info "[1/3] Packaging admin-console..."
TARBALL="/tmp/admin-console-$$.tar.gz"
COPYFILE_DISABLE=1 tar czf "$TARBALL" -C "$SCRIPT_DIR" admin-console 2>/dev/null || \
  tar czf "$TARBALL" -C "$SCRIPT_DIR" admin-console
SIZE=$(du -h "$TARBALL" | cut -f1)
success "  Packaged: $SIZE"

# ── Step 2: Upload to S3 ─────────────────────────────────────────────────────
info "[2/3] Uploading to S3..."
aws s3 cp "$TARBALL" "s3://${S3_BUCKET}/_deploy/admin-console.tar.gz" \
  --region "$REGION" --quiet
rm -f "$TARBALL"
success "  Uploaded"

# ── Step 3: Rebuild and restart on EC2 via SSM ───────────────────────────────
info "[3/3] Rebuilding admin console on EC2 and restarting openclaw-admin..."

CMD_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --region "$REGION" \
  --timeout-seconds 600 \
  --parameters "commands=[
    \"set -ex\",
    \"cd /tmp && rm -rf openclaw-admin-update && mkdir openclaw-admin-update && cd openclaw-admin-update\",
    \"aws s3 cp s3://${S3_BUCKET}/_deploy/admin-console.tar.gz . --region ${REGION}\",
    \"tar xzf admin-console.tar.gz\",
    \"chown -R ubuntu:ubuntu admin-console\",
    \"su - ubuntu -c 'source /home/ubuntu/.nvm/nvm.sh && cd /tmp/openclaw-admin-update/admin-console && npm install --no-audit --no-fund && npx vite build'\",
    \"/opt/admin-venv/bin/pip install -r /tmp/openclaw-admin-update/admin-console/server/requirements.txt --quiet\",
    \"rm -rf /opt/admin-console/dist /opt/admin-console/server\",
    \"cp -r /tmp/openclaw-admin-update/admin-console/dist    /opt/admin-console/dist\",
    \"cp -r /tmp/openclaw-admin-update/admin-console/server  /opt/admin-console/server\",
    \"cp    /tmp/openclaw-admin-update/admin-console/start.sh /opt/admin-console/start.sh\",
    \"chmod +x /opt/admin-console/start.sh\",
    \"chown -R ubuntu:ubuntu /opt/admin-console\",
    \"systemctl restart openclaw-admin\",
    \"sleep 2\",
    \"systemctl is-active openclaw-admin\",
    \"echo ADMIN_REDEPLOY_COMPLETE\"
  ]" \
  --query 'Command.CommandId' --output text)

info "  SSM command: $CMD_ID — polling (timeout 10 min)..."

for i in $(seq 1 20); do
  sleep 30
  STATUS=$(aws ssm get-command-invocation \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --region "$REGION" \
    --query 'Status' --output text 2>/dev/null || echo "Pending")
  case "$STATUS" in
    Success)
      success "  Admin console redeployed"
      break ;;
    Failed|Cancelled|TimedOut)
      STDERR=$(aws ssm get-command-invocation \
        --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
        --region "$REGION" --query 'StandardErrorContent' --output text 2>/dev/null | tail -30)
      STDOUT=$(aws ssm get-command-invocation \
        --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
        --region "$REGION" --query 'StandardOutputContent' --output text 2>/dev/null | tail -30)
      error "Redeploy failed ($STATUS):\nSTDOUT:\n$STDOUT\nSTDERR:\n$STDERR" ;;
    *)
      echo -n "." ;;
  esac
done
[ "$STATUS" != "Success" ] && error "Redeploy timed out after 10 min"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Admin Console Redeployed${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Open the admin console:"
echo "     aws ssm start-session --target $INSTANCE_ID --region $REGION \\"
echo "       --document-name AWS-StartPortForwardingSession \\"
echo "       --parameters 'portNumber=8099,localPortNumber=8099'"
echo "     → Open http://localhost:8099"
echo "     → 浏览器强制刷新 (Ctrl+Shift+R / Cmd+Shift+R) 以加载新前端"
echo ""
