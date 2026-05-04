#!/usr/bin/env python3
"""
Build dashboards for multiple tickers + an index page.
Designed to run in GitHub Actions and publish to GitHub Pages.

Usage:
    python3 build_all.py
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

from aapl_gex_fetcher import fetch_cboe_chain, compute_gex
from refresh_dashboard import filter_for_dashboard, build_dashboard

# Display timezone for the dashboard (GMT+8, Malaysia/Singapore time)
DISPLAY_TZ = timezone(timedelta(hours=8))
DISPLAY_TZ_LABEL = "MYT"

# Tickers to track. Edit this list to add/remove.
TICKERS = ["AAPL", "SPY", "QQQ", "NVDA", "TSLA", "MSFT"]

# GitHub repo (shown in footer). Format: username/repo
GITHUB_REPO = "weihan-lee/gex-dashboard"

OUTPUT_DIR = "site"
TEMPLATE = "gex_dashboard_template.html"


def build_one(ticker: str) -> dict | None:
    """Build dashboard for one ticker. Returns summary dict or None on failure."""
    try:
        print(f"  Fetching {ticker}...", flush=True)
        raw = fetch_cboe_chain(ticker)
        result = compute_gex(raw, ticker, days_forward=28)
        dashboard_data = filter_for_dashboard(result, range_pct=0.10)

        out_path = os.path.join(OUTPUT_DIR, f"{ticker.lower()}.html")
        build_dashboard(dashboard_data, TEMPLATE, out_path)

        return {
            "ticker": ticker,
            "spot": result["spot"],
            "iv30": result["iv30"],
            "net_gex": result["totals"]["net_gex"],
            "regime": result["totals"]["regime"],
            "call_wall": result["call_wall"]["strike"],
            "put_wall": result["put_wall"]["strike"],
            "gamma_flip": result["gamma_flip"],
            "url": f"{ticker.lower()}.html",
        }
    except Exception as e:
        print(f"  ERROR on {ticker}: {e}", flush=True)
        return None


def build_index(summaries: list[dict]) -> None:
    """Build the index page that lists all tickers with their headline numbers."""
    valid = [s for s in summaries if s is not None]
    now = datetime.now(DISPLAY_TZ).strftime(f"%Y-%m-%d %H:%M {DISPLAY_TZ_LABEL}")

    rows_html = ""
    for s in valid:
        net_m = s["net_gex"] / 1e6
        net_class = "pos" if net_m >= 0 else "neg"
        net_sign = "+" if net_m >= 0 else ""
        regime_class = "go" if s["regime"] == "positive" else "stop"

        rows_html += f"""
        <a class="ticker-card" href="{s['url']}">
          <div class="ticker-row">
            <div class="ticker-name">{s['ticker']}</div>
            <div class="ticker-spot">${s['spot']:.2f}</div>
          </div>
          <div class="ticker-row">
            <div class="ticker-meta">IV {s['iv30']:.1f}%</div>
            <div class="ticker-gex {net_class}">{net_sign}{net_m:.0f}M</div>
          </div>
          <div class="ticker-row dim">
            <div>Wall ${s['call_wall']:.0f} · Flip ${s['gamma_flip'] or '-'}</div>
            <div class="regime {regime_class}">{s['regime'].upper()}</div>
          </div>
        </a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GEX TERMINAL</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Fraunces:opsz,wght@9..144,300;9..144,400;9..144,600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0a0d0a; --bg-2: #111611; --bg-3: #161d16;
    --grid: #1f2a1f; --phosphor: #4ade80; --phosphor-dim: #166534;
    --amber: #fbbf24; --red: #f87171;
    --text: #d4e8d4; --text-dim: #6b7d6b; --text-muted: #4a5a4a;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: 'JetBrains Mono', monospace; font-size: 13px;
    min-height: 100vh;
    background-image: radial-gradient(ellipse at top, rgba(74, 222, 128, 0.04), transparent 50%);
  }}
  body::before {{
    content: ''; position: fixed; inset: 0;
    background: repeating-linear-gradient(0deg, transparent 0px, transparent 2px,
      rgba(74, 222, 128, 0.015) 2px, rgba(74, 222, 128, 0.015) 3px);
    pointer-events: none; z-index: 1000;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 32px 20px; }}
  .header {{
    border-bottom: 1px solid var(--grid); padding-bottom: 24px; margin-bottom: 32px;
    display: grid; grid-template-columns: 1fr auto; gap: 20px; align-items: end;
  }}
  .eyebrow {{
    font-size: 10px; letter-spacing: 0.3em; color: var(--phosphor);
    text-transform: uppercase; margin-bottom: 12px;
  }}
  h1 {{
    font-family: 'Fraunces', serif; font-weight: 300;
    font-size: clamp(40px, 7vw, 80px); line-height: 0.9; letter-spacing: -0.04em;
  }}
  h1 .accent {{ font-style: italic; font-weight: 400; color: var(--phosphor); }}
  .meta {{ text-align: right; font-size: 11px; color: var(--text-dim); line-height: 1.8; }}
  .meta .live {{
    display: inline-flex; align-items: center; gap: 6px; color: var(--amber);
  }}
  .meta .live::before {{
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: var(--amber); box-shadow: 0 0 8px var(--amber);
    animation: pulse 2s ease-in-out infinite;
  }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
  }}
  .ticker-card {{
    display: block; background: var(--bg-2); border: 1px solid var(--grid);
    padding: 20px; text-decoration: none; color: var(--text);
    transition: all 0.15s; position: relative;
  }}
  .ticker-card:hover {{
    border-color: var(--phosphor-dim); background: var(--bg-3);
    transform: translateX(2px);
  }}
  .ticker-row {{
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 6px;
  }}
  .ticker-row.dim {{ font-size: 10px; color: var(--text-muted); margin-top: 8px; }}
  .ticker-name {{
    font-family: 'Fraunces', serif; font-size: 28px; font-weight: 400;
    letter-spacing: -0.02em;
  }}
  .ticker-spot {{
    font-family: 'Fraunces', serif; font-size: 22px; color: var(--text);
    font-variant-numeric: tabular-nums;
  }}
  .ticker-meta {{ font-size: 11px; color: var(--text-dim); letter-spacing: 0.05em; }}
  .ticker-gex {{
    font-family: 'Fraunces', serif; font-size: 18px;
    font-variant-numeric: tabular-nums;
  }}
  .pos {{ color: var(--phosphor); }}
  .neg {{ color: var(--red); }}
  .regime {{ font-weight: 500; letter-spacing: 0.15em; }}
  .regime.go {{ color: var(--phosphor); }}
  .regime.stop {{ color: var(--red); }}
  .footer {{
    margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--grid);
    font-size: 10px; color: var(--text-muted); line-height: 1.8; letter-spacing: 0.05em;
  }}
  @media (max-width: 600px) {{
    .header {{ grid-template-columns: 1fr; }}
    .meta {{ text-align: left; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <div class="eyebrow">// GAMMA EXPOSURE TERMINAL</div>
      <h1>GEX <span class="accent">/watch</span></h1>
    </div>
    <div class="meta">
      <div class="live">UPDATED {now}</div>
      <div>SOURCE: CBOE DELAYED OPRA</div>
      <div>{len(valid)} TICKERS · 30MIN OPEN · HOURLY OTHERWISE</div>
    </div>
  </div>

  <div class="grid">{rows_html}
  </div>

  <div class="footer">
    Tap any ticker for full GEX dashboard · Naive GEX model · Not investment advice<br>
    Source: <a href="https://github.com/{GITHUB_REPO}" style="color:var(--text-dim);text-decoration:underline">github.com/{GITHUB_REPO}</a> · Built with GitHub Actions
  </div>
</div>
</body>
</html>"""

    with open(os.path.join(OUTPUT_DIR, "index.html"), "w") as f:
        f.write(html)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Building dashboards for {len(TICKERS)} tickers...\n", flush=True)
    summaries = []
    for t in TICKERS:
        s = build_one(t)
        summaries.append(s)

    print(f"\nBuilding index page...", flush=True)
    build_index(summaries)

    valid = [s for s in summaries if s is not None]
    print(f"\nDone. Built {len(valid)}/{len(TICKERS)} dashboards in '{OUTPUT_DIR}/'.")
    for s in valid:
        net_m = s["net_gex"] / 1e6
        sign = "+" if net_m >= 0 else ""
        print(f"  {s['ticker']:<6} ${s['spot']:>8.2f}  GEX {sign}{net_m:>6.0f}M  Wall ${s['call_wall']:.0f}  Flip ${s['gamma_flip']}")


if __name__ == "__main__":
    main()
