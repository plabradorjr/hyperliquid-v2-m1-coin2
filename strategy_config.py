import ta  # type: ignore
import pandas as pd  # type: ignore
import numpy as np

from hyperliquid_client import my_print  # type: ignore

# Trading parameters
params = {
    "symbol": "0G/USDC:USDC",
    "timeframe": "5m",
    "position_size_pct": 150.0,
    "leverage": 3,
    "margin_mode": "isolated",  # "isolated" or "cross"
    # Take-Profit and Stop-Loss settings
    "tp_pct": 9,
    "tp_size_pct": 60,
    "sl_pct": 8,
    # Trailing Stop-Loss settings (set to 0 to disable)
    "trailing_sl_pct": 8,
    # EMA settings
    "ema_fast": 30,
    "ema_slow": 40,
    # Choppiness Index settings
    "chop_length": 21,
    "chop_threshold": 1000,  # set to 1000 to disable choppiness filter
}

# Trading conditions to ignore
ignore_longs = False
ignore_shorts = True
ignore_exit = False
ignore_tp = False
ignore_sl = False
ignore_trailing_sl = False

# Verbosity
verbose = True

# Define Technical Indicators


def choppiness_index(high,
                     low,
                     close,
                     window=14,
                     min_periods=None,
                     clip=True):
    """Compute the Choppiness Index (CHOP) without external deps.

    CHOP = 100 * log10( sum(TR, n) / (max(High_n) - min(Low_n)) ) / log10(n)
    where TR = max( High-Low, abs(High - prevClose), abs(Low - prevClose) )
    """
    if window is None or window < 2:
        raise ValueError("window must be an integer >= 2")

    if min_periods is None:
        min_periods = window

    df = pd.concat(
        {"high": pd.Series(high), "low": pd.Series(low),
         "close": pd.Series(close)},
        axis=1,
    )

    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)

    tr = pd.concat([
        (h - l),
        (h - prev_c).abs(),
        (l - prev_c).abs(),
    ], axis=1).max(axis=1)

    tr_sum = tr.rolling(window=window, min_periods=min_periods).sum()
    hh = h.rolling(window=window, min_periods=min_periods).max()
    ll = l.rolling(window=window, min_periods=min_periods).min()

    denom = (hh - ll)
    denom = denom.where(denom > 0)

    chop = 100.0 * np.log10(tr_sum / denom) / np.log10(float(window))
    if clip:
        chop = chop.clip(lower=0, upper=100)

    return chop.astype(float)


# check https://technical-analysis-library-in-python.readthedocs.io/en/latest/ta.html
def compute_indicators(data):
    """Compute technical indicators"""

    # Exponential Moving Averages
    data['EMA_fast'] = ta.trend.ema_indicator(
        data['close'], window=params["ema_fast"])
    data['EMA_slow'] = ta.trend.ema_indicator(
        data['close'], window=params["ema_slow"])

    # Choppiness Index (local implementation)
    data['CHOP'] = choppiness_index(
        data['high'], data['low'], data['close'], window=params["chop_length"])

    # data['ATR'] = ta.volatility.average_true_range(data['high'], data['low'], data['close'], window=params["..."])

    # data['EMAf'] = ta.trend.ema_indicator(data['close'], params["..."])
    # data['EMAs'] = ta.trend.ema_indicator(data['close'], params["..."])

    # MACD = ta.trend.MACD(data['close'], window_slow=params["..."], window_fast=params["..."], window_sign=params["..."])
    # data['MACD'] = MACD.macd()
    # data['MACD_histo'] = MACD.macd_diff()
    # data['MACD_signal'] = MACD.macd_signal()

    # BB = ta.volatility.BollingerBands(close=data['close'], window=params["..."], window_dev=params["..."])
    # data["BB_lower"] = BB.bollinger_lband()
    # data["BB_upper"] = BB.bollinger_hband()
    # data["BB_avg"] = BB.bollinger_mavg()
    return data


def fast_ema_is_bullish(row):
    """Check bullish trend based on EMA cross (EMA_fast > EMA_slow)."""
    my_print(f"Checking fast EMA signal...", verbose)
    # print bullish if ema fast > ema slow, else print bearish
    if row["EMA_fast"] > row["EMA_slow"]:
        my_print("--Bullish", verbose)
    else:
        my_print("--Bearish", verbose)
    return row["EMA_fast"] > row["EMA_slow"]


def is_choppy_market(row):
    """Check if the market is choppy based on Choppiness Index."""
    my_print(f"Checking choppy market...", verbose)
    my_print(f"--CHOP value: {row['CHOP']:.2f}", verbose)
    if row["CHOP"] > params["chop_threshold"]:
        my_print("--Market is choppy, skipping trade.", verbose)
    return row["CHOP"] > params["chop_threshold"]

################################################################################
# Long Position Rules


def check_long_entry_condition(row, previous_candle):
    my_print(f"Checking long entry...", verbose)
    return fast_ema_is_bullish(row) and not is_choppy_market(row)


def check_long_exit_condition(row, previous_candle):
    my_print(f"Checking long exit...", verbose)
    return not fast_ema_is_bullish(row)


def compute_long_tp_level(price):
    return price * (1 + params["tp_pct"] / 100)


def compute_long_sl_level(price):
    return price * (1 - params["sl_pct"] / 100)


def compute_trailing_long_sl_level(price):
    """Compute trailing SL for long relative to current price.

    If trailing_sl_pct is 0 or missing, returns None.
    """
    trailing = float(params.get("trailing_sl_pct", 0))
    if trailing <= 0:
        return None
    return price * (1 - trailing / 100)

################################################################################
# Short Position Rules


def check_short_entry_condition(row, previous_candle):
    my_print(f"Checking short entry...", verbose)
    return not fast_ema_is_bullish(row) and not is_choppy_market(row)


def check_short_exit_condition(row, previous_candle):
    my_print(f"Checking short exit...", verbose)
    return fast_ema_is_bullish(row)


def compute_short_tp_level(price):
    return price * (1 - params["tp_pct"] / 100)


def compute_short_sl_level(price):
    return price * (1 + params["sl_pct"] / 100)


def compute_trailing_short_sl_level(price):
    """Compute trailing SL for short relative to current price.

    If trailing_sl_pct is 0 or missing, returns None.
    """
    trailing = float(params.get("trailing_sl_pct", 0))
    if trailing <= 0:
        return None
    return price * (1 + trailing / 100)

# Define position sizing rules


def calculate_position_size(balance):
    return balance * params["position_size_pct"] / 100
