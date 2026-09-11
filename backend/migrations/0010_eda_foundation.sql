CREATE TABLE IF NOT EXISTS eda_episodes (
    episode_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    timestamp_decision TIMESTAMPTZ NOT NULL,
    episode JSONB NOT NULL,
    episode_hash TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eda_episodes_decision ON eda_episodes(decision_id);
CREATE INDEX IF NOT EXISTS idx_eda_episodes_created ON eda_episodes(created_at DESC);

CREATE TABLE IF NOT EXISTS eda_events (
    event_id TEXT PRIMARY KEY,
    episode_id TEXT,
    source TEXT NOT NULL,
    source_event_id TEXT,
    event_type TEXT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    ingestion_time TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    schema_version TEXT NOT NULL,
    quality JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_eda_events_episode ON eda_events(episode_id,event_time);
CREATE INDEX IF NOT EXISTS idx_eda_events_type_time ON eda_events(event_type,event_time);

CREATE OR REPLACE FUNCTION prevent_eda_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'EDA records are append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS eda_episodes_no_update ON eda_episodes;
CREATE TRIGGER eda_episodes_no_update
    BEFORE UPDATE OR DELETE ON eda_episodes
    FOR EACH ROW EXECUTE FUNCTION prevent_eda_mutation();

DROP TRIGGER IF EXISTS eda_events_no_update ON eda_events;
CREATE TRIGGER eda_events_no_update
    BEFORE UPDATE OR DELETE ON eda_events
    FOR EACH ROW EXECUTE FUNCTION prevent_eda_mutation();
CREATE INDEX IF NOT EXISTS idx_eda_events_source_time ON eda_events(source,event_time);
