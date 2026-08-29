ALTER TABLE orders ADD COLUMN IF NOT EXISTS filled_fees DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(filled_fees >= 0);
ALTER TABLE execution_circuit ADD COLUMN IF NOT EXISTS probe_owner TEXT;
ALTER TABLE execution_circuit ADD COLUMN IF NOT EXISTS probe_until TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS execution_ledger (
    event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    order_id TEXT,
    decision_id TEXT,
    event_type TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    notional DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(notional >= 0),
    fee DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(fee >= 0),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_execution_ledger_order ON execution_ledger(order_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_execution_ledger_decision ON execution_ledger(decision_id, observed_at);

CREATE OR REPLACE FUNCTION vesper_execution_fill_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'execution fills are append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS execution_fills_no_update ON execution_fills;
CREATE TRIGGER execution_fills_no_update BEFORE UPDATE OR DELETE ON execution_fills
FOR EACH ROW EXECUTE FUNCTION vesper_execution_fill_immutable();

CREATE OR REPLACE FUNCTION vesper_execution_ledger_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'execution ledger is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS execution_ledger_no_update ON execution_ledger;
CREATE TRIGGER execution_ledger_no_update BEFORE UPDATE OR DELETE ON execution_ledger
FOR EACH ROW EXECUTE FUNCTION vesper_execution_ledger_immutable();
