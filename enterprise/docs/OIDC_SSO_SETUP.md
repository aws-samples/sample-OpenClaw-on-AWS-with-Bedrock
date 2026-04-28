# OIDC Single Sign-On Setup Guide

OpenClaw Enterprise supports any OpenID Connect (OIDC) compatible identity
provider, including:

- **Alibaba Cloud IDaaS** (EIAM)
- **Microsoft Entra ID** (Azure AD)
- **Okta**
- **Keycloak**
- Any other standards-compliant OIDC provider

All configuration is done **at runtime** through the admin console. No
environment variables or rebuilds required.

## How it works

```
Browser
  → Login page shows "Sign in with SSO" button
  → Click → redirect to IdP login page
  → User authenticates in their IdP
  → Redirect back to /sso/callback with authorization code
  → oidc-client-ts exchanges code for id_token (PKCE)
  → Frontend sends id_token with every API request
  → Backend verifies signature via IdP's JWKS endpoint
  → Backend matches token email to DynamoDB employee record
  → User signed in
```

The app never sees the user's IdP password. All token validation happens
server-side using the IdP's public keys.

## Prerequisites

1. An OIDC-compatible IdP with admin access
2. Your OpenClaw Portal must be reachable via **HTTPS** in production
   (HTTP is only allowed for `localhost`)
3. Each SSO user must have a matching employee record in DynamoDB with the
   `email` field set

## Step 1: Create an OIDC Application in Your IdP

The exact UI varies by provider but the required settings are the same.

### Common Settings (All Providers)

| Setting | Value |
|---|---|
| Application type | **Single-Page Application** (SPA) / **Public Client** |
| Grant type | **Authorization Code** + **PKCE** |
| Redirect URI | `https://your-portal.example.com/sso/callback` |
| Initiate Login URI (optional) | `https://your-portal.example.com/login` |
| Post Logout Redirect URI (optional) | `https://your-portal.example.com/login` |
| Scope | `openid profile email` |
| ID Token signing algorithm | RS256 |
| Client Secret required | **No** (PKCE public client) |

### Alibaba Cloud IDaaS

1. Open your IDaaS instance console
2. **Applications** → **Add Application** → **Custom OIDC Application**
3. Configure:
   - **Application Type**: OIDC SSO
   - **Client Type**: Public Client
   - **Authorization Mode**: `authorization_code`
   - **PKCE**: Enabled (required)
   - **Redirect URI**: `https://your-portal.example.com/sso/callback`
   - **Initiate Login URI**: `https://your-portal.example.com/login`
4. After saving, copy:
   - **Issuer URL**, e.g.
     - Custom domain: `https://{prefix}.aliyunidaas.com/api/v2/iauths_system/oauth2`
     - Public entry: `https://eiam-api-{region}.aliyuncs.com/v2/{instance_id}/{app_id}/oidc`
   - **Client ID**, shape `app_xxxxxxxxxx`
5. Authorize the application to the employees who need access

### Microsoft Entra ID (Azure AD)

1. [Azure Portal](https://portal.azure.com) → **Microsoft Entra ID** → **App registrations**
2. **New registration**
   - **Supported account types**: *Accounts in this organizational directory only*
3. In the new app, go to **Authentication** → **Add a platform** → **Single-page application**
   - **Redirect URI**: `https://your-portal.example.com/sso/callback`
   - Leave all implicit/hybrid flow checkboxes **unchecked**
4. Copy:
   - **Issuer**: `https://login.microsoftonline.com/{tenant_id}/v2.0`
     (tenant_id from Overview → Directory (tenant) ID)
   - **Client ID**: Overview → Application (client) ID
5. API permissions → ensure `openid`, `profile`, `email` are granted

### Okta

1. **Applications** → **Create App Integration** → **OIDC - Single-Page Application**
2. Configure:
   - **Grant types**: Authorization Code + Refresh Token
   - **Sign-in redirect URIs**: `https://your-portal.example.com/sso/callback`
   - **Sign-out redirect URIs**: `https://your-portal.example.com/login`
3. Copy:
   - **Issuer**: `https://{your-okta-domain}/oauth2/default`
   - **Client ID**: Client Credentials → Client ID

### Keycloak

1. In your realm: **Clients** → **Create client**
2. Configure:
   - **Client type**: OpenID Connect
   - **Client authentication**: OFF (public client)
   - **Authentication flow**: check only **Standard flow**
   - **Valid redirect URIs**: `https://your-portal.example.com/sso/callback`
   - **Valid post logout redirect URIs**: `https://your-portal.example.com/login`
   - **Advanced → Proof Key for Code Exchange Code Challenge Method**: S256
3. Copy:
   - **Issuer**: `https://{keycloak-host}/realms/{realm-name}`
   - **Client ID**: the ID you set in step 1

## Step 2: Configure SSO in OpenClaw

1. Sign in to the admin console with an **admin account** using password
2. Go to **Settings** → **SSO** tab
3. Fill in:
   - **Issuer URL**: from your IdP console
   - **Client ID**: from your IdP console
   - **Scopes**: `openid profile email` (default)
4. Click **Test Connection**
   - The backend fetches `{issuer}/.well-known/openid-configuration`
   - Success means the Issuer is reachable and returns valid OIDC metadata
5. Check **Enable SSO** to show the "Sign in with SSO" button on login page
6. (Optional) Check **Auto-redirect on login page** to enable IdP-initiated flow
7. Click **Save**

The configuration takes effect immediately. No restart required.

## Step 3: Link Employees to SSO Identities

The backend identifies SSO users by the `email` claim in the ID token. Each
employee that should be able to sign in via SSO must have a matching `email`
field in their DynamoDB record.

**In the admin console:**

**Organization → Employees** → edit an employee → set the email address that
matches their IdP login email.

**Example:**

| Employee DynamoDB record | IdP profile |
|---|---|
| `email`: `mike.johnson@acme.com` | username: `mike.johnson@acme.com` |

When `mike.johnson` signs in via SSO:

1. IdP issues an id_token with `email: mike.johnson@acme.com`
2. OpenClaw backend finds the employee with that email
3. User is signed in as `emp-mike` with role, department, permissions loaded

## Login Flows Supported

### SP-Initiated (user starts at Portal)

```
User opens https://portal.example.com
  → Redirected to /login
  → Clicks "Sign in with SSO"
  → Redirected to IdP
  → Authenticates
  → Redirected back, signed in
```

### IdP-Initiated (user starts at IdP workspace)

Requires **Auto-redirect on login page** to be enabled.

```
User clicks OpenClaw icon in IdP workspace
  → IdP redirects to https://portal.example.com/login
  → Portal immediately triggers SSO login (no manual click)
  → IdP recognizes existing session, silently issues token
  → User lands in Portal with no manual steps
```

### Password (fallback for administrators)

Always available. Admin can sign in with employee ID + password even if SSO
is misconfigured or the IdP is unreachable.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Click "Sign in with SSO" → IdP shows "Invalid redirect_uri" | Redirect URI in IdP doesn't exactly match OpenClaw's | Ensure `https://your-portal.example.com/sso/callback` is registered in the IdP (exact host, port, protocol, path, no trailing slash) |
| Successful IdP login but OpenClaw shows "未找到邮箱为 xxx 的员工" | No DynamoDB employee has that email | Add the email to the employee record in **Organization → Employees** |
| Test Connection fails with "HTTP error" | Backend cannot reach IdP (network/firewall) | Verify the EC2 instance has egress to the IdP; check security groups and NAT |
| Settings saved but login still uses old config | Caches on backend | Not an issue — backend clears caches automatically on save |
| Token expired unexpectedly | id_token lifetime shorter than expected | Users will be redirected back to /login; they can re-authenticate |
| 401 on every API call after SSO | ID token claims missing required fields | Check the IdP returns `email`, `sub`, `aud`, `iss`, `exp` in the id_token |

## Security Notes

- **No client secret is used** — this is Public Client + PKCE mode. The
  client secret would be exposed in the browser and provide no security.
- **Tokens stored in sessionStorage** — cleared when browser tab closes,
  more XSS-resistant than localStorage.
- **All token verification server-side** — the backend validates the RS256
  signature using the IdP's JWKS endpoint, plus `audience`, `issuer`, and
  `exp` claims.
- **HTTPS required in production** — only `localhost` is allowed for HTTP.
- **No refresh token management** — when id_token expires, the user is
  redirected back to the IdP (via the silent flow if IdP session is still
  alive, or interactive login otherwise).

## Disabling SSO

To turn off SSO while keeping the configuration:

1. Settings → SSO → uncheck **Enable SSO** → Save

To fully remove the configuration:

1. Settings → SSO → clear Issuer/Client ID → uncheck Enable SSO → Save

Users can still sign in with password while SSO is disabled.
