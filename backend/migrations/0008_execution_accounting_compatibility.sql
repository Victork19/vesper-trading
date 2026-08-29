-- Compatibility guard for legacy settlement writers.  New code writes signed
-- PnL/cash movement to dedicated columns; this trigger prevents an older
-- writer from violating the non-negative notional invariant.
ALTER TABLE execution_ledger ADD COLUMN IF NOT EXISTS realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE execution_ledger ADD COLUMN IF NOT EXISTS cash_delta DOUBLE PRECISION NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION vesper_normalize_signed_settlement() RETURNS trigger AS $$
BEGIN
    IF NEW.event_type = 'settlement' AND NEW.notional < 0 THEN
        NEW.realized_pnl = NEW.notional;
        NEW.cash_delta = NEW.notional;
        NEW.notional = 0;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS execution_ledger_signed_settlement ON execution_ledger;
CREATE TRIGGER execution_ledger_signed_settlement
BEFORE INSERT ON execution_ledger
FOR EACH ROW EXECUTE FUNCTION vesper_normalize_signed_settlement();
