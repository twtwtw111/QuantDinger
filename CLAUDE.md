# QuantDinger — 私人加密量化交易系统

## 项目核心目标

全自动化加密货币量化交易，目标稳定月盈利 3-8%。
技术栈：Flask + PostgreSQL + Docker + Ollama (qwen3.6:27b) + CCXT。
交易市场：加密货币（Binance / Bybit / OKX），不做 A 股和外汇。

---

## 快速命令

```bash
# 启动所有服务
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看后端日志（实时）
docker-compose logs -f backend

# 重启后端（改配置后）
docker-compose restart backend

# 停止所有服务
docker-compose down

# 验证 LLM 连通性（Ollama）
curl http://localhost:11434/api/tags

# 验证 QuantDinger API
curl http://localhost:5000/api/health
```

---

## 服务架构

| 服务 | 端口 | 说明 |
|------|------|------|
| frontend (Nginx) | 8888 | 管理界面入口 |
| backend (Flask) | 5000 | REST API + Agent Gateway |
| postgres | 5432 | 主数据库 |
| redis | 6379 | 缓存层 |
| Ollama (本机) | 11434 | 本地 LLM (qwen3.6:27b) |

Docker 容器访问本机 Ollama 使用：`http://host.docker.internal:11434/v1`

---

## 策略开发规范

策略文件格式（ScriptStrategy，唯一支持的格式）：

```python
def on_init(ctx):
    """初始化，只运行一次"""
    ctx.rsi_period = 14
    ctx.overbought = 70
    ctx.oversold = 30

def on_bar(ctx, bar):
    """每根 K 线触发一次"""
    rsi = ctx.indicator("RSI", period=ctx.rsi_period)
    if rsi is None:
        return
    if rsi < ctx.oversold and ctx.position <= 0:
        ctx.buy(amount=ctx.trade_amount)
    elif rsi > ctx.overbought and ctx.position > 0:
        ctx.sell(amount=ctx.position)
```

**沙箱可用模块**（仅限这些）：`math`, `statistics`, `json`, `datetime`, `numpy`, `pandas`, `ta`
**沙箱禁用**：`requests`, `httpx`, `os`, `subprocess`, `open`, `eval`, `exec`

内置指标参考：`backend_api_python/app/services/builtin_indicators.py`
策略开发指南：`docs/STRATEGY_DEV_GUIDE.md`

---

## 策略上线流程

```
写策略代码 → /strategy-review（代码审查）
→ 回测 ≥6个月（Sharpe>1.2，MDD<15%）
→ Paper 交易 ≥2周（连续盈利，MDD<10%）
→ 实盘小仓位（单策略 ≤5% 总资金）
→ 持续监控 + 每周复盘
```

---

## 严格禁止事项（红线，任何情况不得违反）

1. **禁止在未经明确二次确认前设置 `AGENT_LIVE_TRADING_ENABLED=true`**
2. **禁止修改 `.env` 中的 `SECRET_KEY`**（会导致所有已加密的交易所凭证失效）
3. **禁止在策略代码中使用网络库**（`requests/httpx/urllib` 等，沙箱会拒绝执行）
4. **禁止删除或清空 `qd_agent_audit` 表**（操作审计日志）
5. **禁止跳过 paper 验证期直接上实盘**（无论回测结果多好）

---

## 关键文件速查

```
CLAUDE.md                          ← 本文件（Claude Code 引导）
docs/ROADMAP.md                    ← 迭代 Phase 计划
docs/CONTEXT_MAP.md                ← 反幻觉文件索引
docs/STRATEGY_DEV_GUIDE.md         ← 策略开发详细指南
docs/agent/agent-openapi.json      ← Agent Gateway API 规范
backend_api_python/env.example     ← 所有环境变量说明
backend_api_python/app/services/
  live_trading/                    ← 交易所适配器（binance/bybit/okx...）
  strategy_script_runtime.py       ← 策略沙箱执行器
  builtin_indicators.py            ← 可用内置指标
  backtest.py                      ← 回测引擎
  trading_executor.py              ← 实时策略执行器
mcp_server/README.md               ← MCP 工具列表
.claude/agents/                    ← Agent 角色定义
.claude/commands/                  ← 自定义斜杠命令
```

---

## 不确定时的行为准则

- 不确定 API 路径 → 查 `docs/agent/agent-openapi.json`，不要猜
- 不确定环境变量名 → 查 `backend_api_python/env.example`，不要猜
- 不确定策略是否可盈利 → 说"需要回测验证"，不要给无依据的盈利预测
- 不确定是否安全操作 → 先停下来问用户，不要假设
