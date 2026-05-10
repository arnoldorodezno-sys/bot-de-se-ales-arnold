"""
signal_config.py
================
Configuración del Bot de Señales SMC.
Solo envía señales de CALIDAD (score >= 7.0).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================================================
# CREDENCIALES
# ==========================================================================
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ==========================================================================
# SÍMBOLOS (10 más importantes con utilidad real)
# ==========================================================================
SYMBOLS = [
    "BTCUSDT",    # Bitcoin - La más importante
    "ETHUSDT",    # Ethereum - Smart contracts
    "BNBUSDT",    # Binance Coin - Ecosistema Binance
    "SOLUSDT",    # Solana - Blockchain rápida
    "XRPUSDT",    # XRP - Pagos internacionales
    "ADAUSDT",    # Cardano - Blockchain académica
    "LTCUSDT",    # Litecoin - Pagos rápidos
    "DOTUSDT",    # Polkadot - Interoperabilidad
    "AVAXUSDT",   # Avalanche - DeFi y dApps
    "LINKUSDT",   # Chainlink - Oráculos blockchain
]

# ==========================================================================
# TIMEFRAMES
# ==========================================================================
TIMEFRAMES = ["15m", "1h", "4h"]
LOOP_INTERVAL_SECONDS = 60  # Evaluar cada 60 segundos

# ==========================================================================
# PARÁMETROS INDICADORES (MISMOS QUE BOT ACTUAL)
# ==========================================================================

# EMAs
EMA_FAST = 7
EMA_MID = 25
EMA_SLOW = 99

# Supertrend
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0

# Bollinger Bands
BB_PERIOD = 20
BB_STD = 2.0
BB_PROXIMITY = 0.02

# RSI
RSI_FAST = 6
RSI_SLOW = 14
RSI_LONG_MIN = 35.0
RSI_LONG_MAX = 60.0
RSI_SHORT_MIN = 40.0
RSI_SHORT_MAX = 65.0
RSI_FAST_LONG_BLOCK = 70.0
RSI_FAST_SHORT_BLOCK = 30.0

# Volumen
VOL_MA_SLOW = 10
VOL_SWEEP_MULTIPLIER = 1.5

# SMC
SWEEP_LOOKBACK = 8
FIB_TOLERANCE = 0.005
FVG_MIN_SIZE_PCT = 0.001

# ==========================================================================
# CALIDAD DE SEÑALES
# ==========================================================================
SCORE_MIN_SIGNAL = 7.0      # Solo señales con score >= 7.0
SCORE_PERFECT = 9.0         # Señal perfecta
SCORE_GOOD = 7.0            # Señal buena
MIN_RR = 2.0                # R:R mínimo 1:2
MAX_SL_PCT = 0.015          # SL máximo 1.5%

# ==========================================================================
# COOLDOWN (evitar señales repetidas)
# ==========================================================================
SIGNAL_COOLDOWN_MINUTES = 240   # No repetir señal del mismo símbolo en 4 horas

# ==========================================================================
# LOGGING
# ==========================================================================
LOG_PATH = "logs/signals.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
