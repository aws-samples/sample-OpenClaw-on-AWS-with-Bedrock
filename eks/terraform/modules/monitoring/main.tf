################################################################################
# Monitoring Module - Prometheus + Grafana via kube-prometheus-stack
################################################################################

resource "kubernetes_namespace_v1" "monitoring" {
  metadata {
    name = "monitoring"
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "app.kubernetes.io/part-of"    = var.cluster_name
    }
  }
}

################################################################################
# Grafana admin password
################################################################################

resource "random_password" "grafana_admin" {
  length  = 16
  special = true
}

################################################################################
# kube-prometheus-stack (Prometheus + Operator + exporters)
################################################################################

resource "helm_release" "kube_prometheus_stack" {
  name       = "kube-prometheus-stack"
  repository = "oci://public.ecr.aws/t6v6o5d5/helm"
  chart      = "kube-prometheus-stack"
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  version    = "65.1.0"

  timeout = 600

  values = [
    yamlencode({
      # ----------------------------------------------------------------
      # Global: pull all sub-chart images from public.ecr.aws mirrors
      # so deployments in AWS China or air-gapped VPCs succeed without
      # Docker Hub rate limits or GCR connectivity issues.
      # ----------------------------------------------------------------
      global = {
        imageRegistry = "public.ecr.aws"
      }

      # ----------------------------------------------------------------
      # Prometheus Server
      # ----------------------------------------------------------------
      prometheus = {
        prometheusSpec = {
          image = {
            registry   = "public.ecr.aws"
            repository = "bitnami/prometheus"
            tag        = "2.54.1"
          }
          storageSpec = {
            volumeClaimTemplate = {
              spec = {
                storageClassName = "ebs-sc"
                accessModes      = ["ReadWriteOnce"]
                resources = {
                  requests = {
                    storage = "50Gi"
                  }
                }
              }
            }
          }
          retention = "15d"
          resources = {
            requests = {
              cpu    = "500m"
              memory = "2Gi"
            }
            limits = {
              cpu    = "2000m"
              memory = "4Gi"
            }
          }
          # Enable ServiceMonitor auto-discovery across all namespaces
          serviceMonitorSelectorNilUsesHelmValues = false
          podMonitorSelectorNilUsesHelmValues     = false
          ruleSelectorNilUsesHelmValues           = false
        }
      }

      # ----------------------------------------------------------------
      # Prometheus Operator
      # ----------------------------------------------------------------
      prometheusOperator = {
        image = {
          registry   = "public.ecr.aws"
          repository = "bitnami/prometheus-operator"
          tag        = "0.77.1"
        }
        prometheusConfigReloader = {
          image = {
            registry   = "public.ecr.aws"
            repository = "kubecost/prometheus-config-reloader"
            tag        = "v0.77.1"
          }
        }
        admissionWebhooks = {
          patch = {
            image = {
              registry   = "public.ecr.aws"
              repository = "t6v6o5d5/kube-prometheus"
              tag        = "kube-webhook-certgen-v20221220"
            }
          }
        }
      }

      # ----------------------------------------------------------------
      # kube-state-metrics
      # ----------------------------------------------------------------
      kube-state-metrics = {
        image = {
          registry   = "public.ecr.aws"
          repository = "bitnami/kube-state-metrics"
          tag        = "2.13.0"
        }
      }

      # ----------------------------------------------------------------
      # node-exporter
      # ----------------------------------------------------------------
      prometheus-node-exporter = {
        image = {
          registry   = "public.ecr.aws"
          repository = "bitnami/node-exporter"
          tag        = "1.8.2"
        }
      }

      # ----------------------------------------------------------------
      # Disable Grafana inside the stack -- we deploy it separately below
      # so we can configure datasources, persistence, and sidecar images.
      # ----------------------------------------------------------------
      grafana = {
        enabled = false
      }

      # Alertmanager disabled -- users can enable it via a wrapper variable
      # in a future iteration.
      alertmanager = {
        enabled = false
      }
    })
  ]
}

################################################################################
# Grafana (standalone Helm release)
################################################################################

resource "helm_release" "grafana" {
  name       = "grafana"
  repository = "oci://public.ecr.aws/t6v6o5d5/helm"
  chart      = "grafana"
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name

  timeout = 600

  values = [
    yamlencode({
      image = {
        registry   = "public.ecr.aws"
        repository = "t6v6o5d5/kube-prometheus"
        tag        = "grafana-11.2.1"
      }

      # chown init container is not needed when running as non-root with
      # a PVC that has the correct fsGroup set.
      initChownData = {
        enabled = false
      }

      sidecar = {
        image = {
          registry   = "public.ecr.aws"
          repository = "t6v6o5d5/kube-prometheus"
          tag        = "k8s-sidecar-1.27.4"
        }
        dashboards = {
          enabled = true
        }
        datasources = {
          enabled = true
        }
      }

      adminPassword = random_password.grafana_admin.result

      persistence = {
        enabled          = true
        storageClassName = "ebs-sc"
        size             = "10Gi"
      }

      service = {
        type = "ClusterIP"
      }

      # Pre-configure Prometheus as the default datasource so Grafana is
      # immediately usable after deployment.
      datasources = {
        "datasources.yaml" = {
          apiVersion = 1
          datasources = [{
            name      = "Prometheus"
            type      = "prometheus"
            url       = "http://kube-prometheus-stack-prometheus.${kubernetes_namespace_v1.monitoring.metadata[0].name}:9090"
            isDefault = true
          }]
        }
      }
    })
  ]

  depends_on = [helm_release.kube_prometheus_stack]
}
