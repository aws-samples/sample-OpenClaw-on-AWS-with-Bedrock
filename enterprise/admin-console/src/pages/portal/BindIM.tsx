import { useState, useEffect, useCallback, useRef } from 'react';
import { CheckCircle, Loader2, RefreshCw, Link2, ExternalLink, Clock, AlertCircle, UserPlus, Zap, Radio } from 'lucide-react';
import { Card, Badge, Button } from '../../components/ui';
import { api } from '../../api/client';
import { IM_ICONS } from '../../components/IMIcons';

interface Channel {
  id: string;
  label: string;
  description: string;
}

// All mainstream IM platforms. Availability is determined dynamically by
// fetching which channels the admin has configured via OpenClaw Gateway.
const CHANNELS: Channel[] = [
  { id: 'telegram',   label: 'Telegram',         description: 'Scan QR or click the link to open the enterprise bot' },
  { id: 'discord',    label: 'Discord',           description: 'Connect to the enterprise agent in your company Discord server' },
  { id: 'feishu',     label: 'Feishu / Lark',     description: 'Connect to the enterprise Feishu bot' },
  { id: 'dingtalk',   label: 'DingTalk',          description: 'Connect to the enterprise DingTalk bot' },
  { id: 'slack',      label: 'Slack',             description: 'Connect to the enterprise agent in your Slack workspace' },
  { id: 'teams',      label: 'Microsoft Teams',   description: 'Connect to the enterprise agent in Microsoft Teams' },
  { id: 'googlechat', label: 'Google Chat',       description: 'Connect to the enterprise agent in Google Chat' },
  { id: 'whatsapp',   label: 'WhatsApp',          description: 'Connect via WhatsApp Business' },
  { id: 'wechat',     label: 'WeChat',            description: 'Connect via WeChat enterprise bot' },
];

type StepState = 'idle' | 'feishu-prereq' | 'loading' | 'waiting' | 'done' | 'error' | 'expired';

interface PairSession {
  token: string;
  deepLink: string | null;
  botUsername: string;
  channel: string;
  expiresAt: number;
}

function CountdownTimer({ expiresAt }: { expiresAt: number }) {
  const [remaining, setRemaining] = useState(Math.max(0, Math.floor((expiresAt - Date.now()) / 1000)));
  useEffect(() => {
    const t = setInterval(() => {
      const left = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
      setRemaining(left);
      if (left === 0) clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [expiresAt]);
  const m = Math.floor(remaining / 60), s = remaining % 60;
  return (
    <span className={`text-xs font-mono ${remaining < 60 ? 'text-danger' : 'text-text-muted'}`}>
      <Clock size={11} className="inline mr-1" />{m}:{s.toString().padStart(2, '0')}
    </span>
  );
}

function ChannelWizard({ channel, onDone, onCancel }: { channel: Channel; onDone: () => void; onCancel: () => void }) {
  const [state, setState] = useState<StepState>('idle');
  const [session, setSession] = useState<PairSession | null>(null);
  const [error, setError] = useState('');

  const startPairing = useCallback(async () => {
    setState('loading');
    setError('');
    try {
      const data = await api.post<any>('/portal/channel/pair-start', { channel: channel.id });
      setSession({ ...data, expiresAt: Date.now() + data.expiresIn * 1000 });
      setState('waiting');
    } catch (e: any) {
      setError(e?.message || 'Failed to start pairing');
      setState('error');
    }
  }, [channel.id]);

  // Poll for completion
  useEffect(() => {
    if (state !== 'waiting' || !session) return;
    const interval = setInterval(async () => {
      // Check expiry
      if (Date.now() > session.expiresAt) { setState('expired'); clearInterval(interval); return; }
      try {
        const data = await api.get<any>(`/portal/channel/pair-status?token=${session.token}`);
        if (data.status === 'completed') { setState('done'); clearInterval(interval); setTimeout(onDone, 2000); }
        if (data.status === 'expired') { setState('expired'); clearInterval(interval); }
      } catch {}
    }, 2000);
    return () => clearInterval(interval);
  }, [state, session, onDone]);

  if (state === 'idle') return (
    <div className="space-y-4">
      <div className="rounded-xl bg-surface-dim p-4 text-center">
        <div className="flex justify-center mb-2">{IM_ICONS[channel.id] ? (() => { const Icon = IM_ICONS[channel.id]; return <Icon size={48} />; })() : null}</div>
        <h3 className="text-base font-semibold text-text-primary">{channel.label}</h3>
        <p className="text-sm text-text-muted mt-1">{channel.description}</p>
      </div>
      <Button variant="primary" className="w-full"
        onClick={() => channel.id === 'feishu' ? setState('feishu-prereq') : startPairing()}>
        <Link2 size={16} /> Generate Connection Link
      </Button>
      <Button variant="ghost" className="w-full" onClick={onCancel}>Back</Button>
    </div>
  );

  if (state === 'feishu-prereq') return (
    <div className="space-y-4">
      <div className="rounded-xl bg-warning/10 border border-warning/30 p-4 space-y-3">
        <div className="flex items-center gap-2">
          <UserPlus size={18} className="text-warning flex-shrink-0" />
          <h3 className="text-sm font-semibold text-text-primary">Join the Company Feishu First</h3>
        </div>
        <p className="text-xs text-text-secondary leading-relaxed">
          The enterprise bot is only accessible to members of the ACME Corp Feishu organization.
          If you haven't joined yet, please contact your IT Admin to receive an invite link.
        </p>
        <div className="rounded-lg bg-dark-bg border border-dark-border/50 px-3 py-2.5 flex items-center gap-2">
          <span className="text-xs text-text-muted">Need access?</span>
          <span className="text-xs font-medium text-primary">Contact IT Admin to join the company Feishu</span>
        </div>
      </div>
      <div className="rounded-xl bg-info/5 border border-info/20 p-3 text-xs text-info">
        Already a member? Tap below to generate your connection link.
      </div>
      <Button variant="primary" className="w-full" onClick={startPairing}>
        <Link2 size={16} /> I'm in — Generate Connection Link
      </Button>
      <Button variant="ghost" className="w-full" onClick={onCancel}>Back</Button>
    </div>
  );

  if (state === 'loading') return (
    <div className="flex flex-col items-center py-8 gap-3">
      <Loader2 size={32} className="animate-spin text-primary" />
      <p className="text-sm text-text-muted">Generating secure link...</p>
    </div>
  );

  if (state === 'waiting' && session) return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-text-primary">Step 1 — Open {channel.label}</p>
        <CountdownTimer expiresAt={session.expiresAt} />
      </div>

      {/* Feishu: QR to open bot + separate token to copy-paste */}
      {session.deepLink && channel.id === 'feishu' ? (
        <div className="space-y-3">
          <ol className="space-y-1.5 text-xs text-text-secondary">
            <li className="flex gap-2"><span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary text-[10px] font-bold flex items-center justify-center">1</span><span>Scan the QR code below with <strong className="text-text-primary">Feishu</strong> to open ACME Agent directly</span></li>
            <li className="flex gap-2"><span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary text-[10px] font-bold flex items-center justify-center">2</span><span>The bot chat opens automatically</span></li>
            <li className="flex gap-2"><span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary text-[10px] font-bold flex items-center justify-center">3</span><span>Copy the code below and <strong className="text-text-primary">paste + send it</strong> in the chat</span></li>
          </ol>
          <div className="flex justify-center rounded-xl bg-white p-4">
            <img
              src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(session.deepLink)}`}
              alt="Feishu QR code"
              width={200} height={200}
              className="rounded"
            />
          </div>
          <div className="relative">
            <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-dark-border" /></div>
            <div className="relative flex justify-center text-xs"><span className="bg-dark-card px-2 text-text-muted">or open directly</span></div>
          </div>
          <a href={session.deepLink} target="_blank" rel="noopener noreferrer">
            <Button variant="default" className="w-full">
              <ExternalLink size={14} /> Open ACME Agent in Feishu
            </Button>
          </a>
          {/* Token to send after opening */}
          <div className="rounded-xl bg-dark-bg border border-primary/20 p-3">
            <p className="text-[10px] text-text-muted mb-2">Step 3 — Copy and send this command in the Feishu bot chat:</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-sm font-mono text-primary-light bg-primary/10 px-3 py-2 rounded-lg text-center">
                /start {session.token}
              </code>
              <button onClick={() => navigator.clipboard?.writeText(`/start ${session.token}`)}
                className="flex-shrink-0 px-2.5 py-2 rounded-lg bg-dark-hover text-text-muted hover:text-text-primary text-[10px] border border-dark-border/40 transition-colors">
                Copy
              </button>
            </div>
          </div>
        </div>
      ) : session.deepLink ? (
        <div className="space-y-3">
          <div className="flex justify-center rounded-xl bg-white p-4">
            <img
              src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(session.deepLink)}`}
              alt="QR code"
              width={200} height={200}
              className="rounded"
            />
          </div>
          <p className="text-xs text-text-muted text-center">Scan with your phone to open {channel.label}</p>
          <div className="relative">
            <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-dark-border" /></div>
            <div className="relative flex justify-center text-xs"><span className="bg-dark-card px-2 text-text-muted">or</span></div>
          </div>
          <a href={session.deepLink} target="_blank" rel="noopener noreferrer">
            <Button variant="default" className="w-full">
              <ExternalLink size={14} /> Open {channel.label} directly
            </Button>
          </a>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="rounded-xl bg-dark-bg border border-dark-border/50 p-4 space-y-3">
            {channel.id === 'discord' && (
              <ol className="space-y-2 text-xs text-text-secondary">
                <li className="flex gap-2"><span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary text-[10px] font-bold flex items-center justify-center">1</span><span>Open <strong className="text-text-primary">Discord</strong> and go to the ACME Corp server</span></li>
                <li className="flex gap-2"><span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary text-[10px] font-bold flex items-center justify-center">2</span><span>Find <strong className="text-text-primary">ACME Agent</strong> in the Members list and open a DM</span></li>
                <li className="flex gap-2"><span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/20 text-primary text-[10px] font-bold flex items-center justify-center">3</span><span><strong className="text-text-primary">Send this command</strong> in the DM:</span></li>
              </ol>
            )}
            {!['feishu','discord'].includes(channel.id) && (
              <p className="text-xs text-text-muted">Open {channel.label}, find <strong className="text-text-primary">@{session.botUsername || 'ACME Agent'}</strong>, and send:</p>
            )}
            <div className="flex items-center gap-2 mt-2">
              <code className="flex-1 text-sm font-mono text-primary-light bg-primary/10 px-3 py-2.5 rounded-lg block text-center">
                /start {session.token}
              </code>
              <button onClick={() => navigator.clipboard?.writeText(`/start ${session.token}`)}
                className="flex-shrink-0 px-2.5 py-2.5 rounded-lg bg-dark-hover text-text-muted hover:text-text-primary text-[10px] border border-dark-border/40 transition-colors">
                Copy
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="rounded-lg bg-info/5 border border-info/20 px-3 py-2.5 flex items-start gap-2">
        <Loader2 size={14} className="animate-spin text-info mt-0.5 flex-shrink-0" />
        <p className="text-xs text-info">
          {channel.id === 'feishu' ? 'Waiting… After sending the command in Feishu, this page will update automatically.' :
           channel.id === 'discord' ? 'Waiting… After sending the command in Discord DM, this page will update automatically.' :
           `Waiting for you to connect… tap Start in ${channel.label} to complete.`}
        </p>
      </div>

      <Button variant="ghost" className="w-full text-xs" onClick={onCancel}>Cancel</Button>
    </div>
  );

  if (state === 'done') return (
    <div className="flex flex-col items-center py-8 gap-3 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-success/10">
        <CheckCircle size={36} className="text-success" />
      </div>
      <h3 className="text-base font-semibold text-text-primary">Connected!</h3>
      <p className="text-sm text-text-muted">Your {channel.label} is now linked to your Agent.</p>
    </div>
  );

  if (state === 'expired') return (
    <div className="space-y-4 text-center">
      <div className="flex flex-col items-center py-4 gap-2">
        <AlertCircle size={32} className="text-warning" />
        <p className="text-sm text-text-muted">Link expired. Please generate a new one.</p>
      </div>
      <Button variant="primary" className="w-full" onClick={startPairing}><RefreshCw size={14} /> Try Again</Button>
      <Button variant="ghost" className="w-full" onClick={onCancel}>Back</Button>
    </div>
  );

  return (
    <div className="space-y-4 text-center py-4">
      <AlertCircle size={32} className="text-danger mx-auto" />
      <p className="text-sm text-danger">{error || 'Something went wrong'}</p>
      <Button variant="ghost" className="w-full" onClick={onCancel}>Back</Button>
    </div>
  );
}

function GatewayConsoleButton() {
  const [loading, setLoading] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    if (countdown <= 0) return;
    const t = setInterval(() => setCountdown(c => Math.max(0, c - 1)), 1000);
    return () => clearInterval(t);
  }, [countdown]);

  const handleClick = async () => {
    setLoading(true);
    setError('');
    setCountdown(20);
    try {
      const jwt = localStorage.getItem('openclaw_token') || '';
      const resp = await fetch('/api/v1/portal/gateway/dashboard', {
        headers: { 'Authorization': `Bearer ${jwt}` },
      });
      const data = await resp.json();
      if (data.available && data.gatewayToken) {
        const isEks = data.deployMode === 'eks';
        // EKS: always use proxy (in-cluster DNS, no direct URL).
        // ECS: prefer direct URL (EC2 public IP:8098) for WebSocket support.
        const gwUrl = isEks
          ? `/api/v1/portal/gateway/ui/`
          : (data.directUrl || `/api/v1/portal/gateway/ui/`);
        const authParam = isEks ? `auth_token=${jwt}` : `token=${data.gatewayToken}`;
        const url = `${gwUrl}?${authParam}${data.dashboardToken ? '#token=' + data.dashboardToken : ''}`;
        window.open(url, '_blank');
        // Auto-approve device pairing: the browser creates a pending pairing
        // request when it connects to the Gateway Console. Poll to approve it.
        const approveHeaders = { 'Authorization': `Bearer ${jwt}` };
        for (let i = 0; i < 5; i++) {
          await new Promise(r => setTimeout(r, 3000));
          try {
            const ar = await fetch('/api/v1/portal/gateway/approve-pairing', {
              method: 'POST', headers: approveHeaders,
            });
            const ad = await ar.json();
            if (ad.approved) break;
          } catch {}
        }
      } else {
        setError(data.reason || 'Gateway Console not available');
      }
    } catch (e) {
      setError('Failed to connect to Gateway Console');
    } finally {
      setLoading(false);
      setCountdown(0);
    }
  };

  return (
    <div className="mt-3 space-y-2">
      <button
        className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90 transition-colors disabled:opacity-60"
        onClick={handleClick}
        disabled={loading}
      >
        {loading ? (
          <>
            <Loader2 size={14} className="animate-spin" />
            Connecting to Gateway... {countdown > 0 && `(${countdown}s)`}
          </>
        ) : (
          <>
            <Zap size={14} /> Open Gateway Console
          </>
        )}
      </button>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}

export default function BindIM() {
  const [selected, setSelected] = useState<Channel | null>(null);
  const [connected, setConnected] = useState<string[]>([]);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState<string | null>(null);
  const [channelInfo, setChannelInfo] = useState<any>(null);
  const [adminConfigured, setAdminConfigured] = useState<string[]>([]);
  const connectedRef = useRef<string[]>([]);

  // Fetch which channels admin has configured via OpenClaw Gateway
  useEffect(() => {
    api.get<{ configured: string[] }>('/portal/im-channel-status')
      .then(d => setAdminConfigured(d.configured || []))
      .catch(() => {});
  }, []);

  const fetchChannels = useCallback(() => {
    api.get<any>('/portal/channels').then(d => {
      if (d?.connected) {
        connectedRef.current = d.connected;
        setConnected(d.connected);
      }
      setChannelInfo(d);
    }).catch(() => {});
  }, []);

  // Initial fetch
  useEffect(() => { fetchChannels(); }, [fetchChannels]);

  // Background poll every 5s when wizard is open — detects completion even if
  // the wizard's own polling misses the event (e.g. user switched tabs)
  useEffect(() => {
    if (!selected) return;
    const t = setInterval(fetchChannels, 5000);
    return () => clearInterval(t);
  }, [selected, fetchChannels]);

  const handleDone = useCallback((channelId: string) => {
    setConnected(prev => [...prev.filter(c => c !== channelId), channelId]);
    setSelected(null);
    // Re-fetch to confirm server-side status
    setTimeout(fetchChannels, 500);
  }, [fetchChannels]);

  const handleDisconnect = useCallback(async (channelId: string) => {
    setDisconnecting(channelId);
    try {
      await api.del(`/portal/channels/${channelId}`);
      setConnected(prev => prev.filter(c => c !== channelId));
    } catch {}
    setDisconnecting(null);
    setConfirmDisconnect(null);
  }, []);

  if (selected) return (
    <div className="max-w-sm mx-auto p-6">
      <ChannelWizard
        channel={selected}
        onDone={() => handleDone(selected.id)}
        onCancel={() => setSelected(null)}
      />
    </div>
  );

  const deployMode = channelInfo?.deployMode || 'serverless';
  const pairingMode = channelInfo?.pairingMode || 'shared-gateway';
  const instructions = channelInfo?.pairingInstructions || {};

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text-primary">Connect IM Channels</h1>
        <p className="text-sm text-text-muted mt-1">
          Link your messaging apps so your AI Agent can respond directly in your favorite chat.
        </p>
      </div>

      {/* Mode Banner */}
      <div className={`rounded-xl border px-4 py-3 flex items-start gap-3 ${
        deployMode === 'always-on-ecs' || deployMode === 'eks'
          ? 'bg-primary/5 border-primary/20'
          : 'bg-surface-dim border-dark-border/40'
      }`}>
        {deployMode === 'always-on-ecs' || deployMode === 'eks' ? (
          <Zap size={16} className="text-primary mt-0.5 flex-shrink-0" />
        ) : (
          <Radio size={16} className="text-text-muted mt-0.5 flex-shrink-0" />
        )}
        <div className="flex-1">
          <p className="text-sm font-medium text-text-primary">
            {deployMode === 'always-on-ecs' ? '⚡ Always-on (ECS)' : deployMode === 'eks' ? '☸ Always-on (EKS)' : 'Serverless'}
          </p>
          <p className="text-xs text-text-muted mt-0.5">
            {instructions.mode_note || (
              deployMode === 'always-on-ecs' || deployMode === 'eks'
                ? 'Your agent runs 24/7. Open the Gateway Console below to manage your IM connections directly.'
                : 'Your agent starts on demand. Connect via the company bot below.'
            )}
          </p>
          {(deployMode === 'always-on-ecs' || deployMode === 'eks') && (
            <GatewayConsoleButton />
          )}
        </div>
      </div>

      {/* Serverless: show channel pairing cards. Always-on: show status only */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {CHANNELS.map(ch => {
          const isConnected = connected.includes(ch.id);
          const isAvailable = adminConfigured.includes(ch.id);
          // Stale: employee has an old binding but admin removed/never configured the bot
          const isStale = isConnected && !isAvailable;
          return (
            <Card key={ch.id} className={`transition-all ${
              isAvailable ? 'cursor-pointer hover:border-primary/40' : 'opacity-60'
            }`}>
              <div className="flex items-start gap-3">
                <div className="flex-shrink-0 mt-0.5">{(() => { const Icon = IM_ICONS[ch.id]; return Icon ? <Icon size={28} /> : null; })()}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                    <h3 className="text-sm font-semibold text-text-primary">{ch.label}</h3>
                    {isAvailable && isConnected && <Badge color="success" dot>Connected</Badge>}
                    {isStale && <Badge color="warning">Bot removed</Badge>}
                    {!isAvailable && !isConnected && <Badge color="default">Admin not configured</Badge>}
                  </div>
                  <p className="text-xs text-text-muted">
                    {isStale
                      ? 'This channel is no longer active. Contact IT admin or disconnect to clean up.'
                      : isAvailable
                        ? (instructions[ch.id] || ch.description)
                        : 'Contact your IT admin to enable this channel.'}
                  </p>
                </div>
              </div>
              {/* Stale: show disconnect to clean up the dead binding */}
              {isStale && (
                <div className="mt-3">
                  {confirmDisconnect === ch.id ? (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-danger flex-1">Remove stale binding?</span>
                      <Button variant="danger" size="sm" disabled={disconnecting === ch.id}
                        onClick={() => handleDisconnect(ch.id)}>
                        {disconnecting === ch.id ? <Loader2 size={12} className="animate-spin" /> : 'Confirm'}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setConfirmDisconnect(null)}>Cancel</Button>
                    </div>
                  ) : (
                    <Button variant="ghost" size="sm" className="w-full text-text-muted text-xs"
                      onClick={() => setConfirmDisconnect(ch.id)}>
                      Remove binding
                    </Button>
                  )}
                </div>
              )}
              {/* Normal: available channel — connect / reconnect / disconnect */}
              {isAvailable && (
                <div className="mt-3 space-y-1.5">
                  {isConnected ? (
                    <>
                      {confirmDisconnect === ch.id ? (
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-danger flex-1">Disconnect {ch.label}?</span>
                          <Button variant="danger" size="sm" disabled={disconnecting === ch.id}
                            onClick={() => handleDisconnect(ch.id)}>
                            {disconnecting === ch.id ? <Loader2 size={12} className="animate-spin" /> : 'Confirm'}
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => setConfirmDisconnect(null)}>Cancel</Button>
                        </div>
                      ) : (
                        <div className="flex gap-2">
                          <Button variant="ghost" size="sm" className="flex-1 text-xs" onClick={() => setSelected(ch)}>
                            Reconnect
                          </Button>
                          <Button variant="ghost" size="sm"
                            className="text-text-muted hover:text-danger hover:border-danger/30"
                            onClick={() => setConfirmDisconnect(ch.id)}>
                            Disconnect
                          </Button>
                        </div>
                      )}
                    </>
                  ) : (
                    <Button variant="primary" size="sm" className="w-full" onClick={() => setSelected(ch)}>
                      <Link2 size={13} /> Connect
                    </Button>
                  )}
                </div>
              )}
            </Card>
          );
        })}
      </div>

      <div className="rounded-lg bg-dark-bg border border-dark-border/40 px-4 py-3 text-xs text-text-muted">
        All connections are managed by your IT Admin and can be revoked at any time.
        Your messages are routed to your personal AI Agent only.
      </div>
    </div>
  );
}
