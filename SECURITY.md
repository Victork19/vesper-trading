# Security and live-trading policy

Vesper Trading is paper-first. Keep `LIVE_TRADING_ENABLED=false` until the operator has completed historical validation, paper trading, shadow trading, wallet verification, and a live-capital review.

Never commit:

- `backend/.env`
- `POLYMARKET_PRIVATE_KEY`
- Polymarket API secrets
- Sibyl credentials
- SQLite databases

The language model must never receive private keys and must never determine final order size. Live execution must stay behind a separate signer, a capital limit, an order-size limit, daily/weekly kill switches, and operator approval.

Use a dedicated wallet for testing. Store production secrets in a VPS secret manager or protected environment, not Git.
