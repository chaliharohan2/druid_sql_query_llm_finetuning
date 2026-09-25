# Druid SQL conventions

Single source of truth for time semantics and output style used across the v2 dataset
generator (`teacher.py`'s `CONVENTIONS` constant is this file, inlined). The Druid
query-validation harness decides what is *legal*; this file decides what is *meant*.

## Time phrases

| Phrase | Canonical predicate on `__time` |
| --- | --- |
| past / last **N** days (rolling) | `__time >= CURRENT_TIMESTAMP - INTERVAL 'N' DAY` |
| past N hours / minutes | `__time >= CURRENT_TIMESTAMP - INTERVAL 'N' HOUR` (or `MINUTE`) |
| today | `__time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')` |
| yesterday | `__time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')` |
| this week / month / quarter / year (to date) | `__time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W' \| 'P1M' \| 'P3M' \| 'P1Y')` |
| last week / month / quarter / year (calendar, **not** rolling) | `__time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, P), P, -1) AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, P)`, with `P` in `{'P1W', 'P1M', 'P3M', 'P1Y'}` |
| between date A and date B (inclusive of B) | `__time >= TIMESTAMP 'A 00:00:00' AND __time < TIMESTAMP 'B+1 00:00:00'` |

Never map a calendar phrase ("last month") to a rolling window (`INTERVAL '30' DAY`) or the
other way round — that was v1's `tr_0048` bug (see `druid_sql_dataset_v2_plan.md`, finding A9).

## Non-`__time` time columns

| Column encoding | Expression as a timestamp |
| --- | --- |
| epoch milliseconds | `MILLIS_TO_TIMESTAMP(col)` |
| epoch seconds | `MILLIS_TO_TIMESTAMP(col * 1000)` |
| string, known format | `TIME_PARSE(col, '<Joda format>')`, e.g. `'yyyy-MM-dd'`, `'yyyy-MM-dd HH:mm:ss'`, `'yyyy-MM-dd''T''HH:mm:ss''Z'''` |

**Durations between two epoch columns:** raw arithmetic on the values, e.g.
`(end_col - start_col) / 1000.0` for a milliseconds difference expressed in seconds. Exclude
NULLs explicitly (`start_col IS NOT NULL AND end_col IS NOT NULL`) whenever either column is
nullable, unless an instruction already covers it. Use `TIMESTAMPDIFF(unit, ts1, ts2)` only
when both arguments are already timestamps.

## Output style

- every output alias is double-quoted
- `GROUP BY` and `ORDER BY` by ordinal, never by repeating the alias or expression
- no trailing semicolon, no markdown fences, exactly one query

## Missing functions

Druid has no `NOW()`, `DATEADD`, `DATEDIFF`, `DATE_FORMAT`, `IF()`, `ILIKE`, `STDDEV`, or
`TOP n`. Use `CURRENT_TIMESTAMP`, `TIME_SHIFT`/interval arithmetic, `TIME_FORMAT`, `CASE WHEN`,
`LIKE`/`REGEXP_LIKE`, and `ORDER BY ... LIMIT n` instead.

## House style

Prefer `APPROX_COUNT_DISTINCT` / `APPROX_QUANTILE_DS` unless the question or a table
instruction explicitly demands an exact count (`COUNT(DISTINCT ...)`).

## Ordering rule

A bare table scan (no aggregation) may only `ORDER BY __time`. Where the question needs a
different sort with no aggregation, the rewrite must keep the question's original meaning —
for example, group by a natural unique key rather than silently changing what is being ranked
(v1's `hw_ob_01` bug, finding A13).

## Role catalog (what a metric column may be used for)

| Role | Allowed | Not allowed |
| --- | --- | --- |
| `time_primary` (`__time`) | filter, bucket, extract | aggregation |
| `time_epoch_ms` / `time_epoch_s` | convert, filter, bucket, subtract | `SUM`, `AVG` of the raw value |
| `time_string(format)` | `TIME_PARSE` with that exact format | raw comparison with a timestamp |
| `id` (high cardinality) | `COUNT(DISTINCT)`, `APPROX_COUNT_DISTINCT`, group by (with a limit) | `SUM`, `AVG` |
| `category` / `category_numeric` | filter, group by | math |
| `measure_additive` | `SUM`, `AVG`, `MIN`, `MAX` | — |
| `measure_nonadditive` | `AVG`, `MIN`, `MAX`, quantiles | `SUM` unless the question explicitly asks for a total |
| `flag_01` | `SUM` as a count, ratio, `FILTER` | `AVG` without a stated rate meaning |

Polarity (`higher_is_worse` / `higher_is_better` / `neutral`) resolves "worst", "best" and
"slowest" style questions. A `neutral` measure never gets a worst/best question.
See `dataset/roles.py` for the classifier this is implemented against.

## Filter values (label well-posedness)

A gold answer must be derivable from what the model sees. Every string literal a query filters
on has to be recoverable from the **question** or the **rendered schema text**:

- the literal appears in the question (verbatim; any case for a plain lowercase word), or
- it appears in the schema text -- a description that lists its allowed values, a glossary or
  instruction, or sample rows -- in which case the question may *paraphrase* it ("rejected
  payments" -> `'declined'`), **unless** the question names a *different* value from the same
  list (`role is 'driver'` -> `'cleaner'` is the v1 swapped-literal bug), or
- it is a placeholder such as `'none'` / `'unknown'`.

Otherwise the example is dropped (G3). The teacher is told which values exist in the data and
whether the schema text lists them, but it may not rely on a value only it knows: an unlisted
value must be written verbatim, in quotes, in the question.

About 55% of low-cardinality dimension descriptions list their allowed values, inside the first
sentence so that the formats that keep only the first sentence still carry them.

## Time scopes

Each example is assigned one kind of time window, so the mix is designed, not inherited from the
teacher's habits: none 30%, rolling 32%, today/yesterday 9%, this-period-to-date 8%,
completed last-period 15%, explicit date range 6%. The SQL must use exactly that shape (Appendix A
patterns). "last week/month/quarter/year" is always the completed calendar period; a rolling
window is written "past N days/hours". `TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M')` is the start of the
current calendar quarter (Druid floors quarters from the epoch, which is a quarter boundary).

## Answer comparison (G4)

Two independent answers agree if they return the same rows: aliases, SELECT order and row order
are ignored, numbers compare after rounding (and `'05'` equals `5`), and an answer that also shows
an extra column still agrees when its other columns match. The second answer must execute; if it
fails Druid the error is fed back for repair before the gold example is dropped.

## Data facts the generators rely on

The seed data spans **18 months** ending at the anchor in `dataset_meta.json`, denser toward the
present (6% of rows in the last 36 hours, 34% in the last 45 days) so that "today" and "yesterday"
return rows. NULLs, empty multi-value cells and (in about 30% of JSON-bearing schemas) malformed
JSON are present; `PARSE_JSON` errors on malformed input while `TRY_PARSE_JSON` returns NULL, and a
schema with malformed rows says so in the column description. **Regenerate and reload the seeds
when the anchor is more than a few weeks old**, or relative windows go stale.

Window functions, CTEs and joins on subqueries are accepted by Druid 35.0.0 (tested).

## Table rules and glossary (addendum F1-F4)

**Rules** (`rule_bank.py`). Each rule type (nullable metric, unique-by, exact distinct, exclude flag,
duration end) has 30 sentence phrasings and 8 clause phrasings for folding two rules into one sentence.
The constraint a rule imposes is stored structurally on the datasource (`kind`, `binding_column`) and
checked by G6 on the SQL; only the wording varies. A prompt lays its rules out in one of five layouts, drawn
once per prompt: no heading, `### INSTRUCTIONS` / `### Instructions` with `*` bullets, a `**Rules**` block, a
numbered list, or embedded in the table's overview paragraph.

**Glossary** (`glossary_terms.py`, `notes_v3.py`). A datasource carries three or four terms, written per
domain ("rush shipment", "VIP booking", "at-risk account"): the renamed v2 term (a value list on one
column; its predicate is unchanged) plus terms of the shapes `or_cols`, `not_in`, `like_prefix`,
`threshold`, `mixed`, `time_active` (an entity defined by activity in the last N days) and `derived` (a
formula: an epoch difference in stated units, or a ratio of sums). Fourteen definition patterns render them.
A prompt prints the primary term and one to three of the others.

- A question that uses a term must find it defined in its prompt, and its gold SQL applies the
  definition (G6, shape-aware, in `validators.glossary_applied`).
- A glossary term never contains one of the four v2 head words (flagged, priority, notable, key) and no term
  contains another; a term that already occurs in some question as plain English is not used.
- An extra term whose words appear in the question is left out of that prompt (it would read as a
  reference the SQL does not honour).
- **Decoys.** *key, priority, flagged, notable, critical, premium* also occur as ordinary English and as the
  names or values of real columns (`priority`, `severity = 'critical'`, `product_tier = 'premium'`). Decoy rows
  use the word with **no** definition; the gold SQL adds no glossary filter.

## Epoch units are never assumed (addendum F6, gate G10)

A BIGINT time column has no unit of its own. A gold SQL may convert it (`MILLIS_TO_TIMESTAMP`, `* 1000`),
scale a difference (`/ 60000`) or compare it with an epoch-sized literal only if the prompt states the unit:
in the column's name (`_ms`, `Secs`), in the text after the column's name (its description, or a glossary
definition that says "epoch seconds"), or in sample rows. Otherwise the row is dropped, and the check runs
on every new row.

## Nullability annotations (addendum F5)

The enterprise formats print IS_NULLABLE / NULLABLE. v2 marked about 11% of non-time columns nullable;
real schemas mark almost all of them. Each prompt now marks 60-80% of the non-time columns NULLABLE. `__time`
stays NOT NULL, a column that was NULLABLE stays NULLABLE, and a column the gold SQL uses in epoch or
duration arithmetic without an `IS NOT NULL` guard is left exactly as it was. Referenced and unreferenced
columns are flipped with the same probability, so the annotation carries no hint about which columns matter
(the report prints both shares).

## Questions

No backticks and no SQL syntax (function names, calls, upper-case keywords). A bare `__time` is a column
name and may appear. Exact-duplicate questions are kept once.

## Glossary term names (addendum F7, `term_names.py`)

A term is named from its definition, so its everyday meaning matches what it filters on: `high-`/`low-<measure>` for a
threshold, `other-<column>` for a negation, `<column>-series` for a prefix, `begin-to-finish time` /
`uplink-to-downlink ratio` for a formula, `recently active <entity>` for activity in the last N days, an everyday
name where the values have one ("widebody flight leg", "Chicago departure", 52 hand-written in `SEMANTIC`), and otherwise
a neutral coded label ("Tier-B order", "watchlist loan"). A term never contains a value the definition filters on or a
word of one, never a when/where/how word the definition does not filter on, and never a column name of its table.
The F2 name each term had is kept as `gen_term`: the F3/F4 pools were written with those names and are re-rendered
against the current ones by `addendum_apply.py`.

Text conventions: a column name inside backticks is never wrapped in a second pair; plurals of names ending in -sh,
-ch, -x, -z take -es ("hashes"); "a"/"an" follows how the following word (or column name) is read aloud, including
in front of a substituted glossary term.
