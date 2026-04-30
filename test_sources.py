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
    print("\n=== YC Algolia ===")
    url = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"
    headers = {
        "x-algolia-agent": "Algolia for JavaScript (4.14.3); Browser (lite)",
        "x-algolia-api-key": "9f3b9a7fd6e66c93f2bec4e42e3eb94d",
        "x-algolia-application-id": "45BWZJ1SGC",
        "Content-Type": "application/json",
    }
    total = 0
    for batch in ["W25", "S25", "W26", "F25"]:
        try:
            params = urllib.parse.urlencode({
                "query": "",
                "facetFilters": f'[["batch:{batch}"]]',
                "hitsPerPage": 10,
                "attributesToRetrieve": "name,one_liner,website,batch",
            })
            payload = {"requests": [{"indexName": "YCCompany_production", "params": params}]}
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if r.status_code == 200:
                hits = r.json().get("results", [{}])[0].get("hits", [])
                total += len(hits)
                print(f"{PASS} YC {batch}: {len(hits)} hits")
                for h in hits[:2]:
                    print(f"   - {h.get('name')} | {h.get('one_liner','')[:60]}")
            else:
                print(f"{FAIL} YC {batch}: Status {r.status_code} — {r.text[:150]}")
        except Exception as e:
            print(f"{FAIL} YC {batch}: {e}")
    print(f"YC Total: {total} candidates")


# ─── RSS Feeds ────────────────────────────────────────────────────────────────
def test_rss_feeds():
    print("\n=== RSS Feeds ===")
    feeds = [
        ("TechCrunch Seed", "https://techcrunch.com/tag/seed-funding/feed/"),
        ("TechCrunch Startups", "https://techcrunch.com/category/startups/feed/"),
        ("Crunchbase News", "https://news.crunchbase.com/feed/"),
        ("Fortune Term Sheet", "https://fortune.com/feed/fortune-termsheet/"),
        ("VentureBeat", "https://venturebeat.com/category/venture/feed/"),
        ("GeekWire", "https://www.geekwire.com/feed/"),
        ("MedCityNews", "https://medcitynews.com/feed/"),
        ("Fierce Healthcare", "https://www.fiercehealthcare.com/rss/xml"),
        ("BusinessWire", "https://www.businesswire.com/rss/home/?rss=G7"),
        ("vcnewsdaily", "https://vcnewsdaily.com/feed/"),
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
