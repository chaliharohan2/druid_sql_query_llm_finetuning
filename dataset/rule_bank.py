"""Paraphrase banks and layouts for table rules (addendum F1).

v2's table rules came from about six fixed sentences, so a model could learn to recognise the sentences
instead of learning to follow instructions. This module holds:

  RULE_BANK      30 phrasings per rule type (register, length, backticks, imperative / descriptive vary)
  CLAUSES        imperative fragments that fold two rules into one sentence
  LAYOUTS        five ways to lay the rules out (no heading, `### INSTRUCTIONS`, `**Rules**`, numbered,
                 embedded in the overview paragraph)

The constraint each rule imposes is unchanged and is still recorded structurally on the datasource
(`kind`, `binding_column`), so G6 (validators.g6_instructions) checks the SQL, never the wording.

Slots used by the templates:
  {c}  the column in backticks        {b}  the column bare        {a_c}/{a_b}  the column with its article ("a `user_id`", "an `order_id`")
  {n}  what the table holds ("events") {cw} the column in words plural ("driver ids")
  {w}  what an exclusion flag marks ("test records", "soft-deleted records")

Not AI training or inference code.
"""
from __future__ import annotations

import random
import re

from glossary_terms import article

# --------------------------------------------------------------------------- phrasings
RULE_BANK: dict[str, list[str]] = {
    "nullable_metric": [
        "When aggregating {c}, only consider rows where {c} IS NOT NULL; unresolved rows should not be silently coerced into the average.",
        "Aggregates over {c} must skip NULLs: add {c} IS NOT NULL to the filter.",
        "{c} is often empty. Whenever you SUM, AVG, MIN or MAX it, restrict to rows where it is not NULL.",
        "Don't let missing {b} values leak into averages; filter on {b} IS NOT NULL before aggregating it.",
        "For any metric built on {c}, keep only the rows where {c} is populated (NOT NULL).",
        "A NULL {b} means the value was never recorded, so exclude those rows (`{b} IS NOT NULL`) from every aggregate of it.",
        "Always pair an aggregate of {c} with {a_c} IS NOT NULL condition.",
        "Rows with no {b} shouldn't count toward totals or averages. Filter them out with IS NOT NULL.",
        "SUM/AVG/MIN/MAX on {c} should only see non-null values, so add `{b} IS NOT NULL`.",
        "Null {b} = unknown, not zero. Exclude NULL rows before you aggregate {b}.",
        "Before averaging or summing {c}, drop rows where it is NULL.",
        "When you compute anything from {c}, include the condition {b} IS NOT NULL so blanks don't dilute the result.",
        "{c}: aggregate only non-null values. Add the IS NOT NULL predicate whenever it's used in SUM, AVG, MIN or MAX.",
        "Treat blank {b} as missing data. Any aggregate that uses it has to filter on IS NOT NULL first.",
        "Watch out for NULLs in {c} -- they must be filtered out with IS NOT NULL when you aggregate it.",
        "If a query aggregates {c}, it needs `WHERE {b} IS NOT NULL` (or the equivalent) so unresolved records aren't counted.",
        "Only rows that actually have {a_b} belong in {b} aggregates; use IS NOT NULL to enforce that.",
        "Use {c} IS NOT NULL as a filter whenever {c} is summed, averaged, or min/maxed.",
        "Don't coerce a missing {b} to 0. Aggregations of {b} are restricted to rows where it is not NULL.",
        "Reporting on {c} covers only rows with a recorded value, i.e. {c} IS NOT NULL.",
        "NULL-safe aggregation: {c} IS NOT NULL is mandatory when aggregating {c}.",
        "Numeric rollups of {c} (sum, average, min, max) exclude rows where {c} is NULL.",
        "Before you aggregate {b}, make sure the query filters out rows in which {b} is NULL.",
        "Missing {b} values are left as NULL in this table; keep them out of any aggregate by requiring {b} IS NOT NULL.",
        "For {b} statistics, ignore unset rows: add {c} IS NOT NULL.",
        "Heads up: {c} can be NULL. Aggregations must be limited to rows with {c} IS NOT NULL.",
        "The average (or sum, min, max) of {c} is only meaningful over rows where {c} is not NULL, so filter for that.",
        "When {b} is aggregated, include `{b} IS NOT NULL` in the filter. Rows with a NULL {b} are unresolved and must not be coerced.",
        "Ignore rows with a NULL {b} in aggregate calculations on {b}.",
        "Guard every {c} aggregate with an IS NOT NULL check on {c}.",
    ],
    "unique_by": [
        "Records are unique per {c}; do not use DISTINCT when counting {n} by it.",
        "Each row has its own {c}, so count with COUNT(*) rather than COUNT(DISTINCT {c}).",
        "{c} is unique per row in this table. Skip DISTINCT when you count {n} by {b}.",
        "There is exactly one row for each {b}; a plain COUNT is correct and DISTINCT is unnecessary.",
        "No duplicates on {c}: counting {n} by it doesn't need DISTINCT.",
        "Don't wrap {c} in COUNT(DISTINCT ...). It is already unique, so a simple row count gives the number of {n}.",
        "To count {n}, use COUNT(*) or COUNT({b}); the column is unique, so DISTINCT would only add cost.",
        "{c} identifies a single record. Counting by it must not use DISTINCT.",
        "One record per {b}. When someone asks how many {n}, count rows instead of distinct {b} values.",
        "Because {b} is unique in this table, avoid DISTINCT in counts of {n} by {b}.",
        "Never use COUNT(DISTINCT {c}) here: values of {c} never repeat.",
        "Unique key: {c}. Counting {n} is a plain COUNT with no DISTINCT.",
        "Treat {b} as a primary key -- every value appears once -- so counts by it should not use DISTINCT.",
        "DISTINCT is redundant on {c}; each {b} appears in only one record.",
        "{c} never repeats across rows, so use a straight COUNT for {n} (no DISTINCT).",
        "When counting {n} per {b}, don't use DISTINCT; the column is already unique.",
        "Row count = number of {n}: {b} is unique, so no COUNT(DISTINCT {b}) is needed.",
        "Do not apply DISTINCT to {c} when counting -- there is one record for each value.",
        "Each {b} maps to exactly one record, so counting {n} by it is a simple COUNT.",
        "The uniqueness of {c} means DISTINCT must be left out of counts.",
        "COUNT, not COUNT(DISTINCT), for {n} by {c} -- it is unique per record.",
        "A given {b} appears in a single row only. Count {n} without DISTINCT.",
        "No DISTINCT needed (or wanted) when counting {n} via {c}: the values are one-per-record.",
        "{c} is a per-record identifier, so DISTINCT counts on it are forbidden; count rows.",
        "Counting {n}? {c} is unique, therefore skip DISTINCT.",
        "Since every record carries its own {b}, distinct counting on {b} is unnecessary and should be avoided.",
        "Use COUNT(*) for {n}; {c} is guaranteed unique per record.",
        "Rows are keyed by {c}: do not use DISTINCT when counting {n} by it.",
        "There's no need for DISTINCT on {c} -- it holds one value per record.",
        "Keep DISTINCT out of counts on {c}; the column is unique per record.",
    ],
    "exact_distinct": [
        "Use an exact `COUNT(DISTINCT {b})` for reporting on unique {cw}; approximate counts are not acceptable for this field.",
        "Distinct counts of {c} must be exact: use COUNT(DISTINCT {c}), not an APPROX_ function.",
        "Finance signs off on the unique {cw} numbers, so approximations are out; count them with COUNT(DISTINCT {c}).",
        "For unique {cw}, always use exact `COUNT(DISTINCT {b})`. Never APPROX_COUNT_DISTINCT.",
        "{c}: approximate distinct counts are not acceptable here. Use the exact form.",
        "When asked how many different {cw} there are, run an exact COUNT(DISTINCT {c}).",
        "Do not estimate unique {cw}. An exact COUNT(DISTINCT {c}) is required.",
        "Exact cardinality only for {c}: COUNT(DISTINCT), no sketches or approximations.",
        "Counting distinct {b} values? Use COUNT(DISTINCT {b}) -- approximate aggregators are not allowed on this column.",
        "Reports quote unique {cw} to the exact number. Avoid APPROX_COUNT_DISTINCT on {c}.",
        "Unique {cw} must be computed exactly (COUNT(DISTINCT {c})); HLL-style approximations are off limits.",
        "Prefer exactness over speed for {b}: distinct counts use COUNT(DISTINCT {b}).",
        "Use COUNT(DISTINCT {c}) -- not APPROX_COUNT_DISTINCT -- whenever you report unique {cw}.",
        "The distinct count of {c} has to be precise, so approximations are not acceptable.",
        "Anything reporting unique {cw} should call COUNT(DISTINCT {c}) to get an exact figure.",
        "Exact `COUNT(DISTINCT {b})` only; an approximate distinct count would be wrong for this field.",
        "For {b}, the house style of approximate distincts is overridden: use the exact count.",
        "How many unique {cw}? Answer with COUNT(DISTINCT {c}); estimates are not acceptable.",
        "Approximations are banned on {b}. Count its distinct values exactly.",
        "Precision matters for {cw}: use exact distinct counting on {c}.",
        "Whenever {c} is counted distinctly, the count must be exact (COUNT(DISTINCT {c})).",
        "Don't use APPROX_COUNT_DISTINCT on {c}; go with the exact COUNT(DISTINCT ...) form.",
        "Distinct {b} totals are audited; use an exact COUNT(DISTINCT {b}).",
        "Unique {cw} = exact COUNT(DISTINCT {c}). Sketch-based approximations are not acceptable.",
        "The exact number of distinct {cw} is required, so write COUNT(DISTINCT {c}).",
        "For unique-{b} metrics choose exact counting, not the approximate aggregate.",
        "Count unique {cw} exactly with COUNT(DISTINCT {c}).",
        "This field needs an exact distinct count: COUNT(DISTINCT {c}) rather than any approximate function.",
        "Report distinct {cw} with the exact aggregate, COUNT(DISTINCT {c}); accuracy beats speed here.",
        "No approximate counting for {c} -- unique values are counted with COUNT(DISTINCT {c}).",
    ],
    "exclude_flag": [
        "Reports on this table always exclude {w}: keep only rows where {c} = 0.",
        "Filter out {w} in every query: {c} must be 0.",
        "Add `{b} = 0` to the WHERE clause of every query on this table so {w} never show up.",
        "{w} are flagged with {c} = 1 and must never be reported. Keep {c} = 0.",
        "Always exclude {w}. The flag is {b}; only rows with {b} = 0 count.",
        "Analysts never want {w}: restrict all queries to {c} = 0.",
        "Every query on this table needs {c} = 0 so that {w} are excluded.",
        "Exclude {w} from all results using the {b} flag (0 means keep).",
        "Only rows with {b} = 0 are reportable; {w} carry {b} = 1.",
        "Remember: {w} are not part of any report. Filter on {c} = 0.",
        "Make sure {w} are filtered out (`{b} = 0`).",
        "The {b} flag marks {w}; queries must keep {b} = 0.",
        "All figures from this table exclude {w}. Apply {c} = 0.",
        "Drop {w} from every result set -- {c} has to equal 0.",
        "{w}: always excluded, via {c} = 0.",
        "Keep the WHERE clause honest: {c} = 0, so {w} don't inflate the numbers.",
        "Do not include {w} in any metric. Require {b} = 0.",
        "Numbers on this table only count rows where {c} is 0 (i.e. not {w}).",
        "To exclude {w}, filter {b} = 0 on every query.",
        "{c} = 0 is a standing filter on this table: it removes {w}.",
        "Never report {w}; the flag column {b} must be 0.",
        "Rows where {b} = 1 are {w} and must be filtered out of all reports.",
        "Apply the standard exclusion for {w} ({c} = 0) to every question.",
        "Queries should ignore {w}, i.e. require {b} = 0.",
        "Standing rule: {b} = 0. That leaves out {w}.",
        "When reporting from this table, filter to {c} = 0 to leave out {w}.",
        "We never count {w}: restrict to rows with {c} = 0.",
        "{w} must not appear in results. Enforce this with {c} = 0.",
        "Include only rows where {b} is 0; {w} are excluded from every report.",
        "Exclude {w} always (keep rows where {c} = 0).",
    ],
    "duration_end": [
        "For duration questions, only consider rows where {c} IS NOT NULL (an activity with no end time has not finished).",
        "Durations need a finished activity: require {c} IS NOT NULL whenever you compute one.",
        "When measuring how long something took, filter on {c} IS NOT NULL; rows without an end time are still in progress.",
        "An empty {b} means the activity hasn't ended, so leave those rows out of any duration calculation (IS NOT NULL).",
        "Duration metrics only make sense for completed rows: {c} must be NOT NULL.",
        "Add `{b} IS NOT NULL` to every query that computes an elapsed time.",
        "Unfinished work has no {b}. Exclude it (IS NOT NULL) from duration figures.",
        "For time-taken questions, restrict to rows where {c} is populated (NOT NULL).",
        "Elapsed-time calculations must skip rows with a NULL {b}.",
        "If the question is about duration, filter out rows where {c} is NULL -- they haven't finished.",
        "Rows with no {b} are still open; don't include them when computing how long things took (require {b} IS NOT NULL).",
        "{c} IS NOT NULL is required for any duration or elapsed-time result.",
        "How long did it take? Only finished rows count, i.e. {c} IS NOT NULL.",
        "Guard duration math with {a_c} IS NOT NULL predicate: a missing end time means not finished.",
        "Only closed-out records (those with a non-null {b}) belong in duration calculations.",
        "Handling-time style metrics should be limited to rows whose {b} is not NULL.",
        "For anything about time spent or turnaround, keep rows where {c} IS NOT NULL.",
        "No {b}, no duration: filter those NULLs out first.",
        "A NULL {b} means the activity is still running. Duration questions must exclude such rows.",
        "When you compute a duration from {b}, include {b} IS NOT NULL in the filter.",
        "Ignore unfinished rows in duration analysis by requiring {c} IS NOT NULL.",
        "Duration queries: WHERE {c} IS NOT NULL.",
        "To measure how long something took, {c} must be set (not NULL); otherwise the row is excluded.",
        "Elapsed time is undefined until {b} exists; exclude NULL {b} rows from those calculations.",
        "Time-to-complete figures use finished rows only -- {c} IS NOT NULL.",
        "Any duration question must filter out rows with a NULL {b}.",
        "Exclude in-progress rows (NULL {b}) when reporting durations.",
        "Rows still in flight have a NULL {b}; leave them out of duration results with IS NOT NULL.",
        "Use `{b} IS NOT NULL` whenever the answer involves how long something lasted.",
        "Only rows with a recorded {b} may feed a duration measure.",
    ],
}

# imperative fragments (start with a capital, no final full stop) that fold into one sentence
CLAUSES: dict[str, list[str]] = {
    "nullable_metric": [
        "Aggregate {c} only over rows where it is not NULL",
        "Keep {c} IS NOT NULL in any SUM, AVG, MIN or MAX of it",
        "Drop NULL {b} rows before averaging {b}",
        "Filter out NULL {b} values before you aggregate them",
        "Never let NULL {b} rows into an aggregate of {b}",
        "Require {c} IS NOT NULL whenever {b} is aggregated",
        "Skip NULLs when aggregating {c}",
        "Treat a NULL {b} as unresolved and leave it out of {b} averages",
    ],
    "unique_by": [
        "Do not use DISTINCT when counting {n} by {c}, since it is unique per record",
        "Count {n} with COUNT(*); {c} is already unique",
        "Leave DISTINCT off counts on {c}",
        "Skip DISTINCT when counting {n} by {b}",
        "Avoid COUNT(DISTINCT {c}); each value appears once",
        "Count rows, not distinct {b} values, to get the number of {n}",
        "Don't apply DISTINCT to the unique column {c}",
        "Use a plain COUNT for {n} by {b} (no DISTINCT)",
    ],
    "exact_distinct": [
        "Use an exact COUNT(DISTINCT {c}) for unique {cw}, never an approximation",
        "Count unique {cw} exactly with COUNT(DISTINCT {c})",
        "Avoid APPROX_COUNT_DISTINCT on {c} and use the exact form",
        "Report distinct {cw} with an exact COUNT(DISTINCT {b})",
        "Keep distinct counts of {b} exact, not approximate",
        "Use COUNT(DISTINCT {c}) whenever you report unique {cw}",
        "Give exact figures for distinct {cw} (COUNT(DISTINCT {c}))",
        "Don't estimate the number of unique {cw}; count distinct {b} values exactly",
    ],
    "exclude_flag": [
        "Exclude {w} by requiring {c} = 0",
        "Filter every query to {c} = 0 so {w} stay out",
        "Keep only rows where {b} = 0, which leaves out {w}",
        "Leave {w} out of the results ({c} = 0)",
        "Apply {c} = 0 to drop {w}",
        "Never report {w}: require {b} = 0",
        "Add {c} = 0 to remove {w}",
        "Make {b} = 0 a standing filter, since it excludes {w}",
    ],
    "duration_end": [
        "For durations require {c} IS NOT NULL",
        "Only use rows with {c} IS NOT NULL in elapsed-time calculations",
        "Exclude rows with a NULL {b} from duration figures",
        "Filter {c} IS NOT NULL whenever you measure how long something took",
        "Leave unfinished rows (NULL {b}) out of any duration",
        "Restrict duration questions to rows where {b} is not NULL",
        "Skip rows where {b} is NULL when computing time taken",
        "Require {b} IS NOT NULL for turnaround-style metrics",
    ],
}
FOLD_LEADS = ["", "", "", "Two rules: ", "Standing instructions: ", "Note: "]
FOLD_JOINERS = ["; ", ", and ", " -- and ", ". Also, ", "; in addition, "]

_FLAG_NOUNS = {
    "is_test_record": ["test records", "test records", "test rows", "internal test data", "records created by tests",
                       "test traffic", "synthetic test entries"],
    "is_deleted": ["soft-deleted records", "soft-deleted records", "deleted rows", "rows marked as deleted",
                   "logically deleted records", "tombstoned rows"],
}


def plural(word: str) -> str:
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    return word + "s"


def col_words(col: str) -> str:
    """`driverId` / `session_id` -> "driver ids"; `correlation_hash` -> "correlation hashes" (plural, for prose)."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", col.replace("_", " ")).lower().strip()
    head, _, last = s.rpartition(" ")
    return (head + " " if head else "") + plural(last)


def with_article(col: str, text: str) -> str:
    """"a `user_id`" / "an `order_id`": the article follows how the column name is read aloud."""
    first = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", col.replace("_", " ")).lower().split()[0]
    return f"{article(first)} {text}"


def _slots(instr: dict, d: dict, rng: random.Random) -> dict:
    col = instr["binding_column"]
    noun = d.get("noun") or "records"
    slots = {"c": f"`{col}`", "b": col, "n": noun, "cw": col_words(col), "w": "",
             "a_c": with_article(col, f"`{col}`"), "a_b": with_article(col, col)}
    if instr["kind"] == "exclude_flag":
        slots["w"] = rng.choice(_FLAG_NOUNS.get(col, ["excluded records"]))
    return slots


def render_rule(instr: dict, d: dict, rng: random.Random) -> tuple[str, str]:
    """One rule as a sentence. Returns (text, phrasing key) -- the key is `kind:template_index`."""
    bank = RULE_BANK[instr["kind"]]
    i = rng.randrange(len(bank))
    return bank[i].format(**_slots(instr, d, rng)), f"{instr['kind']}:{i}"


def render_clause(instr: dict, d: dict, rng: random.Random) -> tuple[str, str]:
    bank = CLAUSES[instr["kind"]]
    i = rng.randrange(len(bank))
    return bank[i].format(**_slots(instr, d, rng)), f"{instr['kind']}:c{i}"


def render_rules(instrs: list[dict], d: dict, rng: random.Random, fold_p: float = 0.15) -> tuple[list[str], list[str]]:
    """Sentences for a table's rules (two rules are folded into one sentence with probability fold_p)."""
    if len(instrs) >= 2 and rng.random() < fold_p:
        (a, ka), (b, kb) = render_clause(instrs[0], d, rng), render_clause(instrs[1], d, rng)
        lead = rng.choice(FOLD_LEADS)
        joiner = rng.choice(FOLD_JOINERS)
        b_txt = b[0].lower() + b[1:]
        text = f"{lead}{a}{joiner}{b_txt}."
        return [text], [ka, kb]
    texts, keys = [], []
    for ins in instrs:
        t, k = render_rule(ins, d, rng)
        texts.append(t)
        keys.append(k)
    return texts, keys


# --------------------------------------------------------------------------- layouts
LAYOUTS = [("plain", 0.16), ("instructions", 0.24), ("rules_bold", 0.20), ("numbered", 0.20), ("embedded", 0.20)]


def layout_lines(layout: str, overview: str, sentences: list[str], heading_case: str) -> list[str]:
    """Overview + rules under a layout. Returns lines (joined with newlines by the caller)."""
    out = [overview] if overview else []
    if not sentences:
        return out
    if layout == "embedded":
        para = " ".join(sentences)
        return [(overview + " " + para).strip()]
    if layout == "instructions":
        out.append("### INSTRUCTIONS" if heading_case == "upper" else "### Instructions")
        out += [f"* {s}" for s in sentences]
    elif layout == "rules_bold":
        out.append("**Rules**")
        out += [f"- {s}" for s in sentences]
    elif layout == "numbered":
        out += [f"{i}. {s}" for i, s in enumerate(sentences, 1)]
    else:  # plain: bullets, no heading (v2's original layout)
        out += [f"- {s}" for s in sentences]
    return out


def weighted(rng: random.Random, pairs):
    names, weights = zip(*pairs)
    return rng.choices(names, weights=weights)[0]
