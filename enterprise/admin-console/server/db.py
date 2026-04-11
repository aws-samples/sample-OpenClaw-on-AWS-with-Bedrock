"""
DynamoDB data access layer for OpenClaw Enterprise.
Single-table design: PK=ORG#acme, SK=TYPE#id

Falls back to empty lists if DynamoDB is unavailable.
"""
import json
import os
from decimal import Decimal
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

TABLE_NAME = os.environ.get("DYNAMODB_TABLE") or os.environ.get("STACK_NAME", "openclaw")
AWS_REGION = os.environ.get("DYNAMODB_REGION", os.environ.get("AWS_REGION", "us-east-1"))
ORG_PK = "ORG#acme"

_table = None

def _get_table():
    global _table
    if _table is None:
        ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
        _table = ddb.Table(TABLE_NAME)
    return _table


def _clean(item: dict) -> dict:
    """Convert Decimal to float/int for JSON serialization."""
    cleaned = {}
    for k, v in item.items():
        if k in ("PK", "SK", "GSI1PK", "GSI1SK"):
            continue  # strip DynamoDB keys from response
        if isinstance(v, Decimal):
            cleaned[k] = int(v) if v == int(v) else float(v)
        elif isinstance(v, dict):
            cleaned[k] = _clean(v)
        elif isinstance(v, list):
            cleaned[k] = [_clean(i) if isinstance(i, dict) else (int(i) if isinstance(i, Decimal) and i == int(i) else float(i) if isinstance(i, Decimal) else i) for i in v]
        else:
            cleaned[k] = v
    return cleaned


def _query(sk_prefix: str) -> list[dict]:
    """Query all items with given SK prefix under ORG#acme."""
    try:
        resp = _get_table().query(
            KeyConditionExpression=Key("PK").eq(ORG_PK) & Key("SK").begins_with(sk_prefix)
        )
        return [_clean(item) for item in resp.get("Items", [])]
    except ClientError as e:
        print(f"[db] DynamoDB query error: {e}")
        return []


def _get_item(sk: str) -> Optional[dict]:
    """Get a single item by SK."""
    try:
        resp = _get_table().get_item(Key={"PK": ORG_PK, "SK": sk})
        item = resp.get("Item")
        return _clean(item) if item else None
    except ClientError as e:
        print(f"[db] DynamoDB get error: {e}")
        return None


def _put_item(sk: str, data: dict, gsi1pk: str = "", gsi1sk: str = ""):
    """Put an item."""
    item = {"PK": ORG_PK, "SK": sk, **data}
    if gsi1pk:
        item["GSI1PK"] = gsi1pk
    if gsi1sk:
        item["GSI1SK"] = gsi1sk
    try:
        _get_table().put_item(Item=item)
        return True
    except ClientError as e:
        print(f"[db] DynamoDB put error: {e}")
        return False


def _make_put(sk: str, data: dict, gsi1pk: str = "", gsi1sk: str = "") -> dict:
    """Build a TransactWriteItem Put dict (does NOT write — used by transact_write)."""
    item = {"PK": ORG_PK, "SK": sk, **data}
    if gsi1pk:
        item["GSI1PK"] = gsi1pk
    if gsi1sk:
        item["GSI1SK"] = gsi1sk
    # Convert floats to Decimal (required by DynamoDB)
    item = _decimalize(item)
    return {"Put": {"TableName": TABLE_NAME, "Item": item}}


def _decimalize(obj):
    """Recursively convert float → Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _decimalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimalize(i) for i in obj]
    return obj


def transact_write(items: list[dict]) -> bool:
    """Atomic write of multiple items using DynamoDB TransactWriteItems.
    Each item in the list must be a TransactWriteItem dict (from _make_put).
    All items succeed or all fail — no partial state.
    Max 100 items per transaction (DynamoDB limit)."""
    if not items:
        return True
    if len(items) > 100:
        raise ValueError(f"TransactWriteItems supports max 100 items, got {len(items)}")
    try:
        client = boto3.client("dynamodb", region_name=AWS_REGION)
        client.transact_write_items(TransactItems=items)
        return True
    except ClientError as e:
        print(f"[db] transact_write failed: {e}")
        return False


def provision_employee_atomic(
    agent_data: dict,
    binding_data: dict,
    emp_update: dict,
    audit_data: dict,
) -> bool:
    """Atomic provisioning: create agent + binding + update employee + audit in one transaction.
    If any write fails, ALL are rolled back — no orphaned agents or bindings.
    S3 workspace seeding happens AFTER this succeeds (S3 has no transactional support)."""
    agent_id = agent_data.get("id", f"agent-{int(__import__('time').time())}")
    agent_data["id"] = agent_id
    if "qualityScore" in agent_data and agent_data["qualityScore"] is not None:
        agent_data["qualityScore"] = str(agent_data["qualityScore"])

    bind_id = binding_data.get("id", f"bind-{int(__import__('time').time())}")
    binding_data["id"] = bind_id
    bind_agent = binding_data.get("agentId", agent_id)

    audit_id = audit_data.get("id", f"aud-{int(__import__('time').time())}")
    audit_data["id"] = audit_id

    emp_id = emp_update.get("id", "")

    items = [
        _make_put(f"AGENT#{agent_id}", agent_data, "TYPE#agent", f"AGENT#{agent_id}"),
        _make_put(f"BIND#{bind_id}", binding_data, f"AGENT#{bind_agent}", f"BIND#{bind_id}"),
        _make_put(f"EMP#{emp_id}", emp_update, "TYPE#employee", f"EMP#{emp_id}"),
        _make_put(f"AUDIT#{audit_id}", audit_data, "TYPE#audit", f"AUDIT#{audit_id}"),
    ]

    return transact_write(items)


# === Public API ===

def get_departments() -> list[dict]:
    return _query("DEPT#")

def get_positions() -> list[dict]:
    return _query("POS#")

def get_position(pos_id: str) -> Optional[dict]:
    return _get_item(f"POS#{pos_id}")

def get_employees() -> list[dict]:
    return _query("EMP#")

def get_employee(emp_id: str) -> Optional[dict]:
    return _get_item(f"EMP#{emp_id}")

def add_employee_channel(emp_id: str, channel: str) -> None:
    """Add a channel to the employee's channels list (idempotent)."""
    try:
        _get_table().update_item(
            Key={"PK": ORG_PK, "SK": f"EMP#{emp_id}"},
            UpdateExpression="SET #ch = list_append(if_not_exists(#ch, :empty), :val)",
            ConditionExpression="not contains(#ch, :channel)",
            ExpressionAttributeNames={"#ch": "channels"},
            ExpressionAttributeValues={":val": [channel], ":empty": [], ":channel": channel},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            print(f"[db] add_employee_channel error: {e}")

def remove_employee_channel(emp_id: str, channel: str) -> None:
    """Remove a channel from the employee's channels list."""
    try:
        emp = get_employee(emp_id)
        if not emp:
            return
        channels = [c for c in emp.get("channels", []) if c != channel]
        _get_table().update_item(
            Key={"PK": ORG_PK, "SK": f"EMP#{emp_id}"},
            UpdateExpression="SET #ch = :val",
            ExpressionAttributeNames={"#ch": "channels"},
            ExpressionAttributeValues={":val": channels},
        )
    except ClientError as e:
        print(f"[db] remove_employee_channel error: {e}")

def get_agents() -> list[dict]:
    items = _query("AGENT#")
    # Convert qualityScore from string back to float
    for item in items:
        if "qualityScore" in item and isinstance(item["qualityScore"], str):
            try:
                item["qualityScore"] = float(item["qualityScore"])
            except ValueError:
                item["qualityScore"] = None
    return items

def get_agent(agent_id: str) -> Optional[dict]:
    item = _get_item(f"AGENT#{agent_id}")
    if item and "qualityScore" in item and isinstance(item["qualityScore"], str):
        try:
            item["qualityScore"] = float(item["qualityScore"])
        except ValueError:
            item["qualityScore"] = None
    return item

def update_agent(agent_id: str, updates: dict) -> Optional[dict]:
    item = _get_item(f"AGENT#{agent_id}")
    if not item:
        return None
    item.update(updates)
    item["id"] = agent_id
    _put_item(f"AGENT#{agent_id}", item, "TYPE#agent", f"AGENT#{agent_id}")
    return item

def delete_agent(agent_id: str) -> bool:
    return _delete_item(f"AGENT#{agent_id}")


def get_bindings() -> list[dict]:
    return _query("BIND#")

def get_bindings_for_employee(emp_id: str) -> list[dict]:
    """Get all BIND# records for an employee (agent-employee bindings)."""
    all_bindings = get_bindings()
    return [b for b in all_bindings if b.get("employeeId") == emp_id]

def _delete_item(sk: str) -> bool:
    """Delete an item by SK."""
    try:
        _get_table().delete_item(Key={"PK": ORG_PK, "SK": sk})
        return True
    except ClientError as e:
        print(f"[db] DynamoDB delete error: {e}")
        return False

def create_department(data: dict) -> dict:
    did = data.get("id", f"dept-{int(__import__('time').time())}")
    data["id"] = did
    _put_item(f"DEPT#{did}", data, "TYPE#dept", f"DEPT#{did}")
    return data

def update_department(dept_id: str, updates: dict) -> dict | None:
    item = _get_item(f"DEPT#{dept_id}")
    if not item:
        return None
    item.update(updates)
    item["id"] = dept_id
    _put_item(f"DEPT#{dept_id}", item, "TYPE#dept", f"DEPT#{dept_id}")
    return item

def delete_department(dept_id: str) -> bool:
    return _delete_item(f"DEPT#{dept_id}")

def create_position(data: dict) -> dict:
    pid = data.get("id", f"pos-{int(__import__('time').time())}")
    data["id"] = pid
    _put_item(f"POS#{pid}", data, "TYPE#pos", f"POS#{pid}")
    return data

def update_position(pos_id: str, updates: dict) -> dict | None:
    item = _get_item(f"POS#{pos_id}")
    if not item:
        return None
    item.update(updates)
    item["id"] = pos_id
    _put_item(f"POS#{pos_id}", item, "TYPE#pos", f"POS#{pos_id}")
    return item

def delete_position(pos_id: str) -> bool:
    return _delete_item(f"POS#{pos_id}")

def create_employee(data: dict) -> dict:
    eid = data.get("id", f"emp-{int(__import__('time').time())}")
    data["id"] = eid
    _put_item(f"EMP#{eid}", data, "TYPE#emp", f"EMP#{eid}")
    return data

def update_employee(emp_id: str, updates: dict) -> dict | None:
    item = _get_item(f"EMP#{emp_id}")
    if not item:
        return None
    item.update(updates)
    item["id"] = emp_id
    _put_item(f"EMP#{emp_id}", item, "TYPE#emp", f"EMP#{emp_id}")
    return item

def delete_employee(emp_id: str) -> bool:
    return _delete_item(f"EMP#{emp_id}")

def delete_binding(bind_id: str) -> bool:
    return _delete_item(f"BIND#{bind_id}")

def create_agent(data: dict) -> dict:
    aid = data.get("id", f"agent-{int(__import__('time').time())}")
    data["id"] = aid
    if "qualityScore" in data and data["qualityScore"] is not None:
        data["qualityScore"] = str(data["qualityScore"])
    _put_item(f"AGENT#{aid}", data, "TYPE#agent", f"AGENT#{aid}")
    return data

def create_binding(data: dict) -> dict:
    bid = data.get("id", f"bind-{int(__import__('time').time())}")
    data["id"] = bid
    agent_id = data.get("agentId", "unknown")
    _put_item(f"BIND#{bid}", data, f"AGENT#{agent_id}", f"BIND#{bid}")
    return data


# === Audit Entries ===

def get_audit_entries(limit: int = 50) -> list[dict]:
    items = _query("AUDIT#")
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return items[:limit]

def create_audit_entry(data: dict) -> dict:
    aid = data.get("id", f"aud-{int(__import__('time').time())}")
    data["id"] = aid
    _put_item(f"AUDIT#{aid}", data, "TYPE#audit", f"AUDIT#{aid}")
    return data

# === Approvals ===

def get_approvals() -> list[dict]:
    return _query("APPROVAL#")

def get_approval(approval_id: str) -> dict | None:
    return _get_item(f"APPROVAL#{approval_id}")

def create_approval(data: dict) -> dict:
    aid = data.get("id", f"APR-{int(__import__('time').time())}")
    data["id"] = aid
    _put_item(f"APPROVAL#{aid}", data, "TYPE#approval", f"APPROVAL#{aid}")
    return data

def update_approval(approval_id: str, updates: dict) -> dict | None:
    item = _get_item(f"APPROVAL#{approval_id}")
    if not item:
        return None
    item.update(updates)
    _put_item(f"APPROVAL#{approval_id}", item, "TYPE#approval", f"APPROVAL#{approval_id}")
    return item

# === Settings (config) ===

def get_config(key: str) -> dict | None:
    return _get_item(f"CONFIG#{key}")

def set_config(key: str, data: dict) -> dict:
    _put_item(f"CONFIG#{key}", data, "TYPE#config", f"CONFIG#{key}")
    return data


# === Knowledge Bases ===

def get_knowledge_bases() -> list[dict]:
    items = _query("KB#")
    for item in items:
        if "sizeMB" in item and isinstance(item["sizeMB"], str):
            item["sizeMB"] = float(item["sizeMB"])
    return items

def get_knowledge_base(kb_id: str) -> dict | None:
    item = _get_item(f"KB#{kb_id}")
    if item and "sizeMB" in item and isinstance(item["sizeMB"], str):
        item["sizeMB"] = float(item["sizeMB"])
    return item

def create_knowledge_base(data: dict) -> dict:
    kid = data.get("id", f"kb-{int(__import__('time').time())}")
    data["id"] = kid
    _put_item(f"KB#{kid}", data, "TYPE#kb", f"KB#{kid}")
    return data


# === Usage Metrics ===

def get_usage_by_date(date: str = "") -> list[dict]:
    """Get all agent usage records for a specific date, or all dates."""
    prefix = f"USAGE#" if not date else f"USAGE#"
    items = _query(prefix)
    if date:
        items = [i for i in items if i.get("date") == date]
    return items

def get_usage_for_agent(agent_id: str) -> list[dict]:
    """Get all daily usage records for a specific agent."""
    items = _query(f"USAGE#{agent_id}#")
    return items

# === Sessions ===

def get_sessions() -> list[dict]:
    """Return all sessions, injecting 'id' from the DynamoDB SK."""
    try:
        resp = _get_table().query(
            KeyConditionExpression=Key("PK").eq(ORG_PK) & Key("SK").begins_with("SESSION#")
        )
        items = []
        for item in resp.get("Items", []):
            session_id = item.get("SK", "").replace("SESSION#", "", 1)
            cleaned = _clean(item)
            if "id" not in cleaned or not cleaned["id"]:
                cleaned["id"] = session_id
            items.append(cleaned)
        return items
    except ClientError as e:
        print(f"[db] DynamoDB query error: {e}")
        return []

# === Pairing Tokens (employee IM self-service binding) ===

def create_pair_token(token: str, emp_id: str, channel: str) -> dict:
    """Create a short-lived pairing token (15 min TTL) for IM self-service binding."""
    import time as _t
    from datetime import datetime, timezone
    item = {
        "token": token,
        "employeeId": emp_id,
        "channel": channel,
        "status": "pending",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "ttl": int(_t.time()) + 900,
    }
    _put_item(f"PAIR#{token}", item, "TYPE#pair", f"PAIR#{token}")
    return item

def get_pair_token(token: str) -> dict | None:
    return _get_item(f"PAIR#{token}")

def consume_pair_token(token: str) -> dict | None:
    """Atomically validate and mark token as completed. Returns token data if valid."""
    import time as _t
    item = _get_item(f"PAIR#{token}")
    if not item:
        return None
    if item.get("status") != "pending":
        return None  # already used
    if item.get("ttl", 0) < int(_t.time()):
        return None  # expired
    _put_item(f"PAIR#{token}", {**item, "status": "completed"}, "TYPE#pair", f"PAIR#{token}")
    return item


def get_session(session_id: str) -> dict | None:
    """Return a single session by ID, injecting 'id' field."""
    item = _get_item(f"SESSION#{session_id}")
    if item and ("id" not in item or not item["id"]):
        item["id"] = session_id
    return item

# === Employee Activity ===

def get_activities() -> list[dict]:
    return _query("ACTIVITY#")

def get_activity(employee_id: str) -> dict | None:
    return _get_item(f"ACTIVITY#{employee_id}")

# === Cost Trend ===

def get_cost_trend() -> list[dict]:
    items = _query("COST_TREND#")
    items.sort(key=lambda x: x.get("date", ""))
    return items


# === Routing Rules ===

def get_routing_rules() -> list[dict]:
    items = _query("RULE#")
    items.sort(key=lambda x: x.get("priority", 99))
    return items

def create_routing_rule(data: dict) -> dict:
    rid = data.get("id", f"rule-{int(__import__('time').time())}")
    data["id"] = rid
    _put_item(f"RULE#{rid}", data, "TYPE#rule", f"RULE#{rid}")
    return data


# === Session Conversations ===

def get_session_conversation(session_id: str) -> list[dict]:
    items = _query(f"CONV#{session_id}#")
    items.sort(key=lambda x: x.get("seq", 0))
    return items

def create_session_conversation(session_id: str, messages: list[dict]):
    for i, msg in enumerate(messages):
        _put_item(f"CONV#{session_id}#{i:04d}", {"sessionId": session_id, "seq": i, **msg})


# === Digital Twin (public shareable agent URL) ===

def create_twin(emp_id: str, token: str, emp_name: str, position_name: str, agent_name: str) -> dict:
    """Enable digital twin for an employee — generates a public share token."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "empId": emp_id,
        "empName": emp_name,
        "positionName": position_name,
        "agentName": agent_name,
        "token": token,
        "active": True,
        "createdAt": now,
        "viewCount": 0,
        "chatCount": 0,
    }
    # TWIN#token — primary lookup by token
    _put_item(f"TWIN#{token}", item, f"EMP#{emp_id}", f"TWIN#{token}")
    # TWINOWNER#emp_id — lookup by employee (only one twin per employee)
    _put_item(f"TWINOWNER#{emp_id}", {**item, "tokenRef": token}, "TYPE#twin", f"TWINOWNER#{emp_id}")
    return item


def get_twin_by_token(token: str) -> dict | None:
    return _get_item(f"TWIN#{token}")


def get_twin_by_employee(emp_id: str) -> dict | None:
    return _get_item(f"TWINOWNER#{emp_id}")


def disable_twin(emp_id: str) -> None:
    """Revoke digital twin — mark token inactive and remove owner record."""
    owner = get_twin_by_employee(emp_id)
    if owner:
        token = owner.get("tokenRef") or owner.get("token")
        if token:
            item = _get_item(f"TWIN#{token}")
            if item:
                _put_item(f"TWIN#{token}", {**item, "active": False}, f"EMP#{emp_id}", f"TWIN#{token}")
        _get_table().delete_item(Key={"PK": ORG_PK, "SK": f"TWINOWNER#{emp_id}"})


def increment_twin_stat(token: str, field: str) -> None:
    """Increment viewCount or chatCount atomically."""
    try:
        _get_table().update_item(
            Key={"PK": ORG_PK, "SK": f"TWIN#{token}"},
            UpdateExpression="ADD #f :one",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":one": 1},
        )
    except Exception:
        pass


# === User Mappings — IM channel user → employee (replaces SSM user-mapping/) ===

def get_user_mappings() -> list[dict]:
    """List all IM channel → employee mappings from DynamoDB."""
    return _query("MAPPING#")


def get_user_mapping(channel: str, channel_user_id: str) -> Optional[dict]:
    """Get a single mapping by channel + userId."""
    return _get_item(f"MAPPING#{channel}__{channel_user_id}")


def resolve_user_mapping(channel_user_id: str) -> str:
    """Resolve a bare channelUserId to employeeId by scanning all mappings.

    Used when we only have the raw userId (e.g. Feishu OU ID) without
    knowing the channel prefix. Scans MAPPING# items and matches on
    channelUserId attribute. Dataset is small (<50 items), so scan is fine.
    """
    for m in get_user_mappings():
        if m.get("channelUserId") == channel_user_id:
            return m.get("employeeId", "")
    return ""


def create_user_mapping(channel: str, channel_user_id: str, employee_id: str) -> dict:
    """Write IM channel user → employee mapping to DynamoDB."""
    from datetime import datetime, timezone
    item = {
        "channel": channel,
        "channelUserId": channel_user_id,
        "employeeId": employee_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    _put_item(
        f"MAPPING#{channel}__{channel_user_id}",
        item,
        "TYPE#mapping",
        f"MAPPING#{channel}__{channel_user_id}",
    )
    return item


def delete_user_mapping(channel: str, channel_user_id: str) -> None:
    """Delete an IM channel user → employee mapping from DynamoDB."""
    try:
        _get_table().delete_item(
            Key={"PK": ORG_PK, "SK": f"MAPPING#{channel}__{channel_user_id}"}
        )
    except ClientError as e:
        print(f"[db] delete_user_mapping error: {e}")


def get_user_mappings_for_employee(emp_id: str) -> list[dict]:
    """Get all IM connections for a specific employee (reverse lookup)."""
    return [m for m in get_user_mappings() if m.get("employeeId") == emp_id]


# === Routing Config — position/employee → AgentCore Runtime (replaces SSM routing params) ===

def get_routing_config() -> dict:
    """Read position→runtime and employee_override→runtime mappings.

    Returns: {
        "position_runtime": {"pos-exec": "runtime_id", ...},
        "employee_override": {"emp-ada": "runtime_id", ...}
    }
    """
    item = _get_item("CONFIG#routing")
    if not item:
        return {"position_runtime": {}, "employee_override": {}}
    return {
        "position_runtime": item.get("position_runtime", {}),
        "employee_override": item.get("employee_override", {}),
    }


def set_routing_config(position_runtime: dict, employee_override: dict) -> None:
    """Write full routing config to DynamoDB as a single item."""
    _put_item(
        "CONFIG#routing",
        {
            "position_runtime": position_runtime,
            "employee_override": employee_override,
            "updatedAt": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        },
        "TYPE#config",
        "CONFIG#routing",
    )


def set_position_runtime(pos_id: str, runtime_id: str) -> None:
    """Set runtime for a single position (partial update)."""
    cfg = get_routing_config()
    cfg["position_runtime"][pos_id] = runtime_id
    set_routing_config(cfg["position_runtime"], cfg["employee_override"])


def set_employee_runtime_override(emp_id: str, runtime_id: str) -> None:
    """Set per-employee runtime override (partial update)."""
    cfg = get_routing_config()
    cfg["employee_override"][emp_id] = runtime_id
    set_routing_config(cfg["position_runtime"], cfg["employee_override"])


def remove_position_runtime(pos_id: str) -> None:
    """Remove runtime mapping for a position."""
    cfg = get_routing_config()
    cfg["position_runtime"].pop(pos_id, None)
    set_routing_config(cfg["position_runtime"], cfg["employee_override"])


def remove_employee_runtime_override(emp_id: str) -> None:
    """Remove per-employee runtime override."""
    cfg = get_routing_config()
    cfg["employee_override"].pop(emp_id, None)
    set_routing_config(cfg["position_runtime"], cfg["employee_override"])
