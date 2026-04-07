################################################################################
# Outputs for OpenClaw Operator Module
################################################################################

output "operator_namespace" {
  description = "Kubernetes namespace where the OpenClaw Operator is deployed"
  value       = kubernetes_namespace_v1.operator.metadata[0].name
}

output "operator_release_name" {
  description = "Name of the Helm release for the OpenClaw Operator"
  value       = helm_release.openclaw_operator.name
}
