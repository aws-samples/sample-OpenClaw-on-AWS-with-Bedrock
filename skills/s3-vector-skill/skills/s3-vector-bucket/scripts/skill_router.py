#!/usr/bin/env python3
"""
在线查询：将用户 Query 转成向量 → 查询 S3 Vectors → 返回 Top-K 最相关 Skill

用法:
    # 基础查询（JSON 输出）
    python3 skill_router.py \
        --bucket <VectorBucketName> \
        --index  <IndexName> \
        --query  "我想搜索 GitHub Issues" \
        --top-k  5

    # Markdown 输出（适合注入到 LLM 上下文）
    python3 skill_router.py \
        --bucket my-skill-router \
        --index  skills-v1 \
        --query  "AWS EKS 集群 Pod 故障排查" \
        --top-k  5 \
        --output markdown

    # 只输出 Skill 名称列表（适合脚本解析）
    python3 skill_router.py \
        --bucket my-skill-router \
        --index  skills-v1 \
        --query  "查天气" \
        --top-k  3 \
        --output names

设计目标:
    替代向 LLM 注入全部 skill 描述的方式，每次只注入 Top-K 最相关 Skill，
    配合 OpenClaw message:received Hook 上下文注入机制（#8807）实现 Token 消耗降低 90%+。
"""

import argparse
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import base_parser, create_client, fail, run
from embed import embed_text


def main():
    parser = base_parser("在线 Skill 路由查询")
    parser.add_argument("--index", required=True, help="S3 向量索引名称")
    parser.add_argument("--query", required=True, help="用户查询文本")
    parser.add_argument(
        "--top-k", type=int, default=5, help="返回最相关 Skill 数量（默认 5）"
    )
    parser.add_argument(
        "--embed-region",
        default=os.getenv("AWS_BEDROCK_REGION"),
        help="Bedrock Embedding Region（默认跟 --region 一致；如该 region 无 Titan v2 可手动指定）",
    )
    parser.add_argument(
        "--output",
        choices=["json", "markdown", "names"],
        default="json",
        help="输出格式: json（默认）| markdown（适合注入 LLM）| names（仅 Skill 名称）",
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.3,
        help="相似度分数阈值（0~1，低于此分数的结果被过滤，默认 0.3）",
    )
    args = parser.parse_args()

    # 1. 生成查询向量
    # embed_region 优先级：--embed-region > AWS_BEDROCK_REGION 环境变量 > --region（S3 Vectors 同区）
    embed_region = args.embed_region or args.region
    try:
        query_vec = embed_text(
            args.query,
            region=embed_region,
            profile=getattr(args, "profile", None),
        )
    except Exception as e:
        fail(f"Query Embedding 失败: {e}")

    # 2. S3 Vectors 相似度搜索
    client = create_client(args)
    try:
        resp = client.query_vectors(
            vectorBucketName=args.bucket,
            indexName=args.index,
            queryVector={"float32": query_vec},
            topK=args.top_k,
            returnMetadata=True,
        )
    except Exception as e:
        fail(f"S3 Vectors 查询失败: {e}")

    results = resp.get("vectors", [])

    def get_score(r):
        """兼容 S3 Vectors API 返回字段：优先取 distance，其次 score，均无则 None"""
        if "distance" in r:
            return r["distance"]
        if "score" in r:
            return r["score"]
        return None

    # 3. 过滤低分结果（仅在 API 返回相似度字段时生效；score=None 表示 API 未返回分数，不过滤）
    if args.score_threshold > 0:
        results = [r for r in results if get_score(r) is None or get_score(r) >= args.score_threshold]

    # 4. 格式化输出
    if args.output == "names":
        names = [r["key"] for r in results]
        print("\n".join(names))

    elif args.output == "markdown":
        lines = [
            f"<!-- Skill Router: Top-{args.top_k} for query: {args.query!r} -->",
            "<available_skills>",
        ]
        for r in results:
            meta = r.get("metadata", {})
            name = r["key"]
            desc = meta.get("description", "")
            score = get_score(r)
            lines.append(f"  <skill>")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            if score is not None:
                lines.append(f"    <score>{score:.4f}</score>")
            lines.append(f"  </skill>")
        lines.append("</available_skills>")
        print("\n".join(lines))

    else:  # json
        def fmt_result(i, r):
            score = get_score(r)
            entry = {
                "rank": i + 1,
                "name": r["key"],
                "description": r.get("metadata", {}).get("description", ""),
            }
            if score is not None:
                entry["score"] = round(score, 6)
            return entry

        output = {
            "success": True,
            "action": "skill_router",
            "query": args.query,
            "top_k": args.top_k,
            "bucket": args.bucket,
            "index": args.index,
            "results_count": len(results),
            "results": [fmt_result(i, r) for i, r in enumerate(results)],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run(main)
