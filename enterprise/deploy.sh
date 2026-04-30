#!/bin/bash
# =============================================================================
# OpenClaw Enterprise — One-Command Deploy
#
# Usage:
#   cp .env.example .env        # first time: fill in your values
#   bash deploy.sh                  # deploy everything
#   bash deploy.sh --skip-build     # re-deploy without rebuilding Docker image
#   bash deploy.sh --skip-seed      # re-deploy without re-seeding DynamoDB
#   bash deploy.sh --skip-services  # re-deploy without rebuilding services on EC2
#
# What this script does:
#   1. Validates prerequisites (AWS CLI, Docker, Python, Node.js)
#   2. Deploys CloudFormation (VPC or reuses existing, EC2, ECR, S3, IAM)
#   3. Builds and pushes Agent Container image to ECR
#   4. Creates AgentCore Runtime
#   5. Seeds DynamoDB with org data and positions
#   6. Uploads SOUL templates and knowledge docs to S3
#   7. Stores secrets in SSM + configures EC2
#   8. Deploys admin console + gateway services to EC2
# =============================================================================
set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[info]${NC}  $*"; }
success() { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
error()   { echo -e "${RED}[error]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load .env ─────────────────────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo ""
  echo -e "${YELLOW}No .env file found.${NC}"
  echo "  Run:  cp enterprise/.env.example enterprise/.env"
  echo "  Then fill in at least: STACK_NAME, REGION, ADMIN_PASSWORD"
  echo ""
  error ".env file not found at $ENV_FILE"
fi
set -o allexport
# shellcheck source=.env
source "$ENV_FILE"
set +o allexport

# ── Override from CLI flags ────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --skip-build)    SKIP_DOCKER_BUILD=true ;;
    --skip-seed)     SKIP_SEED=true ;;
    --skip-services) SKIP_SERVICES=true ;;
  esac
done

# ── Defaults ──────────────────────────────────────────────────────────────────
STACK_NAME="${STACK_NAME:-openclaw}"
REGION="${REGION:-us-east-1}"
MODEL="${MODEL:-global.anthropic.claude-sonnet-4-5-20250929-v1:0}"
INSTANCE_TYPE="${INSTANCE_TYPE:-c7g.large}"
KEY_PAIR="${KEY_PAIR:-}"
EXISTING_VPC_ID="${EXISTING_VPC_ID:-}"
EXISTING_SUBNET_ID="${EXISTING_SUBNET_ID:-}"
CREATE_VPC_ENDPOINTS="${CREATE_VPC_ENDPOINTS:-false}"
ALLOWED_SSH_CIDR="${ALLOWED_SSH_CIDR:-127.0.0.1/32}"
# IMPORTANT: Table name must match STACK_NAME (IAM policy: table/${StackName})
DYNAMODB_TABLE="${DYNAMODB_TABLE:-$STACK_NAME}"
DYNAMODB_REGION="${DYNAMODB_REGION:-$REGION}"
WORKSPACE_BUCKET_NAME="${WORKSPACE_BUCKET_NAME:-}"
SKIP_DOCKER_BUILD="${SKIP_DOCKER_BUILD:-false}"
SKIP_SEED="${SKIP_SEED:-false}"
SKIP_SERVICES="${SKIP_SERVICES:-false}"

# ── Validate required fields ──────────────────────────────────────────────────
[ -z "${ADMIN_PASSWORD:-}" ]  && error "ADMIN_PASSWORD is required. Set it in .env"

# If ExistingVpcId is set, ExistingSubnetId must also be set
if [ -n "$EXISTING_VPC_ID" ] && [ -z "$EXISTING_SUBNET_ID" ]; then
  error "EXISTING_SUBNET_ID is required when EXISTING_VPC_ID is set"
fi

# Auto-generate JWT_SECRET if not provided
if [ -z "${JWT_SECRET:-}" ]; then
  JWT_SECRET=$(openssl rand -hex 32)
  info "Generated JWT_SECRET (not stored to .env — will differ on redeploy)"
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || error "AWS credentials not configured. Run: aws configure"

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OpenClaw Enterprise — Deploy"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Stack:       $STACK_NAME"
echo "  Region:      $REGION"
echo "  Account:     $ACCOUNT_ID"
echo "  Model:       $MODEL"
echo "  Instance:    $INSTANCE_TYPE"
if [ -n "$EXISTING_VPC_ID" ]; then
echo "  VPC:         $EXISTING_VPC_ID (existing)"
echo "  Subnet:      $EXISTING_SUBNET_ID (existing)"
else
echo "  VPC:         (new — will be created)"
fi
echo "  VPC Endpoints: $CREATE_VPC_ENDPOINTS"
echo "  Skip build:    $SKIP_DOCKER_BUILD"
echo "  Skip seed:     $SKIP_SEED"
echo "  Skip services: $SKIP_SERVICES"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 1: Prerequisites check ───────────────────────────────────────────────
info "[1/8] Checking prerequisites..."

CLI_VERSION=$(aws --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1 || echo "0.0")
CLI_MAJOR=$(echo "$CLI_VERSION" | cut -d. -f1)
CLI_MINOR=$(echo "$CLI_VERSION" | cut -d. -f2)
if [ "$CLI_MAJOR" -lt 2 ] || { [ "$CLI_MAJOR" -eq 2 ] && [ "$CLI_MINOR" -lt 27 ]; }; then
  warn "AWS CLI $CLI_VERSION detected. bedrock-agentcore-control requires >= 2.27"
  warn "Run: pip install --upgrade awscli"
fi
success "AWS CLI $CLI_VERSION"

# Validate Bedrock model access (non-blocking warning)
# Strip "global." or "us." prefix for the API call (cross-region inference IDs aren't queryable directly)
_CHECK_MODEL=$(echo "$MODEL" | sed 's/^global\.\|^us\.//')
info "  Checking Bedrock model access for $MODEL..."
if aws bedrock get-foundation-model --model-identifier "$_CHECK_MODEL" --region "$REGION" \
    --query 'modelDetails.modelId' --output text &>/dev/null; then
  success "  Model $MODEL accessible"
else
  warn "Model $MODEL may not be enabled. Go to Bedrock console → Model access → Enable it."
  warn "Deployment will continue, but agents may fail to respond until model access is granted."
fi

# Docker build runs on the gateway EC2 (ARM64 Graviton), not locally.
# No local Docker required.

# ── Step 2: CloudFormation ────────────────────────────────────────────────────
info "[2/8] Deploying CloudFormation stack..."

CFN_PARAMS="ParameterKey=WorkspaceBucketName,ParameterValue=${WORKSPACE_BUCKET_NAME}"
CFN_PARAMS="$CFN_PARAMS ParameterKey=OpenClawModel,ParameterValue=${MODEL}"
CFN_PARAMS="$CFN_PARAMS ParameterKey=InstanceType,ParameterValue=${INSTANCE_TYPE}"
CFN_PARAMS="$CFN_PARAMS ParameterKey=KeyPairName,ParameterValue=${KEY_PAIR}"
CFN_PARAMS="$CFN_PARAMS ParameterKey=AllowedSSHCIDR,ParameterValue=${ALLOWED_SSH_CIDR}"
CFN_PARAMS="$CFN_PARAMS ParameterKey=CreateVPCEndpoints,ParameterValue=${CREATE_VPC_ENDPOINTS}"
CFN_PARAMS="$CFN_PARAMS ParameterKey=ExistingVpcId,ParameterValue=${EXISTING_VPC_ID}"
CFN_PARAMS="$CFN_PARAMS ParameterKey=ExistingSubnetId,ParameterValue=${EXISTING_SUBNET_ID}"


# Template is 70KB — exceeds 51,200 byte inline limit, must use S3
info "  Uploading CloudFormation template to S3..."
STAGING_BUCKET="openclaw-deploy-${ACCOUNT_ID}-${REGION}"

if ! aws s3api head-bucket --bucket "$STAGING_BUCKET" 2>/dev/null; then
  echo "[info]  Creating staging bucket s3://${STAGING_BUCKET}..."
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$STAGING_BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$STAGING_BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
  aws s3api put-public-access-block --bucket "$STAGING_BUCKET" \
    --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
fi

aws s3 cp "$SCRIPT_DIR/clawdbot-bedrock-agentcore-multitenancy.yaml" \
  "s3://${STAGING_BUCKET}/cfn/clawdbot-bedrock-agentcore-multitenancy.yaml" \
  --region "$REGION" --quiet
TEMPLATE_URL="https://${STAGING_BUCKET}.s3.${REGION}.amazonaws.com/cfn/clawdbot-bedrock-agentcore-multitenancy.yaml"
success "  Template uploaded"

# Try to create; if stack exists, do an update instead
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [ "$STACK_STATUS" = "DOES_NOT_EXIST" ]; then
  info "  Creating new stack (takes ~8 min)..."
  aws cloudformation create-stack \
    --stack-name "$STACK_NAME" \
    --template-url "$TEMPLATE_URL" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" \
    --parameters $CFN_PARAMS
  aws cloudformation wait stack-create-complete \
    --stack-name "$STACK_NAME" --region "$REGION"
else
  info "  Stack exists ($STACK_STATUS) — updating..."
  aws cloudformation update-stack \
    --stack-name "$STACK_NAME" \
    --template-url "$TEMPLATE_URL" \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" \
    --parameters $CFN_PARAMS 2>/dev/null && \
  aws cloudformation wait stack-update-complete \
    --stack-name "$STACK_NAME" --region "$REGION" || \
  info "  No stack changes needed"
fi

# Get stack outputs
ECR_URI=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`MultitenancyEcrRepositoryUri`].OutputValue' --output text)
EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AgentContainerExecutionRoleArn`].OutputValue' --output text)
S3_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`TenantWorkspaceBucketName`].OutputValue' --output text)
INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text)
ECS_CLUSTER=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnEcsClusterName`].OutputValue' --output text)
ECS_TASK_DEF=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnTaskDefinitionArn`].OutputValue' --output text)
ECS_TASK_SG=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnTaskSecurityGroupId`].OutputValue' --output text)
ECS_SUBNET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnSubnetId`].OutputValue' --output text)
EFS_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlwaysOnEFSId`].OutputValue' --output text)
CRON_LAMBDA_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`CronLambdaArn`].OutputValue' --output text 2>/dev/null || echo "")
CRON_SCHEDULER_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`CronSchedulerRoleArn`].OutputValue' --output text 2>/dev/null || echo "")

success "Stack ready — EC2: $INSTANCE_ID | S3: $S3_BUCKET"

# ── Step 3: Build and push Docker image ───────────────────────────────────────
# Always builds on the gateway EC2 (ARM64 Graviton, Docker pre-installed, fast ECR network).
# No local Docker required. Source code is packaged → S3 → EC2 build → ECR push.
if [ "$SKIP_DOCKER_BUILD" = "true" ]; then
  info "[3/8] Skipping Docker build (--skip-build)"
  IMAGE_COUNT=$(aws ecr describe-images --repository-name "${STACK_NAME}-multitenancy-agent" \
    --region "$REGION" --query 'length(imageDetails)' --output text 2>/dev/null || echo "0")
  if [ "$IMAGE_COUNT" = "0" ] || [ -z "$IMAGE_COUNT" ]; then
    warn "  ECR repo is empty — image must be pushed before creating the AgentCore Runtime."
    warn "  Re-run without --skip-build to trigger an EC2 build."
  else
    success "  ECR repo has $IMAGE_COUNT image(s)"
  fi
else
  info "[3/8] Building Agent Container on EC2 (~10-15 min, no local Docker needed)..."

  # Wait for EC2 to be SSM-reachable (it just launched from CloudFormation)
  info "  Waiting for EC2 SSM agent to become available..."
  for i in $(seq 1 30); do
    STATUS=$(aws ssm describe-instance-information \
      --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
      --region "$REGION" --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "")
    [ "$STATUS" = "Online" ] && break
    sleep 10
  done
  [ "$STATUS" != "Online" ] && error "EC2 SSM agent not reachable after 5 min. Check instance status."

  # Package source (agent-container + exec-agent Dockerfiles + the whole enterprise dir for context)
  info "  Packaging source code → S3..."
  TARBALL="/tmp/agent-build-$$.tar.gz"
  COPYFILE_DISABLE=1 tar czf "$TARBALL" \
    -C "$SCRIPT_DIR/.." \
    enterprise/agent-container \
    enterprise/exec-agent \
    enterprise/auth-agent 2>/dev/null || \
  tar czf "$TARBALL" \
    -C "$SCRIPT_DIR/.." \
    enterprise/agent-container \
    enterprise/exec-agent \
    enterprise/auth-agent
  aws s3 cp "$TARBALL" "s3://${S3_BUCKET}/_build/agent-build.tar.gz" \
    --region "$REGION" --quiet
  rm -f "$TARBALL"
  success "  Source uploaded to S3"

  # Run docker build on EC2 via SSM
  info "  Running docker build on EC2 (this takes 10-15 min)..."
  BUILD_CMD_ID=$(aws ssm send-command \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --region "$REGION" \
    --timeout-seconds 1200 \
    --parameters "commands=[
      \"set -ex\",
      \"ACCOUNT_ID=\$(aws sts get-caller-identity --query Account --output text)\",
      \"ECR_URI=${ECR_URI}\",
      \"cd /tmp && rm -rf agent-build && mkdir agent-build && cd agent-build\",
      \"aws s3 cp s3://${S3_BUCKET}/_build/agent-build.tar.gz . --region ${REGION}\",
      \"tar xzf agent-build.tar.gz\",
      \"aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin \${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com\",
      \"docker build -f enterprise/agent-container/Dockerfile -t \${ECR_URI}:latest enterprise/\",
      \"docker push \${ECR_URI}:latest\",
      \"echo BUILD_AND_PUSH_COMPLETE\"
    ]" \
    --query 'Command.CommandId' --output text)

  info "  SSM command: $BUILD_CMD_ID — polling for completion..."
  # Poll every 30s up to 20 minutes
  for i in $(seq 1 40); do
    sleep 30
    BUILD_STATUS=$(aws ssm get-command-invocation \
      --command-id "$BUILD_CMD_ID" \
      --instance-id "$INSTANCE_ID" \
      --region "$REGION" \
      --query 'Status' --output text 2>/dev/null || echo "Pending")
    case "$BUILD_STATUS" in
      Success)
        success "  Docker build + push complete"
        break ;;
      Failed|Cancelled|TimedOut)
        STDERR=$(aws ssm get-command-invocation \
          --command-id "$BUILD_CMD_ID" --instance-id "$INSTANCE_ID" \
          --region "$REGION" --query 'StandardErrorContent' --output text 2>/dev/null | tail -20)
        error "Docker build failed ($BUILD_STATUS):\n$STDERR" ;;
      *)
        echo -n "." ;;
    esac
  done
  [ "$BUILD_STATUS" != "Success" ] && error "Docker build timed out after 20 min"
fi

# ── Step 4: AgentCore Runtime ─────────────────────────────────────────────────
info "[4/8] Creating AgentCore Runtime..."

EXISTING_RUNTIME=$(aws ssm get-parameter \
  --name "/openclaw/${STACK_NAME}/runtime-id" \
  --query Parameter.Value --output text \
  --region "$REGION" 2>/dev/null || echo "")

if [ -n "$EXISTING_RUNTIME" ] && [ "$EXISTING_RUNTIME" != "UNKNOWN" ]; then
  info "  Updating existing runtime $EXISTING_RUNTIME..."
  aws bedrock-agentcore-control update-agent-runtime \
    --agent-runtime-id "$EXISTING_RUNTIME" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}:latest\"}}" \
    --role-arn "$EXECUTION_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":300,"maxLifetime":3600}' \
    --environment-variables \
      STACK_NAME="${STACK_NAME}",AWS_REGION="${REGION}",S3_BUCKET="${S3_BUCKET}",\
BEDROCK_MODEL_ID="${MODEL}",DYNAMODB_TABLE="${DYNAMODB_TABLE}",DYNAMODB_REGION="${DYNAMODB_REGION}",\
EVENTBRIDGE_SCHEDULE_GROUP="openclaw-cron",CRON_LAMBDA_ARN="${CRON_LAMBDA_ARN}",\
EVENTBRIDGE_ROLE_ARN="${CRON_SCHEDULER_ROLE_ARN}",IDENTITY_TABLE_NAME="${DYNAMODB_TABLE}" \
    --region "$REGION" &>/dev/null || warn "  Runtime update failed — may need manual update in console"
  RUNTIME_ID="$EXISTING_RUNTIME"
else
  info "  Creating new runtime..."
  RUNTIME_ID=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "${STACK_NAME//-/_}_runtime" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}:latest\"}}" \
    --role-arn "$EXECUTION_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":300,"maxLifetime":3600}' \
    --environment-variables \
      STACK_NAME="${STACK_NAME}",AWS_REGION="${REGION}",S3_BUCKET="${S3_BUCKET}",\
BEDROCK_MODEL_ID="${MODEL}",DYNAMODB_TABLE="${DYNAMODB_TABLE}",DYNAMODB_REGION="${DYNAMODB_REGION}",\
EVENTBRIDGE_SCHEDULE_GROUP="openclaw-cron",CRON_LAMBDA_ARN="${CRON_LAMBDA_ARN}",\
EVENTBRIDGE_ROLE_ARN="${CRON_SCHEDULER_ROLE_ARN}",IDENTITY_TABLE_NAME="${DYNAMODB_TABLE}" \
    --region "$REGION" \
    --query 'agentRuntimeId' --output text)

  aws ssm put-parameter \
    --name "/openclaw/${STACK_NAME}/runtime-id" \
    --value "$RUNTIME_ID" --type String --overwrite \
    --region "$REGION" &>/dev/null
fi
success "Runtime: $RUNTIME_ID"

# Store runtime-id on the EC2 via SSM Parameter (so tenant_router can read it)
aws ssm put-parameter \
  --name "/openclaw/${STACK_NAME}/runtime-id" \
  --value "$RUNTIME_ID" --type String --overwrite \
  --region "$REGION" &>/dev/null

# Update ALL AgentCore runtimes to use new Docker image (not just the default one).
# Production has 4 runtimes (Standard, Restricted, Engineering, Executive) —
# all must point to the latest image after a Docker rebuild.
info "  Updating all AgentCore runtimes to latest image..."
ALL_RUNTIMES=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query 'agentRuntimes[*].agentRuntimeId' --output text --region "$REGION" 2>/dev/null || echo "")
UPDATED=0
for RT_ID in $ALL_RUNTIMES; do
  [ -z "$RT_ID" ] && continue
  [ "$RT_ID" = "$RUNTIME_ID" ] && continue  # already updated above
  # Get existing role for this runtime (each tier may have a different execution role)
  RT_ROLE=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RT_ID" --query 'roleArn' --output text \
    --region "$REGION" 2>/dev/null || echo "$EXECUTION_ROLE_ARN")
  aws bedrock-agentcore-control update-agent-runtime \
    --agent-runtime-id "$RT_ID" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}:latest\"}}" \
    --role-arn "$RT_ROLE" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --region "$REGION" &>/dev/null && UPDATED=$((UPDATED+1)) || true
done
[ $UPDATED -gt 0 ] && info "  Updated $UPDATED additional runtime(s) to new image"

# ── Step 4.5: Fargate Tier Services ──────────────────────────────────────────
# Create ECS Fargate services for always-on deployment mode (one per security tier).
# Services start with desiredCount=0 — admin enables per-position via Security Center.
info "[4.5/8] Setting up Fargate tier services..."
if [ "$SKIP_SERVICES" = "true" ]; then
  info "  Skipping Fargate tier services (--skip-services)"
else

ECS_CLUSTER="${STACK_NAME}-always-on"
BASE_TASK_DEF="${STACK_NAME}-always-on-agent"

# Read network config from CloudFormation outputs
SUBNET_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AlwaysOnSubnetId'].OutputValue" \
  --output text --region "$REGION" 2>/dev/null || echo "")
TASK_SG=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='AlwaysOnTaskSecurityGroupId'].OutputValue" \
  --output text --region "$REGION" 2>/dev/null || echo "")

if [ -z "$SUBNET_ID" ] || [ "$SUBNET_ID" = "None" ]; then
  warn "  Could not read SubnetId from stack outputs — Fargate setup skipped"
  warn "  (Run CloudFormation update first, or set ECS_SUBNET_ID manually)"
else
  # Store ECS config in SSM for admin_always_on.py
  aws ssm put-parameter --name "/openclaw/${STACK_NAME}/ecs/cluster-name" \
    --value "$ECS_CLUSTER" --type String --overwrite --region "$REGION" &>/dev/null
  aws ssm put-parameter --name "/openclaw/${STACK_NAME}/ecs/subnet-id" \
    --value "$SUBNET_ID" --type String --overwrite --region "$REGION" &>/dev/null
  [ -n "$TASK_SG" ] && [ "$TASK_SG" != "None" ] && \
    aws ssm put-parameter --name "/openclaw/${STACK_NAME}/ecs/task-sg-id" \
      --value "$TASK_SG" --type String --overwrite --region "$REGION" &>/dev/null

  # Define tiers: name:model:guardrailId
  # desiredCount=0 for all — admin activates via Security Center
  # Using case statements instead of associative arrays for bash 3.x (macOS) compatibility
  _GUARDRAIL_MODERATE="${GUARDRAIL_MODERATE_ID:-}"
  _GUARDRAIL_STRICT="${GUARDRAIL_STRICT_ID:-}"

  for TIER_NAME in standard restricted engineering executive; do
    SERVICE_NAME="${STACK_NAME}-tier-${TIER_NAME}"
    case "$TIER_NAME" in
      standard)    TIER_MODEL="${MODEL:-global.amazon.nova-2-lite-v1:0}"; TIER_GUARDRAIL="$_GUARDRAIL_MODERATE" ;;
      restricted)  TIER_MODEL="us.deepseek.r1-v1:0"; TIER_GUARDRAIL="$_GUARDRAIL_STRICT" ;;
      engineering) TIER_MODEL="global.anthropic.claude-sonnet-4-5-20250929-v1:0"; TIER_GUARDRAIL="" ;;
      executive)   TIER_MODEL="global.anthropic.claude-sonnet-4-6"; TIER_GUARDRAIL="" ;;
    esac

    # Register tier-specific task definition with tier env vars
    TIER_FAMILY="${STACK_NAME}-tier-${TIER_NAME}"
    case "$TIER_NAME" in
      standard)    TIER_MODEL="${MODEL:-global.amazon.nova-2-lite-v1:0}"; TIER_GUARDRAIL="${GUARDRAIL_MODERATE_ID:-}" ;;
      restricted)  TIER_MODEL="us.deepseek.r1-v1:0";                     TIER_GUARDRAIL="${GUARDRAIL_STRICT_ID:-}" ;;
      engineering) TIER_MODEL="global.anthropic.claude-sonnet-4-5-20250929-v1:0"; TIER_GUARDRAIL="" ;;
      executive)   TIER_MODEL="global.anthropic.claude-sonnet-4-6";       TIER_GUARDRAIL="" ;;
    esac
    # Build container environment JSON
    TIER_ENV=$(cat <<ENVJSON


[
  {"name":"STACK_NAME","value":"${STACK_NAME}"},
  {"name":"AWS_REGION","value":"${REGION}"},
  {"name":"S3_BUCKET","value":"${S3_BUCKET}"},
  {"name":"DYNAMODB_TABLE","value":"${DYNAMODB_TABLE:-$STACK_NAME}"},
  {"name":"DYNAMODB_REGION","value":"${DYNAMODB_REGION:-$REGION}"},
  {"name":"PORT","value":"8080"},
  {"name":"EFS_ENABLED","value":"true"},
  {"name":"SYNC_INTERVAL","value":"120"},
  {"name":"BEDROCK_MODEL_ID","value":"${TIER_MODEL}"},
  {"name":"GUARDRAIL_ID","value":"${TIER_GUARDRAIL}"},
  {"name":"FARGATE_TIER","value":"${TIER_NAME}"},
  {"name":"SHARED_AGENT_ID","value":"tier-${TIER_NAME}"}
]
ENVJSON
)

    # Get base task definition details
    BASE_TD_ARN=$(aws ecs describe-task-definition --task-definition "$BASE_TASK_DEF" \
      --query 'taskDefinition.taskDefinitionArn' --output text --region "$REGION" 2>/dev/null || echo "")

    if [ -z "$BASE_TD_ARN" ] || [ "$BASE_TD_ARN" = "None" ]; then
      warn "  Base task definition $BASE_TASK_DEF not found — tier $TIER_NAME skipped"
      continue
    fi

    # Register new task definition revision for this tier
    aws ecs register-task-definition \
      --family "$TIER_FAMILY" \
      --task-role-arn "$(aws ecs describe-task-definition --task-definition "$BASE_TASK_DEF" \
        --query 'taskDefinition.taskRoleArn' --output text --region "$REGION")" \
      --execution-role-arn "$(aws ecs describe-task-definition --task-definition "$BASE_TASK_DEF" \
        --query 'taskDefinition.executionRoleArn' --output text --region "$REGION")" \
      --network-mode awsvpc \
      --requires-compatibilities FARGATE \
      --cpu "512" --memory "1024" \
      --runtime-platform cpuArchitecture=ARM64,operatingSystemFamily=LINUX \
      --container-definitions "$(aws ecs describe-task-definition --task-definition "$BASE_TASK_DEF" \
        --query 'taskDefinition.containerDefinitions' --output json --region "$REGION" \
        | python3 -c "
import sys, json
defs = json.load(sys.stdin)
env = json.loads('''${TIER_ENV}''')
for d in defs:
    if d.get('name') == 'always-on-agent':
        d['environment'] = env
        for k in ['cpu','status','taskDefinitionArn','containerInstanceArn','networkBindings','requiredAttributes']:
            d.pop(k, None)
print(json.dumps(defs))
")" \
      --volumes "$(aws ecs describe-task-definition --task-definition "$BASE_TASK_DEF" \
        --query 'taskDefinition.volumes' --output json --region "$REGION")" \
      --region "$REGION" &>/dev/null \
      && info "  Registered task definition: $TIER_FAMILY" \
      || warn "  Failed to register task definition: $TIER_FAMILY"

    # Create ECS Service (if not exists) with desiredCount=0
    EXISTING_SVC=$(aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$SERVICE_NAME" \
      --query 'services[?status==`ACTIVE`].serviceName' --output text --region "$REGION" 2>/dev/null || echo "")

    if [ -z "$EXISTING_SVC" ] || [ "$EXISTING_SVC" = "None" ]; then
      aws ecs create-service \
        --cluster "$ECS_CLUSTER" \
        --service-name "$SERVICE_NAME" \
        --task-definition "$TIER_FAMILY" \
        --desired-count 0 \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_ID],securityGroups=[$TASK_SG],assignPublicIp=ENABLED}" \
        --tags "key=tier,value=$TIER_NAME" "key=stack,value=$STACK_NAME" \
        --region "$REGION" &>/dev/null \
        && info "  Created service: $SERVICE_NAME (desiredCount=0)" \
        || warn "  Failed to create service: $SERVICE_NAME"
    else
      # Update existing service with new task definition
      aws ecs update-service \
        --cluster "$ECS_CLUSTER" \
        --service "$SERVICE_NAME" \
        --task-definition "$TIER_FAMILY" \
        --region "$REGION" &>/dev/null \
        && info "  Updated service: $SERVICE_NAME" \
        || warn "  Failed to update service: $SERVICE_NAME"
    fi
  done

  success "Fargate tier services configured (desiredCount=0, activate via Security Center)"
  fi
fi

# ── Step 5: Upload SOUL templates and knowledge docs ──────────────────────────
# SOUL architecture: workspace_assembler.py merges Global + Position + PERSONAL_SOUL.md
# into SOUL.md (single write). server.py does NOT modify SOUL.md.
# Per-employee PERSONAL_SOUL.md is seeded by seed_workspaces.py (Step 6).
info "[5/8] Uploading templates and knowledge to S3..."

export AWS_REGION="$REGION"
export S3_BUCKET

aws s3 sync "$SCRIPT_DIR/agent-container/templates/" \
  "s3://${S3_BUCKET}/_shared/templates/" --region "$REGION" --quiet

# Upload global SOUL if exists (legacy path)
GLOBAL_SOUL="$SCRIPT_DIR/agent-container/templates/default.md"
[ -f "$GLOBAL_SOUL" ] && \
  aws s3 cp "$GLOBAL_SOUL" "s3://${S3_BUCKET}/_shared/soul/global/SOUL.md" \
    --region "$REGION" --quiet

# Upload full SOUL templates (global: SOUL.md, AGENTS.md, TOOLS.md + per-position)
SOUL_TEMPLATES="$SCRIPT_DIR/admin-console/server/soul-templates"
if [ -d "$SOUL_TEMPLATES/global" ]; then
  aws s3 sync "$SOUL_TEMPLATES/global/" \
    "s3://${S3_BUCKET}/_shared/soul/global/" --region "$REGION" --quiet
  info "  Global SOUL templates uploaded (SOUL.md, AGENTS.md, TOOLS.md)"
fi
if [ -d "$SOUL_TEMPLATES/positions" ]; then
  aws s3 sync "$SOUL_TEMPLATES/positions/" \
    "s3://${S3_BUCKET}/_shared/soul/positions/" --region "$REGION" --quiet
  info "  Position SOUL templates uploaded ($(ls -d "$SOUL_TEMPLATES/positions"/*/ 2>/dev/null | wc -l | tr -d ' ') positions)"
fi

success "Templates uploaded to s3://${S3_BUCKET}/"

# ── Step 5.5: Deploy Cron Lambda code ────────────────────────────────────────
if [ -n "$CRON_LAMBDA_ARN" ] && [ "$CRON_LAMBDA_ARN" != "None" ]; then
  info "[5.5/8] Deploying Cron Lambda code..."
  CRON_LAMBDA_DIR="$SCRIPT_DIR/lambda/cron"
  if [ -f "$CRON_LAMBDA_DIR/index.py" ]; then
    CRON_ZIP="/tmp/cron-lambda-$$.zip"
    (cd "$CRON_LAMBDA_DIR" && zip -q "$CRON_ZIP" index.py)
    CRON_FUNCTION_NAME="${STACK_NAME}-cron-executor"

    # Read AGENTCORE_RUNTIME_ARN from SSM (runtime-id → ARN)
    AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}"

    # Update Lambda code
    aws lambda update-function-code \
      --function-name "$CRON_FUNCTION_NAME" \
      --zip-file "fileb://$CRON_ZIP" \
      --region "$REGION" &>/dev/null \
      && success "  Cron Lambda code deployed" \
      || warn "  Cron Lambda code update failed"

    # Wait for update to complete before updating config
    aws lambda wait function-updated \
      --function-name "$CRON_FUNCTION_NAME" \
      --region "$REGION" 2>/dev/null || sleep 5

    # Update Lambda environment variables
    aws lambda update-function-configuration \
      --function-name "$CRON_FUNCTION_NAME" \
      --environment "Variables={DYNAMODB_TABLE=${DYNAMODB_TABLE},AWS_REGION=${REGION},AGENTCORE_RUNTIME_ARN=${AGENTCORE_RUNTIME_ARN},AGENTCORE_QUALIFIER=DEFAULT,LAMBDA_TIMEOUT_SECONDS=600}" \
      --region "$REGION" &>/dev/null \
      && success "  Cron Lambda config updated" \
      || warn "  Cron Lambda config update failed"

    # Store cron config in SSM for container env derivation
    aws ssm put-parameter --name "/openclaw/${STACK_NAME}/cron/lambda-arn" \
      --value "$CRON_LAMBDA_ARN" --type String --overwrite --region "$REGION" &>/dev/null
    aws ssm put-parameter --name "/openclaw/${STACK_NAME}/cron/scheduler-role-arn" \
      --value "$CRON_SCHEDULER_ROLE_ARN" --type String --overwrite --region "$REGION" &>/dev/null

    rm -f "$CRON_ZIP"
  else
    warn "  lambda/cron/index.py not found — skipping cron Lambda deploy"
  fi
else
  info "[5.5/8] Cron Lambda not found in stack outputs — skipping (run CloudFormation update first)"
fi

# ── Step 6: DynamoDB table + Seed ─────────────────────────────────────────────
# Create table if it doesn't exist (idempotent — no-op if already created)
TABLE_STATUS=$(aws dynamodb describe-table --table-name "$DYNAMODB_TABLE" \
  --region "$DYNAMODB_REGION" --query 'Table.TableStatus' --output text 2>/dev/null || echo "NOT_FOUND")
if [ "$TABLE_STATUS" = "NOT_FOUND" ]; then
  info "[6/8] Creating DynamoDB table $DYNAMODB_TABLE in $DYNAMODB_REGION..."
  aws dynamodb create-table \
    --table-name "$DYNAMODB_TABLE" \
    --attribute-definitions \
      AttributeName=PK,AttributeType=S \
      AttributeName=SK,AttributeType=S \
      AttributeName=GSI1PK,AttributeType=S \
      AttributeName=GSI1SK,AttributeType=S \
    --key-schema \
      AttributeName=PK,KeyType=HASH \
      AttributeName=SK,KeyType=RANGE \
    --global-secondary-indexes '[{
      "IndexName":"GSI1",
      "KeySchema":[
        {"AttributeName":"GSI1PK","KeyType":"HASH"},
        {"AttributeName":"GSI1SK","KeyType":"RANGE"}
      ],
      "Projection":{"ProjectionType":"ALL"}
    }]' \
    --billing-mode PAY_PER_REQUEST \
    --region "$DYNAMODB_REGION" &>/dev/null
  info "  Waiting for table to become active..."
  aws dynamodb wait table-exists --table-name "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION"
  success "DynamoDB table created: $DYNAMODB_TABLE"
else
  success "DynamoDB table exists: $DYNAMODB_TABLE ($TABLE_STATUS)"
fi

if [ "$SKIP_SEED" = "true" ]; then
  info "[6/8] Skipping DynamoDB seed (--skip-seed)"
else
  info "[6/8] Seeding DynamoDB..."
  SEED_DIR="$SCRIPT_DIR/admin-console/server"

  # Store ADMIN_PASSWORD in SSM (EC2 reads it on startup)
  aws ssm put-parameter \
    --name "/openclaw/${STACK_NAME}/admin-password" \
    --value "$ADMIN_PASSWORD" --type SecureString --overwrite \
    --region "$REGION" &>/dev/null
  success "  ADMIN_PASSWORD stored in SSM"

  if [ -n "$JWT_SECRET" ]; then
    aws ssm put-parameter \
      --name "/openclaw/${STACK_NAME}/jwt-secret" \
      --value "$JWT_SECRET" --type SecureString --overwrite \
      --region "$REGION" &>/dev/null
    success "  JWT_SECRET stored in SSM"
  fi

  cd "$SEED_DIR"
  # Use a temporary venv for seed scripts (avoids PEP 668 / macOS Homebrew conflicts)
  SEED_VENV="/tmp/openclaw-seed-venv"
  if [ ! -d "$SEED_VENV" ]; then
    python3 -m venv "$SEED_VENV"
  fi
  "$SEED_VENV/bin/pip" install -q -r requirements.txt
  # Use the venv's python for all seed commands
  SEED_PYTHON="$SEED_VENV/bin/python"
  AWS_REGION="$DYNAMODB_REGION" $SEED_PYTHON seed_dynamodb.py --table "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION" && \
    success "  Org data seeded (employees, positions, departments)"

  AWS_REGION="$DYNAMODB_REGION" $SEED_PYTHON seed_roles.py --table "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION" && \
    success "  Roles seeded (admin/manager/employee)"

  AWS_REGION="$DYNAMODB_REGION" $SEED_PYTHON seed_settings.py --table "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION" 2>/dev/null && \
    success "  Settings seeded" || warn "  seed_settings.py skipped (not found)"

  AWS_REGION="$DYNAMODB_REGION" $SEED_PYTHON seed_knowledge.py --table "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION" 2>/dev/null && \
    success "  Knowledge base metadata seeded" || warn "  seed_knowledge.py skipped"

  AWS_REGION="$REGION" S3_BUCKET="$S3_BUCKET" \
    $SEED_PYTHON seed_knowledge_docs.py --bucket "$S3_BUCKET" --region "$REGION" && \
    success "  Knowledge docs uploaded"

  AWS_REGION="$REGION" S3_BUCKET="$S3_BUCKET" \
    $SEED_PYTHON seed_workspaces.py --bucket "$S3_BUCKET" --region "$REGION" 2>/dev/null && \
    success "  Employee workspaces created" || warn "  seed_workspaces.py skipped"

  # Note: tenant→position mappings are now in DynamoDB (EMP# records have positionId).
  # seed_ssm_tenants.py is no longer needed.

  # Seed skill catalog to S3
  AWS_REGION="$REGION" S3_BUCKET="$S3_BUCKET" \
    $SEED_PYTHON seed_skills_final.py && \
    success "  Skill catalog seeded to S3" || warn "  seed_skills_final.py skipped"
fi

# ── Step 7: Store secrets + write /etc/openclaw/env ──────────────────────────
info "[7/8] Storing secrets in SSM + writing EC2 env file..."

aws ssm put-parameter \
  --name "/openclaw/${STACK_NAME}/admin-password" \
  --value "$ADMIN_PASSWORD" \
  --type SecureString \
  --overwrite \
  --region "$REGION" > /dev/null 2>&1
success "  admin-password stored"

aws ssm put-parameter \
  --name "/openclaw/${STACK_NAME}/jwt-secret" \
  --value "$JWT_SECRET" \
  --type SecureString \
  --overwrite \
  --region "$REGION" > /dev/null 2>&1
success "  jwt-secret stored"

# Write env file locally, upload to S3, then pull onto EC2 via SSM.
# This avoids fragile JSON-in-JSON escaping — variable values with quotes,
# dollars, or backslashes are written safely to a plain file.
ENV_TMPFILE="/tmp/openclaw-env-$$"
cat > "$ENV_TMPFILE" <<ENVEOF
STACK_NAME=${STACK_NAME}
AWS_REGION=${REGION}
SSM_REGION=${REGION}
GATEWAY_REGION=${REGION}
S3_BUCKET=${S3_BUCKET}
DYNAMODB_TABLE=${DYNAMODB_TABLE}
DYNAMODB_REGION=${DYNAMODB_REGION}
AGENTCORE_RUNTIME_ID=${RUNTIME_ID}
BEDROCK_MODEL_ID=${MODEL}
ECS_CLUSTER=${ECS_CLUSTER}
ECS_TASK_DEF=${ECS_TASK_DEF}
ECS_TASK_SG=${ECS_TASK_SG}
ECS_SUBNET=${ECS_SUBNET}
EFS_ID=${EFS_ID}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
CRON_LAMBDA_ARN=${CRON_LAMBDA_ARN}
CRON_SCHEDULER_ROLE_ARN=${CRON_SCHEDULER_ROLE_ARN}
ENVEOF
aws s3 cp "$ENV_TMPFILE" "s3://${S3_BUCKET}/_deploy/env" --region "$REGION" --quiet
rm -f "$ENV_TMPFILE"
success "  env file uploaded to S3"

info "  Installing /etc/openclaw/env on EC2..."
ENV_CMD_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --region "$REGION" \
  --parameters 'commands=["mkdir -p /etc/openclaw","aws s3 cp s3://'"${S3_BUCKET}"'/_deploy/env /etc/openclaw/env --region '"${REGION}"'","echo ENV_WRITTEN"]' \
  --query 'Command.CommandId' --output text)

# Wait for env file write to complete (Step 8 depends on it)
info "  Waiting for env file installation..."
for i in $(seq 1 20); do
  sleep 5
  ENV_STATUS=$(aws ssm get-command-invocation \
    --command-id "$ENV_CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --region "$REGION" \
    --query 'Status' --output text 2>/dev/null || echo "Pending")
  case "$ENV_STATUS" in
    Success)
      success "  /etc/openclaw/env written on EC2"
      break ;;
    Failed|Cancelled|TimedOut)
      error "  env file installation failed ($ENV_STATUS)" ;;
    *)
      echo -n "." ;;
  esac
done
[ "$ENV_STATUS" != "Success" ] && error "env file installation timed out"

# ── Step 8: Deploy services to EC2 ──────────────────────────────────────────
if [ "$SKIP_SERVICES" = "true" ]; then
  info "[8/8] Skipping service deployment (--skip-services)"
else
  info "[8/8] Deploying admin console + gateway services to EC2..."

  # Package admin-console + gateway + ec2-setup.sh → S3
  info "  Packaging services → S3..."
  SVC_TARBALL="/tmp/services-$$.tar.gz"
  COPYFILE_DISABLE=1 tar czf "$SVC_TARBALL" \
    -C "$SCRIPT_DIR/.." \
    enterprise/admin-console \
    enterprise/gateway \
    enterprise/ec2-setup.sh 2>/dev/null || \
  tar czf "$SVC_TARBALL" \
    -C "$SCRIPT_DIR/.." \
    enterprise/admin-console \
    enterprise/gateway \
    enterprise/ec2-setup.sh
  aws s3 cp "$SVC_TARBALL" "s3://${S3_BUCKET}/_deploy/services.tar.gz" \
    --region "$REGION" --quiet
  rm -f "$SVC_TARBALL"
  success "  Services uploaded to S3"

  # Run ec2-setup.sh on EC2 via SSM
  info "  Running ec2-setup.sh on EC2 (builds admin console, installs services)..."
  SVC_CMD_ID=$(aws ssm send-command \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --region "$REGION" \
    --timeout-seconds 900 \
    --parameters "commands=[
      \"set -ex\",
      \"cd /tmp && rm -rf openclaw-services && mkdir openclaw-services && cd openclaw-services\",
      \"aws s3 cp s3://${S3_BUCKET}/_deploy/services.tar.gz . --region ${REGION}\",
      \"tar xzf services.tar.gz\",
      \"bash enterprise/ec2-setup.sh\"
    ]" \
    --query 'Command.CommandId' --output text)

  info "  SSM command: $SVC_CMD_ID — polling for completion..."
  # Poll every 30s up to 15 minutes
  for i in $(seq 1 30); do
    sleep 30
    SVC_STATUS=$(aws ssm get-command-invocation \
      --command-id "$SVC_CMD_ID" \
      --instance-id "$INSTANCE_ID" \
      --region "$REGION" \
      --query 'Status' --output text 2>/dev/null || echo "Pending")
    case "$SVC_STATUS" in
      Success)
        success "  Service deployment complete"
        break ;;
      Failed|Cancelled|TimedOut)
        STDERR=$(aws ssm get-command-invocation \
          --command-id "$SVC_CMD_ID" --instance-id "$INSTANCE_ID" \
          --region "$REGION" --query 'StandardErrorContent' --output text 2>/dev/null | tail -20)
        error "Service deployment failed ($SVC_STATUS):\n$STDERR" ;;
      *)
        echo -n "." ;;
    esac
  done
  [ "$SVC_STATUS" != "Success" ] && error "Service deployment timed out after 15 min"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Deployment Complete!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Stack:      $STACK_NAME"
echo "  Runtime:    $RUNTIME_ID"
echo "  S3:         $S3_BUCKET"
echo "  EC2:        $INSTANCE_ID"
echo ""
echo "  Access Admin Console:"
echo "     aws ssm start-session --target $INSTANCE_ID --region $REGION \\"
echo "       --document-name AWS-StartPortForwardingSession \\"
echo "       --parameters 'portNumber=8099,localPortNumber=8099'"
echo "     → Open http://localhost:8099"
echo "     → Login with your seeded admin employee ID / password: (your ADMIN_PASSWORD)"
echo "     → First login requires setting a personal password"
echo ""
echo "  Connect IM bots (one-time, in OpenClaw Gateway UI):"
echo "     aws ssm start-session --target $INSTANCE_ID --region $REGION \\"
echo "       --document-name AWS-StartPortForwardingSession \\"
echo "       --parameters 'portNumber=18789,localPortNumber=18789'"
echo "     → Open http://localhost:18789 → Channels → Add bot"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
