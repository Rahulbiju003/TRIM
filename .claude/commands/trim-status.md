# TRIM Status Check

Fetch and display the current TRIM server status and metrics summary.

Run the following and report what you find:

```bash
curl -s http://192.168.68.109:8080/health
```

```bash
curl -s \
  -H "X-TRIM-Key: 94c22e7a93ba8d157644de5568aa0564c2c334d48f1543f2c161529b8e687168" \
  http://192.168.68.109:8080/api/metrics | python3 -c "
import json, sys, datetime
data = json.load(sys.stdin)
records = data.get('records', [])
if not records:
    print('No requests recorded yet.')
    sys.exit(0)

total       = len(records)
cache_hits  = sum(1 for r in records if r.get('cache_hit'))
deltas      = sum(1 for r in records if r.get('delta'))
passes      = sum(1 for r in records if r.get('pass_through'))
full        = total - cache_hits - deltas - passes
rtk_saved   = sum(r.get('rtk_tokens_saved', 0) for r in records)
total_in    = sum(r.get('input_tokens', 0) for r in records)
total_out   = sum(r.get('output_tokens', 0) for r in records)
web_reads   = sum(1 for r in records if r.get('route') == 'web-read')
types       = {}
for r in records:
    t = r.get('content_type', 'text')
    types[t] = types.get(t, 0) + 1

print(f'=== TRIM Metrics ===')
print(f'Total requests : {total}')
print(f'  Full (LLM)   : {full}')
print(f'  Cache hits   : {cache_hits}  ({cache_hits/total*100:.0f}% free)')
print(f'  Delta updates: {deltas}')
print(f'  Pass-throughs: {passes}')
print(f'Routes         : bulk-read={total - web_reads}  web-read={web_reads}')
print(f'Content types  : {types}')
print(f'Tokens in/out  : {total_in:,} / {total_out:,}')
print(f'RTK saved      : {rtk_saved:,} tokens')
print()
print('Last 3 requests:')
for r in records[-3:]:
    ts = datetime.datetime.fromtimestamp(r[\"ts\"]).strftime(\"%H:%M:%S\")
    status = \"CACHE\" if r.get(\"cache_hit\") else (\"DELTA\" if r.get(\"delta\") else (\"PASS\" if r.get(\"pass_through\") else \"FULL\"))
    print(f'  [{ts}] {status} | {r.get(\"route\",\"bulk-read\")} | {r.get(\"content_type\",\"text\")} | in={r.get(\"input_tokens\",0)} rtk_saved={r.get(\"rtk_tokens_saved\",0)}')
    print(f'         {r.get(\"file\",\"?\")[-70:]}')
"
```

After running, summarise:
1. Whether the server is healthy
2. Cache hit rate and whether caching is working
3. Whether RTK is saving tokens (rtk_saved > 0)
4. Any patterns in what's being read (files vs web, content types)
5. Anything unusual
