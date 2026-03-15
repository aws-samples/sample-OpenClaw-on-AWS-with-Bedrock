-- ============================================================
-- OpenClaw 模板下载量统计 & 预估 AWS 收入
-- ============================================================
--
-- 前置条件 (一次性配置，已完成):
--   - Athena database: s3logs
--   - Glue table: s3logs.access_logs
--     - 类型: raw string (LazySimpleSerDe)
--     - Partition Projection: year(int), month(int 补零), day(int 补零)
--     - Location: s3://sharefile-jiade/354790194606/cn-northwest-1/sharefile-jiade/
--
-- 使用方法:
--   1. 打开 Athena 控制台，选择 database: s3logs
--   2. 粘贴此 SQL，按需修改 year / month 范围
--   3. 直接运行，无需任何维护
--
-- 参数说明:
--   year = 2026          → 查询年份
--   month BETWEEN 1 AND 12 → 查询月份范围，改成 BETWEEN 3 AND 3 只查 3 月
--   0.20                 → 下载→实际部署转化率 (考虑重复请求、验证预览、爬虫)
--   115.0                → 单个部署预估月消费 USD (EC2 + EBS + VPC Endpoints + Bedrock)
--
-- 注意:
--   - 仅统计通过 Launch Stack 按钮触发的 S3 下载
--   - GitHub clone / 直接下载 YAML 的用户未计入 (实际下载量更高)
--   - 日志时间戳有换行符 bug，用 LIKE 匹配文件名绕过字段解析问题
-- ============================================================

WITH filtered AS (
  SELECT
    CASE
      WHEN raw LIKE '%clawdbot-bedrock-agentcore-multitenancy%' THEN 'clawdbot-bedrock-agentcore-multitenancy.yaml'
      WHEN raw LIKE '%clawdbot-bedrock-mac%'                    THEN 'clawdbot-bedrock-mac.yaml'
      WHEN raw LIKE '%clawdbot-bedrock-agentcore.yaml%'         THEN 'clawdbot-bedrock-agentcore.yaml'
      WHEN raw LIKE '%clawdbot-bedrock.yaml%'                   THEN 'clawdbot-bedrock.yaml'
      WHEN raw LIKE '%clawdbot-china%'                          THEN 'clawdbot-china.yaml'
      ELSE 'other'
    END AS key,
    regexp_extract(raw, '"[^"]*" ([0-9]{3})', 1) AS httpstatus
  FROM s3logs.access_logs
  WHERE year = 2026
    AND month BETWEEN 1 AND 12   -- 修改此处控制月份范围
    AND raw LIKE '%REST.GET.OBJECT%'
    AND (raw LIKE '%clawdbot-bedrock%' OR raw LIKE '%clawdbot-china%')
)
SELECT
  key,
  COUNT(*) AS downloads,
  ROUND(COUNT(*) * 0.20 * 115.0, 2) AS estimated_aws_revenue_usd
FROM filtered
WHERE httpstatus = '200'
  AND key != 'other'
GROUP BY key
ORDER BY estimated_aws_revenue_usd DESC;


-- ============================================================
-- Query 2: 按来源 IP 统计下载量 (独立用户分析)
-- 用途: 了解有多少独立来源在使用模板
-- 注意: 统计范围是所有文件，不限于模板
-- ============================================================
SELECT
  regexp_extract(raw, '\] (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}) ', 1) AS remoteip,
  COUNT(*) AS downloads
FROM s3logs.access_logs
WHERE year = 2026
  AND month BETWEEN 1 AND 12
  AND raw LIKE '%REST.GET.OBJECT%'
  AND regexp_extract(raw, '"[^"]*" ([0-9]{3})', 1) = '200'
GROUP BY 1
HAVING regexp_extract(raw, '\] (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}) ', 1) != ''
ORDER BY downloads DESC;


-- ============================================================
-- Query 3: 按日期 + 模板文件统计每日下载趋势
-- 用途: 观察下载量随时间变化，识别发布/推广带来的峰值
-- 注意: 时间戳有换行 bug，用 LIKE 提取日期部分
-- ============================================================
SELECT
  -- 从原始行提取日期，格式: [09/Mar/2026:01:19:01 +0000]
  -- 先去掉换行，再提取 dd/MMM/yyyy
  date_parse(
    regexp_extract(regexp_replace(raw, chr(10), ' '), '\[(\d{2}/\w{3}/\d{4}):', 1),
    '%d/%b/%Y'
  ) AS download_date,
  CASE
    WHEN raw LIKE '%clawdbot-bedrock-agentcore-multitenancy%' THEN 'clawdbot-bedrock-agentcore-multitenancy.yaml'
    WHEN raw LIKE '%clawdbot-bedrock-mac%'                    THEN 'clawdbot-bedrock-mac.yaml'
    WHEN raw LIKE '%clawdbot-bedrock-agentcore.yaml%'         THEN 'clawdbot-bedrock-agentcore.yaml'
    WHEN raw LIKE '%clawdbot-bedrock.yaml%'                   THEN 'clawdbot-bedrock.yaml'
    WHEN raw LIKE '%clawdbot-china%'                          THEN 'clawdbot-china.yaml'
  END AS key,
  COUNT(*) AS downloads
FROM s3logs.access_logs
WHERE year = 2026
  AND month BETWEEN 1 AND 12
  AND raw LIKE '%REST.GET.OBJECT%'
  AND (raw LIKE '%clawdbot-bedrock%' OR raw LIKE '%clawdbot-china%')
  AND regexp_extract(raw, '"[^"]*" ([0-9]{3})', 1) = '200'
GROUP BY 1, 2
ORDER BY download_date DESC, downloads DESC;




直接看文件（push 到 GitHub 后）：

cat traffic/clones-history.ndjson | python3 -c "
import json,sys
for line in sys.stdin:
    d = json.loads(line)
    c = d['clones']
    print(d['fetched_at'], 'clones:', c['count'], 'uniques:', c['uniques'])
"
GitHub 网页：仓库页面 → Insights → Traffic，直接看图表，但只有 14 天。
workflow push 上去之后，每天自动把数据追加到