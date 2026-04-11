# PRD: IM Channels Module

**Status:** Draft
**Author:** JiaDe Wang + Claude
**Date:** 2026-04-12
**Priority:** P1 — The bridge between employees and their AI agents

---

## 1. Problem Statement

IM Channels is the **message ingestion layer** — every employee interaction from Telegram, Discord, Feishu, Slack, etc. flows through here. The module spans 4 files (admin_im.py, bindings.py, portal.py, gateway_proxy.py) totaling ~2,100 lines. Current issues:

### Admin Side (admin_im.py, 299 lines)

**A1. Duplicated `_list_user_mappings()` function.**
Identical implementation exists in both `admin_im.py:73-102` and `bindings.py:72-101`. Divergence risk — fix one, forget the other.

**A2. SSM pagination bug — `MaxResults=10` without loop.**
`get_im_channels()` at line 176: `ssm.get_parameters_by_path(Path=prefix, Recursive=True, MaxResults=10)` — no pagination loop. If >10 employee mappings exist, channel counts are wrong. The `_list_user_mappings()` in admin_im.py does paginate, but `get_im_channels()` does its own inline SSM query without pagination.

**A3. `subprocess` dependency for channel status.**
`_run_openclaw_channels()` (lines 30-70) calls `openclaw channels list` via subprocess, parses ANSI-stripped text output. Fragile: any change to CLI output format breaks parsing. Also requires `openclaw_cli.py` helper module.

**A4. Hardcoded `ORG#acme` in binding check.**
`im_binding_check()` line 236: `KeyConditionExpression=_KBC("PK").eq("ORG#acme")`. Should use `db.ORG_PK`.

**A5. No audit trail on bot info updates.**
`set_im_bot_info()` writes to DynamoDB but creates no AUDIT# entry. Admin changes IM bot config silently.

**A6. No DM policy guidance.**
IM bots require "Open" DM policy on Discord/Slack to receive messages. No validation or warning in the admin UI.

### Bindings / Pairing (bindings.py, 469 lines)

**B1. `subprocess` for pairing approve.**
`approve_pairing()` (lines 277-343) runs `openclaw pairing approve` via subprocess. Same fragility as A3.

**B2. Shared agent routing code still present.**
`resolve_route()` (lines 396-429) checks for `route_to_shared_agent` action and `shared_agent` rules. We removed shared agent design — this is dead code.

**B3. `_send_im_notification()` incomplete.**
Only handles Telegram and Feishu (lines 104-140). Discord comment says "skip for now". Slack, WhatsApp, Teams, DingTalk, Google Chat — all missing.

**B4. SSM dual-write everywhere.**
`_write_user_mapping()` writes both DynamoDB MAPPING# and SSM. The SSM writes are labeled "backward compat" but add latency and failure modes. Need a migration plan to SSM-off.

**B5. No auth on bindings CRUD.**
`get_bindings()`, `create_binding()`, `get_user_mappings()`, `create_user_mapping()` — none require authentication. Protected by middleware whitelist? Need to verify.

### Portal / Employee Pairing (portal.py, relevant sections)

**P1. SSM-heavy always-on detection.**
`portal_channels()` (lines 680-807) makes 5+ SSM calls per request: always-on check, endpoint, gateway-token, dashboard-token, per-channel dedicated bot checks. Should cache or use DynamoDB.

**P2. `_find_channel_user_id()` SSM scan with MaxResults=10.**
Line 116: reverse lookup by scanning SSM params. No pagination. Breaks with >10 mappings.

**P3. `_list_user_mappings_for_employee()` same SSM scan bug.**
Line 133: `MaxResults=10` without loop.

### Gateway Proxy (gateway_proxy.py, 500 lines)

**G1. Heavy SSM for every proxy request.**
Each proxy call resolves agent endpoint via 3-4 SSM GetParameter calls. Cached for 120s, but cache is in-memory (lost on restart).

**G2. Duplicate JWT validation.**
`_require_employee_auth()` (lines 54-85) reimplements JWT validation instead of using `shared.require_auth()`. Two codepaths = two places to fix if auth changes.

**G3. Public IP exposure via IMDS.**
`get_gateway_dashboard()` line 261-271: fetches EC2 public IP via IMDS and returns it as `directUrl`. This exposes internal infrastructure to the browser.

---

## 2. Solutions

### 2.1 Deduplicate `_list_user_mappings()`

Single implementation in `db.py` (already has `get_user_mappings()`). Remove from both `admin_im.py` and `bindings.py`.

### 2.2 Fix SSM pagination bugs

**admin_im.py `get_im_channels()`**: Replace inline SSM query with `db.get_user_mappings()` (DynamoDB, no pagination issues).

**portal.py `_find_channel_user_id()`**: Replace SSM scan with `db.get_user_mappings_for_employee(emp_id)` (already exists in db.py line 624).

**portal.py `_list_user_mappings_for_employee()`**: Same fix — use `db.get_user_mappings_for_employee()`.

### 2.3 Hardcoded ORG#acme

Replace with `db.ORG_PK` in `im_binding_check()`.

### 2.4 Add audit trail to bot info updates

`set_im_bot_info()`: add `db.create_audit_entry()` after successful write.

### 2.5 Remove shared agent routing code

Delete `resolve_route()` references to `route_to_shared_agent`. Or simplify the entire function since shared agents are removed.

### 2.6 Fix `_send_im_notification()` — best-effort for all channels

Not blocking — this is best-effort notification on disconnect. Add Slack (webhook), leave others as TODO comments. Discord DM requires OAuth which is complex.

### 2.7 Audit on admin pairing approve

`approve_pairing()` already has audit at line 332. Verify it covers all fields.

### 2.8 SSM dual-write cleanup plan

Phase 1 (now): Keep dual-write but add `# TODO: remove SSM write after migration` comments.
Phase 2 (future): After verifying tenant_router reads from DynamoDB MAPPING#, remove SSM writes.

### 2.9 Auth check on bindings endpoints

Verify these are protected by auth middleware. If not, add `require_role()`.

### 2.10 Portal SSM reduction

Replace SSM-based always-on detection with DynamoDB agent record (`deployMode` field). The agent's deploy mode is already in DynamoDB AGENT# record.

### 2.11 Gateway proxy JWT dedup

Replace `_require_employee_auth()` with `shared.require_auth()`. If the gateway_proxy module was intentionally standalone to avoid circular imports, document why.

### 2.12 Remove public IP exposure

Remove IMDS public IP fetch from `get_gateway_dashboard()`. Gateway Console should go through the proxy, not direct IP.

---

## 3. Implementation Plan

### Phase 1: admin_im.py fixes (P0)

| Task | Description |
|------|-------------|
| 1.1 | Fix `get_im_channels()` — use DynamoDB for channel counts instead of SSM inline query |
| 1.2 | Fix `im_binding_check()` — `ORG#acme` → `db.ORG_PK` |
| 1.3 | Add audit trail to `set_im_bot_info()` |
| 1.4 | Remove duplicated `_list_user_mappings()` — use `db.get_user_mappings()` |

### Phase 2: bindings.py cleanup (P0)

| Task | Description |
|------|-------------|
| 2.1 | Remove shared agent routing from `resolve_route()` |
| 2.2 | Remove duplicated `_list_user_mappings()` — use `db.get_user_mappings()` |
| 2.3 | Verify auth on bindings CRUD endpoints |

### Phase 3: portal.py SSM reduction (P1)

| Task | Description |
|------|-------------|
| 3.1 | Replace `_find_channel_user_id()` SSM scan with `db.get_user_mappings_for_employee()` |
| 3.2 | Replace `_list_user_mappings_for_employee()` SSM scan with db call |
| 3.3 | Replace always-on SSM detection with DynamoDB agent.deployMode |

### Phase 4: New admin capabilities (P1)

| Task | Description |
|------|-------------|
| 4.1 | IM channel health API — last message timestamp per channel from AUDIT# |
| 4.2 | Enrollment stats API — unbound employee count, multi-bind count |
| 4.3 | Batch unbind API — disconnect all employees from a channel (token rotation) |
| 4.4 | Validate `set_im_bot_info()` body with Pydantic model |

### Phase 5: gateway_proxy.py cleanup (P2)

| Task | Description |
|------|-------------|
| 5.1 | Remove public IP exposure from `get_gateway_dashboard()` |
| 5.2 | Document JWT validation standalone rationale or consolidate |

---

## 4. TODO

### Must-Do
- [ ] 1.1: Fix `get_im_channels()` SSM pagination bug
- [ ] 1.2: Fix hardcoded `ORG#acme`
- [ ] 1.3: Add audit to `set_im_bot_info()`
- [ ] 1.4: Deduplicate `_list_user_mappings()`
- [ ] 2.1: Remove shared agent routing code
- [ ] 2.2: Deduplicate `_list_user_mappings()` in bindings.py
- [ ] 2.3: Verify/add auth on bindings CRUD
- [ ] 3.1: Fix `_find_channel_user_id()` SSM scan
- [ ] 3.2: Fix `_list_user_mappings_for_employee()` SSM scan
- [ ] 4.1: IM channel health monitoring
- [ ] 4.2: Enrollment stats (unbound employees)
- [ ] 4.3: Batch unbind per channel
- [ ] 4.4: Pydantic validation on bot info update
- [ ] Unit tests

### Should-Do
- [ ] 3.3: Replace always-on SSM detection with DynamoDB
- [ ] 5.1: Remove public IP exposure
- [ ] 5.2: JWT validation consolidation

### Future
- [ ] Remove SSM dual-write (after migration verification)
- [ ] Add DM policy validation/warning in admin UI
- [ ] Expand `_send_im_notification()` to more channels
- [ ] Frontend updates for IM Channels page
- [ ] Fargate always-on IM binding redesign (separate session)
