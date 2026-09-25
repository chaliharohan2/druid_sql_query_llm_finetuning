"""Curated batch 02, part 3: payments, gaming, extras, and requests that cannot be answered."""

E: list[dict] = []


def ex(id, cluster, schemas, question, sql, style="business"):
    E.append({"id": id, "cluster": cluster, "schemas": list(schemas), "question": question,
              "sql": sql.strip(), "style": style})


# ==================================================================== fin_txn
ex("hw2_fin_01", "filtered_agg", ["fin_txn"],
   "What share of card payments were declined last week, per card network?",
   """
SELECT cardNetwork AS "cardNetwork", 100.0 * COUNT(*) FILTER (WHERE authResult = 'declined') / COUNT(*) AS "decline_rate_pct"
FROM ds_fin_txn
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_fin_02", "grouping", ["fin_txn"],
   "Total approved spend in dollars by merchant category over the past 30 days",
   """
SELECT merchantCategory AS "merchantCategory", SUM(amountMinor * fxRate) / 100.0 AS "spend_usd"
FROM ds_fin_txn
WHERE authResult = 'approved'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_fin_03", "string_time_column", ["fin_txn"],
   "How long does settlement take? Average hours between authorisation and settlement for approved payments in the past 14 days.",
   """
SELECT AVG(TIMESTAMPDIFF(HOUR, __time, TIME_PARSE(settledAt, 'yyyy-MM-dd''T''HH:mm:ss''Z'''))) AS "avg_settlement_hours"
FROM ds_fin_txn
WHERE authResult = 'approved'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
""")

ex("hw2_fin_04", "string_time_column", ["fin_txn"],
   "Value settled per day for the last 7 days of settlement dates",
   """
SELECT TIME_FLOOR(TIME_PARSE(settledAt, 'yyyy-MM-dd''T''HH:mm:ss''Z'''), 'P1D') AS "settlement_day", SUM(amountMinor) / 100.0 AS "settled_amount"
FROM ds_fin_txn
WHERE TIME_PARSE(settledAt, 'yyyy-MM-dd''T''HH:mm:ss''Z''') >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_fin_05", "approx_agg", ["fin_txn"],
   "How many distinct merchants processed payments in each of the last 6 full months?",
   """
SELECT TIME_FLOOR(__time, 'P1M') AS "month", APPROX_COUNT_DISTINCT(merchantId) AS "active_merchants"
FROM ds_fin_txn
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -6)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_fin_06", "approx_agg", ["fin_txn"],
   "Fraud score distribution: median and 99th percentile for each merchant category, past 30 days",
   """
SELECT merchantCategory AS "merchantCategory",
       APPROX_QUANTILE_DS(riskScore, 0.5) AS "median_risk",
       APPROX_QUANTILE_DS(riskScore, 0.99) AS "p99_risk"
FROM ds_fin_txn
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 3 DESC
""")

ex("hw2_fin_07", "filtered_agg", ["fin_txn"],
   "Recurring payments as a percentage of all approved payments for each currency, this quarter",
   """
SELECT currencyCode AS "currencyCode",
       100.0 * SUM(isRecurring) / COUNT(*) AS "recurring_pct"
FROM ds_fin_txn
WHERE authResult = 'approved'
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_fin_08", "grouping", ["fin_txn"],
   "Cross-border payments (issuer and acquirer in different countries) per day for the past 10 days",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day", COUNT(*) AS "cross_border_payments"
FROM ds_fin_txn
WHERE issuerCountry <> acquirerCountry
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '10' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_fin_09", "grouping", ["fin_txn"],
   "Merchants with more than 3 declined payments in the past 30 days, worst first",
   """
SELECT merchantId AS "merchantId", COUNT(*) AS "declines"
FROM ds_fin_txn
WHERE authResult = 'declined'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
HAVING COUNT(*) > 3
ORDER BY 2 DESC
""")

ex("hw2_fin_10", "time_shift", ["fin_txn"],
   "Approved volume this month versus last month for each card network",
   """
SELECT cardNetwork AS "cardNetwork",
       SUM(amountMinor) FILTER (WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')) / 100.0 AS "this_month",
       SUM(amountMinor) FILTER (WHERE __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')) / 100.0 AS "last_month"
FROM ds_fin_txn
WHERE authResult = 'approved'
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
GROUP BY 1
ORDER BY 1
""")

ex("hw2_fin_11", "window", ["fin_txn"],
   "For each currency, rank the merchant categories by approved spend over the past 60 days and keep the top 2",
   """
SELECT "currencyCode", "merchantCategory", "spend"
FROM (
  SELECT currencyCode AS "currencyCode", merchantCategory AS "merchantCategory", SUM(amountMinor) / 100.0 AS "spend",
         ROW_NUMBER() OVER (PARTITION BY currencyCode ORDER BY SUM(amountMinor) DESC) AS "rn"
  FROM ds_fin_txn
  WHERE authResult = 'approved'
    AND __time >= CURRENT_TIMESTAMP - INTERVAL '60' DAY
  GROUP BY 1, 2
)
WHERE "rn" <= 2
ORDER BY 1, 3 DESC
""")

ex("hw2_fin_12", "time_extract_format", ["fin_txn"],
   "Which weekday has the most payments?",
   """
SELECT TIME_FORMAT(__time, 'EEEE') AS "weekday", COUNT(*) AS "payments"
FROM ds_fin_txn
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_fin_13", "null_math", ["fin_txn"],
   "Average ticket size in dollars for each issuing country in the past week, ignoring zero amounts",
   """
SELECT issuerCountry AS "issuerCountry", AVG(NULLIF(amountMinor, 0) * fxRate) / 100.0 AS "avg_ticket_usd"
FROM ds_fin_txn
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_fin_14", "cte", ["fin_txn"],
   "Which merchants had a decline rate above 20 percent in the past 30 days, considering only merchants with at least 10 payments?",
   """
WITH "m" AS (
  SELECT merchantId AS "merchantId", COUNT(*) AS "payments",
         100.0 * COUNT(*) FILTER (WHERE authResult = 'declined') / COUNT(*) AS "decline_pct"
  FROM ds_fin_txn
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
  GROUP BY 1
)
SELECT "merchantId", "payments", "decline_pct"
FROM "m"
WHERE "payments" >= 10 AND "decline_pct" > 20
ORDER BY 3 DESC
""")

ex("hw2_fin_15", "latest_earliest", ["fin_txn"],
   "The latest FX rate seen for each currency today",
   """
SELECT currencyCode AS "currencyCode", LATEST(fxRate) AS "latest_fx_rate"
FROM ds_fin_txn
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_fin_16", "order_by_restriction", ["fin_txn"],
   "The 10 most recent referred payments",
   """
SELECT __time AS "authorised_at", txnId AS "txnId", amountMinor / 100.0 AS "amount"
FROM ds_fin_txn
WHERE authResult = 'referred'
ORDER BY __time DESC
LIMIT 10
""")

ex("hw2_fin_17", "vocab_gap", ["fin_txn"],
   "How many transactions above 1,000 dollars did each merchant category see in the past 24 hours?",
   """
SELECT merchantCategory AS "merchantCategory", COUNT(*) AS "large_transactions"
FROM ds_fin_txn
WHERE amountMinor * fxRate / 100.0 > 1000
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY 1
ORDER BY 2 DESC
""", style="vocab_gap")

ex("hw2_fin_18", "grouping", ["fin_txn"],
   "Approved payment count by issuing country for the current year to date",
   """
SELECT issuerCountry AS "issuerCountry", COUNT(*) AS "approved_payments"
FROM ds_fin_txn
WHERE authResult = 'approved'
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1Y')
GROUP BY 1
ORDER BY 2 DESC
""")

# ================================================================ game_sessions
ex("hw2_game_01", "grouping", ["game_sessions"],
   "Average session length in minutes per platform over the last 30 days",
   """
SELECT platform AS "platform", AVG(duration_seconds) / 60.0 AS "avg_session_minutes"
FROM ds_game_sessions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_game_02", "approx_agg", ["game_sessions"],
   "How many players were active each day this week?",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day", APPROX_COUNT_DISTINCT(player_id) AS "active_players"
FROM ds_game_sessions
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_game_03", "grouping", ["game_sessions"],
   "Experience earned per hour played in each game mode, past 2 weeks",
   """
SELECT game_mode AS "game_mode", SUM(xp_earned) / (SUM(duration_seconds) / 3600.0) AS "xp_per_hour"
FROM ds_game_sessions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_game_04", "approx_agg", ["game_sessions"],
   "Median and 90th percentile session length by region, last month",
   """
SELECT region AS "region",
       APPROX_QUANTILE_DS(duration_seconds, 0.5) AS "median_seconds",
       APPROX_QUANTILE_DS(duration_seconds, 0.9) AS "p90_seconds"
FROM ds_game_sessions
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_game_05", "time_shift", ["game_sessions"],
   "Sessions played this week compared with the same days last week",
   """
SELECT COUNT(*) FILTER (WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')) AS "this_week",
       COUNT(*) FILTER (WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
                          AND __time < TIME_SHIFT(CURRENT_TIMESTAMP, 'P1W', -1)) AS "same_days_last_week"
FROM ds_game_sessions
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
""")

ex("hw2_game_06", "filtered_agg", ["game_sessions"],
   "What percentage of sessions in ranked mode lasted longer than 30 minutes, for each platform, past 30 days?",
   """
SELECT platform AS "platform", 100.0 * COUNT(*) FILTER (WHERE duration_seconds > 1800) / COUNT(*) AS "long_session_pct"
FROM ds_game_sessions
WHERE game_mode = 'ranked'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_game_07", "time_extract_format", ["game_sessions"],
   "When are players online? Sessions started by hour of day in Europe/Berlin time",
   """
SELECT TIME_EXTRACT(__time, 'HOUR', 'Europe/Berlin') AS "hour_berlin", COUNT(*) AS "sessions"
FROM ds_game_sessions
GROUP BY 1
ORDER BY 1
""")

ex("hw2_game_08", "window", ["game_sessions"],
   "Top 5 players by total experience in each region over the last 30 days",
   """
SELECT "region", "player_id", "xp"
FROM (
  SELECT region AS "region", player_id AS "player_id", SUM(xp_earned) AS "xp",
         ROW_NUMBER() OVER (PARTITION BY region ORDER BY SUM(xp_earned) DESC) AS "rn"
  FROM ds_game_sessions
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
  GROUP BY 1, 2
)
WHERE "rn" <= 5
ORDER BY 1, 3 DESC
""")

ex("hw2_game_09", "grouping", ["game_sessions"],
   "Players who logged more than 5 hours across all sessions this month",
   """
SELECT player_id AS "player_id", SUM(duration_seconds) / 3600.0 AS "hours_played"
FROM ds_game_sessions
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
HAVING SUM(duration_seconds) > 18000
ORDER BY 2 DESC
""")

ex("hw2_game_10", "time_bucket", ["game_sessions"],
   "Weekly session counts for casual mode across the last 8 weeks",
   """
SELECT TIME_FLOOR(__time, 'P1W') AS "week", COUNT(*) AS "sessions"
FROM ds_game_sessions
WHERE game_mode = 'casual'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '56' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_game_11", "latest_earliest", ["game_sessions"],
   "The most recent game mode played by each player active today",
   """
SELECT player_id AS "player_id", LATEST(game_mode, 64) AS "last_mode"
FROM ds_game_sessions
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_game_12", "order_by_restriction", ["game_sessions"],
   "List the 10 latest sessions on switch with player and length",
   """
SELECT __time AS "started_at", player_id AS "player_id", duration_seconds AS "duration_seconds"
FROM ds_game_sessions
WHERE platform = 'switch'
ORDER BY __time DESC
LIMIT 10
""")

ex("hw2_game_13", "grouping", ["game_sessions"],
   "Bucket sessions into short (under 10 minutes), normal (10 to 60) and marathon (over an hour) and count them by platform for the past week",
   """
SELECT platform AS "platform",
       CASE WHEN duration_seconds < 600 THEN 'short' WHEN duration_seconds <= 3600 THEN 'normal' ELSE 'marathon' END AS "session_kind",
       COUNT(*) AS "sessions"
FROM ds_game_sessions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1, 2
ORDER BY 1, 3 DESC
""")

# ============================================================== more mixed rows
ex("hw2_web_29", "vocab_gap", ["web_events"],
   "How many visitors from India made a purchase on a mobile device in the last 30 days?",
   """
SELECT APPROX_COUNT_DISTINCT(user_id) AS "buyers"
FROM ds_web_events
WHERE event_type = 'purchase'
  AND country_code = 'IN'
  AND device_type = 'mobile'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
""", style="vocab_gap")

ex("hw2_web_30", "grouping", ["web_events"],
   "Checkout to purchase: for each device type, how many checkouts and how many purchases happened yesterday?",
   """
SELECT device_type AS "device_type",
       COUNT(*) FILTER (WHERE event_type = 'checkout') AS "checkouts",
       COUNT(*) FILTER (WHERE event_type = 'purchase') AS "purchases"
FROM ds_web_events
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_ord_16", "grouping", ["orders"],
   "Units sold per payment method since the start of last month, not counting today",
   """
SELECT payment_method AS "payment_method", SUM(quantity) AS "units"
FROM ds_orders
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ad_15", "grouping", ["ad_impressions"],
   "Which exchanges delivered the most clicks in the last 7 days? Top 3 only.",
   """
SELECT exchange AS "exchange", SUM(was_clicked) AS "clicks"
FROM ds_ad_impressions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 2 DESC
LIMIT 3
""")

ex("hw2_iot_14", "grouping", ["iot_readings"],
   "How many readings does each machine class produce per day on average over the last 2 weeks?",
   """
SELECT "machine_class", AVG("readings") AS "avg_readings_per_day"
FROM (
  SELECT machine_class AS "machine_class", TIME_FLOOR(__time, 'P1D') AS "day", COUNT(*) AS "readings"
  FROM ds_iot_readings
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
  GROUP BY 1, 2
)
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_web_31", "calendar_window", ["web_events"],
   "Unique visitors last year",
   """
SELECT APPROX_COUNT_DISTINCT(user_id) AS "unique_visitors"
FROM ds_web_events
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1Y'), 'P1Y', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1Y')
""", style="terse")

ex("hw2_ord_17", "calendar_window", ["orders"],
   "Gross sales for each of the last 3 full months",
   """
SELECT TIME_FLOOR(__time, 'P1M') AS "month", SUM(quantity * unit_price_usd) AS "gross_sales"
FROM ds_orders
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -3)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_sec_19", "calendar_window", ["sec_alerts"],
   "Alert volume by severity last quarter",
   """
SELECT severity AS "severity", COUNT(*) AS "alerts"
FROM ds_sec_alerts
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M'), 'P3M', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_tel_17", "calendar_window", ["telco_cdr"],
   "Data sessions this year so far by network generation",
   """
SELECT network_gen AS "network_gen", COUNT(*) AS "data_sessions"
FROM ds_telco_cdr
WHERE call_type = 'data'
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1Y')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_game_14", "calendar_window", ["game_sessions"],
   "How many sessions did players start yesterday and today so far?",
   """
SELECT CASE WHEN __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') THEN 'today' ELSE 'yesterday' END AS "day",
       COUNT(*) AS "sessions"
FROM ds_game_sessions
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
GROUP BY 1
ORDER BY 1 DESC
""")

ex("hw2_ad_16", "calendar_window", ["ad_impressions"],
   "Impressions per placement for the past calendar week",
   """
SELECT placement AS "placement", COUNT(*) AS "impressions"
FROM ds_ad_impressions
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_iot_15", "calendar_window", ["iot_readings"],
   "Average pressure per site for last month",
   """
SELECT site AS "site", AVG("value") AS "avg_pressure_kpa"
FROM ds_iot_readings
WHERE metric_name = 'pressure_kpa'
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 2 DESC
""")

# ============================================= requests the schema cannot answer
CANNOT = "-- CANNOT_ANSWER: "
ex("hw2_no_01", "unanswerable", ["web_events"],
   "How many new user accounts were created last week?",
   CANNOT + "no signup or account-creation events exist")

ex("hw2_no_02", "unanswerable", ["orders"],
   "How many orders were returned or refunded this month?",
   CANNOT + "the table has no return or refund information")

ex("hw2_no_03", "unanswerable", ["sec_alerts"],
   "Delete all alerts that analysts marked as false positives",
   CANNOT + "DELETE is not supported; only read-only queries")

ex("hw2_no_04", "unanswerable", ["telco_cdr"],
   "What is the customer satisfaction score for each plan?",
   CANNOT + "no customer satisfaction data exists")

ex("hw2_no_05", "unanswerable", ["fin_txn"],
   "Which cardholders' names appear most often in declined payments?",
   CANNOT + "cardholder names are not stored")

ex("hw2_no_06", "unanswerable", ["ad_impressions"],
   "What was the viewability rate for each placement last month?",
   CANNOT + "no viewability or on-screen measurement data exists")

ex("hw2_no_07", "unanswerable", ["game_sessions"],
   "Update the region of player pl-0012 to eu",
   CANNOT + "UPDATE is not supported; only read-only queries")

ex("hw2_no_08", "unanswerable", ["iot_readings"],
   "How much energy did each machine consume last month?",
   CANNOT + "only temperature, vibration and pressure are measured, not energy use")

ex("hw2_no_09", "unanswerable", ["web_events", "orders"],
   "Which sales representatives closed the most orders last month?",
   CANNOT + "no sales representative or salesperson field exists")

ex("hw2_no_10", "unanswerable", ["orders"],
   "Total profit margin by category",
   CANNOT + "cost of goods and margin are not recorded")
