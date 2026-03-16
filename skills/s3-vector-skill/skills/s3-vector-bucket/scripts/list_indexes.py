#!/usr/bin/env python3
"""
列出 Amazon S3 向量桶的所有索引

用法:
    python3 list_indexes.py \
        --bucket <BucketName> \
        --region <Region> \
        [--max-results 10] \
        [--prefix "demo-"]
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("列出 Amazon S3 向量桶的所有索引")
    parser.add_argument("--max-results", type=int, default=None, help="最大返回数量")
    parser.add_argument("--prefix", default=None, help="索引名前缀过滤")
    parser.add_argument("--next-token", default=None, help="分页 Token")
    args = parser.parse_args()
    client = create_client(args)

    kwargs = {"vectorBucketName": args.bucket}
    if args.max_results is not None:
        kwargs["maxResults"] = args.max_results
    if args.prefix:
        kwargs["prefix"] = args.prefix
    if args.next_token:
        kwargs["nextToken"] = args.next_token

    resp = client.list_indexes(**kwargs)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "list_indexes",
        "bucket": args.bucket,
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
