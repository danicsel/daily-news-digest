import os, json, datetime as dt, pathlib, html
import yaml, feedparser
from anthropic import Anthropic

INTERESTS = "Romania news, healthcare innovation, Apple ecosystem and general technology"
LOOKBACK_H = 26
TOPIC_ORDER = ["Romania", "Healthcare", "Tech"]
TOP_N = 5
feedparser.USER_AGENT = "news-digest/1.0 (+github actions)"

def fetch(feeds):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=LOOKBACK_H)
    items = []
    for topic, srcs in feeds.items():
        for s in srcs:
            try:
                parsed = feedparser.parse(s["url"])
            except Exception:
                continue
            for e in parsed.entries:
                t = e.get("published_parsed") or e.get("updated_parsed")
                when = dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else None
                if when and when < cutoff:
                    continue
                items.append({"topic": topic, "source": s["name"],
                              "title": e.get("title", ""), "link": e.get("link", ""),
                              "snippet": e.get("summary", "")[:300]})
    return items

def triage(items):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not items:
        return items, {}
    try:
        client = Anthropic(api_key=key)
        listing = "\n".join(f'{i}. [{x["topic"]}·{x["source"]}] {x["title"]} — {x["snippet"]}'
                            for i, x in enumerate(items))
        prompt = (
            f"My interests: {INTERESTS}.\n\nToday's headlines (indexed):\n{listing}\n\n"
            "Tasks:\n"
            "1. For each category (Romania, Healthcare, Tech) pick the 3-5 most important, "
            "genuinely relevant, non-duplicate stories.\n"
            "2. Tag each with a short category pill (e.g. Politics, Business, Economy, AI, "
            "Apple, Policy, Research, Startups, Security).\n"
            "3. Give each a one-sentence 'why it matters' line.\n"
            "4. Write a 1-2 sentence summary of the day for each category.\n\n"
            "Return ONLY this JSON, no prose or code fences:\n"
            '{"summaries":{"Romania":"...","Healthcare":"...","Tech":"..."},'
            '"items":[{"i":<index>,"score":1-5,"pill":"Politics","line":"..."}]}')
        r = client.messages.create(model="claude-haiku-4-5", max_tokens=2500,
                                   messages=[{"role": "user", "content": prompt}])
        data = json.loads(r.content[0].text)
        out = []
        for p in data.get("items", []):
            it = items[p["i"]]
            it["score"] = p.get("score", 0); it["line"] = p.get("line", "")
            it["pill"] = p.get("pill", "")
            out.append(it)
        out.sort(key=lambda x: -x.get("score", 0))
        return out, data.get("summaries", {})
    except Exception as ex:
        print(f"Triage skipped ({ex}); showing all items.")
        return items, {}

def render(items, summaries):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)  # Bucharest
    by = {}
    for it in items:
        by.setdefault(it["topic"], []).append(it)
    sections = []
    for topic in TOPIC_ORDER + [t for t in by if t not in TOPIC_ORDER]:
        xs = by.get(topic)
        if not xs:
            continue
        xs = xs[:TOP_N]
        summary = summaries.get(topic, "")
        summary_html = f'<p class="summary">{html.escape(summary)}</p>' if summary else ""
        rows = []
        for x in xs:
            pill = f'<span class="pill">{html.escape(x["pill"])}</span>' if x.get("pill") else ""
            note = f'<span class="note">{html.escape(x["line"])}</span>' if x.get("line") else ""
            rows.append(
                f'<li><a href="{html.escape(x["link"])}">{html.escape(x["title"])}</a>{pill}'
                f'<span class="src">{html.escape(x["source"])}</span>{note}</li>')
        sections.append(
            f'<section><h2>{html.escape(topic)}</h2>{summary_html}<ul>{"".join(rows)}</ul></section>')
    body = "".join(sections) or "<p class='empty'>Nothing new in the last 26 hours.</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Brief</title><style>
:root{{--bg:#faf9f6;--ink:#1a1a1a;--sub:#6b6b6b;--line:#e6e3dc;--accent:#b5443b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:680px;margin:0 auto;padding:48px 24px 80px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:8px}}
h1{{font-size:30px;margin:0;letter-spacing:-.02em}}
.date{{color:var(--sub);font-size:14px;margin-top:4px}}
section{{margin-top:40px}}h2{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;
color:var(--accent);margin:0 0 8px}}
.summary{{color:var(--sub);font-size:14.5px;line-height:1.55;margin:0 0 16px;
padding-bottom:14px;border-bottom:1px solid var(--line)}}
ul{{list-style:none;margin:0;padding:0}}li{{padding:14px 0;border-bottom:1px solid var(--line)}}
a{{color:var(--ink);text-decoration:none;font-weight:600;font-size:17px}}
a:hover{{color:var(--accent)}}
.pill{{display:inline-block;font-size:10.5px;font-weight:700;text-transform:uppercase;
letter-spacing:.05em;color:var(--accent);background:rgba(181,68,59,.10);
padding:2px 8px;border-radius:999px;margin-left:8px;vertical-align:middle}}
.src{{color:var(--sub);font-size:13px;margin-left:8px}}
.note{{display:block;color:var(--sub);font-size:14px;margin-top:3px}}
.empty{{color:var(--sub)}}
</style></head><body><div class="wrap"><header>
<h1>Daily Brief</h1><div class="date">{now:%A, %d %B %Y · %H:%M} Bucharest</div>
</header>{body}</div></body></html>"""

if __name__ == "__main__":
    feeds = yaml.safe_load(open("feeds.yaml"))
    picked, summaries = triage(fetch(feeds))
    out = pathlib.Path("_site"); out.mkdir(exist_ok=True)
    (out / "index.html").write_text(render(picked, summaries), encoding="utf-8")
    print(f"Wrote _site/index.html ({len(picked)} items)")
