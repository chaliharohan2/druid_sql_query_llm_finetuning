# Problem
The problem is that many LLMs write logical queries even with large schema enrichments but what they do not do well is understand some druid quirks like `__time` is the main and only timestamp column (everything else either has to be stored as strings or epochs), they do not know the syntax for any of the druid time related functions, do not know that ordering by a non-timestamp column is not allowed unless grouping is happening, etc.

# Target dialect
**Apache Druid 35.0.0**, matching the pin in `druid-harness/`. Every behavioural claim in this document has been executed against that cluster — see [Verified quirks](#verified-quirks-druid-3500). Nothing here is written from memory, and nothing should be added to it that has not been run.

# Data Format
Standard chat-style SFT examples (system/user/assistant), matching the template we will use at inference time.

The system message is a short fixed preamble followed by the schema block. The assistant replies with **bare SQL** — no markdown fence, no prose, no explanation.

```json
{
  "messages": [
    {"role": "system", "content": "You write Apache Druid SQL.\nReturn only the query.\n\n# Database Schema\n\n## Table: `events`\n### Columns:\n`__time` (TIMESTAMP): Event time...\n..."},
    {"role": "user", "content": "Show hourly average latency for 'checkout' events in the last 7 days"},
    {"role": "assistant", "content": "SELECT TIME_FLOOR(__time, 'PT1H') AS \"hour\", AVG(latency_ms) AS avg_latency\nFROM events\nWHERE event_type = 'checkout'\n  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY\nGROUP BY 1\nORDER BY 1"}
  ]
}
```

Two things in that example are load-bearing and easy to get wrong:

- `AS "hour"` is **quoted**. `AS hour` is a syntax error in Druid (see [reserved-word aliases](#verified-quirks-druid-3500)).
- "last 7 days" resolves against `CURRENT_TIMESTAMP`, not against a hardcoded anchor date. Hardcoding an anchor for a relative question teaches the model to invent dates.

## Schema
This is the rough format of the schema you can use in the system prompt. You do not have to stick to it exactly. Just keep the main elements. This is the skeleton of the schema format:

---
# Database Schema

## Table: `events`
### Columns:
`__time` (TIMESTAMP): Explanation of column
`user_id` (VARCHAR): Explanation of column
`event_type` (VARCHAR): Explanation of column
`latency_ms` (BIGINT): Explanation of column
...

## Table: `catalogue`
### Columns:
`__time` (TIMESTAMP): Explanation of column
`product_tag` (BIGINT): Explanation of column
...
---

**Type vocabulary is Druid SQL types** — `TIMESTAMP`, `VARCHAR`, `BIGINT`, `DOUBLE`, `FLOAT` — exactly what `INFORMATION_SCHEMA.COLUMNS` reports, so a schema block can be generated straight off a live cluster. Do not mix in the native names (`STRING`, `LONG`); pick one vocabulary and hold it.

**Multi-value dimensions and JSON-as-string columns both report as `VARCHAR`.** The type alone cannot tell the model what it is looking at, so the column *description* has to say so explicitly:

```
`tags` (VARCHAR): Multi-value dimension. Deployment tags, e.g. prod/web/canary.
`payload` (VARCHAR): JSON object stored as a string. Parse with PARSE_JSON before JSON_VALUE.
```

# Verified quirks (Druid 35.0.0)

Executed against the harness cluster. These are the rules the dataset exists to teach.

## Enforced — the query fails

| Habit | What Druid 35.0.0 does |
| --- | --- |
| `ORDER BY revenue DESC` with no `GROUP BY` | `SQL query requires ordering a table by non-time column [[revenue]], which is not supported` |
| …the same with `LIMIT 10` added | **Still fails.** A `LIMIT` does not rescue it. |
| …the same wrapped in a plain subquery | Still fails; the restriction propagates. |
| `AS hour`, `AS day`, `AS value`, `AS timestamp`, `AS count` | Syntax error. Reserved words must be quoted: `AS "hour"`. |
| `TIMESTAMP '2024-01-01T00:00:00Z'` | `Illegal TIMESTAMP literal … not in format 'yyyy-MM-dd HH:mm:ss'`. ISO-8601 with `T`/`Z` is rejected. Date-only (`TIMESTAMP '2024-01-01'`) is fine. |
| `GROUP BY <select alias>` | `Column 'h' not found`. Use the ordinal or repeat the expression. Note the asymmetry: `HAVING` and `ORDER BY` **can** see select aliases, `GROUP BY` cannot. |
| `ORDER BY <ungrouped column>` alongside `GROUP BY` | `Expression 'clicks' is not being grouped` |
| `UNNEST(STRING_TO_MV(col, ','))` | `Cannot apply 'UNNEST' to arguments of type 'UNNEST(<VARCHAR>)'`. Use `UNNEST(STRING_TO_ARRAY(col, ','))`. |
| `MV_TO_ARRAY(<expression>)` | Only accepts a bare column identifier, not an expression. |
| `LOOKUP(col, 'name')` for an unregistered lookup | `Lookup [name] not found` — fails at plan time. |
| A column literally named `value` or `language` written unquoted | Syntax error. Reserved words are reserved as *identifiers* too, not just as aliases: `AVG("value")`. Probing all 493 column names across the 69 datasources turned up exactly these two. |
| `MAX(string_col)` / `MIN(string_col)` | `Aggregation [MAX] does not support type [STRING]`. Use `LATEST(col, 64)` / `EARLIEST(col, 64)`. |
| Nested aggregates, `TOP n`, `DISTINCT ON` | Rejected. |

**Functions that do not exist in Druid**, each a clean contrastive pair with a working replacement:

| Reflex | Druid equivalent |
| --- | --- |
| `NOW()` | `CURRENT_TIMESTAMP` |
| `DATEADD(day, -7, x)` / `DATE_SUB(x, 7)` | `x - INTERVAL '7' DAY` or `TIMESTAMPADD(DAY, -7, x)` |
| `DATEDIFF(day, a, b)` | `TIMESTAMPDIFF(DAY, a, b)` |
| `TO_CHAR(t, …)` / `DATE_FORMAT(t, …)` | `TIME_FORMAT(t, 'yyyy-MM-dd')` |
| `DATE(t)` | `CAST(t AS DATE)` |
| `IF(c, a, b)` | `CASE WHEN c THEN a ELSE b END` |
| `ILIKE` / `RLIKE` | `LOWER(x) LIKE …` / `REGEXP_LIKE(x, …)` |
| `STDDEV`, `VARIANCE`, `MEDIAN`, `PERCENTILE_CONT` | not available as written; use the datasketches forms |

## Silent — the query runs and is wrong

These are the dangerous ones, because "it returned VALID" does not catch them.

| Habit | What actually happens |
| --- | --- |
| `JSON_VALUE(json_string_col, '$.region')` | **VALID, returns NULL for every row.** Must be `JSON_VALUE(PARSE_JSON(col), '$.region')` or `TRY_PARSE_JSON`. |
| `__time >= '2024-01-01'` (bare string) | VALID via implicit cast, but fragile and ambiguous. Prefer a `TIMESTAMP` literal. |
| Filtering/sorting an epoch or ISO-string column with `BETWEEN` timestamps | VALID; compares lexicographically or numerically, not temporally. Must go through `MILLIS_TO_TIMESTAMP` / `TIME_PARSE`. |
| `JSON_VALUE(PARSE_JSON(col), '$.flag') = 'true'` where the JSON holds a *boolean* | VALID, matches nothing. `JSON_VALUE` renders JSON booleans as `'1'` / `'0'`, not `'true'` / `'false'`. The seed data avoids JSON booleans for this reason. |

## Refuted — do NOT teach these as restrictions

Claims that were in an earlier draft of this document and are **false** on 35.0.0. Training examples that avoid these would make the model worse, not better.

- **"`ORDER BY` is only allowed on grouped/aggregated columns, or with `LIMIT` on `__time`."** Wrong on both halves. The restriction is on *table scans*: with no `GROUP BY` you may only order by `__time`, and `LIMIT` does not help. With a `GROUP BY` you may order by any grouped or aggregated column, no `LIMIT` required.
- **"Druid has broad join restrictions."** Largely obsolete at 35.0.0. `INNER`, `LEFT`, `RIGHT`, `FULL OUTER`, non-equi joins, `UNION` / `UNION ALL`, `IN`-subqueries and correlated subqueries all plan and execute. Remaining concerns are performance, not validity — and performance is not something the harness can verify, so it does not belong in this dataset.
- **"Trailing semicolons are rejected."** They are accepted.
- **"`HAVING` cannot reference a select alias."** It can, and so can `ORDER BY`. Only `GROUP BY` cannot.
- **"A string timestamp column must go through `TIME_PARSE`."** `TIME_PARSE` is the explicit, format-safe form and is what this dataset teaches, but `CAST(str AS TIMESTAMP)` also works and returns identical results for both `'yyyy-MM-dd HH:mm:ss'` and `"yyyy-MM-dd'T'HH:mm:ss'Z'"`. It is an alternative, not a trap — a generated trap that assumed otherwise failed validation and was replaced.

Also confirmed working, so they are safe to teach: `TIME_FLOOR`, `TIME_CEIL`, `TIME_SHIFT`, `TIME_EXTRACT`, `TIME_PARSE`, `TIME_FORMAT` (incl. timezone args), `TIME_IN_INTERVAL`, `DATE_TRUNC`, `FLOOR(x TO HOUR)`, `MILLIS_TO_TIMESTAMP` / `TIMESTAMP_TO_MILLIS`, `APPROX_COUNT_DISTINCT`, `APPROX_QUANTILE`, `APPROX_QUANTILE_DS`, `LATEST` / `EARLIEST` / `LATEST_BY`, `MV_CONTAINS`, `MV_FILTER_ONLY`, `ARRAY_CONTAINS`, `SAFE_DIVIDE`, `NVL`, `IFNULL`, `COALESCE`, `FILTER (WHERE …)`, `HAVING`, window functions, `QUALIFY`, `GROUPING SETS`, `ROLLUP`, CTEs, `LIMIT … OFFSET`, `TIMESTAMPADD` / `TIMESTAMPDIFF`, `REGEXP_LIKE`, `PARSE_JSON` / `TRY_PARSE_JSON`, `LOOKUP`, `SAFE_DIVIDE`.

# Decisions

Settled up front so the dataset is internally consistent:

| Decision | Choice |
| --- | --- |
| Prompt template | **Twelve** formats (`dataset/prompt_formats.py`), sampled evenly, so the model learns to read a schema rather than memorise one layout. The assistant turn never varies: bare SQL, no fence, no prose. `md_sections` is the one `prompt.py` serves. |
| Type vocabulary | Druid SQL types (`TIMESTAMP`/`VARCHAR`/`BIGINT`/`DOUBLE`/`FLOAT`) |
| Output aliases | **Always** double-quoted. Never wrong, and it saves the model from memorising Druid's reserved-word list |
| `GROUP BY` / `ORDER BY` | Ordinals, never select aliases (`GROUP BY` cannot resolve them) |
| Relative time | `CURRENT_TIMESTAMP` arithmetic; never a hardcoded anchor date |
| Counts and percentiles | Druid-idiomatic approximate by default: `APPROX_COUNT_DISTINCT`, `APPROX_QUANTILE_DS` |
| Schema shape | 69 datasources from 19 domain families x 3 variants, plus 9 hand-written and 5 dimension tables. Variants differ in naming convention, column subset and time encoding. |
| Train / validation split | **Schema-disjoint by family.** Six whole families are held out; no validation schema shares a column vocabulary with a training one. |
| Multi-value dimensions | **In scope** — real MVDs, ingested via an `ARRAY<STRING>` signature and `ARRAY_TO_MV` |
| Lookups | **In scope** — registered via the coordinator API; allow 2–4 min to propagate to the Broker |
| Nested JSON (`COMPLEX<json>`) | Out of scope. JSON-as-string via `PARSE_JSON` covers the need |

# Data Creation Instructions
## A few things to keep in mind when creating the data

- **Nothing enters the dataset unless it has been executed.** Every assistant completion must return `VALID` against the 35.0.0 harness cluster. The [Refuted](#refuted--do-not-teach-these-as-restrictions) section above is what happens when Druid behaviour is written from memory instead of run.
- **`VALID` is necessary but not sufficient.** `JSON_VALUE` on an unparsed string column returns `VALID` and NULL for every row. Three further gates:
  1. a non-empty-result assertion for examples that should return rows;
  2. a per-cluster lint that the completion actually uses the construct it is meant to teach;
  3. for every trap, the *naive* SQL kept in a metadata field and asserted `INVALID` (hard traps) or asserted to give a different result (soft traps). The naive SQL is never shown to the model.
- **Vary the schema per example, aggressively.** Since the real use case is "large schema enrichments," the model needs to generalize the *rules* (only `__time` is a timestamp, everything else is VARCHAR/BIGINT/etc.) rather than memorize one schema. Generate hundreds of synthetic schemas and pair each with several NL→SQL examples.
- **Generate schemas from domain templates, not random strings.** Purely random table/column names produce unrealistic schemas and robotic questions. Use realistic domains (adtech, observability, fintech, IoT, e-commerce, security logs, telco, gaming) with randomized naming conventions (`snake_case` vs `camelCase`, `dim_`/`enr_` prefixes, `_ms`/`_id` suffixes) and realistic enrichment columns (lookup-backed dimensions, JSON-as-string payloads, multi-value dimensions). Same generalization pressure, plausible NL.
- **Explicitly cover every quirk as its own cluster of examples**, not just incidentally: the table-scan `ORDER BY` restriction, reserved-word aliases, `TIMESTAMP` literal format, `GROUP BY` by ordinal, `TIME_FLOOR`, `TIME_SHIFT`, `TIME_EXTRACT`, `TIME_PARSE`, `TIME_FORMAT`, `MILLIS_TO_TIMESTAMP`/`TIMESTAMP_TO_MILLIS`, `DATE_TRUNC`, `TIME_IN_INTERVAL`, `APPROX_COUNT_DISTINCT`/`APPROX_QUANTILE_DS`, `LATEST`/`EARLIEST`, multi-value dimensions (`MV_TO_ARRAY`, `MV_CONTAINS`, `UNNEST`), JSON-as-string via `PARSE_JSON`, and lookup joins.
- **The single highest-value trap:** a table whose `__time` is ingestion time while the business timestamp lives in a separate epoch or ISO-string column. "Average latency by order placement hour" must go through `TIME_FLOOR(MILLIS_TO_TIMESTAMP(order_placed_at), 'PT1H')`, not `__time`.
- **Include "trap" examples** — natural-language questions that would tempt a model into writing standard-SQL habits (e.g. "order results by revenue" without a `GROUP BY`, or filtering an epoch column with `BETWEEN` timestamps) paired with the *correct* Druid-idiomatic rewrite. These contrastive cases are what actually kill the bad habits; a LoRA on purely "clean" examples often isn't enough to override strong SQL priors.
- **Size:** this is "domain specialization / capability addition" territory, not a huge behavior change, so 1,000 solid, validated examples is a reasonable target — favor quality/coverage of quirks over raw count.
- **The held-out ~10% must be schema-disjoint *and* generator-disjoint.** A random 10% split from the same generator measures memorization of the generator, not generalization of the rules. Hold out whole domains and hand-write the validation questions in a different style.
- Keep a portion of examples with longer, realistic enriched schemas (10–30+ columns) since that's the actual production shape, not just toy 3-column tables.

# Harness notes

Practical constraints discovered while probing the cluster:

- **`druid-harness` cannot ingest a spec whose time column is literally named `__time`** — Druid already types that name as `TIMESTAMP`, so the loader's `MILLIS_TO_TIMESTAMP(__time)` fails. Work around it by naming the source column `event_ts`; the loader maps it to `__time`. (This is the "data load pending" item from commit `fe6e376`.)
- Real multi-value dimensions need `array<string>` added to the loader's allowed types, with the select expression wrapped in `ARRAY_TO_MV`.
- Lookups need an empty-map POST to `/druid/coordinator/v1/lookups/config` to initialise before the first real lookup, then 2–4 minutes before the Broker will resolve them.
- Ingestion runs ~15–30 s per datasource at `druid_worker_capacity=2`. Load schemas in batches (~20 at a time), run their queries, drop, repeat, rather than holding 100+ datasources on a nano-quickstart historical.

# Steps
1. First explore the codebase and see how the druid harness works. **Do not write or update any AI training/inference code**, only I will do that
2. Next create some datasources and about 50 dataset examples. I will review that.
3. Once approved, then create the whole 1000 example dataset.
4. Then run all the queries to see that everything runs with no problem, after that I will spot check and we can close this dataset creation.
