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

# --- Chart Repository ---------------------------------------------------------

variable "chart_repository" {
  description = "Override Helm chart OCI repository for kata-deploy (e.g. oci://ECR_HOST/charts for China). Empty = default ghcr.io."
  type        = string
  default     = ""
}

variable "ecr_host" {
  description = "Private ECR host for China image mirrors. Empty = use upstream."
  type        = string
  default     = ""
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

# --- gVisor -------------------------------------------------------------------

variable "enable_gvisor" {
  description = "Whether to deploy gVisor (runsc) RuntimeClass and Karpenter NodePool"
  type        = bool
  default     = false
}

variable "gvisor_architecture" {
  description = "CPU architecture for gVisor nodes (amd64 or arm64)"
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["amd64", "arm64"], var.gvisor_architecture)
    error_message = "gvisor_architecture must be one of: amd64, arm64."
  }
}

variable "gvisor_instance_sizes" {
  description = "EC2 instance sizes eligible for gVisor nodes"
  type        = list(string)
  default     = ["2xlarge"]
}

variable "gvisor_capacity_types" {
  description = "Karpenter capacity types for gVisor nodes (on-demand, spot)"
  type        = list(string)
  default     = ["on-demand"]
}

variable "gvisor_limits_cpu" {
  description = "Maximum total vCPUs for gVisor NodePool"
  type        = number
  default     = 32
}

variable "gvisor_limits_memory_gi" {
  description = "Maximum total memory (GiB) for gVisor NodePool"
  type        = number
  default     = 128
}
