"""
daily_push.py — invio automatico dell'analisi mattutina su Telegram.

Pensato per girare come job "one-shot" (es. GitHub Actions cron): fa lo scan
delle 10 azioni, genera i verdetti AI, manda il messaggio su Telegram e termina.
NON richiede un server sempre acceso.

Variabili d'ambiente richieste (impostarle come GitHub Secrets):
  BOT_TOKEN          token del bot Telegram
  GROQ_API_KEY       chiave Groq per i verdetti AI
  TELEGRAM_CHAT_ID   chat a cui inviare (il tuo ID utente o l'ID del gruppo)
Opzionale:
  MODE               "morning" (default) — predisposto per estensioni future
"""
import os
import asyncio
from datetime import datetime

from telegram import Bot
from telegram.constants import ParseMode

from analyzer import scan_cheap_stocks, get_enriched_analysis, format_scan_card
from bot import generate_ai_verdict, ROME

_GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
_MESI_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("GROUP_CHAT_ID")
# Topic opzionale: se il gruppo usa i "Topic" (forum), invia nel thread giusto.
_raw_topic = os.getenv("TELEGRAM_TOPIC_ID") or os.getenv("TOPIC_ANALISI_ID") or ""
TOPIC_ID = int(_raw_topic) if _raw_topic.strip().lstrip("-").isdigit() else None
_TG_LIMIT = 3800  # margine sotto il limite Telegram di 4096 caratteri


def _kw():
    """Parametri comuni per send_message (HTML + eventuale topic del gruppo)."""
    k = {"parse_mode": ParseMode.HTML}
    if TOPIC_ID is not None:
        k["message_thread_id"] = TOPIC_ID
    return k


async def _send_cards(bot, chat_id, header, cards, footer):
    """Invia l'header, poi i blocchi di card raggruppati sotto il limite Telegram, poi il footer."""
    sep = "\n" + "─" * 18 + "\n"
    msg = header
    for card in cards:
        if len(msg) + len(sep) + len(card) > _TG_LIMIT:
            await bot.send_message(chat_id, msg, **_kw())
            msg = card
        else:
            msg = (msg + sep + card) if msg else card
    if msg:
        await bot.send_message(chat_id, msg, **_kw())
    if footer:
        await bot.send_message(chat_id, footer, **_kw())


async def main():
    if not TOKEN:
        raise SystemExit("❌ Manca BOT_TOKEN")
    if not CHAT_ID:
        raise SystemExit("❌ Manca TELEGRAM_CHAT_ID (o GROUP_CHAT_ID)")

    bot = Bot(TOKEN)
    now = datetime.now(ROME)
    date_str = f"{_GIORNI_IT[now.weekday()]} {now.day} {_MESI_IT[now.month - 1]} {now.year}"

    # 1 ── Scanner top 10 (azioni accessibili sotto ~$40)
    risultati = await asyncio.to_thread(scan_cheap_stocks, 40.0, 10)
    if not risultati:
        await bot.send_message(int(CHAT_ID),
                               "⚠️ Scanner non disponibile stamattina. Riprova più tardi.",
                               **_kw())
        return

    # 2 ── Arricchimento dati per ogni azione
    enriched = []
    for r in risultati:
        d = await asyncio.to_thread(get_enriched_analysis, r["ticker"])
        if d:
            d["score"] = r.get("score", 0)
            d["score_10"] = r.get("score_10", 5.0)
            d["vol_ratio"] = r.get("vol_ratio", 1.0)
            enriched.append(d)
    if not enriched:
        await bot.send_message(int(CHAT_ID),
                               "⚠️ Nessun dato disponibile stamattina.",
                               **_kw())
        return

    # 3 ── Verdetti AI (in parallelo)
    ai_raw = await asyncio.gather(*[generate_ai_verdict(d) for d in enriched],
                                  return_exceptions=True)
    ai_verdicts = [
        r if not isinstance(r, Exception) else {"verdict": "", "bullet1": "", "bullet2": ""}
        for r in ai_raw
    ]

    # 4 ── Composizione e invio
    cards = [format_scan_card(d, ai, i + 1)
             for i, (d, ai) in enumerate(zip(enriched, ai_verdicts))]
    header = (f"📊 <b>Analisi mattutina — {date_str}</b>\n"
              f"<i>Top {len(enriched)} azioni sotto €35 (~$40)</i>")
    footer = "<i>Dati: Yahoo Finance | AI: Groq Llama 70B</i>"
    await _send_cards(bot, int(CHAT_ID), header, cards, footer)


if __name__ == "__main__":
    asyncio.run(main())
