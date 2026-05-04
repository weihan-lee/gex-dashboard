#!/usr/bin/env python3
"""
GEX Dashboard - One-step refresh.

Fetches the latest CBOE delayed quotes for AAPL (or any ticker),
recomputes Gamma Exposure, and rebuilds the HTML dashboard.

Usage:
    python3 refresh_dashboard.py [TICKER]

    Default ticker: AAPL

Outputs:
    gex_data.json              - raw computed GEX data
    aapl_gex_dashboard.html    - interactive dashboard (open in browser)
"""

import json
import os
import sys

from aapl_gex_fetcher import fetch_cboe_chain, compute_gex


def filter_for_dashboard(data: dict, range_pct: float = 0.10) -> dict:
    """Keep only strikes within +/- range_pct of spot for the dashboard."""
    spot = data["spot"]
    cutoff = spot * range_pct
    out = dict(data)
    out["strikes"] = [s for s in data["strikes"] if abs(s["strike"] - spot) <= cutoff]
    return out


def build_dashboard(data: dict, template_path: str, output_path: str) -> None:
    with open(template_path) as f:
        template = f.read()
    payload = json.dumps(data, default=str)
    html = template.replace("__DATA_PLACEHOLDER__", payload)
    with open(output_path, "w") as f:
        f.write(html)


def main():
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"

    print(f"\n[1/3] Fetching {ticker} chain from CBOE...")
    raw = fetch_cboe_chain(ticker)

    print(f"[2/3] Computing GEX...")
    result = compute_gex(raw, ticker, days_forward=28)

    # Save full data for reference
    with open("gex_data.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    # Build filtered version for the dashboard (smaller, focused)
    dashboard_data = filter_for_dashboard(result, range_pct=0.10)

    print(f"[3/3] Building dashboard...")
    template = "gex_dashboard_template.html"
    if not os.path.exists(template):
        print(f"  ERROR: {template} not found in current directory")
        sys.exit(1)

    output = f"{ticker.lower()}_gex_dashboard.html"
    build_dashboard(dashboard_data, template, output)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  {ticker} GEX SNAPSHOT")
    print(f"{'=' * 60}")
    print(f"  Spot:       ${result['spot']:.2f}")
    print(f"  IV30:       {result['iv30']:.2f}%")
    print(f"  Net GEX:    ${result['totals']['net_gex'] / 1e6:+.1f}M ({result['totals']['regime']})")
    print(f"  Call Wall:  ${result['call_wall']['strike']:.0f}")
    print(f"  Put Wall:   ${result['put_wall']['strike']:.0f}")
    print(f"  Flip:       ${result['gamma_flip']}")
    print(f"\n  Dashboard:  {os.path.abspath(output)}")
    print(f"  Open in browser to view.\n")


if __name__ == "__main__":
    main()
