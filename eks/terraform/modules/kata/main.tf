################################################################################
# Kata Containers + gVisor + Karpenter Bare-Metal Autoscaling
################################################################################

# --- Kata Namespace -----------------------------------------------------------

resource "kubernetes_namespace_v1" "kata" {
  metadata {
    name = var.kata_namespace
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }
}

# --- Kata Deploy (Helm) ------------------------------------------------------

resource "helm_release" "kata_deploy" {
  name       = "kata-deploy"
  repository = var.chart_repository != "" ? var.chart_repository : "oci://ghcr.io/kata-containers/kata-deploy-charts"
  chart      = "kata-deploy"
  version    = var.kata_version
  namespace  = kubernetes_namespace_v1.kata.metadata[0].name

  wait = false

  values = [yamlencode({
    image = {
      reference = var.ecr_host != "" ? "${var.ecr_host}/kata-containers/kata-deploy" : "quay.io/kata-containers/kata-deploy"
      tag       = var.kata_version
    }
    nodeSelector = {
      "workload-type" = "kata"
    }
    tolerations = [{
      key      = "kata"
      operator = "Equal"
      value    = "true"
      effect   = "NoSchedule"
    }]
    shims = {
      disableAll = true
      qemu       = { enabled = contains(["qemu"], var.kata_hypervisor) }
      fc         = { enabled = contains(["fc"], var.kata_hypervisor) }
      clh        = { enabled = contains(["clh"], var.kata_hypervisor) }
    }
    defaultShim = {
      amd64   = var.kata_hypervisor
      arm64   = var.kata_hypervisor
      s390x   = var.kata_hypervisor
      ppc64le = var.kata_hypervisor
    }
  })]

  timeout = 600

  depends_on = [kubernetes_namespace_v1.kata]
}

# --- RuntimeClass -------------------------------------------------------------

resource "kubectl_manifest" "kata_runtime_class" {
  yaml_body = yamlencode({
    apiVersion = "node.k8s.io/v1"
    kind       = "RuntimeClass"
    metadata = {
      name = "kata-${var.kata_hypervisor}"
    }
    handler = "kata-${var.kata_hypervisor}"
    overhead = {
      podFixed = {
        cpu    = var.kata_hypervisor == "fc" ? "250m" : "100m"
        memory = var.kata_hypervisor == "fc" ? "130Mi" : "200Mi"
      }
    }
    scheduling = {
      nodeSelector = {
        "katacontainers.io/kata-runtime" = "true"
      }
      tolerations = [{
        key      = "kata"
        value    = "true"
        effect   = "NoSchedule"
        operator = "Equal"
      }]
    }
  })

  depends_on = [helm_release.kata_deploy]
}

# --- Karpenter Controller (conditional) --------------------------------------

module "karpenter" {
  count   = var.enable_karpenter ? 1 : 0
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "~> 20.24"

  cluster_name = var.cluster_name

  enable_v1_permissions           = true
  enable_pod_identity             = true
  create_pod_identity_association = true

  node_iam_role_additional_policies = {
    AmazonSSMManagedInstanceCore = "arn:${var.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }

  tags = var.tags
}

resource "helm_release" "karpenter" {
  count      = var.enable_karpenter ? 1 : 0
  name       = "karpenter"
  repository = "oci://public.ecr.aws/karpenter"
  chart      = "karpenter"
  version    = var.karpenter_version
  namespace  = "kube-system"
  wait       = false

  values = [yamlencode({
    settings = {
      clusterName       = var.cluster_name
      clusterEndpoint   = var.cluster_endpoint
      interruptionQueue = try(module.karpenter[0].queue_name, "")
    }
    serviceAccount = {
      annotations = {
        "eks.amazonaws.com/role-arn" = try(module.karpenter[0].iam_role_arn, "")
      }
    }
  })]

  depends_on = [module.karpenter]
}

# --- Karpenter EC2NodeClass for Kata Bare-Metal Nodes -------------------------
# Includes userData to configure devmapper snapshotter and containerd for
# Kata Firecracker. Without this, kata-fc pods fail with
# "snapshotter devmapper was not found".

resource "kubectl_manifest" "kata_node_class" {
  count     = var.enable_karpenter ? 1 : 0
  yaml_body = <<-YAML
    apiVersion: karpenter.k8s.aws/v1
    kind: EC2NodeClass
    metadata:
      name: kata-bare-metal
    spec:
      amiSelectorTerms:
        - alias: al2023@latest
      role: ${try(module.karpenter[0].node_iam_role_name, "")}
      subnetSelectorTerms:
        - tags:
            karpenter.sh/discovery: ${var.cluster_name}
      securityGroupSelectorTerms:
        - tags:
            karpenter.sh/discovery: ${var.cluster_name}
      blockDeviceMappings:
        - deviceName: /dev/xvda
          ebs:
            volumeSize: 200Gi
            volumeType: gp3
            encrypted: true
            deleteOnTermination: true
      userData: |
        MIME-Version: 1.0
        Content-Type: multipart/mixed; boundary="BOUNDARY"

        --BOUNDARY
        Content-Type: text/x-shellscript; charset="us-ascii"

        #!/bin/bash
        # Kata Firecracker devmapper setup — runs BEFORE nodeadm starts containerd.
        # Key: configure devmapper + containerd config before first boot to avoid
        # containerd issue #11390 (multi-snapshotter content store race).
        set -ex
        exec > /var/log/kata-devmapper-setup.log 2>&1

        echo "=== Kata Devmapper Setup (pre-containerd) ==="

        # Install required packages (AL2023 uses dnf)
        dnf install -y lvm2 device-mapper bc

        # Create devmapper thin pool using loop devices on EBS
        DATA_DIR=/var/lib/containerd/io.containerd.snapshotter.v1.devmapper
        POOL_NAME=devpool
        mkdir -p $${DATA_DIR}

        truncate -s 100G "$${DATA_DIR}/data"
        truncate -s 10G "$${DATA_DIR}/meta"

        DATA_DEV=$(losetup --find --show "$${DATA_DIR}/data")
        META_DEV=$(losetup --find --show "$${DATA_DIR}/meta")

        SECTOR_SIZE=512
        DATA_SIZE=$(blockdev --getsize64 -q $${DATA_DEV})
        LENGTH_IN_SECTORS=$(bc <<< "$${DATA_SIZE}/$${SECTOR_SIZE}")

        dmsetup create "$${POOL_NAME}" \
            --table "0 $${LENGTH_IN_SECTORS} thin-pool $${META_DEV} $${DATA_DEV} 128 32768"

        echo "Thin pool created:"
        dmsetup ls

        # Create reload script for reboots (runs before containerd)
        cat > /usr/local/bin/reload-devmapper.sh << 'EOFRELOAD'
        #!/bin/bash
        DATA_DIR=/var/lib/containerd/io.containerd.snapshotter.v1.devmapper
        POOL_NAME=devpool
        if dmsetup ls | grep -q $POOL_NAME; then exit 0; fi
        DATA_DEV=$(losetup --find --show "$DATA_DIR/data")
        META_DEV=$(losetup --find --show "$DATA_DIR/meta")
        DATA_SIZE=$(blockdev --getsize64 -q $DATA_DEV)
        LENGTH=$(( DATA_SIZE / 512 ))
        dmsetup create "$POOL_NAME" --table "0 $LENGTH thin-pool $META_DEV $DATA_DEV 128 32768"
        EOFRELOAD
        chmod +x /usr/local/bin/reload-devmapper.sh

        cat > /etc/systemd/system/devmapper-reload.service << 'EOFSVC'
        [Unit]
        Description=Reload devmapper thin pool for Kata Containers
        After=local-fs.target
        Before=containerd.service
        [Service]
        Type=oneshot
        RemainAfterExit=yes
        ExecStart=/usr/local/bin/reload-devmapper.sh
        [Install]
        WantedBy=multi-user.target
        EOFSVC
        systemctl daemon-reload
        systemctl enable devmapper-reload.service

        # Patch EKS default: discard_unpacked_layers must be false for Kata FC.
        # EKS AMI sets it to true by default. Without this fix, images pulled
        # into overlayfs lose their content blobs, causing "content digest not
        # found" when kata-fc tries to unpack into devmapper (containerd#11390).
        # We sed the config BEFORE nodeadm starts containerd.
        sed -i 's/discard_unpacked_layers = true/discard_unpacked_layers = false/' /etc/containerd/config.toml 2>/dev/null || true

        # Append devmapper snapshotter + kata-fc runtime to containerd config.
        # Uses containerd v2 (config version 3) key paths confirmed from
        # `containerd config default` on EKS AL2023 AMI v20260318.
        cat >> /etc/containerd/config.toml << 'EOFCONTAINERD'

        [plugins.'io.containerd.snapshotter.v1.devmapper']
          pool_name = "devpool"
          root_path = "/var/lib/containerd/io.containerd.snapshotter.v1.devmapper"
          base_image_size = "40GB"
          discard_blocks = true

        [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.kata-fc]
          runtime_type = "io.containerd.kata-fc.v2"
          snapshotter = "devmapper"
        EOFCONTAINERD

        echo "=== Kata Devmapper Setup Complete (pre-containerd) ==="

        --BOUNDARY
        Content-Type: application/node.eks.aws

        apiVersion: node.eks.aws/v1alpha1
        kind: NodeConfig
        spec:
          cluster:
            name: ${var.cluster_name}
            apiServerEndpoint: ${var.cluster_endpoint}
            certificateAuthority: ${var.cluster_ca_data}
            cidr: ${var.vpc_cidr}

        --BOUNDARY--
      tags:
        Name: kata-bare-metal-node
        KarpenterNodeClass: kata-bare-metal
  YAML

  depends_on = [helm_release.karpenter]
}

# --- Karpenter NodePool for Kata Bare-Metal Nodes ----------------------------

resource "kubectl_manifest" "kata_node_pool" {
  count = var.enable_karpenter ? 1 : 0
  yaml_body = yamlencode({
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "kata-bare-metal"
    }
    spec = {
      template = {
        metadata = {
          labels = {
            "katacontainers.io/kata-runtime" = "true"
            "workload-type"                  = "kata"
          }
        }
        spec = {
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "kata-bare-metal"
          }
          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = [var.architecture == "arm64" ? "arm64" : "amd64"]
            },
            {
              key      = "node.kubernetes.io/instance-type"
              operator = "In"
              values   = var.kata_instance_types
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["on-demand"]
            },
          ]
          taints = [{
            key    = "kata"
            value  = "true"
            effect = "NoSchedule"
          }]
        }
      }
      limits = {
        cpu    = "1000"
        memory = "1000Gi"
      }
      disruption = {
        consolidationPolicy = "WhenEmptyOrUnderutilized"
        consolidateAfter    = "1m"
      }
    }
  })

  depends_on = [kubectl_manifest.kata_node_class]
}

################################################################################
# gVisor (runsc) + Karpenter Autoscaling
################################################################################

# --- gVisor RuntimeClass ------------------------------------------------------

resource "kubectl_manifest" "gvisor_runtime_class" {
  count = var.enable_gvisor ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "node.k8s.io/v1"
    kind       = "RuntimeClass"
    metadata = {
      name = "gvisor"
    }
    handler = "runsc"
    scheduling = {
      nodeSelector = {
        "runtime" = "gvisor"
      }
      tolerations = [{
        key      = "gvisor"
        value    = "true"
        effect   = "NoSchedule"
        operator = "Equal"
      }]
    }
  })
}

# --- Karpenter EC2NodeClass for gVisor Nodes ----------------------------------
# userData installs runsc + containerd-shim-runsc-v1 and patches containerd
# config to register the runsc runtime. Works on both x86 and arm64.

resource "kubectl_manifest" "gvisor_node_class" {
  count = var.enable_gvisor && var.enable_karpenter ? 1 : 0

  yaml_body = <<-YAML
    apiVersion: karpenter.k8s.aws/v1
    kind: EC2NodeClass
    metadata:
      name: gvisor
    spec:
      amiSelectorTerms:
        - alias: al2023@latest
      role: ${try(module.karpenter[0].node_iam_role_name, var.node_iam_role_name)}
      subnetSelectorTerms:
        - tags:
            karpenter.sh/discovery: ${var.cluster_name}
      securityGroupSelectorTerms:
        - tags:
            karpenter.sh/discovery: ${var.cluster_name}
      tags:
        Name: gvisor-node
        KarpenterNodeClass: gvisor
      userData: |
        MIME-Version: 1.0
        Content-Type: multipart/mixed; boundary="BOUNDARY"

        --BOUNDARY
        Content-Type: text/x-shellscript; charset="us-ascii"

        #!/bin/bash
        # Install gVisor (runsc) for Karpenter-provisioned nodes.
        # Runs via cloud-init AFTER nodeadm has finished.
        # Supports both x86_64 and aarch64 architectures.
        set -eux
        exec > /var/log/gvisor-install.log 2>&1

        echo "=== gVisor Install ==="

        ARCH=$(uname -m)
        GVISOR_URL="https://storage.googleapis.com/gvisor/releases/release/latest/$${ARCH}"

        cd /tmp
        curl -fsSL "$${GVISOR_URL}/runsc" -o runsc
        curl -fsSL "$${GVISOR_URL}/containerd-shim-runsc-v1" -o containerd-shim-runsc-v1
        chmod +x runsc containerd-shim-runsc-v1
        mv runsc /usr/local/bin/
        mv containerd-shim-runsc-v1 /usr/local/bin/

        # Verify installation
        /usr/local/bin/runsc --version

        # Wait for containerd to be running (nodeadm starts it)
        for i in $(seq 1 30); do
          systemctl is-active containerd && break
          sleep 1
        done

        # Patch containerd config to add runsc runtime
        if ! grep -q runsc /etc/containerd/config.toml; then
          cat >> /etc/containerd/config.toml << 'EOFCONTAINERD'

        [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runsc]
          runtime_type = "io.containerd.runsc.v1"
        EOFCONTAINERD
          systemctl restart containerd
          sleep 3
          systemctl restart kubelet
        fi

        echo "=== gVisor Install Complete ==="

        --BOUNDARY
        Content-Type: application/node.eks.aws

        apiVersion: node.eks.aws/v1alpha1
        kind: NodeConfig
        spec:
          cluster:
            name: ${var.cluster_name}
            apiServerEndpoint: ${var.cluster_endpoint}
            certificateAuthority: ${var.cluster_ca_data}
            cidr: ${var.vpc_cidr}

        --BOUNDARY--
  YAML

  depends_on = [helm_release.karpenter]
}

# --- Karpenter NodePool for gVisor Nodes --------------------------------------

resource "kubectl_manifest" "gvisor_node_pool" {
  count = var.enable_gvisor && var.enable_karpenter ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "gvisor"
    }
    spec = {
      template = {
        metadata = {
          labels = {
            "runtime"       = "gvisor"
            "workload-type" = "gvisor"
          }
        }
        spec = {
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "gvisor"
          }
          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = [var.gvisor_architecture == "arm64" ? "arm64" : "amd64"]
            },
            {
              key      = "karpenter.k8s.aws/instance-category"
              operator = "In"
              values   = ["m"]
            },
            {
              key      = "karpenter.k8s.aws/instance-generation"
              operator = "Gt"
              values   = ["6"]
            },
            {
              key      = "karpenter.k8s.aws/instance-size"
              operator = "In"
              values   = var.gvisor_instance_sizes
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = var.gvisor_capacity_types
            },
          ]
          taints = [{
            key    = "gvisor"
            value  = "true"
            effect = "NoSchedule"
          }]
        }
      }
      limits = {
        cpu    = tostring(var.gvisor_limits_cpu)
        memory = "${var.gvisor_limits_memory_gi}Gi"
      }
      disruption = {
        consolidationPolicy = "WhenEmpty"
        consolidateAfter    = "30m"
      }
    }
  })

  depends_on = [kubectl_manifest.gvisor_node_class]
}
