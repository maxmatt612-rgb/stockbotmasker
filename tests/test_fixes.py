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
