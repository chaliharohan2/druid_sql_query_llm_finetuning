#!/usr/bin/env python3
"""Write dataset/README.md from the artefacts, so its numbers cannot drift."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    st = json.loads((ROOT / "dataset_stats.json").read_text())
    ix = json.loads((ROOT / "schema_index.json").read_text())
    ex = json.loads((ROOT / "examples.json").read_text())
    all_ex = ex["train"] + ex["val"]

    traps = Counter(e["trap"]["expect"] for e in all_ex if e["trap"])
    n_templates = len({e["template"] for e in all_ex}) - 1  # 'handwritten' is not a template
    n_hand = sum(1 for e in ex["train"] if e["template"] == "handwritten")

    fams = defaultdict(list)
    for sid, d in ix.items():
        fams[d["family"]].append(d)
    held = set(st["holdout_families"])

    L: list[str] = []
    A = L.append

    A("# Druid SQL training set")
    A("")
    A(f"`train.jsonl` — **{st['train']['records']} examples**. "
      f"`val.jsonl` — **{st['val']['records']} examples**, on schemas the training set never sees.")
    A("")
    A("Every query in both files was executed against a live Apache Druid 35.0.0 cluster and passed "
      "four gates. Nothing here was written from memory.")
    A("")
    A("## Files")
    A("")
    A("| File | What it is |")
    A("| --- | --- |")
    for f, desc in [
        ("`train.jsonl` / `val.jsonl`", "the deliverable — chat-format SFT records"),
        ("`dataset_stats.json`", "counts behind every number in this document"),
        ("`schema_index.json`", "all 69 datasources: columns, descriptions, value pools, roles"),
        ("`examples.json`", "pre-render examples with their gates and traps"),
        ("`validation_report.json`", "per-example gate results from the live cluster"),
        ("`families.py`", "19 domain families, written once each"),
        ("`gen_schemas.py`", "families → 69 datasource specs, seed rows and the role index"),
        ("`templates.py`", f"{n_templates} query templates, bound to roles rather than column names"),
        ("`schema_view.py`", "the role-addressed view a template sees"),
        ("`prompt_formats.py`", "the 12 prompt renderers"),
        ("`generate.py`", "draws examples, splits, balances clusters and formats"),
        ("`validate_bulk.py`", "runs every query and every trap against Druid"),
        ("`emit_sft.py`", "renders passing examples into the JSONL files"),
        ("`examples/batch01.py`", f"the {n_hand} hand-authored examples reviewed in batch 01"),
        ("`dataset_meta.json`", "the pinned time anchor the seed rows are built around"),
    ]:
        A(f"| {f} | {desc} |")
    A("")

    A("## Reproducing it")
    A("")
    A("```bash")
    A("cd druid-harness && make up          # ~6-8 GB RAM, on-demand only")
    A("cd ../dataset")
    A("export PYTHONPATH=$PWD/../druid-harness")
    A("python3 gen_schemas.py               # specs, seed rows, schema_index.json")
    A("python3 load_all.py                  # ingest all 69 datasources + register lookups (~13 min)")
    A("python3 generate.py                  # examples.json: draw, split, balance")
    A("python3 validate_bulk.py             # four gates against the live cluster")
    A("python3 emit_sft.py                  # train.jsonl, val.jsonl, dataset_stats.json")
    A("python3 make_readme.py               # this file")
    A("```")
    A("")
    A(f"The time anchor is pinned in `dataset_meta.json` (**{st['anchor']}**). Seed rows span the 30 days "
      "ending there, which is what makes `CURRENT_TIMESTAMP`-relative queries return rows. Regenerating "
      "reproduces the seeds byte for byte; **change the anchor and you must reload every datasource**, "
      "because the queries were validated against the data that is currently ingested.")
    A("")

    A("## The split")
    A("")
    A("Whole families are held out, not individual variants. Holding out one variant of a family would "
      "leave its siblings in training with near-identical column names — the split has to be disjoint in "
      "vocabulary, not just in table name.")
    A("")
    A("| | Train | Validation |")
    A("| --- | ---: | ---: |")
    A(f"| Examples | {st['train']['records']} | {st['val']['records']} |")
    A(f"| Datasources appearing in prompts | {len(st['train']['prompt_tables'])} | "
      f"{len(st['val']['prompt_tables'])} |")
    A(f"| Domain families (each hand-written schema counts as one) | "
      f"{len({ix[s]['family'] for s in st['train']['prompt_tables']})} | "
      f"{len({ix[s]['family'] for s in st['val']['prompt_tables']})} |")
    A(f"| Shared datasources | — | **0** |")
    A(f"| Prompts carrying distractor tables | {st['train']['with_distractor_tables']} | "
      f"{st['val']['with_distractor_tables']} |")
    A("")
    A("**Held out for validation:** " + ", ".join(f"`{f}`" for f in sorted(held)) + ".")
    A("")
    A("These six were chosen so validation still exercises every enrichment shape training teaches: "
      "`streaming_media` has a JSON-as-string column, `cdn_edge` has a lookup, `support_tickets` has a "
      "multi-value dimension, `retail_pos` joins to `dim_stores`, and `crypto_trades` is plain numeric. "
      "`emit_sft.py` asserts the two sides share no datasource; the assertion is the guarantee.")
    A("")
    A("All nine hand-written schemas stay in training, because the "
      f"{n_hand} hand-authored examples from batch 01 use them.")
    A("")

    A("## Schemas")
    A("")
    A(f"{len(ix)} datasources, {sum(d['rows'] for d in ix.values()):,} rows ingested. Nineteen domain "
      "families become three variants each (two for `clinical_telemetry` and `workforce`), plus nine "
      "hand-written schemas and five dimension tables.")
    A("")
    A("The three variants of a family deliberately disagree on surface convention:")
    A("")
    A("| Variant | Datasource name | Columns | Secondary time column | Enrichment |")
    A("| --- | --- | --- | --- | --- |")
    A("| `v0` | `ds_app_logs` | snake_case, full width | epoch **milliseconds** | MVD, JSON, lookup |")
    A("| `v1` | `appLogsRaw` | **camelCase**, narrowed | ISO-8601 **string** | JSON only |")
    A("| `v2` | `app_logs_daily` | snake_case, different subset | `'yyyy-MM-dd HH:mm:ss'` **string** | "
      "MVD, lookup, join partner |")
    A("")
    A("| Family | Domain | Variants | Held out |")
    A("| --- | --- | --- | :---: |")
    for fam in sorted(fams, key=lambda f: (fams[f][0]["variant"] == "handwritten",
                                           fams[f][0]["variant"] == "dim", f)):
        ds = fams[fam]
        kind = ds[0]["variant"]
        label = {"handwritten": "hand-written", "dim": "dimension table"}.get(kind, f"{len(ds)}")
        A(f"| `{fam}` | {ds[0]['domain']} | {label} | {'yes' if fam in held else ''} |")
    A("")

    A("## What the examples teach")
    A("")
    A(f"{n_templates} templates across {len(st['train']['clusters'])} quirk clusters. A template binds to "
      "*roles* — a low-cardinality dimension, a numeric measure, the epoch-seconds column — so one "
      "template renders against every schema that has the roles it needs.")
    A("")
    A("| Cluster | Train | Val | What it teaches |")
    A("| --- | ---: | ---: | --- |")
    for c, what in CLUSTER_DOC.items():
        A(f"| `{c}` | {st['train']['clusters'].get(c, 0)} | {st['val']['clusters'].get(c, 0)} | {what} |")
    A("")
    A(f"**{sum(traps.values())} examples carry a trap**: the standard-SQL reflex, validated to fail "
      f"({traps['INVALID']} of them) or to return something different ({traps['DIFFERENT']}). "
      "Traps are never shown to the model — they exist to prove the example teaches something Druid "
      "actually requires.")
    A("")

    A("## Prompt formats")
    A("")
    A("Twelve renderers, sampled as evenly as eligibility allows, so the model learns to read a schema "
      "rather than memorise one layout. **The assistant turn never varies**: bare SQL, no fence, no prose, "
      "no trailing semicolon, every output alias double-quoted.")
    A("")
    A("| Format | Train | Val | Shape |")
    A("| --- | ---: | ---: | --- |")
    for f, what in FORMAT_DOC.items():
        A(f"| `{f}` | {st['train']['formats'].get(f, 0)} | {st['val']['formats'].get(f, 0)} | {what} |")
    A("")
    A("Two knobs turn underneath the format choice:")
    A("")
    A(f"- **Distractor tables.** {st['train']['with_distractor_tables']} of {st['train']['records']} "
      "training prompts carry one or two tables the query never touches. Real schema blocks are pasted in "
      "wholesale, and a model trained only on prompts where every table is needed learns to use every table.")
    A("- **Column order.** Columns are shuffled per example, and the time column leads only about half the "
      "time, so position never becomes the cue for which column is the timestamp.")
    A("")
    A("Some formats cannot express some things — a format with no column descriptions cannot name the keys "
      "inside a JSON string, and one that never mentions lookups cannot support `LOOKUP()`. Each renderer "
      "declares what it can express, each example declares what it needs, and the sampler only draws from "
      "formats that cover it.")
    A("")

    A("## Validation")
    A("")
    A("Four gates, all of which must pass:")
    A("")
    A("| Gate | Check |")
    A("| --- | --- |")
    A("| **EXEC** | the completion returns `VALID` from Druid 35.0.0 |")
    A("| **ROWS** | it returns at least one row |")
    A("| **LINT** | it contains the construct its cluster exists to teach |")
    A("| **TRAP** | the naive version fails, or returns something different |")
    A("")
    A("`VALID` alone is not enough, which is why ROWS and TRAP exist: "
      "`JSON_VALUE(json_string_col, '$.k')` is accepted by Druid and returns `NULL` for every row.")
    A("")
    A(f"**Result: {st['train']['records'] + st['val']['records']} of "
      f"{st['train']['records'] + st['train']['dropped_by_gates'] + st['val']['records'] + st['val']['dropped_by_gates']} "
      f"examples passed all four gates**, plus {sum(traps.values())} trap queries. "
      "`validation_report.json` has the per-example detail.")
    A("")

    A("## Lengths")
    A("")
    A("Characters, as a proxy for tokens.")
    A("")
    A("| | p50 | p95 | max |")
    A("| --- | ---: | ---: | ---: |")
    for split in ("train", "val"):
        p = st[split]["prompt_chars"]
        A(f"| {split} prompt | {p['p50']:,} | {p['p95']:,} | {p['max']:,} |")
    A(f"| answer | {st['train']['answer_chars']['mean']} (mean) | | "
      f"{st['train']['answer_chars']['max']} |")
    A("")
    A("The prompt is five to twenty times the answer. If the training config computes loss over the whole "
      "sequence, the schema block dominates it and the model spends most of its gradient learning to "
      "reproduce schemas. Masking the loss to the completion is worth checking before a long run — that "
      "is a call for `lora_training.py`, which this pipeline does not touch.")
    A("")

    A("## Known limits")
    A("")
    A("- **Approximate aggregators are the house style**, so the model will reach for "
      "`APPROX_COUNT_DISTINCT` even where an exact `COUNT(DISTINCT ...)` was wanted. That is a deliberate "
      "Druid-idiomatic choice, not an accident.")
    A("- **Nested `COMPLEX<json>` columns are out of scope.** JSON is taught as a string parsed with "
      "`PARSE_JSON`.")
    A("- **No performance dimension.** Every query is validated for correctness on ~900-row datasources; "
      "nothing here teaches the model which of two correct queries is cheaper at scale.")
    A(f"- **`reserved_column` is thin** ({st['train']['clusters'].get('reserved_column', 0)} training "
      "examples). Probing all 493 column names found only `value` and `language` colliding with Druid "
      "reserved words, so there is not much material. Reserved-word *aliases* are covered far more heavily "
      f"({st['train']['clusters'].get('reserved_alias', 0)} examples).")
    A("- **The window is 30 days.** Questions about quarters or years would return nothing, so none are "
      "asked.")
    A("")
    A("---")
    A("")
    A("Dialect facts, and the ones that were disproved, live in "
      "[`../druid_dataset_creation.md`](../druid_dataset_creation.md).")
    A("")

    (ROOT / "README.md").write_text("\n".join(L))
    print(f"wrote README.md ({len(L)} lines)")


CLUSTER_DOC = {
    "time_bucketing": "`TIME_FLOOR` / `TIME_CEIL` with ISO period grains",
    "relative_time": "`CURRENT_TIMESTAMP - INTERVAL '7' DAY`, never a hardcoded date",
    "grouping": "ordinals in `GROUP BY` / `ORDER BY`, `HAVING`, `CASE` buckets, subqueries",
    "mvd": "`MV_CONTAINS`, `MV_FILTER_ONLY`, `MV_OVERLAP`, `MV_LENGTH`, `UNNEST(MV_TO_ARRAY(...))`",
    "order_by_restriction": "a table scan may only order by `__time`; `LIMIT` does not rescue it",
    "approx_agg": "`APPROX_COUNT_DISTINCT`, `APPROX_QUANTILE_DS`",
    "missing_function": "no `NOW`, `DATEADD`, `DATEDIFF`, `DATE_FORMAT`, `IF`, `ILIKE`, `STDDEV`, `TOP n`",
    "time_extract_format": "`TIME_EXTRACT`, `TIME_FORMAT`, timezone arguments",
    "reserved_alias": "`AS \"hour\"`, `AS \"value\"`, `AS \"count\"` — quoting output aliases",
    "string_time_column": "`TIME_PARSE` with an explicit format for string timestamps",
    "latest_earliest": "`LATEST` / `EARLIEST` / `LATEST_BY`, and the `, 64` byte limit on strings",
    "timestamp_literal": "`TIMESTAMP 'yyyy-MM-dd HH:mm:ss'` — the ISO form is rejected",
    "filtered_agg": "`FILTER (WHERE ...)` on aggregates",
    "time_shift": "`TIME_SHIFT` and period-over-period comparison",
    "epoch_time_column": "`MILLIS_TO_TIMESTAMP`, and `* 1000` for epoch seconds",
    "join": "joins to a dimension table, including `LEFT JOIN` and filtering on the partner",
    "null_math": "`NULLIF`, `COALESCE`, `ROUND`, `CAST`",
    "string_ops": "`LIKE`, `CONCAT`, `UPPER`, `SUBSTRING`, `LENGTH`",
    "json_string": "`JSON_VALUE(PARSE_JSON(col), '$.key')` — the silent-NULL trap",
    "lookup": "`LOOKUP(col, 'name')` rather than a join to a lookup table",
    "reserved_column": "a column *named* `value` or `language` must be quoted as an identifier",
}

FORMAT_DOC = {
    "md_sections": "markdown headings, full descriptions — the format `prompt.py` serves",
    "ddl": "`CREATE TABLE` with trailing line comments",
    "yaml": "YAML schema block",
    "json": "JSON blob, the shape a programmatic caller injects",
    "pipe_table": "markdown pipe table, as pasted from a wiki or dbt doc",
    "verbose_rules": "long role preamble with an explicit numbered rules list",
    "no_system": "**two turns** — everything in the user message, no system turn",
    "question_first": "question ahead of the schema, so position is not a cue",
    "sample_rows": "schema plus two real rows, the Spider/BIRD convention",
    "compact": "one line per table, types only, no descriptions",
    "druid_native": "Druid's own type names (`LONG`/`STRING`), as the web console shows them",
    "bare": "column names only — no types, no descriptions, no rules",
}

if __name__ == "__main__":
    main()
