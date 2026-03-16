# Amazon S3 Vector Bucket Management Skill

> [中文](README.md) | **English**

> Full lifecycle management OpenClaw Skill for Amazon S3 Vectors, covering vector buckets, indexes, and vector data with **16 core capabilities**.
>
> Based on Amazon S3 Vectors (re:Invent 2025 GA), reducing costs by **90%** compared to traditional vector databases.
>
> With OpenClaw Skill Router integration, Skill-related Token consumption per turn drops by **~90%**, reducing overall LLM costs by **~36%** (measured in Tokyo Region).

---

## ✨ Feature Overview

| Category | Capabilities |
|----------|-------------|
| **Vector Bucket Management** | Create, delete, query, list vector buckets |
| **Bucket Policy Management** | Set, get, delete bucket policies |
| **Index Management** | Create, query, list, delete vector indexes |
| **Vector Data Operations** | Put/update, get, list, delete vectors |
| **Similarity Search** | Top-K semantic search with metadata filtering |
| **Skill Router (Cost Optimizer)** | Offline indexing + online routing + Hook, Token savings **~91%** |

> 📖 Full CLI reference → [references/cli-reference.md](references/cli-reference.md)

---

## 🚀 Quick Start

### Prerequisites

| Dependency | Requirement | Notes |
|-----------|-------------|-------|
| **OpenClaw** | >= 2026.3.11 | Required when used as an OpenClaw Skill |
| **Node.js** | >= 18.0.0 | OpenClaw runtime dependency |
| **Python** | >= 3.10 | Script runtime |
| **boto3** | Latest | AWS Python SDK |
| **AWS Account** | Bedrock + S3 | S3 Vectors and Bedrock access must be enabled |
| **AWS Credentials** | IAM Role / AWS CLI | Use IAM Role on EC2/EKS, `aws configure` locally |

> ⚠️ **Bedrock Model Access**: The Skill Router depends on `amazon.titan-embed-text-v2:0`. You must manually enable it in AWS Console → Bedrock → Model access.

```bash
pip3 install boto3 --upgrade
```

### Credentials

Three authentication methods supported (in order of priority):

**Method 1: Instance IAM Role (Recommended, auto-applies on EC2/EKS)**

Attach an IAM Role with `s3vectors:*` permissions — no additional configuration needed.

**Method 2: Environment Variables**

```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="ap-northeast-1"
```

**Method 3: AWS Profile**

```bash
aws configure --profile my-profile
# Use --profile my-profile in scripts
```

### One-Click Deployment (Recommended)

```bash
git clone https://github.com/RadiumGu/s3-vector-skill.git
cd s3-vector-skill

./install.sh --bucket my-skill-router --yes
```

`install.sh` automates 5 steps: Prerequisites check → Skill index build (with `--sync`) → Hook installation → Environment variable injection (Linux systemd / macOS launchd) → Gateway restart. First deployment takes ~3 minutes.

```bash
# Parameters
./install.sh --bucket <name>           # Vector bucket name (required)
             --prefix skills          # Index prefix (default: skills)
             --region ap-northeast-1  # S3 Vectors Region
             --embed-region us-east-1 # Bedrock Region (if different)
             --yes / -y               # Skip confirmation prompts
             --skip-build             # Skip indexing (if index already exists)
```

### Manual Skill Installation (Without Router)

For vector bucket management only, without Skill routing:

```bash
# Copy to OpenClaw workspace
cp -r s3-vector-skill ~/.openclaw/workspace-<agent>/skills/s3-vector-bucket

# Or Git submodule (recommended for teams)
git submodule add https://github.com/RadiumGu/s3-vector-skill .openclaw/skills/s3-vector-bucket
```

After installation, use natural language in OpenClaw to trigger actions:

| You say | AI executes |
|---------|------------|
| "Create an S3 vector bucket" | Calls `create_vector_bucket.py` |
| "Create a 1024-dim vector index" | Calls `create_index.py` |
| "Insert 5 test vectors" | Calls `put_vectors.py` |
| "Search for vectors similar to this text" | Calls `query_vectors.py` |

**Skill trigger keywords:**
`vector bucket` · `vector index` · `vector search` · `S3 vector` · `S3 vectors` · `skill router` · `skill routing`

---

## 🧭 Skill Router (Token Cost Reduction ~91%)

> Reduces Skill-related Token consumption per turn from ~4,867 to ~430 (saving **~91%**).

### How It Works

OpenClaw injects all Skill descriptions into the LLM context every turn — the more Skills, the higher the cost. The Skill Router injects only the **Top-5 most relevant Skills**, ignoring the rest.

```
[Offline Indexing]
All SKILL.md descriptions
    → Bedrock Titan Embeddings v2 (1024-dim)
    → S3 Vectors index

[Online Routing]
User message (future: message:received hook)
    → Same Embedding model
    → S3 Vectors Cosine similarity search
    → Top-5 Skills → inject into context
```

### 💰 Cost Analysis (Tokyo ap-northeast-1, from AWS Pricing API)

#### Pricing Data

| Service | Billing Item | Price (Tokyo) |
|---------|-------------|--------------|
| Claude Sonnet 4 (global cross-region) | Input tokens | $3.00 / 1M |
| Claude Sonnet 4 (global cross-region) | Output tokens | $15.00 / 1M |
| Titan Text Embeddings V2 | Input tokens | $0.02 / 1M |
| S3 Vectors Query | QueryVectors | $2.70 / 1M requests |
| S3 Vectors Storage | Vector bucket storage | $0.066 / GB-month |

#### Per-Turn Cost Impact

Typical conversation (system prompt + history + output):

| Cost Item | Without Router | With Router (Top-5) | Change |
|-----------|:--------------:|:-------------------:|:------:|
| System prompt (~1,000 tokens) | $0.0030 | $0.0030 | — |
| **Skill injection (3,040 → 305 tokens)** | **$0.0091** | **$0.0009** | **↓ 90%** |
| History + user message (~960 tokens) | $0.0029 | $0.0029 | — |
| Output (~500 tokens) | $0.0075 | $0.0075 | — |
| **Total per turn** | **$0.0225** | **$0.0143** | **↓ 36%** |

> Skill injection is the only item that changes. Overall reduction is 36%, not 91%.

Scaled to **1,000 turns/month**:

| Cost Item | Without × 1,000 | With × 1,000 | Savings |
|-----------|:---------------:|:------------:|:-------:|
| System prompt | $3.00 | $3.00 | — |
| **Skill injection** | **$9.12** | **$0.92** | **$8.20 (↓ 90%)** |
| History + messages | $2.88 | $2.88 | — |
| Output | $7.50 | $7.50 | — |
| S3 routing overhead | — | $0.003 | — |
| **Monthly total** | **$22.50** | **$14.33** | **$8.17 (↓ 36%)** |

Monthly savings at scale:

| Monthly turns | Without Router | With Router | Savings |
|:------------:|:--------------:|:-----------:|:-------:|
| 1,000 | $22.50 | $14.30 | **$8.20** |
| 10,000 | $225 | $143 | **$82** |
| 50,000 | $1,125 | $715 | **$410** |
| 100,000 | $2,250 | $1,430 | **$820** |

Routing infrastructure cost (S3 storage + Embedding) < $0.001/month — negligible.

### Benchmark Results (61 Skills + Real Query Set)

> Environment: OpenClaw general-tech agent, 61 Skills, Bedrock Titan Embeddings v2 (1024-dim), ap-northeast-1

| Metric | Value |
|--------|-------|
| Total Skills | 61 |
| Full injection Tokens / turn | **3,040 tokens** |
| After routing Tokens / turn | 193 ~ 417 tokens |
| **Average savings** | **~91%** |
| Index build time (first run) | ~18s (61 Skills) |
| Query latency | < 1s |

#### Real Query Hit Examples

| Query | Routed tokens | Savings | Top-3 Skills |
|-------|:------------:|:-------:|-------------|
| Check weather | 209 | 93.1% | **weather** ✅, openai-whisper, ordercli |
| EKS Pod troubleshooting | 306 | 89.9% | **aws-eks** ✅, aws-knowledge, session-logs |
| GitHub Issues operations | 262 | 91.4% | **gh-issues** ✅, **github** ✅, brave-web-search |
| CloudWatch log query | 302 | 90.1% | **aws-cloudwatch** ✅, healthcheck, aws-iac |
| Send Slack message | 193 | 93.7% | **slack** ✅, discord, imsg |
| CDK deployment debug | 417 | 86.3% | **aws-iac** ✅, aws-knowledge, healthcheck |

> Token counts are approximate estimates (Chinese ÷1.5, English ÷4). ✅ = Top-1 hit.

### Manual Configuration (Advanced)

> Skip this section if you used `install.sh`.

#### Step 1: Build Index

```bash
# Auto-scan all OpenClaw Skill directories (incremental: only embeds changed Skills)
python3 scripts/build_skill_index.py \
  --bucket my-skill-router \
  --index  skills-v1 \
  --sync

# Multi-agent parallel build (auto-scans all workspace-* directories)
SKILL_ROUTER_BUCKET=my-skill-router ./scripts/build_all.sh

# Force full rebuild (skip incremental comparison)
python3 scripts/build_skill_index.py --bucket my-skill-router --index skills-v1 --sync --force

# Dry run (preview only, no write)
python3 scripts/build_skill_index.py --bucket x --index x --dry-run
```

> 💡 **Incremental builds**: By default, compares description hashes and only re-embeds changed/new Skills.
> Combined with disk cache (`~/.cache/s3-vector-skill/`), routine maintenance Bedrock calls drop by 90%+,
> build time goes from minutes to seconds.

**`build_skill_index.py` parameters:**

| Parameter | Required | Default | Description |
|-----------|:--------:|---------|-------------|
| `--bucket` | ✅ | — | S3 vector bucket name |
| `--index` | ❌ | `skills-v1` | S3 vector index name |
| `--skills-dir` | ❌ | OpenClaw standard paths | Skill directories to scan (multiple allowed) |
| `--region` | ❌ | `ap-northeast-1` | S3 Vectors Region |
| `--embed-region` | ❌ | same as `--region` | Bedrock Embedding Region |
| `--sync` | ❌ | false | Sync mode: auto-delete stale Skill vectors |
| `--force` | ❌ | false | Force full rebuild (skip incremental comparison) |
| `--dry-run` | ❌ | false | Scan only, no write |

#### Step 2: Online Query

```bash
# JSON output (for scripting)
python3 scripts/skill_router.py \
  --bucket my-skill-router \
  --index  skills-v1 \
  --query  "AWS EKS Pod troubleshooting" \
  --top-k  5

# Markdown output (for LLM context injection)
python3 scripts/skill_router.py \
  --bucket my-skill-router \
  --index  skills-v1 \
  --query  "Check today's weather" \
  --output markdown
```

**`skill_router.py` parameters:**

| Parameter | Required | Default | Description |
|-----------|:--------:|---------|-------------|
| `--bucket` | ✅ | — | S3 vector bucket name |
| `--index` | ✅ | — | S3 vector index name |
| `--query` | ✅ | — | User query text |
| `--top-k` | ❌ | `5` | Number of top results |
| `--output` | ❌ | `json` | Output format: `json` / `markdown` / `names` |
| `--score-threshold` | ❌ | `0.3` | Similarity score filter threshold (0~1, skips filter when API returns no score) |
| `--embed-region` | ❌ | same as `--region` | Bedrock Embedding Region |

#### Step 3: Install Hook

The Hook runs at `agent:bootstrap`, selects Top-5 relevant Skills, and writes them to `BOOTSTRAP.md` for LLM injection.

```bash
cp -r hooks/skill-router-hook ~/.openclaw/hooks/

# Single agent
export SKILL_ROUTER_BUCKET=openclaw-skill-router
export SKILL_ROUTER_INDEX=skills-v1

# Multi-agent (recommended): PREFIX auto-appends agent id → skills-general-tech etc.
export SKILL_ROUTER_BUCKET=openclaw-skill-router
export SKILL_ROUTER_INDEX_PREFIX=skills

openclaw hooks enable skill-router-hook
```

**Hook environment variables:**

| Variable | Required | Description |
|----------|:--------:|-------------|
| `SKILL_ROUTER_BUCKET` | ✅ | S3 vector bucket name |
| `SKILL_ROUTER_INDEX_PREFIX` | Multi-agent recommended | Index prefix, auto-appends agent id |
| `SKILL_ROUTER_INDEX` | Single agent | Fixed index name; PREFIX takes priority |
| `SKILL_ROUTER_TOP_K` | ❌ | Top-K count (default 5) |
| `SKILL_ROUTER_REGION` | ❌ | S3 Vectors Region (default ap-northeast-1) |
| `SKILL_ROUTER_SCRIPT` | ❌ | Path to skill_router.py (auto-injected by install.sh; specify manually otherwise) |
| `AWS_BEDROCK_REGION` | ❌ | Bedrock Embedding Region |

### Current Limitations & Roadmap

| Capability | Current | Future (after message:received context injection support) |
|------------|---------|----------------------------------------------------------|
| Trigger timing | `agent:bootstrap` (session start) | Before each message |
| Context source | Recent Memory files | Real-time user message |
| Skill injection | Write to BOOTSTRAP.md | Dynamic replacement of available_skills |
| Token savings | Partial (first turn only) | **Full ~91% every turn** |

> ⚠️ OpenClaw's `message:received` Hook is live (PR [#9387](https://github.com/openclaw/openclaw/pull/9387)),
> but currently runs in non-blocking mode and cannot modify the system prompt.
> Issue [#8807](https://github.com/openclaw/openclaw/issues/8807) proposes blocking context injection support,
> which would enable full per-message dynamic routing.

### 🔧 Index Maintenance

| Scenario | Action |
|----------|--------|
| Install / update / uninstall Skill | Rebuild index (incremental, only processes changed Skills; use `--sync` for uninstall) |
| OpenClaw upgrade | Rebuild index |
| Modify SKILL.md name or description | Rebuild index (incremental, only re-embeds changed Skills) |
| Routine conversation / config changes | No action needed |

```bash
# Single agent sync rebuild
python3 scripts/build_skill_index.py --bucket my-skill-router --index skills-v1 --sync

# Multi-agent full rebuild
SKILL_ROUTER_BUCKET=my-skill-router ./scripts/build_all.sh
```

---

## 🏗️ Project Structure

```
s3-vector-skill/
├── README.md                              # Documentation in Chinese
├── README_EN.md                           # Documentation in English (this file)
├── SKILL.md                               # OpenClaw Skill definition
├── install.sh                             # One-click deployment script
├── scripts/                               # Executable scripts
│   ├── common.py                          # Shared utilities
│   ├── create_vector_bucket.py            # Create vector bucket
│   ├── delete_vector_bucket.py            # Delete vector bucket
│   ├── get_vector_bucket.py               # Get vector bucket info
│   ├── list_vector_buckets.py             # List all vector buckets
│   ├── put_vector_bucket_policy.py        # Set bucket policy
│   ├── get_vector_bucket_policy.py        # Get bucket policy
│   ├── delete_vector_bucket_policy.py     # Delete bucket policy
│   ├── create_index.py                    # Create vector index
│   ├── get_index.py                       # Get index info
│   ├── list_indexes.py                    # List all indexes
│   ├── delete_index.py                    # Delete vector index
│   ├── put_vectors.py                     # Put/update vectors
│   ├── get_vectors.py                     # Get specific vectors
│   ├── list_vectors.py                    # List vectors
│   ├── delete_vectors.py                  # Delete vectors
│   ├── query_vectors.py                   # Similarity search
│   ├── build_skill_index.py               # Skill Router: offline indexing
│   ├── skill_router.py                    # Skill Router: online query
│   ├── build_all.sh                       # Multi-agent parallel build
│   ├── benchmark.py                       # Token savings benchmark
│   ├── extract_queries.py                 # Real query set extraction
│   └── embed.py                           # Bedrock Embedding module
├── hooks/
│   └── skill-router-hook/                 # OpenClaw Hook (Bootstrap injection)
└── references/
    ├── api_reference.md                   # S3 Vectors API reference
    └── cli-reference.md                   # Full CLI command reference
```

---

## ❓ FAQ

**Q: How to verify credentials are valid?**
```bash
aws sts get-caller-identity
```

**Q: How to confirm S3 Vectors is available in the current Region?**
```bash
aws s3vectors list-vector-buckets --region ap-northeast-1
```
An empty list means available; `Could not connect to endpoint` means the Region is not yet supported.

**Q: How to troubleshoot failed API calls?**
1. Check IAM Role / credentials have `s3vectors:*` permissions
2. Verify the Region supports S3 Vectors
3. Ensure the bucket name contains only lowercase letters, numbers, and hyphens
4. Use the `error_code` and `request_id` from the response to search AWS CloudTrail

**Q: Which Regions are supported?**

| Region | Name |
|--------|------|
| `us-east-1` | US East (N. Virginia) |
| `us-west-2` | US West (Oregon) |
| `eu-west-1` | Europe (Ireland) |
| `ap-northeast-1` | Asia Pacific (Tokyo) ✅ Default |
| `ap-southeast-1` | Asia Pacific (Singapore) |

---

## 📚 References

- [Amazon S3 Vectors Product Page](https://aws.amazon.com/s3/features/vectors/)
- [S3 Vectors GA Launch Blog](https://aws.amazon.com/blogs/aws/amazon-s3-vectors-now-generally-available-with-increased-scale-and-performance/)
- [boto3 s3vectors API Docs](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3vectors.html)
- [Amazon Bedrock Titan Embeddings v2](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)
- [re:Invent 2025 STG318 Session](https://www.youtube.com/watch?v=ghUW2SpEYPk)
- [OpenClaw Website](https://openclaw.ai/)
- [ClawHub Skill Marketplace](https://clawhub.com/)

---

## 📄 License

MIT
