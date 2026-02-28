# Second Layer VC Pipeline

An automated daily sourcing and scoring engine built around a proprietary investment thesis — the **Second Layer Approach**.

---

## The Thesis

The majority of venture capital today is concentrated in industries AI is directly shaping — healthtech, fintech, software, manufacturing. The work of founders building in these spaces is essential, and the rebound in VC funding heading into 2026 reflects that.

But to solely look in these areas is a near-sighted approach to a long-term opportunity.

AI will impact innumerable industries. What we can't forget to ask is not just how the industries currently touched by AI will be impacted — but how those industries will impact others. That second-order question is where I focus.

I call this the **Second Layer** approach to venture investing:

```
Step 1a.  What industries are most dominant today?
Step 1b.  What industries are growing the fastest?

Step 2a.  What opportunities might 1a and 1b lead to?
Step 2b.  What risks might 1a and 1b lead to?

Step 3a.  What solutions supplement the growth of 2a?  →  these are investments
Step 3b.  What solutions mitigate the risks of 2b?     →  these are investments
```

**Why this matters now**

2025 was a top-heavy year — over 50% of all VC deal value came from just 0.05% of deals, led by decacorns like Databricks, OpenAI, SpaceX, and Anthropic. 2026 is expected to bring greater deal volume and less concentration as investors shift focus toward measurable outcomes over pure experimentation.

With greater liquidity returning to LPs — finally seeing returns as AI companies pursue enterprise partnerships, subscription tiers, and monetization at scale — there is more dry powder available and greater openness to diversification.

While a majority of investors center their focus on the industries AI is impacting today, by taking a more forward-looking approach, the Second Layer Approach seeks out opportunities both with less competition and with more long-term runway.

```
First Layer (dominant trend)         →    Second Layer (the opportunity)
──────────────────────────────────────────────────────────────────────────
AI adoption scales across industries  →   AI governance & model risk platforms
Fintech expands globally              →   AML, KYB/KYC compliance automation
Healthcare digitizes rapidly          →   HIPAA-compliant workflow infrastructure
Legal AI proliferates                 →   Compliance-grade legal tooling
Crypto/DeFi grows                     →   Financial crime detection infrastructure
```

This is not compliance tech investing. It is a sourcing logic — a systematic way of finding the companies that dominant trends make inevitable, before the broader market recognizes them as a category.

---


## What This Does

Every morning at 7am, this pipeline runs automatically and delivers a ranked digest to my inbox.

**Step 1 — Source**
Pulls candidates from 10 channels simultaneously:
- YC company database (6 batches: W25 → W21)
- Hacker News Show HN posts and seed funding mentions
- SEC EDGAR Form D filings (public seed raise disclosures, often pre-press)
- RSS feeds: TechCrunch, VentureBeat, StrictlyVC, Crunchbase News, TLDR
- GitHub trending repositories in compliance/security/legal verticals
- BetaList (pre-launch startups)
- Wellfound seed/pre-seed job postings
- StartupBase and F6S startup directories
- Claude AI research (surfaces lesser-known companies by vertical, rotated daily)

**Step 2 — Filter**
Three-layer stage gate removes Series B+ companies:
1. Keyword filter on sourcing text (catches obvious cases upfront)
2. Hard pre-scoring filter on company description
3. Post-scoring stage gate — Claude identifies actual current stage and drops anything Series B or later before it reaches the digest

**Step 3 — Score**
Each candidate is scored against an 11-factor weighted rubric:

| Factor | Weight | What It Measures |
|---|---|---|
| 1A Founder-Market Fit | 10% | Domain expertise, prior exits |
| 1B Technical Execution | 8% | Can the team actually build? |
| 1C Founder Commitment | 7% | All-in or side project? |
| 2A Early PMF | 12% | Are users obsessed? |
| 2B Revenue Signals | 8% | Proof of monetization |
| 3A Market Size (TAM) | 12% | Room for a unicorn? |
| 3B Timing & Competition | 8% | Right moment, beatable competition? |
| 4 Traction Quantitative | 7% | Growth rate signals |
| 5 Traction Qualitative | 8% | User sentiment and retention |
| 6 Capital Efficiency | 10% | How much built on how little? |
| 7 Investor Signal | 10% | Quality of existing backers |

Decision thresholds:
- **★★★★★ STRONG YES** — 85%+
- **★★★★ YES** — 75–84%
- **★★★ DEEP DIVE** — 65–74%
- **★★ PROBABLY PASS** — 55–64%
- **★ HARD PASS** — below 55%

**Step 4 — Research Founders**
For every company scoring ≥75%, Claude automatically researches the founder name, title, LinkedIn URL, and relevant background — surfaced directly in the email digest with a one-click LinkedIn connect button.

**Step 5 — Deliver + Log**
- Email digest arrives at 7am with full scorecards for passing companies and a summary table of everything evaluated
- All results log to a Google Sheet with founder info and an outreach tracker (Outreach Sent → Response → Meeting Booked)
- Deduplication check ensures the same company is never evaluated twice

---

## Results So Far

- **Companies evaluated:** 100+
- **Pass rate (≥65%):** ~25–35% depending on the day's vertical
- **Sources contributing daily:** 6–10 active channels
- **Stage gate removals:** ~20–30% of candidates filtered as Series B+
- **Run cost:** ~$0.02–0.05/day (Claude Haiku + Google Sheets API)

---

## Tech Stack

| Component | Tool |
|---|---|
| Automation | GitHub Actions (free tier, daily cron) |
| Sourcing | Python + requests + BeautifulSoup |
| AI Scoring | Anthropic Claude API (Haiku) |
| Storage | Google Sheets API |
| Email delivery | Gmail SMTP |
| Language | Python 3.11 |

Zero infrastructure to manage. No servers. Runs entirely on GitHub's free compute.

---

## Setup

**Prerequisites:** Anthropic API key, Gmail account + App Password, Google Cloud service account

1. Fork this repo
2. Add 6 GitHub Secrets: `ANTHROPIC_API_KEY`, `EMAIL_SENDER`, `EMAIL_PASSWORD`, `EMAIL_RECIPIENT`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID`
3. Enable Google Sheets API and Google Drive API in Google Cloud Console
4. Share your Google Sheet with the service account email
5. Trigger a manual run from the Actions tab to test

Full setup walkthrough in `SETUP.md` *(coming soon)*

---

## Why I Built This

The Second Layer thesis was derived from my work at Gateway Checker, a pharma supply chain compliance startup, where I see firsthand how dominant industry trends create massive downstream compliance and infrastructure needs that most investors underweight. AI has created a lot of hype centered in certain industries; however, we must look beyond its impacts on industries today, and also consider the impact it could have on the industries of tomorrow.

I built this pipeline to operationalize that thesis — to move from intuition to systematic evaluation. After 100+ companies evaluated, the framework has sharpened my conviction in a few key areas: AI governance, healthcare workflow infrastructure, and fintech compliance automation are the verticals producing the most consistently high-scoring candidates.

The pipeline is also a forcing function. Evaluating 10–15 companies every morning before 8am means I'm constantly pressure-testing the thesis against real companies, updating my view on timing and competition, and building a body of work that compounds over time. As AI continues to shape what and how founders build, so will my thesis and the startups aligning with it.

I'm confident this platform and the associated thesis will help me get early exposure to some of the world's most interesting founders and startups, helping me learn more about their approach to solving today's most complex challenges.

---


## File Structure

```
├── sourcer.py          # Main pipeline — sourcing, scoring, email
├── sheets_logger.py    # Google Sheets logging + deduplication
├── .github/
│   └── workflows/
│       └── daily_pipeline.yml  # GitHub Actions cron schedule
└── README.md
```

---

*Built by Bryan Hanley — [bryanhanley.vc](https://bryanhanley.vc) · [LinkedIn](https://linkedin.com/in/bryanhanley)*
