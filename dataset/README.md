# Druid SQL training set

`train.jsonl` — **1000 examples**. `val.jsonl` — **150 examples**, on schemas the training set never sees.

Every query in both files was executed against a live Apache Druid 35.0.0 cluster and passed four gates. Nothing here was written from memory.

## Files

| File | What it is |
| --- | --- |
| `train.jsonl` / `val.jsonl` | the deliverable — chat-format SFT records |
| `dataset_stats.json` | counts behind every number in this document |
| `schema_index.json` | all 69 datasources: columns, descriptions, value pools, roles |
| `examples.json` | pre-render examples with their gates and traps |
| `validation_report.json` | per-example gate results from the live cluster |
| `families.py` | 19 domain families, written once each |
| `gen_schemas.py` | families → 69 datasource specs, seed rows and the role index |
| `templates.py` | 106 query templates, bound to roles rather than column names |
| `schema_view.py` | the role-addressed view a template sees |
| `prompt_formats.py` | the 12 prompt renderers |
| `generate.py` | draws examples, splits, balances clusters and formats |
| `validate_bulk.py` | runs every query and every trap against Druid |
| `emit_sft.py` | renders passing examples into the JSONL files |
| `examples/batch01.py` | the 64 hand-authored examples reviewed in batch 01 |
| `dataset_meta.json` | the pinned time anchor the seed rows are built around |

## Reproducing it

```bash
cd druid-harness && make up          # ~6-8 GB RAM, on-demand only
cd ../dataset
export PYTHONPATH=$PWD/../druid-harness
python3 gen_schemas.py               # specs, seed rows, schema_index.json
python3 load_all.py                  # ingest all 69 datasources + register lookups (~13 min)
python3 generate.py                  # examples.json: draw, split, balance
python3 validate_bulk.py             # four gates against the live cluster
python3 emit_sft.py                  # train.jsonl, val.jsonl, dataset_stats.json
python3 make_readme.py               # this file
```

The time anchor is pinned in `dataset_meta.json` (**2026-08-26T08:00:00+00:00**). Seed rows span the 30 days ending there, which is what makes `CURRENT_TIMESTAMP`-relative queries return rows. Regenerating reproduces the seeds byte for byte; **change the anchor and you must reload every datasource**, because the queries were validated against the data that is currently ingested.

## The split

Whole families are held out, not individual variants. Holding out one variant of a family would leave its siblings in training with near-identical column names — the split has to be disjoint in vocabulary, not just in table name.

| | Train | Validation |
| --- | ---: | ---: |
| Examples | 1000 | 150 |
| Datasources appearing in prompts | 53 | 16 |
| Domain families (each hand-written schema counts as one) | 27 | 6 |
| Shared datasources | — | **0** |
| Prompts carrying distractor tables | 236 | 29 |

**Held out for validation:** `cdn_edge`, `crypto_trades`, `dim_stores`, `retail_pos`, `streaming_media`, `support_tickets`.

These six were chosen so validation still exercises every enrichment shape training teaches: `streaming_media` has a JSON-as-string column, `cdn_edge` has a lookup, `support_tickets` has a multi-value dimension, `retail_pos` joins to `dim_stores`, and `crypto_trades` is plain numeric. `emit_sft.py` asserts the two sides share no datasource; the assertion is the guarantee.

All nine hand-written schemas stay in training, because the 64 hand-authored examples from batch 01 use them.

## Schemas

69 datasources, 54,003 rows ingested. Nineteen domain families become three variants each (two for `clinical_telemetry` and `workforce`), plus nine hand-written schemas and five dimension tables.

The three variants of a family deliberately disagree on surface convention:

| Variant | Datasource name | Columns | Secondary time column | Enrichment |
| --- | --- | --- | --- | --- |
| `v0` | `ds_app_logs` | snake_case, full width | epoch **milliseconds** | MVD, JSON, lookup |
| `v1` | `appLogsRaw` | **camelCase**, narrowed | ISO-8601 **string** | JSON only |
| `v2` | `app_logs_daily` | snake_case, different subset | `'yyyy-MM-dd HH:mm:ss'` **string** | MVD, lookup, join partner |

| Family | Domain | Variants | Held out |
| --- | --- | --- | :---: |
| `api_gateway` | API gateway traffic | 3 |  |
| `app_logs` | Application observability | 3 |  |
| `cdn_edge` | CDN edge delivery | 3 | yes |
| `clinical_telemetry` | Clinical device telemetry | 2 |  |
| `crypto_trades` | Crypto exchange | 3 | yes |
| `email_campaigns` | Marketing automation | 3 |  |
| `energy_meter` | Smart metering | 3 |  |
| `fleet_gps` | Fleet telematics | 3 |  |
| `food_delivery` | Food delivery | 3 |  |
| `inventory` | Warehouse inventory | 3 |  |
| `logistics` | Freight logistics | 3 |  |
| `ml_inference` | ML serving | 3 |  |
| `network_flows` | Network telemetry | 3 |  |
| `payment_gateway` | Payments processing | 3 |  |
| `retail_pos` | Retail point of sale | 3 | yes |
| `ride_hailing` | Ride hailing | 3 |  |
| `streaming_media` | Video streaming | 3 | yes |
| `support_tickets` | Customer support | 3 | yes |
| `workforce` | Workforce management | 2 |  |
| `dim_carriers` | Freight logistics | dimension table |  |
| `dim_drivers` | Ride hailing | dimension table |  |
| `dim_segments` | Marketing automation | dimension table |  |
| `dim_stores` | Retail point of sale | dimension table | yes |
| `dim_warehouses` | Warehouse inventory | dimension table |  |
| `ad_impressions` | Ad tech | hand-written |  |
| `fin_txn` | Payments / fintech | hand-written |  |
| `game_sessions` | Gaming | hand-written |  |
| `iot_readings` | Industrial IoT | hand-written |  |
| `orders` | E-commerce | hand-written |  |
| `products` | E-commerce | hand-written |  |
| `sec_alerts` | Security operations | hand-written |  |
| `telco_cdr` | Telecommunications | hand-written |  |
| `web_events` | Web / product analytics | hand-written |  |

## What the examples teach

106 templates across 21 quirk clusters. A template binds to *roles* — a low-cardinality dimension, a numeric measure, the epoch-seconds column — so one template renders against every schema that has the roles it needs.

| Cluster | Train | Val | What it teaches |
| --- | ---: | ---: | --- |
| `time_bucketing` | 88 | 13 | `TIME_FLOOR` / `TIME_CEIL` with ISO period grains |
| `relative_time` | 79 | 12 | `CURRENT_TIMESTAMP - INTERVAL '7' DAY`, never a hardcoded date |
| `grouping` | 78 | 11 | ordinals in `GROUP BY` / `ORDER BY`, `HAVING`, `CASE` buckets, subqueries |
| `mvd` | 81 | 7 | `MV_CONTAINS`, `MV_FILTER_ONLY`, `MV_OVERLAP`, `MV_LENGTH`, `UNNEST(MV_TO_ARRAY(...))` |
| `order_by_restriction` | 62 | 9 | a table scan may only order by `__time`; `LIMIT` does not rescue it |
| `approx_agg` | 61 | 9 | `APPROX_COUNT_DISTINCT`, `APPROX_QUANTILE_DS` |
| `missing_function` | 54 | 8 | no `NOW`, `DATEADD`, `DATEDIFF`, `DATE_FORMAT`, `IF`, `ILIKE`, `STDDEV`, `TOP n` |
| `time_extract_format` | 53 | 8 | `TIME_EXTRACT`, `TIME_FORMAT`, timezone arguments |
| `reserved_alias` | 52 | 8 | `AS "hour"`, `AS "value"`, `AS "count"` — quoting output aliases |
| `string_time_column` | 46 | 7 | `TIME_PARSE` with an explicit format for string timestamps |
| `latest_earliest` | 44 | 7 | `LATEST` / `EARLIEST` / `LATEST_BY`, and the `, 64` byte limit on strings |
| `timestamp_literal` | 41 | 4 | `TIMESTAMP 'yyyy-MM-dd HH:mm:ss'` — the ISO form is rejected |
| `filtered_agg` | 41 | 7 | `FILTER (WHERE ...)` on aggregates |
| `time_shift` | 36 | 5 | `TIME_SHIFT` and period-over-period comparison |
| `epoch_time_column` | 34 | 7 | `MILLIS_TO_TIMESTAMP`, and `* 1000` for epoch seconds |
| `join` | 33 | 5 | joins to a dimension table, including `LEFT JOIN` and filtering on the partner |
| `null_math` | 33 | 5 | `NULLIF`, `COALESCE`, `ROUND`, `CAST` |
| `string_ops` | 33 | 5 | `LIKE`, `CONCAT`, `UPPER`, `SUBSTRING`, `LENGTH` |
| `json_string` | 27 | 6 | `JSON_VALUE(PARSE_JSON(col), '$.key')` — the silent-NULL trap |
| `lookup` | 17 | 5 | `LOOKUP(col, 'name')` rather than a join to a lookup table |
| `reserved_column` | 7 | 2 | a column *named* `value` or `language` must be quoted as an identifier |

**375 examples carry a trap**: the standard-SQL reflex, validated to fail (358 of them) or to return something different (17). Traps are never shown to the model — they exist to prove the example teaches something Druid actually requires.

## Prompt formats

Twelve renderers, sampled as evenly as eligibility allows, so the model learns to read a schema rather than memorise one layout. **The assistant turn never varies**: bare SQL, no fence, no prose, no trailing semicolon, every output alias double-quoted.

| Format | Train | Val | Shape |
| --- | ---: | ---: | --- |
| `md_sections` | 83 | 12 | markdown headings, full descriptions — the format `prompt.py` serves |
| `ddl` | 83 | 13 | `CREATE TABLE` with trailing line comments |
| `yaml` | 84 | 13 | YAML schema block |
| `json` | 84 | 12 | JSON blob, the shape a programmatic caller injects |
| `pipe_table` | 83 | 13 | markdown pipe table, as pasted from a wiki or dbt doc |
| `verbose_rules` | 83 | 12 | long role preamble with an explicit numbered rules list |
| `no_system` | 84 | 12 | **two turns** — everything in the user message, no system turn |
| `question_first` | 83 | 13 | question ahead of the schema, so position is not a cue |
| `sample_rows` | 83 | 12 | schema plus two real rows, the Spider/BIRD convention |
| `compact` | 83 | 12 | one line per table, types only, no descriptions |
| `druid_native` | 84 | 13 | Druid's own type names (`LONG`/`STRING`), as the web console shows them |
| `bare` | 83 | 13 | column names only — no types, no descriptions, no rules |

Two knobs turn underneath the format choice:

- **Distractor tables.** 236 of 1000 training prompts carry one or two tables the query never touches. Real schema blocks are pasted in wholesale, and a model trained only on prompts where every table is needed learns to use every table.
- **Column order.** Columns are shuffled per example, and the time column leads only about half the time, so position never becomes the cue for which column is the timestamp.

Some formats cannot express some things — a format with no column descriptions cannot name the keys inside a JSON string, and one that never mentions lookups cannot support `LOOKUP()`. Each renderer declares what it can express, each example declares what it needs, and the sampler only draws from formats that cover it.

## Validation

Four gates, all of which must pass:

| Gate | Check |
| --- | --- |
| **EXEC** | the completion returns `VALID` from Druid 35.0.0 |
| **ROWS** | it returns at least one row |
| **LINT** | it contains the construct its cluster exists to teach |
| **TRAP** | the naive version fails, or returns something different |

`VALID` alone is not enough, which is why ROWS and TRAP exist: `JSON_VALUE(json_string_col, '$.k')` is accepted by Druid and returns `NULL` for every row.

**Result: 1150 of 1150 examples passed all four gates**, plus 375 trap queries. `validation_report.json` has the per-example detail.

## Lengths

Characters, as a proxy for tokens.

| | p50 | p95 | max |
| --- | ---: | ---: | ---: |
| train prompt | 1,039 | 3,065 | 7,217 |
| val prompt | 974 | 2,843 | 5,068 |
| answer | 172 (mean) | | 378 |

The prompt is five to twenty times the answer. If the training config computes loss over the whole sequence, the schema block dominates it and the model spends most of its gradient learning to reproduce schemas. Masking the loss to the completion is worth checking before a long run — that is a call for `lora_training.py`, which this pipeline does not touch.

## Known limits

- **Approximate aggregators are the house style**, so the model will reach for `APPROX_COUNT_DISTINCT` even where an exact `COUNT(DISTINCT ...)` was wanted. That is a deliberate Druid-idiomatic choice, not an accident.
- **Nested `COMPLEX<json>` columns are out of scope.** JSON is taught as a string parsed with `PARSE_JSON`.
- **No performance dimension.** Every query is validated for correctness on ~900-row datasources; nothing here teaches the model which of two correct queries is cheaper at scale.
- **`reserved_column` is thin** (7 training examples). Probing all 493 column names found only `value` and `language` colliding with Druid reserved words, so there is not much material. Reserved-word *aliases* are covered far more heavily (52 examples).
- **The window is 30 days.** Questions about quarters or years would return nothing, so none are asked.

---

Dialect facts, and the ones that were disproved, live in [`../druid_dataset_creation.md`](../druid_dataset_creation.md).
