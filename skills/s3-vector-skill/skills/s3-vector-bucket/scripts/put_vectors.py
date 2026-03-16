#!/usr/bin/env python3
"""
向 Amazon S3 向量索引中插入或更新向量数据

用法:
    # 方式 1：直接传 JSON
    python3 put_vectors.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        --vectors '[{"key":"doc-001","data":{"float32":[0.1,0.2,...]},"metadata":{"title":"标题"}}]'

    # 方式 2：通过文件传入
    python3 put_vectors.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        --vectors-file vectors.json

向量 JSON 格式:
    [
        {
            "key": "doc-001",
            "data": {"float32": [0.1, 0.2, ...]},
            "metadata": {"title": "文档标题", "category": "分类"}
        }
    ]
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, fail, run


def main():
    parser = base_parser("向 Amazon S3 向量索引中插入或更新向量数据")
    parser.add_argument("--index", required=True, help="索引名称")
    parser.add_argument("--vectors", default=None, help="向量数据 JSON 字符串")
    parser.add_argument("--vectors-file", default=None, help="向量数据 JSON 文件路径（与 --vectors 二选一）")
    args = parser.parse_args()

    if not args.vectors and not args.vectors_file:
        fail("请通过 --vectors 或 --vectors-file 提供向量数据")

    if args.vectors_file:
        try:
            with open(args.vectors_file, "r", encoding="utf-8") as f:
                vectors = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            fail(f"读取向量文件失败: {e}")
    else:
        try:
            vectors = json.loads(args.vectors)
        except json.JSONDecodeError as e:
            fail(f"向量 JSON 格式错误: {e}")

    if not isinstance(vectors, list):
        fail("向量数据必须是 JSON 数组")

    client = create_client(args)
    resp = client.put_vectors(
        vectorBucketName=args.bucket,
        indexName=args.index,
        vectors=vectors,
    )
    success_output({
        "action": "put_vectors",
        "bucket": args.bucket,
        "index": args.index,
        "region": args.region,
        "vectors_count": len(vectors),
        "request_id": resp.get("ResponseMetadata", {}).get("RequestId", "N/A"),
    })


if __name__ == "__main__":
    run(main)
