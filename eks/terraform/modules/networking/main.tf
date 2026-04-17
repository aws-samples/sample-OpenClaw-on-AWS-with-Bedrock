################################################################################
# Networking -- ALB Controller + CloudFront (optional)
################################################################################

# --- ALB Controller IRSA ------------------------------------------------------

module "alb_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name_prefix                       = "${var.cluster_name}-alb-"
  attach_load_balancer_controller_policy = true

  oidc_providers = {
    main = {
      provider_arn               = var.oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }

  tags = var.tags
}

# --- ALB Controller Helm Release ----------------------------------------------

resource "helm_release" "alb_controller" {
  name       = "aws-load-balancer-controller"
  repository = var.chart_repository != "" ? var.chart_repository : (var.is_china_region ? "oci://public.ecr.aws/eks" : "https://aws.github.io/eks-charts")
  chart      = "aws-load-balancer-controller"
  namespace  = "kube-system"

  set {
    name  = "clusterName"
    value = var.cluster_name
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = module.alb_controller_irsa.iam_role_arn
  }

  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }

  set {
    name  = "vpcId"
    value = var.vpc_id
  }

  timeout = 300
}

# --- CloudFront (placeholder) ------------------------------------------------
# CloudFront distribution is typically configured post-deployment when the ALB
# DNS name is known. This block can be extended to create an
# aws_cloudfront_distribution resource once the ALB Ingress is provisioned.
# For now the module outputs the ALB controller role ARN so downstream callers
# can wire up additional infrastructure.
