ALTER TABLE market_observations
  ADD COLUMN IF NOT EXISTS eligible BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_market_observations_quality_window
  ON market_observations(eligible, observed_at);
