################################################################################
# Networking Module Outputs
################################################################################

output "alb_controller_role_arn" {
  description = "IAM role ARN used by the AWS Load Balancer Controller service account"
  value       = module.alb_controller_irsa.iam_role_arn
}
