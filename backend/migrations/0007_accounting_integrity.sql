-- Financial amounts have different meanings.  Keep notional non-negative and
-- store signed PnL/cash movement in dedicated columns.
ALTER TABLE execution_ledger ADD COLUMN IF NOT EXISTS entry_cost DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(entry_cost >= 0);
ALTER TABLE execution_ledger ADD COLUMN IF NOT EXISTS gross_proceeds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(gross_proceeds >= 0);
ALTER TABLE execution_ledger ADD COLUMN IF NOT EXISTS realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE execution_ledger ADD COLUMN IF NOT EXISTS cash_delta DOUBLE PRECISION NOT NULL DEFAULT 0;

ALTER TABLE capital_reservations DROP CONSTRAINT IF EXISTS capital_reservations_state_check;
ALTER TABLE capital_reservations ADD CONSTRAINT capital_reservations_state_check
    CHECK (state IN ('reserved','submitted','accepted','uncertain','partially_filled','cancel_requested','filled','released','settled'));

CREATE OR REPLACE VIEW active_capital_reservations AS
SELECT * FROM capital_reservations
WHERE state IN ('reserved','submitted','accepted','uncertain','partially_filled','cancel_requested','filled');

CREATE OR REPLACE FUNCTION vesper_reservation_transition() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.filled_capital < OLD.filled_capital THEN
        RAISE EXCEPTION 'capital reservation filled amount cannot decrease';
    END IF;
    IF NEW.filled_capital > NEW.requested_capital + NEW.fee_reserve + 1e-12 THEN
        RAISE EXCEPTION 'capital reservation filled amount exceeds reserved amount';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.state IN ('released','settled') AND NEW.state <> OLD.state THEN
        RAISE EXCEPTION 'terminal capital reservation cannot transition';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.state = 'filled' AND NEW.state NOT IN ('filled','settled','released') THEN
        RAISE EXCEPTION 'filled capital reservation cannot regress';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS capital_reservation_transition_guard ON capital_reservations;
CREATE TRIGGER capital_reservation_transition_guard
BEFORE UPDATE ON capital_reservations
FOR EACH ROW EXECUTE FUNCTION vesper_reservation_transition();

CREATE INDEX IF NOT EXISTS idx_capital_reservations_decision ON capital_reservations(decision_id);
