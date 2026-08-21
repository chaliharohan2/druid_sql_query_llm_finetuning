# Druid Query Test Harness

Local **Apache Druid 35.0.0** cluster plus a CLI that loads JSON datasource specs and reports whether a SQL query is valid Druid SQL.

This validates query *syntax* against a real Broker. It does not check result correctness, generate training data, or run any LLM code.

Dialect is pinned to **35.0.0**. Changing the image tag is a deliberate decision, not a silent upgrade.

## Memory and lifecycle

The compose stack is sized to nano-quickstart heaps (about **6–8 GB** peak: Druid + ZooKeeper + Postgres + Docker). That stays well under the 70 GB unified-RAM cap on this machine.

**On-demand only.** There is no `restart: always`, no boot-time service, and no start-on-login. Bring the cluster up when you need it and tear it down when you are done so the RAM is free for other work.

| Command | What it does |
| --- | --- |
| `make up` | Start containers and wait until Router, Coordinator, SQL, and Overlord are actually ready |
| `make down` | Stop and **remove** containers (not pause). Named volumes are kept. RAM is freed. |
| `make reset` | `down` plus delete metadata + deep-storage volumes (clean slate) |
| `make status` | `docker compose ps` plus Router/Coordinator health |
| `make logs` | Follow container logs |
| `make test` | `up` → pytest against the live cluster → `down` (down even if tests fail) |

## Prerequisites

- Docker with Compose v2, and a Unix account in the `docker` group (`sudo usermod -aG docker $USER`, then log out and back in)
- Python 3.12 venv at `/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/venv`

```bash
cd druid-harness
/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/venv/bin/pip install -e ".[dev]"
```

## Cluster

```bash
cd druid-harness
make up
make status
make down    # when finished; do not leave it idling
```

- Console / Router: [http://127.0.0.1:8888](http://127.0.0.1:8888)
- Coordinator: [http://127.0.0.1:8081](http://127.0.0.1:8081)
- Images: locally built `druid-harness:35.0.0` (official 35.0.0 tarball + Temurin 17, native ARM64/amd64), `zookeeper:3.8.4`, `postgres:16-alpine`
- Docker Hub's `apache/druid:35.0.0` is **linux/amd64 only** and will not start on this ARM64 DGX Spark — that is why the image is built locally
- Ports are bound to localhost only
- First `make up` builds the image (downloads the Druid tarball) and can take several minutes

**Metadata store is PostgreSQL 16**, not Derby. Embedded Derby cannot be shared across the Coordinator, Broker, Historical, and MiddleManager containers. Overlord is colocated with Coordinator (nano-quickstart `coordinator-overlord`), which matches Apache’s 35.0.0 compose.

### Logs

- `make logs` — all services
- Inside containers: `/opt/druid/var` (Docker volumes `*_var`)
- Indexing logs: volume `druid_shared` → `/opt/shared/indexing-logs`

## CLI

Default Router URL: `http://127.0.0.1:8888` (override with `--router-url` or `DRUID_ROUTER_URL`).

```bash
# Load / inspect / drop datasources
druid-harness load specs/example_events.json
druid-harness reload specs/example_events.json
druid-harness list
druid-harness inspect example_events
druid-harness drop example_events

# Single query
druid-harness validate --query 'SELECT channel, COUNT(*) FROM example_events GROUP BY channel'
druid-harness validate --file query.sql --timeout-seconds 30 --max-rows 100

# Batch JSONL → JSONL + CSV report
druid-harness validate-batch queries.jsonl --output results.jsonl --report results.csv --workers 4

# Diff two batch runs
druid-harness diff run_a.jsonl run_b.jsonl --output diff.csv
```

### Datasource spec (JSON)

```json
{
  "name": "example_events",
  "columns": [
    {"name": "__time", "type": "long", "is_time": true},
    {"name": "channel", "type": "string"},
    {"name": "delta", "type": "long"}
  ],
  "seed": {
    "mode": "generate",
    "row_count": 20,
    "random_seed": 42
  }
}
```

File seed (CSV or JSON/JSONL), path relative to the spec file or cwd:

```json
"seed": {
  "mode": "file",
  "path": "data/example_metrics.csv",
  "format": "csv"
}
```

Allowed column types: `long`, `float`, `double`, `string`. Exactly one column must have `"is_time": true`. Generator timestamps are millis since epoch. Ingestion is SQL-based (`INSERT`/`REPLACE ... SELECT ... FROM EXTERN(...)`), `PARTITIONED BY ALL`, and the loader waits until segments are queryable.

`load` fails if the datasource already exists. `reload` uses `REPLACE ... OVERWRITE ALL`.

### Batch JSONL

Each input line needs `query`. Optional: `id`, `tags`, `expected_datasources` (copied through, not enforced), `expected_status`, `expected_error_substring`, `expected_error_pattern`.

Output adds `status` (`VALID` / `INVALID` / `TIMEOUT`), `error_message`, `latency_ms`, `row_count`.

Trap/negative examples: set `expected_status` to `INVALID` and supply at least one of `expected_error_substring` or `expected_error_pattern`. Output then also includes `assertion_passed` and `assertion_detail`.

Defaults: 30s per-query timeout, 4 parallel workers, 100-row result sample.

## Python API

Same validator as the CLI. Cluster must already be up (`make up`).

Edit [`run_query.py`](run_query.py) and run it:

```bash
cd druid-harness
python run_query.py
```

Or call it from another script / notebook:

```python
from harness.api import run_query

result = run_query(
    "SELECT channel, COUNT(*) FROM example_events GROUP BY channel"
)
print(result.status)          # VALID / INVALID / TIMEOUT
print(result.error_message)   # Druid's message, verbatim, if INVALID
print(result.row_count)
print(result.sample)          # truncated rows
```

`run_queries([...])` runs several statements on one connection. `timeout_seconds` and `max_rows` are keyword-only and match the CLI defaults (30s, 100 rows).

## Tests

```bash
cd druid-harness
make test
```

Requires Docker. The cluster is started, live ingestion/query tests run, then containers are stopped.
