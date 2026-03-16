import type { HookHandler } from "../../src/hooks/hooks.js";
import { existsSync, readFileSync, writeFileSync } from "fs";
import { join } from "path";

/**
 * Skill Router Hook — agent:bootstrap
 *
 * 读取近期 memory 上下文 → 调用 skill_router.py → 写入 BOOTSTRAP.md
 * 让 LLM 在启动时聚焦于最相关的 Top-K Skill，降低无效 Token 消耗。
 */
const handler: HookHandler = async (event) => {
  if (event.type !== "agent" || event.action !== "bootstrap") return;

  const workspaceDir: string | undefined = event.context?.workspaceDir;
  if (!workspaceDir) return;

  const bucket = process.env.SKILL_ROUTER_BUCKET;
  if (!bucket) return;

  // 从 sessionKey（格式：agent:<agent_id>:<session_id>）提取 agent ID
  // 支持两种配置：
  //   1. 每 Agent 独立索引：设置 SKILL_ROUTER_INDEX_PREFIX=skills → 自动映射为 skills-general-tech 等
  //   2. 固定索引名：设置 SKILL_ROUTER_INDEX=skills-v1（所有 Agent 共用）
  const agentId = event.sessionKey?.split(":")?.[1] ?? "main";
  const indexPrefix = process.env.SKILL_ROUTER_INDEX_PREFIX;
  const index = indexPrefix
    ? `${indexPrefix}-${agentId}`
    : (process.env.SKILL_ROUTER_INDEX ?? "skills-v1");

  const topK = parseInt(process.env.SKILL_ROUTER_TOP_K ?? "5", 10);
  const region = process.env.SKILL_ROUTER_REGION ?? "ap-northeast-1";

  // 定位 skill_router.py 脚本
  const scriptPath = resolveScript();
  if (!scriptPath) {
    console.error("[skill-router-hook] 未找到 skill_router.py，跳过");
    return;
  }

  // 提取最近会话上下文（读取最近 2 天的 memory 文件）
  const context = extractRecentContext(workspaceDir);
  if (!context || context.trim().length < 10) {
    console.log("[skill-router-hook] 无近期上下文，跳过 Skill 路由");
    return;
  }

  try {
    // 调用 skill_router.py，通过 stdin 传入 context（避免 shell 拼接风险）
    const { spawnSync } = await import("child_process");
    const proc = spawnSync("python3", [
      scriptPath,
      "--bucket", bucket,
      "--index",  index,
      "--region", region,
      "--top-k",  String(topK),
      "--output", "markdown",
      "--score-threshold", process.env.SKILL_ROUTER_SCORE_THRESHOLD ?? "0.3",
      "--query",  context.slice(0, 500),   // query 通过参数传，长度已限制
    ], {
      timeout: 15000,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    });

    if (proc.status !== 0 || !proc.stdout) {
      console.error("[skill-router-hook] ⚠️ skill_router.py 返回错误:", proc.stderr?.slice(0, 200));
      return;
    }
    const output = proc.stdout;

    // 写入 BOOTSTRAP.md（注入到 LLM 上下文）
    const bootstrapPath = join(workspaceDir, "BOOTSTRAP.md");
    const content = `# Skill Router — Active Session Context\n\n` +
      `> 本段由 Skill Router Hook 在会话启动时自动生成，基于近期上下文动态筛选。\n\n` +
      `${output}\n`;

    writeFileSync(bootstrapPath, content, "utf8");

    // 添加到 bootstrapFiles，让 LLM 能看到
    if (event.context?.bootstrapFiles) {
      event.context.bootstrapFiles.push({
        name: "BOOTSTRAP.md",
        path: bootstrapPath,
        content,
      } as any);
    }

    console.log(`[skill-router-hook] ✅ 已注入 Top-${topK} Skill 路由到 BOOTSTRAP.md`);
  } catch (err) {
    // 静默失败：不影响正常 bootstrap 流程
    console.error("[skill-router-hook] ⚠️ Skill 路由失败（非阻塞）:", err instanceof Error ? err.message : String(err));
  }
};

/** 查找 skill_router.py 路径（优先环境变量，其次相对路径推断）*/
function resolveScript(): string | null {
  const envPath = process.env.SKILL_ROUTER_SCRIPT;
  if (envPath && existsSync(envPath)) return envPath;

  const candidates = [
    // 相对于 hook 文件的位置推断
    join(__dirname, "../../scripts/skill_router.py"),
  ];

  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return null;
}

/** 读取近期 2 天 memory 文件，优先提取标题和近期段落 (#4) */
function extractRecentContext(workspaceDir: string): string {
  const memoryDir = join(workspaceDir, "memory");
  if (!existsSync(memoryDir)) return "";

  const today = new Date();
  const dates = [today, new Date(today.getTime() - 86400000)].map(
    (d) => d.toISOString().slice(0, 10)
  );

  const lines: string[] = [];
  for (const date of dates) {
    const file = join(memoryDir, `${date}.md`);
    if (existsSync(file)) {
      try {
        const text = readFileSync(file, "utf8");
        lines.push(text);
      } catch {
        // ignore
      }
    }
  }

  if (lines.length === 0) return "";
  const combined = lines.join("\n");

  // 提取标题行（结构性信息密度最高）
  const headings = combined.split("\n")
    .filter(l => l.startsWith("## ") || l.startsWith("### "))
    .map(l => l.trim());

  // 提取最近 3 个非空段落
  const paragraphs = combined.split(/\n{2,}/)
    .filter(p => p.trim().length > 20)
    .slice(-3)
    .map(p => p.trim());

  // 标题优先 + 近期段落，截断到 500 字
  return [...headings, ...paragraphs].join("\n").slice(-500).trim();
}

/** 简单 shell 转义（保留，用于日志输出）*/
function shellEscape(s: string): string {
  return "'" + s.replace(/'/g, "'\\''") + "'";
}

export default handler;
