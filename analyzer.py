import yfinance as yf
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import re


# ─── Analisi standard ────────────────────────────────────────────────────────

def get_full_analysis(ticker: str) -> dict | None:
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="1y")
        if hist is None or hist.empty or len(hist) < 5:
            # Fallback: su alcuni IP (datacenter/Render) Ticker.history viene limitato;
            # yf.download (chart API bulk) è più tollerante.
            try:
                hist = yf.download(ticker.upper(), period="1y", interval="1d",
                                   auto_adjust=True, progress=False)
                if hist is not None and isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.get_level_values(0)
            except Exception:
                hist = None
        if hist is None or hist.empty or len(hist) < 5:
            return None

        # .info e fast_info usano API Yahoo (quoteSummary) che gli IP dei datacenter
        # — es. Render — spesso bloccano. Rendiamoli OPZIONALI e ricaviamo prezzo e
        # massimi/minimi 52w dai dati storici (chart API), che funzionano ovunque.
        try:
            info = stock.info or {}
        except Exception:
            info = {}
        try:
            fast = stock.fast_info
        except Exception:
            fast = None

        closes = hist["Close"].dropna()
        current_price = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else current_price
        y_high = float(hist["High"].max())
        y_low = float(hist["Low"].min())
        try:
            if fast is not None:
                if fast.last_price:      current_price = float(fast.last_price)
                if fast.previous_close:  prev_close = float(fast.previous_close)
                if fast.year_high:       y_high = float(fast.year_high)
                if fast.year_low:        y_low = float(fast.year_low)
        except Exception:
            pass
        if not current_price or not prev_close:
            return None

        day_change_pct = ((current_price - prev_close) / prev_close) * 100
        returns = hist["Close"].pct_change().dropna()
        volatility = returns.std() * (252 ** 0.5) * 100

        week_return = _pct(hist, -6) if len(hist) >= 6 else None
        month_return = _pct(hist, -22) if len(hist) >= 22 else None
        one_year_return = _pct(hist, 0)
        ytd_return = _ytd_return(hist)

        sma_20 = float(hist["Close"].rolling(20).mean().iloc[-1])
        sma_50 = float(hist["Close"].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else None

        rsi = wilder_rsi(hist["Close"])

        if volatility < 30:
            risk_level, risk_emoji = "Basso", "🟢"
        elif volatility < 60:
            risk_level, risk_emoji = "Medio", "🟡"
        else:
            risk_level, risk_emoji = "Alto", "🔴"

        signals = []
        if rsi < 35:
            signals.append("RSI basso — possibile rimbalzo rialzista")
        elif rsi > 65:
            signals.append("RSI alto — attenzione a correzione")
        if sma_50:
            signals.append("Sopra media 50gg — trend rialzista" if current_price > sma_50 else "Sotto media 50gg — trend ribassista")
        signals.append("Sopra media 20gg — momentum positivo" if current_price > sma_20 else "Sotto media 20gg — momentum negativo")

        news_titles = []
        try:
            for item in (stock.news or [])[:3]:
                content = item.get("content", {})
                title = content.get("title", "") if isinstance(content, dict) else str(content)
                if title:
                    news_titles.append(title[:120])
        except Exception:
            pass

        return {
            "ticker": ticker.upper(),
            "name": info.get("shortName", ticker.upper()),
            "current_price": current_price,
            "currency": info.get("currency", "USD"),
            "day_change_pct": day_change_pct,
            "week_52_high": y_high,
            "week_52_low": y_low,
            "volatility": volatility,
            "risk_level": risk_level,
            "risk_emoji": risk_emoji,
            "week_return": week_return,
            "month_return": month_return,
            "ytd_return": ytd_return,
            "one_year_return": one_year_return,
            "rsi": rsi,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "signals": signals,
            "news": news_titles,
            "beta": info.get("beta"),
            "sector": info.get("sector"),
            "pe_ratio": info.get("trailingPE"),
            # Fondamentali aggiuntivi
            "forward_pe":    info.get("forwardPE"),
            "eps":           info.get("trailingEps"),
            "revenue":       info.get("totalRevenue"),
            "debt_equity":   info.get("debtToEquity"),
            "profit_margin": info.get("profitMargins"),
            "dividend_yield":info.get("dividendYield"),
            "market_cap":    info.get("marketCap"),
            "roe":           info.get("returnOnEquity"),
        }
    except Exception as e:
        print(f"[analyzer] Errore per {ticker}: {e}")
        return None


# ─── Analisi trading ─────────────────────────────────────────────────────────

def get_trading_analysis(ticker: str) -> dict | None:
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="3mo")
        if hist.empty or len(hist) < 26:
            return None

        info = stock.info
        fast = stock.fast_info

        current_price = fast.last_price
        prev_close = fast.previous_close
        if not current_price or not prev_close:
            return None

        day_change_pct = ((current_price - prev_close) / prev_close) * 100

        rsi = wilder_rsi(hist["Close"])

        ema_12 = hist["Close"].ewm(span=12, adjust=False).mean()
        ema_26 = hist["Close"].ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        macd_bullish = float(macd_line.iloc[-1]) > float(signal_line.iloc[-1])
        macd_crossover = float(macd_hist.iloc[-1]) > 0 and float(macd_hist.iloc[-2]) <= 0
        macd_crossunder = float(macd_hist.iloc[-1]) < 0 and float(macd_hist.iloc[-2]) >= 0

        sma_20 = hist["Close"].rolling(20).mean()
        std_20 = hist["Close"].rolling(20).std()
        upper_band = float((sma_20 + 2 * std_20).iloc[-1])
        lower_band = float((sma_20 - 2 * std_20).iloc[-1])
        bb_range = upper_band - lower_band
        bb_position = (current_price - lower_band) / bb_range if bb_range > 0 else 0.5

        avg_volume = float(hist["Volume"].rolling(20).mean().iloc[-1])
        today_volume = float(hist["Volume"].iloc[-1])
        volume_ratio = today_volume / avg_volume if avg_volume > 0 else 1

        support = float(hist["Low"].rolling(20).min().iloc[-1])
        resistance = float(hist["High"].rolling(20).max().iloc[-1])

        returns = hist["Close"].pct_change().dropna()
        volatility = returns.std() * (252 ** 0.5) * 100

        buy_pts = sell_pts = 0
        if rsi < 40: buy_pts += 2
        elif rsi > 60: sell_pts += 2
        if macd_bullish: buy_pts += 1
        else: sell_pts += 1
        if macd_crossover: buy_pts += 2
        if macd_crossunder: sell_pts += 2
        if bb_position < 0.2: buy_pts += 2
        elif bb_position > 0.8: sell_pts += 1
        if volume_ratio > 1.5: buy_pts += 1

        if buy_pts > sell_pts + 1:
            signal, signal_emoji = "COMPRA", "🟢"
        elif sell_pts > buy_pts + 1:
            signal, signal_emoji = "VENDI", "🔴"
        else:
            signal, signal_emoji = "ATTENDI", "🟡"

        price_range = resistance - support
        stop_loss = current_price - price_range * 0.3
        target = current_price + price_range * 0.5

        return {
            "ticker": ticker.upper(),
            "name": info.get("shortName", ticker.upper()),
            "current_price": current_price,
            "currency": info.get("currency", "USD"),
            "day_change_pct": day_change_pct,
            "rsi": rsi,
            "macd_bullish": macd_bullish,
            "macd_crossover": macd_crossover,
            "macd_crossunder": macd_crossunder,
            "upper_band": upper_band,
            "lower_band": lower_band,
            "bb_position": bb_position,
            "volume_ratio": volume_ratio,
            "support": support,
            "resistance": resistance,
            "stop_loss": stop_loss,
            "target": target,
            "signal": signal,
            "signal_emoji": signal_emoji,
            "volatility": volatility,
        }
    except Exception as e:
        print(f"[trading] Errore per {ticker}: {e}")
        return None


# ─── Formattatori ────────────────────────────────────────────────────────────

def _h(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(hist, start_idx: int) -> float:
    first = hist["Close"].iloc[start_idx] if start_idx != 0 else hist["Close"].iloc[0]
    return ((hist["Close"].iloc[-1] - first) / first) * 100


def _ytd_return(hist) -> float:
    """Ritorno da inizio anno solare: baseline = ultima chiusura dell'anno
    precedente se presente nella finestra storica, altrimenti la prima
    chiusura dell'anno corrente (finestra troppo corta)."""
    idx = hist.index
    cur_year = idx[-1].year
    prior = hist["Close"][idx.year == cur_year - 1]
    baseline = prior.iloc[-1] if len(prior) else hist["Close"][idx.year == cur_year].iloc[0]
    return ((hist["Close"].iloc[-1] - baseline) / baseline) * 100


def wilder_rsi(close, period: int = 14) -> float:
    """RSI standard (Wilder smoothing) come TradingView/i broker, non una media mobile piatta.
    Ritorna 50.0 (neutro) se non calcolabile invece di propagare NaN nello scoring."""
    if close is None or len(close) < period + 1:
        return 50.0
    delta = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rsi = 100 - (100 / (1 + avg_gain / avg_loss))
    val = float(rsi.iloc[-1])
    return 50.0 if pd.isna(val) else val


def _quality_score_10(rsi: float, chg: float, sma_20, price: float) -> float:
    """Score 0-10 rapido (get_enriched_analysis) da RSI, variazione giornaliera e trend
    vs SMA20. Soglie in config.SCORING['enriched'] — hand-set, non ricalibrate su backtest."""
    from config import SCORING
    s = SCORING["enriched"]
    score_raw = 0
    if rsi < s["rsi_oversold_strong"]:        score_raw += s["rsi_oversold_strong_pts"]
    elif rsi < s["rsi_oversold"]:              score_raw += s["rsi_oversold_pts"]
    elif rsi > s["rsi_overbought_strong"]:     score_raw += s["rsi_overbought_strong_pts"]
    elif rsi > s["rsi_overbought"]:            score_raw += s["rsi_overbought_pts"]
    if chg > s["chg_strong"]:                  score_raw += s["chg_strong_pts"]
    elif chg > s["chg_mid"]:                   score_raw += s["chg_mid_pts"]
    elif chg > 0:                              score_raw += s["chg_positive_pts"]
    elif chg < s["chg_weak"]:                  score_raw += s["chg_weak_pts"]
    if sma_20 and price > sma_20:
        score_raw += s["above_sma20_pts"]
    return round(min(10.0, max(0.0, (score_raw + s["raw_offset"]) / s["raw_range"] * 10)), 1)


def format_analysis_message(d: dict) -> str:
    p = d["current_price"]
    chg = d["day_change_pct"]
    sign = "+" if chg >= 0 else ""
    chg_emoji = "📈" if chg >= 0 else "📉"

    lines = [
        f"<b>{_h(d['name'])} ({_h(d['ticker'])})</b>",
        "",
        f"💰 <b>Prezzo:</b> {p:.2f} {d['currency']}",
        f"{chg_emoji} <b>Oggi:</b> {sign}{chg:.2f}%",
        "",
        "📊 <b>Rendimento:</b>",
    ]
    for label, val in [("Settimana", d["week_return"]), ("Mese", d["month_return"]), ("Anno", d["ytd_return"])]:
        if val is not None:
            lines.append(f"  • {label}: {'+'if val>=0 else''}{val:.1f}%")

    lines += [
        "",
        f"{d['risk_emoji']} <b>Rischio:</b> {d['risk_level']} (volatilità annua: {d['volatility']:.0f}%)",
    ]
    if d["beta"]:
        lines.append(f"  • Beta vs S&amp;P500: {d['beta']:.2f}")

    lines += [
        "",
        "📐 <b>Indicatori tecnici:</b>",
        f"  • RSI (14): {d['rsi']:.0f}/100",
        f"  • Media 20gg: {d['sma_20']:.2f}",
    ]
    if d["sma_50"]:
        lines.append(f"  • Media 50gg: {d['sma_50']:.2f}")
    if d["week_52_high"] and d["week_52_low"]:
        lines.append(f"  • Range 52 settimane: {d['week_52_low']:.2f} — {d['week_52_high']:.2f}")

    if d["signals"]:
        lines += ["", "🔍 <b>Segnali:</b>"]
        for s in d["signals"]:
            lines.append(f"  • {_h(s)}")

    pos = sum(1 for s in d["signals"] if any(w in s for w in ["rialzista", "rimbalzo", "positivo"]))
    neg = sum(1 for s in d["signals"] if any(w in s for w in ["ribassista", "correzione", "negativo"]))
    lines.append("")
    if pos > neg:
        lines.append("✅ <b>Valutazione:</b> Segnali positivi prevalenti — potenziale opportunità")
    elif neg > pos:
        lines.append("⚠️ <b>Valutazione:</b> Segnali negativi prevalenti — cautela")
    else:
        lines.append("⚖️ <b>Valutazione:</b> Segnali misti — aspetta conferma")

    if d["news"]:
        lines += ["", "📰 <b>Ultime notizie:</b>"]
        for n in d["news"]:
            lines.append(f"  • {_h(n)}")

    if d["sector"]:
        lines += ["", f"🏭 Settore: {_h(d['sector'])}"]

    lines.append(f"\n<i>Analisi generata il {datetime.now().strftime('%d/%m/%Y %H:%M')}</i>")
    return "\n".join(lines)


def format_trading_message(d: dict) -> str:
    p = d["current_price"]
    chg = d["day_change_pct"]
    sign = "+" if chg >= 0 else ""
    chg_emoji = "📈" if chg >= 0 else "📉"

    if d["macd_crossover"]:
        macd_desc = "crossover rialzista ⚡ (segnale forte)"
    elif d["macd_crossunder"]:
        macd_desc = "crossunder ribassista ⚡ (segnale forte)"
    elif d["macd_bullish"]:
        macd_desc = "positivo (trend rialzista)"
    else:
        macd_desc = "negativo (trend ribassista)"

    if d["bb_position"] < 0.2:
        bb_desc = "vicino alla banda bassa — possibile rimbalzo"
    elif d["bb_position"] > 0.8:
        bb_desc = "vicino alla banda alta — possibile correzione"
    else:
        bb_desc = f"a metà canale ({d['bb_position']*100:.0f}%)"

    if d["volume_ratio"] > 2:
        vol_desc = f"{d['volume_ratio']:.1f}x la media — interesse molto alto"
    elif d["volume_ratio"] > 1.5:
        vol_desc = f"{d['volume_ratio']:.1f}x la media — interesse alto"
    elif d["volume_ratio"] < 0.7:
        vol_desc = f"{d['volume_ratio']:.1f}x la media — interesse basso"
    else:
        vol_desc = f"{d['volume_ratio']:.1f}x la media — normale"

    lines = [
        f"🎯 <b>Day Trading: {_h(d['name'])} ({_h(d['ticker'])})</b>",
        "",
        f"💰 Prezzo: <b>{p:.2f} {d['currency']}</b>  {chg_emoji} {sign}{chg:.2f}%",
        "",
        f"📊 Segnale: {d['signal_emoji']} <b>{d['signal']}</b>",
        "",
        "📐 <b>Indicatori:</b>",
        f"  • RSI (14): {d['rsi']:.0f}/100",
        f"  • MACD: {_h(macd_desc)}",
        f"  • Bollinger Bands: {_h(bb_desc)}",
        f"  • Volume: {_h(vol_desc)}",
        "",
        "🎯 <b>Livelli chiave:</b>",
        f"  • Supporto: {d['support']:.2f}",
        f"  • Resistenza: {d['resistance']:.2f}",
        f"  • Stop Loss suggerito: {d['stop_loss']:.2f}",
        f"  • Target suggerito: {d['target']:.2f}",
        "",
        f"⏱ <b>Orizzonte:</b> giornata / 1-3 giorni",
        f"⚠️ Volatilità annua: {d['volatility']:.0f}%",
        "",
        f"<i>Non è un consiglio finanziario. Investi sempre con cautela.</i>",
    ]
    return "\n".join(lines)


def format_confronto_message(d1: dict, d2: dict) -> str:
    score1 = score2 = 0
    criteri = []

    # Variazione giornaliera
    if d1["day_change_pct"] > d2["day_change_pct"]:
        score1 += 1
        criteri.append(f"💰 Oggi:  {_sign(d1['day_change_pct'])}  vs  {_sign(d2['day_change_pct'])}  → {d1['ticker']} ✅")
    else:
        score2 += 1
        criteri.append(f"💰 Oggi:  {_sign(d1['day_change_pct'])}  vs  {_sign(d2['day_change_pct'])}  → {d2['ticker']} ✅")

    # Rendimento mensile
    r1 = d1["month_return"] or 0
    r2 = d2["month_return"] or 0
    if r1 > r2:
        score1 += 1
        criteri.append(f"📅 Mese:  {_sign(r1)}  vs  {_sign(r2)}  → {d1['ticker']} ✅")
    else:
        score2 += 1
        criteri.append(f"📅 Mese:  {_sign(r1)}  vs  {_sign(r2)}  → {d2['ticker']} ✅")

    # RSI (più vicino a 40 = miglior segnale acquisto)
    if abs(d1["rsi"] - 40) < abs(d2["rsi"] - 40):
        score1 += 1
        criteri.append(f"📐 RSI:  {d1['rsi']:.0f}  vs  {d2['rsi']:.0f}  → {d1['ticker']} ✅")
    else:
        score2 += 1
        criteri.append(f"📐 RSI:  {d1['rsi']:.0f}  vs  {d2['rsi']:.0f}  → {d2['ticker']} ✅")

    # Rischio (volatilità minore = meglio)
    if d1["volatility"] < d2["volatility"]:
        score1 += 1
        criteri.append(f"⚠️ Rischio:  {d1['risk_level']}  vs  {d2['risk_level']}  → {d1['ticker']} ✅")
    else:
        score2 += 1
        criteri.append(f"⚠️ Rischio:  {d1['risk_level']}  vs  {d2['risk_level']}  → {d2['ticker']} ✅")

    # Segnali tecnici
    pos1 = sum(1 for s in d1["signals"] if any(w in s for w in ["rialzista", "rimbalzo", "positivo"]))
    neg1 = sum(1 for s in d1["signals"] if any(w in s for w in ["ribassista", "negativo"]))
    pos2 = sum(1 for s in d2["signals"] if any(w in s for w in ["rialzista", "rimbalzo", "positivo"]))
    neg2 = sum(1 for s in d2["signals"] if any(w in s for w in ["ribassista", "negativo"]))
    if (pos1 - neg1) >= (pos2 - neg2):
        score1 += 1
        criteri.append(f"🔍 Segnali tecnici  → {d1['ticker']} ✅")
    else:
        score2 += 1
        criteri.append(f"🔍 Segnali tecnici  → {d2['ticker']} ✅")

    lines = [
        f"⚔️ <b>Confronto: {_h(d1['ticker'])} vs {_h(d2['ticker'])}</b>",
        "",
    ]
    lines += criteri
    lines += [""]

    if score1 > score2:
        lines.append(f"🏆 <b>Migliore ora: {_h(d1['name'])} ({_h(d1['ticker'])})</b>")
        lines.append(f"Vince {score1} criteri su 5")
    elif score2 > score1:
        lines.append(f"🏆 <b>Migliore ora: {_h(d2['name'])} ({_h(d2['ticker'])})</b>")
        lines.append(f"Vince {score2} criteri su 5")
    else:
        lines.append("⚖️ <b>Pareggiano</b> — segnali equivalenti")

    lines.append(f"\n<i>Usa /apr TICKER per l'analisi completa</i>")
    return "\n".join(lines)


def _sign(v: float) -> str:
    return f"{'+'if v>=0 else''}{v:.1f}%"


# ─── Scanner mercato ─────────────────────────────────────────────────────────

# ── Universo dinamico US: cache in RAM (scaricato una volta al giorno) ────────
_US_UNIVERSE_CACHE: list | None = None
_US_UNIVERSE_TS: float = 0.0
_US_UNIVERSE_TTL = 86400  # 24 h


def _get_full_us_universe() -> list:
    """Scarica tutti i ticker azionari NASDAQ+NYSE in tempo reale (cache in RAM 24h).
    Fonte: ftp.nasdaqtrader.com — file pubblici, aggiornati ogni sera.
    Esclude automaticamente: ETF, warrant, preferred share, test issue, simboli speciali."""
    global _US_UNIVERSE_CACHE, _US_UNIVERSE_TS
    import urllib.request, io, csv

    if _US_UNIVERSE_CACHE and (time.time() - _US_UNIVERSE_TS) < _US_UNIVERSE_TTL:
        return _US_UNIVERSE_CACHE

    tickers: list[str] = []
    _URLS = [
        "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",   # NASDAQ
        "https://ftp.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",    # NYSE / AMEX / altri
    ]
    # Solo simboli puri: 1-5 lettere, opzionalmente .X per classe (es. BRK.B)
    _VALID = re.compile(r'^[A-Z]{1,5}(\.[A-Z])?$')

    for url in _URLS:
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                content = resp.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content), delimiter="|")
            for row in reader:
                sym = (row.get("Symbol") or row.get("ACT Symbol") or "").strip()
                if not sym or not _VALID.match(sym):
                    continue
                if row.get("ETF", "N") == "Y":
                    continue
                if row.get("Test Issue", "N") == "Y":
                    continue
                tickers.append(sym)
        except Exception as e:
            print(f"[universe] Errore download {url}: {e}")

    tickers = list(dict.fromkeys(tickers))  # dedup mantenendo ordine

    if tickers:
        _US_UNIVERSE_CACHE = tickers
        _US_UNIVERSE_TS = time.time()
        print(f"[universe] {len(tickers)} ticker US scaricati (NASDAQ+NYSE+AMEX)")
    else:
        print("[universe] Download fallito — fallback lista statica da config")
        from config import REVOLUT_UNIVERSE
        _US_UNIVERSE_CACHE = REVOLUT_UNIVERSE
        _US_UNIVERSE_TS = time.time()

    return _US_UNIVERSE_CACHE


_CURRENCY_BY_SUFFIX = {
    "MI": "EUR", "PA": "EUR", "DE": "EUR", "AS": "EUR",
    "MC": "EUR", "BR": "EUR", "LS": "EUR", "VI": "EUR",
    "ST": "SEK", "CO": "DKK", "ZU": "CHF", "HE": "EUR",
    "L":  "GBP",
}

def _ticker_currency(ticker: str) -> str:
    if "." in ticker:
        return _CURRENCY_BY_SUFFIX.get(ticker.rsplit(".", 1)[-1].upper(), "USD")
    return "USD"


def scan_cheap_stocks(max_price: float = 200.0, top_n: int | None = None, universe: list = None) -> list:
    """Scansiona il mercato in 2 fasi quando l'universo è grande (es. ~6.000 titoli):
    Fase 1 — pre-filtro rapido per prezzo (batch 2d): scarta tutto ciò che è sopra max_price.
    Fase 2 — analisi tecnica completa solo per i candidati rimasti.
    Se viene passato un universo esplicito (es. ETF_UNIVERSE) viene usata una sola fase."""
    from config import EUROPEAN_UNIVERSE, AI_UNIVERSE, SCORING
    _AI_SET = set(AI_UNIVERSE)   # lookup O(1)
    _sc = SCORING["scan"]

    if universe is not None:
        # Universo esplicito piccolo (ETF, watchlist…): analisi diretta, una fase
        tickers = universe
        use_two_phase = False
    else:
        # Universo dinamico completo: NASDAQ+NYSE+AMEX + mercati europei
        us = _get_full_us_universe()
        tickers = list(dict.fromkeys(us + EUROPEAN_UNIVERSE))
        use_two_phase = True

    # ── Fase 1: pre-filtro rapido per prezzo (solo se universo grande) ─────────
    if use_two_phase and len(tickers) > 500:
        t0 = time.time()
        candidates: list[str] = []
        chunks_p1 = [tickers[i:i+100] for i in range(0, len(tickers), 100)]

        for chunk in chunks_p1:
            try:
                raw = yf.download(
                    chunk, period="2d", interval="1d",
                    auto_adjust=True, progress=False
                )
                if raw.empty or not isinstance(raw.columns, pd.MultiIndex):
                    continue
                close_df = raw["Close"]
                for t in chunk:
                    if t not in close_df.columns:
                        continue
                    vals = close_df[t].dropna()
                    if not len(vals):
                        continue
                    price = float(vals.iloc[-1])
                    if 0 < price <= max_price:
                        candidates.append(t)
            except Exception as e:
                print(f"[scan_p1] errore chunk: {e}")
                continue

        elapsed = time.time() - t0
        print(f"[scan] Fase 1: {len(candidates)}/{len(tickers)} candidati "
              f"<= ${max_price:.0f} in {elapsed:.1f}s")
        tickers = candidates

    # ── Fase 2: analisi tecnica completa ──────────────────────────────────────
    risultati = []
    chunks = [tickers[i:i+50] for i in range(0, len(tickers), 50)]

    for chunk in chunks:
        try:
            raw = yf.download(
                tickers=chunk,
                period="4mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
            if raw.empty:
                continue

            if isinstance(raw.columns, pd.MultiIndex):
                close_df = raw["Close"]
                volume_df = raw["Volume"]
            else:
                continue

            for ticker in chunk:
                try:
                    if ticker not in close_df.columns:
                        continue

                    close = close_df[ticker].dropna()
                    volume = volume_df[ticker].dropna()

                    if len(close) < 5:
                        continue

                    current_price = float(close.iloc[-1])
                    # Floor a $3: sotto è territorio penny stock (raramente "buone")
                    if current_price < 3.0 or current_price > max_price:
                        continue

                    prev_close = float(close.iloc[-2])
                    day_change_pct = ((current_price - prev_close) / prev_close) * 100

                    # RSI
                    rsi = wilder_rsi(close)

                    # Volume ratio + LIQUIDITÀ (filtro qualità: scarta penny/junk illiquidi)
                    vol_ratio = 1.0
                    avg_vol = 0.0
                    if len(volume) >= 5:
                        avg_vol = float(volume.rolling(min(20, len(volume))).mean().iloc[-1])
                        last_vol = float(volume.iloc[-1])
                        avg10 = float(volume.rolling(min(10, len(volume))).mean().iloc[-1])
                        if avg10 > 0:
                            vol_ratio = last_vol / avg10
                    avg_dollar_vol = avg_vol * current_price
                    # GATE liquidità: sotto ~$3M/giorno di controvalore = troppo illiquido → scarta
                    if avg_dollar_vol < 3_000_000:
                        continue

                    # Medie mobili 20 e 50 giorni (qualità del trend)
                    sma20 = float(close.rolling(min(20, len(close))).mean().iloc[-1])
                    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else sma20
                    above_sma20 = current_price > sma20
                    above_sma50 = current_price > sma50
                    uptrend_align = sma20 > sma50

                    # Rendimenti multi-timeframe (forza COSTANTE, non un singolo pop)
                    week_return = float((current_price - close.iloc[-6]) / close.iloc[-6] * 100) if len(close) >= 6 else 0.0
                    month_return = float((current_price - close.iloc[-22]) / close.iloc[-22] * 100) if len(close) >= 22 else 0.0

                    # Rischio
                    returns = close.pct_change().dropna()
                    volatility = float(returns.std() * (252 ** 0.5) * 100) if len(returns) > 1 else 50.0
                    if volatility < 30:
                        risk_level, risk_emoji = "Basso", "🟢"
                    elif volatility < 60:
                        risk_level, risk_emoji = "Medio", "🟡"
                    else:
                        risk_level, risk_emoji = "Alto", "🔴"

                    # ════════════════════════════════════════════════════════════
                    # NUOVO SCORING — premia QUALITÀ del trend e forza costante,
                    # non il "pop" di un singolo giorno (che spesso è un pump che svanisce)
                    # ════════════════════════════════════════════════════════════
                    score = 0

                    # 1) QUALITÀ DEL TREND (la spina dorsale dei titoli buoni) — max +4
                    trend_pts = 0
                    if above_sma20:   trend_pts += _sc["trend_component_pts"]
                    if above_sma50:   trend_pts += _sc["trend_component_pts"]
                    if uptrend_align: trend_pts += _sc["trend_component_pts"]
                    if above_sma20 and above_sma50 and uptrend_align: trend_pts += _sc["trend_component_pts"]  # trend pulito
                    score += trend_pts

                    # 2) MOMENTUM MULTI-TIMEFRAME (forza costante settimana+mese) — max +4
                    mom_pts = 0
                    if week_return > _sc["week_return_pos"]:      mom_pts += _sc["week_return_pos_pts"]
                    if week_return > _sc["week_return_strong"]:   mom_pts += _sc["week_return_strong_pts"]
                    if month_return > _sc["month_return_pos"]:    mom_pts += _sc["month_return_pos_pts"]
                    if month_return > _sc["month_return_strong"]: mom_pts += _sc["month_return_strong_pts"]
                    if month_return < _sc["month_return_weak"]:   mom_pts += _sc["month_return_weak_pts"]   # downtrend forte = penalità
                    score += mom_pts

                    # 3) RSI in ZONA SANA (non gli estremi: né ipercomprato né coltello che cade)
                    rsi_pts = 0
                    if _sc["rsi_ideal_lo"] <= rsi <= _sc["rsi_ideal_hi"]:            rsi_pts = _sc["rsi_ideal_pts"]      # zona ideale
                    elif _sc["rsi_pullback_lo"] <= rsi < _sc["rsi_pullback_hi"]:     rsi_pts = _sc["rsi_pullback_pts"]   # pullback leggero in trend
                    elif rsi > _sc["rsi_extended"]:                                  rsi_pts = _sc["rsi_extended_pts"]  # esteso / ipercomprato
                    elif rsi > _sc["rsi_overbought"]:                                rsi_pts = _sc["rsi_overbought_pts"]
                    elif rsi < _sc["rsi_falling_knife"]:                             rsi_pts = _sc["rsi_falling_knife_pts"]  # falling knife: penalizza, non premiare
                    score += rsi_pts

                    # 4) VOLUME come CONFERMA (solo se il prezzo sale) — modesto
                    vol_pts = _sc["vol_confirm_pts"] if (vol_ratio > _sc["vol_ratio_confirm"] and day_change_pct > 0) else 0
                    score += vol_pts

                    # 5) SANITÀ DELLA VOLATILITÀ
                    if _sc["volatility_healthy_lo"] <= volatility <= _sc["volatility_healthy_hi"]:  score += _sc["volatility_healthy_pts"]  # volatilità sana
                    elif volatility > _sc["volatility_junk"]:    score += _sc["volatility_junk_pts"]    # territorio pump/junk
                    elif volatility > _sc["volatility_high"]:    score += _sc["volatility_high_pts"]
                    # spike parabolico in un giorno = rischio di inseguire il top
                    if day_change_pct > _sc["parabolic_spike_pct"]:  score += _sc["parabolic_spike_pts"]

                    # ── Volatilità anomala: ultimi 5gg vs mese intero ─────────
                    recent_vol = float(returns.iloc[-5:].std() * (252**0.5) * 100) if len(returns) >= 5 else volatility
                    low_vol_alert = (recent_vol < volatility * 0.45) and (volatility > 25)

                    # ── Giorno della settimana: rendimento medio Mon-Fri ──────
                    dow_ret: dict[int, list] = {0:[], 1:[], 2:[], 3:[], 4:[]}
                    for i in range(1, len(close)):
                        try:
                            d = close.index[i].weekday()
                            r = float((close.iloc[i] - close.iloc[i-1]) / close.iloc[i-1] * 100)
                            if 0 <= d <= 4 and not pd.isna(r):
                                dow_ret[d].append(r)
                        except Exception:
                            pass
                    dow_avg = {d: round(sum(v)/len(v), 2) if v else 0.0 for d, v in dow_ret.items()}

                    # ── Doppio segnale (basato su qualità del trend) ──────────
                    bull_sigs = 0
                    bear_sigs = 0
                    if above_sma20 and above_sma50: bull_sigs += 1
                    elif not above_sma20 and not above_sma50: bear_sigs += 1
                    if uptrend_align: bull_sigs += 1
                    else:             bear_sigs += 1
                    if week_return > 0 and month_return > 0: bull_sigs += 1
                    elif week_return < 0 and month_return < 0: bear_sigs += 1
                    if _sc["rsi_ideal_lo"] <= rsi <= _sc["rsi_ideal_hi"]: bull_sigs += 1
                    elif rsi > _sc["rsi_extended"] or rsi < _sc["rsi_falling_knife"]: bear_sigs += 1
                    double_signal = "bull" if bull_sigs >= 3 else ("bear" if bear_sigs >= 3 else "")

                    # Bonus AI: piccolo boost per aziende AI-focused
                    is_ai = ticker in _AI_SET
                    ai_bonus_pts = _sc["ai_ticker_bonus_pts"] if is_ai else 0
                    score += ai_bonus_pts

                    # Normalizza (range grezzo ~ -7..+13) → 0-10
                    score_10 = round(min(10.0, max(0.0, (score + _sc["raw_offset"]) / _sc["raw_range"] * 10)), 1)

                    # Trade setup
                    daily_vol_pct = volatility / (252 ** 0.5) / 100
                    daily_vol_dollar = current_price * daily_vol_pct
                    stop_mult = 1.5 if volatility < 30 else (2.0 if volatility < 60 else 2.5)
                    stop_dist = max(daily_vol_dollar * stop_mult, current_price * 0.005)
                    target_dist = stop_dist * 2.5
                    stop_price = round(current_price - stop_dist, 2)
                    target_price = round(current_price + target_dist, 2)
                    trade_setup = {
                        "entry": round(current_price, 2),
                        "stop": stop_price,
                        "target": target_price,
                        "stop_pct": round(-stop_dist / current_price * 100, 1),
                        "target_pct": round(target_dist / current_price * 100, 1),
                        "rr": 2.5,
                    }

                    risultati.append({
                        "ticker": ticker,
                        "current_price": current_price,
                        "currency": _ticker_currency(ticker),
                        "day_change_pct": day_change_pct,
                        "rsi": rsi,
                        "vol_ratio": vol_ratio,
                        "score": score,
                        "score_10": score_10,
                        "risk_emoji": risk_emoji,
                        "risk_level": risk_level,
                        "volatility": volatility,
                        "is_ai": is_ai,
                        "low_vol_alert": low_vol_alert,
                        "double_signal": double_signal,
                        "bull_signals": bull_sigs,
                        "bear_signals": bear_sigs,
                        "dow_avg": dow_avg,
                        "week_return": round(week_return, 1),
                        "month_return": round(month_return, 1),
                        "above_sma50": above_sma50,
                        "uptrend": above_sma20 and above_sma50 and uptrend_align,
                        "avg_dollar_vol": round(avg_dollar_vol),
                        "score_breakdown": {
                            "rsi": rsi_pts,
                            "momentum": mom_pts,
                            "volume": vol_pts,
                            "trend": trend_pts,
                            "ai_bonus": ai_bonus_pts,
                        },
                        "trade_setup": trade_setup,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[scan_p2] Errore chunk: {e}")
            continue

    risultati.sort(key=lambda x: x["score"], reverse=True)
    return risultati if top_n is None else risultati[:top_n]


# ─── Analisi arricchita (report mattutino) ───────────────────────────────────

def get_enriched_analysis(ticker: str) -> dict | None:
    """Full analysis + earnings (oggi e prossimi), news sentiment, 52w upside, daily estimate, score."""
    base = get_full_analysis(ticker)
    if not base:
        return None

    # ── Earnings oggi + prossima data earnings ──
    earnings_today = False
    next_earnings_str = None
    days_to_earnings = None
    try:
        stock = yf.Ticker(ticker.upper())
        cal = stock.calendar
        today = datetime.now(ZoneInfo("America/New_York")).date()  # earnings sono eventi del mercato USA
        future_dates = []
        if isinstance(cal, dict):
            for key in ("Earnings Date", "Earnings Dates"):
                ed = cal.get(key)
                if ed is None:
                    continue
                dates = ed if isinstance(ed, list) else [ed]
                for item in dates:
                    try:
                        d = item.date() if hasattr(item, "date") else item
                        if d == today:
                            earnings_today = True
                        elif d > today:
                            future_dates.append(d)
                    except Exception:
                        pass
        if future_dates:
            nxt = min(future_dates)
            _MESI = ["gen", "feb", "mar", "apr", "mag", "giu",
                     "lug", "ago", "set", "ott", "nov", "dic"]
            next_earnings_str = f"{nxt.day} {_MESI[nxt.month - 1]} {nxt.year}"
            try:
                days_to_earnings = (nxt - today).days
            except Exception:
                days_to_earnings = None
    except Exception:
        pass

    # ── Sentiment notizie ──
    pos_words = {"beat", "surge", "gain", "profit", "growth", "record",
                 "upgrade", "buy", "strong", "rally", "rise", "positive", "exceed", "soar"}
    neg_words = {"miss", "fall", "loss", "decline", "downgrade", "sell", "cut",
                 "risk", "warning", "weak", "drop", "down", "negative", "concern"}
    sent = 0
    for title in base.get("news", []):
        tl = title.lower()
        for w in pos_words:
            if w in tl:
                sent += 1
        for w in neg_words:
            if w in tl:
                sent -= 1

    if sent > 0:
        news_sentiment, news_emoji, news_label = "positive", "🟢", "Positive"
    elif sent < 0:
        news_sentiment, news_emoji, news_label = "negative", "🔴", "Negative"
    else:
        news_sentiment, news_emoji, news_label = "neutre", "⚪", "Neutre"

    # ── Upside verso massimo 52 settimane ──
    upside_52w = 0.0
    if base.get("week_52_high") and base["current_price"] > 0:
        upside_52w = (base["week_52_high"] - base["current_price"]) / base["current_price"] * 100

    # ── Variazione odierna (già realizzata, non una previsione) ──
    daily_estimate_pct = base["day_change_pct"]

    # ── Score 0-10 basato su RSI, variazione e trend ──
    rsi = base.get("rsi", 50.0)
    chg = base.get("day_change_pct", 0.0)
    sma_20 = base.get("sma_20")
    price = base.get("current_price", 0.0)
    score_10 = _quality_score_10(rsi, chg, sma_20, price)

    # ── Stima direzionale a 5 giorni: direzione dallo score, ampiezza dalla volatilità ──
    vol = base.get("volatility") or 40.0
    vol_5d = vol * 0.1409                      # ~ deviazione su 5 sedute (annua/√252·√5)
    conviction = (score_10 - 5.5) / 4.5        # <0 ribassista, >0 rialzista
    estimate_5d_pct = round(max(-15.0, min(15.0, conviction * vol_5d)), 1)
    estimate_5d_price = round(base["current_price"] * (1 + estimate_5d_pct / 100), 2)

    base.update({
        "earnings_today": earnings_today,
        "next_earnings_str": next_earnings_str,
        "days_to_earnings": days_to_earnings,
        "news_sentiment": news_sentiment,
        "news_sentiment_emoji": news_emoji,
        "news_sentiment_label": news_label,
        "upside_52w": upside_52w,
        "daily_estimate_pct": daily_estimate_pct,
        "estimate_5d_pct": estimate_5d_pct,
        "estimate_5d_price": estimate_5d_price,
        "score_10": score_10,
    })
    return base


def format_apr_card(d: dict, ai: dict) -> str:
    """Card professionale per /apr — singola azione con AI verdict."""
    chg = d["day_change_pct"]
    sign = "+" if chg >= 0 else ""
    chg_emoji = "📈" if chg >= 0 else "📉"

    verdict = (ai or {}).get("verdict", "")
    bullet1 = (ai or {}).get("bullet1", "")
    bullet2 = (ai or {}).get("bullet2", "")

    news_line = f"{d.get('news_sentiment_emoji', '⚪')} {d.get('news_sentiment_label', 'Neutre')}"

    daily_pct = d.get("daily_estimate_pct", 0.0)
    daily_sign = "+" if daily_pct >= 0 else ""

    upside = d.get("upside_52w", 0.0)
    score_10 = d.get("score_10", 5.0)

    lines = [
        f"<b>📈 {_h(d['ticker'])} — {_h(d['name'])}</b>",
        f"💵 ${d['current_price']:.2f}  {chg_emoji} {sign}{chg:.2f}%",
        "",
        f"📰 <b>Notizie:</b> {news_line}",
    ]

    # Earnings
    if d.get("earnings_today"):
        lines.append("⚠️ <b>EARNINGS OGGI!</b> Rischio aumentato.")
    elif d.get("next_earnings_str"):
        lines.append(f"📅 <b>Prossimi earnings:</b> {d['next_earnings_str']}")

    # AI verdict + motivazione
    if verdict:
        lines += ["", f"🎯 {_h(verdict)}"]
    bullets = [b for b in [bullet1, bullet2] if b]
    if bullets:
        lines += ["", "📋 <b>Motivazione:</b>"]
        for b in bullets:
            lines.append(f"• {_h(b)}")

    # Stima + target + rischio + score
    if upside > 0:
        target_line = f"📊 <b>Target 52w:</b> +{upside:.1f}% (max ${d.get('week_52_high', 0):.2f})"
    else:
        target_line = f"📊 <b>Target 52w:</b> Già ai massimi 52w 🏆"

    lines += [
        "",
        f"📊 <b>Oggi finora:</b> {daily_sign}{daily_pct:.1f}%",
        target_line,
        f"{d.get('risk_emoji', '🟡')} <b>Rischio:</b> {d.get('risk_level', 'Medio')} (volatilità {d.get('volatility', 0):.0f}%)",
        f"⭐ <b>Score:</b> {score_10}/10",
    ]

    # Indicatori tecnici
    lines += [
        "",
        "📐 <b>Indicatori tecnici:</b>",
        f"  • RSI (14): {d['rsi']:.0f}/100",
    ]
    for label, val in [("Settimana", d.get("week_return")), ("Mese", d.get("month_return")), ("Anno", d.get("ytd_return"))]:
        if val is not None:
            lines.append(f"  • {label}: {'+'if val>=0 else''}{val:.1f}%")
    if d.get("sma_50"):
        lines.append(
            f"  • Trend 50gg: {'📈 rialzista' if d['current_price'] > d['sma_50'] else '📉 ribassista'}"
        )
    if d.get("pe_ratio"):
        lines.append(f"  • P/E: {d['pe_ratio']:.1f}x")

    if d.get("sector"):
        lines += ["", f"🏭 <b>Settore:</b> {_h(d['sector'])}"]

    lines.append(f"\n<i>Dati: Yahoo Finance | AI: Groq Llama 70B</i>")
    return "\n".join(lines)


def format_morning_card(d: dict, ai: dict, rank: int) -> str:
    """Formatta una card azione per il report mattutino professionale."""
    chg = d["day_change_pct"]
    sign = "+" if chg >= 0 else ""
    chg_emoji = "📈" if chg >= 0 else "📉"

    news_line = f"{d.get('news_sentiment_emoji', '⚪')} {d.get('news_sentiment_label', 'Neutre')}"

    daily_pct = d.get("daily_estimate_pct", 0.0)
    daily_sign = "+" if daily_pct >= 0 else ""

    upside = d.get("upside_52w", 0.0)
    score_10 = d.get("score_10", 5.0)

    verdict = (ai or {}).get("verdict", "")
    bullet1 = (ai or {}).get("bullet1", "")
    bullet2 = (ai or {}).get("bullet2", "")

    lines = [
        f"<b>{rank}. {_h(d['ticker'])} — {_h(d['name'])}</b>",
        f"💵 ${d['current_price']:.2f}  {chg_emoji} {sign}{chg:.2f}%",
        "",
        f"📰 Notizie: {news_line}",
    ]
    if d.get("earnings_today"):
        lines.append("⚠️ <b>EARNINGS oggi!</b> Rischio aumentato.")

    if verdict:
        lines += ["", f"🎯 {_h(verdict)}"]

    bullets = [b for b in [bullet1, bullet2] if b]
    if bullets:
        lines += ["", "📋 <b>Motivazione:</b>"]
        for b in bullets:
            lines.append(f"• {_h(b)}")

    # Target: positivo = upside, negativo = già sui massimi
    if upside > 0:
        target_line = f"📊 <b>Target:</b> +{upside:.1f}%"
    else:
        target_line = f"📊 <b>Target:</b> Ai massimi 52w 🏆"

    lines += [
        "",
        f"📊 <b>Oggi finora:</b> {daily_sign}{daily_pct:.1f}%",
        target_line,
        f"{d.get('risk_emoji', '🟡')} {d.get('risk_level', 'Medio')}",
        f"⭐ {score_10}/10",
    ]
    return "\n".join(lines)


def format_scan_card(d: dict, ai: dict, rank: int) -> str:
    """Card compatta per /analisi — tutto in un solo messaggio."""
    chg = d["day_change_pct"]
    sign = "+" if chg >= 0 else ""
    chg_emoji = "📈" if chg >= 0 else "📉"

    verdict = (ai or {}).get("verdict", "")
    bullet1 = (ai or {}).get("bullet1", "")

    news_line = f"{d.get('news_sentiment_emoji', '⚪')} {d.get('news_sentiment_label', 'Neutre')}"

    if d.get("earnings_today"):
        earn_str = "⚠️ EARNINGS OGGI"
    elif d.get("next_earnings_str"):
        earn_str = f"📅 {d['next_earnings_str']}"
    else:
        earn_str = "📅 N/D"

    daily_pct = d.get("daily_estimate_pct", 0.0)
    daily_sign = "+" if daily_pct >= 0 else ""

    upside = d.get("upside_52w", 0.0)
    target_str = f"+{upside:.0f}% vs max 52w" if upside > 0 else "Già ai massimi 🏆"
    score_10 = d.get("score_10", 5.0)

    lines = [
        f"<b>{rank}. {_h(d['ticker'])} — {_h(d['name'])}</b>",
        f"💵 ${d['current_price']:.2f} {chg_emoji}{sign}{chg:.1f}% | {d.get('risk_emoji','🟡')} {d.get('risk_level','Medio')} | ⭐{score_10}/10",
        f"📰 {news_line}  {earn_str}",
    ]
    if verdict:
        lines.append(f"🎯 {_h(verdict)}")
    if bullet1:
        lines.append(f"• {_h(bullet1)}")
    lines.append(f"📊 Oggi finora: {daily_sign}{daily_pct:.1f}% | 📊 {target_str}")
    return "\n".join(lines)


# ─── Analisi lungo termine ───────────────────────────────────────────────────

def _cagr(hist, trading_days: int) -> float | None:
    """CAGR su N giorni di trading (approssimazione anni = days/252)."""
    if len(hist) < trading_days:
        return None
    p0 = float(hist["Close"].iloc[-trading_days])
    p1 = float(hist["Close"].iloc[-1])
    if p0 <= 0:
        return None
    years = trading_days / 252
    return ((p1 / p0) ** (1 / years) - 1) * 100


def _projection_basis(cagr_3y: float | None, cagr_1y: float | None) -> tuple[float, str]:
    """Sceglie il tasso annuo usato per le proiezioni multi-anno e ne dichiara la fonte,
    così un'ipotesi inventata non è mai indistinguibile da uno storico calcolato."""
    if cagr_3y is not None:
        return cagr_3y / 100, "CAGR 3 anni"
    if cagr_1y is not None:
        return cagr_1y / 100, "CAGR 1 anno"
    return 7.0 / 100, "ipotesi 7%/anno (storico insufficiente)"


def get_longterm_analysis(ticker: str) -> dict | None:
    """Rendimenti storici CAGR, proiezioni scenari e dati fondamentali pluriennali."""
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="5y")
        if hist.empty or len(hist) < 20:
            return None

        info = stock.info
        current_price = float(hist["Close"].iloc[-1])

        cagr_1y = _cagr(hist, 252)
        cagr_3y = _cagr(hist, 252 * 3)
        cagr_5y = _cagr(hist, 252 * 5)

        # Proiezione scenari a 3 anni
        base_rate, projection_basis = _projection_basis(cagr_3y, cagr_1y)
        proj = {}
        for label, mult, years in [
            ("1y_base", 1.0, 1), ("1y_bull", 1.5, 1), ("1y_bear", 0.4, 1),
            ("3y_base", 1.0, 3), ("3y_bull", 1.5, 3), ("3y_bear", 0.4, 3),
            ("5y_base", 1.0, 5), ("5y_bull", 1.5, 5), ("5y_bear", 0.4, 5),
        ]:
            r = base_rate * mult
            proj[label] = round(current_price * (1 + r) ** years, 2)

        # ETF specifico
        three_yr_avg = info.get("threeYearAverageReturn")
        five_yr_avg  = info.get("fiveYearAverageReturn")
        expense_ratio = info.get("expenseRatio")
        fund_family   = info.get("fundFamily")
        category      = info.get("category") or info.get("quoteType")

        # Fondamentali azioni
        target_mean = info.get("targetMeanPrice")
        target_high = info.get("targetHighPrice")
        target_low  = info.get("targetLowPrice")
        rev_growth  = info.get("revenueGrowth")
        eps_growth  = info.get("earningsGrowth")
        fwd_pe      = info.get("forwardPE")

        upside_target = None
        if target_mean and current_price > 0:
            upside_target = round((target_mean - current_price) / current_price * 100, 1)

        return {
            "ticker": ticker.upper(),
            "current_price": current_price,
            "currency": info.get("currency", "USD"),
            "cagr_1y": round(cagr_1y, 1) if cagr_1y is not None else None,
            "cagr_3y": round(cagr_3y, 1) if cagr_3y is not None else None,
            "cagr_5y": round(cagr_5y, 1) if cagr_5y is not None else None,
            "projections": proj,
            "projection_basis": projection_basis,
            "target_mean": target_mean,
            "target_high": target_high,
            "target_low": target_low,
            "upside_target": upside_target,
            "revenue_growth": round(rev_growth * 100, 1) if rev_growth else None,
            "eps_growth":     round(eps_growth * 100, 1) if eps_growth else None,
            "forward_pe": round(fwd_pe, 1) if fwd_pe else None,
            # ETF
            "three_yr_avg": round(three_yr_avg * 100, 1) if three_yr_avg else None,
            "five_yr_avg":  round(five_yr_avg  * 100, 1) if five_yr_avg  else None,
            "expense_ratio": round(expense_ratio * 100, 2) if expense_ratio else None,
            "fund_family":  fund_family,
            "category":     category,
        }
    except Exception as e:
        print(f"[longterm] Errore {ticker}: {e}")
        return None


def format_report_line(d: dict) -> str:
    p = d["current_price"]
    chg = d["day_change_pct"]
    emoji = "📈" if chg >= 0 else "📉"
    sign = "+" if chg >= 0 else ""
    return f"{emoji} <b>{_h(d['ticker'])}</b>  {p:.2f} {d['currency']} ({sign}{chg:.1f}%)  {d['risk_emoji']}"
