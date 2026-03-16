#!/usr/bin/env python3
"""
删除 Amazon S3 向量桶

用法:
    python3 delete_vector_bucket.py \
        --bucket <BucketName> \
        --region <Region>
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("删除 Amazon S3 向量桶")
    args = parser.parse_args()
    client = create_client(args)

    resp = client.delete_vector_bucket(vectorBucketName=args.bucket)
    success_output({
        "action": "delete_vector_bucket",
        "bucket": args.bucket,
        "region": args.region,
        "request_id": resp.get("ResponseMetadata", {}).get("RequestId", "N/A"),
    })


if __name__ == "__main__":
    run(main)
