-- Keep the paper dashboard read path bounded and index the common decision feed.
CREATE INDEX IF NOT EXISTS idx_memory_cold_action_updated
    ON memory (updated_at DESC)
    WHERE tier='COLD' AND (value ? 'action');
CREATE INDEX IF NOT EXISTS idx_memory_tier_updated
    ON memory (tier, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_eda_replays_created
    ON eda_replay_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eda_models_created
    ON eda_model_registry (created_at DESC);
