ALTER TABLE reconciliation_leases ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE reconciliation_leases ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;
ALTER TABLE reconciliation_leases ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reconciliation_leases ADD COLUMN IF NOT EXISTS last_order_status TEXT;

CREATE TABLE IF NOT EXISTS reconciliation_health (
    id INTEGER PRIMARY KEY,
    service_owner TEXT,
    last_cycle_started_at TIMESTAMPTZ,
    last_cycle_completed_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_enumeration_at TIMESTAMPTZ,
    last_account_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    cycles BIGINT NOT NULL DEFAULT 0,
    orders_checked BIGINT NOT NULL DEFAULT 0,
    orders_updated BIGINT NOT NULL DEFAULT 0,
    unknown_orders BIGINT NOT NULL DEFAULT 0,
    stale BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO reconciliation_health(id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS reconciliation_incidents (
    incident_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    venue_order_id TEXT,
    local_order_id TEXT,
    status TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'critical',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reconciliation_open_incident
    ON reconciliation_incidents(kind, venue_order_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_reconciliation_incident_status
    ON reconciliation_incidents(status, last_seen_at DESC);
