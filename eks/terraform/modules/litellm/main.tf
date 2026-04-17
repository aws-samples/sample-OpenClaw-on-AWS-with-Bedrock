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
  count   = var.enable_db ? 1 : 0
  length  = 32
  special = false
}

resource "random_password" "db_admin_password" {
  count   = var.enable_db ? 1 : 0
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
  force_update = true
  name       = "litellm"
  repository = var.chart_repository != "" ? var.chart_repository : "oci://ghcr.io/berriai"
  chart      = "litellm-helm"
  namespace  = kubernetes_namespace_v1.litellm.metadata[0].name

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

  # Container image — controlled by enable_db toggle (see DB mode section below)
  set {
    name  = "image.tag"
    value = "main-latest"
  }

  # ------------------------------------------------------------------
  # Master key (generated, never hardcoded)
  # ------------------------------------------------------------------
  set_sensitive {
    name  = "envVars.LITELLM_MASTER_KEY"
    value = "sk-${random_password.master_key.result}"
  }

  # ------------------------------------------------------------------
  # DB mode toggle
  # enable_db=false (default): stateless config-only, lighter image, no PG
  # enable_db=true: PostgreSQL sidecar, virtual key management enabled
  # ------------------------------------------------------------------
  set {
    name  = "image.repository"
    value = var.enable_db ? (var.ecr_host != "" ? "${var.ecr_host}/berriai/litellm-database" : "ghcr.io/berriai/litellm-database") : (var.ecr_host != "" ? "${var.ecr_host}/berriai/litellm" : "ghcr.io/berriai/litellm")
  }

  # db.deployStandalone=false: we deploy PostgreSQL ourselves via helm_release.postgresql
  set {
    name  = "db.deployStandalone"
    value = "false"
  }

  set {
    name  = "db.useExisting"
    value = var.enable_db ? "true" : "false"
  }

  dynamic "set" {
    for_each = var.enable_db ? [1] : []
    content {
      name  = "db.endpoint"
      value = "litellm-postgresql.${kubernetes_namespace_v1.litellm.metadata[0].name}.svc"
    }
  }

  set {
    name  = "envVars.STORE_MODEL_IN_DB"
    value = var.enable_db ? "True" : "False"
  }

  dynamic "set_sensitive" {
    for_each = var.enable_db ? [1] : []
    content {
      name  = "envVars.DATABASE_PASSWORD"
      value = random_password.db_password[0].result
    }
  }

  depends_on = [helm_release.postgresql]

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

  # Prometheus metrics callback — only enable when Prometheus is deployed
  dynamic "set" {
    for_each = var.enable_monitoring ? [1] : []
    content {
      name  = "proxy_config.litellm_settings.callbacks[0]"
      value = "prometheus"
    }
  }

  # Always disable built-in ServiceMonitor (we create our own when needed)
  set {
    name  = "serviceMonitor.enabled"
    value = "false"
  }
}

################################################################################
# PostgreSQL (only when enable_db = true)
# Deployed as a standalone helm_release so the chart source is fully controlled
# (supports ECR mirror for China). LiteLLM connects via db.useExisting=true.
################################################################################

resource "helm_release" "postgresql" {
  count      = var.enable_db ? 1 : 0
  name       = "litellm-postgresql"
  repository = var.chart_repository != "" ? "${var.chart_repository}/charts" : "oci://registry-1.docker.io/bitnamicharts"
  chart      = "postgresql"
  version    = "18.5.10"
  namespace  = kubernetes_namespace_v1.litellm.metadata[0].name

  set {
    name  = "auth.username"
    value = "litellm"
  }

  set {
    name  = "auth.database"
    value = "litellm"
  }

  set_sensitive {
    name  = "auth.password"
    value = random_password.db_password[0].result
  }

  set_sensitive {
    name  = "auth.postgresPassword"
    value = random_password.db_admin_password[0].result
  }

  set {
    name  = "primary.persistence.storageClass"
    value = "ebs-sc"
  }

  set {
    name  = "image.registry"
    value = var.ecr_host != "" ? var.ecr_host : "docker.io"
  }
}

################################################################################
# ServiceMonitor for Prometheus scraping (only when enable_monitoring = true)
################################################################################

resource "kubectl_manifest" "litellm_servicemonitor" {
  count = var.enable_monitoring ? 1 : 0
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
