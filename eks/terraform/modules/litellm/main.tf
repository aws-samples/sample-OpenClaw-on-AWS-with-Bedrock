################################################################################
# LiteLLM Module - OpenAI-compatible proxy to AWS Bedrock
################################################################################

locals {
  namespace              = "litellm"
  service_account_name   = "litellm"
  pod_identity_principal = "pods.eks.${var.is_china_region ? "amazonaws.com.cn" : "amazonaws.com"}"
}

################################################################################
# Random secrets (no hardcoded credentials)
################################################################################

resource "random_password" "master_key" {
  length  = 32
  special = false
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "random_password" "db_admin_password" {
  length  = 32
  special = false
}

################################################################################
# Namespace + Service Account
################################################################################

resource "kubernetes_namespace_v1" "litellm" {
  metadata {
    name = local.namespace
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "app.kubernetes.io/part-of"    = var.cluster_name
    }
  }
}

resource "kubernetes_service_account_v1" "litellm" {
  metadata {
    name      = local.service_account_name
    namespace = kubernetes_namespace_v1.litellm.metadata[0].name
  }
}

################################################################################
# IAM - Bedrock access policy
################################################################################

resource "aws_iam_policy" "litellm_bedrock" {
  name_prefix = "${var.cluster_name}-litellm-bedrock-"
  tags        = var.tags

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      Resource = "*"
    }]
  })
}

################################################################################
# IAM - Pod Identity role
################################################################################

resource "aws_iam_role" "litellm_pod_identity" {
  name = "${var.cluster_name}-litellm-pod-identity"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = local.pod_identity_principal
      }
      Action = [
        "sts:AssumeRole",
        "sts:TagSession",
      ]
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "litellm_bedrock" {
  role       = aws_iam_role.litellm_pod_identity.name
  policy_arn = aws_iam_policy.litellm_bedrock.arn
}

resource "aws_eks_pod_identity_association" "litellm" {
  cluster_name    = var.cluster_name
  namespace       = kubernetes_namespace_v1.litellm.metadata[0].name
  service_account = kubernetes_service_account_v1.litellm.metadata[0].name
  role_arn        = aws_iam_role.litellm_pod_identity.arn
}

################################################################################
# LiteLLM Helm release
################################################################################

resource "helm_release" "litellm" {
  name      = "litellm"
  chart     = "oci://ghcr.io/berriai/litellm-helm"
  namespace = kubernetes_namespace_v1.litellm.metadata[0].name

  timeout = 600

  # Use pre-created service account with Pod Identity bindings
  set {
    name  = "serviceAccount.create"
    value = "false"
  }

  set {
    name  = "serviceAccount.name"
    value = kubernetes_service_account_v1.litellm.metadata[0].name
  }

  # Container image
  set {
    name  = "image.tag"
    value = "litellm-main-latest"
  }

  set {
    name  = "image.repository"
    value = "public.ecr.aws/t6v6o5d5/kube-prometheus"
  }

  # ------------------------------------------------------------------
  # Master key (generated, never hardcoded)
  # ------------------------------------------------------------------
  set_sensitive {
    name  = "envVars.LITELLM_MASTER_KEY"
    value = random_password.master_key.result
  }

  # ------------------------------------------------------------------
  # PostgreSQL backend (bitnami sub-chart, deployed alongside LiteLLM)
  # ------------------------------------------------------------------
  set {
    name  = "db.deployStandalone"
    value = "true"
  }

  set {
    name  = "envVars.STORE_MODEL_IN_DB"
    value = "True"
  }

  set {
    name  = "proxy_config.general_settings.database_url"
    value = "os.environ/DATABASE_URL"
  }

  set {
    name  = "db.url"
    value = "postgresql://$(DATABASE_USERNAME):$(DATABASE_PASSWORD)@$(DATABASE_HOST)/$(DATABASE_NAME)"
  }

  set {
    name  = "global.security.allowInsecureImages"
    value = "true"
  }

  # PostgreSQL image from public ECR mirror
  set {
    name  = "postgresql.image.registry"
    value = "public.ecr.aws"
  }

  set {
    name  = "postgresql.image.repository"
    value = "bitnami/postgresql"
  }

  set {
    name  = "postgresql.image.tag"
    value = "latest"
  }

  set_sensitive {
    name  = "postgresql.auth.password"
    value = random_password.db_password.result
  }

  set_sensitive {
    name  = "postgresql.auth.postgres-password"
    value = random_password.db_admin_password.result
  }

  # ------------------------------------------------------------------
  # Default model: Claude Sonnet 4.5 via AWS Bedrock
  # Uses cross-region inference profile for optimal availability.
  # ------------------------------------------------------------------
  set {
    name  = "proxy_config.model_list[0].model_name"
    value = "claude-sonnet-4-5"
  }

  set {
    name  = "proxy_config.model_list[0].litellm_params.model"
    value = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"
  }

  # ------------------------------------------------------------------
  # LiteLLM settings
  # ------------------------------------------------------------------
  set {
    name  = "proxy_config.litellm_settings.drop_params"
    value = "true"
  }

  # Enable Prometheus metrics callback
  set {
    name  = "proxy_config.litellm_settings.callbacks[0]"
    value = "prometheus"
  }

  # Disable built-in ServiceMonitor -- we create a custom one below with
  # the correct scrape path.
  set {
    name  = "serviceMonitor.enabled"
    value = "false"
  }
}

################################################################################
# ServiceMonitor for Prometheus scraping
################################################################################

resource "kubectl_manifest" "litellm_servicemonitor" {
  yaml_body = yamlencode({
    apiVersion = "monitoring.coreos.com/v1"
    kind       = "ServiceMonitor"
    metadata = {
      name      = "litellm"
      namespace = kubernetes_namespace_v1.litellm.metadata[0].name
      labels = {
        release = "kube-prometheus-stack"
      }
    }
    spec = {
      selector = {
        matchLabels = {
          "app.kubernetes.io/name"     = "litellm"
          "app.kubernetes.io/instance" = "litellm"
        }
      }
      endpoints = [{
        port          = "http"
        path          = "/metrics"
        interval      = "30s"
        scrapeTimeout = "10s"
      }]
    }
  })

  depends_on = [helm_release.litellm]
}
