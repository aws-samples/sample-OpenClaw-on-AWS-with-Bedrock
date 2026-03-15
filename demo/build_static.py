#!/usr/bin/env python3
"""Build a fully static index.html from console_ui.html + console.py mock data + architecture image."""
import base64, json, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)

# 1. Load HTML
html_path = os.path.join(ROOT, "console_ui.html")
html = open(html_path, encoding="utf-8").read()

# 2. Load architecture image as base64
img_path = os.path.join(REPO, "images", "architecture-multitenant.drawio.png")
if os.path.exists(img_path):
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    # Replace the /arch.png src with inline base64
    html = html.replace('src="/arch.png"', f'src="data:image/png;base64,{img_b64}"')
    print(f"  Embedded architecture image ({len(img_b64)//1024}KB base64)")
else:
    print(f"  WARNING: {img_path} not found, skipping image embed")

# 3. Import mock data from console.py
sys.path.insert(0, ROOT)
from console import TENANTS, AUDIT, APPROVALS, SKILLS, TASKS, TOPOLOGY, ALL_TOOLS, ALWAYS_BLOCKED, init, sim_resp
init()  # populate AUDIT and APPROVALS

# Build all API responses
api_data = {
    "/api/dashboard": {
        "tenants": len(TENANTS),
        "active": sum(1 for t in TENANTS.values() if t["status"] == "active"),
        "reqs": sum(t["reqs"] for t in TENANTS.values()),
        "pending": len([x for x in APPROVALS if x["status"] == "pending"]),
        "violations": sum(1 for e in AUDIT if e["ev"] == "permission_denied"),
        "tokens": sum(t["tokens_today"] for t in TENANTS.values()),
        "cost_today": round(sum(t["tokens_today"] for t in TENANTS.values()) / 1000000 * 2.5, 2),
    },
    "/api/tenants": {"tenants": [{"id": k, **v} for k, v in TENANTS.items()]},
    "/api/approvals": {"items": APPROVALS},
    "/api/audit": {"events": list(reversed(AUDIT[-50:]))},
    "/api/skills": {"skills": SKILLS},
    "/api/tasks": {"tasks": TASKS},
    "/api/topology": TOPOLOGY,
    "/api/usage": {
        "days": [{"date": f"03/{i+1:02d}", "tokens": 40000 + i * 3000, "cost": round(0.10 + i * 0.007, 2), "reqs": 80 + i * 8} for i in range(14)],
        "by_tenant": [{"id": k, "name": v["name"], "tokens": v["tokens_today"], "cost": round(v["tokens_today"] / 1000000 * 2.5, 2)} for k, v in TENANTS.items()],
    },
}
# Per-tenant endpoints
for k, v in TENANTS.items():
    api_data[f"/api/tenants/{k}"] = {"id": k, **v}

# 4. Replace the fetch-based API helper with inline data
api_js = f"""
const _API_DATA = {json.dumps(api_data, default=str)};
const _TENANTS = {json.dumps(TENANTS, default=str)};
const _ALL_TOOLS = {json.dumps(ALL_TOOLS)};
const _ALWAYS_BLOCKED = {json.dumps(ALWAYS_BLOCKED)};

const A = async (p, m='GET', b=null) => {{
  // Static mode: return inline data for GET, simulate for POST/PUT
  if (m === 'GET' && _API_DATA[p]) return _API_DATA[p];
  if (m === 'GET' && p.startsWith('/api/tenants/')) {{
    const id = p.split('/api/tenants/')[1];
    if (_API_DATA['/api/tenants/' + id]) return _API_DATA['/api/tenants/' + id];
  }}
  if (m === 'PUT' && p.startsWith('/api/tenants/')) {{
    const id = p.split('/api/tenants/')[1];
    if (b) {{
      const d = typeof b === 'string' ? JSON.parse(b) : b;
      if (d.tools && _TENANTS[id]) {{
        _TENANTS[id].tools = d.tools.filter(t => !_ALWAYS_BLOCKED.includes(t));
        const updated = {{id, ..._TENANTS[id]}};
        _API_DATA['/api/tenants/' + id] = updated;
        return updated;
      }}
    }}
    return _API_DATA['/api/tenants/' + id] || {{}};
  }}
  if (p === '/api/demo/send' && m === 'POST') {{
    const d = typeof b === 'string' ? JSON.parse(b) : b;
    const tid = d.tenant_id, msg = d.message || '';
    const t = _TENANTS[tid];
    if (!t) return {{response: 'Unknown tenant', violations: []}};
    const tools = t.tools;
    const blocked = [..._ALL_TOOLS, ..._ALWAYS_BLOCKED].filter(x => !tools.includes(x));
    const sp = 'Allowed: ' + tools.join(', ') + '. Blocked: ' + blocked.join(', ') + '.';
    let resp = 'Based on my web search: The answer involves several key points.';
    const ml = msg.toLowerCase();
    if (['shell','run','execute','ls','terminal'].some(w => ml.includes(w)))
      resp = tools.includes('shell') ? 'Running command... [shell] ls -la\\ntotal 24\\ndrwxr-xr-x 3 ubuntu ubuntu 4096 .\\n-rw-r--r-- 1 ubuntu ubuntu 256 README.md' : "I don't have permission to execute shell commands. Contact your administrator.";
    else if (['install','skill','plugin'].some(w => ml.includes(w)))
      resp = 'I cannot install skills. Permanently blocked for security. [install_skill] denied.';
    else if (['file','read','write','save'].some(w => ml.includes(w)))
      resp = tools.includes('file') ? 'Reading file... [file] /home/ubuntu/projects/README.md\\n# My Project' : 'No file access. Contact administrator.';
    else if (['code','python','script'].some(w => ml.includes(w)))
      resp = tools.includes('code_execution') ? "Executing... [code_execution]\\n>>> print('Hello')\\nHello" : 'No code execution permissions.';
    const pat = /\\b(shell|browser|file_write|code_execution|install_skill|load_extension|eval)\\b/gi;
    const matches = [...new Set((resp.match(pat) || []).map(x => x.toLowerCase()))];
    const viol = matches.filter(x => !tools.includes(x));
    return {{tid, response: resp, sp, violations: viol, plan_a: 'allowed='+tools.length+',blocked='+blocked.length, plan_e: viol.length ? 'VIOLATION' : 'PASS'}};
  }}
  if (p.startsWith('/api/approvals/') && m === 'POST') {{
    const ps = p.split('/');
    const rid = ps[3], act = ps[4];
    const items = _API_DATA['/api/approvals'].items;
    const a = items.find(x => x.id === rid);
    if (a) {{
      a.status = act === 'approve' ? 'approved' : 'rejected';
      if (act === 'approve' && a.rtype === 'tool' && _TENANTS[a.tid]) {{
        if (!_TENANTS[a.tid].tools.includes(a.res) && !_ALWAYS_BLOCKED.includes(a.res))
          _TENANTS[a.tid].tools.push(a.res);
        _API_DATA['/api/tenants/' + a.tid] = {{id: a.tid, ..._TENANTS[a.tid]}};
      }}
      _API_DATA['/api/dashboard'].pending = items.filter(x => x.status === 'pending').length;
    }}
    return a || {{}};
  }}
  return {{}};
}};
"""

# Replace the original A= fetch helper — find the complete function
# The original is: const A=async(p,m='GET',b=null)=>{...};
# It's a single line ending with .json()};
orig_marker = "const A=async(p,m='GET',b=null)=>"
idx = html.find(orig_marker)
if idx >= 0:
    # Find the end of this statement — it ends with .json()};
    end_marker = ".json()};"
    end_idx = html.find(end_marker, idx)
    if end_idx >= 0:
        end_idx += len(end_marker)
        html = html[:idx] + api_js + html[end_idx:]
        print("  Replaced fetch API with inline data")
    else:
        print("  WARNING: Could not find end of fetch API function")
else:
    print("  WARNING: Could not find fetch API pattern to replace")

# 5. Write output
out_dir = os.path.join(ROOT, "static")
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "index.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

size_kb = os.path.getsize(out_path) // 1024
print(f"  Built: {out_path} ({size_kb}KB)")
print(f"  Ready to deploy to S3+CloudFront")
