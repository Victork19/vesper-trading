CREATE TABLE IF NOT EXISTS observability_metrics (
    metric_key TEXT PRIMARY KEY,
    metric_name TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    labels JSONB NOT NULL DEFAULT '{}'::jsonb,
    value DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS observability_metric_samples (
    sample_id BIGSERIAL PRIMARY KEY,
    metric_key TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    labels JSONB NOT NULL DEFAULT '{}'::jsonb,
    value DOUBLE PRECISION NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_observability_samples_metric_time ON observability_metric_samples(metric_key,observed_at DESC);