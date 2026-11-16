# nq-levels

Every weekday before the NY open, GitHub Actions runs `levels.py`, which pulls
1-minute NQ futures bars and writes `levels.json`: prior-day high/low/close from the
full electronic session (6 PM–5 PM ET), Asia high/low (6–9 PM ET), London high/low
(2–5 AM ET), overnight high/low,
and whether each level has already been swept.

The NQ Morning Brief reads `levels.json` from this repo to fill in its Key Levels card.

Run it by hand: **Actions → NQ session levels → Run workflow**.

Data: Yahoo Finance `NQ=F` (free, unofficial, a few minutes delayed). Always confirm on your chart.
