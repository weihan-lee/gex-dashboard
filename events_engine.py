#!/usr/bin/env python3
"""
Events Engine — pulls macro and earnings events that could affect calendar trades.

Sources:
  - Forex Factory (free JSON, this week of macro events)
  - Nasdaq earnings calendar (free, by date)
  - Hardcoded known FOMC dates (since macro feed is limited to current week)

Outputs a list of events with date, impact level, type, and affected tickers.
"""

import json
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

# Known major future FOMC meeting dates (hardcoded, since free macro feeds
# only cover current week). Update annually from federalreserve.gov.
KNOWN_FOMC_DATES = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

HIGH_IMPACT_KEYWORDS = {
    "non-farm employment change": "critical",
    "non farm payrolls": "critical",
    "nfp": "critical",
    "fomc statement": "critical",
    "federal funds rate": "critical",
    "fomc press conference": "critical",
    "cpi m/m": "critical",
    "core cpi": "critical",
    "ppi": "critical",
    "unemployment rate": "high",
    "average hourly earnings": "high",
    "ism services pmi": "high",
    "ism manufacturing pmi": "high",
    "retail sales": "high",
    "advance gdp": "high",
    "gdp price index": "high",
    "jolts job openings": "high",
    "fomc minutes": "medium",
    "fomc member": "medium",
    "unemployment claims": "medium",
    "consumer confidence": "medium",
    "uom consumer sentiment": "medium",
    "adp non-farm": "medium",
}


def classify_macro_event(title: str, impact: str) -> str:
    t = title.lower().strip()

    # ADP is medium, not critical
    if "adp" in t:
        return "medium"

    # Individual Fed speakers — demote to low (too many, too noisy)
    # Unless it's the FOMC Statement/Press Conference itself
    if "fomc member" in t and "speaks" in t:
        return "low"

    # Iterate in order of specificity (longest keywords first)
    for keyword in sorted(HIGH_IMPACT_KEYWORDS, key=len, reverse=True):
        if keyword in t:
            return HIGH_IMPACT_KEYWORDS[keyword]

    return {"High": "high", "Medium": "medium", "Low": "low",
            "Holiday": "holiday"}.get(impact, "low")


def fetch_macro_events(start: date, end: date) -> list[dict]:
    events = []
    try:
        req = urllib.request.Request(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ff_events = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  Warning: Could not fetch macro calendar: {e}", flush=True)
        ff_events = []

    for e in ff_events:
        if e.get("country") != "USD":
            continue
        try:
            dt = datetime.fromisoformat(e["date"])
            event_date = dt.date()
        except (ValueError, KeyError):
            continue
        if event_date < start or event_date > end:
            continue

        tier = classify_macro_event(e["title"], e.get("impact", ""))
        if tier in ("low", "holiday"):
            continue

        events.append({
            "date": dt.isoformat(),
            "title": e["title"],
            "impact": tier,
            "ff_impact": e.get("impact", ""),
            "type": "macro",
            "country": "US",
            "source": "forexfactory",
            "forecast": e.get("forecast", ""),
            "previous": e.get("previous", ""),
        })

    # Add hardcoded FOMC meetings in window
    for fomc_date_str in KNOWN_FOMC_DATES:
        fomc_date = date.fromisoformat(fomc_date_str)
        if start <= fomc_date <= end:
            already = any(
                "fomc" in ev["title"].lower() and "statement" in ev["title"].lower()
                and date.fromisoformat(ev["date"][:10]) == fomc_date
                for ev in events
            )
            if not already:
                events.append({
                    "date": f"{fomc_date_str}T14:00:00-04:00",
                    "title": "FOMC Statement & Press Conference",
                    "impact": "critical",
                    "ff_impact": "High",
                    "type": "macro",
                    "country": "US",
                    "source": "hardcoded",
                    "forecast": "",
                    "previous": "",
                })

    # Dedupe — Forex Factory occasionally lists the same event twice
    seen = set()
    deduped = []
    for ev in events:
        key = (ev["date"][:16], ev["title"])  # same minute + title
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)

    deduped.sort(key=lambda e: e["date"])
    return deduped


def fetch_earnings_for_ticker(ticker: str, start: date, end: date) -> list[dict]:
    """Check Nasdaq earnings calendar for ticker between start and end."""
    earnings = []
    current = start
    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        try:
            url = f"https://api.nasdaq.com/api/calendar/earnings?date={current.isoformat()}"
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read())

            rows = (payload.get("data") or {}).get("rows") or []
            for row in rows:
                if (row.get("symbol") or "").upper() == ticker.upper():
                    earnings.append({
                        "date": current.isoformat() + "T16:00:00-04:00",
                        "title": f"{ticker} Earnings",
                        "impact": "critical",
                        "ff_impact": "High",
                        "type": "earnings",
                        "country": "US",
                        "source": "nasdaq",
                        "forecast": row.get("epsForecast", ""),
                        "previous": row.get("lastYearEPS", ""),
                        "time_of_day": row.get("time", ""),
                    })
        except (urllib.error.URLError, json.JSONDecodeError):
            pass
        current += timedelta(days=1)

    return earnings


def get_events_for_window(ticker: str, start: date, end: date,
                           macro_cache: list | None = None) -> list[dict]:
    """Combine macro + earnings events for a single ticker's trade window.

    Pass `macro_cache` when looping over multiple tickers to avoid refetching
    the macro calendar each time.
    """
    macro = macro_cache if macro_cache is not None else fetch_macro_events(start, end)
    earnings = fetch_earnings_for_ticker(ticker, start, end)
    combined = list(macro) + earnings
    combined.sort(key=lambda e: e["date"])
    return combined


def adjust_verdict_for_events(verdict: dict, events: list[dict],
                               front_expiry: date) -> dict:
    """
    Downgrade trade verdict if critical events fall in the window.
    """
    critical_in_window = [
        e for e in events
        if e["impact"] == "critical"
        and date.fromisoformat(e["date"][:10]) <= front_expiry
    ]

    if not critical_in_window:
        return verdict

    new_verdict = dict(verdict)
    if len(critical_in_window) >= 2:
        if verdict["action"] == "trade":
            new_verdict["action"] = "skip"
            new_verdict["reasons"] = list(verdict["reasons"]) + [
                f"{len(critical_in_window)} critical events in window — too risky"
            ]
    elif critical_in_window:
        if verdict["action"] == "trade":
            new_verdict["action"] = "reduce"
            ev = critical_in_window[0]
            ev_date = date.fromisoformat(ev["date"][:10])
            new_verdict["reasons"] = list(verdict["reasons"]) + [
                f"⚠ {ev['title']} on {ev_date.strftime('%a %d %b')}"
            ]

    return new_verdict


if __name__ == "__main__":
    today = date.today()
    end = today + timedelta(days=14)
    print(f"Events for window: {today} to {end}\n")

    events = get_events_for_window("AAPL", today, end)
    if not events:
        print("No notable events in this window.")
    else:
        for e in events:
            dt = datetime.fromisoformat(e["date"])
            print(f"  {dt.strftime('%a %d %b %H:%M')} [{e['impact']:8}] {e['title']}")
