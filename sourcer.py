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
- ONLY Seed or Series A stage — absolutely no Series B, C, D or later
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
- ONLY Seed or Series A — no Series B or later
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
    all_companies.extend(source_consumer())

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
Score 0-10 per factor. Default 5 if unknown. Reserve 8-10 for exceptional only.
1A FMF(10%): 9=exit+domain,7=strong bg,5=adjacent,3=limited
1B Tech(8%): 9=working product,7=prototype,5=MVP,3=struggling
1C Commit(7%): 9=quit job,7=fulltime,5=recent,3=side project
2A PMF(12%): 9=obsessed users,7=good engage,5=some users,3=low
2B Rev(8%): 9=strong rev,7=some rev,5=pilots,3=minimal,0=none
3A TAM(12%): 9=$50B+,7=$10-50B,5=$1-10B,3=$100M-1B,0=<$100M
3B Timing(8%): 9=greenfield,7=beatable comp,5=crowded,3=poor
4 TrxQ(7%): 9=>20%wk,7=10-20%,5=5-10%,3=<5%,0=none
5 TrxQl(8%): 9=devastated if gone,7=strong NPS,5=useful,3=mixed
6 CapEff(10%): 9=big on<$100K,7=efficient,5=avg,3=intensive
7 Investor(10%): 9=Seq/a16z/YC,7=tier1-2,5=angels,3=unknown
"""

SCORE_PROMPT = """You are a VC analyst applying the Second Layer investment framework.

{second_layer_context}

{scoring_rubric}

Research and score this company:
Name: {company_name}
Description: {description}
Source: {source}

Important: If you have limited information, score conservatively (4-6 range).
Do not hallucinate specific metrics — note "limited info" in weaknesses if applicable.

This may be a B2B OR consumer company. Apply the framework accordingly:
- For B2B: weight enterprise traction, contracts, ARR, logo customers
- For consumer: weight DAU/MAU, retention, app store ratings, organic growth, NPS

Look for ANY standout traction signals:
- B2B: customer logos, ARR/MRR figures, enterprise contracts, pilot programs
- Consumer: app store ratings/reviews, DAU/MAU, waitlist size, press coverage
- Both: GitHub stars, notable investors, accelerator participation, job posting volume

Respond ONLY with a single valid JSON object. No markdown, no explanation, just JSON:
{{"company_name":"string","founded":"YYYY or unknown","stage":"Pre-Seed/Seed/Series A/unknown","raise":"$XM or unknown","vertical":"concise label","what_they_do":"2-3 sentences","second_layer_alignment":true,"second_layer_logic":"First Layer trend → risk/problem → solution","scores":{{"1A":5,"1B":5,"1C":5,"2A":5,"2B":5,"3A":5,"3B":5,"4":5,"5":5,"6":5,"7":5}},"weighted_score":5.0,"score_pct":50.0,"decision":"★★ PROBABLY PASS","key_strength":"one sentence","key_weakness":"one sentence","stage_gate":"PASS or FAIL — FAIL if Series B or later","traction_highlights":["specific signal 1 if found","specific signal 2 if found"]}}"""

WEIGHTS = {"1A":0.10,"1B":0.08,"1C":0.07,"2A":0.12,"2B":0.08,
           "3A":0.12,"3B":0.08,"4":0.07,"5":0.08,"6":0.10,"7":0.10}

LATE_STAGE_KEYWORDS_HARD = ["series b", "series c", "series d", "series e",
                               "growth equity", "pre-ipo", "late stage"]

def is_definitely_late_stage(co):
    """Hard filter — skip scoring entirely if company is clearly Series B+."""
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
        stage      = data.get("stage", "").lower()
        late_stages = ["series b", "series c", "series d", "series e",
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
    For companies scoring ≥75%, asks Claude to identify the founder
    name and LinkedIn URL. Only runs on high-scoring companies to save cost.
    """
    prompt = f"""You are a startup researcher.

Find the founder(s) of this company:
Company: {company.get("company_name", "")}
What they do: {company.get("what_they_do", "")}
Vertical: {company.get("vertical", "")}

Return ONLY valid JSON, no other text:
{{
  "founder_name": "Full Name or unknown",
  "founder_title": "CEO/CTO/Co-Founder or unknown",
  "linkedin_url": "https://linkedin.com/in/handle or unknown",
  "founder_background": "One sentence on relevant background"
}}

If you are not confident about the LinkedIn URL, return "unknown" rather than guessing."""

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
            print(f"  Founder: {data.get('founder_name','')} — {data.get('linkedin_url','')}")
            return data
    except Exception as e:
        print(f"  Founder research error: {e}")
    return {"founder_name": "unknown", "founder_title": "unknown",
            "linkedin_url": "unknown", "founder_background": "unknown"}

# ── EMAIL ─────────────────────────────────────────────────────────────────────
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
    passing  = sorted([r for r in results if r.get("score_pct",0) >= MIN_SCORE_PCT],
                      key=lambda x: x.get("score_pct",0), reverse=True)
    below    = sorted([r for r in results if r.get("score_pct",0) < MIN_SCORE_PCT],
                      key=lambda x: x.get("score_pct",0), reverse=True)
    n_total  = len(results)
    n_pass   = len(passing)
    pass_rate = (n_pass / n_total * 100) if n_total > 0 else 0

    sc = {}
    for r in results:
        s = r.get("source", "?")
        sc[s] = sc.get(s, 0) + 1
    src_summary = " | ".join(s + ":" + str(c) for s, c in sorted(sc.items()))

    subject = ("Second Layer - " + date_str + " | "
               + str(n_pass) + " passing of " + str(n_total)
               + " | " + str(total_seen) + " total pipeline")

    # ── Passing cards ──────────────────────────────────────────────────────────
    if passing:
        cards = "".join(company_card(c) for c in passing)
    else:
        cards = "<p style='color:#888;text-align:center;padding:30px;'>No companies met threshold today.</p>"

    # ── Below threshold table ──────────────────────────────────────────────────
    if below:
        brows = []
        for i, r in enumerate(below):
            bg = "#ffffff" if i % 2 == 0 else "#f9f9f9"
            brows.append(
                "<tr style='background:" + bg + ";'>"
                + "<td style='padding:6px 10px;font-size:12px;'>" + str(r.get("company_name","")) + "</td>"
                + "<td style='padding:6px 10px;font-size:11px;'>" + src_badge(str(r.get("source",""))) + "</td>"
                + "<td style='padding:6px 10px;font-size:12px;color:#666;'>" + str(r.get("vertical","")) + "</td>"
                + "<td style='padding:6px 10px;font-size:12px;font-weight:bold;text-align:center;'>" + str(round(r.get("score_pct",0),1)) + "%</td>"
                + "<td style='padding:6px 10px;font-size:12px;'>" + str(r.get("decision","")) + "</td>"
                + "<td style='padding:6px 10px;font-size:12px;color:#888;'>" + str(r.get("key_weakness","")) + "</td>"
                + "</tr>"
            )
        below_section = (
            "<div style='margin-bottom:20px;'>"
            "<h2 style='color:#666;font-size:13px;border-bottom:1px solid #ddd;padding-bottom:5px;margin-bottom:8px;'>"
            "Evaluated But Filtered (below " + str(int(MIN_SCORE_PCT)) + "%)</h2>"
            "<table style='width:100%;border-collapse:collapse;background:white;border-radius:6px;overflow:hidden;'>"
            "<tr style='background:#1b3a6b;'>"
            "<th style='padding:7px 10px;color:white;font-size:11px;text-align:left;'>Company</th>"
            "<th style='padding:7px 10px;color:white;font-size:11px;text-align:left;'>Source</th>"
            "<th style='padding:7px 10px;color:white;font-size:11px;text-align:left;'>Vertical</th>"
            "<th style='padding:7px 10px;color:white;font-size:11px;'>Score</th>"
            "<th style='padding:7px 10px;color:white;font-size:11px;text-align:left;'>Decision</th>"
            "<th style='padding:7px 10px;color:white;font-size:11px;text-align:left;'>Weakness</th>"
            "</tr>"
            + "".join(brows)
            + "</table></div>"
        )
    else:
        below_section = ""

    # ── Founder outreach table ─────────────────────────────────────────────────
    outreach_candidates = [
        r for r in passing
        if r.get("founder", {}).get("founder_name", "unknown") not in ["", "unknown"]
    ]
    if outreach_candidates:
        orows = []
        for i, r in enumerate(outreach_candidates):
            bg      = "#ffffff" if i % 2 == 0 else "#f9f9f9"
            founder = r.get("founder", {})
            li_url  = founder.get("linkedin_url", "")
            if li_url and li_url != "unknown":
                li_btn = "<a href='" + li_url + "' target='_blank' style='color:#0a66c2;font-weight:bold;text-decoration:none;'>LinkedIn</a>"
            else:
                li_btn = "<span style='color:#aaa;'>-</span>"
            orows.append(
                "<tr style='background:" + bg + ";'>"
                + "<td style='padding:8px 12px;font-size:12px;font-weight:bold;'>" + str(r.get("company_name","")) + "</td>"
                + "<td style='padding:8px 12px;font-size:12px;'>" + str(founder.get("founder_name","")) + "</td>"
                + "<td style='padding:8px 12px;font-size:12px;color:#666;'>" + str(founder.get("founder_title","")) + "</td>"
                + "<td style='padding:8px 12px;font-size:12px;'>" + str(r.get("vertical","")) + "</td>"
                + "<td style='padding:8px 12px;font-size:12px;font-weight:bold;'>" + str(round(r.get("score_pct",0),1)) + "%</td>"
                + "<td style='padding:8px 12px;font-size:12px;'>" + li_btn + "</td>"
                + "</tr>"
            )
        outreach_section = (
            "<div style='margin-bottom:20px;'>"
            "<h2 style='color:#1b3a6b;font-size:15px;border-bottom:2px solid #c55a11;padding-bottom:5px;margin-bottom:8px;'>"
            "Founder Outreach List</h2>"
            "<p style='font-size:11px;color:#888;margin-bottom:10px;'>High-scoring companies with identified founders.</p>"
            "<table style='width:100%;border-collapse:collapse;background:white;border-radius:6px;overflow:hidden;'>"
            "<tr style='background:#1b3a6b;'>"
            "<th style='padding:8px 12px;color:white;font-size:11px;text-align:left;'>Company</th>"
            "<th style='padding:8px 12px;color:white;font-size:11px;text-align:left;'>Founder</th>"
            "<th style='padding:8px 12px;color:white;font-size:11px;text-align:left;'>Title</th>"
            "<th style='padding:8px 12px;color:white;font-size:11px;text-align:left;'>Vertical</th>"
            "<th style='padding:8px 12px;color:white;font-size:11px;'>Score</th>"
            "<th style='padding:8px 12px;color:white;font-size:11px;text-align:left;'>LinkedIn</th>"
            "</tr>"
            + "".join(orows)
            + "</table></div>"
        )
    else:
        outreach_section = ""

    # ── Assemble full HTML ─────────────────────────────────────────────────────
    html = (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
        "<body style='font-family:Arial,sans-serif;max-width:840px;margin:0 auto;background:#f4f6f9;padding:20px;'>"

        # Header
        "<div style='background:#1b3a6b;border-radius:10px 10px 0 0;padding:22px 26px;'>"
        "<div style='color:white;font-size:20px;font-weight:bold;'>Second Layer VC Pipeline</div>"
        "<div style='color:#aac4e8;font-size:12px;margin-top:3px;'>" + date_str + " - Daily Digest</div>"
        "<div style='color:#d6e4f7;font-size:11px;margin-top:6px;'>Sources: " + src_summary + "</div>"
        "</div>"

        # Stats bar
        "<div style='background:#2e75b6;padding:12px 26px;display:flex;gap:24px;margin-bottom:18px;'>"
        "<div style='text-align:center;'><div style='color:white;font-size:22px;font-weight:bold;'>" + str(n_total) + "</div><div style='color:#aac4e8;font-size:10px;'>TODAY</div></div>"
        "<div style='text-align:center;'><div style='color:#c6efce;font-size:22px;font-weight:bold;'>" + str(n_pass) + "</div><div style='color:#aac4e8;font-size:10px;'>PASSING</div></div>"
        "<div style='text-align:center;'><div style='color:#ffc7ce;font-size:22px;font-weight:bold;'>" + str(n_total - n_pass) + "</div><div style='color:#aac4e8;font-size:10px;'>FILTERED</div></div>"
        "<div style='text-align:center;'><div style='color:#ffeb9c;font-size:22px;font-weight:bold;'>" + str(round(pass_rate)) + "%</div><div style='color:#aac4e8;font-size:10px;'>PASS RATE</div></div>"
        "<div style='text-align:center;border-left:1px solid #5a9fd4;padding-left:24px;'><div style='color:white;font-size:22px;font-weight:bold;'>" + str(total_seen) + "</div><div style='color:#aac4e8;font-size:10px;'>TOTAL PIPELINE</div></div>"
        "</div>"

        # Cards
        "<div style='margin-bottom:22px;'>"
        "<h2 style='color:#1b3a6b;font-size:15px;margin-bottom:12px;border-bottom:2px solid #2e75b6;padding-bottom:5px;'>"
        "Meeting Threshold (>=" + str(int(MIN_SCORE_PCT)) + "%)</h2>"
        + cards +
        "</div>"

        + below_section
        + outreach_section

        # Footer
        + "<div style='text-align:center;color:#aaa;font-size:10px;margin-top:16px;padding-top:14px;border-top:1px solid #ddd;'>"
        "Bryan Hanley - Second Layer VC Framework - Never repeats a company"
        "</div>"
        "</body></html>"
    )

    return subject, html


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_SENDER, EMAIL_PASSWORD)
        s.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
    print(f"✅ Email sent: {subject}")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    today    = datetime.date.today()
    date_str = today.strftime("%A, %B %d %Y")
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
            # Research founder for high-scoring companies only (saves API cost)
            if result.get("score_pct", 0) >= 75:
                result["founder"] = research_founder(result)
                time.sleep(1)
            else:
                result["founder"] = {}
            results.append(result)
            print(f"  → {result.get('score_pct',0):.1f}% | {result.get('decision','')}")
        else:
            stage_gated += 1  # score_company returns None for stage gate fails too
        time.sleep(10)  # 10s delay keeps Haiku under 50k token/min rate limit
    print(f"Stage gated (removed): {stage_gated}")

    append_results_to_sheet(results, date_str)
    total_seen = len(previously_seen) + len(results)

    print(f"\nBuilding digest for {len(results)} scored companies...")
    subject, html = build_email(results, date_str, total_seen)
    send_email(subject, html)
    print("Done.")


if __name__ == "__main__":
    main()
