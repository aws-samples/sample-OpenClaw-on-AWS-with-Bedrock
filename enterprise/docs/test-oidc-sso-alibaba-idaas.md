# OIDC SSO 联测指南 — 阿里云 IDaaS

> 从 0 到 1 在阿里云 IDaaS 创建 OIDC 应用,接入 OpenClaw Enterprise 并完整
> 验证 SP-Initiated 和 IdP-Initiated 两种登录流程。
>
> 面向人员:QA / 部署工程师 / 新环境联调
> 预计耗时:首次 30 分钟(含创建 IDaaS 应用),再次 5 分钟

---

## 0. 前置准备

开始之前确认:

- [ ] 有一个可用的 **阿里云 IDaaS 实例**(EIAM 2.0),拥有管理员权限
- [ ] OpenClaw Enterprise 已部署并可以用**管理员账号 + 密码**登录后台
- [ ] 部署域名可通过 HTTPS 访问(例如 CloudFront 域名 / 自有域名)
- [ ] 至少有一个测试员工账号,其 `email` 字段在 DynamoDB 和 IDaaS 里保持一致

> **测试期脱敏约定**:本文一律用以下占位符,真实操作时替换为实际值:
> - 测试员工邮箱:`t***@example.com`
> - 测试员工姓名:`测试员工 A`
> - 阿里云 IDaaS 域名前缀:`yyy`(实际形如 `zi****`)
> - 应用 Client ID:`app_xxxxxxxx`
> - Portal 部署域名:`portal.example.com`(实际可能是 CloudFront 或 ALB 域名)

---

## 1. 阿里云 IDaaS 侧 — 创建 OIDC 应用

### 1.1 登录 IDaaS 控制台

用阿里云主账号或被授权的子账号登录:

```
https://eiam.aliyun.com/
```

进入目标 IDaaS 实例(一般叫"云身份服务"或"IDaaS EIAM 2.0")。

### 1.2 创建自定义应用

1. 左侧菜单 **应用管理** → **应用**
2. 点击右上角 **添加应用** → 选 **自定义应用**
3. 填写应用信息:
   - **应用名称**:`OpenClaw Enterprise`
   - **应用图标**:可选,上传你的 logo
   - **应用描述**:可选

### 1.3 配置 SSO 协议

在应用详情页 → **登录访问** / **SSO 配置**:

1. **协议类型** 选 **OIDC**
2. **客户端类型** 选 **应用端机密客户端**(Confidential Client)
   > BFF 架构要求 Confidential Client,OpenClaw 后端用 client_secret 鉴权
3. **授权模式** 勾选 `authorization_code`
4. **PKCE** 勾选(强制启用,方法选 `S256`)
5. **Token 签名算法** 选 `RS256`(默认)

### 1.4 填写回调地址

从 OpenClaw 管理后台 **Settings → SSO tab** 复制以下两个 URL:

```
Redirect URI:         https://portal.example.com/api/v1/auth/sso/callback
Initiate Login URI:   https://portal.example.com/login?sso=idp
```

粘贴到 IDaaS 应用配置:

- **登录回调地址 / Redirect URI**:填上面第一个
  > 必须**完全一致**(协议、域名、端口、路径、尾斜杠)
  > 注意这是后端路径 `/api/v1/auth/sso/callback`,不是前端 `/sso/callback`
- **登录发起地址 / Initiate Login URI**:填上面第二个
  > 可选,但**IdP-Initiated 流程必需**
- **登出回调地址 / Post Logout Redirect URI**(可选):
  填 `https://portal.example.com/login`

记得**复制 Client Secret**,后面填到 OpenClaw Settings 里。
Secret 通常只在创建时显示一次,丢了要重新生成。

### 1.5 配置 Scope

**Scope 允许列表** 勾选:
- [x] `openid`
- [x] `profile`
- [x] `email`

### 1.6 保存并记录关键信息

保存后,在应用详情页记下(后面要填到 OpenClaw):

| 字段 | 形如 | 填到 OpenClaw 的位置 |
|---|---|---|
| **Issuer URL** | `https://yyy.aliyunidaas.com/api/v2/iauths_system/oauth2` | Settings → SSO → Issuer URL |
| **Client ID** | `app_xxxxxxxx` | Settings → SSO → Client ID |

> IDaaS 也可能在"应用配置"和"开发者接入"tab 分开展示,Issuer 有时也叫 "Issuer URL"、"发行者"或在"OIDC Endpoints"段落里的 `issuer` 字段。

### 1.7 授权员工访问该应用

默认新建的应用**没有任何员工可见**。要让测试员工能看到这个应用:

1. 应用详情页 → **授权管理** / **权限管理**
2. **添加授权** → 选 **组织机构** 或 **账户**
3. 勾选测试员工 `测试员工 A`
4. 保存

### 1.8 确认员工邮箱一致

员工 `测试员工 A` 在 IDaaS 里的邮箱必须和 OpenClaw DynamoDB 里的 email 字段一致:

```
IDaaS         → 账号管理 → 找到"测试员工 A" → 邮箱 = t***@example.com
OpenClaw DB   → Organization → Employees → "测试员工 A" → email = t***@example.com
```

**任何不一致都会导致 SSO 登录后报"未找到员工"错误**。

---

## 2. OpenClaw 侧 — 配置 SSO

### 2.1 用密码登录管理后台

```
https://portal.example.com/login
```

用 admin 员工的 employeeId + password 登录(比如 `emp-admin` / 初始 `ADMIN_PASSWORD`)。

### 2.2 进入 SSO 配置页

**Settings → SSO tab**

### 2.3 填写配置

| 字段 | 值 |
|---|---|
| **Issuer URL** | 粘贴 IDaaS 的 Issuer URL(见 1.6) |
| **Client ID** | 粘贴 IDaaS 的 Client ID(见 1.6) |
| **Scopes** | 保持默认 `openid profile email` |

### 2.4 测试连接

点击 **Test Connection** 按钮。

**成功表现**:绿色提示,显示 `Reachable. Token endpoint: ...`

**失败常见原因**:
- `HTTP error` → Issuer URL 错误或 EC2 不能出公网访问 IDaaS
- `Invalid OIDC metadata` → Issuer 格式错误,缺少 `.well-known/openid-configuration` 端点

不通过**不要继续**,先排查(见第 4 节)。

### 2.5 启用 SSO

- [x] 勾选 **Enable SSO**
- [ ] 暂时**不勾** **Auto-redirect on login page**(先测试 SP-Initiated,再单独验证 IdP-Initiated)

点击 **Save**。成功后右侧出现 `Last updated by ... at ...`。

---

## 3. 端到端测试

### 3.1 测试场景 1:SP-Initiated(员工从 Portal 发起)

**前置**:退出当前登录态
1. 点右上角头像 → **Logout**
2. 确认回到 `/login` 页面,显示 **Sign in with SSO** 按钮

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 点击 **Sign in with SSO** 按钮 | 浏览器跳到 IDaaS 登录页 | [ ] |
| 2 | URL 里包含 `client_id=app_xxx` `code_challenge=xxx` `state=xxx` | 说明 PKCE 生效 | [ ] |
| 3 | 在 IDaaS 登录页输入 `测试员工 A` 的账号密码 | 登录成功 | [ ] |
| 4 | 浏览器自动回到 `/api/v1/auth/sso/callback?code=...&state=...` (后端端点) | 后端处理完 302 到 `/login/sso-success` | [ ] |
| 5 | 最终落到 `/portal` 或 `/dashboard`(按 role) | 已登录员工身份 | [ ] |
| 6 | 右上角显示 `测试员工 A` 的姓名 | 姓名来自 DynamoDB | [ ] |
| 7 | **Settings → Account tab** 里 `Auth Mode` 显示 `Single Sign-On (OIDC)` | 确认是 SSO 登录 | [ ] |

### 3.2 测试场景 2:SSO 已登录时,API 调用正常

**前置**:已经通过 SSO 登录(场景 1 完成)

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 点 Portal 各个页面(My Agents / My Skills / My Usage) | 页面正常加载,不会跳回 login | [ ] |
| 2 | 打开浏览器开发者工具 → Network | 每个 API 请求 Authorization header 都是 `Bearer eyJ...`(id_token) | [ ] |
| 3 | 观察 token 开头 3 个字符是 `eyJ` 且解码后 header `alg=RS256` | 确认用的是 SSO id_token 不是本地 JWT | [ ] |

### 3.3 测试场景 3:IdP-Initiated(员工从 IDaaS 工作台发起)

**前置**:启用 Auto-redirect
1. 用密码(或 SSO)登录管理后台
2. Settings → SSO → 勾选 **Auto-redirect on login page** → Save

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 打开新的无痕窗口(确保无缓存登录态) | | [ ] |
| 2 | 访问阿里云 IDaaS 用户工作台:`https://yyy.aliyunidaas.com/` | IDaaS 登录页 | [ ] |
| 3 | 用 `测试员工 A` 账号登录 | 进入"我的应用" | [ ] |
| 4 | 找到 **OpenClaw Enterprise** 应用图标并点击 | 浏览器跳到 Portal 的 `/login` | [ ] |
| 5 | `/login` 页面**不要求手动点按钮**,自动发起 SSO | 闪一下 loading | [ ] |
| 6 | 因 IDaaS session 已存在,silent 完成授权 | 不再弹登录框 | [ ] |
| 7 | 最终落到 `/portal` 或 `/dashboard` | 和场景 1 效果一致 | [ ] |

### 3.4 测试场景 4:错误场景 — email 未匹配

**目的**:验证当 IDaaS 里有一个员工但 OpenClaw 里没对应 email 记录时,有友好错误提示。

**前置**:
1. 在 IDaaS 创建一个新员工 `临时员工 B`,邮箱 `t**b@example.com`,授权访问 OpenClaw 应用
2. OpenClaw DynamoDB **不**添加这个员工

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 退出登录,点 **Sign in with SSO** | | [ ] |
| 2 | 用 `临时员工 B` 账号在 IDaaS 登录 | | [ ] |
| 3 | 后端 `/api/v1/auth/sso/callback` 处理后,302 到 `/login?error=email_not_found&email=xxx` | | [ ] |
| 4 | 页面顶部红色错误框显示:`SSO 登录成功,但未找到邮箱为 t**b@example.com 的员工。请联系管理员。` | 邮箱能看到完整 | [ ] |

### 3.5 测试场景 5:本地密码登录仍然可用(兜底)

**目的**:验证 SSO 故障时管理员仍能登录。

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 退出登录,回到 `/login` | 看到 Sign in with SSO 和密码表单 | [ ] |
| 2 | 不点 SSO,填 admin employeeId + password 直接登录 | 成功进入 `/dashboard` | [ ] |
| 3 | Settings → Account → Auth Mode 显示 `Password` | 确认走的本地路径 | [ ] |

### 3.6 测试场景 6:Settings 热更新

**目的**:验证改 SSO 配置后不用重启即可生效。

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | Settings → SSO → 取消勾选 **Enable SSO** → Save | | [ ] |
| 2 | 退出登录回到 `/login` | SSO 按钮消失 | [ ] |
| 3 | 重新勾选 Enable SSO → Save | | [ ] |
| 4 | 刷新 `/login` | SSO 按钮再次出现 | [ ] |

---

### 3.7 测试场景 7:自动创建员工(Auto-Provisioning 默认启用)

**目的**:验证 IDaaS 有新员工但 OpenClaw 还没建对应 employee 时,自动创建成功。

**前置**:
1. Settings → SSO tab → 确认 **Auto-create employees on first SSO login** 已勾选
2. 填好 **Default Position**(例如 `pos-sde` Software Engineer)
3. **Default Role** 保持 `employee`
4. 保存配置
5. 在 IDaaS 创建一个新员工 `新员工 X`,邮箱 `n***x@example.com`,授权访问 OpenClaw
6. OpenClaw DynamoDB **不**手动添加此员工

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 退出登录,点 **Sign in with SSO** | | [ ] |
| 2 | 用 `新员工 X` 账号在 IDaaS 登录 | | [ ] |
| 3 | 回调后**无需**看到"请联系管理员"错误 | | [ ] |
| 4 | 直接进入 `/portal` | | [ ] |
| 5 | 右上角显示 `新员工 X` 姓名(或 email 前缀 `n***x`) | | [ ] |
| 6 | 以 admin 身份登录,进入 Organization → Employees | 看到新员工 `emp-n***x` | [ ] |
| 7 | 新员工的 position 是 `pos-sde`,department 是 `dept-eng` | 按 position 推导 | [ ] |
| 8 | Agent Factory 页面看到新建的 `agent-n***x`,skills 等于 pos-sde 的 defaultSkills | | [ ] |
| 9 | Audit Log 看到 `employee_auto_create` 事件,detail 包含 email/position/agent | | [ ] |

### 3.8 测试场景 8:IdP-Initiated via `?sso=idp`

**目的**:验证从 IDaaS 工作台点图标时,OpenClaw 自动进入 SSO 流程。

**前置**:
1. IDaaS 应用的 **登录发起 URI** 填为 `https://portal.example.com/login?sso=idp`

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 用无痕窗口访问 `https://portal.example.com/login` | 显示登录表单(没有自动跳) | [ ] |
| 2 | 同窗口访问 `https://portal.example.com/login?sso=idp` | 立即跳 IDaaS | [ ] |
| 3 | 完整流程到 `/portal` | | [ ] |
| 4 | 打开 IDaaS 工作台 → 点 OpenClaw 图标 → 自动到 `/login?sso=idp` → 跳转 | 员工无感登录 | [ ] |

### 3.9 测试场景 9:禁用 Auto-Provisioning 后的行为

**目的**:验证 Auto-Provisioning 开关关闭时,IDaaS 新员工无法自动登录。

**步骤**:

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | admin 登录 → Settings → SSO → 取消勾选 Auto-create → Save | | [ ] |
| 2 | 让 `新员工 Y`(OpenClaw 没此员工) 尝试 SSO 登录 | | [ ] |
| 3 | 回调后跳回 `/login`,显示"未找到邮箱为 y***@... 的员工。请联系管理员" | | [ ] |
| 4 | 重新勾选 Auto-create → Save → `新员工 Y` 再试 | 这次自动创建成功 | [ ] |

---

## 4. 故障排查

### 4.1 IDaaS 登录后显示 `Invalid redirect_uri`

**原因**:IDaaS 应用里注册的 Redirect URI 和 OpenClaw 实际发起的不一致。

**排查**:
1. 浏览器开发者工具 → Network → 找到跳转到 IDaaS 的请求,看 URL 里的 `redirect_uri=...` 参数
2. IDaaS 应用配置里"登录回调地址"是否**完全一致**(含 `https://`、域名、`/api/v1/auth/sso/callback`)
3. 特别注意**尾斜杠**:`/api/v1/auth/sso/callback` ≠ `/api/v1/auth/sso/callback/`
4. 注意路径必须是后端 `/api/v1/auth/sso/callback`(不是前端 `/sso/callback`)

### 4.2 "SSO 登录成功,但未找到邮箱为 xxx 的员工"

**原因**:IDaaS 返回的 email 在 DynamoDB 员工表里找不到。

**排查**:
1. 进入 **Organization → Employees**,编辑目标员工,确认 **Email** 字段等于 IDaaS 里那个邮箱
2. 大小写**不敏感**(后端会 lowercase 比较),但**空格敏感**
3. 若 IDaaS 里员工邮箱和 OpenClaw 员工邮箱应该不同(比如多个别名),在 OpenClaw 员工记录里改 email 字段匹配 IDaaS 返回的

### 4.3 Test Connection 失败 `HTTP error`

**原因**:后端从 EC2 访问 Issuer URL 失败。

**排查**:
1. SSH 登录 EC2,手动测试:
   ```bash
   curl -v https://yyy.aliyunidaas.com/api/v2/iauths_system/oauth2/.well-known/openid-configuration
   ```
2. 如果 curl 也超时 → EC2 没配 NAT Gateway,无法出公网
3. 如果 curl 返回 HTML → Issuer URL 错了,缺 `/api/v2/iauths_system/oauth2` 路径
4. 如果 curl 返回 JSON → 后端代码问题,查 `/var/log/openclaw-admin.log`

### 4.4 点 Sign in with SSO 按钮没反应

**原因**:
- 前端没读到 SSO 配置(按钮根本没出现)
- 或者配置不完整(issuer/clientId 为空)

**排查**:
1. 浏览器开发者工具 → Network → 找 `/api/v1/public/sso/config`,看响应:
   - `{"enabled": true, "issuer": "xxx", "clientId": "xxx", ...}` → 配置正常
   - `{"enabled": false, ...}` → 管理员没勾 Enable SSO
   - 404 / 500 → 后端服务问题
2. 浏览器开发者工具 → Console,看是否有 CORS 错误或 oidc-client-ts 错误

### 4.5 Callback 页长时间转圈不跳转

**原因**:后端 `/auth/me` 调用失败。

**排查**:
1. Network → `auth/me` 请求响应码
   - 401 → id_token 验签失败(Issuer 在后端配置和 IDaaS 侧不匹配)
   - 200 + 空 body → email 查不到员工(但前端应识别为 null 跳回 /login,此情况应不会卡住)
2. Console 面板看有无 JS 错误

### 4.6 Auto-redirect 开启后无限跳转

**症状**:`/login` 页一直自动跳到 IDaaS 再跳回 `/login`,无限循环。

**原因**:IDaaS 那边还没授权用户访问 OpenClaw 应用,但 Silent 模式下发回带 `error=` 的 URL,前端 autoRedirect 判断没拦截到。

**排查**:
1. 浏览器开发者工具 → Network 看跳转链,若 URL 里有 `?error=login_required` 或 `error=access_denied`
2. 到 IDaaS 控制台给目标员工授权访问 OpenClaw 应用(见 1.7)
3. 如果授权过了仍循环,临时方案:Settings → SSO → 取消勾选 **Auto-redirect on login page** → Save,改用 SP-Initiated

### 4.7 Silent 授权失败提示"同意"

**原因**:新员工首次登录应用,IDaaS 要求显式同意数据访问(类似 Google OAuth 第一次的"允许访问"页)。

**处理**:让员工在 IDaaS 同意一次即可,之后不会再出现。管理员也可以在 IDaaS 应用配置里把该应用设为"免同意"。

---

## 5. 清理(测试完成后)

如果这只是临时测试,不想保留:

1. OpenClaw: Settings → SSO → 取消 **Enable SSO** → Save
2. IDaaS: 应用管理 → 找到 OpenClaw 应用 → 停用或删除
3. 清理 DynamoDB 里临时员工记录(如果有的话)

---

## 附录:测试账号清单(填写自己的真实值时脱敏保留)

| 角色 | 用途 | IDaaS 账号 | OpenClaw email | OpenClaw role |
|---|---|---|---|---|
| A | 正常 SSO 登录 | `测试员工 A` | `t***@example.com` | employee |
| B | 测试 email 未匹配 | `临时员工 B` | 不在 DB 中 | — |
| admin | 密码兜底 | — | `admin@example.com` | admin |
