/**
 * 通用 OIDC 客户端封装。
 *
 * 与后端 /api/v1/public/sso/config 端点配合,运行时读取管理员在 Settings
 * 配置的 IdP 参数,按需构造 oidc-client-ts 的 UserManager 单例。
 *
 * 不使用 client_secret (PKCE 公共客户端),token 存 sessionStorage。
 */
import { UserManager, WebStorageStateStore } from 'oidc-client-ts';

export interface SsoPublicConfig {
    enabled: boolean;
    issuer: string;
    clientId: string;
    scopes: string;
    autoRedirect: boolean;
}

let _cachedConfig: SsoPublicConfig | null = null;
let _cachedManager: UserManager | null = null;
let _configPromise: Promise<SsoPublicConfig | null> | null = null;

export async function getSsoConfig(): Promise<SsoPublicConfig | null> {
    if (_cachedConfig) return _cachedConfig;
    if (_configPromise) return _configPromise;

    _configPromise = (async () => {
        try {
            const resp = await fetch('/api/v1/public/sso/config');
            if (!resp.ok) return null;
            const cfg = (await resp.json()) as SsoPublicConfig;
            if (cfg && cfg.enabled && cfg.issuer && cfg.clientId) {
                _cachedConfig = cfg;
                return cfg;
            }
            return null;
        } catch {
            return null;
        } finally {
            _configPromise = null;
        }
    })();
    return _configPromise;
}

export async function getOidcManager(): Promise<UserManager | null> {
    if (_cachedManager) return _cachedManager;
    const cfg = await getSsoConfig();
    if (!cfg) return null;

    _cachedManager = new UserManager({
        authority: cfg.issuer,
        client_id: cfg.clientId,
        redirect_uri: `${window.location.origin}/sso/callback`,
        post_logout_redirect_uri: `${window.location.origin}/login`,
        response_type: 'code',
        scope: cfg.scopes || 'openid profile email',
        userStore: new WebStorageStateStore({ store: window.sessionStorage }),
        stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
        automaticSilentRenew: false,
    });
    return _cachedManager;
}

/** Settings 保存后主动清除本地缓存,强制下次重新读配置。 */
export function clearOidcCache(): void {
    _cachedConfig = null;
    _cachedManager = null;
    _configPromise = null;
}
