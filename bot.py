# Stock Bot v2.1 — professional AI edition
import asyncio
import json
import logging
import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from groq import AsyncGroq
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram import InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from analyzer import (
    get_full_analysis,
    get_trading_analysis,
    get_enriched_analysis,
    scan_cheap_stocks,
    format_analysis_message,
    format_trading_message,
    format_confronto_message,
    format_morning_card,
    format_scan_card,
    format_report_line,
    format_apr_card,
)
from config import BOT_TOKEN, DEFAULT_WATCHLIST, REPORT_HOUR, REPORT_MINUTE, GROUP_CHAT_ID, TOPIC_ANALISI_ID, TOPIC_NOTIZIE_ID, TOPIC_GRAFICO_ID

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

ROME = ZoneInfo("Europe/Rome")
DATA_FILE      = Path(__file__).parent / "user_data.json"
HISTORY_FILE   = Path(__file__).parent / "analysis_history.json"

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


# ─── Livelli utente ──────────────────────────────────────────────────────────

def get_user_level(data: dict, uid: str) -> str:
    return data.get("levels", {}).get(str(uid), "intermedio")

def set_user_level(data: dict, uid: str, level: str):
    data.setdefault("levels", {})[str(uid)] = level


# ─── Prompt analisi grafico per livello ──────────────────────────────────────

_GRAFICO_PROMPT = {
    "dilettante": (
        "Sei un esperto di trading che aiuta un principiante. Analizza questo grafico e rispondi in italiano "
        "in modo MOLTO semplice. Usa questo formato:\n\n"
        "📍 SITUAZIONE: [una frase: il prezzo sta salendo/scendendo/laterale e perché]\n"
        "🛑 STOP LOSS: [valore preciso] — [spiegazione semplicissima in 1 riga]\n"
        "🎯 TAKE PROFIT: [valore preciso] — [spiegazione semplice in 1 riga]\n"
        "💡 CONSIGLIO: COMPRA / VENDI / ASPETTA\n"
        "[2 righe max, spiega come a un bambino]\n\n"
        "Usa emoji, sii incoraggiante, zero gergo tecnico."
    ),
    "intermedio": (
        "Sei un analista tecnico. Analizza questo grafico in italiano con questo formato:\n\n"
        "📈 TREND: [direzione e forza]\n"
        "🔑 LIVELLI CHIAVE: Supporto $X.XX | Resistenza $X.XX\n"
        "🛑 STOP LOSS: $X.XX [-X%] | Invalidazione: [motivazione tecnica]\n"
        "🎯 TP1: $X.XX [+X%] — R/R 1:X\n"
        "🎯 TP2: $X.XX [+X%] — R/R 1:X\n"
        "📊 SEGNALI: [RSI visivo, volume, pattern rilevati]\n"
        "⚖️ BIAS: Rialzista / Ribassista / Neutro\n"
        "🎯 ENTRY IDEALE: $X.XX\n\n"
        "Sii preciso e conciso."
    ),
    "esperto": (
        "Sei un analista quantitativo senior. Analisi tecnica professionale del grafico in italiano:\n\n"
        "🏗️ STRUTTURA: [macro trend, micro price action, key levels]\n"
        "📐 PATTERN: [pattern tecnici: H&S, wedge, flag, double top/bottom, triangoli, ecc.]\n"
        "🔑 LIVELLI: [supporti/resistenze statici, dinamici, pivot points]\n"
        "📏 FIBONACCI: [livelli di ritracciamento/estensione se visibili]\n"
        "📦 VOLUME: [analisi volume se presente nel grafico]\n"
        "🛑 STOP LOSS: $X.XX | Invalidazione tecnica: [dettaglio]\n"
        "🎯 TP1: $X.XX — R/R 1:X\n"
        "🎯 TP2: $X.XX — R/R 1:X\n"
        "🎯 TP3 (esteso): $X.XX — R/R 1:X\n"
        "📍 ENTRY: $X.XX [limite/mercato, motivazione]\n"
        "🧠 TESI: [tesi di trading in 2 righe]\n"
        "⚠️ RISCHIO: max 1-2% capitale per operazione\n\n"
        "Dati precisi, linguaggio da trader professionista."
    ),
}

_LEVEL_LABEL = {"dilettante": "🟢 Base", "intermedio": "🟡 Pro", "esperto": "🔴 Expert"}


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


async def generate_ai_resoconto(d: dict) -> str:
    """Resoconto AI dettagliato: motivazioni, rischi, prospettive."""
    if not groq_client:
        return "❌ AI non disponibile — aggiungi <b>GROQ_API_KEY</b> nelle Variables di Railway."

    pe = d.get("pe_ratio")
    pe_str = f"{pe:.1f}x" if pe and pe > 0 else "N/D"
    upside = d.get("upside_52w", 0.0)
    upside_str = f"+{upside:.1f}%" if upside >= 0 else f"{upside:.1f}% (già ai massimi)"

    prompt = (
        f"Analisi approfondita dell'azione {d['ticker']} ({d['name']}):\n"
        f"- Prezzo: ${d['current_price']:.2f}, oggi {d['day_change_pct']:+.1f}%\n"
        f"- RSI: {d['rsi']:.0f}, Volatilità: {d['volatility']:.0f}%, Rischio: {d['risk_level']}\n"
        f"- Settimana: {(d.get('week_return') or 0):+.1f}%, Mese: {(d.get('month_return') or 0):+.1f}%, Anno: {(d.get('ytd_return') or 0):+.1f}%\n"
        f"- Upside verso max 52w: {upside_str}\n"
        f"- P/E: {pe_str} | Settore: {d.get('sector','N/D')}\n"
        f"- Notizie: {d.get('news_sentiment_label','Neutre')}\n"
        f"- Earnings oggi: {'Sì ⚠️' if d.get('earnings_today') else 'No'}\n"
        f"- Prossimi earnings: {d.get('next_earnings_str','N/D')}\n\n"
        "Scrivi un resoconto professionale in italiano con queste 3 sezioni ESATTE:\n\n"
        "PERCHE_SI: [2-3 motivi concreti per cui potrebbe andare bene, con dati]\n"
        "RISCHI: [2-3 rischi reali e specifici da considerare]\n"
        "CONCLUSIONE: [valutazione finale in 1-2 frasi, sii diretto]"
    )
    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=450,
            messages=[
                {"role": "system", "content": "Sei un analista finanziario senior. Rispondi sempre in italiano, con dati concreti. Non dare mai consigli finanziari definitivi."},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()

        # Parsing sezioni
        perche_si = rischi = conclusione = ""
        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("PERCHE_SI:"):
                perche_si = line[10:].strip()
            elif line.upper().startswith("RISCHI:"):
                rischi = line[7:].strip()
            elif line.upper().startswith("CONCLUSIONE:"):
                conclusione = line[12:].strip()

        # Fallback: se il modello non rispetta il formato, usa il testo grezzo
        if not (perche_si or rischi or conclusione):
            return f"🤖 <b>Resoconto AI — {d['ticker']}</b>\n\n{text}"

        result = [f"🤖 <b>Resoconto AI — {d['ticker']} ({d['name']})</b>\n"]
        if perche_si:
            result += ["✅ <b>Perché potrebbe andare bene:</b>", perche_si, ""]
        if rischi:
            result += ["⚠️ <b>Rischi da considerare:</b>", rischi, ""]
        if conclusione:
            result += ["🎯 <b>Conclusione:</b>", conclusione]
        result.append("\n<i>Non è un consiglio finanziario. Investi con cautela.</i>")
        return "\n".join(result)

    except Exception as e:
        logger.error(f"AI resoconto {d['ticker']}: {e}")
        return "❌ Errore AI. Riprova tra qualche secondo."


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
        data["watchlists"][uid] = {}
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
        [btn("💼 Portafoglio", "help_portafoglio"), btn("🤖 Chiedi all'AI", "help_chiediai")],
    ])
    await update.message.reply_text(
        "📖 <b>Comandi disponibili:</b>\n\n"
        "📊 /analisi — top 10 azioni sotto €35 (~$40) adesso\n"
        "📈 /apr — analisi completa di una azione\n"
        "🎯 /trading — day trading: compra e vendi subito\n"
        "⚔️ /confronto — confronta due azioni con AI\n"
        "💼 /portafoglio — il tuo portafoglio con P&amp;L\n"
        "🤖 /chiediai — chiedi all'AI\n"
        "📸 <b>Foto grafico → analisi SL/TP</b> — invia una foto di un grafico nel topic Grafico\n"
        "🎚 /livello — imposta il tuo livello (Base / Pro / Expert)\n\n"
        "<i>Clicca un bottone per sapere come si usa</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ─── /analisi — scanner mercato con card professionali ───────────────────────

async def cmd_analisi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔍 <b>Scansiono ~400 azioni Revolut sotto €35...</b>\n<i>Con notizie + AI — circa 60 secondi</i>",
        parse_mode=ParseMode.HTML,
    )
    risultati = await asyncio.to_thread(scan_cheap_stocks, 40.0, 10)

    if not risultati:
        await msg.edit_text("❌ Nessun dato disponibile. Riprova tra qualche minuto.")
        return

    await msg.edit_text(
        f"✅ <b>Trovate {len(risultati)} azioni — genero analisi AI...</b>\n<i>Ancora qualche secondo</i>",
        parse_mode=ParseMode.HTML,
    )

    # Arricchimento dati per ogni azione
    enriched = []
    for r in risultati:
        d = await asyncio.to_thread(get_enriched_analysis, r["ticker"])
        if d:
            d["score"] = r.get("score", 0)
            d["score_10"] = r.get("score_10", 5.0)
            d["vol_ratio"] = r.get("vol_ratio", 1.0)
            enriched.append(d)

    if not enriched:
        await msg.edit_text("❌ Errore nel recuperare i dati. Riprova tra qualche minuto.")
        return

    # AI verdicts concorrenti
    ai_raw = await asyncio.gather(
        *[generate_ai_verdict(d) for d in enriched], return_exceptions=True
    )
    ai_verdicts = [
        r if not isinstance(r, Exception) else {"verdict": "", "bullet1": "", "bullet2": ""}
        for r in ai_raw
    ]

    # Tutte le card in un solo messaggio
    total = len(enriched)
    SEP = "\n" + "─" * 22 + "\n"
    cards = [
        format_scan_card(d, ai, i + 1)
        for i, (d, ai) in enumerate(zip(enriched, ai_verdicts))
    ]
    body = SEP.join(cards)
    full_msg = (
        f"📊 <b>Top {total} azioni sotto €35 (~$40) — adesso</b>\n\n"
        + body
        + "\n\n<i>Dati: Yahoo Finance | AI: Groq Llama 70B</i>"
    )

    # Bottoni per analisi dettagliata
    rows = []
    for i in range(0, len(enriched), 3):
        rows.append([btn(f"📈 {enriched[j]['ticker']}", f"apr:{enriched[j]['ticker']}") for j in range(i, min(i+3, len(enriched)))])

    await msg.edit_text(full_msg, parse_mode=ParseMode.HTML, reply_markup=kb(rows))


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
    msg = await update.message.reply_text(
        f"⏳ <b>Analizzo {ticker}...</b>\n<i>Recupero dati + notizie + AI verdict</i>",
        parse_mode=ParseMode.HTML,
    )
    data = await asyncio.to_thread(get_enriched_analysis, ticker)
    if not data:
        await msg.edit_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
        return
    ai = await generate_ai_verdict(data)
    keyboard = kb([
        [btn(f"🤖 Resoconto AI", f"resoconto:{ticker}"), btn(f"⚔️ Confronta con...", f"help_confronto")],
    ])
    await msg.edit_text(format_apr_card(data, ai), parse_mode=ParseMode.HTML, reply_markup=keyboard)


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
    data = await asyncio.to_thread(get_trading_analysis, ticker)
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
    d1, d2 = await asyncio.gather(
        asyncio.to_thread(get_full_analysis, t1),
        asyncio.to_thread(get_full_analysis, t2),
    )
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


# ─── Portfolio helpers ───────────────────────────────────────────────────────

def _port_get(data: dict, uid: str) -> dict:
    """Restituisce il portafoglio come dict {TICKER: {price, qty}}.
    Compatibile con il vecchio formato lista."""
    raw = data["watchlists"].get(uid, {})
    if isinstance(raw, list):
        # migrazione dal vecchio formato lista
        migrated = {t: {"price": 0.0, "qty": 0.0} for t in raw}
        data["watchlists"][uid] = migrated
        save_data(data)
        return migrated
    return raw


def _port_tickers(data: dict, uid: str) -> list:
    return list(_port_get(data, uid).keys())


# ─── /portafoglio ────────────────────────────────────────────────────────────

async def cmd_portafoglio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = load_data()
    args = context.args or []

    # ── Subcomando: aggiungi ──────────────────────────────────────────────────
    if args and args[0].lower() == "aggiungi":
        if len(args) < 4:
            await update.message.reply_text(
                "📝 <b>Uso:</b> /portafoglio aggiungi TICKER PREZZO QUANTITÀ\n"
                "<b>Esempio:</b> /portafoglio aggiungi AAPL 172.50 10",
                parse_mode=ParseMode.HTML,
            )
            return
        ticker = args[1].upper()
        try:
            price = float(args[2].replace(",", "."))
            qty = float(args[3].replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Prezzo e quantità devono essere numeri.\nEsempio: /portafoglio aggiungi AAPL 172.50 10", parse_mode=ParseMode.HTML)
            return
        port = _port_get(data, uid)
        port[ticker] = {"price": price, "qty": qty}
        data["watchlists"][uid] = port
        save_data(data)
        invested = price * qty
        keyboard = kb([[btn(f"📈 Analizza {ticker}", f"apr:{ticker}"), btn(f"🎯 Trading {ticker}", f"trading:{ticker}")]])
        await update.message.reply_text(
            f"✅ <b>{ticker}</b> aggiunto al portafoglio!\n"
            f"📦 {qty:g} azioni @ ${price:.2f} = <b>${invested:,.2f}</b> investiti",
            parse_mode=ParseMode.HTML, reply_markup=keyboard,
        )
        return

    # ── Subcomando: rimuovi ───────────────────────────────────────────────────
    if args and args[0].lower() == "rimuovi":
        if len(args) < 2:
            await update.message.reply_text("Usa: /portafoglio rimuovi TICKER", parse_mode=ParseMode.HTML)
            return
        ticker = args[1].upper()
        port = _port_get(data, uid)
        if ticker not in port:
            await update.message.reply_text(f"❌ <b>{ticker}</b> non è nel portafoglio.", parse_mode=ParseMode.HTML)
            return
        del port[ticker]
        data["watchlists"][uid] = port
        save_data(data)
        await update.message.reply_text(f"✅ <b>{ticker}</b> rimosso dal portafoglio.", parse_mode=ParseMode.HTML)
        return

    # ── Vista portafoglio ─────────────────────────────────────────────────────
    port = _port_get(data, uid)
    if not port:
        await update.message.reply_text(
            "💼 <b>Portafoglio vuoto.</b>\n\n"
            "Aggiungi la prima azione:\n"
            "<b>/portafoglio aggiungi TICKER PREZZO QUANTITÀ</b>\n"
            "Esempio: /portafoglio aggiungi AAPL 172.50 10",
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.message.reply_text("⏳ Aggiorno prezzi portafoglio...", parse_mode=ParseMode.HTML)

    tickers = list(port.keys())
    lines = ["💼 <b>Il tuo portafoglio</b>\n"]
    total_invested = 0.0
    total_current = 0.0

    import yfinance as _yf_port

    def _fetch_fast_info(t):
        try:
            return _yf_port.Ticker(t).fast_info
        except Exception:
            return None

    for t in tickers:
        entry = port[t]
        buy_price = entry.get("price", 0.0)
        qty = entry.get("qty", 0.0)
        try:
            fast = await asyncio.to_thread(_fetch_fast_info, t)
            if fast is None:
                raise ValueError("no data")
            cur = float(fast.last_price or 0)
            chg = ((cur - float(fast.previous_close or cur)) / float(fast.previous_close or cur)) * 100 if fast.previous_close else 0.0
        except Exception:
            cur = buy_price
            chg = 0.0

        invested = buy_price * qty
        current_val = cur * qty
        pl = current_val - invested
        pl_pct = (pl / invested * 100) if invested > 0 else 0.0
        total_invested += invested
        total_current += current_val

        chg_str = f"{'+'if chg>=0 else''}{chg:.1f}%"
        chg_emoji = "📈" if chg >= 0 else "📉"
        pl_emoji = "🟢" if pl >= 0 else "🔴"
        pl_sign = "+" if pl >= 0 else ""

        lines.append(
            f"<b>{t}</b> {chg_emoji} ${cur:.2f} ({chg_str} oggi)\n"
            f"  📦 {qty:g} az. @ ${buy_price:.2f} → <b>${current_val:,.2f}</b>\n"
            f"  {pl_emoji} P&L: {pl_sign}${pl:,.2f} ({pl_sign}{pl_pct:.1f}%)"
        )

    # Totale
    total_pl = total_current - total_invested
    total_pl_pct = (total_pl / total_invested * 100) if total_invested > 0 else 0.0
    total_emoji = "🟢" if total_pl >= 0 else "🔴"
    total_sign = "+" if total_pl >= 0 else ""
    lines += [
        "",
        "━━━━━━━━━━━━━━━",
        f"💰 Investito: <b>${total_invested:,.2f}</b>",
        f"📊 Valore attuale: <b>${total_current:,.2f}</b>",
        f"{total_emoji} P&L totale: <b>{total_sign}${total_pl:,.2f} ({total_sign}{total_pl_pct:.1f}%)</b>",
        "",
        "<i>/portafoglio aggiungi TICKER PREZZO QTÀ\n/portafoglio rimuovi TICKER</i>",
    ]

    rows = []
    for i in range(0, len(tickers), 3):
        rows.append([btn(f"📈 {t}", f"apr:{t}") for t in tickers[i:i+3]])

    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb(rows) if rows else None)


async def cmd_aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shortcut per /portafoglio aggiungi."""
    await update.message.reply_text(
        "📝 Usa il comando completo:\n<b>/portafoglio aggiungi TICKER PREZZO QUANTITÀ</b>\n\nEsempio: /portafoglio aggiungi AAPL 172.50 10",
        parse_mode=ParseMode.HTML,
    )


async def cmd_rimuovi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shortcut per /portafoglio rimuovi."""
    if not context.args:
        await update.message.reply_text("Usa: /portafoglio rimuovi TICKER", parse_mode=ParseMode.HTML)
        return
    ticker = context.args[0].upper()
    uid = str(update.effective_user.id)
    data = load_data()
    port = _port_get(data, uid)
    if ticker not in port:
        await update.message.reply_text(f"❌ <b>{ticker}</b> non è nel portafoglio.", parse_mode=ParseMode.HTML)
        return
    del port[ticker]
    data["watchlists"][uid] = port
    save_data(data)
    await update.message.reply_text(f"✅ <b>{ticker}</b> rimosso.", parse_mode=ParseMode.HTML)


# ─── /valuta ─────────────────────────────────────────────────────────────────

async def cmd_valuta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Valutazione AI del portafoglio: panoramica, rischi, consigli, verdict."""
    uid = str(update.effective_user.id)
    data = load_data()
    port = _port_get(data, uid)

    if not port:
        await update.message.reply_text(
            "💼 Portafoglio vuoto.\n\nAggiungi azioni con:\n<code>/portafoglio aggiungi TICKER PREZZO QUANTITÀ</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not groq_client:
        await update.message.reply_text("❌ AI non disponibile (GROQ_API_KEY mancante).", parse_mode=ParseMode.HTML)
        return

    msg = await update.message.reply_text("🤖 <b>Analisi AI portafoglio in corso…</b>", parse_mode=ParseMode.HTML)

    # Fetch prezzi correnti
    import yfinance as _yf_v
    def _get_price(t):
        try:
            fi = _yf_v.Ticker(t).fast_info
            return float(fi.last_price or 0), float(fi.previous_close or 0)
        except Exception:
            return 0.0, 0.0

    prices = {}
    for t in port:
        cur, prev = await asyncio.to_thread(_get_price, t)
        prices[t] = {"cur": cur, "prev": prev}

    # Calcola P&L
    total_inv = 0.0; total_val = 0.0
    position_lines = []
    for t, entry in port.items():
        buy_p = entry.get("price", 0.0)
        qty = entry.get("qty", 0.0)
        cur = prices[t]["cur"]
        inv = buy_p * qty
        val = cur * qty if cur > 0 else inv
        pl = val - inv
        pl_pct = (pl / inv * 100) if inv > 0 else 0.0
        total_inv += inv; total_val += val
        sign = "+" if pl >= 0 else ""
        emoji = "📈" if pl >= 0 else "📉"
        position_lines.append(
            f"{emoji} {t}: {qty:.0f} az. | acquisto ${buy_p:.2f} → ora ${cur:.2f} | P&L: {sign}${pl:.2f} ({sign}{pl_pct:.1f}%)"
        )

    total_pl = total_val - total_inv
    total_pl_pct = (total_pl / total_inv * 100) if total_inv > 0 else 0.0
    total_sign = "+" if total_pl >= 0 else ""

    portfolio_summary = "\n".join(position_lines)
    portfolio_summary += f"\n\nTOTALE — Investito: ${total_inv:.2f} | Valore: ${total_val:.2f} | P&L: {total_sign}${total_pl:.2f} ({total_sign}{total_pl_pct:.1f}%)"

    prompt = (
        f"Portafoglio dell'investitore:\n{portfolio_summary}\n\n"
        f"Analizza questo portafoglio e rispondi SOLO in questo formato (italiano, conciso, max 2 righe per sezione):\n"
        "PANORAMICA: [valuta diversificazione, concentrazione e qualità generale del portafoglio]\n"
        "RISCHI: [2-3 rischi specifici e concreti di questo portafoglio]\n"
        "CONSIGLI: [2-3 azioni concrete: cosa vendere, tenere, ribilanciare o aggiungere]\n"
        "VERDICT: POSITIVO oppure NEUTRO oppure ATTENZIONE"
    )

    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=400,
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

        if not verdict:
            verdict = "NEUTRO"
        if verdict not in ("POSITIVO", "NEUTRO", "ATTENZIONE"):
            verdict = "NEUTRO"

        verdict_emoji = {"POSITIVO": "🟢", "NEUTRO": "🟡", "ATTENZIONE": "🔴"}.get(verdict, "🟡")

        # Riepilogo posizioni
        pos_recap = []
        for t, entry in port.items():
            buy_p = entry.get("price", 0.0); qty = entry.get("qty", 0.0)
            cur = prices[t]["cur"]
            pl = (cur - buy_p) * qty if cur > 0 else 0
            s = "+" if pl >= 0 else ""; e = "📈" if pl >= 0 else "📉"
            pos_recap.append(f"{e} <b>{t}</b>  ${cur:.2f}  {s}${pl:.2f}")

        result_msg = (
            f"🤖 <b>Valutazione AI Portafoglio</b>\n\n"
            + "\n".join(pos_recap)
            + f"\n\n💰 Totale: ${total_inv:.2f} → ${total_val:.2f}  |  P&L: <b>{total_sign}${total_pl:.2f} ({total_sign}{total_pl_pct:.1f}%)</b>\n\n"
            + (f"📊 <b>Panoramica:</b>\n{panoramica}\n\n" if panoramica else "")
            + (f"⚠️ <b>Rischi:</b>\n{rischi}\n\n" if rischi else "")
            + (f"💡 <b>Consigli:</b>\n{consigli}\n\n" if consigli else "")
            + f"{verdict_emoji} <b>Verdict: {verdict}</b>\n\n"
            + "<i>Solo a scopo informativo. Non è consulenza finanziaria.</i>"
        )
        await msg.edit_text(result_msg, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"cmd_valuta AI error: {e}")
        await msg.edit_text("❌ Errore nell'analisi AI. Riprova tra qualche secondo.", parse_mode=ParseMode.HTML)


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
        loading = await query.message.reply_text(
            f"⏳ <b>Analizzo {ticker}...</b>\n<i>Recupero dati + notizie + AI verdict</i>",
            parse_mode=ParseMode.HTML,
        )
        d = await asyncio.to_thread(get_enriched_analysis, ticker)
        if not d:
            await loading.edit_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
            return
        ai = await generate_ai_verdict(d)
        keyboard = kb([
            [btn(f"🤖 Resoconto AI", f"resoconto:{ticker}"), btn(f"⚔️ Confronta con...", f"help_confronto")],
        ])
        await loading.edit_text(format_apr_card(d, ai), parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Resoconto AI dettagliato
    elif data.startswith("resoconto:"):
        ticker = data.split(":")[1]
        loading = await query.message.reply_text(
            f"🤖 <b>Genero resoconto AI per {ticker}...</b>\n<i>Analisi motivazioni e rischi</i>",
            parse_mode=ParseMode.HTML,
        )
        d = await asyncio.to_thread(get_enriched_analysis, ticker)
        if not d:
            await loading.edit_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
            return
        resoconto = await generate_ai_resoconto(d)
        keyboard = kb([
            [btn(f"📈 Rianalisi {ticker}", f"apr:{ticker}"), btn(f"⚔️ Confronta con...", f"help_confronto")],
        ])
        await loading.edit_text(resoconto, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Trading
    elif data.startswith("trading:"):
        ticker = data.split(":")[1]
        await query.message.reply_text(f"⏳ Trading analysis <b>{ticker}</b>...", parse_mode=ParseMode.HTML)
        d = await asyncio.to_thread(get_trading_analysis, ticker)
        if not d:
            await query.message.reply_text(f"❌ Nessun dato per <b>{ticker}</b>.", parse_mode=ParseMode.HTML)
            return
        keyboard = kb([[btn(f"📈 Analisi {ticker}", f"apr:{ticker}"), btn(f"💼 Aggiungi", f"add_portfolio:{ticker}")]])
        await query.message.reply_text(format_trading_message(d), parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Aggiungi al portafoglio
    elif data.startswith("add_portfolio:"):
        ticker = data.split(":")[1]
        await query.message.reply_text(
            f"💼 Per aggiungere <b>{ticker}</b> al portafoglio digita:\n\n"
            f"<code>/portafoglio aggiungi {ticker} PREZZO QUANTITÀ</code>\n\n"
            f"<i>Esempio: /portafoglio aggiungi {ticker} 10.50 100</i>",
            parse_mode=ParseMode.HTML,
        )

    # Aggiorna scanner (non più usato ma gestito per sicurezza)
    elif data == "refresh_scan":
        await query.message.reply_text(
            "🔄 Usa /analisi per ottenere l'analisi aggiornata con AI.",
            parse_mode=ParseMode.HTML,
        )

    # Help contestuale
    elif data == "help":
        keyboard = kb([
            [btn("📊 Analisi mercato", "help_analisi"), btn("📈 Analisi azione", "help_apr")],
            [btn("🎯 Day Trading", "help_trading"), btn("⚔️ Confronto", "help_confronto")],
            [btn("💼 Portafoglio", "help_portafoglio"), btn("🤖 Chiedi all'AI", "help_chiediai")],
        ])
        await query.message.reply_text(
            "📖 <b>Comandi disponibili:</b>\n\n"
            "📊 /analisi · 📈 /apr · 🎯 /trading\n"
            "⚔️ /confronto · 💼 /portafoglio · 🤖 /chiediai",
            parse_mode=ParseMode.HTML, reply_markup=keyboard,
        )
    elif data == "help_analisi":
        await query.message.reply_text("📊 <b>/analisi</b>\n\nScansiona ~400 azioni Revolut sotto €35 (~$40) e mostra le top 10 migliori in questo momento.\n\nScrivi solo: /analisi", parse_mode=ParseMode.HTML)
    elif data == "help_apr":
        await query.message.reply_text("📈 <b>/apr TICKER</b>\n\nAnalisi completa: prezzo, rendimento, rischio, RSI, medie mobili, notizie e valutazione.\n\nEsempio: /apr PLTR", parse_mode=ParseMode.HTML)
    elif data == "help_trading":
        await query.message.reply_text("🎯 <b>/trading TICKER</b>\n\nDay trading: segnale COMPRA/VENDI/ATTENDI, stop loss, target, MACD, Bollinger Bands.\n\nEsempio: /trading NVDA", parse_mode=ParseMode.HTML)
    elif data == "help_confronto":
        await query.message.reply_text("⚔️ <b>/confronto TICKER1 TICKER2</b>\n\nConfronta due azioni su 5 criteri + valutazione AI finale.\n\nEsempio: /confronto PLTR NIO", parse_mode=ParseMode.HTML)
    elif data == "help_portafoglio":
        await query.message.reply_text(
            "💼 <b>/portafoglio</b>\n\nVedi le tue azioni con P&amp;L aggiornato.\n\n"
            "📌 <b>Aggiungi:</b>\n<code>/portafoglio aggiungi TICKER PREZZO QUANTITÀ</code>\n"
            "<i>Es: /portafoglio aggiungi PLTR 17.50 50</i>\n\n"
            "🗑 <b>Rimuovi:</b>\n<code>/portafoglio rimuovi TICKER</code>",
            parse_mode=ParseMode.HTML,
        )
    elif data == "help_chiediai":
        await query.message.reply_text("🤖 <b>/chiediai [domanda]</b>\n\nFai qualsiasi domanda sul mercato azionario all'AI.\n\nEsempio: /chiediai l'oro è un buon investimento adesso?", parse_mode=ParseMode.HTML)

    elif data.startswith("livello:"):
        chosen = data.split(":")[1]
        user_data = load_data()
        set_user_level(user_data, uid, chosen)
        save_data(user_data)
        await query.message.edit_text(
            f"✅ Livello impostato: {_LEVEL_LABEL[chosen]}\n\n"
            "Invia un grafico (foto) qui in privato o nel topic <b>Grafico</b> del gruppo "
            "per ricevere un'analisi con stop loss e take profit.",
            parse_mode=ParseMode.HTML
        )


# ─── Job mattutino (7:30) ────────────────────────────────────────────────────

_GIORNI_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
_MESI_IT = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


async def _send_to_group(bot, text: str, topic_id: int, **kwargs):
    """Invia un messaggio al topic del gruppo se configurato."""
    if GROUP_CHAT_ID and topic_id:
        try:
            await bot.send_message(
                GROUP_CHAT_ID, text,
                message_thread_id=topic_id,
                parse_mode=ParseMode.HTML,
                **kwargs,
            )
        except Exception as e:
            logger.warning(f"Errore invio topic {topic_id}: {e}")


def _save_history_snapshot(date_iso: str, enriched: list, ai_verdicts: list):
    """Salva snapshot analisi giornaliera su analysis_history.json (ultimi 60 gg)."""
    history_file = HISTORY_FILE
    try:
        history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else {}
    except Exception:
        history = {}

    history[date_iso] = {
        "date": date_iso,
        "generated_at": f"{date_iso}T07:30:00",
        "stocks": [
            {
                "ticker": d["ticker"],
                "name": d.get("name", d["ticker"]),
                "price_at_analysis": round(float(d.get("current_price", 0)), 4),
                "score_10": float(d.get("score_10", 5.0)),
                "risk_level": d.get("risk_level", "Medio"),
                "risk_emoji": d.get("risk_emoji", "🟡"),
                "day_change_pct": round(float(d.get("day_change_pct", 0)), 2),
                "rsi": round(float(d.get("rsi", 50)), 1),
                "verdict": (ai_verdicts[i] or {}).get("verdict", "") if i < len(ai_verdicts) else "",
            }
            for i, d in enumerate(enriched)
        ],
    }

    # Mantieni solo gli ultimi 60 giorni
    all_dates = sorted(history.keys(), reverse=True)
    history = {k: history[k] for k in all_dates[:60]}

    try:
        history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Storico salvato: {date_iso} ({len(enriched)} azioni)")
    except Exception as e:
        logger.error(f"Errore salvataggio storico: {e}")


async def job_daily_report(context: ContextTypes.DEFAULT_TYPE):
    from datetime import datetime as _dt
    now = _dt.now(ROME)

    # Solo lun–ven
    if now.weekday() >= 5:
        return

    date_str = f"{_GIORNI_IT[now.weekday()]} {now.day} {_MESI_IT[now.month - 1]} {now.year}"

    data = load_data()
    all_uids = [uid for uid in data["watchlists"].keys()]
    if not all_uids and not GROUP_CHAT_ID:
        return

    # 1 ── Scanner top 10
    logger.info("Report mattutino: scansione in corso...")
    risultati = await asyncio.to_thread(scan_cheap_stocks, 40.0, 10)
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

    # 3 ── AI verdicts concorrenti
    logger.info(f"Report mattutino: AI per {len(enriched)} azioni...")
    ai_raw = await asyncio.gather(*[generate_ai_verdict(d) for d in enriched], return_exceptions=True)
    ai_verdicts = [
        r if not isinstance(r, Exception) else {"verdict": "", "bullet1": "", "bullet2": ""}
        for r in ai_raw
    ]

    # 4 ── UN SOLO messaggio con format_scan_card (compatto)
    SEP = "\n" + "─" * 18 + "\n"
    cards = [
        format_scan_card(d, ai, i + 1)
        for i, (d, ai) in enumerate(zip(enriched, ai_verdicts))
    ]
    text = (
        f"📊 <b>Analisi mattutina — {date_str}</b>\n"
        f"<i>Top {len(enriched)} azioni sotto €35 (~$40)</i>\n\n"
        + SEP.join(cards)
        + "\n\n<i>Dati: Yahoo Finance | AI: Groq Llama 70B</i>"
    )

    # 5 ── Salva snapshot storico
    _save_history_snapshot(now.strftime("%Y-%m-%d"), enriched, ai_verdicts)

    # 6 ── Invia: UN messaggio al gruppo + UN messaggio per ogni DM
    await _send_to_group(context.bot, text, TOPIC_ANALISI_ID)
    for uid in all_uids:
        try:
            await context.bot.send_message(int(uid), text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Errore report mattutino {uid}: {e}")


# ─── Job serale (22:00) ──────────────────────────────────────────────────────

async def job_evening_report(context: ContextTypes.DEFAULT_TYPE):
    from datetime import datetime as _dt
    now = _dt.now(ROME)
    # Solo lun–ven
    if now.weekday() >= 5:
        return

    today = now.strftime("%Y-%m-%d")
    date_str = f"{_GIORNI_IT[now.weekday()]} {now.day} {_MESI_IT[now.month - 1]} {now.year}"

    data = load_data()
    all_uids = list(data["watchlists"].keys())
    if not all_uids and not GROUP_CHAT_ID:
        return

    # ── 1. Carica snapshot mattutino di oggi ────────────────────────────────
    history_file = HISTORY_FILE
    history = {}
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            history = {}

    snap = history.get(today)

    if snap and not snap.get("closed"):
        # ── 2. Scarica prezzi di chiusura per le stesse azioni del mattino ──
        tickers = [s["ticker"] for s in snap.get("stocks", [])]
        logger.info(f"Chiusura mercato: fetch prezzi per {tickers}")

        def _fetch_close_prices(tks: list) -> dict:
            import yfinance as yf
            out = {}
            for t in tks:
                try:
                    fi = yf.Ticker(t).fast_info
                    out[t] = round(float(fi.last_price or 0), 4)
                except Exception:
                    out[t] = 0.0
            return out

        close_prices = await asyncio.to_thread(_fetch_close_prices, tickers)

        # ── 3. Aggiorna snapshot con price_at_close ──────────────────────────
        for s in snap["stocks"]:
            t = s["ticker"]
            pc = close_prices.get(t, 0.0)
            pa = s.get("price_at_analysis", 0.0)
            s["price_at_close"] = pc
            if pa > 0 and pc > 0:
                s["close_vs_morning_pct"] = round((pc - pa) / pa * 100, 2)

        snap["closed"] = True
        snap["closed_at"] = now.strftime("%Y-%m-%dT%H:%M:%S")
        history[today] = snap

        try:
            history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Storico chiusura salvato: {today}")
        except Exception as e:
            logger.error(f"Errore salvataggio storico chiusura: {e}")

        # ── 4. Messaggio Telegram: mattina → chiusura ────────────────────────
        stocks_sorted = sorted(
            snap["stocks"],
            key=lambda x: x.get("close_vs_morning_pct", 0),
            reverse=True,
        )

        lines = [f"📊 <b>Chiusura mercato — {date_str}</b>\n"
                 f"<i>Analisi mattutina → prezzi di chiusura NYSE</i>\n"]

        for s in stocks_sorted:
            pa   = s.get("price_at_analysis", 0)
            pc   = s.get("price_at_close", 0)
            chg  = s.get("close_vs_morning_pct")
            emj  = "📈" if (chg or 0) >= 0 else "📉"
            chg_str = f"{chg:+.2f}%" if chg is not None else "—"
            verdict = s.get("verdict", "")
            score   = s.get("score_10", 0)
            lines.append(
                f"{emj} <b>{s['ticker']}</b> {s.get('name', '')}  Score {score:.1f}/10\n"
                f"   ${pa:.4f} → <b>${pc:.4f}</b>  ({chg_str})\n"
                + (f"   🎯 {verdict}\n" if verdict else "")
            )

        if stocks_sorted:
            best   = stocks_sorted[0]
            worst  = stocks_sorted[-1]
            lines += [
                f"🏆 Migliore: <b>{best['ticker']}</b> "
                f"{best.get('close_vs_morning_pct', 0):+.2f}%",
                f"💔 Peggiore: <b>{worst['ticker']}</b> "
                f"{worst.get('close_vs_morning_pct', 0):+.2f}%",
            ]

        lines.append("\n<i>Dati: Yahoo Finance · Prossima analisi domani alle 7:30</i>")
        text = "\n".join(lines)

        await _send_to_group(context.bot, text, TOPIC_ANALISI_ID)
        for uid in all_uids:
            try:
                await context.bot.send_message(int(uid), text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"Errore recap serale {uid}: {e}")
        return

    # ── Fallback: nessuno snapshot mattutino oggi (es. giorno non lavorativo) ─
    logger.info("Nessuno snapshot mattutino trovato — recap serale con scanner fresco")
    scanner_raw = await asyncio.to_thread(scan_cheap_stocks, 40.0, 10)
    if not scanner_raw:
        return

    raw_results = await asyncio.gather(
        *[asyncio.to_thread(get_full_analysis, r["ticker"]) for r in scanner_raw],
        return_exceptions=True,
    )
    risultati = [d for d in raw_results if d and not isinstance(d, Exception)]
    if not risultati:
        return

    risultati.sort(key=lambda x: x["day_change_pct"], reverse=True)
    migliore, peggiore = risultati[0], risultati[-1]

    lines = [f"🌙 <b>Recap serale — {date_str}</b>\n"]
    for d in risultati:
        lines.append(format_report_line(d))
    lines += [
        "",
        f"🏆 Migliore oggi: <b>{migliore['ticker']}</b> {migliore['day_change_pct']:+.1f}%",
        f"💔 Peggiore oggi: <b>{peggiore['ticker']}</b> {peggiore['day_change_pct']:+.1f}%",
        "\n<i>Domani mattina alle 7:30 trovi la nuova classifica aggiornata.</i>",
    ]
    text = "\n".join(lines)

    await _send_to_group(context.bot, text, TOPIC_ANALISI_ID)
    for uid in all_uids:
        try:
            await context.bot.send_message(int(uid), text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Errore recap serale {uid}: {e}")


# ─── Job settimanale (Sabato 10:00) ─────────────────────────────────────────

async def job_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    all_uids = list(data["watchlists"].keys())
    if not all_uids and not GROUP_CHAT_ID:
        return

    # Usa lo scanner per il recap settimanale
    scanner_raw = await asyncio.to_thread(scan_cheap_stocks, 40.0, 10)
    if not scanner_raw:
        return

    raw_results = await asyncio.gather(
        *[asyncio.to_thread(get_full_analysis, r["ticker"]) for r in scanner_raw],
        return_exceptions=True,
    )
    risultati = [d for d in raw_results if d and not isinstance(d, Exception)]
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

    await _send_to_group(context.bot, text, TOPIC_ANALISI_ID)
    for uid in all_uids:
        try:
            await context.bot.send_message(int(uid), text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"Errore recap settimanale {uid}: {e}")


# ─── Job notizie portafoglio (ogni 4 ore) ────────────────────────────────────

async def job_news_portafoglio(context: ContextTypes.DEFAULT_TYPE):
    """Controlla notizie nuove per le azioni nel portafoglio → topic Notizie."""
    if not GROUP_CHAT_ID or not TOPIC_NOTIZIE_ID:
        return

    import yfinance as _yf

    data = load_data()
    # Raccogli tutti i ticker unici da tutti i portafogli
    all_tickers = set()
    for uid in data["watchlists"]:
        for t in _port_get(data, uid).keys():
            all_tickers.add(t)
    if not all_tickers:
        return

    sent_ids = set(data.get("sent_news_ids", []))
    new_sent = []
    news_lines = [f"📰 <b>Notizie portafoglio</b>\n"]
    found = 0

    def _fetch_news_sync(t):
        try:
            return _yf.Ticker(t).news or []
        except Exception:
            return []

    for ticker in sorted(all_tickers):
        try:
            news_items = await asyncio.to_thread(_fetch_news_sync, ticker)
            for item in news_items[:3]:
                content = item.get("content", {})
                title = content.get("title", "") if isinstance(content, dict) else str(content)
                if not title:
                    continue
                uid_key = title[:80]
                if uid_key in sent_ids:
                    continue
                # URL articolo
                url = ""
                if isinstance(content, dict):
                    cp = content.get("canonicalUrl", {})
                    url = cp.get("url", "") if isinstance(cp, dict) else ""
                link = f' <a href="{url}">→</a>' if url else ""
                news_lines.append(f"<b>{ticker}</b>: {title[:120]}{link}")
                new_sent.append(uid_key)
                found += 1
                if found >= 15:
                    break
        except Exception:
            continue
        if found >= 15:
            break

    if found == 0:
        return

    news_lines.append("\n<i>Aggiornamento automatico ogni 4 ore</i>")
    await _send_to_group(context.bot, "\n".join(news_lines), TOPIC_NOTIZIE_ID)

    # Salva IDs inviati (max 1000)
    all_ids = list(sent_ids) + new_sent
    data["sent_news_ids"] = all_ids[-1000:]
    save_data(data)


# ─── Web Server (FastAPI) ────────────────────────────────────────────────────

def _run_web_server():
    """Avvia il web server FastAPI in un thread separato."""
    try:
        import uvicorn
        from web_server import app as web_app
        port = int(os.getenv("PORT", 8080))
        logger.info(f"🌐 Web dashboard avviata su porta {port}")
        uvicorn.run(web_app, host="0.0.0.0", port=port, log_level="warning")
    except Exception as e:
        logger.error(f"Errore web server: {e}")


# ─── /livello — imposta livello esperienza ────────────────────────────────────

async def cmd_livello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    uid = str(update.effective_user.id)
    data = load_data()
    current = get_user_level(data, uid)

    if not args:
        kb_rows = [[
            btn("🟢 Base",   f"livello:dilettante"),
            btn("🟡 Pro",    f"livello:intermedio"),
            btn("🔴 Expert", f"livello:esperto"),
        ]]
        await update.message.reply_text(
            f"📊 <b>Livello attuale:</b> {_LEVEL_LABEL.get(current, current)}\n\n"
            "Scegli il tuo livello di esperienza per l'analisi grafico:",
            parse_mode=ParseMode.HTML, reply_markup=kb(kb_rows)
        )
        return

    level_map = {"base": "dilettante", "pro": "intermedio", "expert": "esperto",
                 "dilettante": "dilettante", "intermedio": "intermedio", "esperto": "esperto"}
    chosen = level_map.get(args[0].lower())
    if not chosen:
        await update.message.reply_text("❌ Livelli disponibili: base, pro, expert")
        return
    set_user_level(data, uid, chosen)
    save_data(data)
    await update.message.reply_text(
        f"✅ Livello impostato: {_LEVEL_LABEL[chosen]}\n"
        "Le prossime analisi grafico useranno questo livello."
    )


# ─── Analisi grafico da foto ──────────────────────────────────────────────────

async def handle_grafico_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analizza una foto di grafico inviata nel topic Grafico o in privato."""
    msg = update.message
    if not msg or not msg.photo:
        return

    # Filtra: accetta solo in privato o nel topic Grafico del gruppo
    is_private = msg.chat.type == "private"
    is_grafico_topic = (
        GROUP_CHAT_ID and TOPIC_GRAFICO_ID and
        msg.chat.id == GROUP_CHAT_ID and
        msg.message_thread_id == TOPIC_GRAFICO_ID
    )
    if not is_private and not is_grafico_topic:
        return

    if not groq_client:
        await msg.reply_text("❌ AI non configurata (GROQ_API_KEY mancante)")
        return

    uid = str(update.effective_user.id)
    data = load_data()
    level = get_user_level(data, uid)

    # Caption può sovrascrivere il livello
    caption = (msg.caption or "").lower()
    if "base" in caption or "dilettante" in caption:
        level = "dilettante"
    elif "pro" in caption or "intermedio" in caption:
        level = "intermedio"
    elif "expert" in caption or "esperto" in caption:
        level = "esperto"

    wait_msg = await msg.reply_text(
        f"🔍 Analisi grafico in corso… {_LEVEL_LABEL[level]}\n"
        "<i>Invio all'AI, circa 10-20 secondi…</i>",
        parse_mode=ParseMode.HTML
    )

    try:
        # Scarica la foto in memoria (qualità massima)
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        import io, base64
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("utf-8")
        image_url = f"data:image/jpeg;base64,{b64}"

        prompt = _GRAFICO_PROMPT[level]

        resp = await groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text",  "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            max_tokens=700,
            temperature=0.4,
        )

        analysis = resp.choices[0].message.content.strip()
        level_header = {
            "dilettante": "🟢 ANALISI BASE",
            "intermedio": "🟡 ANALISI TECNICA PRO",
            "esperto":    "🔴 ANALISI EXPERT",
        }[level]

        reply = (
            f"<b>{level_header}</b>\n\n"
            f"{analysis}\n\n"
            f"<i>⚠️ Solo a scopo informativo. Non costituisce consulenza finanziaria.</i>"
        )

        await wait_msg.delete()
        # Se nel gruppo, rispondi nel topic
        if is_grafico_topic:
            await context.bot.send_message(
                GROUP_CHAT_ID, reply,
                message_thread_id=TOPIC_GRAFICO_ID,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=msg.message_id,
            )
        else:
            await msg.reply_text(reply, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Errore analisi grafico: {e}")
        try:
            await wait_msg.edit_text(f"❌ Errore analisi: {str(e)[:120]}")
        except Exception:
            pass


# ─── Avvio ───────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("ERRORE: BOT_TOKEN mancante")
        return

    # Avvia web server in background (thread daemon)
    import threading
    web_thread = threading.Thread(target=_run_web_server, daemon=True, name="web-server")
    web_thread.start()

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
    app.add_handler(CommandHandler("valuta", cmd_valuta))
    app.add_handler(CommandHandler("chiediai", cmd_chiediai))
    app.add_handler(CommandHandler("livello", cmd_livello))
    app.add_handler(MessageHandler(filters.PHOTO, handle_grafico_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))

    jq = app.job_queue
    # Analisi mattutina lun–ven alle 7:30
    jq.run_daily(job_daily_report, time=time(hour=7, minute=30, tzinfo=ROME), days=(0, 1, 2, 3, 4))
    # Recap serale lun–ven alle 22:00
    jq.run_daily(job_evening_report, time=time(hour=22, minute=0, tzinfo=ROME), days=(0, 1, 2, 3, 4))
    # Notizie portafoglio ogni 4 ore
    jq.run_repeating(job_news_portafoglio, interval=14400, first=60)

    logger.info("Bot avviato!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
