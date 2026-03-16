#!/usr/bin/env python3
"""
查询 Amazon S3 向量索引信息

用法:
    python3 get_index.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName>
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("查询 Amazon S3 向量索引信息")
    parser.add_argument("--index", required=True, help="索引名称")
    args = parser.parse_args()
    client = create_client(args)

    resp = client.get_index(vectorBucketName=args.bucket, indexName=args.index)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "get_index",
        "bucket": args.bucket,
        "index": args.index,
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
