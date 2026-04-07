################################################################################
# Kata Module Variables
################################################################################

# --- Cluster ------------------------------------------------------------------

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "cluster_endpoint" {
  description = "Endpoint URL of the EKS cluster API server"
  type        = string
}

variable "cluster_ca_data" {
  description = "Base64-encoded certificate authority data for the EKS cluster"
  type        = string
}

# --- Kata Containers ----------------------------------------------------------

variable "kata_namespace" {
  description = "Kubernetes namespace for Kata Containers components"
  type        = string
  default     = "kata-system"
}

variable "kata_hypervisor" {
  description = "Kata Containers hypervisor backend (qemu, clh, or fc)"
  type        = string

  validation {
    condition     = contains(["qemu", "clh", "fc"], var.kata_hypervisor)
    error_message = "kata_hypervisor must be one of: qemu, clh, fc."
  }
}

variable "kata_version" {
  description = "Version of the Kata Containers Helm chart to deploy"
  type        = string
  default     = "3.27.0"
}

variable "kata_instance_types" {
  description = "EC2 bare-metal instance types eligible for Kata workloads"
  type        = list(string)
}

variable "architecture" {
  description = "CPU architecture for Kata nodes (amd64 or arm64)"
  type        = string

  validation {
    condition     = contains(["amd64", "arm64"], var.architecture)
    error_message = "architecture must be one of: amd64, arm64."
  }
}

# --- Karpenter ----------------------------------------------------------------

variable "enable_karpenter" {
  description = "Whether to deploy Karpenter for bare-metal node autoscaling"
  type        = bool
}

variable "karpenter_version" {
  description = "Version of the Karpenter Helm chart to deploy"
  type        = string
  default     = "1.7.4"
}

# --- IAM / Networking ---------------------------------------------------------

variable "node_iam_role_name" {
  description = "Name of the IAM role attached to Karpenter-managed nodes"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC (used in node bootstrap configuration)"
  type        = string
}

# --- Region / Partition -------------------------------------------------------

variable "is_china_region" {
  description = "Set to true when deploying to an AWS China region (affects container image registry)"
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
