# Deploy OpenClaw EKS Stack -- Guide for AI Agents

> This guide is designed for AI coding assistants (Claude Code, OpenClaw, Kiro, Cursor).
> It contains exact commands, expected outputs, and decision trees for deploying
> the OpenClaw Enterprise platform on Amazon EKS.
>
> Execute each step sequentially. Stop and report on any failure.

---

## Pre-flight Checks

Run every check. All must pass before proceeding.

```bash
# AWS CLI
aws --version
# Expected: aws-cli/2.27.x or higher

# Terraform
terraform --version
# Expected: Terraform v1.3.x or higher

# kubectl
kubectl version --client
# Expected: Client Version: v1.28.x or higher

# Docker
docker --version
# Expected: Docker version 20.x or higher

# Helm
helm version --short
# Expected: v3.12.x or higher

# Node.js
node --version
# Expected: v22.x or higher

# Docker daemon running
docker info > /dev/null 2>&1 && echo "Docker daemon: OK" || echo "Docker daemon: NOT RUNNING"
```

If any tool is missing, install it before continuing. Do not proceed with a missing prerequisite.

### AWS Credentials Check

```bash
# Verify credentials are configured and detect region
aws sts get-caller-identity --output json
# Expected: JSON with Account, Arn, UserId — no error

# Capture the account ID for later use
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account: $ACCOUNT_ID"
```

If this fails, credentials are not configured. Run `aws configure` (or `aws configure --profile china` for China accounts).

### Bedrock Model Access Check (Global regions only)

```bash
# Verify at least one model is accessible
aws bedrock list-foundation-models \
  --region us-west-2 \
  --query 'modelSummaries[?contains(modelId, `nova`)].modelId' \
  --output text
# Expected: one or more model IDs (e.g., amazon.nova-2-lite-v1:0)
```

If no models are returned, enable model access in the [Bedrock Console](https://console.aws.amazon.com/bedrock/home#/modelaccess).

---

## Decision: Region

Detect whether this is an AWS China deployment or a Global deployment. This decision affects every subsequent step.

```bash
# Set your target region here (change as needed)
DEPLOY_REGION="us-west-2"

# Auto-detect China
if [[ "$DEPLOY_REGION" == cn-* ]]; then
  IS_CHINA=true
  echo "CHINA deployment detected"
else
  IS_CHINA=false
  echo "GLOBAL deployment detected"
fi
```

### If China (`IS_CHINA=true`)

You must account for these constraints:

1. **AWS CLI profile** -- All commands need `--profile china` (or `export AWS_PROFILE=china`)
2. **Image mirror** -- `ghcr.io` and Docker Hub are inaccessible; all 10 operator images must be mirrored to China ECR
3. **No Bedrock** -- Amazon Bedrock does not operate in China regions; use LiteLLM proxy or direct API keys
4. **ECR suffix** -- ECR host is `ACCOUNT.dkr.ecr.REGION.amazonaws.com.cn` (note `.cn`)
5. **Terraform workspace** -- Use a separate workspace (`china`)
6. **Architecture** -- Use `x86` (Graviton availability is limited in China)

### If Global (`IS_CHINA=false`)

Standard deployment. Default architecture is `arm64` (Graviton). Bedrock is available.

---

## Step 1: Build & Mirror Images

This builds the admin console Docker image and pushes it to ECR. For China, it also mirrors all 10 operator images.

Run this BEFORE `terraform apply` -- Terraform creates the ECR repository during apply, but the `build-and-mirror.sh` script creates it independently if needed.

### Global

```bash
cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock

bash eks/scripts/build-and-mirror.sh \
  --region us-west-2 \
  --name openclaw-prod
```

### China

```bash
cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock

bash eks/scripts/build-and-mirror.sh \
  --region cn-northwest-1 \
  --name openclaw-cn \
  --profile china
```

### Expected output (success)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Build & Mirror -- openclaw-prod (us-west-2)
  Account: 123456789012
  ECR Host: 123456789012.dkr.ecr.us-west-2.amazonaws.com
  China: false
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[ok]    ECR login
[ok]    Docker build complete
[ok]    Admin console image pushed
[info]  Global region -- image mirror not needed (use --mirror to force)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Done!
  Admin Console: 123456789012.dkr.ecr.us-west-2.amazonaws.com/openclaw-prod/admin-console:latest
  Next: run terraform apply
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Expected output (China -- mirror section)

For China deployments, the mirror section should show all 10 images:

```
[info]  Mirroring 10 images to ECR (ACCOUNT.dkr.ecr.cn-northwest-1.amazonaws.com.cn)...

  ghcr.io/openclaw/openclaw:latest                        → PUSHED
  ghcr.io/astral-sh/uv:0.6-bookworm-slim                 → PUSHED
  nginx:1.27-alpine                                        → PUSHED
  otel/opentelemetry-collector:0.120.0                     → PUSHED
  chromedp/headless-shell:stable                           → PUSHED
  ghcr.io/tailscale/tailscale:latest                      → PUSHED
  ollama/ollama:latest                                     → PUSHED
  tsl0922/ttyd:latest                                      → PUSHED
  rclone/rclone:latest                                     → PUSHED
  ghcr.io/openclaw-rocks/openclaw-operator:v0.25.2        → PUSHED

[ok]    Mirror done: 10 pushed, 0 skipped (already exist), 0 failed
```

### Failure: Docker daemon not running

```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock
```

Fix: Start Docker (`sudo systemctl start docker` or open Docker Desktop).

### Failure: ECR login fails

```
An error occurred (AccessDeniedException)
```

Fix: Verify AWS credentials have `ecr:GetAuthorizationToken` permission. For China, ensure `--profile china` is set.

---

## Step 2: Terraform Deploy

### Initialize

```bash
cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock/eks/terraform

terraform init
```

Expected output ends with:

```
Terraform has been successfully initialized!
```

If you see provider download failures, check network connectivity. For China, the Terraform registry may be slow -- retry.

### China only: Create workspace

```bash
# Only for China deployments
terraform workspace new china 2>/dev/null || terraform workspace select china
```

### Plan

Generate a plan to see what will be created. Set a strong admin password.

**Global:**

```bash
terraform plan \
  -var="name=openclaw-prod" \
  -var="region=us-west-2" \
  -var="architecture=arm64" \
  -var="enable_efs=true" \
  -var="enable_alb_controller=true" \
  -var="enable_admin_console=true" \
  -var="admin_password=CHANGE_ME_STRONG_PASSWORD"
```

**China:**

```bash
AWS_PROFILE=china terraform plan \
  -var="name=openclaw-cn" \
  -var="region=cn-northwest-1" \
  -var="architecture=x86" \
  -var="enable_efs=true" \
  -var="enable_alb_controller=true" \
  -var="enable_admin_console=true" \
  -var="admin_password=CHANGE_ME_STRONG_PASSWORD"
```

Expected output ends with:

```
Plan: ~99 to add, 0 to change, 0 to destroy.
```

The exact count varies (80-110 resources) depending on enabled features. If the count is drastically different (e.g., <50), check that `enable_admin_console=true` and `enable_alb_controller=true` are set.

### Apply

Run apply with the same variables. This takes 15-25 minutes (EKS cluster creation is the bottleneck).

**Global:**

```bash
terraform apply \
  -var="name=openclaw-prod" \
  -var="region=us-west-2" \
  -var="architecture=arm64" \
  -var="enable_efs=true" \
  -var="enable_alb_controller=true" \
  -var="enable_admin_console=true" \
  -var="admin_password=CHANGE_ME_STRONG_PASSWORD" \
  -auto-approve
```

**China:**

```bash
AWS_PROFILE=china terraform apply \
  -var="name=openclaw-cn" \
  -var="region=cn-northwest-1" \
  -var="architecture=x86" \
  -var="enable_efs=true" \
  -var="enable_alb_controller=true" \
  -var="enable_admin_console=true" \
  -var="admin_password=CHANGE_ME_STRONG_PASSWORD" \
  -auto-approve
```

### Expected outputs after apply

```
Outputs:

admin_console_ecr = "123456789012.dkr.ecr.us-west-2.amazonaws.com/openclaw-prod/admin-console"
admin_console_url = "kubectl -n openclaw port-forward svc/admin-console 8099:8099"
cluster_endpoint  = "https://XXXXX.gr7.us-west-2.eks.amazonaws.com"
cluster_name      = "openclaw-prod"
configure_kubectl = "aws eks --region us-west-2 update-kubeconfig --name openclaw-prod"
operator_namespace = "openclaw-operator-system"
openclaw_namespace = "openclaw"
vpc_id            = "vpc-0abc123..."
bedrock_role_arn  = "arn:aws:iam::123456789012:role/openclaw-prod-bedrock"
```

Capture these values:

```bash
CLUSTER_NAME=$(terraform output -raw cluster_name)
ECR_URI=$(terraform output -raw admin_console_ecr)
CONFIGURE_CMD=$(terraform output -raw configure_kubectl)
echo "Cluster: $CLUSTER_NAME"
echo "ECR: $ECR_URI"
```

### Configure kubectl

```bash
# Run the configure command from terraform output
eval "$(terraform output -raw configure_kubectl)"

# Verify connectivity
kubectl get nodes
# Expected: 2+ nodes in Ready state
```

### Common errors

**Error: ECR repository empty (admin console pod in ImagePullBackOff)**

Terraform creates the ECR repo but does not build the image. If you skipped Step 1, go back and run `build-and-mirror.sh`.

**Error: State lock**

```
Error: Error acquiring the state lock
```

Fix: If you are certain no other process is running Terraform:

```bash
terraform force-unlock LOCK_ID
```

The LOCK_ID is printed in the error message.

**Error: Namespace not found during apply**

This usually means the EKS cluster is not yet ready. Terraform handles dependency ordering, but if you see this on a re-run, try:

```bash
terraform apply -target=module.eks_cluster ...
# Then re-run the full apply
terraform apply ...
```

**Error: Timeout creating EKS cluster**

EKS cluster creation can take 15-20 minutes. If it times out, re-run `terraform apply` -- Terraform will pick up where it left off.

---

## Step 3: Push Admin Console Image (if not done in Step 1)

If you already ran `build-and-mirror.sh` in Step 1, the image is in ECR. Verify:

```bash
ECR_URI=$(cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock/eks/terraform && terraform output -raw admin_console_ecr)

aws ecr describe-images \
  --repository-name "$(echo $ECR_URI | sed 's|.*/||' | sed 's|:.*||')" \
  --region us-west-2 \
  --query 'imageDetails[0].imagePushedAt' \
  --output text
# Expected: a timestamp (e.g., 2025-01-15T10:30:00+00:00)
# If "None" or error: the image was not pushed. Run Step 1.
```

If you need to rebuild and push:

```bash
ECR_URI=$(cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock/eks/terraform && terraform output -raw admin_console_ecr)

cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock/enterprise/admin-console

# Login to ECR
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin "$(echo $ECR_URI | cut -d/ -f1)"

# Build and push
docker build -t "$ECR_URI:latest" .
docker push "$ECR_URI:latest"

# Restart the deployment to pick up the new image
kubectl -n openclaw rollout restart deployment/admin-console
kubectl -n openclaw rollout status deployment/admin-console --timeout=120s
# Expected: deployment "admin-console" successfully rolled out
```

---

## Step 4: Seed Data

Terraform automatically seeds DynamoDB with sample organization data and uploads SOUL templates to S3. This is idempotent and will not overwrite existing records.

If you suspect seed data is missing (empty admin console, no agents listed), re-seed manually using the standalone deploy script:

```bash
cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock/enterprise/admin-console

bash deploy-eks.sh \
  --cluster openclaw-prod \
  --region us-west-2 \
  --password YOUR_ADMIN_PASSWORD \
  --skip-build
```

The `--skip-build` flag skips Docker image build and only runs the seed + Helm deploy steps.

---

## Step 5: Verify

Run all verification commands. Every check must pass.

### Pods

```bash
kubectl -n openclaw get pods
# Expected:
# NAME                             READY   STATUS    RESTARTS   AGE
# admin-console-xxxxxxxxxx-xxxxx   1/1     Running   0          5m

kubectl -n openclaw-operator-system get pods
# Expected:
# NAME                                                    READY   STATUS    RESTARTS   AGE
# openclaw-operator-controller-manager-xxxxxxxxxx-xxxxx   1/1     Running   0          5m
```

Both pods must show `1/1 Running`. If not, see Troubleshooting below.

### StorageClass

```bash
kubectl get storageclass
# Expected: efs-sc marked as (default)
# NAME            PROVISIONER             RECLAIMPOLICY   VOLUMEBINDINGMODE   ALLOWVOLUMEEXPANSION   AGE
# efs-sc (default) efs.csi.aws.com        Delete          Immediate           true                   5m
# gp2              kubernetes.io/aws-ebs   Delete          WaitForFirstConsumer false                  5m
```

If `efs-sc` is not the default, set it:

```bash
kubectl annotate storageclass efs-sc \
  storageclass.kubernetes.io/is-default-class=true --overwrite
```

### Ingress (if ALB controller enabled)

```bash
kubectl -n openclaw get ingress admin-console
# Expected:
# NAME            CLASS   HOSTS   ADDRESS                                         PORTS   AGE
# admin-console   alb     *       k8s-openclaw-adminconsole-xxxxx.us-west-2.elb.amazonaws.com   80    5m
```

The ADDRESS column should contain an ALB hostname. If it is empty, the ALB Controller may not be ready yet -- wait 2-3 minutes and check again.

### Health check

```bash
# Via ALB (if available)
ALB_URL=$(kubectl -n openclaw get ingress admin-console \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)

if [[ -n "$ALB_URL" ]]; then
  curl -sf "http://${ALB_URL}/api/v1/health" && echo " -- OK"
  # Expected: {"status":"ok"} -- OK
else
  echo "ALB not ready. Use port-forward instead."
fi

# Via port-forward (always works)
kubectl -n openclaw port-forward svc/admin-console 8099:8099 &
PF_PID=$!
sleep 3
curl -sf http://localhost:8099/api/v1/health && echo " -- OK"
# Expected: {"status":"ok"} -- OK
kill $PF_PID 2>/dev/null
```

---

## Step 6: Deploy an OpenClaw Instance (Integration Test)

This validates the full pipeline: login, deploy an agent instance, verify the pod starts, then clean up.

### Option A: Use the integration test script

```bash
cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock

# Start port-forward in background
kubectl -n openclaw port-forward svc/admin-console 8099:8099 &
PF_PID=$!
sleep 3

# Global
bash eks/scripts/integration-test.sh \
  --cluster openclaw-prod \
  --region us-west-2 \
  --password YOUR_ADMIN_PASSWORD

# China
bash eks/scripts/integration-test.sh \
  --cluster openclaw-cn \
  --region cn-northwest-1 \
  --password YOUR_ADMIN_PASSWORD \
  --registry ACCOUNT.dkr.ecr.cn-northwest-1.amazonaws.com.cn

# Clean up port-forward
kill $PF_PID 2>/dev/null
```

Expected output ends with:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ALL TESTS PASSED -- openclaw-prod (us-west-2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Option B: Manual step-by-step

Use this if you want to control each step individually.

#### 6.1 Login

```bash
# Ensure port-forward is running
kubectl -n openclaw port-forward svc/admin-console 8099:8099 &
PF_PID=$!
sleep 3

BASE_URL="http://localhost:8099"

TOKEN=$(curl -sf -X POST "${BASE_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"employeeId":"emp-jiade","password":"YOUR_ADMIN_PASSWORD"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")

echo "Token: ${TOKEN:0:20}..."
# Expected: Token: eyJhbGciOiJIUzI1NiI...
# If empty: login failed -- check password matches what you set in terraform apply
```

#### 6.2 Deploy an agent instance

```bash
# Global deployment
DEPLOY_RESP=$(curl -sf -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"bedrock/us.amazon.nova-2-lite-v1:0"}' \
  "${BASE_URL}/api/v1/admin/eks/agent-helpdesk/deploy")
echo "$DEPLOY_RESP"
# Expected: {"deployed": true, ...}

# China deployment (with globalRegistry)
# DEPLOY_RESP=$(curl -sf -X POST \
#   -H "Authorization: Bearer $TOKEN" \
#   -H "Content-Type: application/json" \
#   -d '{"model":"anthropic/claude-sonnet-4-5-20250929","globalRegistry":"ACCOUNT.dkr.ecr.cn-northwest-1.amazonaws.com.cn"}' \
#   "${BASE_URL}/api/v1/admin/eks/agent-helpdesk/deploy")
```

#### 6.3 Poll status until Running

```bash
for i in $(seq 1 20); do
  sleep 15
  STATUS=$(curl -sf -H "Authorization: Bearer $TOKEN" \
    "${BASE_URL}/api/v1/admin/eks/agent-helpdesk/status")
  POD_RUNNING=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('running',False))")
  CRD_STATUS=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('crdStatus',''))")
  echo "[$i/20] crd=$CRD_STATUS running=$POD_RUNNING"

  if [[ "$POD_RUNNING" == "True" ]]; then
    echo "Agent is Running."
    break
  fi
done
# Expected: Pod transitions from Pending -> Running within 5 minutes
```

#### 6.4 Verify CRD spec

```bash
kubectl -n openclaw get openclawinstance agent-helpdesk -o yaml | head -30
# Expected: spec.model set to the model you chose
# For China: spec.registry should equal the globalRegistry you set
```

#### 6.5 Stop and clean up

```bash
# Stop the instance
curl -sf -X POST \
  -H "Authorization: Bearer $TOKEN" \
  "${BASE_URL}/api/v1/admin/eks/agent-helpdesk/stop"
# Expected: {"stopped": true}

# Wait for cleanup
sleep 5

# Remove finalizer if CRD deletion is stuck
kubectl -n openclaw patch openclawinstance agent-helpdesk --type=json \
  -p='[{"op":"remove","path":"/metadata/finalizers"}]' 2>/dev/null || true

# Verify cleanup
kubectl -n openclaw get openclawinstance
# Expected: No resources found

# Kill port-forward
kill $PF_PID 2>/dev/null
```

---

## Step 7: Access

### Via ALB (internet-accessible)

```bash
ALB_URL=$(kubectl -n openclaw get ingress admin-console \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "Admin Console: http://${ALB_URL}"
```

Open in browser. Login with:
- **Employee ID:** `emp-jiade`
- **Password:** the value you set in `admin_password` during terraform apply

### Via port-forward (local access, always works)

```bash
kubectl -n openclaw port-forward svc/admin-console 8099:8099
# Then open: http://localhost:8099
```

Login with the same credentials above.

---

## Troubleshooting Decision Tree

Use this to diagnose failures. Each item is structured as: symptom, decision, fix.

### Pod `ImagePullBackOff`

```bash
kubectl -n openclaw describe pod -l app=admin-console | grep -A5 "Events:"
```

**Decision: Is this a China deployment?**

- **Yes (China):** Images cannot be pulled from ghcr.io/Docker Hub.
  - For the admin console: verify Step 1 ran with `--profile china`.
  - For agent instances: set `globalRegistry` when deploying:
    ```bash
    curl -X POST .../deploy -d '{"globalRegistry":"ACCOUNT.dkr.ecr.cn-northwest-1.amazonaws.com.cn"}'
    ```
- **No (Global):** ECR image does not exist.
  - Run Step 1 (`build-and-mirror.sh`) to build and push the image.
  - Verify:
    ```bash
    ECR_URI=$(cd eks/terraform && terraform output -raw admin_console_ecr)
    aws ecr describe-images --repository-name "${ECR_URI##*/}" --region us-west-2
    ```

### Pod `Pending`

```bash
kubectl -n openclaw describe pod -l app=admin-console | grep -A5 "Events:"
```

**Decision: Is the event about unbound PVC or insufficient resources?**

- **Unbound PVC:** StorageClass is missing or not default.
  ```bash
  kubectl get storageclass
  # If efs-sc is not (default):
  kubectl annotate storageclass efs-sc \
    storageclass.kubernetes.io/is-default-class=true --overwrite
  ```
- **Insufficient CPU/memory:** Nodes are at capacity.
  ```bash
  kubectl describe nodes | grep -A5 "Allocated resources"
  # If all nodes are near capacity, the node group needs to scale.
  # Check the min/max in Terraform core_node_count variable.
  ```

### Terraform state lock

```
Error: Error acquiring the state lock
Lock Info:
  ID:        xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Fix (only if no other Terraform process is running):

```bash
terraform force-unlock xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

For local state files:

```bash
rm -f .terraform.tfstate.lock.info
```

### 403 on Kubernetes API

```bash
kubectl auth can-i get pods -n openclaw
# Expected: yes
# If no: RBAC is missing
```

Terraform and the Helm chart both create RBAC automatically (ClusterRole + ClusterRoleBinding). If RBAC is missing after a manual install:

```bash
helm upgrade admin-console enterprise/admin-console/chart \
  --namespace openclaw --reuse-values --set rbac.create=true
```

### Operator not found

```bash
kubectl get deployment -n openclaw-operator-system
# Expected: openclaw-operator-controller-manager
```

If empty, the operator module did not deploy. Check:

```bash
kubectl get namespace openclaw-operator-system
# If not found: terraform apply did not complete the operator module.
# Re-run terraform apply.

helm list -A | grep openclaw-operator
# Should show the operator Helm release.
```

### ALB not provisioning

```bash
kubectl get deployment -n kube-system aws-load-balancer-controller
# Expected: 1/1 READY
```

If the controller is not running:

```bash
# Check if enable_alb_controller was true in terraform apply
# Re-run with enable_alb_controller=true if it was missing

# Or install manually:
helm repo add eks https://aws.github.io/eks-charts
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system --set clusterName=openclaw-prod
```

### Pod Identity 403 (AWS API calls fail from within the pod)

```bash
# Verify the Pod Identity agent addon is installed
kubectl get pods -n kube-system -l app.kubernetes.io/name=eks-pod-identity-agent
# Expected: 2+ pods Running

# Verify the association exists
aws eks list-pod-identity-associations \
  --cluster-name openclaw-prod --namespace openclaw --region us-west-2
# Expected: at least one association for service account "admin-console"
```

If the addon is missing:

```bash
aws eks create-addon \
  --cluster-name openclaw-prod \
  --addon-name eks-pod-identity-agent \
  --region us-west-2
```

---

## Rollback / Cleanup

To tear down the entire stack and all AWS resources created by Terraform:

### Global

```bash
cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock/eks/terraform

# First, delete any OpenClaw instances (CRDs) to avoid orphaned resources
kubectl delete openclawinstance --all -n openclaw 2>/dev/null || true

# Wait for instance cleanup
sleep 10

# Destroy all Terraform-managed resources
terraform destroy \
  -var="name=openclaw-prod" \
  -var="region=us-west-2" \
  -var="architecture=arm64" \
  -var="enable_efs=true" \
  -var="enable_alb_controller=true" \
  -var="enable_admin_console=true" \
  -var="admin_password=dummy" \
  -auto-approve
```

### China

```bash
cd /path/to/sample-OpenClaw-on-AWS-with-Bedrock/eks/terraform

# Select the China workspace first
terraform workspace select china

# Delete CRDs
kubectl delete openclawinstance --all -n openclaw 2>/dev/null || true
sleep 10

AWS_PROFILE=china terraform destroy \
  -var="name=openclaw-cn" \
  -var="region=cn-northwest-1" \
  -var="architecture=x86" \
  -var="enable_efs=true" \
  -var="enable_alb_controller=true" \
  -var="enable_admin_console=true" \
  -var="admin_password=dummy" \
  -auto-approve
```

Expected: `Destroy complete! Resources: ~99 destroyed.`

The destroy takes 10-15 minutes. EKS cluster and VPC deletion are the slowest parts.

**Note:** ECR images are not deleted by Terraform destroy. To clean up ECR repositories:

```bash
# Global
aws ecr delete-repository --repository-name openclaw-prod/admin-console \
  --region us-west-2 --force

# China (repeat for all 10+ mirrored repos)
AWS_PROFILE=china aws ecr delete-repository --repository-name openclaw/openclaw \
  --region cn-northwest-1 --force
```

---

## Quick Reference: Full Deploy Sequence (Global)

For copy-paste execution of the entire flow without branching:

```bash
# Variables -- set these
DEPLOY_NAME="openclaw-prod"
DEPLOY_REGION="us-west-2"
DEPLOY_ARCH="arm64"
ADMIN_PASSWORD="YourStrongPassword123!"
REPO_ROOT="/path/to/sample-OpenClaw-on-AWS-with-Bedrock"

# Step 1: Build images
cd "$REPO_ROOT"
bash eks/scripts/build-and-mirror.sh --region "$DEPLOY_REGION" --name "$DEPLOY_NAME"

# Step 2: Terraform
cd "$REPO_ROOT/eks/terraform"
terraform init
terraform apply \
  -var="name=$DEPLOY_NAME" \
  -var="region=$DEPLOY_REGION" \
  -var="architecture=$DEPLOY_ARCH" \
  -var="enable_efs=true" \
  -var="enable_alb_controller=true" \
  -var="enable_admin_console=true" \
  -var="admin_password=$ADMIN_PASSWORD" \
  -auto-approve

# Step 3: Configure kubectl
eval "$(terraform output -raw configure_kubectl)"

# Step 4: Verify
kubectl -n openclaw get pods
kubectl -n openclaw-operator-system get pods
kubectl get storageclass
kubectl -n openclaw get ingress admin-console

# Step 5: Access
ALB_URL=$(kubectl -n openclaw get ingress admin-console \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "Open: http://${ALB_URL}"
echo "Login: emp-jiade / ${ADMIN_PASSWORD}"
```
