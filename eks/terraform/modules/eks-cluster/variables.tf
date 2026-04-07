################################################################################
# EKS Cluster Module - Variables
################################################################################

variable "name" {
  description = "Name of the EKS cluster and related resources"
  type        = string
}

variable "cluster_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC where the cluster will be deployed"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for the EKS cluster and node groups"
  type        = list(string)
}

variable "ami_type" {
  description = "AMI type for the managed node group (e.g. AL2023_ARM_64_STANDARD, AL2023_x86_64_STANDARD)"
  type        = string
}

variable "core_instance_types" {
  description = "List of EC2 instance types for the core node group"
  type        = list(string)
}

variable "core_node_count" {
  description = "Node count configuration for the core node group"
  type = object({
    min     = number
    max     = number
    desired = number
  })
}

variable "access_entries" {
  description = "Map of access entries for the EKS cluster"
  type        = any
  default     = {}
}

variable "kms_key_admin_roles" {
  description = "List of ARNs to be added as KMS key administrators"
  type        = list(string)
  default     = []
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
