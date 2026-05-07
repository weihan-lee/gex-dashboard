#!/usr/bin/env python3
"""
Iron Condor builder — finds short strikes near target delta, builds the 4-leg
spread, computes credit, max loss, breakevens, R/R.

Also provides a comparator to pick the better of {calendar, iron_condor}
based on current conditions (R/R, IV regime, GEX setup).
"""

from datetime import date, datetime, timedelta


def get_mid(opt: dict) -> float | None:
    bid = opt.get("bid", 0)
    ask = opt.get("ask", 0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2


def find_strike_at_delta(chain_idx: dict, expiry: str, cp: str,
                          target_delta: float) -> tuple | None:
    """
    Find the option closest to the target delta (absolute value).
    For puts, deltas are negative — pass positive target (e.g., 0.15)
    and we'll match against abs(delta).

    Returns (strike, option_dict, actual_delta) or None.
    """
    candidates = []
    for (e, c, strike), opt in chain_idx.items():
        if e != expiry or c != cp:
            continue
        if get_mid(opt) is None:
            continue
        delta = opt.get("delta")
        if delta is None or delta == 0:
            continue
        candidates.append((strike, opt, abs(delta)))

    if not candidates:
        return None

    # Find candidate with delta closest to target
    best = min(candidates, key=lambda x: abs(x[2] - target_delta))
    return best


def find_strike_at_offset(chain_idx: dict, expiry: str, cp: str,
                           target_strike: float) -> tuple | None:
    """Find the closest strike (with valid B/A) at or near target_strike."""
    candidates = []
    for (e, c, strike), opt in chain_idx.items():
        if e != expiry or c != cp:
            continue
        if get_mid(opt) is None:
            continue
        candidates.append((strike, opt))

    if not candidates:
        return None

    return min(candidates, key=lambda x: abs(x[0] - target_strike))


def build_iron_condor(ticker_data: dict, raw_chain: dict, chain_idx: dict,
                       ticker: str, expiry: str, dte: int,
                       short_delta_target: float = 0.15,
                       wing_width: float = 5.0) -> dict | None:
    """
    Build a 7-DTE iron condor with conservative ~15 delta short strikes
    and ~$5 wide wings.

    Structure:
      Sell put (short_put_strike, ~15 delta)
      Buy put (short_put_strike - wing_width)
      Sell call (short_call_strike, ~15 delta)
      Buy call (short_call_strike + wing_width)

    Returns dict with full trade details, or None if can't be built.
    """
    spot = ticker_data["spot"]

    # Find short strikes at target delta
    short_put_result = find_strike_at_delta(chain_idx, expiry, "P", short_delta_target)
    short_call_result = find_strike_at_delta(chain_idx, expiry, "C", short_delta_target)

    if not short_put_result or not short_call_result:
        return {"error": "No options at target delta on this expiry"}

    short_put_strike, short_put_opt, sp_delta = short_put_result
    short_call_strike, short_call_opt, sc_delta = short_call_result

    # Sanity: short put must be below spot, short call above
    if short_put_strike >= spot or short_call_strike <= spot:
        return {"error": f"Short strikes don't bracket spot (sp={short_put_strike}, sc={short_call_strike}, spot={spot:.2f})"}

    # Find wings
    long_put_result = find_strike_at_offset(chain_idx, expiry, "P",
                                              short_put_strike - wing_width)
    long_call_result = find_strike_at_offset(chain_idx, expiry, "C",
                                               short_call_strike + wing_width)

    if not long_put_result or not long_call_result:
        return {"error": "No liquid wing strikes available"}

    long_put_strike, long_put_opt = long_put_result
    long_call_strike, long_call_opt = long_call_result

    # Ensure wings are actually outside short strikes (sanity check)
    if long_put_strike >= short_put_strike or long_call_strike <= short_call_strike:
        return {"error": "Wing strikes don't extend beyond short strikes"}

    # Mid prices
    sp_mid = get_mid(short_put_opt)
    lp_mid = get_mid(long_put_opt)
    sc_mid = get_mid(short_call_opt)
    lc_mid = get_mid(long_call_opt)

    if any(m is None for m in [sp_mid, lp_mid, sc_mid, lc_mid]):
        return {"error": "Missing bid/ask on one of the legs"}

    # Net credit per share = (sp + sc) - (lp + lc)
    credit = (sp_mid + sc_mid) - (lp_mid + lc_mid)

    if credit <= 0:
        return {"error": f"Negative credit (${credit:.2f}) — spread doesn't pay"}

    # Per-contract dollar values (×100 multiplier)
    credit_dollars = credit * 100

    # Max loss = wing_width - credit (per side, times 100)
    # Both wings are equal width, so max loss is the same regardless of which side breaks
    actual_put_wing_width = short_put_strike - long_put_strike
    actual_call_wing_width = long_call_strike - short_call_strike
    max_wing_width = max(actual_put_wing_width, actual_call_wing_width)
    max_loss_per_share = max_wing_width - credit
    max_loss_dollars = max_loss_per_share * 100

    # Breakevens
    be_low = short_put_strike - credit
    be_high = short_call_strike + credit

    # Stop / target rules
    # Standard rule: stop at 2x credit collected (i.e., spread value 3x credit)
    # Take profit at 50% of credit captured
    stop_value = credit * 2  # close if cost-to-close = 2x credit (loss = credit)
    stop_loss_dollars = credit * 100  # losing 1x credit
    take_profit_value = credit * 0.5  # close at 50% credit captured
    take_profit_dollars = credit * 0.5 * 100

    # Action plan with concrete dates
    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").date()
    if expiry_dt.weekday() < 5:
        close_by_date = expiry_dt
        close_by_note = "by 3:30 PM ET (expiry day)"
    else:
        close_by_date = expiry_dt - timedelta(days=1)
        close_by_note = "by close (day before expiry)"

    rr_ratio = take_profit_dollars / stop_loss_dollars if stop_loss_dollars > 0 else 0

    # Probability of profit (rough estimate using deltas)
    # P(price stays between short strikes) ≈ 1 - |sp_delta| - |sc_delta|
    pop_estimate = max(0, 1 - abs(sp_delta) - abs(sc_delta))

    return {
        "structure": "Iron Condor",
        "strike": None,  # Not single-strike
        "expiry": expiry,
        "dte": dte,
        "legs": [
            {
                "action": "BUY TO OPEN",
                "type": "put",
                "strike": long_put_strike,
                "mid": lp_mid,
                "bid": long_put_opt.get("bid"),
                "ask": long_put_opt.get("ask"),
                "label": "LONG PUT (wing)",
                "color": "buy",
            },
            {
                "action": "SELL TO OPEN",
                "type": "put",
                "strike": short_put_strike,
                "mid": sp_mid,
                "bid": short_put_opt.get("bid"),
                "ask": short_put_opt.get("ask"),
                "delta": sp_delta,
                "label": "SHORT PUT",
                "color": "sell",
            },
            {
                "action": "SELL TO OPEN",
                "type": "call",
                "strike": short_call_strike,
                "mid": sc_mid,
                "bid": short_call_opt.get("bid"),
                "ask": short_call_opt.get("ask"),
                "delta": sc_delta,
                "label": "SHORT CALL",
                "color": "sell",
            },
            {
                "action": "BUY TO OPEN",
                "type": "call",
                "strike": long_call_strike,
                "mid": lc_mid,
                "bid": long_call_opt.get("bid"),
                "ask": long_call_opt.get("ask"),
                "label": "LONG CALL (wing)",
                "color": "buy",
            },
        ],
        "entry": {
            "credit": credit,
            "credit_dollars": credit_dollars,
            "max_loss": max_loss_dollars,
        },
        "exit": {
            "stop_value": stop_value,
            "stop_loss_dollars": stop_loss_dollars,
            "target_value": take_profit_value,
            "take_profit_dollars": take_profit_dollars,
            "max_profit_dollars": credit_dollars,  # Max profit = credit
            "close_by": close_by_date.isoformat(),
            "close_by_note": close_by_note,
        },
        "breakevens": {
            "lower": be_low,
            "upper": be_high,
            "width_pct": (be_high - be_low) / spot * 100,
        },
        "short_strikes": {
            "put": short_put_strike,
            "call": short_call_strike,
        },
        "wing_strikes": {
            "put": long_put_strike,
            "call": long_call_strike,
        },
        "rr_ratio": rr_ratio,
        "pop_estimate": pop_estimate,  # rough probability of profit
        "deltas": {
            "short_put": sp_delta,
            "short_call": sc_delta,
        },
    }


def calc_calendar_score(cal_trade: dict, ticker_data: dict) -> float:
    """
    Score the calendar trade for current conditions. Higher = better fit.
    Considers: R/R, IV (calendar wants low), call wall pin tightness.
    """
    if not cal_trade:
        return 0

    score = 0

    # R/R component (calendar always 1:1, so this is constant)
    score += 30

    # Low IV is good for calendars (cheaper entry, room for IV expansion)
    iv = ticker_data["iv30"]
    if iv < 20:
        score += 30
    elif iv < 25:
        score += 20
    elif iv < 30:
        score += 10
    else:
        score -= 10  # high IV is bad for calendars

    # Spot near call wall = ideal pin location
    spot = ticker_data["spot"]
    call_wall = ticker_data["call_wall"]["strike"]
    distance_pct = abs(spot - call_wall) / spot
    if distance_pct < 0.005:
        score += 30
    elif distance_pct < 0.015:
        score += 20
    elif distance_pct < 0.03:
        score += 10

    # Strong call wall (high gamma concentration)
    call_wall_gex = ticker_data["call_wall"]["gex"] / 1e6
    if call_wall_gex > 100:
        score += 10
    if call_wall_gex > 200:
        score += 10

    return score


def calc_condor_score(condor_trade: dict, ticker_data: dict) -> float:
    """
    Score the iron condor trade for current conditions. Higher = better fit.
    Considers: R/R, IV (condor wants higher), POP, wide profit zone.
    """
    if not condor_trade or condor_trade.get("error"):
        return 0

    score = 0

    # R/R component — condors typically have 1:2 to 1:5 (risk more than reward)
    # Adjust score based on actual R/R
    rr = condor_trade["rr_ratio"]
    if rr > 0.5:
        score += 30
    elif rr > 0.33:
        score += 20
    elif rr > 0.25:
        score += 10
    else:
        score += 0

    # Higher IV is GOOD for condors (more credit collected)
    iv = ticker_data["iv30"]
    if iv > 30:
        score += 30
    elif iv > 25:
        score += 20
    elif iv > 20:
        score += 10
    else:
        score -= 10  # low IV = thin credits

    # POP estimate — condors thrive when probability is high
    pop = condor_trade.get("pop_estimate", 0)
    if pop > 0.75:
        score += 30
    elif pop > 0.65:
        score += 20
    elif pop > 0.55:
        score += 10

    # Bonus if both walls (call & put) are present and bracket spot
    spot = ticker_data["spot"]
    call_wall = ticker_data["call_wall"]["strike"]
    put_wall = ticker_data["put_wall"]["strike"]
    if put_wall < spot < call_wall:
        score += 10
    if call_wall - put_wall > spot * 0.02:  # Walls reasonably wide apart
        score += 10

    return score


def pick_better_strategy(calendar_rec: dict, condor_rec: dict,
                          ticker_data: dict) -> dict:
    """
    Compare calendar and iron condor recommendations, return the better one
    along with both scores for transparency.
    """
    cal_trade = calendar_rec.get("trade") if calendar_rec else None
    condor_trade = condor_rec if condor_rec and not condor_rec.get("error") else None

    cal_score = calc_calendar_score(cal_trade, ticker_data) if cal_trade else 0
    condor_score = calc_condor_score(condor_trade, ticker_data) if condor_trade else 0

    if cal_score == 0 and condor_score == 0:
        chosen = "none"
    elif cal_score >= condor_score:
        chosen = "calendar"
    else:
        chosen = "iron_condor"

    return {
        "chosen": chosen,
        "calendar_score": cal_score,
        "condor_score": condor_score,
        "calendar": calendar_rec,
        "iron_condor": condor_rec,
    }


if __name__ == "__main__":
    import sys
    from aapl_gex_fetcher import fetch_cboe_chain, compute_gex
    from trade_engine import index_chain, find_weekly_expiries

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"Testing iron condor for {ticker}...\n")

    raw = fetch_cboe_chain(ticker)
    gex = compute_gex(raw, ticker, days_forward=28)
    chain_idx = index_chain(raw, ticker)

    today = date.today()
    expiries = find_weekly_expiries(chain_idx, today, gap_days=7, spot=gex["spot"])
    if not expiries:
        print("No suitable expiries")
        sys.exit(1)

    front_exp, back_exp, front_dte, back_dte = expiries
    print(f"Using front expiry: {front_exp} (DTE {front_dte})\n")

    condor = build_iron_condor(gex, raw, chain_idx, ticker, front_exp, front_dte,
                                short_delta_target=0.15, wing_width=5.0)

    if condor.get("error"):
        print(f"Error: {condor['error']}")
        sys.exit(1)

    print(f"=== {ticker} Iron Condor ===")
    print(f"Spot:       ${gex['spot']:.2f}")
    print(f"Expiry:     {condor['expiry']} (DTE {condor['dte']})")
    print(f"\nLegs:")
    for leg in condor["legs"]:
        delta_str = f" Δ{leg.get('delta', 0):.2f}" if leg.get('delta') else ""
        print(f"  {leg['action']:<14} {leg['type'].upper():<5} ${leg['strike']:.0f} @ ${leg['mid']:.2f}{delta_str}  ({leg['label']})")
    print(f"\nCredit:     ${condor['entry']['credit']:.2f} (${condor['entry']['credit_dollars']:.0f}/contract)")
    print(f"Max Loss:   ${condor['entry']['max_loss']:.0f}")
    print(f"Stop:       Loss ${condor['exit']['stop_loss_dollars']:.0f}")
    print(f"Target:     Gain ${condor['exit']['take_profit_dollars']:.0f}")
    print(f"Max Profit: ${condor['exit']['max_profit_dollars']:.0f} (if expires between shorts)")
    print(f"Breakevens: ${condor['breakevens']['lower']:.2f} – ${condor['breakevens']['upper']:.2f}")
    print(f"R/R:        1:{condor['rr_ratio']:.2f}")
    print(f"POP est:    {condor['pop_estimate']*100:.0f}%")
    print(f"Close by:   {condor['exit']['close_by']}")
