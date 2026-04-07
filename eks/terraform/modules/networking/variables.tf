################################################################################
# Networking Module Variables
################################################################################

# --- Cluster ------------------------------------------------------------------

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "cluster_version" {
  description = "Kubernetes version of the EKS cluster"
  type        = string
}

# --- OIDC / IAM ---------------------------------------------------------------

variable "oidc_provider_arn" {
  description = "ARN of the OIDC provider associated with the EKS cluster"
  type        = string
}

variable "cluster_oidc_issuer" {
  description = "OIDC issuer URL for the EKS cluster (without the https:// prefix)"
  type        = string
}

# --- VPC ----------------------------------------------------------------------

variable "vpc_id" {
  description = "ID of the VPC where the ALB controller will provision load balancers"
  type        = string
}

# --- CloudFront ---------------------------------------------------------------

variable "enable_cloudfront" {
  description = "Whether to create a CloudFront distribution in front of the ALB (placeholder, not yet implemented)"
  type        = bool
  default     = false
}

# --- Region / Partition -------------------------------------------------------

variable "is_china_region" {
  description = "Set to true when deploying to an AWS China region"
  type        = bool
}

variable "partition" {
  description = "AWS partition identifier (aws, aws-cn, or aws-us-gov)"
  type        = string
}

# --- Tags ---------------------------------------------------------------------

variable "tags" {
  description = "Tags to apply to all resources created by this module"
  type        = map(string)
  default     = {}
}
