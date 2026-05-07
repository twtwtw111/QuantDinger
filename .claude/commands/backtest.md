# /backtest — 运行策略回测

## 用途
对指定策略代码运行回测，并输出格式化的分析报告。

## 使用方式
```
/backtest [策略名] [交易对] [时间框架] [开始日期] [结束日期]
示例: /backtest RSI均值回归 BTC/USDT 1H 2024-01-01 2024-12-31
```

## 执行步骤

1. 确认策略代码存在（从 QuantDinger 策略列表中查找或用户提供代码）
2. 调用回测接口：
   ```
   POST /api/agent/v1/backtests
   {
     "code": "<策略代码>",
     "market": "Crypto",
     "symbol": "<交易对>",
     "timeframe": "<时间框架>",
     "start_date": "<开始日期>",
     "end_date": "<结束日期>"
   }
   ```
3. 轮询任务状态：`GET /api/agent/v1/jobs/{job_id}`，直到状态为 completed
4. 解析回测结果，调用 quant-analyst agent 进行分析
5. 调用 risk-manager agent 给出准入评估
6. 输出完整格式化报告

## 输出格式

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 回测报告：[策略名称]
━━━━━━━━━━━━━━━━━━━━━━━━━━━

基本信息
  交易对:   [symbol]
  时间框架: [timeframe]
  回测区间: [start] ~ [end]

核心指标
  总收益率:     XX.XX%
  年化收益率:   XX.XX%
  Sharpe Ratio: X.XX
  Calmar Ratio: X.XX
  最大回撤:     XX.XX%
  胜率:         XX.XX%
  盈亏比:       X.XX
  总交易次数:   XXX

准入评估（quant-analyst + risk-manager）
  [评估结论]

优化建议
  [具体可操作的改进方向]
━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
