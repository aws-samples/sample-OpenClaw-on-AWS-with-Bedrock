# --- Core ---
variable "name" {
  description = "Name for the VPC and EKS cluster"
  type        = string
  default     = "openclaw-eks"
}

variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-west-2"
}

variable "is_china_region" {
  description = "Whether deploying to AWS China region. Auto-detected from region if not set."
  type        = bool
  default     = null
}

variable "eks_cluster_version" {
  description = "EKS Kubernetes version"
  type        = string
  default     = "1.35"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.1.0.0/16"
}

# --- Architecture ---
variable "architecture" {
  description = "CPU architecture: x86 or arm64 (Graviton)"
  type        = string
  default     = "arm64"
  validation {
    condition     = contains(["x86", "arm64"], var.architecture)
    error_message = "Architecture must be x86 or arm64."
  }
}

variable "core_instance_types" {
  description = "Instance types for the core node group. Empty list uses architecture defaults."
  type        = list(string)
  default     = []
}

variable "core_node_count" {
  description = "Core node group sizing"
  type = object({
    min     = number
    max     = number
    desired = number
  })
  default = {
    min     = 2
    max     = 5
    desired = 2
  }
}

# --- Kata Containers ---
variable "enable_kata" {
  description = "Enable Kata Containers for VM-level isolation"
  type        = bool
  default     = false
}

variable "kata_hypervisor" {
  description = "Kata hypervisor type"
  type        = string
  default     = "fc"
  validation {
    condition     = contains(["qemu", "fc", "clh"], var.kata_hypervisor)
    error_message = "Hypervisor must be qemu, fc, or clh."
  }
}

variable "kata_instance_types" {
  description = "Bare metal instance types for Kata workloads. Null uses architecture defaults."
  type        = list(string)
  default     = null
}

variable "enable_karpenter" {
  description = "Enable Karpenter for bare-metal node autoscaling (only with Kata)"
  type        = bool
  default     = false
}

# --- Networking ---
variable "enable_alb_controller" {
  description = "Enable AWS Load Balancer Controller for ALB Ingress"
  type        = bool
  default     = true
}

variable "enable_cloudfront" {
  description = "Enable CloudFront CDN distribution"
  type        = bool
  default     = false
}

# --- Storage ---
variable "enable_efs" {
  description = "Enable EFS file system with CSI driver (recommended for OpenClaw workspace persistence)"
  type        = bool
  default     = true
}

# --- AI Proxy ---
variable "enable_litellm" {
  description = "Enable LiteLLM proxy for multi-model AI gateway"
  type        = bool
  default     = false
}

# --- Monitoring ---
variable "enable_monitoring" {
  description = "Enable Prometheus + Grafana monitoring stack"
  type        = bool
  default     = false
}

# --- Agent Sandbox ---
variable "enable_agent_sandbox" {
  description = "Enable Agent Sandbox CRDs"
  type        = bool
  default     = false
}

# --- Access ---
variable "access_entries" {
  description = "Map of access entries for the EKS cluster"
  type        = any
  default     = {}
}

# kms_key_admin_roles removed — KMS encryption disabled for workshop
