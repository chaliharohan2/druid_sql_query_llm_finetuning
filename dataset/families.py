"""Domain families for generated schemas.

One family describes a business domain once; `gen_schemas.py` turns each into
three datasources that differ in naming convention, column subset, time-column
encoding and enrichment shape. The point is that no single surface convention
(snake_case, a `ds_` prefix, `__time` listed first, epoch-millis side columns)
is welded to the dialect being taught.

Every dimension carries its literal value pool. The pool is used both to
generate rows and to choose the literals a query template puts in a WHERE
clause, which is what keeps generated queries returning non-empty results.

Not AI training or inference code: this only describes Druid fixtures.
"""
from __future__ import annotations

FAMILIES: list[dict] = []


def fam(**kw):
    kw.setdefault("mvd", None)
    kw.setdefault("jsn", None)
    kw.setdefault("lookup", None)
    kw.setdefault("partner", None)
    FAMILIES.append(kw)
    return kw


# dims:    (name, description, [pool values])
# ids:     (name, description, kind)          kind in idkinds() below
# metrics: (name, loader type, description, gen spec)
#          gen spec: ("uni", lo, hi) | ("logn", mu, sigma) | ("pick", [values])
# mvd:     (name, description, [tag pool])
# jsn:     (name, description, {key: [values]})
# lookup:  (lookup name, keyed-on column, description, {key: value})
# partner: (dim table key, local join column, remote key column)

fam(
    key="app_logs", domain="Application observability",
    purpose="Structured log records emitted by a microservice fleet.",
    time_name="log_ts", time_desc="Time the log line was emitted.",
    entity=("service_name", "Emitting service.",
            ["checkout", "search", "auth", "billing", "catalog", "notify", "gateway"]),
    dims=[("log_level", "Severity of the record.", ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]),
          ("env", "Deployment environment.", ["prod", "staging", "dev"]),
          ("region", "Cloud region the pod ran in.", ["us-east-1", "eu-west-1", "ap-south-1", "us-west-2"]),
          ("k8s_namespace", "Kubernetes namespace.", ["core", "edge", "data", "platform"])],
    ids=[("trace_id", "Distributed trace identifier. High cardinality.", "hex16"),
         ("pod_name", "Pod the record came from.", "pod")],
    metrics=[("duration_ms", "long", "Handler duration in milliseconds.", ("logn", 4.4, 1.0)),
             ("heap_used_mb", "long", "Heap in use when the record was written, in MB.", ("uni", 120, 3800)),
             ("gc_pause_ms", "double", "Longest GC pause in the preceding second.", ("uni", 0.4, 220.0))],
    mvd=("log_tags", "Free-form labels attached by the logging library.",
         ["retryable", "user_facing", "slo_breach", "cold_start", "degraded"]),
    jsn=("context_json", "Request context. Keys: tenant, release, canary.",
         {"tenant": ["acme", "globex", "initech", "umbrella"],
          "release": ["2024.11", "2025.01", "2025.04"], "canary": ["canary", "stable"]}),
    lookup=("service_owner", "service_name", "Maps a service to the team that owns it.",
            {"checkout": "payments", "search": "discovery", "auth": "identity", "billing": "payments",
             "catalog": "discovery", "notify": "growth", "gateway": "platform"}),
)

fam(
    key="api_gateway", domain="API gateway traffic",
    purpose="Per-request records from a public API gateway.",
    time_name="request_ts", time_desc="Time the gateway accepted the request.",
    entity=("api_name", "Logical API the route belongs to.",
            ["accounts", "payments", "inventory", "shipping", "reports", "webhooks"]),
    dims=[("http_method", "HTTP verb.", ["GET", "POST", "PUT", "PATCH", "DELETE"]),
          ("route_template", "Matched route pattern.",
           ["/v1/accounts/{id}", "/v1/payments", "/v1/inventory/{sku}", "/v2/reports/run", "/v1/webhooks"]),
          ("auth_mode", "How the caller authenticated.", ["api_key", "oauth2", "mtls", "anonymous"]),
          ("client_tier", "Rate-limit tier of the calling client.", ["free", "standard", "premium", "internal"])],
    ids=[("client_id", "Calling client identifier. High cardinality.", "hex10"),
         ("upstream_host", "Backend instance that served the call.", "host")],
    metrics=[("status_code", "long", "HTTP status returned to the caller.",
              ("pick", [200, 200, 200, 201, 204, 304, 400, 401, 429, 500, 502, 503])),
             ("gateway_latency_ms", "long", "Time spent inside the gateway.", ("logn", 3.1, 0.8)),
             ("upstream_latency_ms", "long", "Time spent waiting on the backend.", ("logn", 4.6, 1.1)),
             ("response_bytes", "long", "Response body size in bytes.", ("uni", 180, 900000)),
             ("rate_limit_remaining", "long", "Quota left in the caller's window.", ("uni", 0, 5000))],
    mvd=("policy_tags", "Gateway policies that fired on this request.",
         ["quota", "waf", "cors", "cache_hit", "retry", "circuit_open"]),
)

fam(
    key="cdn_edge", domain="CDN edge delivery",
    purpose="Edge cache access records from a content delivery network.",
    time_name="edge_ts", time_desc="Time the edge node served the object.",
    entity=("pop_code", "Point-of-presence that served the request.",
            ["lhr1", "iad2", "fra3", "nrt1", "gru2", "sin1", "syd1"]),
    dims=[("cache_status", "Edge cache outcome.", ["HIT", "MISS", "REVALIDATED", "EXPIRED", "BYPASS"]),
          ("content_type", "MIME family of the object.", ["image", "video", "script", "html", "font", "json"]),
          ("protocol", "Wire protocol.", ["h2", "h3", "http1.1"]),
          ("client_country", "ISO-3166 alpha-2 country of the client.",
           ["GB", "US", "DE", "JP", "BR", "SG", "AU", "IN"])],
    ids=[("object_key", "Cache key of the requested object. High cardinality.", "hex12"),
         ("origin_host", "Origin the edge would fall back to.", "host")],
    metrics=[("bytes_served", "long", "Bytes written to the client.", ("uni", 1200, 12000000)),
             ("ttfb_ms", "long", "Time to first byte, in milliseconds.", ("logn", 3.4, 0.9)),
             ("origin_fetch_ms", "long", "Time spent fetching from origin. Zero on a cache hit.",
              ("uni", 0, 2400))],
    mvd=("edge_rules", "Edge rules applied while serving.",
         ["compress", "image_resize", "geo_block", "signed_url", "stale_while_revalidate"]),
    lookup=("pop_region", "pop_code", "Maps a POP code to its continental region.",
            {"lhr1": "emea", "iad2": "amer", "fra3": "emea", "nrt1": "apac",
             "gru2": "amer", "sin1": "apac", "syd1": "apac"}),
)

fam(
    key="streaming_media", domain="Video streaming",
    purpose="Playback telemetry from a subscription video service.",
    time_name="playback_ts", time_desc="Time the playback heartbeat was recorded.",
    entity=("title_id", "Catalogue title being watched.", ["t-100", "t-101", "t-102", "t-103",
                                                           "t-104", "t-105", "t-106", "t-107"]),
    dims=[("device_class", "Playback device family.", ["smart_tv", "mobile", "web", "console", "stb"]),
          ("cdn_provider", "CDN serving the segments.", ["akamai", "fastly", "cloudfront", "inhouse"]),
          ("stream_quality", "Ladder rung in use.", ["360p", "480p", "720p", "1080p", "4k"]),
          ("subscription_plan", "Viewer's plan.", ["basic", "standard", "premium", "trial"])],
    ids=[("profile_id", "Viewer profile identifier. High cardinality.", "hex10"),
         ("session_id", "Playback session identifier.", "hex12")],
    metrics=[("watch_seconds", "long", "Seconds watched in this heartbeat window.", ("uni", 5, 900)),
             ("rebuffer_ms", "long", "Milliseconds spent rebuffering.", ("uni", 0, 9000)),
             ("bitrate_kbps", "long", "Delivered bitrate.", ("uni", 400, 18000)),
             ("startup_ms", "long", "Time from play to first frame.", ("logn", 6.9, 0.7))],
    jsn=("player_json", "Player state. Keys: app_version, drm, autoplay.",
         {"app_version": ["6.1.0", "6.2.1", "7.0.0"], "drm": ["widevine", "fairplay", "playready"],
          "autoplay": ["autoplay", "manual"]}),
)

fam(
    key="ride_hailing", domain="Ride hailing",
    purpose="Completed trip records from a ride-hailing marketplace.",
    time_name="trip_started_ts", time_desc="Time the trip began.",
    entity=("city", "Operating city.", ["lisbon", "warsaw", "nairobi", "bogota", "manila", "toronto"]),
    dims=[("product_tier", "Vehicle tier booked.", ["economy", "comfort", "xl", "premium"]),
          ("payment_method", "How the rider paid.", ["card", "cash", "wallet", "corporate"]),
          ("cancel_reason", "Why the trip ended early, or none.",
           ["none", "rider_cancel", "driver_cancel", "no_show"]),
          ("surge_band", "Surge multiplier band in force.", ["1.0x", "1.2x", "1.5x", "2.0x"])],
    ids=[("driver_id", "Driver identifier.", "driver"),
         ("rider_id", "Rider identifier. High cardinality.", "hex10")],
    metrics=[("distance_km", "double", "Trip distance in kilometres.", ("uni", 0.4, 48.0)),
             ("duration_min", "long", "Trip duration in minutes.", ("uni", 3, 95)),
             ("fare_local", "double", "Fare charged in local currency.", ("uni", 2.5, 180.0)),
             ("driver_rating", "double", "Rating the rider gave, 1 to 5.", ("uni", 1.0, 5.0))],
    partner=("dim_drivers", "driver_id", "driver_id"),
)

fam(
    key="food_delivery", domain="Food delivery",
    purpose="Order lifecycle records from a food delivery platform.",
    time_name="ordered_ts", time_desc="Time the order was placed.",
    entity=("restaurant_id", "Restaurant fulfilling the order.", [f"r-{i:03d}" for i in range(40)]),
    dims=[("cuisine", "Kitchen category.",
           ["pizza", "sushi", "burgers", "thai", "vegan", "bakery", "indian"]),
          ("order_channel", "Where the order came from.", ["ios", "android", "web", "partner_api"]),
          ("fulfilment", "How the order reached the customer.", ["courier", "pickup", "dine_in"]),
          ("promo_code", "Promotion applied, or none.", ["none", "FIRST10", "WEEKEND", "FREEDEL"])],
    ids=[("customer_id", "Customer identifier. High cardinality.", "hex10"),
         ("courier_id", "Assigned courier.", "driver")],
    metrics=[("basket_total", "double", "Order subtotal before fees.", ("uni", 6.0, 140.0)),
             ("delivery_fee", "double", "Delivery fee charged.", ("uni", 0.0, 9.5)),
             ("prep_minutes", "long", "Minutes the kitchen took.", ("uni", 4, 55)),
             ("items_count", "long", "Line items in the basket.", ("uni", 1, 14))],
    mvd=("dietary_flags", "Dietary labels across the basket.",
         ["vegetarian", "vegan", "gluten_free", "halal", "nut_free", "spicy"]),
)

fam(
    key="logistics", domain="Freight logistics",
    purpose="Scan events from a parcel and freight network.",
    time_name="scanned_ts", time_desc="Time the parcel was scanned.",
    entity=("carrier_code", "Carrier handling the leg.", ["DHL", "UPS", "FEDX", "DPD", "GLS", "TNT"]),
    dims=[("scan_type", "What the scan recorded.",
           ["pickup", "in_transit", "customs", "out_for_delivery", "delivered", "exception"]),
          ("service_level", "Service purchased.", ["economy", "standard", "express", "same_day"]),
          ("origin_country", "Country the shipment left.", ["DE", "NL", "PL", "CZ", "FR", "ES"]),
          ("dest_country", "Country the shipment is bound for.", ["GB", "IT", "SE", "PT", "AT", "IE"])],
    ids=[("tracking_number", "Parcel tracking number. High cardinality.", "hex12"),
         ("hub_code", "Sorting hub that produced the scan.", "hub")],
    metrics=[("weight_kg", "double", "Billable weight in kilograms.", ("uni", 0.1, 640.0)),
             ("declared_value_eur", "double", "Declared customs value.", ("uni", 5.0, 4200.0)),
             ("leg_distance_km", "long", "Distance covered on this leg.", ("uni", 3, 1800)),
             ("dwell_minutes", "long", "Minutes the parcel sat at the hub.", ("uni", 2, 2600))],
    partner=("dim_carriers", "carrier_code", "carrier_code"),
)

fam(
    key="energy_meter", domain="Smart metering",
    purpose="Interval readings from residential and commercial smart meters.",
    time_name="interval_ts", time_desc="Start of the metering interval.",
    entity=("meter_id", "Meter that produced the reading.", [f"m-{i:04d}" for i in range(80)]),
    dims=[("tariff", "Tariff the account is on.", ["flat", "time_of_use", "economy7", "dynamic"]),
          ("premise_type", "Kind of premise.", ["residential", "small_business", "industrial"]),
          ("grid_zone", "Distribution zone.", ["north", "midlands", "south_east", "south_west", "wales"]),
          ("quality_flag", "Reading quality.", ["actual", "estimated", "substituted"])],
    ids=[("account_ref", "Billing account reference.", "hex10")],
    metrics=[("kwh_consumed", "double", "Energy drawn during the interval.", ("uni", 0.0, 14.5)),
             ("kwh_exported", "double", "Energy exported back to the grid.", ("uni", 0.0, 6.0)),
             ("voltage_v", "double", "Average supply voltage.", ("uni", 218.0, 252.0)),
             ("power_factor", "double", "Power factor for the interval.", ("uni", 0.72, 1.0))],
    lookup=("zone_operator", "grid_zone", "Maps a grid zone to its network operator.",
            {"north": "northgrid", "midlands": "centralpower", "south_east": "seboard",
             "south_west": "westnet", "wales": "cymrupower"}),
)

fam(
    key="clinical_telemetry", domain="Clinical device telemetry",
    purpose="Synthetic bedside monitor readings from a hospital ward.",
    time_name="observed_ts", time_desc="Time the observation was taken.",
    entity=("ward", "Ward the bed sits in.", ["icu", "cardiology", "respiratory", "surgical", "maternity"]),
    dims=[("device_model", "Monitor model.", ["mx550", "mx700", "cx90", "portable2"]),
          ("observation_type", "What was measured.",
           ["heart_rate", "spo2", "resp_rate", "systolic_bp", "temperature"]),
          ("alarm_state", "Alarm the monitor was in.", ["none", "advisory", "warning", "crisis"]),
          ("shift", "Nursing shift in progress.", ["day", "late", "night"])],
    ids=[("bed_id", "Bed identifier.", "bed"), ("encounter_ref", "Encounter reference.", "hex10")],
    metrics=[("value", "double", "Measured value in the unit implied by observation_type.",
              ("uni", 32.0, 190.0)),
             ("battery_pct", "long", "Monitor battery remaining.", ("uni", 3, 100)),
             ("signal_quality", "double", "Signal quality score, 0 to 1.", ("uni", 0.2, 1.0))],
)

fam(
    key="workforce", domain="Workforce management",
    purpose="Shift and attendance records for a distributed field workforce.",
    time_name="shift_start_ts", time_desc="Scheduled start of the shift.",
    entity=("site_code", "Site the shift was worked at.",
            ["ste-01", "ste-02", "ste-03", "ste-04", "ste-05", "ste-06"]),
    dims=[("role", "Role worked.", ["technician", "supervisor", "driver", "picker", "cleaner"]),
          ("shift_pattern", "Pattern the shift belongs to.", ["early", "late", "night", "weekend"]),
          ("attendance", "How the shift resolved.", ["worked", "late", "absent", "swapped", "cancelled"]),
          ("contract_type", "Employment contract.", ["permanent", "agency", "zero_hours"])],
    ids=[("employee_ref", "Employee reference. High cardinality.", "hex10")],
    metrics=[("scheduled_minutes", "long", "Minutes rostered.", ("pick", [240, 300, 360, 420, 480, 600])),
             ("worked_minutes", "long", "Minutes actually worked.", ("uni", 0, 620)),
             ("overtime_minutes", "long", "Minutes beyond the roster.", ("uni", 0, 180)),
             ("hourly_rate", "double", "Hourly rate paid.", ("uni", 11.5, 38.0))],
)

fam(
    key="email_campaigns", domain="Marketing automation",
    purpose="Send and engagement events from an email marketing platform.",
    time_name="event_ts", time_desc="Time the engagement event fired.",
    entity=("campaign_id", "Campaign the message belongs to.", [f"cmp-{i:03d}" for i in range(30)]),
    dims=[("event_kind", "Engagement event.",
           ["sent", "delivered", "opened", "clicked", "bounced", "unsubscribed", "complained"]),
          ("mail_client", "Client that rendered the message.", ["gmail", "outlook", "apple_mail", "yahoo", "other"]),
          ("segment", "Audience segment targeted.", ["new", "active", "lapsed", "vip", "winback"]),
          ("send_channel", "Delivery channel.", ["broadcast", "triggered", "transactional"])],
    ids=[("recipient_hash", "Hashed recipient address. High cardinality.", "hex12"),
         ("message_id", "Per-message identifier.", "hex16")],
    metrics=[("open_latency_s", "long", "Seconds between delivery and first open.", ("uni", 0, 260000)),
             ("link_position", "long", "Ordinal of the clicked link.", ("uni", 1, 12)),
             ("attributed_revenue", "double", "Revenue attributed to the event.", ("uni", 0.0, 420.0))],
    partner=("dim_segments", "segment", "segment"),
)

fam(
    key="inventory", domain="Warehouse inventory",
    purpose="Stock movements across a warehouse network.",
    time_name="movement_ts", time_desc="Time the movement was booked.",
    entity=("warehouse_code", "Warehouse the movement happened in.",
            ["wh-lon", "wh-man", "wh-bir", "wh-gla", "wh-bri"]),
    dims=[("movement_type", "Kind of movement.",
           ["receipt", "putaway", "pick", "cycle_count", "adjustment", "return", "writeoff"]),
          ("storage_zone", "Zone within the warehouse.", ["ambient", "chilled", "frozen", "bulk", "hazmat"]),
          ("reason_code", "Reason booked against the movement.",
           ["none", "damaged", "expired", "miscount", "customer_return"]),
          ("uom", "Unit of measure.", ["each", "case", "pallet"])],
    ids=[("item_code", "Stock item code. High cardinality.", "sku"),
         ("operator_ref", "Operator who booked the movement.", "hex10")],
    metrics=[("quantity", "long", "Units moved. Negative for outbound.", ("uni", -400, 900)),
             ("unit_cost", "double", "Cost per unit at booking time.", ("uni", 0.3, 260.0)),
             ("shelf_life_days", "long", "Days of shelf life left.", ("uni", 0, 720))],
    partner=("dim_warehouses", "warehouse_code", "warehouse_code"),
)

fam(
    key="crypto_trades", domain="Crypto exchange",
    purpose="Matched trades from a spot cryptocurrency exchange.",
    time_name="matched_ts", time_desc="Time the trade matched on the book.",
    entity=("pair", "Traded pair.", ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-EUR", "ETH-BTC", "XRP-USD"]),
    dims=[("side", "Aggressor side.", ["buy", "sell"]),
          ("order_type", "Order type that crossed.", ["market", "limit", "stop_limit", "post_only"]),
          ("venue", "Matching venue.", ["primary", "dark", "otc"]),
          ("maker_taker", "Fee role of the reported side.", ["maker", "taker"])],
    ids=[("trade_id", "Trade identifier. High cardinality.", "hex16"),
         ("account_ref", "Account that traded.", "hex10")],
    metrics=[("price", "double", "Execution price in the quote currency.", ("uni", 0.3, 71000.0)),
             ("quantity_base", "double", "Quantity in the base currency.", ("uni", 0.0001, 42.0)),
             ("fee_quote", "double", "Fee charged in the quote currency.", ("uni", 0.0, 190.0)),
             ("slippage_bps", "double", "Slippage against the mid, in basis points.", ("uni", -45.0, 60.0))],
)

fam(
    key="support_tickets", domain="Customer support",
    purpose="Ticket state transitions from a customer support desk.",
    time_name="transition_ts", time_desc="Time the ticket changed state.",
    entity=("queue", "Queue the ticket sits in.",
            ["billing", "technical", "onboarding", "abuse", "returns", "enterprise"]),
    dims=[("priority", "Priority assigned.", ["p1", "p2", "p3", "p4"]),
          ("channel", "How the customer got in touch.", ["email", "chat", "phone", "self_serve", "social"]),
          ("state", "State the ticket moved into.",
           ["new", "assigned", "pending_customer", "escalated", "resolved", "closed"]),
          ("language", "Language of the conversation.", ["en", "de", "fr", "es", "pt"])],
    ids=[("ticket_ref", "Ticket reference. High cardinality.", "hex10"),
         ("agent_ref", "Agent who made the transition.", "hex10")],
    metrics=[("age_minutes", "long", "Ticket age at transition time.", ("uni", 1, 40000)),
             ("reply_count", "long", "Replies exchanged so far.", ("uni", 0, 34)),
             ("csat_score", "long", "Satisfaction score, 1 to 5. Zero when unrated.",
              ("pick", [0, 0, 1, 2, 3, 4, 5, 5, 5]))],
    mvd=("ticket_labels", "Labels an agent applied to the ticket.",
         ["refund", "bug", "how_to", "outage", "vip", "regression", "duplicate"]),
)

fam(
    key="fleet_gps", domain="Fleet telematics",
    purpose="GPS and CAN-bus samples from a commercial vehicle fleet.",
    time_name="sample_ts", time_desc="Time the sample was captured on the vehicle.",
    entity=("vehicle_id", "Vehicle that produced the sample.", [f"veh-{i:03d}" for i in range(50)]),
    dims=[("vehicle_class", "Vehicle category.", ["van", "rigid", "artic", "car", "refuse"]),
          ("fuel_type", "Powertrain.", ["diesel", "electric", "hybrid", "cng"]),
          ("depot", "Home depot.", ["dep-north", "dep-south", "dep-east", "dep-west"]),
          ("driving_event", "Driving event flagged by the telematics unit.",
           ["none", "harsh_brake", "harsh_accel", "overspeed", "idle", "cornering"])],
    ids=[("driver_id", "Driver signed in at sample time.", "driver")],
    metrics=[("speed_kph", "double", "Instantaneous road speed.", ("uni", 0.0, 118.0)),
             ("engine_rpm", "long", "Engine revolutions per minute.", ("uni", 0, 3800)),
             ("fuel_level_pct", "double", "Fuel or charge remaining.", ("uni", 2.0, 100.0)),
             ("odometer_km", "long", "Lifetime odometer reading.", ("uni", 1200, 640000))],
    partner=("dim_drivers", "driver_id", "driver_id"),
)

fam(
    key="payment_gateway", domain="Payments processing",
    purpose="Authorisation attempts passing through a card payment gateway.",
    time_name="attempted_ts", time_desc="Time the authorisation was attempted.",
    entity=("merchant_id", "Merchant taking the payment.", [f"mch-{i:03d}" for i in range(45)]),
    dims=[("card_scheme", "Card network.", ["visa", "mastercard", "amex", "discover"]),
          ("auth_result", "Outcome of the authorisation.",
           ["approved", "declined", "referred", "timeout", "3ds_challenge"]),
          ("decline_reason", "Issuer reason, or none.",
           ["none", "insufficient_funds", "do_not_honour", "expired_card", "suspected_fraud"]),
          ("entry_mode", "How the card was presented.", ["ecommerce", "contactless", "chip", "moto"])],
    ids=[("psp_reference", "Gateway reference. High cardinality.", "hex16"),
         ("issuer_bin", "Issuing bank BIN.", "bin")],
    metrics=[("amount_minor", "long", "Amount in the currency's minor unit.", ("uni", 99, 480000)),
             ("risk_score", "long", "Fraud engine score, 0 to 100.", ("uni", 0, 100)),
             ("processing_ms", "long", "Round trip to the issuer, in milliseconds.", ("logn", 6.2, 0.6))],
    jsn=("gateway_json", "Gateway metadata. Keys: acquirer, currency, retry.",
         {"acquirer": ["adyen", "worldpay", "stripe", "checkout"],
          "currency": ["GBP", "EUR", "USD", "SEK"], "retry": ["retried", "first_try"]}),
)

fam(
    key="ml_inference", domain="ML serving",
    purpose="Per-request telemetry from an online model serving tier.",
    time_name="served_ts", time_desc="Time the prediction was served.",
    entity=("model_name", "Model that served the request.",
            ["fraud_v3", "ranker_v7", "churn_v2", "ocr_v1", "embed_v4", "asr_v2"]),
    dims=[("model_version", "Deployed version tag.", ["v1", "v2", "v3", "canary"]),
          ("accelerator", "Hardware the request ran on.", ["cpu", "a10g", "l4", "h100"]),
          ("request_source", "Caller surface.", ["batch", "online", "shadow", "replay"]),
          ("outcome", "How the request finished.", ["ok", "fallback", "timeout", "error"])],
    ids=[("request_id", "Request identifier. High cardinality.", "hex16"),
         ("replica_host", "Serving replica.", "host")],
    metrics=[("inference_ms", "double", "Model forward-pass time.", ("uni", 1.2, 940.0)),
             ("queue_ms", "double", "Time queued before execution.", ("uni", 0.0, 420.0)),
             ("input_tokens", "long", "Tokens in the request payload.", ("uni", 4, 8100)),
             ("confidence", "double", "Top-class confidence, 0 to 1.", ("uni", 0.31, 0.999))],
    mvd=("feature_flags", "Serving flags active on the request.",
         ["cache", "quantized", "batched", "guardrail", "ab_holdout"]),
)

fam(
    key="retail_pos", domain="Retail point of sale",
    purpose="Line items rung through tills in a bricks-and-mortar chain.",
    time_name="rung_ts", time_desc="Time the line item was rung through.",
    entity=("store_code", "Store the sale happened in.",
            ["st-001", "st-002", "st-003", "st-004", "st-005", "st-006", "st-007"]),
    dims=[("department", "Store department.",
           ["grocery", "produce", "bakery", "household", "clothing", "electronics"]),
          ("tender_type", "How the customer paid.", ["card", "cash", "voucher", "giftcard", "mobile"]),
          ("promotion", "Promotion applied to the line.", ["none", "bogof", "multibuy", "clearance", "loyalty"]),
          ("till_type", "Till the sale went through.", ["staffed", "self_checkout", "mobile_scanner"])],
    ids=[("basket_ref", "Basket identifier. High cardinality.", "hex12"),
         ("item_code", "Product code sold.", "sku")],
    metrics=[("line_quantity", "long", "Units on the line.", ("uni", 1, 12)),
             ("line_total", "double", "Line total after promotions.", ("uni", 0.35, 340.0)),
             ("margin_pct", "double", "Gross margin on the line.", ("uni", -8.0, 62.0))],
    partner=("dim_stores", "store_code", "store_code"),
)

fam(
    key="network_flows", domain="Network telemetry",
    purpose="Sampled flow records exported by core network devices.",
    time_name="flow_start_ts", time_desc="Time the flow was first observed.",
    entity=("exporter", "Device that exported the flow.",
            ["core-01", "core-02", "edge-01", "edge-02", "dc-01", "dc-02"]),
    dims=[("protocol", "IP protocol.", ["tcp", "udp", "icmp", "gre"]),
          ("direction", "Flow direction relative to the site.", ["ingress", "egress", "internal"]),
          ("tcp_flags", "Flag combination seen on the flow.", ["SYN", "SYN_ACK", "ACK", "FIN", "RST"]),
          ("vlan", "VLAN the flow was tagged with.", ["vlan-10", "vlan-20", "vlan-30", "vlan-99"])],
    ids=[("src_addr", "Source address. High cardinality.", "ip"),
         ("dst_addr", "Destination address. High cardinality.", "ip")],
    metrics=[("packets", "long", "Packets in the flow.", ("uni", 1, 90000)),
             ("bytes", "long", "Bytes in the flow.", ("uni", 40, 42000000)),
             ("dst_port", "long", "Destination port.",
              ("pick", [22, 53, 80, 443, 3306, 5432, 8080, 9092, 9200])),
             ("duration_ms", "long", "How long the flow lasted.", ("uni", 1, 610000))],
    mvd=("flow_labels", "Classifier labels attached to the flow.",
         ["scan", "bulk_transfer", "interactive", "encrypted", "unclassified"]),
)


# ------------------------------------------------------------- dimension tables
# Join partners referenced by `partner=` above. These are small, slowly changing
# tables; the fact table joins to them on a shared key.
DIMS: list[dict] = [
    dict(key="dim_drivers", domain="Ride hailing", purpose="Driver roster for the marketplace.",
         time_name="onboarded_ts", time_desc="Date the driver was onboarded.",
         entity=("driver_id", "Driver identifier. Joins to the fact table.", "driver"),
         dims=[("driver_status", "Current account status.", ["active", "suspended", "churned", "onboarding"]),
               ("home_city", "City the driver is based in.",
                ["lisbon", "warsaw", "nairobi", "bogota", "manila", "toronto"]),
               ("vehicle_make", "Make of the registered vehicle.",
                ["toyota", "vw", "renault", "hyundai", "byd"])],
         metrics=[("lifetime_trips", "long", "Trips completed to date.", ("uni", 0, 9400)),
                  ("acceptance_rate", "double", "Share of offers accepted.", ("uni", 0.32, 1.0))],
         rows=80),
    dict(key="dim_carriers", domain="Freight logistics", purpose="Carrier reference data.",
         time_name="contract_ts", time_desc="Date the carrier contract started.",
         entity=("carrier_code", "Carrier code. Joins to the fact table.",
                 ["DHL", "UPS", "FEDX", "DPD", "GLS", "TNT"]),
         dims=[("carrier_name", "Trading name.",
                ["Deutsche Post DHL", "United Parcel", "Federal Express", "DPDgroup",
                 "General Logistics", "TNT Express"]),
               ("contract_tier", "Commercial tier.", ["spot", "committed", "strategic"]),
               ("hq_country", "Country of the carrier's headquarters.", ["DE", "US", "US", "FR", "NL", "NL"])],
         metrics=[("sla_hours", "long", "Contracted delivery SLA in hours.", ("pick", [24, 48, 72, 96])),
                  ("cost_index", "double", "Relative cost index, 1.0 is the baseline.", ("uni", 0.8, 1.6))],
         rows=6, exhaustive=True),
    dict(key="dim_warehouses", domain="Warehouse inventory", purpose="Warehouse reference data.",
         time_name="opened_ts", time_desc="Date the warehouse opened.",
         entity=("warehouse_code", "Warehouse code. Joins to the fact table.",
                 ["wh-lon", "wh-man", "wh-bir", "wh-gla", "wh-bri"]),
         dims=[("warehouse_name", "Site name.", ["London Gateway", "Manchester North", "Birmingham Central",
                                                 "Glasgow East", "Bristol South"]),
               ("automation_level", "Degree of automation.", ["manual", "semi_auto", "goods_to_person"]),
               ("region", "Region the site serves.", ["south_east", "north_west", "midlands",
                                                      "scotland", "south_west"])],
         metrics=[("pallet_capacity", "long", "Pallet positions on site.", ("uni", 4000, 68000)),
                  ("dock_doors", "long", "Loading dock doors.", ("uni", 6, 60))],
         rows=5, exhaustive=True),
    dict(key="dim_segments", domain="Marketing automation", purpose="Audience segment definitions.",
         time_name="defined_ts", time_desc="Date the segment was defined.",
         entity=("segment", "Segment key. Joins to the fact table.",
                 ["new", "active", "lapsed", "vip", "winback"]),
         dims=[("segment_label", "Human-readable segment name.",
                ["New signups", "Active buyers", "Lapsed 90d", "Top spenders", "Winback target"]),
               ("owner_team", "Team that owns the segment.", ["growth", "lifecycle", "crm", "growth", "crm"]),
               ("refresh_cadence", "How often membership is recomputed.", ["hourly", "daily", "weekly"])],
         metrics=[("member_count", "long", "Members at definition time.", ("uni", 900, 480000)),
                  ("target_open_rate", "double", "Open rate the team targets.", ("uni", 0.08, 0.52))],
         rows=5, exhaustive=True),
    dict(key="dim_stores", domain="Retail point of sale", purpose="Store estate reference data.",
         time_name="opened_ts", time_desc="Date the store opened.",
         entity=("store_code", "Store code. Joins to the fact table.",
                 ["st-001", "st-002", "st-003", "st-004", "st-005", "st-006", "st-007"]),
         dims=[("store_name", "Trading name of the branch.",
                ["Kings Cross", "Deansgate", "Bull Ring", "Buchanan St", "Cabot Circus",
                 "Broadmead", "Trinity Leeds"]),
               ("store_format", "Estate format.", ["metro", "superstore", "express", "outlet"]),
               ("catchment", "Catchment classification.", ["urban", "suburban", "retail_park"])],
         metrics=[("floor_sqm", "long", "Trading floor area in square metres.", ("uni", 180, 9800)),
                  ("checkout_lanes", "long", "Checkout lanes installed.", ("uni", 3, 42))],
         rows=7, exhaustive=True),
]
