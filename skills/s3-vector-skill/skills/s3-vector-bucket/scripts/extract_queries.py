#!/usr/bin/env python3
"""
extract_queries.py — 从 OpenClaw session logs 提取真实用户查询
作为 benchmark.py 的真实查询集

数据来源: ~/.openclaw/agents/<agent>/sessions/*.jsonl
格式: 每行一个 JSON 事件，type=message, message.role=user

用法:
    # 提取所有 agent 的查询（最近 N 条，去重、过滤系统消息）
    python3 extract_queries.py --limit 50 --output queries.json

    # 只看 general-tech agent
    python3 extract_queries.py --agent general-tech --limit 30

    # 提取后直接跑 benchmark
    python3 extract_queries.py --limit 50 | python3 benchmark.py --queries-stdin
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SESSIONS_ROOT = os.path.expanduser("~/.openclaw/agents")

# 过滤掉这些系统/内部消息前缀（不是真实用户查询）
SKIP_PREFIXES = [
    "[cron:", "[heartbeat", "HEARTBEAT",
    "[System:", "System:", "[message_id:",
    "Conversation info", "Sender (untrusted",
    "[Thread history",
]

# 过滤太短或明显是系统注入的消息
MIN_LEN = 5
MAX_LEN = 500  # 超长的是带有大量元数据的系统消息


def is_real_user_query(text: str) -> bool:
    """判断是否是真实用户查询（排除系统消息和 cron 触发）"""
    text = text.strip()
    if len(text) < MIN_LEN or len(text) > MAX_LEN:
        return False
    for prefix in SKIP_PREFIXES:
        if text.startswith(prefix):
            return False
    # 过滤纯英文大写（系统命令）
    if re.match(r'^[A-Z_]+$', text):
        return False
    return True


def extract_text_from_content(content) -> str:
    """从 message.content（可能是 str 或 list）提取纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def extract_actual_query(raw_text: str) -> str:
    """
    从 OpenClaw 注入了大量上下文的 user message 中，提取实际用户发的那句话。

    OpenClaw 的 user message 格式（Slack 等渠道）:
        [Thread history - for context]
        ...
        System: [timestamp] Slack message in #channel from User: <实际查询>

        Conversation info (untrusted metadata): {...}

        Sender (untrusted metadata): {...}

        <实际查询重复一次>

    提取策略：
      1. 优先用 "System: [...] from <Name>: <text>" 正则，取最后一个匹配
      2. 回退：取 Sender metadata 块之后的最后一段文本
      3. 再回退：取全文最后3行
    """
    # 策略1: System: [...] from Name: <text>
    matches = re.findall(
        r'System:\s*\[.*?\].*?from\s+\w+:\s*(.+?)(?=\n\nConversation\s+info|\Z)',
        raw_text, re.DOTALL
    )
    if matches:
        text = matches[-1].strip()
        # 去掉 [Slack file: ...] 等附件注释
        text = re.sub(r'\[Slack file:.*?\]', '', text).strip()
        text = re.sub(r'\[media attached:.*?\]', '', text).strip()
        return text[:300]

    # 策略2: Sender metadata 之后的文本
    m = re.search(r'Sender \(untrusted metadata\).*?```\s*\n(.+)', raw_text, re.DOTALL)
    if m:
        text = m.group(1).strip()
        text = re.sub(r'\[.*?\]', '', text).strip()
        if len(text) > MIN_LEN:
            return text[:300]

    # 策略3: 最后几行
    lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
    clean = [l for l in lines[-5:] if not l.startswith(('[', 'System:', 'Conversation', 'Sender'))]
    return ' '.join(clean)[:300]


def extract_from_session(path: str) -> list[dict]:
    """从单个 session.jsonl 文件提取用户消息"""
    queries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if obj.get("type") != "message":
                    continue
                msg = obj.get("message", {})
                if msg.get("role") != "user":
                    continue

                text = extract_text_from_content(msg.get("content", ""))
                actual = extract_actual_query(text)

                if not is_real_user_query(actual):
                    continue

                queries.append({
                    "text": actual,
                    "timestamp": obj.get("timestamp", ""),
                    "session": os.path.basename(path),
                })
    except (OSError, UnicodeDecodeError):
        pass
    return queries


def main():
    ap = argparse.ArgumentParser(description="提取 OpenClaw 真实用户查询集")
    ap.add_argument("--agent", default=None,
                    help="只提取指定 agent（默认所有 agent）")
    ap.add_argument("--limit", type=int, default=50,
                    help="提取最近 N 条查询（默认 50）")
    ap.add_argument("--output", default=None,
                    help="输出文件路径（默认 stdout）")
    ap.add_argument("--dedup", action="store_true", default=True,
                    help="去重（默认开启）")
    ap.add_argument("--stats", action="store_true",
                    help="打印统计信息（不输出查询列表）")
    ap.add_argument("--for-benchmark", action="store_true",
                    help="输出 benchmark.py 的 TEST_QUERIES 格式")
    args = ap.parse_args()

    # 1. 找所有 session 文件
    sessions_root = Path(SESSIONS_ROOT)
    if not sessions_root.exists():
        print(f"错误: {SESSIONS_ROOT} 不存在", file=sys.stderr)
        sys.exit(1)

    if args.agent:
        agent_dirs = [sessions_root / args.agent]
    else:
        agent_dirs = [d for d in sessions_root.iterdir() if d.is_dir()]

    all_session_files = []
    for agent_dir in agent_dirs:
        sess_dir = agent_dir / "sessions"
        if sess_dir.exists():
            all_session_files.extend(sorted(
                sess_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True  # 最近的在前
            ))

    print(f"找到 {len(all_session_files)} 个 session 文件", file=sys.stderr)

    # 2. 提取查询
    all_queries = []
    seen_texts = set()

    for sf in all_session_files:
        qs = extract_from_session(str(sf))
        for q in qs:
            text = q["text"]
            if args.dedup:
                # 简单去重：前50字相同视为重复
                key = text[:50].lower()
                if key in seen_texts:
                    continue
                seen_texts.add(key)
            all_queries.append(q)

    # 按时间排序（最近的在前），取 limit 条
    all_queries.sort(key=lambda q: q["timestamp"], reverse=True)
    all_queries = all_queries[:args.limit]

    print(f"提取到 {len(all_queries)} 条唯一用户查询", file=sys.stderr)

    # 3. 统计模式
    if args.stats:
        print(f"\n=== 查询统计 ===")
        print(f"总数: {len(all_queries)}")
        lengths = [len(q["text"]) for q in all_queries]
        print(f"平均长度: {sum(lengths)/len(lengths):.0f} 字符")
        print(f"最短: {min(lengths)} / 最长: {max(lengths)}")
        print(f"\n最近 10 条查询示例:")
        for q in all_queries[:10]:
            ts = q["timestamp"][:10] if q["timestamp"] else "?"
            print(f"  [{ts}] {q['text'][:60]}")
        return

    # 4. 输出
    if args.for_benchmark:
        # 生成 Python 格式的 TEST_QUERIES
        print("# 真实查询集（从 OpenClaw session logs 提取）")
        print("TEST_QUERIES = [")
        for q in all_queries:
            text = q["text"].replace('"', '\\"').replace("\\", "\\\\")
            # 用查询前8字作为标签
            label = q["text"][:10].strip()
            print(f'    ("{text}", "{label}"),')
        print("]")
    else:
        output = {
            "total": len(all_queries),
            "extracted_at": datetime.now().isoformat(),
            "queries": all_queries,
        }
        out_str = json.dumps(output, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out_str)
            print(f"已保存到 {args.output}", file=sys.stderr)
        else:
            print(out_str)


if __name__ == "__main__":
    main()
