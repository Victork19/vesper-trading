CREATE TABLE IF NOT EXISTS eda_opportunities (
    opportunity_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    snapshot_hash TEXT,
    episode_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eda_opportunities_status_time ON eda_opportunities(status, observed_at);
CREATE TABLE IF NOT EXISTS eda_replay_runs (
    replay_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS eda_model_registry (
    model_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    record JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(model_id, version)
);