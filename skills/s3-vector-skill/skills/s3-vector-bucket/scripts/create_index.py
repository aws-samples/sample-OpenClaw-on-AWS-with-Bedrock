#!/usr/bin/env python3
"""
创建 Amazon S3 向量索引

用法:
    python3 create_index.py \
        --bucket <BucketName> \
        --region <Region> \
        --index <IndexName> \
        --dimension <Dimension> \
        [--data-type float32] \
        [--distance-metric cosine|euclidean] \
        [--non-filterable-keys key1,key2]
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import base_parser, create_client, success_output, run


def main():
    parser = base_parser("创建 Amazon S3 向量索引")
    parser.add_argument("--index", required=True, help="索引名称")
    parser.add_argument("--dimension", required=True, type=int, help="向量维度，范围 1-4096")
    parser.add_argument("--data-type", default="float32", choices=["float32"], help="向量数据类型，当前支持 float32（默认）")
    parser.add_argument("--distance-metric", default="cosine", choices=["cosine", "euclidean"], help="距离度量：cosine（默认）或 euclidean")
    parser.add_argument("--non-filterable-keys", default=None, help="非过滤元数据键列表，逗号分隔")
    args = parser.parse_args()
    client = create_client(args)

    kwargs = {
        "vectorBucketName": args.bucket,
        "indexName": args.index,
        "dataType": args.data_type,
        "dimension": args.dimension,
        "distanceMetric": args.distance_metric,
    }
    if args.non_filterable_keys:
        kwargs["metadataConfiguration"] = {
            "nonFilterableMetadataKeys": [k.strip() for k in args.non_filterable_keys.split(",")]
        }

    resp = client.create_index(**kwargs)
    resp.pop("ResponseMetadata", None)
    success_output({
        "action": "create_index",
        "bucket": args.bucket,
        "index": args.index,
        "dimension": args.dimension,
        "data_type": args.data_type,
        "distance_metric": args.distance_metric,
        "region": args.region,
        "response_data": resp,
    })


if __name__ == "__main__":
    run(main)
