# Druid Query Test Harness — Dev Plan for Cursor Agent

## Purpose

Build a local Druid environment + tooling that can:
1. Spin up a real Druid cluster and load synthetic/enriched datasources.
2. Take a SQL query (ours, or LLM-generated) and report whether it's valid Druid SQL — and why, if not.
3. Be run in bulk (batch mode) against a JSONL/CSV file of queries, for validating training-set candidates and for evaluating fine-tuned model output later.

**Explicitly out of scope for the agent:** any LLM code, training code, dataset-generation logic (NL question generation, schema-variation generation content, etc.). The agent builds the *harness and the DB*, not the ML pipeline or the data itself. It should stop at "given a schema + query, tell me if it's valid" and "given a schema description, load it into Druid."

---

## Software and memory constraints
- We are using python3.12 in this environment and a venv is available in **/home/nz-dgx-spark-01/Documents/Nyalazone/druid_llm_finetuning/venv**

- When installing and setting up the Druid harness, **ensure** that the unified RAM **does not cross 70 GB**. If you estimate that any work/process in this plan might exceed these memory requirements then inform me. I will tell you when to resume when you have the full 122GB unified RAM available.

## Phase 0 — Environment

- [ ] `docker-compose.yml` for a single-node Druid cluster (Coordinator, Overlord, Broker, Router, Historical, MiddleManager, plus ZooKeeper + metadata store — Derby is fine for local dev, don't bother with Postgres/MySQL metadata store unless we hit issues).
- [ ] **Pin the Druid image to version 35.0.0** everywhere (compose file, README, CI if any) — Druid SQL behavior (function names, `ORDER BY` rules, etc.) has drifted across versions, so training the model against the wrong version's dialect defeats the purpose. If the version ever changes, that's a deliberate decision to revisit, not a silent drift.
- [ ] Sensible resource limits in the compose file (heap sizes, JVM opts) sized for the DGX Spark's unified memory, kept modest since datasources will hold minimal data (see Phase 1).
- [ ] **No persistent background service.** This must not run as a daemon or auto-start (no `restart: always`/`unless-stopped` in the compose file, no boot-time service, no "start on login"). It runs only when explicitly brought up and must be fully torn down (containers stopped, not just idle) when not in active use, since the RAM is needed for other work on this machine. `make down` should actually stop the containers, not just pause them.
- [ ] `make up` / `make down` / `make reset` (or equivalent scripts) — `reset` should nuke deep storage + metadata store so we can get back to a clean slate cheaply. Consider an optional `make status` that reports whether the cluster is currently up, so it's obvious at a glance if it was accidentally left running.
- [ ] Health-check script that polls the Router/Coordinator until the cluster is actually ready to accept ingestion and queries (Druid takes a bit to come up — don't just check the container is running).
- [ ] README documenting how to bring the cluster up, tear it down, and where logs live.

## Phase 1 — Datasource / schema loading tooling

This is the piece that lets *us* (not the agent) later feed in whatever synthetic schemas we generate for the training set.

- [ ] A defined **input format** for "here's a datasource to create" — e.g. a YAML/JSON spec per datasource: table name, columns (name, type, and whether it's `__time`), and a way to point at seed data (CSV/JSON/Parquet file, or an inline row generator config).
- [ ] **Ingestion method: SQL-based ingestion (`INSERT INTO ... SELECT ... FROM extern(...)`, or `REPLACE`), not native batch JSON specs.** Decision: since the harness only needs to validate query *syntax* correctness (not result correctness), datasources can hold a minimal number of rows — just enough to exercise every column/type combination and support `GROUP BY`/`ORDER BY` behavior, not realistic data volumes. SQL-based ingestion is easier to generate programmatically from the datasource spec and keeps the loader simpler.
  - Submits the SQL ingestion statement via the SQL-based ingestion endpoint and **polls until segments are fully available** (Druid ingestion is async — querying a datasource before segments are published will give false negatives on otherwise-valid queries).
  - Fails loudly with a clear error if ingestion fails, rather than silently leaving a partial datasource.
- [ ] Deterministic seed data generation helper (fixed random seed) so re-running `reset` + reload gives byte-identical datasources — important for reproducible test results. Since data volumes are small (syntax-focused, not result-focused), this should also make loads fast.
- [ ] A way to list/inspect currently loaded datasources and their schemas from the CLI (wraps Druid's `INFORMATION_SCHEMA` queries), so we can sanity-check what's actually loaded without going into the Druid console.
- [ ] Support for **dropping/reloading a single datasource** without nuking the whole cluster — we'll be iterating on schemas a lot.

## Phase 2 — Query validation harness (the core piece)

- [ ] A function/CLI command: given a single SQL query string (+ target datasource context implied by the query itself), execute it against the Druid Broker's SQL endpoint and report:
  - `VALID` — ran successfully, with row count and a truncated result sample.
  - `INVALID` — with Druid's actual error message surfaced verbatim (this is the signal we care about most for both dataset curation and later model eval).
  - `TIMEOUT` — query exceeded a configurable time limit (protect the harness from a runaway/cartesian-join query hanging the whole batch run).
- [ ] **Batch mode**: input = JSONL where each line has at minimum `{query, expected_datasource(s)}`, optionally `{id, tags}` for traceability back to the dataset entry. Output = JSONL with the same records plus `status`, `error_message`, `latency_ms`, `row_count`.
- [ ] Sensible query-level guardrails so a bad query can't take down the harness: per-query timeout, a max-rows-returned cap on fetches, and the harness process itself should not crash on a single bad query — log and move to the next. Joins aren't expected to appear in the training data or inference questions, so this isn't primarily about cartesian-join protection — but keep the timeout as a general safety net against any unexpectedly expensive or hanging query.
- [ ] Concurrency control for batch runs (parallel workers hitting the Broker) with a configurable limit, since Druid's Broker has finite query capacity.
- [ ] Clear CLI summary at the end of a batch run: total, valid, invalid, timeout, plus a breakdown of the most common error message patterns (this alone will be useful for spotting systematic issues in either our dataset or the model's output).

## Phase 3 — Handling test-specific edge cases

A few things that are specific to *why* we're building this (Druid quirks + LLM output), which a generic "run this SQL" harness won't handle by default:

- [ ] **Time anchoring: no special handling needed — use real Druid `CURRENT_TIMESTAMP` as-is.** Decision: since the LLM prompts include the actual current date/day/time at generation time, relative-time questions ("last 7 days") should produce queries using `CURRENT_TIMESTAMP`-based expressions (e.g. `__time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY`), which the harness just executes normally — Druid resolves `CURRENT_TIMESTAMP` against real wall-clock time, and since we only care about syntactic validity (not the actual date range returned), there's no need to mock or inject a fixed "as-of" time. Fixed-period questions ("between May 20–28, 2024") should produce queries with literal date/timestamp constants directly and likewise need no special harness support. One implication worth keeping in mind: this means dataset entries with relative-time phrasing are tied to *when* they were generated (the prompt's stated "current date" and the query's relative-time logic should conceptually match) — not a harness concern, but worth remembering during dataset curation.
- [ ] **Negative/trap query validation mode.** Some training examples will intentionally be "this is what NOT to do" pairs (e.g., illegal `ORDER BY`). For those, the harness should support asserting a query is *expected* to fail, and ideally checking that it fails for the *expected reason* (e.g., matching the error message against a substring/pattern), not just "it errored." This distinguishes "correctly rejected for the quirk we're testing" from "failed for an unrelated reason (typo, wrong table name)."
- [ ] **Result correctness is explicitly out of scope.** No golden set, no expected-result comparison. "Runs without a Druid error" is the full definition of `VALID` for this harness. Don't build any result-checksum/expected-row-count machinery.

## Phase 4 — Reporting / feedback loop

- [ ] Export batch-run results to a format we can easily eyeball and filter (CSV or a simple local HTML/JSON report) — grouped by error type, so during dataset curation we can quickly find "which of my 500 candidate queries are actually invalid" without reading 500 lines of JSONL.
- [ ] Simple diffing between two batch runs (e.g., same query set against two Druid versions, or before/after a schema change) to catch regressions.

## Suggested repo structure

```
druid-harness/
  docker/                # compose file, Druid config overrides
  scripts/                # up/down/reset/health-check
  harness/
    loader/                # datasource spec -> ingestion spec -> load + poll
    validator/              # single-query + batch validation, timeout/guardrails
    reporting/               # summarize batch results
  specs/                   # example datasource specs (agent creates a couple of samples; we generate the rest)
  tests/                    # harness's own tests (not training data) — e.g. "does the loader correctly load a 5-column datasource", "does the validator correctly flag a bad ORDER BY"
  README.md
```

## Decisions (resolved)

1. **Ingestion method:** SQL-based ingestion (`INSERT`/`REPLACE ... SELECT ... FROM extern(...)`), with minimal row counts per datasource — the harness only needs to prove queries are syntactically valid, not that they return correct results, so realistic data volumes aren't necessary even for complex/enriched schemas.
2. **Time anchoring:** no mocking or fixed "as-of" time. Relative-time questions ("last 7 days") map to `CURRENT_TIMESTAMP`-based expressions and are validated against real wall-clock time; fixed-period questions use literal date/timestamp constants directly. No special harness support required either way.
3. **Result correctness:** out of scope entirely. "Valid Druid SQL" (executes without a Druid error) is the full definition of success for this harness. No golden set, no expected-result machinery.
4. **Druid version:** pinned to **35.0.0**.
5. **Hardware / lifecycle:** runs on the DGX Spark, but strictly on-demand — no background service, no auto-start, must be fully stopped (not idling) when not in active use, since the RAM is shared with other work.

## Other confirmed requirements

- **Segment-availability polling** after ingestion — without it you'll get flaky "invalid" results on freshly-loaded datasources that are actually just not queryable yet.
- **Negative-example validation** (Phase 3) — since a chunk of the dataset is deliberately "here's the wrong way," the harness needs a mode for confirming a query *correctly fails* for the *right reason*, not just pass/fail.
- **Reset/teardown discipline** — with potentially hundreds of synthetic schemas being iterated on, disk usage and metadata-store cruft will build up fast without a clean `reset` path.
- **Per-query timeout** kept as a general safety net (joins aren't expected to appear in this dataset, so it's not primarily about cartesian-join protection — just cheap insurance against any unexpectedly expensive or hanging query).
- **Version pinning to 35.0.0** — carried through the compose file, README, and any docs the agent writes, so it's never ambiguous which dialect this harness validates against.

Nothing here requires touching the model or training code — all of the above is harness/infra work, consistent with keeping the agent scoped to setup only.