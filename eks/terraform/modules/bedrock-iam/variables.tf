################################################################################
# Variables for Bedrock IAM Module
################################################################################

variable "name" {
  description = "Base name used for resource naming (e.g., cluster or project name)"
  type        = string
}

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "cluster_oidc_issuer" {
  description = "OIDC issuer URL of the EKS cluster (without https:// prefix)"
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the OIDC provider for the EKS cluster"
  type        = string
}

variable "openclaw_namespace" {
  description = "Kubernetes namespace for OpenClaw workloads"
  type        = string
  default     = "openclaw"
}

variable "is_china_region" {
  description = "Whether the deployment targets an AWS China region (cn-north-1 or cn-northwest-1)"
  type        = bool
  default     = false
}

variable "partition" {
  description = "AWS partition (aws, aws-cn, or aws-us-gov)"
  type        = string
  default     = "aws"
}

variable "tags" {
  description = "Tags to apply to all resources created by this module"
  type        = map(string)
  default     = {}
}
