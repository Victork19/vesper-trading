#!/usr/bin/env bash
set -euo pipefail
mkdir -p backups
docker compose -f backend/docker-compose.yml stop trading pipeline
docker run --rm -v vesper-trading_trading-data:/data -v "$PWD/backups":/backup alpine tar czf /backup/trading-memory-$(date -u +%Y%m%dT%H%M%SZ).tgz -C /data .
docker compose -f backend/docker-compose.yml start trading pipeline
