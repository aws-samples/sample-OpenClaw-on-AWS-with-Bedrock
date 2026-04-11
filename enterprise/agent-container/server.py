"""
Agent Container HTTP server for Amazon Bedrock AgentCore.

Wraps `openclaw agent --session-id <tenant_id> --message <text> --json`
as a subprocess for each /invocations request.

Plan A: inject allowed tools into system prompt via SOUL.md prepend.
Plan E: audit response for blocked tool usage.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from permissions import read_permission_profile
from observability import log_agent_invocation, log_permission_denied
from safety import validate_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Path to openclaw binary (system install in container, nvm on EC2)
_OPENCLAW_CANDIDATES = [
    "/usr/local/bin/openclaw",
    "/usr/bin/openclaw",
]

_TOOL_PATTERN = re.compile(
    r'\b(shell|browser|file_write|code_execution|install_skill|load_extension|eval)\b',
    re.IGNORECASE,
)


def _find_openclaw() -> str:
    for p in _OPENCLAW_CANDIDATES:
        if os.path.isfile(p):
            return p
    # fallback: hope it's on PATH
    return "openclaw"


OPENCLAW_BIN = _find_openclaw()
logger.info("openclaw binary: %s", OPENCLAW_BIN)

# Track which tenants have had their workspace assembled
_assembled_tenants: set = set()
_assembly_lock = threading.Lock()

# Config version tracking — when IT changes global SOUL/KB, the version bumps.
# Every CONFIG_VERSION_CHECK_INTERVAL seconds, we query DynamoDB for the version.
# If it changed, all tenants are evicted from _assembled_tenants so they re-assemble
# on their next request (picking up the latest SOUL/KB from S3).
_config_version: str = ""
_config_version_checked_at: float = 0.0
_CONFIG_VERSION_CHECK_INTERVAL = 300  # seconds (5 minutes)

# Guardrail config read from environment variables set on the Runtime.
# Exec Runtime has no GUARDRAIL_ID → no guardrail enforcement.
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")


def _check_and_refresh_config_version() -> None:
    """Check DynamoDB CONFIG#global-version and clear assembly cache if changed.
    Called before each invocation; throttled to once per 5 minutes."""
    global _config_version, _config_version_checked_at
    now = time.time()
    if now - _config_version_checked_at < _CONFIG_VERSION_CHECK_INTERVAL:
        return
    _config_version_checked_at = now
    try:
        import boto3 as _b3cv
        ddb = _b3cv.resource("dynamodb", region_name=DYNAMODB_REGION)
        resp = ddb.Table(DYNAMODB_TABLE).get_item(
            Key={"PK": "ORG#acme", "SK": "CONFIG#global-version"})
        new_version = resp.get("Item", {}).get("version", "")
        if new_version and new_version != _config_version:
            logger.info(
                "Global config version changed: %s → %s — clearing assembly cache for %d tenants",
                _config_version or "(initial)", new_version, len(_assembled_tenants))
            _assembled_tenants.clear()
            _config_version = new_version
    except Exception as e:
        logger.warning("Config version check failed (non-fatal): %s", e)

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
S3_BUCKET = os.environ.get("S3_BUCKET", "openclaw-tenants-000000000000")
STACK_NAME = os.environ.get("STACK_NAME", "dev")
AWS_REGION_RUNTIME = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
DYNAMODB_REGION = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))


def _append_conversation_turn(tenant_id: str, user_message: str, assistant_reply: str, model: str, duration_ms: int):
    """Append a user+assistant turn to DynamoDB CONV# AND local daily memory file.

    The DynamoDB write enables Session Detail view in the Admin Console.
    The local memory file write ensures memory persists even for short sessions
    that never trigger OpenClaw Gateway's compaction threshold — the watchdog
    syncs workspace/memory/{date}.md to S3 within 60s, so the next session
    always has context regardless of session length.
    """
    from datetime import datetime, timezone
    ts_dt = datetime.now(timezone.utc)
    ts = ts_dt.isoformat()

    # 1. Write to DynamoDB for Session Detail view
    try:
        import boto3 as _b3_conv
        ddb = _b3_conv.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        org_pk = "ORG#acme"
        session_sk = f"SESSION#{tenant_id[:40]}"

        try:
            resp = table.get_item(Key={"PK": org_pk, "SK": session_sk})
            turns = int(resp.get("Item", {}).get("turns", 0))
        except Exception:
            turns = 0

        seq_base = (turns - 1) * 2
        table.put_item(Item={
            "PK": org_pk, "SK": f"CONV#{tenant_id[:40]}#{seq_base:04d}",
            "sessionId": tenant_id[:40], "seq": seq_base, "role": "user",
            "content": user_message[:2000], "ts": ts,
        })
        table.put_item(Item={
            "PK": org_pk, "SK": f"CONV#{tenant_id[:40]}#{seq_base + 1:04d}",
            "sessionId": tenant_id[:40], "seq": seq_base + 1, "role": "assistant",
            "content": assistant_reply[:4000], "ts": ts,
            "model": model, "durationMs": duration_ms,
        })
    except Exception as e:
        logger.warning("CONV# write failed (non-fatal): %s", e)

    # 2. Append to daily memory file — ensures memory persists for short sessions
    # that never trigger Gateway compaction. OpenClaw reads memory/*.md at session
    # start, so this guarantees continuity even for 1-message microVM sessions.
    try:
        workspace = os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace")
        memory_dir = os.path.join(workspace, "memory")
        os.makedirs(memory_dir, exist_ok=True)
        date_str = ts_dt.strftime("%Y-%m-%d")
        time_str = ts_dt.strftime("%H:%M UTC")
        daily_file = os.path.join(memory_dir, f"{date_str}.md")

        entry = (
            f"\n## {time_str}\n"
            f"**User:** {user_message[:300]}\n"
            f"**Agent:** {assistant_reply[:300]}\n"
        )
        with open(daily_file, "a", encoding="utf-8") as f:
            f.write(entry)
        logger.info("Memory checkpoint written: %s", daily_file)
    except Exception as e:
        logger.warning("Daily memory write failed (non-fatal): %s", e)


def _write_usage_to_dynamodb(tenant_id: str, base_id: str, usage: dict, model: str, duration_ms: int, message: str = ""):
    """Fire-and-forget: write usage metrics, session, and audit entry to DynamoDB.
    Runs in a background thread to avoid blocking the response."""
    try:
        import boto3 as _b3
        from datetime import datetime, timezone
        from decimal import Decimal

        ddb = _b3.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        org_pk = "ORG#acme"

        input_tokens = int(usage.get("input", 0))
        output_tokens = int(usage.get("output", 0))
        total_tokens = int(usage.get("total", input_tokens + output_tokens))

        # Estimate cost based on model (Nova 2 Lite: $0.30/$2.50 per 1M tokens)
        cost = Decimal(str(round(input_tokens * 0.30 / 1_000_000 + output_tokens * 2.50 / 1_000_000, 6)))

        # 1. Atomic increment USAGE#{base_id}#{date}
        table.update_item(
            Key={"PK": org_pk, "SK": f"USAGE#{base_id}#{today}"},
            UpdateExpression="SET #d = :date, agentId = :aid, model = :model, GSI1PK = :gsi1pk, GSI1SK = :gsi1sk ADD inputTokens :inp, outputTokens :out, requests :one, cost :cost",
            ExpressionAttributeNames={"#d": "date"},
            ExpressionAttributeValues={
                ":date": today,
                ":aid": base_id,
                ":model": model,
                ":inp": input_tokens,
                ":out": output_tokens,
                ":one": 1,
                ":cost": cost,
                ":gsi1pk": "TYPE#usage",
                ":gsi1sk": f"USAGE#{today}#{base_id}",
            },
        )

        # 2. Update or create SESSION#{tenant_id} — increment turns, update lastMessage
        # 'id' is set explicitly so the admin console can find sessions by ID without parsing the SK
        session_id = tenant_id[:40]
        table.update_item(
            Key={"PK": org_pk, "SK": f"SESSION#{session_id}"},
            UpdateExpression="SET id = :id, agentId = :aid, employeeId = :eid, #s = :status, lastActive = :now, GSI1PK = :gsi1pk, GSI1SK = :gsi1sk ADD turns :one, tokensUsed :tokens",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":id": session_id,
                ":aid": base_id,
                ":eid": base_id,
                ":status": "active",
                ":now": now.isoformat(),
                ":one": 1,
                ":tokens": total_tokens,
                ":gsi1pk": "TYPE#session",
                ":gsi1sk": f"SESSION#{session_id}",
            },
        )

        logger.info("DynamoDB usage written: %s tokens=%d cost=%s", base_id, total_tokens, cost)

        # 3. Write audit entry — makes ALL channels (Discord, Telegram, Portal) visible
        #    in the Admin Console Audit Center, not just Portal chat.
        #    Try to resolve the employee display name from DynamoDB EMP# record.
        actor_name = base_id
        try:
            emp_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{base_id}"})
            emp_item = emp_resp.get("Item", {})
            if emp_item.get("name"):
                actor_name = emp_item["name"]
        except Exception:
            pass

        # Detect channel from tenant_id prefix (wa__, tg__, dc__, sl__, port__, etc.)
        channel = "unknown"
        t_parts = tenant_id.split("__")
        if t_parts:
            ch_map = {"wa": "WhatsApp", "tg": "Telegram", "dc": "Discord",
                      "sl": "Slack", "ms": "Teams", "im": "iMessage",
                      "gc": "Google Chat", "web": "Web", "port": "Portal"}
            channel = ch_map.get(t_parts[0], t_parts[0].upper())

        detail_msg = message[:100] if message else "(no message)"
        audit_id = f"aud-{int(now.timestamp() * 1000)}"  # ms precision avoids overwrite
        table.put_item(Item={
            "PK": "ORG#acme",
            "SK": f"AUDIT#{audit_id}",
            "GSI1PK": "TYPE#audit",
            "GSI1SK": f"AUDIT#{audit_id}",
            "id": audit_id,
            "timestamp": now.isoformat(),
            "eventType": "agent_invocation",
            "actorId": base_id,
            "actorName": actor_name,
            "targetType": "agent",
            "targetId": base_id,
            "channel": channel,
            "detail": f"{channel} chat: {detail_msg}",
            "status": "success",
            "durationMs": duration_ms,
            "model": model,
        })
        logger.info("Audit entry written: %s channel=%s", audit_id, channel)

    except Exception as e:
        logger.warning("DynamoDB usage write failed (non-fatal): %s", e)


def _session_storage_has_workspace() -> bool:
    """Check if Session Storage restored a previous workspace.
    Session Storage mounts at WORKSPACE path and restores files from the previous session.
    If SOUL.md exists, the workspace was previously assembled and persisted."""
    soul_path = os.path.join(WORKSPACE, "SOUL.md")
    return os.path.isfile(soul_path) and os.path.getsize(soul_path) > 50


def _ensure_workspace_assembled(tenant_id: str) -> None:
    """Assemble workspace on first invocation for a tenant.
    Runs workspace_assembler.py to merge Global + Position + Personal SOUL.
    Thread-safe: only runs once per tenant per microVM lifecycle.

    Session Storage optimization: if the workspace was restored from a previous
    session and config_version hasn't changed, skip S3 download and assembly."""
    if tenant_id in _assembled_tenants or tenant_id == "unknown":
        return

    with _assembly_lock:
        if tenant_id in _assembled_tenants:
            return  # double-check after acquiring lock

        # Session Storage optimization: if workspace already has assembled files
        # AND global config hasn't changed, skip the full S3 download + assembly.
        # This reduces session resume from ~6s to ~0.5s.
        if _session_storage_has_workspace() and _config_version:
            # Config version is already loaded and hasn't changed since last check
            logger.info("Session Storage resume for tenant %s — workspace intact, config_version=%s",
                        tenant_id, _config_version)
            _assembled_tenants.add(tenant_id)
            return

        logger.info("First invocation for tenant %s — assembling workspace", tenant_id)

        # Extract base employee ID for S3 paths
        # Tenant ID formats:
        #   port__emp-carol__bbee1f93  → base = emp-carol (Portal, 3 parts)
        #   tg__emp-w5__a1b2c3d4      → base = emp-w5 (Telegram, 3 parts)
        #   unknown__1484960930608578580 → base = 1484960930608578580 (Discord via H2 Proxy, 2 parts)
        #   actions__a                 → base = a (H2 Proxy fallback, 2 parts)
        #   emp-carol                  → base = emp-carol (direct)
        base_id = tenant_id
        parts = tenant_id.split("__")
        if len(parts) >= 3:
            # channel__user_id__hash → take user_id (middle)
            base_id = parts[1]
        elif len(parts) == 2:
            # channel__user_id → take user_id (second part, the actual identifier)
            base_id = parts[1]

        # Resolve IM channel user IDs (e.g. Feishu OU ID) to employee IDs.
        # Checks DynamoDB MAPPING# first, SSM as fallback.
        if not base_id.startswith("emp-"):
            try:
                import boto3 as _b3_mapping
                ddb = _b3_mapping.resource("dynamodb", region_name=os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1")))
                table = ddb.Table(os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw")))
                channel_prefix = parts[0] if len(parts) >= 2 else ""
                # Try exact channel+userId key first
                resp_ddb = table.get_item(Key={"PK": "ORG#acme", "SK": f"MAPPING#{channel_prefix}__{base_id}"})
                ddb_item = resp_ddb.get("Item")
                if ddb_item:
                    resolved = ddb_item.get("employeeId", "")
                    logger.info("DynamoDB user-mapping resolved: %s__%s → %s", channel_prefix, base_id, resolved)
                    base_id = resolved
                else:
                    # Scan all MAPPING# items for this channelUserId (handles channel-prefix mismatch)
                    from boto3.dynamodb.conditions import Key as _Key, Attr as _Attr
                    scan_resp = table.query(
                        KeyConditionExpression=_Key("PK").eq("ORG#acme") & _Key("SK").begins_with("MAPPING#"),
                        FilterExpression=_Attr("channelUserId").eq(base_id),
                    )
                    if scan_resp.get("Items"):
                        resolved = scan_resp["Items"][0].get("employeeId", "")
                        logger.info("DynamoDB user-mapping (scan) resolved: %s → %s", base_id, resolved)
                        base_id = resolved
                    else:
                        # SSM fallback for backward compat
                        ssm = _b3_mapping.client("ssm", region_name=AWS_REGION_RUNTIME)
                        for mapping_key in [f"{channel_prefix}__{base_id}", base_id]:
                            try:
                                resp = ssm.get_parameter(
                                    Name=f"/openclaw/{STACK_NAME}/user-mapping/{mapping_key}")
                                resolved = resp["Parameter"]["Value"]
                                logger.info("SSM user-mapping fallback resolved: %s → %s", mapping_key, resolved)
                                base_id = resolved
                                break
                            except Exception:
                                pass
            except Exception as e:
                logger.warning("User-mapping lookup failed: %s", e)

        # 0. Load shared OpenClaw credentials (discord allowFrom list) into microVM
        #    This is how the microVM knows which Discord users are approved.
        #    The EC2 updates this file on every pairing approval and uploads to S3.
        creds_dir = "/root/.openclaw/credentials"
        os.makedirs(creds_dir, exist_ok=True)
        try:
            subprocess.run(
                ["aws", "s3", "cp",
                 f"s3://{S3_BUCKET}/_shared/openclaw-creds/discord-default-allowFrom.json",
                 f"{creds_dir}/discord-default-allowFrom.json", "--quiet"],
                capture_output=True, text=True, timeout=10
            )
            logger.info("Discord allowFrom credentials loaded into microVM")
        except Exception as e:
            logger.warning("Could not load discord credentials (non-fatal): %s", e)

        # 1. Sync tenant's personal workspace from S3 using BASE ID
        # IMPORTANT: Use 'cp --recursive' instead of 'sync' to force S3 → local overwrite.
        # The entrypoint.sh initial sync uses tenant=unknown, creating empty workspace files.
        # 'aws s3 sync' won't overwrite these because the local files are newer.
        # 'aws s3 cp --recursive' always downloads from S3, ensuring seed data (MEMORY.md,
        # USER.md, memory/*.md) is correctly loaded.
        s3_base = f"s3://{S3_BUCKET}/{base_id}"
        try:
            subprocess.run(
                ["aws", "s3", "cp", f"{s3_base}/workspace/", f"{WORKSPACE}/",
                 "--recursive", "--quiet"],
                capture_output=True, text=True, timeout=30
            )
            logger.info("S3 workspace copied for tenant %s (base: %s)", tenant_id, base_id)
        except Exception as e:
            logger.warning("S3 cp failed for %s: %s", tenant_id, e)

        # 2. Run workspace_assembler.py to merge three-layer SOUL
        assembler = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace_assembler.py")
        if os.path.isfile(assembler):
            try:
                result = subprocess.run(
                    [sys.executable, assembler,
                     "--tenant", tenant_id,
                     "--workspace", WORKSPACE,
                     "--bucket", S3_BUCKET,
                     "--stack", STACK_NAME,
                     "--region", AWS_REGION_RUNTIME],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    logger.info("Workspace assembled for %s: %s", tenant_id, result.stdout.strip().split('\n')[-1])
                else:
                    logger.warning("Workspace assembly failed for %s: %s", tenant_id, result.stderr[:200])
            except Exception as e:
                logger.warning("Workspace assembly error for %s: %s", tenant_id, e)
        else:
            logger.warning("workspace_assembler.py not found at %s", assembler)

        # 3. Plan A + Twin + KB + Language context:
        # ALL moved to workspace_assembler.py _build_context_block().
        # server.py no longer modifies SOUL.md. The assembler writes a complete
        # SOUL.md in a single pass (3-layer merge + context block).

        # 4. Re-source skill env vars (in case skills were loaded)
        skill_env = "/tmp/skill_env.sh"
        if os.path.isfile(skill_env):
            try:
                with open(skill_env) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("export ") and "=" in line:
                            kv = line[7:]
                            key, _, val = kv.partition("=")
                            os.environ[key] = val.strip("'\"")
            except IOError:
                pass

        # Write tenant_id so watchdog sync knows where to write back
        try:
            with open("/tmp/tenant_id", "w") as f:
                f.write(tenant_id)
            with open("/tmp/base_tenant_id", "w") as f:
                f.write(base_id)
            logger.info("Base tenant ID written: %s", base_id)
        except IOError:
            pass

        # Synthesize MEMORY.md from daily memory files if it's empty.
        # In serverless AgentCore microVMs, the OpenClaw Gateway compaction daemon
        # never runs persistently, so MEMORY.md stays at "# Memory" (9 bytes) forever.
        # We fix this by concatenating recent daily memory files into MEMORY.md at
        # session start so the agent has cross-session context.
        try:
            memory_md_path = os.path.join(WORKSPACE, "MEMORY.md")
            memory_dir = os.path.join(WORKSPACE, "memory")
            current_content = ""
            if os.path.isfile(memory_md_path):
                with open(memory_md_path) as f:
                    current_content = f.read().strip()

            # Synthesize only if MEMORY.md is empty / just a header
            if len(current_content) < 50 and os.path.isdir(memory_dir):
                daily_files = sorted(
                    [f for f in os.listdir(memory_dir) if f.endswith(".md")],
                    reverse=True)[:3]  # last 3 days
                if daily_files:
                    parts = ["# Memory\n\n*Auto-synthesized from recent conversations*\n"]
                    for fname in daily_files:
                        fpath = os.path.join(memory_dir, fname)
                        try:
                            with open(fpath) as f:
                                content = f.read().strip()
                            if content:
                                date_str = fname.replace(".md", "")
                                parts.append(f"\n## {date_str}\n{content[:3000]}")
                        except Exception:
                            pass
                    if len(parts) > 1:
                        with open(memory_md_path, "w") as f:
                            f.write("\n".join(parts))
                        logger.info("MEMORY.md synthesized from %d daily files for %s",
                                    len(daily_files), base_id)
        except Exception as e:
            logger.warning("MEMORY.md synthesis failed (non-fatal): %s", e)

        # 5. Dynamic agent config: read from DynamoDB and update openclaw.json
        # Hierarchy: employee override > position override > global default
        # Covers: model, memory compaction, context window, language preference
        try:
            import boto3 as _b3_model
            ddb = _b3_model.resource("dynamodb", region_name=DYNAMODB_REGION)
            table = ddb.Table(DYNAMODB_TABLE)

            # Read position for this employee from DynamoDB
            pos_id = ""
            try:
                emp_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{base_id}"})
                pos_id = emp_resp.get("Item", {}).get("positionId", "")
            except Exception:
                pass

            # --- Model ---
            model_config_resp = table.get_item(Key={"PK": "ORG#acme", "SK": "CONFIG#model"})
            if "Item" in model_config_resp:
                mc = model_config_resp["Item"]
                emp_model_overrides = mc.get("employeeOverrides", {})
                pos_model_overrides = mc.get("positionOverrides", {})

                if base_id in emp_model_overrides:
                    new_model_id = emp_model_overrides[base_id].get("modelId", "")
                    logger.info("Employee model override: %s → %s", base_id, new_model_id)
                elif pos_id and pos_id in pos_model_overrides:
                    new_model_id = pos_model_overrides[pos_id].get("modelId", "")
                    logger.info("Position model override: %s → %s", pos_id, new_model_id)
                else:
                    new_model_id = mc.get("default", {}).get("modelId", "")

                if new_model_id:
                    oc_config_path = os.path.expanduser("~/.openclaw/openclaw.json")
                    if os.path.isfile(oc_config_path):
                        with open(oc_config_path) as f:
                            oc_config = json.load(f)
                        old_model = os.environ.get("BEDROCK_MODEL_ID", "global.amazon.nova-2-lite-v1:0")
                        oc_json_str = json.dumps(oc_config)
                        oc_json_str = oc_json_str.replace(old_model, new_model_id)
                        with open(oc_config_path, "w") as f:
                            f.write(oc_json_str)
                        os.environ["BEDROCK_MODEL_ID"] = new_model_id
                        logger.info("Model updated to %s", new_model_id)

            # --- Agent Config (compaction, context, language) ---
            agent_cfg_resp = table.get_item(Key={"PK": "ORG#acme", "SK": "CONFIG#agent-config"})
            if "Item" in agent_cfg_resp:
                agent_cfg = agent_cfg_resp["Item"]
                emp_cfg  = agent_cfg.get("employeeConfig", {}).get(base_id, {})
                pos_cfg  = agent_cfg.get("positionConfig", {}).get(pos_id, {}) if pos_id else {}
                eff_cfg  = {**pos_cfg, **emp_cfg}  # employee wins over position

                oc_config_path = os.path.expanduser("~/.openclaw/openclaw.json")
                if eff_cfg and os.path.isfile(oc_config_path):
                    with open(oc_config_path) as f:
                        oc = json.load(f)
                    changed = False

                    # Memory compaction
                    if "recentTurnsPreserve" in eff_cfg:
                        oc.setdefault("agents", {}).setdefault("defaults", {}).setdefault("compaction", {})["recentTurnsPreserve"] = int(eff_cfg["recentTurnsPreserve"])
                        changed = True
                    if "compactionMode" in eff_cfg:
                        oc.setdefault("agents", {}).setdefault("defaults", {}).setdefault("compaction", {})["mode"] = eff_cfg["compactionMode"]
                        changed = True

                    # Context window / max tokens
                    if "maxTokens" in eff_cfg:
                        for provider in oc.get("models", {}).get("providers", {}).values():
                            for m in provider.get("models", []):
                                m["maxTokens"] = int(eff_cfg["maxTokens"])
                        changed = True

                    if changed:
                        with open(oc_config_path, "w") as f:
                            json.dump(oc, f, indent=2)
                        logger.info("Agent config applied for %s: %s", base_id, list(eff_cfg.keys()))

                    # Language + KB injection: ALL moved to workspace_assembler.py
                    # _build_context_block(). server.py no longer modifies SOUL.md.

        except Exception as e:
            logger.warning("Dynamic agent config failed (non-fatal): %s", e)

        # CHANNELS.md is generated by workspace_assembler.py (step 7).
        # It runs both at container startup (entrypoint.sh bg worker) and here on
        # first request, so always-on containers have it before the first message.

        # In EFS mode, the OpenClaw Gateway (started at container boot) reads SOUL.md
        # from its default workspace path: HOME/.openclaw/workspace/ (= /.openclaw/workspace/).
        # OPENCLAW_WORKSPACE points to the EFS path, but the Gateway may have cached its
        # workspace from startup (before the EFS was populated). Mirror the assembled
        # SOUL.md to the Gateway's default workspace so it always has the latest version.
        default_workspace = os.path.expanduser("~/.openclaw/workspace")
        if default_workspace != WORKSPACE:
            try:
                import shutil
                os.makedirs(default_workspace, exist_ok=True)
                for fname in ["SOUL.md", "AGENTS.md", "TOOLS.md", "IDENTITY.md", "CHANNELS.md"]:
                    src = os.path.join(WORKSPACE, fname)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(default_workspace, fname))
                logger.info("Mirrored workspace files to Gateway default path: %s", default_workspace)
            except Exception as e:
                logger.warning("Gateway workspace mirror failed (non-fatal): %s", e)

        # Write SOUL hash + config version to DynamoDB SESSION# for admin monitoring.
        # Admin Console can display this to verify the agent is running the correct config.
        try:
            import hashlib as _hl
            soul_path = os.path.join(WORKSPACE, "SOUL.md")
            soul_hash = ""
            if os.path.isfile(soul_path):
                with open(soul_path, "rb") as f:
                    soul_hash = _hl.sha256(f.read()).hexdigest()[:16]
            import boto3 as _b3sh
            from datetime import datetime, timezone
            ddb_sh = _b3sh.resource("dynamodb", region_name=DYNAMODB_REGION)
            session_key = tenant_id[:40]
            ddb_sh.Table(DYNAMODB_TABLE).update_item(
                Key={"PK": "ORG#acme", "SK": f"SESSION#{session_key}"},
                UpdateExpression="SET soulHash = :h, configVersion = :v, assembledAt = :t",
                ExpressionAttributeValues={
                    ":h": soul_hash,
                    ":v": _config_version or "initial",
                    ":t": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info("SOUL hash written to DynamoDB: %s config=%s", soul_hash, _config_version)
        except Exception as e:
            logger.warning("SOUL hash write failed (non-fatal): %s", e)

        _assembled_tenants.add(tenant_id)
        logger.info("Workspace ready for tenant %s", tenant_id)



def _build_system_prompt(tenant_id: str) -> str:
    """Plan A: build constraint text to prepend to SOUL.md."""
    try:
        profile = read_permission_profile(tenant_id)
        allowed = profile.get("tools", ["web_search"])
        blocked = [t for t in ["shell", "browser", "file", "file_write", "code_execution",
                                "install_skill", "load_extension", "eval"]
                   if t not in allowed]
    except Exception:
        allowed = ["web_search"]
        blocked = ["shell", "browser", "file", "file_write", "code_execution",
                   "install_skill", "load_extension", "eval"]

    lines = [f"Allowed tools for this session: {', '.join(allowed)}."]
    if blocked:
        lines.append(
            f"You MUST NOT use these tools: {', '.join(blocked)}. "
            "If the user requests an action requiring a blocked tool, "
            "explain that you don't have permission."
        )
    return " ".join(lines)


def _audit_response(tenant_id: str, response_text: str, allowed_tools: list) -> None:
    """Plan E: scan response for blocked tool usage."""
    matches = _TOOL_PATTERN.findall(response_text)
    if not matches:
        return
    for tool in set(t.lower() for t in matches):
        if tool not in allowed_tools:
            log_permission_denied(
                tenant_id=tenant_id,
                tool_name=tool,
                cedar_decision="RESPONSE_AUDIT",
                request_id=None,
            )
            logger.warning("AUDIT: blocked tool '%s' in response tenant_id=%s", tool, tenant_id)


def _sync_heartbeat_and_memory(base_id: str) -> None:
    """Immediately sync HEARTBEAT.md and memory/*.md to S3 after each invocation.

    AgentCore microVMs may receive SIGKILL (not SIGTERM) after returning a response,
    which bypasses entrypoint.sh cleanup(). This function ensures reminders and per-turn
    memory reach S3 so the next session can load them — even if the microVM is killed
    immediately after.
    """
    if not base_id or base_id == "unknown":
        return
    sync_target = f"s3://{S3_BUCKET}/{base_id}/workspace/"
    try:
        # Sync memory directory (daily checkpoint files written by _append_conversation_turn)
        subprocess.run(
            ["aws", "s3", "sync", os.path.join(WORKSPACE, "memory") + "/",
             f"{sync_target}memory/", "--quiet"],
            capture_output=True, text=True, timeout=15,
        )
        # Copy HEARTBEAT.md if it exists (created by OpenClaw when user sets a reminder)
        heartbeat_path = os.path.join(WORKSPACE, "HEARTBEAT.md")
        if os.path.isfile(heartbeat_path):
            subprocess.run(
                ["aws", "s3", "cp", heartbeat_path, f"{sync_target}HEARTBEAT.md", "--quiet"],
                capture_output=True, text=True, timeout=10,
            )
            logger.info("HEARTBEAT.md synced to S3 for %s", base_id)
        # Copy MEMORY.md if it exists (updated by Gateway compaction)
        memory_md_path = os.path.join(WORKSPACE, "MEMORY.md")
        if os.path.isfile(memory_md_path):
            subprocess.run(
                ["aws", "s3", "cp", memory_md_path, f"{sync_target}MEMORY.md", "--quiet"],
                capture_output=True, text=True, timeout=10,
            )
    except Exception as e:
        logger.warning("Post-invocation S3 sync failed (non-fatal): %s", e)


def invoke_openclaw(tenant_id: str, message: str, timeout: int = 300, max_retries: int = 2) -> dict:
    """
    Run openclaw agent CLI with automatic retry on transient failures.
    Retries on: empty output, JSON parse errors, timeouts.
    Does NOT retry on successful responses (even if the content is an error message).
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return _invoke_openclaw_once(tenant_id, message, timeout)
        except RuntimeError as e:
            last_error = e
            if attempt < max_retries:
                wait = (attempt + 1) * 2  # 2s, 4s linear backoff
                logger.warning(
                    "openclaw retry %d/%d after %ds: %s",
                    attempt + 1, max_retries, wait, e,
                )
                time.sleep(wait)
    raise last_error


def _invoke_openclaw_once(tenant_id: str, message: str, timeout: int = 300) -> dict:
    """
    Run: openclaw agent --session-id <tenant_id> --message <message> --json
    Returns parsed JSON result dict.
    Runs as 'ubuntu' user if we're root (EC2 host) so openclaw config is accessible.
    """
    env = os.environ.copy()

    # Inject skill API keys from /tmp/skill_env.sh (written by skill_loader.py)
    skill_env_file = "/tmp/skill_env.sh"
    if os.path.isfile(skill_env_file):
        try:
            with open(skill_env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export ") and "=" in line:
                        kv = line[7:]  # strip "export "
                        key, _, val = kv.partition("=")
                        # Strip surrounding quotes
                        val = val.strip("'\"")
                        env[key] = val
        except IOError:
            pass

    # Ensure node is on PATH for nvm installs (glob to avoid hardcoded version)
    import glob as _glob
    nvm_bins = _glob.glob("/home/ubuntu/.nvm/versions/node/*/bin")
    if nvm_bins:
        env["PATH"] = nvm_bins[0] + ":" + env.get("PATH", "")
        env["HOME"] = "/home/ubuntu"

    openclaw_cmd = [
        OPENCLAW_BIN,
        "agent",
        "--session-id", tenant_id,
        "--message", message,
        "--json",
        "--timeout", str(timeout),
    ]

    # If running as root on EC2 host (not inside an ECS container), sudo to ubuntu
    # so openclaw can find its config at /home/ubuntu/.openclaw/.
    # In ECS containers, openclaw.json is at $HOME/.openclaw/ (HOME=/ for root process)
    # and /home/ubuntu exists from the base image — using sudo would look for config
    # in /home/ubuntu/.openclaw/ (not found) and lose the workspace path.
    # Detect ECS via ECS_CONTAINER_METADATA_URI_V4 env var (set by Fargate automatically).
    run_env = None  # None = inherit current process env (used in container as ubuntu)
    in_ecs = bool(os.environ.get("ECS_CONTAINER_METADATA_URI_V4"))
    if os.geteuid() == 0 and os.path.isdir("/home/ubuntu") and not in_ecs:
        path_val = env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        aws_region = env.get("AWS_REGION", "us-east-1")
        workspace_val = env.get("OPENCLAW_WORKSPACE", WORKSPACE)
        cmd = [
            "sudo", "-u", "ubuntu",
            "env",
            f"PATH={path_val}",
            "HOME=/home/ubuntu",
            f"AWS_REGION={aws_region}",
            f"AWS_DEFAULT_REGION={aws_region}",
            f"OPENCLAW_WORKSPACE={workspace_val}",
            "OPENCLAW_SKIP_ONBOARDING=1",
        ] + openclaw_cmd
        run_env = None  # let sudo handle the environment
    else:
        cmd = openclaw_cmd
        run_env = env  # pass env in container (running as ubuntu already)

    logger.info("Invoking openclaw tenant_id=%s cmd=%s", tenant_id, " ".join(cmd[:5]))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"openclaw timed out after {timeout}s")

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if stderr:
        # openclaw logs info/warnings to stderr — log at WARNING for visibility
        for line in stderr.splitlines():
            logger.warning("[openclaw stderr] %s", line)

    # OpenClaw may write JSON response to stderr when Gateway fallback occurs
    # ("Gateway agent failed; falling back to embedded" → JSON goes to stderr)
    if not stdout and stderr:
        json_start_stderr = stderr.find('{')
        if json_start_stderr != -1:
            logger.info("JSON found in stderr (Gateway fallback mode), using stderr as output")
            stdout = stderr[json_start_stderr:]

    if not stdout:
        raise RuntimeError(f"openclaw returned empty output (exit={result.returncode})")

    # Find the first JSON object in stdout (may have log lines before it)
    json_start = stdout.find('{')
    if json_start == -1:
        raise RuntimeError(f"No JSON in openclaw output: {stdout[:200]}")

    # Use JSONDecoder to parse only the first complete JSON object
    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(stdout, json_start)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse openclaw JSON: {e} — output: {stdout[:200]}")

    return data


def _apply_guardrail(text: str, source: str, tenant_id: str) -> str:
    """Apply Bedrock Guardrail to text.  Returns the blockedMessaging string if
    content was blocked/filtered; returns empty string if content passes.
    source must be 'INPUT' or 'OUTPUT'.
    Logs a guardrail_block audit event to DynamoDB if blocked."""
    try:
        import boto3 as _b3gr
        region = os.environ.get("AWS_REGION", "us-east-1")
        bedrock = _b3gr.client("bedrock-runtime", region_name=region)
        resp = bedrock.apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            source=source,
            content=[{"text": {"text": text}}],
        )
        action = resp.get("action", "NONE")
        if action in ("GUARDRAIL_INTERVENED",):
            # Extract the blockedMessaging from outputs
            blocked_msg = ""
            for out in resp.get("outputs", []):
                t = out.get("text", "")
                if t:
                    blocked_msg = t
                    break
            if not blocked_msg:
                blocked_msg = "该话题涉及未公开业务信息。根据合规政策，AI 助手无法提供相关内容，请联系合规部门。"

            # Log guardrail_block audit event (fire-and-forget)
            policy_name = ""
            assessments = resp.get("assessments", [])
            if assessments:
                topics = assessments[0].get("topicPolicy", {}).get("topics", [])
                if topics:
                    policy_name = topics[0].get("name", "")

            threading.Thread(
                target=_write_guardrail_block_to_dynamodb,
                args=(tenant_id, text[:200], source, policy_name),
                daemon=True,
            ).start()

            logger.info("Guardrail %s BLOCKED source=%s tenant=%s policy=%s", GUARDRAIL_ID, source, tenant_id, policy_name)
            return blocked_msg
        return ""
    except Exception as e:
        logger.warning("Guardrail check failed (non-fatal, allowing): %s", e)
        return ""


def _write_guardrail_block_to_dynamodb(tenant_id: str, input_snippet: str, source: str, policy_name: str):
    """Write a guardrail_block audit event to DynamoDB."""
    try:
        import boto3 as _b3gb
        from datetime import datetime, timezone
        ddb = _b3gb.resource("dynamodb", region_name=DYNAMODB_REGION)
        table = ddb.Table(DYNAMODB_TABLE)
        now = datetime.now(timezone.utc)
        audit_id = f"grd-{int(now.timestamp() * 1000)}"

        # Resolve display name
        base_id = tenant_id
        parts = tenant_id.split("__")
        if len(parts) >= 2:
            base_id = parts[1]
        try:
            with open("/tmp/base_tenant_id") as f:
                resolved = f.read().strip()
                if resolved and resolved != "unknown":
                    base_id = resolved
        except Exception:
            pass

        actor_name = base_id
        try:
            emp_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{base_id}"})
            emp_item = emp_resp.get("Item", {})
            if emp_item.get("name"):
                actor_name = emp_item["name"]
        except Exception:
            pass

        table.put_item(Item={
            "PK": "ORG#acme",
            "SK": f"AUDIT#{audit_id}",
            "GSI1PK": "TYPE#audit",
            "GSI1SK": f"AUDIT#{audit_id}",
            "id": audit_id,
            "timestamp": now.isoformat(),
            "eventType": "guardrail_block",
            "actorId": base_id,
            "actorName": actor_name,
            "targetType": "guardrail",
            "targetId": GUARDRAIL_ID,
            "guardrailId": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION,
            "guardrailSource": source,
            "guardrailPolicy": policy_name,
            "detail": f"Guardrail blocked {source.lower()}: {input_snippet}",
            "status": "blocked",
        })
    except Exception as e:
        logger.warning("Guardrail block audit write failed (non-fatal): %s", e)


class AgentCoreHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # noqa: A002
        logger.info(format, *args)

    def do_GET(self):
        if self.path == "/ping":
            self._respond(200, {"status": "Healthy", "time_of_last_update": int(time.time())})
        elif self.path == "/gateway-dashboard":
            self._handle_gateway_dashboard()
        elif self.path == "/gateway-approve-pairing":
            self._handle_gateway_approve_pairing()
        else:
            self._respond(404, {"error": "not found"})

    def _handle_gateway_dashboard(self):
        """Run `openclaw dashboard --no-open` and return the dashboard URL.
        This generates a fresh pairing token each time, so the caller can
        open the Gateway Console without needing prior pairing setup."""
        try:
            env = os.environ.copy()
            env["HOME"] = os.environ.get("HOME", "/root")
            env["PATH"] = "/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
            result = subprocess.run(
                [OPENCLAW_BIN, "dashboard", "--no-open"],
                capture_output=True, text=True, timeout=45, env=env,
            )
            output = result.stdout + result.stderr
            # Extract URL: "Dashboard URL: http://127.0.0.1:18789/#token=abc123..."
            url_match = re.search(r'(https?://\S+#token=\S+)', output)
            if url_match:
                url = url_match.group(1)
                # Extract components
                gw_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
                dash_token_match = re.search(r'#token=([a-f0-9]+)', url)
                dash_token = dash_token_match.group(1) if dash_token_match else ""
                self._respond(200, {
                    "url": url,
                    "gatewayToken": gw_token,
                    "dashboardToken": dash_token,
                    "port": 18789,
                })
            else:
                logger.warning("gateway-dashboard: no URL in output: %s", output[-200:])
                self._respond(500, {"error": "Gateway dashboard URL not found", "output": output[-300:]})
        except subprocess.TimeoutExpired:
            self._respond(504, {"error": "Gateway dashboard timed out"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _handle_gateway_approve_pairing(self):
        """Auto-approve the most recent pending device pairing request.
        Called by the admin console after the browser opens the Gateway Console
        and creates a pending pairing request."""
        try:
            env = os.environ.copy()
            env["HOME"] = os.environ.get("HOME", "/root")
            env["PATH"] = "/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
            gw_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
            cmd = [OPENCLAW_BIN, "devices", "approve", "--latest", "--json"]
            if gw_token:
                cmd.extend(["--token", gw_token])
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, env=env,
            )
            output = result.stdout + result.stderr
            logger.info("gateway-approve-pairing: exit=%d output=%s", result.returncode, output[:300])
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                except (json.JSONDecodeError, ValueError):
                    data = {"raw": output[:300]}
                self._respond(200, {"approved": True, "detail": data})
            else:
                self._respond(200, {"approved": False, "reason": output[:300]})
        except subprocess.TimeoutExpired:
            self._respond(504, {"error": "Approve pairing timed out"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_POST(self):
        if self.path != "/invocations":
            self._respond(404, {"error": "not found"})
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid json"})
            return

        # Extract tenant_id from headers or payload
        _file_tenant = ""
        try:
            with open("/tmp/tenant_id") as f:
                _file_tenant = f.read().strip()
        except Exception:
            pass

        tenant_id = (
            self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")
            or self.headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
            or payload.get("runtimeSessionId")
            or payload.get("sessionId")
            or payload.get("tenant_id")
            or _file_tenant
            or "unknown"
        )

        message = validate_message(
            payload.get("prompt") or payload.get("message") or str(payload)
        )

        logger.info("Invocation tenant_id=%s message_len=%d", tenant_id, len(message))
        self._handle_invocation(tenant_id, message, payload)

    def _handle_invocation(self, tenant_id: str, message: str, payload: dict):
        # Check if global config (SOUL/KB) changed — evicts stale assembly cache
        _check_and_refresh_config_version()

        # ── Guardrail INPUT check ─────────────────────────────────────────────
        # Reads GUARDRAIL_ID from env (set per-Runtime). Exec Runtime has no
        # GUARDRAIL_ID so this is a no-op for exec agents.
        if GUARDRAIL_ID:
            blocked_msg = _apply_guardrail(message, source="INPUT", tenant_id=tenant_id)
            if blocked_msg:
                self._respond(200, {"response": blocked_msg, "status": "guardrail_blocked", "guardrailId": GUARDRAIL_ID})
                return

        # Check session takeover — if admin has taken over, skip agent invocation
        stack = os.environ.get("STACK_NAME", "openclaw")
        region = os.environ.get("AWS_REGION", "us-east-1")
        session_key = tenant_id[:40]
        try:
            import boto3 as _b3tk
            ssm_tk = _b3tk.client("ssm", region_name=region)
            admin_param = ssm_tk.get_parameter(
                Name=f"/openclaw/{stack}/sessions/{session_key}/takeover")
            admin_id = admin_param["Parameter"]["Value"]
            logger.info("Session %s is in takeover by %s — skipping agent", session_key, admin_id)
            self._respond(200, {
                "response": "",
                "status": "takeover",
                "takenOverBy": admin_id,
                "message": "Session is being managed by a human admin.",
            })
            return
        except Exception:
            pass  # Not in takeover, proceed normally

        # Ensure workspace is assembled for this tenant (first invocation only)
        _ensure_workspace_assembled(tenant_id)

        start_ms = int(time.time() * 1000)
        try:
            timeout = int(payload.get("timeout", 300))
            data = invoke_openclaw(tenant_id, message, timeout=timeout)
            duration_ms = int(time.time() * 1000) - start_ms

            # Extract text from openclaw JSON response.
            # Embedded mode:  {"payloads": [...], "meta": {...}}
            # Gateway mode:   {"runId": "...", "result": {"payloads": [...], "meta": {...}}}
            result_block = data.get("result", data)  # unwrap Gateway's "result" wrapper
            payloads = result_block.get("payloads", [])
            response_text = " ".join(
                p.get("text", "") for p in payloads if p.get("text")
            ).strip()

            if not response_text:
                response_text = result_block.get("text", data.get("text", ""))
            if not response_text:
                logger.warning("Empty response_text from openclaw, raw data keys: %s", list(data.keys()))
                response_text = "(no response)"

            # ── Guardrail OUTPUT check ────────────────────────────────────────
            if GUARDRAIL_ID:
                blocked_msg = _apply_guardrail(response_text, source="OUTPUT", tenant_id=tenant_id)
                if blocked_msg:
                    self._respond(200, {"response": blocked_msg, "status": "guardrail_blocked", "guardrailId": GUARDRAIL_ID})
                    return

            # Plan E audit
            try:
                profile = read_permission_profile(tenant_id)
                allowed = profile.get("tools", ["web_search"])
            except Exception:
                allowed = ["web_search"]
            _audit_response(tenant_id, response_text, allowed)

            # Extract model usage for observability
            meta = result_block.get("meta", data.get("meta", {}))
            agent_meta = meta.get("agentMeta", {})
            model = agent_meta.get("model", "unknown")
            usage = agent_meta.get("usage", {})

            log_agent_invocation(
                tenant_id=tenant_id,
                tools_used=[],
                duration_ms=duration_ms,
                status="success",
            )
            logger.info(
                "Response tenant_id=%s duration_ms=%d model=%s tokens=%s text_len=%d",
                tenant_id, duration_ms, model, usage.get("total", "?"), len(response_text),
            )

            # Fire-and-forget: write usage to DynamoDB in background thread
            base_id = tenant_id
            parts = tenant_id.split("__")
            if len(parts) >= 3:
                base_id = parts[1]
            elif len(parts) == 2:
                base_id = parts[1]
            # Use resolved base_id from workspace assembly if available
            try:
                with open("/tmp/base_tenant_id") as f:
                    resolved = f.read().strip()
                    if resolved and resolved != "unknown":
                        base_id = resolved
            except Exception:
                pass

            threading.Thread(
                target=_write_usage_to_dynamodb,
                args=(tenant_id, base_id, usage, model, duration_ms, message),
                daemon=True,
            ).start()

            # Playground sessions are read-only: don't write conversation turns
            # or sync memory back to the employee's S3 workspace.
            is_playground = tenant_id.startswith("pgnd__")

            if not is_playground:
                # Fire-and-forget: write conversation turn to DynamoDB for Session Detail view
                threading.Thread(
                    target=_append_conversation_turn,
                    args=(tenant_id, message, response_text, model, duration_ms),
                    daemon=True,
                ).start()

                # Fire-and-forget: immediately sync HEARTBEAT.md + memory to S3 after each turn.
                # AgentCore microVMs may be killed (SIGKILL) after the response without SIGTERM,
                # bypassing the cleanup() flush. Syncing here ensures reminders and memory
                # reach S3 regardless of how the microVM terminates.
                threading.Thread(
                    target=_sync_heartbeat_and_memory,
                    args=(base_id,),
                    daemon=True,
                ).start()

            self._respond(200, {
                "response": response_text,
                "status": "success",
                "model": model,
                "usage": usage,
            })

        except Exception as e:
            duration_ms = int(time.time() * 1000) - start_ms
            log_agent_invocation(tenant_id=tenant_id, tools_used=[], duration_ms=duration_ms, status="error")
            logger.error("Invocation failed tenant_id=%s error=%s", tenant_id, e)
            self._respond(500, {"error": str(e)})

    def _respond(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), AgentCoreHandler)
    logger.info("HTTP server listening on port %d", port)
    logger.info("openclaw binary: %s", OPENCLAW_BIN)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
