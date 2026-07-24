import pytest


def _scan_sc():
    from config import SCORING
    return SCORING["scan"]


# ─── RSI scoring (scan_cheap_stocks) — bug: due bande "grigie" non coperte ────
@pytest.mark.parametrize("rsi,expected_key", [
    (50, "rsi_ideal_pts"),        # zona ideale (45-65)
    (45, "rsi_ideal_pts"),        # bordo incluso
    (65, "rsi_ideal_pts"),        # bordo incluso
    (42, "rsi_pullback_pts"),     # pullback (40-45)
    (80, "rsi_extended_pts"),     # esteso (>75)
    (72, "rsi_overbought_pts"),   # ipercomprato (70-75]
    (35, "rsi_lower_gray_pts"),   # BUG FIX: prima 30<=rsi<40 non prendeva nulla (0)
    (39, "rsi_lower_gray_pts"),
    (68, "rsi_upper_gray_pts"),   # BUG FIX: prima 65<rsi<=70 non prendeva nulla (0)
    (70, "rsi_upper_gray_pts"),
    (20, "rsi_falling_knife_pts"),  # falling knife (<30)
])
def test_scan_rsi_points_matches_expected_band(rsi, expected_key):
    from analyzer import _scan_rsi_points

    sc = _scan_sc()
    assert _scan_rsi_points(rsi, sc) == sc[expected_key]


def test_scan_rsi_points_no_gap_across_full_range():
    """Regressione diretta del bug: prima della fix, RSI in (65,70] e [30,40)
    ritornava silenziosamente 0 (né bonus né penalità) senza che fosse una
    scelta di design — la catena ora copre 0-100 senza buchi."""
    from analyzer import _scan_rsi_points

    sc = _scan_sc()
    for rsi_hundredths in range(0, 1001):  # step 0.1 da 0 a 100
        rsi = rsi_hundredths / 10
        pts = _scan_rsi_points(rsi, sc)
        # ogni punto deve corrispondere a uno dei punteggi noti, mai un default
        # "silenzioso" diverso da quelli esplicitamente configurati
        assert pts in {
            sc["rsi_ideal_pts"], sc["rsi_pullback_pts"], sc["rsi_extended_pts"],
            sc["rsi_overbought_pts"], sc["rsi_upper_gray_pts"],
            sc["rsi_falling_knife_pts"], sc["rsi_lower_gray_pts"],
        }, f"rsi={rsi} ha ritornato {pts}, non uno score configurato"


def test_scan_rsi_points_nan_is_neutral_not_crash():
    from analyzer import _scan_rsi_points

    assert _scan_rsi_points(float("nan"), _scan_sc()) == 0


# ─── Watchdog dello scan (evita che _scan_in_progress resti bloccato per sempre) ──
def test_scan_slot_busy_true_while_within_watchdog_window():
    import time
    import web_server as ws

    ws._scan_in_progress = True
    ws._scan_started_at = time.monotonic()  # appena iniziato
    try:
        assert ws._scan_slot_busy() is True
        assert ws._scan_in_progress is True  # non liberato: è ancora nella finestra normale
    finally:
        ws._scan_in_progress = False


def test_scan_slot_busy_frees_a_stuck_scan_past_the_watchdog():
    import time
    import web_server as ws

    ws._scan_in_progress = True
    ws._scan_started_at = time.monotonic() - ws._SCAN_WATCHDOG_SEC - 10  # oltre la soglia
    try:
        assert ws._scan_slot_busy() is False
        assert ws._scan_in_progress is False  # il watchdog l'ha liberato
    finally:
        ws._scan_in_progress = False


def test_scan_slot_busy_false_when_no_scan_running():
    import web_server as ws

    ws._scan_in_progress = False
    assert ws._scan_slot_busy() is False
