import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { getOidcManager } from '../config/oidcClient';
import ClawForgeLogo from '../components/ClawForgeLogo';

/**
 * OIDC 回调页。IdP 重定向到 /sso/callback?code=xxx&state=yyy 后在此处理:
 *   1. oidc-client-ts 用 code + PKCE verifier 换 id_token
 *   2. 调 /auth/me 拉员工信息
 *   3. 按角色跳转
 *   4. 失败携带 error 参数跳回 /login
 */
export default function SsoCallback() {
  const navigate = useNavigate();
  const { completeSsoLogin } = useAuth();
  const [status, setStatus] = useState<'processing' | 'error'>('processing');
  const [errorDetail, setErrorDetail] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const um = await getOidcManager();
        if (!um) {
          if (!cancelled) navigate('/login?error=sso_failed', { replace: true });
          return;
        }
        // 这步完成 code → id_token 交换,并把 OidcUser 存进 sessionStorage
        const oidcUser = await um.signinRedirectCallback();
        if (cancelled) return;

        if (!oidcUser || !oidcUser.id_token) {
          navigate('/login?error=sso_failed', { replace: true });
          return;
        }

        // 拉员工信息 (如果后端找不到邮箱对应员工会 401)
        const me = await completeSsoLogin();
        if (cancelled) return;

        if (!me) {
          // email 匹配失败: 从 id_token 解析 email 给用户看
          let email = '';
          try {
            const payload = JSON.parse(atob(oidcUser.id_token.split('.')[1]));
            email = payload.email || payload.preferred_username || payload.upn || '';
          } catch { /* ignore */ }
          await um.removeUser();
          navigate(`/login?error=email_not_found&email=${encodeURIComponent(email)}`, { replace: true });
          return;
        }

        navigate(me.role === 'employee' ? '/portal' : '/dashboard', { replace: true });
      } catch (e: any) {
        if (cancelled) return;
        setStatus('error');
        setErrorDetail(e?.message || 'SSO callback failed');
      }
    })();
    return () => { cancelled = true; };
  }, [completeSsoLogin, navigate]);

  if (status === 'error') {
    return (
      <div className="min-h-screen bg-dark-bg flex items-center justify-center p-4">
        <div className="text-center max-w-md">
          <h2 className="text-lg font-semibold text-text-primary mb-2">Sign-in failed</h2>
          <p className="text-sm text-text-muted mb-6">{errorDetail}</p>
          <button
            onClick={() => navigate('/login', { replace: true })}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary/90"
          >
            Back to login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-dark-bg flex items-center justify-center">
      <div className="text-center">
        <ClawForgeLogo size={48} animate="working" />
        <p className="text-sm text-text-muted mt-3">Completing sign-in…</p>
      </div>
    </div>
  );
}
