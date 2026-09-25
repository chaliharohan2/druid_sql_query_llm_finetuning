"""Teacher-LLM client (plan D5, P0-3).

Two different Gemini models play the two teacher roles the plan calls for:
  TEACHER_WRITER  writes the analyst question and the gold Druid SQL.
  TEACHER_JUDGE   writes an independent second SQL for the same question
                  (G4 result agreement) and acts as the G9 semantic judge.

Calls go through Vertex AI (`aiplatform.googleapis.com`), authenticated with
the caller's own `gcloud` credentials against a specific billed project --
not the plain Gemini Developer API with a bare API key. That distinction
matters here: an ad hoc `GEMINI_API_KEY` (AI Studio) is tied to whatever
project *that key* belongs to, and gets free-tier limits (as low as 20
requests/day, 0/day for pro-tier models) unless billing happens to be linked
to that exact project. Vertex AI instead bills directly against
VERTEX_PROJECT, which is confirmed to have an active billing account, so
this reaches standard paid-tier quota regardless of what any stray API key
in the environment is doing. Requires `gcloud auth login` to have been run
once; this module shells out to `gcloud auth print-access-token` and caches
the token for its ~1 hour lifetime.

Not AI training or inference code: this calls an LLM to help *generate*
training data; it is not the model being trained or served.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import time

import requests

VERTEX_PROJECT = "project-7c5f220b-f6f7-4501-bc5"
VERTEX_LOCATION = "global"
API_BASE = f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}/publishers/google/models"
# Two different model families/tiers stand in for the plan's "two different
# strong models" (D5): a pro-tier writer for quality, a distinct flash-tier
# model for the independent second opinion (G4) and the semantic judge (G9),
# so the judge is never just the writer grading its own homework.
TEACHER_WRITER = "gemini-3.1-pro-preview"
TEACHER_JUDGE = "gemini-2.5-flash"
# The preview writer has a small shared quota and returns 429 under load. A writer-tier call tries the
# preview model briefly and otherwise falls back to the GA pro model, so throughput is not pinned to the
# preview quota. The model that actually answered is recorded per example (meta.teacher).
WRITER_CHAIN = [TEACHER_WRITER, "gemini-2.5-pro"]
# A flash-tier writer for a share of attempts: the pro preview's quota caps throughput at roughly a dozen
# calls a minute, while flash-tier models have ample capacity. Every row still clears the same gates, and
# the writer of each row is recorded in meta.teacher. It differs from the independent answerer/judge.
FLASH_WRITER = "gemini-3.5-flash"


class TeacherError(RuntimeError):
    pass


class QuotaError(TeacherError):
    """HTTP 429 that outlasted every retry. Not a verdict on the example: callers leave the attempt
    unrecorded so a later run redoes it."""


class TruncatedError(TeacherError):
    """Response hit maxOutputTokens (thinking tokens count toward it); retrying will not help."""


_token_cache: dict[str, float | str] = {"token": "", "expires": 0.0}

import collections  # noqa: E402
import threading  # noqa: E402

USAGE: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
# HTTP status counts and seconds spent waiting on 429s, per model (diagnoses throughput limits)
API_STATS: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
# USD per million tokens (input, output+thinking). Approximate list prices, used only for the
# --budget-usd guard in generate_v2; the real bill comes from Google Cloud.
PRICES = {"gemini-3.1-pro-preview": (2.00, 12.00), "gemini-2.5-pro": (1.25, 10.00),
          "gemini-2.5-flash": (0.30, 2.50), "gemini-3.5-flash": (0.30, 2.50)}


def estimated_cost_usd(usage: dict | None = None) -> float:
    total = 0.0
    for model, c in (usage or USAGE).items():
        pin, pout = PRICES.get(model, (2.0, 12.0))
        total += c.get("prompt_tokens", 0) / 1e6 * pin
        total += (c.get("output_tokens", 0) + c.get("thought_tokens", 0)) / 1e6 * pout
    return total
_usage_lock = threading.Lock()


def _record_usage(model: str, data: dict, seconds: float) -> None:
    u = data.get("usageMetadata") or {}
    with _usage_lock:
        c = USAGE[model]
        c["calls"] += 1
        c["prompt_tokens"] += u.get("promptTokenCount", 0)
        c["output_tokens"] += u.get("candidatesTokenCount", 0)
        c["thought_tokens"] += u.get("thoughtsTokenCount", 0)
        c["seconds"] += seconds


def _access_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return str(_token_cache["token"])
    try:
        out = subprocess.run(["gcloud", "auth", "print-access-token"],
                             capture_output=True, text=True, timeout=30, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise TeacherError(f"gcloud auth print-access-token failed: {exc}")
    token = out.stdout.strip()
    if not token:
        raise TeacherError("gcloud auth print-access-token returned no token")
    _token_cache["token"] = token
    _token_cache["expires"] = now + 45 * 60  # refresh before the real ~1h expiry
    return token


def generate(model: str, system_instruction: str, prompt: str, *,
            temperature: float = 0.7, max_output_tokens: int = 3000,
            json_mode: bool = True, retries: int = 6, max_quota_waits: int = 14) -> str:
    """One Vertex AI Gemini call. Returns the raw text of the first candidate."""
    url = f"{API_BASE}/{model}:generateContent"
    gen_config: dict = {"temperature": temperature, "maxOutputTokens": max_output_tokens}
    if json_mode:
        gen_config["responseMimeType"] = "application/json"
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen_config,
    }
    last_exc: Exception | None = None
    quota_waits = attempt = 0
    while True:
        try:
            headers = {"Authorization": f"Bearer {_access_token()}"}
            t_call = time.time()
            resp = requests.post(url, json=payload, headers=headers, timeout=180)
            with _usage_lock:
                API_STATS[model][f"http_{resp.status_code}"] += 1
            if resp.status_code == 401:
                _token_cache["token"] = ""  # force a fresh token and retry
                raise TeacherError(f"HTTP 401: {resp.text[:300]}")
            if resp.status_code == 429:
                raise QuotaError(f"HTTP 429: {resp.text[:200]}")
            if resp.status_code >= 500:
                raise TeacherError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
            data = resp.json()
            _record_usage(model, data, time.time() - t_call)
            candidates = data.get("candidates") or []
            if not candidates:
                raise TeacherError(f"no candidates: {json.dumps(data)[:300]}")
            parts = candidates[0].get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            finish = candidates[0].get("finishReason", "?")
            if finish == "MAX_TOKENS":
                raise TruncatedError(f"{model} hit max_output_tokens={max_output_tokens} "
                                     f"(thoughts={(data.get('usageMetadata') or {}).get('thoughtsTokenCount')})")
            if not text.strip():
                raise TeacherError(f"empty response, finishReason={finish}")
            return text
        except TruncatedError:
            raise
        except QuotaError as exc:
            # Capacity pressure on a shared preview model. Waits are counted separately from ordinary
            # retries so a burst of 429s is outlasted (up to ~15 minutes), with jitter so the workers do
            # not retry in lockstep; if it persists, QuotaError propagates and the attempt goes unrecorded.
            quota_waits += 1
            if quota_waits >= max_quota_waits:
                raise QuotaError(f"{model} still rate limited after {quota_waits} waits: {exc}")
            wait = min(15 * 2 ** min(quota_waits, 3), 120) * (0.5 + random.random())
            with _usage_lock:
                API_STATS[model]["quota_wait_seconds"] += wait
            time.sleep(wait)
        except (requests.RequestException, TeacherError) as exc:
            last_exc = exc
            attempt += 1
            if attempt >= retries:
                raise TeacherError(f"{model} failed after {retries} attempts: {last_exc}")
            time.sleep(min(2 ** attempt, 20))


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise TeacherError(f"no JSON object in response: {text[:300]}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        # A response occasionally has a raw, unescaped newline inside a string
        # value (usually the multi-line `sql`) despite JSON mode being on --
        # invalid per strict JSON, but recoverable: escape bare newlines that
        # fall inside a quoted string rather than discarding the whole call.
        fixed = _escape_newlines_in_strings(m.group(0))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            raise TeacherError(f"malformed JSON ({exc}): {text[:300]}")


def _escape_newlines_in_strings(s: str) -> str:
    out, in_string, escape = [], False, False
    for ch in s:
        if in_string and ch == "\n" and not escape:
            out.append("\\n")
            continue
        out.append(ch)
        if in_string and ch == "\\" and not escape:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
        escape = False
    return "".join(out)


CONVENTIONS = """Apache Druid 35.0.0 dialect conventions (CONVENTIONS.md is the source of truth):
- past/last N days (rolling): __time >= CURRENT_TIMESTAMP - INTERVAL 'N' DAY
- past N hours/minutes: __time >= CURRENT_TIMESTAMP - INTERVAL 'N' HOUR (or MINUTE)
- today: __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
- yesterday: __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
- this week/month/quarter/year (to date): __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'|'P1M'|'P3M'|'P1Y')
- last week/month/quarter/year (calendar, NOT rolling): __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, P), P, -1) AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, P), P in {'P1W','P1M','P3M','P1Y'}
- between date A and date B inclusive: __time >= TIMESTAMP 'A 00:00:00' AND __time < TIMESTAMP 'B+1 00:00:00'
- epoch milliseconds column: convert with MILLIS_TO_TIMESTAMP(col)
- epoch seconds column: convert with MILLIS_TO_TIMESTAMP(col * 1000)
- string time column, known format: TIME_PARSE(col, '<Joda format>')
- duration between two epoch columns: raw arithmetic, e.g. (end_col - start_col) / 1000.0 for
  milliseconds-to-seconds; if either column is nullable, add "start_col IS NOT NULL AND end_col
  IS NOT NULL" explicitly unless an instruction already covers it
- use TIMESTAMPDIFF(unit, ts1, ts2) only when both arguments are already timestamps
- output style: EVERY alias you define anywhere -- output columns, CTE columns, subquery columns, window
  columns -- is double-quoted (AS "name", never AS name); GROUP BY and ORDER BY use ordinals, never
  repeat the alias or expression; no trailing semicolon; no markdown fences; exactly one query
- Druid has no NOW(), DATEADD, DATEDIFF, DATE_FORMAT, IF(), ILIKE, STDDEV, or TOP n -- use
  CURRENT_TIMESTAMP, TIME_SHIFT/interval arithmetic, TIME_FORMAT, CASE WHEN, LIKE, ORDER BY ...
  LIMIT n instead
- prefer APPROX_COUNT_DISTINCT / APPROX_QUANTILE_DS unless the question or an instruction
  explicitly demands an exact count
- a bare table scan (no aggregation) may only ORDER BY __time; if the question needs another
  sort with no aggregation, keep the question's meaning (e.g. group by a natural unique key)
  rather than silently changing what is being ranked"""

_WRITER_INSTRUCTIONS = """You write realistic analyst questions and correct Apache Druid 35.0.0 SQL \
for a text-to-SQL training set. You are given one schema, a required "skill spec" \
(which Druid/SQL features the answer must use), and a question style. Follow every dialect \
convention below exactly. Output strict JSON only, no markdown fences, matching this shape:
{"question": "<the analyst's question, in the requested style>",
 "sql": "<the single Druid SQL query that answers it, following every convention>",
 "required_columns": ["<datasource>.<column>", "..."],
 "required_predicates": ["<a WHERE/HAVING condition the SQL must contain, verbatim>"],
 "forbidden_constructs": ["<a construct the SQL must NOT contain>"],
 "feature_families": ["<skill-spec tags actually used>"],
 "sql_features": ["<general-SQL tags actually used, e.g. cte, case, subquery>"]}
The SQL must reference only columns that exist in the given schema. Do not invent columns. \
If an instruction or glossary entry is given and is relevant to the question, the SQL must \
obey it, and its constraint belongs in required_predicates/forbidden_constructs. If the \
question is a "business paraphrase" style, do not use the literal column name in the \
question text unless there is no other way to phrase it. Write the question the way a real person types \
it into a chat box: short and direct ("average handling time of calls per agent last month"), never a \
preamble like "Looking at the data..." or "Please provide..." and never a restatement of the SQL. It must have exactly one \
correct answer: name the statistic unambiguously (average, median, total, maximum, count of distinct ...; \
never "typical" or "usual"), say what a "top" or "worst" ranking is ranked by, state a limit when you want one, \
and name every filter value you use. If a literal value is named in the \
question, the exact same literal must appear in the SQL, and vice versa -- never let the two \
diverge."""

WRITER_SYSTEM = _WRITER_INSTRUCTIONS + "\n\n" + CONVENTIONS

JUDGE_SQL_SYSTEM = ("""You write Apache Druid 35.0.0 SQL. You are given a schema and a question \
that another engineer already answered. Write your own independent SQL query that answers the \
same question, using the same dialect conventions, WITHOUT looking at the other engineer's \
query (it is not shown to you). Output strict JSON only: {"sql": "<query>"}\n\n""" + CONVENTIONS)

JUDGE_SEMANTIC_SYSTEM = """You are a strict reviewer of Druid SQL training examples. Given a \
schema, a question, and a candidate SQL query, decide whether the SQL actually answers the \
question as asked: correct grain (one row per what the question asks for), correct filters, \
correct direction (ascending/descending, worst/best), correct aggregation, and no columns \
invented or borrowed from a table not referenced. If the schema text includes an overview, \
instruction or glossary entry that is relevant to this specific question, the SQL must obey \
it -- if it does not, that is a failure too. Do NOT fail a query for differences that leave the result unchanged: an extra ORDER BY, output alias names, column order, or formatting. Fail only when the grain, filters, aggregation, ranking direction, limit or time window differ from what was asked. Output strict JSON only: \
{"answers_question": true|false, "reason": "<one sentence>"}"""


def write_example(schema_prompt: str, brief: str, chain: list[str] | None = None) -> dict:
    prompt = f"{schema_prompt}\n\n---\nAssignment:\n{brief}"
    text, used = generate_writer(WRITER_SYSTEM, prompt, chain=chain, temperature=0.85, max_output_tokens=16000)
    out = _extract_json(text)
    out["_model"] = used
    return out


def write_independent_sql(schema_prompt: str, question: str, model: str | None = None) -> str:
    prompt = f"{schema_prompt}\n\n---\nQuestion: {question}"
    if model == TEACHER_WRITER:
        text, _ = generate_writer(JUDGE_SQL_SYSTEM, prompt, temperature=0.3, max_output_tokens=8000)
    else:
        text = generate(model or TEACHER_JUDGE, JUDGE_SQL_SYSTEM, prompt, temperature=0.3, max_output_tokens=8000)
    obj = _extract_json(text)
    sql = obj.get("sql", "")
    if not sql:
        raise TeacherError(f"judge returned no sql: {text[:300]}")
    return sql


def semantic_judge(schema_prompt: str, question: str, sql: str) -> dict:
    prompt = f"{schema_prompt}\n\n---\nQuestion: {question}\n\nCandidate SQL:\n{sql}"
    text = generate(TEACHER_JUDGE, JUDGE_SEMANTIC_SYSTEM + "\n\nDialect conventions that are CORRECT (do not fail a query for following them; "
                    "e.g. TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M') is the start of the current calendar quarter):\n" + CONVENTIONS, prompt, temperature=0.0, max_output_tokens=8000)
    return _extract_json(text)


# ------------------------------------------------------------ unanswerable (P1-4)
UNANSWERABLE_KINDS = {
    "missing_field": "Ask a natural analytics question that needs a field or measure that does NOT exist "
                     "anywhere in the tables shown. Nothing in any table can answer it, even approximately.",
    "missing_table": "Ask a natural analytics question about an entity or dataset that is not among the "
                     "tables shown (e.g. a different business object entirely).",
    "write_operation": "Ask for a data modification (insert, update, delete, drop, alter, create a table). "
                       "A read-only SELECT cannot do it.",
    "near_miss": "Ask for something where a SIMILAR but different column exists, and answering with that "
                 "column would be WRONG because the meaning differs (e.g. asking for refunds when only "
                 "order amounts exist). Name the concept naturally; do not mention the similar column.",
}

UNANSWERABLE_WRITER_SYSTEM = """You write realistic requests that a text-to-SQL assistant must DECLINE because \
the schema shown cannot answer them. Output strict JSON only: \
{"question": "<the request, phrased like a real user>", "missing": "<what is missing, <=12 words>"}. \
The request must be phrased naturally and confidently, as if the data existed. Never mention the schema, \
tables or columns being absent."""

ANSWERABLE_CHECK_SYSTEM = ("""You are checking whether a request can be answered by ONE read-only Apache Druid 35.0.0 \
SQL query over the schema shown, using only tables and columns that exist. Be strict: a query that would need \
a column that is not there, or would answer a subtly different question, does not count. Output strict JSON \
only: {"answerable": true|false, "sql": "<query or null>", "reason": "<one sentence>"}\n\n""" + CONVENTIONS)


def write_unanswerable(schema_prompt: str, kind: str) -> dict:
    prompt = f"{schema_prompt}\n\n---\nRequest type: {kind}. {UNANSWERABLE_KINDS[kind]}"
    text, used = generate_writer(UNANSWERABLE_WRITER_SYSTEM, prompt, temperature=0.9, max_output_tokens=8000)
    out = _extract_json(text)
    out["_model"] = used
    return out


def check_answerable(schema_prompt: str, question: str) -> dict:
    prompt = f"{schema_prompt}\n\n---\nRequest: {question}"
    return _extract_json(generate(TEACHER_JUDGE, ANSWERABLE_CHECK_SYSTEM, prompt, temperature=0.0,
                                  max_output_tokens=8000))


# ------------------------------------------------------------- follow-ups (P2-1)
FOLLOWUP_KINDS = [
    "add a breakdown by one more column ('now split that by ...')",
    "change the time window ('same but last quarter', 'and for the past 7 days?')",
    "add one filter on a categorical column ('only for ...')",
    "change the metric while keeping the grouping and window ('what about the average instead?')",
    "keep only the top N or bottom N of the previous result",
    "add a second measure next to the existing one",
]

FOLLOWUP_WRITER_SYSTEM = ("""You continue a database conversation. An analyst asked a question and got a Druid SQL \
answer; now write the analyst's short natural FOLLOW-UP that builds on it (it refers to 'that', 'same', 'now', \
etc. rather than repeating the whole question) and the new single Apache Druid 35.0.0 SQL query that answers the \
follow-up in the context of the earlier exchange. Ground every column in the schema shown, name any literal value \
you filter on in the follow-up, and obey every convention below. Output strict JSON only: \
{"followup": "<follow-up question>", "sql": "<complete new query>"}\n\n""" + CONVENTIONS)

FOLLOWUP_INDEPENDENT_SYSTEM = ("""You continue a database conversation. Given the schema, the earlier question and \
its SQL answer, and the analyst's follow-up, write the single Apache Druid 35.0.0 SQL query that answers the \
follow-up. Output strict JSON only: {"sql": "<query>"}\n\n""" + CONVENTIONS)


def write_followup(schema_prompt: str, prev_q: str, prev_sql: str, kind: str) -> dict:
    prompt = (f"{schema_prompt}\n\n---\nEarlier question: {prev_q}\nEarlier SQL:\n{prev_sql}\n\n"
              f"Follow-up type: {kind}")
    text, used = generate_writer(FOLLOWUP_WRITER_SYSTEM, prompt, temperature=0.85, max_output_tokens=16000)
    out = _extract_json(text)
    out["_model"] = used
    return out


def independent_followup_sql(schema_prompt: str, prev_q: str, prev_sql: str, followup: str) -> str:
    prompt = (f"{schema_prompt}\n\n---\nEarlier question: {prev_q}\nEarlier SQL:\n{prev_sql}\n\n"
              f"Follow-up: {followup}")
    sql = _extract_json(generate(TEACHER_JUDGE, FOLLOWUP_INDEPENDENT_SYSTEM, prompt, temperature=0.3,
                                 max_output_tokens=8000)).get("sql", "")
    if not sql:
        raise TeacherError("no follow-up sql from the independent model")
    return sql


REPAIR_SYSTEM = ("""You fix Apache Druid 35.0.0 SQL. You are given a schema, a question, a query that Druid REJECTED, and \
Druid's error. Return a corrected single query that still answers the question, using only functions Druid has. \
Output strict JSON only: {"sql": "<query>"}\n\n""" + CONVENTIONS)


def repair_sql(schema_prompt: str, question: str, bad_sql: str, error: str, model: str | None = None) -> str:
    prompt = (f"{schema_prompt}\n\n---\nQuestion: {question}\n\nRejected query:\n{bad_sql}\n\n"
              f"Druid error: {error[:400]}")
    if model == TEACHER_WRITER:
        text, _ = generate_writer(REPAIR_SYSTEM, prompt, temperature=0.0, max_output_tokens=8000)
    else:
        text = generate(model or TEACHER_JUDGE, REPAIR_SYSTEM, prompt, temperature=0.0, max_output_tokens=8000)
    sql = _extract_json(text).get("sql", "")
    if not sql:
        raise TeacherError("repair returned no sql")
    return sql



def generate_writer(system_instruction: str, prompt: str, chain: list[str] | None = None, **kw) -> tuple[str, str]:
    """Writer-tier call: the preview writer first, with patient retries, then the GA pro model as a
    last resort. Returns (text, model_used).

    Measured on this project: the preview model succeeds on ~92% of calls (a 429 clears within a
    minute), while gemini-2.5-pro has a much smaller quota and returns 429 on most calls under load.
    So the preview model gets the patience and the fallback is only for a sustained outage."""
    last: Exception | None = None
    models = chain or WRITER_CHAIN
    for i, model in enumerate(models):
        try:
            return generate(model, system_instruction, prompt,
                            max_quota_waits=8 if i == 0 else 14, **kw), model
        except QuotaError as exc:
            last = exc
    raise QuotaError(f"every writer model is rate limited: {last}")


AUDITOR_SYSTEM = (JUDGE_SEMANTIC_SYSTEM + """

IMPORTANT: the candidate SQL has ALREADY been executed successfully on Apache Druid 35.0.0, on three different
seed datasets, and returned rows. Do not flag syntax, function support or planner concerns (window functions over
aggregates, LATEST/EARLIEST(expr[, maxBytes]) which order by __time implicitly, `col = 'x'` on a multi-value
dimension which matches any value, TIME_FLOOR/TIME_SHIFT idioms). Judge ONLY whether the query answers the
question as asked: right columns for the concepts named, grain, filters, window, ranking direction, limit.
A loose but reasonable business-word-to-column mapping is acceptable when the schema offers no better column.

Dialect conventions that are CORRECT:
""" + CONVENTIONS)


def audit_answer(prompt_text: str, question: str, sql: str, model: str = "gemini-2.5-pro") -> dict:
    """Second-opinion semantic check by a stronger model than the pipeline's G9 judge."""
    out = generate(model, AUDITOR_SYSTEM,
                   f"{prompt_text}\n\n---\nQuestion (the last user message above): {question}\n\nCandidate SQL:\n{sql}",
                   temperature=0.0, max_output_tokens=12000, max_quota_waits=30)
    return _extract_json(out)
