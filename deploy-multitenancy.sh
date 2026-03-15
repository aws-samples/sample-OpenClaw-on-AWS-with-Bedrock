#!/bin/bash
# =============================================================================
# Deploy OpenClaw Multi-Tenant Platform — Full Pipeline
#
# This script deploys the complete multi-tenant platform:
#   1. CloudFormation stack (EC2 + ECR + S3 + SSM + CloudWatch)
#   2. Build and push Agent Container to ECR
#   3. Upload SOUL.md templates to S3
#   4. Create AgentCore Runtime
#   5. Store Runtime ID in SSM
#
# Usage: bash deploy-multitenancy.sh [STACK_NAME] [REGION]
# =============================================================================
set -euo pipefail

STACK_NAME="${1:-openclaw-multitenancy}"
REGION="${2:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "============================================"
echo "  OpenClaw Multi-Tenant Platform Deployment"
echo "============================================"
echo "  Stack:   $STACK_NAME"
echo "  Region:  $REGION"
echo "  Account: $ACCOUNT_ID"
echo ""

# =============================================================================
# Step 0: Upgrade AWS CLI (bedrock-agentcore-control requires CLI >= 2.27)
# =============================================================================
echo "[0/5] Checking AWS CLI version..."
CLI_VERSION=$(aws --version 2>&1 | grep -oP 'aws-cli/\K[0-9]+\.[0-9]+' || echo "0.0")
CLI_MAJOR=$(echo "$CLI_VERSION" | cut -d. -f1)
CLI_MINOR=$(echo "$CLI_VERSION" | cut -d. -f2)
if [ "$CLI_MAJOR" -lt 2 ] || ([ "$CLI_MAJOR" -eq 2 ] && [ "$CLI_MINOR" -lt 27 ]); then
    echo "  WARNING: AWS CLI $CLI_VERSION detected. bedrock-agentcore-control requires >= 2.27"
    echo "  Run: pip install --upgrade awscli  OR  brew upgrade awscli"
    echo "  Continuing anyway — commands may fail if CLI is too old."
fi

# =============================================================================
# Step 1: Deploy CloudFormation
# =============================================================================
echo "[1/5] Deploying CloudFormation stack..."

aws cloudformation create-stack \
    --stack-name "$STACK_NAME" \
    --template-body file://clawdbot-bedrock-agentcore-multitenancy.yaml \
    --capabilities CAPABILITY_NAMED_IAM \
    --region "$REGION" \
    --parameters \
        ParameterKey=KeyPairName,ParameterValue=none \
        ParameterKey=OpenClawModel,ParameterValue=global.amazon.nova-2-lite-v1:0 \
    2>/dev/null || echo "  Stack may already exist, checking..."

echo "  Waiting for stack to complete (this takes ~8 minutes)..."
aws cloudformation wait stack-create-complete \
    --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null \
    || echo "  Stack already exists or update needed"

# Get outputs
ECR_URI=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`MultitenancyEcrRepositoryUri`].OutputValue' \
    --output text)

EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`AgentContainerExecutionRoleArn`].OutputValue' \
    --output text)

S3_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`TenantWorkspaceBucketName`].OutputValue' \
    --output text)

INSTANCE_ID=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' \
    --output text)

echo "  ECR:      $ECR_URI"
echo "  Role:     $EXECUTION_ROLE_ARN"
echo "  S3:       $S3_BUCKET"
echo "  Instance: $INSTANCE_ID"

# =============================================================================
# Step 2: Upload SOUL.md templates to S3
# =============================================================================
echo ""
echo "[2/5] Uploading SOUL.md templates to S3..."

aws s3 cp agent-container/templates/default.md "s3://${S3_BUCKET}/_shared/templates/default.md" --region "$REGION"
aws s3 cp agent-container/templates/intern.md "s3://${S3_BUCKET}/_shared/templates/intern.md" --region "$REGION"
aws s3 cp agent-container/templates/engineer.md "s3://${S3_BUCKET}/_shared/templates/engineer.md" --region "$REGION"
echo "  Uploaded 3 templates"

# =============================================================================
# Step 3: Build and push Agent Container
# =============================================================================
echo ""
echo "[3/5] Building and pushing Agent Container to ECR..."

aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "  Building Docker image (platform=linux/arm64)..."
docker build --platform linux/arm64 -f agent-container/Dockerfile -t "${ECR_URI}:latest" .

echo "  Pushing to ECR..."
docker push "${ECR_URI}:latest"
echo "  Image pushed: ${ECR_URI}:latest"

# =============================================================================
# Step 4: Create AgentCore Runtime
# =============================================================================
echo ""
echo "[4/5] Creating AgentCore Runtime..."

RUNTIME_ID=$(aws bedrock-agentcore-control create-agent-runtime \
    --agent-runtime-name "${STACK_NAME//-/_}_runtime" \
    --agent-runtime-artifact '{"containerConfiguration":{"containerUri":"'"${ECR_URI}:latest"'"}}' \
    --role-arn "$EXECUTION_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":300,"maxLifetime":3600}' \
    --environment-variables STACK_NAME="${STACK_NAME}",AWS_REGION="${REGION}",S3_BUCKET="${S3_BUCKET}",BEDROCK_MODEL_ID="global.amazon.nova-2-lite-v1:0" \
    --region "$REGION" \
    --query 'agentRuntimeId' --output text 2>&1) || {
    echo "  create-agent-runtime failed: $RUNTIME_ID"
    echo "  Trying to get existing runtime from SSM..."
    RUNTIME_ID=$(aws ssm get-parameter \
        --name "/openclaw/${STACK_NAME}/runtime-id" \
        --query Parameter.Value --output text \
        --region "$REGION" 2>/dev/null || echo "UNKNOWN")
}

echo "  Runtime ID: $RUNTIME_ID"

# =============================================================================
# Step 5: Create AgentCore Runtime Endpoint
# =============================================================================
echo ""
echo "[5/6] Creating AgentCore Runtime Endpoint..."

ENDPOINT_NAME="${STACK_NAME//-/_}_endpoint"
aws bedrock-agentcore-control create-agent-runtime-endpoint \
    --agent-runtime-id "$RUNTIME_ID" \
    --name "$ENDPOINT_NAME" \
    --region "$REGION" \
    --query 'agentRuntimeEndpointArn' --output text 2>&1 || {
    echo "  Endpoint may already exist or runtime not ready yet."
    echo "  You can create it later once runtime status is READY."
}

# =============================================================================
# Step 6: Store Runtime ID in SSM
# =============================================================================
echo ""
echo "[6/6] Storing Runtime ID in SSM..."

aws ssm put-parameter \
    --name "/openclaw/${STACK_NAME}/runtime-id" \
    --value "$RUNTIME_ID" \
    --type String \
    --overwrite \
    --region "$REGION"

echo "  Stored at /openclaw/${STACK_NAME}/runtime-id"

# =============================================================================
# Done
# =============================================================================
echo ""
echo "============================================"
echo "  Deployment Complete!"
echo "============================================"
echo ""
echo "  Stack:      $STACK_NAME"
echo "  Runtime ID: $RUNTIME_ID"
echo "  S3 Bucket:  $S3_BUCKET"
echo "  Instance:   $INSTANCE_ID"
echo ""
echo "  Next steps:"
echo "  1. Connect to EC2: aws ssm start-session --target $INSTANCE_ID --region $REGION"
echo "  2. Configure Telegram bot in OpenClaw Web UI"
echo "  3. Start Tenant Router:"
echo "     export STACK_NAME=$STACK_NAME AWS_REGION=$REGION AGENTCORE_RUNTIME_ID=$RUNTIME_ID"
echo "     python3 src/gateway/tenant_router.py"
echo "  4. Send a message on Telegram to test!"
echo ""
