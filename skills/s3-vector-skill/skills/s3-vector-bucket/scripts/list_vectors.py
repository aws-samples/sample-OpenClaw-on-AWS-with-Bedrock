#!/usr/bin/env python3
"""
列出 Amazon S3 向量索引中的向量列表

用法:
    python3 list_vectors.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        [--max-results 10] \
        [--return-data] \
        [--return-metadata] \
        [--segment-count 4] \
        [--segment-index 0]
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("列出 Amazon S3 向量索引中的向量列表")
    parser.add_argument("--index", required=True, help="索引名称")
    parser.add_argument("--max-results", type=int, default=None, help="最大返回数量")
    parser.add_argument("--next-token", default=None, help="分页 Token")
    parser.add_argument("--return-data", action="store_true", default=False, help="是否返回向量数据")
    parser.add_argument("--return-metadata", action="store_true", default=False, help="是否返回元数据")
    parser.add_argument("--segment-count", type=int, default=None, help="并行分段总数（用于大规模并行遍历）")
    parser.add_argument("--segment-index", type=int, default=None, help="当前分段索引（从 0 开始）")
    args = parser.parse_args()
    client = create_client(args)

    kwargs = {
        "vectorBucketName": args.bucket,
        "indexName": args.index,
    }
    if args.max_results is not None:
        kwargs["maxResults"] = args.max_results
    if args.next_token:
        kwargs["nextToken"] = args.next_token
    if args.return_data:
        kwargs["returnData"] = True
    if args.return_metadata:
        kwargs["returnMetadata"] = True
    if args.segment_count is not None and args.segment_index is not None:
        kwargs["segmentCount"] = args.segment_count
        kwargs["segmentIndex"] = args.segment_index

    resp = client.list_vectors(**kwargs)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "list_vectors",
        "bucket": args.bucket,
        "index": args.index,
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
