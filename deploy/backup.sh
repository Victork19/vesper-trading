#!/usr/bin/env bash
set -euo pipefail
umask 077
mkdir -p backups
if [ -f backend/.env ]; then
  set -a
  . backend/.env
  set +a
fi
: "${DATABASE_URL:?Set DATABASE_URL to the Supabase Supavisor session-mode connection string}"
backup="backups/trading-postgres-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker run --rm -e DATABASE_URL postgres:16-alpine sh -c 'pg_dump --format=custom --no-owner --dbname="$DATABASE_URL"' > "$backup"
echo "Created $backup"
