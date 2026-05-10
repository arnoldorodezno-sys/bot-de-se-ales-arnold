"""
signal_main.py
==============
Bot de Señales SMC.
Analiza 10 símbolos cada 60 segundos.
Envía SOLO señales de CALIDAD (score >= 7.0) a Telegram.
NO ejecuta trades automáticamente.
"""

import logging
import logging.handlers
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional
import requests
import pandas as pd
from binance.client import Client
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import signal_config as cfg

# ==========================================================================
# LOGGING
# ==========================================================================
def setup_logging():
    Path("logs").mkdir(exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, cfg.LOG_LEVEL))
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.handlers.RotatingFileHandler(
        cfg.LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logging.getLogger("SignalBot")


logger = setup_logging()

# ==========================================================================
# TELEGRAM
# ==========================================================================
def send_telegram(message: str) -> None:
    """Envía mensaje a Telegram."""
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.warning("Telegram no configurado")
        return
    try:
        url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": cfg.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Error Telegram: {e}")


# ==========================================================================
# INDICADORES
# ==========================================================================
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Añade todos los indicadores al DataFrame."""
    df = df.copy()

    # EMAs
    df["ema_7"] = df["close"].ewm(span=cfg.EMA_FAST, adjust=False).mean()
    df["ema_25"] = df["close"].ewm(span=cfg.EMA_MID, adjust=False).mean()
    df["ema_99"] = df["close"].ewm(span=cfg.EMA_SLOW, adjust=False).mean()

    # RSI
    for period, col in [(cfg.RSI_FAST, "rsi_6"), (cfg.RSI_SLOW, "rsi_14")]:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))
        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        df[col] = 100 - (100 / (1 + rs))

    # Bollinger Bands
    df["bb_mid"] = df["close"].rolling(cfg.BB_PERIOD).mean()
    bb_std = df["close"].rolling(cfg.BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + cfg.BB_STD * bb_std
    df["bb_lower"] = df["bb_mid"] - cfg.BB_STD * bb_std

    # Supertrend
    df = add_supertrend(df)

    # Volumen MA
    df["vol_ma_10"] = df["volume"].rolling(cfg.VOL_MA_SLOW).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma_10"]

    return df


def add_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula Supertrend(10, 3)."""
    import numpy as np
    period = cfg.SUPERTREND_PERIOD
    multiplier = cfg.SUPERTREND_MULTIPLIER

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    hl2 = (df["high"] + df["low"]) / 2
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    upper_band = upper_basic.copy()
    lower_band = lower_basic.copy()

    for i in range(1, len(df)):
        if pd.isna(upper_basic.iloc[i]):
            continue
        if upper_basic.iloc[i] < upper_band.iloc[i-1] or df["close"].iloc[i-1] > upper_band.iloc[i-1]:
            upper_band.iloc[i] = upper_basic.iloc[i]
        else:
            upper_band.iloc[i] = upper_band.iloc[i-1]
        if lower_basic.iloc[i] > lower_band.iloc[i-1] or df["close"].iloc[i-1] < lower_band.iloc[i-1]:
            lower_band.iloc[i] = lower_basic.iloc[i]
        else:
            lower_band.iloc[i] = lower_band.iloc[i-1]

    supertrend = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    for i in range(len(df)):
        if i == 0 or pd.isna(atr.iloc[i]):
            supertrend.iloc[i] = np.nan
            direction.iloc[i] = 1
            continue
        prev_st = supertrend.iloc[i-1]
        if pd.isna(prev_st):
            supertrend.iloc[i] = upper_band.iloc[i]
            direction.iloc[i] = -1
            continue
        if prev_st == upper_band.iloc[i-1]:
            if df["close"].iloc[i] > upper_band.iloc[i]:
                supertrend.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
            else:
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
        else:
            if df["close"].iloc[i] < lower_band.iloc[i]:
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
            else:
                supertrend.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1

    df["supertrend"] = supertrend
    df["supertrend_dir"] = direction
    return df


# ==========================================================================
# ANÁLISIS SMC
# ==========================================================================
def detect_liquidity_sweep(df: pd.DataFrame, direction: str) -> bool:
    """Detecta sweep de liquidez en últimas N velas."""
    if len(df) < cfg.SWEEP_LOOKBACK + 10:
        return False
    recent = df.iloc[-cfg.SWEEP_LOOKBACK:]
    prior = df.iloc[-(cfg.SWEEP_LOOKBACK + 20):-cfg.SWEEP_LOOKBACK]

    if direction == "LONG":
        prior_low = prior["low"].min()
        for _, row in recent.iterrows():
            if row["low"] < prior_low and row["close"] > prior_low:
                wick = row["close"] - row["low"]
                body = abs(row["close"] - row["open"])
                if body > 0 and wick > body * 1.5:
                    return True
    else:
        prior_high = prior["high"].max()
        for _, row in recent.iterrows():
            if row["high"] > prior_high and row["close"] < prior_high:
                wick = row["high"] - row["close"]
                body = abs(row["close"] - row["open"])
                if body > 0 and wick > body * 1.5:
                    return True
    return False


def detect_order_block(df: pd.DataFrame, direction: str, price: float) -> Optional[Dict]:
    """Detecta Order Block no mitigado."""
    if len(df) < 50:
        return None
    sub = df.iloc[-50:].reset_index(drop=True)
    obs = []
    for i in range(2, len(sub) - 3):
        if direction == "LONG":
            if sub.loc[i, "close"] < sub.loc[i, "open"]:
                future_high = sub.loc[i+1:i+3, "high"].max()
                if future_high > sub.loc[i, "high"] * 1.002:
                    obs.append({
                        "low": float(sub.loc[i, "low"]),
                        "high": float(sub.loc[i, "high"]),
                        "mid": float((sub.loc[i, "low"] + sub.loc[i, "high"]) / 2),
                        "age": len(sub) - i,
                    })
        else:
            if sub.loc[i, "close"] > sub.loc[i, "open"]:
                future_low = sub.loc[i+1:i+3, "low"].min()
                if future_low < sub.loc[i, "low"] * 0.998:
                    obs.append({
                        "low": float(sub.loc[i, "low"]),
                        "high": float(sub.loc[i, "high"]),
                        "mid": float((sub.loc[i, "low"] + sub.loc[i, "high"]) / 2),
                        "age": len(sub) - i,
                    })

    valid = []
    for ob in obs:
        if direction == "LONG" and price > ob["mid"]:
            valid.append(ob)
        elif direction == "SHORT" and price < ob["mid"]:
            valid.append(ob)

    return min(valid, key=lambda x: x["age"]) if valid else None


def detect_fvg(df: pd.DataFrame, direction: str) -> bool:
    """Detecta Fair Value Gap."""
    if len(df) < 10:
        return False
    sub = df.iloc[-20:].reset_index(drop=True)
    for i in range(len(sub) - 2):
        c1, c3 = sub.iloc[i], sub.iloc[i+2]
        if direction == "LONG" and c3["low"] > c1["high"]:
            gap = (c3["low"] - c1["high"]) / c1["high"]
            if gap > cfg.FVG_MIN_SIZE_PCT:
                return True
        elif direction == "SHORT" and c3["high"] < c1["low"]:
            gap = (c1["low"] - c3["high"]) / c1["low"]
            if gap > cfg.FVG_MIN_SIZE_PCT:
                return True
    return False


def detect_bos(df: pd.DataFrame, direction: str) -> bool:
    """Detecta Break of Structure."""
    if len(df) < 20:
        return False
    sub = df.iloc[-20:]
    if direction == "LONG":
        prior_high = sub.iloc[:-3]["high"].max()
        recent_high = sub.iloc[-3:]["high"].max()
        return recent_high > prior_high * 1.0005
    prior_low = sub.iloc[:-3]["low"].min()
    recent_low = sub.iloc[-3:]["low"].min()
    return recent_low < prior_low * 0.9995


def analyze_symbol(
    client: Client,
    symbol: str,
    direction: str,
) -> Optional[Dict]:
    """
    Analiza un símbolo y retorna señal si cumple criterios de calidad.
    """
    try:
        # Descargar datos
        dfs = {}
        for tf in cfg.TIMEFRAMES:
            raw = client.futures_klines(symbol=symbol, interval=tf, limit=500)
            df = pd.DataFrame(
                raw,
                columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "quote_vol", "trades",
                    "taker_buy_base", "taker_buy_quote", "ignore",
                ],
            )
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = df[col].astype(float)
            dfs[tf] = add_indicators(df)

        df_15m = dfs["15m"]
        df_1h = dfs["1h"]
        df_4h = dfs["4h"]
        price = float(df_15m["close"].iloc[-1])

        score = 0.0
        details = []
        confluences = []

        # ── FILTRO 1: Tendencia macro ──────────────────────────────────
        expected_st = 1 if direction == "LONG" else -1
        st_count = sum(
            1 for df in (df_15m, df_1h, df_4h)
            if df["supertrend_dir"].iloc[-1] == expected_st
        )

        def emas_aligned(df, dir_):
            last = df.iloc[-1]
            if dir_ == "LONG":
                return last["ema_7"] > last["ema_25"] > last["ema_99"]
            return last["ema_7"] < last["ema_25"] < last["ema_99"]

        if st_count >= 2 and emas_aligned(df_4h, direction) and emas_aligned(df_1h, direction):
            score += 1.5
            details.append("✅ Tendencia macro")
            confluences.append("TENDENCIA")
        else:
            details.append("❌ Tendencia macro")
            return None  # Crítico

        # ── FILTRO 2: EMA 99 como soporte/resistencia ─────────────────
        last_1h = df_1h.iloc[-1]
        if direction == "LONG" and price > last_1h["ema_99"]:
            score += 1.5
            details.append("✅ Precio sobre EMA99")
        elif direction == "SHORT" and price < last_1h["ema_99"]:
            score += 1.5
            details.append("✅ Precio bajo EMA99")
        else:
            details.append("❌ EMA99")
            return None  # Crítico

        # ── FILTRO 3: Liquidity Sweep ─────────────────────────────────
        sweep = detect_liquidity_sweep(df_15m, direction)
        if sweep:
            score += 1.5
            details.append("✅ Liquidity Sweep")
            confluences.append("SWEEP")
        else:
            details.append("❌ Sin sweep")
            return None  # Crítico

        # ── FILTRO 4: RSI ─────────────────────────────────────────────
        rsi_14_1h = df_1h["rsi_14"].iloc[-1]
        rsi_ok = False
        if direction == "LONG":
            rsi_ok = cfg.RSI_LONG_MIN <= rsi_14_1h <= cfg.RSI_LONG_MAX
            rsi_blocked = any(
                df["rsi_6"].iloc[-1] > cfg.RSI_FAST_LONG_BLOCK
                for df in (df_15m, df_1h, df_4h)
            )
        else:
            rsi_ok = cfg.RSI_SHORT_MIN <= rsi_14_1h <= cfg.RSI_SHORT_MAX
            rsi_blocked = any(
                df["rsi_6"].iloc[-1] < cfg.RSI_FAST_SHORT_BLOCK
                for df in (df_15m, df_1h, df_4h)
            )

        if rsi_ok and not rsi_blocked:
            score += 1.5
            details.append(f"✅ RSI OK ({rsi_14_1h:.1f})")
        else:
            details.append(f"❌ RSI ({rsi_14_1h:.1f})")
            return None  # Crítico

        # ── FILTRO 5: Order Block ─────────────────────────────────────
        ob = detect_order_block(df_15m, direction, price)
        if ob:
            score += 1.5
            details.append(f"✅ Order Block @ {ob['mid']:.4f}")
            confluences.append("OB")
            entry_price = ob["mid"]
        else:
            details.append("❌ Sin Order Block")
            entry_price = price

        # ── FILTRO 6: Confluencias extra ──────────────────────────────
        last_15m = df_15m.iloc[-1]
        conf_count = 0

        # Bollinger Bands
        if direction == "LONG" and price <= last_15m["bb_lower"] * 1.02:
            conf_count += 1
            confluences.append("BOLL")
        elif direction == "SHORT" and price >= last_15m["bb_upper"] * 0.98:
            conf_count += 1
            confluences.append("BOLL")

        # FVG
        if detect_fvg(df_15m, direction):
            conf_count += 1
            confluences.append("FVG")

        # BOS
        if detect_bos(df_15m, direction):
            conf_count += 1
            confluences.append("BOS")

        # Volumen
        if last_15m.get("vol_ratio", 0) > cfg.VOL_SWEEP_MULTIPLIER:
            conf_count += 1
            confluences.append("VOL")

        if conf_count >= 2:
            score += 1.5
            details.append(f"✅ {conf_count} confluencias extra")
        else:
            details.append(f"⚠️ Solo {conf_count} confluencias extra")

        # ── FILTRO 7: Risk/Reward ─────────────────────────────────────
        if direction == "LONG":
            sl_price = df_15m["low"].iloc[-cfg.SWEEP_LOOKBACK:].min() * 0.998
            sl_pct = (entry_price - sl_price) / entry_price
            tp1 = entry_price + (entry_price - sl_price) * 2.0
            tp2 = entry_price + (entry_price - sl_price) * 3.5
        else:
            sl_price = df_15m["high"].iloc[-cfg.SWEEP_LOOKBACK:].max() * 1.002
            sl_pct = (sl_price - entry_price) / entry_price
            tp1 = entry_price - (sl_price - entry_price) * 2.0
            tp2 = entry_price - (sl_price - entry_price) * 3.5

        sl_dist = abs(entry_price - sl_price)
        tp1_dist = abs(tp1 - entry_price)
        rr = tp1_dist / sl_dist if sl_dist > 0 else 0

        if sl_pct <= cfg.MAX_SL_PCT and rr >= cfg.MIN_RR:
            score += 1.5
            details.append(f"✅ R:R 1:{rr:.1f}")
        else:
            details.append(f"❌ R:R 1:{rr:.1f}")
            return None  # Crítico

        # ── SCORE FINAL ───────────────────────────────────────────────
        score = min(score, 10.0)
        if score < cfg.SCORE_MIN_SIGNAL:
            logger.debug(f"{symbol} {direction}: score={score:.1f} < mínimo {cfg.SCORE_MIN_SIGNAL}")
            return None

        tp1_pct = abs(tp1 - entry_price) / entry_price * 100
        tp2_pct = abs(tp2 - entry_price) / entry_price * 100
        sl_pct_display = sl_pct * 100

        return {
            "symbol": symbol,
            "direction": direction,
            "score": round(score, 1),
            "entry": entry_price,
            "sl": sl_price,
            "tp1": tp1,
            "tp2": tp2,
            "sl_pct": sl_pct_display,
            "tp1_pct": tp1_pct,
            "tp2_pct": tp2_pct,
            "rr": rr,
            "confluences": confluences,
            "details": details,
            "st_count": st_count,
        }

    except Exception as e:
        logger.error(f"Error analizando {symbol} {direction}: {e}")
        return None


# ==========================================================================
# FORMATO DE SEÑAL TELEGRAM
# ==========================================================================
def format_signal(signal: Dict) -> str:
    """Genera mensaje bonito para Telegram."""
    emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    score = signal["score"]

    if score >= cfg.SCORE_PERFECT:
        quality = "🏆 SEÑAL PERFECTA"
    elif score >= 8.0:
        quality = "⭐ SEÑAL EXCELENTE"
    else:
        quality = "✅ SEÑAL BUENA"

    conf_str = " + ".join(signal["confluences"]) if signal["confluences"] else "N/A"

    msg = (
        f"{emoji} <b>{signal['direction']}</b> | <b>{signal['symbol']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{quality}\n"
        f"📊 Score: <b>{score}/10</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entrada: <b>${signal['entry']:,.4f}</b>\n"
        f"🛑 Stop Loss: <b>${signal['sl']:,.4f}</b> (-{signal['sl_pct']:.2f}%)\n"
        f"🎯 TP1: <b>${signal['tp1']:,.4f}</b> (+{signal['tp1_pct']:.2f}%)\n"
        f"🎯 TP2: <b>${signal['tp2']:,.4f}</b> (+{signal['tp2_pct']:.2f}%)\n"
        f"📐 R:R → 1:{signal['rr']:.1f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 Confluencias: {conf_str}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Solo análisis técnico. No es consejo financiero.</i>"
    )
    return msg


# ==========================================================================
# BOT PRINCIPAL
# ==========================================================================
class SignalBot:
    """Bot de señales SMC para 10 símbolos."""

    def __init__(self):
        self.client = Client(
            api_key=cfg.BINANCE_API_KEY,
            api_secret=cfg.BINANCE_API_SECRET,
            testnet=False,
        )
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.running = False
        # Cooldown: {symbol: last_signal_time}
        self.last_signals: Dict[str, datetime] = {}
        logger.info("Bot de Señales SMC inicializado")

    def is_in_cooldown(self, symbol: str) -> bool:
        """Evita señales repetidas del mismo símbolo."""
        if symbol not in self.last_signals:
            return False
        elapsed = datetime.now(timezone.utc) - self.last_signals[symbol]
        return elapsed < timedelta(minutes=cfg.SIGNAL_COOLDOWN_MINUTES)

    def evaluate_all(self):
        """Evalúa todos los símbolos."""
        logger.info("=" * 50)
        logger.info(f"Evaluando {len(cfg.SYMBOLS)} símbolos...")
        signals_found = 0

        for symbol in cfg.SYMBOLS:
            if self.is_in_cooldown(symbol):
                logger.debug(f"{symbol}: en cooldown, skip")
                continue

            logger.info(f"Analizando {symbol}...")

            for direction in ("LONG", "SHORT"):
                signal = analyze_symbol(self.client, symbol, direction)
                if signal:
                    msg = format_signal(signal)
                    send_telegram(msg)
                    logger.info(
                        f"✅ SEÑAL: {symbol} {direction} "
                        f"score={signal['score']} R:R=1:{signal['rr']:.1f}"
                    )
                    self.last_signals[symbol] = datetime.now(timezone.utc)
                    signals_found += 1
                    break  # Solo 1 dirección por símbolo

        if signals_found == 0:
            logger.info("Sin señales de calidad en este ciclo")
        else:
            logger.info(f"{signals_found} señal(es) enviada(s)")

    def start(self):
        logger.info("=" * 60)
        logger.info("BOT DE SEÑALES SMC INICIANDO")
        logger.info(f"Símbolos: {cfg.SYMBOLS}")
        logger.info(f"Score mínimo: {cfg.SCORE_MIN_SIGNAL}/10")
        logger.info(f"Intervalo: cada {cfg.LOOP_INTERVAL_SECONDS}s")
        logger.info("=" * 60)

        self.running = True

        send_telegram(
            "📡 <b>Bot de Señales SMC iniciado</b>\n"
            f"Monitoreando {len(cfg.SYMBOLS)} símbolos\n"
            f"Score mínimo: {cfg.SCORE_MIN_SIGNAL}/10\n"
            "Solo señales de CALIDAD ✅"
        )

        # Evaluar inmediatamente al inicio
        self.evaluate_all()

        # Programar evaluaciones periódicas
        self.scheduler.add_job(
            self.evaluate_all,
            IntervalTrigger(seconds=cfg.LOOP_INTERVAL_SECONDS),
            id="evaluate_all",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()

        try:
            while self.running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            self.stop()

    def stop(self):
        logger.info("Deteniendo bot de señales...")
        self.running = False
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        send_telegram("🛑 Bot de Señales SMC detenido")
        logger.info("Bot detenido")


# ==========================================================================
# ENTRY POINT
# ==========================================================================
bot = None

def handle_signal(signum, frame):
    if bot:
        bot.stop()
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    bot = SignalBot()
    bot.start()
