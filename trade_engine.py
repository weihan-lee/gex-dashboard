#!/usr/bin/env python3
"""
Trade Engine — recommends calendar spreads using live CBOE option prices.

Logic:
1. Determine if conditions favor a calendar (verdict from GEX analysis)
2. Pick optimal strike (call wall)
3. Find the two nearest weekly expiries with ~7 day gap
4. Use real bid/ask mid prices from the chain (no BS approximation)
5. Compute entry debit, stop loss, take profit, P/L scenarios
6. Flag GEX-based events (gamma flip breach, call wall pin)
"""

import re
from datetime import datetime, date, timedelta


def previous_trading_day(d: date) -> date:
    """Return the previous US trading day (Mon-Fri, no holidays)."""
    prev = d - timedelta(days=1)
    # If Saturday, go back to Friday; if Sunday, go back to Friday
    while prev.weekday() >= 5:  # 5=Sat, 6=Sun
        prev -= timedelta(days=1)
    return prev


def parse_occ_symbol(sym: str, ticker: str):
    """Parse OCC option symbol back to (expiry, cp, strike)."""
    pattern = rf"{ticker}(\d{{2}})(\d{{2}})(\d{{2}})([CP])(\d{{8}})"
    m = re.match(pattern, sym)
    if not m:
        return None
    yy, mm, dd, cp, strike_raw = m.groups()
    expiry = f"20{yy}-{mm}-{dd}"
    strike = int(strike_raw) / 1000
    return expiry, cp, strike


def index_chain(raw: dict, ticker: str) -> dict:
    """
    Index the option chain for fast lookups.
    Returns: { (expiry_str, cp, strike): option_dict }
    """
    idx = {}
    for opt in raw["data"]["options"]:
        parsed = parse_occ_symbol(opt["option"], ticker)
        if not parsed:
            continue
        idx[parsed] = opt
    return idx


def get_mid(opt: dict) -> float | None:
    """Compute mid price from bid/ask. Returns None if invalid."""
    bid = opt.get("bid", 0)
    ask = opt.get("ask", 0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2


def find_weekly_expiries(chain_idx: dict, today: date, gap_days: int = 7,
                          spot: float | None = None) -> tuple | None:
    """
    Find two expiries: front >= 5 DTE, back ~= front + gap_days.
    Only considers expiries with liquid ATM calls (valid bid/ask).
    Returns (front_expiry_str, back_expiry_str, front_dte, back_dte) or None.
    """
    # Build set of expiries that have at least one liquid ATM-ish call
    liquid_expiries = set()
    for (exp, cp, strike), opt in chain_idx.items():
        if cp != "C":
            continue
        if spot is not None and abs(strike - spot) > spot * 0.05:
            continue
        if get_mid(opt) is not None:
            liquid_expiries.add(exp)

    expiries = sorted(liquid_expiries)
    if not expiries:
        return None

    # Front: closest expiry >= 5 DTE
    front = None
    for exp_str in expiries:
        exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
        dte = (exp - today).days
        if dte >= 5:
            front = (exp_str, exp, dte)
            break
    if not front:
        return None

    front_exp = front[1]
    target_back_date = front_exp + timedelta(days=gap_days)

    # Back: closest expiry to (front + gap_days)
    best_back = None
    best_diff = 99
    for exp_str in expiries:
        exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
        if exp <= front_exp:
            continue
        diff = abs((exp - target_back_date).days)
        if diff < best_diff:
            best_diff = diff
            best_back = (exp_str, exp, (exp - today).days)

    if not best_back or best_diff > 7:
        return None

    return (front[0], best_back[0], front[2], best_back[2])


def find_strike_with_liquid_options(chain_idx: dict, target_strike: float,
                                    front_exp: str, back_exp: str) -> float | None:
    """
    Given a target strike (e.g. call wall), find the closest strike that has
    valid bid/ask on BOTH the front and back expiry.
    """
    candidates = []
    for (exp, cp, strike), opt in chain_idx.items():
        if cp != "C":
            continue
        if exp not in (front_exp, back_exp):
            continue
        if get_mid(opt) is not None:
            candidates.append((exp, strike))

    front_strikes = sorted(set(s for (e, s) in candidates if e == front_exp))
    back_strikes = sorted(set(s for (e, s) in candidates if e == back_exp))
    valid = sorted(set(front_strikes) & set(back_strikes))

    if not valid:
        return None

    return min(valid, key=lambda s: abs(s - target_strike))


def compute_breakevens(spot: float, strike: float, debit: float,
                       front_iv: float, front_dte: int) -> tuple[float, float]:
    """
    Approximate calendar spread breakevens.

    The classic approximation: at front-month expiry, breakevens are where
    long-call value equals the debit paid. For a back-month call with same
    strike, value at front expiry is roughly the residual time value.

    Use the rough rule: BE width ~= 1.5x the debit on each side for a
    7-day calendar at moderate IV. This is a heuristic — real breakevens
    depend on IV at front expiry and term structure.
    """
    # Quick heuristic: BE distance from strike ~= sqrt(debit/strike) * scaling
    # More robust: use ATM straddle approximation
    # For 7-day calendars near ATM, empirical BE width is roughly 2-3x debit
    be_width = max(debit * 2.5, spot * 0.015)  # at least 1.5% of spot
    return (strike - be_width, strike + be_width)


def estimate_max_profit(debit: float, front_iv: float, back_iv: float,
                        front_dte: int, back_dte: int) -> float:
    """
    Estimate max profit at front-month expiry assuming spot pins at strike.

    At pin, short call expires worthless ($0).
    Long back-month call at strike has value = back-leg time value with
    (back_dte - front_dte) days remaining.

    Rough rule: max profit ~= 50-100% of debit for ATM 7-day calendars
    when IV is stable. Use 80% as a conservative estimate.
    """
    # Conservative max profit assumption
    return debit * 0.85


def evaluate_verdict(ticker_data: dict) -> dict:
    """
    Determine trade verdict based on GEX + IV.
    Returns dict with: action ('trade'|'reduce'|'skip'), reasons, score.
    """
    spot = ticker_data["spot"]
    iv30 = ticker_data["iv30"]
    net_gex = ticker_data["totals"]["net_gex"]
    regime = ticker_data["totals"]["regime"]
    flip = ticker_data["gamma_flip"]
    call_wall = ticker_data["call_wall"]["strike"]

    # Treat flip as not blocking if it sits AT or above the call wall
    # (degenerate case — flip detector found the largest positive bar)
    flip_blocks = (flip is not None) and (spot < flip) and (flip < call_wall)
    above_flip = not flip_blocks
    positive_gex = net_gex > 0
    low_iv = iv30 < 30
    moderate_iv = iv30 < 40

    reasons = []
    if positive_gex:
        reasons.append(f"Positive GEX (${net_gex/1e6:+.0f}M)")
    else:
        reasons.append(f"Negative GEX (${net_gex/1e6:+.0f}M)")

    if above_flip:
        reasons.append(f"Above gamma flip (${flip})")
    else:
        reasons.append(f"Below gamma flip (${flip})")

    if low_iv:
        reasons.append(f"Low IV ({iv30:.1f}%)")
    elif moderate_iv:
        reasons.append(f"Moderate IV ({iv30:.1f}%)")
    else:
        reasons.append(f"Elevated IV ({iv30:.1f}%)")

    distance_to_wall = abs(spot - call_wall) / spot
    if distance_to_wall < 0.02:
        reasons.append(f"Near call wall (${call_wall:.0f})")

    if positive_gex and above_flip and low_iv:
        action = "trade"
    elif not above_flip or iv30 > 45:
        action = "skip"
    else:
        action = "reduce"

    return {"action": action, "reasons": reasons}


def build_trade_recommendation(ticker_data: dict, raw_chain: dict, ticker: str,
                                today: date | None = None,
                                events: list[dict] | None = None) -> dict | None:
    """
    Build a complete calendar spread trade recommendation.
    Returns None if conditions don't support a trade.

    If `events` is provided (list of macro/earnings events from events_engine),
    the verdict will be downgraded if critical events fall in the window.
    """
    if today is None:
        today = date.today()

    verdict = evaluate_verdict(ticker_data)
    if verdict["action"] == "skip":
        return {"verdict": verdict, "trade": None, "events": events or []}

    chain_idx = index_chain(raw_chain, ticker)
    spot = ticker_data["spot"]
    call_wall = ticker_data["call_wall"]["strike"]
    flip = ticker_data["gamma_flip"]
    iv30 = ticker_data["iv30"]

    # Find expiry pair
    expiries = find_weekly_expiries(chain_idx, today, gap_days=7, spot=spot)
    if not expiries:
        return {"verdict": verdict, "trade": None,
                "error": "No suitable weekly expiries found"}

    front_exp, back_exp, front_dte, back_dte = expiries

    # Pick strike — prefer call wall if liquid, else fallback to ATM,
    # else nearest strike with valid bid/ask on both legs
    chosen_strike = find_strike_with_liquid_options(chain_idx, call_wall,
                                                     front_exp, back_exp)
    if not chosen_strike:
        chosen_strike = find_strike_with_liquid_options(chain_idx, spot,
                                                         front_exp, back_exp)
    if not chosen_strike:
        # Last resort: scan all strikes within +/- 5% of spot
        for offset_pct in [0.01, 0.02, 0.03, 0.05]:
            target = spot * (1 + offset_pct)
            chosen_strike = find_strike_with_liquid_options(chain_idx, target,
                                                             front_exp, back_exp)
            if chosen_strike:
                break
    if not chosen_strike:
        return {"verdict": verdict, "trade": None,
                "error": "No liquid strikes for selected expiries"}

    # Get the legs
    front_leg = chain_idx.get((front_exp, "C", chosen_strike))
    back_leg = chain_idx.get((back_exp, "C", chosen_strike))
    if not front_leg or not back_leg:
        return {"verdict": verdict, "trade": None,
                "error": "Missing leg data"}

    front_mid = get_mid(front_leg)
    back_mid = get_mid(back_leg)
    if front_mid is None or back_mid is None:
        return {"verdict": verdict, "trade": None,
                "error": "Invalid bid/ask on legs"}

    debit = back_mid - front_mid
    if debit <= 0:
        return {"verdict": verdict, "trade": None,
                "error": "Negative debit (back leg cheaper than front)"}

    # Per-contract dollars (×100 multiplier)
    debit_dollars = debit * 100
    max_loss = debit_dollars  # absolute worst case
    stop_loss_value = debit * 0.5  # 50% of debit
    stop_loss_dollars = max_loss * 0.5  # $ loss at stop
    take_profit_value = debit * 1.5  # 150% of debit (50% gain)
    take_profit_dollars = (take_profit_value - debit) * 100  # $ gain at target
    max_profit_estimate = estimate_max_profit(debit, iv30/100, iv30/100,
                                               front_dte, back_dte)
    max_profit_dollars = max_profit_estimate * 100

    # Breakevens
    be_low, be_high = compute_breakevens(spot, chosen_strike, debit,
                                          iv30/100, front_dte)

    # Front leg IV/OI for context
    front_iv = front_leg.get("iv", 0)
    back_iv = back_leg.get("iv", 0)
    front_oi = front_leg.get("open_interest", 0)
    back_oi = back_leg.get("open_interest", 0)

    # GEX-based event flags
    gex_events = []
    if flip and be_low < flip:
        gex_events.append({
            "type": "warning",
            "label": f"Lower BE (${be_low:.2f}) is below gamma flip (${flip:.0f})",
            "detail": "If price reaches lower BE, regime turns amplifying. Tighten stop."
        })
    elif flip:
        gex_events.append({
            "type": "ok",
            "label": f"Gamma flip (${flip:.0f}) is below lower BE (${be_low:.2f})",
            "detail": "Stabilizing regime supports the position throughout the trade."
        })

    if abs(spot - call_wall) / spot < 0.01:
        gex_events.append({
            "type": "great",
            "label": f"Spot pinned at call wall (${call_wall:.0f})",
            "detail": "Maximum dealer hedging support — trade entered at ideal location."
        })

    # Action plan with concrete dates/levels
    front_exp_dt = datetime.strptime(front_exp, "%Y-%m-%d").date()
    back_exp_dt = datetime.strptime(back_exp, "%Y-%m-%d").date()
    if front_exp_dt.weekday() < 5:  # Mon-Fri
        close_by_date = front_exp_dt
        close_by_note = "by 3:30 PM ET (front expiry day)"
    else:
        close_by_date = previous_trading_day(front_exp_dt)
        close_by_note = "by close (previous trading day)"

    # Filter events to those within trade window (today through front expiry)
    relevant_events = []
    if events:
        for ev in events:
            try:
                ev_date = date.fromisoformat(ev["date"][:10])
                if today <= ev_date <= front_exp_dt:
                    relevant_events.append(ev)
            except (ValueError, KeyError):
                continue

    # Apply event-based verdict downgrade
    from events_engine import adjust_verdict_for_events
    final_verdict = adjust_verdict_for_events(verdict, relevant_events, front_exp_dt)

    return {
        "verdict": final_verdict,
        "events": relevant_events,
        "trade": {
            "ticker": ticker,
            "structure": "Call Calendar Spread",
            "strike": chosen_strike,
            "front": {
                "expiry": front_exp,
                "dte": front_dte,
                "action": "SELL TO OPEN",
                "mid": front_mid,
                "bid": front_leg.get("bid"),
                "ask": front_leg.get("ask"),
                "iv": front_iv * 100 if front_iv < 5 else front_iv,
                "oi": front_oi,
            },
            "back": {
                "expiry": back_exp,
                "dte": back_dte,
                "action": "BUY TO OPEN",
                "mid": back_mid,
                "bid": back_leg.get("bid"),
                "ask": back_leg.get("ask"),
                "iv": back_iv * 100 if back_iv < 5 else back_iv,
                "oi": back_oi,
            },
            "entry": {
                "debit": debit,
                "debit_dollars": debit_dollars,
                "max_loss": max_loss,
            },
            "exit": {
                "stop_value": stop_loss_value,
                "stop_loss_dollars": stop_loss_dollars,
                "target_value": take_profit_value,
                "take_profit_dollars": take_profit_dollars,
                "max_profit_dollars": max_profit_dollars,
                "close_by": close_by_date.isoformat(),
                "close_by_note": close_by_note,
            },
            "breakevens": {
                "lower": be_low,
                "upper": be_high,
                "width_pct": (be_high - be_low) / spot * 100,
            },
            "context": {
                "spot": spot,
                "call_wall": call_wall,
                "gamma_flip": flip,
                "iv30": iv30,
            },
            "gex_events": gex_events,
            "rr_ratio": take_profit_dollars / stop_loss_dollars if stop_loss_dollars > 0 else 0,
        }
    }


if __name__ == "__main__":
    # Quick test
    import sys
    from aapl_gex_fetcher import fetch_cboe_chain, compute_gex

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"Testing trade engine for {ticker}...\n")

    raw = fetch_cboe_chain(ticker)
    gex_result = compute_gex(raw, ticker, days_forward=28)

    rec = build_trade_recommendation(gex_result, raw, ticker)

    print(f"Verdict: {rec['verdict']['action'].upper()}")
    for r in rec["verdict"]["reasons"]:
        print(f"  • {r}")

    if rec.get("trade"):
        t = rec["trade"]
        print(f"\nRecommended Trade: {t['structure']}")
        print(f"  Strike:    ${t['strike']:.0f}")
        print(f"  SELL:      {t['front']['expiry']} (DTE {t['front']['dte']}) @ ${t['front']['mid']:.2f}")
        print(f"  BUY:       {t['back']['expiry']} (DTE {t['back']['dte']}) @ ${t['back']['mid']:.2f}")
        print(f"  Debit:     ${t['entry']['debit']:.2f} (${t['entry']['debit_dollars']:.0f}/contract)")
        print(f"  Stop:      ${t['exit']['stop_value']:.2f} (loss ${t['exit']['stop_loss_dollars']:.0f})")
        print(f"  Target:    ${t['exit']['target_value']:.2f} (gain ${t['exit']['take_profit_dollars']:.0f})")
        print(f"  Max P:     ${t['exit']['max_profit_dollars']:.0f}")
        print(f"  Breakevens: ${t['breakevens']['lower']:.2f} – ${t['breakevens']['upper']:.2f}")
        print(f"  R:R:       1:{t['rr_ratio']:.2f}")
        print(f"  Close by:  {t['exit']['close_by']}")
        for ev in t.get("gex_events", []):
            print(f"  [{ev['type']}] {ev['label']}")
    elif rec.get("error"):
        print(f"\nNo trade: {rec['error']}")
