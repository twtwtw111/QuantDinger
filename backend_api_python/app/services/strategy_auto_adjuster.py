"""
StrategyAutoAdjuster — K 线收盘时的自动策略调整模块。

调用时机：trading_executor 每次 K 线刷新完成后调用 on_kline_close()。
保证非阻塞：同步规则层 O(ms)，LLM 层跑在独立 daemon 线程。

两层逻辑
  规则层（同步）：
    - MarketRegimeService 检测当前 Regime（bear/bull/range/high_vol）
    - 统计近期连续亏损笔数
    - 根据 Regime 和亏损状况直接修改 trading_config dict + 持久化到 DB
  LLM 层（异步，仅当 enable_ai_filter=true）：
    - 在 daemon 线程中调用 FastAnalysisService（DeepSeek）
    - 结果写入 _pending 队列，下根 K 线收盘时由执行器线程安全消费
"""

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CHECK_EVERY_N_BARS = 3          # 每 N 根 K 线做一次规则检查（节流）
_LLM_MIN_INTERVAL_SEC = 3600     # LLM 每条策略最多每小时触发一次
_CONSECUTIVE_LOSS_THRESHOLD = 3  # 连续亏损 N 笔触发减仓
_BEAR_REGIMES = frozenset({'bear_trend', 'high_volatility'})
_BULL_REGIMES = frozenset({'bull_trend', 'range_compression'})


class StrategyAutoAdjuster:
    _instance: Optional['StrategyAutoAdjuster'] = None
    _init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'StrategyAutoAdjuster':
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        # per-strategy runtime state（只在执行器线程读写规则部分）
        self._state: Dict[int, Dict] = {}
        # LLM daemon 线程写，执行器线程读消费（需要锁保护）
        self._pending: Dict[int, Dict] = {}
        self._lock = threading.Lock()

    # ─────────────────────────────────────────────────────────────────────
    # Public API（由 trading_executor 调用）
    # ─────────────────────────────────────────────────────────────────────

    def on_kline_close(
        self,
        strategy_id: int,
        df,                              # pd.DataFrame，已包含最新 K 线
        trading_config: Dict[str, Any],  # 执行器持有的 live dict（会直接修改）
        ai_model_config: Dict[str, Any],
        symbol: str,
        timeframe: str,
    ) -> None:
        """K 线收盘时调用。立即返回，绝不阻塞策略线程。"""
        try:
            # 1. 消费 StrategyIntelligenceMonitor 写入的指令（DB，最高优先级）
            self._apply_intelligence_directives(strategy_id, trading_config)
            # 2. 消费上一次本地 LLM 线程排队的调整
            self._apply_pending(strategy_id, trading_config)
            # 3. 本地同步规则检查 + 异步 LLM 触发
            self._check(strategy_id, df, trading_config, ai_model_config, symbol, timeframe)
        except Exception as exc:
            logger.warning(f"[AutoAdjuster:{strategy_id}] on_kline_close error: {exc}")

    def clear_state(self, strategy_id: int) -> None:
        """策略停止时调用，清理内存状态。"""
        with self._lock:
            self._state.pop(strategy_id, None)
            self._pending.pop(strategy_id, None)

    # ─────────────────────────────────────────────────────────────────────
    # 内部逻辑
    # ─────────────────────────────────────────────────────────────────────

    def _apply_intelligence_directives(self, strategy_id: int, trading_config: Dict[str, Any]) -> None:
        """消费 StrategyIntelligenceMonitor 写入的指令并执行。"""
        try:
            from app.services.strategy_intelligence_monitor import pop_pending_directives
            directives = pop_pending_directives(strategy_id)
        except Exception:
            return
        if not directives:
            return

        orig = self._get_state(strategy_id).get('original_entry_pct') or self._read_entry_pct(trading_config)
        for d in directives:
            action = d.get('action', '')
            reason = d.get('reason', '')
            source = d.get('source', '')
            if action == 'pause_entry':
                self._write_to_config(trading_config, {'entry_pct': 0.0})
                self._persist_to_db(strategy_id, {'entry_pct': 0.0})
                self._update_state(strategy_id, {'paused_by_adjuster': True, 'pause_reason': f'intel:{source}'})
                self._log(strategy_id, f"[Intelligence] pause_entry — {reason}")
            elif action == 'resume_entry':
                self._write_to_config(trading_config, {'entry_pct': orig})
                self._persist_to_db(strategy_id, {'entry_pct': orig})
                self._update_state(strategy_id, {'paused_by_adjuster': False, 'pause_reason': ''})
                self._log(strategy_id, f"[Intelligence] resume_entry — {reason}")
            elif action == 'reduce_position':
                current = self._read_entry_pct(trading_config)
                reduced = round(current * 0.5, 4) if current > orig * 0.3 else current
                self._write_to_config(trading_config, {'entry_pct': reduced})
                self._persist_to_db(strategy_id, {'entry_pct': reduced})
                self._log(strategy_id, f"[Intelligence] reduce_position {current:.2f}→{reduced:.2f} — {reason}")
            elif action == 'stop_strategy':
                self._log(strategy_id, f"[Intelligence] stop_strategy triggered — {reason}")
                try:
                    from app.services.trading_executor import get_trading_executor
                    get_trading_executor().stop_strategy(strategy_id)
                except Exception as exc:
                    logger.warning(f"[AutoAdjuster:{strategy_id}] stop_strategy failed: {exc}")

    def _apply_pending(self, strategy_id: int, trading_config: Dict[str, Any]) -> None:
        """消费 LLM 线程排队的参数调整（在执行器线程中安全执行）。"""
        with self._lock:
            pending = self._pending.pop(strategy_id, {})
        if not pending:
            return
        self._write_to_config(trading_config, pending)
        self._persist_to_db(strategy_id, pending)
        self._log(strategy_id, f"Applied LLM async adjustments: {pending}")

    def _check(
        self,
        strategy_id: int,
        df,
        trading_config: Dict[str, Any],
        ai_model_config: Dict[str, Any],
        symbol: str,
        timeframe: str,
    ) -> None:
        state = self._get_state(strategy_id)
        bar_count = state['bar_count'] + 1
        self._update_state(strategy_id, {'bar_count': bar_count})

        # 节流：每 N 根 K 线才做完整检查
        if bar_count % _CHECK_EVERY_N_BARS != 0:
            return

        # 首次运行时记住原始仓位比例
        if state['original_entry_pct'] is None:
            orig = self._read_entry_pct(trading_config)
            self._update_state(strategy_id, {'original_entry_pct': orig})
            state['original_entry_pct'] = orig

        # ── 规则层（同步，O(ms)）──────────────────────────────────────
        regime_result = self._detect_regime(df, symbol, timeframe)
        regime = (regime_result or {}).get('regime')
        losses = self._count_consecutive_losses(strategy_id)

        adjustments = self._compute_rule_adjustments(state, regime, losses, trading_config)
        if adjustments:
            self._write_to_config(trading_config, adjustments)
            self._persist_to_db(strategy_id, adjustments)
            self._update_state(strategy_id, {
                'last_regime': regime,
                'paused_by_adjuster': adjustments.get('_paused', state['paused_by_adjuster']),
                'pause_reason': adjustments.get('_pause_reason', state.get('pause_reason', '')),
            })
            display = {k: v for k, v in adjustments.items() if not k.startswith('_')}
            self._log(strategy_id,
                f"Rule adjustment applied — regime={regime} losses={losses} changes={display}")

        # ── LLM 层（异步，daemon 线程）────────────────────────────────
        if self._ai_filter_enabled(ai_model_config, trading_config):
            now = time.time()
            if now - state['last_llm_ts'] >= _LLM_MIN_INTERVAL_SEC:
                self._update_state(strategy_id, {'last_llm_ts': now})
                # daemon=True：主进程退出时不等待此线程
                threading.Thread(
                    target=self._llm_worker,
                    args=(strategy_id, symbol, regime, dict(state)),
                    daemon=True,
                    name=f"auto-adj-llm-{strategy_id}",
                ).start()

    # ── 规则层 ─────────────────────────────────────────────────────────

    def _compute_rule_adjustments(
        self,
        state: Dict,
        regime: Optional[str],
        losses: int,
        trading_config: Dict,
    ) -> Dict:
        out: Dict[str, Any] = {}
        orig = state['original_entry_pct'] or 0.4
        paused = state['paused_by_adjuster']
        current_pct = self._read_entry_pct(trading_config)

        # 熊市 / 高波动 → 停止新开仓
        if regime in _BEAR_REGIMES and not paused:
            out['entry_pct'] = 0.0
            out['_paused'] = True
            out['_pause_reason'] = regime

        # 市场恢复 → 还原仓位（仅还原由规则/LLM暂停的，不干涉手动停仓）
        elif regime in _BULL_REGIMES and paused and state.get('pause_reason') in (_BEAR_REGIMES | {'llm_bearish'}):
            out['entry_pct'] = orig
            out['_paused'] = False
            out['_pause_reason'] = ''

        # 连续亏损 ≥ 阈值 且未暂停 → 减半仓位
        if losses >= _CONSECUTIVE_LOSS_THRESHOLD and not paused and current_pct > orig * 0.55:
            out['entry_pct'] = round(orig * 0.5, 4)
            out.setdefault('_paused', paused)
            out.setdefault('_pause_reason', state.get('pause_reason', ''))

        return out

    # ── LLM 层（daemon 线程中运行）──────────────────────────────────────

    def _llm_worker(
        self,
        strategy_id: int,
        symbol: str,
        current_regime: Optional[str],
        state_snapshot: Dict,
    ) -> None:
        try:
            from app.services.fast_analysis import get_fast_analysis_service
            result = get_fast_analysis_service().analyze("Crypto", symbol, "zh-CN")

            decision = str(result.get('decision') or '').strip().upper()
            confidence = int(result.get('confidence') or 0)
            summary = str(result.get('summary') or '')[:120]

            self._log(strategy_id,
                f"[LLM] decision={decision} conf={confidence}% regime={current_regime} | {summary}")

            paused = state_snapshot.get('paused_by_adjuster', False)
            orig = state_snapshot.get('original_entry_pct') or 0.4

            if decision == 'SELL' and confidence >= 65 and not paused:
                with self._lock:
                    self._pending.setdefault(strategy_id, {})['entry_pct'] = 0.0
                self._update_state(strategy_id, {
                    'paused_by_adjuster': True,
                    'pause_reason': 'llm_bearish',
                })
                self._log(strategy_id, f"[LLM] queued entry pause (SELL@{confidence}%)")

            elif decision == 'BUY' and confidence >= 65 and paused and state_snapshot.get('pause_reason') == 'llm_bearish':
                with self._lock:
                    self._pending.setdefault(strategy_id, {})['entry_pct'] = orig
                self._update_state(strategy_id, {
                    'paused_by_adjuster': False,
                    'pause_reason': '',
                })
                self._log(strategy_id, f"[LLM] queued entry restore (BUY@{confidence}%)")

        except Exception as exc:
            logger.warning(f"[AutoAdjuster:{strategy_id}] LLM worker error: {exc}")

    # ── 辅助方法 ───────────────────────────────────────────────────────

    def _detect_regime(self, df, symbol: str, timeframe: str) -> Optional[Dict]:
        try:
            if df is None or len(df) < 30:
                return None
            from app.services.experiment.regime import MarketRegimeService
            return MarketRegimeService().detect(df, symbol=symbol, timeframe=timeframe)
        except Exception as exc:
            logger.debug(f"[AutoAdjuster] regime detect error: {exc}")
            return None

    def _count_consecutive_losses(self, strategy_id: int) -> int:
        try:
            from app.utils.db import get_db_connection
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT profit FROM qd_strategy_trades "
                    "WHERE strategy_id = %s AND profit IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT 5",
                    (strategy_id,),
                )
                rows = cur.fetchall()
                cur.close()
            count = 0
            for row in rows:
                if float(row.get('profit') or 0) < 0:
                    count += 1
                else:
                    break
            return count
        except Exception:
            return 0

    def _read_entry_pct(self, trading_config: Dict) -> float:
        for key in ('entry_pct', 'position_ratio', 'positionRatio'):
            val = (trading_config or {}).get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return 0.4

    def _write_to_config(self, trading_config: Dict, adjustments: Dict) -> None:
        """直接修改执行器线程持有的 trading_config dict（同线程调用，无锁）。"""
        for k, v in adjustments.items():
            if not k.startswith('_'):
                trading_config[k] = v

    def _persist_to_db(self, strategy_id: int, adjustments: Dict) -> None:
        updates = {k: v for k, v in adjustments.items() if not k.startswith('_')}
        if not updates:
            return
        try:
            from app.utils.db import get_db_connection
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT trading_config FROM qd_strategies_trading WHERE id = %s",
                    (strategy_id,),
                )
                row = cur.fetchone()
                if not row:
                    cur.close()
                    return
                tc = row.get('trading_config') or {}
                if isinstance(tc, str):
                    tc = json.loads(tc) if tc.strip() else {}
                tc.update(updates)
                cur.execute(
                    "UPDATE qd_strategies_trading SET trading_config = %s WHERE id = %s",
                    (json.dumps(tc, ensure_ascii=False), strategy_id),
                )
                db.commit()
                cur.close()
        except Exception as exc:
            logger.warning(f"[AutoAdjuster:{strategy_id}] persist to DB failed: {exc}")

    def _ai_filter_enabled(self, ai_model_config: Any, trading_config: Any) -> bool:
        amc = ai_model_config if isinstance(ai_model_config, dict) else {}
        tc = trading_config if isinstance(trading_config, dict) else {}
        for val in (
            amc.get('enable_ai_filter'), amc.get('enableAiFilter'),
            tc.get('enable_ai_filter'), tc.get('enableAiFilter'),
        ):
            if val is None:
                continue
            if isinstance(val, bool):
                return val
            s = str(val).strip().lower()
            if s in ('1', 'true', 'yes', 'on', 'enabled'):
                return True
            if s in ('0', 'false', 'no', 'off', 'disabled'):
                return False
        return False

    def _get_state(self, strategy_id: int) -> Dict:
        with self._lock:
            if strategy_id not in self._state:
                self._state[strategy_id] = {
                    'bar_count': 0,
                    'last_regime': None,
                    'last_llm_ts': 0.0,
                    'original_entry_pct': None,
                    'paused_by_adjuster': False,
                    'pause_reason': '',
                }
            return dict(self._state[strategy_id])

    def _update_state(self, strategy_id: int, updates: Dict) -> None:
        with self._lock:
            if strategy_id not in self._state:
                self._state[strategy_id] = {}
            self._state[strategy_id].update(updates)

    def _log(self, strategy_id: int, msg: str) -> None:
        try:
            from app.utils.strategy_runtime_logs import append_strategy_log
            append_strategy_log(strategy_id, "info", f"[AutoAdjuster] {msg}")
        except Exception:
            pass


def get_auto_adjuster() -> StrategyAutoAdjuster:
    return StrategyAutoAdjuster.get_instance()
