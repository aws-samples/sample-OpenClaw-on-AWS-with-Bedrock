#!/usr/bin/env python3
"""
创建 Amazon S3 向量桶

用法:
    python3 create_vector_bucket.py \
        --bucket <BucketName> \
        --region <Region> \
        [--sse-type SSE-S3|SSE-KMS] \
        [--kms-key-arn <KmsKeyArn>]
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("创建 Amazon S3 向量桶")
    parser.add_argument(
        "--sse-type",
        choices=["SSE-S3", "SSE-KMS"],
        default=None,
        help="加密类型：SSE-S3 或 SSE-KMS（可选）",
    )
    parser.add_argument(
        "--kms-key-arn",
        default=None,
        help="KMS 密钥 ARN，仅 --sse-type SSE-KMS 时需要",
    )
    args = parser.parse_args()
    client = create_client(args)

    kwargs = {"vectorBucketName": args.bucket}
    if args.sse_type:
        enc = {"sseType": args.sse_type}
        if args.kms_key_arn:
            enc["kmsKeyArn"] = args.kms_key_arn
        kwargs["encryptionConfiguration"] = enc

    resp = client.create_vector_bucket(**kwargs)
    success_output({
        "action": "create_vector_bucket",
        "bucket": args.bucket,
        "region": args.region,
        "encrypted": args.sse_type is not None,
        "sse_type": args.sse_type,
        "request_id": resp.get("ResponseMetadata", {}).get("RequestId", "N/A"),
    })


if __name__ == "__main__":
    run(main)
