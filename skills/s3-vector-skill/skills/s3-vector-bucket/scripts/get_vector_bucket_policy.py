#!/usr/bin/env python3
"""
获取 Amazon S3 向量桶策略

用法:
    python3 get_vector_bucket_policy.py \
        --bucket <BucketName> \
        --region <Region>
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("获取 Amazon S3 向量桶策略")
    args = parser.parse_args()
    client = create_client(args)

    resp = client.get_vector_bucket_policy(vectorBucketName=args.bucket)
    success_output({
        "action": "get_vector_bucket_policy",
        "bucket": args.bucket,
        "region": args.region,
        "policy": resp.get("policy", ""),
    })


if __name__ == "__main__":
    run(main)
