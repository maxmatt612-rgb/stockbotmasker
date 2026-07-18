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
