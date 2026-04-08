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
# Helm Release — deploys ServiceAccount, RBAC, Deployment, Service
# Uses the chart at enterprise/admin-console/chart/
# -----------------------------------------------------------------------------
resource "helm_release" "admin_console" {
  name      = "admin-console"
  chart     = "${path.module}/../../../../enterprise/admin-console/chart"
  namespace = var.openclaw_namespace

  set {
    name  = "image.repository"
    value = local.ecr_uri
  }
  set {
    name  = "image.tag"
    value = var.image_tag
  }
  set {
    name  = "aws.region"
    value = var.region
  }
  set {
    name  = "aws.stackName"
    value = local.stack_name
  }
  set {
    name  = "aws.dynamodbTable"
    value = local.dynamodb_table
  }
  set {
    name  = "aws.dynamodbRegion"
    value = var.region
  }
  set {
    name  = "aws.s3Bucket"
    value = aws_s3_bucket.workspaces.id
  }
  set_sensitive {
    name  = "auth.adminPassword"
    value = var.admin_password
  }
  set {
    name  = "namespace"
    value = var.openclaw_namespace
  }

  # Ingress — enabled by default in Terraform deployments
  set {
    name  = "ingress.enabled"
    value = "true"
  }
  set {
    name  = "ingress.className"
    value = var.ingress_class
  }
  dynamic "set" {
    for_each = var.ingress_host != "" ? [1] : []
    content {
      name  = "ingress.host"
      value = var.ingress_host
    }
  }
  # When a certificate is provided, switch to HTTPS; otherwise default HTTP is fine
  dynamic "set" {
    for_each = var.ingress_certificate_arn != "" ? [1] : []
    content {
      name  = "ingress.annotations.alb\\.ingress\\.kubernetes\\.io/certificate-arn"
      value = var.ingress_certificate_arn
    }
  }
  dynamic "set" {
    for_each = var.ingress_certificate_arn != "" ? [1] : []
    content {
      name  = "ingress.annotations.alb\\.ingress\\.kubernetes\\.io/listen-ports"
      value = "[{\"HTTPS\":443}]"
    }
  }
  dynamic "set" {
    for_each = var.ingress_certificate_arn != "" ? [1] : []
    content {
      name  = "ingress.annotations.alb\\.ingress\\.kubernetes\\.io/ssl-redirect"
      value = "443"
    }
  }

  wait    = true
  timeout = 300

}

# -----------------------------------------------------------------------------
# EKS Pod Identity Association (AWS-side — not in Helm chart)
# Pod Identity references the SA by name; it doesn't need the SA to exist first.
# The Helm chart creates the SA; the pod picks up the identity on next restart.
# -----------------------------------------------------------------------------
resource "aws_eks_pod_identity_association" "admin_console" {
  cluster_name    = var.cluster_name
  namespace       = var.openclaw_namespace
  service_account = local.service_account
  role_arn        = aws_iam_role.admin_console.arn

  tags = var.tags
}

# -----------------------------------------------------------------------------
# Bootstrap: create admin user (always runs on first deploy)
# This is the minimum required to login — just 1 employee + 1 department.
# -----------------------------------------------------------------------------
resource "null_resource" "bootstrap_admin" {
  triggers = {
    table_arn = aws_dynamodb_table.enterprise.arn
  }

  provisioner "local-exec" {
    environment = {
      AWS_REGION = var.region
    }
    command = <<-EOT
      echo "[bootstrap] Ensuring admin user in ${aws_dynamodb_table.enterprise.name}"
      python3 -c "
import boto3
ddb = boto3.resource('dynamodb', region_name='${var.region}')
table = ddb.Table('${aws_dynamodb_table.enterprise.name}')

# Create admin user if not exists
try:
    table.put_item(
        Item={
            'PK': 'ORG#acme', 'SK': 'EMP#emp-admin',
            'GSI1PK': 'TYPE#employee', 'GSI1SK': 'EMP#emp-admin',
            'id': 'emp-admin', 'name': 'Admin',
            'email': 'admin@example.com',
            'department': 'IT', 'departmentId': 'dept-it',
            'position': 'Platform Admin', 'positionId': 'pos-admin',
            'role': 'admin', 'status': 'active',
        },
        ConditionExpression='attribute_not_exists(PK)',
    )
    print('[bootstrap] Admin user created: emp-admin')
except table.meta.client.exceptions.ConditionalCheckFailedException:
    # User exists (maybe from demo seed) — ensure role=admin
    table.update_item(
        Key={'PK': 'ORG#acme', 'SK': 'EMP#emp-admin'},
        UpdateExpression='SET #r = :role',
        ExpressionAttributeNames={'#r': 'role'},
        ExpressionAttributeValues={':role': 'admin'},
    )
    print('[bootstrap] Admin user exists, ensured role=admin')

# Ensure department exists
try:
    table.put_item(
        Item={
            'PK': 'ORG#acme', 'SK': 'DEPT#dept-it',
            'GSI1PK': 'TYPE#department', 'GSI1SK': 'DEPT#dept-it',
            'id': 'dept-it', 'name': 'IT', 'parentId': '',
        },
        ConditionExpression='attribute_not_exists(PK)',
    )
except Exception:
    pass
"
      echo "[bootstrap] Done"
    EOT
  }

  depends_on = [
    aws_dynamodb_table.enterprise,
    aws_ssm_parameter.admin_password,
  ]
}

# -----------------------------------------------------------------------------
# Demo Data (optional) — sample org with 20 employees, SOUL templates, KBs
# Only runs when seed_demo_data = true. Idempotent (won't overwrite existing).
# To re-seed: terraform taint 'module.admin_console[0].null_resource.seed_demo'
# -----------------------------------------------------------------------------
resource "null_resource" "seed_demo" {
  count = var.seed_demo_data ? 1 : 0

  triggers = {
    table_arn = aws_dynamodb_table.enterprise.arn
    bucket    = aws_s3_bucket.workspaces.id
  }

  provisioner "local-exec" {
    working_dir = "${path.module}/../../../../enterprise/admin-console/server"
    environment = {
      AWS_REGION        = var.region
      SEED_NO_OVERWRITE = "1"
    }
    command = <<-EOT
      echo "[demo-seed] Seeding demo data into ${aws_dynamodb_table.enterprise.name} (skip existing)"
      python3 seed_dynamodb.py   --table "${aws_dynamodb_table.enterprise.name}" --region "${var.region}" 2>/dev/null || echo "[demo-seed] seed_dynamodb.py skipped"
      python3 seed_roles.py      --table "${aws_dynamodb_table.enterprise.name}" --region "${var.region}" 2>/dev/null || echo "[demo-seed] seed_roles.py skipped"
      python3 seed_settings.py   --table "${aws_dynamodb_table.enterprise.name}" --region "${var.region}" 2>/dev/null || echo "[demo-seed] seed_settings.py skipped"
      python3 seed_knowledge_docs.py --bucket "${aws_s3_bucket.workspaces.id}" --region "${var.region}" 2>/dev/null || echo "[demo-seed] seed_knowledge_docs.py skipped"
      python3 seed_ssm_tenants.py --region "${var.region}" --stack "${local.stack_name}" 2>/dev/null || echo "[demo-seed] seed_ssm_tenants.py skipped"

      echo "[demo-seed] Uploading SOUL templates to S3"
      if [ -d "../server/soul-templates" ]; then
        aws s3 sync "../server/soul-templates/" "s3://${aws_s3_bucket.workspaces.id}/_shared/" \
          --region "${var.region}" --size-only --quiet
      fi

      echo "[demo-seed] Done"
    EOT
  }

  depends_on = [
    aws_dynamodb_table.enterprise,
    aws_s3_bucket.workspaces,
    null_resource.bootstrap_admin,
  ]
}
