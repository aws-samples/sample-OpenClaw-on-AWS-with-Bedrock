################################################################################
# Outputs for Bedrock IAM Module
################################################################################

output "bedrock_role_arn" {
  description = "ARN of the IAM role for OpenClaw Bedrock access (used by IRSA)"
  value       = module.openclaw_bedrock_irsa.iam_role_arn
}

output "service_account_name" {
  description = "Name of the Kubernetes ServiceAccount annotated with the Bedrock IAM role"
  value       = kubernetes_service_account_v1.openclaw_sandbox.metadata[0].name
}

output "openclaw_namespace" {
  description = "Name of the Kubernetes namespace created for OpenClaw workloads"
  value       = kubernetes_namespace_v1.openclaw.metadata[0].name
}
