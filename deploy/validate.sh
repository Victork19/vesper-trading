#!/usr/bin/env bash
set -euo pipefail

: "${VESPER_API_URL:?Set VESPER_API_URL}"
: "${VESPER_API_KEY:?Set VESPER_API_KEY}"
test "${VESPER_API_KEY}" != "replace-with-a-long-random-api-key"
test -n "${VESPER_ADMIN_KEY:-}" || echo "warning: VESPER_ADMIN_KEY is not exported to this shell"

docker compose -f backend/docker-compose.yml config >/dev/null
curl --fail --silent --show-error "${VESPER_API_URL}/health" >/dev/null
curl --fail --silent --show-error -H "X-Vesper-Key: ${VESPER_API_KEY}" "${VESPER_API_URL}/ready" >/dev/null
curl --fail --silent --show-error -H "X-Vesper-Key: ${VESPER_API_KEY}" "${VESPER_API_URL}/observability" >/dev/null
curl --fail --silent --show-error -H "X-Vesper-Key: ${VESPER_API_KEY}" "${VESPER_API_URL}/alerts" >/dev/null
echo "Vesper deployment validation passed"
