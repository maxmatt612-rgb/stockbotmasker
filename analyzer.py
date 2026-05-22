import yfinance as yf
import pandas as pd
from datetime import datetime


# ─── Analisi standard ────────────────────────────────────────────────────────

def get_full_analysis(ticker: str) -> dict | None:
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="1y")
        if hist.empty or len(hist) < 5:
            return None

        info = stock.info
        fast = stock.fast_info

        current_price = fast.last_price
        prev_close = fast.previous_close
        if not current_price or not prev_close:
            return None

        day_change_pct = ((current_price - prev_close) / prev_close) * 100
        returns = hist["Close"].pct_change().dropna()
        volatility = returns.std() * (252 ** 0.5) * 100

        week_return = _pct(hist, -6) if len(hist) >= 6 else None
        month_return = _pct(hist, -22) if len(hist) >= 22 else None
        ytd_return = _pct(hist, 0)

        sma_20 = float(hist["Close"].rolling(20).mean().iloc[-1])
        sma_50 = float(hist["Close"].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else None

        delta = hist["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = float((100 - (100 / (1 + gain / loss))).iloc[-1])

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
            "week_52_high": fast.year_high,
            "week_52_low": fast.year_low,
            "volatility": volatility,
            "risk_level": risk_level,
            "risk_emoji": risk_emoji,
            "week_return": week_return,
            "month_return": month_return,
            "ytd_return": ytd_return,
            "rsi": rsi,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "signals": signals,
            "news": news_titles,
            "beta": info.get("beta"),
            "sector": info.get("sector"),
            "pe_ratio": info.get("trailingPE"),
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

        delta = hist["Close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = float((100 - (100 / (1 + gain / loss))).iloc[-1])

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

def scan_cheap_stocks(max_price: float = 20.0, top_n: int = 10) -> list:
    from config import REVOLUT_UNIVERSE

    risultati = []
    chunks = [REVOLUT_UNIVERSE[i:i+50] for i in range(0, len(REVOLUT_UNIVERSE), 50)]

    for chunk in chunks:
        try:
            raw = yf.download(
                tickers=chunk,
                period="1mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if raw.empty:
                continue

            # Normalizza colonne (batch vs singolo ticker)
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
                    if current_price <= 0 or current_price > max_price:
                        continue

                    prev_close = float(close.iloc[-2])
                    day_change_pct = ((current_price - prev_close) / prev_close) * 100

                    # RSI
                    if len(close) >= 15:
                        delta = close.diff()
                        gain = delta.clip(lower=0).rolling(14).mean()
                        loss = (-delta.clip(upper=0)).rolling(14).mean()
                        rs = gain / loss
                        rsi = float((100 - (100 / (1 + rs))).iloc[-1])
                        if pd.isna(rsi):
                            rsi = 50.0
                    else:
                        rsi = 50.0

                    # Volume ratio
                    vol_ratio = 1.0
                    if len(volume) >= 5:
                        avg_vol = float(volume.rolling(10).mean().iloc[-1])
                        if avg_vol > 0:
                            vol_ratio = float(volume.iloc[-1]) / avg_vol

                    # Media mobile 20gg
                    above_sma20 = False
                    if len(close) >= 20:
                        sma20 = float(close.rolling(20).mean().iloc[-1])
                        above_sma20 = current_price > sma20

                    # Punteggio momento
                    score = 0
                    if rsi < 35:      score += 3
                    elif rsi < 45:    score += 2
                    elif rsi > 70:    score -= 3
                    elif rsi > 60:    score -= 1

                    if day_change_pct > 3:    score += 3
                    elif day_change_pct > 1:  score += 2
                    elif day_change_pct > 0:  score += 1
                    elif day_change_pct < -3: score -= 2

                    if vol_ratio > 2.5:   score += 3
                    elif vol_ratio > 1.5: score += 2
                    elif vol_ratio > 1.2: score += 1

                    if above_sma20: score += 1

                    # Rischio
                    returns = close.pct_change().dropna()
                    volatility = float(returns.std() * (252 ** 0.5) * 100) if len(returns) > 1 else 50.0
                    if volatility < 30:
                        risk_emoji = "🟢"
                    elif volatility < 60:
                        risk_emoji = "🟡"
                    else:
                        risk_emoji = "🔴"

                    # Normalizza score grezzo (-5..10) → 0-10
                    score_10 = round(min(10.0, max(0.0, (score + 5) / 15 * 10)), 1)

                    risultati.append({
                        "ticker": ticker,
                        "current_price": current_price,
                        "day_change_pct": day_change_pct,
                        "rsi": rsi,
                        "vol_ratio": vol_ratio,
                        "score": score,
                        "score_10": score_10,
                        "risk_emoji": risk_emoji,
                        "volatility": volatility,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[scan] Errore chunk: {e}")
            continue

    risultati.sort(key=lambda x: x["score"], reverse=True)
    return risultati[:top_n]


# ─── Analisi arricchita (report mattutino) ───────────────────────────────────

def get_enriched_analysis(ticker: str) -> dict | None:
    """Full analysis + earnings (oggi e prossimi), news sentiment, 52w upside, daily estimate, score."""
    base = get_full_analysis(ticker)
    if not base:
        return None

    # ── Earnings oggi + prossima data earnings ──
    earnings_today = False
    next_earnings_str = None
    try:
        stock = yf.Ticker(ticker.upper())
        cal = stock.calendar
        today = datetime.now().date()
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

    # ── Stima giornaliera = variazione attuale ──
    daily_estimate_pct = base["day_change_pct"]
    daily_estimate_price = base["current_price"] * (1 + daily_estimate_pct / 100)

    # ── Score 0-10 basato su RSI, variazione e trend ──
    rsi = base.get("rsi", 50.0)
    chg = base.get("day_change_pct", 0.0)
    sma_20 = base.get("sma_20")
    price = base.get("current_price", 0.0)
    score_raw = 0
    if rsi < 35:      score_raw += 3
    elif rsi < 45:    score_raw += 2
    elif rsi > 70:    score_raw -= 3
    elif rsi > 60:    score_raw -= 1
    if chg > 3:       score_raw += 3
    elif chg > 1:     score_raw += 2
    elif chg > 0:     score_raw += 1
    elif chg < -3:    score_raw -= 2
    if sma_20 and price > sma_20:
        score_raw += 1
    score_10 = round(min(10.0, max(0.0, (score_raw + 5) / 15 * 10)), 1)

    base.update({
        "earnings_today": earnings_today,
        "next_earnings_str": next_earnings_str,
        "news_sentiment": news_sentiment,
        "news_sentiment_emoji": news_emoji,
        "news_sentiment_label": news_label,
        "upside_52w": upside_52w,
        "daily_estimate_pct": daily_estimate_pct,
        "daily_estimate_price": daily_estimate_price,
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
    daily_price = d.get("daily_estimate_price", d["current_price"])
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
        f"📅 <b>Stima oggi:</b> {daily_sign}{daily_pct:.1f}% → ${daily_price:.2f}",
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
    daily_price = d.get("daily_estimate_price", d["current_price"])
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
        f"📅 <b>Stima oggi:</b> {daily_sign}{daily_pct:.1f}% → ${daily_price:.2f}",
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
    daily_price = d.get("daily_estimate_price", d["current_price"])
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
    lines.append(f"📅 Stima: {daily_sign}{daily_pct:.1f}% → ${daily_price:.2f} | 📊 {target_str}")
    return "\n".join(lines)


def format_report_line(d: dict) -> str:
    p = d["current_price"]
    chg = d["day_change_pct"]
    emoji = "📈" if chg >= 0 else "📉"
    sign = "+" if chg >= 0 else ""
    return f"{emoji} <b>{_h(d['ticker'])}</b>  {p:.2f} {d['currency']} ({sign}{chg:.1f}%)  {d['risk_emoji']}"
