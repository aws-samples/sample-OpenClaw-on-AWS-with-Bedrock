################################################################################
# Variables for OpenClaw Operator Module
################################################################################

variable "cluster_name" {
  description = "Name of the EKS cluster where the operator will be deployed"
  type        = string
}

variable "operator_namespace" {
  description = "Kubernetes namespace for the OpenClaw Operator"
  type        = string
  default     = "openclaw-operator-system"
}

variable "operator_version" {
  description = "Version of the OpenClaw Operator Helm chart to deploy"
  type        = string
  default     = "0.22.2"
}

variable "is_china_region" {
  description = "Whether the deployment targets an AWS China region; when true, uses ECR mirror images"
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags to apply to all resources created by this module"
  type        = map(string)
  default     = {}
}
