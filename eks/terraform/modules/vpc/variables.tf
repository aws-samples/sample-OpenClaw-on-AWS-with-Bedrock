################################################################################
# VPC Module - Variables
################################################################################

variable "name" {
  description = "Name prefix for VPC and related resources"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}

variable "azs" {
  description = "List of availability zones to deploy subnets into"
  type        = list(string)
}

variable "enable_alb_controller" {
  description = "Whether to tag public subnets for ALB controller discovery"
  type        = bool
  default     = false
}

variable "enable_karpenter" {
  description = "Whether to tag private subnets for Karpenter discovery"
  type        = bool
  default     = false
}

variable "cluster_name" {
  description = "Name of the EKS cluster, used for Karpenter discovery tags"
  type        = string
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
