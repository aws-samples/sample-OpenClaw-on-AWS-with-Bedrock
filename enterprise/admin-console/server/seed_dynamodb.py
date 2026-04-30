"""
Seed DynamoDB with enterprise demo data.
Single-table design: PK/SK pattern from PRD §15.

Usage: python seed_dynamodb.py [--region us-east-1] [--table openclaw]
"""
import argparse
import os
import json
import time
import boto3

ORG = "ORG#acme"

def seed(table_name: str, region: str):
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)

    items = []

    # --- Organization meta ---
    items.append({"PK": ORG, "SK": "META", "GSI1PK": "TYPE#org", "GSI1SK": ORG,
        "name": "ACME Corp", "plan": "enterprise", "createdAt": "2026-01-10T00:00:00Z"})

    # --- Departments ---
    depts = [
        ("dept-eng", "Engineering", None, 22), ("dept-eng-platform", "Platform Team", "dept-eng", 5),
        ("dept-eng-backend", "Backend Team", "dept-eng", 8), ("dept-eng-frontend", "Frontend Team", "dept-eng", 5),
        ("dept-eng-qa", "QA Team", "dept-eng", 4), ("dept-sales", "Sales", None, 12),
        ("dept-sales-ent", "Enterprise Sales", "dept-sales", 5), ("dept-sales-smb", "SMB Sales", "dept-sales", 7),
        ("dept-product", "Product", None, 6), ("dept-finance", "Finance", None, 5),
        ("dept-hr", "HR & Admin", None, 4), ("dept-cs", "Customer Success", None, 6),
        ("dept-legal", "Legal & Compliance", None, 3),
    ]
    for did, name, parent, hc in depts:
        items.append({"PK": ORG, "SK": f"DEPT#{did}", "GSI1PK": "TYPE#dept", "GSI1SK": f"DEPT#{did}",
            "id": did, "name": name, "parentId": parent, "headCount": hc, "createdAt": "2026-01-10T00:00:00Z"})

    # --- Positions ---
    positions = [
        ("pos-sa", "Solutions Architect", "dept-eng", "Engineering", ["jina-reader","deep-research","arch-diagram-gen","cost-calculator"], ["web_search","shell","browser","file","code_execution"], 3),
        ("pos-sde", "Software Engineer", "dept-eng", "Engineering", ["jina-reader","deep-research","github-pr","code-review"], ["web_search","shell","browser","file","file_write","code_execution"], 8),
        ("pos-devops", "DevOps Engineer", "dept-eng-platform", "Platform Team", ["jina-reader","deep-research","github-pr"], ["web_search","shell","browser","file","file_write","code_execution"], 3),
        ("pos-qa", "QA Engineer", "dept-eng-qa", "QA Team", ["jina-reader","deep-research","jira-query"], ["web_search","shell","file","code_execution"], 3),
        ("pos-ae", "Account Executive", "dept-sales", "Sales", ["jina-reader","web-search","crm-query"], ["web_search","file"], 6),
        ("pos-pm", "Product Manager", "dept-product", "Product", ["jina-reader","deep-research","jira-query","transcript"], ["web_search","browser","file"], 4),
        ("pos-fa", "Finance Analyst", "dept-finance", "Finance", ["jina-reader","sap-connector","excel-gen"], ["web_search","file"], 3),
        ("pos-hr", "HR Specialist", "dept-hr", "HR & Admin", ["jina-reader","web-search"], ["web_search","file"], 3),
        ("pos-csm", "Customer Success Manager", "dept-cs", "Customer Success", ["jina-reader","web-search","crm-query","slack-bridge"], ["web_search","file","browser"], 4),
        ("pos-legal", "Legal Counsel", "dept-legal", "Legal & Compliance", ["jina-reader","deep-research"], ["web_search","file"], 2),
        ("pos-exec", "Executive", "dept-eng", "Engineering", ["jina-reader","deep-research","web_search"], ["web_search","shell","browser","file","file_write","code_execution"], 1),
    ]
    for pid, name, did, dname, skills, tools, mc in positions:
        items.append({"PK": ORG, "SK": f"POS#{pid}", "GSI1PK": "TYPE#pos", "GSI1SK": f"POS#{pid}",
            "id": pid, "name": name, "departmentId": did, "departmentName": dname,
            "defaultSkills": skills, "toolAllowlist": tools, "memberCount": mc, "createdAt": "2026-01-20T00:00:00Z"})

    # --- Employees ---
    employees = [
        # Engineering — Solutions Architects
        ("emp-jiade",  "JiaDe Wang",    "EMP-001", "pos-sa",    "Solutions Architect",         "dept-eng",          "Engineering",       ["discord","slack"],   "agent-sa-jiade",   "active"),
        ("emp-marcus", "Marcus Bell",   "EMP-002", "pos-sa",    "Solutions Architect",         "dept-eng",          "Engineering",       ["slack","telegram"],  "agent-sa-marcus",  "active"),
        ("emp-daniel", "Daniel Kim",    "EMP-003", "pos-sa",    "Solutions Architect",         "dept-eng",          "Engineering",       ["slack"],             "agent-sa-daniel",  "active"),
        # Engineering — Software Engineers
        ("emp-ryan",   "Ryan Park",     "EMP-004", "pos-sde",   "Software Engineer",           "dept-eng-backend",  "Backend Team",      ["slack","discord"],   "agent-sde-ryan",   "active"),
        ("emp-sophie", "Sophie Turner", "EMP-005", "pos-sde",   "Software Engineer",           "dept-eng-backend",  "Backend Team",      ["slack"],             "agent-sde-sophie", "active"),
        ("emp-nathan", "Nathan Brooks", "EMP-006", "pos-sde",   "Software Engineer",           "dept-eng-frontend", "Frontend Team",     ["slack"],             "agent-sde-nathan", "active"),
        # Engineering — DevOps & QA
        ("emp-chris",  "Chris Morgan",  "EMP-007", "pos-devops","DevOps Engineer",             "dept-eng-platform", "Platform Team",     ["slack","telegram"],  "agent-devops-chris","active"),
        ("emp-lisa",   "Lisa Chen",     "EMP-008", "pos-devops","DevOps Engineer",             "dept-eng-platform", "Platform Team",     ["slack"],             "agent-devops-lisa", "active"),
        ("emp-tony",   "Tony Reed",     "EMP-009", "pos-qa",    "QA Engineer",                 "dept-eng-qa",       "QA Team",           ["slack"],             "agent-qa-tony",    "active"),
        # Sales
        ("emp-mike",   "Mike Johnson",  "EMP-011", "pos-ae",    "Account Executive",           "dept-sales-ent",    "Enterprise Sales",  ["whatsapp","slack"],  "agent-ae-mike",    "active"),
        ("emp-sarah",  "Sarah Kim",     "EMP-012", "pos-ae",    "Account Executive",           "dept-sales-ent",    "Enterprise Sales",  ["whatsapp"],          "agent-ae-sarah",   "active"),
        ("emp-tom",    "Tom Wilson",    "EMP-013", "pos-ae",    "Account Executive",           "dept-sales-smb",    "SMB Sales",         ["slack"],             "agent-ae-tom",     "active"),
        # Product
        ("emp-alex",   "Alex Rivera",   "EMP-015", "pos-pm",    "Product Manager",             "dept-product",      "Product",           ["slack"],             "agent-pm-alex",    "active"),
        ("emp-priya",  "Priya Patel",   "EMP-014", "pos-pm",    "Product Manager",             "dept-product",      "Product",           ["slack","discord"],   "agent-pm-priya",   "active"),
        # Finance
        ("emp-carol",  "Carol Zhang",   "EMP-016", "pos-fa",    "Finance Analyst",             "dept-finance",      "Finance",           ["slack","telegram"],  "agent-fa-carol",   "active"),
        ("emp-david",  "David Park",    "EMP-017", "pos-fa",    "Finance Analyst",             "dept-finance",      "Finance",           ["slack"],             "agent-fa-david",   "active"),
        # HR, CS, Legal
        ("emp-jenny",  "Jenny Liu",     "EMP-018", "pos-hr",    "HR Specialist",               "dept-hr",           "HR & Admin",        ["slack"],             "agent-hr-jenny",   "active"),
        ("emp-emma",   "Emma Chen",     "EMP-019", "pos-csm",   "Customer Success Manager",    "dept-cs",           "Customer Success",  ["slack","whatsapp"],  "agent-csm-emma",   "active"),
        ("emp-rachel", "Rachel Li",     "EMP-021", "pos-legal", "Legal Counsel",               "dept-legal",        "Legal & Compliance",["slack"],             "agent-legal-rachel","active"),
        # Executive
        ("emp-peter",  "Peter Wu",      "EMP-031", "pos-exec",  "Executive",                   "dept-eng",          "Engineering",       ["discord"],           "agent-exec-peter", "active"),
    ]
    for eid, name, eno, pid, pname, did, dname, chs, aid, ast in employees:
        # 按 {id去prefix}@acme.com 规则自动生成 demo 邮箱,用于 SSO 登录 email 匹配
        email = f"{eid.removeprefix('emp-')}@acme.com"
        item = {"PK": ORG, "SK": f"EMP#{eid}", "GSI1PK": "TYPE#emp", "GSI1SK": f"EMP#{eid}",
            "id": eid, "name": name, "employeeNo": eno, "email": email,
            "positionId": pid, "positionName": pname,
            "departmentId": did, "departmentName": dname, "channels": chs, "agentStatus": ast, "createdAt": "2026-01-20T00:00:00Z"}
        if aid:
            item["agentId"] = aid
        items.append(item)

    # --- Agents ---
    agents = [
        # SA agents
        ("agent-sa-jiade",  "SA Agent - JiaDe",   "emp-jiade",  "JiaDe Wang",    "pos-sa",    "Solutions Architect",         "active", None, ["jina-reader","deep-research","arch-diagram-gen","cost-calculator"], ["discord","slack"]),
        ("agent-sa-marcus", "SA Agent - Marcus",  "emp-marcus", "Marcus Bell",   "pos-sa",    "Solutions Architect",         "active", 4.6, ["jina-reader","deep-research","arch-diagram-gen","cost-calculator"], ["slack","telegram"]),
        ("agent-sa-daniel", "SA Agent - Daniel",  "emp-daniel", "Daniel Kim",    "pos-sa",    "Solutions Architect",         "active", 4.4, ["jina-reader","deep-research","arch-diagram-gen"], ["slack"]),
        # SDE agents
        ("agent-sde-ryan",   "SDE Agent - Ryan",   "emp-ryan",   "Ryan Park",     "pos-sde",   "Software Engineer",           "active", 4.5, ["jina-reader","deep-research","github-pr","code-review"], ["slack","discord"]),
        ("agent-sde-sophie", "SDE Agent - Sophie", "emp-sophie", "Sophie Turner", "pos-sde",   "Software Engineer",           "active", 4.2, ["jina-reader","deep-research","github-pr"], ["slack"]),
        # DevOps agents
        ("agent-devops-chris","DevOps Agent - Chris","emp-chris","Chris Morgan",  "pos-devops","DevOps Engineer",             "active", 4.7, ["jina-reader","deep-research","github-pr"], ["slack","telegram"]),
        ("agent-devops-lisa", "DevOps Agent - Lisa","emp-lisa",  "Lisa Chen",     "pos-devops","DevOps Engineer",             "active", 4.1, ["jina-reader","deep-research","github-pr"], ["slack"]),
        # QA
        ("agent-qa-tony",    "QA Agent - Tony",    "emp-tony",   "Tony Reed",     "pos-qa",    "QA Engineer",                 "active", 4.3, ["jina-reader","deep-research","jira-query"], ["slack"]),
        # Sales
        ("agent-ae-mike",    "Sales Agent - Mike", "emp-mike",   "Mike Johnson",  "pos-ae",    "Account Executive",           "active", 3.9, ["jina-reader","web-search","crm-query"], ["whatsapp","slack"]),
        ("agent-ae-sarah",   "Sales Agent - Sarah","emp-sarah",  "Sarah Kim",     "pos-ae",    "Account Executive",           "active", 4.4, ["jina-reader","web-search","crm-query"], ["whatsapp"]),
        # Product
        ("agent-pm-alex",    "PM Agent - Alex",    "emp-alex",   "Alex Rivera",   "pos-pm",    "Product Manager",             "active", 4.2, ["jina-reader","deep-research","jira-query"], ["slack"]),
        ("agent-pm-priya",   "PM Agent - Priya",   "emp-priya",  "Priya Patel",   "pos-pm",    "Product Manager",             "active", 4.5, ["jina-reader","deep-research","jira-query","transcript"], ["slack","discord"]),
        # Finance
        ("agent-fa-carol",   "Finance Agent - Carol","emp-carol","Carol Zhang",   "pos-fa",    "Finance Analyst",             "active", 4.5, ["jina-reader","sap-connector","excel-gen"], ["slack","telegram"]),
        ("agent-fa-david",   "Finance Agent - David","emp-david","David Park",    "pos-fa",    "Finance Analyst",             "active", 4.2, ["jina-reader","sap-connector"], ["slack"]),
        # HR, CS, Legal
        ("agent-hr-jenny",   "HR Agent - Jenny",   "emp-jenny",  "Jenny Liu",     "pos-hr",    "HR Specialist",               "active", 4.1, ["jina-reader","web-search"], ["slack"]),
        ("agent-csm-emma",   "CSM Agent - Emma",   "emp-emma",   "Emma Chen",     "pos-csm",   "Customer Success Manager",    "active", 4.6, ["jina-reader","web-search","crm-query","slack-bridge"], ["slack","whatsapp"]),
        ("agent-legal-rachel","Legal Agent - Rachel","emp-rachel","Rachel Li",    "pos-legal", "Legal Counsel",               "active", 4.8, ["jina-reader","deep-research"], ["slack"]),
        # Executive
        ("agent-exec-peter", "Executive Agent - Peter","emp-peter","Peter Wu",   "pos-exec",  "Executive",                   "active", None, ["jina-reader","deep-research","web_search"], ["discord"]),
        # Previously idle employees — now auto-provisioned
        ("agent-sde-nathan", "SDE Agent - Nathan", "emp-nathan", "Nathan Brooks", "pos-sde",   "Software Engineer",           "active", None, ["jina-reader","deep-research","github-pr"], ["slack"]),
        ("agent-ae-tom",     "Sales Agent - Tom",  "emp-tom",    "Tom Wilson",    "pos-ae",    "Account Executive",           "active", None, ["jina-reader","web-search","crm-query"], ["slack"]),
    ]
    for aid, name, eid, ename, pid, pname, status, qs, skills, chs in agents:
        item = {"PK": ORG, "SK": f"AGENT#{aid}", "GSI1PK": "TYPE#agent", "GSI1SK": f"AGENT#{aid}",
            "id": aid, "name": name, "employeeName": ename, "positionId": pid, "positionName": pname,
            "status": status, "qualityScore": str(qs), "skills": skills, "channels": chs,
            "soulVersions": {"global": 3, "position": 1, "personal": 1 if eid else 0},
            "createdAt": "2026-01-25T00:00:00Z", "updatedAt": "2026-03-20T00:00:00Z"}
        if eid:
            item["employeeId"] = eid
        items.append(item)

    # --- Bindings ---
    # Every employee automatically gets a 1:1 Serverless agent.
    # Admin can upgrade to Always-on (Fargate) for scheduled tasks and instant response.
    agent_name_map = {aid: aname for aid, aname, *_ in agents}
    bindings = []
    for eid, ename, _eno, _pid, _pname, _did, _dname, chs, aid, _ast in employees:
        if not aid:
            continue
        primary_ch = chs[0] if chs else "serverless"
        bid = f"bind-{eid.replace('emp-', '')}-auto"
        aname = agent_name_map.get(aid, aid)
        bindings.append((bid, eid, ename, aid, aname, "1:1", primary_ch, "bound"))
    for bid, eid, ename, aid, aname, mode, ch, st in bindings:
        items.append({"PK": ORG, "SK": f"BIND#{bid}", "GSI1PK": f"AGENT#{aid}", "GSI1SK": f"BIND#{bid}",
            "id": bid, "employeeId": eid, "employeeName": ename, "agentId": aid, "agentName": aname,
            "mode": mode, "channel": ch, "status": st, "createdAt": "2026-02-01T00:00:00Z"})

    # --- Write all items ---
    print(f"Writing {len(items)} items to {table_name}...")
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"Done! {len(items)} items seeded.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=os.environ.get("DYNAMODB_TABLE", "openclaw"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()
    seed(args.table, args.region)
