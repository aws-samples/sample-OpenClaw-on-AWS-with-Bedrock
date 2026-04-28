# 通用 OIDC SSO 登录设计方案

> 用一个通用方案替代当前 Azure AD 专用实现，支持**任何符合 OpenID Connect 标准的外部身份提供方**（External IdP）。
> 涵盖阿里云 IDaaS、Azure AD、Okta、Keycloak、飞书、腾讯 TCIC 等。
>
> **状态**:Phase 1 实施完成,待真人/IdP 联测
> **作者**:—
> **最后更新**:2026-04-28

---

## 1. 背景与目标

### 1.1 现状

当前 `enterprise/admin-console` 已实现 Azure AD SSO（PR #87/#92），特点：

- 后端 `auth.py` 用 `_verify_azure_token` 硬编码 Microsoft JWKS / Issuer
- 前端用 `@azure/msal-browser` + `@azure/msal-react`
- 配置走环境变量 `AZURE_TENANT_ID / AZURE_CLIENT_ID`
- 登录页有固定的 "Sign in with Microsoft" 按钮

### 1.2 问题

- **只支持 Azure AD**，客户如果用阿里云 IDaaS / Okta / Keycloak 等其他 IdP 无法接入
- **env 配置**要求客户部署后 SSH 改 `.env` 并重新构建前端（`VITE_AZURE_*` 是构建期变量）
- 代码里有大量 "Azure AD 专属" 分支，扩展第二家 IdP 成本高

### 1.3 目标

| 目标 | 落地 |
|---|---|
| 支持任意 OIDC 兼容的 IdP | 后端一个通用 `_verify_oidc_token`，前端一个 `oidc-client-ts` |
| 企业管理员自助配置 | Settings 页新增 SSO tab，存 DynamoDB `CONFIG#sso` |
| 支持 IdP-Initiated 跳转 | `autoRedirect` 开关 + `initiate_login_uri` 约定 |
| 保留本地密码兜底 | `HS256` 本地 JWT 路径完全不动 |
| 移除 Azure AD 专用代码 | 迁移到通用 OIDC 路径（Azure AD 作为 OIDC Provider 配置即可） |

### 1.4 非目标

- **不做多租户 SaaS**。每个企业部署自己独立的 OpenClaw 实例，一套实例对接一家 IdP。
- **不做多 IdP 并存**。一套 OpenClaw 同一时间只启用一个 SSO Provider。
- **不做 refresh token 管理**。id_token 过期让 oidc-client-ts 自动重登。
- **不做自助绑定兜底页**。email 匹配不到直接友好报错，管理员去员工表补 email。

---

## 2. 架构总览

```
┌──────────────── OpenClaw 企业部署（单家企业使用）────────────────┐
│                                                                  │
│  企业管理员 (一次性配置):                                          │
│    ① 在 IdP 控制台创建 OIDC 应用                                   │
│       - Redirect URI: https://portal.example.com/sso/callback   │
│       - Initiate Login URI: https://portal.example.com/login    │
│       - 拿到 Issuer + Client ID                                   │
│    ② 在 OpenClaw Settings → SSO tab 填入                          │
│       - Issuer / Client ID / Scopes / Enabled / Auto Redirect    │
│    ③ 保存 → DynamoDB CONFIG#sso                                   │
│                                                                  │
│  员工登录 (3 种场景):                                              │
│    SP-Initiated: 打开 Portal → 点 SSO 按钮 → IdP → 回调 → Portal  │
│    IdP-Initiated: 在 IdP 工作台点图标 → Portal → 自动 SSO → 进入   │
│    本地密码: 填 employeeId + password → 签本地 JWT → 进入         │
│                                                                  │
│  后端 Token 分流:                                                  │
│    HS256 → _verify_local_token                                   │
│    RS256 → _verify_oidc_token (按 CONFIG#sso 的 issuer 验签)      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 核心设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| Token 是否转换 | **不转换**，前端直接用 IdP 的 id_token 调 API | 和现有 Azure AD 实现一致；避免转换引入的复杂度 |
| 员工匹配键 | **email** | 企业邮箱全局唯一且稳定；OIDC 标准 claim |
| 配置存储 | **DynamoDB `CONFIG#sso`** | 客户自助、运行时读取、不用重启 |
| 前端存储 | **sessionStorage** | 安全（关标签页即清），配合 IdP session 静默续期 |
| 本地 JWT | **完全保留** | 兜底路径，SSO 故障不锁死管理员 |
| 客户端类型 | **Public Client + PKCE**（不用 client_secret） | SPA 标准做法，浏览器本来就存不住机密 |

---

## 3. 数据模型

### 3.1 DynamoDB 新增 `CONFIG#sso` 条目

```json
{
  "PK": "ORG#acme",
  "SK": "CONFIG#sso",
  "enabled": true,
  "issuer": "https://xxx.aliyunidaas.com/api/v2/iauths_system/oauth2",
  "clientId": "app_xxxxxxxxxx",
  "scopes": "openid profile email",
  "autoRedirect": false,
  "updatedAt": "2026-04-28T10:00:00Z",
  "updatedBy": "emp-admin"
}
```

| 字段 | 含义 |
|---|---|
| `enabled` | SSO 开关，false 时前端不显示 SSO 按钮 |
| `issuer` | IdP 的 OIDC Issuer URL，用于发现 `.well-known/openid-configuration` |
| `clientId` | IdP 控制台分配的应用 ID |
| `scopes` | 请求的权限范围，默认 `openid profile email` |
| `autoRedirect` | IdP-Initiated 场景，`/login` 页加载时自动触发 SSO |

**刻意不存的字段**：
- `clientSecret` —— PKCE 公共客户端不需要
- `jwksUri / authorizeUrl / tokenUrl` —— 从 `issuer/.well-known/openid-configuration` 自动发现

### 3.2 `EMP#.email` 字段（复用现有）

Azure AD 实现已引入，不再改动。seed 脚本里给 demo 员工按 `{id去 emp- 前缀}@acme.com` 规则生成。

### 3.3 不新增临时条目

OAuth `state` / PKCE `code_verifier` 由 oidc-client-ts 在浏览器 sessionStorage 管理。后端完全无状态。

---

## 4. 后端 Token 验证流程

```
收到 Authorization: Bearer <token>
  ↓
auth_middleware → get_user_from_request
  ↓
_peek_alg(token):
  ├── "HS256" → _verify_local_token
  ├── "RS256" → _verify_oidc_token
  └── 其他   → None (401)

_verify_oidc_token:
  ① cfg = db.get_config("sso")         [60 秒内存缓存]
  ② if not cfg.enabled → None
  ③ jwks = _get_jwks_client(cfg.issuer) [1 小时缓存]
  ④ jwt.decode(
       token, jwks_key,
       algorithms=["RS256"],
       audience=cfg.clientId,
       issuer=cfg.issuer,
       options={"require": ["exp", "iss", "aud", "sub"]}
     )
  ⑤ claims.email → db.get_employee_by_email
  ⑥ 匹配成功 → UserContext
     匹配失败 → None (前端 401, 显示友好错误)
```

### 4.1 缓存策略

| 缓存 | TTL | 失效机制 |
|---|---|---|
| `CONFIG#sso` 内存缓存 | 60 秒 | Settings PUT 成功时主动清除 |
| JWKS 公钥缓存 | 1 小时（PyJWKClient 内置） | Settings PUT 切换 issuer 时主动清除 |

### 4.2 安全防护

| 风险 | 防护 |
|---|---|
| Token 伪造 | JWKS RS256 验签 + `audience` + `issuer` 校验 |
| `iss` 欺骗 | jwt.decode 传入 `issuer=cfg.issuer`，精确匹配 |
| 过期 Token | jwt.decode 默认校验 `exp` |
| 必要 claim 缺失 | `options={"require": ["exp", "iss", "aud", "sub"]}` |
| 配置注入 | Settings PUT 端点加 role=admin 鉴权 |

---

## 5. 前端流程

### 5.1 SP-Initiated（员工主动）

```
员工访问 https://portal.example.com
  ↓
App.tsx 发现无 user 状态 → Navigate('/login')
  ↓
Login.tsx 挂载:
  - fetch /api/v1/public/sso/config
  - 若 enabled=true 显示 "Sign in with SSO" 按钮
  ↓ 员工点击
  ↓
loginWithSso():
  - const um = await getOidcManager()
  - um.signinRedirect()  (自动生成 state + PKCE 存 sessionStorage)
  ↓
浏览器 302 到 IdP /authorize
  ↓ 员工在 IdP 登录
  ↓
IdP 302 回 /sso/callback?code=xxx&state=yyy
  ↓
SsoCallback.tsx:
  - um.signinRedirectCallback() → id_token
  - fetch /api/v1/auth/me  Bearer <id_token>
  ↓
后端 _verify_oidc_token → UserContext
  ↓
前端收到 user:
  - authMode = 'sso'
  - role === 'employee' → navigate('/portal')
  - else → navigate('/dashboard')
```

### 5.2 IdP-Initiated（员工在 IdP 工作台点图标）

```
员工在阿里云 IDaaS 工作台点 OpenClaw 图标
  ↓
IDaaS 302 到 initiate_login_uri = https://portal.example.com/login
  ↓
Login.tsx 挂载:
  - fetch /api/v1/public/sso/config → { enabled: true, autoRedirect: true }
  - 在 useEffect 里检测 autoRedirect=true → 立即触发 loginWithSso()
  ↓ (之后流程和 SP-Initiated 完全一样)
  ↓
员工体验: 点图标 → 加载闪一下 → 进入 Portal
```

### 5.3 本地密码登录（兜底）

```
员工访问 /login
  ↓
填 employeeId + password → POST /api/v1/auth/login
  ↓
后端签 HS256 JWT → 存 localStorage → navigate
```

SSO 故障不影响本地登录（管理员可以永远用密码进后台）。

---

## 6. 代码改动清单

### 6.1 后端 Python

```
enterprise/admin-console/server/
├── auth.py                          【重构】
│   移除:
│   - AZURE_TENANT_ID / AZURE_CLIENT_ID 读 env
│   - _JWKS_URI / _ISSUER 硬编码常量
│   - _get_jwks_client (Azure 专用)
│   - _verify_azure_token
│   - _user_from_azure_claims (#EXT# 处理逻辑)
│   新增:
│   - _get_sso_config()           读 CONFIG#sso, 60 秒缓存
│   - clear_sso_config_cache()    供 settings.py 调用
│   - _get_oidc_jwks_client(issuer)  按 issuer 缓存
│   - _verify_oidc_token(token)   通用 OIDC 验证
│   - _user_from_oidc_claims(claims)  按 email 映射员工
│   修改:
│   - UserContext 字段不变 (email 已有)
│   - get_user_from_request() 分流: RS256 走 _verify_oidc_token
│   - _peek_alg 保留, 无需 _peek_iss
│
├── routers/settings.py              【改动 ~80 行】
│   新增端点:
│   - GET  /api/v1/settings/sso
│     返回当前 CONFIG#sso (admin 只)
│   - PUT  /api/v1/settings/sso
│     body: { issuer, clientId, scopes, enabled, autoRedirect }
│     保存后调 auth.clear_sso_config_cache()
│   - POST /api/v1/settings/sso/test
│     body: { issuer }
│     后端 GET {issuer}/.well-known/openid-configuration 验证可达
│     返回 { ok: true/false, error: "..." }
│
├── routers/auth_public.py           【新增 ~40 行】
│   无鉴权端点 (前缀 /api/v1/public/ 在 main.py 白名单):
│   - GET  /api/v1/public/sso/config
│     返回前端所需公开字段:
│     { enabled, issuer, clientId, scopes, autoRedirect, providerDisplayName }
│     Response header: Cache-Control: max-age=60
│
└── main.py                          【改动 ~5 行】
    - include_router(_auth_public_router)
    - 移除 Azure AD 相关注释
    - _AUTH_PUBLIC_PREFIXES 已含 /api/v1/public/，无需改
```

### 6.2 前端 TypeScript/React

```
enterprise/admin-console/src/
├── config/
│   ├── msalConfig.ts                【删除】
│   └── oidcClient.ts                【新增 ~60 行】
│       - getOidcManager()              获取或创建 UserManager 单例
│       - getOidcConfig()               返回公开配置 (autoRedirect 判断用)
│       - clearOidcCache()              Settings 保存后清除
│       内部:
│         const cfg = await fetch('/api/v1/public/sso/config')
│         new UserManager({
│           authority: cfg.issuer,
│           client_id: cfg.clientId,
│           redirect_uri: origin + '/sso/callback',
│           post_logout_redirect_uri: origin + '/login',
│           response_type: 'code',
│           scope: cfg.scopes,
│           userStore: new WebStorageStateStore({ store: sessionStorage }),
│         })
│
├── pages/
│   ├── Login.tsx                    【重写 ~110 行】
│   │   移除:
│   │   - handleMicrosoftLogin / loginWithMicrosoft 调用
│   │   - 微软 4 色方块 SVG
│   │   新增:
│   │   - useEffect 拉 /public/sso/config 得到 {enabled, autoRedirect}
│   │   - 若 enabled=true: 显示按钮 "Sign in with SSO"
│   │     按钮上增加提示: "IDaaS / Azure AD / Okta / Keycloak"
│   │   - 若 autoRedirect=true: 直接调 handleSsoLogin()
│   │   - handleSsoLogin → loginWithSso()
│   │
│   ├── SsoCallback.tsx              【新增 ~60 行】
│   │   路由 /sso/callback:
│   │   - useEffect 调 um.signinRedirectCallback()
│   │   - 拿到 oidcUser.id_token
│   │   - 调 AuthContext.completeSsoLogin(id_token)
│   │     → fetch /api/v1/auth/me 取用户信息 → setUser
│   │   - navigate by role
│   │   - 失败展示 error message 并提供 "回到登录" 按钮
│   │
│   └── Settings.tsx                 【改动 ~120 行】
│       - AccountTab 里 authMode 显示从 'azure' → 'sso', 'local' 保持
│       - 新增 SsoTab:
│         - 读/写 /api/v1/settings/sso
│         - 字段: Issuer, Client ID, Scopes, Enabled, Auto Redirect
│         - "测试连接" 按钮 → POST /settings/sso/test
│         - 下方帮助链接: "如何配置 IDaaS / Azure AD / Okta"
│       - Tabs 数组末尾加 { id: 'sso', label: 'SSO' }
│
├── contexts/AuthContext.tsx         【重写 ~150 行】
│   移除:
│   - import { useMsal, useIsAuthenticated } from '@azure/msal-react'
│   - loginWithMicrosoft
│   - getAzureToken
│   修改:
│   - authMode: 'azure' → 'sso'
│   - getAccessToken():
│       if authMode === 'sso':
│         const um = await getOidcManager()
│         const oidcUser = await um.getUser()
│         return oidcUser?.id_token ?? null
│       else: return localStorage.getItem('openclaw_token')
│   新增:
│   - loginWithSso():
│       const um = await getOidcManager()
│       await um.signinRedirect()
│   - completeSsoLogin():
│       (SsoCallback 调用，拉 /auth/me 填充 user, setAuthMode='sso')
│   修改:
│   - logout():
│       if authMode === 'sso':
│         const um = await getOidcManager()
│         await um.removeUser()
│         window.location.href = '/login'
│       else: localStorage.removeItem('openclaw_token')
│   启动恢复顺序:
│     ① oidcManager.getUser() → 若存在且未过期，fetchMe
│     ② localStorage 'openclaw_token' → fetchMe
│     ③ setLoading(false), 无用户
│
├── main.tsx                         【改动 ~20 行】
│   移除:
│   - PublicClientApplication / MsalProvider
│   - msalInstance.initialize() 等异步初始化
│   保留:
│   - QueryClientProvider
│   简化:
│   - 直接 ReactDOM.createRoot(...).render(<App />)
│
├── App.tsx                          【改动 1 行】
│   + <Route path="/sso/callback" element={<SsoCallback />} />
│
└── package.json                     【改动 3 行】
    移除:
    - "@azure/msal-browser"
    - "@azure/msal-react"
    新增:
    + "oidc-client-ts": "^3.0.0"
```

### 6.3 配置与脚本

```
enterprise/
├── .env.example                     【改动】
│   移除: AZURE_CLIENT_ID, AZURE_TENANT_ID
│
├── admin-console/.env.sample        【删除】
│   (VITE_AZURE_* 不再需要, 运行时从后端拉)
│
├── ec2-setup.sh                     【改动】
│   移除 VITE_AZURE_* 构建期注入
│
├── deploy.sh                        【改动】
│   移除 AZURE_* 环境变量检查逻辑
│
└── AZURE_AD_SETUP.md                【删除】
```

### 6.4 新增文档

```
enterprise/docs/
├── design-oidc-sso.md               【本文件】
└── OIDC_SSO_SETUP.md                【新增】
    管理员配置指引:
    - 阿里云 IDaaS (详细步骤)
    - Azure AD (作为 OIDC Provider 接入)
    - Okta (作为 OIDC Provider 接入)
    - 通用 OIDC Provider (Keycloak 等)
    - Troubleshooting
```

### 6.5 Seed 脚本

```
enterprise/admin-console/server/seed_dynamodb.py  【已在 Azure AD 实现中改过】
确认 20 个 demo 员工都有 email 字段: {id去prefix}@acme.com
```

---

## 7. IdP 控制台配置规范

### 7.1 所有 OIDC Provider 通用要求

| 配置 | 值 |
|---|---|
| 应用类型 | Single-Page Application (SPA) / Public Client |
| 授权模式 | Authorization Code + PKCE (必选) |
| Redirect URI | `https://{portal-domain}/sso/callback` |
| Initiate Login URI (可选) | `https://{portal-domain}/login` |
| Post Logout Redirect URI (可选) | `https://{portal-domain}/login` |
| Scope | `openid profile email` |
| id_token 签名算法 | RS256 |
| 是否需要 Client Secret | 否 (PKCE 公共客户端) |

### 7.2 阿里云 IDaaS

```
应用类型:          OIDC 单点登录
客户端类型:        Public Client
授权模式:          authorization_code
PKCE:             强制启用

Redirect URI:     https://portal.example.com/sso/callback
登录发起 URI:     https://portal.example.com/login

Issuer 格式:
  方式1 (自有域名): https://{prefix}.aliyunidaas.com/api/v2/iauths_system/oauth2
  方式2 (公共入口): https://eiam-api-cn-hangzhou.aliyuncs.com/v2/{instance_id}/{app_id}/oidc

拷贝给 OpenClaw:
- Issuer URL
- Client ID (形如 app_xxxxxxxxxx)
```

### 7.3 Azure AD（作为 OIDC Provider）

```
App registrations → New registration → SPA
Redirect URI:     https://portal.example.com/sso/callback

拷贝给 OpenClaw:
- Issuer:    https://login.microsoftonline.com/{tenant_id}/v2.0
- Client ID: Application (client) ID
```

### 7.4 Okta

```
Applications → Create App Integration → OIDC - Single-Page Application
Grant type:  Authorization Code + Refresh Token + PKCE
Redirect URI: https://portal.example.com/sso/callback

拷贝给 OpenClaw:
- Issuer:    https://{your-okta-domain}/oauth2/default
- Client ID: Client Credentials → Client ID
```

---

## 8. 安全设计要点

| 风险 | 防护 |
|---|---|
| CSRF | `state` 参数（oidc-client-ts 自动管理） |
| Auth Code 拦截 | PKCE `code_verifier + code_challenge` |
| id_token 伪造 | JWKS 公钥验 RS256 签名 + audience + issuer 校验 |
| Token 泄漏 | sessionStorage（关闭标签页即清） |
| Client Secret 泄漏 | 不使用 client secret（public client + PKCE） |
| 任意 iss 欺骗 | `jwt.decode(issuer=cfg.issuer)` 精确匹配 |
| SSO 故障锁死管理员 | 本地密码登录路径独立保留 |
| email 未匹配员工 | 友好错误提示: "SSO 成功但未找到邮箱 xxx@... 对应员工，请联系管理员" |
| 公开端点被滥用 | `/public/sso/config` 加 `Cache-Control: max-age=60` |
| 配置热更新 | Settings PUT 成功后主动清除缓存 |
| HTTPS 强制 | 生产必须 HTTPS，只有 `localhost` 允许明文 |

---

## 9. 交付阶段

### Phase 1：Big Bang（一次性交付 ✓ 用户选 B）

**目标**：一次性完成 Azure AD → 通用 OIDC 迁移，含 SP-Initiated + IdP-Initiated + 测试连接 + 错误友好提示。

**任务清单**：

#### 后端
- [x] 重构 `auth.py`：移除 Azure AD 专用代码，加 `_verify_oidc_token`
- [x] `routers/auth_public.py`：新增 `/public/sso/config` 端点
- [x] `routers/settings.py`：新增 `/settings/sso` GET/PUT/test 端点
- [x] `main.py`：注册 auth_public router
- [x] `requirements.txt`:加 httpx
- [x] `seed_dynamodb.py`:demo 员工自动生成 email (`{id去prefix}@acme.com`)

#### 前端
- [x] 移除 `@azure/msal-browser`、`@azure/msal-react`，装 `oidc-client-ts`
- [x] 删除 `config/msalConfig.ts`，新增 `config/oidcClient.ts`
- [x] 重写 `contexts/AuthContext.tsx`（authMode='azure' → 'sso'）
- [x] 简化 `main.tsx`（移除 MsalProvider）
- [x] 重写 `pages/Login.tsx`（移除 Microsoft 按钮，加 SSO 按钮 + autoRedirect）
- [x] 新增 `pages/SsoCallback.tsx`
- [x] 改 `pages/Settings.tsx`（新增 SSO tab + AccountTab authMode 显示)

- [x] `App.tsx` 加 `/sso/callback` 路由

#### 配置与文档
- [x] 清理 `enterprise/.env.example`、删除 `admin-console/.env.sample`
- [x] 清理 `ec2-setup.sh`、`deploy.sh` 的 Azure 逻辑
- [x] 删除 `AZURE_AD_SETUP.md`
- [x] 新增 `docs/OIDC_SSO_SETUP.md`
- [x] `enterprise/README.md` 文档链接更新

#### 验收（代码层面已就绪,待真人/IdP 联测)
- [ ] 本地密码登录仍正常工作
- [ ] Settings 页 SSO tab 能保存配置
- [ ] "测试连接"能验证 Issuer 可达
- [ ] 员工点 SSO 按钮走完 Authorization Code + PKCE
- [ ] `autoRedirect=true` 时 `/login` 自动跳转
- [ ] email 未匹配时前端显示友好错误
- [ ] Settings 保存后后端缓存立即刷新

---

## 10. 附录

### 10.1 术语

| 术语 | 说明 |
|---|---|
| OIDC | OpenID Connect，基于 OAuth 2.0 的身份层协议 |
| IdP | Identity Provider，身份提供方（如阿里云 IDaaS、Azure AD） |
| SP | Service Provider，服务提供方（如 OpenClaw Portal） |
| SP-Initiated | 登录由 SP 发起（员工先到 Portal） |
| IdP-Initiated | 登录由 IdP 发起（员工先在 IdP 工作台） |
| PKCE | Proof Key for Code Exchange, 防止 Authorization Code 拦截 |
| Authorization Code Flow | OAuth 2.0 中浏览器场景最标准的授权模式 |
| JWKS | JSON Web Key Set，IdP 发布的公钥集合，用于验 RS256 签名 |
| id_token | OIDC 定义的用户身份令牌，JWT 格式，IdP 签名 |

### 10.2 参考

- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)
- [RFC 6749 - OAuth 2.0](https://tools.ietf.org/html/rfc6749)
- [RFC 7636 - PKCE](https://tools.ietf.org/html/rfc7636)
- [RFC 9700 - OAuth 2.0 Security Best Current Practice (2024)](https://www.rfc-editor.org/rfc/rfc9700)
- [oidc-client-ts 文档](https://github.com/authts/oidc-client-ts)
- [阿里云 IDaaS OIDC 集成文档](https://help.aliyun.com/zh/idaas/developer-reference/api-eiam-2021-12-01-oauth2-oidc-api/)

### 10.3 演进历史

| 方案 | 评估结果 |
|---|---|
| A: 管理员手动填每个员工 email | ❌ 运维成本高 |
| B: 员工首次自助绑定 | ❌ 需要员工额外操作 |
| C: 邮箱命名规则反解 | ❌ 脆弱，有重名/误绑风险 |
| D: 按 email 统一映射，env 存配置 | ⚠️ env 改动要重启 + 重新构建 |
| D+: 双路由分流 Azure/IDaaS | ⚠️ 保留历史包袱 |
| **D+1: 完全通用 OIDC，Settings 配置** | ✅ 最终采用 |
