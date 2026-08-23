#!/usr/bin/env python3
"""Run a Druid SQL query from Python.

Edit QUERY below, then:

    python run_query.py

Or import it from another script / notebook (venv must have druid-harness installed):

    from harness.api import run_query
    result = run_query("SELECT COUNT(*) FROM example_events")
    print(result.status, result.sample)

The cluster must already be up (`cd druid-harness && make up`).
"""

from __future__ import annotations

import json
import sys

from harness.api import result_as_dict, run_query

# Replace this with the SQL you want to run.
QUERY = """
SELECT TIME_FLOOR(__time, 'PT1H') AS "hour",
       AVG(latency_ms) AS "avg_latency_ms"
FROM ds_web_events
WHERE event_type = 'checkout'
  AND __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
"""

ROUTER_URL = "http://127.0.0.1:8888"
TIMEOUT_SECONDS = 30
MAX_ROWS = 100


def main() -> int:
    result = run_query(
        QUERY,
        router_url=ROUTER_URL,
        timeout_seconds=TIMEOUT_SECONDS,
        max_rows=MAX_ROWS,
    )
    print(json.dumps(result_as_dict(result), indent=2, default=str))
    return 0 if result.status == "VALID" else 1


if __name__ == "__main__":
    sys.exit(main())
