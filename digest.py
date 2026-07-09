import os, json, re, datetime as dt, pathlib, html, traceback, glob
import yaml, feedparser
from anthropic import Anthropic

INTERESTS = "Romania news, healthcare innovation, Apple ecosystem and general technology"
LOOKBACK_H = 26
TOPIC_ORDER = ["Romania", "Healthcare", "Tech"]
TOP_N = 5
ISSUES_DIR = pathlib.Path("issues")   # committed to the repo (persists)
SITE_DIR = pathlib.Path("_site")      # rebuilt every run, deployed to Pages
NAMEPLATE = "Daniel\u2019s Daily <em>Briefing</em>"
feedparser.USER_AGENT = "news-digest/1.0 (+github actions)"

# ---------- feed parsing ----------
def clean_text(t):
    t = re.sub(r"<[^>]+>", "", t or "")
    return html.unescape(t).strip()

def extract_image(e):
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            url = m.get("url")
            if url and (m.get("medium") in (None, "image") or re.search(r"\.(jpg|jpeg|png|webp|gif)", url, re.I)):
                return url
    for l in e.get("links", []) or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            return l.get("href")
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

# ---------- Claude triage ----------
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
        return items, {}, []
    if not items:
        print("!!! fetch() returned 0 items.")
        return items, {}, []
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
        print(raw[:4000]); print("=== END ===")
        data = extract_json(raw)
        out, picked_idx = [], set()
        for p in data.get("items", []):
            i = p.get("i")
            if not isinstance(i, int) or i < 0 or i >= len(items):
                continue
            it = items[i]
            it["score"] = p.get("score", 0); it["line"] = p.get("line", "")
            it["pill"] = p.get("pill", "")
            out.append(it); picked_idx.add(i)
        out.sort(key=lambda x: -x.get("score", 0))
        seen = {x["link"] for x in out}
        others = []
        for i, it in enumerate(items):
            if i in picked_idx or not it.get("title") or it["link"] in seen:
                continue
            seen.add(it["link"]); others.append(it)
            if len(others) >= 20:
                break
        print(f"Triage OK: {len(out)} picked, {len(others)} other, "
              f"summaries={list(data.get('summaries', {}).keys())}")
        return out, data.get("summaries", {}), others
    except Exception as ex:
        print(f"!!! Triage FAILED: {type(ex).__name__}: {ex}")
        traceback.print_exc()
        return items, {}, []

# ---------- rendering ----------
HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400;1,6..72,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{--ink:#1b1a17;--paper:#f0eee7;--petrol:#0e4d52;--brass:#9c7b34;--muted:#6c685e;--line:#dcd8ce}}
*{{box-sizing:border-box}}html{{-webkit-text-size-adjust:100%}}
body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Newsreader",Georgia,serif;
 font-optical-sizing:auto;line-height:1.5;
 background-image:radial-gradient(circle at 1px 1px,rgba(27,26,23,.035) 1px,transparent 0);background-size:22px 22px}}
.wrap{{max-width:1120px;margin:0 auto;padding:40px 28px 96px}}
.masthead{{border-bottom:1px solid var(--ink);padding-bottom:20px;margin-bottom:8px}}
.kicker{{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.28em;text-transform:uppercase;color:var(--brass);margin:0 0 10px}}
.nameplate{{font-weight:500;font-size:clamp(42px,8.5vw,74px);line-height:.95;letter-spacing:-.02em;margin:0}}
.nameplate a{{color:inherit;text-decoration:none}}
.nameplate em{{font-style:italic;color:var(--petrol)}}
.dateline{{font-family:"IBM Plex Mono",monospace;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-top:16px;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}}
.dateline .dot{{color:var(--brass)}}.dateline b{{color:var(--ink);font-weight:600}}
.issue-nav{{display:flex;gap:20px;align-items:center;margin-top:16px;font-family:"IBM Plex Mono",monospace;font-size:12px;letter-spacing:.06em;text-transform:uppercase}}
.issue-nav a{{color:var(--petrol);text-decoration:none;border-bottom:1px solid transparent;padding-bottom:1px}}
.issue-nav a:hover{{border-color:var(--petrol)}}
.issue-nav .spacer{{flex:1}}
.archived{{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--brass);background:rgba(156,123,52,.10);border-left:2px solid var(--brass);padding:10px 14px;margin:20px 0 0;border-radius:2px}}
.archived a{{color:var(--petrol)}}
.banner{{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--petrol);background:rgba(14,77,82,.08);border-left:2px solid var(--petrol);padding:10px 14px;margin:20px 0 0;border-radius:2px}}
.section{{margin-top:56px}}
.eyebrow{{display:flex;align-items:center;gap:16px;margin-bottom:18px}}
.eyebrow .topic{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;font-weight:600;letter-spacing:.22em;text-transform:uppercase;color:var(--petrol)}}
.eyebrow .rule{{flex:1;height:1px;background:var(--line)}}
.eyebrow .count{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--brass);letter-spacing:.1em}}
.standfirst{{font-style:italic;font-size:clamp(17px,2.1vw,21px);line-height:1.5;color:#42403a;margin:0 0 26px;max-width:100%}}
.item{{margin:0}}.item .body{{min-width:0}}
.meta{{display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}}
.pill{{font-family:"IBM Plex Mono",monospace;font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--petrol);background:rgba(14,77,82,.10);padding:3px 8px;border-radius:3px}}
.src{{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}
h3{{margin:0;font-weight:500;line-height:1.18;letter-spacing:-.01em}}
h3 a{{color:var(--ink);text-decoration:none}}h3 a:hover{{color:var(--petrol)}}
.note{{color:var(--muted);font-size:15px;line-height:1.5;margin:8px 0 0;max-width:60ch}}
.thumb{{display:block;overflow:hidden;border-radius:4px;background:var(--line)}}
.thumb img{{display:block;width:100%;height:100%;object-fit:cover}}
.thumb.ph{{background:var(--petrol);position:relative;display:flex;align-items:center;justify-content:center}}
.thumb.ph::before{{content:"";position:absolute;inset:-25%;
 background:repeating-radial-gradient(circle at 50% 42%,rgba(240,238,231,.10) 0 1px,transparent 1px 10px)}}
.thumb.ph span{{position:relative;font-family:"Newsreader",serif;font-style:italic;color:var(--paper);
 line-height:1.05;text-align:center;padding:0 16px;letter-spacing:-.01em;
 font-size:clamp(22px,3.4vw,36px)}}
.item.lead{{display:grid;grid-template-columns:1.05fr 1fr;gap:28px;align-items:center;padding-bottom:34px;margin-bottom:34px;border-bottom:1px solid var(--line)}}
.item.lead .thumb{{aspect-ratio:16/10}}.item.lead h3{{font-size:clamp(23px,3vw,32px)}}.item.lead .note{{font-size:16px}}
.item.lead:not(:has(.thumb)){{display:block}}
.item.lead:not(:has(.thumb)) h3{{font-size:clamp(26px,4vw,38px);max-width:20ch}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:30px 34px}}
.grid .item{{display:block}}
.grid .item::after{{content:"";display:block;clear:both}}
.grid .item .thumb{{float:left;width:104px;aspect-ratio:1/1;margin:3px 16px 6px 0}}
.grid .item .thumb.ph span{{font-size:15px}}
.grid .item .meta{{display:block;margin-bottom:6px}}
.grid .item .pill,.grid .item .src{{display:inline-block;vertical-align:middle}}
.grid .item .src{{margin-left:8px}}
.grid .item h3{{font-size:19px}}.grid .item .note{{font-size:14px;margin-top:6px;max-width:none}}
.section{{animation:rise .6s cubic-bezier(.2,.7,.2,1) both}}
.section:nth-of-type(2){{animation-delay:.06s}}.section:nth-of-type(3){{animation-delay:.12s}}
@keyframes rise{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:none}}}}
@media (prefers-reduced-motion:reduce){{.section{{animation:none}}}}
a:focus-visible{{outline:2px solid var(--petrol);outline-offset:3px;border-radius:2px}}
/* archive page */
.arc-month{{margin-top:48px}}
.arc-month h2{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;font-weight:600;letter-spacing:.22em;text-transform:uppercase;color:var(--petrol);margin:0 0 4px;padding-bottom:10px;border-bottom:1px solid var(--line)}}
.arc-row{{display:grid;grid-template-columns:150px 1fr auto;gap:18px;align-items:baseline;padding:16px 0;border-bottom:1px solid var(--line);text-decoration:none;color:var(--ink)}}
.arc-row:hover{{color:var(--petrol)}}
.arc-date{{font-family:"IBM Plex Mono",monospace;font-size:13px;letter-spacing:.05em;color:var(--muted)}}
.arc-date b{{display:block;color:var(--ink);font-size:15px;letter-spacing:0}}
.arc-teaser{{font-size:17px;font-weight:500;line-height:1.25}}
.arc-count{{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--brass);white-space:nowrap}}
.empty{{color:var(--muted)}}
/* other news */
.other .eyebrow .topic{{color:var(--brass)}}
.otherlist{{columns:2;column-gap:44px;margin-top:2px}}
.orow{{display:block;break-inside:avoid;padding:11px 0;border-top:1px solid var(--line);text-decoration:none;color:var(--ink)}}
.orow:hover .otitle{{color:var(--petrol)}}
.ometa{{display:block;margin-bottom:3px}}
.otag{{font-family:"IBM Plex Mono",monospace;font-size:9.5px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--brass);background:rgba(156,123,52,.12);padding:2px 6px;border-radius:3px}}
.osrc{{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin-left:8px}}
.otitle{{display:block;font-size:15px;font-weight:500;line-height:1.3}}
@media (max-width:720px){{
 .wrap{{padding:28px 20px 72px}}
 .item.lead{{grid-template-columns:1fr;gap:18px}}.item.lead .thumb{{aspect-ratio:16/9}}
 .grid{{grid-template-columns:1fr;gap:24px}}
 .grid .item .thumb{{width:88px;margin-right:14px}}
 .arc-row{{grid-template-columns:1fr;gap:4px}}.arc-count{{display:none}}
 .otherlist{{columns:1}}
}}
</style></head><body><div class="wrap">"""

def _card(x, lead=False):
    pill = f'<span class="pill">{html.escape(x["pill"])}</span>' if x.get("pill") else ""
    line = f'<p class="note">{html.escape(x["line"])}</p>' if x.get("line") else ""
    img = ""
    if x.get("image"):
        onerr = ("this.closest('.thumb').classList.add('ph');"
                 "this.replaceWith(Object.assign(document.createElement('span'),"
                 "{textContent:this.getAttribute('data-src')}))") if lead else "this.parentNode.remove()"
        img = (f'<a class="thumb" href="{html.escape(x["link"])}" tabindex="-1">'
               f'<img loading="lazy" src="{html.escape(x["image"])}" alt="" '
               f'data-src="{html.escape(x["source"])}" onerror="{onerr}"></a>')
    elif lead:
        img = (f'<a class="thumb ph" href="{html.escape(x["link"])}" tabindex="-1">'
               f'<span>{html.escape(x["source"])}</span></a>')
    cls = "item lead" if lead else "item"
    return (f'<article class="{cls}">{img}<div class="body">'
            f'<div class="meta">{pill}<span class="src">{html.escape(x["source"])}</span></div>'
            f'<h3><a href="{html.escape(x["link"])}">{html.escape(x["title"])}</a></h3>'
            f'{line}</div></article>')

def render_issue(data, all_dates, idx):
    """data = {date, ai_on, summaries, items}. all_dates sorted newest-first. idx = position."""
    date = data["date"]
    d = dt.date.fromisoformat(date)
    issue_no = d.timetuple().tm_yday
    ai_on = data.get("ai_on", False)
    summaries = data.get("summaries", {})
    items = data.get("items", [])
    is_latest = (idx == 0)

    by = {}
    for it in items:
        by.setdefault(it["topic"], []).append(it)
    sections = []
    for topic in TOPIC_ORDER + [t for t in by if t not in TOPIC_ORDER]:
        xs = by.get(topic)
        if not xs:
            continue
        xs = xs[:TOP_N]
        summ = summaries.get(topic, "")
        summ_html = f'<p class="standfirst">{html.escape(summ)}</p>' if summ else ""
        lead = _card(xs[0], lead=True)
        rest = "".join(_card(x) for x in xs[1:])
        rest_html = f'<div class="grid">{rest}</div>' if rest else ""
        sections.append(
            f'<section class="section"><div class="eyebrow">'
            f'<span class="topic">{html.escape(topic)}</span>'
            f'<span class="rule"></span><span class="count">{len(xs):02d}</span></div>'
            f'{summ_html}{lead}{rest_html}</section>')
    body = "".join(sections) or '<p class="empty">Nothing new this day.</p>'

    others = data.get("others", [])
    if others:
        rows = "".join(
            f'<a class="orow" href="{html.escape(o["link"])}"><span class="ometa">'
            f'<span class="otag">{html.escape(o["topic"])}</span>'
            f'<span class="osrc">{html.escape(o["source"])}</span></span>'
            f'<span class="otitle">{html.escape(o["title"])}</span></a>'
            for o in others)
        body += (f'<section class="section other"><div class="eyebrow">'
                 f'<span class="topic">Other news</span><span class="rule"></span>'
                 f'<span class="count">{len(others):02d}</span></div>'
                 f'<div class="otherlist">{rows}</div></section>')

    newer = all_dates[idx-1] if idx > 0 else None
    older = all_dates[idx+1] if idx < len(all_dates)-1 else None
    nav = ['<nav class="issue-nav">']
    if newer:
        nav.append(f'<a href="{newer}.html">\u2190 Newer</a>')
    nav.append('<a href="archive.html">All issues</a>')
    nav.append('<span class="spacer"></span>')
    if older:
        nav.append(f'<a href="{older}.html">Older \u2192</a>')
    nav.append('</nav>')
    nav_html = "".join(nav)

    archived = "" if is_latest else ('<div class="archived">Archived issue \u00b7 '
                                     '<a href="index.html">Back to today\u2019s brief</a></div>')
    banner = "" if ai_on else ('<div class="banner">AI curation off \u2014 raw feeds for this day.</div>')

    home = "index.html" if is_latest else f"{date}.html"
    return (HEAD.format(title=f"Daniel\u2019s Daily Briefing \u2014 {d:%d %b %Y}") +
            f'<header class="masthead">'
            f'<p class="kicker">Private edition \u00b7 curated for Daniel</p>'
            f'<h1 class="nameplate"><a href="{home}">{NAMEPLATE}</a></h1>'
            f'<div class="dateline"><b>{d:%A, %d %B %Y}</b><span class="dot">\u25c6</span>'
            f'<span>No. {issue_no}</span><span class="dot">\u25c6</span><span>Bucharest</span>'
            f'<span class="dot">\u25c6</span><span>{"AI curated" if ai_on else "raw feed"}</span></div>'
            f'{nav_html}</header>{archived}{banner}{body}</div></body></html>')

def render_archive(index):
    """index = list of issue-data dicts, newest first."""
    by_month = {}
    for data in index:
        d = dt.date.fromisoformat(data["date"])
        by_month.setdefault(f"{d:%B %Y}", []).append((d, data))
    blocks = []
    for month, rows in by_month.items():
        out = [f'<div class="arc-month"><h2>{month}</h2>']
        for d, data in rows:
            items = data.get("items", [])
            teaser = html.escape(items[0]["title"]) if items else "\u2014"
            n = len(items)
            out.append(
                f'<a class="arc-row" href="{data["date"]}.html">'
                f'<span class="arc-date">{d:%a}<b>{d:%d %b}</b>No. {d.timetuple().tm_yday}</span>'
                f'<span class="arc-teaser">{teaser}</span>'
                f'<span class="arc-count">{n} stories</span></a>')
        out.append('</div>')
        blocks.append("".join(out))
    body = "".join(blocks) or '<p class="empty">No issues yet.</p>'
    return (HEAD.format(title="Daniel\u2019s Daily Briefing \u2014 Archive") +
            f'<header class="masthead">'
            f'<p class="kicker">Private edition \u00b7 curated for Daniel</p>'
            f'<h1 class="nameplate"><a href="index.html">{NAMEPLATE}</a></h1>'
            f'<div class="dateline"><b>Archive</b><span class="dot">\u25c6</span>'
            f'<span>{len(index)} issues</span></div>'
            f'<nav class="issue-nav"><a href="index.html">\u2190 Back to today</a></nav>'
            f'</header>{body}</div></body></html>')

# ---------- build the whole site from stored issues ----------
def build_site():
    SITE_DIR.mkdir(exist_ok=True)
    files = sorted(glob.glob(str(ISSUES_DIR / "*.json")), reverse=True)  # newest first
    index = []
    for f in files:
        try:
            index.append(json.load(open(f, encoding="utf-8")))
        except Exception as ex:
            print(f"skip {f}: {ex}")
    all_dates = [d["date"] for d in index]
    for idx, data in enumerate(index):
        page = render_issue(data, all_dates, idx)
        (SITE_DIR / f'{data["date"]}.html').write_text(page, encoding="utf-8")
    if index:
        (SITE_DIR / "index.html").write_text(render_issue(index[0], all_dates, 0), encoding="utf-8")
    else:
        (SITE_DIR / "index.html").write_text(
            HEAD.format(title="Daniel\u2019s Daily Briefing") + '<p class="empty">No issues yet.</p></div></body></html>',
            encoding="utf-8")
    (SITE_DIR / "archive.html").write_text(render_archive(index), encoding="utf-8")
    print(f"Built site: {len(index)} issues.")

if __name__ == "__main__":
    feeds = yaml.safe_load(open("feeds.yaml"))
    picked, summaries, others = triage(fetch(feeds))
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).date().isoformat()
    ISSUES_DIR.mkdir(exist_ok=True)
    json.dump({"date": today, "ai_on": bool(summaries),
               "summaries": summaries, "items": picked, "others": others},
              open(ISSUES_DIR / f"{today}.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Saved issue {today} ({len(picked)} items, AI={'on' if summaries else 'OFF'})")
    build_site()
