# Druid SQL SFT Dataset v2: Rebuild Plan

**Audience:** the coding agent that owns the dataset generator.
**Scope:** dataset generation, validation and reporting only. Do not write or change training code.
**Target model:** Qwen3.5-2B, LoRA SFT, thinking disabled; the assistant turn is the SQL only.
**Dialect:** Apache Druid **35.0.0**. The Druid query-validation harness is the source of truth for whether a query is legal.

---

## 0. Ground rules

1. **Keep the model general.** Do not copy the owner's production prompt, company names, business glossary or table names into training data. Reproduce the *structural properties* of real enterprise prompts (wide tables, overlapping column names, blank descriptions, table-level instructions, business vocabulary) using **invented** companies and domains.
2. **Every example must pass every automated gate in Section 6.** An example that fails any gate is dropped, not patched by hand, unless it is in the handwritten set.
3. **Report, don't assume.** Every generation run writes the statistics report described in Section 10, and the owner judges progress from it.
4. **Keep the output style consistent, and make the inputs diverse.** Output conventions (double-quoted aliases, `GROUP BY` ordinals, no semicolon, no fences) stay as they are. The questions, schemas and prompts must vary widely.

---

## 1. Why v1 failed in production

v1 scores well on its validation split but fails on real enterprise prompts in two ways:

- **Invented or borrowed column names.** Example: for "average handling time of calls per agent", the model emitted `agent_name`. No such column exists; the table has `actioner_full_name` and `actioner_email_id`.
- **Druid functions misused in shapes it hasn't seen.** For the same question it produced `AVG(TIMESTAMPDIFF(MILLIS_TO_TIMESTAMP, answer_time, TIME_PARSE(end_time, 'yyyy-MM-dd''T''HH:mm:ss''Z''')))`. That is v1's `st_iso_hour_gap` template (15 near-identical training examples) with the slots filled wrongly. The right answer was plain arithmetic, `(end_time - answer_time) / 1000.0`, on two BIGINT epoch-ms columns. No v1 example teaches that.

The un-finetuned base model grounded columns correctly on the same prompt but wrote MySQL-flavoured SQL. So the fine-tune **added Druid vocabulary but traded away grounding**. The cause is that v1 teaches template recall rather than composition, and its questions give away the column names. The audit below shows both.

---

## 2. Audit of v1 (measured on the actual files)

Files: `train.jsonl` (1,000 rows), `val.jsonl` (150 rows). There are 21 clusters and 107 templates (64 of them "handwritten"), across 12 prompt formats.

| # | Finding | Evidence | Effect on the model |
|---|---|---|---|
| A1 | **Filter literal in the answer differs from the question** | 26 train and 7 val rows. All come from 3 templates, each wrong most of the time: `jn_filter_partner` 5/6, `ob_scan_filtered` 10/13, `tb_sum_filtered` 11/14. Example: *"where role is 'driver'"* → `WHERE role = 'cleaner'` | Directly trains "don't copy values from the question" |
| A2 | **Query shapes are recycled** | 1,000 answers collapse to **162** skeletons (identifiers, literals and numbers masked). The top skeleton appears 24 times. **149/150** val skeletons and **79/79** val templates also appear in train | Validation measures "same shape, new schema", not generalization. It overstates quality |
| A3 | **Questions give away column names** | **760/1000** questions contain an exact column name from the schema, e.g. *"Break engine_rpm down weekly where fuel_type is 'electric'"* | The model never learned to map business words ("agent", "handling time") to columns, so at inference it makes up a plausible name instead |
| A4 | **Schemas are small and clean** | 7–24 columns per table (median 11). **0** blank descriptions, **0** nullability annotations, **0** table-level instructions. Prompt length is about 260 tokens at p50, ~770 at p95 and ~1.8k at max | Real prompts: tables of 26–188 columns, many blank descriptions, instruction paragraphs, ~25k tokens. The model never practised search at that scale |
| A5 | **Distractor tables are too easy** | 264 multi-table prompts, but only 13 have a distractor from the same domain. Column names shared across domains are almost all the synthetic time columns | Real schemas repeat names (`case_id`, `priority`, `channel`) across tables, so "wrong-table column" errors were never penalised |
| A6 | **Few time-column variants, and descriptions give away the function** | Only **3** epoch column names (`received_at_ms`, `request_started_at_ms`, `served_at_epoch_s`) and **4** string-time names. When the answer uses `MILLIS_TO_TIMESTAMP`, the description names the function 54% of the time; `TIME_PARSE` 47%; `PARSE_JSON`/`JSON_VALUE` 85% | The model links "epoch" to a name suffix plus a copied hint. It never sees `end_time BIGINT -- epoch millis` and has to work out the conversion |
| A7 | **No epoch arithmetic** | **0** answers subtract two epoch columns. Only 19 use `TIMESTAMPDIFF`, and 15 of those share one skeleton | Durations between two BIGINT time columns (very common in real data) aren't covered |
| A8 | **Measures aggregated in meaningless ways** | **78** `SUM`s over non-additive measures (latency, pct, score, rpm, price, voltage), e.g. `SUM(gc_pause_ms)`, `SUM(fuelLevelPct)`. "worst batteryPct" / "worst dstPort" map to `MAX ... DESC` (`ob_group_all_columns`, including val `va_0057`) | Teaches aggregation that doesn't fit the column, and "worst/best" with no sense of polarity |
| A9 | **Calendar time phrases are missing or wrong** | Only 1 answer uses a calendar-month window. `tr_0048` answers *"this month"* with `CURRENT_TIMESTAMP - INTERVAL '30' DAY`. No "last month / last quarter / last week" in the calendar sense | Real questions ("last month") get turned into rolling windows or made-up syntax |
| A10 | **Limited SQL constructs, little composition** | 0 CTEs, 0 window functions, 17 subqueries, 3 `IS NOT NULL`, 0 exact `COUNT(DISTINCT)`. Only 99 answers combine two Druid-specific feature families and none combine three | Real questions need filters, null handling and several quirks together |
| A11 | **No "can't answer" cases** | 0 examples where the requested field doesn't exist | With no way to decline, the model invents a column |
| A12 | **Pipe-table format lists lookups as pseudo-columns** | Rows such as `\| LOOKUP(host_id, 'host_tier') \| VARCHAR \| ... \|` inside the column table | Blurs the line between a column name and an expression |
| A13 | **One handwritten example changes the meaning to satisfy a dialect rule** | `hw_ob_01` *"10 slowest requests"* → `GROUP BY session_id, page_path ... MAX(latency_ms)`, which dedups by session and page rather than returning individual requests | Shows that working around a rule can change what the query means |

**Keep from v1:** gold answers are well grounded (every referenced column exists in its table). The 12 prompt formats and the no-system-prompt variants are good. The 64 handwritten examples are the best-quality rows: they use business language and correct windows. Keep and expand them, apart from A13.

---

## 3. Principles for v2

1. **Question-first composition, not slot filling.** Start from what an analyst wants to know and write the SQL that answers it. Don't pick a SQL template and fill in random columns.
2. **Force grounding.** Most questions must use business vocabulary that doesn't match any column name, so the model has to read the descriptions to find the right column.
3. **Force search.** Many prompts must have wide tables and related distractor tables with overlapping names.
4. **Infer from types, not hints.** Most time and JSON columns are described by what they *are* ("epoch milliseconds", "ISO-8601 string", "JSON payload"), not by which function to call.
5. **Every quirk appears in combination with others.**
6. **The label must be right.** Noisy labels do more damage than too few labels. Verify everything automatically, cross-check a sample with a second model, and spot-check by hand.

---

## 4. Work items

Priority: **P0** blocks the next training run. **P1** belongs in v2. **P2** is optional or can follow.

### P0-1 · Fix the literal-mismatch bug and guard against it
- **Problem:** A1. The three templates draw the question's value and the SQL's value independently.
- **Do:** Draw each filter value **once** and pass it to both the question and the SQL renderer. Audit every template (and any new generator) for values drawn twice.
- **Gate:** see G3 in Section 6 (quoted **and** unquoted enumerated values).
- **Accept:** 0 mismatches across all splits.

### P0-2 · Give columns semantic roles and use them to choose aggregations
- **Problem:** A8. The generator doesn't know what a column means.
- **Do:** Attach a role to every synthetic column and let the role decide which operations are allowed:

  | Role | Allowed | Not allowed |
  |---|---|---|
  | `time_primary` (`__time`) | filter, bucket, extract | aggregation |
  | `time_epoch_ms` / `time_epoch_s` | convert, filter, bucket, subtract | `SUM`, `AVG` of the raw value |
  | `time_string(format)` | `TIME_PARSE` with that exact format | raw comparison with a timestamp |
  | `id` (high cardinality) | `COUNT(DISTINCT)`, `APPROX_COUNT_DISTINCT`, group by (with a limit) | `SUM`, `AVG` |
  | `category(values=[...])` | filter, group by | math |
  | `measure_additive` (bytes, cost, count, amount) | `SUM`, `AVG`, `MIN`, `MAX` | — |
  | `measure_nonadditive` (latency, pct, score, rpm, price, temperature) | `AVG`, `MIN`, `MAX`, quantiles | `SUM` unless the question explicitly asks for a total |
  | `flag_01` | `SUM` as a count, ratio, `FILTER` | `AVG` without a stated rate meaning |
  | `json_string(keys)`, `mvd`, `lookup_key(lookup_name)` | their own operations | — |

- Give each measure a **polarity** (`higher_is_worse` / `higher_is_better` / `neutral`). "Worst", "best", "slowest" and similar words resolve through polarity. Neutral measures (e.g. `dstPort`) never get worst/best questions.
- **Gate:** G5.
- **Accept:** 0 role violations.

### P0-3 · Replace slot-filled templates with compositional, teacher-generated data
- **Problem:** A2, A7, A10. 162 skeletons is far too few, and the model learned to recall them.
- **Do:** The generation loop per example (details in Section 5):
  1. Sample a schema, a **skill spec** (1–3 Druid feature families plus 0–2 general SQL features; see Appendix B), a question style (P0-4) and a prompt format.
  2. A strong **teacher LLM** writes a realistic analyst question and the Druid SQL that answers it, following the conventions in Appendix A.
  3. Run the Section 6 gates.
- **Diversity caps:** no skeleton may exceed **0.5%** of the train split. Target **≥ 1,500 distinct skeletons** per 6,000 examples.
- **Keep** a small templated core (≤ 15%) only for rare syntax that teachers get wrong. Every template must go through P0-1 and P0-2.
- **Accept:** diversity stats in the Section 10 report meet the targets.

### P0-4 · Make most questions use business language
- **Problem:** A3.
- **Do:** Label each example with a question style and hit these targets:

  | Style | Share | Example |
  |---|---|---|
  | Business paraphrase (no exact column names) | **≥ 55%** | "average time agents spent on answered calls last month, per agent" |
  | Vocabulary gap: the natural word isn't a column, but a differently named one is | **≥ 15%** | says "agent"; schema has `actioner_full_name`, `handled_by_email` |
  | Mentions exact column names | **≤ 20%** | "sum bytes_sent by device_type" |
  | Terse or casual | ~10% | "calls/agent last mo" |

- Also add **"column exists only in another table"** traps. The natural column exists in a distractor table but not in the table that answers the question, and the answer must use the right table's column or join correctly.
- **Gate:** G2 (grounding, including wrong-table checks) and G7 (style labels match reality).
- **Accept:** exact-column-name questions ≤ 20%, measured automatically.

### P0-5 · Make schemas realistic
- **Problem:** A4, A5, A12.
- **Do:**
  - **Width:** columns per table: 7–25 (40%), 26–80 (40%), 81–200 (20%).
  - **Near-duplicate column families:** e.g. `created_by_first_name / created_by_full_name / created_by_email`, `owner_id / previous_owner_id`, `case_create_datetime / case_close_datetime / expected_resolution_time`.
  - **Blank descriptions:** 10–30% of columns in about 40% of tables. Include `IS_NULLABLE` / `(NULLABLE)` annotations in about 40% of prompts.
  - **Descriptions that list allowed values** over several lines (e.g. "Possible values: incoming, outgoing").
  - **Table-level overview prose:** what the table is for and when to use it rather than another table. Required in ≥ 40% of multi-table prompts.
  - **Distractors:** in ≥ 50% of multi-table prompts, at least one distractor is from the **same or a related domain** and shares ≥ 5 column names with the target table (e.g. a case-level table and a case-event-log table).
  - **Tables per prompt:** 1 (30%), 2–3 (40%), 4–8 (30%).
  - **Lookups** go in a separate "Lookups:" section, never as rows in the column table (A12).
  - **Formats:** keep the 12 v1 formats. Add two enterprise-style markdown formats:
    - (a) sections per datasource: heading, overview prose, optional instructions, then a column table with `COLUMN_NAME | DATA_TYPE | IS_NULLABLE | DESCRIPTION`;
    - (b) the same structure as a backtick column list, as in the v1 `md_sections` format.

    These copy the **structure** of real prompts, not their content.
- **Accept:** distributions in the report are within ±5 percentage points of the targets.

### P0-6 · Stop naming the function in time and JSON descriptions
- **Problem:** A6.
- **Do:** Give each such column a hint level:

  | Hint level | Share | Example description |
  |---|---|---|
  | `none` (type only) | 30% | `end_time (BIGINT)` with a blank or generic description |
  | `semantic` (says what it is, not how to handle it) | 55% | "Call end time as epoch milliseconds" / "Date the action happened, stored as text like 2026-03-14" |
  | `explicit` (names the function) | 15% | "…convert with MILLIS_TO_TIMESTAMP" |

- **Varied names:** time-like columns must use arbitrary realistic names such as `end_time`, `answer_time`, `closed_on`, `action_date`, `evt_ts`, `created`, `last_seen`, `dispatch_at`. Keep suffixes like `_ms` or `_epoch_s` on **≤ 30%** of them.
- **Varied encodings:** epoch ms, epoch s, ISO-8601 with `Z`, `yyyy-MM-dd HH:mm:ss`, date-only `yyyy-MM-dd`, `dd/MM/yyyy`.
- **Accept:** hint-level and naming shares within ±5 points of target.

### P0-7 · Teach instruction and business-rule following (generic)
- **Problem:** the base model obeyed table instructions, but the fine-tuned model ignored them. v1 has 0 examples where an instruction changes the answer.
- **Do:** in about 35% of prompts, include **table-level instructions** and/or a **glossary of business terms** for an invented organisation. In ≥ 70% of those, the instruction must **change the correct SQL**. Examples:
  - "For duration questions, only consider rows where `answered_at` IS NOT NULL."
  - "Records are unique per `call_ref`; don't use DISTINCT."
  - "An *escalated ticket* means `tier >= 2` OR `status = 'escalated'`."
  - "*Active customer*: at least one order in the last 90 days."
  - "Use exact `COUNT(DISTINCT ...)` for user counts."
  - A glossary term that maps to a multi-value set, e.g. "Premium plans: gold, platinum, enterprise".
- Store the constraint each instruction imposes in metadata (`required_predicates`, `forbidden_constructs`) so G6 can check it.
- **Accept:** G6 passes on 100% of instruction examples.

### P0-8 · Rebuild the evaluation splits so they measure generalization
- **Problem:** A2. v1 validation is in-distribution for query shape.
- **Do:** see Section 9: hold out domains **and** skeletons, and add a production-like eval set.
- **Accept:** < 10% of `test_heldout_shape` skeletons appear in train.

### P1-1 · Long-context examples
- **Problem:** A4.
- **Do:** prompt-length buckets (Qwen tokenizer): < 1k (30%), 1–4k (35%), 4–12k (25%), 12–24k (10%). Build long prompts from many wide tables, overview prose and glossaries, not padding. Record `prompt_tokens` in metadata.
- **Note for the owner:** training `max_length` must cover the longest bucket.

### P1-2 · Calendar time semantics
- **Problem:** A9.
- **Do:** cover "today", "yesterday", "this week / month / quarter / year (to date)", "last week / month / quarter / year" (calendar), "past N days / hours" (rolling), and explicit date ranges. Follow Appendix A exactly. Fix `tr_0048`-style mappings of "this month" to 30 days.
- **Gate:** G8 checks the phrase against the window pattern.

### P1-3 · Wider SQL coverage
- **Problem:** A10.
- **Do:** include CTEs, subqueries, `CASE`, `COALESCE` / `NULLIF`, `IS [NOT] NULL`, `HAVING`, exact and approximate distinct counts, ratios with `FILTER`, top-N per group, and several metrics in one query. Include window functions **only if the harness accepts them on 35.0.0**; test them first and record the result in the report.
- **Accept:** composition distribution (Appendix B) within ±5 points.

### P1-4 · Unanswerable requests
- **Problem:** A11.
- **Do:** about 4% of examples ask for something the schema can't answer (a missing field, a missing table, or a write operation). The assistant output follows **Decision D1**. Include near-misses, where a similar but different column exists and using it would be wrong.
- **Accept:** each one is confirmed unanswerable by the teacher and by the G9 cross-check.

### P1-5 · Fix or replace semantically wrong handwritten rows
- **Do:** fix `hw_ob_01` (A13). Workarounds for Druid's ordering rule must keep the question's meaning: group by a unique row key where one exists, or phrase the question per group. Re-review all 64 handwritten rows with G9.

### P2-1 · Multi-turn follow-ups
About 5% two-turn conversations ("now split that by channel", "same but last quarter"). The final assistant turn is the only target; earlier assistant turns are the prior SQL.

### P2-2 · Output-wrapper robustness (Decision D2)
About 10% of prompts ask for a different output wrapper (e.g. a JSON object with a `sql` or `query` field, or a fenced block). Use several different wrapper specs so the model learns to follow the requested format, not memorise one. The SQL inside must pass all gates.

### P2-3 · General-SQL mix-in (Decision D3)
An optional separate file of cleaned general text-to-SQL data (e.g. BIRD-Platinum, after a licence check). The system prompt must state the dialect explicitly (e.g. "Dialect: SQLite") so it isn't mixed up with Druid. The owner decides whether and how much to mix in.

### P2-4 · RL-ready metadata
For each example, store what a later RL stage needs: `gold_sql`, `required_columns`, `required_predicates`, and result fingerprints of the gold query on 3 seeded datasources (see G4).

---

## 5. Generation pipeline

```
schema_factory ─► skill/style/format sampler ─► teacher LLM (question + SQL)
      │                                                 │
      ▼                                                 ▼
 datasource seeds (x3) ◄──── harness loader     validation gates G1–G9
                                                        │
                                             dedupe + diversity caps
                                                        │
                                          split assignment (Section 9)
                                                        │
                                     jsonl + stats report (Section 10)
```

1. **schema_factory.** Generates invented domains (≥ 40 for train, ≥ 10 more held out) with role-annotated columns, value lists, polarity, time encodings, JSON keys, MVDs and lookups. It also builds **related table groups** (fact table, event log, dimension table) that share keys and names, and table-level overview prose and instructions. Never uses the owner's real schema or organisation.
2. **Seeded datasources.** For each table, load **3 independently seeded datasources** into the harness. Each has ≥ 60 rows, several rows per group, NULLs in nullable columns, empty and multi-valued MVDs, some rows with malformed JSON, and timestamps spread across the last 18 months relative to generation time. A single tiny datasource lets wrong queries "match" by coincidence.
3. **Sampler.** Draws a skill spec (Appendix B), a question style (P0-4), a hint level (P0-6), a prompt format, the tables in the prompt and their distractors, and a length bucket (P1-1).
4. **Teacher.** Given the schema, skill spec, style, the conventions in Appendix A, and the role table from P0-2, it writes the question and gold SQL. Use **two different teacher models** (or two independent samples) to produce a second SQL used for G4.
5. **Gates G1–G9** (Section 6).
6. **Dedupe and caps.** Near-duplicate questions (embedding cosine > 0.92 within a schema) are dropped. The skeleton cap from P0-3 is enforced.
7. **Render.** The chosen prompt format, with thinking disabled. The assistant content is the SQL only (or the D1/D2 output where relevant).

---

## 6. Validation gates (every example must pass)

| Gate | Check | How |
|---|---|---|
| **G1 Executes** | The gold SQL runs on Druid 35.0.0 on all 3 seeds without error | Existing harness |
| **G2 Grounded** | Every column reference exists in **the table it's qualified with**. No borrowing from distractors. No invented identifiers | Parse with sqlglot (`read="druid"`), resolve aliases and subquery outputs, check against the role catalog. Ignore `TIMESTAMPDIFF`/`TIME_EXTRACT` unit keywords (they look like columns to the parser) |
| **G3 Literal fidelity** | Every value named in the question (quoted, or matching a column's value list) appears in the SQL, and no filter value appears in the SQL without being asked for or required by an instruction | Regex on quoted strings plus value-list matching |
| **G4 Result agreement** | The gold SQL and the independent second SQL return the same result on **all 3 seeds** (order-insensitive unless the question implies an order; float tolerance 1e-6) | Harness. If they disagree, send to G9 or drop |
| **G5 Role compliance** | No aggregation or operation that P0-2 disallows. Worst/best respect polarity | Parser plus role catalog |
| **G6 Instruction compliance** | Every `required_predicate` is present and every `forbidden_construct` is absent | Parser/regex on metadata |
| **G7 Style truth** | Examples labelled "business paraphrase" contain no exact column names. "Vocabulary gap" examples really have a gap | String match against the schema |
| **G8 Time phrase ↔ window** | Calendar and rolling phrases map to the Appendix A patterns | Phrase lexicon plus regex |
| **G9 Semantic judge** | A second LLM (not the teacher) confirms the SQL answers the question as asked, including grain, filters and direction | Run on 100% of G4 disagreements and a 10% random sample; the owner reviews a further 2% by hand |

Log every rejection with gate and reason. The rejection-rate table goes in the report.

---

## 7. Target composition for v2 (train split)

Suggested size: **about 6,000 train rows** (see D4).

| Axis | Target |
|---|---|
| Question style | business paraphrase ≥ 55%, vocabulary gap ≥ 15%, exact-column ≤ 20%, terse ~10% |
| Druid feature families per answer | 0: 20%, 1: 40%, 2: 28%, 3+: 12% |
| Tables in prompt | 1: 30%, 2–3: 40%, 4–8: 30% |
| Same- or related-domain distractor (multi-table prompts) | ≥ 50% |
| Columns per target table | 7–25: 40%, 26–80: 40%, 81–200: 20% |
| Prompt tokens | < 1k: 30%, 1–4k: 35%, 4–12k: 25%, 12–24k: 10% |
| Time/JSON hint level | none 30%, semantic 55%, explicit 15% |
| Instructions/glossary present | ~35% (≥ 70% of these change the answer) |
| Epoch arithmetic (difference between two BIGINT time columns) | ≥ 3% |
| Calendar-window questions | ≥ 8% |
| Unanswerable | ~4% |
| Multi-turn (P2) | ~5% |
| Output-wrapper variants (P2) | ~10% |
| Handwritten/curated rows | keep all v1 handwritten rows (after P1-5) plus ≥ 150 new ones |
| Prompt formats | the 14 formats (12 v1 + 2 enterprise-style), roughly balanced; "no system prompt" kept at ~8% |

---

## 8. Record schema

```json
{
  "messages": [ ... ],
  "meta": {
    "id": "v2_000123",
    "split": "train | val_heldout_domain | test_heldout_shape | test_prodlike",
    "domain": "invented_domain_name",
    "target_tables": ["..."],
    "prompt_tables": ["..."],
    "distractor_relation": "unrelated | same_domain | related_group",
    "format": "md_enterprise_table",
    "question_style": "business | vocab_gap | exact_column | terse",
    "hint_level": "none | semantic | explicit",
    "feature_families": ["epoch_arith", "calendar_window", "filtered_agg"],
    "sql_features": ["cte", "case", "is_not_null"],
    "skeleton_hash": "sha1-of-masked-sql",
    "required_columns": ["tbl.col", "..."],
    "required_predicates": ["answered_at IS NOT NULL"],
    "forbidden_constructs": ["DISTINCT"],
    "unanswerable": false,
    "prompt_tokens": 5321,
    "teacher": "model-a",
    "g4_agreement": true,
    "result_fingerprints": ["seed1:...", "seed2:...", "seed3:..."],
    "source": "teacher | template | handwritten"
  }
}
```

---

## 9. Splits and eval sets

| Split | Size | Construction | Measures |
|---|---|---|---|
| `train` | ~6,000 | as in Section 7 | — |
| `val_heldout_domain` | ~300 | Domains not in train; same distributions as train | Checkpoint selection |
| `test_heldout_shape` | ~300 | Held-out domains **and** skeleton hashes not in train (< 10% overlap) | Real compositional generalization |
| `test_prodlike` | 150–300, **hand-verified** | Invented enterprise-style schemas that copy the *structure* of real prompts: 4+ tables, 50–200 columns, overlapping names, blank descriptions, overview prose, instructions and glossary, 10–25k tokens, business-language and vocabulary-gap questions | The metric the owner cares about |

The report compares **base model vs fine-tuned** on every eval split using these metrics:
- G1 validity (executes);
- G2 grounding rate (no invented or borrowed columns);
- G4-style result match against gold on 3 seeds;
- G6 instruction compliance.

A fine-tune that grounds columns worse than the base model is a failed run, whatever its validity score.

---

## 10. Deliverables

1. Updated generator code (schema_factory, sampler, teacher client, renderer) with P0-1 and P0-2 fixes and unit tests for them.
2. `validators/` implementing G1–G9 as reusable functions. The owner will reuse them as reward components for RL later.
3. Data files: `train.jsonl`, `val_heldout_domain.jsonl`, `test_heldout_shape.jsonl`, `test_prodlike.jsonl`, plus any optional P2 files kept separate.
4. `dataset_report.md` (and `.json`) regenerated on every run, with:
   - counts per split; distribution tables for every axis in Section 7, target vs actual;
   - distinct skeletons, the top 20 skeleton shares, and skeleton overlap between splits;
   - gate rejection rates per gate and per generator path;
   - the share of questions containing exact column names;
   - prompt-token histogram; hint-level and time-column name distributions;
   - literal-mismatch count, role-violation count and ungrounded-column count (all must be 0);
   - whether window functions were accepted by the harness on 35.0.0.
5. A short `CONVENTIONS.md` copying Appendix A, which is the single source of truth for time and alias conventions.

---

## 11. Decisions (defaults the agent should use unless the owner overrides)

| ID | Decision | Default |
|---|---|---|
| D1 | Assistant output for unanswerable requests (bare-SQL training) | A single SQL comment line: `-- CANNOT_ANSWER: <short reason naming what's missing>`. The owner's application maps this prefix to its own response format |
| D2 | Include output-wrapper variants (P2-2)? | Yes, ~10%, several different wrapper specs, none copying the owner's production format |
| D3 | General-SQL mix-in (P2-3)? | Build it as a separate file; don't merge it into `train.jsonl`. The owner decides after the first v2 run |
| D4 | Train size | ~6,000 after filtering; generate ~9,000 candidates to allow for rejections |
| D5 | Teacher models | Two different strong models: one writes, the other writes the independent G4 SQL and acts as the G9 judge. Record which model did what in `meta.teacher` |
| D6 | Reference time for relative windows | Queries use `CURRENT_TIMESTAMP`. Seed data covers the last 18 months relative to generation time, so windows return rows |

---

## 12. Out of scope

- Training, LoRA configs, chat-template or thinking settings (the owner handles these).
- Any use of the owner's production prompt, organisation, glossary, table names or real data.
- Changing Druid SQL output-style conventions (alias quoting, ordinals, no semicolon).

---

## Appendix A. Druid 35.0.0 conventions (single source of truth)

The harness decides what's **legal**; this appendix decides what's **meant**. The phrase-to-window mappings are fixed: never map a calendar phrase to a rolling window or the other way round.

| Phrase | Canonical predicate on `__time` |
|---|---|
| past / last **N** days (rolling) | `__time >= CURRENT_TIMESTAMP - INTERVAL 'N' DAY` |
| past N hours / minutes | `__time >= CURRENT_TIMESTAMP - INTERVAL 'N' HOUR` (or `MINUTE`) |
| today | `__time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')` |
| yesterday | `__time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')` |
| this week / month / quarter / year (to date) | `__time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W' \| 'P1M' \| 'P3M' \| 'P1Y')` |
| last week / month / quarter / year (calendar) | `__time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, P), P, -1) AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, P)`, with P ∈ {`'P1W'`, `'P1M'`, `'P3M'`, `'P1Y'`} |
| between date A and date B (inclusive of B) | `__time >= TIMESTAMP 'A 00:00:00' AND __time < TIMESTAMP 'B+1 00:00:00'` |

The same patterns apply to non-`__time` time columns after conversion:

| Column encoding | Expression as a timestamp |
|---|---|
| epoch milliseconds | `MILLIS_TO_TIMESTAMP(col)` |
| epoch seconds | `MILLIS_TO_TIMESTAMP(col * 1000)` |
| string, known format | `TIME_PARSE(col, '<Joda format>')`, e.g. `'yyyy-MM-dd'`, `'yyyy-MM-dd HH:mm:ss'`, `'yyyy-MM-dd''T''HH:mm:ss''Z'''` |

**Durations between two epoch columns:** use arithmetic on the raw values, e.g. seconds `= (end_col - start_col) / 1000.0` for milliseconds. Exclude NULLs explicitly (`start_col IS NOT NULL AND end_col IS NOT NULL`) whenever either column is nullable, unless an instruction says otherwise. Use `TIMESTAMPDIFF(unit, ts1, ts2)` only when both arguments are timestamps.

**Output style (unchanged from v1):**
- double-quoted output aliases;
- `GROUP BY` and `ORDER BY` by ordinal;
- no trailing semicolon, no markdown fences;
- one query only.

**Ordering rule:** where the harness rejects ordering by a non-time column without aggregation, the workaround must keep the question's meaning (P1-5).

---

## Appendix B. Feature families for skill specs

**Druid-specific:**
- `time_bucket` (`TIME_FLOOR`, `DATE_TRUNC`, `FLOOR(__time TO x)`)
- `rolling_window`, `calendar_window`, `time_shift` (period-over-period)
- `time_extract_format` (`TIME_EXTRACT`, `TIME_FORMAT`, time zones)
- `epoch_convert`, `epoch_arith`, `string_time_parse`
- `json_value` (`PARSE_JSON` / `TRY_PARSE_JSON`, `JSON_VALUE`)
- `mvd` (`MV_*`, `UNNEST(MV_TO_ARRAY(...))`)
- `lookup` (`LOOKUP`)
- `approx` (`APPROX_COUNT_DISTINCT`, `APPROX_QUANTILE_DS`)
- `latest_earliest` (`LATEST_BY`, `EARLIEST`, …)
- `filtered_agg` (`FILTER (WHERE ...)`)
- `reserved_identifier` (quoting reserved words)
- `order_by_rule`
- `join` (only if the harness accepts it for the table pair)

**General SQL:**
- `case`, `null_handling` (`COALESCE`, `NULLIF`, `IS [NOT] NULL`), `having`, `subquery`, `cte`
- `ratio`, `top_n_per_group`, `multi_metric`, `exact_distinct`, `string_ops` (`LIKE`, `REGEXP_LIKE`, `UPPER`, `SUBSTRING`)

A skill spec picks 1–3 Druid families and 0–2 general features. The sampler must weight **rare combinations** up, so that no pair of families accounts for more than 3% of train.