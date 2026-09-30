#!/bin/sh
set -eu

umask 077

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${BACKUP_S3_URI:?Set BACKUP_S3_URI in backend/backup.env}"
: "${AWS_REGION:?Set AWS_REGION in backend/backup.env}"

postgres_host="${POSTGRES_HOST:-db}"
postgres_port="${POSTGRES_PORT:-5432}"
backup_dir="${BACKUP_DIR:-/backups}"
interval="${BACKUP_INTERVAL_SECONDS:-900}"
local_retention_days="${BACKUP_LOCAL_RETENTION_DAYS:-2}"

mkdir -p "$backup_dir"
export PGPASSWORD="$POSTGRES_PASSWORD"

while :; do
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="$backup_dir/trading-postgres-$timestamp.dump"
  checksum="$backup.sha256"
  remote_base="${BACKUP_S3_URI%/}/$(basename "$backup")"

  if pg_isready -h "$postgres_host" -p "$postgres_port" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    echo "[$(date -u +%FT%TZ)] Creating $backup"
    if pg_dump --format=custom --no-owner \
      --host="$postgres_host" --port="$postgres_port" \
      --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" > "$backup"; then
      sha256sum "$backup" > "$checksum"
      aws s3 cp "$backup" "$remote_base" --region "$AWS_REGION" --sse AES256
      aws s3 cp "$checksum" "$remote_base.sha256" --region "$AWS_REGION" --sse AES256
      echo "[$(date -u +%FT%TZ)] Uploaded $remote_base"
      find "$backup_dir" -type f -name 'trading-postgres-*.dump*' \
        -mtime "+$local_retention_days" -delete
    else
      echo "[$(date -u +%FT%TZ)] pg_dump failed" >&2
      rm -f "$backup" "$checksum"
    fi
  else
    echo "[$(date -u +%FT%TZ)] PostgreSQL is not ready; retrying" >&2
  fi

  sleep "$interval"
done
