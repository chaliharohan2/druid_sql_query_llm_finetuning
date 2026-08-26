"""Review batch: ~55 hand-authored examples covering every quirk cluster.

Each entry is authored here, then validated by dataset/validate.py against the live
Druid 35.0.0 cluster before it is allowed into the SFT file.

House style (see druid_dataset_creation.md):
  - output aliases are ALWAYS double-quoted (never wrong; `AS hour` is a syntax error)
  - GROUP BY / ORDER BY use ordinals, never select aliases
  - relative time resolves against CURRENT_TIMESTAMP
  - approximate aggregates by default

`trap.naive_sql` is the standard-SQL reflex. It is validated to fail (or to differ)
and is NEVER shown to the model.
"""

E = []


def ex(id, cluster, schemas, question, sql, must=(), rows=True, trap=None, note=None):
    E.append({"id": id, "cluster": cluster, "schemas": list(schemas), "question": question,
              "sql": sql.strip(), "gates": {"must_contain": list(must), "expect_rows": rows},
              "trap": trap, "note": note})


# ---------------------------------------------------------------- time bucketing
ex("tb_01", "time_bucketing", ["web_events"],
   "Show hourly average latency for checkout events over the last 7 days",
   """
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour",
       AVG(latency_ms) AS "avg_latency_ms"
FROM ds_web_events
WHERE event_type = 'checkout'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"])

ex("tb_02", "time_bucketing", ["web_events"],
   "Daily revenue for the past 30 days",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       SUM(revenue_usd) AS "revenue_usd"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"])

ex("tb_03", "time_bucketing", ["telco_cdr"],
   "Bucket call records into 5 minute windows for the last 6 hours and count them",
   """
SELECT TIME_FLOOR(__time, 'PT5M') AS "bucket",
       COUNT(*) AS "cdr_count"
FROM ds_telco_cdr
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '6' HOUR
GROUP BY 1
ORDER BY 1
""", must=["PT5M"])

ex("tb_04", "time_bucketing", ["game_sessions"],
   "How many sessions started each week?",
   """
SELECT DATE_TRUNC('week', __time) AS "week_start",
       COUNT(*) AS "session_count"
FROM ds_game_sessions
GROUP BY 1
ORDER BY 1
""", must=["DATE_TRUNC"])

ex("tb_05", "time_bucketing", ["iot_readings"],
   "Count distinct reporting devices per hour for the last 2 days",
   """
SELECT FLOOR(__time TO HOUR) AS "hour",
       APPROX_COUNT_DISTINCT(device_id) AS "active_devices"
FROM ds_iot_readings
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
GROUP BY 1
ORDER BY 1
""", must=["FLOOR"])

ex("tb_06", "time_bucketing", ["telco_cdr"],
   "Monthly rated cost broken down by plan tier",
   """
SELECT TIME_FLOOR(__time, 'P1M') AS "month",
       plan_tier,
       SUM(rated_cost_usd) AS "rated_cost_usd"
FROM ds_telco_cdr
GROUP BY 1, 2
ORDER BY 1, 2
""", must=["P1M"])

# ---------------------------------------------------------------- relative time
ex("rt_01", "relative_time", ["sec_alerts"],
   "How many alerts fired in the last 24 hours, by severity?",
   """
SELECT severity,
       COUNT(*) AS "alert_count"
FROM ds_sec_alerts
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY 1
ORDER BY 2 DESC
""", must=["CURRENT_TIMESTAMP"],
   trap={"naive_sql": "SELECT severity, COUNT(*) AS alert_count FROM ds_sec_alerts "
                      "WHERE __time >= NOW() - INTERVAL '24' HOUR GROUP BY 1",
         "expect": "INVALID", "note": "NOW() does not exist in Druid"})

ex("rt_02", "relative_time", ["orders"],
   "How many orders were placed yesterday?",
   """
SELECT COUNT(*) AS "order_count"
FROM ds_orders
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
""", must=["TIME_FLOOR", "CURRENT_TIMESTAMP"])

ex("rt_03", "relative_time", ["web_events"],
   "Error rate over the last 90 minutes",
   """
SELECT COUNT(*) FILTER (WHERE status_code >= 500) * 1.0 / COUNT(*) AS "error_rate"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '90' MINUTE
""", must=["INTERVAL '90' MINUTE"])

ex("rt_04", "relative_time", ["fin_txn"],
   "Total approved transaction volume so far this month",
   """
SELECT SUM(amountMinor) / 100.0 AS "approved_volume"
FROM ds_fin_txn
WHERE authResult = 'approved'
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
""", must=["TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')"])

ex("rt_05", "relative_time", ["ad_impressions"],
   "Impressions per day for the last 7 complete days, excluding today",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       COUNT(*) AS "impressions"
FROM ds_ad_impressions
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '7' DAY
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 1
""", must=["TIME_FLOOR"])

# ---------------------------------------------------------------- extract / format
ex("tf_01", "time_extract_format", ["web_events"],
   "What is the hour-of-day traffic profile?",
   """
SELECT TIME_EXTRACT(__time, 'HOUR') AS "hour_of_day",
       COUNT(*) AS "event_count"
FROM ds_web_events
GROUP BY 1
ORDER BY 1
""", must=["TIME_EXTRACT"])

ex("tf_02", "time_extract_format", ["game_sessions"],
   "Which day of the week has the most play sessions?",
   """
SELECT TIME_EXTRACT(__time, 'DOW') AS "day_of_week",
       COUNT(*) AS "session_count"
FROM ds_game_sessions
GROUP BY 1
ORDER BY 2 DESC
""", must=["TIME_EXTRACT"],
   trap={"naive_sql": "SELECT DAYOFWEEK(__time) AS dow, COUNT(*) FROM ds_game_sessions GROUP BY 1",
         "expect": "INVALID", "note": "DAYOFWEEK is not a Druid function"})

ex("tf_03", "time_extract_format", ["telco_cdr"],
   "Daily call volume labelled with dates in Asia/Kolkata local time",
   """
SELECT TIME_FORMAT(__time, 'yyyy-MM-dd', 'Asia/Kolkata') AS "local_date",
       COUNT(*) AS "call_count"
FROM ds_telco_cdr
GROUP BY 1
ORDER BY 1
""", must=["TIME_FORMAT"],
   trap={"naive_sql": "SELECT TO_CHAR(__time, 'YYYY-MM-DD') AS local_date, COUNT(*) "
                      "FROM ds_telco_cdr GROUP BY 1",
         "expect": "INVALID", "note": "TO_CHAR does not exist; use TIME_FORMAT"})

ex("tf_04", "time_extract_format", ["ad_impressions"],
   "Bucket impressions by hour of day in the America/New_York timezone",
   """
SELECT TIME_EXTRACT(__time, 'HOUR', 'America/New_York') AS "ny_hour",
       COUNT(*) AS "impressions"
FROM ds_ad_impressions
GROUP BY 1
ORDER BY 1
""", must=["America/New_York"])

# ---------------------------------------------------------------- epoch columns
ex("ep_01", "epoch_time_column", ["web_events"],
   "Average latency bucketed by the hour the client started the request, last 3 days",
   """
SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP(request_started_at_ms), 'PT1H') AS "request_hour",
       AVG(latency_ms) AS "avg_latency_ms"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '3' DAY
GROUP BY 1
ORDER BY 1
""", must=["MILLIS_TO_TIMESTAMP"],
   trap={"naive_sql": "SELECT TIME_FLOOR(request_started_at_ms, 'PT1H') AS request_hour, "
                      "AVG(latency_ms) FROM ds_web_events GROUP BY 1",
         "expect": "INVALID", "note": "request_started_at_ms is BIGINT, not a TIMESTAMP"})

ex("ep_02", "epoch_time_column", ["ad_impressions"],
   "Daily impression counts based on the ad server timestamp rather than log arrival time",
   """
SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP(served_at_epoch_s * 1000), 'P1D') AS "served_day",
       COUNT(*) AS "impressions"
FROM ds_ad_impressions
GROUP BY 1
ORDER BY 1
""", must=["served_at_epoch_s * 1000"],
   trap={"naive_sql": "SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP(served_at_epoch_s), 'P1D') AS served_day, "
                      "COUNT(*) FROM ds_ad_impressions GROUP BY 1",
         "expect": "DIFFERENT",
         "note": "served_at_epoch_s is SECONDS; without *1000 the dates land in 1970"})

ex("ep_03", "epoch_time_column", ["web_events"],
   "Average queueing delay between client request start and server log time",
   """
SELECT AVG(TIMESTAMP_TO_MILLIS(__time) - request_started_at_ms) AS "avg_queue_ms"
FROM ds_web_events
""", must=["TIMESTAMP_TO_MILLIS"])

ex("ep_04", "epoch_time_column", ["ad_impressions"],
   "Clicks in the last day according to the ad server clock",
   """
SELECT COUNT(*) AS "clicks"
FROM ds_ad_impressions
WHERE was_clicked = 1
  AND MILLIS_TO_TIMESTAMP(served_at_epoch_s * 1000) >= CURRENT_TIMESTAMP - INTERVAL '1' DAY
""", must=["MILLIS_TO_TIMESTAMP"])

# ---------------------------------------------------------------- string time columns
ex("st_01", "string_time_column", ["iot_readings"],
   "Average sensor value per hour based on when the device took the reading",
   """
SELECT TIME_FLOOR(TIME_PARSE(reading_taken_at, 'yyyy-MM-dd HH:mm:ss'), 'PT1H') AS "reading_hour",
       AVG("value") AS "avg_value"
FROM ds_iot_readings
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
GROUP BY 1
ORDER BY 1
""", must=["TIME_PARSE", '"value"'],
   trap={"naive_sql": "SELECT TIME_FLOOR(reading_taken_at, 'PT1H') AS reading_hour, AVG(value) "
                      "FROM ds_iot_readings GROUP BY 1",
         "expect": "INVALID",
         "note": "reading_taken_at is VARCHAR and `value` is a reserved word"})

ex("st_02", "string_time_column", ["fin_txn"],
   "How many transactions settled each day?",
   """
SELECT TIME_FLOOR(TIME_PARSE(settledAt, 'yyyy-MM-dd''T''HH:mm:ss''Z'''), 'P1D') AS "settle_day",
       COUNT(*) AS "txn_count"
FROM ds_fin_txn
GROUP BY 1
ORDER BY 1
""", must=["TIME_PARSE"])

ex("st_03", "string_time_column", ["fin_txn"],
   "Average settlement lag in hours by card network",
   """
SELECT cardNetwork,
       AVG(TIMESTAMPDIFF(HOUR, __time, TIME_PARSE(settledAt, 'yyyy-MM-dd''T''HH:mm:ss''Z'''))) AS "avg_lag_hours"
FROM ds_fin_txn
GROUP BY 1
ORDER BY 2 DESC
""", must=["TIMESTAMPDIFF", "TIME_PARSE"],
   trap={"naive_sql": "SELECT cardNetwork, AVG(DATEDIFF(hour, __time, settledAt)) AS avg_lag_hours "
                      "FROM ds_fin_txn GROUP BY 1",
         "expect": "INVALID", "note": "DATEDIFF does not exist; use TIMESTAMPDIFF"})

# ---------------------------------------------------------------- ORDER BY restriction
ex("ob_01", "order_by_restriction", ["web_events"],
   "Show me the 10 slowest requests",
   """
SELECT session_id,
       page_path,
       MAX(latency_ms) AS "latency_ms"
FROM ds_web_events
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 10
""", must=["GROUP BY"],
   trap={"naive_sql": "SELECT session_id, page_path, latency_ms FROM ds_web_events "
                      "ORDER BY latency_ms DESC LIMIT 10",
         "expect": "INVALID",
         "note": "a table scan can only ORDER BY __time; LIMIT does not rescue it"})

ex("ob_02", "order_by_restriction", ["sec_alerts"],
   "List the 20 most recent alerts with their rule and severity",
   """
SELECT __time,
       rule_name,
       severity
FROM ds_sec_alerts
ORDER BY __time DESC
LIMIT 20
""", must=["ORDER BY __time"],
   trap={"naive_sql": "SELECT __time, rule_name, severity FROM ds_sec_alerts "
                      "ORDER BY severity DESC LIMIT 20",
         "expect": "INVALID", "note": "severity is not __time and there is no GROUP BY"})

ex("ob_03", "order_by_restriction", ["orders"],
   "Rank customers by how much they spent, highest first",
   """
SELECT customer_id,
       SUM(quantity * unit_price_usd - discount_usd) AS "net_spend_usd"
FROM ds_orders
GROUP BY 1
ORDER BY 2 DESC
LIMIT 25
""", must=["GROUP BY"],
   trap={"naive_sql": "SELECT customer_id, quantity * unit_price_usd - discount_usd AS net_spend_usd "
                      "FROM ds_orders ORDER BY net_spend_usd DESC LIMIT 25",
         "expect": "INVALID", "note": "ordering a scan by a computed non-time column"})

ex("ob_04", "order_by_restriction", ["telco_cdr"],
   "Which cells had the worst average setup latency? Top 15.",
   """
SELECT cell_id,
       AVG(setup_latency_ms) AS "avg_setup_latency_ms",
       COUNT(*) AS "cdr_count"
FROM ds_telco_cdr
GROUP BY 1
ORDER BY 2 DESC
LIMIT 15
""", must=["ORDER BY 2 DESC"])

ex("ob_05", "order_by_restriction", ["iot_readings"],
   "Show the highest readings first, just the top 5 rows",
   """
SELECT device_id,
       metric_name,
       MAX("value") AS "max_value"
FROM ds_iot_readings
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 5
""", must=["GROUP BY"],
   trap={"naive_sql": 'SELECT device_id, metric_name, "value" FROM ds_iot_readings '
                      'ORDER BY "value" DESC LIMIT 5',
         "expect": "INVALID", "note": "scan ordered by a non-time column"})

# ---------------------------------------------------------------- reserved aliases
ex("ra_01", "reserved_alias", ["web_events"],
   "Give me a count of events per day labelled as day and count",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       COUNT(*) AS "count"
FROM ds_web_events
GROUP BY 1
ORDER BY 1
""", must=['AS "day"', 'AS "count"'],
   trap={"naive_sql": "SELECT TIME_FLOOR(__time, 'P1D') AS day, COUNT(*) AS count "
                      "FROM ds_web_events GROUP BY 1",
         "expect": "INVALID", "note": "day and count are reserved words and must be quoted"})

ex("ra_02", "reserved_alias", ["iot_readings"],
   "Average value per metric, with the column called value",
   """
SELECT metric_name,
       AVG("value") AS "value"
FROM ds_iot_readings
GROUP BY 1
ORDER BY 2 DESC
""", must=['AVG("value")'],
   trap={"naive_sql": "SELECT metric_name, AVG(value) AS value FROM ds_iot_readings GROUP BY 1",
         "expect": "INVALID", "note": "`value` is reserved both as a column reference and as an alias"})

ex("ra_03", "reserved_alias", ["fin_txn"],
   "Approved transaction count per hour, with columns named hour and timestamp",
   """
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour",
       MIN(__time) AS "timestamp",
       COUNT(*) AS "approved_count"
FROM ds_fin_txn
WHERE authResult = 'approved'
GROUP BY 1
ORDER BY 1
""", must=['AS "hour"', 'AS "timestamp"'])

# ---------------------------------------------------------------- timestamp literals
ex("tl_01", "timestamp_literal", ["orders"],
   "Orders placed between 2026-08-01 and 2026-08-08",
   """
SELECT COUNT(*) AS "order_count"
FROM ds_orders
WHERE __time >= TIMESTAMP '2026-08-01 00:00:00'
  AND __time < TIMESTAMP '2026-08-08 00:00:00'
""", must=["TIMESTAMP '2026-08-01 00:00:00'"], rows=False,
   trap={"naive_sql": "SELECT COUNT(*) FROM ds_orders "
                      "WHERE __time >= TIMESTAMP '2026-08-01T00:00:00Z' "
                      "AND __time < TIMESTAMP '2026-08-08T00:00:00Z'",
         "expect": "INVALID", "note": "ISO-8601 with T/Z is rejected in a TIMESTAMP literal"})

ex("tl_02", "timestamp_literal", ["telco_cdr"],
   "Count call records inside the interval 2026-08-01 to 2026-08-15",
   """
SELECT COUNT(*) AS "cdr_count"
FROM ds_telco_cdr
WHERE TIME_IN_INTERVAL(__time, '2026-08-01/2026-08-15')
""", must=["TIME_IN_INTERVAL"], rows=False)

# ---------------------------------------------------------------- approximate aggregates
ex("aa_01", "approx_agg", ["web_events"],
   "How many unique users visited in the last 7 days?",
   """
SELECT APPROX_COUNT_DISTINCT(user_id) AS "unique_users"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
""", must=["APPROX_COUNT_DISTINCT"])

ex("aa_02", "approx_agg", ["web_events"],
   "What is p95 latency by device type?",
   """
SELECT device_type,
       APPROX_QUANTILE_DS(latency_ms, 0.95) AS "p95_latency_ms"
FROM ds_web_events
GROUP BY 1
ORDER BY 2 DESC
""", must=["APPROX_QUANTILE_DS"])

ex("aa_03", "approx_agg", ["telco_cdr"],
   "Give me p50, p95 and p99 call setup latency per network generation",
   """
SELECT network_gen,
       APPROX_QUANTILE_DS(setup_latency_ms, 0.50) AS "p50_ms",
       APPROX_QUANTILE_DS(setup_latency_ms, 0.95) AS "p95_ms",
       APPROX_QUANTILE_DS(setup_latency_ms, 0.99) AS "p99_ms"
FROM ds_telco_cdr
GROUP BY 1
ORDER BY 1
""", must=["APPROX_QUANTILE_DS"],
   trap={"naive_sql": "SELECT network_gen, MEDIAN(setup_latency_ms) AS p50_ms "
                      "FROM ds_telco_cdr GROUP BY 1",
         "expect": "INVALID", "note": "MEDIAN does not exist in Druid"})

ex("aa_04", "approx_agg", ["ad_impressions"],
   "Unique creatives served per campaign",
   """
SELECT campaign_name,
       APPROX_COUNT_DISTINCT(creative_id) AS "unique_creatives"
FROM ds_ad_impressions
GROUP BY 1
ORDER BY 2 DESC
""", must=["APPROX_COUNT_DISTINCT"])

# ---------------------------------------------------------------- LATEST / EARLIEST
ex("le_01", "latest_earliest", ["sec_alerts"],
   "What is the most recent analyst verdict for each host?",
   """
SELECT host_id,
       LATEST(analyst_verdict, 32) AS "latest_verdict"
FROM ds_sec_alerts
GROUP BY 1
ORDER BY 1
LIMIT 50
""", must=["LATEST("])

ex("le_02", "latest_earliest", ["iot_readings"],
   "First firmware version we ever saw for each device",
   """
SELECT device_id,
       EARLIEST(firmware, 16) AS "first_firmware"
FROM ds_iot_readings
GROUP BY 1
ORDER BY 1
LIMIT 50
""", must=["EARLIEST("])

ex("le_03", "latest_earliest", ["telco_cdr"],
   "Latest contract status per subscriber, ranked by their total rated cost",
   """
SELECT subscriber_id,
       LATEST_BY(contract_status, __time, 16) AS "current_status",
       SUM(rated_cost_usd) AS "total_cost_usd"
FROM ds_telco_cdr
GROUP BY 1
ORDER BY 3 DESC
LIMIT 20
""", must=["LATEST_BY"])

# ---------------------------------------------------------------- multi-value dimensions
ex("mv_01", "mvd", ["web_events"],
   "How many events were in the new_checkout experiment?",
   """
SELECT COUNT(*) AS "event_count"
FROM ds_web_events
WHERE MV_CONTAINS(experiment_tags, 'new_checkout')
""", must=["MV_CONTAINS"])

ex("mv_02", "mvd", ["sec_alerts"],
   "Count alerts per MITRE tag",
   """
SELECT t.tag AS "tag",
       COUNT(*) AS "alert_count"
FROM ds_sec_alerts, UNNEST(MV_TO_ARRAY(alert_tags)) AS t(tag)
GROUP BY 1
ORDER BY 2 DESC
""", must=["UNNEST", "MV_TO_ARRAY"],
   trap={"naive_sql": "SELECT t.tag, COUNT(*) FROM ds_sec_alerts, UNNEST(alert_tags) AS t(tag) GROUP BY 1",
         "expect": "INVALID", "note": "UNNEST needs an ARRAY; wrap the MVD in MV_TO_ARRAY"})

ex("mv_03", "mvd", ["ad_impressions"],
   "Average win price for video creatives",
   """
SELECT AVG(win_price_usd) AS "avg_win_price_usd"
FROM ds_ad_impressions
WHERE MV_CONTAINS(creative_tags, 'video')
  AND win_price_usd > 0
""", must=["MV_CONTAINS"])

ex("mv_04", "mvd", ["web_events"],
   "Revenue per experiment tag, restricted to the price_test and banner_v2 experiments",
   """
SELECT t.tag AS "experiment",
       SUM(revenue_usd) AS "revenue_usd"
FROM ds_web_events, UNNEST(MV_TO_ARRAY(experiment_tags)) AS t(tag)
WHERE t.tag IN ('price_test', 'banner_v2')
GROUP BY 1
ORDER BY 2 DESC
""", must=["UNNEST"])

# ---------------------------------------------------------------- JSON as string
ex("js_01", "json_string", ["web_events"],
   "Break down events by the tier field inside the attributes payload",
   """
SELECT JSON_VALUE(PARSE_JSON(attrs_json), '$.tier') AS "tier",
       COUNT(*) AS "event_count"
FROM ds_web_events
GROUP BY 1
ORDER BY 2 DESC
""", must=["PARSE_JSON"],
   trap={"naive_sql": "SELECT JSON_VALUE(attrs_json, '$.tier') AS tier, COUNT(*) "
                      "FROM ds_web_events GROUP BY 1",
         "expect": "DIFFERENT",
         "note": "runs fine but returns NULL for every row -- the string is never parsed"})

ex("js_02", "json_string", ["sec_alerts"],
   "Alert counts by environment from the enrichment payload, critical only",
   """
SELECT JSON_VALUE(PARSE_JSON(enrichment_json), '$.env') AS "env",
       COUNT(*) AS "alert_count"
FROM ds_sec_alerts
WHERE severity = 'critical'
GROUP BY 1
ORDER BY 2 DESC
""", must=["PARSE_JSON"])

ex("js_03", "json_string", ["sec_alerts"],
   "How many alerts are on hosts whose patch level is not current?",
   """
SELECT COUNT(*) AS "alert_count"
FROM ds_sec_alerts
WHERE JSON_VALUE(TRY_PARSE_JSON(enrichment_json), '$.patch_level') <> 'current'
""", must=["TRY_PARSE_JSON"])

# ---------------------------------------------------------------- lookups
ex("lk_01", "lookup", ["sec_alerts"],
   "Alert counts by host criticality tier",
   """
SELECT LOOKUP(host_id, 'host_tier') AS "tier",
       COUNT(*) AS "alert_count"
FROM ds_sec_alerts
GROUP BY 1
ORDER BY 2 DESC
""", must=["LOOKUP("])

ex("lk_02", "lookup", ["sec_alerts"],
   "Critical alerts on gold-tier hosts in the last 14 days",
   """
SELECT rule_name,
       COUNT(*) AS "alert_count"
FROM ds_sec_alerts
WHERE severity = 'critical'
  AND LOOKUP(host_id, 'host_tier') = 'gold'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 2 DESC
""", must=["LOOKUP("])

ex("lk_03", "lookup", ["sec_alerts"],
   "Bytes transferred per tier per day",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       LOOKUP(host_id, 'host_tier') AS "tier",
       SUM(bytes_transferred) AS "bytes_transferred"
FROM ds_sec_alerts
GROUP BY 1, 2
ORDER BY 1, 2
""", must=["LOOKUP(", "TIME_FLOOR"])

# ---------------------------------------------------------------- joins
ex("jn_01", "join", ["orders", "products"],
   "Revenue by product category",
   """
SELECT p.category AS "category",
       SUM(o.quantity * o.unit_price_usd - o.discount_usd) AS "net_revenue_usd"
FROM ds_orders o
JOIN ds_products p ON o.product_sku = p.sku
GROUP BY 1
ORDER BY 2 DESC
""", must=["JOIN"])

ex("jn_02", "join", ["orders", "products"],
   "Top 10 brands by units sold in the last 30 days",
   """
SELECT p.brand AS "brand",
       SUM(o.quantity) AS "units_sold"
FROM ds_orders o
JOIN ds_products p ON o.product_sku = p.sku
WHERE o.__time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10
""", must=["JOIN"])

ex("jn_03", "join", ["orders", "products"],
   "Order counts per channel including any SKUs missing from the catalogue",
   """
SELECT o.channel AS "channel",
       COUNT(*) AS "order_count",
       COUNT(*) FILTER (WHERE p.sku IS NULL) AS "uncatalogued"
FROM ds_orders o
LEFT JOIN ds_products p ON o.product_sku = p.sku
GROUP BY 1
ORDER BY 2 DESC
""", must=["LEFT JOIN"])

# ---------------------------------------------------------------- missing functions
ex("mf_01", "missing_function", ["fin_txn"],
   "Decline rate per merchant category over the past week",
   """
SELECT merchantCategory,
       COUNT(*) FILTER (WHERE authResult = 'declined') * 1.0 / COUNT(*) AS "decline_rate"
FROM ds_fin_txn
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""", must=["FILTER (WHERE"],
   trap={"naive_sql": "SELECT merchantCategory, AVG(IF(authResult = 'declined', 1, 0)) AS decline_rate "
                      "FROM ds_fin_txn GROUP BY 1",
         "expect": "INVALID", "note": "IF() does not exist; use CASE WHEN or FILTER"})

ex("mf_02", "missing_function", ["web_events"],
   "Bucket events into fast, normal and slow by latency and count each bucket",
   """
SELECT CASE
         WHEN latency_ms < 100 THEN 'fast'
         WHEN latency_ms < 500 THEN 'normal'
         ELSE 'slow'
       END AS "latency_bucket",
       COUNT(*) AS "event_count"
FROM ds_web_events
GROUP BY 1
ORDER BY 2 DESC
""", must=["CASE"])

ex("mf_03", "missing_function", ["web_events"],
   "Count events whose referrer host contains google, case-insensitively",
   """
SELECT COUNT(*) AS "event_count"
FROM ds_web_events
WHERE LOWER(referrer_host) LIKE '%google%'
""", must=["LOWER("],
   trap={"naive_sql": "SELECT COUNT(*) FROM ds_web_events WHERE referrer_host ILIKE '%google%'",
         "expect": "INVALID", "note": "ILIKE is not supported"})

ex("mf_04", "missing_function", ["sec_alerts"],
   "Alerts whose rule name starts with suspicious",
   """
SELECT rule_name,
       COUNT(*) AS "alert_count"
FROM ds_sec_alerts
WHERE REGEXP_LIKE(rule_name, '^suspicious')
GROUP BY 1
ORDER BY 2 DESC
""", must=["REGEXP_LIKE"])

ex("mf_05", "missing_function", ["game_sessions"],
   "Sessions from 7 days ago onwards, per platform",
   """
SELECT platform,
       COUNT(*) AS "session_count"
FROM ds_game_sessions
WHERE __time >= TIMESTAMPADD(DAY, -7, CURRENT_TIMESTAMP)
GROUP BY 1
ORDER BY 2 DESC
""", must=["TIMESTAMPADD"],
   trap={"naive_sql": "SELECT platform, COUNT(*) FROM ds_game_sessions "
                      "WHERE __time >= DATEADD(day, -7, GETDATE()) GROUP BY 1",
         "expect": "INVALID", "note": "DATEADD and GETDATE do not exist in Druid"})

# ---------------------------------------------------------------- TIME_SHIFT
ex("ts_01", "time_shift", ["web_events"],
   "Compare revenue in the last 7 days against the 7 days before that",
   """
SELECT SUM(revenue_usd) FILTER (
         WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
       ) AS "this_period",
       SUM(revenue_usd) FILTER (
         WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
           AND __time < CURRENT_TIMESTAMP - INTERVAL '7' DAY
       ) AS "prior_period"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
""", must=["FILTER"])

ex("ts_02", "time_shift", ["ad_impressions"],
   "Shift each impression timestamp back one week and bucket by day",
   """
SELECT TIME_FLOOR(TIME_SHIFT(__time, 'P1D', -7), 'P1D') AS "shifted_day",
       COUNT(*) AS "impressions"
FROM ds_ad_impressions
GROUP BY 1
ORDER BY 1
""", must=["TIME_SHIFT"])

ex("ts_03", "time_shift", ["telco_cdr"],
   "Dropped-call rate for the same 3 hour window today and one week ago",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       SUM(dropped_flag) * 1.0 / COUNT(*) AS "drop_rate"
FROM ds_telco_cdr
WHERE (__time >= CURRENT_TIMESTAMP - INTERVAL '3' HOUR)
   OR (__time >= TIME_SHIFT(CURRENT_TIMESTAMP, 'P1D', -7) - INTERVAL '3' HOUR
       AND __time < TIME_SHIFT(CURRENT_TIMESTAMP, 'P1D', -7))
GROUP BY 1
ORDER BY 1
""", must=["TIME_SHIFT"])

# ---------------------------------------------------------------- misc idioms
ex("mi_01", "grouping", ["orders"],
   "Order counts by channel and payment method, with subtotals",
   """
SELECT channel,
       payment_method,
       COUNT(*) AS "order_count"
FROM ds_orders
GROUP BY GROUPING SETS ((channel, payment_method), (channel), ())
ORDER BY 1, 2
""", must=["GROUPING SETS"])

ex("mi_02", "grouping", ["fin_txn"],
   "Merchants with more than 20 approved transactions",
   """
SELECT merchantId,
       COUNT(*) AS "approved_count"
FROM ds_fin_txn
WHERE authResult = 'approved'
GROUP BY 1
HAVING COUNT(*) > 20
ORDER BY 2 DESC
""", must=["HAVING"], rows=False)

ex("mi_04", "grouping", ["fin_txn"],
   "Transaction counts per merchant category, busiest first",
   """
SELECT merchantCategory AS "category",
       COUNT(*) AS "txn_count"
FROM ds_fin_txn
GROUP BY 1
ORDER BY 2 DESC
""", must=["GROUP BY 1"],
   trap={"naive_sql": "SELECT merchantCategory AS category, COUNT(*) AS txn_count "
                      "FROM ds_fin_txn GROUP BY category ORDER BY txn_count DESC",
         "expect": "INVALID",
         "note": "GROUP BY cannot see a select alias (HAVING and ORDER BY can); use the ordinal"})

ex("mi_03", "grouping", ["telco_cdr"],
   "Daily data volume in gigabytes, guarding against divide-by-zero",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day",
       (SUM(bytes_up) + SUM(bytes_down)) / 1073741824.0 AS "total_gb",
       SAFE_DIVIDE(SUM(bytes_down), SUM(bytes_up)) AS "down_up_ratio"
FROM ds_telco_cdr
GROUP BY 1
ORDER BY 1
""", must=["SAFE_DIVIDE"])
