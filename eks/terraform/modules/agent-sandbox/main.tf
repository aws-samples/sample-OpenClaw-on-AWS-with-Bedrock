################################################################################
# Agent Sandbox Module
#
# Creates the agent-sandbox-system namespace for the Agent Sandbox controller
# and CRDs. The actual CRD manifests (manifest.yaml, extensions.yaml) are
# large (~800+ lines each) and should be applied after the namespace exists.
#
# To apply the CRD manifests after terraform apply:
#
#   kubectl apply -f manifests/agent-sandbox/manifest.yaml
#   kubectl apply -f manifests/agent-sandbox/extensions.yaml
#
# If the gauntlet/kubectl provider is available in your Terraform workspace
# and you have the manifest files locally, you can uncomment the
# kubectl_manifest resources below to manage them declaratively.
################################################################################

resource "kubernetes_namespace_v1" "agent_sandbox" {
  metadata {
    name = "agent-sandbox-system"
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "app.kubernetes.io/part-of"    = var.cluster_name
    }
  }
}

# ============================================================================
# CRD manifests (commented out -- uncomment if manifest files are available)
# ============================================================================
#
# The Agent Sandbox CRDs and controller are defined in two manifest files.
# When the files are present at the paths below, uncomment these resources
# to have Terraform manage the full lifecycle.
#
# resource "kubectl_manifest" "agent_sandbox_core" {
#   for_each  = toset(split("---", file("${path.module}/../../manifests/agent-sandbox/manifest.yaml")))
#   yaml_body = each.value
#
#   depends_on = [kubernetes_namespace_v1.agent_sandbox]
# }
#
# resource "kubectl_manifest" "agent_sandbox_extensions" {
#   for_each  = toset(split("---", file("${path.module}/../../manifests/agent-sandbox/extensions.yaml")))
#   yaml_body = each.value
#
#   depends_on = [kubernetes_namespace_v1.agent_sandbox]
# }
