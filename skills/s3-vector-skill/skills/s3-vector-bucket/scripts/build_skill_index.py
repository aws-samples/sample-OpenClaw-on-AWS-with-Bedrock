#!/usr/bin/env python3
"""
离线建库：扫描 OpenClaw Skill 目录 → 生成 Embeddings → 写入 S3 Vectors

用法:
    python3 build_skill_index.py \
        --bucket <VectorBucketName> \
        --index  <IndexName> \
        [--skills-dir <dir1> <dir2> ...] \
        [--region ap-northeast-1] \
        [--embed-region us-east-1] \
        [--profile <profile>] \
        [--sync] \
        [--dry-run]

示例（使用默认 OpenClaw 目录）:
    python3 build_skill_index.py \
        --bucket my-skill-router \
        --index  skills-v1

示例（指定额外目录）:
    python3 build_skill_index.py \
        --bucket my-skill-router \
        --index  skills-v1 \
        --skills-dir ~/.openclaw/workspace-general-tech/skills ~/.nvm/versions/node/v22.22.0/lib/node_modules/openclaw/skills

原理:
    1. 递归扫描指定目录下的 SKILL.md 文件
    2. 解析 YAML frontmatter 提取 name + description
    3. 用 Bedrock Titan Embeddings v2 (1024 维) 生成向量
    4. 若向量桶/索引不存在则自动创建（dimension=1024, metric=cosine）
    5. 批量 PutVectors 写入 S3 Vectors（每批最多 500 条）
    6. --sync 模式：对比索引中已有 key 与磁盘 Skill，自动删除索引中已不存在的废弃向量
"""

import argparse
import json
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import base_parser, create_client, success_output, fail, run, find_skills, desc_hash, DEFAULT_SKILL_DIRS
from embed import embed_texts, embed_text, EMBED_DIMENSION

BATCH_SIZE = 100  # S3 Vectors PutVectors 单次上限 500，保守取 100


INDEX_NAME_DEFAULT = "skills-v1"


def ensure_bucket(client, bucket: str, region: str):
    """若向量桶不存在则创建"""
    try:
        client.get_vector_bucket(vectorBucketName=bucket)
        print(f"  ✓ 向量桶 '{bucket}' 已存在")
    except client.exceptions.NotFoundException:
        print(f"  → 创建向量桶 '{bucket}'...")
        client.create_vector_bucket(vectorBucketName=bucket)
        print(f"  ✓ 向量桶 '{bucket}' 创建成功")
    except Exception as e:
        from botocore.exceptions import ClientError
        if isinstance(e, ClientError) and e.response["Error"]["Code"] in ("NotFoundException", "NoSuchBucket"):
            print(f"  → 创建向量桶 '{bucket}'...")
            client.create_vector_bucket(vectorBucketName=bucket)
        else:
            raise


def ensure_index(client, bucket: str, index: str):
    """若索引不存在则创建（1024 维 cosine）"""
    try:
        client.get_index(vectorBucketName=bucket, indexName=index)
        print(f"  ✓ 索引 '{index}' 已存在")
    except client.exceptions.NotFoundException:
        print(f"  → 创建索引 '{index}'（dim={EMBED_DIMENSION}, metric=cosine）...")
        client.create_index(
            vectorBucketName=bucket,
            indexName=index,
            dataType="float32",
            dimension=EMBED_DIMENSION,
            distanceMetric="cosine",
        )
        print(f"  ✓ 索引 '{index}' 创建成功")
    except Exception as e:
        from botocore.exceptions import ClientError
        if isinstance(e, ClientError) and e.response["Error"]["Code"] == "NotFoundException":
            print(f"  → 创建索引 '{index}'（dim={EMBED_DIMENSION}, metric=cosine）...")
            client.create_index(
                vectorBucketName=bucket,
                indexName=index,
                dataType="float32",
                dimension=EMBED_DIMENSION,
                distanceMetric="cosine",
            )
            print(f"  ✓ 索引 '{index}' 创建成功")
        else:
            raise


def list_all_vector_keys(client, bucket: str, index: str) -> set:
    """列出索引中所有向量的 key（分页遍历）"""
    keys = set()
    kwargs = {
        "vectorBucketName": bucket,
        "indexName": index,
        "maxResults": 500,
    }
    while True:
        resp = client.list_vectors(**kwargs)
        for v in resp.get("vectors", []):
            keys.add(v["key"])
        next_token = resp.get("nextToken")
        if not next_token:
            break
        kwargs["nextToken"] = next_token
    return keys


def sync_cleanup(client, bucket: str, index: str, skills: list) -> list:
    """
    对比索引中已有 key 与磁盘扫描到的 Skill names，
    删除索引中存在但磁盘上已不存在的废弃向量。
    返回被删除的 key 列表。
    """
    disk_names = {s["name"] for s in skills}

    print(f"  正在列出索引 '{index}' 中所有向量...")
    try:
        index_keys = list_all_vector_keys(client, bucket, index)
    except Exception as e:
        print(f"  ⚠️ 无法列出索引向量，跳过同步清理: {e}")
        return []

    stale_keys = index_keys - disk_names
    print(f"  索引中共 {len(index_keys)} 个向量，磁盘上 {len(disk_names)} 个 Skill")

    if not stale_keys:
        print(f"  ✓ 无废弃向量，无需清理")
        return []

    print(f"  → 发现 {len(stale_keys)} 个废弃向量，正在删除:")
    for key in sorted(stale_keys):
        print(f"    🗑️  {key}")

    # 分批删除（每批最多 100 个 key）
    stale_list = sorted(stale_keys)
    for start in range(0, len(stale_list), BATCH_SIZE):
        batch = stale_list[start: start + BATCH_SIZE]
        try:
            client.delete_vectors(
                vectorBucketName=bucket,
                indexName=index,
                keys=batch,
            )
        except Exception as e:
            print(f"  ⚠️ 批量删除失败（keys: {batch[:3]}...）: {e}")

    print(f"  ✓ 已清理 {len(stale_keys)} 个废弃向量")
    return stale_list


def main():
    parser = base_parser("离线建库：扫描 Skill 目录并写入 S3 Vectors")
    parser.add_argument(
        "--index",
        default=INDEX_NAME_DEFAULT,
        help=f"S3 向量索引名称（默认 {INDEX_NAME_DEFAULT}）",
    )
    parser.add_argument(
        "--skills-dir",
        nargs="+",
        default=None,
        metavar="DIR",
        help="要扫描的 Skill 目录（可指定多个），默认自动扫描 OpenClaw 标准路径",
    )
    parser.add_argument(
        "--embed-region",
        default=os.getenv("AWS_BEDROCK_REGION"),
        help="Bedrock Embedding Region（默认跟 --region 一致；如该 region 无 Titan v2 可手动指定）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅扫描和生成 Embedding，不写入 S3 Vectors",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="同步模式：写入后自动删除索引中已不存在的废弃 Skill 向量",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制全量重建（忽略增量对比，重新 embed 所有 Skill）",
    )
    args = parser.parse_args()

    skill_dirs = args.skills_dir or DEFAULT_SKILL_DIRS

    # 1. 扫描 Skills
    print(f"\n{'='*60}")
    print("Step 1: 扫描 Skill 目录")
    print(f"{'='*60}")
    for d in skill_dirs:
        print(f"  目录: {os.path.expanduser(d)}")

    skills = find_skills(skill_dirs)
    if not skills:
        fail(f"未找到任何有效的 SKILL.md 文件，请检查目录: {skill_dirs}")

    print(f"\n  发现 {len(skills)} 个 Skill:")
    for s in skills:
        print(f"    - {s['name']}: {s['description'][:60]}...")

    if args.dry_run:
        print(f"\n[Dry Run] 跳过 Embedding 和写入步骤")
        success_output({
            "action": "build_skill_index",
            "dry_run": True,
            "skills_found": len(skills),
            "skills": [{"name": s["name"], "path": s["path"]} for s in skills],
        })
        return

    # 2. 初始化 S3 Vectors 客户端（提前，增量对比需要）
    print(f"\n{'='*60}")
    print("Step 2: 初始化 S3 Vectors 资源")
    print(f"{'='*60}")
    client = create_client(args)
    ensure_bucket(client, args.bucket, args.region)
    ensure_index(client, args.bucket, args.index)

    # 3. 增量对比（#2）：只 embed 变化的 Skill
    print(f"\n{'='*60}")
    embed_region = args.embed_region or args.region
    if args.force:
        print(f"Step 3: 全量 Embedding（--force）（Titan v2, {EMBED_DIMENSION} 维，Region={embed_region}）")
        print(f"{'='*60}")
        changed_skills = skills
        unchanged_skills = []
    else:
        print(f"Step 3: 增量对比 + Embedding（Titan v2, {EMBED_DIMENSION} 维，Region={embed_region}）")
        print(f"{'='*60}")
        # 获取索引中已有向量的 metadata（含 desc_hash）
        existing_hashes = {}
        try:
            existing_keys = list_all_vector_keys(client, args.bucket, args.index)
            if existing_keys:
                # 分批 get_vectors 获取 metadata
                key_list = sorted(existing_keys)
                for start in range(0, len(key_list), BATCH_SIZE):
                    batch = key_list[start: start + BATCH_SIZE]
                    resp = client.get_vectors(
                        vectorBucketName=args.bucket,
                        indexName=args.index,
                        keys=batch,
                        returnMetadata=True,
                    )
                    for v in resp.get("vectors", []):
                        meta = v.get("metadata", {})
                        existing_hashes[v["key"]] = meta.get("desc_hash", "")
        except Exception as e:
            print(f"  ⚠️ 无法获取已有向量 metadata，回退全量构建: {e}")
            existing_hashes = {}

        # 对比 desc_hash
        changed_skills = []
        unchanged_skills = []
        for s in skills:
            current_hash = desc_hash(s["description"])
            if s["name"] in existing_hashes and existing_hashes[s["name"]] == current_hash:
                unchanged_skills.append(s)
            else:
                changed_skills.append(s)

        print(f"  变化/新增: {len(changed_skills)} 个，未变化: {len(unchanged_skills)} 个")

    if not changed_skills:
        print(f"  ✓ 所有 Skill 均未变化，跳过 Embedding")
    else:
        print(f"  正在为 {len(changed_skills)} 个 Skill 生成向量（预计耗时 {len(changed_skills)*0.5:.0f}s）...")
        texts = [f"{s['name']}: {s['description']}" for s in changed_skills]
        try:
            vectors_data = embed_texts(texts, region=embed_region, profile=getattr(args, "profile", None))
        except Exception as e:
            fail(f"Embedding 生成失败: {e}")
        print(f"  ✓ 向量生成完成（维度={len(vectors_data[0])}）")

    # 4. 写入向量（仅变化部分）
    print(f"\n{'='*60}")
    print("Step 4: 写入向量到 S3 Vectors")
    print(f"{'='*60}")

    if not changed_skills:
        print(f"  ✓ 无需写入")
        total_written = 0
    else:
        vectors = [
            {
                "key": changed_skills[i]["name"],
                "data": {"float32": vectors_data[i]},
                "metadata": {
                    "name": changed_skills[i]["name"],
                    "description": changed_skills[i]["description"][:500],
                    "desc_hash": desc_hash(changed_skills[i]["description"]),
                },
            }
            for i in range(len(changed_skills))
        ]

        total_written = 0
        for start in range(0, len(vectors), BATCH_SIZE):
            batch = vectors[start: start + BATCH_SIZE]
            client.put_vectors(
                vectorBucketName=args.bucket,
                indexName=args.index,
                vectors=batch,
            )
            total_written += len(batch)
            print(f"  ✓ 已写入 {total_written}/{len(vectors)} 条向量")

    # 5. 同步模式：清理索引中已不存在的废弃 Skill 向量
    deleted_keys = []
    if args.sync:
        print(f"\n{'='*60}")
        print("Step 5: 同步清理（--sync）")
        print(f"{'='*60}")
        deleted_keys = sync_cleanup(client, args.bucket, args.index, skills)

    print(f"\n{'='*60}")
    print("✅ 建库完成！")
    print(f"{'='*60}")

    result = {
        "action": "build_skill_index",
        "bucket": args.bucket,
        "index": args.index,
        "region": args.region,
        "embed_region": embed_region,
        "skills_total": len(skills),
        "skills_changed": len(changed_skills),
        "skills_unchanged": len(unchanged_skills),
        "vector_dimension": EMBED_DIMENSION,
        "skills": [{"name": s["name"], "description": s["description"][:80]} for s in skills],
    }
    if args.sync:
        result["sync"] = {
            "enabled": True,
            "deleted_count": len(deleted_keys),
            "deleted_keys": deleted_keys,
        }
    success_output(result)


if __name__ == "__main__":
    run(main)
