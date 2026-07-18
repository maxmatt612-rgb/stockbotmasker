# MASKER — audit fix campaign (branch `audit-fixes`)

Nine issues found by an honesty/correctness audit of the scoring, accuracy, and
backtest logic, fixed one at a time with tests and a commit each. Full detail
and evidence for each item is in [MASKER_FIX.md](MASKER_FIX.md).

## 1. Fake "YTD" (CRITICAL)
`ytd_return` was actually a trailing-12-month return, not a calendar year-to-date
figure — SPY showed 19.75% when the true calendar YTD was 9.38%. Added
`_ytd_return()`, which uses the prior calendar year's final close as baseline
(or the first close of the current year if there isn't one yet). The old
trailing value is kept under a new, honestly-named `one_year_return` field.

## 2. Fake "today's estimate" (HIGH)
`daily_estimate_price` applied the already-realized day change a *second* time
on top of `current_price` (which already contained it) — the "→ $X" figure was
mathematically meaningless. Removed the field entirely; the card line now
reads "Oggi finora: {pct}%" instead of implying a forecast with a price.

## 3. Accuracy page graded a verdict that was never populated (CRITICAL)
`/api/accuracy` had dead branches trying to read a `"verdict"` field that was
hardcoded to `""` at the only write site — accuracy was always driven by the
numeric `score_10` alone, while the UI implied it graded the bot's AI signal.
Removed the dead field and dead branches; the Track record page now states
exactly what's measured (score direction vs. close, not an AI judgment).

## 4. Backtest used an unachievable entry price + zero costs (HIGH)
`price_at_analysis` was captured at 07:35 Rome (≈01:35 ET, markets shut), so it
was really the *prior day's close* relabeled as a "morning" price, and the
backtest graded it with no trading costs — a fill no trader could ever get.
The close job now also captures the day's real Open as `price_at_open`; the
backtest uses that as entry (excluding older rows that don't have it) and
subtracts a `COST_PCT` round-trip cost from every simulated trade. New
snapshots are stamped `engine_version`. **History rows saved before
`engine_version: 2` do not have `price_at_open` and are excluded from the
backtest** — they were graded under the old, unachievable-fill assumption and
are not comparable to rows saved after this fix. `/api/accuracy` still
includes them (direction-only, less sensitive to entry price) but reports a
`by_engine_version` breakdown so the mix is visible rather than hidden.

## 5. Three non-standard RSI implementations (MED)
RSI was computed with a plain rolling mean in three separate places (one with
no NaN guard, so a flat 14-day window fed NaN straight into scoring) — a
fourth, previously-unflagged copy turned up in `/api/stock/{ticker}/technicals`
during the fix. Consolidated all four onto one `wilder_rsi()` using proper
Wilder smoothing, matching how TradingView and brokers compute it, returning a
neutral 50.0 instead of NaN when it can't be computed. Scores shift slightly
as a result — expected, and covered by the `engine_version` bump to 3.

## 6. Scheduler had no catch-up, scan cache key had no date (MED)
The scheduler fired only on exact minute equality, so a restart or busy loop
silently skipped that day's scan/snapshot/close with no recovery — and the
07:30 scan branch was missing the weekday guard the other two jobs had. Added
a `should_run()` catch-up check so a missed tick runs on the next pass instead
of vanishing. The scan cache key had no date, so a late-day restart could keep
serving yesterday's scan as "fresh" under its 24h TTL; introduced a dated
`_scan_key()` and switched every read/write site to it (eight sites, not just
the two originally flagged — the others would have silently gone stale-keyed
otherwise).

## 7. Earnings date compared in the wrong timezone (LOW-MED)
Earnings-today / days-to-earnings compared a naive local-clock date against
Yahoo's earnings dates, while the rest of the app anchors to Europe/Rome — near
midnight the two could disagree on which day it was. Earnings are a US-market
event, so the comparison now anchors to `America/New_York` instead.

## 8. Invented 7%/year projection was indistinguishable from real history (MED)
When neither a 3-year nor 1-year CAGR was available, the multi-year price
projections silently fell back to an assumed 7%/year with no indication it
wasn't computed from real history. Extracted `_projection_basis()`, which
returns both the rate and a label ("CAGR 3 anni" / "CAGR 1 anno" / "ipotesi
7%/anno (storico insufficiente)"), exposed as `projection_basis` in the API
response. No frontend currently renders this data, so there was no UI copy to
update — the label is ready for whenever a consumer is built.

## 9. Scoring constants were scattered magic numbers (MED, optional)
Moved every scoring threshold/point value into a single `SCORING` dict in
`config.py`, each entry commented "hand-set, no backtest" — the honest
default, since none of these were ever calibrated against historical
performance. Also surfaced the previously-invisible "+1 AI ticker" bonus in
`score_breakdown` so it's disclosed to users instead of being folded silently
into the total score.

---

**Status:** all 9 checklist items done, `pytest -q` green (16 tests), smoke-tested
locally against `/api/stock/SPY`, `/api/accuracy`, `/api/backtest` (all 200;
`ytd_return` confirmed to differ from `one_year_return` on live SPY data) and
against `scan_cheap_stocks` on a live mini-universe. Ready for the owner to
review the `audit-fixes` branch and deploy.
