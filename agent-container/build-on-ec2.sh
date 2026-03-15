#!/bin/bash
set -ex
ECR_URI="263168716248.dkr.ecr.us-east-1.amazonaws.com/openclaw-multitenancy-multitenancy-agent"
LOG="/tmp/build.log"

exec > >(tee "$LOG") 2>&1

cd /tmp && rm -rf docker-build && mkdir docker-build && cd docker-build
aws s3 cp s3://openclaw-tenants-263168716248/_build/agent-build.tar.gz . --region us-east-1
tar xzf agent-build.tar.gz

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 263168716248.dkr.ecr.us-east-1.amazonaws.com

docker build -f agent-container/Dockerfile -t ${ECR_URI}:latest .
docker push ${ECR_URI}:latest

echo "BUILD_AND_PUSH_COMPLETE"
aws s3 cp "$LOG" s3://openclaw-tenants-263168716248/_build/build.log --region us-east-1
