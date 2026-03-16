#!/usr/bin/env python3
"""
benchmark.py — 测量 S3 Vector Skill 路由前后真实 Token 消耗并生成对比图

原理:
  - 路由前: 全量注入所有 SKILL.md 描述（模拟当前 OpenClaw 行为）
  - 路由后: 用 TF-IDF 余弦相似度本地模拟 Top-K 路由（无需 S3 Vectors API）
           （若已建库可改用 --use-s3，走真实 S3 Vectors + Bedrock Embeddings）

用法:
    # 本地 TF-IDF 模式（快速，不需要 AWS 资源）
    python3 benchmark.py --output chart.png

    # 真实 S3 Vectors 模式（需先 build_skill_index.py 建库）
    python3 benchmark.py --use-s3 --bucket my-skill-router --index skills-v1 \
                         --output chart.png
"""

import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import find_skills, DEFAULT_SKILL_DIRS


# ── Token 计数（不依赖 tiktoken，~4 char/token for EN, ~2 for ZH）──────
def count_tokens(text: str) -> int:
    # 中文字符约 1.5 char/token，英文约 4 char/token，混合取折中
    zh = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en = len(text) - zh
    return int(zh / 1.5 + en / 4)



# ── 本地 TF-IDF 路由（不需要 AWS，仅作备用 fallback）────────────────
def tfidf_route(query: str, skills: list, top_k: int = 5) -> list:
    """用简单 TF-IDF 余弦相似度选出 Top-K Skill（fallback，不推荐生产使用）"""
    from collections import Counter
    import math

    def tokenize(text):
        return re.findall(r'[\w\u4e00-\u9fff]+', text.lower())

    def tf(tokens):
        c = Counter(tokens)
        total = len(tokens)
        return {k: v / total for k, v in c.items()}

    corpus = [f"{s['name']} {s['description']}" for s in skills]
    all_tokens = [tokenize(d) for d in corpus]

    N = len(corpus)
    df = Counter()
    for tokens in all_tokens:
        for t in set(tokens):
            df[t] += 1
    idf = {t: math.log(N / (1 + df[t])) for t in df}

    def vec(tokens):
        t = tf(tokens)
        return {k: t[k] * idf.get(k, 0) for k in t}

    def cosine(v1, v2):
        keys = set(v1) & set(v2)
        if not keys: return 0
        dot = sum(v1[k] * v2[k] for k in keys)
        n1 = math.sqrt(sum(x**2 for x in v1.values()))
        n2 = math.sqrt(sum(x**2 for x in v2.values()))
        return dot / (n1 * n2 + 1e-9)

    q_vec = vec(tokenize(query))
    scores = [(cosine(q_vec, vec(t)), i) for i, t in enumerate(all_tokens)]
    scores.sort(reverse=True)
    return [skills[i] for _, i in scores[:top_k]]


# ── Bedrock Embeddings 路由（语义准确，推荐）────────────────────────
_skill_vecs: list | None = None   # 缓存 skill 向量，避免重复调用

def bedrock_route(query: str, skills: list, top_k: int = 5,
                  embed_region: str = "ap-northeast-1", profile=None) -> list:
    """
    用 Bedrock Titan Embeddings v2 生成向量，内存余弦相似度选出 Top-K Skill。
    首次调用会为所有 Skill 批量生成向量（约 30s/61个），后续调用缓存命中，< 1s。
    """
    global _skill_vecs
    import math
    from embed import embed_text, embed_texts

    # 首次：批量生成所有 Skill 向量
    if _skill_vecs is None:
        print(f"  [Bedrock] 为 {len(skills)} 个 Skill 生成向量（首次，约需 {len(skills)*0.3:.0f}s）...",
              file=sys.stderr)
        texts = [f"{s['name']}: {s['description']}" for s in skills]
        _skill_vecs = embed_texts(texts, region=embed_region, profile=profile, batch_delay=0.05)
        print(f"  [Bedrock] ✓ Skill 向量缓存完毕", file=sys.stderr)

    # 查询向量
    q_vec = embed_text(query, region=embed_region, profile=profile)

    # 余弦相似度
    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x**2 for x in a))
        nb = math.sqrt(sum(x**2 for x in b))
        return dot / (na * nb + 1e-9)

    scores = [(cosine(q_vec, sv), i) for i, sv in enumerate(_skill_vecs)]
    scores.sort(reverse=True)
    return [skills[i] for _, i in scores[:top_k]]


# ── 真实 S3 Vectors 路由 ──────────────────────────────────────────────
def s3_route(query: str, bucket: str, index: str, top_k: int,
             region: str, embed_region: str, profile=None) -> list:
    from embed import embed_text
    import boto3
    session_kwargs = {}
    if profile:
        session_kwargs["profile_name"] = profile
    session = boto3.Session(**session_kwargs)
    client = session.client("s3vectors", region_name=region)

    q_vec = embed_text(query, region=embed_region, profile=profile)
    resp = client.query_vectors(
        vectorBucketName=bucket,
        indexName=index,
        queryVector={"float32": q_vec},
        topK=top_k,
        returnMetadata=True,
    )
    return [
        {"name": r["key"], "description": r.get("metadata", {}).get("description", "")}
        for r in resp.get("vectors", [])
    ]


# ── 测试查询集 ────────────────────────────────────────────────────────
TEST_QUERIES = [
    ("今天天气怎么样",             "天气查询"),
    ("帮我看看 EKS Pod 崩溃了",    "EKS 排障"),
    ("搜索 GitHub Issues bug",     "GitHub 操作"),
    ("查一下 CloudWatch 日志",     "AWS 监控"),
    ("用 Codex 帮我写代码",        "编程辅助"),
    ("CDK deploy 失败怎么排查",    "IaC 操作"),
    ("帮我发一条 Slack 消息",      "消息发送"),
    ("今天股市行情",               "股票分析"),
    ("帮我建一个 Cron Job",        "定时任务"),
    ("搜索最新 AWS 文档",          "AWS 知识"),
]


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Skill 路由 Token 节省率基准测试")
    ap.add_argument("--skills-dir", nargs="+", default=None)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--output", default="/tmp/skill_router_benchmark.png")
    ap.add_argument("--use-s3", action="store_true", help="使用真实 S3 Vectors（需先建库）")
    ap.add_argument("--use-tfidf", action="store_true", help="使用 TF-IDF 路由（不调用 Bedrock，快速但不准确）")
    ap.add_argument("--bucket", default=os.getenv("SKILL_ROUTER_BUCKET", ""))
    ap.add_argument("--index",  default=os.getenv("SKILL_ROUTER_INDEX", "skills-v1"))
    ap.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "ap-northeast-1"))
    ap.add_argument("--embed-region", default=os.getenv("AWS_BEDROCK_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-northeast-1")))
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    # 1. 加载所有 Skill
    print("加载 Skills...")
    skills = find_skills(args.skills_dir)
    print(f"  发现 {len(skills)} 个 Skill")

    # 2. 计算"路由前"基准 Token（全量注入所有 Skill 描述）
    full_injection_text = "\n".join(
        f"<skill><name>{s['name']}</name><description>{s['description']}</description></skill>"
        for s in skills
    )
    base_tokens = count_tokens(full_injection_text)
    print(f"\n路由前（全量注入）: {base_tokens} tokens（{len(skills)} 个 Skill）")

    # 3. 对每条查询测量路由后 Token
    results = []
    print(f"\n路由后（Top-{args.top_k}）测量:")
    print(f"  {'查询':<25} {'路由前':>8} {'路由后':>8} {'节省':>7} {'节省率':>7}")
    print("  " + "-" * 65)

    for query, label in TEST_QUERIES:
        if args.use_s3 and args.bucket:
            top_skills = s3_route(query, args.bucket, args.index, args.top_k,
                                  args.region, args.embed_region, args.profile)
        elif args.use_tfidf:
            top_skills = tfidf_route(query, skills, args.top_k)
        else:
            # 默认：Bedrock Embeddings（语义准确，Skill 向量首次调用后缓存）
            top_skills = bedrock_route(query, skills, args.top_k,
                                       args.embed_region, args.profile)

        routed_text = "\n".join(
            f"<skill><name>{s['name']}</name><description>{s['description']}</description></skill>"
            for s in top_skills
        )
        routed_tokens = count_tokens(routed_text)
        saved = base_tokens - routed_tokens
        pct = saved / base_tokens * 100

        print(f"  {label:<25} {base_tokens:>8,} {routed_tokens:>8,} {saved:>7,} {pct:>6.1f}%")
        results.append({
            "label": label,
            "query": query,
            "before": base_tokens,
            "after": routed_tokens,
            "saved": saved,
            "saving_pct": pct,
            "top_skills": [s["name"] for s in top_skills],
        })

    avg_saving = sum(r["saving_pct"] for r in results) / len(results)
    print(f"\n  平均节省率: {avg_saving:.1f}%")

    # 4. 保存数据
    data_path = args.output.replace(".png", "_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n数据已保存: {data_path}")

    # 5. 生成图表
    generate_chart(results, avg_saving, args.output, args.top_k, len(skills))
    print(f"图表已保存: {args.output}")


def generate_chart(results, avg_saving, out_path, top_k, total_skills):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib import font_manager as fm
    import numpy as np

    # 中文字体
    font_candidates = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
    ]
    prop = None
    for fp in font_candidates:
        if os.path.exists(fp):
            prop = fm.FontProperties(fname=fp)
            break
    if prop is None:
        prop = fm.FontProperties()  # fallback

    labels = [r["label"] for r in results]
    before = [r["before"] for r in results]
    after  = [r["after"]  for r in results]
    savings_pct = [r["saving_pct"] for r in results]

    fig, ax = plt.subplots(figsize=(14, 8))
    fig.patch.set_facecolor("#FAFAFA")
    ax.set_facecolor("#FAFAFA")

    x = np.arange(len(labels))
    w = 0.38

    ax.bar(x - w/2, before, w, color="#F28B82", zorder=2, linewidth=0, label="路由前")
    ax.bar(x + w/2, after,  w, color="#57BB8A", zorder=2, linewidth=0, label="路由后")

    # 节省率标注
    for xi, (b, a, s) in enumerate(zip(before, after, savings_pct)):
        ax.text(xi - w/2, b + max(before) * 0.015,
                f"-{s:.0f}%", ha="center", va="bottom",
                color="#1A73E8", fontsize=9, fontweight="bold", fontproperties=prop)
        # after 柱顶显示实际值
        ax.text(xi + w/2, a + max(before) * 0.015,
                f"{a:,}", ha="center", va="bottom",
                color="#0F9D58", fontsize=8, fontproperties=prop)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontproperties=prop, fontsize=10)
    ax.set_ylabel("Token 消耗量（每轮对话）", fontproperties=prop, fontsize=11)
    ax.set_ylim(0, max(before) * 1.2)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.tick_params(axis="both", length=0)
    ax.grid(axis="y", linestyle="--", alpha=0.4, zorder=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    mode = "S3 Vectors" if "--use-s3" in sys.argv else ("TF-IDF 本地" if "--use-tfidf" in sys.argv else "Bedrock Embeddings")
    ax.set_title(
        f"S3 Vector Skill 路由前后 Token 消耗对比\n"
        f"（{total_skills} 个 Skill → Top-{top_k} 路由，平均节省 {avg_saving:.1f}%，路由方式: {mode}）",
        fontproperties=prop, fontsize=13, fontweight="bold", pad=16,
    )

    ax.legend(handles=[
        mpatches.Patch(color="#F28B82", label=f"路由前（全量 {total_skills} 个 Skill）"),
        mpatches.Patch(color="#57BB8A", label=f"路由后（Top-{top_k} Skill）"),
    ], prop=prop, loc="upper right", framealpha=0.9)

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()


if __name__ == "__main__":
    main()
