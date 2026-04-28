import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { getSsoConfig } from '../config/oidcClient';
import { AlertCircle, LogIn, KeyRound } from 'lucide-react';
import ClawForgeLogo from '../components/ClawForgeLogo';

export default function Login() {
  const { loginWithSso, loginWithPassword, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [empId, setEmpId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(false);
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [ssoChecked, setSsoChecked] = useState(false);
  const autoTriggered = useRef(false);

  // Callback 页可能带 error query 回到 /login,显示给用户
  useEffect(() => {
    const err = searchParams.get('error');
    if (err === 'email_not_found') {
      const email = searchParams.get('email') || '';
      setError(
        email
          ? `SSO 登录成功,但未找到邮箱为 ${email} 的员工。请联系管理员。`
          : 'SSO 登录成功,但未找到对应员工。请联系管理员。'
      );
    } else if (err === 'sso_failed') {
      setError('SSO 登录失败。请重试或使用密码登录。');
    }
  }, [searchParams]);

  // 已登录自动跳走
  useEffect(() => {
    if (user) {
      navigate(user.role === 'employee' ? '/portal' : '/dashboard', { replace: true });
    }
  }, [user, navigate]);

  // 拉公开 SSO 配置,决定是否显示按钮 + 是否自动触发
  useEffect(() => {
    let cancelled = false;
    getSsoConfig()
      .then(cfg => {
        if (cancelled) return;
        const enabled = !!cfg?.enabled;
        setSsoEnabled(enabled);
        setSsoChecked(true);
        // IdP-Initiated: autoRedirect=true 且无 error query → 自动跳转
        if (
          enabled &&
          cfg?.autoRedirect &&
          !searchParams.get('error') &&
          !autoTriggered.current
        ) {
          autoTriggered.current = true;
          handleSsoLogin();
        }
      })
      .catch(() => {
        if (!cancelled) setSsoChecked(true);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSsoLogin = async () => {
    setSsoLoading(true);
    setError('');
    try {
      await loginWithSso();
    } catch (e: any) {
      setError(e.message || 'SSO sign-in failed');
      setSsoLoading(false);
    }
  };

  const handlePasswordLogin = async () => {
    if (!empId || !password) return;
    setLoading(true);
    setError('');
    try {
      await loginWithPassword(empId, password);
      const saved = localStorage.getItem('openclaw_token');
      if (saved) {
        const payload = JSON.parse(atob(saved.split('.')[1]));
        if (payload.mustChangePassword) navigate('/change-password');
        else if (payload.role === 'employee') navigate('/portal');
        else navigate('/dashboard');
      }
    } catch (e: any) {
      setError(e.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-dark-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex mb-4"><ClawForgeLogo size={56} animate="idle" /></div>
          <h1 className="text-2xl font-bold text-text-primary">OpenClaw Enterprise</h1>
          <p className="text-sm text-text-muted mt-1">on AgentCore - aws-samples</p>
        </div>

        <div className="rounded-xl border border-dark-border bg-dark-card p-6 mb-6">
          <h2 className="text-lg font-semibold text-text-primary mb-4">Sign In</h2>

          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 mb-4">
              <AlertCircle size={16} className="text-red-400 mt-0.5 flex-shrink-0" />
              <span className="text-sm text-red-400">{error}</span>
            </div>
          )}

          {/* SSO 登录按钮 (配置启用时显示) */}
          {ssoEnabled && (
            <>
              <button
                onClick={handleSsoLogin}
                disabled={ssoLoading}
                className="w-full flex items-center justify-center gap-3 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                <KeyRound size={18} />
                {ssoLoading ? 'Redirecting…' : 'Sign in with SSO'}
              </button>
              <p className="text-xs text-text-muted text-center mt-2">
                IDaaS · Azure AD · Okta · Keycloak
              </p>

              <div className="flex items-center gap-3 my-5">
                <div className="flex-1 h-px bg-dark-border" />
                <span className="text-xs text-text-muted">or sign in with password</span>
                <div className="flex-1 h-px bg-dark-border" />
              </div>
            </>
          )}

          {/* 本地密码登录 */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm text-text-muted mb-1">Employee ID</label>
              <input
                type="text"
                value={empId}
                onChange={e => setEmpId(e.target.value)}
                placeholder="emp-jiade or EMP-030"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm text-text-muted mb-1">Password</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && empId && password && handlePasswordLogin()}
                placeholder="Enter password"
                className="w-full rounded-lg border border-dark-border bg-dark-bg px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
              />
            </div>
            <button
              onClick={handlePasswordLogin}
              disabled={!empId || !password || loading}
              className="w-full flex items-center justify-center gap-2 rounded-lg bg-surface-dim border border-dark-border px-4 py-2.5 text-sm font-medium text-text-primary hover:bg-surface-raised disabled:opacity-50 transition-colors"
            >
              <LogIn size={16} /> {loading ? 'Signing in…' : 'Sign In'}
            </button>
          </div>

          {!ssoChecked && (
            <p className="text-xs text-text-muted text-center mt-4">Checking SSO availability…</p>
          )}
        </div>

        <div className="text-center mt-6">
          <p className="text-xs text-text-muted">
            Built by <a href="mailto:wjiad@amazon.com" className="text-primary-light hover:underline">wjiad@aws</a> - Contributions welcome
          </p>
        </div>
      </div>
    </div>
  );
}
