#!/usr/bin/env python3
"""
获取 Amazon S3 向量索引中的指定向量

用法:
    python3 get_vectors.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        --keys key1,key2,key3 \
        [--return-data] \
        [--return-metadata]
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("获取 Amazon S3 向量索引中的指定向量")
    parser.add_argument("--index", required=True, help="索引名称")
    parser.add_argument("--keys", required=True, help="向量键列表，逗号分隔")
    parser.add_argument("--return-data", action="store_true", default=False, help="是否返回向量数据")
    parser.add_argument("--return-metadata", action="store_true", default=False, help="是否返回元数据")
    args = parser.parse_args()
    client = create_client(args)

    keys = [k.strip() for k in args.keys.split(",")]
    kwargs = {
        "vectorBucketName": args.bucket,
        "indexName": args.index,
        "keys": keys,
    }
    if args.return_data:
        kwargs["returnData"] = True
    if args.return_metadata:
        kwargs["returnMetadata"] = True

    resp = client.get_vectors(**kwargs)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "get_vectors",
        "bucket": args.bucket,
        "index": args.index,
        "keys": keys,
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
