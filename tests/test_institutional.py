import pytest


SAMPLE_INFOTABLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>20471924668</value>
    <shrsOrPrnAmt>
      <sshPrnamt>80664820</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <votingAuthority><Sole>80664820</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>1000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>4000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <votingAuthority><Sole>4000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>COCA COLA CO</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>191216100</cusip>
    <value>21501063540</value>
    <shrsOrPrnAmt>
      <sshPrnamt>282722729</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <votingAuthority><Sole>282722729</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
</informationTable>
"""


def test_parse_infotable_xml_extracts_and_sums_split_rows():
    from institutional import _parse_infotable_xml

    rows = _parse_infotable_xml(SAMPLE_INFOTABLE_XML)
    by_issuer = {r["issuer_name"]: r for r in rows}

    assert set(by_issuer) == {"APPLE INC", "COCA COLA CO"}
    # Le due righe APPLE (stesso CUSIP, split per manager) vanno sommate in una sola posizione.
    assert by_issuer["APPLE INC"]["value_usd"] == pytest.approx(20471924668 + 1000000)
    assert by_issuer["APPLE INC"]["shares"] == 80664820 + 4000
    assert by_issuer["COCA COLA CO"]["value_usd"] == pytest.approx(21501063540)


def test_parse_infotable_xml_value_is_whole_dollars_not_thousands():
    """Guardia di regressione: lo schema XML corrente riporta <value> in dollari
    interi, non in migliaia — un vecchio bug moltiplicava per 1000 e gonfiava
    i valori di 1000x (verificato dal vivo contro un filing SEC reale)."""
    from institutional import _parse_infotable_xml

    rows = _parse_infotable_xml(SAMPLE_INFOTABLE_XML)
    coke = next(r for r in rows if r["issuer_name"] == "COCA COLA CO")
    per_share = coke["value_usd"] / coke["shares"]
    assert 1 < per_share < 1000  # un prezzo per azione plausibile, non miliardi/azione


@pytest.mark.parametrize("a,b,expected", [
    ("Apple Inc.", "APPLE INC", True),
    ("Alphabet Inc.", "ALPHABET INC-CL A", True),
    ("Meta Platforms, Inc.", "META PLATFORMS INC", True),
    ("Exxon Mobil Corporation", "EXXON MOBIL CORP", True),
    # Falso positivo reale trovato testando dal vivo: nomi corti e non correlati
    # (5 lettere ciascuno) toccavano ~0.6 di similarità con la soglia vecchia.
    ("Tesla, Inc.", "INTEL CORP", False),
    ("Intel Corporation", "TESLA INC", False),
    ("NVIDIA Corporation", "COCA COLA CO", False),
])
def test_names_match_company_name_matching(a, b, expected):
    from institutional import _names_match

    ok, _score = _names_match(a, b)
    assert ok is expected


def test_match_ticker_in_holdings_finds_and_ranks_by_value():
    from institutional import match_ticker_in_holdings

    all_holdings = {
        "cik1": {"filer": "Fund A", "filed_date": "2026-05-01",
                 "holdings": [{"issuer_name": "APPLE INC", "cusip": "x", "value_usd": 5_000_000_000, "shares": 100}]},
        "cik2": {"filer": "Fund B", "filed_date": "2026-05-02",
                 "holdings": [{"issuer_name": "Apple Inc", "cusip": "x", "value_usd": 9_000_000_000, "shares": 200},
                              {"issuer_name": "TESLA INC", "cusip": "y", "value_usd": 1_000_000_000, "shares": 50}]},
    }
    matches = match_ticker_in_holdings("AAPL", "Apple Inc.", all_holdings)
    assert [m["filer"] for m in matches] == ["Fund B", "Fund A"]  # ordinato per valore desc
    assert all(m["issuer_name_in_filing"] != "TESLA INC" for m in matches)


def _prices_df(days, **series):
    import pandas as pd

    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    return pd.DataFrame({k: v for k, v in series.items()}, index=idx)


def test_sector_rotation_ranks_outperformer_first():
    from institutional import _sector_rotation_from_prices

    n = 70
    flat = [100.0] * n
    spy = flat.copy()
    winner = flat.copy()
    winner[-1] = 130.0  # +30% ultimo giorno rispetto al resto, batte nettamente SPY
    loser = flat.copy()
    loser[-1] = 90.0

    closes = _prices_df(n, SPY=spy, XLK=winner, XLE=loser)
    rows = _sector_rotation_from_prices(closes)
    by_etf = {r["etf"]: r for r in rows}

    assert by_etf["XLK"]["rank_1m"] == 1
    assert by_etf["XLK"]["rel_to_spy_1m"] > 0
    assert by_etf["XLE"]["rel_to_spy_1m"] < 0
    assert by_etf["XLK"]["rank_1m"] < by_etf["XLE"]["rank_1m"]


def test_sector_rotation_skips_when_spy_missing():
    from institutional import _sector_rotation_from_prices

    closes = _prices_df(30, XLK=[100.0] * 30)
    assert _sector_rotation_from_prices(closes) == []


def test_unusual_volume_ratio_flags_spike():
    import pandas as pd
    from institutional import _unusual_volume_ratio

    volumes = pd.Series([1_000_000] * 20 + [3_500_000])
    ratio = _unusual_volume_ratio(volumes)
    assert ratio == pytest.approx(3.5, rel=0.05)


def test_unusual_volume_ratio_none_on_too_little_history():
    import pandas as pd
    from institutional import _unusual_volume_ratio

    assert _unusual_volume_ratio(pd.Series([1_000_000, 1_100_000])) is None


def test_get_smart_money_context_shape_with_no_data():
    from institutional import get_smart_money_context

    ctx = get_smart_money_context("XYZ", "Nonexistent Co", {}, [], sector=None, volume_ratio=None)
    assert ctx["institutional"]["holder_count"] == 0
    assert ctx["sector_rotation"] is None
    assert ctx["unusual_volume"]["flag"] is False
    assert ctx["congress"]["available"] is False


# ─── Congress trading (FMP) ─────────────────────────────────────────────────
FMP_SAMPLE_ROWS = [
    {"symbol": "AAPL", "assetType": "Stock", "firstName": "Dan", "lastName": "Crenshaw",
     "office": "Dan Crenshaw", "transactionDate": "2026-06-01", "disclosureDate": "2026-07-17",
     "type": "Sale", "amount": "$1,001 - $15,000", "assetDescription": "Apple Inc"},
    {"symbol": "FMCC", "assetType": "Municipal Security", "firstName": "Debbie", "lastName": "Dingell",
     "office": "Debbie Dingell", "transactionDate": "2026-07-14", "disclosureDate": "2026-07-23",
     "type": "Purchase", "amount": "$15,001 - $50,000", "assetDescription": "Federal Home Loan Mortgage Corp"},
    {"symbol": "TSLA", "assetType": "Stock Option", "firstName": "Some", "lastName": "Trader",
     "office": "Some Trader", "transactionDate": "2026-06-10", "disclosureDate": "2026-07-01",
     "type": "Purchase", "amount": "$50,001 - $100,000", "assetDescription": "Tesla Inc Option"},
    {"symbol": "", "assetType": "Stock", "firstName": "No", "lastName": "Symbol",
     "transactionDate": "2026-06-10", "disclosureDate": "2026-07-01", "type": "Sale",
     "amount": "$1,001 - $15,000", "assetDescription": "Unknown"},
]


def test_normalize_congress_rows_filters_options_and_municipal_bonds():
    from institutional import _normalize_congress_rows

    rows = _normalize_congress_rows(FMP_SAMPLE_ROWS, "Senato")
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"AAPL"}  # FMCC (municipal), TSLA (option), '' (no symbol) esclusi


def test_normalize_congress_rows_maps_fields():
    from institutional import _normalize_congress_rows

    rows = _normalize_congress_rows(FMP_SAMPLE_ROWS[:1], "Camera")
    r = rows[0]
    assert r == {
        "ticker": "AAPL", "chamber": "Camera", "politician": "Dan Crenshaw",
        "transaction_date": "2026-06-01", "disclosure_date": "2026-07-17",
        "type": "Sale", "amount": "$1,001 - $15,000", "asset": "Apple Inc",
    }


def test_normalize_congress_rows_empty_input():
    from institutional import _normalize_congress_rows

    assert _normalize_congress_rows([], "Senato") == []
    assert _normalize_congress_rows(None, "Senato") == []


def test_match_ticker_congress_trades_filters_by_ticker():
    from institutional import match_ticker_congress_trades

    trades = [
        {"ticker": "AAPL", "politician": "A"},
        {"ticker": "MSFT", "politician": "B"},
        {"ticker": "aapl", "politician": "C"},  # case-insensitive
    ]
    matched = match_ticker_congress_trades("AAPL", trades)
    assert [m["politician"] for m in matched] == ["A", "C"]
    assert match_ticker_congress_trades("AAPL", None) == []
    assert match_ticker_congress_trades("AAPL", []) == []
