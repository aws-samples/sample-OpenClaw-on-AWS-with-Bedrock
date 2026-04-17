# =============================================================================
# Core Infrastructure (always deployed)
# =============================================================================

module "vpc" {
  source = "./modules/vpc"

  name     = local.name
  vpc_cidr = var.vpc_cidr
  azs      = local.azs

  enable_alb_controller = var.enable_alb_controller
  enable_karpenter      = var.enable_karpenter
  cluster_name          = local.name

  tags = local.tags
}

module "eks_cluster" {
  source = "./modules/eks-cluster"

  name            = local.name
  cluster_version = var.eks_cluster_version
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets

  ami_type            = local.ami_type
  core_instance_types = local.core_instance_types
  core_node_count     = var.core_node_count

  # Explicitly add the Terraform caller (e.g. CodeBuild role) as cluster admin.
  # enable_cluster_creator_admin_permissions=true should handle this, but doesn't
  # reliably work with assumed roles in some EKS module versions.
  access_entries = merge(var.access_entries, {
    terraform_caller = {
      principal_arn = data.aws_iam_session_context.current.issuer_arn
      type          = "STANDARD"
      policy_associations = {
        admin = {
          policy_arn = "arn:${local.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = {
            type = "cluster"
          }
        }
      }
    }
  })
  is_china_region = local.is_china_region
  partition       = local.partition

  tags = local.tags
}

module "storage" {
  source = "./modules/storage"

  cluster_name           = module.eks_cluster.cluster_name
  vpc_id                 = module.vpc.vpc_id
  private_subnets        = module.vpc.private_subnets
  node_security_group_id = module.eks_cluster.node_security_group_id

  enable_efs      = var.enable_efs
  is_china_region = local.is_china_region
  partition       = local.partition

  tags = local.tags

  depends_on = [module.eks_cluster]
}

# =============================================================================
# Bedrock IAM (always deployed — needed for OpenClaw model access)
# =============================================================================

module "bedrock_iam" {
  source = "./modules/bedrock-iam"

  name                = local.name
  cluster_name        = module.eks_cluster.cluster_name
  cluster_oidc_issuer = module.eks_cluster.oidc_issuer
  oidc_provider_arn   = module.eks_cluster.oidc_provider_arn
  openclaw_namespace  = local.openclaw_namespace

  is_china_region = local.is_china_region
  partition       = local.partition

  tags = local.tags

  depends_on = [module.eks_cluster]
}

# =============================================================================
# OpenClaw Operator (always deployed)
# =============================================================================

module "operator" {
  source = "./modules/operator"

  cluster_name       = module.eks_cluster.cluster_name
  operator_namespace = local.operator_namespace
  chart_repository   = local.chart_repository
  ecr_host           = local.is_china_region ? local.ecr_host : ""
  is_china_region    = local.is_china_region

  tags = local.tags

  depends_on = [module.eks_cluster]
}

# =============================================================================
# Optional: Kata Containers + Karpenter
# =============================================================================

module "kata" {
  count  = var.enable_kata ? 1 : 0
  source = "./modules/kata"

  cluster_name        = module.eks_cluster.cluster_name
  cluster_endpoint    = module.eks_cluster.cluster_endpoint
  cluster_ca_data     = module.eks_cluster.cluster_ca_data
  kata_namespace      = local.kata_namespace
  kata_hypervisor     = var.kata_hypervisor
  kata_instance_types = local.kata_instance_types
  architecture        = var.architecture
  enable_karpenter    = var.enable_karpenter
  node_iam_role_name  = module.eks_cluster.node_iam_role_name
  vpc_cidr            = var.vpc_cidr

  chart_repository = local.chart_repository
  ecr_host         = local.is_china_region ? local.ecr_host : ""
  is_china_region  = local.is_china_region
  partition        = local.partition

  tags = local.tags

  depends_on = [module.eks_cluster]
}

# =============================================================================
# Optional: Networking (ALB Controller + CloudFront)
# =============================================================================

module "networking" {
  count  = var.enable_alb_controller ? 1 : 0
  source = "./modules/networking"

  cluster_name        = module.eks_cluster.cluster_name
  cluster_version     = var.eks_cluster_version
  oidc_provider_arn   = module.eks_cluster.oidc_provider_arn
  cluster_oidc_issuer = module.eks_cluster.oidc_issuer
  vpc_id              = module.vpc.vpc_id

  enable_cloudfront = var.enable_cloudfront

  chart_repository = local.chart_repository
  is_china_region  = local.is_china_region
  partition        = local.partition

  tags = local.tags

  depends_on = [module.eks_cluster]
}

# =============================================================================
# Optional: Monitoring (Prometheus + Grafana)
# =============================================================================

module "monitoring" {
  count  = var.enable_monitoring ? 1 : 0
  source = "./modules/monitoring"

  cluster_name     = module.eks_cluster.cluster_name
  chart_repository = local.chart_repository
  ecr_host         = local.is_china_region ? local.ecr_host : ""

  tags = local.tags

  depends_on = [module.storage]
}

# =============================================================================
# Optional: LiteLLM AI Proxy
# =============================================================================

module "litellm" {
  count  = var.enable_litellm ? 1 : 0
  source = "./modules/litellm"

  cluster_name        = module.eks_cluster.cluster_name
  cluster_oidc_issuer = module.eks_cluster.oidc_issuer
  oidc_provider_arn   = module.eks_cluster.oidc_provider_arn

  chart_repository  = local.chart_repository
  ecr_host          = local.is_china_region ? local.ecr_host : ""
  is_china_region   = local.is_china_region
  partition         = local.partition
  enable_monitoring = var.enable_monitoring

  tags = local.tags

  depends_on = [module.eks_cluster]
}

# =============================================================================
# Optional: Agent Sandbox CRDs
# =============================================================================

module "agent_sandbox" {
  count  = var.enable_agent_sandbox ? 1 : 0
  source = "./modules/agent-sandbox"

  cluster_name = module.eks_cluster.cluster_name

  depends_on = [module.eks_cluster]
}
