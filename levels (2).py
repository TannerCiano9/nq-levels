"""
NQ session levels for the morning brief.

Pulls 1-minute NQ futures bars and writes levels.json with:
  prior-day RTH high/low/close, Asia high/low, London high/low,
  overnight (Globex) high/low, last price, and whether each level
  has already been swept.

Session times (America/New_York):
  Prior day  full electronic (ETH) session: 18:00 -> 17:00 of the
             most recent completed trading day (e.g. Thu 6 PM -> Fri 5 PM)
  Overnight  18:00 prior evening -> run time
  Asia       18:00 -> 21:00 prior evening
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

# Level classification (NQ points)
TOUCH_TOL = 5.0      # within this many points of a level counts as a touch
REVERSAL_PTS = 40.0  # move away from the level after a touch that counts as "respected"
CLOSE_TF = "5min"    # a close beyond the level on this timeframe counts as "taken"
ARM_PTS = 15.0       # price must first move this far away before a return counts as a touch
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

    # Prior completed ETH session (18:00 the evening before -> 17:00).
    # Walk back over weekends/holidays until a session with data is found.
    pd_hi = pd_lo = pd_close = None
    pd_day = None
    for back in range(1, 8):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:  # Sat/Sun are not session end days
            continue
        start = _at(d - timedelta(days=1), 18)  # Monday's opens Sunday 18:00
        w = _window(df, start, _at(d, 17))
        if len(w) > 60:
            pd_hi, pd_lo = _hl(w)
            pd_close = round(float(w["Close"].iloc[-1]), 2)
            pd_day = d
            break

    eve = today - timedelta(days=1)  # Sunday evening on Mondays
    on = _window(df, _at(eve, 18), now)
    asia = _window(df, _at(eve, 18), _at(eve, 21))
    london = _window(df, _at(today, 2), _at(today, 5))

    on_hi, on_lo = _hl(on)
    as_hi, as_lo = _hl(asia)
    ld_hi, ld_lo = _hl(london)

    after_asia = _window(df, _at(eve, 21), now)
    after_london = _window(df, _at(today, 5), now)
    last = round(float(on["Close"].iloc[-1]), 2) if not on.empty else None

    def fmt(ts):
        t = ts.astimezone(ET)
        return t.strftime("%-I:%M %p").replace(" AM", "a").replace(" PM", "p")

    def status(price, later, side):
        """Classify what price did at a level after the level's session ended.

        Taken     - a 5-min candle closed beyond the level (passed through it)
        Respected - price touched the level (within TOUCH_TOL, wicks through
                    allowed) and reversed REVERSAL_PTS away without a 5-min
                    close beyond it
        Testing   - touched but hasn't closed through or reversed yet
        Not reached
        """
        if price is None:
            return "unk", "Check chart"
        if later is None or later.empty:
            return "unk", "Not reached"
        up = side == "high"

        bars5 = later.resample(CLOSE_TF, label="right", closed="left").agg(
            {"High": "max", "Low": "min", "Close": "last"}).dropna()
        thru = bars5[bars5["Close"] > price] if up else bars5[bars5["Close"] < price]
        taken_at = thru.index[0] if not thru.empty else None

        pre = later if taken_at is None else later[later.index < taken_at]
        # Only count a touch once price has first moved away from the level,
        # so a level set right at the session boundary isn't "touched" instantly.
        armed = pre[pre["High"] <= price - ARM_PTS] if up else pre[pre["Low"] >= price + ARM_PTS]
        if not armed.empty:
            pre_armed = pre[pre.index > armed.index[0]]
        else:
            pre_armed = pre.iloc[0:0]
        touch = (pre_armed[pre_armed["High"] >= price - TOUCH_TOL] if up
                 else pre_armed[pre_armed["Low"] <= price + TOUCH_TOL])
        respected_at = None
        if not touch.empty:
            after = pre[pre.index >= touch.index[0]]
            away = after[after["Low"] <= price - REVERSAL_PTS] if up else after[after["High"] >= price + REVERSAL_PTS]
            if not away.empty:
                respected_at = touch.index[0]

        if taken_at is not None:
            note = "Taken " + fmt(taken_at)
            if respected_at is not None:
                note = "Respected " + fmt(respected_at) + ", then taken " + fmt(taken_at)
            return "bear", note
        if respected_at is not None:
            return "bull", "Respected " + fmt(respected_at)
        if not touch.empty:
            return "neu", "Testing"
        return "unk", "Not reached"

    extreme = ("unk", "Session extreme")

    levels = [
        ("Prior day high (PDH)", pd_hi, status(pd_hi, on, "high")),
        ("Overnight high", on_hi, extreme if on_hi else ("unk", "Check chart")),
        ("London high", ld_hi, status(ld_hi, after_london, "high")),
        ("Asia high", as_hi, status(as_hi, after_asia, "high")),
        ("Asia low", as_lo, status(as_lo, after_asia, "low")),
        ("London low", ld_lo, status(ld_lo, after_london, "low")),
        ("Overnight low", on_lo, extreme if on_lo else ("unk", "Check chart")),
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
        "prior_session_date": pd_day.isoformat() if pd_day else None,
        "last": last,
        "prior_close": pd_close,
        "prior_day_equilibrium": round((pd_hi + pd_lo) / 2, 2) if pd_hi and pd_lo else None,
        "zone_vs_prior_day": zone,
        "levels": [
            {"name": n, "price": p, "status": s[0], "statusText": s[1]} for n, p, s in levels
        ],
        "note": "Yahoo NQ=F continuous contract, 1-minute bars, a few minutes delayed. "
                "Taken = a 5-min close through the level. Respected = touched (within "
                f"{TOUCH_TOL:g} pts) and reversed {REVERSAL_PTS:g}+ pts without a 5-min close through. "
                "Times are ET.",
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
