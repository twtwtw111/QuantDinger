# QuantDinger 迭代路线图

**核心目标**：全自动加密货币量化交易，稳定月盈利 3-8%
**交易市场**：加密货币（Binance / Bybit / OKX）
**本地 LLM**：qwen3.6:27b via Ollama

---

## Phase 0 — 基础设施搭建（当前阶段）

**目标**：让整个系统跑通，paper 模式可用

- [ ] Docker Compose 启动，4 个服务全部健康（postgres / redis / backend / frontend）
- [ ] `.env` 配置完成：Ollama 接入（`CUSTOM_API_URL=http://host.docker.internal:11434/v1`）
- [ ] Binance 或 Bybit 配置 paper 交易 API Key（Testnet 密钥）
- [ ] 访问 `http://localhost:8888` 登录管理界面
- [ ] MCP Server 接入 Claude Code（`mcp_server/README.md`）
- [ ] 接入 `tradingview-mcp`（实时技术指标数据源）
- [ ] 接入 `finance-trading-ai-agents-mcp`（金融分析多 Agent）

**完成标准**：能在界面上看到 K 线、能运行一次 paper 回测

---

## Phase 1 — 首批策略 + Paper 交易验证（第 1-2 周）

**目标**：3 个基础策略全部通过 paper 验证

### 策略开发（优先顺序）
1. **RSI 均值回归策略**（震荡市）
   - BTC/USDT，1H 时间框架
   - RSI < 30 买入，RSI > 70 卖出
   - 止损：-3%，止盈：+5%

2. **MACD 趋势跟随策略**（趋势市）
   - ETH/USDT，4H 时间框架
   - MACD 金叉买入，死叉卖出
   - 加入成交量确认信号

3. **布林带突破策略**（波动市）
   - BTC/USDT，15m 时间框架
   - 价格突破上轨做多，跌破下轨平仓

### 每个策略的准入标准
| 指标 | 最低要求 |
|------|---------|
| 回测周期 | ≥ 6 个月历史数据 |
| Sharpe Ratio | > 1.2 |
| 最大回撤 (MDD) | < 15% |
| 胜率 | > 50% |
| 盈亏比 | > 1.5 |

### Paper 交易要求
- 每个策略 paper 运行 ≥ 2 周
- 连续 2 周正盈利才可进入 Phase 3

### 外部工具接入
- [ ] `TradingAgents` 框架部署（多 Agent 辅助决策）
- [ ] 配置 Telegram Bot 接收策略信号通知

---

## Phase 2 — 策略优化 + 市场机制识别（第 3-6 周）

**目标**：Paper 月收益率 > 3%，胜率 > 55%

### 参数优化
- 使用 QuantDinger `experiment` 模块做网格搜索/进化优化
- 接口：`POST /api/agent/v1/experiments/structured-tune`
- 每个策略生成 top-5 参数组合，分别 paper 验证

### 市场机制识别（Regime Detection）
- 使用 `POST /api/agent/v1/experiments/regime/detect`
- 识别三种市场状态：牛市趋势 / 熊市趋势 / 震荡横盘
- 根据市场状态动态启用/禁用对应策略：
  - 牛市 → MACD 趋势策略权重 ↑
  - 熊市 → 降仓或空仓
  - 震荡 → RSI 均值回归 ↑

### AI 分析增强
- 开启 `ENABLE_CONFIDENCE_CALIBRATION=true`（AI 置信度校准）
- 开启 `ENABLE_REFLECTION_WORKER=true`（每日 AI 复盘）
- `tradingview-mcp` 提供多时间框架技术指标叠加分析

---

## Phase 3 — 小仓位实盘（第 7-10 周）

**目标**：真实资金小仓位验证，建立实盘信心

### 实盘准入门槛（全部满足才开启）
- [ ] Paper 交易连续 4 周盈利
- [ ] 最大回撤 < 10%
- [ ] Sharpe Ratio > 1.5（实盘要求更高）
- [ ] 用户手动确认并设置 `AGENT_LIVE_TRADING_ENABLED=true`

### 实盘风控规则
| 规则 | 数值 |
|------|------|
| 单策略最大仓位 | 总资金的 5% |
| 单日最大亏损 | 总资金的 2%（触发当日停止交易）|
| 单笔最大止损 | -3% |
| 最大并发策略数 | 3 个 |

### 监控配置
- 开启 `ENABLE_PORTFOLIO_MONITOR=true`（已默认开启）
- Telegram 实时推送：开仓 / 平仓 / 止损 / 日报
- 每周一人工审查上周交易记录

---

## Phase 4 — 规模化自动化（持续迭代）

**目标**：系统自我优化，减少人工干预

### 多策略管理
- 并行运行 ≤ 8 个策略（`STRATEGY_MAX_THREADS=64` 已够用）
- 建立策略绩效数据库，AI 每月自动评估并淘汰低效策略
- 引入跨品种策略（SOL、BNB、DOGE 等）

### AI 自动化增强
- `FinRobot` 接入做基本面增强信号
- `ENABLE_AI_ENSEMBLE=true`（多模型共识）
- 开启 `AI_CODE_GEN_MODEL=qwen3.6:27b` 让 AI 自动生成策略候选

### 数据基础设施
- 历史数据本地存储（避免每次回测重新拉取）
- 建立信号质量评分系统
- 构建策略相关性矩阵（避免策略过于同质）

---

## 里程碑总览

| 里程碑 | 预计时间 | 成功指标 |
|--------|---------|---------|
| 系统跑通 | 本周 | 4 服务健康，paper 回测可用 |
| 首个策略 paper 验证 | 第 2 周 | Sharpe>1.2，2周paper盈利 |
| 3 策略全部 paper 通过 | 第 4 周 | 组合 Sharpe>1.5 |
| 实盘首笔交易 | 第 8 周 | 小仓位，验证执行链路 |
| 稳定月盈利 3% | 第 12 周 | 连续 4 周实盘月收益>3% |
| 全自动化运行 | 第 16 周 | 人工干预 <1h/周 |
