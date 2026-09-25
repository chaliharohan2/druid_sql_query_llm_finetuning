#!/usr/bin/env python3
"""v2 generation loop (druid_sql_dataset_v2_plan.md, Section 5).

sample -> teacher writes question+SQL -> gates G1-G9 -> dedupe/diversity cap
-> jsonl + stats report.

Resumable and parallel. Every kept example is appended to `<out>.jsonl` and
every rejection to `<out>_rejects.jsonl` the moment it is decided, and each
attempt draws from its own RNG (`seed:attempt`), so re-running the same
command after an interruption continues where it stopped and never repeats or
skips an attempt.

Usage:
  generate_v2.py --n 200 --out pilot          # a pilot batch
  generate_v2.py --n 9000 --out train_v2      # the full candidate pool
  generate_v2.py --out pilot --report-only    # rebuild the report from the files

Not AI training or inference code: this only generates and validates
training *data*; `lora_training.py` / `inference.py` are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timedelta
import json
import math
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import notes_v3  # noqa: E402
import prompt_formats as pf  # noqa: E402
from addendum_lib import SQLISH, nullable_override  # noqa: E402
import sampler  # noqa: E402
import teacher  # noqa: E402
import validators as V  # noqa: E402
from harness.client import DruidClient  # noqa: E402

_KEEP_UPPER = re.compile(
    r"\b(SELECT|FROM|WHERE|GROUP|BY|ORDER|HAVING|AND|OR|NOT|IN|IS|NULL|AS|ON|JOIN|INNER|LEFT|"
    r"LIMIT|DESC|ASC|CASE|WHEN|THEN|ELSE|END|DISTINCT|BETWEEN|LIKE|FILTER|INTERVAL|DAY|HOUR|"
    r"MINUTE|WEEK|MONTH|YEAR|SUM|AVG|MIN|MAX|COUNT|TIME_FLOOR|TIME_SHIFT|TIME_EXTRACT|"
    r"TIME_FORMAT|TIME_PARSE|MILLIS_TO_TIMESTAMP|CURRENT_TIMESTAMP|APPROX_COUNT_DISTINCT|"
    r"APPROX_QUANTILE_DS|TIMESTAMPDIFF|CAST|COALESCE|NULLIF|WITH|UNION|EXISTS)\b", re.I)
_QUOTED = re.compile(r"'([^']{1,60})'")
_TOKENIZER_PATH = ROOT.parent / "models" / "qwen_3_5_2B_lora" / "checkpoint-1500" / "tokenizer.json"
_tok = None


def count_tokens(text: str) -> int:
    """Qwen token count (the plan's P1-1 buckets are defined on the Qwen tokenizer)."""
    global _tok
    if _tok is None:
        try:
            from tokenizers import Tokenizer
            _tok = Tokenizer.from_file(str(_TOKENIZER_PATH))
        except Exception:  # noqa: BLE001 -- fall back to the chars/4 estimate
            _tok = False
    return len(_tok.encode(text).ids) if _tok else len(text) // 4


def skeleton_hash(sql: str) -> str:
    """Mask literals, numbers and identifiers; keep SQL/Druid keywords."""
    s = re.sub(r"'[^']*'", "S", sql)
    s = re.sub(r"\b\d+\.?\d*\b", "N", s)

    def repl(m: re.Match) -> str:
        word = m.group(0)
        return word.upper() if _KEEP_UPPER.fullmatch(word) else "ID"

    s = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", repl, s)
    s = re.sub(r"\s+", " ", s).strip()
    return hashlib.sha1(s.encode()).hexdigest()


def build_brief(sample: dict, index: dict, schema_prompt: str = "") -> str:
    target = index[sample["target_table"]]
    style_txt = sampler.STYLE_BRIEF[sample["question_style"]]
    parts = [
        f"Target table for the answer: `{target['datasource']}` (you may also reference "
        f"other tables shown if the question genuinely needs a join; do not use a table "
        f"just because it is present -- most prompts here include at least one table the "
        f"query should NOT touch).",
        f"Question style: {sample['question_style']}. {style_txt}",
        f"Time scope: {sampler.TIME_SCOPE_BRIEF[sample['time_scope']]}",
        f"Complexity: {sampler.COMPLEXITY_BRIEF[sample['complexity']]}",
    ]
    if sample["feature_families"]:
        parts.append(f"Required Druid feature families in the answer SQL: {', '.join(sample['feature_families'])}.")
    else:
        parts.append("Use plain SQL for the core question; do not add Druid-specific functions unless the "
                     "question needs them.")
    if sample["sql_features"]:
        parts.append(f"Also use these general SQL features if they fit naturally: "
                     f"{', '.join(sample['sql_features'])}.")
    sem = sampler.column_semantics(target)
    if sem:
        parts.append("Column notes for the target table (for you only -- never quote these in the "
                     "question). Use best/worst/slowest/fastest wording only for a measure whose "
                     "direction is stated; a measure with no good/bad direction never gets it:\n" + sem)
    hints = sampler.value_hints(target, schema_prompt)
    meta = json.loads((ROOT / "dataset_meta.json").read_text())
    today = meta["anchor"][:10]
    first = (datetime.fromisoformat(meta["anchor"]) - timedelta(days=int(meta["span_days"]) - 10)).strftime("%Y-%m-%d")
    parts.append(f"Data facts (for you only -- never quote or reveal them): today is {today}. The data runs from "
                 f"about {first} to today, so any explicit date range must fall inside it. Each table holds only "
                 f"about 2,400 rows in total (a handful per day), so any threshold in a HAVING or WHERE must be "
                 f"small (2 to 20, never hundreds or thousands), and a filter value must be one that exists.\n" + hints)
    if sample["wrong_table_trap"]:
        parts.append("Trap to include: pick a concept whose natural column name also exists in another "
                     "table shown but NOT in the target table under that name, so the answer must use "
                     "the target table's own differently-named column (or a correct join).")
    if "epoch_arith" in sample["feature_families"]:
        parts.append("For epoch_arith, ask a duration question (e.g. average handling time) and compute it "
                     "as plain arithmetic on the two epoch columns, converting units to what the "
                     "question asks for, with both columns IS NOT NULL.")
    if sample["show_notes"]:
        if sample.get("notes_must_apply"):
            parts.append("The tables below carry rules and glossary terms. Write a question on which at "
                         "least one rule or glossary term BINDS -- the correct SQL must differ from what a "
                         "naive query would do -- and obey it exactly. Using a glossary term means saying "
                         "the term in the question and applying its definition in the SQL.")
        else:
            parts.append("The tables below carry rules and glossary terms; obey any that bind your query.")
    parts.append("Write the question in plain business English: no backticks, no SQL keywords or function names "
                 "(COUNT, SUM, TIME_FLOOR, ...), no quoted column syntax.")
    if sample.get("defn"):
        parts.append(defn_brief(sample, index))
    if sample.get("decoy"):
        parts.append(decoy_brief(sample, index))
    return "\n".join(parts)


def _defn_entry(sample: dict, index: dict) -> dict:
    return next(e for e in index[sample["target_table"]]["glossary"] if e["term"] == sample["defn"]["term"])


def defn_brief(sample: dict, index: dict) -> str:
    """F3: the writer must build the question around one glossary term and apply its definition exactly."""
    e = _defn_entry(sample, index)
    how = {
        "value_list": "the definition is a filter on the listed value(s)",
        "or_cols": "the definition is an OR across two columns -- keep BOTH branches, inside parentheses if combined with other conditions",
        "not_in": "the definition is a negation (NOT IN / <>) -- apply it exactly, not its complement",
        "like_prefix": "the definition is a prefix match on a string column -- use LIKE 'prefix%'",
        "threshold": "the definition is a numeric threshold -- use exactly that comparison and number",
        "mixed": "the definition mixes AND and OR -- keep the parentheses exactly as in the definition",
        "time_active": f"the term is an ENTITY defined by recent activity: count or list distinct {e.get('entity_col')} restricted to "
                       f"rows with __time in the last {e.get('window_days')} days (rolling window from CURRENT_TIMESTAMP)",
        "derived": "the term is a computed METRIC: compute it with the definition's formula (units and divisors as stated), "
                   "aggregate it the way the question asks, and never treat it as a stored column",
    }[e["shape"]]
    extra = ""
    if sample["defn"]["shape"] == "multi_term":
        extra = (" The prompt's glossary lists several terms; use ONLY this one, and do not apply or mention the others.")
    return (f"Glossary assignment: the notes above define the business term \"{e['term']}\". Write a natural analyst question that "
            f"uses this exact term (plural or possessive is fine) the way a colleague would say it -- do NOT restate or paraphrase "
            f"its definition in the question -- and whose SQL applies the definition. Canonical logic (for you only): "
            f"{e['predicate']}; {how}.{extra} No other glossary term may appear in the question.")


def decoy_brief(sample: dict, index: dict) -> str:
    """F4: a word that looks like a glossary term but defines nothing."""
    dc = sample["decoy"]
    if dc["mode"] == "real_column":
        ref = (f"the real column `{dc['col']}`" + (f" with its real value '{dc['value']}'" if dc["value"] else ""))
        how = (f"In this question the word \"{dc['word']}\" must refer to {ref}: e.g. \"{dc['word']} <things>\" means "
               f"{dc['col']} = '{dc['value'] or '<a real value>'}'. The SQL filters that column with that literal value "
               f"(state the value in the question so it can be recovered)")
    else:
        how = (f"The question must use the word \"{dc['word']}\" purely "
               f"{sampler.DECOY_WORD_HINT[dc['word']]}: it must not change which rows or columns the SQL uses, and the SQL "
               f"must not contain the word")
    return (f"Decoy assignment: {how}. There is NO glossary definition for \"{dc['word']}\". If the notes above define other "
            f"terms, ignore them: the question must not use any of them and the SQL must not apply any of their definitions.")


def render_schema_prompt(sample: dict, index: dict, seeds: dict) -> tuple[str, str]:
    """Reference schema text for the teacher/judge (rendered with an empty question)."""
    fmt = sample["format"]
    turns = pf.render(fmt, index, sample["table_ids"], "", seeds=seeds,
                      notes_for=sample["notes_for"], overview_for=sample["overview_for"],
                      notes_style=sample.get("notes_style"))
    return "\n\n".join(t for _, t in turns), fmt


def note_literals(sample: dict, index: dict) -> set[str]:
    """Literals a shown instruction/glossary entry legitimately injects into the SQL (G3)."""
    out: set[str] = set()
    style = sample.get("notes_style")
    for t in sample["notes_for"]:
        d = index[t]
        entries = style.picked.get(t) if style is not None and t in style.picked else d.get("glossary") or []
        for g in entries:
            out.update(str(v) for v in g.get("values") or [])
            out.update(_QUOTED.findall(g.get("predicate", "")))
        for i in d.get("instructions") or []:
            out.update(_QUOTED.findall(i.get("required_predicate") or ""))
    return out


def shown_tables(sample: dict, index: dict) -> list[dict]:
    """The tables whose rules were shown, with the glossary reduced to the entries actually printed."""
    style = sample.get("notes_style")
    out = []
    for t in sample["notes_for"]:
        d = dict(index[t])
        if style is not None and t in style.picked:
            d["glossary"] = style.picked[t]
        out.append(d)
    return out


def term_in(term: str, text: str) -> bool:
    return bool(re.search(rf"\b{re.escape(term.lower())}(?:e?s)?(?:'s)?(?!\w)", text.lower()))


def check_addendum_spec(cand: dict, index: dict) -> list[str]:
    """F3 / F4: the assigned term is used and applied; decoys use their word and apply no glossary filter."""
    sample, question, sql = cand["sample"], cand["question"], cand["sql"]
    fails = []
    clean = re.sub(r"\s+", " ", sql.replace('"', ""))
    if sample.get("defn"):
        term = sample["defn"]["term"]
        if not term_in(term, question):
            fails.append(f"SPEC the question does not use the assigned glossary term {term!r}")
        others = [e["term"] for es in sample["notes_style"].picked.values() for e in es if e["term"] != term]
        if any(term_in(o, question) for o in others):
            fails.append("SPEC the question also uses a second glossary term")
    if sample.get("decoy"):
        dc = sample["decoy"]
        if not re.search(rf"\b{dc['word']}\b", question, re.I):
            fails.append(f"SPEC the decoy word {dc['word']!r} is not in the question")
        for es in sample["notes_style"].picked.values():
            for e in es:
                if term_in(e["term"], question):
                    fails.append(f"SPEC the question uses glossary term {e['term']!r}")
                elif V.glossary_applied(e, clean)[0]:
                    fails.append(f"DECOY the SQL applies glossary term {e['term']!r} that the question never used")
        if dc["mode"] == "real_column":
            if not re.search(rf"\b{re.escape(dc['col'])}\b", clean) or (dc["value"] and f"'{dc['value']}'" not in clean):
                fails.append("SPEC the real-column decoy does not filter its column")
        elif dc["word"] in clean.lower():
            fails.append("SPEC an ordinary-English decoy word must not appear in the SQL (aliases included)")
    return fails


def leaky_columns(question: str, index: dict, table_ids: list[str]) -> list[str]:
    """Multi-word (snake_case / camelCase) column names appearing verbatim in the question."""
    q = question.lower()
    out = []
    for sid in table_ids:
        for name, _, _ in index[sid]["columns"]:
            multiword = "_" in name or re.search(r"[a-z][A-Z]", name)
            if multiword and re.search(rf"(?<![a-z0-9_]){re.escape(name.lower())}(?![a-z0-9_])", q):
                out.append(name)
    return out


_ENFORCED_FAMILIES = set(V._FEATURE_PATTERNS) | {"epoch_arith"}


_TIME_PRED = re.compile(r"__time\"?\s*(>=|<=|>|<|BETWEEN)", re.I)


def check_time_scope(scope: str | None, sql: str) -> list[str]:
    """The SQL's time window must be the kind the brief asked for (Appendix A shapes)."""
    if not scope:
        return []
    flat = re.sub(r"\s+", " ", sql.replace('"', ""))
    up = flat.upper()
    ok = {
        "none": not _TIME_PRED.search(flat) and "CURRENT_TIMESTAMP" not in up,
        "rolling": bool(re.search(r"CURRENT_TIMESTAMP\s*-\s*INTERVAL", up)),
        "definition": bool(re.search(r"CURRENT_TIMESTAMP\s*-\s*INTERVAL|TIME_SHIFT\(CURRENT_TIMESTAMP", up)),
        "day": bool(re.search(r"TIME_FLOOR\(CURRENT_TIMESTAMP,\s*'P1D'\)", up)),
        "this_period": bool(re.search(r"__TIME\s*>=\s*TIME_FLOOR\(CURRENT_TIMESTAMP,\s*'(P1W|P1M|P3M|P1Y)'\)", up)),
        "last_period": bool(re.search(r"TIME_SHIFT\(TIME_FLOOR\(CURRENT_TIMESTAMP,\s*'(P1W|P1M|P3M|P1Y)'\),\s*'(P1W|P1M|P3M|P1Y)',\s*-1\)", up)),
        "explicit_range": bool(re.search(r"TIMESTAMP '20\d\d-\d\d-\d\d", up)),
    }[scope]
    return [] if ok else [f"SPEC time scope {scope!r} not honoured by the SQL"]


def run_cheap_gates(cand: dict, index: dict) -> list[str]:
    fails = []
    sample, sql, question = cand["sample"], cand["sql"], cand["question"]
    _, reasons = V.g0_style(sql)
    fails += reasons
    _, reasons = V.g2_grounded(sql, sample["table_ids"], index)
    fails += reasons
    _, reasons = V.g3_literal_fidelity(question, sql, allowed_extra=note_literals(sample, index),
                                       prompt_text=cand["schema_prompt"],
                                       value_lists=sampler.visible_value_lists(cand["schema_prompt"], sample["table_ids"], index))
    fails += reasons
    _, reasons = V.g5_role_compliance(sql, sample["table_ids"], index,
                                      allow_sum_total="total" in question.lower()
                                      or "sum" in question.lower())
    fails += reasons
    shown = shown_tables(sample, index)
    _, reasons, req, forb, changing = V.g6_instructions(sql, question, shown)
    fails += reasons
    cand["required_predicates"], cand["forbidden_constructs"], cand["instructions_changing"] = req, forb, changing
    if sample.get("notes_must_apply") and shown and changing == 0:
        fails.append("G6 no shown instruction or glossary term changed the query")
    cols = [c.rsplit(".", 1)[-1] for c in cand.get("required_columns", [])]
    cols += leaky_columns(question, index, [sample["target_table"]])
    _, reasons = V.g7_style_truth(question, sorted(set(cols)), sample["question_style"])
    fails += reasons
    _, reasons = V.g8_time_phrase(question, sql)
    fails += reasons
    _, reasons = V.g10_epoch_unit(sql, cand["schema_prompt"], sample["table_ids"], index)
    fails += reasons
    fails += check_addendum_spec(cand, index)
    if SQLISH.search(question):  # addendum F6: an analyst never types backticks or SQL syntax
        fails.append("SPEC the question contains backticks or SQL syntax")
    detected = V.detect_features(sql, [index[t]["roles"] for t in sample["table_ids"]])
    cand["detected"] = detected
    fails += check_time_scope(sample.get("time_scope"), sql)
    cap = sampler.WORD_CAPS.get(sample.get("complexity"), 60)
    if sample["question_style"] == "terse":
        cap = min(cap, sampler.TERSE_WORD_CAP)
    n_words = len(question.split())
    if n_words > cap:
        fails.append(f"SPEC question has {n_words} words, over the {cap}-word cap for its complexity/style")
    missing = [f for f in sample["feature_families"] if f in _ENFORCED_FAMILIES and f not in detected]
    if missing:
        fails.append(f"SPEC assigned feature families not in the SQL: {missing}")
    return fails


class Run:
    """Shared state for one generation run (kept/rejected files, counters, lock)."""

    def __init__(self, out: str, skeleton_cap: int):
        self.lock = threading.Lock()
        self.kept_path = ROOT / f"{out}.jsonl"
        self.rej_path = ROOT / f"{out}_rejects.jsonl"
        self.skeleton_cap = skeleton_cap
        self.kept: list[dict] = []
        self.rejects: list[dict] = []
        self.done_attempts: set[int] = set()
        self.skeletons: Counter = Counter()
        self.infra_errors = 0
        self.samples: dict[int, dict] = {}  # attempt -> the sample it drew (attributes logged on rejects)
        self._adapt: dict = {}
        self._adapt_at = -1
        for path, bucket in ((self.kept_path, self.kept), (self.rej_path, self.rejects)):
            if path.exists():
                good, bad = [], 0
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        bucket.append(json.loads(line))
                        good.append(line)
                    except json.JSONDecodeError:  # a run killed mid-write leaves a partial last line
                        bad += 1
                if bad:
                    path.write_text("\n".join(good) + "\n", encoding="utf-8")
                    print(f"  dropped {bad} truncated line(s) from {path.name}", flush=True)
        for r in self.kept:
            self.done_attempts.add(r["meta"]["attempt"])
            self.skeletons[r["meta"]["skeleton_hash"]] += 1
        for r in self.rejects:
            self.done_attempts.add(r["attempt"])

    def addendum_counts(self):
        """(rows kept per F3 shape, uses per term / real-column combination) for the addendum samplers."""
        have: Counter = Counter()
        uses: Counter = Counter()
        for r in self.kept:
            m = r["meta"]
            if m.get("defn_shape"):
                have[m["defn_shape"]] += 1
                uses[m["defn_term"]] += 1
            dc = m.get("decoy")
            if dc and dc.get("mode") == "real_column":
                uses[m["target_tables"][0] + dc["col"]] += 1
        return have, uses

    def add_reject(self, attempt: int, gates: list[str], cand: dict | None, detail: list[str]):
        rec = {"attempt": attempt, "gates": gates, "detail": detail[:6]}
        smp = self.samples.get(attempt)
        if smp:
            rec["attrs"] = {"style": smp["question_style"], "time_scope": smp.get("time_scope"),
                            "complexity": smp.get("complexity"), "format": smp["format"],
                            "unans": bool(smp.get("unans")), "length_bucket": smp.get("length_bucket_planned")}
        if cand:
            rec["question"] = cand.get("question")
            rec["sql"] = cand.get("sql")
            if cand.get("sql_second"):
                rec["sql_second"] = cand["sql_second"]
            rec["style"] = cand["sample"]["question_style"]
            rec["format"] = cand["sample"]["format"]
        with self.lock:
            self.rejects.append(rec)
            with self.rej_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def adapt(self) -> dict:
        """Per-category multipliers = target share / observed keep rate, recomputed every 20 decisions.

        Gates reject some kinds of question far more than others (e.g. business-style questions
        trip G7/G3 more than exact-column ones), so the kept set drifts from the plan's targets
        unless sampling leans toward the categories that lose more. Priors keep early estimates sane."""
        n = len(self.kept) + len(self.rejects)
        if n - self._adapt_at < 20 and self._adapt:
            return self._adapt
        self._adapt_at = n
        att: dict[str, Counter] = {k: Counter() for k in ("style", "time_scope", "complexity", "format", "length_bucket")}
        kept: dict[str, Counter] = {k: Counter() for k in att}
        for r in self.kept:
            m = r["meta"]
            if m.get("unanswerable") or "time_scope" not in m:
                continue
            for k, v in (("style", m["question_style"]), ("time_scope", m["time_scope"]),
                         ("complexity", m["complexity"]), ("format", m["format"]),
                         ("length_bucket", m.get("length_bucket_planned"))):
                att[k][v] += 1
                kept[k][v] += 1
        for r in self.rejects:
            a = r.get("attrs")
            if not a or a.get("unans"):
                continue
            for k in att:
                if a.get(k) is not None:
                    att[k][a[k]] += 1
        out = {}
        for k in att:
            cats = list(att[k])
            if len(cats) < 2:
                continue
            kr = {c: (kept[k][c] + 2) / (att[k][c] + 4) for c in cats}
            mean = sum(kr.values()) / len(kr)
            out[k] = {c: mean / max(kr[c], 0.1) for c in cats}  # relative: a low keep rate -> multiplier > 1
        self._adapt = out
        return out

    def try_keep(self, record: dict, cap_exempt: bool = False) -> bool:
        with self.lock:
            skel = record["meta"]["skeleton_hash"]
            if not cap_exempt and self.skeletons[skel] >= self.skeleton_cap:
                return False
            self.skeletons[skel] += 1
            record["meta"]["id"] = f"v2_{len(self.kept):05d}"
            self.kept.append(record)
            with self.kept_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True


_tls = threading.local()


def client() -> DruidClient:
    if not hasattr(_tls, "c"):
        _tls.c = DruidClient()
    return _tls.c


UNANSWERABLE_KIND_WEIGHTS = [("missing_field", 0.40), ("near_miss", 0.25), ("missing_table", 0.20),
                             ("write_operation", 0.15)]
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|merge|remove|rename|"
                          r"add|set|change|modify|overwrite|purge|clear)\b", re.I)


def cannot_answer_line(reason: str) -> str:
    reason = re.sub(r"\s+", " ", reason).strip().rstrip(".")[:140]
    return f"-- CANNOT_ANSWER: {reason}"


def process_unanswerable(attempt: int, args, run: Run, index: dict, fact_ids: list[str], seeds: dict,
                         rng: random.Random, sample: dict, schema_prompt: str):
    kind = sampler._weighted(rng, UNANSWERABLE_KIND_WEIGHTS)
    sample["feature_families"], sample["sql_features"], sample["unans"] = [], [], True
    try:
        w = teacher.write_unanswerable(schema_prompt, kind)
    except teacher.QuotaError:
        raise
    except teacher.TeacherError as exc:
        run.add_reject(attempt, ["TEACHER"], None, [str(exc)[:300]])
        return
    question, missing = (w.get("question") or "").strip(), (w.get("missing") or "").strip()
    cand = {"sample": sample, "question": question, "sql": cannot_answer_line(f"{missing}"),
            "unanswerable_kind": kind}
    if not question or not missing:
        run.add_reject(attempt, ["UNANS"], cand, ["empty question/missing"])
        return
    if kind == "write_operation":
        m = _WRITE_WORDS.search(question)
        if not m:
            run.add_reject(attempt, ["UNANS"], cand, ["write request has no write verb"])
            return
        cand["sql"] = cannot_answer_line(f"{m.group(1).upper()} requests are not supported; only read-only queries")
    if kind != "write_operation":
        # Two independent confirmations that nothing can answer it: the checker model must say no,
        # and any SQL it volunteers must not be grounded in the real schema.
        try:
            chk = teacher.check_answerable(schema_prompt, question)
        except teacher.QuotaError:
            raise
        except teacher.TeacherError as exc:
            run.add_reject(attempt, ["UNANS_UNAVAILABLE"], cand, [str(exc)[:200]])
            return
        if chk.get("answerable") is not False:
            run.add_reject(attempt, ["UNANS"], cand, [f"checker says answerable: {chk.get('reason')}"])
            return
        alt = (chk.get("sql") or "").strip()
        if alt and alt.lower() != "null" and V.g2_grounded(alt, sample["table_ids"], index)[0]:
            run.add_reject(attempt, ["UNANS"], cand, ["checker produced a grounded SQL anyway"])
            return
    turns = pf.render(sample["format"], index, sample["table_ids"], question,
                      seeds=seeds, notes_for=sample["notes_for"], overview_for=sample["overview_for"])
    messages = [{"role": r, "content": t} for r, t in turns]
    messages.append({"role": "assistant", "content": cand["sql"]})
    target = index[sample["target_table"]]
    record = {"messages": messages, "meta": {
        "id": "", "attempt": attempt, "split": SPLIT_NAME[args.pool], "question": question,
        "domain": target["domain"],
        "target_tables": [sample["target_table"]], "prompt_tables": sample["table_ids"],
        "distractor_relation": sample["distractor_relation"],
        "length_bucket_planned": sample["length_bucket_planned"], "wrong_table_trap": False,
        "format": sample["format"], "question_style": "business", "hint_level": target.get("hint_level"),
        "feature_families": [], "sql_features": [], "skeleton_hash": "unanswerable:" + kind,
        "required_columns": [], "required_predicates": [], "forbidden_constructs": [],
        "unanswerable": True, "unanswerable_kind": kind,
        "prompt_tokens_est": count_tokens("\n".join(m["content"] for m in messages[:-1])),
        "n_columns_target": len(target["columns"]),
        "teacher": w.get("_model", teacher.TEACHER_WRITER), "judge": teacher.TEACHER_JUDGE, "g4_agreement": None,
        "result_fingerprints": [], "exact_column_in_question": False,
        "notes_shown": bool(sample["notes_for"]), "overview_shown": bool(sample["overview_for"] or sample["notes_for"]), "source": "teacher"}}
    run.try_keep(record, cap_exempt=True)


def pool_families(pool: str) -> set[str] | None:
    """Families belonging to a split pool (None = all train families, i.e. everything not held out)."""
    cfg = json.loads((ROOT / "splits.json").read_text())
    held = {f for k in ("val_heldout_domain", "test_heldout_shape", "test_prodlike") for f in cfg[k]}
    if pool == "train":
        return {"__train__"} | held  # sentinel handled in family_ok
    return set(cfg[{"val": "val_heldout_domain", "test_shape": "test_heldout_shape",
                    "prodlike": "test_prodlike"}[pool]])


def family_ok(pool: str, family: str) -> bool:
    fams = pool_families(pool)
    return (family not in fams) if pool == "train" else (family in fams)


SPLIT_NAME = {"train": "train", "val": "val_heldout_domain", "test_shape": "test_heldout_shape",
              "prodlike": "test_prodlike"}


def independent_sql(schema_prompt: str, question: str, sample: dict, index: dict) -> str | None:
    """A second, independent SQL for G4 that actually executes. The judge model is weaker at
    Druid's dialect (it invents MAX_BY, ARGMAX, HOUR()), so a query Druid rejects is repaired with
    the error fed back, twice; if it still fails, the writer model gives a third independent try.
    None means no independent answer could be obtained (the example is dropped, not waived)."""
    for model in (teacher.TEACHER_JUDGE, teacher.TEACHER_WRITER):
        try:
            sql2 = teacher.write_independent_sql(schema_prompt, question, model=model)
            for _ in range(3):
                ok, reasons, _ = V.g1_execute(sql2, client())
                if ok:
                    return sql2
                sql2 = teacher.repair_sql(schema_prompt, question, sql2, reasons[0], model=model)
        except teacher.QuotaError:
            raise
        except teacher.TeacherError:
            continue
    return None


def _process_attempt(attempt: int, args, run: Run, index: dict, fact_ids: list[str], seeds: dict):
    rng = random.Random(f"{args.seed}:{attempt}")
    prodlike = args.pool == "prodlike"
    if args.mode == "standard":
        sample = sampler.sample_candidate(rng, index, all_fact_ids if prodlike else fact_ids,
                                          profile="prodlike" if prodlike else "standard",
                                          adapt=run.adapt() if args.adaptive else None,
                                          target_ids=fact_ids if prodlike else None)
    else:  # addendum F3 (definition shapes) / F4 (decoys)
        have, uses = run.addendum_counts()
        sample = sampler.sample_addendum(rng, index, fact_ids, args.mode, have, uses,
                                         profile="prodlike" if prodlike else "standard",
                                         adapt=run.adapt() if args.adaptive else None,
                                         all_ids=all_fact_ids if prodlike else None)
    # F1/F2: one style per prompt (layout, rule wording, glossary pattern), fixed by the attempt's seed
    sample["notes_style"] = notes_v3.NotesStyle(f"{args.seed}:{attempt}|notes", **(sample.get("notes_style_kwargs") or {}))
    run.samples[attempt] = sample
    schema_prompt, _ = render_schema_prompt(sample, index, seeds)
    # Cap the ACTUAL rendered size before spending a teacher call (verbose formats such as JSON
    # run well past the chars/4 estimate the sampler sizes tables with).
    while count_tokens(schema_prompt) > MAX_RENDERED_TOKENS and len(sample["table_ids"]) > 1:
        partner = (index[sample["target_table"]]["roles"].get("partner") or {}).get("schema")
        droppable = [t for t in sample["table_ids"][1:] if t != partner]
        if not droppable:
            break
        biggest = max(droppable, key=lambda t: len(index[t]["columns"]))
        sample["table_ids"].remove(biggest)
        sample["notes_for"].discard(biggest)
        sample["overview_for"].discard(biggest)
        schema_prompt, _ = render_schema_prompt(sample, index, seeds)
    if rng.random() < args.unanswerable_share:
        return process_unanswerable(attempt, args, run, index, fact_ids, seeds, rng, sample, schema_prompt)
    try:
        chain = ([teacher.FLASH_WRITER] + teacher.WRITER_CHAIN) if rng.random() < args.flash_writer_share else None
        written = teacher.write_example(schema_prompt, build_brief(sample, index, schema_prompt), chain=chain)
    except teacher.QuotaError:
        raise
    except teacher.TeacherError as exc:
        run.add_reject(attempt, ["TEACHER"], None, [str(exc)[:300]])
        return
    question, sql = (written.get("question") or "").strip(), (written.get("sql") or "").strip()
    if not question or not sql:
        run.add_reject(attempt, ["TEACHER"], None, ["empty question/sql"])
        return
    cand = {"sample": sample, "schema_prompt": schema_prompt, "question": question, "sql": sql,
            "teacher_model": written.get("_model", teacher.TEACHER_WRITER),
            "required_columns": written.get("required_columns", []),
            "required_predicates": written.get("required_predicates", []),
            "forbidden_constructs": written.get("forbidden_constructs", []),
            "feature_families": written.get("feature_families", sample["feature_families"]),
            "sql_features": written.get("sql_features", sample["sql_features"])}

    fails = run_cheap_gates(cand, index)
    if fails:
        run.add_reject(attempt, sorted({f.split(" ", 1)[0] for f in fails}), cand, fails)
        return
    ok, reasons, _ = V.g1_execute(sql, client())
    if not ok:
        run.add_reject(attempt, ["G1"], cand, reasons)
        return
    if not args.skip_g9:
        ok, reasons = V.g9_semantic_judge(schema_prompt, question, sql)
        if not ok:
            run.add_reject(attempt, ["G9"], cand, reasons)
            return
    g4_agreement, fingerprints = None, []
    if not args.skip_g4:
        second_sql = independent_sql(schema_prompt, question, sample, index)
        if second_sql is None:
            run.add_reject(attempt, ["G4_UNVERIFIED"], cand, ["no executable independent SQL after repair and fallback"])
            return
        ok, reasons, fingerprints = V.g4_result_agreement(sql, second_sql, sample["table_ids"], index, client())
        if not ok:
            cand["sql_second"] = second_sql
            run.add_reject(attempt, ["G4"], cand, reasons)
            return
        g4_agreement = True

    # Final render: the style is rebuilt with any glossary term whose words the question happens to use left
    # out (it would read as a reference to a definition the SQL does not apply); F5 nullability is applied now
    # that the gold SQL, and so the referenced columns, are known.
    kw = dict(sample.get("notes_style_kwargs") or {})
    exclude = set()
    for t in sample["notes_for"]:
        exclude |= notes_v3.colliding_terms(index[t], [question])
    style = notes_v3.NotesStyle(f"{args.seed}:{attempt}|notes", exclude_terms=exclude, **kw)
    fake = {"meta": {"prompt_tables": sample["table_ids"], "unanswerable": False}, "messages": [{"content": sql}]}
    nb, nstat = (nullable_override(fake, index, random.Random(f"{args.seed}:{attempt}|nullable"), 1.0)
                 if sample["format"] in ("enterprise_pipe", "enterprise_backtick") else (None, {}))
    turns = pf.render(sample["format"], index, sample["table_ids"], question,
                      seeds=seeds, notes_for=sample["notes_for"], overview_for=sample["overview_for"],
                      notes_style=style, nullable_override=nb)
    messages = [{"role": role, "content": text} for role, text in turns]
    messages.append({"role": "assistant", "content": sql})
    target = index[sample["target_table"]]
    all_cols = {c[0] for sid in sample["table_ids"] for c in index[sid]["columns"] if len(c[0]) >= 4}
    q_tokens = set(re.findall(r"[A-Za-z0-9_]+", question))
    record = {
        "messages": messages,
        "meta": {
            "id": "", "attempt": attempt, "split": SPLIT_NAME[args.pool], "question": question,
            "domain": target["domain"],
            "target_tables": [sample["target_table"]],
            "prompt_tables": sample["table_ids"],
            "distractor_relation": sample["distractor_relation"],
            "length_bucket_planned": sample["length_bucket_planned"],
            "wrong_table_trap": sample["wrong_table_trap"],
            "format": sample["format"],
            "question_style": sample["question_style"],
            "time_scope": sample["time_scope"], "complexity": sample["complexity"],
            "hint_level": target.get("hint_level"),
            "feature_families": sorted(cand["detected"] & sampler.DRUID_COUNTED),
            "sql_features": sorted(cand["detected"] & set(sampler.FEATURE_FAMILIES_GENERAL)),
            "assigned_families": sample["feature_families"],
            "skeleton_hash": skeleton_hash(sql),
            "required_columns": cand["required_columns"],
            "required_predicates": cand["required_predicates"],
            "forbidden_constructs": cand["forbidden_constructs"],
            "instructions_changing": cand["instructions_changing"],
            "unanswerable": False,
            "prompt_tokens_est": count_tokens("\n".join(m["content"] for m in messages[:-1])),
            "n_columns_target": len(target["columns"]),
            "teacher": cand["teacher_model"],
            "judge": teacher.TEACHER_JUDGE,
            "g4_agreement": g4_agreement,
            "result_fingerprints": fingerprints,
            "exact_column_in_question": bool(all_cols & q_tokens),
            "notes_shown": bool(sample["notes_for"]), "overview_shown": bool(sample["overview_for"] or sample["notes_for"]),
            "source": "teacher",
            "notes_style": style.meta(),
            "glossary_terms_shown": sorted({e["term"] for es in style.picked.values() for e in es}),
        },
    }
    if nb is not None:
        record["meta"]["nullable_annotations"] = dict(nstat)
    if sample.get("defn"):
        record["meta"]["defn_shape"] = sample["defn"]["shape"]
        record["meta"]["defn_term"] = sample["defn"]["term"]
    if sample.get("decoy"):
        record["meta"]["decoy"] = dict(sample["decoy"], glossary_terms=record["meta"]["glossary_terms_shown"])
    if not run.try_keep(record):
        run.add_reject(attempt, ["DIVERSITY_CAP"], cand, ["skeleton cap reached"])


MAX_RENDERED_TOKENS = 26000
all_fact_ids: list[str] = []  # every usable fact table (set in main); prod-like distractors come from here


def process_attempt(attempt: int, args, run: Run, index: dict, fact_ids: list[str], seeds: dict):
    """One candidate. A Druid outage is not a rejection: the attempt is left unrecorded (so a
    resumed run redoes it) and the worker backs off."""
    try:
        _process_attempt(attempt, args, run, index, fact_ids, seeds)
    except (V.InfraError, teacher.QuotaError) as exc:
        with run.lock:
            run.infra_errors += 1
        print(f"  attempt {attempt}: infrastructure error, not recorded ({str(exc)[:80]})", file=sys.stderr, flush=True)
        time.sleep(30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150, help="examples to keep")
    ap.add_argument("--out", default="pilot", help="output basename (<out>.jsonl, <out>_rejects.jsonl)")
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-attempts-mult", type=float, default=3.0)
    ap.add_argument("--skip-g9", action="store_true", help="skip the semantic-judge LLM call")
    ap.add_argument("--skip-g4", action="store_true", help="skip the result-agreement gate")
    ap.add_argument("--pool", choices=["train", "val", "test_shape", "prodlike"], default="train",
                    help="which split's domain families to draw tables from (splits.json)")
    ap.add_argument("--flash-writer-share", type=float, default=0.0,
                    help="fraction of attempts whose writer is the flash-tier model (quota relief); recorded in meta.teacher")
    ap.add_argument("--unanswerable-share", type=float, default=0.04)
    ap.add_argument("--no-adaptive", dest="adaptive", action="store_false",
                    help="draw categories at their nominal shares instead of correcting for gate selection bias")
    ap.add_argument("--budget-usd", type=float, default=0.0,
                    help="stop gracefully once the estimated API spend (all sessions of this --out) reaches this")
    ap.add_argument("--mode", choices=["standard", "defn", "decoy"], default="standard",
                    help="standard = the plan's mix; defn = addendum F3 (glossary definition shapes); decoy = addendum F4")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    run = Run(args.out, skeleton_cap=max(3, math.ceil(args.n * 0.005)))
    if args.report_only:
        write_report(args.out, run, 0.0)
        return 0

    index = json.loads((ROOT / "schema_index.json").read_text())
    seeds = pf.load_seeds()
    c = DruidClient()
    if not c.health():
        print("Druid is not up. Run `make up` in druid-harness/.", file=sys.stderr)
        return 1
    loaded = {r["TABLE_NAME"] for r in c.sql_rows(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'druid'")}
    import load_all  # noqa: E402
    state = json.loads(load_all.STATE.read_text()) if load_all.STATE.exists() else {}

    def fresh(name: str) -> bool:
        """Loaded AND loaded from the current spec/seed (Druid may still hold stale older data)."""
        spec = ROOT / "specs" / f"{name}.json"
        return name in loaded and state.get(name) == load_all._fingerprint(spec)

    def usable(d: dict) -> bool:
        variants = d.get("g4_variants") or [d["datasource"]]
        need = variants + [p[0] for p in d.get("partners") or []]
        return all(fresh(v) for v in variants) and all(
            all(fresh(x) for x in index[p[0]].get("g4_variants") or [index[p[0]]["datasource"]])
            for p in d.get("partners") or [])

    all_fact_ids[:] = [s for s, d in index.items() if not d["id"].startswith("dim_") and usable(d)]
    fact_ids = [s for s in all_fact_ids if family_ok(args.pool, index[s]["family"])]
    print(f"{len(fact_ids)} fact schemas fully loaded (all seeds); resuming with "
          f"{len(run.kept)} kept / {len(run.rejects)} rejected", flush=True)

    t0 = time.time()
    usage_path = ROOT / f"{args.out}_usage.json"
    prior = json.loads(usage_path.read_text()) if usage_path.exists() else {}

    def spend() -> float:
        now = {m: dict(c) for m, c in teacher.USAGE.items()}
        merged = {m: {k: prior.get(m, {}).get(k, 0) + now.get(m, {}).get(k, 0)
                      for k in ("prompt_tokens", "output_tokens", "thought_tokens", "calls", "seconds")}
                  for m in set(prior) | set(now)}
        return teacher.estimated_cost_usd(merged), merged

    max_attempts = int(args.n * args.max_attempts_mult)
    next_attempt, pending = 0, set()
    errors = 0
    stopped_on_budget = False
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while True:
            while (len(pending) < args.workers * 2 and len(run.kept) + len(pending) < args.n
                   and next_attempt < max_attempts):
                if next_attempt not in run.done_attempts:
                    pending.add(pool.submit(process_attempt, next_attempt, args, run,
                                            index, fact_ids, seeds))
                next_attempt += 1
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in done:
                exc = f.exception()
                if exc:
                    errors += 1
                    print(f"  attempt crashed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if len(run.kept) and len(run.kept) % 10 == 0:
                cost, merged = spend()
                usage_path.write_text(json.dumps(merged, indent=1) + "\n")
                (ROOT / f"{args.out}_apistats.json").write_text(json.dumps(
                    {m: dict(c) for m, c in teacher.API_STATS.items()}, indent=1) + "\n")
                print(f"  kept {len(run.kept)}/{args.n}  rejected {len(run.rejects)}  "
                      f"{time.time()-t0:.0f}s  est. spend ${cost:,.0f}", flush=True)
                if args.budget_usd and cost >= args.budget_usd:
                    print(f"BUDGET GUARD: estimated spend ${cost:,.0f} reached --budget-usd {args.budget_usd:,.0f}; "
                          f"stopping (progress is saved, rerun the same command to continue)", flush=True)
                    stopped_on_budget = True
                    break
            if len(run.kept) >= args.n:
                break
    write_report(args.out, run, time.time() - t0)
    cost, merged = spend()
    usage_path.write_text(json.dumps(merged, indent=1) + "\n")
    print(f"estimated API spend for {args.out} so far: ${cost:,.0f}  ({json.dumps(merged)})")
    print(f"\nkept {len(run.kept)}, rejected {len(run.rejects)}, crashed {errors}, "
          f"infrastructure errors (unrecorded) {run.infra_errors}; "
          f"wrote {run.kept_path.name}")
    return 0


def write_report(out: str, run: Run, elapsed_s: float) -> None:
    report = build_report(run.kept, run.rejects, elapsed_s)
    (ROOT / f"{out}_report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


def build_report(kept: list[dict], rejects: list[dict], elapsed_s: float) -> dict:
    metas = [r["meta"] for r in kept]
    n = max(1, len(metas))

    def pct(x):
        return round(100 * x / n, 1)

    def dist(key):
        return {k: pct(v) for k, v in Counter(m[key] for m in metas).most_common()}

    gate_counts: Counter = Counter()
    for r in rejects:
        for g in r["gates"]:
            gate_counts[g] += 1
    skeletons = Counter(m["skeleton_hash"] for m in metas if not m.get("unanswerable"))
    toks = sorted(m["prompt_tokens_est"] for m in metas)
    widths = Counter("7-25" if m["n_columns_target"] <= 25 else "26-80" if m["n_columns_target"] <= 80
                     else "81-200" for m in metas)
    return {
        "kept": len(metas), "rejected": len(rejects),
        "attempts": len(metas) + len(rejects), "elapsed_seconds": round(elapsed_s),
        "keep_rate_pct": round(100 * len(metas) / max(1, len(metas) + len(rejects)), 1),
        "rejections_by_gate": dict(gate_counts.most_common()),
        "question_style_pct": dist("question_style"),
        "exact_column_in_question_pct": pct(sum(m["exact_column_in_question"] for m in metas)),
        "format_counts": dict(Counter(m["format"] for m in metas).most_common()),
        "tables_per_prompt_pct": {str(k): pct(v) for k, v in
                                  sorted(Counter(len(m["prompt_tables"]) for m in metas).items())},
        "distractor_relation_pct": dist("distractor_relation"),
        "hint_level_pct": dist("hint_level"),
        "target_width_pct": {k: pct(v) for k, v in widths.items()},
        "distinct_skeletons": len(skeletons),
        "top_skeleton_share_pct": pct(skeletons.most_common(1)[0][1]) if skeletons else 0,
        "notes_shown_pct": pct(sum(m["notes_shown"] for m in metas)),
        "overview_shown_pct": pct(sum(m.get("overview_shown", False) for m in metas)),
        "instruction_changes_answer_pct_of_notes_shown": round(100 * sum(
            1 for m in metas if m["notes_shown"] and m.get("instructions_changing", 0) > 0)
            / max(1, sum(1 for m in metas if m["notes_shown"])), 1),
        "prompt_length_bucket_pct": {b: pct(sum(1 for t in toks if lo <= t < hi)) for b, (lo, hi) in
                                     {"<1k": (0, 1000), "1-4k": (1000, 4000), "4-12k": (4000, 12000),
                                      "12-24k": (12000, 10**9)}.items()},
        "druid_family_count_pct": {str(k): pct(v) for k, v in sorted(Counter(
            min(3, len(m["feature_families"])) for m in metas if not m.get("unanswerable")).items())},
        "epoch_arith_pct": pct(sum("epoch_arith" in m["feature_families"] for m in metas)),
        "calendar_window_pct": pct(sum("calendar_window" in m["feature_families"] for m in metas)),
        "time_scope_note": "assigned time scope of kept rows",
        "time_scope_pct": {k: round(100 * v / max(1, sum(1 for x in metas if "time_scope" in x)), 1) for k, v in
                           Counter(x["time_scope"] for x in metas if "time_scope" in x).most_common()},
        "unanswerable_pct": pct(sum(m.get("unanswerable", False) for m in metas)),
        "wrong_table_trap_pct": pct(sum(m.get("wrong_table_trap", False) for m in metas)),
        "prompt_tokens_est": {"p50": toks[len(toks) // 2] if toks else 0,
                              "p95": toks[int(len(toks) * 0.95)] if toks else 0,
                              "max": toks[-1] if toks else 0},
    }


if __name__ == "__main__":
    sys.exit(main())
