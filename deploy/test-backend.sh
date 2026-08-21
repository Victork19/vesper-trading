#!/usr/bin/env bash
set -euo pipefail

test_container=""
cleanup() {
  if [ -n "$test_container" ]; then
    docker rm -f "$test_container" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [ -z "${VESPER_TEST_DATABASE_URL:-}" ]; then
  test_container="vesper-test-postgres-${RANDOM}"
  test_port="${VESPER_TEST_POSTGRES_PORT:-55432}"
  docker run -d --name "$test_container" -p "${test_port}:5432" \
    -e POSTGRES_USER=vesper -e POSTGRES_PASSWORD=vesper -e POSTGRES_DB=vesper_test \
    postgres:16-alpine >/dev/null
  VESPER_TEST_DATABASE_URL="postgresql://vesper:vesper@127.0.0.1:${test_port}/vesper_test"
fi
if [ -n "${DATABASE_URL:-}" ] && [ "${VESPER_TEST_DATABASE_URL}" = "${DATABASE_URL}" ]; then
  echo "Refusing to use the production DATABASE_URL for tests." >&2
  exit 1
fi
case "${VESPER_TEST_DATABASE_URL}" in
  *supabase.com*|*pooler.supabase.com*)
    echo "Refusing to run destructive tests against a Supabase URL. Use a disposable test database." >&2
    exit 1
    ;;
esac

docker build -f backend/Dockerfile.test -t vesper-backend-tests backend
for attempt in $(seq 1 30); do
  if docker run --rm --network host -e DATABASE_URL="${VESPER_TEST_DATABASE_URL}" vesper-backend-tests \
    python -c "import os, psycopg; connection=psycopg.connect(os.environ['DATABASE_URL']); connection.close()"; then
    break
  fi
  if [ "${attempt}" -eq 30 ]; then
    echo "Timed out waiting for the disposable Postgres database." >&2
    exit 1
  fi
  sleep 2
done
docker run --rm --network host \
  -e DATABASE_URL="${VESPER_TEST_DATABASE_URL}" \
  -e VESPER_AUTH_REQUIRED=true \
  -e VESPER_API_KEY=client-test-key \
  -e VESPER_ADMIN_KEY=admin-test-key \
  vesper-backend-tests
