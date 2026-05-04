#!/usr/bin/env python3
"""
AAPL GEX Dashboard - Data Fetcher
Pulls free delayed options quotes from CBOE and computes Gamma Exposure.

Usage:
    python3 aapl_gex_fetcher.py [TICKER] [--days N]

    TICKER: stock ticker (default: AAPL)
    --days: number of days forward to consider expirations (default: 28)

Outputs: gex_data.json (consumed by the HTML dashboard)
"""

import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta


def fetch_cboe_chain(ticker: str) -> dict:
    """Pull free delayed options chain from CBOE."""
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def parse_occ_symbol(sym: str, ticker: str):
    """Parse OCC option symbol: {TICKER}{YY}{MM}{DD}{C|P}{strike*1000:08d}"""
    pattern = rf"{ticker}(\d{{2}})(\d{{2}})(\d{{2}})([CP])(\d{{8}})"
    m = re.match(pattern, sym)
    if not m:
        return None
    yy, mm, dd, cp, strike_raw = m.groups()
    expiry = f"20{yy}-{mm}-{dd}"
    strike = int(strike_raw) / 1000
    return expiry, cp, strike


def compute_gex(raw: dict, ticker: str, days_forward: int = 28) -> dict:
    """Compute GEX from raw CBOE chain."""
    spot = raw["data"]["current_price"]
    iv30 = raw["data"].get("iv30", 0)
    timestamp = raw.get("timestamp", "")

    today = datetime.utcnow().date()
    end_date = today + timedelta(days=days_forward)

    options = raw["data"]["options"]

    # Aggregations
    call_gex_strike = defaultdict(float)
    put_gex_strike = defaultdict(float)
    oi_call_strike = defaultdict(int)
    oi_put_strike = defaultdict(int)

    # Per-expiry breakdowns for richer dashboard
    by_expiry = defaultdict(lambda: {"call_gex": 0.0, "put_gex": 0.0, "call_oi": 0, "put_oi": 0})

    contracts_used = 0
    for opt in options:
        parsed = parse_occ_symbol(opt["option"], ticker)
        if not parsed:
            continue
        exp_str, cp, strike = parsed
        try:
            exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if exp < today or exp > end_date:
            continue

        gamma = opt.get("gamma") or 0
        oi = opt.get("open_interest") or 0
        if gamma == 0 or oi == 0:
            continue

        # Standard naive GEX: dealers assumed short customer flow
        # GEX per 1% move (in dollars)
        gex = gamma * oi * 100 * spot * spot * 0.01

        if cp == "C":
            call_gex_strike[strike] += gex
            oi_call_strike[strike] += oi
            by_expiry[exp_str]["call_gex"] += gex
            by_expiry[exp_str]["call_oi"] += oi
        else:
            put_gex_strike[strike] += gex  # store positive, sign-flip in net
            oi_put_strike[strike] += oi
            by_expiry[exp_str]["put_gex"] += gex
            by_expiry[exp_str]["put_oi"] += oi

        contracts_used += 1

    # Build strike rows
    all_strikes = sorted(set(list(call_gex_strike.keys()) + list(put_gex_strike.keys())))
    strike_rows = []
    for s in all_strikes:
        call_g = call_gex_strike.get(s, 0)
        put_g = put_gex_strike.get(s, 0)
        net_g = call_g - put_g  # puts contribute negatively
        strike_rows.append({
            "strike": s,
            "call_gex": call_g,
            "put_gex": -put_g,  # display as negative
            "net_gex": net_g,
            "call_oi": oi_call_strike.get(s, 0),
            "put_oi": oi_put_strike.get(s, 0),
        })

    # Find walls and flip
    call_wall = max(strike_rows, key=lambda r: r["call_gex"]) if strike_rows else None
    put_wall_row = min(strike_rows, key=lambda r: r["put_gex"]) if strike_rows else None

    # Gamma flip = strike where cumulative net GEX crosses zero (scanning up)
    flip = None
    cum = 0.0
    for row in strike_rows:
        prev = cum
        cum += row["net_gex"]
        if prev < 0 <= cum:
            flip = row["strike"]
            break

    total_call_gex = sum(r["call_gex"] for r in strike_rows)
    total_put_gex = sum(r["put_gex"] for r in strike_rows)
    net_gex = total_call_gex + total_put_gex

    # Build expiry rows
    expiry_rows = []
    for exp_str, vals in sorted(by_expiry.items()):
        expiry_rows.append({
            "expiry": exp_str,
            "call_gex": vals["call_gex"],
            "put_gex": -vals["put_gex"],
            "net_gex": vals["call_gex"] - vals["put_gex"],
            "call_oi": vals["call_oi"],
            "put_oi": vals["put_oi"],
        })

    return {
        "ticker": ticker,
        "spot": spot,
        "iv30": iv30,
        "timestamp": timestamp,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "contracts_used": contracts_used,
        "days_forward": days_forward,
        "totals": {
            "call_gex": total_call_gex,
            "put_gex": total_put_gex,
            "net_gex": net_gex,
            "regime": "positive" if net_gex > 0 else "negative",
        },
        "call_wall": {"strike": call_wall["strike"], "gex": call_wall["call_gex"]} if call_wall else None,
        "put_wall": {"strike": put_wall_row["strike"], "gex": put_wall_row["put_gex"]} if put_wall_row else None,
        "gamma_flip": flip,
        "strikes": strike_rows,
        "expiries": expiry_rows,
    }


def main():
    ticker = "AAPL"
    days = 28
    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        ticker = args[0].upper()
        args = args[1:]
    if "--days" in args:
        i = args.index("--days")
        days = int(args[i + 1])

    print(f"Fetching {ticker} options chain from CBOE...")
    raw = fetch_cboe_chain(ticker)

    print(f"Computing GEX (next {days} days)...")
    result = compute_gex(raw, ticker, days)

    out = "gex_data.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  {ticker} GEX SNAPSHOT")
    print(f"{'=' * 60}")
    print(f"  Spot:          ${result['spot']:.2f}")
    print(f"  IV30:          {result['iv30']:.2f}%")
    print(f"  Net GEX:       ${result['totals']['net_gex'] / 1e6:+.1f}M")
    print(f"  Regime:        {result['totals']['regime'].upper()}")
    print(f"  Call Wall:     ${result['call_wall']['strike']:.0f}")
    print(f"  Put Wall:      ${result['put_wall']['strike']:.0f}")
    print(f"  Gamma Flip:    ${result['gamma_flip']}")
    print(f"  Contracts:     {result['contracts_used']:,}")
    print(f"\n  Saved to:      {out}")


if __name__ == "__main__":
    main()
