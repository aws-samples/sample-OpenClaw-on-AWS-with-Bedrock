################################################################################
# EKS Cluster Module
################################################################################

data "aws_caller_identity" "current" {}
data "aws_iam_session_context" "current" {
  arn = data.aws_caller_identity.current.arn
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = var.name
  cluster_version = var.cluster_version

  cluster_endpoint_public_access = true

  enable_cluster_creator_admin_permissions = false

  access_entries = var.access_entries

  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids

  create_kms_key           = false
  cluster_encryption_config = {}

  cluster_security_group_additional_rules = {
    ingress_nodes_ephemeral_ports_tcp = {
      description                = "Nodes on ephemeral ports"
      protocol                   = "tcp"
      from_port                  = 1025
      to_port                    = 65535
      type                       = "ingress"
      source_node_security_group = true
    }
  }

  node_security_group_additional_rules = {
    ingress_cluster_to_node_all_traffic = {
      description                   = "Cluster API to Nodegroup all traffic"
      protocol                      = "-1"
      from_port                     = 0
      to_port                       = 0
      type                          = "ingress"
      source_cluster_security_group = true
    }
  }

  eks_managed_node_group_defaults = {
    iam_role_additional_policies = {
      AmazonSSMManagedInstanceCore = "arn:${var.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
      AmazonEFSCSIDriverPolicy     = "arn:${var.partition}:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy"
    }

    ebs_optimized = true
    block_device_mappings = {
      xvda = {
        device_name = "/dev/xvda"
        ebs = {
          volume_size = 100
          volume_type = "gp3"
        }
      }
    }
  }

  eks_managed_node_groups = {
    core_node_group = {
      name        = "core-node-group"
      description = "Core node group for system workloads"

      subnet_ids = var.subnet_ids

      min_size     = var.core_node_count.min
      max_size     = var.core_node_count.max
      desired_size = var.core_node_count.desired

      ami_type       = var.ami_type
      instance_types = var.core_instance_types

      labels = {
        WorkerType    = "ON_DEMAND"
        NodeGroupType = "core"
      }
    }
  }

  node_security_group_tags = {
    "karpenter.sh/discovery" = var.name
  }

  cluster_addons = {
    coredns = {
      most_recent = true
    }
    vpc-cni = {
      most_recent = true
      pod_identity_association = [{
        role_arn        = aws_iam_role.vpc_cni.arn
        service_account = "aws-node"
      }]
    }
    eks-pod-identity-agent = {
      most_recent = true
    }
    aws-ebs-csi-driver = {
      most_recent = true
      pod_identity_association = [{
        role_arn        = aws_iam_role.ebs_csi.arn
        service_account = "ebs-csi-controller-sa"
      }]
    }
    aws-efs-csi-driver = {
      most_recent = true
      pod_identity_association = [{
        role_arn        = aws_iam_role.efs_csi.arn
        service_account = "efs-csi-controller-sa"
      }]
    }
  }

  tags = var.tags
}

################################################################################
# VPC CNI - Pod Identity IAM Role
################################################################################

resource "aws_iam_role" "vpc_cni" {
  name_prefix = "${var.name}-vpc-cni-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "pods.eks.amazonaws.com"
      }
      Action = [
        "sts:AssumeRole",
        "sts:TagSession"
      ]
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "vpc_cni" {
  role       = aws_iam_role.vpc_cni.name
  policy_arn = "arn:${var.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
}

################################################################################
# EBS CSI Driver - Pod Identity IAM Role
################################################################################

resource "aws_iam_role" "ebs_csi" {
  name_prefix = "${var.name}-ebs-csi-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "pods.eks.amazonaws.com"
      }
      Action = [
        "sts:AssumeRole",
        "sts:TagSession"
      ]
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:${var.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

################################################################################
# EFS CSI Driver - Pod Identity IAM Role (managed addon, always enabled)
################################################################################

resource "aws_iam_role" "efs_csi" {
  name_prefix = "${var.name}-efs-csi-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "pods.eks.amazonaws.com"
      }
      Action = [
        "sts:AssumeRole",
        "sts:TagSession"
      ]
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "efs_csi" {
  role       = aws_iam_role.efs_csi.name
  policy_arn = "arn:${var.partition}:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy"
}
