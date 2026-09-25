"""Curated batch 02, part 2: security alerts, telecom CDRs, industrial IoT (see batch02.py)."""

E: list[dict] = []


def ex(id, cluster, schemas, question, sql, style="business"):
    E.append({"id": id, "cluster": cluster, "schemas": list(schemas), "question": question,
              "sql": sql.strip(), "style": style})


# ================================================================== sec_alerts
ex("hw2_sec_01", "lookup", ["sec_alerts"],
   "How many alerts came from gold-tier hosts in the last 7 days?",
   """
SELECT COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE LOOKUP(host_id, 'host_tier') = 'gold'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
""")

ex("hw2_sec_02", "lookup", ["sec_alerts"],
   "Alerts per host tier last month, most first",
   """
SELECT LOOKUP(host_id, 'host_tier') AS "host_tier", COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 2 DESC
""", style="terse")

ex("hw2_sec_03", "filtered_agg", ["sec_alerts"],
   "For each detection rule, what proportion of the triaged alerts turned out to be true positives? Only look at the past 30 days and ignore alerts still pending.",
   """
SELECT rule_name AS "rule_name",
       1.0 * COUNT(*) FILTER (WHERE analyst_verdict = 'true_positive') / COUNT(*) AS "true_positive_rate"
FROM ds_sec_alerts
WHERE analyst_verdict <> 'pending'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_sec_04", "mvd", ["sec_alerts"],
   "Number of alerts tagged with exfiltration in the past 2 weeks, per day",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day", COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE MV_CONTAINS(alert_tags, 'exfiltration')
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_sec_05", "mvd", ["sec_alerts"],
   "Which attack techniques show up most often in critical alerts?",
   """
SELECT t.tag AS "technique", COUNT(*) AS "alerts"
FROM ds_sec_alerts AS d, UNNEST(MV_TO_ARRAY(d.alert_tags)) AS t(tag)
WHERE d.severity = 'critical'
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_sec_06", "json_string", ["sec_alerts"],
   "Break down high severity alerts by the environment of the affected asset over the last 30 days",
   """
SELECT JSON_VALUE(PARSE_JSON(enrichment_json), '$.env') AS "environment", COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE severity = 'high'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_sec_07", "json_string", ["sec_alerts"],
   "Which teams own the assets behind the most beaconing_dns alerts?",
   """
SELECT JSON_VALUE(PARSE_JSON(enrichment_json), '$.asset_owner') AS "asset_owner", COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE rule_name = 'beaconing_dns'
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_sec_08", "approx_agg", ["sec_alerts"],
   "How many distinct hosts triggered an alert today?",
   """
SELECT APPROX_COUNT_DISTINCT(host_id) AS "hosts_alerting"
FROM ds_sec_alerts
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
""")

ex("hw2_sec_09", "grouping", ["sec_alerts"],
   "Total data moved in flagged activity by severity for the past week, in gigabytes",
   """
SELECT severity AS "severity", SUM(bytes_transferred) / 1073741824.0 AS "gigabytes"
FROM ds_sec_alerts
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_sec_10", "grouping", ["sec_alerts"],
   "Which destination ports are most often hit by alerts this month? Give the top 10 with counts.",
   """
SELECT dest_port AS "dest_port", COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10
""")

ex("hw2_sec_11", "time_bucket", ["sec_alerts"],
   "Hourly count of critical alerts over the past 2 days",
   """
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour", COUNT(*) AS "critical_alerts"
FROM ds_sec_alerts
WHERE severity = 'critical'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_sec_12", "time_shift", ["sec_alerts"],
   "Did we see more alerts this week than last week? Give both totals.",
   """
SELECT COUNT(*) FILTER (WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')) AS "this_week",
       COUNT(*) FILTER (WHERE __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')) AS "last_week"
FROM ds_sec_alerts
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
""")

ex("hw2_sec_13", "latest_earliest", ["sec_alerts"],
   "For each host with an alert in the last 24 hours, what was the most recent rule that fired?",
   """
SELECT host_id AS "host_id", LATEST(rule_name, 64) AS "last_rule"
FROM ds_sec_alerts
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY 1
ORDER BY 1
""")

ex("hw2_sec_14", "grouping", ["sec_alerts"],
   "Rules that produced more false positives than true positives in the last 90 days",
   """
SELECT rule_name AS "rule_name",
       COUNT(*) FILTER (WHERE analyst_verdict = 'false_positive') AS "false_positives",
       COUNT(*) FILTER (WHERE analyst_verdict = 'true_positive') AS "true_positives"
FROM ds_sec_alerts
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '90' DAY
GROUP BY 1
HAVING COUNT(*) FILTER (WHERE analyst_verdict = 'false_positive') > COUNT(*) FILTER (WHERE analyst_verdict = 'true_positive')
ORDER BY 2 DESC
""")

ex("hw2_sec_15", "window", ["sec_alerts"],
   "Give me the top 3 noisiest rules per severity level for the past 30 days",
   """
SELECT "severity", "rule_name", "alerts"
FROM (
  SELECT severity AS "severity", rule_name AS "rule_name", COUNT(*) AS "alerts",
         ROW_NUMBER() OVER (PARTITION BY severity ORDER BY COUNT(*) DESC) AS "rn"
  FROM ds_sec_alerts
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
  GROUP BY 1, 2
)
WHERE "rn" <= 3
ORDER BY 1, 3 DESC
""")

ex("hw2_sec_16", "order_by_restriction", ["sec_alerts"],
   "The 25 most recent critical alerts with the rule and host",
   """
SELECT __time AS "detected_at", rule_name AS "rule_name", host_id AS "host_id"
FROM ds_sec_alerts
WHERE severity = 'critical'
ORDER BY __time DESC
LIMIT 25
""")

ex("hw2_sec_17", "string_ops", ["sec_alerts"],
   "Alerts from the 10.0 network range over the last 30 days, per severity",
   """
SELECT severity AS "severity", COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE src_ip LIKE '10.0.%'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_sec_18", "cte", ["sec_alerts"],
   "Which hosts triggered alerts from more than 3 different rules in the past 14 days? Include how many alerts each had.",
   """
WITH "h" AS (
  SELECT host_id AS "host_id", APPROX_COUNT_DISTINCT(rule_name) AS "rules", COUNT(*) AS "alerts"
  FROM ds_sec_alerts
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
  GROUP BY 1
)
SELECT "host_id", "rules", "alerts"
FROM "h"
WHERE "rules" > 3
ORDER BY 3 DESC
""")

# ================================================================== telco_cdr
ex("hw2_tel_01", "filtered_agg", ["telco_cdr"],
   "What was the dropped voice call rate by network generation last month?",
   """
SELECT network_gen AS "network_gen", 100.0 * SUM(dropped_flag) / COUNT(*) AS "drop_rate_pct"
FROM ds_telco_cdr
WHERE call_type = 'voice'
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_02", "approx_agg", ["telco_cdr"],
   "Median call setup time for each serving region over the past week",
   """
SELECT serving_region AS "serving_region", APPROX_QUANTILE_DS(setup_latency_ms, 0.5) AS "median_setup_ms"
FROM ds_telco_cdr
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_03", "grouping", ["telco_cdr"],
   "Total data volume (up plus down) per plan tier over the last 30 days, in gigabytes",
   """
SELECT plan_tier AS "plan_tier", SUM(bytes_up + bytes_down) / 1073741824.0 AS "gigabytes"
FROM ds_telco_cdr
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_04", "grouping", ["telco_cdr"],
   "Average talk time in minutes for outbound voice calls, by home region, yesterday",
   """
SELECT home_region AS "home_region", AVG(duration_seconds) / 60.0 AS "avg_minutes"
FROM ds_telco_cdr
WHERE call_type = 'voice'
  AND direction = 'outbound'
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_05", "approx_agg", ["telco_cdr"],
   "How many unique subscribers used roaming in the last 30 days?",
   """
SELECT APPROX_COUNT_DISTINCT(subscriber_id) AS "roaming_subscribers"
FROM ds_telco_cdr
WHERE roaming_partner <> 'none'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
""")

ex("hw2_tel_06", "grouping", ["telco_cdr"],
   "Revenue per handset vendor this quarter, highest first",
   """
SELECT device_vendor AS "device_vendor", SUM(rated_cost_usd) AS "revenue"
FROM ds_telco_cdr
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_07", "time_extract_format", ["telco_cdr"],
   "Peak calling hour: which hour of the day has the most voice calls?",
   """
SELECT TIME_EXTRACT(__time, 'HOUR') AS "hour_of_day", COUNT(*) AS "calls"
FROM ds_telco_cdr
WHERE call_type = 'voice'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 1
""")

ex("hw2_tel_08", "grouping", ["telco_cdr"],
   "The 50 suspended-contract subscribers with the highest cost from billable records in the past 14 days",
   """
SELECT subscriber_id AS "subscriber_id", COUNT(*) AS "records", SUM(rated_cost_usd) AS "cost"
FROM ds_telco_cdr
WHERE contract_status = 'suspended'
  AND rated_cost_usd > 0
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 3 DESC
LIMIT 50
""")

ex("hw2_tel_09", "grouping", ["telco_cdr"],
   "Bucket signal strength into poor (below -100 dBm), fair (-100 to -85) and good (above -85) and show the average download volume for each, for the past 7 days",
   """
SELECT CASE WHEN signal_dbm < -100 THEN 'poor' WHEN signal_dbm <= -85 THEN 'fair' ELSE 'good' END AS "signal_band",
       AVG(bytes_down) AS "avg_bytes_down"
FROM ds_telco_cdr
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_10", "time_shift", ["telco_cdr"],
   "Compare total call minutes this month against last month for each call type",
   """
SELECT call_type AS "call_type",
       SUM(duration_seconds) FILTER (WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')) / 60.0 AS "minutes_this_month",
       SUM(duration_seconds) FILTER (WHERE __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')) / 60.0 AS "minutes_last_month"
FROM ds_telco_cdr
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
GROUP BY 1
ORDER BY 1
""")

ex("hw2_tel_11", "window", ["telco_cdr"],
   "Each plan's share of total rated cost over the last 30 days",
   """
SELECT "plan_name", "cost", 100.0 * "cost" / SUM("cost") OVER () AS "share_pct"
FROM (
  SELECT plan_name AS "plan_name", SUM(rated_cost_usd) AS "cost"
  FROM ds_telco_cdr
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
  GROUP BY 1
)
ORDER BY 3 DESC
""")

ex("hw2_tel_12", "null_math", ["telco_cdr"],
   "Cost per megabyte of data for each network generation in the past week, skipping records with no downlink",
   """
SELECT network_gen AS "network_gen",
       SUM(rated_cost_usd) / NULLIF(SUM(bytes_down) / 1048576.0, 0) AS "cost_per_mb"
FROM ds_telco_cdr
WHERE call_type = 'data'
  AND bytes_down > 0
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_13", "vocab_gap", ["telco_cdr"],
   "Which handset models have the worst dropped-call percentage among voice calls in the last 30 days? Show the worst 5.",
   """
SELECT device_model AS "device_model", 100.0 * SUM(dropped_flag) / COUNT(*) AS "drop_pct"
FROM ds_telco_cdr
WHERE call_type = 'voice'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
LIMIT 5
""", style="vocab_gap")

ex("hw2_tel_14", "time_bucket", ["telco_cdr"],
   "Daily SMS volume over the last 3 weeks",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day", COUNT(*) AS "sms"
FROM ds_telco_cdr
WHERE call_type = 'sms'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '21' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_tel_15", "string_ops", ["telco_cdr"],
   "Calls handled by iPhone models in the past week, split by model",
   """
SELECT device_model AS "device_model", COUNT(*) AS "records"
FROM ds_telco_cdr
WHERE device_model LIKE 'iphone%'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_16", "approx_agg", ["telco_cdr"],
   "95th percentile voice call duration in seconds per call direction",
   """
SELECT direction AS "direction", APPROX_QUANTILE_DS(duration_seconds, 0.95) AS "p95_duration_s"
FROM ds_telco_cdr
WHERE call_type = 'voice'
GROUP BY 1
ORDER BY 1
""")

# ================================================================ iot_readings
ex("hw2_iot_01", "reserved_column", ["iot_readings"],
   "Average temperature reading for each machine class over the last 24 hours",
   """
SELECT machine_class AS "machine_class", AVG("value") AS "avg_temperature_c"
FROM ds_iot_readings
WHERE metric_name = 'temperature_c'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_iot_02", "reserved_column", ["iot_readings"],
   "Highest vibration measured at each site in the past week",
   """
SELECT site AS "site", MAX("value") AS "peak_vibration_mm_s"
FROM ds_iot_readings
WHERE metric_name = 'vibration_mm_s'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_iot_03", "string_time_column", ["iot_readings"],
   "How many readings were taken on the device in the last 6 hours (by device clock)?",
   """
SELECT COUNT(*) AS "readings"
FROM ds_iot_readings
WHERE TIME_PARSE(reading_taken_at, 'yyyy-MM-dd HH:mm:ss') >= CURRENT_TIMESTAMP - INTERVAL '6' HOUR
""")

ex("hw2_iot_04", "string_time_column", ["iot_readings"],
   "Average lag between when a reading was taken on the device and when it reached the pipeline, in seconds, for the past 3 days",
   """
SELECT AVG(TIMESTAMPDIFF(SECOND, TIME_PARSE(reading_taken_at, 'yyyy-MM-dd HH:mm:ss'), __time)) AS "avg_lag_seconds"
FROM ds_iot_readings
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '3' DAY
""")

ex("hw2_iot_05", "string_time_column", ["iot_readings"],
   "Total readings for each day on the device's own clock, over the last 7 days",
   """
SELECT TIME_FLOOR(TIME_PARSE(reading_taken_at, 'yyyy-MM-dd HH:mm:ss'), 'P1D') AS "device_day", COUNT(*) AS "readings"
FROM ds_iot_readings
WHERE TIME_PARSE(reading_taken_at, 'yyyy-MM-dd HH:mm:ss') >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_iot_06", "filtered_agg", ["iot_readings"],
   "What share of readings are flagged suspect or bad for each plant site, last 30 days?",
   """
SELECT site AS "site",
       100.0 * COUNT(*) FILTER (WHERE quality_flag IN ('suspect', 'bad')) / COUNT(*) AS "poor_quality_pct"
FROM ds_iot_readings
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_iot_07", "approx_agg", ["iot_readings"],
   "How many distinct sensors reported from each site yesterday?",
   """
SELECT site AS "site", APPROX_COUNT_DISTINCT(device_id) AS "sensors"
FROM ds_iot_readings
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_iot_08", "grouping", ["iot_readings"],
   "Average pressure by firmware version, only versions with more than 50 readings in the past month",
   """
SELECT firmware AS "firmware", AVG("value") AS "avg_pressure_kpa", COUNT(*) AS "readings"
FROM ds_iot_readings
WHERE metric_name = 'pressure_kpa'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
HAVING COUNT(*) > 50
ORDER BY 2 DESC
""")

ex("hw2_iot_09", "time_bucket", ["iot_readings"],
   "Hourly average kiln temperature for the last 2 days",
   """
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour", AVG("value") AS "avg_temperature_c"
FROM ds_iot_readings
WHERE machine_class = 'kiln'
  AND metric_name = 'temperature_c'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_iot_10", "latest_earliest", ["iot_readings"],
   "The most recent reading from each sensor that reported today",
   """
SELECT device_id AS "device_id", LATEST("value") AS "latest_value"
FROM ds_iot_readings
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_iot_11", "time_shift", ["iot_readings"],
   "Compare the average temperature this week to last week for each plant",
   """
SELECT site AS "site",
       AVG("value") FILTER (WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')) AS "this_week_avg",
       AVG("value") FILTER (WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
                              AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')) AS "last_week_avg"
FROM ds_iot_readings
WHERE metric_name = 'temperature_c'
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
GROUP BY 1
ORDER BY 1
""")

ex("hw2_iot_12", "order_by_restriction", ["iot_readings"],
   "Show the 20 latest bad-quality readings with device and site",
   """
SELECT __time AS "read_at", device_id AS "device_id", site AS "site"
FROM ds_iot_readings
WHERE quality_flag = 'bad'
ORDER BY __time DESC
LIMIT 20
""")

ex("hw2_iot_13", "grouping", ["iot_readings"],
   "For each machine class, the difference between its highest and lowest temperature this month",
   """
SELECT machine_class AS "machine_class", MAX("value") - MIN("value") AS "temperature_range_c"
FROM ds_iot_readings
WHERE metric_name = 'temperature_c'
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 2 DESC
""")
