# GitHub Traffic Data

自动收集的 GitHub 仓库访问数据，每天 UTC 1:00 (北京时间 9:00) 更新。

## 文件说明

- `clones-history.ndjson` — 每行一条 clone 记录，包含 14 天滚动窗口数据
- `views-history.ndjson` — 每行一条 views 记录

## 数据格式

```json
{"fetched_at":"2026-03-12","clones":{"count":45,"uniques":23,"clones":[...]}}
```

## 注意

- GitHub Traffic API 只保留最近 14 天数据
- 本文件通过每日自动 commit 积累历史数据
- 2026-03-12 之前的数据已丢失（未及时收集）

## 查询示例

```bash
# 查看总 clone 数
cat traffic/clones-history.ndjson | python3 -c "
import json, sys
for line in sys.stdin:
    d = json.loads(line)
    print(d['fetched_at'], 'clones:', d['clones']['count'], 'uniques:', d['clones']['uniques'])
"
```
