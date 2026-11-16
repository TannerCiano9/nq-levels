"""
NQ session levels for the morning brief.

Pulls 1-minute NQ futures bars and writes levels.json with:
  prior-day RTH high/low/close, Asia high/low, London high/low,
  overnight (Globex) high/low, last price, and whether each level
  has already been swept.

Session times (America/New_York):
  RTH        09:30-16:00 (most recent completed weekday session)
  Overnight  18:00 prior evening -> run time
  Asia       20:00 prior evening -> 00:00
  London     02:00 -> 05:00

Data source: Yahoo Finance continuous contract NQ=F via yfinance
(free, unofficial, a few minutes delayed - fine for levels that
were set hours before the run).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
SYMBOL = "NQ=F"
OUT = Path(__file__).with_name("levels.json")


def fetch_bars() -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(SYMBOL, period="5d", interval="1m", progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError("No data returned for " + SYMBOL)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.index = pd.to_datetime(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    return df.tz_convert(ET).sort_index()


def _window(df: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    return df[(df.index >= start) & (df.index < end)]


def _at(day, hh, mm=0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=ET)


def _hl(w: pd.DataFrame):
    if w.empty:
        return None, None
    return round(float(w["High"].max()), 2), round(float(w["Low"].min()), 2)


def compute(df: pd.DataFrame, now: datetime) -> dict:
    today = now.astimezone(ET).date()

    # Prior completed RTH session: walk back over weekends/holidays.
    pd_hi = pd_lo = pd_close = None
    pd_day = None
    for back in range(1, 8):
        d = today - timedelta(days=back)
        w = _window(df, _at(d, 9, 30), _at(d, 16, 0))
        if len(w) > 30:
            pd_hi, pd_lo = _hl(w)
            pd_close = round(float(w["Close"].iloc[-1]), 2)
            pd_day = d
            break

    eve = today - timedelta(days=1)  # Sunday evening on Mondays
    on = _window(df, _at(eve, 18), now)
    asia = _window(df, _at(eve, 20), _at(today, 0))
    london = _window(df, _at(today, 2), _at(today, 5))

    on_hi, on_lo = _hl(on)
    as_hi, as_lo = _hl(asia)
    ld_hi, ld_lo = _hl(london)

    after_asia = _window(df, _at(today, 0), now)
    after_london = _window(df, _at(today, 5), now)
    last = round(float(on["Close"].iloc[-1]), 2) if not on.empty else None

    def status(price, later, side):
        """side='high' is swept if later trades above; 'low' if below."""
        if price is None:
            return "unk", "Check chart"
        if later is None or later.empty:
            return "neu", "Resting"
        hit = later["High"].max() > price if side == "high" else later["Low"].min() < price
        return ("bear", "Taken") if hit else ("neu", "Resting")

    levels = [
        ("Prior day high (PDH)", pd_hi, status(pd_hi, on, "high")),
        ("Overnight high", on_hi, ("neu", "Resting") if on_hi else ("unk", "Check chart")),
        ("London high", ld_hi, status(ld_hi, after_london, "high")),
        ("Asia high", as_hi, status(as_hi, after_asia, "high")),
        ("Asia low", as_lo, status(as_lo, after_asia, "low")),
        ("London low", ld_lo, status(ld_lo, after_london, "low")),
        ("Overnight low", on_lo, ("neu", "Resting") if on_lo else ("unk", "Check chart")),
        ("Prior day low (PDL)", pd_lo, status(pd_lo, on, "low")),
    ]

    zone = None
    if None not in (pd_hi, pd_lo, last):
        eq = (pd_hi + pd_lo) / 2
        zone = "premium" if last > eq else "discount"

    return {
        "symbol": SYMBOL,
        "generated_at": now.astimezone(ET).isoformat(timespec="minutes"),
        "trading_date": today.isoformat(),
        "prior_rth_date": pd_day.isoformat() if pd_day else None,
        "last": last,
        "prior_close": pd_close,
        "prior_day_equilibrium": round((pd_hi + pd_lo) / 2, 2) if pd_hi and pd_lo else None,
        "zone_vs_prior_day": zone,
        "levels": [
            {"name": n, "price": p, "status": s[0], "statusText": s[1]} for n, p, s in levels
        ],
        "note": "Yahoo NQ=F continuous contract, 1-minute bars, a few minutes delayed. "
                "Status 'Taken' = price has traded through the level since its session ended.",
    }


def main() -> int:
    now = datetime.now(ET)
    try:
        result = compute(fetch_bars(), now)
    except Exception as exc:  # keep the file honest instead of stale
        result = {
            "symbol": SYMBOL,
            "generated_at": now.isoformat(timespec="minutes"),
            "trading_date": now.date().isoformat(),
            "error": str(exc),
            "levels": [],
        }
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
