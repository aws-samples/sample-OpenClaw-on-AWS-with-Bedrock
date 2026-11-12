#!/usr/bin/env bash
# =============================================================================
# OpenClaw Enterprise — Teardown
#
# Usage:
#   bash destroy.sh                         # show the deletion plan only
#   bash destroy.sh --confirm               # remove infrastructure; retain data
#   bash destroy.sh --confirm --purge-data  # permanently remove all project data
#
# Safety model:
#   * Dry-run is the default.
#   * --confirm still requires typing the exact stack name interactively.
#   * Without --purge-data, the S3 workspace bucket, EFS filesystem, DynamoDB
#     table, and ECR repository are retained to protect application data.
#   * The deployment staging bucket is shared by stacks in an account/region and
#     is NEVER removed unless --delete-shared-staging-bucket is also provided.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[info]${NC}  $*"; }
success() { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
error()   { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: bash destroy.sh [options]

Options:
  --confirm                       Execute the teardown after an exact-name prompt.
  --purge-data                    Also permanently delete S3/EFS/DynamoDB data and ECR images.
  --delete-shared-staging-bucket  Also delete openclaw-deploy-ACCOUNT-REGION (normally retained).
  --help                          Show this help.

The default is a dry-run. The deployment staging bucket is potentially shared by
multiple OpenClaw stacks, so it requires its own explicit deletion flag.
EOF
}

EXECUTE=false
PURGE_DATA=false
DELETE_STAGING_BUCKET=false

for arg in "$@"; do
  case "$arg" in
    --confirm) EXECUTE=true ;;
    --purge-data) PURGE_DATA=true ;;
    --delete-shared-staging-bucket) DELETE_STAGING_BUCKET=true ;;
    --help|-h) usage; exit 0 ;;
    *) error "Unknown option: $arg (use --help)" ;;
  esac
done

[ -f "$ENV_FILE" ] || error "Missing $ENV_FILE. Copy .env.example and configure the target stack first."
set -o allexport
# shellcheck disable=SC1090
source "$ENV_FILE"
set +o allexport

STACK_NAME="${STACK_NAME:-openclaw}"
REGION="${REGION:-us-east-1}"
DYNAMODB_TABLE="${DYNAMODB_TABLE:-$STACK_NAME}"
DYNAMODB_REGION="${DYNAMODB_REGION:-$REGION}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || error "AWS credentials are not configured. Run aws configure first."
STAGING_BUCKET="openclaw-deploy-${ACCOUNT_ID}-${REGION}"

STACK_STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")
[ "$STACK_STATUS" != "DOES_NOT_EXIST" ] || error "CloudFormation stack '$STACK_NAME' was not found in $REGION. Refusing to guess resource names."
case "$STACK_STATUS" in
  DELETE_COMPLETE|DELETE_IN_PROGRESS) error "Stack '$STACK_NAME' is already being deleted or was deleted." ;;
esac

stack_output() {
  local key="$1"
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" --output text 2>/dev/null || true
}

S3_BUCKET=$(stack_output "TenantWorkspaceBucketName")
ECR_URI=$(stack_output "MultitenancyEcrRepositoryUri")
ECS_CLUSTER=$(stack_output "AlwaysOnEcsClusterName")
EFS_ID=$(stack_output "AlwaysOnEFSId")
CRON_LAMBDA_ARN=$(stack_output "CronLambdaArn")

[ -n "$S3_BUCKET" ] && [ "$S3_BUCKET" != "None" ] || error "Unable to resolve TenantWorkspaceBucketName from stack outputs."
[ -n "$ECR_URI" ] && [ "$ECR_URI" != "None" ] || error "Unable to resolve MultitenancyEcrRepositoryUri from stack outputs."
ECR_REPOSITORY="${ECR_URI##*/}"
[ -n "$ECS_CLUSTER" ] && [ "$ECS_CLUSTER" != "None" ] || ECS_CLUSTER="${STACK_NAME}-always-on"
[ -n "$EFS_ID" ] && [ "$EFS_ID" != "None" ] || EFS_ID=""
[ -n "$CRON_LAMBDA_ARN" ] && [ "$CRON_LAMBDA_ARN" != "None" ] || CRON_LAMBDA_ARN=""

RUNTIME_ID=$(aws ssm get-parameter --name "/openclaw/${STACK_NAME}/runtime-id" --region "$REGION" \
  --query 'Parameter.Value' --output text 2>/dev/null || true)
[ "$RUNTIME_ID" = "None" ] && RUNTIME_ID=""

run() {
  if [ "$EXECUTE" = false ]; then
    printf '  DRY RUN: '; printf '%q ' "$@"; printf '\n'
  else
    "$@"
  fi
}

purge_versioned_bucket() {
  local bucket="$1"
  if [ "$EXECUTE" = false ]; then
    info "Would permanently delete every object version and delete marker in s3://${bucket}"
    return
  fi

  info "Permanently deleting all object versions in s3://${bucket}..."
  python3 - "$bucket" "$REGION" <<'PY'
import json
import subprocess
import sys

bucket, region = sys.argv[1:]

def aws(*args):
    proc = subprocess.run(["aws", *args, "--region", region], text=True, capture_output=True)
    if proc.returncode:
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return json.loads(proc.stdout or "{}")

markers = []
while True:
    page = aws("s3api", "list-object-versions", "--bucket", bucket, "--output", "json", *markers)
    objects = [
        {"Key": item["Key"], "VersionId": item["VersionId"]}
        for item in page.get("Versions", []) + page.get("DeleteMarkers", [])
    ]
    for offset in range(0, len(objects), 1000):
        aws("s3api", "delete-objects", "--bucket", bucket,
            "--delete", json.dumps({"Objects": objects[offset:offset + 1000], "Quiet": True}),
            "--output", "json")
    if not page.get("IsTruncated"):
        break
    markers = ["--key-marker", page["NextKeyMarker"]]
    if page.get("NextVersionIdMarker"):
        markers += ["--version-id-marker", page["NextVersionIdMarker"]]
PY
}

empty_ecr_repository() {
  local repository="$1"
  if [ "$EXECUTE" = false ]; then
    info "Would permanently delete every image in ECR repository ${repository}"
    return
  fi

  info "Permanently deleting all images in ECR repository ${repository}..."
  python3 - "$repository" "$REGION" <<'PY'
import json
import subprocess
import sys

repository, region = sys.argv[1:]

def aws(*args):
    proc = subprocess.run(["aws", *args, "--region", region], text=True, capture_output=True)
    if proc.returncode:
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return json.loads(proc.stdout or "{}")

next_token = None
while True:
    args = ["ecr", "list-images", "--repository-name", repository, "--max-results", "1000", "--output", "json"]
    if next_token:
        args += ["--next-token", next_token]
    page = aws(*args)
    image_ids = page.get("imageIds", [])
    for offset in range(0, len(image_ids), 100):
        aws("ecr", "batch-delete-image", "--repository-name", repository,
            "--image-ids", json.dumps(image_ids[offset:offset + 100]), "--output", "json")
    next_token = page.get("nextToken")
    if not next_token:
        break
PY
}

delete_project_schedules() {
  [ -n "$CRON_LAMBDA_ARN" ] || return
  local schedules
  schedules=$(aws scheduler list-schedules --group-name openclaw-cron --region "$REGION" \
    --query "Schedules[?Target.Arn=='${CRON_LAMBDA_ARN}'].Name" --output text 2>/dev/null || true)
  [ -n "$schedules" ] && [ "$schedules" != "None" ] || return

  info "Removing EventBridge schedules that target this stack's cron Lambda..."
  local name
  for name in $schedules; do
    run aws scheduler delete-schedule --name "$name" --group-name openclaw-cron --region "$REGION"
  done
}

delete_fargate_services() {
  local tier service family task_defs task_def
  for tier in standard restricted engineering executive; do
    service="${STACK_NAME}-tier-${tier}"
    family="${STACK_NAME}-tier-${tier}"
    if aws ecs describe-services --cluster "$ECS_CLUSTER" --services "$service" --region "$REGION" \
        --query 'services[?status==`ACTIVE`].serviceName | [0]' --output text 2>/dev/null | grep -qx "$service"; then
      info "Deleting ECS service ${service}..."
      run aws ecs update-service --cluster "$ECS_CLUSTER" --service "$service" --desired-count 0 --region "$REGION"
      run aws ecs delete-service --cluster "$ECS_CLUSTER" --service "$service" --force --region "$REGION"
      if [ "$EXECUTE" = true ]; then
        aws ecs wait services-inactive --cluster "$ECS_CLUSTER" --services "$service" --region "$REGION"
      fi
    fi

    task_defs=$(aws ecs list-task-definitions --family-prefix "$family" --status ACTIVE --region "$REGION" \
      --query 'taskDefinitionArns[]' --output text 2>/dev/null || true)
    for task_def in $task_defs; do
      run aws ecs deregister-task-definition --task-definition "$task_def" --region "$REGION"
    done
  done
}

delete_runtime() {
  [ -n "$RUNTIME_ID" ] || return
  info "Deleting AgentCore runtime ${RUNTIME_ID}..."
  run aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION"

  # The runtime references the stack's execution role. Wait for its removal
  # before deleting the stack so CloudFormation can delete that role cleanly.
  if [ "$EXECUTE" = true ]; then
    for _ in $(seq 1 60); do
      if ! aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
          --region "$REGION" >/dev/null 2>&1; then
        success "AgentCore runtime deleted"
        return
      fi
      sleep 5
    done
    warn "AgentCore runtime deletion is still in progress; CloudFormation may need a retry if its execution role remains in use."
  fi
}

delete_openclaw_ssm_parameters() {
  if [ "$EXECUTE" = false ]; then
    info "Would delete all SSM parameters under /openclaw/${STACK_NAME}/"
    return
  fi

  local params=() batch=() name
  while IFS= read -r name; do
    [ -n "$name" ] && [ "$name" != "None" ] && params+=("$name")
  done < <(aws ssm get-parameters-by-path --path "/openclaw/${STACK_NAME}" --recursive --region "$REGION" \
    --query 'Parameters[].Name' --output text 2>/dev/null | tr '\t' '\n')

  for name in "${params[@]}"; do
    batch+=("$name")
    if [ "${#batch[@]}" -eq 10 ]; then
      aws ssm delete-parameters --names "${batch[@]}" --region "$REGION" >/dev/null
      batch=()
    fi
  done
  [ "${#batch[@]}" -gt 0 ] && aws ssm delete-parameters --names "${batch[@]}" --region "$REGION" >/dev/null
}

if [ "$DELETE_STAGING_BUCKET" = true ] && [ "$PURGE_DATA" = false ]; then
  error "--delete-shared-staging-bucket requires --purge-data because it permanently wipes a potentially shared bucket."
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OpenClaw Enterprise — Teardown Plan"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Stack:            $STACK_NAME"
echo "  AWS account:      $ACCOUNT_ID"
echo "  Infrastructure:   $REGION"
echo "  DynamoDB:         $DYNAMODB_TABLE ($DYNAMODB_REGION)"
echo "  Workspace S3:     $S3_BUCKET"
echo "  ECR repository:   $ECR_REPOSITORY"
echo "  ECS cluster:      $ECS_CLUSTER"
echo "  AgentCore runtime:${RUNTIME_ID:+ }${RUNTIME_ID:- none recorded}"
echo ""
if [ "$PURGE_DATA" = true ]; then
  echo -e "  ${RED}DATA MODE: permanent deletion${NC}"
  echo "  This will remove S3 object versions, EFS data, DynamoDB records, and ECR images."
else
  echo -e "  ${YELLOW}SAFE MODE: application data retained${NC}"
  echo "  S3 workspace bucket, EFS filesystem, DynamoDB table, and ECR repository are retained."
fi
if [ "$DELETE_STAGING_BUCKET" = true ]; then
  echo -e "  ${RED}SHARED STAGING BUCKET: will be permanently deleted (${STAGING_BUCKET})${NC}"
else
  echo "  Shared staging bucket retained: $STAGING_BUCKET"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ "$EXECUTE" = false ]; then
  info "Dry-run only — no AWS resources were changed. Re-run with --confirm after reviewing this plan."
  exit 0
fi

warn "This operation deletes the CloudFormation stack and stops all OpenClaw services."
if [ "$PURGE_DATA" = true ]; then
  warn "--purge-data is irreversible once object versions, EFS data, and DynamoDB records are removed."
fi
read -r -p "Type the exact stack name (${STACK_NAME}) to continue: " confirmation
[ "$confirmation" = "$STACK_NAME" ] || error "Confirmation did not match. No resources were changed."

delete_project_schedules
delete_fargate_services
delete_runtime

if [ "$PURGE_DATA" = true ]; then
  purge_versioned_bucket "$S3_BUCKET"
  empty_ecr_repository "$ECR_REPOSITORY"
fi

info "Deleting CloudFormation stack ${STACK_NAME}..."
if [ "$PURGE_DATA" = true ]; then
  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
else
  # These three resources carry persistent employee state or build artifacts.
  # Retaining them also prevents CloudFormation from failing on non-empty S3/ECR.
  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION" \
    --retain-resources TenantWorkspaceBucket MultitenancyEcrRepository AlwaysOnEFS
fi
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION"
success "CloudFormation stack deleted"

# Parameters written by deploy.sh are not all CloudFormation resources; remove
# the full stack namespace only after the stack is gone.
delete_openclaw_ssm_parameters

# awslogs-create-group creates this Fargate log group outside CloudFormation.
aws logs delete-log-group --log-group-name "/ecs/${STACK_NAME}-always-on" --region "$REGION" 2>/dev/null || true

if [ "$PURGE_DATA" = true ]; then
  if aws dynamodb describe-table --table-name "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION" >/dev/null 2>&1; then
    info "Deleting DynamoDB table ${DYNAMODB_TABLE}..."
    aws dynamodb delete-table --table-name "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION" >/dev/null
    aws dynamodb wait table-not-exists --table-name "$DYNAMODB_TABLE" --region "$DYNAMODB_REGION"
    success "DynamoDB table deleted"
  else
    warn "DynamoDB table ${DYNAMODB_TABLE} was not found; skipping."
  fi
fi

if [ "$DELETE_STAGING_BUCKET" = true ]; then
  purge_versioned_bucket "$STAGING_BUCKET"
  info "Deleting shared staging bucket s3://${STAGING_BUCKET}..."
  aws s3api delete-bucket --bucket "$STAGING_BUCKET" --region "$REGION"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Teardown complete.${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$PURGE_DATA" = false ]; then
  echo "  Retained S3:       s3://${S3_BUCKET}"
  echo "  Retained ECR:      ${ECR_REPOSITORY}"
  [ -n "$EFS_ID" ] && echo "  Retained EFS:      ${EFS_ID}"
  echo "  Retained DynamoDB: ${DYNAMODB_TABLE} (${DYNAMODB_REGION})"
  echo "  To remove retained data later, re-deploy or use this script with --confirm --purge-data."
fi
