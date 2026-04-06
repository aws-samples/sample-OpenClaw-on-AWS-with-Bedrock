# OpenClaw Enterprise on AgentCore

**Full documentation has moved to the project root for better visibility:**

**[→ README_ENTERPRISE.md](../README_ENTERPRISE.md)**

---

Quick start:

```bash
cd enterprise
cp .env.example .env        # edit: STACK_NAME, REGION, ADMIN_PASSWORD
bash deploy.sh              # ~15 min — infra + Docker build + seed
```

Then follow Steps 4–6 in [README_ENTERPRISE.md](../README_ENTERPRISE.md) to deploy the Admin Console and Gateway services.
