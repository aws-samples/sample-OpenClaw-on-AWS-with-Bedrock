#!/bin/bash
# Setup script for running the AWS multi-tenant demo on an EC2 instance.
#
# Prerequisites:
#   - EC2 instance deployed via CloudFormation (standard or multitenancy template)
#   - Bedrock model access enabled
#
# Usage:
#   # Connect to EC2 via SSM
#   aws ssm start-session --target <INSTANCE_ID> --region <REGION>
#   sudo su - ubuntu
#
#   # Clone repo (if not already present)
#   git clone https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock.git
#   cd sample-OpenClaw-on-AWS-with-Bedrock
#
#   # Run setup
#   bash demo/setup_aws_demo.sh
#
#   # Run demo
#   python3 demo/aws_demo.py

set -e

echo "=========================================="
echo "OpenClaw Multi-Tenant Demo — Setup"
echo "=========================================="

# Detect region
if [ -z "$AWS_REGION" ]; then
    IMDS_TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null || echo "")
    if [ -n "$IMDS_TOKEN" ]; then
        AWS_REGION=$(curl -s -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || echo "us-east-1")
    else
        AWS_REGION="us-east-1"
    fi
fi
echo "Region: $AWS_REGION"

# Install Python dependencies
echo "[1/3] Installing Python dependencies..."
pip3 install --break-system-packages requests boto3 2>/dev/null || pip3 install requests boto3

# Verify AWS credentials
echo "[2/3] Verifying AWS credentials..."
if aws sts get-caller-identity --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "✓ AWS credentials OK"
else
    echo "✗ AWS credentials not configured. Run: aws configure"
    exit 1
fi

# Verify Bedrock access
echo "[3/3] Verifying Bedrock model access..."
MODEL_ID="global.amazon.nova-2-lite-v1:0"
if aws bedrock-runtime invoke-model \
    --model-id "$MODEL_ID" \
    --body '{"messages":[{"role":"user","content":[{"text":"hi"}]}],"inferenceConfig":{"maxTokens":10}}' \
    --region "$AWS_REGION" \
    /tmp/bedrock-test.json > /dev/null 2>&1; then
    echo "✓ Bedrock access OK (model: $MODEL_ID)"
    rm -f /tmp/bedrock-test.json
else
    echo "✗ Bedrock access failed. Enable $MODEL_ID in Bedrock Console."
    echo "  https://console.aws.amazon.com/bedrock/"
    exit 1
fi

echo ""
echo "=========================================="
echo "Setup complete! Run the demo:"
echo ""
echo "  python3 demo/aws_demo.py"
echo ""
echo "Or with a specific stack name:"
echo ""
echo "  STACK_NAME=openclaw-bedrock python3 demo/aws_demo.py"
echo "=========================================="
