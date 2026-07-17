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
