"""
test_sources.py
===============
Lightweight test of YC Algolia and RSS feeds.
Runs independently — no Google Sheets, no Anthropic API needed.
Use via the "Test Sources" GitHub Actions workflow.
"""

import requests
import feedparser
import urllib.parse
import re

PASS = "✅"
FAIL = "❌"

# ─── YC Algolia ───────────────────────────────────────────────────────────────
def test_yc_algolia():
    print("\n=== YC (via yc-oss public API) ===")
    # yc-oss/api serves batch JSON files publicly — no API key needed
    base = "https://yc-oss.github.io/api/batches"
    batches = ["w25", "s25", "w26", "f25"]
    total = 0
    for batch in batches:
        try:
            url = f"{base}/{batch}.json"
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                companies = r.json()
                total += len(companies)
                print(f"{PASS} YC {batch.upper()}: {len(companies)} companies")
                for c in companies[:2]:
                    print(f"   - {c.get('name')} | {c.get('one_liner','')[:60]}")
            else:
                print(f"{FAIL} YC {batch.upper()}: Status {r.status_code}")
        except Exception as e:
            print(f"{FAIL} YC {batch.upper()}: {e}")
    print(f"YC Total: {total} candidates")


# ─── RSS Feeds ────────────────────────────────────────────────────────────────
def test_rss_feeds():
    print("\n=== RSS Feeds ===")
    feeds = [
        ("TechCrunch Seed", "https://techcrunch.com/tag/seed-funding/feed/"),
        ("TechCrunch Startups", "https://techcrunch.com/category/startups/feed/"),
        ("Crunchbase News", "https://news.crunchbase.com/feed/"),
        ("GeekWire", "https://www.geekwire.com/feed/"),
        ("MedCityNews", "https://medcitynews.com/feed/"),
        ("Fierce Healthcare", "https://www.fiercehealthcare.com/rss/xml"),
    ]

    funding_pattern = re.compile(
        r"([A-Z][A-Za-z0-9.\- ]{2,40})\s+(?:raises?|secures?|closes?|lands?|bags?|announces?)\s+\$(\d+(?:\.\d+)?)\s*([MK])",
        re.IGNORECASE,
    )
    seed_keywords = ["seed", "pre-seed", "preseed", "series a", "early-stage"]

    total_matches = 0
    for name, url in feeds:
        try:
            feed = feedparser.parse(url)
            status = feed.get("status", "?")
            entries = len(feed.entries)
            matches = 0
            if entries > 0:
                for entry in feed.entries[:40]:
                    title = entry.get("title", "") or ""
                    summary = entry.get("summary", "") or ""
                    combined = f"{title} {summary}".lower()
                    if any(k in combined for k in seed_keywords):
                        if funding_pattern.search(title) or funding_pattern.search(summary):
                            matches += 1
                total_matches += matches
                icon = PASS if entries > 0 else FAIL
                print(f"{icon} {name}: {entries} entries, {matches} seed funding matches (HTTP {status})")
                if feed.entries:
                    print(f"   Sample: {feed.entries[0].get('title','')[:70]}")
            else:
                print(f"{FAIL} {name}: 0 entries (HTTP {status})")
        except Exception as e:
            print(f"{FAIL} {name}: {e}")

    print(f"\nRSS Total seed funding matches: {total_matches}")


if __name__ == "__main__":
    test_yc_algolia()
    test_rss_feeds()
    print("\n=== Done ===")
