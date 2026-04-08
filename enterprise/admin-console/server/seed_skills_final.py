"""Final authoritative skill catalog. Replaces all previous seed scripts."""
import json, boto3, os

AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")

def get_bucket():
    bucket = os.environ.get("S3_BUCKET", "")
    if bucket:
        return bucket
    account = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()["Account"]
    return f"openclaw-tenants-{account}"

SKILLS = [
    # ===================== GLOBAL (all employees) =====================
    {"name": "web-search", "version": "1.0.0", "description": "Search the web using multiple search engines.", "author": "OpenClaw Core", "layer": 1, "category": "information", "scope": "global",
     "requires": {"env": [], "tools": []}, "permissions": {"allowedRoles": ["*"], "blockedRoles": []}},
    {"name": "jina-reader", "version": "2.1.0", "description": "Extract clean text from any URL using Jina AI.", "author": "OpenClaw Core", "layer": 1, "category": "information", "scope": "global",
     "requires": {"env": [], "tools": ["web_fetch"]}, "permissions": {"allowedRoles": ["*"], "blockedRoles": []}},
    {"name": "deep-research", "version": "1.3.0", "description": "Multi-step research with sub-agent orchestration across multiple sources.", "author": "OpenClaw Core", "layer": 1, "category": "information", "scope": "global",
     "requires": {"env": [], "tools": ["web_search", "jina-reader"]}, "permissions": {"allowedRoles": ["*"], "blockedRoles": ["intern"]}},
    {"name": "s3-files", "version": "1.2.0", "description": "Upload, download, and share files via S3 with pre-signed URLs.", "author": "OpenClaw Core", "layer": 1, "category": "productivity", "scope": "global",
     "requires": {"env": [], "tools": []}, "permissions": {"allowedRoles": ["*"], "blockedRoles": []}, "awsService": "Amazon S3"},
    {"name": "summarize", "version": "1.4.0", "description": "Summarize URLs, PDFs, audio files, and YouTube videos.", "author": "ClawHub", "layer": 1, "category": "productivity", "scope": "global",
     "requires": {"env": [], "tools": ["web_fetch"]}, "permissions": {"allowedRoles": ["*"], "blockedRoles": []}, "downloads": 26100},
    {"name": "self-improving-agent", "version": "1.3.0", "description": "Captures corrections and learnings across sessions. Promotes recurring learnings to permanent memory.", "author": "ClawHub", "layer": 1, "category": "memory", "scope": "global",
     "requires": {"env": [], "tools": ["file", "file_write"]}, "permissions": {"allowedRoles": ["*"], "blockedRoles": []}, "downloads": 32000},

    # ===================== ENGINEERING (SA, SDE, DevOps, QA) =====================
    {"name": "github", "version": "3.0.0", "description": "Manage GitHub issues, PRs, and CI runs. Triage issues, review PRs, debug CI failures.", "author": "ClawHub", "layer": 1, "category": "development", "scope": "department",
     "requires": {"env": ["GITHUB_TOKEN"], "tools": ["shell"]}, "permissions": {"allowedRoles": ["engineering", "devops", "qa"], "blockedRoles": ["intern"]}, "downloads": 24800},
    {"name": "jira-query", "version": "1.0.0", "description": "Query Jira issues by ID or JQL. Create and update tickets.", "author": "ACME IT", "layer": 2, "category": "project-management", "scope": "department",
     "requires": {"env": ["JIRA_API_TOKEN", "JIRA_BASE_URL"], "tools": ["web_fetch"]}, "permissions": {"allowedRoles": ["engineering", "product", "qa", "management"], "blockedRoles": ["intern"]}},
    {"name": "firecrawl", "version": "1.2.0", "description": "Advanced web scraping and browser automation. Handles JS-heavy pages and bot-protected sites.", "author": "Firecrawl", "layer": 1, "category": "information", "scope": "department",
     "requires": {"env": ["FIRECRAWL_API_KEY"], "tools": ["web_fetch"]}, "permissions": {"allowedRoles": ["engineering", "product", "devops"], "blockedRoles": []}, "downloads": 45000},

    # ===================== SALES & CSM =====================
    {"name": "crm-query", "version": "1.1.0", "description": "Query Salesforce CRM — accounts, contacts, opportunities, pipeline data.", "author": "ACME Sales Ops", "layer": 2, "category": "crm", "scope": "department",
     "requires": {"env": ["SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_INSTANCE_URL"], "tools": ["web_fetch"]}, "permissions": {"allowedRoles": ["sales", "csm", "management"], "blockedRoles": []}},
    {"name": "aws-ses-mailer", "version": "1.0.0", "description": "Send emails via Amazon SES. HTML templates, attachments, CC/BCC, delivery tracking via SNS.", "author": "ACME IT", "layer": 2, "category": "communication", "scope": "department",
     "requires": {"env": ["SES_FROM_EMAIL"], "tools": []}, "permissions": {"allowedRoles": ["sales", "csm", "hr", "management"], "blockedRoles": ["intern"]}, "awsService": "Amazon SES + SNS",
     "approvalRequired": True, "approvalNote": "Every email requires employee confirmation before sending"},
    {"name": "aws-nova-sonic-caller", "version": "1.0.0", "description": "Make AI-powered outbound voice calls using Amazon Nova Sonic + Connect. Real-time conversation with CRM lookup during calls.", "author": "ACME IT", "layer": 2, "category": "communication", "scope": "department",
     "requires": {"env": ["CONNECT_INSTANCE_ID", "CONNECT_CONTACT_FLOW_ID"], "tools": []}, "permissions": {"allowedRoles": ["sales", "csm"], "blockedRoles": []}, "awsService": "Amazon Bedrock (Nova Sonic) + Connect",
     "approvalRequired": True, "approvalNote": "Every outbound call requires employee confirmation"},
    {"name": "aws-sns-notify", "version": "1.0.0", "description": "Send notifications via Amazon SNS — SMS, email, push, or webhook for customer alerts and team updates.", "author": "ACME IT", "layer": 2, "category": "communication", "scope": "department",
     "requires": {"env": ["SNS_TOPIC_ARN"], "tools": []}, "permissions": {"allowedRoles": ["sales", "csm", "management"], "blockedRoles": ["intern"]}, "awsService": "Amazon SNS",
     "approvalRequired": True, "approvalNote": "External notifications require employee confirmation"},
    {"name": "pptx-creator", "version": "1.6.0", "description": "Generate PowerPoint presentations from outlines or data. Templates: minimal, corporate, creative, dark, executive. Speaker notes and charts.", "author": "ClawHub", "layer": 1, "category": "productivity", "scope": "department",
     "requires": {"env": [], "tools": ["file_write"]}, "permissions": {"allowedRoles": ["sales", "csm", "product", "management"], "blockedRoles": ["intern"]}, "downloads": 18500},

    # ===================== PRODUCT =====================
    {"name": "notion", "version": "1.8.0", "description": "Full Notion API: read/write pages, databases, blocks, comments. Handles pagination for large datasets.", "author": "ClawHub", "layer": 1, "category": "productivity", "scope": "department",
     "requires": {"env": ["NOTION_API_KEY"], "tools": []}, "permissions": {"allowedRoles": ["product", "engineering", "management"], "blockedRoles": ["intern"]}, "downloads": 13900},
    {"name": "transcript", "version": "1.0.0", "description": "Extract transcripts from YouTube videos for analysis and summarization.", "author": "OpenClaw Core", "layer": 1, "category": "productivity", "scope": "department",
     "requires": {"env": [], "tools": ["web_fetch"]}, "permissions": {"allowedRoles": ["product", "management"], "blockedRoles": []}},

    # ===================== FINANCE =====================
    {"name": "sap-connector", "version": "1.0.0", "description": "Query SAP ERP — invoices, purchase orders, budget reports, expense tracking.", "author": "ACME Finance IT", "layer": 2, "category": "erp", "scope": "department",
     "requires": {"env": ["SAP_CLIENT_ID", "SAP_CLIENT_SECRET", "SAP_BASE_URL"], "tools": ["web_fetch"]}, "permissions": {"allowedRoles": ["finance", "management"], "blockedRoles": []}},
    {"name": "excel-gen", "version": "1.0.0", "description": "Generate Excel spreadsheets from structured data. Supports charts, formatting, and multiple sheets.", "author": "ACME IT", "layer": 2, "category": "data", "scope": "department",
     "requires": {"env": [], "tools": ["file_write"]}, "permissions": {"allowedRoles": ["finance", "management", "hr"], "blockedRoles": []}},
    {"name": "aws-s3-docs", "version": "1.0.0", "description": "Create and share documents in S3. Markdown-to-PDF, version history, pre-signed URLs for secure sharing.", "author": "ACME IT", "layer": 2, "category": "productivity", "scope": "department",
     "requires": {"env": [], "tools": ["file_write"]}, "permissions": {"allowedRoles": ["finance", "legal", "management"], "blockedRoles": []}, "awsService": "Amazon S3"},

    # ===================== HR =====================
    {"name": "himalaya", "version": "2.0.0", "description": "IMAP/SMTP email for any provider — Outlook, ProtonMail, custom domains. Read, send, reply, forward.", "author": "ClawHub", "layer": 1, "category": "communication", "scope": "department",
     "requires": {"env": ["IMAP_HOST", "IMAP_USER", "IMAP_PASS", "SMTP_HOST", "SMTP_USER", "SMTP_PASS"], "tools": []}, "permissions": {"allowedRoles": ["hr", "management"], "blockedRoles": []}, "downloads": 9200,
     "approvalRequired": True, "approvalNote": "Email send requires employee confirmation"},
    {"name": "calendar-check", "version": "1.0.0", "description": "Check Google Calendar availability, list meetings, find free slots for scheduling.", "author": "ACME IT", "layer": 2, "category": "productivity", "scope": "department",
     "requires": {"env": ["GOOGLE_SERVICE_ACCOUNT_KEY"], "tools": []}, "permissions": {"allowedRoles": ["hr", "management", "csm"], "blockedRoles": []}},
    {"name": "aws-transcribe-notes", "version": "1.0.0", "description": "Transcribe meeting recordings via Amazon Transcribe, then generate structured notes with action items and decisions.", "author": "ACME IT", "layer": 2, "category": "productivity", "scope": "department",
     "requires": {"env": [], "tools": ["file_write"]}, "permissions": {"allowedRoles": ["hr", "product", "management"], "blockedRoles": []}, "awsService": "Amazon Transcribe"},

    # ===================== LEGAL =====================
    {"name": "aws-bedrock-kb-search", "version": "1.0.0", "description": "Search enterprise knowledge bases via Amazon Bedrock Knowledge Bases (RAG). Semantic search with source attribution.", "author": "ACME IT", "layer": 2, "category": "information", "scope": "department",
     "requires": {"env": ["BEDROCK_KB_ID"], "tools": []}, "permissions": {"allowedRoles": ["legal", "management"], "blockedRoles": []}, "awsService": "Amazon Bedrock Knowledge Bases"},

    # ===================== MARKETING / CREATIVE =====================
    {"name": "aws-nova-canvas", "version": "1.0.0", "description": "Generate and edit images using Amazon Nova Canvas via Bedrock. Text-to-image, variations, background removal, inpainting.", "author": "ACME IT", "layer": 2, "category": "creative", "scope": "department",
     "requires": {"env": [], "tools": ["file_write"]}, "permissions": {"allowedRoles": ["sales", "csm", "product", "management"], "blockedRoles": ["intern"]}, "awsService": "Amazon Bedrock (Nova Canvas)"},
    {"name": "gog", "version": "2.1.0", "description": "Google Workspace all-in-one: Gmail, Calendar, Drive, Docs, Sheets. Send emails, create documents, schedule meetings.", "author": "ClawHub", "layer": 1, "category": "productivity", "scope": "department",
     "requires": {"env": ["GOOGLE_SERVICE_ACCOUNT_KEY"], "tools": []}, "permissions": {"allowedRoles": ["sales", "csm", "hr", "management"], "blockedRoles": ["intern"]}, "downloads": 33800},

    # ===================== SECURITY (IT only) =====================
    {"name": "skill-vetter", "version": "1.1.0", "description": "Security scanner for ClawHub skills. Checks for undeclared env access, hidden network calls, obfuscated commands.", "author": "ClawHub", "layer": 1, "category": "security", "scope": "department",
     "requires": {"env": [], "tools": ["file"]}, "permissions": {"allowedRoles": ["engineering", "devops", "management"], "blockedRoles": []}, "downloads": 3500},
]

def seed():
    s3 = boto3.client("s3", region_name=AWS_REGION)
    bucket = get_bucket()
    for skill in SKILLS:
        key = f"_shared/skills/{skill['name']}/skill.json"
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(skill, indent=2).encode("utf-8"), ContentType="application/json")
        roles = skill["permissions"]["allowedRoles"]
        scope = "ALL" if "*" in roles else ", ".join(roles)
        print(f"  L{skill['layer']} {skill['name']:25s} → {scope}")
    print(f"\nDone! {len(SKILLS)} skills.")

if __name__ == "__main__":
    seed()
