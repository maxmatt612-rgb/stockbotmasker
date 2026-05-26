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

from fastapi import FastAPI, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from analyzer import get_enriched_analysis, scan_cheap_stocks

STATIC = Path(__file__).parent / "static"
HISTORY_FILE = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "analysis_history.json"
SCAN_FILE    = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "last_scan.json"
WEB_USERS_FILE = Path(__file__).parent / "web_users.json"

app = FastAPI(title="Stock Bot Dashboard", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _startup_load_cache():
    """Carica l'ultimo scan da disco e avvia immediatamente un refresh in background."""
    loaded = False
    if SCAN_FILE.exists():
        try:
            data = json.loads(SCAN_FILE.read_text(encoding="utf-8"))
            if data:
                _store("scan:10", data)
                loaded = True
                print(f"[startup] Caricati {len(data)} titoli da last_scan.json")
        except Exception as e:
            print(f"[startup] Impossibile caricare last_scan.json: {e}")

    # Avvia sempre un refresh in background all'avvio così i dati si aggiornano
    # subito (se abbiamo dati stale) oppure la prima scansione parte in parallelo
    asyncio.create_task(_refresh_scan_background(10))
    asyncio.create_task(_pdf_scheduler())
    if not loaded:
        print("[startup] Nessun dato salvato — scan di avvio in background iniziato")


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
_scan_in_progress: bool = False          # evita scan concorrenti

# ─── PDF report in memoria ────────────────────────────────────────────────────
_morning_pdf: Optional[bytes] = None
_morning_pdf_ts: Optional[str] = None   # timestamp generazione
_evening_pdf: Optional[bytes] = None
_evening_pdf_ts: Optional[str] = None
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

def _save_scan_to_disk(data: list):
    """Persiste l'ultimo scan su disco per sopravvivere ai riavvii."""
    try:
        SCAN_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        print(f"[scan] Errore salvataggio su disco: {e}")


# ─── PDF generation ───────────────────────────────────────────────────────────

def _build_pdf(stocks: list, title: str, subtitle: str) -> bytes:
    """Genera un PDF professionale con i dati dei titoli."""
    from fpdf import FPDF

    class PDF(FPDF):
        def header(self):
            self.set_fill_color(15, 15, 25)
            self.rect(0, 0, 210, 28, 'F')
            self.set_text_color(196, 166, 255)
            self.set_font("Helvetica", "B", 16)
            self.set_xy(10, 8)
            self.cell(0, 8, "MASKER Stock Intelligence", ln=True)
            self.set_text_color(180, 180, 200)
            self.set_font("Helvetica", "", 9)
            self.set_x(10)
            self.cell(0, 6, subtitle, ln=True)
            self.ln(4)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(120, 120, 140)
            self.cell(0, 6, "Solo a scopo informativo — non costituisce consulenza finanziaria", align="C")

    pdf = PDF()
    pdf.set_margins(10, 32, 10)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Titolo sezione
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(30, 30, 50)
    pdf.cell(0, 8, title, ln=True)
    pdf.ln(2)

    # Intestazione tabella
    col_w = [20, 38, 28, 22, 18, 20, 20, 24]
    headers = ["Ticker", "Score", "Prezzo", "Var. %", "RSI", "Rischio", "Vol.", "Segnale"]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(240, 238, 255)
    pdf.set_text_color(40, 40, 80)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()

    # Righe
    pdf.set_font("Helvetica", "", 8)
    for rank, s in enumerate(stocks, 1):
        chg = s.get("day_change_pct", 0) or 0
        rsi = s.get("rsi", 50) or 50
        score = s.get("score_10", 5) or 5
        vol = s.get("vol_ratio", 1) or 1
        ccy = s.get("currency", "USD")
        sym = "€" if ccy == "EUR" else ("£" if ccy == "GBP" else "$")
        price_str = f"{sym}{s['current_price']:.2f}" if s.get("current_price") else "—"
        chg_str = f"{'+' if chg >= 0 else ''}{chg:.2f}%"
        vol_str = f"{vol:.1f}x"
        rsi_str = f"{rsi:.0f}"
        score_str = f"{score:.1f}/10"
        signal = "COMPRA" if score >= 7 else ("VENDI" if score <= 3 else "ATTENDI")

        # Alternanza righe
        if rank % 2 == 0:
            pdf.set_fill_color(248, 247, 255)
        else:
            pdf.set_fill_color(255, 255, 255)

        # Colore variazione
        if chg >= 0:
            pdf.set_text_color(20, 120, 60)
        else:
            pdf.set_text_color(180, 30, 30)

        row = [s["ticker"], score_str, price_str, chg_str, rsi_str,
               s.get("risk_level", "—"), vol_str, signal]
        for i, cell in enumerate(row):
            align = "L" if i == 0 else "C"
            pdf.cell(col_w[i], 6, str(cell), border=1, fill=True, align=align)
        pdf.set_text_color(30, 30, 50)
        pdf.ln()

    # Dettaglio per ogni titolo
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(30, 30, 50)
    pdf.cell(0, 7, "Dettaglio titoli", ln=True)
    pdf.ln(2)

    for rank, s in enumerate(stocks, 1):
        if pdf.get_y() > 250:
            pdf.add_page()

        chg = s.get("day_change_pct", 0) or 0
        score = s.get("score_10", 5) or 5
        rsi = s.get("rsi", 50) or 50
        ccy = s.get("currency", "USD")
        sym = "€" if ccy == "EUR" else ("£" if ccy == "GBP" else "$")
        price_str = f"{sym}{s['current_price']:.2f}" if s.get("current_price") else "—"
        chg_str = f"{'+' if chg >= 0 else ''}{chg:.2f}%"

        # Header card
        pdf.set_fill_color(230, 228, 255)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(40, 40, 100)
        pdf.cell(0, 6, f"#{rank}  {s['ticker']}  —  {price_str}  {chg_str}  |  Score {score:.1f}/10  |  RSI {rsi:.0f}  |  {s.get('risk_level','—')}", fill=True, ln=True)

        # Trade setup
        ts = s.get("trade_setup")
        if ts:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(60, 60, 80)
            pdf.cell(0, 5, f"   Entry {sym}{ts['entry']:.2f}  |  Stop {sym}{ts['stop']:.2f} ({ts['stop_pct']}%)  |  Target {sym}{ts['target']:.2f} (+{ts['target_pct']}%)  |  R/R 1:{ts['rr']}", ln=True)

        pdf.ln(2)

    return bytes(pdf.output())


async def _generate_morning_pdf():
    """Genera il PDF analisi mattutina dai dati in cache."""
    global _morning_pdf, _morning_pdf_ts
    data = (_cache.get("scan:10") or {}).get("data") or []
    if not data:
        print("[pdf] Nessun dato per il PDF mattutino")
        return
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    try:
        pdf_bytes = await asyncio.to_thread(
            _build_pdf, data,
            f"Analisi Mattutina — {datetime.now().strftime('%A %d %B %Y').capitalize()}",
            f"Generato il {now_str} | Top {len(data)} titoli per score"
        )
        _morning_pdf = pdf_bytes
        _morning_pdf_ts = now_str
        print(f"[pdf] PDF mattutino generato ({len(pdf_bytes)//1024} KB)")
    except Exception as e:
        print(f"[pdf] Errore generazione PDF mattutino: {e}")


async def _generate_evening_pdf():
    """Genera il PDF recap serale dai dati in cache."""
    global _evening_pdf, _evening_pdf_ts
    data = (_cache.get("scan:10") or {}).get("data") or []
    if not data:
        print("[pdf] Nessun dato per il PDF serale")
        return
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    try:
        pdf_bytes = await asyncio.to_thread(
            _build_pdf, data,
            f"Recap Serale — {datetime.now().strftime('%A %d %B %Y').capitalize()}",
            f"Chiusura mercati | Generato il {now_str}"
        )
        _evening_pdf = pdf_bytes
        _evening_pdf_ts = now_str
        print(f"[pdf] PDF serale generato ({len(pdf_bytes)//1024} KB)")
    except Exception as e:
        print(f"[pdf] Errore generazione PDF serale: {e}")


async def _pdf_scheduler():
    """Genera PDF automaticamente: 07:35 (mattina) e 17:40 (chiusura EU)."""
    while True:
        now = datetime.now()
        h, m = now.hour, now.minute
        # Mattina: 07:35 (5 min dopo il report mattutino delle 7:30)
        if h == 7 and m == 35:
            await _generate_morning_pdf()
            await asyncio.sleep(60)  # evita doppio trigger
        # Chiusura USA: 22:05 (5 min dopo la chiusura delle 22:00 ora italiana)
        elif h == 22 and m == 5:
            await _generate_evening_pdf()
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(30)  # controlla ogni 30 secondi


async def _refresh_scan_background(top: int):
    """Aggiorna il scan in background senza bloccare la risposta."""
    global _scan_in_progress
    if _scan_in_progress:
        return  # già in corso, skip
    _scan_in_progress = True
    try:
        print(f"[scan_bg] Avvio scan top={top}…")
        results = await asyncio.to_thread(scan_cheap_stocks, 60.0, top)
        clean = _clean(results or [])
        if clean:
            _store(f"scan:{top}", clean)
            if top == 10:
                _save_scan_to_disk(clean)
            print(f"[scan_bg] Completato: {len(clean)} titoli")
        else:
            print("[scan_bg] Scan restituito vuoto")
    except Exception as e:
        print(f"[scan_bg] Errore: {e}")
    finally:
        _scan_in_progress = False


@app.get("/api/scan")
async def api_scan(top: int = 10):
    key = f"scan:{top}"
    fresh = _cached(key, _SCAN_TTL)
    if fresh is not None:
        return fresh
    # Dati stale in cache → restituisci subito e aggiorna in background
    stale = _cache.get(key)
    if stale:
        asyncio.create_task(_refresh_scan_background(top))
        return stale["data"]
    # Nessun dato in cache: assicuriamoci che il background task sia avviato
    # (potrebbe non essersi avviato se il startup event ha avuto problemi).
    if not _scan_in_progress:
        asyncio.create_task(_refresh_scan_background(top))
    # Rispondi subito: il frontend mostra "in avvio" e riprova ogni 10s
    return JSONResponse([], status_code=202)


@app.post("/api/scan/force")
async def api_scan_force():
    """Invalida la cache dello scan e forza una nuova scansione in background."""
    # Rimuovi dalla cache così il prossimo /api/scan vede dati stale → refresh
    _cache.pop("scan:10", None)
    if not _scan_in_progress:
        asyncio.create_task(_refresh_scan_background(10))
    return {"ok": True, "scan_in_progress": _scan_in_progress}


@app.get("/api/report/morning")
async def api_report_morning():
    """Scarica il PDF analisi mattutina. Se non ancora generato, lo genera ora."""
    global _morning_pdf, _morning_pdf_ts
    if _morning_pdf is None:
        await _generate_morning_pdf()
    if _morning_pdf is None:
        return JSONResponse({"error": "Nessun dato disponibile per il PDF"}, status_code=404)
    from fastapi.responses import Response
    date_str = datetime.now().strftime("%Y%m%d")
    return Response(
        content=_morning_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=masker_mattina_{date_str}.pdf"},
    )


@app.get("/api/report/evening")
async def api_report_evening():
    """Scarica il PDF recap serale. Se non ancora generato, lo genera ora."""
    global _evening_pdf, _evening_pdf_ts
    if _evening_pdf is None:
        await _generate_evening_pdf()
    if _evening_pdf is None:
        return JSONResponse({"error": "Nessun dato disponibile per il PDF"}, status_code=404)
    from fastapi.responses import Response
    date_str = datetime.now().strftime("%Y%m%d")
    return Response(
        content=_evening_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=masker_sera_{date_str}.pdf"},
    )


@app.get("/api/report/status")
async def api_report_status():
    """Restituisce lo stato dei PDF disponibili."""
    return {
        "morning": {"available": _morning_pdf is not None, "generated_at": _morning_pdf_ts},
        "evening": {"available": _evening_pdf is not None, "generated_at": _evening_pdf_ts},
    }


@app.post("/api/report/generate")
async def api_report_generate(kind: str = "morning"):
    """Genera manualmente un PDF (morning o evening)."""
    if kind == "evening":
        asyncio.create_task(_generate_evening_pdf())
    else:
        asyncio.create_task(_generate_morning_pdf())
    return {"ok": True, "generating": kind}


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
        f"Analisi trading di {data['ticker']} ({data.get('name', data['ticker'])}):\n"
        f"- Prezzo: ${data['current_price']:.2f}, oggi {data['day_change_pct']:+.1f}%\n"
        f"- RSI: {data.get('rsi', 50):.0f}, Rischio: {data.get('risk_level', 'N/D')}, "
        f"Volatilità: {data.get('volatility', 0):.0f}%\n"
        f"- Settimana: {(data.get('week_return') or 0):+.1f}%, "
        f"Mese: {(data.get('month_return') or 0):+.1f}%\n"
        f"- Notizie: {data.get('news_sentiment_label', 'Neutre')}\n"
        f"- Prossimi earnings: {data.get('next_earnings_str', 'N/D')}\n\n"
        "Rispondi SOLO con questo formato esatto (italiano, max 2 righe a sezione):\n"
        "PERCHE_SI: [2-3 motivi tecnici/fondamentali concreti]\n"
        "RISCHI: [2-3 rischi specifici e reali]\n"
        "CONCLUSIONE: [1-2 frasi DIRETTE senza ambiguità]\n"
        "MOTIVO: [1 frase secca — es: 'RSI oversold + momentum positivo + earnings beat']\n"
        "CONFIDENZA: [numero intero da 50 a 95]\n"
        "VERDICT: COMPRA oppure VENDI oppure ASPETTA\n\n"
        "REGOLA: devi scegliere ESATTAMENTE una delle tre opzioni. "
        "Vietato scrivere 'valuta', 'dipende', 'potrebbe'. Sii netto."
    )

    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=350,
            messages=[
                {"role": "system", "content": "Sei un analista finanziario AI che dà segnali di trading NETTI. Devi sempre dare una raccomandazione precisa: COMPRA, VENDI, o ASPETTA. Non usare mai 'valuta con cautela', 'dipende', 'potrebbe'. Analizza i dati tecnici e fondamentali e dai una decisione chiara. Rispondi in italiano."},
                {"role": "user",   "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()

        perche_si = rischi = conclusione = motivo = verdict = ""
        confidenza = 70
        for line in text.split("\n"):
            l = line.strip()
            if l.upper().startswith("PERCHE_SI:"):     perche_si   = l[10:].strip()
            elif l.upper().startswith("RISCHI:"):      rischi      = l[7:].strip()
            elif l.upper().startswith("CONCLUSIONE:"): conclusione = l[12:].strip()
            elif l.upper().startswith("MOTIVO:"):      motivo      = l[7:].strip()
            elif l.upper().startswith("CONFIDENZA:"):
                try:
                    confidenza = int("".join(c for c in l[11:] if c.isdigit())[:2] or "70")
                except Exception:
                    confidenza = 70
            elif l.upper().startswith("VERDICT:"):     verdict     = l[8:].strip().upper()

        if verdict not in ("COMPRA", "VENDI", "ASPETTA"):
            low = (conclusione + " " + motivo).lower()
            if any(w in low for w in ["compra", "acquista", "sì", "si ", "opportunità", "interessante"]):
                verdict = "COMPRA"
            elif any(w in low for w in ["vendi", "evitare", "non consiglio", "troppo rischios", "ribassista"]):
                verdict = "VENDI"
            else:
                verdict = "ASPETTA"

        result = {
            "perche_si":  perche_si  or "Dati insufficienti.",
            "rischi":     rischi     or "Dati insufficienti.",
            "conclusione": conclusione or text,
            "motivo":     motivo     or "",
            "confidenza": max(50, min(95, confidenza)),
            "verdict":    verdict,
        }
        _store(key, result)
        return result

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stock/{ticker}/why-today")
async def api_why_today(ticker: str):
    """3 bullet points: perché il titolo si è mosso oggi (AI, cached 30 min)."""
    t = ticker.upper()
    key = f"why:{t}"
    if (c := _cached(key, _AI_TTL)) is not None:
        return c

    if not groq_client:
        return JSONResponse({"error": "AI non disponibile"}, status_code=503)

    data = await asyncio.to_thread(get_enriched_analysis, t)
    if not data:
        return JSONResponse({"error": "ticker non trovato"}, status_code=404)
    data = _clean(data)

    change = data.get("day_change_pct", 0) or 0
    rsi = data.get("rsi", 50) or 50
    vol_ratio = data.get("vol_ratio", 1) or 1
    name = data.get("name", t) or t
    news = data.get("news_sentiment_label", "neutre") or "neutre"
    direction = "salito" if change >= 0 else "sceso"

    prompt = (
        f"Il titolo {t} ({name}) ha {direction} del {abs(change):.1f}% oggi.\n"
        f"Dati tecnici: RSI={rsi:.0f}, volume {vol_ratio:.1f}x la media, "
        f"notizie {news}, volatilità {data.get('volatility', 0):.0f}%.\n"
        "Spiega in ESATTAMENTE 3 bullet points brevissimi (max 12 parole ciascuno) "
        "perché si è mosso così oggi.\n"
        "Formato:\n"
        "• [motivo 1]\n"
        "• [motivo 2]\n"
        "• [motivo 3]\n"
        "Rispondi SOLO con i 3 bullet, nessun'altra parola. In italiano."
    )

    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.4,
        )
        text = resp.choices[0].message.content.strip()
        result = {"ticker": t, "bullets": text, "change_pct": change}
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
    """S&P500, NASDAQ, DOW, VIX, DAX, FTSE MIB, Nikkei, Hang Seng, BTC, Gold, Oil — cache 60s."""
    key = "market:indices"
    if (c := _cached(key, 60)) is not None:
        return c

    def _get():
        import yfinance as yf
        syms = {
            # USA
            "SP500":  ("^GSPC",   "S&P 500"),
            "NASDAQ": ("^IXIC",   "NASDAQ"),
            "DOW":    ("^DJI",    "DOW"),
            "VIX":    ("^VIX",    "VIX"),
            # Europa
            "DAX":    ("^GDAXI",  "DAX"),
            "MIB":    ("FTSEMIB.MI", "FTSE MIB"),
            "CAC":    ("^FCHI",   "CAC 40"),
            # Asia
            "NIKKEI": ("^N225",   "Nikkei 225"),
            "HSI":    ("^HSI",    "Hang Seng"),
            "STI":    ("^STI",    "Singapore"),
            # Commodities / Crypto
            "BTC":    ("BTC-USD", "Bitcoin"),
            "GOLD":   ("GC=F",    "Oro"),
            "OIL":    ("CL=F",    "Petrolio"),
        }
        result = {}
        for key_id, (sym, label) in syms.items():
            try:
                fi = yf.Ticker(sym).fast_info
                price = float(fi.last_price or 0)
                prev  = float(fi.previous_close or price)
                chg   = round(((price - prev) / prev * 100) if prev else 0, 2)
                result[key_id] = {"price": round(price, 2), "chg": chg, "label": label}
            except Exception:
                result[key_id] = {"price": 0, "chg": 0, "label": label}
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
                    "all":    rows,   # tutti i titoli ordinati per performance, usati dalla heatmap
                    "total":  len(rows),
                }
            return result
        except Exception as e:
            print(f"[movers] {e}")
            return {}

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return _clean(data)


@app.get("/api/search")
async def api_search(q: str = ""):
    """Ricerca globale di qualsiasi azione/ETF/crypto nel mondo via yfinance — cache 5 min."""
    q = q.strip()
    if len(q) < 1:
        return []
    key = f"search:{q.lower()}"
    if (c := _cached(key, 300)) is not None:
        return c

    def _get():
        import yfinance as yf
        try:
            res = yf.Search(q, max_results=10, enable_fuzzy_query=True)
            quotes = res.quotes or []
            out = []
            for r in quotes:
                sym = r.get("symbol", "")
                if not sym:
                    continue
                out.append({
                    "ticker":   sym,
                    "name":     r.get("shortName") or r.get("longName") or sym,
                    "exchange": r.get("fullExchangeName") or r.get("exchange") or "",
                    "type":     r.get("quoteType") or "",
                })
            return out[:8]
        except Exception as e:
            print(f"[search] {e}")
            return []

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
    level: Optional[str] = "intermedio"  # dilettante | intermedio | esperto


_LEVEL_SYSTEM = {
    "dilettante": (
        "Sei un consulente finanziario amichevole che parla con un principiante. "
        "Usa un linguaggio semplice, zero gergo tecnico, spiega ogni concetto come se fosse la prima volta. "
        "Dai consigli chiari e diretti: compra, vendi, aspetta. Usa analogie semplici. Rispondi SEMPRE in italiano."
    ),
    "intermedio": (
        "Sei un analista finanziario che parla con un investitore con esperienza media. "
        "Puoi usare termini tecnici come RSI, MACD, supporti/resistenze, P/E ratio, ma spiegali brevemente. "
        "Bilancia semplicità e profondità. Rispondi SEMPRE in italiano."
    ),
    "esperto": (
        "Sei un analista quantitativo senior. Parli con un trader/investitore esperto. "
        "Usa terminologia avanzata: Greeks, correlazioni, beta, Sharpe ratio, drawdown, mean reversion, momentum. "
        "Sii denso di informazioni, niente spiegazioni base. Rispondi SEMPRE in italiano."
    ),
}


@app.post("/api/portfolio/evaluate")
async def api_portfolio_evaluate(req: PortfolioEvalRequest):
    """Valutazione AI del portafoglio — Groq Llama analizza P&L e dà consigli."""
    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)
    if not req.positions:
        return JSONResponse({"error": "portafoglio vuoto"}, status_code=400)

    level = req.level if req.level in _LEVEL_SYSTEM else "intermedio"
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
            max_tokens=500,
            messages=[
                {"role": "system", "content": _LEVEL_SYSTEM[level]},
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


# ─── Orario migliore ──────────────────────────────────────────────────────────

@app.get("/api/stock/{ticker}/best-hour")
async def api_best_hour(ticker: str):
    t = ticker.upper()
    key = f"besthour:{t}"
    if (c := _cached(key, 3600)) is not None:
        return c

    def _get():
        import yfinance as yf, math
        try:
            df = yf.download(t, period="60d", interval="30m", progress=False, auto_adjust=True)
            if df is None or df.empty:
                return None
            close = df["Close"]
            if hasattr(close, "squeeze"):
                close = close.squeeze()
            hourly: dict[int, list] = {}
            for ts, price in close.items():
                try:
                    h = ts.hour
                    p = float(price)
                    if math.isnan(p) or p <= 0:
                        continue
                    hourly.setdefault(h, []).append(p)
                except Exception:
                    continue
            if not hourly:
                return None
            avg = {h: sum(v)/len(v) for h, v in hourly.items() if len(v) >= 3}
            if not avg:
                return None
            overall_avg = sum(avg.values()) / len(avg)
            pct = {h: round((v - overall_avg) / overall_avg * 100, 2) for h, v in avg.items()}
            best  = min(pct, key=pct.get)
            worst = max(pct, key=pct.get)
            return {"best_hour": best, "worst_hour": worst, "hourly_pct": pct}
        except Exception:
            return None

    data = await asyncio.to_thread(_get)
    if not data:
        return JSONResponse({"error": "dati non disponibili"}, status_code=404)
    _store(key, data)
    return data


# ─── Correlazione portafoglio ─────────────────────────────────────────────────

class CorrelationBody(BaseModel):
    tickers: List[str]


@app.post("/api/portfolio/correlation")
async def api_portfolio_correlation(body: CorrelationBody):
    tickers = [t.upper() for t in body.tickers if t.strip()][:12]
    if len(tickers) < 2:
        return JSONResponse({"error": "Servono almeno 2 ticker"}, status_code=400)
    key = f"corr:{'_'.join(sorted(tickers))}"
    if (c := _cached(key, 3600)) is not None:
        return c

    def _get():
        import yfinance as yf, math
        try:
            raw = yf.download(tickers, period="60d", interval="1d", progress=False, auto_adjust=True)
            close = raw["Close"] if "Close" in raw.columns else raw
            close = close.dropna(how="all")
            rets = close.pct_change().dropna()
            result = {}
            for t1 in tickers:
                result[t1] = {}
                for t2 in tickers:
                    try:
                        c = float(rets[t1].corr(rets[t2]))
                        result[t1][t2] = round(c, 2) if not math.isnan(c) else 0.0
                    except Exception:
                        result[t1][t2] = 0.0
            return {"tickers": tickers, "matrix": result}
        except Exception as e:
            return None

    data = await asyncio.to_thread(_get)
    if not data:
        return JSONResponse({"error": "Dati insufficienti"}, status_code=404)
    _store(key, data)
    return data


# ─── Sfida vs mercato ─────────────────────────────────────────────────────────

class VsMarketBody(BaseModel):
    positions: List[PortfolioPosition]


@app.post("/api/portfolio/vs-market")
async def api_vs_market(body: VsMarketBody):
    if not body.positions:
        return JSONResponse({"error": "portafoglio vuoto"}, status_code=400)
    tickers = [p.ticker.upper() for p in body.positions]
    key = f"vsmarket:{'_'.join(sorted(tickers))}"
    if (c := _cached(key, 300)) is not None:
        return c

    def _get():
        import yfinance as yf, math
        try:
            to_dl = tickers + ["SPY"]
            raw = yf.download(to_dl, period="1y", interval="1wk", progress=False, auto_adjust=True)
            close = raw["Close"] if "Close" in raw.columns else raw
            close = close.dropna(how="all")
            spy = close["SPY"].dropna()
            if spy.empty:
                return None

            dates = [str(d.date()) for d in spy.index.tolist()]
            spy_start = float(spy.iloc[0])
            spy_pct = [(float(v) / spy_start - 1) * 100 for v in spy]

            # Weighted portfolio return
            total_inv = sum(p.buyPrice * p.qty for p in body.positions)
            port_pct = []
            for i, date in enumerate(spy.index):
                weighted = 0.0
                for p in body.positions:
                    t = p.ticker.upper()
                    if t not in close.columns:
                        continue
                    series = close[t].dropna()
                    if series.empty:
                        continue
                    try:
                        start_price = float(series.iloc[0])
                        curr_price = float(series.iloc[min(i, len(series)-1)])
                        weight = (p.buyPrice * p.qty) / total_inv if total_inv > 0 else 0
                        weighted += weight * (curr_price / start_price - 1) * 100
                    except Exception:
                        continue
                port_pct.append(round(weighted, 2))

            spy_final = round(spy_pct[-1], 2)
            port_final = round(port_pct[-1], 2)
            return {
                "dates": dates,
                "portfolio_pct": port_pct,
                "spy_pct": [round(v, 2) for v in spy_pct],
                "portfolio_final": port_final,
                "spy_final": spy_final,
                "winning": port_final > spy_final,
            }
        except Exception:
            return None

    data = await asyncio.to_thread(_get)
    if not data:
        return JSONResponse({"error": "Dati insufficienti"}, status_code=404)
    _store(key, data)
    return data


# ─── Analisi grafico (upload immagine) ───────────────────────────────────────

_GRAFICO_PROMPTS = {
    "dilettante": (
        "Sei un esperto di trading che aiuta un principiante. Analizza questo grafico e rispondi in italiano "
        "in modo MOLTO semplice, senza gergo tecnico. Usa questo formato:\n\n"
        "📍 SITUAZIONE: [una frase su cosa sta facendo il prezzo]\n"
        "🛑 STOP LOSS: [valore preciso] — [perché in 1 riga semplice]\n"
        "🎯 TAKE PROFIT: [valore preciso] — [perché in 1 riga semplice]\n"
        "💡 CONSIGLIO: COMPRA / VENDI / ASPETTA\n"
        "[2 righe max di spiegazione semplicissima]\n\n"
        "Usa emoji, sii chiaro e incoraggiante."
    ),
    "intermedio": (
        "Sei un analista tecnico. Analizza questo grafico in italiano con questo formato:\n\n"
        "📈 TREND: [direzione e forza]\n"
        "🔑 LIVELLI CHIAVE: Supporto $X | Resistenza $X\n"
        "🛑 STOP LOSS: $X [-X%] | Invalidazione: [motivazione tecnica]\n"
        "🎯 TP1: $X [+X%] — R/R 1:X\n"
        "🎯 TP2: $X [+X%] — R/R 1:X\n"
        "📊 SEGNALI: [RSI visivo, volume, pattern]\n"
        "⚖️ BIAS: Rialzista / Ribassista / Neutro\n"
        "🎯 ENTRY IDEALE: $X"
    ),
    "esperto": (
        "Sei un analista quantitativo senior. Analisi tecnica professionale in italiano:\n\n"
        "🏗️ STRUTTURA: [macro trend, micro price action, key levels]\n"
        "📐 PATTERN: [pattern tecnici identificati: H&S, wedge, flag, double top/bottom, ecc.]\n"
        "🔑 LIVELLI: [supporti/resistenze statici, dinamici, pivot]\n"
        "📏 FIBONACCI: [livelli se visibili]\n"
        "📦 VOLUME: [analisi volume]\n"
        "🛑 STOP LOSS: $X | Invalidazione: [dettaglio]\n"
        "🎯 TP1: $X — R/R 1:X\n"
        "🎯 TP2: $X — R/R 1:X\n"
        "🎯 TP3: $X — R/R 1:X\n"
        "📍 ENTRY: $X [motivazione]\n"
        "🧠 TESI: [tesi di trading in 2 righe]"
    ),
}


class ChartAnalysisBody(BaseModel):
    image_b64: str
    level: Optional[str] = "intermedio"
    question: Optional[str] = None


@app.post("/api/analyze-chart")
async def api_analyze_chart(body: ChartAnalysisBody):
    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)
    level = body.level if body.level in _GRAFICO_PROMPTS else "intermedio"
    prompt = _GRAFICO_PROMPTS[level]
    # Se l'utente ha aggiunto una domanda specifica, appendila al prompt
    if body.question:
        prompt = f"{prompt}\n\nDomanda specifica dell'utente: {body.question}"

    # Assicura il prefisso data URL
    img = body.image_b64
    if not img.startswith("data:"):
        img = f"data:image/jpeg;base64,{img}"

    try:
        resp = await groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": img}},
                ],
            }],
            max_tokens=800,
            temperature=0.4,
        )
        return {"analysis": resp.choices[0].message.content.strip()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Confessionale finanziario ────────────────────────────────────────────────

class ConfessionaleBody(BaseModel):
    text: str
    level: Optional[str] = "intermedio"


@app.post("/api/confessionale")
async def api_confessionale(body: ConfessionaleBody):
    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)
    if not body.text or len(body.text.strip()) < 10:
        return JSONResponse({"error": "Testo troppo breve"}, status_code=400)

    level = body.level if body.level in _LEVEL_SYSTEM else "intermedio"

    if level == "dilettante":
        tone = (
            "Parla come se spiegassi a un amico che non sa nulla di finanza. "
            "Usa parole semplici, esempi concreti della vita quotidiana, nessun termine tecnico. "
            "Sii rassicurante, incoraggiante e molto pratico. "
            "Se menzioni un bias, spiega brevemente cosa significa con parole semplici."
        )
        depth = "ANALISI: [2-3 frasi semplici e rassicuranti su cosa sta provando emotivamente, con esempi concreti]\nCONSIGLIO: [1-2 azioni pratiche e immediate che può fare oggi, spiegate semplicemente]"
    elif level == "esperto":
        tone = (
            "Parla con un investitore esperto che conosce la finanza comportamentale. "
            "Usa terminologia accademica appropriata (Kahneman, Thaler, Prospect Theory, ecc. se pertinenti). "
            "Sii diretto, analitico e sfidante — non rassicurare, ma analizzare con rigore. "
            "Puoi menzionare meccanismi neurali, asimmetrie cognitive, effetti di framing."
        )
        depth = "ANALISI: [3-4 frasi di analisi approfondita con riferimenti teorici se pertinenti, identifica meccanismi psicologici specifici]\nCONSIGLIO: [2-3 strategie concrete basate su evidence (regole di decision-making, pre-commitment, checklist)]"
    else:  # intermedio
        tone = (
            "Parla con un investitore con esperienza base-media. "
            "Usa un linguaggio chiaro ma non evitare i termini tecnici — spiegali brevemente. "
            "Sii empatico ma diretto."
        )
        depth = "ANALISI: [2-3 frasi che spiegano cosa sta succedendo psicologicamente]\nCONSIGLIO: [1-2 frasi concrete su come gestire questo momento emotivo]"

    sys_prompt = (
        "Sei uno psicologo comportamentale specializzato in finanza behaviorale. "
        "Analizza il testo dell'investitore e identifica i bias cognitivi presenti (FOMO, anchoring, loss aversion, "
        "overconfidence, herding, recency bias, panic selling, status quo bias, mental accounting, ecc.). "
        "Rispondi SEMPRE in italiano. " + tone
    )
    user_prompt = (
        f"L'investitore scrive:\n\"{body.text.strip()}\"\n\n"
        "Rispondi ESATTAMENTE in questo formato (niente altro):\n"
        f"BIAS: [elenco bias rilevati, separati da virgola]\n{depth}"
    )

    try:
        max_tok = 600 if level == "esperto" else 400
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=max_tok,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
        bias = analisi = consiglio = ""
        for line in text.split("\n"):
            l = line.strip()
            if l.upper().startswith("BIAS:"):       bias      = l[5:].strip()
            elif l.upper().startswith("ANALISI:"):  analisi   = l[8:].strip()
            elif l.upper().startswith("CONSIGLIO:"): consiglio = l[10:].strip()
        return {"bias": bias, "analisi": analisi, "consiglio": consiglio, "level": level}
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
            "closed": history[d].get("closed", False),
            "closed_at": history[d].get("closed_at"),
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
    is_closed = snap.get("closed", False)

    if is_closed:
        # Mercato chiuso: usa price_at_close già salvato, niente fetch live
        enriched = []
        for s in stocks:
            pa  = s.get("price_at_analysis", 0.0)
            pc  = s.get("price_at_close", pa)
            pct = s.get("close_vs_morning_pct") or (
                round((pc - pa) / pa * 100, 2) if pa > 0 and pc > 0 else None
            )
            enriched.append({
                **s,
                "current_price": round(pc, 4),
                "pct_since_analysis": pct,
                "closed": True,
            })
        return _clean({
            "date": date,
            "generated_at": snap.get("generated_at"),
            "closed": True,
            "closed_at": snap.get("closed_at"),
            "stocks": enriched,
        })

    # Mercato ancora aperto o dato intraday: fetch prezzi live
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
        "closed": False,
        "stocks": enriched,
    })


# ─── Score storico per ticker ─────────────────────────────────────────────────

@app.get("/api/history/score/{ticker}")
async def api_score_history(ticker: str):
    """Storico dello score giornaliero di un ticker (tutte le date disponibili)."""
    ticker = ticker.upper()
    if not HISTORY_FILE.exists():
        return []
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    points = []
    for date_iso in sorted(history.keys()):
        snap = history[date_iso]
        for s in snap.get("stocks", []):
            if s.get("ticker", "").upper() == ticker:
                points.append({
                    "date": date_iso,
                    "score_10": s.get("score_10"),
                    "price_at_analysis": s.get("price_at_analysis"),
                    "price_at_close": s.get("price_at_close"),
                    "verdict": s.get("verdict", ""),
                    "closed": snap.get("closed", False),
                })
                break

    return points


# ─── Accuracy storica dei segnali ─────────────────────────────────────────────

@app.get("/api/accuracy")
async def api_accuracy():
    """Accuracy storica: quante volte il segnale di Masker ha predetto la direzione corretta."""
    if not HISTORY_FILE.exists():
        return {"total": 0, "correct": 0, "accuracy_pct": 0, "by_score": {}}
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"total": 0, "correct": 0, "accuracy_pct": 0, "by_score": {}}

    total = 0
    correct = 0
    by_score = {
        "high": {"label": "Score 8–10", "total": 0, "correct": 0, "pct": 0},
        "mid":  {"label": "Score 6–7",  "total": 0, "correct": 0, "pct": 0},
        "low":  {"label": "Score 0–5",  "total": 0, "correct": 0, "pct": 0},
    }

    for snap in history.values():
        if not snap.get("closed", False):
            continue
        for s in snap.get("stocks", []):
            pa = s.get("price_at_analysis") or 0.0
            pc = s.get("price_at_close") or 0.0
            score = s.get("score_10") or 5.0
            if pa <= 0 or pc <= 0:
                continue
            pct = (pc - pa) / pa * 100
            verdict = (s.get("verdict") or "").upper()
            # Determina se il segnale era bullish o bearish
            if score >= 7 or any(k in verdict for k in ("COMPRA", "BUY", "SI", "SÌ")):
                expected_up = True
            elif score <= 4 or any(k in verdict for k in ("VENDI", "SELL", "NO")):
                expected_up = False
            else:
                continue  # neutro: skip
            is_correct = (expected_up and pct > 0) or (not expected_up and pct < 0)
            total += 1
            if is_correct:
                correct += 1
            bucket = "high" if score >= 8 else ("mid" if score >= 6 else "low")
            by_score[bucket]["total"] += 1
            if is_correct:
                by_score[bucket]["correct"] += 1

    for b in by_score.values():
        b["pct"] = round(b["correct"] / b["total"] * 100, 1) if b["total"] > 0 else 0

    return {
        "total": total,
        "correct": correct,
        "accuracy_pct": round(correct / total * 100, 1) if total > 0 else 0,
        "by_score": by_score,
    }


# ─── Backtest storico dei segnali ─────────────────────────────────────────────

@app.get("/api/backtest")
async def api_backtest():
    """
    Backtest: per ogni segnale nella history con price_at_close,
    calcola il ritorno mattina→chiusura, raggruppato per score bucket.
    Mostra avg return, win rate, best/worst, e portafoglio cumulativo simulato.
    """
    key = "backtest:v1"
    if (c := _cached(key, 1800)) is not None:
        return c

    if not HISTORY_FILE.exists():
        return {"total": 0, "days": 0, "by_score": {}, "portfolio": []}

    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"total": 0, "days": 0, "by_score": {}, "portfolio": []}

    signals: list = []
    portfolio_days: list = []  # per la curva cumulativa

    # Ordina le date per simulare in cronologia
    sorted_keys = sorted(history.keys())

    for date_key in sorted_keys:
        snap = history[date_key]
        if not snap.get("closed", False):
            continue
        date_str = date_key[:10]
        day_high_stocks = []

        for s in snap.get("stocks", []):
            pa = s.get("price_at_analysis") or 0.0
            pc = s.get("price_at_close") or 0.0
            score_10 = s.get("score_10") or 0.0
            ticker = s.get("ticker", "")
            if pa <= 0 or pc <= 0 or not ticker:
                continue
            ret = (pc - pa) / pa * 100
            signals.append({
                "ticker": ticker,
                "date": date_str,
                "score_10": score_10,
                "entry": round(pa, 4),
                "exit": round(pc, 4),
                "return_pct": round(ret, 2),
                "win": ret > 0,
            })
            if score_10 >= 8:
                day_high_stocks.append(ret)

        # Portafoglio simulato: equal-weight su tutti i Score≥8 di quel giorno
        if day_high_stocks:
            avg_day = sum(day_high_stocks) / len(day_high_stocks)
            portfolio_days.append({"date": date_str, "daily_return": round(avg_day, 2), "n": len(day_high_stocks)})

    # Calcola valore cumulativo portafoglio ($10k iniziali)
    portfolio_value = 10000.0
    portfolio_curve = []
    for pd_day in portfolio_days:
        portfolio_value *= (1 + pd_day["daily_return"] / 100)
        portfolio_curve.append({"date": pd_day["date"], "value": round(portfolio_value, 2)})

    # Raggruppa per score bucket
    def _bucket(s10: float) -> str:
        if s10 >= 8: return "high"
        if s10 >= 6: return "mid"
        return "low"

    buckets: dict = {
        "high": {"label": "Score ≥8",  "sigs": []},
        "mid":  {"label": "Score 6-7", "sigs": []},
        "low":  {"label": "Score <6",  "sigs": []},
    }
    for sig in signals:
        buckets[_bucket(sig["score_10"])]["sigs"].append(sig)

    by_score: dict = {}
    for k, b in buckets.items():
        sigs = b["sigs"]
        if not sigs:
            by_score[k] = {"label": b["label"], "total": 0, "avg_return": 0,
                           "win_rate": 0, "best": 0, "worst": 0, "top3": []}
            continue
        rets = [s["return_pct"] for s in sigs]
        wins = sum(1 for s in sigs if s["win"])
        top3 = sorted(sigs, key=lambda x: x["return_pct"], reverse=True)[:3]
        by_score[k] = {
            "label": b["label"],
            "total": len(sigs),
            "avg_return": round(sum(rets) / len(rets), 2),
            "win_rate": round(wins / len(sigs) * 100, 0),
            "best": round(max(rets), 2),
            "worst": round(min(rets), 2),
            "top3": [{"ticker": s["ticker"], "date": s["date"], "return_pct": s["return_pct"]} for s in top3],
        }

    result = {
        "total": len(signals),
        "days": len([k for k, v in history.items() if v.get("closed")]),
        "by_score": by_score,
        "portfolio": portfolio_curve,
        "portfolio_final": round(portfolio_value, 2),
        "portfolio_return_pct": round((portfolio_value - 10000) / 100, 2),
    }
    _store(key, result)
    return result


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

@app.get("/tg-login")
async def tg_login_page():
    """Pagina standalone per il widget Telegram Login (evita problemi con iniezione dinamica)."""
    bot_username = os.getenv("BOT_USERNAME", "")
    if not bot_username:
        return HTMLResponse("<p>Login non configurato (BOT_USERNAME mancante)</p>", status_code=503)
    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accedi con Telegram</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Inter,sans-serif;background:#06061A;color:#E6E1EF;
  display:flex;align-items:center;justify-content:center;min-height:100vh}}
.wrap{{text-align:center;padding:32px 24px}}
.icon{{font-size:2.2rem;margin-bottom:10px}}
h2{{font-size:1rem;font-weight:800;margin-bottom:6px}}
p{{font-size:.8rem;color:#8E8A9E;margin-bottom:20px;line-height:1.5}}
</style>
</head>
<body>
<div class="wrap">
  <div class="icon">📨</div>
  <h2>Accedi con Telegram</h2>
  <p>Clicca il pulsante per autorizzare l'accesso.</p>
  <script async src="https://telegram.org/js/telegram-widget.js?22"
    data-telegram-login="{bot_username}"
    data-size="large"
    data-onauth="onAuth(user)"
    data-request-access="write">
  </script>
</div>
<script>
function onAuth(user) {{
  if (window.opener && !window.opener.closed) {{
    window.opener.onTelegramAuth(user);
  }}
  window.close();
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


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


# ─── Watchlist Judge ──────────────────────────────────────────────────────────

class WatchlistJudgeBody(BaseModel):
    tickers: List[str]

@app.post("/api/watchlist/judge")
async def api_watchlist_judge(body: WatchlistJudgeBody):
    """
    Per ogni ticker nella watchlist, genera un verdetto AI in una riga.
    Cached per ticker-set, TTL 30 min.
    """
    tickers = [t.upper() for t in body.tickers[:12]]  # max 12
    if not tickers:
        return {"verdicts": []}

    if not groq_client:
        return JSONResponse({"error": "AI non disponibile"}, status_code=503)

    key = "wljudge:" + ",".join(sorted(tickers))
    if (c := _cached(key, 1800)) is not None:
        return c

    # Fetch dati per ogni ticker (in parallelo tramite asyncio)
    async def _fetch(t: str):
        try:
            d = await asyncio.to_thread(get_enriched_analysis, t)
            return t, _clean(d) if d else None
        except Exception:
            return t, None

    results = await asyncio.gather(*[_fetch(t) for t in tickers])

    # Costruisci contesto compatto per il prompt
    lines = []
    for ticker, d in results:
        if not d:
            lines.append(f"{ticker}: nessun dato")
            continue
        chg   = d.get("day_change_pct", 0) or 0
        rsi   = d.get("rsi", 50) or 50
        score = d.get("score_10", 5) or 5
        vol   = d.get("volatility", 0) or 0
        price = d.get("current_price", 0) or 0
        news  = d.get("news_sentiment_label", "neutre") or "neutre"
        lines.append(
            f"{ticker}: prezzo ${price:.2f}, oggi {chg:+.1f}%, RSI={rsi:.0f}, "
            f"score={score}/10, volatilità={vol:.0f}%, notizie={news}"
        )

    context = "\n".join(lines)
    prompt = (
        f"Sei un analista che valuta ogni stock della watchlist di un trader.\n"
        f"Dati aggiornati:\n{context}\n\n"
        f"Per OGNI ticker, scrivi ESATTAMENTE una riga nel formato:\n"
        f"TICKER | [emoji] [verdetto breve, max 12 parole]\n\n"
        f"Emoji: ✅ compra/interessante, ⚠️ attento/aspetta, 🔴 evita/pericoloso, ⏸ neutro.\n"
        f"Sii diretto, concreto, in italiano. Zero generalità. Cita numeri specifici.\n"
        f"Esempio: 'NVDA | ⚠️ RSI 68, aspetta pullback sotto $118 prima di entrare'"
    )

    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.35,
        )
        text = resp.choices[0].message.content.strip()

        # Parse: TICKER | verdict
        verdicts = []
        for line in text.split("\n"):
            line = line.strip()
            if "|" in line:
                parts = line.split("|", 1)
                t = parts[0].strip().upper().lstrip("•-*0123456789. ")
                v = parts[1].strip()
                if t and v:
                    verdicts.append({"ticker": t, "verdict": v})

        result = {"verdicts": verdicts, "generated_at": datetime.now(timezone.utc).isoformat()}
        _store(key, result)
        return result

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Notizie titolo ───────────────────────────────────────────────────────────

@app.get("/api/stock/{ticker}/news")
async def api_news(ticker: str):
    t = ticker.upper()
    key = f"news:{t}"
    if (c := _cached(key, 1800)) is not None:
        return c

    def _get():
        import yfinance as yf
        items = yf.Ticker(t).news or []
        result = []
        for n in items[:6]:
            cnt = n.get("content", {}) if isinstance(n.get("content"), dict) else {}
            title = cnt.get("title") or n.get("title", "")
            if not title:
                continue
            link = ""
            for k in ("canonicalUrl", "clickThroughUrl"):
                v = cnt.get(k)
                if isinstance(v, dict):
                    link = v.get("url", ""); break
            if not link:
                link = n.get("link", "")
            pub = (cnt.get("provider") or {}).get("displayName") or n.get("publisher", "")
            ts = cnt.get("pubDate") or n.get("providerPublishTime")
            result.append({"title": title, "link": link, "publisher": pub, "time": ts})
        return result

    data = await asyncio.to_thread(_get)

    # Sentiment AI su ogni titolo (una sola chiamata Groq per tutte le headline)
    if groq_client and data:
        titles_str = "\n".join(f"{i+1}. {r['title']}" for i, r in enumerate(data))
        sent_prompt = (
            f"Per ognuno di questi titoli di notizie finanziarie su {t}, "
            "rispondi SOLO con il numero e una parola: POSITIVO, NEGATIVO o NEUTRO.\n\n"
            + titles_str
        )
        try:
            resp = await groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile", max_tokens=60,
                messages=[{"role": "user", "content": sent_prompt}]
            )
            for line in resp.choices[0].message.content.strip().split("\n"):
                parts = line.strip().split(".", 1)
                if len(parts) == 2:
                    try:
                        idx = int(parts[0].strip()) - 1
                        s = parts[1].strip().upper()
                        if 0 <= idx < len(data) and s in ("POSITIVO", "NEGATIVO", "NEUTRO"):
                            data[idx]["sentiment"] = s
                    except Exception:
                        pass
        except Exception:
            pass
    for r in data:
        r.setdefault("sentiment", "NEUTRO")

    _store(key, data)
    return data


# ─── Price target + short interest ───────────────────────────────────────────

@app.get("/api/stock/{ticker}/targets")
async def api_targets(ticker: str):
    t = ticker.upper()
    key = f"targets:{t}"
    if (c := _cached(key, 3600)) is not None:
        return c

    def _get():
        import yfinance as yf
        info = yf.Ticker(t).info or {}
        return {
            "targetMean":   info.get("targetMeanPrice"),
            "targetHigh":   info.get("targetHighPrice"),
            "targetLow":    info.get("targetLowPrice"),
            "recommendation": info.get("recommendationKey", ""),
            "numAnalysts":  info.get("numberOfAnalystOpinions"),
            "shortFloat":   info.get("shortPercentOfFloat"),
            "shortRatio":   info.get("shortRatio"),
            "sector":       info.get("sector", ""),
            "industry":     info.get("industry", ""),
        }

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return data


# ─── Earnings calendar ────────────────────────────────────────────────────────

@app.get("/api/earnings")
async def api_earnings(tickers: str = ""):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()][:20]
    if not ticker_list:
        return []
    key = f"earnings:{'_'.join(sorted(ticker_list))}"
    if (c := _cached(key, 3600)) is not None:
        return c

    def _get():
        import yfinance as yf
        result = []
        for t in ticker_list:
            try:
                cal = yf.Ticker(t).calendar
                if not cal:
                    continue
                # yfinance calendar può essere dict o DataFrame
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date") or cal.get("earningsDate")
                else:
                    try:
                        ed = cal.index.tolist() if hasattr(cal, 'index') else []
                    except Exception:
                        ed = []
                if not ed:
                    continue
                if isinstance(ed, (list, tuple)) and len(ed) > 0:
                    date_val = str(ed[0])[:10]
                elif isinstance(ed, str):
                    date_val = ed[:10]
                else:
                    date_val = str(ed)[:10]
                eps_est = None
                rev_est = None
                if isinstance(cal, dict):
                    eps_est = cal.get("EPS Estimate") or cal.get("epsEstimate")
                    rev_est = cal.get("Revenue Estimate") or cal.get("revenueEstimate")
                result.append({"ticker": t, "date": date_val, "eps_est": eps_est, "rev_est": rev_est})
            except Exception:
                pass
        result.sort(key=lambda x: x["date"])
        return result

    data = await asyncio.to_thread(_get)
    _store(key, data)
    return data


# ─── Risk metrics portafoglio ──────────────────────────────────────────────────

@app.get("/api/portfolio/risk")
async def api_portfolio_risk(tickers: str = "", weights: str = ""):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    weight_list = [float(w) for w in weights.split(",") if w.strip()]
    if not ticker_list or not weight_list or len(ticker_list) != len(weight_list):
        return JSONResponse({"error": "Parametri non validi"}, status_code=400)
    key = f"risk:{'_'.join(sorted(ticker_list))}"
    if (c := _cached(key, 3600)) is not None:
        return c

    def _get():
        import yfinance as yf
        import math
        all_t = list(set(ticker_list + ["SPY"]))
        raw = yf.download(all_t, period="1y", interval="1d", auto_adjust=True, progress=False)
        if isinstance(raw.columns, object) and hasattr(raw.columns, 'get_level_values'):
            closes = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw
        else:
            closes = raw
        if len(all_t) == 1:
            closes = closes.to_frame(name=all_t[0])
        returns = closes.pct_change().dropna()

        # Portfolio returns (weighted)
        port_ret = sum(
            w * returns[t] for t, w in zip(ticker_list, weight_list)
            if t in returns.columns
        )
        spy_ret = returns["SPY"] if "SPY" in returns.columns else port_ret

        days = len(port_ret)
        if days < 30:
            return {"error": "Storico insufficiente"}

        vol = float(port_ret.std() * math.sqrt(252)) * 100
        rf_daily = 0.045 / 252
        sharpe = float((port_ret.mean() - rf_daily) / port_ret.std() * math.sqrt(252)) if port_ret.std() > 0 else 0

        cov = float(port_ret.cov(spy_ret))
        var_spy = float(spy_ret.var())
        beta = round(cov / var_spy, 2) if var_spy > 0 else 1.0

        cum = (1 + port_ret).cumprod()
        rolling_max = cum.cummax()
        max_dd = float(((cum - rolling_max) / rolling_max).min()) * 100

        total_ret = float((cum.iloc[-1] - 1) * 100)

        return {
            "volatility": round(vol, 1),
            "sharpe": round(sharpe, 2),
            "beta": round(beta, 2),
            "max_drawdown": round(max_dd, 1),
            "total_return": round(total_ret, 1),
            "days": days,
        }

    try:
        data = await asyncio.to_thread(_get)
        _store(key, data)
        return data
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Chat AI ──────────────────────────────────────────────────────────────────

class ChatBody(BaseModel):
    message: str
    portfolio: Optional[dict] = {}
    watchlist: Optional[list] = []
    history: Optional[list] = []
    level: Optional[str] = "intermedio"


@app.post("/api/chat")
async def api_chat(body: ChatBody):
    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)

    level = body.level if body.level in _LEVEL_SYSTEM else "intermedio"

    # ── Fetch prezzi real-time per portfolio + watchlist ──────────────────────
    all_tickers = list(dict.fromkeys(
        list(body.portfolio.keys()) +
        [t for t in (body.watchlist or []) if isinstance(t, str)]
    ))[:25]

    prices: dict = {}
    if all_tickers:
        def _fetch_rt():
            import yfinance as yf
            tkrs = " ".join(all_tickers)
            raw = yf.download(tkrs, period="2d", interval="1d",
                              progress=False, auto_adjust=True)
            if raw.empty:
                return {}
            out: dict = {}
            if len(all_tickers) == 1:
                cl = raw["Close"]
                if len(cl) >= 2:
                    out[all_tickers[0]] = {
                        "p": round(float(cl.iloc[-1]), 2),
                        "c": round(float((cl.iloc[-1] - cl.iloc[-2]) / cl.iloc[-2] * 100), 2),
                    }
            else:
                cl = raw["Close"]
                for t in all_tickers:
                    if t in cl.columns:
                        s = cl[t].dropna()
                        if len(s) >= 2:
                            out[t] = {
                                "p": round(float(s.iloc[-1]), 2),
                                "c": round(float((s.iloc[-1] - s.iloc[-2]) / s.iloc[-2] * 100), 2),
                            }
            return out
        try:
            prices = await asyncio.to_thread(_fetch_rt)
        except Exception:
            prices = {}

    # ── Contesto mercato real-time ─────────────────────────────────────────────
    market_ctx = ""
    if prices:
        market_ctx = "\n\n📊 PREZZI AGGIORNATI (mercato real-time):\n"
        for t, d in prices.items():
            sign = "+" if d["c"] >= 0 else ""
            market_ctx += f"  {t}: ${d['p']:.2f} ({sign}{d['c']:.2f}%)\n"

    # ── Contesto portafoglio con P&L corrente ─────────────────────────────────
    port_ctx = ""
    if body.portfolio:
        port_ctx = "\n\n💼 PORTAFOGLIO UTENTE:\n"
        for t, p in body.portfolio.items():
            qty = p.get("qty", 0)
            buy = p.get("buyPrice", 0)
            curr = prices.get(t, {}).get("p", 0)
            if curr and buy:
                pl = (curr - buy) / buy * 100
                sign = "+" if pl >= 0 else ""
                port_ctx += (
                    f"  {t}: {qty} az. | acquisto ${buy:.2f} | ora ${curr:.2f} "
                    f"| P&L {sign}{pl:.1f}%\n"
                )
            else:
                port_ctx += f"  {t}: {qty} azioni | acquisto ${buy:.2f}\n"

    # ── Watchlist ─────────────────────────────────────────────────────────────
    wl_ctx = ""
    if body.watchlist:
        wl_ctx = "\n📌 WATCHLIST: " + ", ".join(str(x) for x in body.watchlist)

    # ── Stile risposta per livello ─────────────────────────────────────────────
    level_style = {
        "dilettante": (
            "Rispondi in modo semplice, usa analogie quotidiane, zero gergo tecnico. "
            "Sii rassicurante e didattico. Massimo 4-5 frasi chiare."
        ),
        "intermedio": (
            "Usa terminologia tecnica (RSI, supporti, beta, P/E) ma spiega brevemente. "
            "Bilancia dettaglio e leggibilità. Puoi usare punti elenco."
        ),
        "esperto": (
            "Usa terminologia avanzata: Greeks, correlazioni, Sharpe, alpha, flow, "
            "mean reversion, momentum, regime di mercato. Sii denso e diretto, "
            "niente spiegazioni base. Dati prima, opinione dopo."
        ),
    }.get(level, "Rispondi in modo chiaro e professionale.")

    sys_prompt = (
        "Sei Marco, trader e analista finanziario con 20 anni di esperienza "
        "tra Goldman Sachs, Point72 e hedge fund europei. "
        "Hai una conoscenza enciclopedica di analisi tecnica, fondamentale, macro, "
        "opzioni, ETF, settori, earnings, Federal Reserve e BCE. "
        "Hai accesso ai dati di mercato in tempo reale mostrati qui sotto. "
        "\n\nREGOLE FONDAMENTALI:"
        "\n- Rispondi SOLO alle domande finanziarie che ti vengono poste."
        "\n- NON fare analisi spontanee o riepiloghi non richiesti."
        "\n- Sii diretto, concreto, dai opinioni nette quando richiesto."
        "\n- Se l'utente chiede di un titolo specifico, usa i dati real-time e il portafoglio."
        "\n- DISCLAIMER: le tue risposte sono puramente informative."
        "\n- Rispondi SEMPRE in italiano."
        f"\n\n{level_style}"
        + market_ctx + port_ctx + wl_ctx
    )

    messages = [{"role": "system", "content": sys_prompt}]
    for msg in (body.history or [])[-12:]:
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": body.message})

    try:
        max_tok = 900 if level == "esperto" else 700
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=max_tok,
            temperature=0.55,
        )
        return {"reply": resp.choices[0].message.content.strip()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Screener ─────────────────────────────────────────────────────────────────

_SCREENER_UNIVERSES: dict[str, list[str]] = {
    "us_large": [
        "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","AMD","INTC",
        "ORCL","CRM","NFLX","QCOM","TXN","MU","JPM","BAC","GS","V","MA","WFC",
        "BLK","MS","JNJ","UNH","LLY","ABBV","PFE","MRK","XOM","CVX","BA","CAT",
        "GE","HON","WMT","HD","COST","PG","NKE","DIS","MCD","SBUX",
    ],
    "us_tech": [
        "AAPL","MSFT","NVDA","GOOGL","META","TSLA","AVGO","AMD","INTC","ORCL",
        "CRM","NFLX","QCOM","TXN","MU","SHOP","SNOW","NET","PLTR","UBER","ABNB",
        "DDOG","ZS","CRWD","HOOD","SOFI","ARM","SMCI","DELL","HPQ",
    ],
    "europa": [
        "ASML","SAP","STM","ERIC","AZN","SNY","NVS","SHEL","BP","EQNR",
        "UBS","ING","BBVA","SAN","TTE","RIO","GSK","VOD",
    ],
    "asia": [
        "TSM","TM","SONY","BIDU","JD","BABA","INFY","WIT","HDB",
        "SE","MELI","NIO","LI","XPEV","BEKE",
    ],
}
_SCREENER_TTL = 120  # 2 min


class ScreenerBody(BaseModel):
    universe: str = "us_large"
    custom_tickers: Optional[List[str]] = []
    min_change: Optional[float] = None
    max_change: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    direction: str = "all"       # all | up | down
    mcap: str = "all"            # all | mega | large | mid | small
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    volume_anomalo: bool = False  # volume > 2x media
    near_52w: Optional[str] = None  # "high" | "low"


@app.post("/api/screener")
async def api_screener(body: ScreenerBody):
    if body.universe == "custom" and body.custom_tickers:
        tickers = [t.upper() for t in body.custom_tickers[:40]]
    else:
        tickers = _SCREENER_UNIVERSES.get(body.universe, _SCREENER_UNIVERSES["us_large"])

    needs_advanced = any([body.pe_min, body.pe_max, body.volume_anomalo, body.near_52w])
    cache_key = f"screener:{'adv' if needs_advanced else 'base'}:{body.universe}:{hash(tuple(sorted(tickers)))}"
    universe_data = _cached(cache_key, _SCREENER_TTL)

    if universe_data is None:
        def _fetch_one(t: str):
            import yfinance as yf
            try:
                tk = yf.Ticker(t)
                fi = tk.fast_info
                price = fi.last_price
                prev  = fi.regular_market_previous_close
                if not price or not prev or prev == 0:
                    return None
                chg = (price - prev) / prev * 100
                result = {
                    "ticker": t,
                    "price": round(float(price), 4),
                    "chg_pct": round(float(chg), 2),
                    "mcap": int(fi.market_cap or 0),
                    "pe": None,
                    "52w_high": None,
                    "52w_low": None,
                    "vol_ratio": None,
                }
                if needs_advanced:
                    try:
                        info = tk.info or {}
                        result["pe"] = info.get("forwardPE") or info.get("trailingPE")
                        result["52w_high"] = info.get("fiftyTwoWeekHigh")
                        result["52w_low"]  = info.get("fiftyTwoWeekLow")
                        # Volume ratio (current vs 10-day avg)
                        hist = tk.history(period="10d", interval="1d")
                        if not hist.empty and len(hist) >= 2:
                            avg_vol = float(hist["Volume"].iloc[:-1].mean())
                            curr_vol = float(hist["Volume"].iloc[-1])
                            if avg_vol > 0:
                                result["vol_ratio"] = round(curr_vol / avg_vol, 2)
                    except Exception:
                        pass
                return result
            except Exception:
                return None

        tasks = [asyncio.to_thread(_fetch_one, t) for t in tickers]
        results = await asyncio.gather(*tasks)
        universe_data = [r for r in results if r]
        _store(cache_key, universe_data)

    out = list(universe_data)

    if body.direction == "up":
        out = [x for x in out if x["chg_pct"] > 0]
    elif body.direction == "down":
        out = [x for x in out if x["chg_pct"] < 0]
    if body.min_change is not None:
        out = [x for x in out if x["chg_pct"] >= body.min_change]
    if body.max_change is not None:
        out = [x for x in out if x["chg_pct"] <= body.max_change]
    if body.min_price is not None:
        out = [x for x in out if x["price"] >= body.min_price]
    if body.max_price is not None:
        out = [x for x in out if x["price"] <= body.max_price]
    if body.mcap != "all":
        def _mcap_ok(m: int) -> bool:
            if body.mcap == "mega":  return m >= 200_000_000_000
            if body.mcap == "large": return 10_000_000_000 <= m < 200_000_000_000
            if body.mcap == "mid":   return 2_000_000_000 <= m < 10_000_000_000
            if body.mcap == "small": return 0 < m < 2_000_000_000
            return True
        out = [x for x in out if _mcap_ok(x["mcap"])]
    if body.pe_min is not None:
        out = [x for x in out if x.get("pe") is not None and x["pe"] >= body.pe_min]
    if body.pe_max is not None:
        out = [x for x in out if x.get("pe") is not None and x["pe"] <= body.pe_max]
    if body.volume_anomalo:
        out = [x for x in out if (x.get("vol_ratio") or 0) >= 2.0]
    if body.near_52w == "high":
        out = [x for x in out if x.get("52w_high") and x["price"] >= x["52w_high"] * 0.95]
    elif body.near_52w == "low":
        out = [x for x in out if x.get("52w_low") and x["price"] <= x["52w_low"] * 1.05]

    out.sort(key=lambda x: abs(x["chg_pct"]), reverse=True)
    return out[:30]


# ─── Confronto AI tra 2 azioni ───────────────────────────────────────────────

class CompareBody(BaseModel):
    ticker1: str
    ticker2: str


@app.post("/api/compare")
async def api_compare(body: CompareBody):
    """Confronto AI approfondito tra due ticker con verdetto finale."""
    t1 = body.ticker1.upper().strip()
    t2 = body.ticker2.upper().strip()
    if not t1 or not t2 or t1 == t2:
        return JSONResponse({"error": "Servono due ticker diversi"}, status_code=400)
    key = f"compare:{t1}:{t2}"
    if (c := _cached(key, 1800)) is not None:
        return c

    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)

    def _fetch(t: str):
        import yfinance as yf
        try:
            tk = yf.Ticker(t)
            fi = tk.fast_info
            info = tk.info or {}
            price = fi.last_price or 0
            prev = fi.regular_market_previous_close or price
            chg = (price - prev) / prev * 100 if prev else 0
            return {
                "ticker": t,
                "name": info.get("shortName") or info.get("longName") or t,
                "price": round(float(price), 2),
                "chg": round(float(chg), 2),
                "pe": info.get("forwardPE"),
                "eps": info.get("trailingEps"),
                "margin": info.get("profitMargins"),
                "roe": info.get("returnOnEquity"),
                "div": info.get("dividendYield"),
                "mcap": info.get("marketCap"),
                "sector": info.get("sector", "N/D"),
            }
        except Exception:
            return None

    d1, d2 = await asyncio.gather(
        asyncio.to_thread(_fetch, t1),
        asyncio.to_thread(_fetch, t2),
    )
    if not d1 or not d2:
        return JSONResponse({"error": "Dati non disponibili per uno o entrambi i ticker"}, status_code=404)

    def _fmt(d: dict) -> str:
        pe  = f"{d['pe']:.1f}" if d.get("pe") else "N/D"
        eps = f"{d['eps']:.2f}" if d.get("eps") else "N/D"
        mrg = f"{d['margin']*100:.1f}%" if d.get("margin") else "N/D"
        roe = f"{d['roe']*100:.1f}%" if d.get("roe") else "N/D"
        div = f"{d['div']*100:.2f}%" if d.get("div") else "N/D"
        mc  = f"${d['mcap']/1e9:.1f}B" if d.get("mcap") else "N/D"
        return (f"{d['ticker']} ({d['name']}) — €{d['price']}, oggi {d['chg']:+.1f}%\n"
                f"  Settore: {d['sector']} | Market Cap: {mc}\n"
                f"  P/E forward: {pe} | EPS: {eps} | Margine: {mrg} | ROE: {roe} | Dividendo: {div}")

    prompt = (
        "Confronta queste due azioni e dimmi quale preferire come investimento.\n\n"
        f"AZIONE 1:\n{_fmt(d1)}\n\n"
        f"AZIONE 2:\n{_fmt(d2)}\n\n"
        "Rispondi SOLO con questo formato (in italiano, max 2 righe a sezione):\n"
        "PRO_1: [punti di forza di AZIONE 1]\n"
        "PRO_2: [punti di forza di AZIONE 2]\n"
        "CONTRO_1: [debolezze di AZIONE 1]\n"
        "CONTRO_2: [debolezze di AZIONE 2]\n"
        "VINCITORE: [solo il ticker vincitore, es: AAPL]\n"
        "MOTIVO: [1 frase secca che spiega perché]\n"
        "CONFIDENZA: [numero intero da 55 a 92]\n"
    )
    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
            messages=[
                {"role": "system", "content": "Sei un analista finanziario esperto. Confronta oggettivamente due azioni usando dati fondamentali e tecnici. Sii diretto e dai un vincitore chiaro. Rispondi in italiano."},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
        pro1 = pro2 = contro1 = contro2 = vincitore = motivo = ""
        confidenza = 70
        for line in text.split("\n"):
            l = line.strip()
            lu = l.upper()
            if lu.startswith("PRO_1:"):       pro1      = l[6:].strip()
            elif lu.startswith("PRO_2:"):     pro2      = l[6:].strip()
            elif lu.startswith("CONTRO_1:"):  contro1   = l[9:].strip()
            elif lu.startswith("CONTRO_2:"):  contro2   = l[9:].strip()
            elif lu.startswith("VINCITORE:"): vincitore = l[10:].strip().upper()
            elif lu.startswith("MOTIVO:"):    motivo    = l[7:].strip()
            elif lu.startswith("CONFIDENZA:"):
                try:
                    confidenza = int("".join(c for c in l[11:] if c.isdigit())[:2] or "70")
                except Exception:
                    confidenza = 70
        result = {
            "ticker1": t1, "name1": d1["name"],
            "ticker2": t2, "name2": d2["name"],
            "pro1": pro1, "pro2": pro2,
            "contro1": contro1, "contro2": contro2,
            "vincitore": vincitore or t1,
            "motivo": motivo,
            "confidenza": max(55, min(92, confidenza)),
        }
        _store(key, result)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Briefing mercato giornaliero ─────────────────────────────────────────────

@app.get("/api/briefing")
async def api_briefing():
    """AI genera un briefing di mercato giornaliero basato sugli indici principali."""
    key = "briefing:daily"
    if (c := _cached(key, 3600)) is not None:
        return c

    if not groq_client:
        return JSONResponse({"error": "GROQ_API_KEY non configurata"}, status_code=503)

    def _fetch_indices():
        import yfinance as yf
        indices = {
            "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "DOW JONES": "^DJI",
            "FTSE MIB": "FTSEMIB.MI", "DAX": "^GDAXI", "CAC 40": "^FCHI",
            "BTC": "BTC-USD", "ORO": "GC=F", "PETROLIO": "CL=F",
        }
        rows = []
        for name, sym in indices.items():
            try:
                fi = yf.Ticker(sym).fast_info
                price = fi.last_price or 0
                prev  = fi.regular_market_previous_close or price
                chg   = (price - prev) / prev * 100 if prev else 0
                rows.append(f"{name}: {price:,.2f} ({chg:+.2f}%)")
            except Exception:
                pass
        return rows

    rows = await asyncio.to_thread(_fetch_indices)
    if not rows:
        return JSONResponse({"error": "Dati indici non disponibili"}, status_code=503)

    market_str = "\n".join(rows)
    import datetime
    today = datetime.date.today().strftime("%d %B %Y")

    prompt = (
        f"Dati di mercato di oggi {today}:\n{market_str}\n\n"
        "Scrivi un briefing di mercato professionale in italiano. Formato ESATTO:\n"
        "TITOLO: [titolo breve e incisivo del giorno]\n"
        "APERTURA: [2-3 frasi sul sentiment generale dei mercati]\n"
        "MACRO: [1-2 frasi su trend macroeconomici rilevanti dal contesto]\n"
        "CRYPTO: [1-2 frasi su BTC e crypto]\n"
        "COMMODITIES: [1-2 frasi su oro e petrolio]\n"
        "OUTLOOK: [1-2 frasi di outlook per le prossime ore]\n"
        "MOOD: BULL oppure BEAR oppure NEUTRO\n"
    )
    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            messages=[
                {"role": "system", "content": "Sei un analista di mercato senior che scrive briefing quotidiani per investitori italiani. Tono professionale ma diretto. Scrivi in italiano."},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
        titolo = apertura = macro = crypto = commodities = outlook = mood = ""
        for line in text.split("\n"):
            l = line.strip()
            lu = l.upper()
            if lu.startswith("TITOLO:"):       titolo      = l[7:].strip()
            elif lu.startswith("APERTURA:"):   apertura    = l[9:].strip()
            elif lu.startswith("MACRO:"):      macro       = l[6:].strip()
            elif lu.startswith("CRYPTO:"):     crypto      = l[7:].strip()
            elif lu.startswith("COMMODITIES:"): commodities= l[12:].strip()
            elif lu.startswith("OUTLOOK:"):    outlook     = l[8:].strip()
            elif lu.startswith("MOOD:"):
                raw = l[5:].strip().upper()
                mood = "BULL" if "BULL" in raw else "BEAR" if "BEAR" in raw else "NEUTRO"
        result = {
            "date": today,
            "indices": rows,
            "titolo": titolo,
            "apertura": apertura,
            "macro": macro,
            "crypto": crypto,
            "commodities": commodities,
            "outlook": outlook,
            "mood": mood or "NEUTRO",
        }
        _store(key, result)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Manifest / SW (PWA) ──────────────────────────────────────────────────────

@app.get("/manifest.json")
async def pwa_manifest():
    return FileResponse(str(STATIC / "manifest.json"), media_type="application/manifest+json")


@app.get("/sw.js")
async def pwa_sw():
    return FileResponse(str(STATIC / "sw.js"), media_type="application/javascript")


@app.get("/icon.svg")
async def pwa_icon():
    return FileResponse(str(STATIC / "icon.svg"), media_type="image/svg+xml")


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "cache_keys": len(_cache), "jwt_ok": _JWT_OK}


@app.get("/")
async def root():
    return FileResponse(str(STATIC / "index.html"))


@app.get("/landing")
async def landing():
    return FileResponse(str(STATIC / "landing.html"))
