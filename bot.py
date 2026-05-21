import asyncio
import json
import logging
import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from groq import AsyncGroq, AsyncGroqError
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from analyzer import (
    get_full_analysis,
    get_trading_analysis,
    get_enriched_analysis,
    scan_cheap_stocks,
    format_analysis_message,
    format_trading_message,
    format_confronto_message,
    format_morning_card,
    format_report_line,
)
from config import BOT_TOKEN, DEFAULT_WATCHLIST, REPORT_HOUR, REPORT_MINUTE

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

ROME = ZoneInfo("Europe/Rome")
DATA_FILE = Path("user_data.json")

_GROQ_KEY = os.getenv("GROQ_API_KEY")
groq_client = AsyncGroq(api_key=_GROQ_KEY) if _GROQ_KEY else None
if not _GROQ_KEY:
    logging.warning("GROQ_API_KEY non trovata — funzioni AI disabilitate")


# ─── Persistenza ─────────────────────────────────────────────────────────────

def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"watchlists": {}, "watchlist2": {}}


def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── Helpers bottoni ─────────────────────────────────────────────────────────

def btn(text: str, callback: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=callback)


def kb(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


# ─── AI helpers ──────────────────────────────────────────────────────────────

async def ai_confronto(d1: dict, d2: dict) -> str:
    if not groq_client:
        return "⚠️ AI non disponibile — GROQ_API_KEY mancante su Railway Variables."
    prompt = (
        f"Confronta queste due azioni e dimmi quale è migliore da acquistare ADESSO e perché.\n\n"
        f"{d1['ticker']} ({d1['name']}):\n"
        f"- Prezzo: ${d1['current_price']:.2f}, oggi {d1['day_change_pct']:+.1f}%\n"
        f"- RSI: {d1['rsi']:.0f}, Rischio: {d1['risk_level']}, Volatilità: {d1['volatility']:.0f}%\n"
        f"- Mese: {(d1['month_return'] or 0):+.1f}%, Anno: {(d1['ytd_return'] or 0):+.1f}%\n"
        f"- Segnali: {', '.join(d1['signals'])}\n\n"
        f"{d2['ticker']} ({d2['name']}):\n"
        f"- Prezzo: ${d2['current_price']:.2f}, oggi {d2['day_change_pct']:+.1f}%\n"
        f"- RSI: {d2['rsi']:.0f}, Rischio: {d2['risk_level']}, Volatilità: {d2['volatility']:.0f}%\n"
        f"- Mese: {(d2['month_return'] or 0):+.1f}%, Anno: {(d2['ytd_return'] or 0):+.1f}%\n"
        f"- Segnali: {', '.join(d2['signals'])}\n\n"
        f"Rispondi in 3-4 frasi in italiano. Sii diretto e dai una valutazione chiara."
    )
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[
                {"role": "system", "content": "Sei un esperto di mercati azionari. Rispondi sempre in italiano, conciso e pratico. Non dare mai consigli finanziari definitivi."},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Errore AI confronto: {e}")
        return "⚠️ AI non disponibile al momento."


async def generate_ai_verdict(d: dict) -> dict:
    """Genera verdict + 2 bullet motivazioni per una singola azione."""
    if not groq_client:
        return {"verdict": "", "bullet1": "", "bullet2": ""}

    pe = d.get("pe_ratio")
    pe_str = f"{pe:.1f}x" if pe and pe > 0 else "N/D"
    upside = d.get("upside_52w", 0.0)
    upside_str = f"+{upside:.1f}%" if upside >= 0 else f"{upside:.1f}% (già sui massimi)"

    prompt = (
        f"Azione: {d['ticker']} ({d['name']})\n"
        f"Prezzo: ${d['current_price']:.2f}, oggi {d['day_change_pct']:+.1f}%\n"
        f"RSI: {d['rsi']:.0f}, Volatilità: {d['volatility']:.0f}%, Rischio: {d['risk_level']}\n"
        f"Upside verso max 52w: {upside_str}\n"
        f"P/E: {pe_str}\n"
        f"Notizie: {d.get('news_sentiment_label', 'Neutre')}\n"
        f"Earnings oggi: {'Sì' if d.get('earnings_today') else 'No'}\n\n"
        "Rispondi SOLO in questo formato (italiano, conciso):\n"
        "VERDICT: [es. 'Interessante:', 'Rischioso:', 'Neutro:'] + max 12 parole di valutazione\n"
        "BULLET1: prima motivazione concreta max 12 parole\n"
        "BULLET2: seconda motivazione concreta max 12 parole"
    )
    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=130,
            messages=[
                {"role": "system", "content": "Sei un analista finanziario professionale. Solo italiano, diretto e conciso."},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
        verdict = bullet1 = bullet2 = ""
        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("VERDICT:"):
                verdict = line[8:].strip()
            elif line.upper().startswith("BULLET1:"):
                bullet1 = line[8:].strip()
            elif line.upper().startswith("BULLET2:"):
                bullet2 = line[8:].strip()
        return {"verdict": verdict, "bullet1": bullet1, "bullet2": bullet2}
    except Exception as e:
        logger.error(f"AI verdict {d['ticker']}: {e}")
        return {"verdict": "", "bullet1": "", "bullet2": ""}


# ─── /start ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    if uid not in data["watchlists"]:
        data["watchlists"][uid] = DEFAULT_WATCHLIST.copy()
        save_data(data)

    await update.message.reply_text(
        "👋 <b>Benvenuto nel tuo Assistente Azionario!</b>\n\n"
        "🌅 Report automatico ogni mattina alle <b>7:30</b>\n"
        "🌙 Recap serale alle <b>22:00</b> quando chiude il mercato USA\n"
        "📅 Recap settimanale ogni <b>Sabato</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Scrivi /help per vedere tutti i comandi.\n\n"
        "<i>Dati da Yahoo Finance · AI by Groq Llama 70B</i>",
        parse_mode=ParseMode.HTML,
    )


# ─── /help ───────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = kb([
        [btn("📊 Analisi mercato", "help_analisi"), btn("📈 Analisi azione", "help_apr")],
        [btn("🎯 Day Trading", "help_trading"), btn("⚔️ Confronto", "help_confronto")],
        [btn("💼 Portafoglio", "help_portafoglio"), btn("👁 Watchlist", "help_watchlist")],
        [btn("🤖 Chiedi all'AI", "help_chiediai")],
    ])
    await update.message.reply_text(
        "📖 <b>Comandi disponibili:</b>\n\n"
        "📊 /analisi — top 10 azioni sotto $20 adesso\n"
        "📈 /apr — analisi completa di una azione\n"
        "🎯 /trading — day trading: compra e vendi subito\n"
        "⚔️ /confronto — confronta due azioni con AI\n"
        "💼 /portafoglio — le tue azioni\n"
        "👁 /watchlist — lista azioni da seguire\n"
        "🤖 /chiediai — chiedi all'AI\n\n"
        "<i>Clicca un bottone per sapere come si usa</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ─── /analisi — scanner mercato ──────────────────────────────────────────────

async def cmd_analisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔍 <b>Scansiono ~250 azioni Revolut sotto i $20...</b>\n<i>Circa 30 secondi</i>",
        parse_mode=ParseMode.HTML,
    )
    risultati = await asyncio.to_thread(scan_cheap_stocks, 20.0, 10)

    if not risultati:
        await msg.edit_text("❌ Nessun dato disponibile. Riprova tra qualche minuto.")
        return

    lines = ["📊 <b>Top 10 azioni sotto $20 — adesso</b>\n"]
    for i, d in enumerate(risultati):
        chg = d["day_change_pct"]
        sign = "+" if chg >= 0 else ""
        emoji = "📈" if chg >= 0 else "📉"
        vol_str = f"Vol {d['vol_ratio']:.1f}x" if d["vol_ratio"] > 1.2 else ""
        lines.append(
            f"{i+1}. <b>{d['ticker']}</b> ${d['current_price']:.2f} "
            f"{emoji}{sign}{chg:.1f}%  {d['risk_emoji']}  ⭐{d['score_10']}/10\n"
            f"   RSI {d['rsi']:.0f} {vol_str}"
        )
    lines.append("\n<i>Clicca un bottone per analizzare</i>")

    # Bottoni per i primi 5
    top5 = risultati[:5]
    rows = []
    for i in range(0, len(top5), 2):
        row = [btn(f"📈 {top5[i]['ticker']}", f"apr:{top5[i]['ticker']}")]
        if i + 1 < len(top5):
            row.append(btn(f"📈 {top5[i+1]['ticker']}", f"apr:{top5[i+1]['ticker']}"))
        rows.append(row)
    rows.append([btn("🔄 Aggiorna scanner", "refresh_scan")])

    await msg.edit_text("\n\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb(rows))


# ─── /apr — analisi singola ──────────────────────────────────────────────────

async def cmd_apr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "📈 <b>/apr</b>\n\nAnalisi completa di una singola azione.\n\n"
            "<b>Uso:</b> /apr TICKER\n<b>Esempi:</b> /apr PLTR · /apr NIO · /apr F",
            parse_mode=ParseMode.HTML,
        )
        return
    ticker = context.args[0].upper()
    msg = await update.message.reply_text(f"⏳ Analizzo <b>{ticker}</b>...", parse_mode=ParseMode.HTML)
    data = get_full_analysis(ticker)
    if not data:
        await msg.edit_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
        return
    keyboard = kb([
        [btn(f"🎯 Trading {ticker}", f"trading:{ticker}"), btn(f"💼 Aggiungi", f"add_portfolio:{ticker}")],
        [btn(f"⚔️ Confronta con...", f"help_confronto")],
    ])
    await msg.edit_text(format_analysis_message(data), parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ─── /trading ────────────────────────────────────────────────────────────────

async def cmd_trading(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🎯 <b>/trading</b>\n\nAnalisi day trading — compra e rivendi in giornata o 1-3 giorni.\n"
            "Segnale COMPRA/VENDI/ATTENDI + stop loss e target.\n\n"
            "<b>Uso:</b> /trading TICKER\n<b>Esempi:</b> /trading NVDA · /trading TSLA",
            parse_mode=ParseMode.HTML,
        )
        return
    ticker = context.args[0].upper()
    msg = await update.message.reply_text(f"⏳ Analizzo <b>{ticker}</b> per trading...", parse_mode=ParseMode.HTML)
    data = get_trading_analysis(ticker)
    if not data:
        await msg.edit_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
        return
    keyboard = kb([
        [btn(f"📈 Analisi {ticker}", f"apr:{ticker}"), btn(f"💼 Aggiungi", f"add_portfolio:{ticker}")],
    ])
    await msg.edit_text(format_trading_message(data), parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ─── /confronto ──────────────────────────────────────────────────────────────

async def cmd_confronto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "⚔️ <b>/confronto</b>\n\nConfronta due azioni con dati + valutazione AI.\n\n"
            "<b>Uso:</b> /confronto TICKER1 TICKER2\n<b>Esempi:</b> /confronto PLTR NIO · /confronto TSLA RIVN",
            parse_mode=ParseMode.HTML,
        )
        return
    t1, t2 = context.args[0].upper(), context.args[1].upper()
    msg = await update.message.reply_text(
        f"⏳ Confronto <b>{t1}</b> vs <b>{t2}</b>...\n<i>Analizzo i dati e chiedo all'AI</i>",
        parse_mode=ParseMode.HTML,
    )
    d1 = get_full_analysis(t1)
    d2 = get_full_analysis(t2)
    if not d1:
        await msg.edit_text(f"❌ Nessun dato per <b>{t1}</b>.", parse_mode=ParseMode.HTML)
        return
    if not d2:
        await msg.edit_text(f"❌ Nessun dato per <b>{t2}</b>.", parse_mode=ParseMode.HTML)
        return

    confronto_text = format_confronto_message(d1, d2)
    ai_text = await ai_confronto(d1, d2)
    full_text = confronto_text + f"\n\n🤖 <b>Valutazione AI:</b>\n{ai_text}"

    keyboard = kb([
        [btn(f"📈 Analisi {t1}", f"apr:{t1}"), btn(f"📈 Analisi {t2}", f"apr:{t2}")],
        [btn(f"🎯 Trading {t1}", f"trading:{t1}"), btn(f"🎯 Trading {t2}", f"trading:{t2}")],
    ])
    await msg.edit_text(full_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ─── /portafoglio ────────────────────────────────────────────────────────────

async def cmd_portafoglio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    watchlist = data["watchlists"].get(uid, DEFAULT_WATCHLIST)
    lines = ["💼 <b>Il tuo portafoglio:</b>\n"] if watchlist else ["💼 <b>Portafoglio vuoto.</b>\n"]
    for t in watchlist:
        lines.append(f"  • {t}")
    lines.append("\n<b>Comandi:</b>\n/aggiungi TICKER · /rimuovi TICKER")

    # Bottoni per ogni azione nel portafoglio
    rows = []
    for i in range(0, len(watchlist), 3):
        rows.append([btn(f"📈 {t}", f"apr:{t}") for t in watchlist[i:i+3]])

    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=kb(rows) if rows else None,
    )


async def cmd_aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usa: /aggiungi TICKER")
        return
    ticker = context.args[0].upper()
    uid = str(update.effective_user.id)
    data = load_data()
    watchlist = data["watchlists"].setdefault(uid, DEFAULT_WATCHLIST.copy())
    if ticker in watchlist:
        await update.message.reply_text(f"<b>{ticker}</b> è già nel portafoglio.", parse_mode=ParseMode.HTML)
        return
    watchlist.append(ticker)
    save_data(data)
    keyboard = kb([[btn(f"📈 Analizza {ticker}", f"apr:{ticker}"), btn(f"🎯 Trading {ticker}", f"trading:{ticker}")]])
    await update.message.reply_text(f"✅ <b>{ticker}</b> aggiunto!", parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cmd_rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usa: /rimuovi TICKER")
        return
    ticker = context.args[0].upper()
    uid = str(update.effective_user.id)
    data = load_data()
    watchlist = data["watchlists"].get(uid, [])
    if ticker not in watchlist:
        await update.message.reply_text(f"<b>{ticker}</b> non è nel portafoglio.", parse_mode=ParseMode.HTML)
        return
    watchlist.remove(ticker)
    save_data(data)
    await update.message.reply_text(f"✅ <b>{ticker}</b> rimosso.", parse_mode=ParseMode.HTML)


# ─── /watchlist ──────────────────────────────────────────────────────────────

async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    wl = data.get("watchlist2", {}).get(uid, [])
    lines = ["👁 <b>La tua watchlist:</b>\n"] if wl else ["👁 <b>Watchlist vuota.</b>\n"]
    for t in wl:
        lines.append(f"  • {t}")
    lines.append("\n<b>Comandi:</b>\n/wadd TICKER · /wdel TICKER")
    rows = []
    for i in range(0, len(wl), 3):
        rows.append([btn(f"📈 {t}", f"apr:{t}") for t in wl[i:i+3]])
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=kb(rows) if rows else None,
    )


async def cmd_wadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usa: /wadd TICKER")
        return
    ticker = context.args[0].upper()
    uid = str(update.effective_user.id)
    data = load_data()
    wl = data.setdefault("watchlist2", {}).setdefault(uid, [])
    if ticker in wl:
        await update.message.reply_text(f"<b>{ticker}</b> è già nella watchlist.", parse_mode=ParseMode.HTML)
        return
    wl.append(ticker)
    save_data(data)
    await update.message.reply_text(f"✅ <b>{ticker}</b> aggiunto alla watchlist!", parse_mode=ParseMode.HTML)


async def cmd_wdel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usa: /wdel TICKER")
        return
    ticker = context.args[0].upper()
    uid = str(update.effective_user.id)
    data = load_data()
    wl = data.get("watchlist2", {}).get(uid, [])
    if ticker not in wl:
        await update.message.reply_text(f"<b>{ticker}</b> non è nella watchlist.", parse_mode=ParseMode.HTML)
        return
    wl.remove(ticker)
    save_data(data)
    await update.message.reply_text(f"✅ <b>{ticker}</b> rimosso.", parse_mode=ParseMode.HTML)


# ─── /chiediai ───────────────────────────────────────────────────────────────

async def cmd_chiediai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🤖 <b>/chiediai</b>\n\nFai qualsiasi domanda sul mondo azionario.\n\n"
            "<b>Uso:</b> /chiediai [domanda]\n\n"
            "<b>Esempi:</b>\n/chiediai su cosa investire oggi?\n"
            "/chiediai l'oro è un buon investimento?\n/chiediai cosa sono gli ETF?",
            parse_mode=ParseMode.HTML,
        )
        return
    question = " ".join(context.args)
    if not groq_client:
        await update.message.reply_text("❌ AI non disponibile — aggiungi <b>GROQ_API_KEY</b> nelle Variables di Railway.", parse_mode=ParseMode.HTML)
        return
    msg = await update.message.reply_text("🤖 Sto pensando...", parse_mode=ParseMode.HTML)
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=600,
            messages=[
                {"role": "system", "content": "Sei un esperto di mercati azionari, finanza e trading. Rispondi sempre in italiano, chiaro e pratico. Usa emoji. Ricorda che le tue risposte non sono consigli finanziari ufficiali."},
                {"role": "user", "content": question},
            ],
        )
        answer = response.choices[0].message.content
        await msg.edit_text(f"🤖 <b>Risposta AI:</b>\n\n{answer}", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Errore AI: {e}")
        await msg.edit_text("❌ Errore AI. Controlla che <b>GROQ_API_KEY</b> sia su Railway.", parse_mode=ParseMode.HTML)


# ─── Callback handler (bottoni) ──────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = str(update.effective_user.id)

    # Analisi singola azione
    if data.startswith("apr:"):
        ticker = data.split(":")[1]
        await query.message.reply_text(f"⏳ Analizzo <b>{ticker}</b>...", parse_mode=ParseMode.HTML)
        d = get_full_analysis(ticker)
        if not d:
            await query.message.reply_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
            return
        keyboard = kb([
            [btn(f"🎯 Trading {ticker}", f"trading:{ticker}"), btn(f"💼 Aggiungi", f"add_portfolio:{ticker}")],
        ])
        await query.message.reply_text(format_analysis_message(d), parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Trading
    elif data.startswith("trading:"):
        ticker = data.split(":")[1]
        await query.message.reply_text(f"⏳ Trading analysis <b>{ticker}</b>...", parse_mode=ParseMode.HTML)
        d = get_trading_analysis(ticker)
        if not d:
            await query.message.reply_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
            return
        keyboard = kb([[btn(f"📈 Analisi {ticker}", f"apr:{ticker}"), btn(f"💼 Aggiungi", f"add_portfolio:{ticker}")]])
        await query.message.reply_text(format_trading_message(d), parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Aggiungi al portafoglio
    elif data.startswith("add_portfolio:"):
        ticker = data.split(":")[1]
        user_data = load_data()
        watchlist = user_data["watchlists"].setdefault(uid, DEFAULT_WATCHLIST.copy())
        if ticker in watchlist:
            await query.message.reply_text(f"<b>{ticker}</b> è già nel portafoglio.", parse_mode=ParseMode.HTML)
        else:
            watchlist.append(ticker)
            save_data(user_data)
            await query.message.reply_text(f"✅ <b>{ticker}</b> aggiunto al portafoglio!", parse_mode=ParseMode.HTML)

    # Aggiorna scanner
    elif data == "refresh_scan":
        msg = await query.message.reply_text(
            "🔍 <b>Aggiorno scanner...</b>\n<i>Circa 30 secondi</i>", parse_mode=ParseMode.HTML
        )
        risultati = await asyncio.to_thread(scan_cheap_stocks, 20.0, 10)
        if not risultati:
            await msg.edit_text("❌ Nessun dato. Riprova.")
            return
        lines = ["📊 <b>Top 10 azioni sotto $20 — aggiornato</b>\n"]
        for i, d in enumerate(risultati):
            chg = d["day_change_pct"]
            sign = "+" if chg >= 0 else ""
            emoji = "📈" if chg >= 0 else "📉"
            vol_str = f"Vol {d['vol_ratio']:.1f}x" if d["vol_ratio"] > 1.2 else ""
            lines.append(f"{i+1}. <b>{d['ticker']}</b> ${d['current_price']:.2f} {emoji}{sign}{chg:.1f}%  {d['risk_emoji']}  ⭐{d['score_10']}/10\n   RSI {d['rsi']:.0f} {vol_str}")
        top5 = risultati[:5]
        rows = []
        for i in range(0, len(top5), 2):
            row = [btn(f"📈 {top5[i]['ticker']}", f"apr:{top5[i]['ticker']}")]
            if i + 1 < len(top5):
                row.append(btn(f"📈 {top5[i+1]['ticker']}", f"apr:{top5[i+1]['ticker']}"))
            rows.append(row)
        rows.append([btn("🔄 Aggiorna scanner", "refresh_scan")])
        await msg.edit_text("\n\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb(rows))

    # Help contestuale
    elif data == "help":
        keyboard = kb([
            [btn("📊 Analisi mercato", "help_analisi"), btn("📈 Analisi azione", "help_apr")],
            [btn("🎯 Day Trading", "help_trading"), btn("⚔️ Confronto", "help_confronto")],
            [btn("💼 Portafoglio", "help_portafoglio"), btn("👁 Watchlist", "help_watchlist")],
            [btn("🤖 Chiedi all'AI", "help_chiediai")],
        ])
        await query.message.reply_text(
            "📖 <b>Comandi disponibili:</b>\n\n"
            "📊 /analisi · 📈 /apr · 🎯 /trading\n"
            "⚔️ /confronto · 💼 /portafoglio\n"
            "👁 /watchlist · 🤖 /chiediai",
            parse_mode=ParseMode.HTML, reply_markup=keyboard,
        )
    elif data == "help_analisi":
        await query.message.reply_text("📊 <b>/analisi</b>\n\nScansiona ~250 azioni Revolut sotto i $20 e mostra le top 10 migliori in questo momento.\n\nScrivi solo: /analisi", parse_mode=ParseMode.HTML)
    elif data == "help_apr":
        await query.message.reply_text("📈 <b>/apr TICKER</b>\n\nAnalisi completa: prezzo, rendimento, rischio, RSI, medie mobili, notizie e valutazione.\n\nEsempio: /apr PLTR", parse_mode=ParseMode.HTML)
    elif data == "help_trading":
        await query.message.reply_text("🎯 <b>/trading TICKER</b>\n\nDay trading: segnale COMPRA/VENDI/ATTENDI, stop loss, target, MACD, Bollinger Bands.\n\nEsempio: /trading NVDA", parse_mode=ParseMode.HTML)
    elif data == "help_confronto":
        await query.message.reply_text("⚔️ <b>/confronto TICKER1 TICKER2</b>\n\nConfronta due azioni su 5 criteri + valutazione AI finale.\n\nEsempio: /confronto PLTR NIO", parse_mode=ParseMode.HTML)
    elif data == "help_portafoglio":
        await query.message.reply_text("💼 <b>/portafoglio</b>\n\nVedi le tue azioni. Il report mattutino e il recap serale usano questa lista.\n\n/aggiungi TICKER — aggiungi\n/rimuovi TICKER — rimuovi", parse_mode=ParseMode.HTML)
    elif data == "help_watchlist":
        await query.message.reply_text("👁 <b>/watchlist</b>\n\nLista separata di azioni da tenere d'occhio (non usata nel report automatico).\n\n/wadd TICKER — aggiungi\n/wdel TICKER — rimuovi", parse_mode=ParseMode.HTML)
    elif data == "help_chiediai":
        await query.message.reply_text("🤖 <b>/chiediai [domanda]</b>\n\nFai qualsiasi domanda sul mercato azionario all'AI.\n\nEsempio: /chiediai l'oro è un buon investimento adesso?", parse_mode=ParseMode.HTML)


# ─── Job mattutino (7:30) ────────────────────────────────────────────────────

_GIORNI_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MESI_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


async def job_daily_report(context: ContextTypes.DEFAULT_TYPE):
    from datetime import datetime as _dt
    now = _dt.now(ROME)
    date_str = f"{_GIORNI_IT[now.weekday()]} {now.day} {_MESI_IT[now.month - 1]} {now.year}"

    data = load_data()
    all_uids = [uid for uid, wl in data["watchlists"].items()]
    if not all_uids:
        return

    # 1 ── Scanner top 10
    logger.info("Report mattutino: scansione in corso...")
    risultati = await asyncio.to_thread(scan_cheap_stocks, 20.0, 10)
    if not risultati:
        for uid in all_uids:
            try:
                await context.bot.send_message(int(uid),
                    "⚠️ Scanner non disponibile stamattina. Usa /analisi per riprovare.",
                    parse_mode=ParseMode.HTML)
            except Exception:
                pass
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
        return

    # 3 ── Generazione AI verdict concorrente
    logger.info(f"Report mattutino: AI per {len(enriched)} azioni...")
    ai_raw = await asyncio.gather(*[generate_ai_verdict(d) for d in enriched], return_exceptions=True)
    ai_verdicts = [
        r if not isinstance(r, Exception) else {"verdict": "", "bullet1": "", "bullet2": ""}
        for r in ai_raw
    ]

    # 4 ── Costruzione messaggi (5 azioni per messaggio)
    total = len(enriched)
    chunks = [enriched[i:i + 5] for i in range(0, total, 5)]
    ai_chunks = [ai_verdicts[i:i + 5] for i in range(0, total, 5)]
    num_parts = len(chunks)
    SEP = "\n\n" + "━" * 20 + "\n\n"

    # 5 ── Invio a tutti gli utenti registrati
    for uid in all_uids:
        try:
            await context.bot.send_message(
                int(uid),
                f"📊 <b>Analisi automatica — {date_str}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"Errore header {uid}: {e}")
            continue

        base_rank = 1
        for ci, (chunk, ai_chunk) in enumerate(zip(chunks, ai_chunks)):
            part_label = f"({ci + 1}/{num_parts})"
            cards = [
                format_morning_card(d, ai, base_rank + i)
                for i, (d, ai) in enumerate(zip(chunk, ai_chunk))
            ]
            base_rank += len(chunk)

            body = SEP.join(cards)
            full_msg = (
                f"🔝 <b>Top {total} azioni sotto $20 {part_label}</b>\n\n"
                + body
                + "\n\n<i>Dati: Yahoo Finance | Analisi: AI</i>"
            )
            try:
                await context.bot.send_message(int(uid), full_msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"Errore chunk {ci} per {uid}: {e}")

        try:
            await context.bot.send_message(
                int(uid),
                "✅ <b>Report completato!</b>\n<i>Ci rivediamo alle 22:00 con il recap serale.</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.warning(f"Errore chiusura {uid}: {e}")


# ─── Job serale (22:00) ──────────────────────────────────────────────────────

async def job_evening_report(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    all_uids = list(data["watchlists"].keys())
    if not all_uids:
        return

    # Usa lo scanner per il recap serale (stesso metodo del mattino)
    scanner_raw = await asyncio.to_thread(scan_cheap_stocks, 20.0, 10)
    if not scanner_raw:
        return

    risultati = [d for r in scanner_raw if (d := get_full_analysis(r["ticker"]))]
    if not risultati:
        return

    risultati.sort(key=lambda x: x["day_change_pct"], reverse=True)
    migliore, peggiore = risultati[0], risultati[-1]

    lines = ["🌙 <b>Recap serale — chiusura mercato USA</b>\n"]
    for d in risultati:
        lines.append(format_report_line(d))
    lines += [
        "",
        f"🏆 Migliore oggi: <b>{migliore['ticker']}</b> {migliore['day_change_pct']:+.1f}%",
        f"💔 Peggiore oggi: <b>{peggiore['ticker']}</b> {peggiore['day_change_pct']:+.1f}%",
        "\n<i>Domani mattina alle 7:30 trovi la nuova classifica aggiornata.</i>",
    ]
    text = "\n".join(lines)

    for uid in all_uids:
        try:
            await context.bot.send_message(int(uid), text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Errore recap serale {uid}: {e}")


# ─── Job settimanale (Sabato 10:00) ─────────────────────────────────────────

async def job_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    all_uids = list(data["watchlists"].keys())
    if not all_uids:
        return

    # Usa lo scanner per il recap settimanale
    scanner_raw = await asyncio.to_thread(scan_cheap_stocks, 20.0, 10)
    if not scanner_raw:
        return

    risultati = [d for r in scanner_raw if (d := get_full_analysis(r["ticker"]))]
    if not risultati:
        return

    risultati.sort(key=lambda x: (x.get("week_return") or 0), reverse=True)
    migliore = risultati[0]
    peggiore = risultati[-1]

    lines = ["📅 <b>Recap settimanale — come è andata questa settimana</b>\n"]
    for d in risultati:
        wr = d.get("week_return")
        if wr is not None:
            sign = "+" if wr >= 0 else ""
            emoji = "📈" if wr >= 0 else "📉"
            lines.append(f"{emoji} <b>{d['ticker']}</b>  settimana: {sign}{wr:.1f}%  {d['risk_emoji']}")
        else:
            lines.append(f"❓ <b>{d['ticker']}</b>  dati insufficienti")

    wr_best = migliore.get("week_return") or 0
    wr_worst = peggiore.get("week_return") or 0
    lines += [
        "",
        f"🏆 Migliore settimana: <b>{migliore['ticker']}</b> {'+' if wr_best >= 0 else ''}{wr_best:.1f}%",
        f"💔 Peggiore settimana: <b>{peggiore['ticker']}</b> {'+' if wr_worst >= 0 else ''}{wr_worst:.1f}%",
        "\n<i>Buon weekend! Lunedì mattina alle 7:30 ricomincia l'analisi.</i>",
    ]
    text = "\n".join(lines)

    for uid in all_uids:
        try:
            await context.bot.send_message(int(uid), text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Errore recap settimanale {uid}: {e}")


# ─── Avvio ───────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("ERRORE: BOT_TOKEN mancante")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("analisi", cmd_analisi))
    app.add_handler(CommandHandler("apr", cmd_apr))
    app.add_handler(CommandHandler("trading", cmd_trading))
    app.add_handler(CommandHandler("confronto", cmd_confronto))
    app.add_handler(CommandHandler("portafoglio", cmd_portafoglio))
    app.add_handler(CommandHandler("aggiungi", cmd_aggiungi))
    app.add_handler(CommandHandler("rimuovi", cmd_rimuovi))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("wadd", cmd_wadd))
    app.add_handler(CommandHandler("wdel", cmd_wdel))
    app.add_handler(CommandHandler("chiediai", cmd_chiediai))
    app.add_handler(CallbackQueryHandler(handle_callback))

    jq = app.job_queue
    jq.run_daily(job_daily_report, time=time(hour=7, minute=30, tzinfo=ROME))
    jq.run_daily(job_evening_report, time=time(hour=22, minute=0, tzinfo=ROME))
    jq.run_daily(
        job_weekly_report,
        time=time(hour=10, minute=0, tzinfo=ROME),
        days=(5,),  # 5 = sabato
    )

    logger.info("Bot avviato!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
