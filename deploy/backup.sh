#!/usr/bin/env bash
set -euo pipefail
umask 077
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

provided_backup_source="${BACKUP_DATABASE_SOURCE-}"
provided_database_url="${DATABASE_URL-}"
if [ -f backend/.env ]; then
  set -a
  . backend/.env
  set +a
fi
if [ -n "$provided_backup_source" ]; then
  BACKUP_DATABASE_SOURCE="$provided_backup_source"
fi
if [ -n "$provided_database_url" ]; then
  DATABASE_URL="$provided_database_url"
fi
backup_dir="${BACKUP_DIR:-backups}"
mkdir -p "$backup_dir"
backup="${backup_dir%/}/trading-postgres-$(date -u +%Y%m%dT%H%M%SZ).dump"

case "${BACKUP_DATABASE_SOURCE:-compose}" in
  compose)
    compose_file="${COMPOSE_FILE:-backend/docker-compose.yml}"
    db_service="${POSTGRES_SERVICE:-db}"
    db_name="${POSTGRES_DB:-vesper}"
    db_user="${POSTGRES_USER:-vesper}"
    docker compose -f "$compose_file" exec -T "$db_service" \
      pg_dump --format=custom --no-owner --username="$db_user" --dbname="$db_name" \
      > "$backup"
    ;;
  url)
    : "${DATABASE_URL:?Set DATABASE_URL when BACKUP_DATABASE_SOURCE=url}"
    docker run --rm -e DATABASE_URL \
      postgres:16-alpine sh -c 'pg_dump --format=custom --no-owner --dbname="$DATABASE_URL"' \
      > "$backup"
    ;;
  *)
    echo "Unsupported BACKUP_DATABASE_SOURCE: ${BACKUP_DATABASE_SOURCE}" >&2
    echo "Use compose for the local Docker database or url for an external PostgreSQL service." >&2
    exit 2
    ;;
esac

if [ ! -s "$backup" ]; then
  echo "Backup was empty: $backup" >&2
  exit 1
fi

sha256sum "$backup" > "$backup.sha256"
echo "Created $backup"
echo "Created $backup.sha256"
if [ -n "${BACKUP_UPLOAD_COMMAND:-}" ]; then
  upload_backup() {
    # The file path is available to the command as $1.
    sh -c "$BACKUP_UPLOAD_COMMAND" -- "$1"
  }
  upload_backup "$backup"
  upload_backup "$backup.sha256"
  echo "Uploaded $backup using BACKUP_UPLOAD_COMMAND"
else
  echo "WARNING: backups remain local; configure BACKUP_UPLOAD_COMMAND for remote object storage"
fi
