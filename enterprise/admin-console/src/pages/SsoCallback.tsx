import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import ClawForgeLogo from '../components/ClawForgeLogo';

/**
 * SSO 登录成功回调页 (BFF 模式):
 *   1. 后端 302 到 /login/sso-success#token=<本地 HS256 JWT>
 *   2. 此页面从 URL hash 读 token 存 localStorage
 *   3. 解析 JWT payload 里的 role,按角色跳 /portal 或 /dashboard
 *   4. 失败跳 /login?error=...
 *
 * 注意: 路由路径是 /login/sso-success (非 /sso/callback),避免和 IdP 原 callback
 *       路径冲突 — 后端真正的 OAuth callback 是 /api/v1/auth/sso/callback。
 */
export default function SsoCallback() {
  const navigate = useNavigate();

  useEffect(() => {
    // 从 URL hash 读 token (格式: #token=xxx)
    const hash = window.location.hash;
    if (!hash || !hash.startsWith('#token=')) {
      navigate('/login?error=no_token', { replace: true });
      return;
    }
    const token = hash.substring('#token='.length);
    if (!token) {
      navigate('/login?error=no_token', { replace: true });
      return;
    }

    // 存本地存储 + 立刻清 URL hash 避免刷新重入
    localStorage.setItem('openclaw_token', token);
    window.history.replaceState(null, '', '/login/sso-success');

    // 解析 payload 决定跳哪里
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      if (payload.mustChangePassword) {
        navigate('/change-password', { replace: true });
      } else if (payload.role === 'employee') {
        navigate('/portal', { replace: true });
      } else {
        navigate('/dashboard', { replace: true });
      }
      // 强制 reload,让 AuthContext 重新 bootstrap 读取 localStorage
      window.location.reload();
    } catch {
      navigate('/login?error=invalid_token', { replace: true });
    }
  }, [navigate]);

  return (
    <div className="min-h-screen bg-dark-bg flex items-center justify-center">
      <div className="text-center">
        <ClawForgeLogo size={48} animate="working" />
        <p className="text-sm text-text-muted mt-3">Completing sign-in…</p>
      </div>
    </div>
  );
}
