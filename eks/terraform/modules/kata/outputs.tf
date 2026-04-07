################################################################################
# Kata Module Outputs
################################################################################

output "kata_namespace" {
  description = "Name of the Kubernetes namespace where Kata Containers components are deployed"
  value       = kubernetes_namespace_v1.kata.metadata[0].name
}

output "kata_runtime_class_name" {
  description = "Name of the RuntimeClass resource created for the selected Kata hypervisor"
  value       = "kata-${var.kata_hypervisor}"
}

output "karpenter_node_pool_name" {
  description = "Name of the Karpenter NodePool for bare-metal Kata nodes (null when Karpenter is disabled)"
  value       = var.enable_karpenter ? "kata-bare-metal" : null
}
