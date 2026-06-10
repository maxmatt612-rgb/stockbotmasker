"""
daily_push.py — invio automatico analisi su Telegram (mattina + recap serale).

Pensato per girare come job "one-shot" (GitHub Actions cron). Due modalità via
variabile MODE:
  MODE=morning (default) -> scan top 10 + verdetti AI, invio, e salva le scelte
                            in morning_picks.json (usato la sera + tiene vivo il repo).
  MODE=evening           -> rilegge morning_picks.json e manda il recap
                            (prezzo del mattino -> prezzo attuale, migliore/peggiore).

Variabili d'ambiente (GitHub Secrets):
  BOT_TOKEN          token del bot Telegram
  GROQ_API_KEY       chiave Groq (verdetti AI)
  TELEGRAM_CHAT_ID   chat/gruppo a cui inviare
Opzionali:
  TELEGRAM_TOPIC_ID  topic del gruppo (se è un forum)
  MODE               "morning" | "evening"
"""
import os
import json
import asyncio
from datetime import datetime

from telegram import Bot
from telegram.constants import ParseMode

from analyzer import scan_cheap_stocks, get_enriched_analysis, format_scan_card
from bot import generate_ai_verdict, ROME

_GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
_MESI_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

PICKS_FILE = "morning_picks.json"
MODE = (os.getenv("MODE") or "morning").strip().lower()

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("GROUP_CHAT_ID")
_raw_topic = os.getenv("TELEGRAM_TOPIC_ID") or os.getenv("TOPIC_ANALISI_ID") or ""
TOPIC_ID = int(_raw_topic) if _raw_topic.strip().lstrip("-").isdigit() else None
_TG_LIMIT = 3800  # margine sotto il limite Telegram di 4096


def _kw():
    k = {"parse_mode": ParseMode.HTML}
    if TOPIC_ID is not None:
        k["message_thread_id"] = TOPIC_ID
    return k


async def _send_blocks(bot, chat_id, blocks, sep="\n"):
    """Unisce i blocchi e invia in più messaggi se si supera il limite Telegram.
    I tag HTML sono sempre chiusi dentro un singolo blocco/riga, quindi è sicuro."""
    msg = ""
    for b in blocks:
        if msg and len(msg) + len(sep) + len(b) > _TG_LIMIT:
            await bot.send_message(chat_id, msg, **_kw())
            msg = b
        else:
            msg = (msg + sep + b) if msg else b
    if msg:
        await bot.send_message(chat_id, msg, **_kw())


def _fetch_prices(tickers):
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            out[t] = round(float(yf.Ticker(t).fast_info.last_price or 0), 4)
        except Exception:
            out[t] = 0.0
    return out


# ─────────────────────────────────────────────────────────────────────────────
# MATTINA
# ─────────────────────────────────────────────────────────────────────────────
async def run_morning(bot):
    now = datetime.now(ROME)
    date_str = f"{_GIORNI_IT[now.weekday()]} {now.day} {_MESI_IT[now.month - 1]} {now.year}"

    risultati = await asyncio.to_thread(scan_cheap_stocks, 40.0, 10)
    if not risultati:
        await bot.send_message(int(CHAT_ID),
                               "⚠️ Scanner non disponibile stamattina. Riprova più tardi.", **_kw())
        return

    enriched = []
    for r in risultati:
        d = await asyncio.to_thread(get_enriched_analysis, r["ticker"])
        if d:
            d["score"] = r.get("score", 0)
            d["score_10"] = r.get("score_10", 5.0)
            d["vol_ratio"] = r.get("vol_ratio", 1.0)
            enriched.append(d)
    if not enriched:
        await bot.send_message(int(CHAT_ID), "⚠️ Nessun dato disponibile stamattina.", **_kw())
        return

    ai_raw = await asyncio.gather(*[generate_ai_verdict(d) for d in enriched], return_exceptions=True)
    ai_verdicts = [r if not isinstance(r, Exception) else {"verdict": "", "bullet1": "", "bullet2": ""}
                   for r in ai_raw]

    cards = [format_scan_card(d, ai, i + 1) for i, (d, ai) in enumerate(zip(enriched, ai_verdicts))]
    header = (f"📊 <b>Analisi mattutina — {date_str}</b>\n"
              f"<i>Top {len(enriched)} azioni sotto €35 (~$40)</i>")
    footer = "<i>Dati: Yahoo Finance | AI: Groq Llama 70B</i>"
    sep = "\n" + "─" * 18 + "\n"
    await _send_blocks(bot, int(CHAT_ID), [header] + cards + [footer], sep=sep)

    # Salva le scelte del mattino (per il recap serale + tiene vivo il repo)
    picks = {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "stocks": [
            {
                "ticker": d["ticker"],
                "name": (d.get("name") or "")[:30],
                "price_at_analysis": d.get("current_price", 0) or 0,
                "score_10": d.get("score_10", 0),
                "verdict": (ai.get("verdict", "") if isinstance(ai, dict) else ""),
            }
            for d, ai in zip(enriched, ai_verdicts)
        ],
    }
    try:
        with open(PICKS_FILE, "w", encoding="utf-8") as f:
            json.dump(picks, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# SERA (recap)
# ─────────────────────────────────────────────────────────────────────────────
async def run_evening(bot):
    now = datetime.now(ROME)
    today = now.strftime("%Y-%m-%d")
    date_str = f"{_GIORNI_IT[now.weekday()]} {now.day} {_MESI_IT[now.month - 1]} {now.year}"

    if not os.path.exists(PICKS_FILE):
        await bot.send_message(int(CHAT_ID), "ℹ️ Nessuna analisi mattutina trovata: recap saltato.", **_kw())
        return
    try:
        picks = json.load(open(PICKS_FILE, encoding="utf-8"))
    except Exception:
        await bot.send_message(int(CHAT_ID), "ℹ️ Impossibile leggere l'analisi del mattino.", **_kw())
        return

    if picks.get("date") != today:
        await bot.send_message(int(CHAT_ID),
                               "ℹ️ Nessuna analisi del mattino di oggi: recap serale saltato.", **_kw())
        return

    stocks = picks.get("stocks", [])
    prices = await asyncio.to_thread(_fetch_prices, [s["ticker"] for s in stocks])
    for s in stocks:
        pa = s.get("price_at_analysis", 0) or 0
        pc = prices.get(s["ticker"], 0) or 0
        s["_pc"] = pc
        s["_chg"] = round((pc - pa) / pa * 100, 2) if (pa > 0 and pc > 0) else None

    stocks.sort(key=lambda x: (x["_chg"] if x["_chg"] is not None else -999), reverse=True)

    blocks = [f"📊 <b>Recap serale — {date_str}</b>\n<i>Analisi del mattino → prezzo attuale</i>"]
    for s in stocks:
        chg = s["_chg"]
        emj = "📈" if (chg or 0) >= 0 else "📉"
        chg_str = f"{chg:+.2f}%" if chg is not None else "—"
        blocks.append(
            f"{emj} <b>{s['ticker']}</b> {s.get('name', '')}\n"
            f"   ${s.get('price_at_analysis', 0):.4f} → <b>${s['_pc']:.4f}</b> ({chg_str})"
        )

    valid = [s for s in stocks if s["_chg"] is not None]
    if valid:
        best, worst = valid[0], valid[-1]
        avg = sum(s["_chg"] for s in valid) / len(valid)
        blocks.append(
            f"\n🏆 Migliore: <b>{best['ticker']}</b> {best['_chg']:+.2f}%\n"
            f"💔 Peggiore: <b>{worst['ticker']}</b> {worst['_chg']:+.2f}%\n"
            f"📊 Media del giorno: {avg:+.2f}%"
        )
    blocks.append("\n<i>Dati: Yahoo Finance · Prossima analisi domani mattina</i>")
    await _send_blocks(bot, int(CHAT_ID), blocks, sep="\n")


async def main():
    if not TOKEN:
        raise SystemExit("❌ Manca BOT_TOKEN")
    if not CHAT_ID:
        raise SystemExit("❌ Manca TELEGRAM_CHAT_ID (o GROUP_CHAT_ID)")
    bot = Bot(TOKEN)
    if MODE == "evening":
        await run_evening(bot)
    else:
        await run_morning(bot)


if __name__ == "__main__":
    asyncio.run(main())
