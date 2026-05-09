# ── 沙箱注入变量（由平台运行时提供，VSCode 类型提示用）──
import pandas as pd   # noqa: F401
import numpy as np    # noqa: F401
df: pd.DataFrame      # noqa: F821
params: dict          # noqa: F821

my_indicator_name = "RSI 均值回归策略 v2"
my_indicator_description = "RSI 超卖买入，止盈/止损/RSI超买三重出场，适合震荡行情。BTC/USDT 1H。"

# @param rsi_period int 14 RSI 计算周期
# @param oversold float 32 超卖阈值（买入）
# @param overbought float 65 超买阈值（信号卖出）
# @param stop_loss_pct float 0.025 止损比例（2.5%）
# @param take_profit_pct float 0.07 止盈比例（7%）
# @param ema_filter_len int 200 趋势过滤 EMA 周期

# @strategy entryPct 0.25
# @strategy tradeDirection long

df = df.copy()

rsi_period       = int(params.get('rsi_period', 14))
oversold         = float(params.get('oversold', 32))
overbought       = float(params.get('overbought', 65))
stop_loss_pct    = float(params.get('stop_loss_pct', 0.025))
take_profit_pct  = float(params.get('take_profit_pct', 0.07))
ema_filter_len   = int(params.get('ema_filter_len', 200))

# ── 指标计算 ──────────────────────────────────────
delta = df['close'].diff()
gain  = delta.clip(lower=0).ewm(alpha=1 / rsi_period, adjust=False).mean()
loss  = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_period, adjust=False).mean()
rs    = gain / loss.replace(0, float('nan'))
rsi   = 100 - (100 / (1 + rs))

ema200 = df['close'].ewm(span=ema_filter_len, adjust=False).mean()

# ── 买入信号：RSI 触底回升 + 在 EMA200 上方 ────────
# 用 RSI 从低位开始回升（而不是仍在低位），减少接飞刀
rsi_cross_up = (rsi > rsi.shift(1)) & (rsi.shift(1) < oversold)
raw_buy = rsi_cross_up & (df['close'] > ema200)
buy = raw_buy.fillna(False).astype(bool)

# ── 卖出信号：RSI 超买 ────────────────────────────
raw_sell = rsi > overbought
sell = (raw_sell.fillna(False) & (~raw_sell.shift(1).fillna(False))).astype(bool)

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
            "name": "RSI",
            "data": rsi.fillna(50).tolist(),
            "color": "#722ed1",
            "overlay": False
        }
    ],
    "signals": [
        {"type": "buy",  "text": "B", "data": buy_marks,  "color": "#00E676"},
        {"type": "sell", "text": "S", "data": sell_marks, "color": "#FF5252"}
    ]
}
