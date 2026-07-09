import os, json, smtplib, datetime as dt
from email.mime.text import MIMEText
import yaml, feedparser
from anthropic import Anthropic

INTERESTS = "Romania news, healthcare innovation, Apple ecosystem and general technology"
LOOKBACK_H = 26

def fetch(feeds):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=LOOKBACK_H)
    items = []
    for topic, srcs in feeds.items():
        for s in srcs:
            for e in feedparser.parse(s["url"]).entries:
                t = e.get("published_parsed") or e.get("updated_parsed")
                when = dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else None
                if when and when < cutoff:
                    continue
                items.append({"topic": topic, "source": s["name"],
                              "title": e.get("title",""), "link": e.get("link",""),
                              "snippet": e.get("summary","")[:300]})
    return items

def triage(items):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not items:
        return items
    client = Anthropic(api_key=key)
    listing = "\n".join(f'{i}. [{x["source"]}] {x["title"]} — {x["snippet"]}'
                        for i, x in enumerate(items))
    prompt = (f"My interests: {INTERESTS}.\n\nHere are today's headlines:\n{listing}\n\n"
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

def render(items):
    by = {}
    for it in items:
        by.setdefault(it["topic"], []).append(it)
    parts = [f"<h2>Your brief — {dt.date.today():%A, %d %b}</h2>"]
    for topic, xs in by.items():
        parts.append(f"<h3>{topic}</h3><ul>")
        for x in xs:
            note = f' — {x["line"]}' if x.get("line") else ""
            parts.append(f'<li><a href="{x["link"]}">{x["title"]}</a> '
                         f'<small>({x["source"]})</small>{note}</li>')
        parts.append("</ul>")
    return "".join(parts)

def send(html):
    msg = MIMEText(html, "html")
    msg["Subject"] = f"News brief — {dt.date.today():%d %b}"
    msg["From"] = os.environ["MAIL_FROM"]; msg["To"] = os.environ["MAIL_TO"]
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], 465) as srv:
        srv.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        srv.send_message(msg)

if __name__ == "__main__":
    feeds = yaml.safe_load(open("feeds.yaml"))
    items = triage(fetch(feeds))
    if items:
        send(render(items))
        print(f"Sent {len(items)} items.")
    else:
        print("Nothing new.")
