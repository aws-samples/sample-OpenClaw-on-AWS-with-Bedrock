import { useState, useCallback } from 'react';
import {
  Github, GitBranch, LifeBuoy, Check, X, Loader,
  KeyRound, ChevronDown, ChevronUp, Unplug, Plug, AlertCircle, ShieldCheck, Zap, ExternalLink,
} from 'lucide-react';
import { Card, Badge, Button, PageHeader } from '../components/ui';
import { api } from '../api/client';

// ─── Types ───────────────────────────────────────────────────────────────────

type MCPProvider = 'github' | 'ado' | 'jira';
type ConnectionStatus = 'idle' | 'validating' | 'connected' | 'error';

interface SecretField {
  key: string;      // env var name, e.g. GITHUB_TOKEN
  label: string;
  placeholder: string;
  hint: string;
  isUrl?: boolean;
}

interface ProviderConfig {
  id: MCPProvider;
  name: string;
  description: string;
  docsUrl: string;
  color: string;          // tailwind-compatible CSS color var
  gradientFrom: string;
  gradientTo: string;
  iconBg: string;
  secretFields: SecretField[];
  mcpServer: string;      // npm package or command
}

interface ConnectionState {
  status: ConnectionStatus;
  secretValues: Record<string, string>;       // key → user-entered secret NAME in Secrets Manager
  expandedConfig: boolean;
  validationError: string;
  connectedAt: string;
  testResult: string;
}

// ─── Provider Definitions ─────────────────────────────────────────────────────

const PROVIDERS: ProviderConfig[] = [
  {
    id: 'github',
    name: 'GitHub',
    description: 'Read/write repos, PRs, issues, branches and code via GitHub MCP server.',
    docsUrl: 'https://github.com/modelcontextprotocol/servers/tree/main/src/github',
    color: '#e8eaed',
    gradientFrom: '#1f2937',
    gradientTo: '#111827',
    iconBg: 'rgba(255,255,255,0.08)',
    mcpServer: 'npx @modelcontextprotocol/server-github',
    secretFields: [
      {
        key: 'GITHUB_TOKEN',
        label: 'GitHub Personal Access Token',
        placeholder: 'e.g. /openclaw/mcp/GITHUB_TOKEN',
        hint: 'Secret name in AWS Secrets Manager. Needs repo, read:org scopes.',
      },
    ],
  },
  {
    id: 'ado',
    name: 'Azure DevOps',
    description: 'Access ADO boards, repos, pipelines, and work items via MCP.',
    docsUrl: 'https://github.com/microsoft/azure-devops-mcp',
    color: '#60a5fa',
    gradientFrom: '#1e3a5f',
    gradientTo: '#111827',
    iconBg: 'rgba(96,165,250,0.1)',
    mcpServer: 'npx @azure-devops/mcp-server',
    secretFields: [
      {
        key: 'ADO_TOKEN',
        label: 'ADO Personal Access Token',
        placeholder: 'e.g. /openclaw/mcp/ADO_TOKEN',
        hint: 'Secret name in AWS Secrets Manager. Needs Code (Read & Write), Work Items scopes.',
      },
      {
        key: 'ADO_ORG_URL',
        label: 'Organization URL',
        placeholder: 'e.g. /openclaw/mcp/ADO_ORG_URL',
        hint: 'Secret holding your ADO org URL like https://dev.azure.com/myorg',
        isUrl: true,
      },
    ],
  },
  {
    id: 'jira',
    name: 'Jira',
    description: 'Create, search, update and transition Jira issues from agent conversations.',
    docsUrl: 'https://github.com/sooperset/mcp-atlassian',
    color: '#60a5fa',
    gradientFrom: '#1a2d4a',
    gradientTo: '#111827',
    iconBg: 'rgba(96,165,250,0.1)',
    mcpServer: 'uvx mcp-atlassian',
    secretFields: [
      {
        key: 'JIRA_URL',
        label: 'Jira Base URL',
        placeholder: 'e.g. /openclaw/mcp/JIRA_URL',
        hint: 'Secret holding your Jira URL, e.g. https://yourorg.atlassian.net',
        isUrl: true,
      },
      {
        key: 'JIRA_USER_EMAIL',
        label: 'Jira Account Email',
        placeholder: 'e.g. /openclaw/mcp/JIRA_USER_EMAIL',
        hint: 'Secret holding your Atlassian account email.',
      },
      {
        key: 'JIRA_API_TOKEN',
        label: 'Jira API Token',
        placeholder: 'e.g. /openclaw/mcp/JIRA_API_TOKEN',
        hint: 'Secret name in AWS Secrets Manager. Generate at id.atlassian.com/manage-profile/security/api-tokens.',
      },
    ],
  },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

function initialConnectionState(): ConnectionState {
  return {
    status: 'idle',
    secretValues: {},
    expandedConfig: false,
    validationError: '',
    connectedAt: '',
    testResult: '',
  };
}

// ─── Provider Icon ─────────────────────────────────────────────────────────

function ProviderIcon({ id, size = 24 }: { id: MCPProvider; size?: number }) {
  if (id === 'github') return <Github size={size} />;
  if (id === 'ado')    return <GitBranch size={size} />;
  return <LifeBuoy size={size} />;
}

// ─── Status Indicator ────────────────────────────────────────────────────────

function StatusPill({ status, error }: { status: ConnectionStatus; error?: string }) {
  if (status === 'connected') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
      style={{ background: 'rgba(74,222,128,0.12)', color: '#4ade80' }}>
      <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
      Connected
    </span>
  );
  if (status === 'validating') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
      style={{ background: 'rgba(165,180,252,0.12)', color: '#a5b4fc' }}>
      <Loader size={10} className="animate-spin" />
      Validating…
    </span>
  );
  if (status === 'error') return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
      style={{ background: 'rgba(248,113,113,0.12)', color: '#f87171' }}>
      <X size={10} />
      {error || 'Error'}
    </span>
  );
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
      style={{ background: 'rgba(255,255,255,0.06)', color: '#9aa0a6' }}>
      <span className="w-1.5 h-1.5 rounded-full bg-[#9aa0a6]" />
      Not connected
    </span>
  );
}

// ─── Provider Card ────────────────────────────────────────────────────────────

function ProviderCard({
  provider,
  state,
  onFieldChange,
  onConnect,
  onDisconnect,
  onTest,
  onToggleExpand,
}: {
  provider: ProviderConfig;
  state: ConnectionState;
  onFieldChange: (key: string, value: string) => void;
  onConnect: () => void;
  onDisconnect: () => void;
  onTest: () => void;
  onToggleExpand: () => void;
}) {
  const isConnected = state.status === 'connected';
  const isValidating = state.status === 'validating';
  const allFilled = provider.secretFields.every(f => (state.secretValues[f.key] || '').trim() !== '');

  return (
    <div className="rounded-2xl overflow-hidden border transition-all duration-300"
      style={{
        borderColor: isConnected ? 'rgba(74,222,128,0.25)' : 'var(--color-dark-border)',
        background: 'var(--color-dark-card)',
        boxShadow: isConnected ? '0 0 0 1px rgba(74,222,128,0.1)' : 'none',
      }}>

      {/* ── Header ── */}
      <div className="flex items-center justify-between p-5">
        <div className="flex items-center gap-4">
          {/* Icon */}
          <div className="w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: provider.iconBg, color: provider.color }}>
            <ProviderIcon id={provider.id} size={22} />
          </div>

          <div>
            <div className="flex items-center gap-2.5">
              <h3 className="text-base font-semibold text-text-primary">{provider.name}</h3>
              <StatusPill status={state.status} error={state.validationError} />
            </div>
            <p className="text-xs text-text-muted mt-0.5 max-w-md">{provider.description}</p>
            {isConnected && state.connectedAt && (
              <p className="text-[10px] text-green-400/70 mt-1">Connected {state.connectedAt}</p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {isConnected && (
            <>
              <button onClick={onTest}
                className="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors flex items-center gap-1.5"
                style={{ background: 'rgba(165,180,252,0.1)', color: '#a5b4fc' }}>
                <Zap size={12} /> Test
              </button>
              <button onClick={onDisconnect}
                className="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors flex items-center gap-1.5"
                style={{ background: 'rgba(248,113,113,0.08)', color: '#f87171' }}>
                <Unplug size={12} /> Disconnect
              </button>
            </>
          )}
          <a href={provider.docsUrl} target="_blank" rel="noopener noreferrer"
            className="rounded-lg p-2 transition-colors text-text-muted hover:text-text-primary hover:bg-dark-hover">
            <ExternalLink size={14} />
          </a>
          <button onClick={onToggleExpand}
            className="rounded-lg p-2 transition-colors text-text-muted hover:text-text-primary hover:bg-dark-hover">
            {state.expandedConfig ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
      </div>

      {/* ── Config Panel ── */}
      {state.expandedConfig && (
        <div className="border-t px-5 pb-5 pt-4 space-y-4"
          style={{ borderColor: 'var(--color-dark-border)' }}>

          {/* MCP server info badge */}
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-mono"
            style={{ background: 'var(--color-dark-bg)', color: '#9aa0a6' }}>
            <Plug size={11} className="flex-shrink-0" />
            <span className="text-text-muted">MCP Server:</span>
            <span className="text-primary">{provider.mcpServer}</span>
          </div>

          {/* Secret name fields */}
          <div className="space-y-3">
            {provider.secretFields.map(field => (
              <div key={field.key}>
                <label className="block text-xs font-medium text-text-secondary mb-1.5">
                  <span className="flex items-center gap-1.5">
                    <KeyRound size={11} className="text-text-muted" />
                    {field.label}
                    {field.isUrl && <Badge color="info">URL</Badge>}
                  </span>
                </label>
                <div className="relative">
                  <input
                    type="text"
                    value={state.secretValues[field.key] || ''}
                    onChange={e => onFieldChange(field.key, e.target.value)}
                    placeholder={field.placeholder}
                    disabled={isConnected || isValidating}
                    className="w-full rounded-xl px-4 py-2.5 text-sm font-mono transition-colors focus:outline-none focus:ring-2"
                    style={{
                      background: 'var(--color-dark-bg)',
                      border: '1px solid var(--color-dark-border)',
                      color: 'var(--color-text-primary)',
                      opacity: isConnected ? 0.6 : 1,
                    }}
                    onFocus={e => (e.target.style.borderColor = '#a5b4fc')}
                    onBlur={e => (e.target.style.borderColor = 'var(--color-dark-border)')}
                  />
                </div>
                <p className="text-[11px] text-text-muted mt-1.5 ml-1">{field.hint}</p>
              </div>
            ))}
          </div>

          {/* Error message */}
          {state.status === 'error' && state.validationError && (
            <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-xl"
              style={{ background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.2)' }}>
              <AlertCircle size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-red-300">{state.validationError}</p>
            </div>
          )}

          {/* Test result */}
          {state.testResult && (
            <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-xl"
              style={{ background: 'rgba(74,222,128,0.08)', border: '1px solid rgba(74,222,128,0.2)' }}>
              <ShieldCheck size={14} className="text-green-400 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-green-300">{state.testResult}</p>
            </div>
          )}

          {/* Connect button */}
          {!isConnected && (
            <button
              onClick={onConnect}
              disabled={!allFilled || isValidating}
              className="w-full flex items-center justify-center gap-2 rounded-xl py-2.5 text-sm font-medium transition-all duration-200"
              style={{
                background: allFilled && !isValidating
                  ? 'linear-gradient(135deg, #6366f1, #4f46e5)'
                  : 'rgba(255,255,255,0.06)',
                color: allFilled && !isValidating ? '#fff' : '#9aa0a6',
                cursor: allFilled && !isValidating ? 'pointer' : 'not-allowed',
              }}>
              {isValidating
                ? <><Loader size={14} className="animate-spin" /> Validating secrets…</>
                : <><Check size={14} /> Validate & Connect</>
              }
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Agentic Pipeline Preview ─────────────────────────────────────────────────

function AgenticPipelinePreview({ connected }: { connected: MCPProvider[] }) {
  const examples: Record<MCPProvider, string> = {
    github: '"Review open PRs touching the auth module and summarize risk"',
    ado:    '"List all active work items in sprint 42 assigned to me"',
    jira:   '"Create a high-priority Jira ticket for the login bug reported today"',
  };

  if (connected.length === 0) return null;

  return (
    <div className="rounded-2xl border p-5 space-y-3"
      style={{ borderColor: 'rgba(165,180,252,0.15)', background: 'rgba(99,102,241,0.05)' }}>
      <div className="flex items-center gap-2">
        <Zap size={14} className="text-primary" />
        <h4 className="text-sm font-semibold text-text-primary">Try in Playground</h4>
        <Badge color="primary">Live</Badge>
      </div>
      <p className="text-xs text-text-muted">Connected MCPs are ready. Use these prompts in the Playground:</p>
      <div className="space-y-2">
        {connected.map(id => (
          <div key={id} className="flex items-start gap-2.5 px-3 py-2.5 rounded-xl"
            style={{ background: 'var(--color-dark-bg)', border: '1px solid var(--color-dark-border)' }}>
            <ProviderIcon id={id} size={13} />
            <span className="text-xs text-text-secondary font-mono">{examples[id]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function MCPConnections() {
  const [states, setStates] = useState<Record<MCPProvider, ConnectionState>>(
    () => Object.fromEntries(PROVIDERS.map(p => [p.id, initialConnectionState()])) as Record<MCPProvider, ConnectionState>
  );

  const update = useCallback((id: MCPProvider, patch: Partial<ConnectionState>) => {
    setStates(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }, []);

  const handleFieldChange = (id: MCPProvider, key: string, value: string) => {
    setStates(prev => ({
      ...prev,
      [id]: {
        ...prev[id],
        secretValues: { ...prev[id].secretValues, [key]: value },
        validationError: '',
        status: 'idle',
      },
    }));
  };

  const handleConnect = async (provider: ProviderConfig) => {
    update(provider.id, { status: 'validating', validationError: '', testResult: '' });

    try {
      // Call backend to validate secrets exist in Secrets Manager and start MCP
      await api.post('/mcp/connect', {
        provider: provider.id,
        secretNames: provider.secretFields.map(f => ({
          key: f.key,
          secretName: states[provider.id].secretValues[f.key],
        })),
      });

      update(provider.id, {
        status: 'connected',
        connectedAt: new Date().toLocaleTimeString(),
        expandedConfig: false,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error
        ? err.message
        : 'Could not resolve secrets in AWS Secrets Manager. Check the secret name and IAM permissions.';
      update(provider.id, {
        status: 'error',
        validationError: msg,
      });
    }
  };

  const handleDisconnect = async (provider: ProviderConfig) => {
    try {
      await api.post('/mcp/disconnect', { provider: provider.id });
    } catch { /* best-effort */ }
    update(provider.id, { ...initialConnectionState(), expandedConfig: false });
  };

  const handleTest = async (provider: ProviderConfig) => {
    update(provider.id, { testResult: '' });
    try {
      const res = await api.post<{ message: string }>('/mcp/test', { provider: provider.id });
      update(provider.id, { testResult: res.message || 'Connection healthy ✓' });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Test failed';
      update(provider.id, { testResult: `⚠ ${msg}` });
    }
  };

  const handleToggleExpand = (id: MCPProvider) => {
    update(id, { expandedConfig: !states[id].expandedConfig });
  };

  const connectedProviders = PROVIDERS
    .filter(p => states[p.id].status === 'connected')
    .map(p => p.id);

  const connectedCount = connectedProviders.length;

  return (
    <div className="space-y-6 animate-fade-enter">
      <PageHeader
        title="MCP Connections"
        subtitle="Connect your developer tools to OpenClaw agents via Model Context Protocol. Tokens are resolved securely from AWS Secrets Manager."
        actions={
          <div className="flex items-center gap-3">
            {connectedCount > 0 && (
              <Badge color="success">{connectedCount} connected</Badge>
            )}
            <a
              href="https://modelcontextprotocol.io"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors">
              <ExternalLink size={12} /> MCP Docs
            </a>
          </div>
        }
      />

      {/* How it works banner */}
      <div className="rounded-2xl border p-4 flex items-start gap-4"
        style={{ borderColor: 'rgba(165,180,252,0.15)', background: 'rgba(99,102,241,0.04)' }}>
        <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: 'rgba(165,180,252,0.1)' }}>
          <ShieldCheck size={16} className="text-primary" />
        </div>
        <div className="text-xs text-text-muted space-y-1">
          <p className="text-text-secondary font-medium">How it works</p>
          <p>
            Enter the <span className="text-primary font-mono">secret name</span> (path) stored in{' '}
            <span className="text-primary">AWS Secrets Manager</span>. OpenClaw validates the secret exists,
            injects it into the MCP server subprocess, and makes the tools available to your playground agents
            — all without the token ever touching the UI.
          </p>
        </div>
      </div>

      {/* Provider cards */}
      <div className="space-y-4">
        {PROVIDERS.map(provider => (
          <ProviderCard
            key={provider.id}
            provider={provider}
            state={states[provider.id]}
            onFieldChange={(key, val) => handleFieldChange(provider.id, key, val)}
            onConnect={() => handleConnect(provider)}
            onDisconnect={() => handleDisconnect(provider)}
            onTest={() => handleTest(provider)}
            onToggleExpand={() => handleToggleExpand(provider.id)}
          />
        ))}
      </div>

      {/* Playground prompt suggestions when at least one connected */}
      <AgenticPipelinePreview connected={connectedProviders} />

      {/* Secret layout reference */}
      <div className="rounded-2xl border p-5 space-y-3"
        style={{ borderColor: 'var(--color-dark-border)', background: 'var(--color-dark-card)' }}>
        <div className="flex items-center gap-2">
          <KeyRound size={14} className="text-text-muted" />
          <h4 className="text-sm font-semibold text-text-primary">Recommended Secret Layout</h4>
        </div>
        <pre className="text-xs font-mono text-text-secondary leading-6 overflow-x-auto"
          style={{ background: 'var(--color-dark-bg)', borderRadius: 12, padding: '12px 16px' }}>
{`# Org-wide (admin configures once)
/openclaw/mcp/GITHUB_TOKEN
/openclaw/mcp/ADO_TOKEN
/openclaw/mcp/ADO_ORG_URL
/openclaw/mcp/JIRA_URL
/openclaw/mcp/JIRA_USER_EMAIL
/openclaw/mcp/JIRA_API_TOKEN

# Per-user overrides (optional)
/openclaw/mcp/{emp_id}/GITHUB_TOKEN`}
        </pre>
        <p className="text-[11px] text-text-muted">
          Per-user secrets take priority over org-wide ones. The EC2 IAM role needs{' '}
          <span className="font-mono text-primary">secretsmanager:GetSecretValue</span> on{' '}
          <span className="font-mono text-primary">arn:aws:secretsmanager:*:*:secret:/openclaw/mcp/*</span>
        </p>
      </div>
    </div>
  );
}
