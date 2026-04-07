# Deploying Admin Console on EKS

Deploy the OpenClaw Enterprise Admin Console to an existing Amazon EKS cluster. This runs the control panel (React + FastAPI) as a Kubernetes pod with Pod Identity for AWS access.

---

## Prerequisites

| Requirement | Version | Check |
|-------------|---------|-------|
| AWS CLI | >= 2.27 | `aws --version` |
| kubectl | >= 1.28 | `kubectl version --client` |
| Docker | >= 20.0 | `docker --version` |
| Node.js | >= 22 | `node --version` |
| Python | >= 3.10 | `python3 --version` |
| EKS cluster | Running, with `eks-pod-identity-agent` addon | `aws eks list-addons --cluster-name NAME` |

### EKS Pod Identity Agent

The deploy script uses [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html) (not IRSA) for IAM access. Verify the addon is installed:

```bash
aws eks describe-addon --cluster-name YOUR_CLUSTER --addon-name eks-pod-identity-agent --region us-west-2
```

If not installed:

```bash
aws eks create-addon --cluster-name YOUR_CLUSTER --addon-name eks-pod-identity-agent --region us-west-2
```

---

## Option A: Standalone Deploy Script (No Terraform)

The fastest way to deploy — creates all required AWS resources and deploys to your EKS cluster in one command.

```bash
cd enterprise/admin-console

bash deploy-eks.sh \
  --cluster dev-cluster \
  --region us-west-2 \
  --password YOUR_ADMIN_PASSWORD
```

### What the script creates

| Resource | Name | Purpose |
|----------|------|---------|
| ECR repository | `{stack}/admin-console` | Docker image storage |
| DynamoDB table | `{stack}-enterprise` | All enterprise data (single-table) |
| S3 bucket | `{stack}-workspaces-{account}` | SOUL templates, workspaces, knowledge |
| IAM role | `{stack}-admin-console` | Pod Identity with DynamoDB/S3/SSM/EKS/ECR/CloudWatch |
| SSM parameters | `/openclaw/{stack}/admin-password`, `jwt-secret` | Secrets |
| K8s ServiceAccount | `admin-console` in `openclaw` namespace | Pod Identity binding |
| K8s Deployment | `admin-console` | 1 replica, port 8099 |
| K8s Service | `admin-console` (ClusterIP) | Internal access |

### Script flags

| Flag | Default | Description |
|------|---------|-------------|
| `--cluster` | (required) | EKS cluster name |
| `--region` | `us-west-2` | AWS region |
| `--namespace` | `openclaw` | Kubernetes namespace |
| `--stack` | `openclaw-eks` | Resource name prefix |
| `--password` | `admin123` | Admin console login password |
| `--skip-build` | false | Skip Docker image build (use existing image) |
| `--skip-seed` | false | Skip DynamoDB seed data |

### After deployment

```bash
# Port-forward to access the console
kubectl -n openclaw port-forward svc/admin-console 8099:8099

# Open in browser
open http://localhost:8099

# Login with employee ID: emp-jiade, password: (your --password value)
```

---

## Option B: Terraform Module

For infrastructure-as-code deployments, use the Terraform module at `eks/terraform/modules/admin-console/`.

### Enable in terraform.tfvars

```hcl
enable_admin_console = true
admin_password       = "YOUR_ADMIN_PASSWORD"
```

### Apply

```bash
cd eks/terraform
terraform plan -var="admin_password=YOUR_ADMIN_PASSWORD"
terraform apply -var="admin_password=YOUR_ADMIN_PASSWORD"
```

### Resources created

Same as Option A, plus the Terraform module manages the full lifecycle (create/update/destroy). The module uses `aws_eks_pod_identity_association` for IAM binding.

### Build and push the Docker image

Terraform creates the ECR repository but doesn't build the Docker image. Build and push manually:

```bash
cd enterprise/admin-console

# Get ECR URI from Terraform output
ECR_URI=$(cd ../../eks/terraform && terraform output -raw admin_console_ecr)

# Build and push
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $ECR_URI
docker build -t $ECR_URI:latest .
docker push $ECR_URI:latest

# Restart the deployment to pick up the new image
kubectl -n openclaw rollout restart deployment/admin-console
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  EKS Cluster                                     │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │  openclaw namespace                          │ │
│  │                                               │ │
│  │  ┌───────────────────┐  ┌────────────────┐  │ │
│  │  │  admin-console    │  │  OpenClaw       │  │ │
│  │  │  (FastAPI+React)  │  │  Instances      │  │ │
│  │  │  port 8099        │──│  (CRD-managed)  │  │ │
│  │  └────────┬──────────┘  └────────────────┘  │ │
│  │           │ Pod Identity                      │ │
│  └───────────┼───────────────────────────────────┘ │
│              │                                     │
│  ┌───────────┼───────────────────────────────────┐ │
│  │  openclaw-operator-system                     │ │
│  │  OpenClaw Operator (watches CRDs)             │ │
│  └───────────────────────────────────────────────┘ │
└──────────────┼─────────────────────────────────────┘
               │
    ┌──────────┴──────────┐
    │   AWS Services      │
    │  DynamoDB  S3  SSM  │
    │  ECR  EKS  CloudWatch│
    └─────────────────────┘
```

### Three runtime backends

The admin console manages agents across three runtimes:

| Runtime | Backend | Status Source | Routing |
|---------|---------|---------------|---------|
| **Serverless** (default) | AgentCore microVM | CloudWatch logs | Tenant Router → AgentCore |
| **ECS** | ECS Fargate task | ECS DescribeTasks | SSM endpoint → Fargate task |
| **EKS** | K8s OpenClawInstance CRD | Pod status API | SSM endpoint → K8s Service |

Agents are deployed to EKS by creating an `OpenClawInstance` CRD. The OpenClaw Operator watches for CRDs and creates the StatefulSet, Service, PVC, and ConfigMap.

---

## Seed Data

The deploy script seeds DynamoDB with a sample organization:

- 13 departments (Engineering, Sales, Finance, HR, Legal, etc.)
- 10 positions with role-specific SOUL templates
- 20 employees across all departments
- Skills, knowledge bases, settings

To re-seed an existing deployment:

```bash
cd enterprise/admin-console
bash deploy-eks.sh --cluster dev-cluster --region us-west-2 --skip-build
```

---

## Troubleshooting

### Pod stuck in `CrashLoopBackOff`

```bash
kubectl -n openclaw logs -l app=admin-console --tail=50
```

Common causes:
- **Missing Python dependency**: Check `requirements.txt` includes all imports
- **SSM unreachable**: Pod Identity not configured, or IAM role missing SSM permissions
- **DynamoDB table not found**: Table name or region mismatch in env vars

### Pod Identity not working (403 on AWS API calls)

```bash
# Verify association exists
aws eks list-pod-identity-associations --cluster-name YOUR_CLUSTER --namespace openclaw --region us-west-2

# Verify addon is running
kubectl get pods -n kube-system -l app.kubernetes.io/name=eks-pod-identity-agent
```

### Cannot discover EKS clusters (Settings → EKS tab)

The IAM role needs `eks:ListClusters` and `eks:DescribeCluster` permissions. The deploy script grants these by default.

### OpenClaw Operator not found

Check if the operator is installed:

```bash
kubectl get pods -n openclaw-operator-system
```

Install via the admin console UI (Settings → EKS → Install Operator) or via Helm:

```bash
helm install openclaw-operator oci://ghcr.io/openclaw-rocks/charts/openclaw-operator \
  --namespace openclaw-operator-system --create-namespace
```
