#!/usr/bin/env bash
# Poll Router + Coordinator until the cluster accepts SQL.
# Container "running" is not enough — Druid takes a while to come up.
set -euo pipefail

ROUTER_URL="${DRUID_ROUTER_URL:-http://127.0.0.1:8888}"
COORDINATOR_URL="${DRUID_COORDINATOR_URL:-http://127.0.0.1:8081}"
TIMEOUT_SEC="${DRUID_HEALTH_TIMEOUT_SEC:-300}"
SLEEP_SEC=3

deadline=$((SECONDS + TIMEOUT_SEC))

echo "Waiting for Druid 35.0.0 cluster (timeout ${TIMEOUT_SEC}s)..."

check_http() {
  local url="$1"
  curl -sf --max-time 5 "$url" >/dev/null 2>&1
}

check_sql() {
  curl -sf --max-time 10 \
    -X POST "${ROUTER_URL}/druid/v2/sql" \
    -H "Content-Type: application/json" \
    -d '{"query":"SELECT CURRENT_TIMESTAMP","resultFormat":"object"}' \
    >/dev/null 2>&1
}

check_indexer() {
  curl -sf --max-time 5 "${ROUTER_URL}/druid/indexer/v1/leader" >/dev/null 2>&1
}

while (( SECONDS < deadline )); do
  router_ok=0
  coord_ok=0
  sql_ok=0
  indexer_ok=0
  check_http "${ROUTER_URL}/status/health" && router_ok=1
  check_http "${COORDINATOR_URL}/status/health" && coord_ok=1
  if (( router_ok )); then
    check_sql && sql_ok=1
    check_indexer && indexer_ok=1
  fi
  if (( router_ok && coord_ok && sql_ok && indexer_ok )); then
    echo "Cluster ready: Router, Coordinator, SQL, and Overlord leader are up."
    exit 0
  fi
  echo "  not ready yet (router=${router_ok} coordinator=${coord_ok} sql=${sql_ok} overlord=${indexer_ok}); retrying..."
  sleep "${SLEEP_SEC}"
done

echo "Timed out after ${TIMEOUT_SEC}s waiting for Druid to become healthy." >&2
echo "  Router health:      ${ROUTER_URL}/status/health" >&2
echo "  Coordinator health: ${COORDINATOR_URL}/status/health" >&2
echo "  SQL:                POST ${ROUTER_URL}/druid/v2/sql" >&2
echo "Container status:" >&2
docker compose -f "$(dirname "$0")/../docker/docker-compose.yml" ps -a >&2 || true
echo "Run: make logs" >&2
exit 1
