# Amazon S3 Vector Bucket Skill

> [中文](skills/s3-vector-bucket/../../README_CN.md) | **English**

Full lifecycle management OpenClaw Skill for **Amazon S3 Vectors**, covering vector buckets, indexes, and vector data with **16 core capabilities** + a **Skill Router** subsystem that reduces Token consumption by ~91%.

## What It Does

| Category | Capabilities |
|----------|-------------|
| **Vector Bucket Management** | Create, delete, query, list vector buckets |
| **Bucket Policy Management** | Set, get, delete bucket policies |
| **Index Management** | Create, query, list, delete vector indexes |
| **Vector Data Operations** | Put/update, get, list, delete vectors |
| **Similarity Search** | Top-K semantic search with metadata filtering |
| **Skill Router** | Offline indexing + online routing + Hook, Token savings **~91%** |

## Key Features

- **S3 Vectors** (re:Invent 2025 GA) — serverless vector storage, ~90% cheaper than traditional vector DBs
- **Bedrock Titan Embeddings v2** (1024-dim) — for Skill indexing and semantic search
- **Incremental builds** — only re-embeds changed Skills, with disk cache for cross-process reuse
- **Multi-Agent support** — auto-scans all OpenClaw workspace directories, parallel index building
- **One-click deployment** — `install.sh` handles everything: build → Hook install → env vars → Gateway restart

## Quick Start

```bash
cd skills/s3-vector-bucket
./install.sh --bucket my-skill-router --yes
```

Or manual:

```bash
# 1. Build Skill index
python3 scripts/build_skill_index.py --bucket my-skill-router --index skills-v1 --sync

# 2. Query (test)
python3 scripts/skill_router.py --bucket my-skill-router --index skills-v1 --query "EKS troubleshooting" --top-k 5

# 3. Install Hook
cp -r hooks/skill-router-hook ~/.openclaw/hooks/
export SKILL_ROUTER_BUCKET=my-skill-router
export SKILL_ROUTER_INDEX_PREFIX=skills
openclaw hooks enable skill-router-hook
```

## Prerequisites

- Python 3.8+ with `boto3`
- AWS credentials with `s3vectors:*` and `bedrock:InvokeModel` permissions
- S3 Vectors available in your Region (us-east-1, us-west-2, eu-west-1, ap-northeast-1, ap-southeast-1)

## Architecture

```
[Offline Indexing]
SKILL.md descriptions → Bedrock Titan v2 (1024d) → S3 Vectors index

[Online Routing — agent:bootstrap Hook]
Recent memory context → Titan v2 embedding → S3 Vectors cosine search → Top-5 Skills → BOOTSTRAP.md → LLM context
```

## Full Documentation

See the detailed [SKILL.md](skills/s3-vector-bucket/SKILL.md) for complete CLI reference, all 16 script parameters, Hook configuration, and cost analysis.

## Related Links

- [Amazon S3 Vectors](https://aws.amazon.com/s3/features/vectors/)
- [Bedrock Titan Embeddings v2](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)
- [Standalone repo](https://github.com/RadiumGu/s3-vector-skill)

## License

MIT — See [LICENSE](../../LICENSE)
