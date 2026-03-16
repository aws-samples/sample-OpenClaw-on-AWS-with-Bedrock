#!/usr/bin/env python3
"""
设置 Amazon S3 向量桶策略

用法:
    python3 put_vector_bucket_policy.py \
        --bucket <BucketName> \
        --region <Region> \
        --policy '{"Statement": [...]}'
"""

import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, fail, run


def main():
    parser = base_parser("设置 Amazon S3 向量桶策略")
    parser.add_argument("--policy", required=True, help="策略 JSON 字符串")
    args = parser.parse_args()

    # 校验 JSON 格式
    try:
        policy_obj = json.loads(args.policy)
        policy_str = json.dumps(policy_obj)
    except json.JSONDecodeError as e:
        fail(f"策略 JSON 格式错误: {e}")

    client = create_client(args)
    resp = client.put_vector_bucket_policy(
        vectorBucketName=args.bucket,
        policy=policy_str,
    )
    success_output({
        "action": "put_vector_bucket_policy",
        "bucket": args.bucket,
        "region": args.region,
        "request_id": resp.get("ResponseMetadata", {}).get("RequestId", "N/A"),
    })


if __name__ == "__main__":
    run(main)
