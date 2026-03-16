#!/usr/bin/env python3
"""
Amazon S3 向量相似度搜索

用法:
    # 方式 1：直接传查询向量
    python3 query_vectors.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        --query-vector '[0.1, 0.2, ...]' \
        --top-k 5 \
        [--filter '{"category": {"$eq": "AI"}}'] \
        [--return-metadata]

    # 方式 2：通过文件传入查询向量
    python3 query_vectors.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        --query-vector-file query.json \
        --top-k 5
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, fail, run


def main():
    parser = base_parser("Amazon S3 向量相似度搜索")
    parser.add_argument("--index", required=True, help="索引名称")
    parser.add_argument("--query-vector", default=None, help="查询向量 JSON 数组字符串，如 '[0.1, 0.2, ...]'")
    parser.add_argument("--query-vector-file", default=None, help="查询向量 JSON 文件路径（与 --query-vector 二选一）")
    parser.add_argument("--top-k", required=True, type=int, help="返回最相似的 K 个结果")
    parser.add_argument("--filter", default=None, help="过滤条件 JSON 字符串，如 '{\"category\": {\"$eq\": \"AI\"}}'")
    parser.add_argument("--return-metadata", action="store_true", default=False, help="是否返回元数据")
    args = parser.parse_args()

    # 解析查询向量
    if not args.query_vector and not args.query_vector_file:
        fail("请通过 --query-vector 或 --query-vector-file 提供查询向量")

    if args.query_vector_file:
        try:
            with open(args.query_vector_file, "r", encoding="utf-8") as f:
                query_vector = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            fail(f"读取查询向量文件失败: {e}")
    else:
        try:
            query_vector = json.loads(args.query_vector)
        except json.JSONDecodeError as e:
            fail(f"查询向量 JSON 格式错误: {e}")

    if not isinstance(query_vector, list):
        fail("查询向量必须是 JSON 数组")

    client = create_client(args)

    kwargs = {
        "vectorBucketName": args.bucket,
        "indexName": args.index,
        "queryVector": {"float32": query_vector},
        "topK": args.top_k,
    }
    if args.filter:
        try:
            kwargs["filter"] = json.loads(args.filter)
        except json.JSONDecodeError as e:
            fail(f"过滤条件 JSON 格式错误: {e}")
    if args.return_metadata:
        kwargs["returnMetadata"] = True

    resp = client.query_vectors(**kwargs)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "query_vectors",
        "bucket": args.bucket,
        "index": args.index,
        "top_k": args.top_k,
        "query_vector_dimension": len(query_vector),
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
