import {
  createContext, useContext, useState, useEffect, useCallback, ReactNode,
} from 'react';
import { getOidcManager } from '../config/oidcClient';

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
  loginWithSso: () => Promise<void>;
  loginWithPassword: (employeeId: string, password: string) => Promise<void>;
  completeSsoLogin: () => Promise<AuthUser | null>;
  logout: () => Promise<void>;
  updateToken: (newToken: string) => void;
  getAccessToken: () => Promise<string | null>;
  isAdmin: boolean;
  isManager: boolean;
  isEmployee: boolean;
}

const AuthContext = createContext<AuthContextType>({
  user: null, loading: true, authMode: null,
  loginWithSso: async () => {},
  loginWithPassword: async () => {},
  completeSsoLogin: async () => null,
  logout: async () => {},
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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [authMode, setAuthMode] = useState<AuthMode>(null);

  /** 统一 token 获取器,api/client.ts 通过 window.__openclaw_getToken 调用 */
  const getAccessToken = useCallback(async (): Promise<string | null> => {
    if (authMode === 'sso') {
      const um = await getOidcManager();
      if (!um) return null;
      const oidcUser = await um.getUser();
      if (!oidcUser || oidcUser.expired) return null;
      return oidcUser.id_token ?? null;
    }
    return localStorage.getItem('openclaw_token');
  }, [authMode]);

  useEffect(() => {
    (window as any).__openclaw_getToken = getAccessToken;
    return () => { delete (window as any).__openclaw_getToken; };
  }, [getAccessToken]);

  /** 启动时恢复 session: 先看 OIDC,再看本地 JWT */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. 尝试 OIDC session (sessionStorage)
      try {
        const um = await getOidcManager();
        if (um) {
          const oidcUser = await um.getUser();
          if (oidcUser && !oidcUser.expired && oidcUser.id_token) {
            const me = await fetchMe(oidcUser.id_token);
            if (me && !cancelled) {
              setUser(me);
              setAuthMode('sso');
              setLoading(false);
              return;
            }
          }
        }
      } catch { /* ignore */ }

      // 2. 尝试本地 JWT
      const saved = localStorage.getItem('openclaw_token');
      if (saved) {
        const me = await fetchMe(saved);
        if (me && !cancelled) {
          setUser(me);
          setAuthMode('local');
          setLoading(false);
          return;
        }
        localStorage.removeItem('openclaw_token');
      }

      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, []);

  /** SSO 登录入口 (Login 页按钮点击或 autoRedirect 触发) */
  const loginWithSso = async () => {
    const um = await getOidcManager();
    if (!um) {
      throw new Error('SSO is not configured. Contact your administrator.');
    }
    await um.signinRedirect();
  };

  /** SsoCallback 页面在 signinRedirectCallback 完成后调用,拉 /auth/me 填充 user */
  const completeSsoLogin = async (): Promise<AuthUser | null> => {
    const um = await getOidcManager();
    if (!um) return null;
    const oidcUser = await um.getUser();
    if (!oidcUser || !oidcUser.id_token) return null;
    const me = await fetchMe(oidcUser.id_token);
    if (me) {
      setUser(me);
      setAuthMode('sso');
    }
    return me;
  };

  /** 本地密码登录 */
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
    setUser({ ...data.employee as AuthUser, mustChangePassword: data.mustChangePassword ?? false });
    setAuthMode('local');
  };

  /** 改密后签发的新 token */
  const updateToken = (newToken: string) => {
    localStorage.setItem('openclaw_token', newToken);
    try {
      const payload = JSON.parse(atob(newToken.split('.')[1]));
      setUser(prev => prev ? { ...prev, mustChangePassword: payload.mustChangePassword ?? false } : prev);
    } catch { /* ignore */ }
  };

  const logout = async () => {
    if (authMode === 'sso') {
      const um = await getOidcManager();
      setUser(null);
      setAuthMode(null);
      if (um) {
        try {
          await um.removeUser();
          await um.signoutRedirect();
          return;
        } catch {
          // signoutRedirect 可能失败 (IdP 未配 end_session_endpoint),回退到本地清理
        }
      }
      window.location.href = '/login';
      return;
    }
    // 本地登录
    localStorage.removeItem('openclaw_token');
    setUser(null);
    setAuthMode(null);
  };

  return (
    <AuthContext.Provider value={{
      user, loading, authMode,
      loginWithSso, loginWithPassword, completeSsoLogin,
      logout, updateToken, getAccessToken,
      isAdmin: user?.role === 'admin',
      isManager: user?.role === 'manager',
      isEmployee: user?.role === 'employee',
    }}>
      {children}
    </AuthContext.Provider>
  );
}
