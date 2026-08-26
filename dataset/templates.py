"""Query templates.

A template is a small function that binds to a schema through its roles and
returns one (question, SQL) pair plus the gates that prove it. The same template
renders against every schema that has the roles it needs, which is how sixty-odd
schemas and a hundred templates produce a thousand examples without any of them
being near-duplicates.

House style, enforced here and documented in druid_dataset_creation.md:
  - output aliases are ALWAYS double-quoted
  - GROUP BY / ORDER BY use ordinals, never select aliases
  - relative time resolves against CURRENT_TIMESTAMP, never a hardcoded date
  - approximate aggregators by default

`trap.naive_sql` is the standard-SQL reflex. It is validated to fail or to
differ, and it is NEVER shown to the model.

Not AI training or inference code: this produces training *data*.
"""
from __future__ import annotations

from datetime import timedelta

from schema_view import SV

T: list[dict] = []
ANCHOR = None  # set by generate.py; the time the seed data was built around


def tpl(cluster: str, needs: tuple | list = ()):
    def deco(fn):
        T.append({"id": fn.__name__, "cluster": cluster, "needs": tuple(needs), "fn": fn})
        return fn
    return deco


def Q(question: str, sql: str, must=(), rows: bool = True, trap=None) -> dict:
    return {"question": question, "sql": sql.strip(), "must": tuple(must),
            "rows": rows, "trap": trap}


def INVALID(sql: str) -> dict:
    return {"naive_sql": sql.strip(), "expect": "INVALID"}


def DIFFERENT(sql: str) -> dict:
    return {"naive_sql": sql.strip(), "expect": "DIFFERENT"}


def anchor_date(days_ago: int) -> str:
    return (ANCHOR - timedelta(days=days_ago)).strftime("%Y-%m-%d 00:00:00")


def pick(s: SV, *options):
    return s.rng.choice(options)


# ============================================================== time bucketing
@tpl("time_bucketing", ["metrics"])
def tb_avg_by_grain(s):
    g, gp, gn = s.grain()
    w, wp, _ = s.window()
    m = s.met()
    return Q(pick(s, f"Show {gp} average {m} over {wp}",
                  f"What is the {gp} mean {m} across {wp}?",
                  f"Average {m} {gp} for {wp}"),
             f"""
SELECT TIME_FLOOR(__time, '{g}') AS "{gn}",
       AVG({m}) AS "avg_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL {w}
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"])


@tpl("time_bucketing", [])
def tb_count_by_grain(s):
    g, gp, gn = s.grain()
    w, wp, _ = s.window()
    return Q(pick(s, f"Count {s.noun()} {gp} over {wp}",
                  f"How many {s.noun()} were there {gp} in {wp}?",
                  f"{gp.capitalize()} volume of {s.noun()} for {wp}"),
             f"""
SELECT TIME_FLOOR(__time, '{g}') AS "{gn}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL {w}
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"])


@tpl("time_bucketing", ["metrics", "dims"])
def tb_sum_filtered(s):
    g, gp, gn = s.grain()
    w, wp, _ = s.wide_window()  # an equality filter on a 24h window can match nothing
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Total {m} {gp} for {d} = {s.lit(d)} over {wp}",
                  f"Break {m} down {gp} where {d} is {s.lit(d)}, {wp}"),
             f"""
SELECT TIME_FLOOR(__time, '{g}') AS "{gn}",
       SUM({m}) AS "total_{m}"
FROM {s.ds}
WHERE {d} = {s.lit(d)}
  AND __time >= CURRENT_TIMESTAMP - INTERVAL {w}
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"])


@tpl("time_bucketing", ["metrics", "dims"])
def tb_grain_by_dim(s):
    g, gp, gn = s.grain()
    w, wp, _ = s.window()
    d, m = s.dim(), s.met()
    return Q(pick(s, f"{gp.capitalize()} {m} broken down by {d} for {wp}",
                  f"Show {m} {gp} per {d} across {wp}"),
             f"""
SELECT TIME_FLOOR(__time, '{g}') AS "{gn}",
       {d} AS "{d}",
       SUM({m}) AS "total_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL {w}
GROUP BY 1, 2
ORDER BY 1, 2
""", must=["TIME_FLOOR", "GROUP BY 1, 2"])


@tpl("time_bucketing", ["hi_card"])
def tb_distinct_by_grain(s):
    g, gp, gn = s.grain()
    w, wp, _ = s.window()
    h = s.hi()
    return Q(pick(s, f"Count distinct {h} {gp} for {wp}",
                  f"How many unique {h} values appear {gp} over {wp}?"),
             f"""
SELECT TIME_FLOOR(__time, '{g}') AS "{gn}",
       APPROX_COUNT_DISTINCT({h}) AS "distinct_{h}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL {w}
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR", "APPROX_COUNT_DISTINCT"])


@tpl("time_bucketing", ["metrics"])
def tb_ceil(s):
    m = s.met()
    return Q(pick(s, f"Round each record up to the next hour and show peak {m} per hour for the last 3 days",
                  f"Using hour-ending buckets, what is the maximum {m} per hour over the last 3 days?"),
             f"""
SELECT TIME_CEIL(__time, 'PT1H') AS "hour_ending",
       MAX({m}) AS "peak_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '3' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_CEIL"])


@tpl("time_bucketing", ["two_metrics"])
def tb_two_metrics(s):
    a, b = s.mets(2)
    g, gp, gn = s.grain()
    return Q(pick(s, f"Show {gp} totals for both {a} and {b} over the last 14 days",
                  f"{gp.capitalize()} {a} and {b} for the last fortnight"),
             f"""
SELECT TIME_FLOOR(__time, '{g}') AS "{gn}",
       SUM({a}) AS "total_{a}",
       SUM({b}) AS "total_{b}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"])


# ================================================================ relative time
@tpl("relative_time", [])
def rt_count_window(s):
    w, wp, _ = s.window()
    return Q(pick(s, f"How many {s.noun()} in {wp}?",
                  f"Count {s.noun()} recorded during {wp}",
                  f"Total {s.noun()} over {wp}"),
             f"""
SELECT COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL {w}
""", must=["CURRENT_TIMESTAMP"],
             trap=INVALID(f"SELECT COUNT(*) AS record_count FROM {s.ds} "
                          f"WHERE __time >= NOW() - INTERVAL {w}"))


@tpl("relative_time", ["metrics"])
def rt_avg_window(s):
    w, wp, _ = s.window()
    m = s.met()
    return Q(pick(s, f"What was the average {m} in {wp}?",
                  f"Mean {m} over {wp}"),
             f"""
SELECT AVG({m}) AS "avg_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL {w}
""", must=["CURRENT_TIMESTAMP"],
             trap=INVALID(f"SELECT AVG({m}) AS avg_{m} FROM {s.ds} "
                          f"WHERE __time >= DATEADD(day, -7, CURRENT_TIMESTAMP)"))


@tpl("relative_time", ["dims"])
def rt_prior_window(s):
    d = s.dim()
    return Q(pick(s, f"Count {s.noun()} per {d} for the week before last",
                  f"How many {s.noun()} per {d} between 14 and 7 days ago?"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
  AND __time < CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""", must=["CURRENT_TIMESTAMP"])


@tpl("relative_time", ["metrics"])
def rt_since_midnight(s):
    m = s.met()
    return Q(pick(s, f"What is the average {m} so far today?",
                  f"Average {m} since midnight UTC"),
             f"""
SELECT AVG({m}) AS "avg_{m}"
FROM {s.ds}
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
""", must=["TIME_FLOOR(CURRENT_TIMESTAMP"], rows=False)


@tpl("relative_time", ["dims"])
def rt_last_month_group(s):
    d = s.dim()
    return Q(pick(s, f"Break the last 30 days of {s.noun()} down by {d}",
                  f"Counts per {d} for the last 30 days"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""", must=["INTERVAL '30' DAY"])


@tpl("relative_time", ["metrics"])
def rt_hours(s):
    m = s.met()
    n = pick(s, "6", "12", "36", "48")
    return Q(pick(s, f"Highest {m} in the last {n} hours",
                  f"What is the peak {m} over the past {n} hours?"),
             f"""
SELECT MAX({m}) AS "max_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '{n}' HOUR
""", must=["HOUR"])


# ==================================================== time extract and format
@tpl("time_extract_format", [])
def te_hour_of_day(s):
    return Q(pick(s, f"Which hour of the day sees the most {s.noun()}?",
                  f"Distribution of {s.noun()} by hour of day over the last 14 days",
                  f"Count {s.noun()} per hour of day for the last fortnight"),
             f"""
SELECT TIME_EXTRACT(__time, 'HOUR') AS "hour_of_day",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_EXTRACT"],
             trap=INVALID(f"SELECT DATEPART(hour, __time) AS hour_of_day, COUNT(*) AS record_count "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("time_extract_format", ["metrics"])
def te_day_of_week(s):
    m = s.met()
    return Q(pick(s, f"Average {m} by day of week over the last 30 days",
                  f"Which weekday has the highest {m}?"),
             f"""
SELECT TIME_EXTRACT(__time, 'DOW') AS "day_of_week",
       AVG({m}) AS "avg_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_EXTRACT(__time, 'DOW')"])


@tpl("time_extract_format", [])
def te_format_day(s):
    return Q(pick(s, f"Show {s.noun()} per calendar day as a yyyy-MM-dd string for the last 7 days",
                  f"Daily counts of {s.noun()} with the date formatted yyyy-MM-dd, last week"),
             f"""
SELECT TIME_FORMAT(__time, 'yyyy-MM-dd') AS "day",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_FORMAT"],
             trap=INVALID(f"SELECT DATE_FORMAT(__time, '%Y-%m-%d') AS \"day\", COUNT(*) AS record_count "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("time_extract_format", ["metrics"])
def te_format_month(s):
    m = s.met()
    return Q(pick(s, f"Monthly total {m}, month rendered as yyyy-MM",
                  f"Sum {m} per month with the month as a yyyy-MM label"),
             f"""
SELECT TIME_FORMAT(__time, 'yyyy-MM') AS "month",
       SUM({m}) AS "total_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["TIME_FORMAT(__time, 'yyyy-MM')"])


@tpl("time_extract_format", [])
def te_timezone(s):
    tz = pick(s, "America/New_York", "Europe/London", "Asia/Tokyo", "Australia/Sydney")
    return Q(pick(s, f"Count {s.noun()} by hour of day in {tz} for the last week",
                  f"Hourly distribution of {s.noun()} in the {tz} timezone, last 7 days"),
             f"""
SELECT TIME_EXTRACT(__time, 'HOUR', '{tz}') AS "local_hour",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=[f"'{tz}'"])


@tpl("time_extract_format", ["dims"])
def te_month_dim(s):
    d = s.dim()
    return Q(pick(s, f"Count {s.noun()} by month number and {d}",
                  f"Per-month-number breakdown of {s.noun()} across {d}"),
             f"""
SELECT TIME_EXTRACT(__time, 'MONTH') AS "month_number",
       {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1, 2
ORDER BY 1, 2
""", must=["TIME_EXTRACT(__time, 'MONTH')"])


# =========================================================== epoch time column
@tpl("epoch_time_column", ["epoch_ms"])
def ep_ms_bucket(s):
    c = s.r["epoch_ms"]
    return Q(pick(s, f"Bucket {s.noun()} by the hour of {c} for the last 7 days",
                  f"Hourly counts based on {c} rather than the ingest time, last week"),
             f"""
SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP({c}), 'PT1H') AS "hour",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=["MILLIS_TO_TIMESTAMP"],
             trap=INVALID(f"SELECT TIME_FLOOR({c}, 'PT1H') AS \"hour\", COUNT(*) AS record_count "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("epoch_time_column", ["epoch_ms"])
def ep_ms_lag(s):
    c = s.r["epoch_ms"]
    return Q(pick(s, f"Average lag in seconds between {c} and the event time, last 7 days",
                  f"How many seconds pass between {c} and __time on average?"),
             f"""
SELECT AVG(TIMESTAMPDIFF(SECOND, MILLIS_TO_TIMESTAMP({c}), __time)) AS "avg_lag_seconds"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
""", must=["MILLIS_TO_TIMESTAMP", "TIMESTAMPDIFF"],
             trap=INVALID(f"SELECT AVG(DATEDIFF(second, {c}, __time)) AS avg_lag_seconds FROM {s.ds}"))


@tpl("epoch_time_column", ["epoch_s"])
def ep_s_bucket(s):
    c = s.r["epoch_s"]
    return Q(pick(s, f"Daily counts keyed on {c}, which is stored as epoch seconds",
                  f"Bucket by day using {c} for the last 14 days"),
             f"""
SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP({c} * 1000), 'P1D') AS "day",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 1
""", must=["* 1000", "MILLIS_TO_TIMESTAMP"],
             trap=DIFFERENT(f"SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP({c}), 'P1D') AS \"day\", "
                            f"COUNT(*) AS record_count FROM {s.ds} GROUP BY 1 ORDER BY 1"))


@tpl("epoch_time_column", ["epoch_s"])
def ep_s_format(s):
    c = s.r["epoch_s"]
    return Q(pick(s, f"Show {c} as a readable timestamp for the 5 most recent {s.noun()}",
                  f"Render {c} as yyyy-MM-dd HH:mm:ss for the latest five {s.noun()}"),
             f"""
SELECT __time AS "event_time",
       TIME_FORMAT(MILLIS_TO_TIMESTAMP({c} * 1000), 'yyyy-MM-dd HH:mm:ss') AS "queued_at"
FROM {s.ds}
ORDER BY __time DESC
LIMIT 5
""", must=["MILLIS_TO_TIMESTAMP"])


@tpl("epoch_time_column", ["epoch_ms", "dims"])
def ep_ms_filter(s):
    c, d = s.r["epoch_ms"], s.dim()
    return Q(pick(s, f"Count {s.noun()} per {d} where {c} falls in the last 3 days",
                  f"Using {c} for the time filter, count per {d} over three days"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE MILLIS_TO_TIMESTAMP({c}) >= CURRENT_TIMESTAMP - INTERVAL '3' DAY
GROUP BY 1
ORDER BY 2 DESC
""", must=["MILLIS_TO_TIMESTAMP"])


# ========================================================== string time column
@tpl("string_time_column", ["str_space"])
def st_space_bucket(s):
    c = s.r["str_space"]
    return Q(pick(s, f"Daily counts based on {c}, which is a string timestamp",
                  f"Bucket {s.noun()} by the day of {c}"),
             f"""
SELECT TIME_FLOOR(TIME_PARSE({c}, 'yyyy-MM-dd HH:mm:ss'), 'P1D') AS "day",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["TIME_PARSE"],
             trap=INVALID(f"SELECT TIME_FLOOR({c}, 'P1D') AS \"day\", COUNT(*) AS record_count "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("string_time_column", ["str_space", "metrics"])
def st_space_lag(s):
    c, m = s.r["str_space"], s.met()
    return Q(pick(s, f"Average {m} for records where {c} is within the last 7 days",
                  f"Mean {m} filtered on the parsed {c} column, last week"),
             f"""
SELECT AVG({m}) AS "avg_{m}"
FROM {s.ds}
WHERE TIME_PARSE({c}, 'yyyy-MM-dd HH:mm:ss') >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
""", must=["TIME_PARSE"])


@tpl("string_time_column", ["str_iso"])
def st_iso_bucket(s):
    c = s.r["str_iso"]
    return Q(pick(s, f"Bucket by the day of {c}, an ISO-8601 string column",
                  f"Daily counts using the ISO timestamp in {c}"),
             f"""
SELECT TIME_FLOOR(TIME_PARSE({c}, 'yyyy-MM-dd''T''HH:mm:ss''Z'''), 'P1D') AS "day",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["TIME_PARSE"],
             trap=INVALID(f"SELECT TIME_FLOOR({c}, 'P1D') AS \"day\", "
                          f"COUNT(*) AS record_count FROM {s.ds} GROUP BY 1"))


@tpl("string_time_column", ["str_iso", "dims"])
def st_iso_hour_gap(s):
    c, d = s.r["str_iso"], s.dim()
    return Q(pick(s, f"Average hours between the event time and {c}, per {d}",
                  f"Per {d}, how many hours elapse between __time and {c}?"),
             f"""
SELECT {d} AS "{d}",
       AVG(TIMESTAMPDIFF(HOUR, __time, TIME_PARSE({c}, 'yyyy-MM-dd''T''HH:mm:ss''Z'''))) AS "avg_gap_hours"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["TIME_PARSE", "TIMESTAMPDIFF"],
             trap=INVALID(f"SELECT {d}, AVG(DATEDIFF(hour, __time, {c})) AS avg_gap_hours "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("string_time_column", ["str_space"])
def st_space_hour(s):
    c = s.r["str_space"]
    return Q(pick(s, f"Hour-of-day histogram taken from {c}",
                  f"Which hour does {c} cluster in?"),
             f"""
SELECT TIME_EXTRACT(TIME_PARSE({c}, 'yyyy-MM-dd HH:mm:ss'), 'HOUR') AS "hour_of_day",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["TIME_PARSE", "TIME_EXTRACT"])


# ======================================================== order by restriction
@tpl("order_by_restriction", ["metrics"])
def ob_recent_rows(s):
    m, d = s.met(), (s.dim() if s.has("dims") else s.r["time"])
    n = pick(s, "5", "10", "20")
    return Q(pick(s, f"Show the {n} most recent {s.noun()} with their {m}",
                  f"List the latest {n} {s.noun()}, newest first"),
             f"""
SELECT __time AS "event_time",
       {d} AS "{d}",
       {m} AS "{m}"
FROM {s.ds}
ORDER BY __time DESC
LIMIT {n}
""", must=["ORDER BY __time DESC"],
             trap=INVALID(f"SELECT __time, {d}, {m} FROM {s.ds} ORDER BY {m} DESC LIMIT {n}"))


@tpl("order_by_restriction", ["metrics", "hi_card"])
def ob_top_by_group(s):
    m, h = s.met(), s.hi()
    n = pick(s, "10", "20", "25")
    return Q(pick(s, f"Top {n} {h} values by total {m}",
                  f"Which {n} {h} values have the highest {m}?"),
             f"""
SELECT {h} AS "{h}",
       SUM({m}) AS "total_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
LIMIT {n}
""", must=["ORDER BY 2 DESC"],
             trap=INVALID(f"SELECT {h}, {m} FROM {s.ds} ORDER BY {m} DESC LIMIT {n}"))


@tpl("order_by_restriction", ["metrics"])
def ob_oldest_rows(s):
    m = s.met()
    return Q(pick(s, f"Show the 10 oldest {s.noun()} and their {m}",
                  f"Earliest ten {s.noun()} in the table"),
             f"""
SELECT __time AS "event_time",
       {m} AS "{m}"
FROM {s.ds}
ORDER BY __time
LIMIT 10
""", must=["ORDER BY __time"])


@tpl("order_by_restriction", ["dims", "metrics"])
def ob_scan_filtered(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Latest 15 {s.noun()} where {d} is {s.lit(d)}",
                  f"Most recent fifteen rows with {d} = {s.lit(d)}"),
             f"""
SELECT __time AS "event_time",
       {d} AS "{d}",
       {m} AS "{m}"
FROM {s.ds}
WHERE {d} = {s.lit(d)}
ORDER BY __time DESC
LIMIT 15
""", must=["ORDER BY __time DESC"],
             trap=INVALID(f"SELECT __time, {d}, {m} FROM {s.ds} WHERE {d} = {s.lit(d)} "
                          f"ORDER BY {d}, __time DESC LIMIT 15"))


@tpl("order_by_restriction", ["metrics", "dims"])
def ob_group_all_columns(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Show every {d} and its worst {m}, ordered by that {m}",
                  f"Rank {d} values by their maximum {m}"),
             f"""
SELECT {d} AS "{d}",
       MAX({m}) AS "max_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["GROUP BY 1", "ORDER BY 2 DESC"])


# =============================================================== reserved alias
@tpl("reserved_alias", [])
def ra_hour(s):
    return Q(pick(s, f"Count {s.noun()} per hour for the last 2 days and call the column hour",
                  f"Hourly counts over two days, with the bucket column named hour"),
             f"""
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour",
       COUNT(*) AS "count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
GROUP BY 1
ORDER BY 1
""", must=['AS "hour"', 'AS "count"'],
             trap=INVALID(f"SELECT TIME_FLOOR(__time, 'PT1H') AS hour, COUNT(*) AS count "
                          f"FROM {s.ds} GROUP BY 1 ORDER BY 1"))


@tpl("reserved_alias", ["metrics"])
def ra_value(s):
    m = s.met()
    return Q(pick(s, f"Daily average {m}, output columns named day and value",
                  f"Give me day and value columns for the daily mean {m}"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       AVG({m}) AS "value"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=['AS "day"', 'AS "value"'],
             trap=INVALID(f"SELECT TIME_FLOOR(__time, 'P1D') AS day, AVG({m}) AS value "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("reserved_alias", ["dims", "metrics"])
def ra_timestamp(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"For each {d}, show the earliest timestamp and total {m}, naming the columns timestamp and size",
                  f"Per {d}: a timestamp column and a size column"),
             f"""
SELECT {d} AS "{d}",
       MIN(__time) AS "timestamp",
       SUM({m}) AS "size"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=['AS "timestamp"', 'AS "size"'],
             trap=INVALID(f"SELECT {d}, MIN(__time) AS timestamp, SUM({m}) AS size "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("reserved_alias", ["dims"])
def ra_end(s):
    d = s.dim()
    return Q(pick(s, f"Per {d}, give the first and last event times as columns named start and end",
                  f"Show start and end columns per {d}"),
             f"""
SELECT {d} AS "{d}",
       MIN(__time) AS "start",
       MAX(__time) AS "end"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=['AS "start"', 'AS "end"'],
             trap=INVALID(f"SELECT {d}, MIN(__time) AS start, MAX(__time) AS end "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("reserved_alias", ["metrics"])
def ra_date_rank(s):
    m = s.met()
    return Q(pick(s, f"Daily total {m} with columns named date and rank order by date",
                  f"Produce date and rank columns for daily {m}"),
             f"""
SELECT TIME_FORMAT(__time, 'yyyy-MM-dd') AS "date",
       SUM({m}) AS "rank"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=['AS "date"', 'AS "rank"'],
             trap=INVALID(f"SELECT TIME_FORMAT(__time, 'yyyy-MM-dd') AS date, SUM({m}) AS rank "
                          f"FROM {s.ds} GROUP BY 1"))


# ============================================================ timestamp literal
@tpl("timestamp_literal", [])
def tl_since(s):
    day = anchor_date(20)
    return Q(pick(s, f"How many {s.noun()} since {day[:10]}?",
                  f"Count {s.noun()} recorded on or after {day[:10]}"),
             f"""
SELECT COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= TIMESTAMP '{day}'
""", must=["TIMESTAMP '"],
             trap=INVALID(f"SELECT COUNT(*) AS record_count FROM {s.ds} "
                          f"WHERE __time >= TIMESTAMP '{day[:10]}T00:00:00Z'"))


@tpl("timestamp_literal", ["metrics"])
def tl_between(s):
    a, b = anchor_date(20), anchor_date(10)
    m = s.met()
    return Q(pick(s, f"Average {m} between {a[:10]} and {b[:10]}",
                  f"What was the mean {m} from {a[:10]} up to {b[:10]}?"),
             f"""
SELECT AVG({m}) AS "avg_{m}"
FROM {s.ds}
WHERE __time >= TIMESTAMP '{a}'
  AND __time < TIMESTAMP '{b}'
""", must=["TIMESTAMP '"])


@tpl("timestamp_literal", ["dims"])
def tl_day_of(s):
    day = anchor_date(12)
    d = s.dim()
    return Q(pick(s, f"Break {s.noun()} on {day[:10]} down by {d}",
                  f"Counts per {d} for the single day {day[:10]}"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= TIMESTAMP '{day}'
  AND __time < TIMESTAMP '{anchor_date(11)}'
GROUP BY 1
ORDER BY 2 DESC
""", must=["TIMESTAMP '"])


# ================================================================ approximate
@tpl("approx_agg", ["hi_card"])
def aa_distinct(s):
    h = s.hi()
    return Q(pick(s, f"How many distinct {h} values in the last 14 days?",
                  f"Approximate unique count of {h} over the last fortnight"),
             f"""
SELECT APPROX_COUNT_DISTINCT({h}) AS "distinct_{h}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
""", must=["APPROX_COUNT_DISTINCT"])


@tpl("approx_agg", ["hi_card", "dims"])
def aa_distinct_by_dim(s):
    h, d = s.hi(), s.dim()
    return Q(pick(s, f"Distinct {h} per {d}",
                  f"Count unique {h} values broken down by {d}"),
             f"""
SELECT {d} AS "{d}",
       APPROX_COUNT_DISTINCT({h}) AS "distinct_{h}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["APPROX_COUNT_DISTINCT"])


@tpl("approx_agg", ["metrics"])
def aa_p95(s):
    m = s.met()
    p = pick(s, ("0.95", "95th"), ("0.99", "99th"), ("0.90", "90th"))
    return Q(pick(s, f"What is the {p[1]} percentile {m} over the last 7 days?",
                  f"{p[1]} percentile of {m}, last week"),
             f"""
SELECT APPROX_QUANTILE_DS({m}, {p[0]}) AS "p{p[0][2:]}_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
""", must=["APPROX_QUANTILE_DS"],
             trap=INVALID(f"SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY {m}) AS p95 FROM {s.ds}"))


@tpl("approx_agg", ["metrics", "dims"])
def aa_median_by_dim(s):
    m, d = s.met(), s.dim()
    return Q(pick(s, f"Median {m} per {d}",
                  f"What is the middle {m} for each {d}?"),
             f"""
SELECT {d} AS "{d}",
       APPROX_QUANTILE_DS({m}, 0.5) AS "median_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["APPROX_QUANTILE_DS"],
             trap=INVALID(f"SELECT {d}, MEDIAN({m}) AS median_{m} FROM {s.ds} GROUP BY 1"))


@tpl("approx_agg", ["metrics"])
def aa_quantile_grain(s):
    m = s.met()
    return Q(pick(s, f"Hourly p95 {m} for the last 2 days",
                  f"Show the 95th percentile {m} per hour over two days"),
             f"""
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour",
       APPROX_QUANTILE_DS({m}, 0.95) AS "p95_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
GROUP BY 1
ORDER BY 1
""", must=["APPROX_QUANTILE_DS", "TIME_FLOOR"])


@tpl("approx_agg", ["hi_card"])
def aa_distinct_grain(s):
    h = s.hi()
    return Q(pick(s, f"Daily distinct {h} for the last 21 days",
                  f"How many unique {h} values appear each day over three weeks?"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       APPROX_COUNT_DISTINCT({h}) AS "distinct_{h}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '21' DAY
GROUP BY 1
ORDER BY 1
""", must=["APPROX_COUNT_DISTINCT"])


# ============================================================ latest / earliest
@tpl("latest_earliest", ["metrics"])
def le_latest_numeric(s):
    m = s.met()
    return Q(pick(s, f"What is the most recent {m} value?",
                  f"Give me the latest recorded {m}"),
             f"""
SELECT LATEST({m}) AS "latest_{m}"
FROM {s.ds}
""", must=["LATEST("],
             trap=INVALID(f"SELECT LAST({m}) AS latest_{m} FROM {s.ds}"))


@tpl("latest_earliest", ["metrics", "dims"])
def le_latest_by_dim(s):
    m, d = s.met(), s.dim()
    return Q(pick(s, f"Latest {m} per {d}",
                  f"For each {d}, what is the most recent {m}?"),
             f"""
SELECT {d} AS "{d}",
       LATEST({m}) AS "latest_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["LATEST("])


@tpl("latest_earliest", ["dims", "metrics"])
def le_latest_string(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Most recent {d} seen for each bucket of the last 7 days",
                  f"Daily latest {d} value, last week"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       LATEST({d}, 64) AS "latest_{d}",
       LATEST({m}) AS "latest_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=["LATEST(", ", 64)"])


@tpl("latest_earliest", ["metrics", "dims"])
def le_earliest(s):
    m, d = s.met(), s.dim()
    return Q(pick(s, f"First recorded {m} for each {d}",
                  f"Earliest {m} per {d}"),
             f"""
SELECT {d} AS "{d}",
       EARLIEST({m}) AS "earliest_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["EARLIEST("])


@tpl("latest_earliest", ["metrics", "dims"])
def le_latest_by(s):
    m, d = s.met(), s.dim()
    return Q(pick(s, f"For each {d}, the {m} attached to its most recent record",
                  f"Per {d}, take {m} from the newest row"),
             f"""
SELECT {d} AS "{d}",
       LATEST_BY({m}, __time) AS "latest_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["LATEST_BY"])


# ================================================== multi-value dimensions
@tpl("mvd", ["mvd"])
def mv_count_tag(s):
    c, v = s.mvd, s.mvd_val()
    return Q(pick(s, f"How many {s.noun()} carry the {v} tag in {c}?",
                  f"Count records tagged {v}"),
             f"""
SELECT COUNT(*) AS "record_count"
FROM {s.ds}
WHERE MV_CONTAINS({c}, {v})
""", must=["MV_CONTAINS"])


@tpl("mvd", ["mvd"])
def mv_group_tags(s):
    c = s.mvd
    return Q(pick(s, f"Count {s.noun()} per value in {c}",
                  f"Break {s.noun()} down by each {c} entry"),
             f"""
SELECT {c} AS "tag",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["GROUP BY 1"])


@tpl("mvd", ["mvd", "metrics"])
def mv_filter_only(s):
    c, m = s.mvd, s.met()
    vals = s.mvd_vals(2)
    return Q(pick(s, f"Total {m} per {c} value, restricted to {vals.replace(chr(39), '')}",
                  f"Sum {m} for only the {vals.replace(chr(39), '')} entries of {c}"),
             f"""
SELECT MV_FILTER_ONLY({c}, ARRAY[{vals}]) AS "tag",
       SUM({m}) AS "total_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["MV_FILTER_ONLY"])


@tpl("mvd", ["mvd", "dims"])
def mv_overlap(s):
    c, d = s.mvd, s.dim()
    vals = s.mvd_vals(2)
    return Q(pick(s, f"Count {s.noun()} per {d} where {c} includes any of {vals.replace(chr(39), '')}",
                  f"Per {d}, how many records overlap the {c} values {vals.replace(chr(39), '')}?"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE MV_OVERLAP({c}, ARRAY[{vals}])
GROUP BY 1
ORDER BY 2 DESC
""", must=["MV_OVERLAP"])


@tpl("mvd", ["mvd"])
def mv_length(s):
    c = s.mvd
    return Q(pick(s, f"Distribution of how many {c} entries each record has",
                  f"Count records by the number of values in {c}"),
             f"""
SELECT MV_LENGTH({c}) AS "tag_count",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["MV_LENGTH"])


@tpl("mvd", ["mvd", "metrics"])
def mv_unnest(s):
    c, m = s.mvd, s.met()
    return Q(pick(s, f"Explode {c} and give the average {m} per tag",
                  f"Unnest {c} then average {m} for each value"),
             f"""
SELECT t.tag AS "tag",
       AVG(d.{m}) AS "avg_{m}"
FROM {s.ds} AS d, UNNEST(MV_TO_ARRAY(d.{c})) AS t(tag)
GROUP BY 1
ORDER BY 2 DESC
""", must=["UNNEST", "MV_TO_ARRAY"],
             trap=INVALID(f"SELECT t.tag, AVG(d.{m}) AS avg_{m} FROM {s.ds} AS d, "
                          f"UNNEST(d.{c}) AS t(tag) GROUP BY 1"))


# ================================================================ JSON strings
@tpl("json_string", ["json"])
def js_group_key(s):
    c, k = s.json, s.json_key()
    return Q(pick(s, f"Break {s.noun()} down by the {k} field inside {c}",
                  f"Count records per {k} value from the {c} payload"),
             f"""
SELECT JSON_VALUE(PARSE_JSON({c}), '$.{k}') AS "{k}",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["PARSE_JSON", "JSON_VALUE"],
             trap=DIFFERENT(f"SELECT JSON_VALUE({c}, '$.{k}') AS {k}, COUNT(*) AS record_count "
                            f"FROM {s.ds} GROUP BY 1 ORDER BY 2 DESC"))


@tpl("json_string", ["json", "metrics"])
def js_filter_key(s):
    c, m = s.json, s.met()
    k = s.json_key()
    v = s.json_val(k)
    return Q(pick(s, f"Average {m} where the {k} field in {c} is {v}",
                  f"Mean {m} for records whose {c} payload has {k} = {v}"),
             f"""
SELECT AVG({m}) AS "avg_{m}"
FROM {s.ds}
WHERE JSON_VALUE(PARSE_JSON({c}), '$.{k}') = {v}
""", must=["PARSE_JSON"],
             trap=DIFFERENT(f"SELECT AVG({m}) AS avg_{m} FROM {s.ds} "
                            f"WHERE JSON_VALUE({c}, '$.{k}') = {v}"))


@tpl("json_string", ["json", "dims"])
def js_two_dims(s):
    c, d = s.json, s.dim()
    k = s.json_key()
    return Q(pick(s, f"Cross-tab {d} against the {k} field inside {c}",
                  f"Counts by {d} and by {k} from the {c} payload"),
             f"""
SELECT {d} AS "{d}",
       JSON_VALUE(PARSE_JSON({c}), '$.{k}') AS "{k}",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1, 2
ORDER BY 3 DESC
""", must=["PARSE_JSON"])


@tpl("json_string", ["json"])
def js_daily_key(s):
    c, k = s.json, s.json_key()
    return Q(pick(s, f"Daily counts split by the {k} value in {c}, last 14 days",
                  f"How does {k} from {c} trend day by day over the last fortnight?"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       JSON_VALUE(PARSE_JSON({c}), '$.{k}') AS "{k}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1, 2
ORDER BY 1, 2
""", must=["PARSE_JSON", "TIME_FLOOR"])


# ==================================================================== lookups
@tpl("lookup", ["lookup"])
def lk_group(s):
    name, col = s.lookup
    return Q(pick(s, f"Count {s.noun()} grouped by the {name} of {col}",
                  f"Break {s.noun()} down using the {name} lookup"),
             f"""
SELECT LOOKUP({col}, '{name}') AS "{name}",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["LOOKUP("],
             trap=INVALID(f"SELECT l.v AS {name}, COUNT(*) AS record_count FROM {s.ds} AS d "
                          f"JOIN {name} AS l ON d.{col} = l.k GROUP BY 1"))


@tpl("lookup", ["lookup", "metrics"])
def lk_metric(s):
    name, col = s.lookup
    m = s.met()
    return Q(pick(s, f"Average {m} per {name}",
                  f"What is the mean {m} for each {name} bucket?"),
             f"""
SELECT LOOKUP({col}, '{name}') AS "{name}",
       AVG({m}) AS "avg_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["LOOKUP("])


@tpl("lookup", ["lookup"])
def lk_daily(s):
    name, col = s.lookup
    return Q(pick(s, f"Daily {s.noun()} counts per {name} for the last 14 days",
                  f"Trend {s.noun()} by {name} over the last fortnight"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       LOOKUP({col}, '{name}') AS "{name}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1, 2
ORDER BY 1, 2
""", must=["LOOKUP("])


# ====================================================================== joins
@tpl("join", ["partner", "metrics"])
def jn_group_partner(s):
    p, local, remote = s.partner()
    pd = p.dim_nonkey()
    m = s.met()
    return Q(pick(s, f"Total {m} by {pd} from {p.ds}",
                  f"Join to {p.ds} and sum {m} per {pd}"),
             f"""
SELECT p.{pd} AS "{pd}",
       SUM(f.{m}) AS "total_{m}"
FROM {s.ds} AS f
INNER JOIN {p.ds} AS p ON f.{local} = p.{remote}
GROUP BY 1
ORDER BY 2 DESC
""", must=["JOIN"])


@tpl("join", ["partner", "metrics"])
def jn_top_n(s):
    p, local, remote = s.partner()
    pd = p.dim_nonkey()
    m = s.met()
    return Q(pick(s, f"Top 10 {pd} values by {m} in the last 30 days",
                  f"Which ten {pd} values lead on {m} this month?"),
             f"""
SELECT p.{pd} AS "{pd}",
       SUM(f.{m}) AS "total_{m}"
FROM {s.ds} AS f
INNER JOIN {p.ds} AS p ON f.{local} = p.{remote}
WHERE f.__time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10
""", must=["JOIN", "LIMIT 10"])


@tpl("join", ["partner", "dims"])
def jn_left(s):
    p, local, remote = s.partner()
    pd = p.dim_nonkey()
    d = s.dim()
    return Q(pick(s, f"Count {s.noun()} per {d}, keeping rows whose {local} is missing from {p.ds}",
                  f"Left join to {p.ds} and count per {d}, including unmatched keys"),
             f"""
SELECT f.{d} AS "{d}",
       COUNT(*) AS "record_count",
       COUNT(p.{pd}) AS "matched_count"
FROM {s.ds} AS f
LEFT JOIN {p.ds} AS p ON f.{local} = p.{remote}
GROUP BY 1
ORDER BY 2 DESC
""", must=["LEFT JOIN"])


@tpl("join", ["partner", "metrics"])
def jn_filter_partner(s):
    p, local, remote = s.partner()
    pd = p.dim_nonkey()
    m = s.met()
    return Q(pick(s, f"Average {m} for rows whose {p.ds} {pd} is {p.lit(pd)}",
                  f"Restrict to {pd} = {p.lit(pd)} in {p.ds} and average {m}"),
             f"""
SELECT AVG(f.{m}) AS "avg_{m}"
FROM {s.ds} AS f
INNER JOIN {p.ds} AS p ON f.{local} = p.{remote}
WHERE p.{pd} = {p.lit(pd)}
""", must=["JOIN"])


@tpl("join", ["partner", "metrics"])
def jn_daily(s):
    p, local, remote = s.partner()
    pd = p.dim_nonkey()
    m = s.met()
    return Q(pick(s, f"Daily {m} per {pd} for the last 14 days",
                  f"Trend {m} by {pd} over the last fortnight"),
             f"""
SELECT TIME_FLOOR(f.__time, 'P1D') AS "day",
       p.{pd} AS "{pd}",
       SUM(f.{m}) AS "total_{m}"
FROM {s.ds} AS f
INNER JOIN {p.ds} AS p ON f.{local} = p.{remote}
WHERE f.__time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1, 2
ORDER BY 1, 2
""", must=["JOIN", "TIME_FLOOR"])


# ========================================================== missing functions
@tpl("missing_function", ["metrics"])
def mf_stddev(s):
    m = s.met()
    return Q(pick(s, f"How spread out is {m}? Give the min, max and mean",
                  f"Summarise the spread of {m} with min, max and average"),
             f"""
SELECT MIN({m}) AS "min_{m}",
       MAX({m}) AS "max_{m}",
       AVG({m}) AS "avg_{m}"
FROM {s.ds}
""", must=["MIN(", "MAX("],
             trap=INVALID(f"SELECT STDDEV({m}) AS stddev_{m} FROM {s.ds}"))


@tpl("missing_function", ["dims"])
def mf_ilike(s):
    d = s.dim()
    pre = s.prefix(d)
    return Q(pick(s, f"Count {s.noun()} whose {d} starts with {pre.strip(chr(39)).rstrip('%')}, case-insensitively",
                  f"Case-insensitive prefix match on {d}"),
             f"""
SELECT COUNT(*) AS "record_count"
FROM {s.ds}
WHERE LOWER({d}) LIKE LOWER({pre})
""", must=["LOWER("],
             trap=INVALID(f"SELECT COUNT(*) AS record_count FROM {s.ds} WHERE {d} ILIKE {pre}"))


@tpl("missing_function", ["dims", "metrics"])
def mf_if(s):
    d, m = s.dim(), s.met()
    v = s.lit(d)
    return Q(pick(s, f"Split {m} into the {v} share and everything else",
                  f"Sum {m} for {d} = {v} versus the rest"),
             f"""
SELECT SUM(CASE WHEN {d} = {v} THEN {m} ELSE 0 END) AS "matched_{m}",
       SUM(CASE WHEN {d} <> {v} THEN {m} ELSE 0 END) AS "other_{m}"
FROM {s.ds}
""", must=["CASE WHEN"],
             trap=INVALID(f"SELECT SUM(IF({d} = {v}, {m}, 0)) AS matched_{m} FROM {s.ds}"))


@tpl("missing_function", [])
def mf_date(s):
    return Q(pick(s, f"Count {s.noun()} per calendar date",
                  f"Daily record counts by date"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"],
             trap=INVALID(f"SELECT DATE(__time) AS \"day\", COUNT(*) AS record_count "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("missing_function", ["dims"])
def mf_to_char(s):
    d = s.dim()
    return Q(pick(s, f"Label each row with the month name and {d}",
                  f"Month label plus {d}, counted"),
             f"""
SELECT TIME_FORMAT(__time, 'MMMM') AS "month_name",
       {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1, 2
ORDER BY 3 DESC
""", must=["TIME_FORMAT"],
             trap=INVALID(f"SELECT TO_CHAR(__time, 'Month') AS month_name, COUNT(*) AS record_count "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("missing_function", ["metrics", "dims"])
def mf_nested_agg(s):
    m, d = s.met(), s.dim()
    return Q(pick(s, f"Highest per-{d} total {m}",
                  f"What is the largest {m} total across all {d} values?"),
             f"""
SELECT MAX("total_{m}") AS "max_total_{m}"
FROM (
  SELECT {d} AS "{d}",
         SUM({m}) AS "total_{m}"
  FROM {s.ds}
  GROUP BY 1
)
""", must=["SELECT"],
             trap=INVALID(f"SELECT MAX(SUM({m})) AS max_total_{m} FROM {s.ds} GROUP BY {d}"))


@tpl("missing_function", ["metrics"])
def mf_top_keyword(s):
    m = s.met()
    d = s.dim() if s.has("dims") else s.hi()
    return Q(pick(s, f"Top 5 {d} values by {m}",
                  f"Give me the five biggest {d} values measured by {m}"),
             f"""
SELECT {d} AS "{d}",
       SUM({m}) AS "total_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
LIMIT 5
""", must=["LIMIT 5"],
             trap=INVALID(f"SELECT TOP 5 {d}, SUM({m}) AS total_{m} FROM {s.ds} GROUP BY {d}"))


# ================================================================= time shift
@tpl("time_shift", ["metrics"])
def sh_yesterday(s):
    m = s.met()
    return Q(pick(s, f"Compare today's average {m} against the same window a day earlier",
                  f"Average {m} for the last 24 hours and for the 24 hours before that"),
             f"""
SELECT AVG(CASE WHEN __time >= CURRENT_TIMESTAMP - INTERVAL '1' DAY
                THEN {m} END) AS "current_avg_{m}",
       AVG(CASE WHEN __time < CURRENT_TIMESTAMP - INTERVAL '1' DAY
                THEN {m} END) AS "prior_avg_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
""", must=["CASE WHEN"])


@tpl("time_shift", [])
def sh_shift_day(s):
    return Q(pick(s, f"For each record in the last 3 days show the timestamp shifted back one day",
                  f"List event times alongside the same moment a day earlier, last three days"),
             f"""
SELECT __time AS "event_time",
       TIME_SHIFT(__time, 'P1D', -1) AS "same_time_yesterday"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '3' DAY
ORDER BY __time DESC
LIMIT 20
""", must=["TIME_SHIFT"])


@tpl("time_shift", ["metrics"])
def sh_week_over_week(s):
    m = s.met()
    return Q(pick(s, f"Week-over-week totals of {m} bucketed by day",
                  f"Daily {m} for this week shown next to the day one week earlier"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       SUM({m}) AS "total_{m}",
       TIME_SHIFT(TIME_FLOOR(__time, 'P1D'), 'P1W', -1) AS "week_earlier"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_SHIFT"])


@tpl("time_shift", ["dims"])
def sh_shift_filter(s):
    d = s.dim()
    return Q(pick(s, f"Count {s.noun()} per {d} in the 24 hours ending one day ago",
                  f"Per {d}, counts for the day-before-yesterday window"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE __time >= TIME_SHIFT(CURRENT_TIMESTAMP, 'P1D', -2)
  AND __time < TIME_SHIFT(CURRENT_TIMESTAMP, 'P1D', -1)
GROUP BY 1
ORDER BY 2 DESC
""", must=["TIME_SHIFT"])


# =================================================================== grouping
@tpl("grouping", ["two_dims"])
def gr_two_dims(s):
    a, b = s.dims(2)
    return Q(pick(s, f"Cross-tab {s.noun()} by {a} and {b}",
                  f"Counts for every combination of {a} and {b}"),
             f"""
SELECT {a} AS "{a}",
       {b} AS "{b}",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1, 2
ORDER BY 3 DESC
""", must=["GROUP BY 1, 2"],
             trap=INVALID(f"SELECT {a} AS grp_a, {b} AS grp_b, COUNT(*) AS record_count "
                          f"FROM {s.ds} GROUP BY grp_a, grp_b"))


@tpl("grouping", ["dims", "metrics"])
def gr_having(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Which {d} values have more than 5 {s.noun()}? Include their average {m}",
                  f"Only {d} groups above 5 records, with mean {m}"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count",
       AVG({m}) AS "avg_{m}"
FROM {s.ds}
GROUP BY 1
HAVING COUNT(*) > 5
ORDER BY 2 DESC
""", must=["HAVING"])


@tpl("grouping", ["dims", "metrics"])
def gr_ratio(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Average {m} per record for each {d}, computed as a ratio",
                  f"For each {d}, divide total {m} by the record count"),
             f"""
SELECT {d} AS "{d}",
       SUM({m}) * 1.0 / COUNT(*) AS "{m}_per_record"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["COUNT(*)"])


@tpl("grouping", ["dims"])
def gr_case_bucket(s):
    d = s.dim()
    v1, v2 = s.lit(d), s.lit(d)
    return Q(pick(s, f"Group {s.noun()} into {v1}, {v2} and other by {d}",
                  f"Bucket {d} into three groups and count each"),
             f"""
SELECT CASE WHEN {d} = {v1} THEN 'group_one'
            WHEN {d} = {v2} THEN 'group_two'
            ELSE 'other' END AS "bucket",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["CASE WHEN"])


@tpl("grouping", ["dims", "metrics"])
def gr_order_by_ordinal(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Rank {d} by total {m}, largest first",
                  f"Order {d} groups by their {m} total"),
             f"""
SELECT {d} AS "{d}",
       SUM({m}) AS "total_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["ORDER BY 2 DESC"],
             trap=INVALID(f"SELECT {d} AS grp, SUM({m}) AS total FROM {s.ds} GROUP BY grp ORDER BY total DESC"))


@tpl("grouping", ["dims"])
def gr_in_list(s):
    d = s.dim()
    vals = s.lits(d, 3)
    return Q(pick(s, f"Counts for {d} in ({vals.replace(chr(39), '')})",
                  f"Restrict {d} to {vals.replace(chr(39), '')} and count each"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE {d} IN ({vals})
GROUP BY 1
ORDER BY 2 DESC
""", must=["IN ("])


@tpl("grouping", ["two_dims", "metrics"])
def gr_subquery(s):
    a, b = s.dims(2)
    m = s.met()
    return Q(pick(s, f"Average of the per-{b} totals of {m}, for each {a}",
                  f"Roll {m} up by {b} first, then average those totals per {a}"),
             f"""
SELECT "{a}",
       AVG("total_{m}") AS "avg_group_total"
FROM (
  SELECT {a} AS "{a}",
         {b} AS "{b}",
         SUM({m}) AS "total_{m}"
  FROM {s.ds}
  GROUP BY 1, 2
)
GROUP BY 1
ORDER BY 2 DESC
""", must=["GROUP BY 1, 2"])


# ============================================================= filtered aggs
@tpl("filtered_agg", ["dims"])
def fa_count_filter(s):
    d = s.dim()
    v = s.lit(d)
    return Q(pick(s, f"Total {s.noun()} and how many of those had {d} = {v}",
                  f"Overall count plus the {v} subset in one row"),
             f"""
SELECT COUNT(*) AS "record_count",
       COUNT(*) FILTER (WHERE {d} = {v}) AS "matched_count"
FROM {s.ds}
""", must=["FILTER"])


@tpl("filtered_agg", ["dims", "metrics"])
def fa_share(s):
    d, m = s.dim(), s.met()
    v = s.lit(d)
    return Q(pick(s, f"What share of {m} comes from {d} = {v}?",
                  f"Percentage of total {m} attributable to {v}"),
             f"""
SELECT SUM({m}) FILTER (WHERE {d} = {v}) * 100.0 / SUM({m}) AS "pct_of_{m}"
FROM {s.ds}
""", must=["FILTER"])


@tpl("filtered_agg", ["dims", "metrics"])
def fa_by_grain(s):
    d, m = s.dim(), s.met()
    v = s.lit(d)
    return Q(pick(s, f"Daily {m} split into the {v} part and the total, last 14 days",
                  f"Per day, total {m} and the {v} slice of it"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       SUM({m}) AS "total_{m}",
       SUM({m}) FILTER (WHERE {d} = {v}) AS "{v.strip(chr(39))}_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 1
""", must=["FILTER", "TIME_FLOOR"])


@tpl("filtered_agg", ["two_dims"])
def fa_two_filters(s):
    a, b = s.dims(2)
    va, vb = s.lit(a), s.lit(b)
    return Q(pick(s, f"Counts for {a} = {va} and for {b} = {vb} side by side",
                  f"Two filtered counts in a single row"),
             f"""
SELECT COUNT(*) FILTER (WHERE {a} = {va}) AS "count_a",
       COUNT(*) FILTER (WHERE {b} = {vb}) AS "count_b",
       COUNT(*) AS "record_count"
FROM {s.ds}
""", must=["FILTER"])


# ================================================================ string ops
@tpl("string_ops", ["dims"])
def so_like(s):
    d = s.dim()
    pre = s.prefix(d)
    return Q(pick(s, f"Count {s.noun()} whose {d} matches {pre}",
                  f"How many rows have {d} like {pre}?"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) AS "record_count"
FROM {s.ds}
WHERE {d} LIKE {pre}
GROUP BY 1
ORDER BY 2 DESC
""", must=["LIKE"])


@tpl("string_ops", ["two_dims"])
def so_concat(s):
    a, b = s.dims(2)
    return Q(pick(s, f"Combine {a} and {b} into one label and count each",
                  f"Counts per {a} and {b} joined with a slash"),
             f"""
SELECT CONCAT({a}, '/', {b}) AS "combined",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
LIMIT 25
""", must=["CONCAT"])


@tpl("string_ops", ["dims"])
def so_upper(s):
    d = s.dim()
    return Q(pick(s, f"Uppercase {d} and count each value",
                  f"Counts per {d}, normalised to upper case"),
             f"""
SELECT UPPER({d}) AS "{d}_upper",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["UPPER("])


@tpl("string_ops", ["dims"])
def so_substring(s):
    d = s.dim()
    return Q(pick(s, f"Group {s.noun()} by the first three characters of {d}",
                  f"Counts by the {d} prefix, first three characters"),
             f"""
SELECT SUBSTRING({d}, 1, 3) AS "{d}_prefix",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["SUBSTRING"])


@tpl("string_ops", ["hi_card"])
def so_length(s):
    h = s.hi()
    return Q(pick(s, f"Distribution of {h} string lengths",
                  f"Count rows by how long {h} is"),
             f"""
SELECT LENGTH({h}) AS "id_length",
       COUNT(*) AS "record_count"
FROM {s.ds}
GROUP BY 1
ORDER BY 1
""", must=["LENGTH("])


# ============================================================ null and math
@tpl("null_math", ["dims", "metrics"])
def nm_nullif(s):
    d, m = s.dim(), s.met()
    v = s.lit(d)
    return Q(pick(s, f"Ratio of {v} {s.noun()} to all others, guarding against divide by zero",
                  f"Safe ratio of the {v} count to the rest"),
             f"""
SELECT COUNT(*) FILTER (WHERE {d} = {v}) * 1.0
         / NULLIF(COUNT(*) FILTER (WHERE {d} <> {v}), 0) AS "ratio"
FROM {s.ds}
""", must=["NULLIF"])


@tpl("null_math", ["dims", "metrics"])
def nm_coalesce(s):
    d, m = s.dim(), s.met()
    return Q(pick(s, f"Average {m} per {d}, showing zero where there is nothing to average",
                  f"Mean {m} by {d} with nulls replaced by zero"),
             f"""
SELECT {d} AS "{d}",
       COALESCE(AVG({m}), 0) AS "avg_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["COALESCE"])


@tpl("null_math", ["two_metrics"])
def nm_ratio_metrics(s):
    a, b = s.mets(2)
    return Q(pick(s, f"Ratio of {a} to {b} per day for the last 7 days",
                  f"Daily {a} over {b}"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       SUM({a}) * 1.0 / NULLIF(SUM({b}), 0) AS "ratio"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=["NULLIF"])


@tpl("null_math", ["metrics"])
def nm_round(s):
    m = s.met()
    return Q(pick(s, f"Average {m} rounded to two decimal places, per day, last week",
                  f"Daily mean {m} to 2dp"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       ROUND(AVG({m}), 2) AS "avg_{m}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=["ROUND("])


@tpl("null_math", ["metrics", "dims"])
def nm_cast(s):
    m, d = s.met(), s.dim()
    return Q(pick(s, f"Total {m} per {d} as a whole number",
                  f"Sum {m} by {d}, cast to an integer"),
             f"""
SELECT {d} AS "{d}",
       CAST(SUM({m}) AS BIGINT) AS "total_{m}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=["CAST("])


# ========================================================== reserved columns
# `value` and `language` are the two column names in the index that Druid
# refuses unquoted; every other name in all 69 schemas parses bare. These
# templates are the only ones that bind to them, and they always double-quote.
@tpl("reserved_column", ["reserved_num", "dims"])
def rc_agg(s):
    c, d = s.reserved_numeric(), s.dim()
    return Q(pick(s, f"Average {c} per {d}",
                  f"What is the mean {c} for each {d}?",
                  f"Break the {c} column down by {d}"),
             f"""
SELECT {d} AS "{d}",
       AVG("{c}") AS "avg_{c}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=[f'"{c}"'],
             trap=INVALID(f'SELECT {d}, AVG({c}) AS avg_{c} FROM {s.ds} GROUP BY 1'))


@tpl("reserved_column", ["reserved_num"])
def rc_bucket(s):
    c = s.reserved_numeric()
    return Q(pick(s, f"Daily maximum {c} for the last 14 days",
                  f"Peak {c} per day over the last fortnight"),
             f"""
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       MAX("{c}") AS "max_{c}"
FROM {s.ds}
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 1
""", must=[f'"{c}"', "TIME_FLOOR"],
             trap=INVALID(f"SELECT TIME_FLOOR(__time, 'P1D') AS \"day\", MAX({c}) AS max_{c} "
                          f"FROM {s.ds} GROUP BY 1"))


@tpl("reserved_column", ["reserved", "dims"])
def rc_filter(s):
    c, d = s.reserved(), s.dim()
    return Q(pick(s, f"Count {s.noun()} per {d} where {c} is present",
                  f"Per {d}, how many rows have a non-null {c}?"),
             f"""
SELECT {d} AS "{d}",
       COUNT(*) FILTER (WHERE "{c}" IS NOT NULL) AS "with_{c}"
FROM {s.ds}
GROUP BY 1
ORDER BY 2 DESC
""", must=[f'"{c}"'],
             trap=INVALID(f"SELECT {d}, COUNT(*) FILTER (WHERE {c} IS NOT NULL) AS with_{c} "
                          f"FROM {s.ds} GROUP BY 1"))
