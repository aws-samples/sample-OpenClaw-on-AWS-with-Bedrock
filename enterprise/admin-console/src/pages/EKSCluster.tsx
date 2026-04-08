/**
 * EKS components — imported by Settings (cluster/operator) and AgentList (instances).
 * Not a standalone page.
 */
import { useState } from 'react';
import {
  Cloud, Server, RefreshCw, Link2, Unlink, Download, Plus,
  Square, RotateCw, Terminal,
  Loader2, AlertTriangle, Box,
} from 'lucide-react';
import { Card, StatCard, Badge, Button, Table, Modal, Input, Select, Toggle } from '../components/ui';
import {
  useEksCluster, useDiscoverClusters, useAssociateCluster, useDisassociateCluster,
  useEksInstances, useInstallOperator,
  useDeployEksAgent, useStopEksAgent, useReloadEksAgent, useEksAgentLogs,
} from '../hooks/useApi';
import type { EksDeployParams } from '../hooks/useApi';
import type { Agent } from '../types';

// ─── Cluster & Operator (for Settings page) ─────────────────────────────────

export function EksClusterTab() {
  const { data: cluster, isLoading, refetch } = useEksCluster();
  const discover = useDiscoverClusters();
  const associate = useAssociateCluster();
  const disassociate = useDisassociateCluster();
  const installOp = useInstallOperator();
  const [discovered, setDiscovered] = useState<any[]>([]);
  const [showDiscover, setShowDiscover] = useState(false);
  const [error, setError] = useState('');

  const handleDiscover = async () => {
    setError('');
    try {
      const result = await discover.mutateAsync();
      setDiscovered(result.clusters || []);
      setShowDiscover(true);
    } catch (e: any) {
      setError(e?.message || 'Failed to discover clusters');
    }
  };

  const handleAssociate = async (name: string, region: string) => {
    setError('');
    try {
      await associate.mutateAsync({ name, region });
      setShowDiscover(false);
      refetch();
    } catch (e: any) {
      setError(e?.message || 'Failed to associate cluster');
    }
  };

  const handleDisassociate = async () => {
    if (!confirm('Remove cluster association? EKS agents will become unreachable.')) return;
    await disassociate.mutateAsync();
    refetch();
  };

  const handleInstallOperator = async () => {
    setError('');
    try {
      await installOp.mutateAsync({});
      refetch();
    } catch (e: any) {
      setError(e?.message || 'Failed to install operator');
    }
  };

  if (isLoading) {
    return <div className="flex items-center gap-2 text-text-muted py-12 justify-center"><Loader2 size={20} className="animate-spin" /> Loading cluster config...</div>;
  }

  const configured = cluster?.configured;
  const inCluster = cluster?.in_cluster;
  const operator = cluster?.operator;

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          title="Cluster"
          value={configured ? (cluster.cluster_name || 'In-cluster') : 'Not configured'}
          subtitle={configured
            ? (inCluster ? 'Running inside EKS (auto-detected)' : `${cluster.cluster_region} - K8s ${cluster.cluster_version}`)
            : 'Associate an EKS cluster to enable'}
          icon={<Cloud size={20} />}
          color={configured ? 'success' : 'warning'}
        />
        <StatCard
          title="Operator"
          value={operator?.installed ? `v${operator.version}` : 'Not installed'}
          subtitle={operator?.installed ? `${operator.pods?.length || 0} pod(s) running` : configured ? 'Install to deploy agents' : 'Associate cluster first'}
          icon={<Box size={20} />}
          color={operator?.installed ? 'success' : configured ? 'warning' : 'info'}
        />
        <StatCard
          title="CRD"
          value={operator?.crd_exists ? 'Registered' : 'Missing'}
          subtitle={operator?.crd_exists ? 'OpenClawInstance v1alpha1' : 'Installed with operator'}
          icon={<Server size={20} />}
          color={operator?.crd_exists ? 'success' : 'info'}
        />
      </div>

      {/* Current cluster config */}
      {configured ? (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-semibold text-text-primary">Associated Cluster</h3>
              <p className="text-sm text-text-muted mt-0.5">Connected to EKS cluster for agent deployment</p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => refetch()}>
                <RefreshCw size={14} /> Refresh
              </Button>
              <Button size="sm" variant="danger" onClick={handleDisassociate}>
                <Unlink size={14} /> Disassociate
              </Button>
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-text-muted text-xs">Cluster Name</p>
              <p className="text-text-primary font-medium mt-0.5">{cluster.cluster_name}</p>
            </div>
            <div>
              <p className="text-text-muted text-xs">Region</p>
              <p className="text-text-primary font-medium mt-0.5">{cluster.cluster_region}</p>
            </div>
            <div>
              <p className="text-text-muted text-xs">Kubernetes Version</p>
              <p className="text-text-primary font-medium mt-0.5">{cluster.cluster_version}</p>
            </div>
            <div>
              <p className="text-text-muted text-xs">Endpoint</p>
              <p className="text-text-primary font-mono text-xs mt-0.5 truncate">{cluster.cluster_endpoint}</p>
            </div>
          </div>

          {/* Operator section */}
          {!operator?.installed && (
            <div className="mt-5 p-4 rounded-xl bg-warning/5 border border-warning/20">
              <div className="flex items-center gap-3">
                <AlertTriangle size={18} className="text-warning" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-text-primary">OpenClaw Operator not installed</p>
                  <p className="text-xs text-text-muted mt-0.5">Install the operator to enable agent deployment on this cluster.</p>
                </div>
                <Button size="sm" variant="primary" onClick={handleInstallOperator} disabled={installOp.isPending}>
                  {installOp.isPending ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                  Install Operator
                </Button>
              </div>
            </div>
          )}
        </Card>
      ) : (
        <Card>
          <div className="text-center py-8">
            <Cloud size={48} className="mx-auto text-text-muted mb-4" />
            <h3 className="text-lg font-semibold text-text-primary">No EKS Cluster Associated</h3>
            <p className="text-sm text-text-muted mt-1 max-w-md mx-auto">
              Discover and associate an EKS cluster to deploy OpenClaw agents on Kubernetes.
            </p>
            <Button variant="primary" className="mt-5" onClick={handleDiscover} disabled={discover.isPending}>
              {discover.isPending ? <Loader2 size={16} className="animate-spin" /> : <Cloud size={16} />}
              Discover EKS Clusters
            </Button>
          </div>
        </Card>
      )}

      {error && (
        <div className="p-3 rounded-xl bg-danger/10 border border-danger/20 text-sm text-danger">{error}</div>
      )}

      {/* Discover modal */}
      <Modal open={showDiscover} onClose={() => setShowDiscover(false)} title="Discover EKS Clusters" size="lg">
        {discovered.length === 0 ? (
          <p className="text-sm text-text-muted py-4 text-center">No EKS clusters found in this region.</p>
        ) : (
          <Table
            columns={[
              { key: 'name', label: 'Cluster Name', render: (c: any) => <span className="font-medium">{c.name}</span> },
              { key: 'status', label: 'Status', render: (c: any) => (
                <Badge color={c.status === 'ACTIVE' ? 'success' : c.status === 'CREATING' ? 'warning' : 'danger'} dot>
                  {c.status}
                </Badge>
              )},
              { key: 'version', label: 'K8s', render: (c: any) => c.version },
              { key: 'region', label: 'Region', render: (c: any) => c.region },
              { key: 'action', label: '', render: (c: any) => (
                <Button size="sm" variant="primary"
                  onClick={() => handleAssociate(c.name, c.region)}
                  disabled={c.status !== 'ACTIVE' || associate.isPending}>
                  {associate.isPending ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                  Associate
                </Button>
              )},
            ]}
            data={discovered}
          />
        )}
      </Modal>
    </div>
  );
}

// ─── Instances List (for Agent Factory page) ─────────────────────────────────

export function EksInstancesTab({ agents }: { agents?: Agent[] }) {
  const { data: cluster } = useEksCluster();
  const { data: instancesData, isLoading, refetch } = useEksInstances();
  const deployAgent = useDeployEksAgent();
  const stopAgent = useStopEksAgent();
  const reloadAgent = useReloadEksAgent();
  const [logsAgent, setLogsAgent] = useState('');
  const [showDeploy, setShowDeploy] = useState(false);

  const instances = instancesData?.instances || [];
  const configured = cluster?.configured;
  const operatorReady = cluster?.operator?.installed;
  const deployedNames = new Set(instances.map((i: any) => i.name));

  if (!configured) {
    return (
      <div className="text-center py-8 text-text-muted">
        <Server size={40} className="mx-auto mb-3 opacity-50" />
        <p className="text-sm">Associate an EKS cluster in Settings → EKS first.</p>
      </div>
    );
  }

  if (!operatorReady) {
    return (
      <div className="text-center py-8 text-text-muted">
        <Box size={40} className="mx-auto mb-3 opacity-50" />
        <p className="text-sm">Install the OpenClaw Operator in Settings → EKS first.</p>
      </div>
    );
  }

  const phaseColor = (phase: string) => {
    if (phase === 'Running') return 'success' as const;
    if (phase === 'Pending' || phase === 'Unknown') return 'warning' as const;
    return 'danger' as const;
  };

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm text-text-muted">
          {instances.length} instance{instances.length !== 1 ? 's' : ''} in namespace <code className="text-xs bg-dark-hover px-1.5 py-0.5 rounded">{instancesData?.namespace}</code>
        </p>
        <div className="flex gap-2">
          <Button size="sm" variant="primary" onClick={() => setShowDeploy(true)}>
            <Plus size={14} /> Deploy Agent
          </Button>
          <Button size="sm" onClick={() => refetch()}>
            <RefreshCw size={14} /> Refresh
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8"><Loader2 size={20} className="animate-spin text-text-muted" /></div>
      ) : (
        <Table
          columns={[
            { key: 'name', label: 'Instance', render: (i: any) => (
              <div>
                <span className="font-medium text-text-primary">{i.name}</span>
                <span className="text-xs text-text-muted ml-2">{i.employee}</span>
              </div>
            )},
            { key: 'phase', label: 'Status', render: (i: any) => (
              <Badge color={phaseColor(i.phase)} dot>{i.phase}</Badge>
            )},
            { key: 'model', label: 'Model', render: (i: any) => (
              <span className="text-xs font-mono text-text-secondary">{i.model || '-'}</span>
            )},
            { key: 'position', label: 'Position', render: (i: any) => i.position },
            { key: 'created', label: 'Created', render: (i: any) => (
              <span className="text-xs text-text-muted">{i.created ? new Date(i.created).toLocaleDateString() : '-'}</span>
            )},
            { key: 'actions', label: 'Actions', render: (i: any) => (
              <div className="flex gap-1.5">
                <button onClick={() => reloadAgent.mutate({ agentId: i.name })} title="Reload"
                  className="p-1.5 rounded-lg hover:bg-dark-hover text-text-muted hover:text-primary transition-colors">
                  <RotateCw size={14} />
                </button>
                <button onClick={() => setLogsAgent(i.name)} title="Logs"
                  className="p-1.5 rounded-lg hover:bg-dark-hover text-text-muted hover:text-info transition-colors">
                  <Terminal size={14} />
                </button>
                <button onClick={() => { if (confirm(`Stop ${i.name}?`)) stopAgent.mutate(i.name); }} title="Stop"
                  className="p-1.5 rounded-lg hover:bg-dark-hover text-text-muted hover:text-danger transition-colors">
                  <Square size={14} />
                </button>
              </div>
            )},
          ]}
          data={instances}
          emptyText="No OpenClaw instances deployed. Create an agent with EKS deploy mode to get started."
        />
      )}

      {/* Logs modal */}
      {logsAgent && <LogsModal agentId={logsAgent} onClose={() => setLogsAgent('')} />}

      {/* Deploy modal */}
      {showDeploy && (
        <DeployEksModal
          agents={(agents || []).filter(a => !deployedNames.has(a.id))}
          onDeploy={async (params) => {
            await deployAgent.mutateAsync(params);
            setShowDeploy(false);
            refetch();
          }}
          isPending={deployAgent.isPending}
          error={deployAgent.error?.message}
          onClose={() => setShowDeploy(false)}
        />
      )}
    </div>
  );
}

// ─── Deploy Modal ───────────────────────────────────────────────────────────

const DEFAULT_MODELS = [
  { value: 'bedrock/us.amazon.nova-2-lite-v1:0', label: 'Nova 2 Lite' },
  { value: 'bedrock/global.anthropic.claude-sonnet-4-5-20250929-v1:0', label: 'Claude Sonnet 4.5' },
  { value: 'bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0', label: 'Claude Haiku 4.5' },
  { value: 'bedrock/us.amazon.nova-pro-v1:0', label: 'Nova Pro' },
];

const SERVICE_TYPES = [
  { value: '', label: 'ClusterIP (default)' },
  { value: 'LoadBalancer', label: 'LoadBalancer' },
  { value: 'NodePort', label: 'NodePort' },
];

function DeployEksModal({ agents, onDeploy, isPending, error, onClose }: {
  agents: Agent[];
  onDeploy: (params: EksDeployParams) => Promise<void>;
  isPending: boolean;
  error?: string;
  onClose: () => void;
}) {
  const [agentId, setAgentId] = useState('');
  const [model, setModel] = useState(DEFAULT_MODELS[0].value);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Image overrides
  const [imageOverride, setImageOverride] = useState('');
  const [globalRegistry, setGlobalRegistry] = useState('');

  // Resources
  const [cpuRequest, setCpuRequest] = useState('500m');
  const [cpuLimit, setCpuLimit] = useState('2');
  const [memoryRequest, setMemoryRequest] = useState('2Gi');
  const [memoryLimit, setMemoryLimit] = useState('4Gi');

  // Storage
  const [storageClass, setStorageClass] = useState('');
  const [storageSize, setStorageSize] = useState('10Gi');

  // Sidecars & features
  const [chromium, setChromium] = useState(false);

  // Security & isolation
  const [runtimeClass, setRuntimeClass] = useState('');
  const [nodeSelectorStr, setNodeSelectorStr] = useState('');
  const [tolerationsStr, setTolerationsStr] = useState('');

  // Networking
  const [serviceType, setServiceType] = useState('');

  // Backup
  const [backupSchedule, setBackupSchedule] = useState('');

  const handleDeploy = async () => {
    if (!agentId) return;
    const params: EksDeployParams = { agentId, model };

    // Image overrides
    if (imageOverride) params.image = imageOverride;
    if (globalRegistry) params.globalRegistry = globalRegistry;

    // Only send non-default values
    if (cpuRequest !== '500m') params.cpuRequest = cpuRequest;
    if (cpuLimit !== '2') params.cpuLimit = cpuLimit;
    if (memoryRequest !== '2Gi') params.memoryRequest = memoryRequest;
    if (memoryLimit !== '4Gi') params.memoryLimit = memoryLimit;
    if (storageClass) params.storageClass = storageClass;
    if (storageSize !== '10Gi') params.storageSize = storageSize;
    if (chromium) params.chromium = true;
    if (runtimeClass) params.runtimeClass = runtimeClass;
    if (serviceType) params.serviceType = serviceType;
    if (backupSchedule) params.backupSchedule = backupSchedule;

    // Parse nodeSelector JSON
    if (nodeSelectorStr.trim()) {
      try { params.nodeSelector = JSON.parse(nodeSelectorStr); }
      catch { /* ignore invalid JSON */ }
    }
    // Parse tolerations JSON
    if (tolerationsStr.trim()) {
      try { params.tolerations = JSON.parse(tolerationsStr); }
      catch { /* ignore invalid JSON */ }
    }

    await onDeploy(params);
  };

  return (
    <Modal open={true} onClose={onClose} title="Deploy Agent to EKS" size="lg" footer={
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={handleDeploy} disabled={!agentId || isPending}>
          {isPending ? <Loader2 size={14} className="animate-spin" /> : <Server size={14} />}
          Deploy
        </Button>
      </div>
    }>
      <div className="space-y-4">
        {/* Agent selection */}
        <Select
          label="Agent"
          value={agentId}
          onChange={setAgentId}
          options={[
            { value: '', label: 'Select an agent...' },
            ...agents.map(a => ({ value: a.id, label: `${a.name} (${a.positionName || a.positionId})` })),
          ]}
          description="Only agents not already deployed to EKS are shown"
        />

        {/* Model */}
        <Select
          label="Bedrock Model"
          value={model}
          onChange={setModel}
          options={DEFAULT_MODELS}
        />

        {/* Image overrides (important for China) */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Input
            label="Container Image"
            value={imageOverride}
            onChange={setImageOverride}
            placeholder="default: ghcr.io/openclaw/openclaw:latest"
            description="Main OpenClaw container image (ECR URI for custom builds)"
          />
          <Input
            label="Global Registry"
            value={globalRegistry}
            onChange={setGlobalRegistry}
            placeholder="e.g. 834204282212.dkr.ecr.cn-northwest-1.amazonaws.com.cn"
            description="Rewrites registry for ALL images (required for China regions)"
          />
        </div>

        {/* Resource presets */}
        <div>
          <p className="text-sm font-medium text-text-primary mb-2">Compute Resources</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Input label="CPU Request" value={cpuRequest} onChange={setCpuRequest} placeholder="500m" />
            <Input label="CPU Limit" value={cpuLimit} onChange={setCpuLimit} placeholder="2" />
            <Input label="Memory Request" value={memoryRequest} onChange={setMemoryRequest} placeholder="2Gi" />
            <Input label="Memory Limit" value={memoryLimit} onChange={setMemoryLimit} placeholder="4Gi" />
          </div>
        </div>

        {/* Storage */}
        <div className="grid grid-cols-2 gap-3">
          <Input label="Storage Class" value={storageClass} onChange={setStorageClass} placeholder="cluster default (efs-sc)" />
          <Input label="Storage Size" value={storageSize} onChange={setStorageSize} placeholder="10Gi" />
        </div>

        {/* Chromium toggle */}
        <Toggle
          label="Chromium Browser Sidecar"
          checked={chromium}
          onChange={setChromium}
          description="Enable headless Chromium for browser automation and web scraping"
        />

        {/* Advanced section */}
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="text-xs text-primary hover:underline"
        >
          {showAdvanced ? 'Hide' : 'Show'} advanced options
        </button>

        {showAdvanced && (
          <div className="space-y-4 border-t border-dark-border pt-4">
            {/* Runtime class (Kata) */}
            <Input
              label="Runtime Class"
              value={runtimeClass}
              onChange={setRuntimeClass}
              placeholder="e.g. kata-qemu for Firecracker isolation"
              description="Use kata-qemu for hardware-level VM isolation (requires Kata Containers on nodes)"
            />

            {/* Networking */}
            <Select
              label="Service Type"
              value={serviceType}
              onChange={setServiceType}
              options={SERVICE_TYPES}
              description="How the agent's K8s Service is exposed"
            />

            {/* Backup */}
            <Input
              label="Backup Schedule"
              value={backupSchedule}
              onChange={setBackupSchedule}
              placeholder='e.g. 0 2 * * * (daily at 2 AM)'
              description="Cron schedule for S3 workspace backups (requires s3-backup-credentials Secret)"
            />

            {/* Node selector */}
            <Input
              label="Node Selector (JSON)"
              value={nodeSelectorStr}
              onChange={setNodeSelectorStr}
              placeholder='{"katacontainers.io/kata-runtime": "true"}'
              description="K8s nodeSelector labels for pod scheduling"
            />

            {/* Tolerations */}
            <Input
              label="Tolerations (JSON)"
              value={tolerationsStr}
              onChange={setTolerationsStr}
              placeholder='[{"key": "kata", "value": "true", "effect": "NoSchedule"}]'
              description="K8s tolerations for tainted nodes (GPU, Kata, spot instances)"
            />
          </div>
        )}

        {error && (
          <div className="p-3 rounded-xl bg-danger/10 border border-danger/20 text-sm text-danger">{error}</div>
        )}
      </div>
    </Modal>
  );
}

function LogsModal({ agentId, onClose }: { agentId: string; onClose: () => void }) {
  const { data, isLoading, refetch } = useEksAgentLogs(agentId);

  return (
    <Modal open={true} onClose={onClose} title={`Logs: ${agentId}`} size="lg">
      <div className="flex justify-end mb-2">
        <Button size="sm" onClick={() => refetch()}>
          <RefreshCw size={14} /> Refresh
        </Button>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-8"><Loader2 size={20} className="animate-spin text-text-muted" /></div>
      ) : data?.logs ? (
        <pre className="bg-dark-bg rounded-xl p-4 text-xs text-text-secondary font-mono max-h-96 overflow-auto whitespace-pre-wrap">
          {data.logs}
        </pre>
      ) : (
        <p className="text-sm text-text-muted text-center py-4">No logs available.</p>
      )}
    </Modal>
  );
}
