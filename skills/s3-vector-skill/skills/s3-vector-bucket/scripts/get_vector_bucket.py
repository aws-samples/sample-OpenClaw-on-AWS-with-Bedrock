#!/usr/bin/env python3
"""
查询 Amazon S3 向量桶信息

用法:
    python3 get_vector_bucket.py \
        --bucket <BucketName> \
        --region <Region>
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("查询 Amazon S3 向量桶信息")
    args = parser.parse_args()
    client = create_client(args)

    resp = client.get_vector_bucket(vectorBucketName=args.bucket)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "get_vector_bucket",
        "bucket": args.bucket,
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
