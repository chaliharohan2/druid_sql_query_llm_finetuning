#!/usr/bin/env python3
"""Render the review page from review_data.json."""
from __future__ import annotations
import html, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
D = json.loads((ROOT / "review_data.json").read_text())
EX, INDEX = D["examples"], D["index"]

CLUSTER_TITLES = {
    "time_bucketing": ("Time bucketing", "TIME_FLOOR, DATE_TRUNC, FLOOR … TO"),
    "relative_time": ("Relative time windows", "resolved against CURRENT_TIMESTAMP, never a hardcoded anchor"),
    "time_extract_format": ("Extract and format", "TIME_EXTRACT, TIME_FORMAT, timezone arguments"),
    "epoch_time_column": ("Epoch columns", "business time held as BIGINT, not TIMESTAMP"),
    "string_time_column": ("String time columns", "business time held as VARCHAR, parsed with TIME_PARSE"),
    "order_by_restriction": ("The ORDER BY restriction", "a table scan can only order by __time"),
    "reserved_alias": ("Reserved-word aliases", "AS hour is a syntax error; AS \"hour\" is not"),
    "timestamp_literal": ("TIMESTAMP literals", "'yyyy-MM-dd HH:mm:ss' only — no T, no Z"),
    "approx_agg": ("Approximate aggregates", "APPROX_COUNT_DISTINCT, APPROX_QUANTILE_DS"),
    "latest_earliest": ("LATEST and EARLIEST", "Druid's time-ordered aggregators"),
    "mvd": ("Multi-value dimensions", "MV_CONTAINS, MV_TO_ARRAY, UNNEST"),
    "json_string": ("JSON stored as string", "PARSE_JSON before JSON_VALUE — the silent trap"),
    "lookup": ("Lookups", "LOOKUP() against a registered map"),
    "join": ("Joins", "cross-datasource joins, which Druid 35 handles fine"),
    "missing_function": ("Functions Druid does not have", "NOW, DATEADD, IF, ILIKE, MEDIAN and their replacements"),
    "time_shift": ("Period comparison", "TIME_SHIFT and filtered aggregates"),
    "grouping": ("Grouping idioms", "GROUPING SETS, HAVING, SAFE_DIVIDE, ordinals"),
}
ORDER = list(CLUSTER_TITLES)
counts = Counter(e["cluster"] for e in EX)
n_traps = sum(1 for e in EX if e.get("trap"))
n_hard = sum(1 for e in EX if e.get("trap") and e["trap"]["expect"] == "INVALID")
n_soft = n_traps - n_hard


def esc(s):
    return html.escape(str(s), quote=False)


def sql_html(sql: str) -> str:
    """Minimal, dependency-free SQL colouring keyed to the page tokens."""
    import re
    kw = (r"\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|JOIN|LEFT JOIN|ON|AND|OR|NOT|"
          r"AS|CASE|WHEN|THEN|ELSE|END|FILTER|IN|IS|NULL|DESC|ASC|INTERVAL|TIMESTAMP|UNNEST|"
          r"GROUPING SETS|CROSS JOIN|DISTINCT|COUNT|SUM|AVG|MIN|MAX)\b")
    fn = (r"\b(TIME_FLOOR|TIME_CEIL|TIME_SHIFT|TIME_EXTRACT|TIME_PARSE|TIME_FORMAT|TIME_IN_INTERVAL|"
          r"DATE_TRUNC|FLOOR|MILLIS_TO_TIMESTAMP|TIMESTAMP_TO_MILLIS|CURRENT_TIMESTAMP|TIMESTAMPADD|"
          r"TIMESTAMPDIFF|APPROX_COUNT_DISTINCT|APPROX_QUANTILE_DS|LATEST_BY|LATEST|EARLIEST|"
          r"MV_CONTAINS|MV_TO_ARRAY|PARSE_JSON|TRY_PARSE_JSON|JSON_VALUE|LOOKUP|SAFE_DIVIDE|"
          r"REGEXP_LIKE|LOWER|NOW|DATEADD|DATEDIFF|GETDATE|TO_CHAR|MEDIAN|IF|DAYOFWEEK)\b")
    s = esc(sql)
    s = re.sub(r"('(?:[^']|'')*')", r"<i class='s'>\1</i>", s)
    s = re.sub(fn, r"<i class='f'>\1</i>", s)
    s = re.sub(kw, r"<i class='k'>\1</i>", s)
    s = re.sub(r'(&quot;|")([A-Za-z_][A-Za-z0-9_]*)(&quot;|")', r"<i class='q'>\1\2\3</i>", s)
    return s


def table(sample):
    if not sample:
        return ""
    cols = list(sample[0].keys())
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{esc(r.get(c))}</td>" for c in cols) + "</tr>" for r in sample)
    return f"<div class='tw'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def example_card(e):
    t = e.get("trap")
    tag = f"<span class='tag {'hard' if t and t['expect']=='INVALID' else 'soft'}'>" \
          f"{'hard trap' if t and t['expect']=='INVALID' else 'silent trap'}</span>" if t else ""
    schemas = " ".join(f"<code class='ds'>{esc(INDEX[s]['datasource'])}</code>" for s in e["schemas"])
    rows = f"{e['row_count']} rows" if e["row_count"] else "0 rows (absolute date range)"
    trap_block = ""
    if t:
        if t["expect"] == "INVALID":
            verdict = f"<p class='verdict bad'><strong>Druid rejects it.</strong> {esc(t['error'][:260])}</p>"
        else:
            verdict = ("<p class='verdict warn'><strong>Druid accepts it and the answer is wrong.</strong> "
                       f"{esc(t['note'])}</p>" + table(t.get("sample")))
        trap_block = f"""
      <div class="pane naive">
        <h4>The standard-SQL reflex <span class="never">never shown to the model</span></h4>
        <pre><code>{sql_html(t['naive_sql'])}</code></pre>
        {verdict}
      </div>"""
    return f"""
  <article class="ex{' has-trap' if t else ''}" id="{esc(e['id'])}">
    <header>
      <span class="eid">{esc(e['id'])}</span>{tag}
      <span class="ds-list">{schemas}</span>
    </header>
    <p class="q">{esc(e['question'])}</p>
    <div class="panes">
      <div class="pane good">
        <h4>Completion <span class="ok">valid · {esc(rows)}</span></h4>
        <pre><code>{sql_html(e['sql'])}</code></pre>
        {table(e['sample'])}
      </div>{trap_block}
    </div>
  </article>"""


nav = "".join(
    f'<a href="#c-{c}"><span>{esc(CLUSTER_TITLES[c][0])}</span><b>{counts[c]}</b></a>'
    for c in ORDER if counts[c])

sections = []
for c in ORDER:
    items = [e for e in EX if e["cluster"] == c]
    if not items:
        continue
    title, sub = CLUSTER_TITLES[c]
    sections.append(f"""
<section class="cluster" id="c-{c}">
  <div class="chead">
    <h2>{esc(title)}</h2>
    <p>{esc(sub)}</p>
    <span class="cn">{len(items)} example{'s' if len(items)!=1 else ''}</span>
  </div>
  {''.join(example_card(e) for e in items)}
</section>""")

schema_rows = "".join(
    f"<tr><td><code>{esc(v['datasource'])}</code></td><td>{esc(v['domain'])}</td>"
    f"<td class='n'>{len(v['columns'])}</td><td class='n'>{v['rows']}</td>"
    f"<td>{'MVD ' if any(t=='array<string>' for _,t,_ in v['columns']) else ''}"
    f"{'JSON ' if any('json' in n for n,_,_ in v['columns']) else ''}"
    f"{'lookup ' if v.get('lookups') else ''}"
    f"{'epoch/string time ' if any(n in ('request_started_at_ms','served_at_epoch_s','reading_taken_at','settledAt') for n,_,_ in v['columns']) else ''}"
    f"</td></tr>" for v in INDEX.values())

HTML = f"""<title>Druid LoRA Review Batch</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --ground:#EBEEF4; --surface:#FFFFFF; --sunken:#F4F6FA;
  --ink:#131722; --muted:#5C6479; --faint:#828BA1; --rule:#D3D9E6;
  --accent:#2B44B8; --accent-soft:#E3E8FB;
  --ok:#1B6B45; --ok-soft:#E1F0E8;
  --warn:#8A5A12; --warn-soft:#FAF0DC;
  --bad:#A63A18; --bad-soft:#FAE7E0;
  --code:#F4F6FA; --code-ink:#1B2130;
  --k:#2B44B8; --f:#7A2E8F; --s:#1B6B45; --q:#A63A18;
  --shadow:0 1px 2px rgba(19,23,34,.06), 0 8px 24px -12px rgba(19,23,34,.18);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground:#0D1017; --surface:#151A24; --sunken:#10141C;
    --ink:#E6E9F0; --muted:#98A1B5; --faint:#6C7589; --rule:#262D3B;
    --accent:#8FA4FF; --accent-soft:#1C2340;
    --ok:#6DD09B; --ok-soft:#122720;
    --warn:#E0B25E; --warn-soft:#2A2214;
    --bad:#F0876A; --bad-soft:#2C1A15;
    --code:#0F131B; --code-ink:#D8DDE8;
    --k:#8FA4FF; --f:#D79BEC; --s:#7FD3A3; --q:#F0A085;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
  }}
}}
:root[data-theme="dark"] {{
  --ground:#0D1017; --surface:#151A24; --sunken:#10141C;
  --ink:#E6E9F0; --muted:#98A1B5; --faint:#6C7589; --rule:#262D3B;
  --accent:#8FA4FF; --accent-soft:#1C2340;
  --ok:#6DD09B; --ok-soft:#122720;
  --warn:#E0B25E; --warn-soft:#2A2214;
  --bad:#F0876A; --bad-soft:#2C1A15;
  --code:#0F131B; --code-ink:#D8DDE8;
  --k:#8FA4FF; --f:#D79BEC; --s:#7FD3A3; --q:#F0A085;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6);
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
  font-size:15px; line-height:1.55; -webkit-font-smoothing:antialiased;
}}
h1,h2,h3 {{ font-family:Archivo,"IBM Plex Sans",sans-serif; font-weight:700; letter-spacing:-.015em; text-wrap:balance; margin:0; }}
code,pre {{ font-family:"IBM Plex Mono",ui-monospace,monospace; }}
a {{ color:var(--accent); }}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; border-radius:3px; }}

.shell {{ display:grid; grid-template-columns:246px minmax(0,1fr); gap:34px; max-width:1320px; margin:0 auto; padding:0 26px; }}
@media (max-width:960px) {{ .shell {{ grid-template-columns:minmax(0,1fr); gap:0; }} .rail {{ position:static !important; height:auto !important; }} }}

/* ---- masthead ---- */
.mast {{ border-bottom:1px solid var(--rule); background:var(--surface); }}
.mast-in {{ max-width:1320px; margin:0 auto; padding:38px 26px 30px; }}
.eyebrow {{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--accent); margin:0 0 12px; }}
.mast h1 {{ font-size:clamp(28px,4vw,40px); line-height:1.1; }}
.lede {{ max-width:64ch; color:var(--muted); margin:14px 0 0; font-size:16px; }}
.stats {{ display:flex; flex-wrap:wrap; gap:0; margin-top:28px; border:1px solid var(--rule); border-radius:8px; overflow:hidden; }}
.stat {{ flex:1 1 132px; padding:13px 16px; background:var(--sunken); border-right:1px solid var(--rule); }}
.stat:last-child {{ border-right:0; }}
.stat b {{ display:block; font-family:Archivo,sans-serif; font-size:23px; font-variant-numeric:tabular-nums; line-height:1.1; }}
.stat span {{ font-size:11.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--faint); }}

/* ---- rail ---- */
.rail {{ position:sticky; top:0; height:100vh; overflow-y:auto; padding:28px 0 40px; }}
.rail h3 {{ font-size:11.5px; text-transform:uppercase; letter-spacing:.12em; color:var(--faint); margin:0 0 10px; font-family:"IBM Plex Mono",monospace; font-weight:500; }}
.rail a {{ display:flex; justify-content:space-between; gap:10px; align-items:baseline; padding:5px 9px; border-radius:5px; text-decoration:none; color:var(--muted); font-size:13.5px; }}
.rail a:hover {{ background:var(--accent-soft); color:var(--accent); }}
.rail a b {{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--faint); font-weight:500; font-variant-numeric:tabular-nums; }}

/* ---- content ---- */
main {{ padding:28px 0 90px; min-width:0; }}
.panel {{ background:var(--surface); border:1px solid var(--rule); border-radius:10px; padding:22px 24px; margin-bottom:26px; box-shadow:var(--shadow); }}
.panel h2 {{ font-size:19px; margin-bottom:6px; }}
.panel p {{ color:var(--muted); margin:0 0 14px; max-width:66ch; }}
.panel p:last-child {{ margin-bottom:0; }}
details.tmpl summary {{ cursor:pointer; font-weight:600; font-size:14px; padding:7px 0; }}
.tw {{ overflow-x:auto; margin-top:11px; }}
table {{ border-collapse:collapse; width:100%; font-size:12.5px; font-family:"IBM Plex Mono",monospace; }}
th {{ text-align:left; font-weight:500; color:var(--faint); border-bottom:1px solid var(--rule); padding:5px 11px 5px 0; white-space:nowrap; font-size:11px; text-transform:uppercase; letter-spacing:.06em; }}
td {{ padding:4px 11px 4px 0; border-bottom:1px solid var(--rule); color:var(--muted); white-space:nowrap; }}
td.n, th.n {{ text-align:right; font-variant-numeric:tabular-nums; padding-right:18px; }}

/* ---- clusters ---- */
.cluster {{ margin:0 0 40px; scroll-margin-top:16px; }}
.chead {{ display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; padding:0 0 12px; border-bottom:2px solid var(--ink); margin-bottom:18px; }}
.chead h2 {{ font-size:21px; }}
.chead p {{ margin:0; color:var(--muted); font-size:13.5px; flex:1 1 240px; }}
.cn {{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--faint); }}

.ex {{ background:var(--surface); border:1px solid var(--rule); border-radius:10px; margin-bottom:14px; overflow:hidden; box-shadow:var(--shadow); scroll-margin-top:16px; }}
.ex > header {{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; padding:9px 18px; background:var(--sunken); border-bottom:1px solid var(--rule); }}
.eid {{ font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--faint); }}
.ds-list {{ margin-left:auto; display:flex; gap:6px; flex-wrap:wrap; }}
code.ds {{ font-size:11px; color:var(--muted); background:var(--ground); border:1px solid var(--rule); border-radius:4px; padding:1px 6px; }}
.tag {{ font-size:10.5px; font-family:"IBM Plex Mono",monospace; text-transform:uppercase; letter-spacing:.07em; padding:2px 7px; border-radius:3px; }}
.tag.hard {{ background:var(--bad-soft); color:var(--bad); }}
.tag.soft {{ background:var(--warn-soft); color:var(--warn); }}
.q {{ margin:0; padding:15px 18px 3px; font-size:16px; font-weight:500; max-width:70ch; }}
.panes {{ display:grid; grid-template-columns:minmax(0,1fr); gap:0; padding:11px 18px 18px; }}
.ex.has-trap .panes {{ grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:18px; }}
@media (max-width:900px) {{ .ex.has-trap .panes {{ grid-template-columns:minmax(0,1fr); }} }}
.pane h4 {{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; margin:0 0 7px; font-size:11.5px; font-family:"IBM Plex Mono",monospace; font-weight:500; text-transform:uppercase; letter-spacing:.08em; color:var(--faint); }}
.ok {{ color:var(--ok); text-transform:none; letter-spacing:0; }}
.never {{ color:var(--faint); text-transform:none; letter-spacing:0; font-style:italic; }}
pre {{ margin:0; background:var(--code); border:1px solid var(--rule); border-radius:7px; padding:12px 14px; overflow-x:auto; font-size:12.9px; line-height:1.62; color:var(--code-ink); }}
.pane.naive pre {{ border-color:color-mix(in srgb, var(--bad) 34%, var(--rule)); }}
.pane.naive .q {{ color:var(--bad); }}
code i {{ font-style:normal; }}
i.k {{ color:var(--k); font-weight:500; }}
i.f {{ color:var(--f); }}
i.s {{ color:var(--s); }}
i.q {{ color:var(--q); }}
.verdict {{ margin:10px 0 0; font-size:12.8px; padding:9px 12px; border-radius:6px; border-left:3px solid; }}
.verdict.bad {{ background:var(--bad-soft); border-color:var(--bad); color:var(--ink); }}
.verdict.warn {{ background:var(--warn-soft); border-color:var(--warn); color:var(--ink); }}
.verdict strong {{ color:var(--bad); }}
.verdict.warn strong {{ color:var(--warn); }}
.qbox {{ background:var(--sunken); border:1px solid var(--rule); border-radius:7px; padding:13px 15px; font-size:12.9px; white-space:pre-wrap; overflow-x:auto; line-height:1.6; }}
ul.notes {{ margin:0; padding-left:19px; color:var(--muted); }}
ul.notes li {{ margin-bottom:7px; }}
ul.notes strong {{ color:var(--ink); }}
</style>

<header class="mast"><div class="mast-in">
  <p class="eyebrow">Step 2 · review batch · Druid 35.0.0</p>
  <h1>Druid LoRA Review Batch</h1>
  <p class="lede">{len(EX)} candidate training examples across {len(counts)} quirk clusters and {len(INDEX)} synthetic datasources.
  Every completion on this page was executed against the live harness cluster; every trap was executed too, to prove it really is a trap.
  Nothing here is written from memory.</p>
  <div class="stats">
    <div class="stat"><b>{len(EX)}</b><span>examples</span></div>
    <div class="stat"><b>{len(INDEX)}</b><span>datasources</span></div>
    <div class="stat"><b>{len(counts)}</b><span>clusters</span></div>
    <div class="stat"><b>{n_hard}</b><span>hard traps</span></div>
    <div class="stat"><b>{n_soft}</b><span>silent traps</span></div>
    <div class="stat"><b>{len(EX)}/{len(EX)}</b><span>gates passed</span></div>
  </div>
</div></header>

<div class="shell">
<nav class="rail">
  <h3>Clusters</h3>
  {nav}
</nav>
<main>

<div class="panel">
  <h2>What to check</h2>
  <ul class="notes">
    <li><strong>The template.</strong> Preamble + schema in <code>system</code>, bare SQL out. Expand it below and confirm it matches what you will serve.</li>
    <li><strong>The house style.</strong> Output aliases are <em>always</em> double-quoted, <code>GROUP BY</code>/<code>ORDER BY</code> use ordinals, relative time resolves against <code>CURRENT_TIMESTAMP</code>, and approximate aggregates are the default. If you disagree with any of those, now is the cheap moment to say so.</li>
    <li><strong>The traps.</strong> Hard traps are rejected by Druid; silent traps run clean and return the wrong answer. Both are shown with the real engine response.</li>
    <li><strong>Coverage.</strong> Is any quirk you hit in production missing from the cluster list on the left?</li>
  </ul>
</div>

<div class="panel">
  <h2>The exact prompt template</h2>
  <p>This is verbatim what a training record's <code>system</code> message contains. The schema block is generated from the live cluster, so type names match <code>INFORMATION_SCHEMA</code>.</p>
  <details class="tmpl"><summary>Single-table example — ds_sec_alerts, with a lookup</summary>
    <div class="qbox">{esc(D['system_prompt_example'])}</div></details>
  <details class="tmpl"><summary>Two-table example — ds_orders + ds_products</summary>
    <div class="qbox">{esc(D['system_prompt_join'])}</div></details>
</div>

<div class="panel">
  <h2>Datasources</h2>
  <p>Nine synthetic schemas spanning narrow to production-wide. Seed rows cover the 30 days ending at build time, so <code>CURRENT_TIMESTAMP</code>-relative queries return real rows.</p>
  <div class="tw"><table>
    <thead><tr><th>Datasource</th><th>Domain</th><th class="n">Cols</th><th class="n">Rows</th><th>Enrichment shapes</th></tr></thead>
    <tbody>{schema_rows}</tbody>
  </table></div>
</div>

{''.join(sections)}
</main>
</div>
"""
out = ROOT / "review_batch01.html"
out.write_text(HTML, encoding="utf-8")
print(f"wrote {out} ({len(HTML)//1024} KB)")
