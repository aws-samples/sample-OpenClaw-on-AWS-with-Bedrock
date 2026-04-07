################################################################################
# OpenClaw Operator Module
#
# Deploys the OpenClaw Operator into the EKS cluster via Helm.
# Supports both global and China region deployments with ECR mirror images.
################################################################################

# -----------------------------------------------------------------------------
# Operator Namespace
# -----------------------------------------------------------------------------
resource "kubernetes_namespace_v1" "operator" {
  metadata {
    name = var.operator_namespace
  }
}

# -----------------------------------------------------------------------------
# OpenClaw Operator Helm Release
# -----------------------------------------------------------------------------
resource "helm_release" "openclaw_operator" {
  name       = "openclaw-operator"
  repository = "oci://ghcr.io/openclaw-rocks/charts"
  chart      = "openclaw-operator"
  version    = var.operator_version
  namespace  = kubernetes_namespace_v1.operator.metadata[0].name

  set {
    name  = "crds.install"
    value = "true"
  }

  set {
    name  = "crds.keep"
    value = "true"
  }

  # For China region, use ECR mirror image
  dynamic "set" {
    for_each = var.is_china_region ? [1] : []
    content {
      name  = "image.repository"
      value = "public.ecr.aws/t6v6o5d5/kube-prometheus"
    }
  }

  dynamic "set" {
    for_each = var.is_china_region ? [1] : []
    content {
      name  = "image.tag"
      value = "openclaw-operator-v${var.operator_version}"
    }
  }

  timeout = 600
}
