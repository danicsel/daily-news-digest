import os, json, re, datetime as dt, pathlib, html, traceback
import yaml, feedparser
from anthropic import Anthropic

INTERESTS = "Romania news, healthcare innovation, Apple ecosystem and general technology"
LOOKBACK_H = 26
TOPIC_ORDER = ["Romania", "Healthcare", "Tech"]
TOP_N = 5
feedparser.USER_AGENT = "news-digest/1.0 (+github actions)"

def clean_text(t):
    t = re.sub(r"<[^>]+>", "", t or "")
    return html.unescape(t).strip()

def extract_image(e):
    # 1. Media RSS <media:content> / <media:thumbnail>
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            url = m.get("url")
            if url and (m.get("medium") in (None, "image") or re.search(r"\.(jpg|jpeg|png|webp|gif)", url, re.I)):
                return url
    # 2. Enclosures / links marked as images
    for l in e.get("links", []) or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            return l.get("href")
    # 3. First <img> inside content or summary HTML
    blobs = [c.get("value", "") for c in e.get("content", []) or []]
    blobs.append(e.get("summary", ""))
    for b in blobs:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', b or "")
        if m:
            return m.group(1)
    return None

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
                              "title": clean_text(e.get("title", "")),
                              "link": e.get("link", ""),
                              "image": extract_image(e),
                              "snippet": clean_text(e.get("summary", ""))[:300]})
    return items

def extract_json(raw):
    s = raw.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
        if m:
            s = m.group(1).strip()
    if not s.startswith("{"):
        a, b = s.find("{"), s.rfind("}")
        if a != -1 and b != -1:
            s = s[a:b+1]
    return json.loads(s)

def triage(items):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("!!! No ANTHROPIC_API_KEY in environment.")
        return items, {}
    if not items:
        print("!!! fetch() returned 0 items.")
        return items, {}
    try:
        client = Anthropic(api_key=key)
        listing = "\n".join(f'{i}. [{x["topic"]}\u00b7{x["source"]}] {x["title"]} \u2014 {x["snippet"]}'
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
        raw = r.content[0].text
        print("=== RAW CLAUDE RESPONSE ===")
        print(raw[:4000])
        print("=== END ===")
        data = extract_json(raw)
        out = []
        for p in data.get("items", []):
            it = items[p["i"]]
            it["score"] = p.get("score", 0); it["line"] = p.get("line", "")
            it["pill"] = p.get("pill", "")
            out.append(it)
        out.sort(key=lambda x: -x.get("score", 0))
        print(f"Triage OK: {len(out)} items, summaries={list(data.get('summaries', {}).keys())}")
        return out, data.get("summaries", {})
    except Exception as ex:
        print(f"!!! Triage FAILED: {type(ex).__name__}: {ex}")
        traceback.print_exc()
        return items, {}

def render(items, summaries, ai_on):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
    issue = now.timetuple().tm_yday
    by = {}
    for it in items:
        by.setdefault(it["topic"], []).append(it)

    def card(x, lead=False):
        pill = f'<span class="pill">{html.escape(x["pill"])}</span>' if x.get("pill") else ""
        line = f'<p class="note">{html.escape(x["line"])}</p>' if x.get("line") else ""
        img = ""
        if x.get("image"):
            img = (f'<a class="thumb" href="{html.escape(x["link"])}" tabindex="-1">'
                   f'<img loading="lazy" src="{html.escape(x["image"])}" alt="" '
                   f'onerror="this.parentNode.remove()"></a>')
        cls = "item lead" if lead else "item"
        return (f'<article class="{cls}">{img}<div class="body">'
                f'<div class="meta">{pill}<span class="src">{html.escape(x["source"])}</span></div>'
                f'<h3><a href="{html.escape(x["link"])}">{html.escape(x["title"])}</a></h3>'
                f'{line}</div></article>')

    sections = []
    for topic in TOPIC_ORDER + [t for t in by if t not in TOPIC_ORDER]:
        xs = by.get(topic)
        if not xs:
            continue
        xs = xs[:TOP_N]
        n = f'{len(xs):02d}'
        summ = summaries.get(topic, "")
        summ_html = f'<p class="standfirst">{html.escape(summ)}</p>' if summ else ""
        lead = card(xs[0], lead=True)
        rest = "".join(card(x) for x in xs[1:])
        rest_html = f'<div class="grid">{rest}</div>' if rest else ""
        sections.append(
            f'<section class="section"><div class="eyebrow">'
            f'<span class="topic">{html.escape(topic)}</span>'
            f'<span class="rule"></span><span class="count">{n}</span></div>'
            f'{summ_html}{lead}{rest_html}</section>')

    body = "".join(sections) or '<p class="empty">Nothing new in the last 26 hours.</p>'
    banner = "" if ai_on else ('<div class="banner">AI curation off \u2014 showing raw feeds. '
                               'Check the workflow log for the reason.</div>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Brief \u2014 {now:%d %b %Y}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400;1,6..72,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{
  --ink:#1b1a17; --paper:#f0eee7; --petrol:#0e4d52; --brass:#9c7b34;
  --muted:#6c685e; --line:#dcd8ce;
}}
*{{box-sizing:border-box}}
html{{-webkit-text-size-adjust:100%}}
body{{margin:0;background:var(--paper);color:var(--ink);
  font-family:"Newsreader",Georgia,serif;
  font-optical-sizing:auto;line-height:1.5;
  background-image:radial-gradient(circle at 1px 1px, rgba(27,26,23,.035) 1px, transparent 0);
  background-size:22px 22px;}}
.wrap{{max-width:1120px;margin:0 auto;padding:40px 28px 96px}}
.masthead{{border-bottom:1px solid var(--ink);padding-bottom:20px;margin-bottom:8px}}
.kicker{{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.28em;
  text-transform:uppercase;color:var(--brass);margin:0 0 10px}}
.nameplate{{font-weight:500;font-size:clamp(42px,8.5vw,74px);line-height:.95;
  letter-spacing:-.02em;margin:0}}
.nameplate em{{font-style:italic;color:var(--petrol)}}
.dateline{{font-family:"IBM Plex Mono",monospace;font-size:12px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);margin-top:16px;
  display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}}
.dateline .dot{{color:var(--brass)}}
.dateline b{{color:var(--ink);font-weight:600}}
.banner{{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--petrol);
  background:rgba(14,77,82,.08);border-left:2px solid var(--petrol);
  padding:10px 14px;margin:20px 0 0;border-radius:2px}}
.section{{margin-top:56px}}
.eyebrow{{display:flex;align-items:center;gap:16px;margin-bottom:18px}}
.eyebrow .topic{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;font-weight:600;
  letter-spacing:.22em;text-transform:uppercase;color:var(--petrol)}}
.eyebrow .rule{{flex:1;height:1px;background:var(--line)}}
.eyebrow .count{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--brass);
  letter-spacing:.1em}}
.standfirst{{font-style:italic;font-size:clamp(17px,2.1vw,21px);line-height:1.5;
  color:#42403a;margin:0 0 26px;max-width:100%}}
.item{{margin:0}}
.item .body{{min-width:0}}
.meta{{display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}}
.pill{{font-family:"IBM Plex Mono",monospace;font-size:10px;font-weight:600;
  letter-spacing:.1em;text-transform:uppercase;color:var(--petrol);
  background:rgba(14,77,82,.10);padding:3px 8px;border-radius:3px}}
.src{{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted)}}
h3{{margin:0;font-weight:500;line-height:1.18;letter-spacing:-.01em}}
h3 a{{color:var(--ink);text-decoration:none}}
h3 a:hover{{color:var(--petrol)}}
.note{{color:var(--muted);font-size:15px;line-height:1.5;margin:8px 0 0;max-width:60ch}}
.thumb{{display:block;overflow:hidden;border-radius:4px;background:var(--line)}}
.thumb img{{display:block;width:100%;height:100%;object-fit:cover}}
.item.lead{{display:grid;grid-template-columns:1.05fr 1fr;gap:28px;align-items:center;
  padding-bottom:34px;margin-bottom:34px;border-bottom:1px solid var(--line)}}
.item.lead .thumb{{aspect-ratio:16/10}}
.item.lead h3{{font-size:clamp(23px,3vw,32px)}}
.item.lead .note{{font-size:16px}}
.item.lead:not(:has(.thumb)){{display:block}}
.item.lead:not(:has(.thumb)) h3{{font-size:clamp(26px,4vw,38px);max-width:20ch}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:30px 34px}}
.grid .item{{display:grid;grid-template-columns:96px 1fr;gap:16px;align-items:start}}
.grid .item .thumb{{aspect-ratio:1/1;width:96px}}
.grid .item:not(:has(.thumb)){{display:block}}
.grid .item h3{{font-size:19px}}
.grid .item .note{{font-size:14px;margin-top:6px}}
.section{{animation:rise .6s cubic-bezier(.2,.7,.2,1) both}}
.section:nth-of-type(2){{animation-delay:.06s}}
.section:nth-of-type(3){{animation-delay:.12s}}
@keyframes rise{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:none}}}}
@media (prefers-reduced-motion:reduce){{.section{{animation:none}}}}
a:focus-visible{{outline:2px solid var(--petrol);outline-offset:3px;border-radius:2px}}
@media (max-width:720px){{
  .wrap{{padding:28px 20px 72px}}
  .item.lead{{grid-template-columns:1fr;gap:18px}}
  .item.lead .thumb{{aspect-ratio:16/9}}
  .grid{{grid-template-columns:1fr;gap:24px}}
  .grid .item{{grid-template-columns:76px 1fr;gap:14px}}
  .grid .item .thumb{{width:76px}}
}}
</style></head><body><div class="wrap">
<header class="masthead">
  <p class="kicker">Private edition \u00b7 curated for Daniel</p>
  <h1 class="nameplate">Daily <em>Brief</em></h1>
  <div class="dateline"><b>{now:%A, %d %B %Y}</b><span class="dot">\u25c6</span>
    <span>No. {issue}</span><span class="dot">\u25c6</span><span>Bucharest</span>
    <span class="dot">\u25c6</span><span>{'AI curated' if ai_on else 'raw feed'}</span></div>
</header>{banner}
{body}
</div></body></html>"""

if __name__ == "__main__":
    feeds = yaml.safe_load(open("feeds.yaml"))
    picked, summaries = triage(fetch(feeds))
    ai_on = bool(summaries)
    out = pathlib.Path("_site"); out.mkdir(exist_ok=True)
    (out / "index.html").write_text(render(picked, summaries, ai_on), encoding="utf-8")
    print(f"Wrote _site/index.html ({len(picked)} items, AI={'on' if ai_on else 'OFF'})")
