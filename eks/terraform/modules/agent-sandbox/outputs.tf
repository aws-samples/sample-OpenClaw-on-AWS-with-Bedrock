################################################################################
# Agent Sandbox Module - Outputs
################################################################################

output "agent_sandbox_namespace" {
  description = "Kubernetes namespace for the Agent Sandbox system"
  value       = kubernetes_namespace_v1.agent_sandbox.metadata[0].name
}
