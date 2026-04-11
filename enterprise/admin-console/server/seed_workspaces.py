"""Seed S3 with sample workspace files for key employees."""
import boto3, os

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

def get_bucket():
    account = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()["Account"]
    return f"openclaw-tenants-{account}"

def put(s3, bucket, key, content):
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"), ContentType="text/markdown")

EMPLOYEES = {
    "emp-jiade": {"name": "JiaDe Wang", "role": "Solutions Architect", "dept": "Engineering", "tz": "America/Los_Angeles", "lang": "English",
        "focus": "OpenClaw Enterprise on AgentCore — multi-tenant digital workforce platform", "style": "Technical, concise. Architecture diagrams and cost comparisons.",
        "memory": "Leading OpenClaw Enterprise project. Gateway architecture with H2 Proxy + Tenant Router. 20 employees, 20 agents deployed. Discord Bot connected.",
        "daily": "Deployed SESSION_CONTEXT.md injection. Updated all seed data to English-only org. Verified SOUL assembly across all 5 access paths."},
    "emp-marcus": {"name": "Marcus Bell", "role": "Solutions Architect", "dept": "Engineering", "tz": "America/New_York", "lang": "English",
        "focus": "Cloud migration projects — TechCorp and RetailCo workloads", "style": "Concise, code examples over prose. Comparison tables for costs.",
        "memory": "TechCorp migration: ECS chosen over EKS. HTTP API vs REST API decision pending. DynamoDB single-table design recommended.",
        "daily": "Reviewed microservice architecture for TechCorp. Cost estimate $847/mo. Suggested 40% optimization via Graviton + HTTP API."},
    "emp-ryan": {"name": "Ryan Park", "role": "Software Engineer", "dept": "Backend Team", "tz": "America/Los_Angeles", "lang": "English",
        "focus": "Payment API microservice, backend infrastructure", "style": "Code-first, unit test examples in every response.",
        "memory": "Payment API feature branch ready for PR. Chose DynamoDB over RDS for session storage. Auth service refactor in progress.",
        "daily": "Merged payment API feature branch. Code review for Sophie's PR. Investigated latency spike in auth service."},
    "emp-mike": {"name": "Mike Johnson", "role": "Account Executive", "dept": "Enterprise Sales", "tz": "America/New_York", "lang": "English",
        "focus": "Fortune 500 accounts — TechCorp and Acme Manufacturing deals", "style": "ROI-focused, always prepare battle cards before calls.",
        "memory": "TechCorp deal at Negotiation stage, $250K. Acme Manufacturing at Proposal, $180K. Q2 pipeline target: $1.2M.",
        "daily": "Prepared competitive analysis for TechCorp. Updated CRM pipeline. Scheduled QBR with Acme for next week."},
    "emp-carol": {"name": "Carol Zhang", "role": "Finance Analyst", "dept": "Finance", "tz": "America/Los_Angeles", "lang": "English",
        "focus": "Q2 2026 budget variance reports", "style": "Tables and charts over narrative. Always include variance analysis.",
        "memory": "Engineering Q2 budget: $500K allocated, 37.5% utilized. SaaS license renewal due April 15. Travel budget under-spent.",
        "daily": "Generated Q2 budget variance report for Engineering. Flagged SaaS license renewal. Updated forecast model."},
    "emp-alex": {"name": "Alex Rivera", "role": "Product Manager", "dept": "Product", "tz": "America/New_York", "lang": "English",
        "focus": "Enterprise console v2 features, user research synthesis", "style": "Data-driven, RICE framework for prioritization.",
        "memory": "Top feature request: department tree drag-and-drop. NPS score: 72. Sprint 12 velocity: 34 points.",
        "daily": "Synthesized 5 user interviews. Key finding: admins want bulk agent provisioning. Updated roadmap in Notion."},
    "emp-emma": {"name": "Emma Chen", "role": "Customer Success Manager", "dept": "Customer Success", "tz": "America/New_York", "lang": "English",
        "focus": "Enterprise accounts QBR preparation", "style": "Health score driven, proactive outreach for at-risk accounts.",
        "memory": "TechCorp health score: 85 (green). DataFlow Inc: 62 (yellow, declining usage). QBR deck template updated.",
        "daily": "Prepared QBR deck for TechCorp. Flagged DataFlow as at-risk. Scheduled check-in call for Friday."},
    "emp-rachel": {"name": "Rachel Li", "role": "Legal Counsel", "dept": "Legal & Compliance", "tz": "America/New_York", "lang": "English",
        "focus": "GDPR compliance review, vendor contract templates", "style": "Cite specific regulations. Always add legal disclaimer.",
        "memory": "Updated DPA template for GDPR Article 28. Vendor contract review backlog: 3 pending. SOC 2 audit scheduled May.",
        "daily": "Reviewed 2 vendor contracts. Flagged missing data processing addendum in CloudVendor agreement."},
    "emp-peter": {"name": "Peter Wu", "role": "Executive", "dept": "Engineering", "tz": "America/New_York", "lang": "English",
        "focus": "Strategic planning, team management, technology evaluation", "style": "High-level, strategic. Focus on business impact and ROI.",
        "memory": "Evaluating OpenClaw Enterprise for team adoption. Interested in cost savings vs ChatGPT Team. Cold start latency acceptable at 6s.",
        "daily": "Tested Discord Bot integration. Verified role-based access control — no finance/HR data access for Executive role."},
}

def seed():
    s3 = boto3.client("s3", region_name=AWS_REGION)
    bucket = get_bucket()
    count = 0

    for emp_id, e in EMPLOYEES.items():
        prefix = f"{emp_id}/workspace"

        # IDENTITY.md
        put(s3, bucket, f"{prefix}/IDENTITY.md", f"""# Agent Identity

- **Name:** {e['name']}'s AI Assistant
- **Role:** {e['role']} Digital Employee
- **Department:** {e['dept']}
- **Vibe:** Professional, knowledgeable, {e['style'].split('.')[0].lower()}
""")

        # USER.md
        put(s3, bucket, f"{prefix}/USER.md", f"""# User Profile — {e['name']}

- **Name:** {e['name']}
- **Role:** {e['role']}
- **Department:** {e['dept']}
- **Timezone:** {e['tz']}
- **Language:** {e['lang']}
- **Communication style:** {e['style']}
- **Current focus:** {e['focus']}
""")

        # MEMORY.md
        put(s3, bucket, f"{prefix}/MEMORY.md", f"""# Agent Memory — {e['name']}

## Key Context
{e['memory']}

## Learned Preferences
- {e['style']}
""")

        # PERSONAL_SOUL.md — employee's personal SOUL layer (editable, persisted to S3)
        put(s3, bucket, f"{prefix}/PERSONAL_SOUL.md", f"""# Personal Preferences — {e['name']}

- **Communication style:** {e['style']}
- **Current focus:** {e['focus']}
""")

        # Daily memory
        put(s3, bucket, f"{prefix}/memory/2026-03-20.md", f"""# March 20, 2026

## Session Summary
{e['daily']}
""")

        count += 1
        print(f"  {emp_id} ({e['name']}): IDENTITY.md, USER.md, MEMORY.md, PERSONAL_SOUL.md, memory/2026-03-20.md")

    print(f"\nDone! {count} employee workspaces seeded.")

if __name__ == "__main__":
    seed()
