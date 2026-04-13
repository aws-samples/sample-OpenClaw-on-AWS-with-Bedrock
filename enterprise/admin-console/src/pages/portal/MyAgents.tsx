import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bot, Zap, MessageSquare, Wifi, WifiOff, Settings, Lock } from 'lucide-react';
import { Card, Badge, Button, Modal } from '../../components/ui';
import { useAuth } from '../../contexts/AuthContext';
import { api } from '../../api/client';

const IM_PLATFORMS = [
  { id: 'feishu', label: 'Feishu / Lark', fields: [{ key: 'app_id', label: 'App ID', placeholder: 'cli_xxxxxxxx' }, { key: 'app_secret', label: 'App Secret', placeholder: '', secret: true }] },
  { id: 'telegram', label: 'Telegram', fields: [{ key: 'token', label: 'Bot Token', placeholder: '123456:ABC-DEF...', secret: true }] },
  { id: 'slack', label: 'Slack', fields: [{ key: 'bot_token', label: 'Bot Token (xoxb-)', placeholder: 'xoxb-...', secret: true }, { key: 'app_token', label: 'App Token (xapp-)', placeholder: 'xapp-...', secret: true }] },
  { id: 'discord', label: 'Discord', fields: [{ key: 'token', label: 'Bot Token', placeholder: '', secret: true }] },
];

export default function MyAgents() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [connectModal, setConnectModal] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [connecting, setConnecting] = useState(false);

  // Fetch agents on mount
  useState(() => {
    api.get('/portal/my-agents').then(setAgents).catch(() => {}).finally(() => setLoading(false));
  });

  const handleConnect = async () => {
    if (!connectModal) return;
    setConnecting(true);
    try {
      await api.post('/portal/agent/channels/add', { channel: connectModal, ...credentials });
      // Refresh
      const updated = await api.get('/portal/my-agents');
      setAgents(updated);
      setConnectModal(null);
      setCredentials({});
    } catch (e: any) {
      alert(e?.message || 'Connection failed');
    }
    setConnecting(false);
  };

  const handleDisconnect = async (channel: string) => {
    if (!confirm(`Disconnect ${channel}?`)) return;
    try {
      await api.del(`/portal/agent/channels/${channel}`);
      const updated = await api.get('/portal/my-agents');
      setAgents(updated);
    } catch {}
  };

  if (loading) return <div className="flex justify-center py-20 text-text-muted">Loading...</div>;

  const serverless = agents?.serverless || {};
  const alwaysOn = agents?.alwaysOn || {};
  const allowedPlatforms = agents?.allowedIMPlatforms || [];

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-text-primary">My Agents</h1>
        <p className="text-sm text-text-muted mt-1">Your AI assistants at ACME Corp</p>
      </div>

      {/* Serverless Agent */}
      <Card>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10"><Bot size={20} className="text-primary" /></div>
            <div>
              <h3 className="font-semibold text-text-primary">Serverless Agent</h3>
              <p className="text-xs text-text-muted">{serverless.positionName} &middot; On-demand, ~30s cold start</p>
            </div>
          </div>
          <Badge color={serverless.status === 'active' ? 'success' : 'default'}>{serverless.status}</Badge>
        </div>
        <p className="text-sm text-text-secondary mt-3">
          Available through the company shared IM bot and Portal chat. Uses AgentCore serverless mode.
        </p>
        <div className="mt-3">
          <Button variant="primary" size="sm" onClick={() => navigate('/portal/chat')}>
            <MessageSquare size={14} /> Open Chat
          </Button>
        </div>
      </Card>

      {/* Always-On Agent */}
      <Card>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-success/10"><Zap size={20} className="text-success" /></div>
            <div>
              <h3 className="font-semibold text-text-primary">Always-On Agent</h3>
              {alwaysOn.enabled ? (
                <p className="text-xs text-text-muted">{alwaysOn.tier} tier &middot; Instant response, HEARTBEAT enabled</p>
              ) : (
                <p className="text-xs text-text-muted">Not configured</p>
              )}
            </div>
          </div>
          <Badge color={alwaysOn.status === 'running' ? 'success' : alwaysOn.status === 'stopped' ? 'warning' : 'default'}>
            {alwaysOn.status === 'running' ? 'Running' : alwaysOn.status === 'stopped' ? 'Stopped' : 'Not Configured'}
          </Badge>
        </div>

        {!alwaysOn.enabled ? (
          <div className="mt-3 rounded-lg bg-dark-bg p-4 text-center">
            <Lock size={20} className="mx-auto text-text-muted mb-2" />
            <p className="text-sm text-text-muted">Your administrator has not enabled always-on mode for your account.</p>
            <p className="text-xs text-text-muted mt-1">Contact your IT admin to request always-on access.</p>
          </div>
        ) : (
          <div className="mt-4 space-y-4">
            {/* Chat button */}
            <Button variant="primary" size="sm" onClick={() => navigate('/portal/chat?agent=always-on')}>
              <Zap size={14} /> Open Always-On Chat
            </Button>

            {/* IM Connections */}
            <div>
              <p className="text-sm font-medium text-text-primary mb-2">IM Connections</p>
              {(alwaysOn.imChannels || []).length === 0 ? (
                <p className="text-xs text-text-muted mb-2">No IM channels connected yet. Connect one below to chat directly from your IM app.</p>
              ) : (
                <div className="space-y-2 mb-3">
                  {(alwaysOn.imChannels || []).map((ch: any) => (
                    <div key={ch.channel} className="flex items-center justify-between rounded-lg bg-dark-bg px-3 py-2">
                      <div className="flex items-center gap-2">
                        <Wifi size={14} className="text-success" />
                        <span className="text-sm font-medium">{ch.channel}</span>
                        <span className="text-xs text-text-muted">Connected {ch.connectedAt ? new Date(ch.connectedAt).toLocaleDateString() : ''}</span>
                      </div>
                      <button onClick={() => handleDisconnect(ch.channel)}
                        className="text-xs text-danger hover:text-danger/80">
                        <WifiOff size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Connect buttons for allowed platforms */}
              <div className="flex flex-wrap gap-2">
                {IM_PLATFORMS.filter(p => allowedPlatforms.includes(p.id))
                  .filter(p => !(alwaysOn.imChannels || []).some((ch: any) => ch.channel === p.id))
                  .map(p => (
                    <Button key={p.id} variant="default" size="sm" onClick={() => { setConnectModal(p.id); setCredentials({}); }}>
                      <Settings size={14} /> Connect {p.label}
                    </Button>
                  ))
                }
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Connect IM Modal */}
      {connectModal && (
        <Modal open={true} onClose={() => setConnectModal(null)}
          title={`Connect ${IM_PLATFORMS.find(p => p.id === connectModal)?.label || connectModal}`}
          footer={
            <div className="flex justify-end gap-3">
              <Button variant="default" onClick={() => setConnectModal(null)}>Cancel</Button>
              <Button variant="primary" disabled={connecting} onClick={handleConnect}>
                {connecting ? 'Connecting...' : 'Connect & Verify'}
              </Button>
            </div>
          }>
          <div className="space-y-4">
            <div className="rounded-lg bg-info/5 border border-info/20 px-3 py-2 text-xs text-info">
              {connectModal === 'feishu' && 'Create an enterprise app at open.feishu.cn, get admin approval, then enter the App ID and Secret below.'}
              {connectModal === 'telegram' && 'Message @BotFather on Telegram to create a new bot. Copy the token it gives you.'}
              {connectModal === 'slack' && 'Create a Slack app at api.slack.com/apps with Socket Mode enabled. Copy both tokens.'}
              {connectModal === 'discord' && 'Create an application at discord.com/developers, add a Bot, and copy the token.'}
            </div>
            {IM_PLATFORMS.find(p => p.id === connectModal)?.fields.map(f => (
              <div key={f.key}>
                <label className="block text-xs text-text-muted mb-1">{f.label}</label>
                <input
                  type={f.secret ? 'password' : 'text'}
                  placeholder={f.placeholder}
                  value={credentials[f.key] || ''}
                  onChange={e => setCredentials(prev => ({ ...prev, [f.key]: e.target.value }))}
                  className="w-full rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary font-mono focus:border-primary focus:outline-none"
                />
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
