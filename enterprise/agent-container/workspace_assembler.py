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
    --region us-east-2
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
    """Get the position ID for a tenant.

    Resolution order:
    1. SSM /tenants/{tenant_id}/position  (exact match)
    2. Strip prefix → base_id, then SSM /tenants/{base_id}/position
    3. If base_id is not an emp-id, resolve via DynamoDB user-mapping → emp_id,
       then read positionId from DynamoDB employees table.
       (Fixes IM channel users: Feishu OU IDs, Discord numeric IDs, etc.)
    """
    # Try exact match first
    try:
        resp = ssm.get_parameter(
            Name=f"/openclaw/{stack_name}/tenants/{tenant_id}/position"
        )
        return resp["Parameter"]["Value"]
    except ClientError:
        pass

    # Strip Tenant Router prefix/suffix: <channel>__<user_id>__<hash>
    base_id = tenant_id
    parts = base_id.split("__")
    if len(parts) >= 3:
        base_id = parts[1]
    elif len(parts) == 2:
        base_id = parts[1]

    if base_id != tenant_id:
        try:
            resp = ssm.get_parameter(
                Name=f"/openclaw/{stack_name}/tenants/{base_id}/position"
            )
            logger.info("Found position for base tenant %s (from %s)", base_id, tenant_id)
            return resp["Parameter"]["Value"]
        except ClientError:
            pass

    # If base_id is not an emp-id, it's a channel user ID (e.g. Feishu OU ID).
    # Resolve via DynamoDB user-mapping → emp_id → positionId.
    if not base_id.startswith("emp-"):
        try:
            import boto3 as _b3wa
            ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
            ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
            ddb = _b3wa.resource("dynamodb", region_name=ddb_region)
            table = ddb.Table(ddb_table)

            # Find the emp_id via MAPPING# scan
            channel_prefix = parts[0] if len(parts) >= 2 else ""
            emp_id = ""
            resp_ddb = table.get_item(
                Key={"PK": "ORG#acme", "SK": f"MAPPING#{channel_prefix}__{base_id}"})
            ddb_item = resp_ddb.get("Item")
            if ddb_item:
                emp_id = ddb_item.get("employeeId", "")
            else:
                from boto3.dynamodb.conditions import Key as _Key, Attr as _Attr
                scan = table.query(
                    KeyConditionExpression=_Key("PK").eq("ORG#acme") & _Key("SK").begins_with("MAPPING#"),
                    FilterExpression=_Attr("channelUserId").eq(base_id),
                )
                if scan.get("Items"):
                    emp_id = scan["Items"][0].get("employeeId", "")

            if emp_id:
                logger.info("DynamoDB user-mapping resolved %s → %s", base_id, emp_id)
                # Get positionId from DynamoDB employees table
                emp_resp = table.get_item(Key={"PK": "ORG#acme", "SK": f"EMP#{emp_id}"})
                emp_item = emp_resp.get("Item", {})
                pos_id = emp_item.get("positionId", "")
                if pos_id:
                    logger.info("DynamoDB employee position %s → %s", emp_id, pos_id)
                    return pos_id
                # Fallback: SSM for the resolved emp_id
                try:
                    resp = ssm.get_parameter(
                        Name=f"/openclaw/{stack_name}/tenants/{emp_id}/position")
                    return resp["Parameter"]["Value"]
                except ClientError:
                    pass
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

    # 4. Read personal layer (employee's own preferences only).
    # Use .personal_soul_backup.md if it exists — this is the employee's raw personal
    # layer, saved before the first assembly. Without this, subsequent assemblies would
    # read the already-merged SOUL.md (which includes global + position + KB blocks)
    # as the personal layer, causing a snowball: SOUL.md grows unboundedly across sessions.
    personal_soul_path = os.path.join(workspace, "SOUL.md")
    backup_path = os.path.join(workspace, ".personal_soul_backup.md")
    personal_soul = ""

    if os.path.isfile(backup_path):
        # Backup exists = workspace has been assembled before; use the original personal layer
        with open(backup_path) as f:
            personal_soul = f.read()
        logger.info("Personal layer (from backup): SOUL=%d chars", len(personal_soul))
    elif os.path.isfile(personal_soul_path):
        with open(personal_soul_path) as f:
            personal_soul = f.read()
        # Save backup so future assemblies use the original personal preferences
        with open(backup_path, "w") as f:
            f.write(personal_soul)
        logger.info("Personal layer: SOUL=%d chars (backup created)", len(personal_soul))

    # 5. Merge and write
    merged_soul = merge_soul(global_soul, position_soul, personal_soul)
    merged_agents = merge_agents_md(global_agents, position_agents)

    # Write merged SOUL.md — this is what OpenClaw reads
    with open(os.path.join(workspace, "SOUL.md"), "w") as f:
        f.write(merged_soul)
    logger.info("Merged SOUL.md: %d chars", len(merged_soul))

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
            ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
            ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
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
        ddb_region = os.environ.get("DYNAMODB_REGION", "us-east-2")
        ddb_table = os.environ.get("DYNAMODB_TABLE", "openclaw-enterprise")
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
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--position", default="", help="Position ID (e.g. pos-sa). If not provided, reads from SSM.")
    args = parser.parse_args()
    
    s3 = boto3.client("s3", region_name=args.region)
    ssm = boto3.client("ssm", region_name=args.region)
    
    logger.info("=== Workspace Assembler START tenant=%s ===", args.tenant)
    result = assemble_workspace(s3, ssm, args.bucket, args.stack, args.tenant, args.workspace, args.position or None)
    logger.info("=== Workspace Assembler DONE: %s ===", result)


if __name__ == "__main__":
    main()
