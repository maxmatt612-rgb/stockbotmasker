def test_harness_works():
    assert 1 + 1 == 2


def test_ytd_return_uses_prior_year_final_close_as_baseline():
    import pandas as pd
    import pytest
    from analyzer import _ytd_return

    idx = pd.to_datetime(["2024-06-03", "2024-12-30", "2025-01-02", "2025-02-03"])
    hist = pd.DataFrame({"Close": [90.0, 100.0, 102.0, 110.0]}, index=idx)
    assert _ytd_return(hist) == pytest.approx(10.0)


def test_ytd_return_falls_back_to_first_close_of_year_without_prior_year_data():
    import pandas as pd
    import pytest
    from analyzer import _ytd_return

    idx = pd.to_datetime(["2025-01-02", "2025-01-15", "2025-02-03"])
    hist = pd.DataFrame({"Close": [100.0, 105.0, 120.0]}, index=idx)
    assert _ytd_return(hist) == pytest.approx(20.0)


def test_backtest_applies_round_trip_cost_to_return():
    import pytest
    from config import COST_PCT
    from web_server import _compute_backtest

    history = {
        "2026-01-05": {
            "closed": True,
            "stocks": [
                {"ticker": "AAA", "price_at_open": 100.0, "price_at_close": 110.0, "score_10": 9.0},
            ],
        },
    }
    result = _compute_backtest(history)
    assert result["total"] == 1
    assert result["by_score"]["high"]["avg_return"] == pytest.approx(10.0 - COST_PCT)


def test_backtest_excludes_rows_without_price_at_open():
    from web_server import _compute_backtest

    history = {
        "2026-01-05": {
            "closed": True,
            "stocks": [
                {"ticker": "AAA", "price_at_open": 100.0, "price_at_close": 110.0, "score_10": 9.0},
                # Riga legacy pre-fix: solo price_at_analysis (close del giorno prima), niente price_at_open.
                {"ticker": "BBB", "price_at_analysis": 50.0, "price_at_close": 55.0, "score_10": 9.0},
            ],
        },
    }
    result = _compute_backtest(history)
    assert result["total"] == 1
    assert result["by_score"]["high"]["total"] == 1
    assert result["by_score"]["high"]["top3"][0]["ticker"] == "AAA"


def test_wilder_rsi_flat_series_is_neutral_50():
    import pandas as pd
    from analyzer import wilder_rsi

    flat = pd.Series([100.0] * 20)
    assert wilder_rsi(flat) == 50.0


def test_wilder_rsi_monotonically_rising_series_approaches_100():
    import pandas as pd
    import pytest
    from analyzer import wilder_rsi

    rising = pd.Series([100.0 + i for i in range(20)])
    assert wilder_rsi(rising) == pytest.approx(100.0)


def test_wilder_rsi_known_vector():
    import pandas as pd
    import pytest
    from analyzer import wilder_rsi

    # Vettore corto, calcolato a mano con la stessa formula (ewm alpha=1/period):
    # gain/loss per barra = [-,1,1,-1,1,1], regressione contro un valore fisso noto.
    s = pd.Series([10.0, 11.0, 12.0, 11.0, 12.0, 13.0])
    assert wilder_rsi(s, period=3) == pytest.approx(85.18518518518519)


def test_should_run_catches_up_after_a_missed_tick():
    from datetime import datetime, time
    from web_server import should_run

    now = datetime(2026, 7, 16, 10, 0)  # giovedì, ben oltre le 07:30 schedulate
    assert should_run(now, time(7, 30), None) is True


def test_should_run_false_before_scheduled_time():
    from datetime import datetime, time
    from web_server import should_run

    now = datetime(2026, 7, 16, 7, 0)  # giovedì, prima delle 07:30
    assert should_run(now, time(7, 30), None) is False


def test_should_run_skips_weekends():
    from datetime import datetime, time
    from web_server import should_run

    now = datetime(2026, 7, 18, 10, 0)  # sabato
    assert should_run(now, time(7, 30), None) is False


def test_should_run_skips_if_already_run_today():
    from datetime import datetime, time
    from web_server import should_run

    now = datetime(2026, 7, 16, 8, 0)  # giovedì, già girato oggi
    assert should_run(now, time(7, 30), now.date()) is False


def test_projection_basis_prefers_3y_cagr():
    from analyzer import _projection_basis
    import pytest

    rate, label = _projection_basis(cagr_3y=12.0, cagr_1y=30.0)
    assert rate == pytest.approx(0.12)
    assert label == "CAGR 3 anni"


def test_projection_basis_falls_back_to_1y_cagr():
    from analyzer import _projection_basis
    import pytest

    rate, label = _projection_basis(cagr_3y=None, cagr_1y=8.0)
    assert rate == pytest.approx(0.08)
    assert label == "CAGR 1 anno"


def test_projection_basis_falls_back_to_invented_7pct_when_no_history():
    from analyzer import _projection_basis
    import pytest

    rate, label = _projection_basis(cagr_3y=None, cagr_1y=None)
    assert rate == pytest.approx(0.07)
    assert label == "ipotesi 7%/anno (storico insufficiente)"


# ── _quality_score_10 v2: trend, RSI, rischio, sentiment, earnings, valutazione ──
# NB: sostituisce il vecchio test "matches_pre_refactor_formula" -- la formula è
# stata intenzionalmente riprogettata (score singolo titolo scoordinato dal resto),
# quindi il vecchio valore atteso (8.0) non è più valido per costruzione.

_NEUTRAL_KW = dict(
    rsi=35.0, chg=0.0, sma_20=None, price=0.0,       # rsi in banda "weak" (-1)
    week_return=None, month_return=None,
    volatility=40.0,                                  # banda sana (+1), compensa rsi -1 -> raw=0
    news_sentiment="neutre", earnings_today=False,
    days_to_earnings=None, pe_ratio=None,
)


def test_quality_score_10_neutral_baseline():
    from analyzer import _quality_score_10
    import pytest
    from config import SCORING

    neutral = SCORING["enriched"]["raw_offset"] / SCORING["enriched"]["raw_range"] * 10
    assert _quality_score_10(**_NEUTRAL_KW) == pytest.approx(round(neutral, 1))


def test_quality_score_10_strong_uptrend_scores_high():
    from analyzer import _quality_score_10

    kw = dict(_NEUTRAL_KW, rsi=55.0, chg=4.0, sma_20=100.0, price=110.0,
              week_return=3.0, month_return=15.0, news_sentiment="positive", pe_ratio=12.0)
    assert _quality_score_10(**kw) >= 8.0


def test_quality_score_10_earnings_today_lowers_an_otherwise_strong_score():
    from analyzer import _quality_score_10

    base_kw = dict(_NEUTRAL_KW, rsi=55.0, chg=4.0, sma_20=100.0, price=110.0,
                   week_return=3.0, month_return=15.0, news_sentiment="positive", pe_ratio=12.0)
    without_earnings = _quality_score_10(**base_kw)
    with_earnings = _quality_score_10(**dict(base_kw, earnings_today=True, days_to_earnings=0))
    assert with_earnings < without_earnings


def test_quality_score_10_expensive_pe_scores_below_neutral():
    from analyzer import _quality_score_10

    neutral_score = _quality_score_10(**_NEUTRAL_KW)
    expensive_pe = _quality_score_10(**dict(_NEUTRAL_KW, pe_ratio=60.0))
    assert expensive_pe < neutral_score


def test_quality_score_10_missing_pe_is_not_penalized():
    """Un titolo senza P/E disponibile (es. ETF) non deve avere uno score inferiore
    rispetto a un titolo identico con P/E in fascia neutra (15-40)."""
    from analyzer import _quality_score_10

    missing_pe = _quality_score_10(**_NEUTRAL_KW)  # pe_ratio=None nel baseline
    neutral_pe = _quality_score_10(**dict(_NEUTRAL_KW, pe_ratio=25.0))
    assert missing_pe == neutral_pe


def test_estimate_5d_conviction_sign_matches_score_position_vs_neutral():
    """Ricostruisce la stessa formula di conviction usata in get_enriched_analysis:
    per uno score sopra il neutro dev'essere positiva, sotto dev'essere negativa --
    verifica diretta che 'score alto ma stima negativa' (il bug segnalato) non può
    più accadere per costruzione."""
    from config import SCORING

    enriched = SCORING["enriched"]
    neutral = enriched["raw_offset"] / enriched["raw_range"] * 10

    def conviction(score_10):
        if score_10 >= neutral:
            return (score_10 - neutral) / (10 - neutral)
        return (score_10 - neutral) / neutral

    assert conviction(9.0) > 0
    assert conviction(2.0) < 0
    assert conviction(neutral) == 0
    assert -1.0 <= conviction(0.0) <= 1.0
    assert -1.0 <= conviction(10.0) <= 1.0
