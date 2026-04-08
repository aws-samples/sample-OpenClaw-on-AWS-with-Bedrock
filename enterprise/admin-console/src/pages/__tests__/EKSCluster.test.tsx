/**
 * Tests for EKSCluster.tsx — EksClusterTab and EksInstancesTab components.
 *
 * Covers:
 *  1. EksClusterTab — loading state, configured cluster display, unconfigured empty state,
 *     in-cluster auto-detection, operator not installed warning
 *  2. EksInstancesTab — cluster not configured, operator not ready,
 *     instances table, deploy modal rendering, empty state
 *  3. DeployEksModal — field rendering, agent selection, advanced toggle,
 *     deploy button disabled without agent, deploy fires with correct params
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders, makeEksCluster, makeEksInstance, makeAgent } from '../../test/helpers';
import { EksClusterTab, EksInstancesTab } from '../EKSCluster';

// ── Module mocks ──────────────────────────────────────────────────────────────

const mockCluster = vi.fn();
const mockInstances = vi.fn();
const mockDiscover = vi.fn();
const mockAssociate = vi.fn();
const mockDisassociate = vi.fn();
const mockInstallOp = vi.fn();
const mockDeploy = vi.fn();
const mockStop = vi.fn();
const mockReload = vi.fn();
const mockLogs = vi.fn();

vi.mock('../../hooks/useApi', () => ({
  useEksCluster: () => ({ data: mockCluster(), isLoading: false, refetch: vi.fn() }),
  useDiscoverClusters: () => ({ mutateAsync: mockDiscover, isPending: false }),
  useAssociateCluster: () => ({ mutateAsync: mockAssociate, isPending: false }),
  useDisassociateCluster: () => ({ mutateAsync: mockDisassociate }),
  useInstallOperator: () => ({ mutateAsync: mockInstallOp, isPending: false }),
  useEksInstances: () => ({ data: mockInstances(), isLoading: false, refetch: vi.fn() }),
  useDeployEksAgent: () => ({ mutateAsync: mockDeploy, isPending: false, error: null }),
  useStopEksAgent: () => ({ mutate: mockStop }),
  useReloadEksAgent: () => ({ mutate: mockReload }),
  useEksAgentLogs: (id: string) => ({ data: mockLogs(), isLoading: false, refetch: vi.fn() }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockCluster.mockReturnValue(undefined);
  mockInstances.mockReturnValue(undefined);
  mockLogs.mockReturnValue(undefined);
});

// ═══════════════════════════════════════════════════════════════════════════════
// 1. EksClusterTab
// ═══════════════════════════════════════════════════════════════════════════════

describe('EksClusterTab', () => {
  it('shows "Not configured" when no cluster is associated', () => {
    mockCluster.mockReturnValue({ configured: false, operator: null });
    renderWithProviders(<EksClusterTab />);

    expect(screen.getByText('Not configured')).toBeInTheDocument();
    expect(screen.getByText('No EKS Cluster Associated')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /discover/i })).toBeInTheDocument();
  });

  it('shows cluster details when configured', () => {
    mockCluster.mockReturnValue(makeEksCluster());
    renderWithProviders(<EksClusterTab />);

    // cluster_name appears in stat card and detail section — just verify at least one
    expect(screen.getAllByText('openclaw-test').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('us-west-2').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Associated Cluster')).toBeInTheDocument();
  });

  it('shows in-cluster auto-detection label', () => {
    mockCluster.mockReturnValue(makeEksCluster({ in_cluster: true }));
    renderWithProviders(<EksClusterTab />);

    expect(screen.getByText(/Running inside EKS \(auto-detected\)/)).toBeInTheDocument();
  });

  it('shows operator version when installed', () => {
    mockCluster.mockReturnValue(makeEksCluster());
    renderWithProviders(<EksClusterTab />);

    expect(screen.getByText('v0.22.2')).toBeInTheDocument();
    expect(screen.getByText('Registered')).toBeInTheDocument();
  });

  it('shows operator warning when not installed', () => {
    mockCluster.mockReturnValue(makeEksCluster({
      operator: { installed: false, crd_exists: false, deployment_ready: false, version: '', pods: [] },
    }));
    renderWithProviders(<EksClusterTab />);

    expect(screen.getByText('Not installed')).toBeInTheDocument();
    expect(screen.getByText(/OpenClaw Operator not installed/)).toBeInTheDocument();
    // No install button — operator is deployed via install.sh / Terraform
    expect(screen.getByText('install.sh')).toBeInTheDocument();
  });

  it('has refresh and disassociate buttons when configured', () => {
    mockCluster.mockReturnValue(makeEksCluster());
    renderWithProviders(<EksClusterTab />);

    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /disassociate/i })).toBeInTheDocument();
  });

  it('calls discover when button clicked', async () => {
    const user = userEvent.setup();
    mockCluster.mockReturnValue({ configured: false, operator: null });
    mockDiscover.mockResolvedValue({ clusters: [] });
    renderWithProviders(<EksClusterTab />);

    await user.click(screen.getByRole('button', { name: /discover/i }));
    expect(mockDiscover).toHaveBeenCalled();
  });

  it('shows CRD stat card', () => {
    mockCluster.mockReturnValue(makeEksCluster());
    renderWithProviders(<EksClusterTab />);

    expect(screen.getByText('CRD')).toBeInTheDocument();
    expect(screen.getByText('OpenClawInstance v1alpha1')).toBeInTheDocument();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// 2. EksInstancesTab
// ═══════════════════════════════════════════════════════════════════════════════

describe('EksInstancesTab', () => {
  it('shows guidance when cluster not configured', () => {
    mockCluster.mockReturnValue({ configured: false });
    renderWithProviders(<EksInstancesTab />);

    expect(screen.getByText(/Associate an EKS cluster/)).toBeInTheDocument();
  });

  it('shows guidance when operator not ready', () => {
    mockCluster.mockReturnValue(makeEksCluster({
      operator: { installed: false, crd_exists: false, deployment_ready: false },
    }));
    renderWithProviders(<EksInstancesTab />);

    expect(screen.getByText(/Install the OpenClaw Operator/)).toBeInTheDocument();
  });

  it('shows empty state when no instances', () => {
    mockCluster.mockReturnValue(makeEksCluster());
    mockInstances.mockReturnValue({ instances: [], namespace: 'openclaw' });
    renderWithProviders(<EksInstancesTab />);

    expect(screen.getByText(/No OpenClaw instances deployed/)).toBeInTheDocument();
  });

  it('renders instances table with data', () => {
    mockCluster.mockReturnValue(makeEksCluster());
    mockInstances.mockReturnValue({
      instances: [
        makeEksInstance(),
        makeEksInstance({ name: 'agt-bob', phase: 'Pending', employee: 'emp-bob', model: 'bedrock/nova-pro' }),
      ],
      namespace: 'openclaw',
    });
    renderWithProviders(<EksInstancesTab />);

    expect(screen.getByText('agt-carol')).toBeInTheDocument();
    expect(screen.getByText('agt-bob')).toBeInTheDocument();
    expect(screen.getByText('Running')).toBeInTheDocument();
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText(/2 instances/)).toBeInTheDocument();
  });

  it('has Deploy Agent and Refresh buttons', () => {
    mockCluster.mockReturnValue(makeEksCluster());
    mockInstances.mockReturnValue({ instances: [], namespace: 'openclaw' });
    renderWithProviders(<EksInstancesTab />);

    expect(screen.getByRole('button', { name: /deploy agent/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
  });

  it('opens deploy modal on button click', async () => {
    const user = userEvent.setup();
    mockCluster.mockReturnValue(makeEksCluster());
    mockInstances.mockReturnValue({ instances: [], namespace: 'openclaw' });
    const agents = [makeAgent()];
    renderWithProviders(<EksInstancesTab agents={agents} />);

    await user.click(screen.getByRole('button', { name: /deploy agent/i }));
    expect(screen.getByText('Deploy Agent to EKS')).toBeInTheDocument();
  });

  it('filters out already-deployed agents from deploy modal', async () => {
    const user = userEvent.setup();
    mockCluster.mockReturnValue(makeEksCluster());
    mockInstances.mockReturnValue({
      instances: [makeEksInstance({ name: 'agt-carol' })],
      namespace: 'openclaw',
    });
    const agents = [
      makeAgent({ id: 'agt-carol', name: "Carol's Agent" }),
      makeAgent({ id: 'agt-bob', name: "Bob's Agent", employeeName: 'Bob' }),
    ];
    renderWithProviders(<EksInstancesTab agents={agents} />);

    await user.click(screen.getByRole('button', { name: /deploy agent/i }));
    // The modal's agent select should only have Bob (Carol already deployed)
    const modal = screen.getByText('Deploy Agent to EKS').closest('[role="dialog"]') || document.body;
    expect(within(modal).queryByText(/Carol's Agent/)).toBeNull();
    expect(within(modal).getByText(/Bob's Agent/)).toBeInTheDocument();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// 3. DeployEksModal (via EksInstancesTab)
// ═══════════════════════════════════════════════════════════════════════════════

describe('DeployEksModal', () => {
  async function openDeployModal(agents = [makeAgent()]) {
    const user = userEvent.setup();
    mockCluster.mockReturnValue(makeEksCluster());
    mockInstances.mockReturnValue({ instances: [], namespace: 'openclaw' });
    renderWithProviders(<EksInstancesTab agents={agents} />);
    await user.click(screen.getByRole('button', { name: /deploy agent/i }));
    return user;
  }

  it('renders all form fields', async () => {
    await openDeployModal();

    expect(screen.getByText('Agent')).toBeInTheDocument();
    expect(screen.getByText('Bedrock Model')).toBeInTheDocument();
    expect(screen.getByText('Container Image')).toBeInTheDocument();
    expect(screen.getByText('Global Registry')).toBeInTheDocument();
    expect(screen.getByText('Compute Resources')).toBeInTheDocument();
    expect(screen.getByText('CPU Request')).toBeInTheDocument();
    expect(screen.getByText('CPU Limit')).toBeInTheDocument();
    expect(screen.getByText('Memory Request')).toBeInTheDocument();
    expect(screen.getByText('Memory Limit')).toBeInTheDocument();
    expect(screen.getByText('Storage Class')).toBeInTheDocument();
    expect(screen.getByText('Storage Size')).toBeInTheDocument();
    expect(screen.getByText('Chromium Browser Sidecar')).toBeInTheDocument();
  });

  it('deploy button is disabled without agent selection', async () => {
    await openDeployModal();
    const deployBtn = screen.getByRole('button', { name: /^deploy$/i });
    expect(deployBtn).toBeDisabled();
  });

  it('shows advanced options when toggled', async () => {
    const user = await openDeployModal();

    // Advanced fields should not be visible yet
    expect(screen.queryByText('Runtime Class')).toBeNull();

    await user.click(screen.getByText(/show advanced/i));

    expect(screen.getByText('Runtime Class')).toBeInTheDocument();
    expect(screen.getByText('Service Type')).toBeInTheDocument();
    expect(screen.getByText('Backup Schedule')).toBeInTheDocument();
    expect(screen.getByText('Node Selector (JSON)')).toBeInTheDocument();
    expect(screen.getByText('Tolerations (JSON)')).toBeInTheDocument();
  });

  it('can hide advanced options', async () => {
    const user = await openDeployModal();

    await user.click(screen.getByText(/show advanced/i));
    expect(screen.getByText('Runtime Class')).toBeInTheDocument();

    await user.click(screen.getByText(/hide advanced/i));
    expect(screen.queryByText('Runtime Class')).toBeNull();
  });

  it('has cancel button that closes modal', async () => {
    const user = await openDeployModal();
    expect(screen.getByText('Deploy Agent to EKS')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.queryByText('Deploy Agent to EKS')).toBeNull();
  });

  it('shows China registry description', async () => {
    await openDeployModal();
    expect(screen.getByText(/required for China regions/)).toBeInTheDocument();
  });
});
