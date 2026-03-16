#!/usr/bin/env python3
"""
Embedding 工具模块 — 通过 Bedrock Titan Embeddings v2 生成 1024 维向量。
支持内存缓存 + 磁盘缓存（相同文本跨进程不重复调用 API）。
"""

import json
import os
import hashlib
import random
import time
from typing import Optional

_clients: dict[tuple, object] = {}   # key: (region, profile) → client
_cache: dict[str, list[float]] = {}  # key: MD5(text) → embedding vector

EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBED_DIMENSION = 1024
EMBED_REGION = os.getenv("AWS_BEDROCK_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-northeast-1"))  # 默认跟 S3 Vectors 同 Region

# ── 磁盘缓存 ─────────────────────────────────────────────────────────
CACHE_DIR = os.path.expanduser("~/.cache/s3-vector-skill")
CACHE_FILE = os.path.join(CACHE_DIR, "embed_cache.json")
_disk_cache: dict[str, list[float]] | None = None  # lazy load


def _load_disk_cache() -> dict[str, list[float]]:
    global _disk_cache
    if _disk_cache is not None:
        return _disk_cache
    _disk_cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                _disk_cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            _disk_cache = {}
    return _disk_cache


def _save_disk_cache():
    if _disk_cache is None:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(_disk_cache, f)
    except OSError:
        pass  # 非致命，静默失败


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _get_client(region: Optional[str] = None, profile: Optional[str] = None):
    key = (region or EMBED_REGION, profile)
    if key not in _clients:
        import boto3
        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs)
        _clients[key] = session.client("bedrock-runtime", region_name=key[0])
    return _clients[key]


def embed_text(
    text: str,
    region: Optional[str] = None,
    profile: Optional[str] = None,
    use_cache: bool = True,
    retry: int = 3,
) -> list[float]:
    """
    对给定文本生成 1024 维 Titan Embeddings v2 向量。
    优先查内存缓存 → 磁盘缓存 → 调用 API。
    """
    key = _cache_key(text)

    # 内存缓存
    if use_cache and key in _cache:
        return _cache[key]

    # 磁盘缓存
    if use_cache:
        disk = _load_disk_cache()
        if key in disk:
            _cache[key] = disk[key]
            return disk[key]

    client = _get_client(region=region, profile=profile)
    body = json.dumps({
        "inputText": text[:8000],  # Titan v2 最大 8192 tokens
        "dimensions": EMBED_DIMENSION,
        "normalize": True,
    })

    for attempt in range(retry):
        try:
            resp = client.invoke_model(
                modelId=EMBED_MODEL_ID,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(resp["body"].read())
            vec = result["embedding"]
            if use_cache:
                _cache[key] = vec
                disk = _load_disk_cache()
                disk[key] = vec
                _save_disk_cache()
            return vec
        except Exception as e:
            if attempt < retry - 1:
                time.sleep(2 ** attempt + random.uniform(0, 1))  # jitter (#9)
            else:
                raise RuntimeError(f"Embedding 生成失败（{retry}次重试后）: {e}") from e


def embed_texts(
    texts: list[str],
    region: Optional[str] = None,
    profile: Optional[str] = None,
    batch_delay: float = 0.1,
) -> list[list[float]]:
    """
    批量生成向量，自动加延迟避免限流。
    """
    results = []
    for i, text in enumerate(texts):
        if i > 0:
            time.sleep(batch_delay)
        results.append(embed_text(text, region=region, profile=profile))
    return results
