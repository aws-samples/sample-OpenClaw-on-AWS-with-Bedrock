#!/usr/bin/env python3
"""
列出所有 Amazon S3 向量桶

用法:
    python3 list_vector_buckets.py \
        --bucket <任意占位> \
        --region <Region> \
        [--max-results 10] \
        [--prefix "my-"]
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("列出所有 Amazon S3 向量桶")
    # 对于 list 操作，--bucket 不需要，但 base_parser 要求必填；用 nargs='?' 覆盖
    parser._option_string_actions["--bucket"].required = False
    parser._option_string_actions["--bucket"].default = None
    parser.add_argument("--max-results", type=int, default=None, help="最大返回数量")
    parser.add_argument("--prefix", default=None, help="桶名前缀过滤")
    parser.add_argument("--next-token", default=None, help="分页 Token")
    args = parser.parse_args()
    client = create_client(args)

    kwargs = {}
    if args.max_results is not None:
        kwargs["maxResults"] = args.max_results
    if args.prefix:
        kwargs["prefix"] = args.prefix
    if args.next_token:
        kwargs["nextToken"] = args.next_token

    resp = client.list_vector_buckets(**kwargs)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "list_vector_buckets",
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
