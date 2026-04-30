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
    target_batches = {"W25", "S25", "W26", "F25", "X25"}
    try:
        url = "https://yc-oss.github.io/api/companies/all.json"
        r = requests.get(url, timeout=30)
        print(f"Status: {r.status_code}, Size: {len(r.content)} bytes")
        if r.status_code == 200:
            all_companies = r.json()
            print(f"Total companies in all.json: {len(all_companies)}")
            matches = [c for c in all_companies if c.get("batch") in target_batches]
            print(f"{PASS} Matching recent batches {target_batches}: {len(matches)} companies")
            # Show batch breakdown
            from collections import Counter
            counts = Counter(c.get("batch") for c in matches)
            for batch, count in sorted(counts.items()):
                print(f"   {batch}: {count} companies")
            # Sample
            for c in matches[:3]:
                print(f"   - {c.get('name')} | {c.get('batch')} | {c.get('one_liner','')[:60]}")
        else:
            print(f"{FAIL} Status {r.status_code}")
    except Exception as e:
        print(f"{FAIL} Error: {e}")


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
