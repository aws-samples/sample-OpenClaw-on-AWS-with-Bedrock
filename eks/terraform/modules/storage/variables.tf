################################################################################
# Storage Module - Variables
################################################################################

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "private_subnets" {
  description = "List of private subnet IDs for EFS mount targets"
  type        = list(string)
}

variable "node_security_group_id" {
  description = "Security group ID of the EKS nodes, used to allow NFS traffic to EFS"
  type        = string
}

variable "enable_efs" {
  description = "Whether to create EFS resources (file system, mount targets, CSI driver)"
  type        = bool
  default     = false
}

variable "is_china_region" {
  description = "Whether the deployment is in an AWS China region"
  type        = bool
  default     = false
}

variable "partition" {
  description = "AWS partition (aws, aws-cn, aws-us-gov)"
  type        = string
  default     = "aws"
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
