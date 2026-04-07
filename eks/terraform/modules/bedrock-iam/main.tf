################################################################################
# OpenClaw Bedrock IAM Module
#
# Creates IAM roles and policies for OpenClaw pods to access AWS Bedrock,
# using IRSA (IAM Roles for Service Accounts) for secure pod-level credentials.
################################################################################

# -----------------------------------------------------------------------------
# Kubernetes Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "openclaw" {
  metadata {
    name = var.openclaw_namespace
  }
}

# -----------------------------------------------------------------------------
# Bedrock IAM Policy
# -----------------------------------------------------------------------------
resource "aws_iam_policy" "bedrock_access" {
  name        = "${var.name}-bedrock-access"
  description = "Allow OpenClaw pods to invoke Bedrock models"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:ListFoundationModels",
          "bedrock:GetFoundationModel",
          "bedrock:ListInferenceProfiles"
        ]
        Resource = "*"
      }
    ]
  })

  tags = var.tags
}

# -----------------------------------------------------------------------------
# IRSA Role for OpenClaw Bedrock Access
# -----------------------------------------------------------------------------
module "openclaw_bedrock_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${var.name}-openclaw-bedrock"

  role_policy_arns = {
    bedrock = aws_iam_policy.bedrock_access.arn
  }

  oidc_providers = {
    main = {
      provider_arn               = var.oidc_provider_arn
      namespace_service_accounts = ["${var.openclaw_namespace}:openclaw-sandbox"]
    }
  }

  tags = var.tags
}

# -----------------------------------------------------------------------------
# Kubernetes ServiceAccount with IRSA Annotation
# -----------------------------------------------------------------------------
resource "kubernetes_service_account_v1" "openclaw_sandbox" {
  metadata {
    name      = "openclaw-sandbox"
    namespace = kubernetes_namespace_v1.openclaw.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = module.openclaw_bedrock_irsa.iam_role_arn
    }
  }
}
