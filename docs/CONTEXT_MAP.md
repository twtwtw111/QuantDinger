# Context Map — 反幻觉文件索引

> Claude Code 在开始任何任务前，先查本文件找到正确的文件路径和 API，
> 不要凭记忆猜测路径或接口名称。

---

## 策略开发

| 需要什么 | 去哪里找 |
|---------|---------|
| 策略代码格式（on_init/on_bar） | `docs/STRATEGY_DEV_GUIDE.md` |
| 沙箱允许使用的模块列表 | `backend_api_python/app/services/strategy_script_runtime.py` 顶部 `ALLOWED_MODULES` |
| 内置指标完整列表（RSI/MACD/BB等） | `backend_api_python/app/services/builtin_indicators.py` |
| 指标参数格式 | `backend_api_python/app/services/indicator_params.py` |
| 跨品种策略示例 | `docs/CROSS_SECTIONAL_STRATEGY_GUIDE_EN.md` |

---

## API 接口

| 操作 | 接口路径 | 方法 |
|------|---------|------|
| 提交回测 | `/api/agent/v1/backtests` | POST |
| 查询回测/任务结果 | `/api/agent/v1/jobs/{job_id}` | GET |
| 列出最近任务 | `/api/agent/v1/jobs` | GET |
| 市场机制识别 | `/api/agent/v1/experiments/regime/detect` | POST |
| 参数网格/随机优化 | `/api/agent/v1/experiments/structured-tune` | POST |
| 查询策略列表 | `/api/agent/v1/strategies` | GET |
| 创建策略 | `/api/agent/v1/strategies` | POST |
| 更新/激活策略 | `/api/agent/v1/strategies/{id}` | PATCH |
| Paper 下单 | `/api/agent/v1/quick-trade/orders` | POST |
| 查询 paper 订单 | `/api/agent/v1/portfolio/paper-orders` | GET |
| 查询余额 | `/api/quick-trade/balance` | GET |
| 查询持仓 | `/api/quick-trade/position` | GET |
| Token 身份验证 | `/api/agent/v1/whoami` | GET |

完整 API 规范：`docs/agent/agent-openapi.json`

### structured-tune 注意事项

- `base` 里的日期字段用 **camelCase**：`startDate` / `endDate` / `initialCapital` / `tradeDirection`
- 指标参数路径：`indicator_params.fast_len`（注意下划线，不是 `indicatorParams`）
- 策略风控参数路径：`strategyConfig.stopLossPct`（注意 camelCase 前缀）
- 加密货币市场名称：`"Crypto"`，不是 `"binance"`

---

## 交易所适配器

| 交易所 | 文件路径 |
|--------|---------|
| Binance 合约 | `backend_api_python/app/services/live_trading/binance.py` |
| Binance 现货 | `backend_api_python/app/services/live_trading/binance_spot.py` |
| Bybit | `backend_api_python/app/services/live_trading/bybit.py` |
| OKX | `backend_api_python/app/services/live_trading/okx.py` |
| KuCoin | `backend_api_python/app/services/live_trading/kucoin.py` |
| 工厂入口 | `backend_api_python/app/services/live_trading/factory.py` |

---

## 配置

| 配置项 | 位置 |
|--------|------|
| 所有环境变量说明 | `backend_api_python/env.example` |
| 当前运行配置 | `backend_api_python/.env`（不要提交 git）|
| Docker 服务定义 | `docker-compose.yml` |
| LLM Provider 配置 | `.env` 中的 `LLM_PROVIDER / CUSTOM_API_URL / CUSTOM_MODEL` |

**Docker 服务端口**：
- Frontend: `http://localhost:8888`
- Backend API: `http://localhost:5001`（容器内 5000，宿主机映射 5001）
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`
- Ollama (本机): `http://localhost:11434`

**生产服务器**：`18.141.225.75:8888`（MCP 工具默认指向此地址）

---

## 数据库关键表

| 表名 | 说明 | 注意 |
|------|------|------|
| `qd_strategies_trading` | 策略定义和状态 | status 字段：running/stopped |
| `qd_backtest_runs` | 回测历史记录 | 包含 metrics JSON |
| `qd_pending_orders` | 待执行订单队列 | PendingOrderWorker 消费 |
| `qd_agent_audit` | Agent 操作日志 | **禁止删除** |
| `qd_exchange_credentials` | 加密的交易所 API Key | Fernet 加密，勿直接读 |

---

## MCP 工具

| 工具名 | 功能 | 调用方式 |
|--------|------|---------|
| `whoami` | 验证 token 身份和 scope | MCP call |
| `submit_backtest` | 提交异步回测 | MCP call |
| `get_job` | 查询任务结果（回测/调优） | MCP call |
| `list_jobs` | 列出最近任务 | MCP call |
| `get_klines` | 获取 K 线数据 | MCP call |
| `get_price` | 获取最新价格 | MCP call |
| `list_markets` | 列出可用市场 | MCP call |
| `search_symbols` | 搜索交易品种 | MCP call |
| `regime_detect` | 市场机制识别 | MCP call |
| `submit_structured_tune` | 参数网格/随机优化 | MCP call |
| `list_strategies` | 列出所有策略 | MCP call |
| `get_strategy` | 获取单条策略详情 | MCP call |

完整工具文档：`mcp_server/README.md`

---

## Agent 角色

| Agent | 职责 | 文件 |
|-------|------|------|
| quant-analyst | 策略研究、回测解读 | `.claude/agents/quant-analyst.md` |
| crypto-trader | 订单执行、仓位管理 | `.claude/agents/crypto-trader.md` |
| risk-manager | 风险评估、仓位建议 | `.claude/agents/risk-manager.md` |

---

## 自定义命令

| 命令 | 功能 |
|------|------|
| `/backtest` | 运行策略回测并格式化输出结果 |
| `/strategy-review` | 审查策略代码规范性和逻辑漏洞 |
| `/risk-check` | 评估当前策略组合风险敞口 |
