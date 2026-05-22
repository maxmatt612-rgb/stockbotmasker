"""Web dashboard per Stock Bot — FastAPI server."""
import asyncio
import os
from pathlib import Path

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


@app.get("/api/scan")
async def api_scan(top: int = 10):
    """Scanner top N azioni sotto $20."""
    results = await asyncio.to_thread(scan_cheap_stocks, 20.0, top)
    return results or []


@app.get("/api/stock/{ticker}")
async def api_stock(ticker: str):
    """Analisi arricchita di un singolo ticker."""
    data = await asyncio.to_thread(get_enriched_analysis, ticker.upper())
    if not data:
        return JSONResponse({"error": "ticker non trovato"}, status_code=404)
    return data


@app.get("/api/stock/{ticker}/history")
async def api_history(ticker: str, period: str = "1mo"):
    """Storico prezzi per il grafico."""
    def _get():
        import yfinance as yf
        h = yf.Ticker(ticker.upper()).history(period=period)
        if h.empty:
            return []
        return [
            {"date": str(idx.date()), "close": round(float(c), 4)}
            for idx, c in zip(h.index, h["Close"])
        ]
    return await asyncio.to_thread(_get)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse(str(STATIC / "index.html"))
