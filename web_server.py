"""Web dashboard per Stock Bot — FastAPI server."""
import asyncio
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

# ─── Cache semplice in memoria ────────────────────────────────────────────────
_cache: dict[str, dict] = {}   # key → {"data": ..., "ts": float}
_SCAN_TTL   = 300   # 5 minuti per lo scanner
_STOCK_TTL  = 180   # 3 minuti per analisi singola


def _cached(key: str, ttl: int):
    entry = _cache.get(key)
    if entry and time.monotonic() - entry["ts"] < ttl:
        return entry["data"]
    return None


def _store(key: str, data: Any):
    _cache[key] = {"data": data, "ts": time.monotonic()}


# ─── Serializzazione sicura (numpy → Python native) ──────────────────────────
def _clean(obj: Any) -> Any:
    """Converte ricorsivamente numpy/pandas types in tipi JSON-serializzabili."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    # numpy integer
    try:
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return None if (obj != obj) else float(obj)   # NaN → None
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.bool_):
            return bool(obj)
    except ImportError:
        pass
    # Python float NaN/Inf → None
    if isinstance(obj, float):
        import math
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@app.get("/api/scan")
async def api_scan(top: int = 10):
    """Scanner top N azioni sotto $20 con cache 5 min."""
    key = f"scan:{top}"
    cached = _cached(key, _SCAN_TTL)
    if cached is not None:
        return cached

    results = await asyncio.to_thread(scan_cheap_stocks, 20.0, top)
    clean = _clean(results or [])
    _store(key, clean)
    return clean


@app.get("/api/stock/{ticker}")
async def api_stock(ticker: str):
    """Analisi arricchita singolo ticker con cache 3 min."""
    t = ticker.upper()
    key = f"stock:{t}"
    cached = _cached(key, _STOCK_TTL)
    if cached is not None:
        return cached

    data = await asyncio.to_thread(get_enriched_analysis, t)
    if not data:
        return JSONResponse({"error": "ticker non trovato"}, status_code=404)
    clean = _clean(data)
    _store(key, clean)
    return clean


@app.get("/api/stock/{ticker}/history")
async def api_history(ticker: str, period: str = "1mo"):
    """Storico prezzi per grafico con cache 3 min."""
    t = ticker.upper()
    key = f"history:{t}:{period}"
    cached = _cached(key, _STOCK_TTL)
    if cached is not None:
        return cached

    def _get():
        import yfinance as yf
        h = yf.Ticker(t).history(period=period)
        if h.empty:
            return []
        return [
            {"date": str(idx.date()), "close": round(float(c), 4)}
            for idx, c in zip(h.index, h["Close"])
            if c == c  # skip NaN
        ]

    result = await asyncio.to_thread(_get)
    _store(key, result)
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "cache_keys": len(_cache)}


@app.get("/")
async def root():
    return FileResponse(str(STATIC / "index.html"))
