"""Curated batch 02 (plan Section 7: >=150 new hand-written rows), part 1: web, orders, ads.

Authored against the nine hand-written schemas. Compared with batch 01 these lean on
business language, calendar windows, epoch/string time columns, and composition
(several Druid quirks per query). Every row is re-validated by curated.py: it must
execute on all three seeds and pass G0/G2/G3/G5/G8 and the G9 judge.

House style: double-quoted aliases, ordinal GROUP BY / ORDER BY, no semicolon,
CURRENT_TIMESTAMP-relative windows, approximate aggregates by default.
`style` labels the question: business | vocab_gap | exact_column | terse.
"""

E: list[dict] = []


def ex(id, cluster, schemas, question, sql, style="business"):
    E.append({"id": id, "cluster": cluster, "schemas": list(schemas), "question": question,
              "sql": sql.strip(), "style": style})


# ================================================================== web_events
ex("hw2_web_01", "calendar_window", ["web_events"],
   "How many purchases did we get last month, by device type?",
   """
SELECT device_type AS "device_type", COUNT(*) AS "purchases"
FROM ds_web_events
WHERE event_type = 'purchase'
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_web_02", "calendar_window", ["web_events"],
   "Total revenue so far this year",
   """
SELECT SUM(revenue_usd) AS "revenue_ytd"
FROM ds_web_events
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1Y')
""", style="terse")

ex("hw2_web_03", "calendar_window", ["web_events"],
   "Compare unique visitors this quarter to the whole of last quarter",
   """
SELECT CASE WHEN __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M') THEN 'this_quarter' ELSE 'last_quarter' END AS "period",
       APPROX_COUNT_DISTINCT(user_id) AS "unique_visitors"
FROM ds_web_events
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M'), 'P3M', -1)
GROUP BY 1
ORDER BY 1
""")

ex("hw2_web_04", "calendar_window", ["web_events"],
   "What was our page view count yesterday, hour by hour?",
   """
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour", COUNT(*) AS "page_views"
FROM ds_web_events
WHERE event_type = 'page_view'
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 1
""")

ex("hw2_web_05", "calendar_window", ["web_events"],
   "Searches per country last week",
   """
SELECT country_code AS "country_code", COUNT(*) AS "searches"
FROM ds_web_events
WHERE event_type = 'search'
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')
GROUP BY 1
ORDER BY 2 DESC
""", style="terse")

ex("hw2_web_06", "rolling_window", ["web_events"],
   "In the past 48 hours, what share of events came from mobile devices?",
   """
SELECT 100.0 * COUNT(*) FILTER (WHERE device_type = 'mobile') / COUNT(*) AS "mobile_share_pct"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '48' HOUR
""")

ex("hw2_web_07", "epoch_time_column", ["web_events"],
   "How long does it typically take between the client starting a request and us logging the event? Give the average in seconds for the past 7 days.",
   """
SELECT AVG((TIMESTAMP_TO_MILLIS(__time) - request_started_at_ms) / 1000.0) AS "avg_lag_seconds"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
  AND request_started_at_ms IS NOT NULL
""")

ex("hw2_web_08", "epoch_time_column", ["web_events"],
   "Requests that started on the client in the last 24 hours, counted per hour of client start time",
   """
SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP(request_started_at_ms), 'PT1H') AS "client_hour", COUNT(*) AS "requests"
FROM ds_web_events
WHERE MILLIS_TO_TIMESTAMP(request_started_at_ms) >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY 1
ORDER BY 1
""")

ex("hw2_web_09", "json_string", ["web_events"],
   "Break purchases down by the marketing campaign the visitor came through",
   """
SELECT JSON_VALUE(PARSE_JSON(attrs_json), '$.campaign') AS "campaign", COUNT(*) AS "purchases"
FROM ds_web_events
WHERE event_type = 'purchase'
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_web_10", "json_string", ["web_events"],
   "Revenue from pro-tier customers over the past 30 days",
   """
SELECT SUM(revenue_usd) AS "pro_revenue"
FROM ds_web_events
WHERE JSON_VALUE(PARSE_JSON(attrs_json), '$.tier') = 'pro'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
""")

ex("hw2_web_11", "mvd", ["web_events"],
   "How many events were served to visitors in the new_checkout experiment, per day, for the last two weeks?",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day", COUNT(*) AS "events"
FROM ds_web_events
WHERE MV_CONTAINS(experiment_tags, 'new_checkout')
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_web_12", "mvd", ["web_events"],
   "Which experiments touch the most events overall?",
   """
SELECT t.tag AS "experiment", COUNT(*) AS "events"
FROM ds_web_events AS d, UNNEST(MV_TO_ARRAY(d.experiment_tags)) AS t(tag)
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_web_13", "mvd", ["web_events"],
   "How many events had no experiment flags at all in the last 7 days?",
   """
SELECT COUNT(*) AS "events_without_experiments"
FROM ds_web_events
WHERE (MV_LENGTH(experiment_tags) IS NULL OR MV_LENGTH(experiment_tags) = 0)
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
""")

ex("hw2_web_14", "approx_agg", ["web_events"],
   "Median and 95th percentile server latency per page over the past week",
   """
SELECT page_path AS "page_path",
       APPROX_QUANTILE_DS(latency_ms, 0.5) AS "median_latency_ms",
       APPROX_QUANTILE_DS(latency_ms, 0.95) AS "p95_latency_ms"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 3 DESC
""")

ex("hw2_web_15", "filtered_agg", ["web_events"],
   "For each browser, what fraction of requests errored (status 500 or above) in the past 30 days?",
   """
SELECT browser AS "browser",
       1.0 * COUNT(*) FILTER (WHERE status_code >= 500) / COUNT(*) AS "error_rate"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_web_16", "grouping", ["web_events"],
   "Which countries had more than 20 purchases in the past 90 days, and what was the revenue for each?",
   """
SELECT country_code AS "country_code", COUNT(*) AS "purchases", SUM(revenue_usd) AS "revenue"
FROM ds_web_events
WHERE event_type = 'purchase'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '90' DAY
GROUP BY 1
HAVING COUNT(*) > 20
ORDER BY 3 DESC
""")

ex("hw2_web_17", "time_shift", ["web_events"],
   "Revenue this week so far versus the same point last week",
   """
SELECT 'this_week' AS "period", SUM(revenue_usd) AS "revenue"
FROM ds_web_events
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')
UNION ALL
SELECT 'last_week' AS "period", SUM(revenue_usd) AS "revenue"
FROM ds_web_events
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
  AND __time < TIME_SHIFT(CURRENT_TIMESTAMP, 'P1W', -1)
""")

ex("hw2_web_18", "window", ["web_events"],
   "Rank countries by total revenue over the last 30 days and show each one's share of the total",
   """
SELECT "country_code", "revenue", RANK() OVER (ORDER BY "revenue" DESC) AS "rank",
       100.0 * "revenue" / SUM("revenue") OVER () AS "share_pct"
FROM (
  SELECT country_code AS "country_code", SUM(revenue_usd) AS "revenue"
  FROM ds_web_events
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
  GROUP BY 1
)
ORDER BY 3
""")

ex("hw2_web_19", "cte", ["web_events"],
   "Among sessions with at least 5 events in the past week, what is the average number of events per session?",
   """
WITH "s" AS (
  SELECT session_id AS "session_id", COUNT(*) AS "events"
  FROM ds_web_events
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
  GROUP BY 1
  HAVING COUNT(*) >= 5
)
SELECT AVG("events") AS "avg_events_per_session", COUNT(*) AS "sessions"
FROM "s"
""")

ex("hw2_web_20", "time_extract_format", ["web_events"],
   "Which hour of the day has the most checkouts, in New York time?",
   """
SELECT TIME_EXTRACT(__time, 'HOUR', 'America/New_York') AS "hour_ny", COUNT(*) AS "checkouts"
FROM ds_web_events
WHERE event_type = 'checkout'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 1
""")

ex("hw2_web_21", "string_ops", ["web_events"],
   "Count events from referrers ending in .com over the past 30 days",
   """
SELECT COUNT(*) AS "events"
FROM ds_web_events
WHERE referrer_host LIKE '%.com'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
""")

ex("hw2_web_22", "null_math", ["web_events"],
   "Average revenue per purchase event by device type, ignoring events with zero revenue, for the last 60 days",
   """
SELECT device_type AS "device_type", AVG(NULLIF(revenue_usd, 0)) AS "avg_revenue_per_purchase"
FROM ds_web_events
WHERE event_type = 'purchase'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '60' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_web_23", "latest_earliest", ["web_events"],
   "For each visitor seen this month, which page did they view most recently? Show 100 of them.",
   """
SELECT user_id AS "user_id", LATEST(page_path, 64) AS "last_page"
FROM ds_web_events
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 1
LIMIT 100
""")

ex("hw2_web_24", "time_bucket", ["web_events"],
   "Weekly active visitors for the last 12 weeks",
   """
SELECT TIME_FLOOR(__time, 'P1W') AS "week", APPROX_COUNT_DISTINCT(user_id) AS "active_visitors"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '84' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_web_25", "grouping", ["web_events"],
   "Bucket requests into fast (under 100 ms), ok (100 to 500 ms) and slow (over 500 ms) and count each for the past day",
   """
SELECT CASE WHEN latency_ms < 100 THEN 'fast' WHEN latency_ms <= 500 THEN 'ok' ELSE 'slow' END AS "speed",
       COUNT(*) AS "requests"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '1' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_web_26", "order_by_restriction", ["web_events"],
   "The 15 most recent purchases with their country and revenue",
   """
SELECT __time AS "purchase_time", country_code AS "country_code", revenue_usd AS "revenue_usd"
FROM ds_web_events
WHERE event_type = 'purchase'
ORDER BY __time DESC
LIMIT 15
""")

ex("hw2_web_27", "order_by_restriction", ["web_events"],
   "Which pages have the worst slowest-request latency? Show the top 10.",
   """
SELECT page_path AS "page_path", MAX(latency_ms) AS "worst_latency_ms"
FROM ds_web_events
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10
""")

ex("hw2_web_28", "reserved_alias", ["web_events"],
   "Daily event volume for the past 10 days, labelled with the day and the count",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day", COUNT(*) AS "count"
FROM ds_web_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '10' DAY
GROUP BY 1
ORDER BY 1
""")

# ============================================================ orders + products
ex("hw2_ord_01", "join", ["orders", "products"],
   "Revenue by product category over the last 30 days",
   """
SELECT p.category AS "category", SUM(o.quantity * o.unit_price_usd - o.discount_usd) AS "revenue"
FROM ds_orders AS o
INNER JOIN ds_products AS p ON o.product_sku = p.sku
WHERE o.__time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ord_02", "join", ["orders", "products"],
   "Which brands sold the most units last month?",
   """
SELECT p.brand AS "brand", SUM(o.quantity) AS "units"
FROM ds_orders AS o
INNER JOIN ds_products AS p ON o.product_sku = p.sku
WHERE o.__time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
  AND o.__time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ord_03", "join", ["orders", "products"],
   "Total shipping weight in kilograms of electronics ordered in the past week",
   """
SELECT SUM(o.quantity * p.weight_grams) / 1000.0 AS "total_weight_kg"
FROM ds_orders AS o
INNER JOIN ds_products AS p ON o.product_sku = p.sku
WHERE p.category = 'electronics'
  AND o.__time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
""")

ex("hw2_ord_04", "join", ["orders", "products"],
   "Products in the catalogue that nobody ordered in the past 2 days",
   """
SELECT p.sku AS "sku", p.product_name AS "product_name"
FROM ds_products AS p
LEFT JOIN (
  SELECT DISTINCT product_sku AS "sku"
  FROM ds_orders
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' DAY
) AS o ON p.sku = o."sku"
WHERE o."sku" IS NULL
ORDER BY 1
""")

ex("hw2_ord_05", "grouping", ["orders"],
   "Average basket value per sales channel this quarter",
   """
SELECT "channel", AVG("basket_value") AS "avg_basket_value"
FROM (
  SELECT channel AS "channel", order_id AS "order_id", SUM(quantity * unit_price_usd - discount_usd) AS "basket_value"
  FROM ds_orders
  WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P3M')
  GROUP BY 1, 2
)
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ord_06", "filtered_agg", ["orders"],
   "What percentage of orders in the past 60 days used a discount?",
   """
SELECT 100.0 * COUNT(*) FILTER (WHERE discount_usd > 0) / COUNT(*) AS "discounted_pct"
FROM ds_orders
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '60' DAY
""")

ex("hw2_ord_07", "approx_agg", ["orders"],
   "How many different customers ordered in each of the last 6 months, including the current month?",
   """
SELECT TIME_FLOOR(__time, 'P1M') AS "month", APPROX_COUNT_DISTINCT(customer_id) AS "customers"
FROM ds_orders
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -5)
GROUP BY 1
ORDER BY 1
""")

ex("hw2_ord_08", "time_shift", ["orders"],
   "Order volume in the past 7 days compared with the 7 days before that",
   """
SELECT COUNT(*) FILTER (WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY) AS "last_7_days",
       COUNT(*) FILTER (WHERE __time < CURRENT_TIMESTAMP - INTERVAL '7' DAY) AS "previous_7_days"
FROM ds_orders
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
""")

ex("hw2_ord_09", "grouping", ["orders"],
   "Payment methods used for orders shipped to Germany (DE) last week",
   """
SELECT payment_method AS "payment_method", COUNT(*) AS "orders"
FROM ds_orders
WHERE ship_country = 'DE'
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ord_10", "window", ["orders"],
   "Top 3 customers by spend in each sales channel over the last 90 days",
   """
SELECT "channel", "customer_id", "spend"
FROM (
  SELECT channel AS "channel", customer_id AS "customer_id",
         SUM(quantity * unit_price_usd) AS "spend",
         ROW_NUMBER() OVER (PARTITION BY channel ORDER BY SUM(quantity * unit_price_usd) DESC) AS "rn"
  FROM ds_orders
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '90' DAY
  GROUP BY 1, 2
)
WHERE "rn" <= 3
ORDER BY 1, 3 DESC
""")

ex("hw2_ord_11", "null_math", ["orders"],
   "Discount as a percentage of gross sales per channel for the past 30 days",
   """
SELECT channel AS "channel",
       100.0 * SUM(discount_usd) / NULLIF(SUM(quantity * unit_price_usd), 0) AS "discount_pct"
FROM ds_orders
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ord_12", "vocab_gap", ["orders"],
   "How many buyers placed an order for at least 3 different items yesterday?",
   """
SELECT COUNT(*) AS "buyers"
FROM (
  SELECT customer_id AS "customer_id"
  FROM ds_orders
  WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
    AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
  GROUP BY 1
  HAVING COUNT(DISTINCT product_sku) >= 3
)
""", style="vocab_gap")

ex("hw2_ord_13", "string_ops", ["products"],
   "The 20 cheapest products whose name starts with acme",
   """
SELECT sku AS "sku", product_name AS "product_name", list_price_usd AS "list_price_usd"
FROM ds_products
WHERE product_name LIKE 'acme%'
GROUP BY 1, 2, 3
ORDER BY 3
LIMIT 20
""")

ex("hw2_ord_14", "grouping", ["products"],
   "Average list price by category, only for categories with more than 10 products",
   """
SELECT category AS "category", AVG(list_price_usd) AS "avg_list_price", COUNT(*) AS "products"
FROM ds_products
GROUP BY 1
HAVING COUNT(*) > 10
ORDER BY 2 DESC
""")

ex("hw2_ord_15", "grouping", ["orders"],
   "Split orders into small (under 3 units), medium (3 to 9) and bulk (10 or more) and give the count and average discount for each, over the last 30 days",
   """
SELECT CASE WHEN quantity < 3 THEN 'small' WHEN quantity < 10 THEN 'medium' ELSE 'bulk' END AS "order_size",
       COUNT(*) AS "orders", AVG(discount_usd) AS "avg_discount"
FROM ds_orders
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

# ================================================================ ad_impressions
ex("hw2_ad_01", "epoch_time_column", ["ad_impressions"],
   "Impressions per hour, using the ad server's own timestamp, for the last 24 hours",
   """
SELECT TIME_FLOOR(MILLIS_TO_TIMESTAMP(served_at_epoch_s * 1000), 'PT1H') AS "server_hour", COUNT(*) AS "impressions"
FROM ds_ad_impressions
WHERE MILLIS_TO_TIMESTAMP(served_at_epoch_s * 1000) >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY 1
ORDER BY 1
""")

ex("hw2_ad_02", "filtered_agg", ["ad_impressions"],
   "Click-through rate for each placement over the last 14 days",
   """
SELECT placement AS "placement", 1.0 * SUM(was_clicked) / COUNT(*) AS "ctr"
FROM ds_ad_impressions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '14' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ad_03", "null_math", ["ad_impressions"],
   "Win rate by exchange: what share of bids cleared at a positive price, past 30 days?",
   """
SELECT exchange AS "exchange", 100.0 * COUNT(*) FILTER (WHERE win_price_usd > 0) / COUNT(*) AS "win_rate_pct"
FROM ds_ad_impressions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ad_04", "mvd", ["ad_impressions"],
   "Impressions served with a video creative last week",
   """
SELECT COUNT(*) AS "video_impressions"
FROM ds_ad_impressions
WHERE MV_CONTAINS(creative_tags, 'video')
  AND __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W'), 'P1W', -1)
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1W')
""")

ex("hw2_ad_05", "mvd", ["ad_impressions"],
   "Average winning price for each creative label",
   """
SELECT t.tag AS "creative_label", AVG(d.win_price_usd) AS "avg_win_price"
FROM ds_ad_impressions AS d, UNNEST(MV_TO_ARRAY(d.creative_tags)) AS t(tag)
WHERE d.win_price_usd > 0
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ad_06", "approx_agg", ["ad_impressions"],
   "How many distinct creatives ran per advertiser in the last 30 days, for the top 10 advertisers?",
   """
SELECT advertiser_id AS "advertiser_id", APPROX_COUNT_DISTINCT(creative_id) AS "creatives"
FROM ds_ad_impressions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10
""")

ex("hw2_ad_07", "time_bucket", ["ad_impressions"],
   "Daily spend (sum of clearing prices divided by 1000) for the spring_launch campaign over the past 3 weeks",
   """
SELECT TIME_FLOOR(__time, 'P1D') AS "day", SUM(win_price_usd) / 1000.0 AS "spend_usd"
FROM ds_ad_impressions
WHERE campaign_name = 'spring_launch'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '21' DAY
GROUP BY 1
ORDER BY 1
""")

ex("hw2_ad_08", "grouping", ["ad_impressions"],
   "Regions where the average bid was above 2 dollars CPM in the past week, with their impression count",
   """
SELECT geo_region AS "geo_region", AVG(bid_cpm_usd) AS "avg_bid_cpm", COUNT(*) AS "impressions"
FROM ds_ad_impressions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
HAVING AVG(bid_cpm_usd) > 2
ORDER BY 2 DESC
""")

ex("hw2_ad_09", "time_shift", ["ad_impressions"],
   "Clicks this month compared with the same period last month",
   """
SELECT SUM(was_clicked) FILTER (WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M')) AS "clicks_this_month",
       SUM(was_clicked) FILTER (WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
                                  AND __time < TIME_SHIFT(CURRENT_TIMESTAMP, 'P1M', -1)) AS "clicks_same_period_last_month"
FROM ds_ad_impressions
WHERE __time >= TIME_SHIFT(TIME_FLOOR(CURRENT_TIMESTAMP, 'P1M'), 'P1M', -1)
""")

ex("hw2_ad_10", "grouping", ["ad_impressions"],
   "For each campaign, the highest single clearing price seen in the last 90 days",
   """
SELECT campaign_name AS "campaign_name", MAX(win_price_usd) AS "max_win_price"
FROM ds_ad_impressions
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '90' DAY
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ad_11", "cte", ["ad_impressions"],
   "Campaigns whose click-through rate in the last 7 days beat the overall average CTR for the same week",
   """
WITH "c" AS (
  SELECT campaign_name AS "campaign_name", 1.0 * SUM(was_clicked) / COUNT(*) AS "ctr"
  FROM ds_ad_impressions
  WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
  GROUP BY 1
)
SELECT "campaign_name", "ctr"
FROM "c"
WHERE "ctr" > (SELECT 1.0 * SUM(was_clicked) / COUNT(*) FROM ds_ad_impressions WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY)
ORDER BY 2 DESC
""")

ex("hw2_ad_12", "time_extract_format", ["ad_impressions"],
   "Which day of the week gets the most clicks?",
   """
SELECT TIME_FORMAT(__time, 'EEEE') AS "weekday", SUM(was_clicked) AS "clicks"
FROM ds_ad_impressions
GROUP BY 1
ORDER BY 2 DESC
""")

ex("hw2_ad_13", "vocab_gap", ["ad_impressions"],
   "How many ads did we show in the interstitial slot to viewers in the emea or apac regions yesterday?",
   """
SELECT COUNT(*) AS "ads_shown"
FROM ds_ad_impressions
WHERE placement = 'interstitial'
  AND geo_region IN ('emea', 'apac')
  AND __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D') - INTERVAL '1' DAY
  AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
""", style="vocab_gap")

ex("hw2_ad_14", "latest_earliest", ["ad_impressions"],
   "What was the most recent bid price for each creative that ran today?",
   """
SELECT creative_id AS "creative_id", LATEST(bid_cpm_usd) AS "latest_bid_cpm"
FROM ds_ad_impressions
WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'P1D')
GROUP BY 1
ORDER BY 1
""")
