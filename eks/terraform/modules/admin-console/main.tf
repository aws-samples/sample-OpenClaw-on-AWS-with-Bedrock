################################################################################
# Admin Console Module
#
# Deploys the OpenClaw Admin Console to EKS with all required AWS resources:
#   - DynamoDB table (single-table design, on-demand billing)
#   - S3 bucket (SOUL templates, workspaces, knowledge docs)
#   - ECR repository (admin console Docker image)
#   - IAM role with EKS Pod Identity (DynamoDB, S3, SSM, EKS, ECR, CloudWatch)
#   - SSM parameters (admin password, JWT secret)
#   - Kubernetes Deployment + Service
################################################################################

locals {
  stack_name      = var.name
  ecr_repo_name   = "${var.name}/admin-console"
  dynamodb_table  = "${var.name}-enterprise"
  s3_bucket       = "${var.name}-workspaces-${data.aws_caller_identity.current.account_id}"
  service_account = "admin-console"
}

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# ECR Repository
# -----------------------------------------------------------------------------
resource "aws_ecr_repository" "admin_console" {
  count = var.image_repository == "" ? 1 : 0

  name                 = local.ecr_repo_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

locals {
  ecr_uri = var.image_repository != "" ? var.image_repository : aws_ecr_repository.admin_console[0].repository_url
}

# -----------------------------------------------------------------------------
# DynamoDB Table (single-table design)
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "enterprise" {
  name         = local.dynamodb_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  tags = var.tags
}

# -----------------------------------------------------------------------------
# S3 Bucket (workspaces, SOUL, knowledge)
# -----------------------------------------------------------------------------
resource "aws_s3_bucket" "workspaces" {
  bucket        = local.s3_bucket
  force_destroy = true
  tags          = var.tags
}

resource "aws_s3_bucket_versioning" "workspaces" {
  bucket = aws_s3_bucket.workspaces.id
  versioning_configuration {
    status = "Enabled"
  }
}

# -----------------------------------------------------------------------------
# SSM Parameters
# -----------------------------------------------------------------------------
resource "aws_ssm_parameter" "admin_password" {
  name  = "/openclaw/${local.stack_name}/admin-password"
  type  = "SecureString"
  value = var.admin_password
  tags  = var.tags
}

resource "aws_ssm_parameter" "jwt_secret" {
  name  = "/openclaw/${local.stack_name}/jwt-secret"
  type  = "SecureString"
  value = random_password.jwt_secret.result
  tags  = var.tags
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

# -----------------------------------------------------------------------------
# IAM Role (EKS Pod Identity — not IRSA)
# -----------------------------------------------------------------------------
data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
    actions = ["sts:AssumeRole", "sts:TagSession"]
  }
}

resource "aws_iam_role" "admin_console" {
  name               = "${var.name}-admin-console"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "admin_console" {
  name = "admin-console-access"
  role = aws_iam_role.admin_console.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDB"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
          "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
          "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem",
          "dynamodb:DescribeTable",
        ]
        Resource = [
          aws_dynamodb_table.enterprise.arn,
          "${aws_dynamodb_table.enterprise.arn}/index/*",
        ]
      },
      {
        Sid    = "S3"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
          "s3:ListBucket", "s3:GetObjectVersion", "s3:ListBucketVersions",
        ]
        Resource = [
          aws_s3_bucket.workspaces.arn,
          "${aws_s3_bucket.workspaces.arn}/*",
        ]
      },
      {
        Sid    = "SSM"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath",
          "ssm:PutParameter", "ssm:DeleteParameter",
        ]
        Resource = "arn:${var.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/openclaw/${local.stack_name}/*"
      },
      {
        Sid      = "EKS"
        Effect   = "Allow"
        Action   = ["eks:ListClusters", "eks:DescribeCluster"]
        Resource = "*"
      },
      {
        Sid    = "ECR"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage", "ecr:DescribeImages", "ecr:DescribeRepositories",
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:FilterLogEvents", "logs:DescribeLogGroups",
          "logs:GetLogEvents",
        ]
        Resource = "*"
      },
      {
        Sid      = "STS"
        Effect   = "Allow"
        Action   = ["sts:GetCallerIdentity"]
        Resource = "*"
      },
    ]
  })
}

# -----------------------------------------------------------------------------
# RBAC — Admin Console needs K8s API access to manage OpenClawInstance CRDs,
# read pods/logs/deployments (operator status), and list CRDs.
# -----------------------------------------------------------------------------
resource "kubernetes_cluster_role_v1" "admin_console" {
  metadata {
    name = "admin-console"
  }

  rule {
    api_groups = ["openclaw.rocks"]
    resources  = ["openclawinstances", "openclawselfconfigs"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "services", "serviceaccounts", "namespaces"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "statefulsets"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = ["apiextensions.k8s.io"]
    resources  = ["customresourcedefinitions"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "admin_console" {
  metadata {
    name = "admin-console"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.admin_console.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = local.service_account
    namespace = var.openclaw_namespace
  }
}

# -----------------------------------------------------------------------------
# EKS Pod Identity Association
# -----------------------------------------------------------------------------
resource "kubernetes_service_account_v1" "admin_console" {
  metadata {
    name      = local.service_account
    namespace = var.openclaw_namespace
  }
}

resource "aws_eks_pod_identity_association" "admin_console" {
  cluster_name    = var.cluster_name
  namespace       = var.openclaw_namespace
  service_account = local.service_account
  role_arn        = aws_iam_role.admin_console.arn

  tags = var.tags

  depends_on = [kubernetes_service_account_v1.admin_console]
}

# -----------------------------------------------------------------------------
# Kubernetes Deployment
# -----------------------------------------------------------------------------
resource "kubernetes_deployment_v1" "admin_console" {
  metadata {
    name      = "admin-console"
    namespace = var.openclaw_namespace
    labels    = { app = "admin-console" }
  }

  spec {
    replicas = 1

    selector {
      match_labels = { app = "admin-console" }
    }

    template {
      metadata {
        labels = { app = "admin-console" }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.admin_console.metadata[0].name

        container {
          name  = "admin-console"
          image = "${local.ecr_uri}:${var.image_tag}"

          port {
            container_port = 8099
            name           = "http"
          }

          env {
            name  = "AWS_REGION"
            value = var.region
          }
          env {
            name  = "GATEWAY_REGION"
            value = var.region
          }
          env {
            name  = "DYNAMODB_TABLE"
            value = local.dynamodb_table
          }
          env {
            name  = "DYNAMODB_REGION"
            value = var.region
          }
          env {
            name  = "S3_BUCKET"
            value = aws_s3_bucket.workspaces.id
          }
          env {
            name  = "STACK_NAME"
            value = local.stack_name
          }
          env {
            name  = "CONSOLE_PORT"
            value = "8099"
          }
          env {
            name  = "K8S_IN_CLUSTER"
            value = "true"
          }
          env {
            name  = "OPENCLAW_NAMESPACE"
            value = var.openclaw_namespace
          }

          resources {
            requests = {
              cpu    = "250m"
              memory = "512Mi"
            }
            limits = {
              cpu    = "1"
              memory = "1Gi"
            }
          }

          readiness_probe {
            tcp_socket {
              port = 8099
            }
            initial_delay_seconds = 5
            period_seconds        = 10
          }

          liveness_probe {
            tcp_socket {
              port = 8099
            }
            initial_delay_seconds = 10
            period_seconds        = 30
          }
        }
      }
    }
  }

  depends_on = [aws_eks_pod_identity_association.admin_console]
}

# -----------------------------------------------------------------------------
# Kubernetes Service
# -----------------------------------------------------------------------------
resource "kubernetes_service_v1" "admin_console" {
  metadata {
    name      = "admin-console"
    namespace = var.openclaw_namespace
  }

  spec {
    selector = { app = "admin-console" }

    port {
      port        = 8099
      target_port = 8099
      name        = "http"
    }

    type = "ClusterIP"
  }
}
