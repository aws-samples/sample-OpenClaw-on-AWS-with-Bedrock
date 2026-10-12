"""
Workspace Assembler — Merges three-layer workspace for Agent runtime.

This is the critical bridge between Admin Console and Agent Core runtime.
It assembles the final workspace by merging:
  1. Global layer (_shared/soul/global/) — IT locked, all agents
  2. Position layer (_shared/soul/positions/{pos_id}/) — department managed
  3. Personal layer ({tenant_id}/workspace/) — employee's own files

The merged SOUL.md is what OpenClaw actually reads on every session start.

Called by entrypoint.sh AFTER s3 sync but BEFORE OpenClaw starts processing.

Usage:
  python workspace_assembler.py \
    --tenant TENANT_ID \
    --workspace /tmp/workspace \
    --bucket openclaw-tenants-xxx \
    --stack openclaw-prod \
    --region us-east-1
"""

import argparse
import json
import logging
import os
import subprocess

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def read_s3(s3, bucket: str, key: str) -> str:
    """Read a text file from S3, return empty string on failure."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode("utf-8")
    except ClientError:
        return ""


def get_tenant_position(ssm, stack_name: str, tenant_id: str) -> str:
    """Get the position ID for a tenant from DynamoDB.

    Resolution order:
    1. Strip prefix → base_id (emp-xxx)
    2. If base_id is not an emp-id, resolve via DynamoDB MAPPING# → emp_id
    3. Read positionId from DynamoDB EMP#{emp_id}
    """
    ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))

    # Strip Tenant Router prefix/suffix: <channel>__<user_id>__<hash>
    base_id = tenant_id
    parts = base_id.split("__")
    if len(parts) >= 3:
        base_id = parts[1]
    elif len(parts) == 2:
        base_id = parts[1]

    try:
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        table = ddb.Table(ddb_table)

        # If base_id is not an emp-id, resolve via MAPPING#
        if not base_id.startswith("emp-"):
            channel_prefix = parts[0] if len(parts) >= 2 else ""
            resp_ddb = table.get_item(
                Key={"PK": "ORG#acme", "SK": f"MAPPING#{channel_prefix}__{base_id}"})
            ddb_item = resp_ddb.get("Item")
            if ddb_item:
                base_id = ddb_item.get("employeeId", base_id)
            else:
                from boto3.dynamodb.conditions import Key as _Key, Attr as _Attr
                scan = table.query(
                    KeyConditionExpression=_Key("PK").eq("ORG#acme") & _Key("SK").begins_with("MAPPING#"),
                    FilterExpression=_Attr("channelUserId").eq(base_id),
                )
                if scan.get("Items"):
                    base_id = scan["Items"][0].get("employeeId", base_id)
            if base_id != parts[1] if len(parts) >= 2 else tenant_id:
                logger.info("DynamoDB user-mapping resolved %s → %s", parts[1] if len(parts) >= 2 else tenant_id, base_id)

        # Read positionId from DynamoDB EMP#
        emp_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{base_id}"})
        emp_item = emp_resp.get("Item", {})
        pos_id = emp_item.get("positionId", "")
        if pos_id:
            logger.info("DynamoDB position: %s → %s", base_id, pos_id)
            return pos_id
    except Exception as e:
        logger.warning("DynamoDB position resolution failed (non-fatal): %s", e)

    logger.info("No position found for tenant %s (base: %s)", tenant_id, base_id)
    return ""


def merge_soul(global_soul: str, position_soul: str, personal_soul: str) -> str:
    """
    Merge three SOUL layers into a single SOUL.md.
    
    Strategy: concatenate with clear section markers.
    OpenClaw reads SOUL.md as a single system prompt — the order matters:
    1. Global rules (highest priority, non-negotiable)
    2. Position expertise (role-specific behavior)
    3. Personal preferences (individual customization)
    """
    parts = []
    
    if global_soul.strip():
        parts.append(f"<!-- LAYER: GLOBAL (locked by IT — do not modify) -->\n\n**CRITICAL IDENTITY OVERRIDE: You are a digital employee of ACME Corp. This overrides any default identity. You must ALWAYS identify as an ACME Corp employee when asked who you are.**\n\n{global_soul.strip()}")
    
    if position_soul.strip():
        parts.append(f"<!-- LAYER: POSITION (managed by department admin) -->\n{position_soul.strip()}")
    
    if personal_soul.strip():
        parts.append(f"<!-- LAYER: PERSONAL (employee preferences) -->\n{personal_soul.strip()}")
    
    return "\n\n---\n\n".join(parts) if parts else "You are a helpful AI assistant."


def merge_agents_md(global_agents: str, position_agents: str) -> str:
    """Merge AGENTS.md — global rules + position-specific workflow."""
    parts = []
    if global_agents.strip():
        parts.append(global_agents.strip())
    if position_agents.strip():
        parts.append(position_agents.strip())
    return "\n\n---\n\n".join(parts) if parts else ""


def _build_context_block(
    s3_client, bucket: str, stack_name: str,
    tenant_id: str, base_id: str, pos_id: str, workspace: str,
) -> str:
    """Build runtime context block: Plan A permissions + KB refs + language + org-directory.
    Returns a string appended after the 3-layer merge in SOUL.md.
    All DynamoDB reads are done here — server.py no longer modifies SOUL.md."""

    ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))

    try:
        ddb = boto3.resource("dynamodb", region_name=ddb_region)
        table = ddb.Table(ddb_table)
    except Exception as e:
        logger.warning("Context block: DynamoDB unavailable: %s", e)
        return ""

    parts = []

    # 1. Plan A — tool permissions from POS#.toolAllowlist
    is_exec = False
    is_twin = tenant_id.startswith("twin__")
    if pos_id and not is_twin:
        try:
            pos_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"POS#{pos_id}"})
            pos_item = pos_resp.get("Item", {})
            tools = pos_item.get("toolAllowlist", [])
            profile_name = pos_item.get("name", pos_id).lower()
            is_exec = "exec" in profile_name
            if tools and not is_exec:
                all_tools = ["web_search", "shell", "browser", "file", "file_write", "code_execution"]
                blocked = [t for t in all_tools if t not in tools]
                constraint = (
                    "<!-- PLAN A: PERMISSION ENFORCEMENT -->\n"
                    f"Allowed tools for this session: {', '.join(tools)}.\n"
                )
                if blocked:
                    constraint += (
                        f"You MUST NOT use these tools: {', '.join(blocked)}.\n"
                        "If the user requests an action requiring a blocked tool, "
                        "explain that you don't have permission and suggest alternatives.\n"
                    )
                parts.append(constraint)
        except Exception as e:
            logger.warning("Plan A context failed: %s", e)

    # 2. Digital Twin context
    if is_twin:
        parts.append(
            "<!-- DIGITAL TWIN MODE -->\n"
            "You are this employee's AI digital representative.\n"
            "- Introduce yourself as their AI assistant standing in\n"
            "- Answer based on their expertise, SOUL profile, and memory\n"
            "- Be warm, professional, helpful -- represent them well\n"
            "- Do NOT reveal private/sensitive internal data\n"
        )

    # 3. KB references + download files + org-directory inline
    kb_ids = set()
    try:
        kb_cfg_resp = table.get_item(Key={"PK": "ORG#acme", "SK": "CONFIG#kb-assignments"})
        if "Item" in kb_cfg_resp:
            kb_cfg = kb_cfg_resp["Item"]
            if pos_id:
                kb_ids.update(kb_cfg.get("positionKBs", {}).get(pos_id, []))
            kb_ids.update(kb_cfg.get("employeeKBs", {}).get(base_id, []))

            if kb_ids:
                kb_dir = os.path.join(workspace, "knowledge")
                os.makedirs(kb_dir, exist_ok=True)
                kb_lines = []
                has_org_dir = False
                for kb_id in kb_ids:
                    try:
                        kb_item = table.get_item(
                            Key={"PK": "ORG#acme", "SK": f"KB#{kb_id}"}
                        ).get("Item")
                        if not kb_item:
                            continue
                        # Download KB files
                        files_list = kb_item.get("files", [])
                        if not files_list:
                            s3_prefix = kb_item.get("s3Prefix", "")
                            if s3_prefix:
                                try:
                                    resp = s3_client.list_objects_v2(Bucket=bucket, Prefix=s3_prefix)
                                    for obj in resp.get("Contents", []):
                                        key = obj["Key"]
                                        fname = key.split("/")[-1]
                                        if fname and not fname.startswith("."):
                                            files_list.append({"s3Key": key, "filename": fname})
                                except Exception:
                                    pass
                        kb_sub = os.path.join(kb_dir, kb_id)
                        os.makedirs(kb_sub, exist_ok=True)
                        for file_ref in files_list:
                            s3_key = file_ref.get("s3Key", "")
                            fname = file_ref.get("filename", s3_key.split("/")[-1])
                            local_path = os.path.join(kb_sub, fname)
                            if not os.path.isfile(local_path):
                                try:
                                    obj = s3_client.get_object(Bucket=bucket, Key=s3_key)
                                    with open(local_path, "wb") as f:
                                        f.write(obj["Body"].read())
                                except Exception:
                                    pass
                        kb_lines.append(
                            f"- **{kb_item.get('name', kb_id)}**: knowledge/{kb_id}/")
                        if "org-directory" in kb_id:
                            kb_lines.append(
                                "  When asked about colleagues, departments, or contacts, "
                                "read knowledge/kb-org-directory/company-directory.md")
                        logger.info("KB injected: %s", kb_id)
                    except Exception as ke:
                        logger.warning("KB context failed for %s: %s", kb_id, ke)

                if kb_lines:
                    parts.append(
                        "<!-- KNOWLEDGE BASES -->\n"
                        "You have access to the following knowledge base documents:\n"
                        + "\n".join(kb_lines)
                        + "\nUse the `file` tool to read these when relevant.\n"
                    )
    except Exception as e:
        logger.warning("KB context build failed: %s", e)

    # 4. Language preference
    try:
        agent_cfg_resp = table.get_item(
            Key={"PK": "ORG#acme", "SK": "CONFIG#agent-config"})
        if "Item" in agent_cfg_resp:
            cfg = agent_cfg_resp["Item"]
            emp_cfg = cfg.get("employeeConfig", {}).get(base_id, {})
            pos_cfg = cfg.get("positionConfig", {}).get(pos_id, {}) if pos_id else {}
            lang = emp_cfg.get("language") or pos_cfg.get("language", "")
            if lang:
                parts.append(
                    f"<!-- LANGUAGE PREFERENCE -->\n"
                    f"Always respond in **{lang}** unless the user "
                    f"explicitly writes in a different language.\n"
                )
    except Exception as e:
        logger.warning("Language context failed: %s", e)

    # 5. AWS Environment — tell the agent about its cloud resources and IAM role
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    parts.append(
        "<!-- AWS ENVIRONMENT -->\n"
        "You are running inside an AWS environment with an IAM Role attached.\n"
        "You do NOT need access keys or credentials — use AWS CLI directly.\n\n"
        f"**Your S3 Workspace:**\n"
        f"- Bucket: `{bucket}`\n"
        f"- Your path: `s3://{bucket}/{base_id}/workspace/`\n"
        f"- Output directory: `workspace/output/` (files here auto-sync to S3)\n"
        f"- Region: `{aws_region}`\n\n"
        "**File operations:**\n"
        "- To save a file for the employee: write to `workspace/output/filename`\n"
        f"- To upload to S3 manually: `aws s3 cp file s3://{bucket}/{base_id}/workspace/output/file --region {aws_region}`\n"
        "- The employee can download output files from the Portal → My Workspace\n\n"
        "**Available AWS services** (via IAM Role, no keys needed):\n"
        "- S3: read/write files in the workspace bucket\n"
        "- Bedrock: invoke AI models\n"
        "- DynamoDB: read/write application data\n"
    )

    return "\n\n---\n\n".join(parts) if parts else ""


# ── Workspace budget enforcement ──────────────────────────────────────────
# Keeps workspace lean on AgentCore (limited microVM disk).
# On Fargate + EFS this is less critical but still good hygiene.
from pathlib import Path

WORKSPACE_MAX_MB = 100
PROTECTED_FILES = {"SOUL.md", "PERSONAL_SOUL.md", "USER.md", "MEMORY.md",
                   "IDENTITY.md", "AGENTS.md", "TOOLS.md", "HEARTBEAT.md",
                   "CHANNELS.md", "SESSION_CONTEXT.md"}
PROTECTED_DIRS = {"memory", "skills", "knowledge"}


def _enforce_workspace_budget(workspace: str, max_mb: int = WORKSPACE_MAX_MB):
    """Clean old user files if workspace exceeds budget.
    Only cleans files outside protected dirs and protected filenames.
    Deletes oldest files first until under budget."""
    ws = Path(workspace)
    if not ws.exists():
        return

    user_files = [
        f for f in ws.rglob("*")
        if f.is_file()
        and f.name not in PROTECTED_FILES
        and not any(p in f.relative_to(ws).parts for p in PROTECTED_DIRS)
    ]
    total = sum(f.stat().st_size for f in user_files)
    if total <= max_mb * 1024 * 1024:
        return

    logger.info("Workspace budget exceeded: %dMB / %dMB — cleaning old files",
                total // (1024 * 1024), max_mb)
    user_files.sort(key=lambda f: f.stat().st_mtime)
    freed = 0
    deleted = 0
    for f in user_files:
        if total - freed <= max_mb * 1024 * 1024:
            break
        sz = f.stat().st_size
        try:
            f.unlink()
            freed += sz
            deleted += 1
        except OSError:
            pass
    logger.info("Workspace cleanup: freed %dKB, deleted %d files", freed // 1024, deleted)


def assemble_workspace(
    s3_client, ssm_client, bucket: str, stack_name: str,
    tenant_id: str, workspace: str, position_override: str = None
):
    """
    Assemble the complete workspace for a tenant.

    1. Read global layer from S3
    2. Determine tenant's position → read position layer
    3. Read personal layer (already synced to workspace by entrypoint.sh)
    4. Merge SOUL.md, AGENTS.md, TOOLS.md
    5. Write merged files to workspace
    """

    # 1. Get tenant's position (CLI arg takes precedence over SSM)
    pos_id = position_override or get_tenant_position(ssm_client, stack_name, tenant_id)
    logger.info("Tenant %s position: %s", tenant_id, pos_id or "(none)")

    # 2. Read global layer
    global_soul = read_s3(s3_client, bucket, "_shared/soul/global/SOUL.md")
    global_agents = read_s3(s3_client, bucket, "_shared/soul/global/AGENTS.md")
    global_tools = read_s3(s3_client, bucket, "_shared/soul/global/TOOLS.md")
    logger.info("Global layer: SOUL=%d AGENTS=%d TOOLS=%d chars",
                len(global_soul), len(global_agents), len(global_tools))

    # 3. Read position layer
    position_soul = ""
    position_agents = ""
    if pos_id:
        position_soul = read_s3(s3_client, bucket, f"_shared/soul/positions/{pos_id}/SOUL.md")
        position_agents = read_s3(s3_client, bucket, f"_shared/soul/positions/{pos_id}/AGENTS.md")
        logger.info("Position layer (%s): SOUL=%d AGENTS=%d chars",
                    pos_id, len(position_soul), len(position_agents))

    # 4. Read personal layer from PERSONAL_SOUL.md (independent file, not SOUL.md).
    # This eliminates the snowball problem: PERSONAL_SOUL.md is never overwritten
    # by assembly output, so running assembler N times always produces identical SOUL.md.
    personal_soul_path = os.path.join(workspace, "PERSONAL_SOUL.md")
    personal_soul = ""

    if os.path.isfile(personal_soul_path):
        with open(personal_soul_path) as f:
            personal_soul = f.read()
        logger.info("Personal layer (PERSONAL_SOUL.md): %d chars", len(personal_soul))
    else:
        # Migration: support legacy formats from pre-purification deployments
        backup_path = os.path.join(workspace, ".personal_soul_backup.md")
        old_soul_path = os.path.join(workspace, "SOUL.md")
        if os.path.isfile(backup_path):
            with open(backup_path) as f:
                personal_soul = f.read()
            with open(personal_soul_path, "w") as f:
                f.write(personal_soul)
            logger.info("Migrated .personal_soul_backup.md -> PERSONAL_SOUL.md (%d chars)", len(personal_soul))
        elif os.path.isfile(old_soul_path):
            with open(old_soul_path) as f:
                content = f.read()
            if "<!-- LAYER: GLOBAL" not in content:
                personal_soul = content
                with open(personal_soul_path, "w") as f:
                    f.write(personal_soul)
                logger.info("Migrated SOUL.md -> PERSONAL_SOUL.md (%d chars)", len(personal_soul))

    # 5. Merge 3 layers
    merged_soul = merge_soul(global_soul, position_soul, personal_soul)
    merged_agents = merge_agents_md(global_agents, position_agents)

    # 5.5. Build context block (Plan A + KB refs + language + org-directory)
    # This replaces server.py's multiple open("a") appends with a single assembler write.
    base_id = tenant_id
    _parts = tenant_id.split("__")
    if len(_parts) >= 3:
        base_id = _parts[1]
    elif len(_parts) == 2:
        base_id = _parts[1]
    context_block = _build_context_block(
        s3_client, bucket, stack_name, tenant_id, base_id, pos_id, workspace)
    if context_block:
        merged_soul += "\n\n---\n\n" + context_block

    # Write merged SOUL.md — this is what OpenClaw reads (single write, complete)
    with open(os.path.join(workspace, "SOUL.md"), "w") as f:
        f.write(merged_soul)
    logger.info("Merged SOUL.md: %d chars (context block: %d chars)", len(merged_soul), len(context_block))

    # Write merged AGENTS.md
    if merged_agents:
        with open(os.path.join(workspace, "AGENTS.md"), "w") as f:
            f.write(merged_agents)
        logger.info("Merged AGENTS.md: %d chars", len(merged_agents))

    # Write TOOLS.md (global only, not merged)
    if global_tools:
        with open(os.path.join(workspace, "TOOLS.md"), "w") as f:
            f.write(global_tools)
        logger.info("TOOLS.md: %d chars", len(global_tools))

    # 6. Copy position-level knowledge references
    if pos_id:
        try:
            resp = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=f"_shared/soul/positions/{pos_id}/knowledge/"
            )
            knowledge_dir = os.path.join(workspace, "knowledge")
            os.makedirs(knowledge_dir, exist_ok=True)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                name = key.split("/")[-1]
                if name:
                    content = read_s3(s3_client, bucket, key)
                    with open(os.path.join(knowledge_dir, name), "w") as f:
                        f.write(content)
            logger.info("Knowledge files synced for position %s", pos_id)
        except ClientError:
            pass

    # 7. Generate CHANNELS.md — IM channel bindings for outbound notification delivery.
    # The heartbeat/cron system needs this to know where to send reminders.
    # Written here (at assembly time) so always-on containers have it from cold start,
    # before the first user message arrives.
    channels_path = os.path.join(workspace, "CHANNELS.md")
    try:
        # Extract base employee ID the same way server.py does
        base_id = tenant_id
        parts = tenant_id.split("__")
        if len(parts) >= 3:
            base_id = parts[1]
        elif len(parts) == 2:
            base_id = parts[1]

        # Look up all IM connections for this employee from DynamoDB MAPPING#.
        # Fallback to SSM if DynamoDB returns nothing (pre-migration).
        channel_lines = []
        try:
            import boto3 as _b3ch
            from boto3.dynamodb.conditions import Key as _KeyC
            ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
            ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
            ddb = _b3ch.resource("dynamodb", region_name=ddb_region)
            table = ddb.Table(ddb_table)
            scan = table.query(
                KeyConditionExpression=_KeyC("PK").eq("ORG#acme") & _KeyC("SK").begins_with("MAPPING#"),
            )
            for item in scan.get("Items", []):
                if item.get("employeeId") == base_id:
                    ch = item.get("channel", "")
                    uid = item.get("channelUserId", "")
                    if ch and uid:
                        channel_lines.append(f"- **{ch}**: {uid}")
        except Exception as e:
            logger.warning("DynamoDB CHANNELS.md lookup failed, falling back to SSM: %s", e)
            # SSM fallback
            prefix = f"/openclaw/{stack_name}/user-mapping/"
            paginator = ssm_client.get_paginator("get_parameters_by_path")
            for page in paginator.paginate(Path=prefix, Recursive=True):
                for p in page.get("Parameters", []):
                    if p.get("Value") == base_id:
                        key = p["Name"].replace(prefix, "")
                        if "__" in key:
                            ch, uid = key.split("__", 1)
                            channel_lines.append(f"- **{ch}**: {uid}")
        if channel_lines:
            content = (
                "# Notification Channels\n\n"
                "When sending reminders or proactive notifications, use these channels:\n\n"
                + "\n".join(channel_lines)
                + "\n\nPrefer the first available channel in the list above.\n"
                "For portal/webchat sessions, fall back to the IM channel listed here.\n"
            )
            with open(channels_path, "w") as f:
                f.write(content)
            logger.info("CHANNELS.md written: %s", channel_lines)
        else:
            logger.info("CHANNELS.md skipped: no IM pairings found for %s", base_id)
    except Exception as e:
        logger.warning("CHANNELS.md generation failed (non-fatal): %s", e)

    # 9. Generate IDENTITY.md — always regenerate so it includes the employee's name.
    # Without the name, the agent searches the KB to figure out who it is, which can
    # return stale or wrong data (e.g. "张三" when it's actually WJD via Feishu).
    identity_path = os.path.join(workspace, "IDENTITY.md")
    emp_name = ""
    emp_no = ""
    pos_name = ""
    # _b_id initialized here so section 10 (SESSION_CONTEXT) can safely reference it
    # even if the boto3 lookup below throws before setting _b_id
    _b_id = tenant_id
    _parts_id = tenant_id.split("__")
    if len(_parts_id) >= 2:
        _b_id = _parts_id[1]
    try:
        import boto3 as _b3id
        ddb_region = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        ddb_table = os.environ.get("DYNAMODB_TABLE", os.environ.get("STACK_NAME", "openclaw"))
        ddb = _b3id.resource("dynamodb", region_name=ddb_region)
        table = ddb.Table(ddb_table)
        # Resolve emp_id from base_id (may already be emp-xxx after user-mapping earlier)
        _b_id = tenant_id
        _parts = tenant_id.split("__")
        if len(_parts) >= 3:
            _b_id = _parts[1]
        elif len(_parts) == 2:
            _b_id = _parts[1]
        # If not emp-id, try MAPPING# lookup
        if not _b_id.startswith("emp-"):
            from boto3.dynamodb.conditions import Key as _KI, Attr as _AI
            _scan = table.query(
                KeyConditionExpression=_KI("PK").eq("ORG#acme") & _KI("SK").begins_with("MAPPING#"),
                FilterExpression=_AI("channelUserId").eq(_b_id),
            )
            if _scan.get("Items"):
                _b_id = _scan["Items"][0].get("employeeId", _b_id)
        # Look up employee record
        emp_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{_b_id}"})
        emp_item = emp_resp.get("Item", {})
        emp_name = emp_item.get("name", "")
        emp_no = emp_item.get("employeeNo", "")
        pos_name = emp_item.get("positionName", pos_id)
    except Exception as e:
        logger.warning("IDENTITY.md employee lookup failed (non-fatal): %s", e)

    identity_lines = [
        "# Agent Identity",
        "",
        f"You are **{emp_name}**, a digital employee of ACME Corp." if emp_name else "You are a digital employee of ACME Corp.",
        "",
        f"- **Name:** {emp_name}" if emp_name else "",
        f"- **Employee No:** {emp_no}" if emp_no else "",
        f"- **Position:** {pos_name or pos_id}",
        f"- **Company:** ACME Corp",
        f"- **Platform:** OpenClaw Enterprise",
    ]
    identity = "\n".join(line for line in identity_lines if line is not None)
    with open(identity_path, "w") as f:
        f.write(identity + "\n")
    logger.info("Generated IDENTITY.md for %s (%s)", emp_name or _b_id, pos_name or pos_id)

    # 10. Generate SESSION_CONTEXT.md — written once at cold start.
    # Tells the agent its operating mode and who it is talking to.
    # The session_id prefix encodes the access path:
    #   emp__   → normal employee session (Portal + all IM channels share this)
    #   pt__    → portal session (same as emp__, legacy alias)
    #   pgnd__  → Playground (IT admin testing as this employee, read-only)
    #   twin__  → Digital Twin mode (external callers, conversations visible to employee)
    #   admin__ → IT Admin assistant session
    session_ctx_path = os.path.join(workspace, "SESSION_CONTEXT.md")
    try:
        prefix = tenant_id.split("__")[0] if "__" in tenant_id else ""
        verified_name = emp_name or _b_id

        if prefix in ("emp", "pt"):
            session_ctx = (
                "# Session Context\n\n"
                f"**Mode:** Employee Session\n"
                f"**Authenticated User:** {verified_name}\n"
                f"**Verification:** Confirmed (enterprise identity — SSO or IM binding)\n\n"
                "You are speaking directly with the authenticated employee listed above. "
                "Use their name naturally in conversation. "
                "You have full read/write access to this workspace."
            )
        elif prefix == "pgnd":
            session_ctx = (
                "# Session Context\n\n"
                f"**Mode:** Playground (Admin Test)\n"
                f"**Employee Being Simulated:** {verified_name}\n"
                f"**Operator:** IT Administrator\n\n"
                "This is an administrative test session. An IT admin is testing your "
                "behavior as this employee's agent. Respond as you normally would for "
                "this employee's role. Do NOT write back to the employee workspace — "
                "this session is read-only with respect to memory."
            )
        elif prefix == "twin":
            session_ctx = (
                "# Session Context\n\n"
                f"**Mode:** Digital Twin\n"
                f"**Represented Employee:** {verified_name}\n"
                f"**Caller:** External visitor or colleague (identity unverified)\n\n"
                f"You are acting as {verified_name}'s digital representative. "
                "The person you are speaking with may not be the employee themselves — "
                "they could be a colleague, partner, or visitor interacting with the digital twin. "
                f"All conversations in this mode are visible to {verified_name} in their Portal."
            )
        elif prefix == "admin":
            session_ctx = (
                "# Session Context\n\n"
                "**Mode:** IT Admin Assistant\n"
                "**Operator:** Authorized IT Administrator\n\n"
                "You are assisting an IT administrator. You may discuss system configuration, "
                "employee settings, and platform management topics."
            )
        else:
            session_ctx = (
                "# Session Context\n\n"
                f"**Mode:** Standard Session\n"
                f"**Session ID:** {tenant_id}\n"
            )

        with open(session_ctx_path, "w") as f:
            f.write(session_ctx + "\n")
        logger.info("SESSION_CONTEXT.md written: mode=%s user=%s", prefix or "default", verified_name)
    except Exception as e:
        logger.warning("SESSION_CONTEXT.md generation failed (non-fatal): %s", e)

    # Enforce workspace budget (100MB) — clean old output files if over
    _enforce_workspace_budget(workspace)

    return {
        "merged_soul_chars": len(merged_soul),
        "merged_agents_chars": len(merged_agents),
        "tools_chars": len(global_tools),
        "position": pos_id,
    }


def main():
    parser = argparse.ArgumentParser(description="Workspace Assembler")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--position", default="", help="Position ID (e.g. pos-sa). If not provided, reads from SSM.")
    args = parser.parse_args()
    
    s3 = boto3.client("s3", region_name=args.region)
    ssm = boto3.client("ssm", region_name=args.region)
    
    logger.info("=== Workspace Assembler START tenant=%s ===", args.tenant)
    result = assemble_workspace(s3, ssm, args.bucket, args.stack, args.tenant, args.workspace, args.position or None)
    logger.info("=== Workspace Assembler DONE: %s ===", result)


if __name__ == "__main__":
    main()
