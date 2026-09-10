"""Metrics reading, aggregation, and HTML rendering for the TRIM dashboard."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from worker import config

# ── Model pricing: USD per million tokens ─────────────────────────────────────
# (input_price, output_price) — add models here as needed
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4.1-nano":            (0.10,  0.40),
    "gpt-4o-mini":             (0.15,  0.60),
    "gpt-4.1-mini":            (0.40,  1.60),
    "gpt-4o":                  (2.50, 10.00),
    "gpt-4.1":                 (2.00,  8.00),
    # Anthropic (direct)
    "claude-haiku-4-5-20251001": (0.80,  4.00),
    "claude-haiku-3-5":        (0.80,  4.00),
    "claude-sonnet-4-5":       (3.00, 15.00),
    # Gemini (direct)
    "gemini/gemini-2.5-flash":      (0.15,  0.60),
    "gemini/gemini-2.5-flash-lite": (0.10,  0.40),
    "gemini/gemini-2.0-flash":      (0.10,  0.40),
    # OpenRouter — paid
    "openrouter/google/gemini-2.5-flash":    (0.15,  0.60),
    "openrouter/openai/gpt-4o-mini":         (0.15,  0.60),
    "openrouter/anthropic/claude-haiku":     (0.80,  4.00),
    "openrouter/deepseek/deepseek-chat-v3-0324": (0.27, 1.10),
    # OpenRouter — free tier (no cost)
    "openrouter/meta-llama/llama-3.1-8b-instruct:free": (0.0, 0.0),
    "openrouter/mistralai/mistral-7b-instruct:free":    (0.0, 0.0),
    "openrouter/deepseek/deepseek-chat-v3-0324:free":   (0.0, 0.0),
    "openrouter/nex-agi/nex-n2.5-mini:free":            (0.0, 0.0),
    "openrouter/nvidia/nemotron-3.5-lightning:free":    (0.0, 0.0),
    # Ollama (local, no cost)
    "ollama/qwen2.5-coder:7b": (0.0, 0.0),
    "ollama/llama3.1:8b":      (0.0, 0.0),
}

_FALLBACK_PRICE = (1.0, 4.0)  # conservative estimate for unknown models


def _model_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate actual LLM cost in USD for a given model and token counts."""
    # Normalize: strip leading/trailing whitespace, lowercase for lookup
    key = model.strip().lower()
    price_in, price_out = _MODEL_PRICING.get(key, _FALLBACK_PRICE)
    return (input_tokens / 1_000_000 * price_in) + (output_tokens / 1_000_000 * price_out)


def _is_known_model(model: str) -> bool:
    return model.strip().lower() in _MODEL_PRICING


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TRIM — Dashboard</title>
<style>
:root {{
  --bg: #0a0e14;
  --surface: #111620;
  --surface2: #161d2b;
  --border: #1e2d40;
  --border-bright: #2a3f58;
  --accent: #4d9de0;
  --accent-dim: rgba(77,157,224,.12);
  --green: #3ddc84;
  --green-dim: rgba(61,220,132,.12);
  --amber: #f0a500;
  --amber-dim: rgba(240,165,0,.12);
  --red: #e05252;
  --muted: #5a7a9a;
  --text: #c8d8e8;
  --text-bright: #e8f2fa;
  --font: "JetBrains Mono","Fira Code","Cascadia Code",ui-monospace,monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  background:var(--bg);color:var(--text);font-family:var(--font);
  font-size:13px;padding:32px;min-height:100vh;
  background-image:radial-gradient(ellipse at 20% 0%,rgba(77,157,224,.06) 0%,transparent 60%);
}}
/* ── header ── */
header{{margin-bottom:28px;display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px}}
.logo{{display:flex;align-items:center;gap:10px}}
.logo-badge{{
  background:var(--accent);color:#000;font-weight:700;font-size:11px;
  padding:3px 8px;border-radius:4px;letter-spacing:.08em
}}
h1{{font-size:18px;color:var(--text-bright);letter-spacing:-.01em;font-weight:600}}
.sub{{color:var(--muted);font-size:11px;margin-top:3px}}
.header-meta{{text-align:right}}
.model-badge{{
  display:inline-block;background:var(--surface2);border:1px solid var(--border-bright);
  border-radius:4px;padding:3px 10px;font-size:11px;color:var(--accent);
}}
/* ── stat cards ── */
.cards{{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:12px;margin-bottom:20px
}}
.card{{
  background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:20px;position:relative;overflow:hidden;
}}
.card::before{{
  content:"";position:absolute;top:0;left:0;right:0;height:2px;
  background:var(--card-accent,var(--border));
}}
.card.accent::before{{background:var(--accent)}}
.card.green::before{{background:var(--green)}}
.card.amber::before{{background:var(--amber)}}
.card-label{{
  color:var(--muted);font-size:10px;text-transform:uppercase;
  letter-spacing:.1em;margin-bottom:12px
}}
.card-value{{font-size:28px;font-weight:700;color:var(--text-bright)}}
.card-sub{{color:var(--muted);font-size:10px;margin-top:4px}}
.card.accent .card-value{{color:var(--accent)}}
.card.green .card-value{{color:var(--green)}}
.card.amber .card-value{{color:var(--amber)}}
/* ── sections ── */
.section{{
  background:var(--surface);border:1px solid var(--border);
  border-radius:10px;padding:20px;margin-bottom:16px
}}
.section-header{{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:16px
}}
.section-title{{
  color:var(--muted);font-size:10px;text-transform:uppercase;
  letter-spacing:.1em
}}
/* ── chart ── */
canvas{{max-height:200px}}
/* ── model breakdown ── */
.model-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}}
.model-card{{
  background:var(--surface2);border:1px solid var(--border);border-radius:8px;
  padding:14px;
}}
.model-name{{color:var(--accent);font-size:11px;margin-bottom:10px;word-break:break-all;font-weight:600}}
.model-stats{{display:grid;grid-template-columns:1fr 1fr;gap:6px}}
.mstat{{}}
.mstat-label{{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em}}
.mstat-value{{color:var(--text-bright);font-size:14px;font-weight:600;margin-top:1px}}
.model-cost-bar{{
  margin-top:10px;height:3px;background:var(--border);border-radius:2px;overflow:hidden
}}
.model-cost-bar-fill{{height:100%;background:var(--green);border-radius:2px;transition:width .4s}}
.unknown-price{{color:var(--amber);font-size:9px;margin-top:4px}}
/* ── table ── */
table{{width:100%;border-collapse:collapse}}
th{{
  color:var(--muted);font-size:10px;text-transform:uppercase;
  letter-spacing:.06em;padding:8px 10px;text-align:left;
  border-bottom:1px solid var(--border);white-space:nowrap
}}
td{{
  padding:8px 10px;border-bottom:1px solid #0f1720;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  vertical-align:middle;
}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:rgba(77,157,224,.04)}}
td.path{{max-width:280px;color:var(--accent);font-size:12px}}
td.num{{font-variant-numeric:tabular-nums;color:var(--text-bright)}}
td.cost-cell{{color:var(--green);font-variant-numeric:tabular-nums}}
.tag{{
  display:inline-block;background:var(--surface2);border:1px solid var(--border);
  border-radius:3px;padding:1px 6px;font-size:10px;color:var(--muted)
}}
.empty{{color:var(--muted);text-align:center;padding:32px}}
/* ── footer ── */
.footer{{
  display:flex;align-items:center;justify-content:space-between;
  color:var(--muted);font-size:10px;margin-top:16px;flex-wrap:wrap;gap:8px
}}
.footer-dot{{width:6px;height:6px;border-radius:50%;background:var(--green);
  display:inline-block;margin-right:6px;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">
      <span class="logo-badge">TRIM</span>
      <h1>Token Routing Intelligence Middleware</h1>
    </div>
    <div class="sub">Intercepting large file reads &mdash; routing to cheap LLMs</div>
  </div>
  <div class="header-meta">
    <div class="model-badge">worker: {model}</div>
    <div class="sub" style="margin-top:6px">auto-refreshes every 30s</div>
  </div>
</header>

<div class="cards">
  <div class="card accent">
    <div class="card-label">Delegations</div>
    <div class="card-value" id="v-delegations">—</div>
    <div class="card-sub" id="v-delegations-sub"></div>
  </div>
  <div class="card">
    <div class="card-label">Tokens Intercepted</div>
    <div class="card-value" id="v-tokens">—</div>
    <div class="card-sub" id="v-tokens-sub"></div>
  </div>
  <div class="card green">
    <div class="card-label">LLM Cost</div>
    <div class="card-value" id="v-cost">—</div>
    <div class="card-sub" id="v-cost-sub"></div>
  </div>
  <div class="card amber">
    <div class="card-label">Avg Latency</div>
    <div class="card-value" id="v-latency">—</div>
    <div class="card-sub">per delegation</div>
  </div>
</div>

<div class="section">
  <div class="section-header">
    <span class="section-title">Delegations by Day</span>
  </div>
  <canvas id="chart"></canvas>
  <p id="chart-empty" class="empty" style="display:none">No data yet — read a file with &gt;350 lines to see activity</p>
</div>

<div class="section">
  <div class="section-header">
    <span class="section-title">Cost by Model</span>
  </div>
  <div class="model-grid" id="model-grid"></div>
  <p id="model-empty" class="empty" style="display:none">No model data yet</p>
</div>

<div class="section">
  <div class="section-header">
    <span class="section-title">Recent Delegations</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Time (UTC)</th><th>File</th><th>Lines</th>
        <th>Latency</th><th>In Tok</th><th>Out Tok</th><th>Cost</th><th>Mode</th><th>Model</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<div class="footer">
  <span><span class="footer-dot"></span>TRIM worker live</span>
  <span id="countdown"></span>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script>
const S = {stats_json};
const RECENT = {recent_json};
const DAILY = {daily_json};

// ── helpers ──────────────────────────────────────────────────────────────────
function fmtTok(n) {{
  if (n >= 1e6) return (n/1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n/1e3).toFixed(1) + "K";
  return n.toLocaleString();
}}
function fmtCost(usd) {{
  if (usd === null || usd === undefined) return "?";
  if (usd === 0) return "$0.00";
  if (usd < 0.0001) return "<$0.0001";
  if (usd < 0.01)   return "$" + usd.toFixed(4);
  return "$" + usd.toFixed(4);
}}

// ── stat cards ───────────────────────────────────────────────────────────────
document.getElementById("v-delegations").textContent = S.total_delegations.toLocaleString();
const days = Object.keys(S.delegations_by_day || {{}});
if (days.length > 1) {{
  const today = S.delegations_by_day[days[days.length-1]] || 0;
  document.getElementById("v-delegations-sub").textContent = "+" + today + " today";
}}

document.getElementById("v-tokens").textContent = fmtTok(S.total_input_tokens);
document.getElementById("v-tokens-sub").textContent =
  fmtTok(S.total_output_tokens) + " out";

document.getElementById("v-cost").textContent = fmtCost(S.total_llm_cost_usd);
const knownCost = S.has_unknown_pricing
  ? "⚠ some models have estimated pricing"
  : "based on published API rates";
document.getElementById("v-cost-sub").textContent = knownCost;

document.getElementById("v-latency").textContent =
  S.avg_latency_ms ? Math.round(S.avg_latency_ms) + " ms" : "—";

// ── bar chart ────────────────────────────────────────────────────────────────
const chartDays = Object.keys(DAILY);
if (chartDays.length) {{
  new Chart(document.getElementById("chart"), {{
    type: "bar",
    data: {{
      labels: chartDays,
      datasets: [{{
        data: Object.values(DAILY),
        backgroundColor: "rgba(77,157,224,.18)",
        borderColor: "#4d9de0",
        borderWidth: 1,
        borderRadius: 3,
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ ticks: {{ color: "#5a7a9a" }}, grid: {{ color: "#111620" }} }},
        y: {{ ticks: {{ color: "#5a7a9a", precision: 0 }},
              grid: {{ color: "#111620" }}, beginAtZero: true }}
      }}
    }}
  }});
}} else {{
  document.getElementById("chart").style.display = "none";
  document.getElementById("chart-empty").style.display = "block";
}}

// ── model cards ──────────────────────────────────────────────────────────────
const grid = document.getElementById("model-grid");
const models = S.model_breakdown || {{}};
const maxCost = Math.max(...Object.values(models).map(m => m.cost_usd || 0), 0.00001);

if (Object.keys(models).length) {{
  Object.entries(models)
    .sort((a,b) => (b[1].cost_usd||0) - (a[1].cost_usd||0))
    .forEach(([name, m]) => {{
      const pct = Math.round(((m.cost_usd||0) / maxCost) * 100);
      const el = document.createElement("div");
      el.className = "model-card";
      el.innerHTML = `
        <div class="model-name">${{name}}</div>
        <div class="model-stats">
          <div class="mstat">
            <div class="mstat-label">Delegations</div>
            <div class="mstat-value">${{m.count.toLocaleString()}}</div>
          </div>
          <div class="mstat">
            <div class="mstat-label">LLM Cost</div>
            <div class="mstat-value" style="color:var(--green)">${{fmtCost(m.cost_usd)}}</div>
          </div>
          <div class="mstat">
            <div class="mstat-label">In Tokens</div>
            <div class="mstat-value">${{fmtTok(m.input_tokens)}}</div>
          </div>
          <div class="mstat">
            <div class="mstat-label">Out Tokens</div>
            <div class="mstat-value">${{fmtTok(m.output_tokens)}}</div>
          </div>
        </div>
        <div class="model-cost-bar">
          <div class="model-cost-bar-fill" style="width:${{pct}}%"></div>
        </div>
        ${{!m.known_price ? '<div class="unknown-price">⚠ pricing estimated</div>' : ''}}
      `;
      grid.appendChild(el);
    }});
}} else {{
  grid.style.display = "none";
  document.getElementById("model-empty").style.display = "block";
}}

// ── recent table ─────────────────────────────────────────────────────────────
const tbody = document.getElementById("tbody");
if (RECENT.length) {{
  RECENT.forEach(r => {{
    const short = r.file.length > 44 ? "\u2026" + r.file.slice(-41) : r.file;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="num">${{r.ts_human}}</td>
      <td class="path" title="${{r.file.replace(/"/g,"&quot;")}}">${{short}}</td>
      <td class="num">${{r.lines.toLocaleString()}}</td>
      <td class="num">${{r.latency_ms}} ms</td>
      <td class="num">${{r.input_tokens.toLocaleString()}}</td>
      <td class="num">${{r.output_tokens.toLocaleString()}}</td>
      <td class="cost-cell">${{fmtCost(r.cost_usd)}}</td>
      <td><span class="tag">${{r.mode}}</span></td>
      <td><span class="tag">${{r.model}}</span></td>`;
    tbody.appendChild(tr);
  }});
}} else {{
  tbody.innerHTML =
    '<tr><td colspan="9" class="empty">No delegations yet — ask Claude to read a file &gt;350 lines</td></tr>';
}}

// ── countdown ────────────────────────────────────────────────────────────────
let t = 30;
const cdEl = document.getElementById("countdown");
const tick = () => {{
  cdEl.textContent = "refresh in " + t-- + "s";
  if (t < 0) location.reload(); else setTimeout(tick, 1000);
}};
tick();
</script>
</body>
</html>"""


def read_metrics() -> list[dict[str, Any]]:
    """Read and parse all JSONL records. Never raises — returns [] on any error."""
    path = Path(config.SHUNT_METRICS_FILE)
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return records


def compute_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive all dashboard statistics from raw metric records."""
    empty = {
        "total_delegations": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_llm_cost_usd": 0.0,
        "has_unknown_pricing": False,
        "avg_latency_ms": 0.0,
        "model_breakdown": {},
        "delegations_by_day": {},
        "recent": [],
    }
    if not records:
        return empty

    total_in = sum(r.get("input_tokens", 0) for r in records)
    total_out = sum(r.get("output_tokens", 0) for r in records)

    latencies = [r["latency_ms"] for r in records if "latency_ms" in r]
    avg_latency = mean(latencies) if latencies else 0.0

    # Per-model aggregation
    model_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "known_price": True}
    )
    day_counts: dict[str, int] = defaultdict(int)
    total_cost = 0.0
    has_unknown = False

    for r in records:
        model = r.get("model", "unknown")
        in_tok = r.get("input_tokens", 0)
        out_tok = r.get("output_tokens", 0)
        cost = _model_cost_usd(model, in_tok, out_tok)
        known = _is_known_model(model)

        model_data[model]["count"] += 1
        model_data[model]["input_tokens"] += in_tok
        model_data[model]["output_tokens"] += out_tok
        model_data[model]["cost_usd"] += cost
        if not known:
            model_data[model]["known_price"] = False
            has_unknown = True

        total_cost += cost

        ts = r.get("ts")
        if ts is not None:
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            day_counts[day] += 1

    # Round model costs
    for m in model_data.values():
        m["cost_usd"] = round(m["cost_usd"], 6)

    recent_formatted = []
    for r in reversed(records[-20:]):
        ts = r.get("ts")
        dt_str = (
            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
            if ts else "—"
        )
        in_tok = r.get("input_tokens", 0)
        out_tok = r.get("output_tokens", 0)
        model = r.get("model", "—")
        recent_formatted.append({
            "ts_human": dt_str,
            "file": r.get("file", "—"),
            "lines": r.get("lines", 0),
            "latency_ms": round(r.get("latency_ms", 0), 1),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": round(_model_cost_usd(model, in_tok, out_tok), 6),
            "model": model,
            "mode": r.get("mode", "—"),
        })

    return {
        "total_delegations": len(records),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_llm_cost_usd": round(total_cost, 6),
        "has_unknown_pricing": has_unknown,
        "avg_latency_ms": round(avg_latency, 1),
        "model_breakdown": {k: dict(v) for k, v in model_data.items()},
        "delegations_by_day": dict(sorted(day_counts.items())),
        "recent": recent_formatted,
    }


def render_html(stats: dict[str, Any]) -> str:
    return _HTML.format(
        stats_json=json.dumps(stats),
        recent_json=json.dumps(stats["recent"]),
        daily_json=json.dumps(stats["delegations_by_day"]),
        model=config.WORKER_MODEL,
    )
