################################################################################
# LiteLLM Module - Outputs
################################################################################

output "litellm_endpoint" {
  description = "In-cluster LiteLLM endpoint URL"
  value       = "http://litellm.${kubernetes_namespace_v1.litellm.metadata[0].name}:4000"
}

output "litellm_namespace" {
  description = "Kubernetes namespace where LiteLLM is deployed"
  value       = kubernetes_namespace_v1.litellm.metadata[0].name
}
