"""Seed DynamoDB with settings (model config, security policy)."""
import argparse
import os
import json
import boto3

ORG = "ORG#acme"
MODEL_ID = os.environ.get("MODEL", "zai.glm-5")
MODEL_DETAILS = {
    "zai.glm-5": {"modelName": "Z.AI GLM-5", "inputRate": "1.00", "outputRate": "3.20"},
}
DEFAULT_MODEL = {"modelId": MODEL_ID, **MODEL_DETAILS.get(
    MODEL_ID, {"modelName": MODEL_ID, "inputRate": "0", "outputRate": "0"}
)}


def seed(table_name: str, region: str):
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)
    items = []

    # Update the deployment-wide defaults while preserving explicit admin overrides.
    # This lets a redeploy migrate legacy Nova defaults to MODEL without discarding
    # employee- or position-specific model choices.
    existing_model_config = table.get_item(
        Key={"PK": ORG, "SK": "CONFIG#model"}
    ).get("Item", {})
    position_overrides = existing_model_config.get("positionOverrides", {})
    employee_overrides = existing_model_config.get("employeeOverrides", {})

    # Model config. Keep deployment, DynamoDB, Gateway, and AgentCore on GLM-5
    # unless an administrator explicitly creates an employee or position override.
    items.append({"PK": ORG, "SK": "CONFIG#model", "GSI1PK": "TYPE#config", "GSI1SK": "CONFIG#model",
        "default": DEFAULT_MODEL,
        "fallback": DEFAULT_MODEL,
        "positionOverrides": position_overrides if isinstance(position_overrides, dict) else {},
        "employeeOverrides": employee_overrides if isinstance(employee_overrides, dict) else {},
        "availableModels": [
            {"modelId": "global.amazon.nova-2-lite-v1:0", "modelName": "Amazon Nova 2 Lite", "inputRate": "0.30", "outputRate": "2.50", "enabled": True},
            {"modelId": "us.amazon.nova-pro-v1:0", "modelName": "Amazon Nova Pro", "inputRate": "0.80", "outputRate": "3.20", "enabled": True},
            {"modelId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "modelName": "Claude Sonnet 4.5", "inputRate": "3.00", "outputRate": "15.00", "enabled": True},
            {"modelId": "global.anthropic.claude-opus-4-6-v1", "modelName": "Claude Opus 4.6", "inputRate": "15.00", "outputRate": "75.00", "enabled": False},
            {"modelId": "us.deepseek.r1-v1:0", "modelName": "DeepSeek R1", "inputRate": "0.55", "outputRate": "2.19", "enabled": False},
            {"modelId": "moonshotai.kimi-k2.5", "modelName": "Kimi K2.5", "inputRate": "0.60", "outputRate": "3.00", "enabled": False},
            {"modelId": "zai.glm-5", "modelName": "Z.AI GLM-5", "inputRate": "1.00", "outputRate": "3.20", "enabled": True},
        ],
    })

    # Security config
    items.append({"PK": ORG, "SK": "CONFIG#security", "GSI1PK": "TYPE#config", "GSI1SK": "CONFIG#security",
        "alwaysBlocked": ["install_skill", "load_extension", "eval", "rm -rf /", "chmod 777"],
        "piiDetection": {"enabled": True, "mode": "redact"},
        "dataSovereignty": {"enabled": True, "region": "us-east-1"},
        "conversationRetention": {"days": 180},
        "dockerSandbox": True,
        "fastPathRouting": True,
        "verboseAudit": False,
    })

    # KB assignments — which knowledge bases each position receives by default.
    # All positions get company policies + onboarding; role-specific KBs are layered on top.
    # Admins can adjust these from Knowledge Base → Assignments tab in the Admin Console.
    items.append({"PK": ORG, "SK": "CONFIG#kb-assignments", "GSI1PK": "TYPE#config", "GSI1SK": "CONFIG#kb-assignments",
        "positionKBs": {
            "pos-sa":     ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-cases", "kb-arch"],
            "pos-sde":    ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-arch", "kb-runbooks"],
            "pos-devops": ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-runbooks"],
            "pos-qa":     ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-arch"],
            "pos-ae":     ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-cases"],
            "pos-pm":     ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-product"],
            "pos-fa":     ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-finance"],
            "pos-hr":     ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-hr"],
            "pos-csm":    ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-customer", "kb-cases"],
            "pos-legal":  ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-legal"],
            "pos-exec":   ["kb-policies", "kb-onboarding", "kb-org-directory", "kb-finance", "kb-product"],
            "pos-admin":  ["kb-policies", "kb-onboarding", "kb-org-directory"],
        },
        "employeeKBs": {},
    })

    # IM bot info — admin configures actual values via Admin Console after
    # setting up bots in the Gateway UI.  Deep link templates are fixed per
    # platform; only the bot-specific fields (appId, username) need filling in.
    items.append({"PK": ORG, "SK": "CONFIG#im-bot-info", "GSI1PK": "TYPE#config", "GSI1SK": "CONFIG#im-bot-info",
        "channels": {
            "telegram": {
                "label": "Telegram",
                "botUsername": "",
                "deepLinkTemplate": "https://t.me/{bot}?start={token}",
            },
            "discord": {
                "label": "Discord",
                "botUsername": "",
                "instructions": "Open Discord → company server → DM the bot → send the command",
            },
            "feishu": {
                "label": "Feishu / Lark",
                "botUsername": "",
                "feishuAppId": "",
                "deepLinkTemplate": "https://applink.feishu.cn/client/bot/open?appId={appId}",
            },
            "slack": {
                "label": "Slack",
                "botUsername": "",
            },
            "whatsapp": {
                "label": "WhatsApp",
                "botUsername": "",
            },
        },
    })

    print(f"Writing {len(items)} config items...")
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print("Done!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=os.environ.get("DYNAMODB_TABLE", "openclaw"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()
    seed(args.table, args.region)
