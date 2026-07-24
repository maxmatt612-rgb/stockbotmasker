"""Smart money: 13F istituzionali/hedge fund, rotazione settoriale, volume anomalo.

Fonti: SEC EDGAR (13F, gratis/ufficiale, dati trimestrali con ~45gg di ritardo per
legge) e yfinance (rotazione settoriale, volume — nessuna dipendenza nuova). Il
Congress trading (fetch_congress_trades_recent) è fase 2, bloccato su una API key
FMP non ancora confermata: ritorna None finché non è verificato che il piano free
serva davvero quegli endpoint.

Il caching (TTL, persistenza su disco) vive in web_server.py, non qui: questo modulo
espone solo funzioni pure/di fetch, così get_smart_money_context() può essere
richiamata a costo quasi zero per ogni ticker analizzato riusando dataset già
scaricati una volta, invece di rifare una chiamata SEC/FMP per titolo."""
import difflib
import json
import os
import re
import time
import urllib.error
import urllib.request

SEC_UA = "MaskerStockBot/1.0 (mancordeveloping@gmail.com)"

# Filer 13F tracciati — CIK verificati live contro SEC EDGAR. Un fondo/entità è
# incluso se ha uno storico di filing 13F-HR reale (controllato uno per uno).
TRACKED_FILERS = {
    "0001067983": "Berkshire Hathaway",
    "0001037389": "Renaissance Technologies",
    "0001350694": "Bridgewater Associates",
    "0001423053": "Citadel Advisors",
    "0001649339": "Scion Asset Management (Michael Burry)",
    "0001167483": "Tiger Global Management",
    "0001029160": "Soros Fund Management",
    "0001697748": "ARK Investment Management",
    "0001336528": "Pershing Square",
    "0001045810": "NVIDIA Corp (portafoglio proprio)",
    "0001652044": "Alphabet Inc (portafoglio proprio)",
}

SECTOR_ETFS = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Health Care",
    "XLY": "Consumer Discretionary", "XLP": "Consumer Staples", "XLI": "Industrials",
    "XLB": "Materials", "XLU": "Utilities", "XLRE": "Real Estate", "XLC": "Communication Services",
}

def _fmp_api_key():
    """Letta ad ogni chiamata, non una costante a livello di modulo: web_server.py
    importa institutional PRIMA di chiamare load_dotenv() (l'import sta in cima
    al file, load_dotenv() qualche riga sotto) — una costante valutata all'import
    catturerebbe sempre None anche con la chiave presente nel .env."""
    return os.getenv("FMP_API_KEY")


# ─── HTTP helper (stesso stile di web_server.py: urllib.request inline, no requests) ──
def _sec_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _sec_get_json(url: str, timeout: int = 15):
    try:
        return json.loads(_sec_get(url, timeout))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


# ─── Normalizzazione nomi società (per matching senza database CUSIP→ticker) ──────
_SUFFIX_RE = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|LLC|PLC|"
    r"CL\s?[A-Z]|CLASS\s?[A-Z]|COM|SPONSORED|ADR|ADS|SHS|SH|HOLDINGS?|GROUP)\b\.?",
    re.I,
)


def _normalize_name(name: str) -> str:
    n = (name or "").upper()
    n = _SUFFIX_RE.sub(" ", n)
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _names_match(a: str, b: str, threshold: float = 0.8) -> tuple[bool, float]:
    """threshold=0.8 e non 0.6: nomi corti e non correlati (es. 'TESLA' vs
    'INTEL', entrambe 5 lettere) toccano ~0.6 di ratio per puro caso — verificato
    dal vivo, era un falso positivo reale nel primo giro di test."""
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return False, 0.0
    if na == nb or (len(na) >= 4 and na in nb) or (len(nb) >= 4 and nb in na):
        return True, 1.0
    if len(na) < 4 or len(nb) < 4:
        return False, 0.0  # troppo corto per un fuzzy-match affidabile
    score = difflib.SequenceMatcher(None, na, nb).ratio()
    return score >= threshold, score


# ─── CIK verification (una tantum per processo, non blocca in caso di mismatch) ──
def verify_filer_ciks(filers: dict = None) -> dict:
    filers = filers or TRACKED_FILERS
    out = {}
    for cik, expected in filers.items():
        data = _sec_get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        actual = (data or {}).get("name")
        ok, score = _names_match(expected, actual or "")
        out[cik] = {"expected": expected, "actual": actual, "ok": ok, "score": round(score, 2)}
        if not ok:
            print(f"[institutional] ATTENZIONE: CIK {cik} atteso '{expected}', SEC dice '{actual}'")
    return out


# ─── 13F: trova l'ultimo filing e ne fa il parsing ────────────────────────────────
def get_filer_latest_13f_accession(cik: str):
    """Ritorna (accession_no_con_trattini, filing_date) dell'ultimo 13F-HR, o None."""
    data = _sec_get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    if not data:
        return None
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    for i, form in enumerate(forms):
        if form.startswith("13F-HR"):
            return accessions[i], dates[i]
    return None


def _filing_index_items(cik: str, accession_no_dashes: str) -> list:
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/index.json"
    data = _sec_get_json(url)
    return ((data or {}).get("directory") or {}).get("item") or []


def _find_infotable_filename(items: list):
    """Il file XML con le posizioni non ha un nome fisso (es. 'infotable.xml' per
    alcuni filer, un nome numerico generato per altri) — solo 'primary_doc.xml'
    (la cover page/riepilogo) è uno standard affidabile da ESCLUDERE."""
    xml_names = [it.get("name") for it in items if (it.get("name") or "").lower().endswith(".xml")]
    for name in xml_names:
        if "infotable" in name.lower():
            return name
    candidates = [n for n in xml_names if n.lower() != "primary_doc.xml"]
    return candidates[0] if candidates else None


def _parse_infotable_xml(xml_bytes: bytes) -> list:
    """XML → [{issuer_name, cusip, value_usd, shares}], una riga per posizione
    REALE (righe split per manager/voting-authority sullo stesso CUSIP vengono
    sommate). Nessuna dipendenza esterna (xml.etree stdlib), namespace-agnostico
    via '{*}tag' (il namespace del 13F infotable varia leggermente tra filer/anni).

    Il campo <value> è in DOLLARI interi (non migliaia) nello schema XML corrente
    — verificato dal vivo confrontando value/shares con un prezzo per azione
    plausibile; NON moltiplicare per 1000."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_bytes)
    rows = {}  # (cusip or issuer_name) -> {issuer_name, cusip, value_usd, shares}
    for row in root.findall(".//{*}infoTable"):
        def _text(tag):
            el = row.find(f"{{*}}{tag}")
            return el.text.strip() if el is not None and el.text else None

        issuer = _text("nameOfIssuer")
        cusip = _text("cusip")
        value_raw = _text("value")
        shares_el = row.find("{*}shrsOrPrnAmt/{*}sshPrnamt")
        shares_raw = shares_el.text.strip() if shares_el is not None and shares_el.text else None
        if not issuer:
            continue
        try:
            value_usd = float(value_raw) if value_raw else 0.0
        except ValueError:
            value_usd = 0.0
        try:
            shares = int(float(shares_raw)) if shares_raw else 0
        except ValueError:
            shares = 0
        key = cusip or issuer
        if key in rows:
            rows[key]["value_usd"] += value_usd
            rows[key]["shares"] += shares
        else:
            rows[key] = {"issuer_name": issuer, "cusip": cusip, "value_usd": value_usd, "shares": shares}
    return list(rows.values())


def fetch_13f_holdings(cik: str):
    """Ultimo 13F-HR di un filer: {filer, filed_date, holdings:[...]} o None."""
    latest = get_filer_latest_13f_accession(cik)
    if not latest:
        return None
    accession, filed_date = latest
    accession_no_dashes = accession.replace("-", "")
    items = _filing_index_items(cik, accession_no_dashes)
    filename = _find_infotable_filename(items)
    if not filename:
        return None
    xml_bytes = _sec_get(
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{filename}"
    )
    holdings = _parse_infotable_xml(xml_bytes)
    holdings.sort(key=lambda h: h.get("value_usd") or 0, reverse=True)
    return {"filer": TRACKED_FILERS.get(cik, cik), "filed_date": filed_date, "holdings": holdings}


def refresh_all_13f_holdings(filers: dict = None) -> dict:
    """Rifà il giro di tutti i filer tracciati. Un filer che fallisce non blocca gli
    altri. Pausa breve tra le richieste per non martellare SEC (loro limite è
    ~10 req/sec, qui restiamo molto sotto)."""
    filers = filers or TRACKED_FILERS
    out = {}
    for cik in filers:
        try:
            result = fetch_13f_holdings(cik)
            if result:
                out[cik] = result
        except Exception as e:
            print(f"[institutional] 13F fallito per {filers[cik]} ({cik}): {e}")
        time.sleep(0.2)
    return out


def match_ticker_in_holdings(ticker: str, company_name: str, all_holdings: dict) -> list:
    """Quali filer tracciati detengono questo ticker, per fuzzy-match sul nome
    (i 13F riportano CUSIP+nome, non il ticker — non esiste un database
    CUSIP→ticker gratuito)."""
    matches = []
    for cik, entry in (all_holdings or {}).items():
        for h in entry.get("holdings", []):
            ok, score = _names_match(company_name, h.get("issuer_name") or "")
            if ok:
                matches.append({
                    "filer": entry.get("filer"),
                    "filed_date": entry.get("filed_date"),
                    "issuer_name_in_filing": h.get("issuer_name"),
                    "value_usd": h.get("value_usd"),
                    "shares": h.get("shares"),
                    "match_score": round(score, 2),
                })
    matches.sort(key=lambda m: m.get("value_usd") or 0, reverse=True)
    return matches


# ─── Rotazione settoriale ──────────────────────────────────────────────────────
def _sector_rotation_from_prices(closes) -> list:
    """closes: DataFrame indicizzato per data, colonne = ticker ETF settoriali + SPY.
    Separata dall'I/O (yf.download) così è testabile con dati sintetici."""
    if "SPY" not in closes.columns:
        return []
    spy = closes["SPY"].dropna()

    def _ret(s, days):
        s = s.dropna()
        if len(s) <= days:
            return None
        return float(s.iloc[-1] / s.iloc[-1 - days] - 1) * 100

    spy_1w, spy_1m, spy_3m = _ret(spy, 5), _ret(spy, 21), _ret(spy, 63)
    rows = []
    for etf, sector in SECTOR_ETFS.items():
        if etf not in closes.columns:
            continue
        s = closes[etf]
        p1w, p1m, p3m = _ret(s, 5), _ret(s, 21), _ret(s, 63)
        rel_1m = (p1m - spy_1m) if (p1m is not None and spy_1m is not None) else None
        rows.append({
            "etf": etf, "sector": sector,
            "perf_1w": p1w, "perf_1m": p1m, "perf_3m": p3m,
            "rel_to_spy_1m": rel_1m,
        })
    ranked = sorted(rows, key=lambda r: (r["rel_to_spy_1m"] is None, -(r["rel_to_spy_1m"] or -999)))
    for i, r in enumerate(ranked, 1):
        r["rank_1m"] = i
    return ranked


def compute_sector_rotation() -> list:
    import yfinance as yf
    import pandas as pd

    tickers = list(SECTOR_ETFS) + ["SPY"]
    raw = yf.download(tickers, period="4mo", interval="1d", auto_adjust=True, progress=False)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    return _sector_rotation_from_prices(closes)


# ─── Volume anomalo ─────────────────────────────────────────────────────────────
def _unusual_volume_ratio(volumes) -> float:
    """volumes: Series di volumi giornalieri, l'ultimo valore è la sessione corrente.
    Separata dall'I/O così è testabile con dati sintetici."""
    v = volumes.dropna()
    if len(v) < 6:
        return None
    today = float(v.iloc[-1])
    window = v.iloc[-21:-1] if len(v) >= 21 else v.iloc[:-1]
    avg = float(window.mean())
    if avg <= 0:
        return None
    return today / avg


def compute_unusual_volume(tickers: list, threshold: float = 2.0) -> list:
    import yfinance as yf
    import pandas as pd

    raw = yf.download(tickers, period="2mo", interval="1d", auto_adjust=True, progress=False)
    vols = raw["Volume"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Volume"]].rename(
        columns={"Volume": tickers[0]}
    )
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(
        columns={"Close": tickers[0]}
    )
    out = []
    for t in tickers:
        if t not in vols.columns:
            continue
        ratio = _unusual_volume_ratio(vols[t])
        if ratio is None:
            continue
        c = closes[t].dropna()
        if c.empty:
            continue
        out.append({
            "ticker": t,
            "volume_ratio": round(ratio, 2),
            "flag": ratio >= threshold,
            "today_volume": int(vols[t].dropna().iloc[-1]),
            "current_price": round(float(c.iloc[-1]), 2),
            "day_change_pct": round((float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100, 2) if len(c) > 1 else 0.0,
        })
    out.sort(key=lambda r: r["volume_ratio"], reverse=True)
    return out


# ─── Congress trading — via Financial Modeling Prep (piano free) ───────────────
# Verificato dal vivo: /stable/senate-latest e /stable/house-latest funzionano
# sul piano gratuito (200, dati reali). Il parametro 'symbol' viene IGNORATO da
# questi endpoint (ritorna comunque la lista intera) e paginazione/limit sono
# a pagamento (402) — quindi si prende il "latest" così com'è (~100 trade a
# camera, qualche mese di copertura) e si filtra per ticker lato client.
# Gli endpoint dedicati per-simbolo (/stable/senate-trading, /stable/house-trading)
# danno 404 sul piano free — non usarli.
def _fmp_get(path: str):
    key = _fmp_api_key()
    if not key:
        return None
    url = f"https://financialmodelingprep.com/stable/{path}"
    sep = "&" if "?" in path else "?"
    try:
        data = json.loads(_sec_get(f"{url}{sep}apikey={key}", timeout=15))
        return data if isinstance(data, list) else None
    except Exception as e:
        print(f"[institutional] FMP {path} fallito: {e}")
        return None


def _normalize_congress_rows(rows: list, chamber: str) -> list:
    """Righe grezze FMP → schema interno. Separata dall'I/O così è testabile
    senza rete. Esclude opzioni/municipal bond: non sono 'azioni' per il
    cross-reference per ticker."""
    out = []
    for r in rows or []:
        sym = (r.get("symbol") or "").strip()
        asset_type = (r.get("assetType") or "").strip()
        if not sym or asset_type in ("Stock Option", "Municipal Security"):
            continue
        out.append({
            "ticker": sym.upper(),
            "chamber": chamber,
            "politician": f"{r.get('firstName','')} {r.get('lastName','')}".strip() or r.get("office", "N/D"),
            "transaction_date": r.get("transactionDate"),
            "disclosure_date": r.get("disclosureDate"),
            "type": r.get("type"),
            "amount": r.get("amount"),
            "asset": r.get("assetDescription"),
        })
    return out


def fetch_congress_trades_recent():
    """Trade recenti di Senato+Camera, formato normalizzato. None se la chiave
    manca o entrambe le chiamate falliscono."""
    if not _fmp_api_key():
        return None
    senate = _fmp_get("senate-latest") or []
    house = _fmp_get("house-latest") or []
    if not senate and not house:
        return None

    return _normalize_congress_rows(senate, "Senato") + _normalize_congress_rows(house, "Camera")


def diversify_congress_feed(trades: list, max_per_politician: int = 3, limit: int = 40) -> list:
    """Il feed 'ultimi trade del Congresso' perde senso se una singola disclosure
    bulk di un politico (decine di trade nello stesso filing — capita spesso, es.
    Alan Armstrong con 96 trade su un totale di 200 osservato dal vivo) riempie da
    sola tutti gli slot mostrati. Round-robin per politico: prima il trade più
    recente di OGNI politico, poi il secondo di ognuno, ecc. (fino a
    max_per_politician), così il feed mostra varietà invece di una sola persona."""
    from collections import defaultdict

    by_politician = defaultdict(list)
    for tr in trades or []:
        by_politician[tr.get("politician") or "?"].append(tr)
    for lst in by_politician.values():
        lst.sort(key=lambda t: t.get("transaction_date") or "", reverse=True)
    # politici ordinati per il trade più recente in assoluto, così chi ha attività
    # più fresca appare prima nel round-robin
    politicians = sorted(by_politician, key=lambda p: by_politician[p][0].get("transaction_date") or "", reverse=True)

    out = []
    for round_idx in range(max_per_politician):
        for p in politicians:
            if len(out) >= limit:
                return out
            if len(by_politician[p]) > round_idx:
                out.append(by_politician[p][round_idx])
    return out


def match_ticker_congress_trades(ticker: str, all_trades) -> list:
    if not all_trades:
        return []
    return [tr for tr in all_trades if (tr.get("ticker") or "").upper() == ticker.upper()]


def group_congress_by_politician(trades: list) -> list:
    """Tutti i trade raggruppati per politico (non troncato, non diversificato —
    a differenza di diversify_congress_feed che serve per il feed 'attività
    recente', questo serve per la lista cliccabile 'vedi tutti i trade di X').
    Ogni gruppo ha i trade ordinati dal più recente, ed è ordinato per numero di
    trade decrescente."""
    from collections import defaultdict

    by_politician = defaultdict(list)
    for tr in trades or []:
        by_politician[tr.get("politician") or "?"].append(tr)
    groups = []
    for politician, lst in by_politician.items():
        lst.sort(key=lambda t: t.get("transaction_date") or "", reverse=True)
        chamber = lst[0].get("chamber") if lst else None
        groups.append({
            "politician": politician,
            "chamber": chamber,
            "trade_count": len(lst),
            "last_trade_date": lst[0].get("transaction_date") if lst else None,
            "trades": lst,
        })
    groups.sort(key=lambda g: g["trade_count"], reverse=True)
    return groups


# ─── Aggregatore per-ticker: nessuna nuova chiamata esterna, solo cross-reference
# contro dataset già scaricati/cachati da web_server.py ──────────────────────────
def get_smart_money_context(ticker: str, company_name: str, all_holdings: dict,
                             sector_rotation: list, sector: str = None,
                             volume_ratio: float = None, congress_trades=None) -> dict:
    holders = match_ticker_in_holdings(ticker, company_name, all_holdings or {})
    sector_rank = None
    if sector:
        sector_rank = next((s for s in (sector_rotation or []) if s.get("sector") == sector), None)
    return {
        "institutional": {
            "holders": holders,
            "holder_count": len(holders),
        },
        "sector_rotation": sector_rank,
        "unusual_volume": {
            "ratio": volume_ratio,
            "flag": bool(volume_ratio and volume_ratio >= 2.0),
        },
        "congress": {
            "available": bool(congress_trades),
            "trades": congress_trades or [],
        },
    }
