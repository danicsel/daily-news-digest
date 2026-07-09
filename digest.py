import os, json, datetime as dt, pathlib, html
import yaml, feedparser
from anthropic import Anthropic

INTERESTS = "Romania news, healthcare innovation, Apple ecosystem and general technology"
LOOKBACK_H = 26
TOPIC_ORDER = ["Romania", "Healthcare", "Tech"]
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
        return items
    try:
        client = Anthropic(api_key=key)
        listing = "\n".join(f'{i}. [{x["source"]}] {x["title"]} — {x["snippet"]}'
                            for i, x in enumerate(items))
        prompt = (f"My interests: {INTERESTS}.\n\nToday's headlines:\n{listing}\n\n"
                  "Return ONLY a JSON array of the genuinely relevant, non-duplicate ones, "
                  'each: {"i": <index>, "score": 1-5, "line": "<one-sentence why-it-matters>"}. '
                  "Skip filler. No prose, no code fences.")
        r = client.messages.create(model="claude-haiku-4-5", max_tokens=2000,
                                   messages=[{"role": "user", "content": prompt}])
        picks = json.loads(r.content[0].text)
        out = []
        for p in picks:
            it = items[p["i"]]; it["score"] = p["score"]; it["line"] = p["line"]
            out.append(it)
        return sorted(out, key=lambda x: -x.get("score", 0))
    except Exception as ex:
        print(f"Triage skipped ({ex}); showing all items.")
        return items

def render(items):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)  # Bucharest
    by = {}
    for it in items:
        by.setdefault(it["topic"], []).append(it)
    sections = []
    for topic in TOPIC_ORDER + [t for t in by if t not in TOPIC_ORDER]:
        xs = by.get(topic)
        if not xs:
            continue
        rows = []
        for x in xs:
            note = f'<span class="note">{html.escape(x["line"])}</span>' if x.get("line") else ""
            rows.append(
                f'<li><a href="{html.escape(x["link"])}">{html.escape(x["title"])}</a>'
                f'<span class="src">{html.escape(x["source"])}</span>{note}</li>')
        sections.append(f'<section><h2>{html.escape(topic)}</h2><ul>{"".join(rows)}</ul></section>')
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
color:var(--accent);margin:0 0 12px}}
ul{{list-style:none;margin:0;padding:0}}li{{padding:14px 0;border-bottom:1px solid var(--line)}}
a{{color:var(--ink);text-decoration:none;font-weight:600;font-size:17px}}
a:hover{{color:var(--accent)}}.src{{color:var(--sub);font-size:13px;margin-left:8px}}
.note{{display:block;color:var(--sub);font-size:14px;margin-top:3px}}
.empty{{color:var(--sub)}}
</style></head><body><div class="wrap"><header>
<h1>Daily Brief</h1><div class="date">{now:%A, %d %B %Y · %H:%M} Bucharest</div>
</header>{body}</div></body></html>"""

if __name__ == "__main__":
    feeds = yaml.safe_load(open("feeds.yaml"))
    out = pathlib.Path("_site"); out.mkdir(exist_ok=True)
    (out / "index.html").write_text(render(triage(fetch(feeds))), encoding="utf-8")
    print("Wrote _site/index.html")
