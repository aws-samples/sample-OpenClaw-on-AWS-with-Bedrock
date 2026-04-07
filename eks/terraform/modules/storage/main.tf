################################################################################
# Storage Module - EBS StorageClass (always created)
################################################################################

resource "kubernetes_storage_class_v1" "ebs_gp3" {
  metadata {
    name = "ebs-sc"
    annotations = {
      # EFS is the default when enabled; EBS is fallback for block storage workloads
      "storageclass.kubernetes.io/is-default-class" = var.enable_efs ? "false" : "true"
    }
  }

  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Delete"
  allow_volume_expansion = true
  volume_binding_mode    = "WaitForFirstConsumer"

  parameters = {
    type      = "gp3"
    encrypted = "true"
  }
}

################################################################################
# EFS File System (conditional)
################################################################################

resource "aws_efs_file_system" "this" {
  count = var.enable_efs ? 1 : 0

  creation_token = "${var.cluster_name}-efs"
  encrypted      = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-efs"
  })
}

################################################################################
# EFS Mount Targets (conditional)
################################################################################

resource "aws_efs_mount_target" "this" {
  count = var.enable_efs ? length(var.private_subnets) : 0

  file_system_id  = aws_efs_file_system.this[0].id
  subnet_id       = var.private_subnets[count.index]
  security_groups = [aws_security_group.efs[0].id]
}

################################################################################
# EFS Security Group (conditional)
################################################################################

resource "aws_security_group" "efs" {
  count = var.enable_efs ? 1 : 0

  name_prefix = "${var.cluster_name}-efs-"
  description = "Security group for EFS mount targets"
  vpc_id      = var.vpc_id

  ingress {
    description     = "NFS from EKS nodes"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [var.node_security_group_id]
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-efs"
  })
}

################################################################################
# EFS StorageClass (conditional)
# EFS CSI driver is deployed as an EKS managed addon in the eks-cluster module
################################################################################

resource "kubernetes_storage_class_v1" "efs" {
  count = var.enable_efs ? 1 : 0

  metadata {
    name = "efs-sc"
    annotations = {
      "storageclass.kubernetes.io/is-default-class" = "true"
    }
  }

  storage_provisioner = "efs.csi.aws.com"
  reclaim_policy      = "Delete"

  parameters = {
    provisioningMode = "efs-ap"
    fileSystemId     = aws_efs_file_system.this[0].id
    directoryPerms   = "700"
  }

  mount_options = ["iam"]

  depends_on = [aws_efs_mount_target.this]
}
