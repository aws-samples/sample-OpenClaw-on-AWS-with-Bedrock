import {
  createContext, useContext, useState, useEffect, useCallback, ReactNode,
} from 'react';

export interface AuthUser {
  id: string;
  name: string;
  role: 'admin' | 'manager' | 'employee';
  departmentId: string;
  departmentName: string;
  positionId: string;
  positionName: string;
  agentId?: string;
  channels?: string[];
  email?: string;
  mustChangePassword?: boolean;
}

type AuthMode = 'sso' | 'local' | null;

interface AuthContextType {
  user: AuthUser | null;
  loading: boolean;
  authMode: AuthMode;
  loginWithSso: () => void;
  loginWithPassword: (employeeId: string, password: string) => Promise<void>;
  logout: () => void;
  updateToken: (newToken: string) => void;
  getAccessToken: () => Promise<string | null>;
  isAdmin: boolean;
  isManager: boolean;
  isEmployee: boolean;
}

const AuthContext = createContext<AuthContextType>({
  user: null, loading: true, authMode: null,
  loginWithSso: () => {},
  loginWithPassword: async () => {},
  logout: () => {},
  updateToken: () => {},
  getAccessToken: async () => null,
  isAdmin: false, isManager: false, isEmployee: false,
});

export function useAuth() { return useContext(AuthContext); }

async function fetchMe(token: string): Promise<AuthUser | null> {
  try {
    const resp = await fetch('/api/v1/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.ok) return await resp.json() as AuthUser;
  } catch { /* ignore */ }
  return null;
}

/**
 * 尝试从 JWT payload 的 "iss" 字段判断它是 SSO 登录得到的还是密码登录得到的。
 * 目前后端签发的本地 JWT 不带 "iss"(兼容旧行为),所以此处用 "ssoLogin" 标记兜底。
 *
 * 两种登录最终都是同一把 HS256 JWT,只是 authMode 显示语义不同 (Settings/Account tab)。
 * 所以这里的判断结果不影响任何安全边界,只是 UI 展示语义。
 */
function detectAuthMode(): AuthMode {
  const marker = localStorage.getItem('openclaw_auth_mode');
  if (marker === 'sso' || marker === 'local') return marker;
  return 'local';  // 默认语义 (密码登录是历史基线)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [authMode, setAuthMode] = useState<AuthMode>(null);

  const getAccessToken = useCallback(async (): Promise<string | null> => {
    return localStorage.getItem('openclaw_token');
  }, []);

  useEffect(() => {
    (window as any).__openclaw_getToken = getAccessToken;
    return () => { delete (window as any).__openclaw_getToken; };
  }, [getAccessToken]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const saved = localStorage.getItem('openclaw_token');
      if (saved) {
        const me = await fetchMe(saved);
        if (me && !cancelled) {
          setUser(me);
          setAuthMode(detectAuthMode());
          setLoading(false);
          return;
        }
        localStorage.removeItem('openclaw_token');
        localStorage.removeItem('openclaw_auth_mode');
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, []);

  /** SSO 登录 — 浏览器直接跳转到后端,由后端完成 OAuth 流程后带本地 JWT 回来
   *
   * 传 ?origin= 参数给后端,是为了在 CloudFront/ALB 等反向代理场景下确保
   * redirect_uri 使用浏览器实际可达的域名(而不是后端猜测出来的内部域名)。
   */
  const loginWithSso = () => {
    localStorage.setItem('openclaw_auth_mode', 'sso');
    const origin = encodeURIComponent(window.location.origin);
    window.location.href = `/api/v1/auth/sso/login?origin=${origin}`;
  };

  const loginWithPassword = async (employeeId: string, password: string) => {
    const resp = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ employeeId, password }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Login failed' }));
      throw new Error(err.detail || 'Login failed');
    }
    const data = await resp.json();
    localStorage.setItem('openclaw_token', data.token);
    localStorage.setItem('openclaw_auth_mode', 'local');
    setUser({ ...data.employee as AuthUser, mustChangePassword: data.mustChangePassword ?? false });
    setAuthMode('local');
  };

  const updateToken = (newToken: string) => {
    localStorage.setItem('openclaw_token', newToken);
    try {
      const payload = JSON.parse(atob(newToken.split('.')[1]));
      setUser(prev => prev ? { ...prev, mustChangePassword: payload.mustChangePassword ?? false } : prev);
    } catch { /* ignore */ }
  };

  const logout = () => {
    localStorage.removeItem('openclaw_token');
    localStorage.removeItem('openclaw_auth_mode');
    setUser(null);
    setAuthMode(null);
    window.location.href = '/login';
  };

  return (
    <AuthContext.Provider value={{
      user, loading, authMode,
      loginWithSso, loginWithPassword,
      logout, updateToken, getAccessToken,
      isAdmin: user?.role === 'admin',
      isManager: user?.role === 'manager',
      isEmployee: user?.role === 'employee',
    }}>
      {children}
    </AuthContext.Provider>
  );
}
