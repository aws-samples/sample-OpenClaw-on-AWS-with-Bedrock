#!/bin/bash
set -ex

# Get AWS account ID dynamically
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-openclaw}"
S3_BUCKET="${S3_BUCKET:-openclaw-tenants-${ACCOUNT_ID}}"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${STACK_NAME}-multitenancy-agent"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.10.4}"
NODE_VERSION="${NODE_VERSION:-24.16.0}"
CLAWHUB_VERSION="${CLAWHUB_VERSION:-0.23.3}"
IMAGE_TAG="${IMAGE_TAG:-${AGENT_IMAGE_TAG:-openclaw-${OPENCLAW_VERSION}}}"
LOG="/tmp/build.log"

exec > >(tee "$LOG") 2>&1

cd /tmp && rm -rf docker-build && mkdir docker-build && cd docker-build
aws s3 cp "s3://${S3_BUCKET}/_build/agent-build.tar.gz" . --region "$REGION"
tar xzf agent-build.tar.gz

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker build \
  --build-arg "OPENCLAW_VERSION=${OPENCLAW_VERSION}" \
  --build-arg "NODE_VERSION=${NODE_VERSION}" \
  --build-arg "CLAWHUB_VERSION=${CLAWHUB_VERSION}" \
  -f enterprise/agent-container/Dockerfile \
  -t "${ECR_URI}:${IMAGE_TAG}" \
  enterprise/
docker run --rm --entrypoint /bin/sh "${ECR_URI}:${IMAGE_TAG}" -c \
  "test \"\$(node --version)\" = \"v${NODE_VERSION}\" && openclaw --version | grep -F \"OpenClaw ${OPENCLAW_VERSION}\" && openclaw agent --help | grep -F -- \"--json\" && openclaw gateway install --help >/dev/null"
docker push "${ECR_URI}:${IMAGE_TAG}"

echo "BUILD_AND_PUSH_COMPLETE"
aws s3 cp "$LOG" "s3://${S3_BUCKET}/_build/build.log" --region "$REGION"
