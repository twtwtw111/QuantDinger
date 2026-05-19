"""
Strategy Intelligence Monitor — 策略智能监控后台服务。

完全独立于策略执行线程，每 SCAN_INTERVAL_SEC 秒扫描一次所有 running 策略。

数据信号（三层叠加）：
  1. 技术面：MarketRegimeService（K线 Regime）
  2. 衍生品：Taker买卖比 / Funding Rate / OI / 多空比（MarketDataCollector）
  3. AI分析：FastAnalysisService（enable_ai_filter=true 时启用，同一 symbol 每小时一次）

决策写入 qd_strategy_directives 表，AutoAdjuster.on_kline_close() 消费执行。
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from typing import Any, Dict, List, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

SCAN_INTERVAL_SEC = int(900)         # 每 15 分钟扫一次
_LLM_PER_SYMBOL_INTERVAL = 3600     # 同一 symbol LLM 最多每小时一次
_MIN_KLINES_FOR_REGIME = 30

_stop_event = threading.Event()
_monitor_thread: Optional[threading.Thread] = None
_llm_last_called: Dict[str, float] = {}   # symbol -> timestamp
_llm_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# 公共入口
# ─────────────────────────────────────────────────────────────────────────────

def start_strategy_intelligence():
    """在 app/__init__.py 的 startup hook 中调用。"""
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _stop_event.clear()
    _monitor_thread = threading.Thread(
        target=_run_loop,
        name="StrategyIntelligenceMonitor",
        daemon=True,
    )
    _monitor_thread.start()
    logger.info("[StrategyIntelligence] monitor started")


def stop_strategy_intelligence():
    _stop_event.set()


# ─────────────────────────────────────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────────────────────────────────────

def _run_loop() -> None:
    # 首次启动延迟 60s，等策略线程和数据库全部就绪
    time.sleep(60)
    _ensure_table()

    while not _stop_event.is_set():
        try:
            _scan_all_strategies()
        except Exception as exc:
            logger.warning(f"[StrategyIntelligence] scan error: {exc}")
        _stop_event.wait(SCAN_INTERVAL_SEC)


def _scan_all_strategies() -> None:
    strategies = _load_running_strategies()
    if not strategies:
        return
    logger.info(f"[StrategyIntelligence] scanning {len(strategies)} running strategies")
    for s in strategies:
        try:
            _analyze_and_write(s)
        except Exception as exc:
            logger.debug(f"[StrategyIntelligence] strategy={s.get('id')} error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 单策略分析
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_and_write(strategy: Dict[str, Any]) -> None:
    sid = strategy["id"]
    tc = strategy.get("trading_config") or {}
    amc = strategy.get("ai_model_config") or {}
    symbol: str = tc.get("symbol", "")
    timeframe: str = tc.get("timeframe", "1H")
    if not symbol:
        return

    base = symbol.split("/")[0].upper()   # BTC/USDT → BTC

    # ── 1. Regime（技术面）──────────────────────────────────────────────────
    regime, regime_conf = _detect_regime(symbol, timeframe)

    # ── 2. 衍生品信号（资金流）──────────────────────────────────────────────
    deriv = _get_derivatives(base)

    # ── 3. 绩效评估 ────────────────────────────────────────────────────────
    perf = _get_performance(sid)

    # ── 4. 综合评分 → 决策 ──────────────────────────────────────────────────
    score, reasons = _score_signals(regime, regime_conf, deriv, perf)
    action = _score_to_action(score)

    # ── 5. LLM 辅助（可选，异步，同 symbol 每小时一次）────────────────────
    llm_enabled = _ai_filter_on(amc, tc)
    if llm_enabled:
        _maybe_fire_llm_async(sid, symbol, score, action, reasons)

    # ── 6. 写指令（仅当确定要调整时）───────────────────────────────────────
    if action != "noop":
        signal_data = {
            "regime": regime, "regime_conf": regime_conf,
            "taker_ratio": deriv.get("taker_buy_sell_ratio"),
            "funding_rate": deriv.get("funding_rate"),
            "oi_change_24h": deriv.get("open_interest_change_24h"),
            "long_short_ratio": deriv.get("long_short_ratio"),
            "consecutive_losses": perf["consecutive_losses"],
            "mdd_pct": perf["mdd_pct"],
            "score": score,
        }
        _write_directive(sid, action, "; ".join(reasons), score, "rule", signal_data)
        logger.info(
            f"[StrategyIntelligence] strategy={sid} symbol={symbol} "
            f"action={action} score={score:.2f} reasons={reasons}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 信号采集
# ─────────────────────────────────────────────────────────────────────────────

def _detect_regime(symbol: str, timeframe: str):
    try:
        from app.services.kline import KlineService
        from app.services.experiment.regime import MarketRegimeService
        import pandas as pd

        klines = KlineService().get_klines(symbol, timeframe, limit=100, market_category="Crypto")
        if not klines or len(klines) < _MIN_KLINES_FOR_REGIME:
            return None, 0.0

        df = pd.DataFrame(klines, columns=["timestamp", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        result = MarketRegimeService().detect(df, symbol=symbol, timeframe=timeframe)
        return result.get("regime"), float(result.get("confidence", 0))
    except Exception as exc:
        logger.debug(f"[StrategyIntelligence] regime detect error: {exc}")
        return None, 0.0


def _get_derivatives(base_symbol: str) -> Dict[str, Any]:
    try:
        from app.services.market_data_collector import get_market_data_collector
        collector = get_market_data_collector()
        return collector._get_crypto_derivatives_metrics(base_symbol)
    except Exception as exc:
        logger.debug(f"[StrategyIntelligence] derivatives error: {exc}")
        return {}


def _get_performance(strategy_id: int) -> Dict[str, Any]:
    result = {"consecutive_losses": 0, "mdd_pct": 0.0, "total_pnl": 0.0, "trade_count": 0}
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            # 最近 10 笔已平仓交易
            cur.execute(
                "SELECT profit FROM qd_strategy_trades "
                "WHERE strategy_id=%s AND profit IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 10",
                (strategy_id,),
            )
            rows = cur.fetchall()
            # 策略初始资金
            cur.execute(
                "SELECT initial_capital FROM qd_strategies_trading WHERE id=%s",
                (strategy_id,),
            )
            cap_row = cur.fetchone()
            cur.close()

        profits = [float(r.get("profit") or 0) for r in rows]
        result["trade_count"] = len(profits)
        result["total_pnl"] = sum(profits)

        # 连续亏损
        losses = 0
        for p in profits:
            if p < 0:
                losses += 1
            else:
                break
        result["consecutive_losses"] = losses

        # 简化 MDD：从初始资金看
        initial = float((cap_row or {}).get("initial_capital") or 10000)
        cumulative = initial
        peak = initial
        max_dd = 0.0
        for p in reversed(profits):   # 从早到晚
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        result["mdd_pct"] = round(max_dd, 2)
    except Exception as exc:
        logger.debug(f"[StrategyIntelligence] performance error: {exc}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 评分 & 决策
# ─────────────────────────────────────────────────────────────────────────────

def _score_signals(regime, regime_conf, deriv, perf) -> tuple:
    """
    综合评分 score：
      > 0  → 偏多，继续运行
      < 0  → 偏空/危险，需要干预
    绝对值越大越确信。
    """
    score = 0.0
    reasons: List[str] = []

    # ── Regime ──
    if regime == "bull_trend":
        score += 2.0 * regime_conf
    elif regime == "bear_trend":
        score -= 2.5 * regime_conf
        reasons.append(f"bear_trend(conf={regime_conf:.0%})")
    elif regime == "high_volatility":
        score -= 1.5 * regime_conf
        reasons.append(f"high_volatility(conf={regime_conf:.0%})")
    elif regime == "range_compression":
        score += 0.5

    # ── Taker 买卖比（< 0.45 = 强卖压）──
    tbr = deriv.get("taker_buy_sell_ratio")
    if tbr is not None:
        if tbr < 0.40:
            score -= 2.0
            reasons.append(f"taker_ratio={tbr:.2f}(强卖压)")
        elif tbr < 0.48:
            score -= 0.8
            reasons.append(f"taker_ratio={tbr:.2f}(偏卖)")
        elif tbr > 0.55:
            score += 0.8

    # ── Funding Rate（负费率 = 空头主导）──
    fr = deriv.get("funding_rate")
    if fr is not None:
        if fr < -0.05:
            score -= 1.5
            reasons.append(f"funding={fr:.3f}%(空头极端)")
        elif fr < -0.01:
            score -= 0.5
        elif fr > 0.10:
            score -= 0.5   # 多头过热，注意反转

    # ── OI 变化（OI 大幅上涨 + 负分 = 空单堆积）──
    oi_ch = deriv.get("open_interest_change_24h")
    if oi_ch is not None and score < 0 and oi_ch > 10:
        score -= 0.8
        reasons.append(f"OI_24h=+{oi_ch:.1f}%(空单堆积)")

    # ── 多空比（< 0.45 = 散户偏空）──
    lsr = deriv.get("long_short_ratio")
    if lsr is not None:
        if lsr < 0.42:
            score -= 0.5
        elif lsr > 0.60:
            score += 0.3

    # ── 策略绩效 ──
    if perf["consecutive_losses"] >= 3:
        score -= 1.5
        reasons.append(f"连续亏损{perf['consecutive_losses']}笔")
    if perf["mdd_pct"] > 15:
        score -= 2.0
        reasons.append(f"MDD={perf['mdd_pct']:.1f}%超阈值")
    elif perf["mdd_pct"] > 10:
        score -= 0.8

    return score, reasons


def _score_to_action(score: float) -> str:
    if score <= -4.0:
        return "stop_strategy"
    if score <= -2.5:
        return "pause_entry"
    if score <= -1.0:
        return "reduce_position"
    if score >= 1.5:
        return "resume_entry"
    return "noop"


# ─────────────────────────────────────────────────────────────────────────────
# LLM 异步辅助
# ─────────────────────────────────────────────────────────────────────────────

def _maybe_fire_llm_async(sid, symbol, rule_score, rule_action, reasons):
    with _llm_lock:
        last = _llm_last_called.get(symbol, 0)
        if time.time() - last < _LLM_PER_SYMBOL_INTERVAL:
            return
        _llm_last_called[symbol] = time.time()

    threading.Thread(
        target=_llm_worker,
        args=(sid, symbol, rule_score, rule_action, reasons),
        daemon=True,
        name=f"intel-llm-{sid}",
    ).start()


def _llm_worker(sid, symbol, rule_score, rule_action, reasons):
    try:
        from app.services.fast_analysis import get_fast_analysis_service
        result = get_fast_analysis_service().analyze("Crypto", symbol, "zh-CN")

        decision = str(result.get("decision") or "").upper()
        confidence = int(result.get("confidence") or 0)
        summary = str(result.get("summary") or "")[:150]

        logger.info(
            f"[StrategyIntelligence LLM] strategy={sid} symbol={symbol} "
            f"decision={decision} conf={confidence}% | {summary}"
        )

        # LLM 结果与规则一致 → 加强确信度写指令
        # LLM 结果与规则相反 → 降低干预（不写或写 noop）
        llm_score = 0.0
        if decision == "SELL":
            llm_score = -(confidence / 100) * 3.0
        elif decision == "BUY":
            llm_score = (confidence / 100) * 3.0

        hybrid_score = rule_score * 0.6 + llm_score * 0.4
        action = _score_to_action(hybrid_score)

        if action != "noop":
            llm_reason = f"LLM={decision}@{confidence}% | {'; '.join(reasons)}"
            _write_directive(sid, action, llm_reason, int(abs(hybrid_score) * 20), "hybrid", {
                "llm_decision": decision,
                "llm_confidence": confidence,
                "rule_score": rule_score,
                "hybrid_score": hybrid_score,
            })
    except Exception as exc:
        logger.warning(f"[StrategyIntelligence] LLM worker error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# DB 操作
# ─────────────────────────────────────────────────────────────────────────────

def _write_directive(
    strategy_id: int,
    action: str,
    reason: str,
    confidence: int,
    source: str,
    signal_data: Dict,
) -> None:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_strategy_directives
                    (strategy_id, action, reason, confidence, source, signal_data)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    strategy_id, action, reason,
                    max(0, min(100, int(confidence))),
                    source,
                    json.dumps(signal_data, ensure_ascii=False),
                ),
            )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.warning(f"[StrategyIntelligence] write directive failed: {exc}")


def _load_running_strategies() -> List[Dict]:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, trading_config, ai_model_config
                FROM qd_strategies_trading
                WHERE status = 'running'
                """,
            )
            rows = cur.fetchall()
            cur.close()
        result = []
        for r in rows:
            row = dict(r)
            for field in ("trading_config", "ai_model_config"):
                v = row.get(field)
                if isinstance(v, str) and v.strip():
                    try:
                        row[field] = json.loads(v)
                    except Exception:
                        row[field] = {}
                elif not isinstance(v, dict):
                    row[field] = {}
            result.append(row)
        return result
    except Exception as exc:
        logger.warning(f"[StrategyIntelligence] load strategies error: {exc}")
        return []


def _ensure_table() -> None:
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qd_strategy_directives (
                    id          SERIAL PRIMARY KEY,
                    strategy_id INTEGER      NOT NULL,
                    action      VARCHAR(50)  NOT NULL,
                    reason      TEXT,
                    confidence  INTEGER      DEFAULT 0,
                    source      VARCHAR(50)  DEFAULT '',
                    signal_data JSONB,
                    expires_at  TIMESTAMP,
                    consumed_at TIMESTAMP,
                    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_directives_strategy_pending
                ON qd_strategy_directives(strategy_id, consumed_at)
                WHERE consumed_at IS NULL
                """
            )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.warning(f"[StrategyIntelligence] ensure table error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────

def _ai_filter_on(amc: Dict, tc: Dict) -> bool:
    for val in (
        amc.get("enable_ai_filter"), amc.get("enableAiFilter"),
        tc.get("enable_ai_filter"), tc.get("enableAiFilter"),
    ):
        if val is None:
            continue
        if isinstance(val, bool):
            return val
        if str(val).lower() in ("1", "true", "yes", "on", "enabled"):
            return True
        if str(val).lower() in ("0", "false", "no", "off", "disabled"):
            return False
    return False


def pop_pending_directives(strategy_id: int) -> List[Dict]:
    """
    AutoAdjuster 在 K 线收盘时调用，取出并标记消费所有未处理指令。
    返回 list[{action, reason, confidence, source}]
    """
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_strategy_directives
                SET consumed_at = NOW()
                WHERE strategy_id = %s AND consumed_at IS NULL
                    AND (expires_at IS NULL OR expires_at > NOW())
                RETURNING action, reason, confidence, source, signal_data
                """,
                (strategy_id,),
            )
            rows = cur.fetchall()
            db.commit()
            cur.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug(f"[StrategyIntelligence] pop directives error: {exc}")
        return []
