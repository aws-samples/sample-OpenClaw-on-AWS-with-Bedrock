provider "aws" {
  region = local.region
}

provider "kubernetes" {
  host                   = module.eks_cluster.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks_cluster.cluster_ca_data)
  token                  = data.aws_eks_cluster_auth.this.token
}

provider "helm" {
  kubernetes {
    host                   = module.eks_cluster.cluster_endpoint
    cluster_ca_certificate = base64decode(module.eks_cluster.cluster_ca_data)
    token                  = data.aws_eks_cluster_auth.this.token
  }

  registry {
    url      = "oci://public.ecr.aws"
    username = "AWS"
    password = data.aws_ecrpublic_authorization_token.this.password
  }
}

data "aws_ecrpublic_authorization_token" "this" {
  provider = aws.us_east_1
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

provider "kubectl" {
  apply_retry_count      = 30
  host                   = module.eks_cluster.cluster_endpoint
  cluster_ca_certificate = base64decode(module.eks_cluster.cluster_ca_data)
  load_config_file       = false
  token                  = data.aws_eks_cluster_auth.this.token
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_eks_cluster_auth" "this" {
  name = module.eks_cluster.cluster_name
}

data "aws_availability_zones" "available" {
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}

locals {
  name   = var.name
  region = var.region

  is_china_region = var.is_china_region != null ? var.is_china_region : startswith(var.region, "cn-")
  partition       = local.is_china_region ? "aws-cn" : "aws"
  dns_suffix      = local.is_china_region ? "amazonaws.com.cn" : "amazonaws.com"

  pod_identity_principal = "pods.eks.amazonaws.com"

  openclaw_namespace = "openclaw"
  operator_namespace = "openclaw-operator-system"
  kata_namespace     = "kata-system"

  azs = slice(data.aws_availability_zones.available.names, 0, 3)

  # Architecture-based instance type defaults
  default_core_instance_types = var.architecture == "arm64" ? ["m6g.xlarge"] : ["m5.xlarge"]
  core_instance_types         = length(var.core_instance_types) > 0 ? var.core_instance_types : local.default_core_instance_types

  default_kata_instance_types = var.architecture == "arm64" ? ["c6g.metal"] : ["m5.metal", "m5d.metal", "c5.metal", "c5d.metal"]
  kata_instance_types         = var.kata_instance_types != null ? var.kata_instance_types : local.default_kata_instance_types

  ami_type = var.architecture == "arm64" ? "AL2023_ARM_64_STANDARD" : "AL2023_x86_64_STANDARD"

  tags = {
    Blueprint  = local.name
    GithubRepo = "github.com/hitsub2/sample-OpenClaw-on-AWS-with-Bedrock"
    Workload   = "openclaw-eks"
    ManagedBy  = "terraform"
  }
}
