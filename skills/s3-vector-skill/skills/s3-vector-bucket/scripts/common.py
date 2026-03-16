#!/usr/bin/env python3
"""
公共基础模块 — 所有 S3 向量桶脚本复用的客户端初始化、错误处理和 Skill 扫描逻辑。
"""

import argparse
import hashlib
import json
import os
import re
import sys


# ── 默认 Skill 扫描目录 ──────────────────────────────────────────────
def _resolve_global_skills_dir():
    """动态解析 openclaw 全局 skills 目录（避免硬编码 nvm 路径）"""
    try:
        import subprocess
        result = subprocess.run(
            ["node", "-e", "console.log(require.resolve('openclaw'))"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # require.resolve returns .../openclaw/index.js → take parent/skills
            openclaw_root = os.path.dirname(result.stdout.strip())
            skills_dir = os.path.join(openclaw_root, "skills")
            if os.path.isdir(skills_dir):
                return skills_dir
    except Exception:
        pass
    # fallback: 常见安装位置
    for candidate in [
        os.path.expanduser("~/.nvm/versions/node"),  # scan nvm versions
    ]:
        if os.path.isdir(candidate):
            for ver in sorted(os.listdir(candidate), reverse=True):
                p = os.path.join(candidate, ver, "lib/node_modules/openclaw/skills")
                if os.path.isdir(p):
                    return p
    return None


_global_skills = _resolve_global_skills_dir()

DEFAULT_SKILL_DIRS = [
    d for d in [
        os.path.expanduser("~/.openclaw/workspace-general-tech/skills"),
        os.path.expanduser("~/.openclaw/workspace/skills"),
        _global_skills,
        os.path.expanduser("~/.openclaw/skills"),
    ] if d is not None
]


def base_parser(description, bucket_required=True):
    """创建基础参数解析器，包含凭证和连接参数"""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_DEFAULT_REGION", "ap-northeast-1"),
        help="AWS Region，如 ap-northeast-1（或设置环境变量 AWS_DEFAULT_REGION）",
    )
    parser.add_argument(
        "--bucket",
        required=bucket_required,
        default=None,
        help="S3 向量桶名称，如 my-vector-bucket",
    )
    parser.add_argument(
        "--profile",
        default=os.getenv("AWS_PROFILE"),
        help="AWS CLI 配置文件名（可选，默认使用实例 IAM Role 或环境变量凭证）",
    )
    return parser


def create_client(args):
    """根据解析后的参数创建 boto3 s3vectors 客户端"""
    try:
        import boto3
    except ImportError:
        fail("boto3 未安装，请运行: pip3 install boto3 --upgrade")

    session_kwargs = {}
    if getattr(args, "profile", None):
        session_kwargs["profile_name"] = args.profile

    try:
        session = boto3.Session(**session_kwargs)
        client = session.client("s3vectors", region_name=args.region)
        return client
    except Exception as e:
        fail(f"创建 AWS 客户端失败: {e}")


def success_output(data):
    """输出成功结果的 JSON（自动处理 datetime 序列化）"""
    result = {"success": True}
    result.update(data)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))


def _json_default(obj):
    """JSON 序列化 fallback：处理 datetime 等非标准类型"""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def fail(message):
    """输出失败结果并退出"""
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False, indent=2))
    sys.exit(1)


def handle_error(e):
    """统一处理 AWS 异常（含 ThrottlingException 提示）"""
    try:
        from botocore.exceptions import ClientError, BotoCoreError
        if isinstance(e, ClientError):
            err = e.response.get("Error", {})
            error_code = err.get("Code", "Unknown")
            message = err.get("Message", str(e))

            # ThrottlingException 特殊提示 (#9)
            if error_code in ("ThrottlingException", "Throttling", "TooManyRequestsException"):
                message = f"请求被限流: {message}。建议：稍后重试或降低并发。"

            print(json.dumps({
                "success": False,
                "error": f"服务端错误: {message}",
                "error_code": error_code,
                "request_id": e.response.get("ResponseMetadata", {}).get("RequestId", "Unknown"),
            }, ensure_ascii=False, indent=2))
        elif isinstance(e, BotoCoreError):
            print(json.dumps({"success": False, "error": f"客户端错误: {e}"}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"success": False, "error": f"未知错误: {e}"}, ensure_ascii=False, indent=2))
    except ImportError:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False, indent=2))
    sys.exit(1)


def run(func):
    """运行主函数并捕获异常"""
    try:
        func()
    except SystemExit:
        raise
    except Exception as e:
        handle_error(e)


# ══════════════════════════════════════════════════════════════════════
# Skill 扫描（共享逻辑，供 build_skill_index.py / benchmark.py 等复用）
# ══════════════════════════════════════════════════════════════════════

def parse_skill_md(path: str) -> dict | None:
    """解析 SKILL.md，返回 {name, description, path}，失败返回 None"""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    # 提取 YAML frontmatter（--- ... ---）
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return None

    fm_text = m.group(1)

    # 优先用 pyyaml（更健壮），fallback 到正则 (#6)
    try:
        import yaml
        parsed = yaml.safe_load(fm_text)
        if isinstance(parsed, dict):
            name = str(parsed.get("name", "")).strip()
            description = str(parsed.get("description", "")).strip()
            if name and description:
                return {"name": name, "description": description, "path": path}
    except ImportError:
        pass  # pyyaml 未安装，fallback
    except Exception:
        pass  # YAML 解析失败，fallback

    # fallback: 正则解析
    return _parse_skill_md_regex(fm_text, path)


def _parse_skill_md_regex(fm: str, path: str) -> dict | None:
    """正则 fallback 解析 SKILL.md frontmatter"""
    name_match = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
    if not name_match:
        return None
    name = name_match.group(1).strip().strip('"\'')

    desc_pos = re.search(r"^description:\s*", fm, re.MULTILINE)
    if not desc_pos:
        return None

    rest = fm[desc_pos.end():]

    if rest.startswith('"'):
        desc_m = re.match(r'"(.*?)"', rest, re.DOTALL)
        description = desc_m.group(1).strip() if desc_m else ""
    elif rest.startswith("'"):
        desc_m = re.match(r"'(.*?)'", rest, re.DOTALL)
        description = desc_m.group(1).strip() if desc_m else ""
    elif rest.startswith("|") or rest.startswith(">"):
        lines = rest.split("\n")[1:]
        block_lines = []
        for line in lines:
            if line and (line[0] == " " or line[0] == "\t"):
                block_lines.append(line.strip())
            elif line.strip() == "":
                continue
            else:
                break
        description = " ".join(block_lines)
    else:
        description = rest.split("\n")[0].strip().strip('"\'')

    if not description:
        return None

    return {"name": name, "description": description, "path": path}


def find_skills(skill_dirs: list[str] | None = None) -> list[dict]:
    """递归扫描目录，收集所有有效 SKILL.md"""
    dirs = skill_dirs or DEFAULT_SKILL_DIRS
    skills = []
    seen_names = set()

    for base_dir in dirs:
        base_dir = os.path.expanduser(base_dir)
        if not os.path.isdir(base_dir):
            continue
        for root, subdirs, files in os.walk(base_dir):
            subdirs[:] = [d for d in subdirs if not d.startswith(".") and d != "node_modules"]
            if "SKILL.md" in files:
                skill_path = os.path.join(root, "SKILL.md")
                skill = parse_skill_md(skill_path)
                if skill and skill["name"] not in seen_names:
                    skills.append(skill)
                    seen_names.add(skill["name"])

    return skills


def desc_hash(description: str) -> str:
    """生成 description 的 MD5 hash，用于增量构建对比"""
    return hashlib.md5(description.encode()).hexdigest()
