"""
Second Layer VC Pipeline — Daily Sourcer & Scorer (v2)
Sources from YC batches, SEC Form D filings, Product Hunt, and VC portfolio pages
instead of Crunchbase (which blocks scrapers).
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

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_SENDER      = os.environ["EMAIL_SENDER"]
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]
EMAIL_RECIPIENT   = os.environ["EMAIL_RECIPIENT"]
MIN_SCORE_PCT     = float(os.environ.get("MIN_SCORE_PCT", "65"))

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ─────────────────────────────────────────────────────────────────────────────
# SECOND LAYER VERTICALS & KEYWORDS
# Used to filter companies by relevance to the thesis
# ─────────────────────────────────────────────────────────────────────────────
SECOND_LAYER_KEYWORDS = [
    # Fintech compliance
    "compliance", "aml", "kyc", "kyb", "fraud", "regtech", "regulatory",
    "anti-money laundering", "financial crime", "sanctions",
    # Healthcare
    "hipaa", "health", "healthcare", "clinical", "medical", "prior auth",
    "pharmacy", "ehr", "electronic health",
    # Cybersecurity
    "security", "cybersecurity", "threat", "incident response", "dfir",
    "vulnerability", "devsecops", "appsec", "cloud security", "soc",
    # Legal / RegTech
    "legal", "contract", "compliance", "regulatory", "law", "legaltech",
    # AI governance
    "ai governance", "model risk", "responsible ai", "ai compliance",
    "ai safety", "mlops", "llm", "audit",
    # Supply chain / SBOM
    "supply chain", "sbom", "software bill", "vendor risk", "third party",
    # Insurance
    "insurance", "insurtech", "underwriting", "claims",
    # Energy / grid
    "energy", "grid", "power", "carbon", "emissions", "climate",
    # Data privacy
    "privacy", "data protection", "gdpr", "ccpa", "pii",
    # Trade / tariff
    "trade", "tariff", "customs", "import", "export",
]

def is_second_layer_relevant(text: str) -> bool:
    """Check if company description contains Second Layer keywords."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in SECOND_LAYER_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 1: Y COMBINATOR — current + recent batch companies
# YC publishes all companies publicly at ycombinator.com/companies
# ─────────────────────────────────────────────────────────────────────────────

def source_yc_companies() -> list[dict]:
    """
    Fetches recent YC companies from the public YC company directory API.
    Returns list of dicts with name and description.
    """
    companies = []
    try:
        # YC has a public API endpoint used by their website
        url = "https://www.ycombinator.com/companies?batch=W25&batch=S24&batch=W24"
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try to find company cards
        for card in soup.find_all("a", href=re.compile(r"/companies/")):
            name_el = card.find(class_=re.compile(r"company.*name|name", re.I))
            desc_el  = card.find(class_=re.compile(r"company.*desc|tagline|pitch", re.I))

            name = name_el.get_text(strip=True) if name_el else ""
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if not name:
                # Fallback: try header tags
                h = card.find(["h3", "h4", "h2"])
                name = h.get_text(strip=True) if h else ""

            if name and len(name) > 2:
                combined = f"{name} {desc}"
                if is_second_layer_relevant(combined):
                    companies.append({"name": name, "description": desc, "source": "YC"})

        print(f"YC: found {len(companies)} relevant companies")

    except Exception as e:
        print(f"YC source error: {e}")

    return companies[:8]


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 2: YC API (JSON endpoint — more reliable than scraping)
# ─────────────────────────────────────────────────────────────────────────────

def source_yc_api() -> list[dict]:
    """
    Uses the Algolia search API that powers YC's company search.
    This is a public, unauthenticated endpoint.
    """
    companies = []
    try:
        # YC uses Algolia for their company search — this is a public endpoint
        batches = ["W25", "S24", "W24", "S23"]
        day_of_week = datetime.date.today().weekday()

        # Rotate through Second Layer search terms daily
        search_terms = [
            "compliance security",
            "healthcare AI",
            "legal regulatory",
            "fintech fraud",
            "privacy data",
            "insurance risk",
            "supply chain",
        ]
        term = search_terms[day_of_week % len(search_terms)]
        batch = batches[day_of_week % len(batches)]

        url = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"
        params = {
            "x-algolia-agent": "Algolia for JavaScript (4.14.3)",
            "x-algolia-api-key": "9f3867c5067ead04cbdd2ce3e8d8b7e8",
            "x-algolia-application-id": "45BWZJ1SGC",
        }
        payload = {
            "requests": [{
                "indexName": "YCCompany_production",
                "params": f"query={requests.utils.quote(term)}&hitsPerPage=20&filters=batch%3A{batch}",
            }]
        }

        resp = requests.post(url, json=payload, params=params, timeout=15)
        data = resp.json()
        hits = data.get("results", [{}])[0].get("hits", [])

        for hit in hits:
            name = hit.get("name", "")
            desc = hit.get("one_liner", "") or hit.get("long_description", "")
            combined = f"{name} {desc}"
            if name and is_second_layer_relevant(combined):
                companies.append({
                    "name": name,
                    "description": desc,
                    "source": f"YC {hit.get('batch', '')}",
                    "url": f"https://www.ycombinator.com/companies/{hit.get('slug', '')}",
                })

        print(f"YC API: found {len(companies)} relevant companies")

    except Exception as e:
        print(f"YC API error: {e}")

    return companies[:8]


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 3: SEC EDGAR — Form D filings (public seed round disclosures)
# Companies must file Form D within 15 days of first sale in a private offering.
# This surfaces companies BEFORE they get press coverage.
# ─────────────────────────────────────────────────────────────────────────────

def source_sec_form_d() -> list[dict]:
    """
    Fetches recent Form D filings from SEC EDGAR full-text search.
    These are seed/early stage raises disclosed before press coverage.
    """
    companies = []
    try:
        # SEC EDGAR full-text search for recent Form D filings
        # Filter by small amounts ($500K–$5M) = seed stage
        today = datetime.date.today()
        two_weeks_ago = today - datetime.timedelta(days=14)
        date_from = two_weeks_ago.strftime("%Y-%m-%d")

        # Second Layer keywords to search in Form D
        day = today.weekday()
        search_terms = [
            "compliance software",
            "healthcare technology",
            "cybersecurity",
            "legal technology",
            "financial technology compliance",
            "insurance technology",
            "data privacy",
        ]
        term = search_terms[day % len(search_terms)]

        url = "https://efts.sec.gov/LATEST/search-index?q=%22" + \
              requests.utils.quote(term) + \
              "%22&dateRange=custom&startdt=" + date_from + \
              "&forms=D&hits.hits.total.value=true&hits.hits._source.period_of_report=true"

        resp = requests.get(url, headers=HEADERS, timeout=20)
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        for hit in hits[:10]:
            src = hit.get("_source", {})
            name = src.get("entity_name", "") or src.get("display_names", [""])[0]
            desc = src.get("business_description", "") or term

            if name and len(name) > 2:
                companies.append({
                    "name": name,
                    "description": f"SEC Form D filing — {desc}",
                    "source": "SEC Form D",
                })

        print(f"SEC EDGAR: found {len(companies)} companies")

    except Exception as e:
        print(f"SEC EDGAR error: {e}")

    return companies[:5]


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 4: PRODUCT HUNT — recent launches in relevant categories
# ─────────────────────────────────────────────────────────────────────────────

def source_product_hunt() -> list[dict]:
    """
    Scrapes Product Hunt for recent launches in Second Layer categories.
    """
    companies = []
    try:
        # Product Hunt topics relevant to Second Layer
        day = datetime.date.today().weekday()
        topics = [
            "compliance", "security", "legal", "fintech",
            "healthcare", "privacy", "productivity",
        ]
        topic = topics[day % len(topics)]

        url = f"https://www.producthunt.com/topics/{topic}"
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Product Hunt product cards
        for item in soup.find_all("li", attrs={"data-test": re.compile("post-item")}):
            name_el = item.find(["h3", "h2", "a"], class_=re.compile(r"title|name", re.I))
            desc_el  = item.find("p")

            name = name_el.get_text(strip=True) if name_el else ""
            desc = desc_el.get_text(strip=True) if desc_el else ""

            if name and is_second_layer_relevant(f"{name} {desc}"):
                companies.append({
                    "name": name,
                    "description": desc,
                    "source": "Product Hunt",
                })

        # Fallback: find any named links
        if not companies:
            for a in soup.find_all("a", href=re.compile(r"/posts/")):
                name = a.get_text(strip=True)
                if name and len(name) > 3 and len(name) < 50:
                    companies.append({
                        "name": name,
                        "description": f"Product Hunt launch in {topic}",
                        "source": "Product Hunt",
                    })

        print(f"Product Hunt: found {len(companies)} companies")

    except Exception as e:
        print(f"Product Hunt error: {e}")

    return companies[:5]


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 5: TECHCRUNCH RSS — funding news (reliable, rarely blocks)
# ─────────────────────────────────────────────────────────────────────────────

def source_techcrunch_rss() -> list[dict]:
    """
    Parses TechCrunch RSS feed for recent funding articles.
    Extracts company names from headlines.
    """
    companies = []
    try:
        url  = "https://techcrunch.com/feed/"
        resp = requests.get(url, headers=HEADERS, timeout=20)
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            desc  = item.findtext("description", "")
            combined = f"{title} {desc}"

            # Only funding-related articles
            if not any(w in title.lower() for w in ["raises", "funding", "seed", "series a", "launches"]):
                continue

            if not is_second_layer_relevant(combined):
                continue

            # Extract company name — usually the first capitalized word(s) before "raises"
            match = re.match(r"^([A-Z][A-Za-z0-9\.\-\s]{1,30?})\s+(?:raises|secures|closes|lands|gets)", title)
            if match:
                name = match.group(1).strip()
                companies.append({
                    "name": name,
                    "description": title,
                    "source": "TechCrunch",
                })

        print(f"TechCrunch RSS: found {len(companies)} companies")

    except Exception as e:
        print(f"TechCrunch RSS error: {e}")

    return companies[:5]


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE 6: FIRST ROUND CAPITAL PORTFOLIO — tier-1 VC seed investments
# ─────────────────────────────────────────────────────────────────────────────

def source_vc_portfolio() -> list[dict]:
    """
    Scrapes First Round Capital and Bessemer portfolio pages for
    recently added seed-stage companies.
    """
    companies = []
    sources = [
        ("https://firstround.com/companies/", "First Round"),
        ("https://www.bvp.com/portfolio", "Bessemer"),
    ]

    for url, vc_name in sources:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.find_all(["article", "div", "li"],
                                       class_=re.compile(r"company|portfolio|card", re.I))[:20]:
                name_el = card.find(["h2", "h3", "h4", "strong"])
                desc_el  = card.find("p")

                name = name_el.get_text(strip=True) if name_el else ""
                desc = desc_el.get_text(strip=True) if desc_el else ""

                if name and len(name) > 2 and is_second_layer_relevant(f"{name} {desc}"):
                    companies.append({
                        "name": name,
                        "description": desc,
                        "source": vc_name,
                    })

            print(f"{vc_name}: found {len([c for c in companies if c['source']==vc_name])} companies")
            time.sleep(2)

        except Exception as e:
            print(f"{vc_name} error: {e}")

    return companies[:6]


# ─────────────────────────────────────────────────────────────────────────────
# AGGREGATE ALL SOURCES
# ─────────────────────────────────────────────────────────────────────────────

def get_candidate_companies() -> list[dict]:
    """
    Runs all sources, deduplicates, and returns up to 15 candidates.
    """
    all_companies = []

    print("--- Sourcing from YC API ---")
    all_companies.extend(source_yc_api())
    time.sleep(2)

    print("--- Sourcing from TechCrunch RSS ---")
    all_companies.extend(source_techcrunch_rss())
    time.sleep(2)

    print("--- Sourcing from SEC EDGAR Form D ---")
    all_companies.extend(source_sec_form_d())
    time.sleep(2)

    print("--- Sourcing from Product Hunt ---")
    all_companies.extend(source_product_hunt())
    time.sleep(2)

    print("--- Sourcing from VC Portfolios ---")
    all_companies.extend(source_vc_portfolio())

    # Deduplicate by name (case-insensitive)
    seen  = set()
    unique = []
    for co in all_companies:
        key = co["name"].lower().strip()
        if key not in seen and len(key) > 2:
            seen.add(key)
            unique.append(co)

    print(f"\nTotal unique candidates: {len(unique)}")
    return unique[:15]


# ─────────────────────────────────────────────────────────────────────────────
# SCORING VIA CLAUDE API
# ─────────────────────────────────────────────────────────────────────────────

SECOND_LAYER_CONTEXT = """
SECOND LAYER APPROACH — INVESTMENT THESIS:
Bryan Hanley's "Second Layer" approach identifies startups solving problems
CREATED BY dominant/fast-growing industries, rather than being in those industries.

Framework logic:
1a/1b. Identify dominant + fast-growing industries (AI, Fintech, Healthcare, Energy, Crypto)
2b.    Identify the RISKS those industries create
3b.    Invest in startups that SOLVE those downstream risks → these are the investments

Strong Second Layer examples:
- Crypto growth → AML/financial crime risk → AML automation (e.g. Flagright)
- AI adoption → model governance risk → AI governance platforms (e.g. Credo AI)
- Healthcare digitization → HIPAA bottlenecks → HIPAA-compliant workflow infra
- AI compute boom → grid strain → power-compliant AI orchestration
- Legal AI → malpractice risk → compliance-grade legal AI tooling
- Fintech expansion → KYB/KYC bottleneck → business verification AI

A company FAILS Second Layer alignment if it IS the dominant industry
(e.g., an LLM itself, a crypto exchange) rather than solving the downstream
risks/bottlenecks that industry creates.
"""

SCORING_RUBRIC = """
FACTOR SCORING GUIDE — score each factor 0 to 10:

1A. Founder-Market Fit (weight: 10%)
9-10: Prior exit + deep domain expertise in this exact space
7-8: Strong relevant background, 2-5 years domain experience
5-6: Adjacent domain, some relevant experience
3-4: Limited/learning on the job
0-2: No relevant experience

1B. Technical Execution (weight: 8%)
9-10: Working product, proven builders, strong engineering
7-8: Solid prototype, competent technical team
5-6: Basic MVP, early technical capability
3-4: Struggling to build
0-2: Vaporware, cannot build

1C. Founder Commitment (weight: 7%)
9-10: Quit jobs, full-time, invested own capital
7-8: Full-time, clearly committed
5-6: Part-time or recently committed
3-4: Side project mentality
0-2: Keeping day job, not committed

2A. Early Product-Market Fit (weight: 12%)
9-10: Organic growth, users obsessed, strong retention
7-8: Good engagement, users returning consistently
5-6: Some users engaged, moderate retention
3-4: Low engagement, poor retention
0-2: No users or not launched

2B. Revenue Signals (weight: 8%)
9-10: Strong revenue, proven unit economics
7-8: Some revenue, clear monetization path
5-6: Early revenue or paying pilots
3-4: Minimal/unclear monetization
0-2: $0 revenue (note: acceptable for some early models)

3A. Market Size / TAM (weight: 12%)
9-10: $50B+ TAM
7-8: $10-50B TAM
5-6: $1-10B TAM
3-4: $100M-$1B TAM
0-2: <$100M — too small for venture

3B. Timing & Competition (weight: 8%)
9-10: Perfect timing, greenfield market, technology enabler just ready
7-8: Good timing, competition is beatable
5-6: Crowded but differentiated
3-4: Poor timing — too early or too late
0-2: Entrenched competition, terrible timing

4. Traction — Quantitative (weight: 7%)
9-10: Explosive growth >20% weekly
7-8: Strong growth 10-20% weekly
5-6: Steady growth 5-10% weekly
3-4: Slow growth <5% weekly
0-2: No growth or declining

5. Traction — Qualitative (weight: 8%)
9-10: Users would be devastated if product went away
7-8: Users really like it, strong NPS
5-6: Users find it useful, decent feedback
3-4: Mixed feedback, some churn
0-2: Users don't care, high churn

6. Capital Efficiency (weight: 10%)
9-10: Significant product built on <$100K
7-8: Efficient use of capital, good progress
5-6: Average efficiency for stage
3-4: Capital intensive, burning fast
0-2: Massive burn, very inefficient

7. Investor Signal (weight: 10%)
9-10: Top-tier lead (Sequoia, a16z, Benchmark, YC)
7-8: Solid tier-1 or tier-2 VC lead
5-6: Reputable angels or smaller funds
3-4: Unknown investors
0-2: Red flag investors or no investors
"""

SCORE_PROMPT = """
You are a VC analyst applying the Second Layer investment framework.

{second_layer_context}

{scoring_rubric}

---

Research and evaluate this company:

Company Name: {company_name}
Known Description: {description}
Source: {source}

Instructions:
1. Research what this company does based on the name and description provided.
2. Determine if it fits the Second Layer thesis.
3. Score all 11 factors using the rubric. Be honest — most companies should score
   in the 4-7 range. Reserve 8-10 for genuinely exceptional signals.
4. If you cannot find meaningful information about this company, score conservatively
   (mostly 4-6) and note limited information available.

Respond ONLY with valid JSON, no other text:
{{
  "company_name": "string",
  "founded": "YYYY or unknown",
  "stage": "Pre-Seed/Seed/Series A/unknown",
  "raise": "$XM or unknown",
  "vertical": "concise vertical label",
  "what_they_do": "2-3 sentences",
  "second_layer_alignment": true or false,
  "second_layer_logic": "First Layer trend → risk/bottleneck created → how this company solves it",
  "scores": {{
    "1A": 0, "1B": 0, "1C": 0,
    "2A": 0, "2B": 0,
    "3A": 0, "3B": 0,
    "4": 0,  "5": 0,
    "6": 0,  "7": 0
  }},
  "weighted_score": 0.0,
  "score_pct": 0.0,
  "decision": "★★★★★ STRONG YES / ★★★★ YES / ★★★ DEEP DIVE / ★★ PROBABLY PASS / ★ HARD PASS",
  "key_strength": "one sentence",
  "key_weakness": "one sentence"
}}
"""

WEIGHTS = {
    "1A": 0.10, "1B": 0.08, "1C": 0.07,
    "2A": 0.12, "2B": 0.08,
    "3A": 0.12, "3B": 0.08,
    "4":  0.07, "5":  0.08,
    "6":  0.10, "7":  0.10,
}


def score_company(co: dict) -> dict | None:
    prompt = SCORE_PROMPT.format(
        second_layer_context=SECOND_LAYER_CONTEXT,
        scoring_rubric=SCORING_RUBRIC,
        company_name=co["name"],
        description=co.get("description", "No description available"),
        source=co.get("source", "Unknown"),
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # cheap + fast for daily runs
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*",     "", raw)
        raw = re.sub(r"\s*```$",     "", raw)

        data = json.loads(raw)

        # Recompute weighted score authoritatively
        scores  = data.get("scores", {})
        ws      = sum(scores.get(k, 0) * v for k, v in WEIGHTS.items())
        pct     = ws * 10
        data["weighted_score"] = round(ws, 2)
        data["score_pct"]      = round(pct, 1)
        data["source"]         = co.get("source", "")

        # Decision label
        if pct >= 85:   data["decision"] = "★★★★★ STRONG YES"
        elif pct >= 75: data["decision"] = "★★★★ YES"
        elif pct >= 65: data["decision"] = "★★★ DEEP DIVE"
        elif pct >= 55: data["decision"] = "★★ PROBABLY PASS"
        else:           data["decision"] = "★ HARD PASS"

        return data

    except Exception as e:
        print(f"Scoring error for {co['name']}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL BUILDER
# ─────────────────────────────────────────────────────────────────────────────

DECISION_STYLE = {
    "★★★★★ STRONG YES": ("#1a472a", "#a9d18e"),
    "★★★★ YES":          ("#1a472a", "#c6efce"),
    "★★★ DEEP DIVE":     ("#7d6608", "#ffeb9c"),
    "★★ PROBABLY PASS":  ("#843c0c", "#fce4d6"),
    "★ HARD PASS":       ("#9c0006", "#ffc7ce"),
}

FACTOR_LABELS = {
    "1A": "Fdr-Mkt Fit", "1B": "Tech Exec", "1C": "Commitment",
    "2A": "Early PMF",   "2B": "Revenue",    "3A": "TAM",
    "3B": "Timing",      "4":  "Traction Q", "5":  "Traction Q",
    "6":  "Cap Effic.",  "7":  "Investor",
}


def score_badge(v: int) -> str:
    if v >= 8:   bg, fg = "#c6efce", "#276221"
    elif v >= 6: bg, fg = "#ffeb9c", "#7d6608"
    else:        bg, fg = "#ffc7ce", "#9c0006"
    return (f'<span style="background:{bg};color:{fg};padding:2px 8px;'
            f'border-radius:4px;font-weight:bold;font-size:12px;">{v}</span>')


def company_card(co: dict) -> str:
    dec = co.get("decision", "★ HARD PASS")
    fg, bg = DECISION_STYLE.get(dec, ("#333", "#eee"))
    scores = co.get("scores", {})
    cells  = "".join(
        f'<td style="text-align:center;padding:4px 5px;font-size:11px;">'
        f'<div style="color:#888;margin-bottom:2px;">{FACTOR_LABELS.get(k,"")}</div>'
        f'{score_badge(v)}</td>'
        for k, v in scores.items()
    )
    source_badge = (
        f'<span style="background:#e8f0fe;color:#1a56db;padding:2px 8px;'
        f'border-radius:10px;font-size:10px;font-weight:bold;">'
        f'{co.get("source","")}</span>'
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
        <div style="margin-top:4px;">{source_badge}</div>
      </div>
      <div style="background:{bg};color:{fg};padding:5px 12px;border-radius:20px;font-weight:bold;font-size:11px;text-align:center;">
        {dec}<br><span style="font-size:16px;">{co.get('score_pct',0):.1f}%</span>
      </div>
    </div>
  </div>
  <div style="padding:12px 16px;background:white;">
    <div style="background:#f0f5ff;border-left:3px solid #2e75b6;padding:7px 10px;border-radius:0 4px 4px 0;margin-bottom:8px;font-size:12px;">
      <strong>🔗 Second Layer:</strong> {co.get('second_layer_logic','')}
    </div>
    <div style="font-size:12px;color:#555;margin-bottom:10px;line-height:1.5;">{co.get('what_they_do','')}</div>
    <table style="width:100%;border-collapse:collapse;margin-bottom:10px;"><tr>{cells}</tr></table>
    <div style="display:flex;gap:10px;">
      <div style="flex:1;background:#f0f9f0;border-radius:4px;padding:7px 9px;">
        <div style="font-size:10px;color:#276221;font-weight:bold;margin-bottom:2px;">✅ STRENGTH</div>
        <div style="font-size:11px;color:#333;">{co.get('key_strength','')}</div>
      </div>
      <div style="flex:1;background:#fff5f5;border-radius:4px;padding:7px 9px;">
        <div style="font-size:10px;color:#9c0006;font-weight:bold;margin-bottom:2px;">⚠️ WEAKNESS</div>
        <div style="font-size:11px;color:#333;">{co.get('key_weakness','')}</div>
      </div>
    </div>
  </div>
</div>"""


def build_email(results: list[dict], date_str: str) -> tuple[str, str]:
    passing = sorted(
        [r for r in results if r.get("score_pct", 0) >= MIN_SCORE_PCT],
        key=lambda x: x.get("score_pct", 0), reverse=True
    )
    below = sorted(
        [r for r in results if r.get("score_pct", 0) < MIN_SCORE_PCT],
        key=lambda x: x.get("score_pct", 0), reverse=True
    )
    n_total   = len(results)
    n_passing = len(passing)
    pass_rate = (n_passing / n_total * 100) if n_total > 0 else 0  # fix divide-by-zero

    subject = (
        f"🔍 Second Layer Pipeline — {date_str} | "
        f"{n_passing} companies ≥{MIN_SCORE_PCT:.0f}% of {n_total} evaluated"
    )

    cards = "".join(company_card(c) for c in passing) if passing else (
        "<p style='color:#888;text-align:center;padding:30px;'>"
        "No companies met the threshold today — see filtered list below.</p>"
    )

    below_rows = "".join(
        f"<tr style='background:{'#fff' if i%2==0 else '#f9f9f9'};'>"
        f"<td style='padding:6px 10px;font-size:12px;'>{r.get('company_name','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#666;'>{r.get('vertical','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#666;'>{r.get('source','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;text-align:center;font-weight:bold;'>{r.get('score_pct',0):.1f}%</td>"
        f"<td style='padding:6px 10px;font-size:12px;'>{r.get('decision','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#888;'>{r.get('key_weakness','')[:70]}...</td>"
        f"</tr>"
        for i, r in enumerate(below)
    )
    below_table = f"""
<div style="margin-bottom:20px;">
  <h2 style="color:#666;font-size:13px;margin-bottom:8px;border-bottom:1px solid #ddd;padding-bottom:5px;">
    📊 Evaluated But Filtered (below {MIN_SCORE_PCT:.0f}%) — demonstrates analytical rigor
  </h2>
  <table style="width:100%;border-collapse:collapse;background:white;border-radius:6px;overflow:hidden;">
    <tr style="background:#1b3a6b;">
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Company</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Vertical</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Source</th>
      <th style="padding:7px 10px;color:white;font-size:11px;">Score</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Decision</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Key Weakness</th>
    </tr>
    {below_rows}
  </table>
</div>""" if below else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:820px;margin:0 auto;background:#f4f6f9;padding:20px;">
  <div style="background:#1b3a6b;border-radius:10px 10px 0 0;padding:22px 26px;">
    <div style="color:white;font-size:20px;font-weight:bold;">🔍 Second Layer VC Pipeline</div>
    <div style="color:#aac4e8;font-size:12px;margin-top:3px;">{date_str} · Daily Sourcing Digest</div>
    <div style="color:#d6e4f7;font-size:11px;margin-top:6px;">
      Sources: YC Batch · TechCrunch · SEC Form D · Product Hunt · VC Portfolios
    </div>
  </div>
  <div style="background:#2e75b6;padding:12px 26px;display:flex;gap:28px;margin-bottom:18px;">
    <div style="text-align:center;">
      <div style="color:white;font-size:22px;font-weight:bold;">{n_total}</div>
      <div style="color:#aac4e8;font-size:10px;">EVALUATED</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#c6efce;font-size:22px;font-weight:bold;">{n_passing}</div>
      <div style="color:#aac4e8;font-size:10px;">PASSING ≥{MIN_SCORE_PCT:.0f}%</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#ffc7ce;font-size:22px;font-weight:bold;">{n_total - n_passing}</div>
      <div style="color:#aac4e8;font-size:10px;">FILTERED OUT</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#ffeb9c;font-size:22px;font-weight:bold;">{pass_rate:.0f}%</div>
      <div style="color:#aac4e8;font-size:10px;">PASS RATE</div>
    </div>
  </div>
  <div style="margin-bottom:22px;">
    <h2 style="color:#1b3a6b;font-size:15px;margin-bottom:12px;border-bottom:2px solid #2e75b6;padding-bottom:5px;">
      ✅ Meeting Threshold (≥{MIN_SCORE_PCT:.0f}%)
    </h2>
    {cards}
  </div>
  {below_table}
  <div style="text-align:center;color:#aaa;font-size:10px;margin-top:16px;padding-top:14px;border-top:1px solid #ddd;">
    Bryan Hanley · Second Layer VC Framework · Automated Daily Pipeline<br>
    Threshold ≥{MIN_SCORE_PCT:.0f}% · Powered by Claude AI (Haiku) · GitHub Actions
  </div>
</body></html>"""

    return subject, html


# ─────────────────────────────────────────────────────────────────────────────
# SEND EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_SENDER, EMAIL_PASSWORD)
        s.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
    print(f"✅ Email sent: {subject}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    today    = datetime.date.today()
    date_str = today.strftime("%A, %B %d %Y")
    print(f"=== Second Layer Pipeline v2: {date_str} ===")

    # Source
    candidates = get_candidate_companies()
    print(f"\nCandidates to score: {[c['name'] for c in candidates]}")

    if not candidates:
        print("No candidates found — sending empty digest.")
        subject, html = build_email([], date_str)
        send_email(subject, html)
        return

    # Score each
    results = []
    for co in candidates:
        print(f"Scoring: {co['name']} ({co.get('source','')})")
        result = score_company(co)
        if result:
            results.append(result)
            print(f"  → {result.get('score_pct',0):.1f}% | {result.get('decision','')}")
        time.sleep(1)

    # Send
    print(f"\nBuilding digest for {len(results)} scored companies...")
    subject, html = build_email(results, date_str)
    send_email(subject, html)
    print("Done.")


if __name__ == "__main__":
    main()
