"""Web dashboard per Stock Bot — FastAPI server."""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from analyzer import get_enriched_analysis, scan_cheap_stocks

STATIC = Path(__file__).parent / "static"
HISTORY_FILE = Path(__file__).parent / "analysis_history.json"
WEB_USERS_FILE = Path(__file__).parent / "web_users.json"

app = FastAPI(title="Stock Bot Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ─── Groq AI client ───────────────────────────────────────────────────────────
_GROQ_KEY = os.getenv("GROQ_API_KEY")
try:
    from groq import AsyncGroq
    groq_client = AsyncGroq(api_key=_GROQ_KEY) if _GROQ_KEY else None
except Exception:
    groq_client = None

# ─── Autenticazione Telegram + JWT ───────────────────────────────────────────
# Variabili env richieste per il login:
#   BOT_TOKEN    — token del bot (già usato da bot.py)
#   BOT_USERNAME — username del bot SENZA @ (es. MaskerStockBot)
#   JWT_SECRET   — chiave segreta per JWT (genera con: python -c "import secrets;print(secrets.token_hex(32))")
_BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
_JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_hex(32)

try:
    import jwt as _pyjwt
    _JWT_OK = True
except ImportError:
    _JWT_OK = False


def _jwt_create(payload: dict) -> str:
    if not _JWT_OK:
        return ""
    now = int(datetime.now(timezone.utc).timestamp())
    p = {**payload, "iat": now, "exp": now + 86400 * 30}
    return _pyjwt.encode(p, _JWT_SECRET, algorithm="HS256")


def _jwt_decode(token: str):
    if not _JWT_OK:
        return None
    try:
        return _pyjwt.decode(token, _JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def _tg_verify(raw: dict) -> bool:
    """Verifica firma hash Telegram Login Widget."""
    if not _BOT_TOKEN:
        return True  # dev mode: skip (solo in locale!)
    check_hash = raw.get("hash", "")
    data_str = "\n".join(f"{k}={v}" for k, v in sorted(raw.items()) if k != "hash")
    secret = hashlib.sha256(_BOT_TOKEN.encode()).digest()
    computed = hmac.new(secret, data_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, check_hash)


def _get_token(authorization: Optional[str]):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return _jwt_decode(authorization[7:])


def _read_users() -> dict:
    if not WEB_USERS_FILE.exists():
        return {}
    try:
        return json.loads(WEB_USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_users(d: dict):
    WEB_USERS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── Cache semplice in memoria ────────────────────────────────────────────────
_cache: dict[str, dict] = {}
_SCAN_TTL     = 300    # 5 min — scanner
_STOCK_TTL    = 180    # 3 min — analisi singola
_AI_TTL       = 600    # 10 min — AI (chiamata Groq costosa)
_FORECAST_TTL = 14400  # 4 h — previsione 7 giorni
_FX_TTL       = 3600   # 1 h — tasso di cambio EUR/USD


def _cached(key: str, ttl: int):
    e = _cache.get(key)
    if e and time.monotonic() - e["ts"] < ttl:
        return e["data"]
    return None


def _store(key: str, data: Any):
    _cache[key] = {"data": data, "ts": time.monotonic()}


# ─── Serializzazione sicura (numpy → Python native) ──────────────────────────
def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    try:
        import numpy as np
        if isinstance(obj, np.integer):  return int(obj)
        if isinstance(obj, np.floating): return None if (obj != obj) else float(obj)
        if isinstance(obj, np.ndarray):  return obj.tolist()
        if isinstance(obj, np.bool_):    return bool(obj)
    except ImportError:
        pass
    if isinstance(obj, float):
        import math
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj


# ─── Ticker per area geografica (Top/Flop 5 per regione) ─────────────────────
_MOVERS_TICKERS: dict = {
    "usa": [
        "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","AMD","INTC",
        "ORCL","CRM","NFLX","QCOM","TXN","MU",
        "JPM","BAC","GS","V","MA","WFC","BLK","MS",
        "JNJ","UNH","LLY","ABBV","PFE","MRK","TMO","ISRG",
        "WMT","HD","COST","PG","NKE","DIS","MCD","SBUX","TGT",
        "XOM","CVX","BA","CAT","GE","HON","MP",
    ],
    "europa": [
        "ASML","SAP","STM","ERIC",
        "AZN","SNY","NVS",
        "SHEL","BP","EQNR",
        "UBS","ING","BBVA","SAN",
    ],
    "asia": [
        "TSM","TM","SONY","BIDU","JD","BABA",
        "INFY","WIT","HDB",
        "SE","MELI","GRAB",
    ],
}

# Flat list per backwards-compat (/api/heatmap rimane funzionante)
_HEATMAP_TICKERS = [t for tlist in _MOVERS_TICKERS.values() for t in tlist]

_INTRADAY_INTERVALS = {"1m","2m","5m","15m","30m","60m","90m","1h"}


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@app.get("/api/scan")
async def api_scan(top: int = 10):
    key = f"scan:{top}"
    if (c := _cached(key, _SCAN_TTL)) is not None:
        return c
    results = await asyncio.to_thread(scan_cheap_stocks, 20.0, top)
    clean = _clean(results or [])
    _store(key, clean)
    return clean


@app.get("/api/stock/{ticker}")
async def api_stock(ticker: str):
    t = ticker.upper()
    key = f"stock:{t}"
    if (c := _cached(key, _STOCK_TTL)) is not None:
        return c
    data = await asyncio.to_thread(get_enriched_analysis, t)
    if not data:
        return JSONResponse({"error": "ticker non trovato"}, status_code=404)
    clean = _clean(data)
    _store(key, clean)
    return clean


@app.get("/api/stock/{ticker}/history")
async def api_stock_history(ticker: str, period: str = "1mo"):
    t = ticker.upper()
    key = f"history:{t}:{period}"
    if (c := _cached(key, _STOCK_TTL)) is not None:
        return c

    def _get():
        import yfinance as yf
        h = yf.Ticker(t).history(period=period)
        if h.empty:
            return []
        return [
            {"date": str(idx.date()), "close": round(float(cl), 4)}
            for idx, cl in zip(h.index, h["Close"])
            if cl == cl
        ]

    result = await asyncio.to_thread(_get)
    _store(key, result)
    return result


@app.get("/api/stock/{ticker}/ai")
async def api_ai(ticker: str):
    """Analisi AI: perché investire, rischi, conclusione SI/NO."""
    t = ticker.upper()
    key = f"ai:{t}"
    if (c := _cached(key, _AI_TTL)) is not None:
        return c

    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)

    data = await asyncio.to_thread(get_enriched_analysis, t)
    if not data:
        return JSONResponse({"error": "ticker non trovato"}, status_code=404)
    data = _clean(data)

    prompt = (
        f"Analisi dell'azione {data['ticker']} ({data.get('name', data['ticker'])}) per un investitore retail:\n"
        f"- Prezzo: ${data['current_price']:.2f}, oggi {data['day_change_pct']:+.1f}%\n"
        f"- RSI: {data.get('rsi', 50):.0f}, Rischio: {data.get('risk_level', 'N/D')}, "
        f"Volatilità: {data.get('volatility', 0):.0f}%\n"
        f"- Settimana: {(data.get('week_return') or 0):+.1f}%, "
        f"Mese: {(data.get('month_return') or 0):+.1f}%\n"
        f"- Notizie: {data.get('news_sentiment_label', 'Neutre')}\n"
        f"- Prossimi earnings: {data.get('next_earnings_str', 'N/D')}\n\n"
        "Rispondi SOLO con questo formato esatto (italiano, conciso, max 2 righe per sezione):\n"
        "PERCHE_SI: [2-3 motivi concreti per cui potrebbe valere la pena investire]\n"
        "RISCHI: [2-3 rischi specifici e reali da considerare]\n"
        "CONCLUSIONE: [1-2 frasi dirette — concludi con consiglio chiaro]\n"
        "VERDICT: SI oppure NO oppure NEUTRO"
    )

    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=350,
            messages=[
                {"role": "system", "content": "Sei un analista finanziario. Rispondi in italiano, diretto e sintetico. Non dare mai consigli finanziari definitivi."},
                {"role": "user",   "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()

        perche_si = rischi = conclusione = verdict = ""
        for line in text.split("\n"):
            l = line.strip()
            if l.upper().startswith("PERCHE_SI:"):     perche_si   = l[10:].strip()
            elif l.upper().startswith("RISCHI:"):      rischi      = l[7:].strip()
            elif l.upper().startswith("CONCLUSIONE:"): conclusione = l[12:].strip()
            elif l.upper().startswith("VERDICT:"):     verdict     = l[8:].strip().upper()

        if not verdict:
            low = conclusione.lower()
            if any(w in low for w in ["sì", "si ", "consiglio", "opportunità", "interessante", "acquistare"]):
                verdict = "SI"
            elif any(w in low for w in ["no ", "evitare", "non consiglio", "troppo rischios"]):
                verdict = "NO"
            else:
                verdict = "NEUTRO"

        result = {
            "perche_si":  perche_si  or "Dati insufficienti.",
            "rischi":     rischi     or "Dati insufficienti.",
            "conclusione": conclusione or text,
            "verdict":    verdict if verdict in ("SI", "NO", "NEUTRO") else "NEUTRO",
        }
        _store(key, result)
        return result

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stock/{ticker}/forecast")
async def api_forecast(ticker: str):
    """Previsione AI 7 giorni — trend, range prezzi, confidenza, catalizzatori."""
    t = ticker.upper()
    key = f"forecast:{t}"
    if (c := _cached(key, _FORECAST_TTL)) is not None:
        return c

    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)

    data = await asyncio.to_thread(get_enriched_analysis, t)
    if not data:
        return JSONResponse({"error": "ticker non trovato"}, status_code=404)
    data = _clean(data)

    price = float(data.get("current_price") or 0)
    segnali = ", ".join((data.get("signals") or [])[:3]) or "nessuno"

    prompt = (
        f"Previsione tecnica 7 giorni per {t} ({data.get('name', t)}):\n"
        f"- Prezzo attuale: ${price:.4f}, oggi {data.get('day_change_pct', 0):+.1f}%\n"
        f"- RSI: {data.get('rsi', 50):.0f}, Volatilità annualizzata: {data.get('volatility', 0):.0f}%\n"
        f"- Performance: settimana {(data.get('week_return') or 0):+.1f}%, mese {(data.get('month_return') or 0):+.1f}%\n"
        f"- Sentiment notizie: {data.get('news_sentiment_label', 'Neutre')}\n"
        f"- Earnings: {data.get('next_earnings_str', 'N/D')}\n"
        f"- Segnali tecnici: {segnali}\n\n"
        "Basandoti su questi dati tecnici, rispondi SOLO in questo formato esatto (italiano):\n"
        "TREND: RIALZO oppure RIBASSO oppure LATERALE\n"
        "RANGE_PCT_LOW: [numero, variazione % minima prevista in 7 giorni rispetto al prezzo attuale, es. -5.2]\n"
        "RANGE_PCT_HIGH: [numero, variazione % massima prevista in 7 giorni rispetto al prezzo attuale, es. +8.1]\n"
        "CONFIDENZA: ALTA oppure MEDIA oppure BASSA\n"
        "CATALIZZATORI: [2-3 fattori chiave da monitorare questa settimana]\n"
        "SINTESI: [1-2 frasi sul perché questo trend atteso]"
    )

    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[
                {"role": "system", "content": "Sei un analista tecnico esperto. Fornisci previsioni orientative basate su indicatori tecnici. Sii preciso nel formato richiesto. Le previsioni sui mercati sono intrinsecamente incerte."},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()

        trend = conf = cat = sintesi = ""
        pct_low: Optional[float] = None
        pct_high: Optional[float] = None

        for line in text.split("\n"):
            l = line.strip()
            lu = l.upper()
            if lu.startswith("TREND:"):
                v = l[6:].strip().upper()
                if v in ("RIALZO", "RIBASSO", "LATERALE"): trend = v
            elif lu.startswith("RANGE_PCT_LOW:"):
                try: pct_low = float(l[14:].strip().replace(",", ".").replace("+", ""))
                except Exception: pass
            elif lu.startswith("RANGE_PCT_HIGH:"):
                try: pct_high = float(l[15:].strip().replace(",", ".").replace("+", ""))
                except Exception: pass
            elif lu.startswith("CONFIDENZA:"):
                v = l[11:].strip().upper()
                if v in ("ALTA", "MEDIA", "BASSA"): conf = v
            elif lu.startswith("CATALIZZATORI:"):
                cat = l[14:].strip()
            elif lu.startswith("SINTESI:"):
                sintesi = l[8:].strip()

        # Fallback range basato su volatilità se il modello non ha risposto bene
        vol = float(data.get("volatility") or 20)
        weekly_vol = vol / (52 ** 0.5) * 1.5   # volatilità settimanale stimata
        if pct_low is None:
            pct_low = round(-weekly_vol, 2)
        if pct_high is None:
            pct_high = round(weekly_vol, 2)
        if not trend:
            trend = "LATERALE"

        result = {
            "trend":        trend,
            "pct_low":      round(pct_low, 2),
            "pct_high":     round(pct_high, 2),
            "price_low":    round(price * (1 + pct_low  / 100), 4) if price else 0,
            "price_high":   round(price * (1 + pct_high / 100), 4) if price else 0,
            "confidenza":   conf or "MEDIA",
            "catalizzatori": cat or "Dati non disponibili.",
            "sintesi":      sintesi or "Analisi non disponibile.",
        }
        _store(key, result)
        return result

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/market/indices")
async def api_indices():
    """S&P500, NASDAQ, DOW, VIX — cache 60s."""
    key = "market:indices"
    if (c := _cached(key, 60)) is not None:
        return c

    def _get():
        import yfinance as yf
        syms = {"SP500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI", "VIX": "^VIX"}
        result = {}
        for name, sym in syms.items():
            try:
                fi = yf.Ticker(sym).fast_info
                price = float(fi.last_price or 0)
                prev  = float(fi.previous_close or price)
                chg   = round(((price - prev) / prev * 100) if prev else 0, 2)
                result[name] = {"price": round(price, 2), "chg": chg}
            except Exception:
                result[name] = {"price": 0, "chg": 0}
        return result

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return data


@app.get("/api/sparklines")
async def api_sparklines(tickers: str):
    """Storico 5gg batch per le sparkline delle card."""
    key = f"spark:{tickers}"
    if (c := _cached(key, _STOCK_TTL)) is not None:
        return c

    def _get():
        import yfinance as yf
        import pandas as pd
        tlist = [t.strip().upper() for t in tickers.split(",") if t.strip()][:15]
        if not tlist:
            return {}
        try:
            raw = yf.download(tlist, period="5d", interval="1d", auto_adjust=True, progress=False)
            if raw.empty:
                return {}
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else None
            if close is None:
                return {}
            result = {}
            for t in tlist:
                if t in close.columns:
                    vals = close[t].dropna().tolist()
                    result[t] = [round(float(v), 4) for v in vals if v == v]
            return result
        except Exception as e:
            print(f"[sparklines] {e}")
            return {}

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return data


@app.get("/api/stock/{ticker}/ohlc")
async def api_ohlc(ticker: str, period: str = "1mo", interval: str = "1d"):
    """OHLC candlestick data — usato per grafico linea e candele nel modal."""
    t = ticker.upper()
    key = f"ohlc:{t}:{period}:{interval}"
    ttl = 60 if interval in _INTRADAY_INTERVALS else _STOCK_TTL
    if (c := _cached(key, ttl)) is not None:
        return c

    def _get():
        import yfinance as yf
        h = yf.Ticker(t).history(period=period, interval=interval)
        if h.empty:
            return []
        intraday = interval in _INTRADAY_INTERVALS
        result = []
        for idx, row in h.iterrows():
            o, hi, lo, cl = (float(row[k]) for k in ["Open", "High", "Low", "Close"])
            if any(v != v for v in [o, hi, lo, cl]):
                continue
            vol = int(row["Volume"]) if str(row["Volume"]) not in ("nan", "None", "0.0") else 0
            result.append({
                "time": int(idx.timestamp()) if intraday else str(idx.date()),
                "open": round(o, 4), "high": round(hi, 4),
                "low": round(lo, 4), "close": round(cl, 4),
                "volume": vol,
            })
        return result

    result = await asyncio.to_thread(_get)
    _store(key, result)
    return result


@app.get("/api/batch_quotes")
async def api_batch_quotes(tickers: str):
    """Prezzi + variazione giornaliera in batch — usato da heatmap e portafoglio."""
    key = f"bq:{tickers}"
    if (c := _cached(key, 60)) is not None:
        return c

    def _get():
        import yfinance as yf
        import pandas as pd
        tlist = [t.strip().upper() for t in tickers.split(",") if t.strip()][:50]
        if not tlist:
            return {}
        try:
            raw = yf.download(tlist, period="2d", interval="1d", auto_adjust=True, progress=False)
            if raw.empty:
                return {}
            close_df = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else None
            if close_df is None:
                return {}
            result = {}
            for t in tlist:
                if t not in close_df.columns:
                    continue
                vals = close_df[t].dropna()
                if len(vals) >= 2:
                    curr = float(vals.iloc[-1]); prev = float(vals.iloc[-2])
                    chg = round((curr - prev) / prev * 100, 2) if prev else 0
                    result[t] = {"price": round(curr, 4), "chg": chg}
                elif len(vals) == 1:
                    result[t] = {"price": round(float(vals.iloc[-1]), 4), "chg": 0}
            return result
        except Exception as e:
            print(f"[batch_quotes] {e}")
            return {}

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return data


@app.get("/api/market/movers")
async def api_market_movers():
    """Top 5 e Bottom 5 per area geografica — cache 60s."""
    key = "market:movers"
    if (c := _cached(key, 60)) is not None:
        return c

    def _get():
        import yfinance as yf
        import pandas as pd

        all_tickers = list(dict.fromkeys(t for tlist in _MOVERS_TICKERS.values() for t in tlist))
        try:
            raw = yf.download(all_tickers, period="2d", interval="1d", auto_adjust=True, progress=False)
            if raw.empty:
                return {}
            close_df = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else None
            if close_df is None:
                return {}

            quotes: dict = {}
            for t in all_tickers:
                if t not in close_df.columns:
                    continue
                vals = close_df[t].dropna()
                if len(vals) >= 2:
                    curr = float(vals.iloc[-1]); prev = float(vals.iloc[-2])
                    chg  = round((curr - prev) / prev * 100, 2) if prev else 0.0
                    quotes[t] = {"price": round(curr, 2), "chg": chg}
                elif len(vals) == 1:
                    quotes[t] = {"price": round(float(vals.iloc[-1]), 2), "chg": 0.0}

            labels = {"usa": "🇺🇸 USA", "europa": "🌍 Europa", "asia": "🌏 Asia & International"}
            result: dict = {}
            for region, tickers in _MOVERS_TICKERS.items():
                rows = [{"ticker": t, **quotes[t]} for t in tickers if t in quotes]
                rows.sort(key=lambda x: x["chg"], reverse=True)
                result[region] = {
                    "label": labels.get(region, region),
                    "top":    rows[:5],
                    "bottom": list(reversed(rows[-5:])) if len(rows) >= 5 else list(reversed(rows)),
                    "total":  len(rows),
                }
            return result
        except Exception as e:
            print(f"[movers] {e}")
            return {}

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return _clean(data)


@app.get("/api/heatmap")
async def api_heatmap_data():
    """Heatmap USA large-cap + Europa ADR — cache 60s."""
    key = "heatmap:main"
    if (c := _cached(key, 60)) is not None:
        return c

    def _get():
        import yfinance as yf
        import pandas as pd
        try:
            raw = yf.download(_HEATMAP_TICKERS, period="2d", interval="1d", auto_adjust=True, progress=False)
            if raw.empty:
                return {}
            close_df = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else None
            if close_df is None:
                return {}
            result = {}
            for t in _HEATMAP_TICKERS:
                if t not in close_df.columns:
                    continue
                vals = close_df[t].dropna()
                if len(vals) >= 2:
                    curr = float(vals.iloc[-1]); prev = float(vals.iloc[-2])
                    chg = round((curr - prev) / prev * 100, 2) if prev else 0
                    result[t] = {"price": round(curr, 2), "chg": chg}
                elif len(vals) == 1:
                    result[t] = {"price": round(float(vals.iloc[-1]), 2), "chg": 0}
            return result
        except Exception as e:
            print(f"[heatmap] {e}")
            return {}

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return _clean(data)



# ─── Portafoglio AI ───────────────────────────────────────────────────────────

class PortfolioPosition(BaseModel):
    ticker: str
    buyPrice: float
    qty: float
    currentPrice: Optional[float] = 0.0
    name: Optional[str] = ""

class PortfolioEvalRequest(BaseModel):
    positions: List[PortfolioPosition]


@app.post("/api/portfolio/evaluate")
async def api_portfolio_evaluate(req: PortfolioEvalRequest):
    """Valutazione AI del portafoglio — Groq Llama analizza P&L e dà consigli."""
    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)
    if not req.positions:
        return JSONResponse({"error": "portafoglio vuoto"}, status_code=400)

    total_inv = 0.0; total_val = 0.0
    lines = []
    for p in req.positions:
        inv = p.buyPrice * p.qty
        val = p.currentPrice * p.qty if p.currentPrice and p.currentPrice > 0 else inv
        pl = val - inv; pl_pct = (pl / inv * 100) if inv > 0 else 0.0
        total_inv += inv; total_val += val
        sign = "+" if pl >= 0 else ""
        e = "📈" if pl >= 0 else "📉"
        name_str = f" ({p.name})" if p.name and p.name != p.ticker else ""
        lines.append(f"{e} {p.ticker}{name_str}: {p.qty:.0f} az. | acquisto ${p.buyPrice:.4f} → ora ${p.currentPrice:.4f} | P&L: {sign}${pl:.2f} ({sign}{pl_pct:.1f}%)")

    total_pl = total_val - total_inv
    total_pl_pct = (total_pl / total_inv * 100) if total_inv > 0 else 0.0
    total_sign = "+" if total_pl >= 0 else ""
    summary = "\n".join(lines) + f"\n\nTOTALE — Investito: ${total_inv:.2f} | Valore: ${total_val:.2f} | P&L: {total_sign}${total_pl:.2f} ({total_sign}{total_pl_pct:.1f}%)"

    prompt = (
        f"Portafoglio dell'investitore:\n{summary}\n\n"
        "Analizza questo portafoglio e rispondi SOLO in questo formato (italiano, conciso, max 2 righe per sezione):\n"
        "PANORAMICA: [valuta diversificazione, concentrazione e qualità generale]\n"
        "RISCHI: [2-3 rischi specifici e concreti del portafoglio]\n"
        "CONSIGLI: [2-3 azioni concrete: cosa vendere, tenere, ribilanciare o aggiungere]\n"
        "VERDICT: POSITIVO oppure NEUTRO oppure ATTENZIONE"
    )

    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=420,
            messages=[
                {"role": "system", "content": "Sei un analista finanziario senior. Rispondi in italiano, diretto e concreto. Non dare mai consigli finanziari definitivi."},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()

        panoramica = rischi = consigli = verdict = ""
        for line in text.split("\n"):
            l = line.strip()
            if l.upper().startswith("PANORAMICA:"): panoramica = l[11:].strip()
            elif l.upper().startswith("RISCHI:"):    rischi     = l[7:].strip()
            elif l.upper().startswith("CONSIGLI:"):  consigli   = l[9:].strip()
            elif l.upper().startswith("VERDICT:"):   verdict    = l[8:].strip().upper()

        if verdict not in ("POSITIVO", "NEUTRO", "ATTENZIONE"):
            verdict = "NEUTRO"

        return {
            "panoramica": panoramica or "Analisi non disponibile.",
            "rischi":     rischi     or "Analisi non disponibile.",
            "consigli":   consigli   or "Analisi non disponibile.",
            "verdict":    verdict,
            "total_pl":   round(total_pl, 2),
            "total_pl_pct": round(total_pl_pct, 2),
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Storico analisi ──────────────────────────────────────────────────────────

@app.get("/api/history")
async def api_history_list():
    """Lista date disponibili nello storico analisi."""
    if not HISTORY_FILE.exists():
        return []
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    dates = sorted(history.keys(), reverse=True)
    return [
        {
            "date": d,
            "count": len(history[d].get("stocks", [])),
            "generated_at": history[d].get("generated_at", d),
        }
        for d in dates
    ]


@app.get("/api/history/{date}")
async def api_history_date(date: str):
    """Azioni analizzate in una data specifica + prezzo attuale per confronto."""
    if not HISTORY_FILE.exists():
        return JSONResponse({"error": "nessuno storico disponibile"}, status_code=404)
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"error": "errore lettura storico"}, status_code=500)

    if date not in history:
        return JSONResponse({"error": "data non trovata"}, status_code=404)

    snap = history[date]
    stocks = snap.get("stocks", [])

    def _get_current_prices(tickers: list) -> dict:
        import yfinance as yf
        result = {}
        for t in tickers:
            try:
                fi = yf.Ticker(t).fast_info
                result[t] = float(fi.last_price or 0)
            except Exception:
                result[t] = 0.0
        return result

    tickers = [s["ticker"] for s in stocks]
    current_prices = await asyncio.to_thread(_get_current_prices, tickers)

    enriched = []
    for s in stocks:
        t = s["ticker"]
        cp = current_prices.get(t, 0.0)
        pa = s.get("price_at_analysis", 0.0)
        pct = round((cp - pa) / pa * 100, 2) if pa > 0 and cp > 0 else None
        enriched.append({**s, "current_price": round(cp, 4), "pct_since_analysis": pct})

    return _clean({
        "date": date,
        "generated_at": snap.get("generated_at"),
        "stocks": enriched,
    })


# ─── FX rate ──────────────────────────────────────────────────────────────────

@app.get("/api/fx")
async def api_fx():
    """Tasso EUR/USD in tempo reale — cache 1h."""
    key = "fx:eurusd"
    if (c := _cached(key, _FX_TTL)) is not None:
        return c
    def _get():
        import yfinance as yf
        try:
            rate = yf.Ticker("EURUSD=X").fast_info.last_price
            return {"rate": round(float(rate), 4)}
        except Exception:
            return {"rate": 0.92}
    data = await asyncio.to_thread(_get)
    _store(key, data)
    return data


# ─── Auth — Telegram Login ────────────────────────────────────────────────────

@app.get("/api/config")
async def api_config():
    """Configurazione pubblica: bot username e flag login."""
    bot_username = os.getenv("BOT_USERNAME", "")
    return {
        "bot_username": bot_username,
        "login_enabled": bool(bot_username and _JWT_OK and _BOT_TOKEN),
    }


class TgAuthBody(BaseModel):
    id: int
    first_name: str
    username: Optional[str] = ""
    photo_url: Optional[str] = ""
    auth_date: int
    hash: str


@app.post("/api/auth/telegram")
async def api_auth_telegram(body: TgAuthBody):
    """Verifica login Telegram e restituisce JWT 30 giorni."""
    raw = body.dict()
    if not _tg_verify(dict(raw)):
        return JSONResponse({"error": "firma non valida"}, status_code=401)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if abs(now_ts - body.auth_date) > 86400:
        return JSONResponse({"error": "token scaduto — riprova il login"}, status_code=401)

    uid = str(body.id)
    users = _read_users()
    if uid not in users:
        users[uid] = {
            "id": uid, "first_name": body.first_name,
            "username": body.username or "", "photo_url": body.photo_url or "",
            "portfolio": {}, "watchlist": {},
        }
    else:
        users[uid].update({
            "first_name": body.first_name,
            "username": body.username or "",
            "photo_url": body.photo_url or "",
        })
    _write_users(users)

    token = _jwt_create({"uid": uid, "fn": body.first_name, "un": body.username or ""})
    return {
        "token": token,
        "user": {
            "id": uid, "first_name": body.first_name,
            "username": body.username or "", "photo_url": body.photo_url or "",
        },
    }


@app.get("/api/auth/me")
async def api_auth_me(authorization: Optional[str] = Header(None)):
    payload = _get_token(authorization)
    if not payload:
        return JSONResponse({"error": "non autenticato"}, status_code=401)
    users = _read_users()
    u = users.get(payload.get("uid", ""))
    if not u:
        return JSONResponse({"error": "utente non trovato"}, status_code=404)
    return {
        "id": u.get("id"), "first_name": u.get("first_name", ""),
        "username": u.get("username", ""), "photo_url": u.get("photo_url", ""),
    }


# ─── User portfolio & watchlist (server-side) ─────────────────────────────────

@app.get("/api/user/portfolio")
async def api_user_portfolio_get(authorization: Optional[str] = Header(None)):
    payload = _get_token(authorization)
    if not payload:
        return JSONResponse({"error": "non autenticato"}, status_code=401)
    return _read_users().get(payload.get("uid", ""), {}).get("portfolio", {})


class BodyPortfolio(BaseModel):
    portfolio: dict


@app.post("/api/user/portfolio")
async def api_user_portfolio_post(body: BodyPortfolio, authorization: Optional[str] = Header(None)):
    payload = _get_token(authorization)
    if not payload:
        return JSONResponse({"error": "non autenticato"}, status_code=401)
    users = _read_users()
    uid = payload.get("uid", "")
    if uid not in users:
        return JSONResponse({"error": "utente non trovato"}, status_code=404)
    users[uid]["portfolio"] = body.portfolio
    _write_users(users)
    return {"ok": True}


@app.get("/api/user/watchlist")
async def api_user_watchlist_get(authorization: Optional[str] = Header(None)):
    payload = _get_token(authorization)
    if not payload:
        return JSONResponse({"error": "non autenticato"}, status_code=401)
    return _read_users().get(payload.get("uid", ""), {}).get("watchlist", {})


class BodyWatchlist(BaseModel):
    watchlist: dict


@app.post("/api/user/watchlist")
async def api_user_watchlist_post(body: BodyWatchlist, authorization: Optional[str] = Header(None)):
    payload = _get_token(authorization)
    if not payload:
        return JSONResponse({"error": "non autenticato"}, status_code=401)
    users = _read_users()
    uid = payload.get("uid", "")
    if uid not in users:
        return JSONResponse({"error": "utente non trovato"}, status_code=404)
    users[uid]["watchlist"] = body.watchlist
    _write_users(users)
    return {"ok": True}


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "cache_keys": len(_cache), "jwt_ok": _JWT_OK}


@app.get("/")
async def root():
    return FileResponse(str(STATIC / "index.html"))
