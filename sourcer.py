"""
Second Layer VC Pipeline — Daily Sourcer & Scorer
Uses Crunchbase free search + Claude API to find, research, and score
seed-stage startups against Bryan Hanley's 11-factor rubric.
"""

import os
import json
import time
import datetime
import smtplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic
import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  (set as GitHub Actions secrets / env vars)
# ─────────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
EMAIL_SENDER      = os.environ["EMAIL_SENDER"]       # Gmail address
EMAIL_PASSWORD    = os.environ["EMAIL_PASSWORD"]     # Gmail App Password
EMAIL_RECIPIENT   = os.environ["EMAIL_RECIPIENT"]    # your email
MIN_SCORE_PCT     = float(os.environ.get("MIN_SCORE_PCT", "65"))  # threshold %

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────────────────────────────────────────
# SECOND LAYER SEARCH QUERIES
# Rotated daily so each run surfaces a different vertical.
# ─────────────────────────────────────────────────────────────────────────────
DAILY_QUERIES = [
    # Monday — Fintech compliance
    ["AML compliance AI seed 2024 2025", "KYC automation startup seed funded", "DeFi compliance infrastructure seed"],
    # Tuesday — Healthcare
    ["HIPAA compliance automation seed startup", "healthcare AI workflow seed 2024", "prior authorization AI seed funded"],
    # Wednesday — Cybersecurity
    ["AI cybersecurity seed 2024 2025 funded", "cloud security compliance seed startup", "DFIR incident response AI seed"],
    # Thursday — Legal + RegTech
    ["legal AI compliance seed 2024", "regtech AI seed funded 2024 2025", "contract compliance AI startup seed"],
    # Friday — Energy + Supply Chain
    ["energy grid AI compliance seed startup", "supply chain risk AI seed 2024", "SBOM compliance startup seed funded"],
    # Saturday — Insurance + Data Privacy
    ["insurtech AI underwriting seed 2024", "data privacy compliance AI seed startup", "PII automation seed funded"],
    # Sunday — Emerging / AI Governance
    ["AI governance compliance seed 2024", "model risk management AI seed", "tariff compliance AI startup seed"],
]

# ─────────────────────────────────────────────────────────────────────────────
# FACTOR WEIGHTS (must sum to 1.0)
# ─────────────────────────────────────────────────────────────────────────────
FACTORS = [
    ("1A. Founder-Market Fit",      0.10),
    ("1B. Technical Execution",     0.08),
    ("1C. Founder Commitment",      0.07),
    ("2A. Early PMF",               0.12),
    ("2B. Revenue Signals",         0.08),
    ("3A. Market Size (TAM)",       0.12),
    ("3B. Timing & Competition",    0.08),
    ("4. Traction Quantitative",    0.07),
    ("5. Traction Qualitative",     0.08),
    ("6. Capital Efficiency",       0.10),
    ("7. Investor Signal",          0.10),
]
FACTOR_NAMES  = [f for f, _ in FACTORS]
FACTOR_WEIGHTS = [w for _, w in FACTORS]

SCORING_RUBRIC = """
FACTOR SCORING GUIDE (score each 0–10):

1A. Founder-Market Fit (10%)
9-10: Prior exit + deep domain expertise
7-8: Strong background, 2-5 years relevant experience
5-6: Some relevant experience, adjacent domain
3-4: Limited experience, young founder learning
0-2: No relevant experience

1B. Technical Execution (8%)
9-10: Strong technical team, working product, proven builders
7-8: Competent technical execution, solid prototype
5-6: Basic technical capability, early MVP
3-4: Weak technical skills, struggling to build
0-2: Cannot build product, vaporware

1C. Founder Commitment (7%)
9-10: Quit jobs, full-time, invested savings, burned bridges
7-8: Full-time, committed, but some safety net
5-6: Part-time or recent commitment
3-4: Side project mentality, minimal sacrifice
0-2: Not committed, keeping day job

2A. Early Product-Market Fit (12%)
9-10: Users obsessed, organic growth, strong retention
7-8: Good engagement, users coming back consistently
5-6: Some users engaged, moderate retention
3-4: Low engagement, poor retention
0-2: No users or product not launched

2B. Revenue Signals (8%)
9-10: Strong revenue, proven unit economics
7-8: Some revenue, clear path to monetization
5-6: Early revenue or paying pilots
3-4: Minimal revenue, unclear monetization
0-2: $0 revenue

3A. Market Size / TAM (12%)
9-10: $50B+ TAM
7-8: $10-50B TAM
5-6: $1-10B TAM
3-4: $100M-$1B TAM
0-2: <$100M TAM

3B. Timing & Competition (8%)
9-10: Perfect timing, greenfield, technology enabler ready
7-8: Good timing, beatable competition
5-6: Okay timing, crowded but differentiated
3-4: Poor timing, too early or too late
0-2: Terrible timing, entrenched competition

4. Traction – Quantitative (7%)
9-10: Explosive growth >20% weekly
7-8: Strong growth 10-20% weekly
5-6: Steady growth 5-10% weekly
3-4: Slow growth <5% weekly
0-2: No growth or declining

5. Traction – Qualitative (8%)
9-10: Users LOVE it, would be devastated if it went away
7-8: Users really like it, strong NPS
5-6: Users find it useful, decent feedback
3-4: Mixed feedback, some churn
0-2: Users don't care, high churn

6. Capital Efficiency (10%)
9-10: Built on <$100K, extremely lean
7-8: Efficient, good progress on reasonable budget
5-6: Average efficiency for stage
3-4: Capital intensive, burning fast
0-2: Massive burn, very inefficient

7. Investor Signal (10%)
9-10: Top-tier lead (Sequoia, a16z, Benchmark)
7-8: Solid tier-1 or tier-2 lead
5-6: Reputable angels or smaller funds
3-4: Unknown investors
0-2: Red flag investors or none
"""

SECOND_LAYER_CONTEXT = """
SECOND LAYER APPROACH — INVESTMENT THESIS:
The "Second Layer" approach identifies startups that solve problems CREATED BY dominant/fast-growing industries,
rather than investing in those dominant industries directly.

Framework:
1a. What industries are dominant today? (AI, Fintech, Healthcare, Energy)
1b. What industries are growing fastest? (AI/LLMs, DeFi, Cloud)
2a. What opportunities do 1a/1b create?
2b. What RISKS do 1a/1b create? → These risks = investment opportunities
3b. What startups solve those risks/bottlenecks? → THESE are the investments

Strong Second Layer examples:
- Crypto/DeFi boom → AML/compliance risk → AML automation startups
- AI adoption → model risk/governance → AI governance platforms
- Healthcare digitization → HIPAA bottlenecks → HIPAA-compliant workflow infra
- AI compute boom → grid strain → power-compliant AI orchestration
- Legal AI → malpractice risk → compliance-grade legal AI tooling

A company FAILS Second Layer alignment if it IS the dominant industry (e.g., an LLM company, a crypto exchange)
rather than solving the downstream risks/problems that industry creates.
"""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: SCRAPE COMPANY NAMES FROM SEARCH
# ─────────────────────────────────────────────────────────────────────────────

def search_crunchbase_free(query: str) -> list[str]:
    """
    Scrapes Crunchbase search results page for company names.
    Returns a list of company name strings.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    url = f"https://www.crunchbase.com/textsearch?q={requests.utils.quote(query)}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        # Crunchbase free search surfaces company names in anchor tags
        names = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/organization/" in href:
                name = a.get_text(strip=True)
                if name and len(name) > 2 and name not in names:
                    names.append(name)
        return names[:8]
    except Exception:
        return []


def search_techcrunch_funding(query: str) -> list[str]:
    """
    Fallback: scrape TechCrunch funding articles for company names.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://techcrunch.com/search/{requests.utils.quote(query)}/"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        names = []
        for article in soup.find_all("h2", class_=re.compile("post-block__title")):
            text = article.get_text(strip=True)
            # Extract company name from headline (usually first word or before "raises")
            match = re.match(r"^([A-Z][A-Za-z0-9\.\-]+)", text)
            if match:
                name = match.group(1)
                if name not in names and len(name) > 2:
                    names.append(name)
        return names[:6]
    except Exception:
        return []


def get_candidate_companies(day_of_week: int) -> list[str]:
    """
    Gets today's candidate companies from search queries.
    Uses day of week to rotate verticals.
    """
    queries = DAILY_QUERIES[day_of_week % 7]
    all_companies = []
    for query in queries:
        found = search_crunchbase_free(query)
        if not found:
            found = search_techcrunch_funding(query)
        all_companies.extend(found)
        time.sleep(2)  # polite delay

    # Deduplicate
    seen = set()
    unique = []
    for c in all_companies:
        key = c.lower().strip()
        if key not in seen and len(key) > 2:
            seen.add(key)
            unique.append(c)
    return unique[:15]  # cap at 15 candidates per day


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: RESEARCH + SCORE EACH COMPANY VIA CLAUDE API
# ─────────────────────────────────────────────────────────────────────────────

RESEARCH_AND_SCORE_PROMPT = """
You are a venture capital analyst applying the "Second Layer Approach" investment framework.

{second_layer_context}

{scoring_rubric}

---

TASK: Research and evaluate the following company:

Company Name: {company_name}

1. First, do a brief research pass to determine:
   - What does this company do?
   - When was it founded?
   - What stage/funding has it raised?
   - Who are the founders?
   - What vertical/industry?
   - Any traction, revenue, or customer signals?

2. Determine if this company fits the Second Layer thesis (does it solve a DOWNSTREAM problem
   created by a dominant industry, rather than being the dominant industry itself?).
   If it does NOT fit the Second Layer thesis at all, set second_layer_alignment = false and
   you can score it low on all factors.

3. Score the company on all 11 factors using the rubric above.

4. Compute the weighted score: Σ(score_i × weight_i)
   Weights: 1A=0.10, 1B=0.08, 1C=0.07, 2A=0.12, 2B=0.08, 3A=0.12, 3B=0.08, 4=0.07, 5=0.08, 6=0.10, 7=0.10

Respond ONLY with valid JSON in exactly this format:
{{
  "company_name": "string",
  "founded": "YYYY or unknown",
  "stage": "Pre-Seed/Seed/Series A/etc",
  "raise": "$XM or unknown",
  "vertical": "string",
  "what_they_do": "2-3 sentence description",
  "second_layer_alignment": true/false,
  "second_layer_logic": "One sentence: [First Layer trend] → [risk/problem] → [this company's solution]",
  "scores": {{
    "1A": 0,
    "1B": 0,
    "1C": 0,
    "2A": 0,
    "2B": 0,
    "3A": 0,
    "3B": 0,
    "4": 0,
    "5": 0,
    "6": 0,
    "7": 0
  }},
  "weighted_score": 0.0,
  "score_pct": 0.0,
  "decision": "★★★★★ STRONG YES / ★★★★ YES / ★★★ DEEP DIVE / ★★ PROBABLY PASS / ★ HARD PASS",
  "key_strength": "string",
  "key_weakness": "string",
  "why_second_layer": "string explaining specifically how this fits or fails the thesis"
}}
"""


def research_and_score(company_name: str) -> dict | None:
    """
    Uses Claude to research a company and score it against the rubric.
    Returns parsed JSON dict or None if failed.
    """
    prompt = RESEARCH_AND_SCORE_PROMPT.format(
        second_layer_context=SECOND_LAYER_CONTEXT,
        scoring_rubric=SCORING_RUBRIC,
        company_name=company_name,
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"^```\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        # Recompute weighted score to verify
        scores = data.get("scores", {})
        weights = {
            "1A": 0.10, "1B": 0.08, "1C": 0.07,
            "2A": 0.12, "2B": 0.08,
            "3A": 0.12, "3B": 0.08,
            "4": 0.07, "5": 0.08,
            "6": 0.10, "7": 0.10,
        }
        computed = sum(scores.get(k, 0) * v for k, v in weights.items())
        data["weighted_score"] = round(computed, 2)
        data["score_pct"] = round(computed * 10, 1)

        # Assign decision label
        pct = data["score_pct"]
        if pct >= 85:   data["decision"] = "★★★★★ STRONG YES"
        elif pct >= 75: data["decision"] = "★★★★ YES"
        elif pct >= 65: data["decision"] = "★★★ DEEP DIVE"
        elif pct >= 55: data["decision"] = "★★ PROBABLY PASS"
        else:           data["decision"] = "★ HARD PASS"

        return data

    except Exception as e:
        print(f"Error scoring {company_name}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: BUILD EMAIL DIGEST
# ─────────────────────────────────────────────────────────────────────────────

DECISION_COLORS = {
    "★★★★★ STRONG YES": ("#1a472a", "#a9d18e"),
    "★★★★ YES":          ("#1a472a", "#c6efce"),
    "★★★ DEEP DIVE":     ("#7d6608", "#ffeb9c"),
    "★★ PROBABLY PASS":  ("#843c0c", "#fce4d6"),
    "★ HARD PASS":       ("#9c0006", "#ffc7ce"),
}

FACTOR_DISPLAY = {
    "1A": "Founder-Mkt Fit", "1B": "Tech Execution", "1C": "Commitment",
    "2A": "Early PMF",       "2B": "Revenue",         "3A": "TAM",
    "3B": "Timing",          "4":  "Traction (Q)",    "5":  "Traction (Q)",
    "6":  "Cap Efficiency",  "7":  "Investor Signal",
}


def score_bar(score: int) -> str:
    """Returns a colored score badge."""
    if score >= 8:
        bg, txt = "#c6efce", "#276221"
    elif score >= 6:
        bg, txt = "#ffeb9c", "#7d6608"
    else:
        bg, txt = "#ffc7ce", "#9c0006"
    return (
        f'<span style="background:{bg};color:{txt};padding:2px 7px;'
        f'border-radius:4px;font-weight:bold;font-size:12px;">{score}</span>'
    )


def build_company_card(co: dict) -> str:
    """Renders one company as an HTML card."""
    decision = co.get("decision", "★ HARD PASS")
    txt_color, bg_color = DECISION_COLORS.get(decision, ("#333", "#f2f2f2"))

    scores = co.get("scores", {})
    score_cells = "".join(
        f'<td style="text-align:center;padding:4px 6px;font-size:11px;">'
        f'<div style="color:#666;margin-bottom:2px;">{FACTOR_DISPLAY.get(k,"")}</div>'
        f'{score_bar(v)}</td>'
        for k, v in scores.items()
    )

    return f"""
<div style="border:1px solid #ddd;border-radius:8px;margin-bottom:20px;overflow:hidden;font-family:Arial,sans-serif;">
  <!-- Header -->
  <div style="background:#1b3a6b;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;">
    <div>
      <span style="color:white;font-size:16px;font-weight:bold;">{co.get('company_name','')}</span>
      <span style="color:#aac4e8;font-size:12px;margin-left:12px;">
        {co.get('stage','')} · {co.get('raise','')} · Founded {co.get('founded','')}
      </span>
    </div>
    <div style="background:{bg_color};color:{txt_color};padding:5px 12px;border-radius:20px;font-weight:bold;font-size:12px;">
      {decision}
    </div>
  </div>
  <!-- Body -->
  <div style="padding:14px 18px;background:white;">
    <div style="display:flex;gap:20px;margin-bottom:10px;">
      <div style="flex:1;">
        <div style="font-size:11px;color:#888;margin-bottom:3px;">VERTICAL</div>
        <div style="font-size:13px;font-weight:bold;color:#1b3a6b;">{co.get('vertical','')}</div>
      </div>
      <div style="flex:1;text-align:right;">
        <div style="font-size:11px;color:#888;margin-bottom:3px;">WEIGHTED SCORE</div>
        <div style="font-size:22px;font-weight:bold;color:{txt_color};">{co.get('score_pct',0):.1f}%</div>
      </div>
    </div>
    <div style="background:#f5f9ff;border-left:3px solid #2e75b6;padding:8px 12px;border-radius:0 4px 4px 0;margin-bottom:10px;font-size:12px;color:#333;">
      <strong>🔗 Second Layer:</strong> {co.get('second_layer_logic','')}
    </div>
    <div style="font-size:12px;color:#555;margin-bottom:10px;line-height:1.5;">
      {co.get('what_they_do','')}
    </div>
    <!-- Scores row -->
    <table style="width:100%;border-collapse:collapse;margin-bottom:10px;">
      <tr>{score_cells}</tr>
    </table>
    <!-- Strength / Weakness -->
    <div style="display:flex;gap:12px;">
      <div style="flex:1;background:#f0f9f0;border-radius:4px;padding:8px 10px;">
        <div style="font-size:10px;color:#276221;font-weight:bold;margin-bottom:3px;">✅ STRENGTH</div>
        <div style="font-size:11px;color:#333;">{co.get('key_strength','')}</div>
      </div>
      <div style="flex:1;background:#fff5f5;border-radius:4px;padding:8px 10px;">
        <div style="font-size:10px;color:#9c0006;font-weight:bold;margin-bottom:3px;">⚠️ WEAKNESS</div>
        <div style="font-size:11px;color:#333;">{co.get('key_weakness','')}</div>
      </div>
    </div>
  </div>
</div>
"""


def build_email(results: list[dict], date_str: str, day_queries: list[str]) -> tuple[str, str]:
    """Builds subject line and HTML email body."""
    passing   = [r for r in results if r.get("score_pct", 0) >= MIN_SCORE_PCT]
    n_total   = len(results)
    n_passing = len(passing)

    # Sort passing by score descending
    passing.sort(key=lambda x: x.get("score_pct", 0), reverse=True)

    subject = (
        f"🔍 Second Layer Pipeline — {date_str} | "
        f"{n_passing} companies ≥{MIN_SCORE_PCT:.0f}% of {n_total} evaluated"
    )

    verticals_today = " · ".join(set(r.get("vertical", "") for r in results if r.get("vertical")))

    cards_html = "".join(build_company_card(co) for co in passing) if passing else (
        "<p style='color:#888;text-align:center;padding:30px;'>No companies met the threshold today.</p>"
    )

    # Below-threshold summary table
    below = [r for r in results if r.get("score_pct", 0) < MIN_SCORE_PCT]
    below.sort(key=lambda x: x.get("score_pct", 0), reverse=True)
    below_rows = "".join(
        f"<tr style='background:{'#fff' if i%2==0 else '#f9f9f9'};'>"
        f"<td style='padding:6px 10px;font-size:12px;'>{r.get('company_name','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#888;'>{r.get('vertical','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;text-align:center;'>{r.get('score_pct',0):.1f}%</td>"
        f"<td style='padding:6px 10px;font-size:12px;'>{r.get('decision','')}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#888;'>{r.get('key_weakness','')[:80]}...</td>"
        f"</tr>"
        for i, r in enumerate(below)
    )

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;background:#f4f6f9;padding:20px;">

  <!-- Header -->
  <div style="background:#1b3a6b;border-radius:10px 10px 0 0;padding:24px 28px;margin-bottom:0;">
    <div style="color:white;font-size:22px;font-weight:bold;">🔍 Second Layer VC Pipeline</div>
    <div style="color:#aac4e8;font-size:13px;margin-top:4px;">{date_str} · Daily Sourcing Digest</div>
    <div style="color:#d6e4f7;font-size:12px;margin-top:8px;">Verticals searched today: {verticals_today}</div>
  </div>

  <!-- Stats bar -->
  <div style="background:#2e75b6;padding:14px 28px;display:flex;gap:30px;margin-bottom:20px;">
    <div style="text-align:center;">
      <div style="color:white;font-size:24px;font-weight:bold;">{n_total}</div>
      <div style="color:#aac4e8;font-size:11px;">EVALUATED</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#c6efce;font-size:24px;font-weight:bold;">{n_passing}</div>
      <div style="color:#aac4e8;font-size:11px;">PASSING ≥{MIN_SCORE_PCT:.0f}%</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#ffc7ce;font-size:24px;font-weight:bold;">{n_total - n_passing}</div>
      <div style="color:#aac4e8;font-size:11px;">FILTERED OUT</div>
    </div>
    <div style="text-align:center;">
      <div style="color:#ffeb9c;font-size:24px;font-weight:bold;">
        {(n_passing/n_total*100):.0f}%
      </div>
      <div style="color:#aac4e8;font-size:11px;">PASS RATE</div>
    </div>
  </div>

  <!-- Passing companies -->
  <div style="margin-bottom:24px;">
    <h2 style="color:#1b3a6b;font-size:16px;margin-bottom:14px;border-bottom:2px solid #2e75b6;padding-bottom:6px;">
      ✅ Companies Meeting Threshold (≥{MIN_SCORE_PCT:.0f}%)
    </h2>
    {cards_html}
  </div>

  <!-- Below threshold table -->
  {'<div style="margin-bottom:24px;"><h2 style="color:#666;font-size:14px;margin-bottom:10px;border-bottom:1px solid #ddd;padding-bottom:6px;">📊 Below Threshold — Evaluated But Filtered</h2><table style="width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;"><tr style="background:#1b3a6b;"><th style="padding:8px 10px;color:white;font-size:11px;text-align:left;">Company</th><th style="padding:8px 10px;color:white;font-size:11px;text-align:left;">Vertical</th><th style="padding:8px 10px;color:white;font-size:11px;">Score</th><th style="padding:8px 10px;color:white;font-size:11px;text-align:left;">Decision</th><th style="padding:8px 10px;color:white;font-size:11px;text-align:left;">Key Weakness</th></tr>' + below_rows + '</table></div>' if below else ''}

  <!-- Footer -->
  <div style="text-align:center;color:#aaa;font-size:11px;margin-top:20px;padding-top:16px;border-top:1px solid #ddd;">
    Bryan Hanley Second Layer VC Framework · Automated Daily Pipeline<br>
    Threshold: ≥{MIN_SCORE_PCT:.0f}% | Powered by Claude AI + Crunchbase
  </div>

</body>
</html>
"""
    return subject, html


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: SEND EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
    print(f"Email sent: {subject}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    today        = datetime.date.today()
    date_str     = today.strftime("%A, %B %d %Y")
    day_of_week  = today.weekday()  # 0=Mon, 6=Sun

    print(f"=== Second Layer Pipeline Run: {date_str} ===")

    # Step 1: Get candidate companies
    print("Sourcing candidates...")
    candidates = get_candidate_companies(day_of_week)
    print(f"Found {len(candidates)} candidates: {candidates}")

    if not candidates:
        print("No candidates found today. Sending empty digest.")
        subject, html = build_email([], date_str, DAILY_QUERIES[day_of_week % 7])
        send_email(subject, html)
        return

    # Step 2: Research and score each
    results = []
    for company in candidates:
        print(f"Scoring: {company}")
        result = research_and_score(company)
        if result:
            results.append(result)
            print(f"  → {result.get('score_pct', 0):.1f}% | {result.get('decision', '')}")
        time.sleep(1)  # rate limiting

    # Step 3: Build and send email
    print(f"Building email digest ({len(results)} scored)...")
    subject, html = build_email(results, date_str, DAILY_QUERIES[day_of_week % 7])
    send_email(subject, html)
    print("Done.")


if __name__ == "__main__":
    main()
