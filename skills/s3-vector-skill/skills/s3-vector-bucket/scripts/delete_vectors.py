#!/usr/bin/env python3
"""
删除 Amazon S3 向量索引中的指定向量

用法:
    python3 delete_vectors.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        --keys key1,key2,key3
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("删除 Amazon S3 向量索引中的指定向量")
    parser.add_argument("--index", required=True, help="索引名称")
    parser.add_argument("--keys", required=True, help="要删除的向量键列表，逗号分隔")
    args = parser.parse_args()
    client = create_client(args)

    keys = [k.strip() for k in args.keys.split(",")]
    resp = client.delete_vectors(
        vectorBucketName=args.bucket,
        indexName=args.index,
        keys=keys,
    )
    success_output({
        "action": "delete_vectors",
        "bucket": args.bucket,
        "index": args.index,
        "deleted_keys": keys,
        "region": args.region,
        "request_id": resp.get("ResponseMetadata", {}).get("RequestId", "N/A"),
    })


if __name__ == "__main__":
    run(main)
