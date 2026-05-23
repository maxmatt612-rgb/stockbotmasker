"""Web dashboard per Stock Bot — FastAPI server."""
import asyncio
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from analyzer import get_enriched_analysis, scan_cheap_stocks

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Stock Bot Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ─── Groq AI client ───────────────────────────────────────────────────────────
_GROQ_KEY = os.getenv("GROQ_API_KEY")
try:
    from groq import AsyncGroq
    groq_client = AsyncGroq(api_key=_GROQ_KEY) if _GROQ_KEY else None
except Exception:
    groq_client = None

# ─── Cache semplice in memoria ────────────────────────────────────────────────
_cache: dict[str, dict] = {}
_SCAN_TTL   = 300    # 5 min — scanner
_STOCK_TTL  = 180    # 3 min — analisi singola
_AI_TTL     = 600    # 10 min — AI (chiamata Groq costosa)


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
async def api_history(ticker: str, period: str = "1mo"):
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
            {"date": str(idx.date()), "close": round(float(c), 4)}
            for idx, c in zip(h.index, h["Close"])
            if c == c
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
            if l.upper().startswith("PERCHE_SI:"):   perche_si  = l[10:].strip()
            elif l.upper().startswith("RISCHI:"):    rischi     = l[7:].strip()
            elif l.upper().startswith("CONCLUSIONE:"): conclusione = l[12:].strip()
            elif l.upper().startswith("VERDICT:"):   verdict    = l[8:].strip().upper()

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


@app.get("/health")
async def health():
    return {"status": "ok", "cache_keys": len(_cache)}


@app.get("/")
async def root():
    return FileResponse(str(STATIC / "index.html"))
