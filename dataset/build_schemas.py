#!/usr/bin/env python3
"""Generate datasource specs, seed data, and schema blocks for the Druid SFT dataset.

Seed rows span the 30 days ending at generation time so that CURRENT_TIMESTAMP-relative
queries return rows. Re-run this (and reload) if the data goes stale.

Not AI training or inference code: this only produces Druid fixtures.
"""

from __future__ import annotations

import json
import random
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPECS, SEEDS = ROOT / "specs", ROOT / "seeds"
# The anchor is pinned in dataset_meta.json. Re-running the generators must
# reproduce the seed rows byte for byte, otherwise the data already ingested
# into Druid no longer matches the queries validated against it.
_META = json.loads((ROOT / "dataset_meta.json").read_text())
NOW = datetime.fromisoformat(_META["anchor"])
SPAN_DAYS = 30

# Druid SQL type shown in the schema block, per loader type.
SQL_TYPE = {"long": "BIGINT", "double": "DOUBLE", "float": "FLOAT",
            "string": "VARCHAR", "array<string>": "VARCHAR"}


def ts_millis(rng: random.Random) -> int:
    delta = timedelta(seconds=rng.uniform(0, SPAN_DAYS * 86400))
    return int(((NOW - timedelta(days=SPAN_DAYS)) + delta).timestamp() * 1000)


def pick(rng, seq):
    return rng.choice(seq)


# ---------------------------------------------------------------- schema defs
# Each schema: id, datasource name, one-line purpose, columns, row builder.
# A column is (name, loader_type, description). The time column comes first and is
# NEVER named __time -- the loader maps it (see druid_dataset_creation.md harness notes).

SCHEMAS: list[dict] = []


def schema(**kw):
    SCHEMAS.append(kw)
    return kw


schema(
    id="web_events",
    datasource="ds_web_events",
    domain="Web / product analytics",
    purpose="Clickstream events from a consumer web app, enriched with device and experiment data.",
    time_col="event_ts",
    columns=[
        ("event_ts", "long", "Ingested event time. Becomes `__time`, the only TIMESTAMP column."),
        ("session_id", "string", "Opaque session identifier."),
        ("user_id", "string", "Hashed user identifier. High cardinality."),
        ("event_type", "string", "One of: page_view, add_to_cart, checkout, purchase, search."),
        ("page_path", "string", "URL path of the page the event fired on."),
        ("country_code", "string", "ISO-3166 alpha-2 country code."),
        ("device_type", "string", "One of: desktop, mobile, tablet."),
        ("browser", "string", "Browser family."),
        ("os_name", "string", "Operating system family."),
        ("referrer_host", "string", "Hostname of the HTTP referrer."),
        ("ab_bucket", "string", "A/B assignment bucket: control, variant_a, variant_b."),
        ("latency_ms", "long", "Server-side request latency in milliseconds."),
        ("status_code", "long", "HTTP status code."),
        ("bytes_sent", "long", "Response size in bytes."),
        ("cart_items", "long", "Number of items in the cart at event time."),
        ("revenue_usd", "double", "Revenue attributed to the event, in USD. Zero for non-purchase events."),
        ("experiment_tags", "array<string>", "Multi-value dimension. Active experiment flags on this request."),
        ("attrs_json", "string", "JSON object stored as a string. Parse with PARSE_JSON before JSON_VALUE. Keys: campaign, tier, is_bot."),
        ("request_started_at_ms", "long", "Client-side request start time as epoch MILLISECONDS. Not a TIMESTAMP -- convert with MILLIS_TO_TIMESTAMP."),
    ],
    rows=600,
)

schema(
    id="ad_impressions",
    datasource="ds_ad_impressions",
    domain="Ad tech",
    purpose="Programmatic ad impressions and bid outcomes.",
    time_col="log_ts",
    columns=[
        ("log_ts", "long", "Log arrival time. Becomes `__time`."),
        ("advertiser_id", "string", "Advertiser account identifier."),
        ("campaign_name", "string", "Human-readable campaign name."),
        ("creative_id", "string", "Creative identifier."),
        ("placement", "string", "Placement slot: banner_top, sidebar, interstitial, native_feed."),
        ("exchange", "string", "Supply-side exchange the bid came from."),
        ("geo_region", "string", "Region code of the viewer."),
        ("creative_tags", "array<string>", "Multi-value dimension. Creative labels, e.g. video, retargeting, holiday."),
        ("bid_cpm_usd", "double", "Bid price in USD CPM."),
        ("win_price_usd", "double", "Clearing price in USD CPM. Zero when the bid lost."),
        ("was_clicked", "long", "1 if the impression was clicked, else 0."),
        ("served_at_epoch_s", "long", "Ad server timestamp as epoch SECONDS (not milliseconds). Multiply by 1000 before MILLIS_TO_TIMESTAMP."),
    ],
    rows=500,
)

schema(
    id="iot_readings",
    datasource="ds_iot_readings",
    domain="Industrial IoT",
    purpose="Sensor telemetry from factory-floor equipment.",
    time_col="ingest_ts",
    columns=[
        ("ingest_ts", "long", "Pipeline ingestion time. Becomes `__time`."),
        ("device_id", "string", "Sensor device identifier."),
        ("site", "string", "Plant site code."),
        ("machine_class", "string", "Equipment class: press, conveyor, welder, kiln."),
        ("metric_name", "string", "Measured quantity: temperature_c, vibration_mm_s, pressure_kpa."),
        ("value", "double", "The measurement itself. `value` is a reserved word -- it must be double-quoted in SQL."),
        ("quality_flag", "string", "Reading quality: good, suspect, bad."),
        ("firmware", "string", "Device firmware version string."),
        ("reading_taken_at", "string", "On-device measurement time as a string in 'yyyy-MM-dd HH:mm:ss' format. Not a TIMESTAMP -- convert with TIME_PARSE."),
    ],
    rows=500,
)

schema(
    id="orders",
    datasource="ds_orders",
    domain="E-commerce",
    purpose="Order lines from the storefront. Joins to ds_products on product_sku.",
    time_col="order_ts",
    columns=[
        ("order_ts", "long", "Order placement time. Becomes `__time`."),
        ("order_id", "string", "Order identifier."),
        ("customer_id", "string", "Customer identifier."),
        ("product_sku", "string", "Stock keeping unit. Joins to ds_products.sku."),
        ("channel", "string", "Sales channel: web, ios, android, partner."),
        ("ship_country", "string", "Destination country code."),
        ("quantity", "long", "Units ordered."),
        ("unit_price_usd", "double", "Price per unit in USD."),
        ("discount_usd", "double", "Discount applied to the line in USD."),
        ("payment_method", "string", "Payment instrument: card, paypal, giftcard, invoice."),
    ],
    rows=500,
    partners=["products"],
)

schema(
    id="products",
    datasource="ds_products",
    domain="E-commerce",
    purpose="Product catalogue dimension table. Joined from ds_orders.",
    time_col="catalogue_ts",
    columns=[
        ("catalogue_ts", "long", "Catalogue snapshot time. Becomes `__time`."),
        ("sku", "string", "Stock keeping unit. Join key from ds_orders.product_sku."),
        ("product_name", "string", "Display name."),
        ("category", "string", "Top-level category: apparel, electronics, home, grocery, toys."),
        ("brand", "string", "Brand name."),
        ("supplier_country", "string", "Country the supplier ships from."),
        ("list_price_usd", "double", "Current list price in USD."),
        ("weight_grams", "long", "Shipping weight in grams."),
    ],
    rows=120,
    partners=["orders"],
)

schema(
    id="fin_txn",
    datasource="ds_fin_txn",
    domain="Payments / fintech",
    purpose="Card authorisation and settlement events. Note the camelCase naming convention.",
    time_col="authTs",
    columns=[
        ("authTs", "long", "Authorisation time. Becomes `__time`."),
        ("txnId", "string", "Transaction identifier."),
        ("merchantId", "string", "Merchant identifier."),
        ("merchantCategory", "string", "MCC description: grocery, travel, fuel, dining, online."),
        ("cardNetwork", "string", "Network: visa, mastercard, amex."),
        ("issuerCountry", "string", "Issuing bank country code."),
        ("acquirerCountry", "string", "Acquiring bank country code."),
        ("currencyCode", "string", "ISO-4217 currency code."),
        ("amountMinor", "long", "Transaction amount in minor units (cents)."),
        ("fxRate", "double", "FX rate applied to convert to USD."),
        ("authResult", "string", "Outcome: approved, declined, referred."),
        ("riskScore", "double", "Fraud model score between 0 and 1."),
        ("settledAt", "string", "Settlement time as an ISO-8601 string, format \"yyyy-MM-dd'T'HH:mm:ss'Z'\". Not a TIMESTAMP -- convert with TIME_PARSE."),
        ("isRecurring", "long", "1 if part of a recurring mandate, else 0."),
    ],
    rows=600,
)

schema(
    id="sec_alerts",
    datasource="ds_sec_alerts",
    domain="Security operations",
    purpose="Detection alerts from the SIEM pipeline, enriched with host and rule metadata.",
    time_col="detected_ts",
    columns=[
        ("detected_ts", "long", "Detection time. Becomes `__time`."),
        ("alert_id", "string", "Alert identifier."),
        ("host_id", "string", "Host identifier. Resolvable through the `host_tier` lookup."),
        ("rule_name", "string", "Detection rule that fired."),
        ("severity", "string", "Severity: low, medium, high, critical."),
        ("src_ip", "string", "Source IP address."),
        ("dest_port", "long", "Destination port."),
        ("bytes_transferred", "long", "Bytes moved during the flagged activity."),
        ("alert_tags", "array<string>", "Multi-value dimension. MITRE-style labels, e.g. persistence, lateral_movement."),
        ("analyst_verdict", "string", "Triage outcome: true_positive, false_positive, pending."),
        ("enrichment_json", "string", "JSON object stored as a string. Parse with PARSE_JSON before JSON_VALUE. Keys: asset_owner, env, patch_level."),
    ],
    rows=500,
    lookups=[("host_tier", "host_id", "Maps host_id to its criticality tier: gold, silver, bronze.")],
)

schema(
    id="game_sessions",
    datasource="ds_game_sessions",
    domain="Gaming",
    purpose="Player session summaries from a live-service title.",
    time_col="session_start_ts",
    columns=[
        ("session_start_ts", "long", "Session start. Becomes `__time`."),
        ("player_id", "string", "Player identifier."),
        ("platform", "string", "Platform: pc, xbox, playstation, switch, mobile."),
        ("game_mode", "string", "Mode played: ranked, casual, coop, custom."),
        ("region", "string", "Matchmaking region."),
        ("duration_seconds", "long", "Session length in seconds."),
        ("xp_earned", "long", "Experience points earned in the session."),
    ],
    rows=400,
)

schema(
    id="telco_cdr",
    datasource="ds_telco_cdr",
    domain="Telecommunications",
    purpose="Call detail records with heavy subscriber and network enrichment. Wide production-shaped schema.",
    time_col="cdr_ts",
    columns=[
        ("cdr_ts", "long", "CDR write time. Becomes `__time`."),
        ("subscriber_id", "string", "Subscriber identifier."),
        ("msisdn_hash", "string", "Hashed phone number."),
        ("imsi_prefix", "string", "First six digits of the IMSI."),
        ("call_type", "string", "Type: voice, sms, data, roaming_voice."),
        ("direction", "string", "Direction: inbound, outbound."),
        ("cell_id", "string", "Serving cell identifier."),
        ("lac", "string", "Location area code."),
        ("network_gen", "string", "Radio generation: 3g, 4g, 5g."),
        ("device_model", "string", "Handset model."),
        ("device_vendor", "string", "Handset manufacturer."),
        ("plan_name", "string", "Tariff plan name."),
        ("plan_tier", "string", "Plan tier: prepaid, postpaid, enterprise."),
        ("contract_status", "string", "Status: active, suspended, churned."),
        ("home_region", "string", "Subscriber home region."),
        ("serving_region", "string", "Region serving the call."),
        ("roaming_partner", "string", "Roaming partner network, or 'none'."),
        ("duration_seconds", "long", "Call duration in seconds."),
        ("bytes_up", "long", "Uplink bytes."),
        ("bytes_down", "long", "Downlink bytes."),
        ("dropped_flag", "long", "1 if the call dropped, else 0."),
        ("setup_latency_ms", "long", "Call setup latency in milliseconds."),
        ("rated_cost_usd", "double", "Rated cost of the record in USD."),
        ("signal_dbm", "double", "Average signal strength in dBm."),
    ],
    rows=700,
)


# ---------------------------------------------------------------- row builders
POOLS = {
    "session_id": [f"sess-{i:04d}" for i in range(200)],
    "user_id": [f"u{i:05d}" for i in range(300)],
    "event_type": ["page_view", "add_to_cart", "checkout", "purchase", "search"],
    "page_path": ["/", "/search", "/product", "/cart", "/checkout", "/account", "/help"],
    "country_code": ["US", "GB", "DE", "IN", "BR", "JP", "CA", "AU", "FR", "NG"],
    "device_type": ["desktop", "mobile", "tablet"],
    "browser": ["chrome", "safari", "firefox", "edge"],
    "os_name": ["windows", "macos", "ios", "android", "linux"],
    "referrer_host": ["google.com", "facebook.com", "t.co", "reddit.com", "direct", "bing.com"],
    "ab_bucket": ["control", "variant_a", "variant_b"],
    "advertiser_id": [f"adv-{i:03d}" for i in range(40)],
    "campaign_name": ["spring_launch", "always_on_brand", "retarget_cart", "holiday_push", "app_installs"],
    "creative_id": [f"cr-{i:04d}" for i in range(80)],
    "placement": ["banner_top", "sidebar", "interstitial", "native_feed"],
    "exchange": ["openx", "pubmatic", "rubicon", "adx", "magnite"],
    "geo_region": ["na-east", "na-west", "emea", "apac", "latam"],
    "device_id": [f"dev-{i:04d}" for i in range(120)],
    "site": ["plant-alpha", "plant-beta", "plant-gamma"],
    "machine_class": ["press", "conveyor", "welder", "kiln"],
    "metric_name": ["temperature_c", "vibration_mm_s", "pressure_kpa"],
    "quality_flag": ["good", "good", "good", "suspect", "bad"],
    "firmware": ["1.4.2", "1.5.0", "2.0.1", "2.1.0"],
    "order_id": [f"ord-{i:05d}" for i in range(400)],
    "customer_id": [f"cust-{i:04d}" for i in range(250)],
    "channel": ["web", "ios", "android", "partner"],
    "ship_country": ["US", "GB", "DE", "IN", "BR", "CA", "AU"],
    "payment_method": ["card", "paypal", "giftcard", "invoice"],
    "category": ["apparel", "electronics", "home", "grocery", "toys"],
    "brand": ["northwind", "acme", "globex", "initech", "umbrella", "soylent"],
    "supplier_country": ["CN", "VN", "MX", "PL", "TR", "US"],
    "txnId": [f"txn-{i:06d}" for i in range(600)],
    "merchantId": [f"mrc-{i:04d}" for i in range(150)],
    "merchantCategory": ["grocery", "travel", "fuel", "dining", "online"],
    "cardNetwork": ["visa", "mastercard", "amex"],
    "issuerCountry": ["US", "GB", "DE", "IN", "SG"],
    "acquirerCountry": ["US", "GB", "DE", "NL", "IE"],
    "currencyCode": ["USD", "GBP", "EUR", "INR", "SGD"],
    "authResult": ["approved", "approved", "approved", "declined", "referred"],
    "alert_id": [f"alr-{i:05d}" for i in range(400)],
    "rule_name": ["suspicious_powershell", "impossible_travel", "mass_download",
                  "priv_esc_attempt", "beaconing_dns"],
    "severity": ["low", "medium", "high", "critical"],
    "analyst_verdict": ["true_positive", "false_positive", "pending"],
    "player_id": [f"plr-{i:05d}" for i in range(300)],
    "platform": ["pc", "xbox", "playstation", "switch", "mobile"],
    "game_mode": ["ranked", "casual", "coop", "custom"],
    "region": ["na", "eu", "apac", "sa"],
    "subscriber_id": [f"sub-{i:05d}" for i in range(400)],
    "call_type": ["voice", "sms", "data", "roaming_voice"],
    "direction": ["inbound", "outbound"],
    "network_gen": ["3g", "4g", "5g"],
    "device_model": ["pixel-8", "galaxy-s24", "iphone-15", "iphone-13", "moto-g", "redmi-12"],
    "device_vendor": ["google", "samsung", "apple", "motorola", "xiaomi"],
    "plan_name": ["freedom_5g", "value_talk", "biz_unlimited", "student_saver"],
    "plan_tier": ["prepaid", "postpaid", "enterprise"],
    "contract_status": ["active", "active", "suspended", "churned"],
    "home_region": ["north", "south", "east", "west"],
    "serving_region": ["north", "south", "east", "west"],
    "roaming_partner": ["none", "none", "none", "vodafone-eu", "att-us", "docomo-jp"],
}
EXPERIMENT_TAGS = ["new_checkout", "fast_search", "price_test", "banner_v2", "recs_v3"]
CREATIVE_TAGS = ["video", "retargeting", "holiday", "static", "carousel"]
ALERT_TAGS = ["persistence", "lateral_movement", "exfiltration", "credential_access", "discovery"]
HOSTS = [f"host-{i:03d}" for i in range(60)]
SKUS = [f"SKU-{i:04d}" for i in range(120)]


def build_row(sid: str, cols, rng: random.Random, tcol: str) -> dict:
    row: dict = {}
    t = ts_millis(rng)
    for name, ctype, _ in cols:
        if name == tcol:
            row[name] = t
        elif name in POOLS:
            row[name] = pick(rng, POOLS[name])
        elif name == "experiment_tags":
            row[name] = rng.sample(EXPERIMENT_TAGS, rng.randint(1, 3))
        elif name == "creative_tags":
            row[name] = rng.sample(CREATIVE_TAGS, rng.randint(1, 3))
        elif name == "alert_tags":
            row[name] = rng.sample(ALERT_TAGS, rng.randint(1, 3))
        elif name == "attrs_json":
            row[name] = json.dumps({"campaign": pick(rng, ["spring", "holiday", "evergreen", "none"]),
                                    "tier": pick(rng, ["free", "plus", "pro"]),
                                    "is_bot": pick(rng, ["yes", "no"])})
        elif name == "enrichment_json":
            row[name] = json.dumps({"asset_owner": pick(rng, ["platform", "payments", "data", "corp-it"]),
                                    "env": pick(rng, ["prod", "staging", "dev"]),
                                    "patch_level": pick(rng, ["current", "n-1", "n-2", "unknown"])})
        elif name == "host_id":
            row[name] = pick(rng, HOSTS)
        elif name in ("product_sku", "sku"):
            row[name] = pick(rng, SKUS)
        elif name == "product_name":
            row[name] = f"{pick(rng, POOLS['brand'])} item {rng.randint(100, 999)}"
        elif name == "request_started_at_ms":
            row[name] = t - rng.randint(50, 5000)
        elif name == "served_at_epoch_s":
            row[name] = (t - rng.randint(1000, 20000)) // 1000
        elif name == "reading_taken_at":
            dt = datetime.fromtimestamp((t - rng.randint(1000, 60000)) / 1000, tz=timezone.utc)
            row[name] = dt.strftime("%Y-%m-%d %H:%M:%S")
        elif name == "settledAt":
            dt = datetime.fromtimestamp((t + rng.randint(3600, 172800) * 1000) / 1000, tz=timezone.utc)
            row[name] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif name in ("src_ip",):
            row[name] = f"10.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(1,254)}"
        elif name in ("msisdn_hash",):
            row[name] = f"h{rng.randrange(16**10):010x}"
        elif name in ("imsi_prefix",):
            row[name] = f"{rng.randint(200000, 999999)}"
        elif name in ("cell_id",):
            row[name] = f"cell-{rng.randint(1000, 9999)}"
        elif name in ("lac",):
            row[name] = f"lac-{rng.randint(10, 99)}"
        elif ctype == "long":
            row[name] = _long_value(name, rng)
        elif ctype == "double":
            row[name] = _double_value(name, rng)
        elif ctype == "float":
            row[name] = round(rng.uniform(0, 100), 3)
        else:
            row[name] = f"{name}_{rng.randint(0, 6)}"
    return row


def _long_value(name: str, rng: random.Random) -> int:
    if name == "latency_ms":
        return int(rng.lognormvariate(4.2, 0.9))
    if name == "status_code":
        return pick(rng, [200, 200, 200, 200, 201, 301, 400, 404, 500, 503])
    if name == "bytes_sent":
        return rng.randint(200, 900_000)
    if name == "cart_items":
        return rng.randint(0, 12)
    if name == "was_clicked":
        return 1 if rng.random() < 0.04 else 0
    if name == "dest_port":
        return pick(rng, [22, 80, 443, 445, 3389, 8080, 9200])
    if name == "bytes_transferred":
        return rng.randint(1_000, 50_000_000)
    if name == "quantity":
        return rng.randint(1, 6)
    if name == "weight_grams":
        return rng.randint(50, 8000)
    if name == "amountMinor":
        return rng.randint(199, 250_000)
    if name == "isRecurring":
        return 1 if rng.random() < 0.18 else 0
    if name == "duration_seconds":
        return rng.randint(30, 7200)
    if name == "xp_earned":
        return rng.randint(0, 5000)
    if name in ("bytes_up", "bytes_down"):
        return rng.randint(1_000, 200_000_000)
    if name == "dropped_flag":
        return 1 if rng.random() < 0.06 else 0
    if name == "setup_latency_ms":
        return int(rng.lognormvariate(5.0, 0.7))
    return rng.randint(0, 1000)


def _double_value(name: str, rng: random.Random) -> float:
    if name == "revenue_usd":
        return round(rng.choice([0.0, 0.0, 0.0, rng.uniform(5, 400)]), 2)
    if name == "bid_cpm_usd":
        return round(rng.uniform(0.2, 18.0), 4)
    if name == "win_price_usd":
        return round(rng.choice([0.0, rng.uniform(0.1, 15.0)]), 4)
    if name == "value":
        return round(rng.uniform(-10, 900), 3)
    if name in ("unit_price_usd", "list_price_usd"):
        return round(rng.uniform(3, 900), 2)
    if name == "discount_usd":
        return round(rng.choice([0.0, 0.0, rng.uniform(1, 60)]), 2)
    if name == "fxRate":
        return round(rng.uniform(0.6, 1.4), 5)
    if name == "riskScore":
        return round(rng.betavariate(2, 8), 4)
    if name == "rated_cost_usd":
        return round(rng.uniform(0.0, 12.0), 4)
    if name == "signal_dbm":
        return round(rng.uniform(-120, -50), 1)
    return round(rng.uniform(0, 1000), 3)


def schema_block(defs: list[dict]) -> str:
    """The `# Database Schema` section of the system prompt for these tables."""
    parts = ["# Database Schema"]
    for d in defs:
        parts.append(f"\n## Table: `{d['datasource']}`")
        parts.append(f"{d['purpose']}")
        parts.append("### Columns:")
        for name, ctype, desc in d["columns"]:
            shown = "__time" if name == d["time_col"] else name
            sql_t = "TIMESTAMP" if name == d["time_col"] else SQL_TYPE[ctype]
            parts.append(f"`{shown}` ({sql_t}): {desc}")
        for lname, lcol, ldesc in d.get("lookups", []):
            parts.append(f"\n### Lookups:")
            parts.append(f"`{lname}`: keyed by `{lcol}`. {ldesc} Use LOOKUP({lcol}, '{lname}').")
    return "\n".join(parts)


def main() -> None:
    SPECS.mkdir(parents=True, exist_ok=True)
    SEEDS.mkdir(parents=True, exist_ok=True)
    index = {}
    for d in SCHEMAS:
        rng = random.Random(zlib.crc32(d["id"].encode()))
        rows = [build_row(d["id"], d["columns"], rng, d["time_col"]) for _ in range(d["rows"])]
        seed_path = SEEDS / f"{d['datasource']}.json"
        with seed_path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        spec = {
            "name": d["datasource"],
            "columns": [
                {"name": n, "type": t, **({"is_time": True} if n == d["time_col"] else {})}
                for n, t, _ in d["columns"]
            ],
            "seed": {"mode": "file", "path": f"../seeds/{d['datasource']}.json", "format": "json"},
        }
        (SPECS / f"{d['datasource']}.json").write_text(json.dumps(spec, indent=2) + "\n")
        index[d["id"]] = {
            "id": d["id"], "datasource": d["datasource"], "domain": d["domain"],
            "time_col": d["time_col"], "rows": d["rows"],
            "partners": d.get("partners", []), "lookups": d.get("lookups", []),
            "columns": d["columns"],
        }
    (ROOT / "schema_index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"generation anchor (UTC): {NOW.isoformat()}  span: {SPAN_DAYS}d")
    for d in SCHEMAS:
        print(f"  {d['datasource']:22} {len(d['columns']):3d} cols  {d['rows']:4d} rows")


if __name__ == "__main__":
    main()
