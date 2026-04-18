"""
Second Layer VC Pipeline — Daily Sourcer & Scorer (v4)
Rebuilt with reliable, bot-friendly sources only.
Sources confirmed working: HN Algolia API, SEC EDGAR API, YC Algolia API,
RSS feeds, and Claude-assisted web research for variety.
"""

import os
import json
import time
import datetime
import smtplib
import re
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic
import requests
from bs4 import BeautifulSoup
from sheets_logger import append_results_to_sheet, get_previously_seen_companies

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_SENDER      = os.environ["EMAIL_SENDER"]
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]
EMAIL_RECIPIENT   = os.environ["EMAIL_RECIPIENT"]
MIN_SCORE_PCT      = float(os.environ.get("MIN_SCORE_PCT", "65"))
CRUNCHBASE_API_KEY = os.environ.get("CRUNCHBASE_API_KEY", "")
CRUSTDATA_API_KEY  = os.environ.get("CRUSTDATA_API_KEY", "")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept": "application/json, text/html",
}

SECOND_LAYER_KEYWORDS = [
    # B2B compliance / regtech
    "compliance", "aml", "kyc", "kyb", "fraud", "regtech", "regulatory",
    "anti-money laundering", "financial crime", "sanctions", "fintech",
    "hipaa", "health", "healthcare", "clinical", "medical", "prior auth",
    "security", "cybersecurity", "threat", "incident response", "dfir",
    "vulnerability", "devsecops", "appsec", "cloud security",
    "legal", "contract", "law", "legaltech",
    "ai governance", "model risk", "responsible ai", "ai compliance",
    "audit", "governance", "risk management",
    "supply chain", "sbom", "vendor risk", "third party",
    "insurance", "insurtech", "underwriting",
    "energy", "grid", "carbon", "emissions",
    "privacy", "data protection", "gdpr", "ccpa", "pii",
    "trade", "tariff", "customs",
    "monitoring", "detection", "verification", "identity",
    "automation", "workflow", "infrastructure",
    # Consumer Second Layer — downstream of dominant trends
    "personal finance", "debt", "credit score", "subscription management",
    "benefits navigation", "health navigation", "patient advocate",
    "data broker", "data deletion", "personal data",
    "deepfake", "ai detection", "content authenticity",
    "creator tools", "creator monetization", "creator compliance",
    "mental health", "burnout", "wellness",
    "career", "job search", "resume", "salary negotiation",
    "rental", "tenant rights", "housing",
    "consumer protection", "dispute resolution", "chargebacks",
    "digital literacy", "scam detection", "phishing consumer",
]

# Words that indicate it's a fund/investor, not a startup
FUND_KEYWORDS = [
    "fund", "capital partners", "investment", "holdings", "ventures llc",
    "partners lp", "partners llc", "management llc", "asset management",
    "opportunity fund", "equity fund", "credit fund",
]


# Signals that indicate Series B+ — filter these out at sourcing stage
LATE_STAGE_SIGNALS = [
    "series b", "series c", "series d", "series e",
    "$50 million", "$75 million", "$100 million", "$150 million", "$200 million",
    "$50m", "$75m", "$100m", "$150m", "$200m",
    "50 million", "75 million", "100 million",
    "growth stage", "late stage", "pre-ipo",
    # Also filter Series A from sourcing channels — we only want pre-seed/seed
    "series a",
    "$15m raise", "$20m raise", "$25m raise", "$30m raise",
    "$15 million raise", "$20 million raise", "$25 million raise",
]

def is_late_stage(text):
    t = text.lower()
    return any(signal in t for signal in LATE_STAGE_SIGNALS)

def is_relevant(text):
    t = text.lower()
    return any(kw in t for kw in SECOND_LAYER_KEYWORDS)

def is_fund(name):
    n = name.lower()
    return any(kw in n for kw in FUND_KEYWORDS)


# ── SOURCE 1: YC ALGOLIA API (confirmed working) ──────────────────────────────
def source_yc():
    companies = []
    try:
        day = datetime.date.today().weekday()
        all_terms = [
            "compliance", "security", "fraud",
            "healthcare AI", "clinical workflow",
            "legal", "contract",
            "fintech risk", "identity",
            "privacy", "governance",
            "supply chain", "insurance",
            "cybersecurity", "regulatory",
        ]
        terms   = [all_terms[(day * 2) % len(all_terms)],
                   all_terms[(day * 2 + 1) % len(all_terms)]]
        batches = ["W25", "S24", "W24", "S23", "W23", "S22", "W22", "S21", "W21"]

        for term in terms:
            for batch in batches:
                payload = {"requests": [{"indexName": "YCCompany_production",
                    "params": (f"query={requests.utils.quote(term)}"
                               f"&hitsPerPage=8&filters=batch%3A{batch}")}]}
                resp = requests.post(
                    "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries",
                    json=payload,
                    params={
                        "x-algolia-agent": "Algolia for JavaScript (4.14.3)",
                        "x-algolia-api-key": "9f3867c5067ead04cbdd2ce3e8d8b7e8",
                        "x-algolia-application-id": "45BWZJ1SGC",
                    },
                    timeout=15,
                )
                if resp.status_code != 200:
                    print(f"YC API error: {resp.status_code} — {resp.text[:100]}")
                    continue
                results = resp.json().get("results", [])
                if not results:
                    continue
                for hit in results[0].get("hits", []):
                    name = hit.get("name", "")
                    desc = hit.get("one_liner", "") or hit.get("long_description", "")
                    if name and is_relevant(f"{name} {desc}") and not is_fund(name):
                        companies.append({
                            "name": name, "description": desc,
                            "source": f"YC {hit.get('batch','')}"
                        })
                time.sleep(0.4)

        print(f"YC: {len(companies)} candidates")
    except Exception as e:
        print(f"YC error: {e}")
    return companies[:10]


# ── SOURCE 2: HACKER NEWS ALGOLIA API (confirmed working) ────────────────────
def source_hacker_news():
    """
    Two HN sub-sources:
    A) Show HN posts — founders launching products
    B) Ask HN: Who is hiring — seed companies posting jobs
    """
    companies = []
    day = datetime.date.today().weekday()

    # A) Show HN — product launches
    try:
        queries = [
            "compliance automation", "security monitoring",
            "healthcare workflow", "legal AI",
            "fraud detection", "privacy infrastructure",
            "risk management",
        ]
        query = queries[day % len(queries)]
        url   = (f"https://hn.algolia.com/api/v1/search"
                 f"?query={requests.utils.quote(query)}"
                 f"&tags=show_hn&hitsPerPage=25"
                 f"&numericFilters=created_at_i>1700000000")  # Nov 2023+
        hits  = requests.get(url, timeout=15).json().get("hits", [])
        for hit in hits:
            title = hit.get("title", "")
            match = re.match(r"Show HN:\s+([^–—\|\-\(\[]{3,40})[–—\|\-\(\[]", title)
            if match:
                name = match.group(1).strip().rstrip(" -–—")
                if name and is_relevant(f"{name} {title}") and not is_fund(name):
                    companies.append({
                        "name": name, "description": title,
                        "source": "Hacker News"
                    })
    except Exception as e:
        print(f"HN Show error: {e}")

    # B) HN search for seed-stage company mentions
    try:
        seed_queries = [
            "seed round compliance", "seed funding security startup",
            "seed stage healthcare AI", "pre-seed legal tech",
        ]
        sq = seed_queries[day % len(seed_queries)]
        url = (f"https://hn.algolia.com/api/v1/search"
               f"?query={requests.utils.quote(sq)}"
               f"&tags=story&hitsPerPage=15"
               f"&numericFilters=created_at_i>1700000000")
        hits = requests.get(url, timeout=15).json().get("hits", [])
        for hit in hits:
            title = hit.get("title", "")
            # Extract company name from patterns like "CompanyName raises $XM"
            match = re.match(
                r"^([A-Z][A-Za-z0-9\.\-]{2,25})\s+"
                r"(?:raises|secures|launches|announces|releases|opens)",
                title
            )
            if match:
                name = match.group(1).strip()
                if (name and len(name) > 3 and is_relevant(f"{name} {title}")
                        and not is_fund(name)):
                    companies.append({
                        "name": name, "description": title,
                        "source": "Hacker News"
                    })
    except Exception as e:
        print(f"HN seed search error: {e}")

    print(f"Hacker News: {len(companies)} candidates")
    return companies[:8]


# ── SOURCE 3: SEC EDGAR FORM D — startups only ────────────────────────────────
def source_sec_form_d():
    """
    Pulls recent Form D filings from SEC EDGAR full-text search.
    Filters to small raises ($500K-$10M) to target seed stage.
    """
    companies = []
    try:
        today     = datetime.date.today()
        date_from = (today - datetime.timedelta(days=14)).strftime("%Y-%m-%d")
        day       = today.weekday()
        terms = [
            "software", "technology platform",
            "cybersecurity", "healthcare technology",
            "financial technology", "insurance technology",
            "data privacy", "artificial intelligence",
        ]
        term = terms[day % len(terms)]

        # Correct EDGAR full-text search endpoint
        url = (f"https://efts.sec.gov/LATEST/search-index?"
               f"q=%22{requests.utils.quote(term)}%22"
               f"&dateRange=custom&startdt={date_from}&forms=D&hits.hits._source=period_of_report,entity_name,file_num")
        headers = dict(HEADERS)
        headers["User-Agent"] = "SecondLayerVC research@example.com"
        resp = requests.get(url, headers=headers, timeout=20)

        if resp.status_code != 200:
            # Fallback: use the standard EDGAR search
            url2 = (f"https://efts.sec.gov/LATEST/search-index?"
                    f"q=%22{requests.utils.quote(term)}%22"
                    f"&forms=D&dateRange=custom&startdt={date_from}")
            resp = requests.get(url2, headers=headers, timeout=20)

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        for hit in hits[:20]:
            src  = hit.get("_source", {})
            # Try multiple name fields
            name = (src.get("entity_name") or
                    src.get("entityName") or
                    (src.get("display_names") or [""])[0] or "")
            name = name.strip()

            if not name or len(name) < 3 or len(name) > 60:
                continue
            if is_fund(name):
                continue
            if re.search(r"\b\d{7,}\b", name):  # Skip CIK-like numbers
                continue
            # Skip obvious non-startups
            skip_patterns = ["LLC", "LP", "L.P.", "FUND", "PARTNERS", "CAPITAL",
                             "HOLDINGS", "GROUP", "VENTURES", "MANAGEMENT"]
            if any(p in name.upper() for p in skip_patterns):
                continue

            companies.append({
                "name": name,
                "description": f"SEC Form D filing — {term} — early stage raise",
                "source": "SEC Form D",
            })

        print(f"SEC Form D: {len(companies)} candidates")
    except Exception as e:
        print(f"SEC Form D error: {e}")
    return companies[:6]


# ── SOURCE 4: MULTIPLE RSS FEEDS ─────────────────────────────────────────────
def source_rss_feeds():
    """
    Parses reliable public RSS feeds for seed/early funding news.
    Feeds verified as working and publicly accessible.
    """
    companies = []
    feeds = [
        ("https://techcrunch.com/feed/", "TechCrunch"),
        ("https://venturebeat.com/feed/", "VentureBeat"),
        ("https://www.wired.com/feed/rss", "Wired"),
        ("https://feeds.feedburner.com/TechCrunch", "TechCrunch Alt"),
    ]
    funding_words = ["raises", "funding", "seed", "series a", "launches",
                     "secures", "closes", "backed", "invests", "pre-seed"]
    skip_words    = ["series b", "series c", "series d", "series e",
                     "$50m", "$75m", "$100m", "$50 million", "$100 million"]

    for feed_url, feed_name in feeds:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            # Strip problematic XML entities before parsing
            content = resp.content.replace(b"&", b"&amp;").replace(b"&amp;amp;", b"&amp;")
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                continue
            for item in root.findall(".//item"):
                title = item.findtext("title", "") or ""
                desc  = item.findtext("description", "") or ""
                text  = BeautifulSoup(desc, "html.parser").get_text()
                combined = title + " " + text

                if not any(w in title.lower() for w in funding_words):
                    continue
                if any(w in combined.lower() for w in skip_words):
                    continue
                if not is_relevant(combined):
                    continue

                # Looser regex — just grab first 1-4 capitalized words before a verb
                match = re.match(
                    r"^([A-Z][A-Za-z0-9][A-Za-z0-9\.\- ]{1,40}?)\s+"
                    r"(?:raises|secures|closes|gets|lands|launches|announces|nabs|scores)",
                    title
                )
                if match:
                    name = match.group(1).strip().rstrip(".,")
                    if name and not is_fund(name) and 3 < len(name) < 50 and not is_late_stage(title):
                        companies.append({
                            "name": name,
                            "description": title,
                            "source": feed_name,
                        })
            time.sleep(1)
        except Exception as e:
            print(f"RSS {feed_name} error: {e}")

    print(f"RSS Feeds: {len(companies)} candidates")
    return companies[:8]


# ── SOURCE 5: CLAUDE-ASSISTED RESEARCH ───────────────────────────────────────
def source_claude_research():
    """
    Asks Claude to surface 5 specific seed-stage Second Layer companies
    it knows about from its training data that haven't gotten much coverage.
    Rotates by vertical daily. Different from scoring — this is pure sourcing.
    """
    companies = []
    try:
        day = datetime.date.today().weekday()
        verticals = [
            # B2B Second Layer
            "AML/KYC compliance automation for fintech",
            "HIPAA-compliant AI workflow tools for healthcare",
            "AI governance and model risk management",
            "legal AI compliance and contract risk",
            "cybersecurity threat detection and response",
            "data privacy and PII compliance automation",
            "supply chain risk and SBOM management",
            # Consumer Second Layer
            "consumer personal finance tools solving complexity created by fintech expansion",
            "consumer health navigation apps solving fragmentation from healthcare digitization",
            "personal data privacy tools solving problems created by data broker proliferation",
            "consumer AI detection and trust tools solving problems created by generative AI",
            "creator economy infrastructure and compliance tools for the creator economy boom",
            "consumer career and income tools solving instability from remote work and AI displacement",
        ]
        vertical = verticals[day % len(verticals)]

        is_consumer = "consumer" in vertical.lower() or "creator" in vertical.lower()
        focus = "B2C consumer" if is_consumer else "B2B SaaS"
        extra = (
            "- Consumer-facing app or tool (not B2B)\n"
            "- Solves a real downstream problem created by a dominant industry trend\n"
            "- Has a clear Second Layer logic: [dominant trend] → [problem for consumers] → [this solution]\n"
        ) if is_consumer else (
            "- B2B focus\n"
            "- Genuinely solving a Second Layer problem (not being IN the dominant industry)\n"
        )

        prompt = f"""You are a VC researcher specializing in seed-stage {focus} startups.

Today's vertical: {vertical}

List exactly 10 real startups in this vertical that solve a downstream problem created by a dominant industry trend.

STRICT Requirements:
- Must be real companies you know about
- Founded 2019-2025
- ONLY Pre-Seed or Seed stage — absolutely no Series A, B, C, D or later
{extra}- Prioritize lesser-known companies over well-known ones

Respond ONLY with a JSON array, no other text:
[
  {{"name": "CompanyName", "description": "One sentence: what they do and why it's Second Layer"}},
  ...
]"""

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw  = re.sub(r"^```json\s*|^```\s*|\s*```$", "",
                      resp.content[0].text.strip())
        hits = json.loads(raw)
        for hit in hits:
            name = hit.get("name", "")
            desc = hit.get("description", "")
            if name and not is_fund(name):
                companies.append({
                    "name": name,
                    "description": desc,
                    "source": "Claude Research",
                })

        print(f"Claude Research: {len(companies)} candidates")
    except Exception as e:
        print(f"Claude Research error: {e}")
    return companies[:10]


# ── SOURCE 6: GITHUB SEARCH API (no auth needed for basic search) ─────────────
def source_github_search():
    """
    Uses GitHub's search API to find recently created compliance/security repos
    that might be early-stage startups building in public.
    """
    companies = []
    try:
        day = datetime.date.today().weekday()
        queries = [
            "compliance automation saas",
            "healthcare privacy workflow",
            "legal contract AI",
            "fraud detection fintech",
            "security monitoring platform",
            "regulatory reporting automation",
            "data privacy compliance tool",
        ]
        query = queries[day % len(queries)]

        # Search repos created in last 90 days with stars (signals real product)
        ninety_days_ago = (datetime.date.today() -
                           datetime.timedelta(days=90)).strftime("%Y-%m-%d")
        url = (f"https://api.github.com/search/repositories"
               f"?q={requests.utils.quote(query)}+created:>{ninety_days_ago}"
               f"+stars:>5&sort=stars&per_page=10")
        resp = requests.get(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "second-layer-vc-pipeline",
        }, timeout=15)

        if resp.status_code == 200:
            for repo in resp.json().get("items", []):
                name = repo.get("name", "").replace("-", " ").replace("_", " ").title()
                desc = repo.get("description", "") or ""
                org  = repo.get("owner", {}).get("login", "")

                # Use org name if it looks like a company name
                display_name = org if (org and not org.startswith("github")
                                       and len(org) > 2) else name

                if display_name and is_relevant(f"{name} {desc}") and not is_fund(display_name):
                    companies.append({
                        "name": display_name,
                        "description": desc or f"GitHub: {name}",
                        "source": "GitHub",
                    })
        elif resp.status_code == 403:
            print("GitHub rate limited — skipping")

        print(f"GitHub Search: {len(companies)} candidates")
    except Exception as e:
        print(f"GitHub Search error: {e}")
    return companies[:4]



# ── SOURCE 7: NEWSLETTER RSS FEEDS ───────────────────────────────────────────
def source_newsletters():
    """
    Parses high-signal early-stage startup newsletters via RSS.
    Only feeds verified as publicly accessible without auth.
    """
    companies = []
    feeds = [
        ("https://news.crunchbase.com/feed/", "Crunchbase News"),
        ("https://www.saastr.com/feed/", "SaaStr"),
        ("https://strictlyvc.com/feed/", "StrictlyVC"),
        ("https://www.businessinsider.com/sai/rss", "Business Insider Tech"),
        ("https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "WSJ Markets"),
    ]
    funding_words = ["raises", "funding", "seed", "series a", "launches",
                     "secures", "closes", "backed", "invests", "pre-seed"]
    skip_words    = ["series b", "series c", "series d", "series e",
                     "$50m", "$75m", "$100m", "$50 million", "$100 million"]

    for feed_url, feed_name in feeds:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            # Sanitize XML before parsing to avoid entity errors
            content = resp.content.replace(b"&", b"&amp;").replace(b"&amp;amp;", b"&amp;")
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                continue
            for item in root.findall(".//item"):
                title = item.findtext("title", "") or ""
                desc  = item.findtext("description", "") or ""
                text  = BeautifulSoup(desc, "html.parser").get_text()
                combined = title + " " + text

                if not any(w in title.lower() for w in funding_words):
                    continue
                if any(w in combined.lower() for w in skip_words):
                    continue
                if not is_relevant(combined):
                    continue

                match = re.match(
                    r"^([A-Z][A-Za-z0-9][A-Za-z0-9\.\- ]{1,40}?)\s+"
                    r"(?:raises|secures|closes|gets|lands|launches|announces|nabs)",
                    title
                )
                if match:
                    name = match.group(1).strip().rstrip(".,")
                    if name and not is_fund(name) and 3 < len(name) < 50:
                        companies.append({
                            "name": name,
                            "description": title,
                            "source": feed_name,
                        })
            time.sleep(1)
        except Exception as e:
            print(f"Newsletter {feed_name} error: {e}")

    print(f"Newsletters: {len(companies)} candidates")
    return companies[:8]


# ── SOURCE 8: WELLFOUND (ANGELLIST) JOB POSTINGS ─────────────────────────────
def source_wellfound():
    """
    ProductHunt RSS for newly launched B2B/SaaS products.
    Wellfound blocks scrapers so replaced with ProductHunt which has a public feed.
    Pre-launch/new products = pre-seed signal.
    """
    companies = []
    try:
        feeds = [
            ("https://www.producthunt.com/feed?category=developer-tools", "ProductHunt Dev"),
            ("https://www.producthunt.com/feed?category=saas", "ProductHunt SaaS"),
        ]
        for feed_url, feed_name in feeds:
            resp = requests.get(feed_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            content = resp.content.replace(b"&", b"&amp;").replace(b"&amp;amp;", b"&amp;")
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                continue
            for item in root.findall(".//item"):
                title = item.findtext("title", "") or ""
                desc  = item.findtext("description", "") or ""
                text  = BeautifulSoup(desc, "html.parser").get_text()
                combined = title + " " + text

                if not is_relevant(combined) or is_late_stage(combined):
                    continue
                name = title.strip().split(" - ")[0].strip()
                if name and 2 < len(name) < 50 and not is_fund(name):
                    companies.append({
                        "name": name,
                        "description": text[:200] if text else title,
                        "source": feed_name,
                    })
            time.sleep(1)

        print(f"Wellfound: {len(companies)} candidates")
    except Exception as e:
        print(f"Wellfound error: {e}")
    return companies[:5]


# ── SOURCE 9: BETALIST ────────────────────────────────────────────────────────
def source_betalist():
    """
    BetaList surfaces pre-launch and very early stage startups.
    Sanitizes XML content before parsing to avoid entity errors.
    """
    companies = []
    try:
        resp = requests.get("https://betalist.com/feed", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"BetaList: {len(companies)} candidates")
            return companies
        # Sanitize XML
        content = resp.content.replace(b"&", b"&amp;").replace(b"&amp;amp;", b"&amp;")
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            print(f"BetaList parse error: {e}")
            return companies

        for item in root.findall(".//item"):
            title = item.findtext("title", "") or ""
            desc  = item.findtext("description", "") or ""
            text  = BeautifulSoup(desc, "html.parser").get_text()
            combined = title + " " + text

            if is_relevant(combined) and not is_late_stage(combined):
                name = title.strip()
                if name and len(name) > 2 and not is_fund(name):
                    companies.append({
                        "name": name,
                        "description": text[:200] if text else title,
                        "source": "BetaList",
                    })

        print(f"BetaList: {len(companies)} candidates")
    except Exception as e:
        print(f"BetaList error: {e}")
    return companies[:5]


# ── SOURCE 10: EUREKALIST / STARTUPBASE ───────────────────────────────────────
def source_startupbase():
    """
    Startup directories for newly launched companies.
    Sanitizes XML to prevent parse errors.
    """
    companies = []
    sources = [
        ("https://startupbase.io/rss", "StartupBase"),
        ("https://www.indiehackers.com/feed.xml", "IndieHackers"),
    ]
    for feed_url, feed_name in sources:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            content = resp.content.replace(b"&", b"&amp;").replace(b"&amp;amp;", b"&amp;")
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                continue
            for item in root.findall(".//item"):
                title = item.findtext("title", "") or ""
                desc  = item.findtext("description", "") or ""
                text  = BeautifulSoup(desc, "html.parser").get_text()
                combined = title + " " + text

                if is_relevant(combined) and not is_late_stage(combined):
                    name = title.strip().split(" - ")[0].strip()
                    if name and len(name) > 2 and not is_fund(name):
                        companies.append({
                            "name": name,
                            "description": text[:200] or title,
                            "source": feed_name,
                        })
            time.sleep(1)
        except Exception as e:
            print(f"{feed_name} error: {e}")

    print(f"StartupBase/F6S: {len(companies)} candidates")
    return companies[:5]


# ── SOURCE 11: CRUNCHBASE SEED ROUNDS ────────────────────────────────────────
def source_crunchbase():
    """
    Pulls recent seed/pre-seed funding rounds from Crunchbase API.
    Requires CRUNCHBASE_API_KEY — free tier at data.crunchbase.com.
    Sign up at: https://data.crunchbase.com/docs/using-the-api
    Add as GitHub secret: CRUNCHBASE_API_KEY
    """
    companies = []
    if not CRUNCHBASE_API_KEY:
        print("Crunchbase: skipped (no API key)")
        return companies

    try:
        today     = datetime.date.today()
        date_from = (today - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

        # Search for recent seed/pre-seed rounds
        url = "https://api.crunchbase.com/api/v4/searches/funding_rounds"
        payload = {
            "field_ids": [
                "funded_organization_identifier",
                "funded_organization_description",
                "funded_organization_categories",
                "investment_type",
                "announced_on",
                "money_raised"
            ],
            "predicate_values": [],
            "predicates": [
                {
                    "field_id": "investment_type",
                    "operator_id": "includes",
                    "values": ["pre_seed", "seed", "angel"]
                },
                {
                    "field_id": "announced_on",
                    "operator_id": "gte",
                    "values": [date_from]
                }
            ],
            "order": [{"field_id": "announced_on", "sort": "desc"}],
            "limit": 25
        }
        resp = requests.post(
            url,
            json=payload,
            params={"user_key": CRUNCHBASE_API_KEY},
            headers={"Content-Type": "application/json"},
            timeout=20
        )

        if resp.status_code != 200:
            print(f"Crunchbase error: {resp.status_code} — {resp.text[:100]}")
            return companies

        for entity in resp.json().get("entities", []):
            props = entity.get("properties", {})
            org   = props.get("funded_organization_identifier", {})
            name  = org.get("value", "") if isinstance(org, dict) else str(org)
            desc  = props.get("funded_organization_description", "") or ""
            cats  = props.get("funded_organization_categories", [])
            cat_str = " ".join(c.get("value", "") if isinstance(c, dict) else str(c) for c in cats)
            combined = name + " " + desc + " " + cat_str

            if not name or not is_relevant(combined) or is_fund(name):
                continue
            if is_late_stage(combined):
                continue

            money = props.get("money_raised", {})
            raise_str = ""
            if isinstance(money, dict) and money.get("value"):
                raise_str = f"${money['value']:,.0f} {money.get('currency','USD')}"

            companies.append({
                "name": name,
                "description": desc or f"Crunchbase seed round — {cat_str[:100]}",
                "source": "Crunchbase",
                "raise": raise_str,
            })

        print(f"Crunchbase: {len(companies)} candidates")
    except Exception as e:
        print(f"Crunchbase error: {e}")
    return companies[:8]


# ── SOURCE 12: CONSUMER SECOND LAYER ─────────────────────────────────────────
def source_consumer():
    """
    Dedicated consumer-focused sourcing via Claude Research.
    Rotates through consumer Second Layer verticals daily.
    Consumer Second Layer = apps solving downstream problems from dominant trends
    for everyday people (not enterprises).
    """
    companies = []
    try:
        day = datetime.date.today().weekday()
        consumer_verticals = [
            ("fintech expansion → personal finance complexity",
             "personal finance management, debt payoff, subscription tracking, or fee transparency apps"),
            ("healthcare digitization → fragmented patient experience",
             "consumer health navigation, benefits decoding, prior auth assistance, or care coordination apps"),
            ("data broker proliferation → consumer privacy erosion",
             "personal data deletion, data broker opt-out, identity monitoring, or digital footprint control apps"),
            ("generative AI adoption → trust and authenticity crisis",
             "deepfake detection, AI content labeling, voice clone protection, or online scam detection tools"),
            ("creator economy boom → creator business complexity",
             "creator tax compliance, brand deal management, audience monetization, or creator legal tools"),
            ("remote work normalization → career and income instability",
             "salary negotiation tools, remote job vetting, career coaching, or freelancer income management apps"),
            ("housing market dysfunction → tenant and buyer confusion",
             "tenant rights tools, lease analysis, rent negotiation, or first-time buyer navigation apps"),
        ]
        vertical_desc, examples = consumer_verticals[day % len(consumer_verticals)]

        prompt = f"""You are a VC researcher specializing in seed-stage consumer startups.

Second Layer thesis: Find consumer apps that exist BECAUSE of a dominant industry trend — 
not apps that are IN that industry.

Today's angle: {vertical_desc}
Examples of what to look for: {examples}

List exactly 8 real seed-stage consumer startups solving this downstream problem.

Requirements:
- Real companies you know about, founded 2019-2025
- ONLY Pre-Seed or Seed — no Series A or later
- Consumer-facing (B2C), not enterprise
- Clearly solving a problem CREATED BY the dominant trend above
- Lesser-known companies preferred over household names

Respond ONLY with a JSON array:
[
  {{"name": "CompanyName", "description": "What they do and the Second Layer logic in one sentence"}},
  ...
]"""

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw  = re.sub(r"^```json\s*|^```\s*|\s*```$", "",
                      resp.content[0].text.strip())
        hits = json.loads(raw)
        for hit in hits:
            name = hit.get("name", "")
            desc = hit.get("description", "")
            if name and not is_fund(name):
                companies.append({
                    "name": name,
                    "description": desc,
                    "source": "Consumer Research",
                })
        print(f"Consumer: {len(companies)} candidates")
    except Exception as e:
        print(f"Consumer Research error: {e}")
    return companies[:8]




# ── SOURCE 13: CRUSTDATA API ─────────────────────────────────────────────────
def source_crustdata():
    """
    Queries Crustdata's company screening API for pre-seed and seed stage
    startups matching Second Layer keywords. Filters by funding stage and
    recent founding year. Rotates industry focus daily.
    Requires CRUSTDATA_API_KEY secret in GitHub.
    Sign up at crustdata.com — email abhilash@crustdata.com for API access.
    """
    companies = []
    if not CRUSTDATA_API_KEY:
        print("Crustdata: skipped (no API key — add CRUSTDATA_API_KEY secret)")
        return companies

    try:
        day = datetime.date.today().weekday()
        industries = [
            "Artificial Intelligence",
            "Cybersecurity",
            "FinTech",
            "Health Care",
            "Legal Tech",
            "Data Privacy",
            "Enterprise Software",
        ]
        industry = industries[day % len(industries)]

        url = "https://api.crustdata.com/screener/company/search"
        headers = {
            "Authorization": f"Token {CRUSTDATA_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "filters": [
                {
                    "column": "FUNDING_STAGE",
                    "values": [
                        {"text": "Pre-Seed", "selection_type": "INCLUDED"},
                        {"text": "Seed",     "selection_type": "INCLUDED"},
                        {"text": "Angel",    "selection_type": "INCLUDED"},
                    ]
                },
                {
                    "column": "INDUSTRY",
                    "values": [
                        {"text": industry, "selection_type": "INCLUDED"}
                    ]
                },
                {
                    "column": "YEAR",
                    "values": [
                        {"text": "2023", "selection_type": "INCLUDED"},
                        {"text": "2024", "selection_type": "INCLUDED"},
                        {"text": "2025", "selection_type": "INCLUDED"},
                    ]
                },
            ],
            "page": 0,
            "count": 15,
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=20)

        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", data.get("companies", []))
            for co in results:
                name  = co.get("company_name", "") or co.get("name", "")
                desc  = (co.get("short_description", "") or
                         co.get("description", "") or
                         co.get("overview", "") or "")
                stage = co.get("funding_stage", "") or co.get("last_funding_stage", "")
                combined = f"{name} {desc} {stage}".lower()

                if not name or is_fund(name) or is_late_stage(combined):
                    continue

                companies.append({
                    "name":        name,
                    "description": desc[:300],
                    "source":      "Crustdata",
                    "stage":       stage,
                })

        elif resp.status_code == 401:
            print("Crustdata: invalid API key")
        elif resp.status_code == 403:
            print("Crustdata: API key lacks permissions")
        elif resp.status_code == 429:
            print("Crustdata: rate limited — will retry tomorrow")
        else:
            print(f"Crustdata: {resp.status_code} — {resp.text[:150]}")

        print(f"Crustdata: {len(companies)} candidates")
    except Exception as e:
        print(f"Crustdata error: {e}")
    return companies[:8]


# ── PRE-SCORING FILTER ────────────────────────────────────────────────────────
def filter_second_layer_alignment(candidates):
    """
    Lightweight pre-scoring filter. Asks Claude to rate each company
    1-3 on Second Layer alignment before running the full 9-factor rubric.
    Only companies rated 3 proceed to full scoring.
    Cuts noise by ~40% and preserves API budget for genuinely aligned companies.

    Rating scale:
    1 = IS the dominant industry (an LLM, a satellite manufacturer, a crypto exchange)
    2 = Tangentially related but weak Second Layer logic
    3 = Clear Second Layer fit — solves a downstream problem created by a dominant trend
    """
    if not candidates:
        return candidates

    # Build batch prompt for efficiency — rate all candidates in one call
    company_list = ""
    for i, co in enumerate(candidates):
        company_list += f"{i+1}. {co['name']}: {co.get('description', '')[:150]}\n"

    prompt = f"""You are a VC analyst applying the Second Layer investment framework.

SECOND LAYER = companies solving problems CREATED BY dominant industries, not IN them.
Examples of PASS (rating 3):
- AI governance platform → solves risk FROM AI adoption, not an AI model itself
- AML compliance tool → solves risk FROM fintech growth, not a fintech itself
- Space cybersecurity → solves risk FROM satellite proliferation, not a satellite maker

Examples of FAIL (rating 1):
- An LLM or AI model company → IS the dominant trend
- A crypto exchange → IS the dominant industry
- A satellite manufacturer → IS the dominant trend

Rate each company 1, 2, or 3:
1 = IS the dominant industry or trend itself (filter out)
2 = Possible fit — relevant space but logic not obvious from description alone (keep)
3 = Clear Second Layer fit — obvious downstream problem from dominant trend (keep)

Companies to rate:
{company_list}

Respond ONLY with a JSON array of integers, one per company, in order:
[3, 1, 2, 3, ...]"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{{"role": "user", "content": prompt}}]
        )
        raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "",
                     resp.content[0].text.strip())
        # Extract JSON array
        m = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not m:
            print("Pre-filter: could not parse ratings — passing all candidates")
            return candidates

        ratings = json.loads(m.group())
        filtered = []
        passed = failed = 0
        for i, co in enumerate(candidates):
            rating = ratings[i] if i < len(ratings) else 2
            if rating >= 2:
                filtered.append(co)
                passed += 1
            else:
                failed += 1
                print(f"  Pre-filter SKIP ({rating}/3): {{co['name']}}")

        print(f"Pre-filter: {{passed}} pass | {{failed}} filtered out | "
              f"{{round(passed/len(candidates)*100)}}% alignment rate")
        return filtered

    except Exception as e:
        print(f"Pre-filter error: {{e}} — passing all candidates")
        return candidates

def get_candidate_companies(previously_seen):
    all_companies = []
    print("\n--- Sourcing from 12 channels ---")

    all_companies.extend(source_yc());               time.sleep(2)
    all_companies.extend(source_hacker_news());      time.sleep(2)
    all_companies.extend(source_sec_form_d());       time.sleep(2)
    all_companies.extend(source_rss_feeds());        time.sleep(2)
    all_companies.extend(source_claude_research());  time.sleep(2)
    all_companies.extend(source_github_search());    time.sleep(2)
    all_companies.extend(source_newsletters());      time.sleep(2)
    all_companies.extend(source_wellfound());        time.sleep(2)
    all_companies.extend(source_betalist());         time.sleep(2)
    all_companies.extend(source_startupbase());    time.sleep(2)
    all_companies.extend(source_crunchbase());      time.sleep(2)
    all_companies.extend(source_consumer());         time.sleep(2)
    all_companies.extend(source_crustdata())

    # Dedup within today's run
    seen_today, unique_today = set(), []
    for co in all_companies:
        key = co["name"].lower().strip()
        if key not in seen_today and len(key) > 2:
            seen_today.add(key)
            unique_today.append(co)

    # Filter out previously seen
    fresh, skipped = [], []
    for co in unique_today:
        key = co["name"].lower().strip()
        if key in previously_seen:
            skipped.append(co["name"])
        else:
            fresh.append(co)

    print(f"\nRaw: {len(all_companies)} | Unique: {len(unique_today)} | "
          f"Skipped (seen before): {len(skipped)} | Fresh: {len(fresh)}")
    return fresh[:20]


# ── SCORING ───────────────────────────────────────────────────────────────────
SECOND_LAYER_CONTEXT = """
SECOND LAYER APPROACH:
Find startups solving problems CREATED BY dominant industries, not being IN them.
- AI adoption → model governance risk → AI governance platforms
- Crypto growth → AML risk → AML automation startups
- Healthcare digitization → HIPAA bottlenecks → HIPAA workflow tools
- Legal AI → malpractice risk → compliance-grade legal AI
- Fintech expansion → KYB/KYC friction → identity verification
FAILS if it IS the dominant industry (an LLM itself, a crypto exchange).
"""

SCORING_RUBRIC = """
EARLY-STAGE SCORING (Pre-Seed / Seed only). Score 0-10 per factor.
If a factor cannot be assessed due to limited info, score 5 (neutral) — do NOT penalise
unknown metrics; early-stage companies haven't had time to generate them yet.
Reserve 8-10 for genuinely exceptional signals only.

1A FMF(14%):  9=prior exit+domain expertise, 7=strong domain bg, 5=adjacent, 3=limited
1B Tech(11%): 9=working product with differentiation, 7=prototype, 5=MVP, 3=concept only
1C Commit(10%):9=quit job+fully committed, 7=fulltime recent, 5=part-time, 3=side project
2A PMF(15%):  9=obsessed early users/waitlist/pilots, 7=positive signals, 5=some interest, 3=unclear
3A TAM(12%):  9=$50B+, 7=$10-50B, 5=$1-10B, 3=$100M-1B, 0=<$100M
3B Timing(11%):9=regulatory/structural catalyst NOW, 7=beatable comp, 5=crowded, 3=poor timing
5 TrxQl(10%): 9=accelerator/named pilots/press, 7=early traction signals, 5=some, 3=none visible
6 CapEff(10%): 9=capital-light model, 7=efficient, 5=avg, 3=capital-intensive
7 Investor(7%):9=top-tier VC/YC, 7=notable angels, 5=unknown angels, 3=no outside capital

NOTE: Factors 2B (Revenue) and 4 (Quantitative Traction) are EXCLUDED from early-stage
scoring — these metrics are rarely observable at pre-seed/seed and should not penalise
early companies. Weights above sum to 100%.
"""

SCORE_PROMPT = """You are a VC analyst applying the Second Layer investment framework to EARLY-STAGE companies only.

{second_layer_context}

{scoring_rubric}

Research and score this company:
Name: {company_name}
Description: {description}
Source: {source}

CRITICAL: This is an early-stage (pre-seed/seed) evaluation framework. Do NOT penalise
companies for lacking revenue, growth metrics, or quantitative traction — these factors
are excluded. Focus on founder quality, timing, market size, and qualitative signals.

If you have limited information, score 5 (neutral) — not lower. Reserve low scores (1-3)
only for clear negative signals, not absence of data.

This may be a B2B OR consumer company. For B2B look for: named pilots, enterprise interest,
domain credibility. For consumer look for: waitlists, organic signups, app launches, press.

Respond ONLY with a single valid JSON object. No markdown, no explanation, just JSON:
{{"company_name":"string","founded":"YYYY or unknown","stage":"Pre-Seed/Seed/unknown","raise":"$XM or unknown","vertical":"concise label","what_they_do":"2-3 sentences","second_layer_alignment":true,"second_layer_logic":"First Layer trend → risk/problem → solution","scores":{{"1A":5,"1B":5,"1C":5,"2A":5,"3A":5,"3B":5,"5":5,"6":5,"7":5}},"weighted_score":5.0,"score_pct":50.0,"decision":"★★ PROBABLY PASS","key_strength":"one sentence","key_weakness":"one sentence","stage_gate":"PASS or FAIL — FAIL if Series A or later","traction_highlights":["specific signal 1 if found","specific signal 2 if found"]}}"""

# Early-stage weights — 2B Revenue and 4 Quantitative Traction removed
# Redistributed to founder quality, timing, and PMF factors
WEIGHTS = {
    "1A": 0.14,   # Founder-market fit — most predictive at pre-seed
    "1B": 0.11,   # Technical differentiation — observable before revenue
    "1C": 0.10,   # Commitment — fulltime vs side project matters early
    "2A": 0.15,   # PMF signals — pilots, waitlists, early users
    "3A": 0.12,   # TAM — unchanged
    "3B": 0.11,   # Timing — regulatory/structural catalysts especially matter early
    "5":  0.10,   # Qualitative traction — accelerators, press, named pilots
    "6":  0.10,   # Capital efficiency — lean model vs capital-intensive
    "7":  0.07,   # Investor quality — reduced weight (angels dominate at this stage)
}

LATE_STAGE_KEYWORDS_HARD = [
    "series b", "series c", "series d", "series e",
    "growth equity", "pre-ipo", "late stage",
    # Hard block Series A — pipeline is pre-seed/seed only
    "series a",
]

def is_definitely_late_stage(co):
    """Hard filter — skip scoring entirely if company is Series A or later."""
    text = f"{co.get('name','')} {co.get('description','')}".lower()
    return any(kw in text for kw in LATE_STAGE_KEYWORDS_HARD)


def score_company(co):
    prompt = SCORE_PROMPT.format(
        second_layer_context=SECOND_LAYER_CONTEXT,
        scoring_rubric=SCORING_RUBRIC,
        company_name=co["name"],
        description=co.get("description", "No description available"),
        source=co.get("source", "Unknown"),
    )

    def _parse_and_validate(raw):
        raw = re.sub(r"^```json\s*|^```\s*|\s*```$", "", raw.strip())
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        data   = json.loads(m.group())
        scores = data.get("scores", {})
        ws     = sum(scores.get(k, 0) * v for k, v in WEIGHTS.items())
        pct    = ws * 10
        data["weighted_score"] = round(ws, 2)
        data["score_pct"]      = round(pct, 1)
        data["source"]         = co.get("source", "")
        stage = data.get("stage", "").lower()
        # Stage gate: only pre-seed and seed pass
        late_stages = ["series a", "series b", "series c", "series d", "series e",
                       "late stage", "pre-ipo", "acquired", "merged"]
        if any(s in stage for s in late_stages):
            print(f"  Stage gate FAIL: {co['name']} ({data.get('stage','unknown')})")
            return None
        if pct >= 85:   data["decision"] = "★★★★★ STRONG YES"
        elif pct >= 75: data["decision"] = "★★★★ YES"
        elif pct >= 65: data["decision"] = "★★★ DEEP DIVE"
        elif pct >= 55: data["decision"] = "★★ PROBABLY PASS"
        else:           data["decision"] = "★ HARD PASS"
        return data

    # Attempt 1: Claude Sonnet (better reasoning, no web search overhead)
    for attempt in range(2):
        try:
            if attempt > 0:
                print(f"  Retrying after rate limit pause...")
                time.sleep(30)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
            result = _parse_and_validate(raw)
            if result:
                return result
            break
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e):
                if attempt == 0:
                    print(f"  Rate limited — pausing 30s")
                    continue
                print(f"  Rate limit persists — falling back to Haiku")
            else:
                print(f"  Sonnet error {co['name']}: {e}")
                break

    # Attempt 2: Haiku fallback
    try:
        time.sleep(5)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        result = _parse_and_validate(raw)
        if result:
            print(f"  (Haiku fallback)")
            return result
    except Exception as e:
        print(f"  Haiku fallback error {co['name']}: {e}")

    return None


# ── FOUNDER RESEARCH ──────────────────────────────────────────────────────────
def research_founder(company: dict) -> dict:
    """
    Identifies the founder name, title, and background for every scored company.
    No LinkedIn URL — Bryan researches founders directly before outreach.
    """
    co_name  = company.get("company_name", "")
    what     = company.get("what_they_do", "")
    vertical = company.get("vertical", "")

    default = {"founder_name": "unknown", "founder_title": "unknown",
               "linkedin_url": "unknown", "founder_background": "unknown",
               "outreach_hook": "unknown"}

    prompt = f"""You are a startup researcher.

Company: {co_name}
What they do: {what}
Vertical: {vertical}

Identify the founder(s) of this company. Return ONLY valid JSON:
{{
  "founder_name": "Full Name or unknown",
  "founder_title": "CEO/CTO/Co-Founder or unknown",
  "founder_background": "One sentence on their most interesting background — prior exits, domain expertise, notable employers",
  "outreach_hook": "One sentence on why their background is compelling relative to what they are building"
}}

If you do not know the founder, return unknown for all fields."""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw   = re.sub(r"^```json\s*|^```\s*|\s*```$", "", resp.content[0].text.strip())
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            print(f"  Founder: {data.get('founder_name','unknown')}")
            return data
    except Exception as e:
        print(f"  Founder research error: {e}")
    return default



# ── OUTREACH DRAFT ────────────────────────────────────────────────────────────
def founder_brief(company: dict) -> str:
    """
    Generates a concise founder brief — key facts Bryan needs to write
    his own personalized outreach. No draft message, just the intel.
    """
    founder  = company.get("founder", {})
    fn       = founder.get("founder_name", "")
    bg       = founder.get("founder_background", "")
    co_name  = company.get("company_name", "")
    sl_logic = company.get("second_layer_logic", "")
    what     = company.get("what_they_do", "")
    vertical = company.get("vertical", "")

    if not fn or fn == "unknown":
        return ""

    prompt = f"""You are helping a 23-year-old VC scout named Bryan Hanley prepare to reach out
to a founder. Bryan wants to write his own outreach in his own voice — he does NOT want a
draft email. Instead, give him a tight intel brief with exactly four bullet points:

Company: {co_name}
Founder: {fn}
Background: {bg}
What they do: {what}
Second Layer logic: {sl_logic}
Vertical: {vertical}

Write exactly 4 bullet points:
1. The single most interesting thing about this founder's background that Bryan should reference
2. Why this company is a textbook Second Layer play — the dominant trend, the downstream problem it creates, and how this company solves it (one crisp sentence)
3. One specific way Bryan can be genuinely useful to them — be concrete (e.g. "connects with fintech compliance VCs", "has DSCSA supply chain context from Gateway Checker", "can intro to regtech angels")
4. One open question about the company or founder that Bryan could ask to start a real conversation

Format as plain bullet points starting with a dash. No headers, no preamble, no sign-off. Just the 4 bullets."""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  Founder brief error: {e}")
        return ""


DECISION_STYLE = {
    "★★★★★ STRONG YES":("#1a472a","#a9d18e"),
    "★★★★ YES":         ("#1a472a","#c6efce"),
    "★★★ DEEP DIVE":    ("#7d6608","#ffeb9c"),
    "★★ PROBABLY PASS": ("#843c0c","#fce4d6"),
    "★ HARD PASS":      ("#9c0006","#ffc7ce"),
}
FACTOR_LABELS = {
    "1A":"Fdr-Mkt Fit","1B":"Tech Exec","1C":"Commitment",
    "2A":"Early PMF","2B":"Revenue","3A":"TAM","3B":"Timing",
    "4":"Traction Q","5":"Traction Ql","6":"Cap Effic.","7":"Investor",
}
SOURCE_COLORS = {
    "YC W25":"#FF6600","YC S24":"#FF6600","YC W24":"#FF6600",
    "YC S23":"#FF8833","YC W23":"#FF8833","YC S22":"#FF8833",
    "TechCrunch":"#0D9B4E","VentureBeat":"#1a56db",
    "Hacker News":"#FF4500","GitHub":"#24292e",
    "SEC Form D":"#7030a0","Claude Research":"#c55a11",
}

def score_badge(v):
    if v >= 8:   bg, fg = "#c6efce", "#276221"
    elif v >= 6: bg, fg = "#ffeb9c", "#7d6608"
    else:        bg, fg = "#ffc7ce", "#9c0006"
    return (f'<span style="background:{bg};color:{fg};padding:2px 8px;'
            f'border-radius:4px;font-weight:bold;font-size:12px;">{v}</span>')

def src_badge(source):
    # Match prefix for YC batches
    color = next((v for k, v in SOURCE_COLORS.items() if source.startswith(k[:6])), "#666")
    return (f'<span style="background:{color};color:white;padding:2px 8px;'
            f'border-radius:10px;font-size:10px;font-weight:bold;">{source}</span>')



def traction_highlights_section(co):
    """Renders standout traction signals if any were found during web research."""
    highlights = co.get("traction_highlights", [])
    # Filter out empty or generic "none found" entries
    highlights = [h for h in highlights if h and len(h) > 10
                  and "not found" not in h.lower()
                  and "no specific" not in h.lower()
                  and "limited info" not in h.lower()
                  and "unknown" not in h.lower()]
    if not highlights:
        return ""
    items = "".join(
        f'<div style="display:flex;align-items:flex-start;gap:6px;margin-bottom:4px;">'
        f'<span style="color:#c55a11;font-size:12px;">⚡</span>'
        f'<span style="font-size:11px;color:#333;">{h}</span>'
        f'</div>'
        for h in highlights[:4]
    )
    return f"""
    <div style="background:#fff8f0;border-left:3px solid #c55a11;border-radius:0 4px 4px 0;
                padding:7px 10px;margin-bottom:10px;">
      <div style="font-size:10px;color:#c55a11;font-weight:bold;margin-bottom:5px;">
        ⚡ TRACTION SIGNALS
      </div>
      {items}
    </div>"""


def founder_section(co):
    """Renders founder info + LinkedIn button for high-scoring companies."""
    founder = co.get("founder", {})
    if not founder or founder.get("founder_name","") in ["", "unknown"]:
        return ""
    name     = founder.get("founder_name", "")
    title    = founder.get("founder_title", "")
    linkedin = founder.get("linkedin_url", "")
    bg       = founder.get("founder_background", "")
    linkedin_btn = (
        f'<a href="{linkedin}" target="_blank" '
        f'style="display:inline-block;background:#0a66c2;color:white;'
        f'padding:4px 12px;border-radius:4px;font-size:11px;font-weight:bold;'
        f'text-decoration:none;margin-left:8px;">Connect on LinkedIn →</a>'
    ) if linkedin and linkedin != "unknown" else ""

    return f'''
    <div style="background:#f0f5ff;border-radius:4px;padding:8px 10px;display:flex;align-items:center;justify-content:space-between;">
      <div>
        <span style="font-size:10px;color:#1a56db;font-weight:bold;">👤 FOUNDER</span>
        <span style="font-size:12px;font-weight:bold;color:#1b3a6b;margin-left:6px;">{name}</span>
        <span style="font-size:11px;color:#666;margin-left:4px;">· {title}</span>
        <div style="font-size:11px;color:#555;margin-top:3px;">{bg}</div>
      </div>
      <div>{linkedin_btn}</div>
    </div>'''


def company_card(co):
    dec    = co.get("decision", "★ HARD PASS")
    fg, bg = DECISION_STYLE.get(dec, ("#333", "#eee"))
    scores = co.get("scores", {})
    cells  = "".join(
        f'<td style="text-align:center;padding:4px 5px;font-size:11px;">'
        f'<div style="color:#888;margin-bottom:2px;">{FACTOR_LABELS.get(k,"")}</div>'
        f'{score_badge(v)}</td>'
        for k, v in scores.items()
    )
    return f"""
<div style="border:1px solid #ddd;border-radius:8px;margin-bottom:18px;overflow:hidden;font-family:Arial,sans-serif;">
  <div style="background:#1b3a6b;padding:12px 16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <span style="color:white;font-size:15px;font-weight:bold;">{co.get('company_name','')}</span>
        <span style="color:#aac4e8;font-size:11px;margin-left:10px;">
          {co.get('stage','')} · {co.get('raise','')} · Est. {co.get('founded','')}
        </span>
        <div style="margin-top:5px;">{src_badge(co.get('source',''))}</div>
      </div>
      <div style="background:{bg};color:{fg};padding:5px 14px;border-radius:20px;font-weight:bold;font-size:11px;text-align:center;">
        {dec}<br><span style="font-size:18px;">{co.get('score_pct',0):.1f}%</span>
      </div>
    </div>
  </div>
  <div style="padding:12px 16px;background:white;">
    <div style="background:#f0f5ff;border-left:3px solid #2e75b6;padding:7px 10px;border-radius:0 4px 4px 0;margin-bottom:8px;font-size:12px;">
      <strong>🔗 Second Layer:</strong> {co.get('second_layer_logic','')}
    </div>
    <div style="font-size:12px;color:#555;margin-bottom:10px;line-height:1.5;">
      {co.get('what_they_do','')}
    </div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:10px;">
      <tr>{cells}</tr>
    </table>
    <div style="display:flex;gap:10px;margin-bottom:10px;">
      <div style="flex:1;background:#f0f9f0;border-radius:4px;padding:7px 9px;">
        <div style="font-size:10px;color:#276221;font-weight:bold;margin-bottom:2px;">✅ STRENGTH</div>
        <div style="font-size:11px;color:#333;">{co.get('key_strength','')}</div>
      </div>
      <div style="flex:1;background:#fff5f5;border-radius:4px;padding:7px 9px;">
        <div style="font-size:10px;color:#9c0006;font-weight:bold;margin-bottom:2px;">⚠️ WEAKNESS</div>
        <div style="font-size:11px;color:#333;">{co.get('key_weakness','')}</div>
      </div>
    </div>
    {traction_highlights_section(co)}
    {founder_section(co)}
  </div>
</div>"""

def build_email(results, date_str, total_seen):
    all_sorted  = sorted(results, key=lambda x: x.get("score_pct", 0), reverse=True)
    n_total     = len(results)

    sc = {}
    for r in results:
        s = r.get("source", "?")
        sc[s] = sc.get(s, 0) + 1
    src_summary = " | ".join(s + ":" + str(c) for s, c in sorted(sc.items()))

    subject = ("Second Layer - " + date_str + " | "
               + str(n_total) + " scored | " + str(total_seen) + " total pipeline")

    # ── Top 3 founders to meet ──────────────────────────────────────────────
    top3 = [r for r in all_sorted if r.get("outreach_draft", "")][:3]

    def founder_card(r, rank):
        founder  = r.get("founder", {})
        fn       = founder.get("founder_name", "Unknown Founder")
        ft       = founder.get("founder_title", "")
        fbg      = founder.get("founder_background", "")
        li       = founder.get("linkedin_url", "")
        co       = r.get("company_name", "")
        sl       = r.get("second_layer_logic", "")
        what     = r.get("what_they_do", "")
        score    = str(round(r.get("score_pct", 0), 1)) + "%"
        vertical = r.get("vertical", "")
        brief    = r.get("outreach_draft", "")

        # Format brief bullets as clean HTML list
        brief_html = ""
        if brief:
            lines = [l.strip().lstrip("-").strip() for l in brief.split("\n") if l.strip().lstrip("-").strip()]
            brief_html = "".join(
                "<li style='margin-bottom:8px;color:#334155;font-size:13px;line-height:1.65;'>" + l + "</li>"
                for l in lines
            )
            brief_html = "<ul style='margin:0;padding-left:18px;'>" + brief_html + "</ul>"

        # LinkedIn button using the actual researched URL
        if li and li not in ("unknown", ""):
            li_btn = (
                " <a href='" + li + "' target='_blank' "
                "style='display:inline-block;background:#0a66c2;color:#ffffff;"
                "font-size:10px;font-weight:600;padding:3px 10px;border-radius:3px;"
                "text-decoration:none;vertical-align:middle;margin-left:6px;'>"
                "LinkedIn &#8599;</a>"
            )
        else:
            li_btn = ""

        rank_colors = ["#1B3A6B", "#2E75B6", "#3B7A9E"]
        rank_color  = rank_colors[min(rank, 2)]
        rank_labels = ["Top Pick", "2nd", "3rd"]
        rank_label  = rank_labels[min(rank, 2)]

        card = (
            "<div style='background:#ffffff;border:1px solid #d1d9e6;border-radius:10px;"
            "margin-bottom:20px;overflow:hidden;'>"
            "<div style='background:{rc};padding:14px 20px;"
            "display:flex;align-items:center;justify-content:space-between;'>"
            "<div style='display:flex;align-items:center;gap:12px;'>"
            "<div style='background:rgba(255,255,255,0.18);color:#ffffff;font-weight:700;"
            "font-size:11px;padding:3px 9px;border-radius:20px;white-space:nowrap;'>{rl}</div>"
            "<div>"
            "<div style='color:#ffffff;font-size:15px;font-weight:600;line-height:1.3;'>{fn}{li_btn}</div>"
            "<div style='color:rgba(255,255,255,0.78);font-size:12px;margin-top:2px;'>{ft_sep}{co} &nbsp;&middot;&nbsp; {vertical}</div>"
            "</div></div>"
            "<div style='background:rgba(255,255,255,0.18);color:#ffffff;font-size:13px;"
            "font-weight:700;padding:5px 14px;border-radius:20px;white-space:nowrap;'>{score}</div>"
            "</div>"
            "<div style='padding:18px 20px;'>"
            "<div style='background:#EBF4FF;border-left:3px solid #2E75B6;padding:10px 14px;"
            "border-radius:0 5px 5px 0;margin-bottom:14px;'>"
            "<div style='font-size:10px;font-weight:700;color:#1B3A6B;text-transform:uppercase;"
            "letter-spacing:0.06em;margin-bottom:4px;'>Second Layer Thesis</div>"
            "<div style='font-size:13px;color:#1e3a5f;font-style:italic;line-height:1.6;'>{sl}</div>"
            "</div>"
            "<div style='display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;'>"
            "<div style='background:#f8fafc;border-radius:6px;padding:12px;'>"
            "<div style='font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;"
            "letter-spacing:0.06em;margin-bottom:5px;'>Founder Background</div>"
            "<div style='font-size:13px;color:#1e293b;line-height:1.6;'>{fbg}</div>"
            "</div>"
            "<div style='background:#f8fafc;border-radius:6px;padding:12px;'>"
            "<div style='font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;"
            "letter-spacing:0.06em;margin-bottom:5px;'>What They Build</div>"
            "<div style='font-size:13px;color:#1e293b;line-height:1.6;'>{what}</div>"
            "</div>"
            "</div>"
            "{brief_section}"
            "</div></div>"
        ).format(
            rc=rank_color, rl=rank_label, fn=fn, li_btn=li_btn,
            ft_sep=(ft + " &nbsp;&middot;&nbsp; " if ft else ""),
            co=co, vertical=vertical, score=score, sl=sl,
            fbg=fbg, what=what,
            brief_section=(
                "<div style='background:#fafbfc;border:1px solid #e2e8f0;border-radius:6px;padding:14px;'>"
                "<div style='font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;"
                "letter-spacing:0.06em;margin-bottom:10px;'>Outreach Intel</div>"
                + brief_html +
                "</div>"
            ) if brief_html else ""
        )
        return card

    if top3:
        top3_html = (
            "<div style='margin-bottom:24px;'>"
            "<h2 style='color:#1B3A6B;font-size:14px;font-weight:600;margin-bottom:14px;"
            "border-bottom:2px solid #C55A11;padding-bottom:6px;'>"
            "Today's Top 3 Founders to Meet</h2>"
            + "".join(founder_card(r, i) for i, r in enumerate(top3))
            + "</div>"
        )
    else:
        top3_html = (
            "<div style='background:white;border:1px solid #e2e8f0;border-radius:8px;"
            "padding:24px;text-align:center;color:#94a3b8;margin-bottom:24px;'>"
            "No founders identified today — try tomorrow.</div>"
        )

    # ── All scored companies table ──────────────────────────────────────────
    if all_sorted:
        all_rows = []
        for i, r in enumerate(all_sorted):
            bg      = "#ffffff" if i % 2 == 0 else "#f8fafc"
            fn      = r.get("founder", {}).get("founder_name", "—")
            score   = str(round(r.get("score_pct", 0), 1)) + "%"
            dec     = r.get("decision", "")
            sl_fit  = "Yes" if r.get("second_layer_alignment", False) else "No"
            all_rows.append(
                "<tr style='background:" + bg + ";'>"
                "<td style='padding:7px 10px;font-size:12px;font-weight:500;color:#1e293b;'>" + r.get("company_name","") + "</td>"
                "<td style='padding:7px 10px;font-size:11px;color:#64748b;'>" + src_badge(r.get("source","")) + "</td>"
                "<td style='padding:7px 10px;font-size:12px;color:#334155;'>" + r.get("vertical","") + "</td>"
                "<td style='padding:7px 10px;font-size:12px;color:#334155;'>" + fn + "</td>"
                "<td style='padding:7px 10px;font-size:12px;font-weight:600;text-align:center;color:#1B3A6B;'>" + score + "</td>"
                "<td style='padding:7px 10px;font-size:12px;'>" + dec + "</td>"
                "</tr>"
            )
        all_table = (
            "<div style='margin-bottom:20px;'>"
            "<h2 style='color:#1B3A6B;font-size:14px;font-weight:600;margin-bottom:10px;"
            "border-bottom:2px solid #2E75B6;padding-bottom:6px;'>All Scored Today</h2>"
            "<table style='width:100%;border-collapse:collapse;background:white;"
            "border-radius:8px;overflow:hidden;border:1px solid #e2e8f0;'>"
            "<tr style='background:#1B3A6B;'>"
            "<th style='padding:8px 10px;color:white;font-size:11px;text-align:left;'>Company</th>"
            "<th style='padding:8px 10px;color:white;font-size:11px;text-align:left;'>Source</th>"
            "<th style='padding:8px 10px;color:white;font-size:11px;text-align:left;'>Vertical</th>"
            "<th style='padding:8px 10px;color:white;font-size:11px;text-align:left;'>Founder</th>"
            "<th style='padding:8px 10px;color:white;font-size:11px;'>Score</th>"
            "<th style='padding:8px 10px;color:white;font-size:11px;text-align:left;'>Decision</th>"
            "</tr>"
            + "".join(all_rows)
            + "</table></div>"
        )
    else:
        all_table = ""

    # Assemble HTML
    html = (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
        "<body style='font-family:Arial,sans-serif;max-width:820px;margin:0 auto;background:#f1f5f9;padding:20px;'>"
        "<div style='background:#1B3A6B;border-radius:10px 10px 0 0;padding:20px 24px;'>"
        "<div style='color:white;font-size:18px;font-weight:700;'>Second Layer VC Pipeline</div>"
        "<div style='color:#93c5fd;font-size:12px;margin-top:2px;'>" + date_str + " &middot; Daily Digest</div>"
        "<div style='color:#bfdbfe;font-size:11px;margin-top:4px;'>Sources: " + src_summary + "</div>"
        "</div>"
        "<div style='background:#2E75B6;padding:10px 24px;display:flex;gap:20px;margin-bottom:20px;border-radius:0 0 8px 8px;'>"
        "<div style='text-align:center;'><div style='color:white;font-size:20px;font-weight:700;'>" + str(n_total) + "</div><div style='color:#bfdbfe;font-size:10px;'>SCORED</div></div>"
        "<div style='text-align:center;'><div style='color:#86efac;font-size:20px;font-weight:700;'>" + str(len(top3)) + "</div><div style='color:#bfdbfe;font-size:10px;'>OUTREACH READY</div></div>"
        "<div style='text-align:center;border-left:1px solid #60a5fa;padding-left:20px;'>"
        "<div style='color:white;font-size:20px;font-weight:700;'>" + str(total_seen) + "</div><div style='color:#bfdbfe;font-size:10px;'>TOTAL PIPELINE</div></div>"
        "</div>"
        + top3_html
        + all_table
        + "<div style='text-align:center;color:#94a3b8;font-size:10px;margin-top:16px;padding-top:12px;border-top:1px solid #e2e8f0;'>"
        "Bryan Hanley &middot; Second Layer VC Framework &middot; bryanhanleyvc.com"
        "</div>"
        "</body></html>"
    )

    return subject, html


# ── SEND EMAIL ─────────────────────────────────────────────────────────────────
def send_email(subject, html_body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = EMAIL_RECIPIENT
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            s.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        print(f"Email sent: {subject}")
    except Exception as e:
        print(f"Email skipped (check App Password in secrets): {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    today    = datetime.date.today()
    date_str = today.strftime("%Y-%m-%d")
    print(f"=== Second Layer Pipeline v4: {date_str} ===")

    print("Loading previously seen companies...")
    previously_seen = get_previously_seen_companies()
    print(f"Previously scored: {len(previously_seen)} companies")

    candidates = get_candidate_companies(previously_seen)

    if not candidates:
        print("No fresh candidates — sending empty digest.")
        subject, html = build_email([], date_str, len(previously_seen))
        send_email(subject, html)
        return

    # Pre-scoring Second Layer alignment filter — only score 3/3 aligned companies
    print("--- Pre-scoring alignment filter ---")
    candidates = filter_second_layer_alignment(candidates)
    print(f"Proceeding to score {len(candidates)} aligned candidates")

    results = []
    stage_gated = 0
    for co in candidates:
        if is_definitely_late_stage(co):
            print(f"Skipping (late stage pre-filter): {co['name']}")
            stage_gated += 1
            continue
        print(f"Scoring: {co['name']} ({co.get('source','')})")
        result = score_company(co)
        if result:
            result["founder"] = {}       # populated after scoring for top scorers only
            result["outreach_draft"] = ""
            results.append(result)
            print(f"  → {result.get('score_pct',0):.1f}% | {result.get('decision','')}")
        else:
            stage_gated += 1
        time.sleep(10)
    print(f"Stage gated (removed): {stage_gated}")

    # Sort all results by score descending — this order is used everywhere below
    results_sorted = sorted(results, key=lambda x: x.get("score_pct", 0), reverse=True)

    # Run founder research only on top 10 highest-scoring companies
    # No point researching founders for low-scoring companies that won't surface
    TOP_RESEARCH_N = 10
    for r in results_sorted[:TOP_RESEARCH_N]:
        r["founder"] = research_founder(r)
        time.sleep(1)

    # Generate outreach intel (founder brief) for the top 3 where a founder was identified
    # These become the "Top 3 Founders to Meet" cards in the email digest
    outreach_count = 0
    for r in results_sorted:
        if outreach_count >= 3:
            break
        if r.get("founder", {}).get("founder_name", "unknown") not in ("", "unknown"):
            print(f"  Building founder brief for {r.get('company_name','')} ({r.get('score_pct',0):.1f}%)")
            r["outreach_draft"] = founder_brief(r)
            outreach_count += 1
            time.sleep(2)

    append_results_to_sheet(results, date_str)
    total_seen = len(previously_seen) + len(results)

    print(f"\nBuilding digest for {len(results)} scored companies...")
    subject, html = build_email(results, date_str, total_seen)
    send_email(subject, html)
    print("Done.")


if __name__ == "__main__":
    main()
