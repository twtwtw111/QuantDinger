-- v3.2.0 Strategy Intelligence Directives
-- 策略智能监控器写入的调整指令，由 AutoAdjuster 在 K 线收盘时消费

CREATE TABLE IF NOT EXISTS qd_strategy_directives (
    id          SERIAL PRIMARY KEY,
    strategy_id INTEGER      NOT NULL,
    action      VARCHAR(50)  NOT NULL,   -- pause_entry | resume_entry | reduce_position | stop_strategy
    reason      TEXT,
    confidence  INTEGER      DEFAULT 0,  -- 0-100
    source      VARCHAR(50)  DEFAULT '',  -- rule | llm | hybrid
    signal_data JSONB,                   -- 触发决策的原始信号快照（调试用）
    expires_at  TIMESTAMP,               -- 指令有效期（NULL = 永久有效直到被消费）
    consumed_at TIMESTAMP,               -- AutoAdjuster 消费时间
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_directives_strategy_pending
    ON qd_strategy_directives(strategy_id, consumed_at)
    WHERE consumed_at IS NULL;
