################################################################################
# Monitoring Module - Outputs
################################################################################

output "grafana_admin_password" {
  description = "Grafana admin password (username: admin)"
  value       = random_password.grafana_admin.result
  sensitive   = true
}

output "grafana_service_name" {
  description = "Grafana service name in the monitoring namespace"
  value       = helm_release.grafana.name
}

output "prometheus_endpoint" {
  description = "In-cluster Prometheus endpoint URL"
  value       = "http://kube-prometheus-stack-prometheus.${kubernetes_namespace_v1.monitoring.metadata[0].name}:9090"
}
