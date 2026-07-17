# MASKER fix campaign

Resume: do the next unchecked item.

## Checklist

### [ ] Item 1 — Fix the fake "YTD" (CRITICAL)
`ytd_return` (analyzer.py:61, via `_pct(hist, 0)` defined at analyzer.py:239-241) is a trailing-12-month return, not year-to-date (audit verified: SPY showed 19.75% when true calendar YTD was 9.38%). Fix: add a new helper `_ytd_return(hist)` — baseline = the last close of the PREVIOUS calendar year if present in the 1y window, else the first close of the current year — and use it for `ytd_return`. Do NOT change `_pct` itself (other call sites use it). Keep the JSON key `ytd_return` (the frontend and the AI prompts at web_server.py:2150, 2793, 3151 consume it — they say "YTD", so the corrected value makes them honest). Add a new `one_year_return` field carrying the old trailing-1y value. Test: synthetic DataFrame spanning two calendar years; assert the baseline is the prior year's final close.

### [ ] Item 2 — Remove the fake "today's estimate" (HIGH)
`daily_estimate_pct = day_change_pct` (analyzer.py:858-860) relabels the already-realized session move as a forecast, and `daily_estimate_price` applies that move a SECOND time on top of current_price (which already contains it) — the printed "→ $X" is mathematically meaningless. Fix: delete `daily_estimate_price` everywhere; change the card line at analyzer.py:953 from "📅 Stima oggi: ... → $..." to honest copy, e.g. "📊 Oggi finora: {sign}{pct:.1f}%". Grep v2.html for both field names: if `daily_estimate_pct` is rendered, relabel it there too ("Oggi finora"), keeping the key; remove dead usages of the price field. Evidence: grep output showing no remaining "Stima oggi" framing.

### [ ] Item 3 — Make the accuracy page grade what it says it grades (CRITICAL)
`"verdict": ""` is hardcoded at the only snapshot-write site (web_server.py:774) and never populated, so the verdict branches in /api/accuracy (web_server.py:4342-4345) are dead code — accuracy is driven solely by score_10 ≥7 / ≤4, while the UI copy (static/v2.html:575 and 836) implies it grades the bot's AI signal. Fix (subtractive — do NOT add LLM calls): remove the dead verdict logic from /api/accuracy and the dead `"verdict"` field from the snapshot write; change the UI copy to state exactly what is measured: direction of the numeric screener score (≥7 = rialzista, ≤4 = ribassista), confronto mattina→chiusura. Test: if the hit-counting logic is cheaply extractable into a pure helper, add a 3-case test; otherwise grep evidence that no verdict reference remains in the accuracy path.

### [ ] Item 4 — Backtest: achievable entry price + costs + version stamp (HIGH)
`price_at_analysis` is captured at 07:35 Rome ≈ 01:35 ET with markets shut (scheduler at web_server.py:820-834; write at web_server.py:769), so it is the PRIOR day's close relabeled as a "morning" price, and /api/backtest (web_server.py:4370-4477) grades it with zero costs — a fill no trader can get. Fix:
- In the 22:05 close job (`_close_history_snapshot_web`, near web_server.py:801-807, which already fetches the closing price), also store that day's daily-bar Open as `price_at_open` on each stock row.
- /api/accuracy and /api/backtest use open→close for rows that HAVE `price_at_open`; older rows without it are excluded from the backtest (the response reports n and the date range used).
- Subtract a flat round-trip cost per backtest trade: `COST_PCT = 0.10` (percent), a named constant in config.py with a comment.
- Add `"engine_version": 2` to newly saved snapshots; /api/accuracy includes a per-engine_version count so mixed-era history is visible.
- Update the UI copy at v2.html:839: entry = apertura, uscita = chiusura, costo 0.10% incluso.
Test: synthetic snapshot rows through the return computation, asserting the cost haircut and the exclusion of rows lacking price_at_open.

### [ ] Item 5 — One correct RSI (MED)
Two duplicated RSI implementations (analyzer.py:66-69 and 604-610) use a plain rolling mean — non-standard, diverges from TradingView/broker RSI — and the first has NO NaN guard (a flat 14-day window yields NaN that flows into scoring; the second copy already guards). Fix: one module-level `wilder_rsi(close, period=14)` using Wilder smoothing (`ewm(alpha=1/period, adjust=False).mean()` on gains/losses), returning 50.0 on NaN; both call sites use it; delete the duplicates. Scores will shift slightly — expected; if Item 4's engine_version 2 hasn't shipped yet, this rides with it, otherwise bump to 3. Test: flat series → 50.0; monotonically rising series → ~100; a short known vector sanity check.

### [ ] Item 6 — Scheduler catch-up + dated cache key (MED)
The while-True scheduler (web_server.py:820-843) fires only on exact minute equality — a restart or busy loop at the wrong moment silently skips that day's scan/snapshot/close, with no catch-up — and the 07:30 scan branch is missing the weekday guard the other two have. The scan cache key `f"scan:{top}"` (web_server.py:1038) has no date, TTL 24h. Fix: keep the loop, add `_last_run: dict[str, date]`; each pass, for each of the three jobs: if now(Rome) ≥ its scheduled time AND last_run != today AND weekday < 5 → run and record (evaluate scan before save so catch-up preserves order). Cache key becomes `f"scan:{top}:{today_rome_iso}"`. Test: a pure `should_run(now, scheduled_time, last_run_date)` helper exercised with a handful of datetimes (missed tick, weekend, already-ran).

### [ ] Item 7 — One timezone for earnings logic (LOW-MED)
analyzer.py:802 uses naive `datetime.now().date()` for earnings-today / days-to-earnings while the app anchors to Europe/Rome elsewhere (web_server.py:16) — near midnight the two disagree. Fix: earnings comparisons use `datetime.now(ZoneInfo("America/New_York")).date()` (earnings are US-market events; one-line comment saying so). Grep analyzer.py for other naive `datetime.now(` date comparisons and fix only those. Evidence: grep output.

### [ ] Item 8 — Flag the invented 7% projection (MED)
`base_rate = (cagr_3y or cagr_1y or 7.0) / 100` (analyzer.py:1104) silently substitutes an assumed 7%/yr into the 9 multi-year projection prices shown to users, indistinguishable from computed history. Fix: extract basis selection into a tiny helper returning (rate, basis_label) with labels "CAGR 3 anni" / "CAGR 1 anno" / "ipotesi 7%/anno (storico insufficiente)"; add `projection_basis` to the response; grep v2.html for where projections render and show the label next to them. Test: 3-case unit test on the helper.

### [ ] Item 9 (OPTIONAL — skip if budget is tight) — Constants into config
Move the scoring constants (the analyzer.py:862-885 block and the scan block at 653-743) into one `SCORING` dict in config.py with a one-line provenance comment each ("hand-set, no backtest" is the honest default). No behavior change: existing tests must pass, plus one before/after equality spot-check on a synthetic frame. Also ensure the +1 "AI ticker" bonus (analyzer.py:722-724) appears in `score_breakdown` so it's disclosed to users.

### [ ] Item 10 — Final smoke + wrap-up
Boot the server locally once; curl /api/stock/SPY, /api/accuracy, /api/backtest — all 200; sanity-check that the corrected `ytd_return` differs from `one_year_return`. `pytest -q` fully green. Write CHANGES.md: one honest paragraph per item (what changed, why), including the note that history rows before engine_version 2 were graded under the old, unachievable-fill assumptions. Update MASKER_FIX.md status to DONE. The owner reviews the branch and deploys.

## Later (noticed in passing, not in scope now)
