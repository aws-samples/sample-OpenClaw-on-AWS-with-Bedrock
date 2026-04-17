# GitHub Traffic Data

Auto-collected daily at UTC 1:00 (Beijing 9:00) via GitHub Actions.

## Files

| File | Format | Content |
|------|--------|---------|
| `clones-history.ndjson` | `{"date":"2026-04-15","count":170,"uniques":60}` | Daily clone counts |
| `views-history.ndjson` | `{"date":"2026-04-15","count":622,"uniques":214}` | Daily page view counts |
| `stats-history.ndjson` | `{"date":"2026-04-17","stars":389,"forks":118,...}` | Daily repo stats snapshot |

## How it works

GitHub Traffic API returns a 14-day rolling window with per-day breakdown. The workflow extracts daily entries, deduplicates by date, and appends only new days. This accumulates a permanent history beyond the 14-day API limit.

## Query

```bash
# Total clones
cat traffic/clones-history.ndjson | python3 -c "
import json,sys
total=0
for line in sys.stdin:
    d=json.loads(line)
    total+=d['count']
    print(f\"  {d['date']}  {d['count']:4d} clones  {d['uniques']:3d} unique\")
print(f'Total: {total}')
"
```
