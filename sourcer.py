"""
Second Layer VC Pipeline — Daily Sourcer & Scorer (v3)
8 diverse sourcing channels + deduplication against Google Sheet history.
Never scores the same company twice.
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

SECOND_LAYER_KEYWORDS = [
    "compliance", "aml", "kyc", "kyb", "fraud", "regtech", "regulatory",
    "anti-money laundering", "financial crime", "sanctions", "fintech",
    "hipaa", "health", "healthcare", "clinical", "medical", "prior auth",
    "pharmacy", "ehr", "electronic health",
    "security", "cybersecurity", "threat", "incident response", "dfir",
    "vulnerability", "devsecops", "appsec", "cloud security", "soc",
    "legal", "contract", "law", "legaltech",
    "ai governance", "model risk", "responsible ai", "ai compliance",
    "ai safety", "mlops", "llm", "audit", "governance",
    "supply chain", "sbom", "software bill", "vendor risk", "third party",
    "insurance", "insurtech", "underwriting", "claims",
    "energy", "grid", "power", "carbon", "emissions", "climate",
    "privacy", "data protection", "gdpr", "ccpa", "pii",
    "trade", "tariff", "customs", "import", "export",
    "risk", "monitoring", "detection", "verification", "identity",
]

def is_relevant(text):
    t = text.lower()
    return any(kw in t for kw in SECOND_LAYER_KEYWORDS)


# ── SOURCE 1: YC ALGOLIA API ──────────────────────────────────────────────────
def source_yc():
    companies = []
    try:
        day = datetime.date.today().weekday()
        all_terms = [
            "compliance security", "fraud detection",
            "healthcare AI workflow", "clinical automation",
            "legal regulatory technology", "contract compliance",
            "fintech risk monitoring", "identity verification",
            "data privacy protection", "AI governance audit",
            "supply chain risk", "insurance underwriting",
            "energy grid optimization", "cybersecurity threat",
        ]
        terms   = [all_terms[(day * 2) % len(all_terms)], all_terms[(day * 2 + 1) % len(all_terms)]]
        batches = ["W25", "S24", "W24", "S23", "W23", "S22"]

        for term in terms:
            for batch in batches:
                url    = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"
                params = {
                    "x-algolia-agent": "Algolia for JavaScript (4.14.3)",
                    "x-algolia-api-key": "9f3867c5067ead04cbdd2ce3e8d8b7e8",
                    "x-algolia-application-id": "45BWZJ1SGC",
                }
                payload = {"requests": [{"indexName": "YCCompany_production",
                    "params": f"query={requests.utils.quote(term)}&hitsPerPage=10&filters=batch%3A{batch}"}]}
                resp = requests.post(url, json=payload, params=params, timeout=15)
                hits = resp.json().get("results", [{}])[0].get("hits", [])
                for hit in hits:
                    name = hit.get("name", "")
                    desc = hit.get("one_liner", "") or hit.get("long_description", "")
                    if name and is_relevant(f"{name} {desc}"):
                        companies.append({"name": name, "description": desc,
                            "source": f"YC {hit.get('batch','')}"})
                time.sleep(0.5)
        print(f"YC: {len(companies)} candidates")
    except Exception as e:
        print(f"YC error: {e}")
    return companies[:10]


# ── SOURCE 2: TECHCRUNCH RSS ──────────────────────────────────────────────────
def source_techcrunch():
    companies = []
    try:
        resp = requests.get("https://techcrunch.com/feed/", headers=HEADERS, timeout=20)
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            desc  = item.findtext("description", "")
            if not any(w in title.lower() for w in ["raises","funding","seed","series a","launches","secures"]):
                continue
            if not is_relevant(f"{title} {desc}"):
                continue
            match = re.match(r"^([A-Z][A-Za-z0-9\.\-\s]{1,30?})\s+(?:raises|secures|closes|lands|gets|launches)", title)
            if match:
                companies.append({"name": match.group(1).strip(), "description": title, "source": "TechCrunch"})
        print(f"TechCrunch: {len(companies)} candidates")
    except Exception as e:
        print(f"TechCrunch error: {e}")
    return companies[:5]


# ── SOURCE 3: SEC EDGAR FORM D ────────────────────────────────────────────────
def source_sec_form_d():
    companies = []
    try:
        today     = datetime.date.today()
        date_from = (today - datetime.timedelta(days=14)).strftime("%Y-%m-%d")
        terms     = ["compliance software","healthcare technology","cybersecurity",
                     "legal technology","financial technology","insurance technology","data privacy"]
        term      = terms[today.weekday() % len(terms)]
        url = (f"https://efts.sec.gov/LATEST/search-index?q=%22{requests.utils.quote(term)}%22"
               f"&dateRange=custom&startdt={date_from}&forms=D")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        for hit in resp.json().get("hits", {}).get("hits", [])[:8]:
            src  = hit.get("_source", {})
            name = src.get("entity_name", "") or (src.get("display_names") or [""])[0]
            if name and len(name) > 2:
                companies.append({"name": name, "description": f"SEC Form D — {term}", "source": "SEC Form D"})
        print(f"SEC Form D: {len(companies)} candidates")
    except Exception as e:
        print(f"SEC Form D error: {e}")
    return companies[:5]


# ── SOURCE 4: HACKER NEWS ─────────────────────────────────────────────────────
def source_hacker_news():
    companies = []
    try:
        day     = datetime.date.today().weekday()
        queries = ["compliance automation","security monitoring","healthcare workflow",
                   "legal AI","fraud detection","privacy infrastructure","risk management"]
        query   = queries[day % len(queries)]
        url     = f"https://hn.algolia.com/api/v1/search?query={requests.utils.quote(query)}&tags=show_hn&hitsPerPage=20"
        hits    = requests.get(url, timeout=15).json().get("hits", [])
        for hit in hits:
            title = hit.get("title", "")
            match = re.match(r"Show HN:\s+([^–—\-]+)[–—\-]", title)
            if match:
                name = match.group(1).strip()
                if name and is_relevant(f"{name} {title}"):
                    companies.append({"name": name, "description": title, "source": "Hacker News"})
        print(f"Hacker News: {len(companies)} candidates")
    except Exception as e:
        print(f"Hacker News error: {e}")
    return companies[:5]


# ── SOURCE 5: GITHUB TRENDING ─────────────────────────────────────────────────
def source_github_trending():
    companies = []
    try:
        resp = requests.get("https://github.com/trending?since=weekly", headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        for repo in soup.find_all("article", class_="Box-row")[:20]:
            h2   = repo.find("h2")
            if not h2: continue
            full = h2.get_text(strip=True).replace("\n","").replace(" ","")
            desc = (repo.find("p") or type("",(),{"get_text":lambda *a,**k:""})).get_text(strip=True)
            if is_relevant(f"{full} {desc}"):
                name = full.split("/")[-1].replace("-"," ").replace("_"," ").title()
                companies.append({"name": name, "description": desc or f"GitHub: {full}", "source": "GitHub Trending"})
        print(f"GitHub Trending: {len(companies)} candidates")
    except Exception as e:
        print(f"GitHub Trending error: {e}")
    return companies[:4]


# ── SOURCE 6: PRODUCT HUNT ────────────────────────────────────────────────────
def source_product_hunt():
    companies = []
    try:
        day    = datetime.date.today().weekday()
        topics = ["compliance","security","legal","fintech","healthcare","privacy","saas"]
        topic  = topics[day % len(topics)]
        resp   = requests.get(f"https://www.producthunt.com/topics/{topic}", headers=HEADERS, timeout=20)
        soup   = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"/posts/")):
            name = a.get_text(strip=True)
            if name and 3 < len(name) < 60 and is_relevant(name):
                companies.append({"name": name, "description": f"Product Hunt — {topic}", "source": "Product Hunt"})
        print(f"Product Hunt: {len(companies)} candidates")
    except Exception as e:
        print(f"Product Hunt error: {e}")
    return companies[:4]


# ── SOURCE 7: INDIE HACKERS ───────────────────────────────────────────────────
def source_indie_hackers():
    companies = []
    try:
        resp = requests.get(
            "https://www.indiehackers.com/products?revenueVerified=true&sorting=revenue",
            headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.find_all(class_=re.compile(r"product", re.I))[:30]:
            name_el = card.find(["h2","h3","strong","a"])
            desc_el  = card.find("p")
            name = name_el.get_text(strip=True) if name_el else ""
            desc = desc_el.get_text(strip=True) if desc_el else ""
            if name and len(name) > 2 and is_relevant(f"{name} {desc}"):
                companies.append({"name": name, "description": desc or "Indie Hackers product", "source": "Indie Hackers"})
        print(f"Indie Hackers: {len(companies)} candidates")
    except Exception as e:
        print(f"Indie Hackers error: {e}")
    return companies[:4]


# ── SOURCE 8: WELLFOUND ───────────────────────────────────────────────────────
def source_wellfound():
    companies = []
    try:
        day     = datetime.date.today().weekday()
        searches = ["compliance engineer","security engineer","healthcare engineer",
                    "legal tech engineer","fintech engineer","privacy engineer","risk engineer"]
        search  = searches[day % len(searches)]
        resp    = requests.get(
            f"https://wellfound.com/jobs?q={requests.utils.quote(search)}&stage[]=seed&stage[]=pre-seed",
            headers=HEADERS, timeout=20)
        soup    = BeautifulSoup(resp.text, "html.parser")
        seen    = set()
        for card in soup.find_all(class_=re.compile(r"job|company|startup", re.I))[:20]:
            name_el = card.find(["h2","h3","strong"])
            desc_el  = card.find("p")
            name = name_el.get_text(strip=True) if name_el else ""
            desc = desc_el.get_text(strip=True) if desc_el else ""
            if name and len(name) > 2 and name not in seen and is_relevant(f"{name} {desc} {search}"):
                seen.add(name)
                companies.append({"name": name, "description": desc or f"Hiring: {search}", "source": "Wellfound"})
        print(f"Wellfound: {len(companies)} candidates")
    except Exception as e:
        print(f"Wellfound error: {e}")
    return companies[:4]


# ── AGGREGATE + DEDUPLICATE ───────────────────────────────────────────────────
def get_candidate_companies(previously_seen):
    all_companies = []
    print("\n--- Sourcing from 8 channels ---")
    all_companies.extend(source_yc());               time.sleep(2)
    all_companies.extend(source_techcrunch());       time.sleep(2)
    all_companies.extend(source_sec_form_d());       time.sleep(2)
    all_companies.extend(source_hacker_news());      time.sleep(2)
    all_companies.extend(source_github_trending());  time.sleep(2)
    all_companies.extend(source_product_hunt());     time.sleep(2)
    all_companies.extend(source_indie_hackers());    time.sleep(2)
    all_companies.extend(source_wellfound())

    # Dedup within today
    seen_today, unique_today = set(), []
    for co in all_companies:
        key = co["name"].lower().strip()
        if key not in seen_today and len(key) > 2:
            seen_today.add(key); unique_today.append(co)

    # Filter out previously seen
    fresh, skipped = [], []
    for co in unique_today:
        key = co["name"].lower().strip()
        if key in previously_seen: skipped.append(co["name"])
        else: fresh.append(co)

    print(f"\nRaw: {len(all_companies)} | Unique today: {len(unique_today)} | "
          f"Skipped (seen before): {len(skipped)} | Fresh: {len(fresh)}")
    return fresh[:15]


# ── SCORING ───────────────────────────────────────────────────────────────────
SECOND_LAYER_CONTEXT = """
SECOND LAYER APPROACH:
Find startups solving problems CREATED BY dominant industries, not being IN them.
AI adoption → model governance risk → AI governance platforms
Crypto growth → AML risk → AML automation
Healthcare digitization → HIPAA bottlenecks → HIPAA workflow tools
Legal AI → malpractice risk → compliance-grade legal AI
Fintech expansion → KYB/KYC friction → identity verification
FAILS if it IS the dominant industry (an LLM itself, a crypto exchange).
"""

SCORING_RUBRIC = """
Score each factor 0-10. Conservative defaults: most companies 4-7.
1A Founder-Market Fit (10%): 9=prior exit+domain, 7=strong background, 5=adjacent, 3=limited, 0=none
1B Technical Execution (8%): 9=working product, 7=solid prototype, 5=MVP, 3=struggling, 0=vaporware
1C Founder Commitment (7%): 9=quit jobs+invested capital, 7=full-time, 5=recent/part-time, 3=side project
2A Early PMF (12%): 9=users obsessed+organic growth, 7=good engagement, 5=some users, 3=low engagement
2B Revenue Signals (8%): 9=strong revenue, 7=some revenue, 5=paying pilots, 3=minimal, 0=$0
3A TAM (12%): 9=$50B+, 7=$10-50B, 5=$1-10B, 3=$100M-$1B, 0=<$100M
3B Timing/Competition (8%): 9=greenfield perfect timing, 7=good/beatable, 5=crowded differentiated, 3=poor
4 Traction Quantitative (7%): 9=>20%wk, 7=10-20%, 5=5-10%, 3=<5%, 0=none
5 Traction Qualitative (8%): 9=users devastated if gone, 7=strong NPS, 5=useful, 3=mixed, 0=don't care
6 Capital Efficiency (10%): 9=big product on <$100K, 7=efficient, 5=average, 3=capital intensive
7 Investor Signal (10%): 9=Sequoia/a16z/YC, 7=tier-1/2 VC, 5=angels, 3=unknown, 0=red flags
"""

SCORE_PROMPT = """
You are a VC analyst. Apply the Second Layer framework and 11-factor rubric.

{second_layer_context}

{scoring_rubric}

Company: {company_name}
Description: {description}
Source: {source}

Respond ONLY with valid JSON:
{{
  "company_name": "string",
  "founded": "YYYY or unknown",
  "stage": "Pre-Seed/Seed/Series A/unknown",
  "raise": "$XM or unknown",
  "vertical": "concise label",
  "what_they_do": "2-3 sentences",
  "second_layer_alignment": true/false,
  "second_layer_logic": "First Layer trend → risk → how this solves it",
  "scores": {{"1A":0,"1B":0,"1C":0,"2A":0,"2B":0,"3A":0,"3B":0,"4":0,"5":0,"6":0,"7":0}},
  "weighted_score": 0.0,
  "score_pct": 0.0,
  "decision": "★ HARD PASS",
  "key_strength": "one sentence",
  "key_weakness": "one sentence"
}}
"""

WEIGHTS = {"1A":0.10,"1B":0.08,"1C":0.07,"2A":0.12,"2B":0.08,
           "3A":0.12,"3B":0.08,"4":0.07,"5":0.08,"6":0.10,"7":0.10}

def score_company(co):
    prompt = SCORE_PROMPT.format(
        second_layer_context=SECOND_LAYER_CONTEXT,
        scoring_rubric=SCORING_RUBRIC,
        company_name=co["name"],
        description=co.get("description","No description"),
        source=co.get("source","Unknown"),
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1200,
            messages=[{"role":"user","content":prompt}]
        )
        raw  = re.sub(r"^```json\s*|^```\s*|\s*```$", "", resp.content[0].text.strip())
        data = json.loads(raw)
        ws   = sum(data.get("scores",{}).get(k,0)*v for k,v in WEIGHTS.items())
        pct  = ws * 10
        data["weighted_score"] = round(ws,2)
        data["score_pct"]      = round(pct,1)
        data["source"]         = co.get("source","")
        if pct>=85:   data["decision"]="★★★★★ STRONG YES"
        elif pct>=75: data["decision"]="★★★★ YES"
        elif pct>=65: data["decision"]="★★★ DEEP DIVE"
        elif pct>=55: data["decision"]="★★ PROBABLY PASS"
        else:         data["decision"]="★ HARD PASS"
        return data
    except Exception as e:
        print(f"Scoring error {co['name']}: {e}")
        return None


# ── EMAIL ─────────────────────────────────────────────────────────────────────
DECISION_STYLE = {
    "★★★★★ STRONG YES":("#1a472a","#a9d18e"),
    "★★★★ YES":         ("#1a472a","#c6efce"),
    "★★★ DEEP DIVE":    ("#7d6608","#ffeb9c"),
    "★★ PROBABLY PASS": ("#843c0c","#fce4d6"),
    "★ HARD PASS":      ("#9c0006","#ffc7ce"),
}
FACTOR_LABELS = {"1A":"Fdr-Mkt Fit","1B":"Tech Exec","1C":"Commitment",
                 "2A":"Early PMF","2B":"Revenue","3A":"TAM","3B":"Timing",
                 "4":"Traction Q","5":"Traction Ql","6":"Cap Effic.","7":"Investor"}
SOURCE_COLORS = {
    "YC W25":"#FF6600","YC S24":"#FF6600","YC W24":"#FF6600",
    "YC S23":"#FF8833","YC W23":"#FF8833","YC S22":"#FF8833",
    "TechCrunch":"#0D9B4E","SEC Form D":"#1a56db","Hacker News":"#FF4500",
    "GitHub Trending":"#24292e","Product Hunt":"#DA552F",
    "Indie Hackers":"#0e2150","Wellfound":"#3366FF",
}

def score_badge(v):
    if v>=8:   bg,fg="#c6efce","#276221"
    elif v>=6: bg,fg="#ffeb9c","#7d6608"
    else:      bg,fg="#ffc7ce","#9c0006"
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-weight:bold;font-size:12px;">{v}</span>'

def src_badge(source):
    c = SOURCE_COLORS.get(source,"#666")
    return f'<span style="background:{c};color:white;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:bold;">{source}</span>'

def company_card(co):
    dec    = co.get("decision","★ HARD PASS")
    fg, bg = DECISION_STYLE.get(dec,("#333","#eee"))
    scores = co.get("scores",{})
    cells  = "".join(
        f'<td style="text-align:center;padding:4px 5px;font-size:11px;">'
        f'<div style="color:#888;margin-bottom:2px;">{FACTOR_LABELS.get(k,"")}</div>{score_badge(v)}</td>'
        for k,v in scores.items()
    )
    return f"""
<div style="border:1px solid #ddd;border-radius:8px;margin-bottom:18px;overflow:hidden;font-family:Arial,sans-serif;">
  <div style="background:#1b3a6b;padding:12px 16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <span style="color:white;font-size:15px;font-weight:bold;">{co.get('company_name','')}</span>
        <span style="color:#aac4e8;font-size:11px;margin-left:10px;">{co.get('stage','')} · {co.get('raise','')} · Est. {co.get('founded','')}</span>
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

def build_email(results, date_str, total_seen):
    passing   = sorted([r for r in results if r.get("score_pct",0)>=MIN_SCORE_PCT], key=lambda x:x.get("score_pct",0), reverse=True)
    below     = sorted([r for r in results if r.get("score_pct",0)<MIN_SCORE_PCT],  key=lambda x:x.get("score_pct",0), reverse=True)
    n_total   = len(results)
    n_passing = len(passing)
    pass_rate = (n_passing/n_total*100) if n_total>0 else 0

    sc = {}
    for r in results: sc[r.get("source","?")] = sc.get(r.get("source","?"),0)+1
    src_summary = " · ".join(f"{s}:{c}" for s,c in sorted(sc.items()))

    subject = (f"🔍 Second Layer — {date_str} | {n_passing} passing of {n_total} | {total_seen} total pipeline")
    cards   = "".join(company_card(c) for c in passing) if passing else "<p style='color:#888;text-align:center;padding:30px;'>No companies met the threshold today.</p>"

    below_rows = "".join(
        f"<tr style='background:{'#fff' if i%2==0 else '#f9f9f9'};'>"
        f"<td style='padding:6px 10px;font-size:12px;'>{r.get('company_name','')}</td>"
        f"<td style='padding:6px 10px;font-size:11px;'>{src_badge(r.get('source',''))}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#666;'>{r.get('vertical','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;font-weight:bold;text-align:center;'>{r.get('score_pct',0):.1f}%</td>"
        f"<td style='padding:6px 10px;font-size:12px;'>{r.get('decision','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#888;'>{r.get('key_weakness','')[:65]}...</td>"
        f"</tr>"
        for i,r in enumerate(below)
    )
    below_section = f"""
<div style="margin-bottom:20px;">
  <h2 style="color:#666;font-size:13px;margin-bottom:8px;border-bottom:1px solid #ddd;padding-bottom:5px;">📊 Evaluated But Filtered (below {MIN_SCORE_PCT:.0f}%)</h2>
  <table style="width:100%;border-collapse:collapse;background:white;border-radius:6px;overflow:hidden;">
    <tr style="background:#1b3a6b;">
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Company</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Source</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Vertical</th>
      <th style="padding:7px 10px;color:white;font-size:11px;">Score</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Decision</th>
      <th style="padding:7px 10px;color:white;font-size:11px;text-align:left;">Key Weakness</th>
    </tr>{below_rows}
  </table>
</div>""" if below else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:840px;margin:0 auto;background:#f4f6f9;padding:20px;">
  <div style="background:#1b3a6b;border-radius:10px 10px 0 0;padding:22px 26px;">
    <div style="color:white;font-size:20px;font-weight:bold;">🔍 Second Layer VC Pipeline</div>
    <div style="color:#aac4e8;font-size:12px;margin-top:3px;">{date_str} · Daily Digest</div>
    <div style="color:#d6e4f7;font-size:11px;margin-top:6px;">Sources today: {src_summary}</div>
  </div>
  <div style="background:#2e75b6;padding:12px 26px;display:flex;gap:24px;margin-bottom:18px;">
    <div style="text-align:center;"><div style="color:white;font-size:22px;font-weight:bold;">{n_total}</div><div style="color:#aac4e8;font-size:10px;">TODAY</div></div>
    <div style="text-align:center;"><div style="color:#c6efce;font-size:22px;font-weight:bold;">{n_passing}</div><div style="color:#aac4e8;font-size:10px;">PASSING ≥{MIN_SCORE_PCT:.0f}%</div></div>
    <div style="text-align:center;"><div style="color:#ffc7ce;font-size:22px;font-weight:bold;">{n_total-n_passing}</div><div style="color:#aac4e8;font-size:10px;">FILTERED</div></div>
    <div style="text-align:center;"><div style="color:#ffeb9c;font-size:22px;font-weight:bold;">{pass_rate:.0f}%</div><div style="color:#aac4e8;font-size:10px;">PASS RATE</div></div>
    <div style="text-align:center;border-left:1px solid #5a9fd4;padding-left:24px;"><div style="color:white;font-size:22px;font-weight:bold;">{total_seen}</div><div style="color:#aac4e8;font-size:10px;">TOTAL PIPELINE</div></div>
  </div>
  <div style="margin-bottom:22px;">
    <h2 style="color:#1b3a6b;font-size:15px;margin-bottom:12px;border-bottom:2px solid #2e75b6;padding-bottom:5px;">✅ Meeting Threshold (≥{MIN_SCORE_PCT:.0f}%)</h2>
    {cards}
  </div>
  {below_section}
  <div style="text-align:center;color:#aaa;font-size:10px;margin-top:16px;padding-top:14px;border-top:1px solid #ddd;">
    Bryan Hanley · Second Layer VC Framework · 8-Source Pipeline · Never repeats a company<br>
    YC (6 batches) · TechCrunch · SEC Form D · Hacker News · GitHub · Product Hunt · Indie Hackers · Wellfound
  </div>
</body></html>"""
    return subject, html


# ── SEND EMAIL ────────────────────────────────────────────────────────────────
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
    print(f"=== Second Layer Pipeline v3: {date_str} ===")

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
    for co in candidates:
        print(f"Scoring: {co['name']} ({co.get('source','')})")
        result = score_company(co)
        if result:
            results.append(result)
            print(f"  → {result.get('score_pct',0):.1f}% | {result.get('decision','')}")
        time.sleep(1)

    append_results_to_sheet(results, date_str)
    total_seen = len(previously_seen) + len(results)

    print(f"\nBuilding digest for {len(results)} companies...")
    subject, html = build_email(results, date_str, total_seen)
    send_email(subject, html)
    print("Done.")

if __name__ == "__main__":
    main()
