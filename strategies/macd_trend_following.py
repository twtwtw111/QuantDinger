# ── 沙箱注入变量（由平台运行时提供，VSCode 类型提示用）──
import pandas as pd   # noqa: F401
import numpy as np    # noqa: F401
df: pd.DataFrame      # noqa: F821
params: dict          # noqa: F821

my_indicator_name = "MACD 趋势跟随策略"
my_indicator_description = (
    "MACD 金叉做多，死叉平仓，成交量确认 + EMA200 趋势过滤。"
    "适合趋势行情，推荐 BTC/USDT，1H 或 4H。"
)

# @param fast_len    int   12   MACD 快线 EMA 周期
# @param slow_len    int   26   MACD 慢线 EMA 周期
# @param signal_len  int   9    MACD 信号线 EMA 周期
# @param vol_mult    float 1.2  成交量确认倍数（当前量 > N 倍均量才入场）
# @param vol_ma_len  int   20   成交量均线周期
# @param ema_len     int   200  趋势过滤 EMA 周期

# @strategy stopLossPct           0.025
# @strategy takeProfitPct         0.12
# @strategy trailingEnabled       false
# @strategy entryPct              0.5
# @strategy tradeDirection        long

df = df.copy()

fast_len   = int(params.get('fast_len',   12))
slow_len   = int(params.get('slow_len',   26))
signal_len = int(params.get('signal_len', 9))
vol_mult   = float(params.get('vol_mult', 1.2))
vol_ma_len = int(params.get('vol_ma_len', 20))
ema_len    = int(params.get('ema_len',    200))

# ── 指标计算 ──────────────────────────────────────
ema_fast = df['close'].ewm(span=fast_len,   adjust=False).mean()
ema_slow = df['close'].ewm(span=slow_len,   adjust=False).mean()
macd     = ema_fast - ema_slow
sig      = macd.ewm(span=signal_len, adjust=False).mean()
hist     = macd - sig

ema200   = df['close'].ewm(span=ema_len, adjust=False).mean()
vol_ma   = df['volume'].rolling(vol_ma_len).mean()

# ── 信号生成 ──────────────────────────────────────
# 金叉：MACD 从下方穿越信号线
golden_cross = (macd > sig) & (macd.shift(1) <= sig.shift(1))
# 死叉：MACD 从上方穿越信号线
death_cross  = (macd < sig) & (macd.shift(1) >= sig.shift(1))

# 过滤条件
trend_up    = df['close'] > ema200              # 价格在 EMA200 上方（大趋势向上）
vol_confirm = df['volume'] > vol_ma * vol_mult  # 成交量放大确认

raw_buy  = golden_cross & trend_up & vol_confirm
raw_sell = death_cross

buy  = raw_buy.fillna(False).astype(bool)
sell = raw_sell.fillna(False).astype(bool)

df['buy']  = buy
df['sell'] = sell

# ── 图表标记 ──────────────────────────────────────
buy_marks  = [df['low'].iloc[i]  * 0.995 if buy.iloc[i]  else None for i in range(len(df))]
sell_marks = [df['high'].iloc[i] * 1.005 if sell.iloc[i] else None for i in range(len(df))]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "EMA 200",
            "data": ema200.fillna(0).tolist(),
            "color": "#faad14",
            "overlay": True
        },
        {
            "name": "MACD",
            "data": macd.fillna(0).tolist(),
            "color": "#1890ff",
            "overlay": False
        },
        {
            "name": "Signal",
            "data": sig.fillna(0).tolist(),
            "color": "#f5222d",
            "overlay": False
        },
        {
            "name": "Histogram",
            "data": hist.fillna(0).tolist(),
            "color": "#52c41a",
            "overlay": False,
            "type": "bar"
        }
    ],
    "signals": [
        {"type": "buy",  "text": "B", "data": buy_marks,  "color": "#00E676"},
        {"type": "sell", "text": "S", "data": sell_marks, "color": "#FF5252"}
    ]
}
