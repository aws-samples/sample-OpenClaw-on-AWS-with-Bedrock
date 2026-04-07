################################################################################
# Storage Module - Outputs
################################################################################

output "ebs_storage_class_name" {
  description = "Name of the EBS gp3 StorageClass"
  value       = kubernetes_storage_class_v1.ebs_gp3.metadata[0].name
}

output "efs_storage_class_name" {
  description = "Name of the EFS StorageClass (null if EFS is disabled)"
  value       = var.enable_efs ? kubernetes_storage_class_v1.efs[0].metadata[0].name : null
}

output "efs_file_system_id" {
  description = "ID of the EFS file system (null if EFS is disabled)"
  value       = var.enable_efs ? aws_efs_file_system.this[0].id : null
}
