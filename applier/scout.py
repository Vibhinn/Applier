
import csv
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from . import claude, config, db

YC_ALL = "https://yc-oss.github.io/api/companies/all.json"
SEASONS = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}


def _batch_order(batch: str):
    m = re.match(r"(\w+) (\d{4})", batch or "")
    return (int(m[2]), SEASONS.get(m[1], -1)) if m else (0, -1)


def scout_yc(conn) -> int:
    companies = requests.get(YC_ALL, timeout=60).json()
    # Future batches show up early with a handful of companies; only count real ones.
    sizes = Counter(c.get("batch") for c in companies if c.get("batch"))
    batches = sorted((b for b, n in sizes.items() if n >= 30), key=_batch_order)
    recent = set(batches[-config.YC_RECENT_BATCHES:])
    added = 0
    for c in companies:
        # isHiring only means "has a YC job-board post", so it's passed to triage, not filtered on.
        if c.get("batch") not in recent or c.get("status") != "Active":
            continue
        where = " ".join(c.get("regions") or []) + " " + (c.get("all_locations") or "")
        if config.YC_REGIONS and not any(r.lower() in where.lower() for r in config.YC_REGIONS):
            continue
        added += db.upsert_company(
            conn,
            name=c["name"],
            website=c.get("website"),
            category="yc",
            one_liner=c.get("one_liner"),
            source=f"YC {c['batch']}",
            source_url=c.get("url"),
            meta={
                "description": c.get("long_description"),
                "team_size": c.get("team_size"),
                "location": c.get("all_locations"),
                "tags": c.get("tags"),
                "industry": c.get("subindustry") or c.get("industry"),
                "yc_job_posts": c.get("isHiring"),
            },
        )
    print(f"  YC ({', '.join(sorted(recent, key=_batch_order))}): {added} new")
    return added


# RSS feeds checked Oct 2026. Tracxn, VCCircle and NASSCOM have no usable feed.
FEEDS = {
    "funded": [
        ("Entrackr", "https://entrackr.com/rss"),
        ("Inc42", "https://inc42.com/buzz/feed/"),
        ("YourStory", "https://yourstory.com/category/funding/feed"),
        ("TechCrunch", "https://techcrunch.com/tag/india/feed/"),
    ],
    "gcc": [
        ("ET GCC", "https://gcc.economictimes.indiatimes.com/rss/topstories"),
        ("PuneGCC", "https://punegcc.com/feed"),
        ("GCCLens", "https://gcclens.com/feed"),
    ],
}
# Cheap pre-filter so only plausible announcements reach the LLM.
KEYWORDS = {
    "funded": re.compile(r"rais|fund|seed|series [a-d]|pre-series|round|crore|\bcr\b|\$\d|mn\b|million|backed|led by", re.I),
    "gcc": re.compile(r"\bgcc|capabilit|tech(nology)? cent|innovation cent|engineering cent|"
                      r"development cent|\bhub\b|opens|inaugurat|expand|sets up|launch", re.I),
}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
MANUAL_GCC = config.ROOT / "data" / "gcc_manual.csv"


def _feed_items(category: str) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=config.NEWS_LOOKBACK_DAYS)
    items, seen = [], set()
    for name, url in FEEDS[category]:
        try:
            feed = feedparser.parse(requests.get(url, headers=UA, timeout=30).content)
        except requests.RequestException as e:
            print(f"    {name}: feed failed ({e.__class__.__name__})")
            continue
        for e in feed.entries:
            when = e.get("published_parsed") or e.get("updated_parsed")
            if when and datetime(*when[:6], tzinfo=timezone.utc) < since:
                continue
            summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:400]
            if e.link in seen or not KEYWORDS[category].search(e.title + " " + summary):
                continue
            seen.add(e.link)
            items.append({"i": len(items), "source": name, "title": e.title, "summary": summary,
                          "url": e.link, "date": time.strftime("%Y-%m-%d", when) if when else None})
    return items


def scout_feeds(conn, category: str) -> int:
    items = _feed_items(category)
    found = claude.extract_announcements(items, category) if items else []
    added = 0
    for a in found:
        if not (0 <= a.index < len(items)):
            continue
        item = items[a.index]
        added += db.upsert_company(
            conn,
            name=a.company.strip(),
            website=a.website,
            category=category,
            one_liner=a.one_liner,
            source=item["source"],
            source_url=item["url"],
            funding=a.details if category == "funded" else None,
            meta={"location": a.location, "announced": item["date"], "headline": item["title"]},
        )
    print(f"  {category}: {added} new ({len(found)} announcements in {len(items)} matching headlines)")
    return added


def scout_manual(conn) -> int:
    """GCC news is messy, so you can curate: one company per row in data/gcc_manual.csv."""
    if not MANUAL_GCC.exists():
        MANUAL_GCC.parent.mkdir(parents=True, exist_ok=True)
        MANUAL_GCC.write_text("name,website,note\n")
        return 0
    added = 0
    with MANUAL_GCC.open() as f:
        for row in csv.DictReader(f):
            if (row.get("name") or "").strip():
                added += db.upsert_company(conn, name=row["name"].strip(), website=row.get("website") or None,
                                           category="gcc", one_liner=row.get("note") or None,
                                           source="Manual list")
    print(f"  manual GCC list: {added} new")
    return added


def run(sources=("yc", "funded", "gcc")):
    print("Scouting…")
    with db.connect() as conn:
        if "yc" in sources:
            scout_yc(conn)
        for cat in ("funded", "gcc"):
            if cat in sources:
                try:
                    scout_feeds(conn, cat)
                except Exception as e:  # one broken source shouldn't kill the run
                    print(f"  {cat}: failed: {e}")
        if "gcc" in sources:
            scout_manual(conn)
