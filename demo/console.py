#!/usr/bin/env python3
"""OpenClaw Multi-Tenant Admin Console. Run: python3 demo/console.py → http://localhost:8099"""
import argparse,json,os,re,sys,random
from datetime import datetime,timezone,timedelta
from http.server import BaseHTTPRequestHandler,HTTPServer
from urllib.parse import urlparse

ALL_TOOLS=["web_search","shell","browser","file","file_write","code_execution"]
ALWAYS_BLOCKED=["install_skill","load_extension","eval"]
TENANTS={
  "wa__intern_sarah":{"name":"Sarah Chen","role":"Intern","dept":"Engineering","ch":"WhatsApp","profile":"basic","tools":["web_search"],"status":"active","last":"2026-03-05T10:30:00Z","reqs":12,"tokens_today":1840},
  "tg__engineer_alex":{"name":"Alex Wang","role":"Senior Engineer","dept":"Engineering","ch":"Telegram","profile":"advanced","tools":["web_search","shell","browser","file","file_write","code_execution"],"status":"active","last":"2026-03-05T11:15:00Z","reqs":45,"tokens_today":28500},
  "dc__admin_jordan":{"name":"Jordan Lee","role":"IT Admin","dept":"IT","ch":"Discord","profile":"advanced","tools":["web_search","shell","browser","file","file_write","code_execution"],"status":"active","last":"2026-03-05T11:40:00Z","reqs":28,"tokens_today":15200},
  "sl__finance_carol":{"name":"Carol Zhang","role":"Finance Analyst","dept":"Finance","ch":"Slack","profile":"finance","tools":["web_search","file"],"status":"active","last":"2026-03-05T09:20:00Z","reqs":8,"tokens_today":4100},
  "wa__sales_mike":{"name":"Mike Johnson","role":"Sales Manager","dept":"Sales","ch":"WhatsApp","profile":"basic","tools":["web_search"],"status":"inactive","last":"2026-03-04T16:00:00Z","reqs":0,"tokens_today":0},
}
AUDIT,APPROVALS,MSGS=[],[],[]
SKILLS=[
  {"id":"s3-files","name":"S3 Files","desc":"Upload and share files via S3 pre-signed URLs","author":"AWS Samples","status":"installed","icon":"📤","tools_required":["file"],"tenants_authorized":3},
  {"id":"jira-integration","name":"Jira Integration","desc":"Create tickets, query sprints, update issues","author":"Community","status":"available","icon":"🎫","tools_required":["web_search"],"tenants_authorized":0},
  {"id":"sap-connector","name":"SAP Connector","desc":"Query financial data from SAP ERP","author":"Enterprise","status":"available","icon":"💼","tools_required":["web_search","file"],"tenants_authorized":0},
  {"id":"slack-bridge","name":"Slack Bridge","desc":"Cross-channel messaging and notifications","author":"Community","status":"installed","icon":"💬","tools_required":["web_search"],"tenants_authorized":5},
  {"id":"code-review","name":"Code Review","desc":"Automated PR review with security scanning","author":"Community","status":"available","icon":"🔍","tools_required":["shell","code_execution"],"tenants_authorized":0},
]
TASKS=[
  {"id":"t1","name":"Weekly Engineering Summary","schedule":"Mon 8:00 AM","tenant":"tg__engineer_alex","status":"active","last_run":"2026-03-03T08:00:00Z","next_run":"2026-03-10T08:00:00Z"},
  {"id":"t2","name":"Daily Finance Report","schedule":"Every day 9:00 AM","tenant":"sl__finance_carol","status":"active","last_run":"2026-03-05T09:00:00Z","next_run":"2026-03-06T09:00:00Z"},
  {"id":"t3","name":"Quarterly Compliance Scan","schedule":"1st of quarter","tenant":"dc__admin_jordan","status":"scheduled","last_run":"2026-01-01T00:00:00Z","next_run":"2026-04-01T00:00:00Z"},
]
TOPOLOGY={"nodes":[
  {"id":"org","label":"Organization Agent","type":"org","x":400,"y":50},
  {"id":"eng","label":"Engineering Dept","type":"dept","x":200,"y":180},
  {"id":"fin","label":"Finance Dept","type":"dept","x":400,"y":180},
  {"id":"sales","label":"Sales Dept","type":"dept","x":600,"y":180},
  {"id":"wa__intern_sarah","label":"Sarah Chen","type":"person","x":100,"y":320},
  {"id":"tg__engineer_alex","label":"Alex Wang","type":"person","x":250,"y":320},
  {"id":"sl__finance_carol","label":"Carol Zhang","type":"person","x":400,"y":320},
  {"id":"dc__admin_jordan","label":"Jordan Lee","type":"person","x":550,"y":320},
  {"id":"wa__sales_mike","label":"Mike Johnson","type":"person","x":650,"y":320},
],"edges":[
  {"from":"org","to":"eng"},{"from":"org","to":"fin"},{"from":"org","to":"sales"},
  {"from":"eng","to":"wa__intern_sarah"},{"from":"eng","to":"tg__engineer_alex"},
  {"from":"fin","to":"sl__finance_carol"},{"from":"fin","to":"dc__admin_jordan"},
  {"from":"sales","to":"wa__sales_mike"},
]}

def init():
    now=datetime.now(timezone.utc);AUDIT.clear();APPROVALS.clear()
    for m in [45,40,35,30,25,20,15,10,5]:
        t=random.choice(list(TENANTS.keys()))
        AUDIT.append({"ts":(now-timedelta(minutes=m)).isoformat(),"tid":t,
          "ev":random.choice(["agent_invocation","agent_invocation","agent_invocation","permission_denied"]),
          "tool":random.choice(ALL_TOOLS),"status":"success","ms":random.randint(80,5000)})
    APPROVALS.extend([
      {"id":"req-001","tid":"wa__intern_sarah","name":"Sarah Chen","res":"shell","rtype":"tool","risk":"高","reason":"Need to check server logs for production issue P-1234","at":(now-timedelta(minutes=8)).isoformat(),"exp":(now+timedelta(minutes=22)).isoformat(),"status":"pending"},
      {"id":"req-002","tid":"sl__finance_carol","name":"Carol Zhang","res":"/data/reports/q1-2026/*","rtype":"data_path","risk":"中","reason":"Preparing quarterly financial report for board meeting","at":(now-timedelta(minutes=3)).isoformat(),"exp":(now+timedelta(minutes=27)).isoformat(),"status":"pending"},
    ])
init()

def sim_resp(msg,tools):
    m=msg.lower()
    if any(w in m for w in ["shell","run","execute","ls","terminal"]):
        return("Running command... [shell] ls -la\ntotal 24\ndrwxr-xr-x 3 ubuntu ubuntu 4096 .\n-rw-r--r-- 1 ubuntu ubuntu 256 README.md"if"shell"in tools else"I don't have permission to execute shell commands. Contact your administrator.")
    if any(w in m for w in ["install","skill","plugin"]):
        return"I cannot install skills. Permanently blocked for security. [install_skill] denied."
    if any(w in m for w in ["file","read","write","save"]):
        return("Reading file... [file] /home/ubuntu/projects/README.md\n# My Project"if"file"in tools else"No file access. Contact administrator.")
    if any(w in m for w in ["code","python","script"]):
        return("Executing... [code_execution]\n>>> print('Hello')\nHello"if"code_execution"in tools else"No code execution permissions.")
    return"Based on my web search: The answer involves several key points I'd be happy to explain in detail."

def handle(path,method,body=None):
    if path=="/api/dashboard":
        a=sum(1 for t in TENANTS.values()if t["status"]=="active")
        return 200,{"tenants":len(TENANTS),"active":a,"reqs":sum(t["reqs"]for t in TENANTS.values()),"pending":len([x for x in APPROVALS if x["status"]=="pending"]),"violations":sum(1 for e in AUDIT if e["ev"]=="permission_denied"),"tokens":sum(t["tokens_today"]for t in TENANTS.values()),"cost_today":round(sum(t["tokens_today"]for t in TENANTS.values())/1000000*2.5,2)}
    if path=="/api/tenants":return 200,{"tenants":[{"id":k,**v}for k,v in TENANTS.items()]}
    if path.startswith("/api/tenants/")and method=="GET":
        k=path.split("/api/tenants/")[1];return(200,{"id":k,**TENANTS[k]})if k in TENANTS else(404,{})
    if path.startswith("/api/tenants/")and method=="PUT":
        k=path.split("/api/tenants/")[1]
        if k not in TENANTS:return 404,{}
        if body:
            d=json.loads(body)
            if"tools"in d:TENANTS[k]["tools"]=[t for t in d["tools"]if t not in ALWAYS_BLOCKED]
        return 200,{"id":k,**TENANTS[k]}
    if path=="/api/approvals":return 200,{"items":APPROVALS}
    if path.startswith("/api/approvals/")and method=="POST":
        ps=path.split("/");rid=ps[3];act=ps[4]
        for a in APPROVALS:
            if a["id"]==rid:
                a["status"]="approved"if act=="approve"else"rejected"
                if act=="approve"and a["rtype"]=="tool":
                    t=a["tid"]
                    if t in TENANTS and a["res"]not in TENANTS[t]["tools"]and a["res"]not in ALWAYS_BLOCKED:TENANTS[t]["tools"].append(a["res"])
                AUDIT.append({"ts":datetime.now(timezone.utc).isoformat(),"tid":a["tid"],"ev":"approval_decision","tool":a["res"],"status":act+"d","ms":0})
                return 200,a
        return 404,{}
    if path=="/api/audit":return 200,{"events":list(reversed(AUDIT[-50:]))}
    if path=="/api/skills":return 200,{"skills":SKILLS}
    if path=="/api/tasks":return 200,{"tasks":TASKS}
    if path=="/api/topology":return 200,TOPOLOGY
    if path=="/api/usage":
        days=[];now=datetime.now(timezone.utc)
        for i in range(14):
            d=now-timedelta(days=13-i)
            days.append({"date":d.strftime("%m/%d"),"tokens":random.randint(30000,80000),"cost":round(random.uniform(0.08,0.20),2),"reqs":random.randint(50,200)})
        return 200,{"days":days,"by_tenant":[{"id":k,"name":v["name"],"tokens":v["tokens_today"],"cost":round(v["tokens_today"]/1000000*2.5,2)}for k,v in TENANTS.items()]}
    if path=="/api/demo/send"and method=="POST":
        if not body:return 400,{}
        d=json.loads(body);tid=d.get("tenant_id","");msg=d.get("message","")
        if tid not in TENANTS:return 404,{}
        t=TENANTS[tid];tools=t["tools"];blocked=[x for x in ALL_TOOLS+ALWAYS_BLOCKED if x not in tools]
        sp=f"Allowed: {', '.join(tools)}. Blocked: {', '.join(blocked)}."
        resp=sim_resp(msg,tools)
        pat=re.compile(r'\[('+'|'.join(ALL_TOOLS+ALWAYS_BLOCKED)+r')\]',re.I)
        viol=[x.lower()for x in set(pat.findall(resp))if x.lower()not in tools]
        AUDIT.append({"ts":datetime.now(timezone.utc).isoformat(),"tid":tid,"ev":"permission_denied"if viol else"agent_invocation","tool":viol[0]if viol else",".join([x for x in ALL_TOOLS if x in resp.lower()and x in tools]),"status":"violation"if viol else"success","ms":random.randint(80,300)})
        t["reqs"]+=1;t["tokens_today"]+=random.randint(200,800);t["last"]=datetime.now(timezone.utc).isoformat()
        return 200,{"tid":tid,"response":resp,"sp":sp,"violations":viol,"plan_a":f"allowed={len(tools)},blocked={len(blocked)}","plan_e":"VIOLATION"if viol else"PASS"}
    return 404,{}

class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        p=urlparse(self.path).path
        if p in("/","/index.html"):d=HTML.encode();self.send_response(200);self.send_header("Content-Type","text/html;charset=utf-8");self.send_header("Content-Length",str(len(d)));self.end_headers();self.wfile.write(d)
        elif p=="/arch.png":
            img=os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","images","architecture-multitenant.drawio.png")
            if os.path.exists(img):
                d=open(img,"rb").read();self.send_response(200);self.send_header("Content-Type","image/png");self.send_header("Content-Length",str(len(d)));self.end_headers();self.wfile.write(d)
            else:self.send_response(404);self.end_headers()
        elif p.startswith("/api/"):s,b=handle(p,"GET");d=json.dumps(b,default=str).encode();self.send_response(s);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(d)));self.end_headers();self.wfile.write(d)
        else:self.send_response(404);self.end_headers()
    def do_PUT(self):
        body=self.rfile.read(int(self.headers.get("Content-Length",0)));s,r=handle(urlparse(self.path).path,"PUT",body.decode()if body else None);d=json.dumps(r,default=str).encode();self.send_response(s);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(d)));self.end_headers();self.wfile.write(d)
    def do_POST(self):
        body=self.rfile.read(int(self.headers.get("Content-Length",0)));s,r=handle(urlparse(self.path).path,"POST",body.decode()if body else None);d=json.dumps(r,default=str).encode();self.send_response(s);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(d)));self.end_headers();self.wfile.write(d)

HTML=""
def main():
    global HTML
    hp=os.path.join(os.path.dirname(os.path.abspath(__file__)),"console_ui.html")
    HTML=open(hp).read()if os.path.exists(hp)else"<h1>console_ui.html not found</h1>"
    pa=argparse.ArgumentParser();pa.add_argument("--port",type=int,default=8099);a=pa.parse_args()
    print(f"\n  🦞 OpenClaw Admin Console\n  http://localhost:{a.port}\n  Ctrl+C to stop\n")
    try:HTTPServer(("0.0.0.0",a.port),H).serve_forever()
    except KeyboardInterrupt:print("\n  Stopped.")
if __name__=="__main__":main()
