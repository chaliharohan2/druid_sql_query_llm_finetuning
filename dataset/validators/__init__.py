"""Validation gates G1-G9 (druid_sql_dataset_v2_plan.md, section 6).

Each gate is a function `(...) -> (ok: bool, reasons: list[str])`. An example
must pass every gate that applies to it before it can enter the dataset.
Gates that need the live cluster (G1, G4) take a `DruidClient`; gates that
only need the schema/text (G2, G3, G5, G6, G7, G8) are pure functions; G9
calls the teacher-judge LLM.

Not AI training or inference code: this only validates generated SQL.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "druid-harness"))

from harness.api import run_query as _raw_run_query  # noqa: E402

import time as _time  # noqa: E402


class InfraError(RuntimeError):
    """Druid itself is unavailable (502/503, refused connection). Not a verdict on the example:
    callers must NOT record a rejection, only retry later."""


_INFRA_PAT = re.compile(r"502 Bad Gateway|503|504|Service Unavailable|Gateway Time-?out|Connection refused|"
                        r"Max retries exceeded|Connection aborted|RemoteDisconnected|Broken pipe|"
                        r"Failed to establish a new connection", re.I)


def run_query(sql, *, client=None, timeout_seconds=45, max_rows=100, tries=8):
    """`harness.api.run_query` that waits out infrastructure failures instead of reporting them
    as an invalid query. A genuine SQL error still comes back as INVALID immediately."""
    last = None
    for i in range(tries):
        r = _raw_run_query(sql, client=client, timeout_seconds=timeout_seconds, max_rows=max_rows)
        if r.status == "VALID" or not (r.error_message and _INFRA_PAT.search(r.error_message)):
            return r
        last = r.error_message
        _time.sleep(min(5 * 2 ** i, 60))
    raise InfraError(f"Druid unavailable after {tries} tries: {(last or '')[:120]}")

import roles as roles_mod  # noqa: E402
import teacher  # noqa: E402
from . import sqlparse as sp  # noqa: E402

WORSE, BETTER, NEUTRAL = roles_mod.WORSE, roles_mod.BETTER, roles_mod.NEUTRAL


# ------------------------------------------------------------------------- G1
def g1_execute(sql: str, client, timeout_seconds: int = 45):
    r = run_query(sql, client=client, timeout_seconds=timeout_seconds)
    if r.status != "VALID":
        return False, [f"G1 {r.status}: {(r.error_message or '')[:250]}"], r
    return True, [], r


# ------------------------------------------------------------------------- G2
def g2_grounded(sql: str, schema_ids: list[str], index: dict):
    """Every column reference resolves to a real column of a table the query actually
    uses, or to an alias defined inside the query (subquery / CTE / window outputs).

    A column that exists only in a *distractor* table is invalid, because only
    the tables the query references contribute columns.
    """
    from sqlglot import exp
    try:
        tree = sp.parse(sql)
    except Exception as exc:  # noqa: BLE001
        return False, [f"G2 parse failed: {exc}"]
    prompt_tables = {index[sid]["datasource"]: {c[0] for c in index[sid]["columns"]} | {"__time"}
                     for sid in schema_ids}
    cte_names = {c.alias for c in tree.find_all(exp.CTE)}
    subq_aliases = {q.alias for q in tree.find_all(exp.Subquery) if q.alias}
    real: dict[str, str] = {}  # qualifier (alias or name) -> datasource
    for t in tree.find_all(exp.Table):
        if t.name in cte_names and not t.db:
            continue
        if t.name not in prompt_tables:
            return False, [f"G2 references table {t.name!r} not in the prompt's schemas"]
        real[t.name] = t.name
        if t.alias:
            real[t.alias] = t.name
    used_cols = set().union(*(prompt_tables[n] for n in set(real.values()))) if real else set()
    alias_defs = Counter(a.alias for a in tree.find_all(exp.Alias) if a.alias)
    derived = set(alias_defs)
    unnest_aliases: set[str] = set()
    for u in tree.find_all(exp.Unnest):  # UNNEST(...) AS t(tag): "t" is a qualifier, "tag" a column
        al = u.args.get("alias")
        if al is not None:
            unnest_aliases.add(al.name)
            derived.update(c.name for c in al.columns)

    reasons = []
    for col in tree.find_all(exp.Column):
        name, qual = col.name, col.table or None
        parent = col.parent
        if not qual and name.upper() in sp._UNIT_WORDS and isinstance(parent, exp.Func) \
                and name not in used_cols:
            continue  # TIMESTAMPDIFF(HOUR, ...) parses its unit as a bare column
        if isinstance(col.this, exp.Star):
            continue
        self_ref = isinstance(parent, exp.Alias) and parent.alias == name
        if qual:
            if qual in real:
                if name not in prompt_tables[real[qual]]:
                    reasons.append(f"G2 column {name!r} not found in table {real[qual]!r}")
            elif qual in unnest_aliases:
                pass
            elif qual in cte_names or qual in subq_aliases:
                if name not in derived and name not in used_cols:
                    reasons.append(f"G2 column {name!r} not produced by {qual!r}")
            else:
                reasons.append(f"G2 unknown qualifier {qual!r} for column {name!r}")
        elif name in used_cols:
            continue
        elif name in derived and (not self_ref or alias_defs[name] >= 2):
            continue  # a lone `x AS "x"` cannot vouch for itself; a CTE's alias re-exposed outside can
        else:
            reasons.append(f"G2 column {name!r} not found in any table the query uses")
    return (not reasons), sorted(set(reasons))


# ------------------------------------------------------------------------- G3
_SENTINELS = {"none", "unknown", "n/a", "null", "other", "unclassified"}


def _is_sentinel(lit: str) -> bool:
    """'none'-style placeholder values (`roaming_partner <> 'none'`) that a question implies."""
    return lit.strip().lower() in _SENTINELS


def _named_in(lit: str, question_lower: str) -> bool:
    """Is this SQL value 'named' in the question, allowing the ways people write an
    enumerated value: `page_view` as "page view", `declined` as "decline(d)", a LIKE
    prefix without its wildcard or trailing dot. A different value never matches."""
    norm = re.sub(r"[_\-]+", " ", lit.lower()).strip(" .%")
    q = re.sub(r"[_\-]+", " ", question_lower)
    if norm and norm in q:
        return True
    toks = [t for t in re.split(r"[^a-z0-9]+", norm) if len(t) >= 3]
    if not toks:
        return False
    stem = toks[0][:5]
    return bool(re.search(rf"\b{re.escape(stem)}", q))


_QUOTED = re.compile(r"'([^']{1,60})'|\"([^\"]{1,60})\"")


def g3_literal_fidelity(question: str, sql: str, allowed_extra: set[str] | None = None,
                        prompt_text: str | None = None, value_lists: list[list[str]] | None = None):
    allowed_extra = {v.lower() for v in (allowed_extra or set())}
    q_phrases = {(m.group(1) or m.group(2)).strip("%") or (m.group(1) or m.group(2))
                 for m in _QUOTED.finditer(question)}
    try:
        tree = sp.parse(sql)
        sql_lits = set(sp.filter_literals(tree))
    except Exception:
        sql_lits = set(re.findall(r"'([^']*)'", sql))
    # LIKE patterns: the question names the prefix/substring, not the wildcards
    sql_lits = {l.strip("%") or l for l in sql_lits}

    reasons = []
    q_lower = question.lower()
    try:
        all_lits = {l.strip("%") or l for l in sp.string_literals(sp.parse(sql))}
    except Exception:
        all_lits = sql_lits
    for phrase in q_phrases:
        # a quoted phrase may be a filter value or a structural argument (JSON path, format, lookup name)
        if not any(phrase.lower() == lit.lower() for lit in sql_lits | all_lits):
            reasons.append(f"G3 question names {phrase!r} but SQL does not filter on it")
    for lit in sql_lits:
        if lit.lower() in allowed_extra or _is_sentinel(lit):
            continue
        if prompt_text is not None:
            # Label well-posedness: the exact literal must be recoverable from what the model sees.
            bare = lit.strip("%").rstrip(". ")
            if bare in question or (bare.islower() and bare.replace(" ", "").isalnum() and bare in q_lower):
                continue  # written verbatim in the question (any case for a plain lowercase word)
            if bare in prompt_text:
                # A paraphrase of a LISTED value is fine ("rejected" -> 'declined') unless the question
                # names a DIFFERENT value from the same list: that is the v1 swapped-literal bug.
                for lst in value_lists or []:
                    if lit in lst:
                        clash = next((o for o in lst if o != lit and len(o) >= 3 and _named_in(o, q_lower)), None)
                        if clash and not _named_in(lit, q_lower):
                            reasons.append(f"G3 question names {clash!r} but SQL filters on {lit!r}")
                continue
            reasons.append(f"G3 filter value {lit!r} is not recoverable from the question or the schema text")
            continue
        if _named_in(lit, q_lower):
            continue
        # a literal used as a value in the SQL must trace back to the question,
        # either quoted verbatim or appearing as a bare word/phrase in it.
        if lit.lower() in q_lower:
            continue
        if any(lit.lower() == p.lower() for p in q_phrases):
            continue
        reasons.append(f"G3 SQL filters on {lit!r} which the question never named "
                       f"and no instruction requires")
    return (not reasons), reasons


# ------------------------------------------------------------------------- G4
G4_MAX_ROWS = 20_000  # bigger results are not realistic answers and strain the broker


_NUMERIC_STR = re.compile(r"^-?\d+(\.\d+)?$")
_DATE_STR = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?Z?$")


def _norm_value(v):
    """Comparable form of one result value: numbers (and numeric-looking strings such as
    TIME_FORMAT's '05') collapse to a rounded float, so EXTRACT(HOUR ...) == TIME_FORMAT(..., 'HH')."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    if isinstance(v, str) and _NUMERIC_STR.match(v.strip()):
        return round(float(v), 6)
    if isinstance(v, str):
        m = _DATE_STR.match(v.strip())
        if m:  # '2026-03' (TIME_FORMAT) and '2026-03-01T00:00:00.000Z' (TIME_FLOOR) are the same month
            y, mo, d, hh, mi, ss = m.groups()
            return f"{y}-{mo}-{d or '01'} {hh or '00'}:{mi or '00'}:{ss or '00'}"
    return v


def _row_key(values: tuple):
    return tuple((0, "") if v is None else (1, repr(v)) for v in values)


def _canonical_rows(rows: list[dict]) -> list[tuple]:
    """Rows as value tuples, independent of output aliases AND of SELECT-list order.

    Values inside a row are sorted, so `SELECT a, b` and `SELECT b, a` agree.
    Two different columns that hold identical values in every row would also
    agree, which is harmless for a result-equality check.
    """
    def norm_row(r: dict) -> tuple:
        vals = [_norm_value(v) for v in r.values()]
        return tuple(sorted(vals, key=lambda v: (v is not None, repr(v))))
    return sorted((norm_row(r) for r in rows), key=_row_key)


def _close_enough(a, b, tol=1e-6) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


def _columns(rows: list[dict]) -> list[list]:
    return [sorted((_norm_value(v) for v in col), key=lambda v: (v is not None, repr(v)))
            for col in zip(*[list(r.values()) for r in rows])] if rows else []


def _project_to_matching_columns(small: list[dict], big: list[dict]):
    """If every column of `small` also appears (same values) in `big`, return `big` cut down to
    those columns; else None. Lets an answer that also displays an extra column agree."""
    cs, cb = _columns(small), _columns(big)
    used, picked = set(), []
    for col in cs:
        hit = next((j for j, other in enumerate(cb) if j not in used and len(other) == len(col)
                    and all(_close_enough(x, y) for x, y in zip(col, other))), None)
        if hit is None:
            return None
        used.add(hit)
        picked.append(hit)
    keys = [list(r.keys()) for r in big]
    return [{k[i]: r[k[i]] for i in picked} for r, k in zip(big, keys)]


def _rows_match(a: list[dict], b: list[dict]) -> bool:
    if len(a) != len(b):
        return False
    if a and b and len(a[0]) != len(b[0]):
        small, big = (a, b) if len(a[0]) < len(b[0]) else (b, a)
        projected = _project_to_matching_columns(small, big)
        if projected is None:
            return False
        a, b = small, projected
    ra, rb = _canonical_rows(a), _canonical_rows(b)
    for row_a, row_b in zip(ra, rb):
        if len(row_a) != len(row_b) or not all(_close_enough(x, y) for x, y in zip(row_a, row_b)):
            return False
    return True


def fingerprint(rows: list[dict]) -> str:
    import hashlib
    canon = [[_norm_value(v) for v in r] for r in _canonical_rows(rows)]
    return hashlib.sha1(repr(canon).encode()).hexdigest()[:16]


def _swap_tables(sql: str, mapping: dict[str, str]) -> str:
    for primary, variant in mapping.items():
        if primary != variant:
            sql = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(primary)}(?![A-Za-z0-9_])", variant, sql)
    return sql


def g4_result_agreement(sql_a: str, sql_b: str, schema_ids: list[str], index: dict, client,
                        min_nonempty: int = 2):
    """Gold and independent SQL must agree on every seed, and the gold result
    must be non-empty on at least `min_nonempty` seeds (two queries that both
    return nothing agree by coincidence). Returns (ok, reasons, fingerprints).
    """
    n_seeds = min(len(index[sid].get("g4_variants") or [index[sid]["datasource"]])
                  for sid in schema_ids)
    reasons, fps, nonempty = [], [], 0
    for i in range(n_seeds):
        mapping = {}
        for sid in schema_ids:
            variants = index[sid].get("g4_variants") or [index[sid]["datasource"]]
            mapping[index[sid]["datasource"]] = variants[i]
        qa, qb = _swap_tables(sql_a, mapping), _swap_tables(sql_b, mapping)
        ra = run_query(qa, client=client, timeout_seconds=45, max_rows=G4_MAX_ROWS)
        rb = run_query(qb, client=client, timeout_seconds=45, max_rows=G4_MAX_ROWS)
        if ra.status != "VALID":
            reasons.append(f"G4 gold SQL failed on seed {i}: {(ra.error_message or '')[:120]}")
            continue
        if (ra.row_count or 0) > G4_MAX_ROWS:
            reasons.append(f"G4 result too large ({ra.row_count} rows) on seed {i}")
            continue
        if rb.status != "VALID":
            reasons.append(f"G4 second SQL failed on seed {i}: {(rb.error_message or '')[:120]}")
            continue
        if not _rows_match(ra.sample or [], rb.sample or []):
            reasons.append(f"G4 result mismatch on seed {i} "
                           f"({ra.row_count} vs {rb.row_count} rows)")
        if ra.row_count:
            nonempty += 1
        fps.append(f"seed{i}:{fingerprint(ra.sample or [])}")
    if not reasons and nonempty < min(min_nonempty, n_seeds):
        reasons.append(f"G4 gold result empty on {n_seeds - nonempty}/{n_seeds} seeds")
    return (not reasons), reasons, fps


# ------------------------------------------------------------------------- G5
_NO_MATH_ROLES = {roles_mod.CATEGORY_NUMERIC}


def g5_role_compliance(sql: str, schema_ids: list[str], index: dict, allow_sum_total: bool = False):
    role_by_col: dict[str, dict] = {}
    id_cols: set[str] = set()
    epoch_cols: set[str] = set()
    for sid in schema_ids:
        d = index[sid]
        for m in d["roles"].get("metrics", []):
            role_by_col[m["name"]] = {"role": m.get("role", roles_mod.NONADDITIVE),
                                      "polarity": m.get("polarity", NEUTRAL)}
        id_cols |= set(d["roles"].get("hi_card", []))
        epoch_cols |= set(d["roles"].get("epoch_cols", []))

    try:
        tree = sp.parse(sql)
    except Exception as exc:  # noqa: BLE001
        return False, [f"G5 parse failed: {exc}"]

    reasons = []
    for func, cols, is_distinct, bare in sp.aggregations(tree):
        for c in bare:
            if c in epoch_cols and func in ("SUM", "AVG"):
                reasons.append(f"G5 {func}({c}) on a raw epoch time column; "
                               f"aggregate a difference or convert it first")
            if c in id_cols and func in ("SUM", "AVG", "MIN", "MAX"):
                reasons.append(f"G5 {func}({c}) on an id column; use COUNT(DISTINCT) or APPROX_COUNT_DISTINCT")
            meta = role_by_col.get(c)
            if not meta:
                continue
            role = meta["role"]
            if role in _NO_MATH_ROLES and func in ("SUM", "AVG", "MIN", "MAX"):
                reasons.append(f"G5 {func}({c}) on a category-like numeric column {c!r}")
            if role == roles_mod.NONADDITIVE and func == "SUM" and not allow_sum_total:
                reasons.append(f"G5 SUM({c}) on a non-additive measure without the question "
                               f"asking for a total")
    return (not reasons), reasons


# ------------------------------------------------------------------------- G6
def g6_instruction_compliance(sql: str, required_predicates: list[str] | None,
                              forbidden_constructs: list[str] | None):
    reasons = []
    norm = re.sub(r"\s+", " ", sql).upper()
    for pred in required_predicates or []:
        if re.sub(r"\s+", " ", pred).upper() not in norm:
            reasons.append(f"G6 missing required predicate {pred!r}")
    for tok in forbidden_constructs or []:
        if tok.upper() in norm:
            reasons.append(f"G6 forbidden construct {tok!r} present")
    return (not reasons), reasons


def _num_pat(n) -> str:
    txt = str(int(n)) if float(n).is_integer() else f"{n:g}"
    return rf"(?<![\w.]){re.escape(txt)}(?:\.0+)?(?![\w.])"


def glossary_applied(g: dict, clean: str) -> tuple[bool, str]:
    """Does the SQL (double quotes stripped, whitespace collapsed) apply glossary entry `g`?

    Deterministic and deliberately loose about *how* (any column order, any operator spelling): it checks
    that the definition's columns, literals and numbers are all used, plus a shape-specific signal (a
    negation for not_in, a LIKE for a prefix, the window for time_active). Whether the logic is RIGHT is
    what G4 (independent answer) and G9 (judge) are for."""
    shape = g.get("shape", "value_list")
    cols = g.get("columns") or [g["binding_column"]]
    for c in cols:
        if not re.search(rf"\b{re.escape(c)}\b", clean):
            return False, f"column {c} not used"
    lits = g.get("values")
    if lits is None:
        lits = re.findall(r"'([^']+)'", g["predicate"])
    up = clean.upper()
    if shape == "like_prefix":
        p = lits[0]
        if f"'{p}" not in clean or not re.search(r"\bLIKE\b|STARTS_WITH|\bLEFT\s*\(|SUBSTR", up):
            return False, f"prefix {p!r} not matched"
        return True, ""
    if shape == "time_active":
        n = g["window_days"]
        if not (re.search(rf"INTERVAL\s+'?{n}'?\s+DAY", clean, re.I) or f"'P{n}D'" in clean.upper()
                or re.search(rf"\b{n}\b", clean)):
            return False, f"{n}-day window missing"
        return True, ""
    for v in lits:
        if f"'{v}'" not in clean:
            return False, f"literal {v!r} missing"
    for n in g.get("numbers") or []:
        if not re.search(_num_pat(n), clean):
            return False, f"number {n} missing"
    if shape == "not_in" and not re.search(r"\bNOT\b|<>|!=", up):
        return False, "negation missing"
    if shape == "derived":
        kind = g.get("derived_kind")
        if kind == "ratio" and "/" not in clean:
            return False, "division missing"
        if kind == "time" and g.get("divisor", 1) != 1 and not re.search(_num_pat(g["divisor"]), clean):
            return False, f"unit conversion by {g['divisor']} missing"
    return True, ""


def g6_instructions(sql: str, question: str, shown_tables: list[dict]):
    """Deterministic instruction compliance (plan P0-7 / G6).

    Whether an instruction BINDS this query, and whether the query obeys it, is
    decided from the SQL and question -- not from what a teacher claims. Only
    tables whose notes were shown, and that the query actually reads, count.
    Returns (ok, reasons, required_predicates, forbidden_constructs, n_changing)
    where n_changing counts obeyed instructions that alter the naive answer.
    """
    try:
        tree = sp.parse(sql)
    except Exception as exc:  # noqa: BLE001
        return False, [f"G6 parse failed: {exc}"], [], [], 0
    used = set(sp.table_aliases(tree).values())
    aggs = sp.aggregations(tree)
    clean = re.sub(r"\s+", " ", sql.replace('"', ""))
    q = question.lower()
    reasons, req, forb, changing = [], [], [], 0
    for d in shown_tables:
        if d["datasource"] not in used:
            continue
        for ins in d.get("instructions") or []:
            kind, col = ins["kind"], ins["binding_column"]
            if kind == "nullable_metric":
                if any(col in cols for f, cols, _, _ in aggs if f in ("SUM", "AVG", "MIN", "MAX", "APPROX_QUANTILE_DS")):
                    req.append(f"{col} IS NOT NULL")
                    if re.search(rf"\b{re.escape(col)}\s+IS\s+NOT\s+NULL\b", clean, re.I):
                        changing += 1
                    else:
                        reasons.append(f"G6 aggregating {col} requires {col} IS NOT NULL")
            elif kind == "unique_by":
                if re.search(rf"COUNT\s*\(\s*DISTINCT\s+{re.escape(col)}\b", clean, re.I):
                    forb.append(f"COUNT(DISTINCT {col})")
                    reasons.append(f"G6 records are unique per {col}; DISTINCT is forbidden")
            elif kind == "exact_distinct":
                if re.search(rf"APPROX_COUNT_DISTINCT\s*\(\s*{re.escape(col)}\b", clean, re.I):
                    forb.append(f"APPROX_COUNT_DISTINCT({col})")
                    reasons.append(f"G6 {col} needs an exact COUNT(DISTINCT); approximate is forbidden")
                elif re.search(rf"COUNT\s*\(\s*DISTINCT\s+{re.escape(col)}\b", clean, re.I):
                    req.append(f"COUNT(DISTINCT {col})")
                    changing += 1
            elif kind == "exclude_flag":
                req.append(f"{col} = 0")
                if re.search(rf"\b{re.escape(col)}\s*(=\s*0|<>\s*1|!=\s*1)", clean, re.I):
                    changing += 1
                else:
                    reasons.append(f"G6 table rule: rows must be filtered to {col} = 0")
            elif kind == "duration_end":
                if re.search(rf"\b{re.escape(col)}\b", clean):
                    req.append(f"{col} IS NOT NULL")
                    if re.search(rf"\b{re.escape(col)}\s+IS\s+NOT\s+NULL\b", clean, re.I):
                        changing += 1
                    else:
                        reasons.append(f"G6 duration rows must have {col} IS NOT NULL")
        for g in d.get("glossary") or []:
            if re.search(rf"\b{re.escape(g['term'].lower())}(?:e?s)?(?:'s)?(?!\w)", q):
                req.append(g["predicate"])
                ok_g, why = glossary_applied(g, clean)
                if ok_g:
                    changing += 1
                else:
                    reasons.append(f"G6 glossary term {g['term']!r} means {g['predicate']} ({why})")
    return (not reasons), reasons, req, forb, changing



# ------------------------------------------------------------------------ G10
# Addendum F6: a gold SQL must not assume the unit of a BIGINT time column that the prompt never states.
_UNIT_WORD = re.compile(r"\b(epoch|unix|milli(?:second)?s?|micro(?:second)?s?|seconds?|secs?|ms|minutes?|mins?|hours?|hrs?|days?)\b", re.I)
_UNIT_NAME = re.compile(r"(_ms|_millis|_msec|_s|_sec|_secs|_seconds|_epoch|_epoch_s|_epoch_ms|_min|_mins|_minutes|_hrs|_hours|_days)$"
                        r"|(Ms|Millis|Msec|Sec|Secs|Seconds|Epoch|EpochS|EpochMs|Min|Mins|Minutes|Hrs|Hours|Days)$")
_UNIT_CONSTS = {60, 1000, 3600, 60000, 3600000, 86400, 86400000, 1000000}


def _epochish_columns(d: dict) -> set[str]:
    """BIGINT columns that carry a time, not a measure: long, and not a metric, dimension, id or flag."""
    roles = d["roles"]
    measure = {m["name"] for m in roles.get("metrics", [])}
    other = set(roles.get("dims", [])) | set(roles.get("hi_card", [])) | set(roles.get("flags", []) or [])
    out = {c[0] for c in d["columns"] if c[1] == "long" and c[0] not in measure and c[0] not in other and c[0] != d["time_col"]}
    return out | (set(roles.get("epoch_cols") or []) - {d["time_col"]})


def _unit_dependent_columns(tree) -> set[str]:
    """Columns the SQL uses in a way that depends on their unit: converted to a timestamp, scaled by a
    time constant, subtracted from another column, or compared with an epoch-sized literal."""
    from sqlglot import exp

    def cols_in(node) -> set[str]:
        return {c.name for c in node.find_all(exp.Column)}

    used: set[str] = set()
    for f in tree.find_all(exp.Anonymous):
        if f.name.upper() == "MILLIS_TO_TIMESTAMP":
            used |= cols_in(f)
    for node in tree.find_all((exp.Div, exp.Mul)):
        consts = {float(l.name) for l in node.find_all(exp.Literal) if not l.is_string and re.fullmatch(r"[\d.]+", l.name)}
        if consts & _UNIT_CONSTS:
            used |= cols_in(node)
    for node in tree.find_all(exp.Sub):
        if len(cols_in(node)) >= 2:
            used |= cols_in(node)
    for cmp_ in tree.find_all((exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
        lits = [l for l in cmp_.find_all(exp.Literal) if not l.is_string and re.fullmatch(r"\d+", l.name) and float(l.name) >= 1e8]
        if lits:
            used |= cols_in(cmp_)
    return used


def epoch_arith_columns(sql: str, table_ids: list[str], index: dict) -> set[str]:
    """Epoch-type columns the SQL uses in duration / epoch arithmetic (addendum F5 keeps their nullability)."""
    try:
        tree = sp.parse(sql)
    except Exception:  # noqa: BLE001
        return set()
    epoch = set().union(*(_epochish_columns(index[t]) for t in table_ids)) if table_ids else set()
    return _unit_dependent_columns(tree) & epoch


def g10_epoch_unit(sql: str, prompt_text: str, schema_ids: list[str], index: dict):
    """Rows whose gold SQL converts or subtracts an epoch column without the prompt giving its unit.

    Evidence of a unit, anywhere the model can see: the column's name (`_ms`, `Secs`), the text right
    after the column's name in the prompt (its description, or a glossary definition that says "epoch
    seconds"), or sample rows (whose magnitude shows it)."""
    from sqlglot import exp
    try:
        tree = sp.parse(sql)
    except Exception:  # noqa: BLE001 -- G0/G2 report unparseable SQL
        return True, []
    epoch: dict[str, str] = {}
    for sid in schema_ids:
        for c in _epochish_columns(index[sid]):
            epoch[c] = sid
    if not epoch:
        return True, []

    used = _unit_dependent_columns(tree)
    reasons = []
    has_samples = "Sample rows:" in prompt_text
    for c in sorted(used & set(epoch)):
        if _UNIT_NAME.search(c) or has_samples:
            continue
        seen = False
        for m in re.finditer(re.escape(c), prompt_text):
            if _UNIT_WORD.search(prompt_text[m.end(): m.end() + 170]):
                seen = True
                break
        if not seen:
            reasons.append(f"G10 the SQL assumes a unit for epoch column {c!r} that the prompt never states")
    return (not reasons), reasons


# ------------------------------------------------------------------------- G0
def g0_style(sql: str):
    """Appendix A output style: one statement, no fences, no trailing semicolon,
    double-quoted output aliases, GROUP BY / ORDER BY by ordinal."""
    import sqlglot
    from sqlglot import exp
    reasons = []
    if "```" in sql:
        reasons.append("G0 markdown fence in the SQL")
    if sql.strip().endswith(";"):
        reasons.append("G0 trailing semicolon")
    try:
        stmts = [t for t in sqlglot.parse(sql, read="druid") if t is not None]
    except Exception as exc:  # noqa: BLE001
        return False, [f"G0 parse failed: {exc}"]
    if len(stmts) != 1:
        return False, reasons + [f"G0 expected exactly one statement, found {len(stmts)}"]
    tree = stmts[0]
    for a in tree.find_all(exp.Alias):
        ident = a.args.get("alias")
        if isinstance(ident, exp.Identifier) and not ident.args.get("quoted"):
            reasons.append(f"G0 output alias {ident.name!r} is not double-quoted")
    def is_ordinal(e):
        return isinstance(e, exp.Literal) and not e.is_string
    for g in tree.find_all(exp.Group):
        for e in g.expressions:
            if isinstance(e, (exp.GroupingSets, exp.Rollup, exp.Cube)):
                continue
            if not is_ordinal(e):
                reasons.append("G0 GROUP BY must use ordinals")
                break
    for o in tree.find_all(exp.Order):
        if isinstance(o.parent, exp.Window):
            continue
        for e in o.expressions:
            key = e.this if isinstance(e, exp.Ordered) else e
            if not (is_ordinal(key) or (isinstance(key, exp.Column) and key.name == "__time")):
                reasons.append("G0 ORDER BY must use ordinals")
                break
    return (not reasons), sorted(set(reasons))


# ------------------------------------------------------- feature detection
_FEATURE_PATTERNS = {
    "time_bucket": r"\bTIME_FLOOR\s*\(\s*\"?__time|\bTIME_CEIL\b|\bDATE_TRUNC\b|\bFLOOR\s*\(\s*\"?__time\"?\s+TO\b",
    "rolling_window": r"CURRENT_TIMESTAMP\s*-\s*INTERVAL",
    "calendar_window": r"TIME_FLOOR\s*\(\s*CURRENT_TIMESTAMP\s*,\s*'P1[WMY]'|TIME_FLOOR\s*\(\s*CURRENT_TIMESTAMP\s*,\s*'P3M'|TIME_FLOOR\s*\(\s*CURRENT_TIMESTAMP\s*,\s*'P1D'",
    "time_shift": r"\bTIME_SHIFT\b",
    "time_extract_format": r"\bTIME_EXTRACT\b|\bTIME_FORMAT\b|\bEXTRACT\s*\(",
    "epoch_convert": r"\bMILLIS_TO_TIMESTAMP\b",
    "string_time_parse": r"\bTIME_PARSE\b",
    "json_value": r"\bPARSE_JSON\b|\bTRY_PARSE_JSON\b|\bJSON_VALUE\b",
    "mvd": r"\bMV_[A-Z_]+\b|\bMV_TO_ARRAY\b|\bUNNEST\b",
    "lookup": r"\bLOOKUP\s*\(",
    "approx": r"\bAPPROX_[A-Z_]+\b",
    "latest_earliest": r"\b(LATEST|EARLIEST|LATEST_BY|EARLIEST_BY)\s*\(",
    "filtered_agg": r"\)\s*FILTER\s*\(\s*WHERE",
    "join": r"\bJOIN\b",
    "case": r"\bCASE\s+WHEN\b",
    "null_handling": r"\bCOALESCE\b|\bNULLIF\b|\bIS\s+(NOT\s+)?NULL\b",
    "having": r"\bHAVING\b",
    "cte": r"^\s*WITH\b",
    "window": r"\bOVER\s*\(",
    "exact_distinct": r"COUNT\s*\(\s*DISTINCT\b",
    "string_ops": r"\bLIKE\b|\bREGEXP_LIKE\b|\bUPPER\s*\(|\bLOWER\s*\(|\bSUBSTRING\s*\(|\bCONCAT\s*\(",
}
# Families that cannot be detected reliably from text; never enforced or reported.
UNDETECTABLE = {"order_by_rule", "reserved_identifier", "ratio", "top_n_per_group", "multi_metric", "subquery"}


def detect_features(sql: str, roles_by_table: list[dict] | None = None) -> set[str]:
    """Feature families actually present in a SQL string (not what a teacher claims)."""
    flat = re.sub(r"\s+", " ", sql)
    found = {f for f, pat in _FEATURE_PATTERNS.items() if re.search(pat, flat, re.I)}
    # `TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, P), P, -1)` is how "last month/week/..." is written; it is the
    # calendar window, not a separate period-over-period comparison, so it must not count as `time_shift` too.
    without_last_period = re.sub(
        r"TIME_SHIFT\s*\(\s*TIME_FLOOR\s*\(\s*CURRENT_TIMESTAMP\s*,\s*'P\d+[WMYD]'\s*\)\s*,\s*'P\d+[WMYD]'\s*,\s*-1\s*\)",
        "", flat.replace('"', ""), flags=re.I)
    if "time_shift" in found and not re.search(r"\bTIME_SHIFT\b", without_last_period, re.I):
        found.discard("time_shift")
    up = flat.upper()
    if up.count("SELECT") > 1 and "cte" not in found:
        found.add("subquery")
    if re.search(r"\bSELECT\b.*\bSELECT\b", up) and "WITH" in up[:6]:
        found.add("subquery")
    for roles in roles_by_table or []:
        for start, end, _ in roles.get("epoch_pairs") or []:
            if re.search(rf"\b{re.escape(end)}\b\s*-\s*\(?\b{re.escape(start)}\b|\b{re.escape(start)}\b\s*-\s*\(?\b{re.escape(end)}\b",
                         flat.replace('"', "")):
                found.add("epoch_arith")
    if "TIME_FLOOR(CURRENT_TIMESTAMP" not in up.replace('"', "").replace(" ", ""):
        found.discard("calendar_window")
    return found


# ------------------------------------------------------------------------- G7
def g7_style_truth(question: str, sql_answer_columns: list[str], style: str):
    q_lower = question.lower()
    named = [c for c in sql_answer_columns
            if re.search(rf"\b{re.escape(c.lower())}\b", q_lower)]
    reasons = []
    if style in ("business", "vocab_gap") and named:
        reasons.append(f"G7 {style!r} question names column(s) {named!r} verbatim")
    if style == "exact_column" and not named:
        reasons.append("G7 'exact_column' question names no answer column verbatim")
    return (not reasons), reasons


# ------------------------------------------------------------------------- G8
_PHRASE_PATTERNS = [
    (re.compile(r"\byesterday\b", re.I), ["TIME_FLOOR", "INTERVAL '1' DAY"]),
    (re.compile(r"\btoday\b", re.I), ["TIME_FLOOR"]),
    (re.compile(r"\blast (week|month|quarter|year)\b", re.I), ["TIME_SHIFT", "TIME_FLOOR"]),
    (re.compile(r"\bthis (week|month|quarter|year)( to date)?\b", re.I), ["TIME_FLOOR"]),
    (re.compile(r"\b(past|last) \d+ (day|hour|minute)s?\b", re.I), ["CURRENT_TIMESTAMP", "INTERVAL"]),
]


def g8_time_phrase(question: str, sql: str):
    reasons = []
    sql_u = sql.upper()
    for pat, must_have in _PHRASE_PATTERNS:
        if pat.search(question):
            if not all(tok in sql_u for tok in must_have):
                reasons.append(f"G8 phrase {pat.pattern!r} matched in question but SQL "
                               f"lacks {must_have}")
    return (not reasons), reasons


# ------------------------------------------------------------------------- G9
def g9_semantic_judge(schema_prompt: str, question: str, sql: str):
    try:
        result = teacher.semantic_judge(schema_prompt, question, sql)
    except teacher.QuotaError:
        raise
    except teacher.TeacherError as exc:
        return False, [f"G9 judge call failed: {exc}"]
    if not result.get("answers_question"):
        return False, [f"G9 {result.get('reason', 'judge says no')}"]
    return True, []
