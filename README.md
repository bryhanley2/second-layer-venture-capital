# Second Layer VC Pipeline — Daily Sourcer

Automatically finds, researches, and scores seed-stage startups every morning
against Bryan Hanley's Second Layer investment thesis and 11-factor rubric.
Results are delivered as a formatted HTML email digest.

---

## How It Works

1. **Sourcing** — Searches Crunchbase (free) and TechCrunch for seed-stage
   companies matching Second Layer verticals. Rotates through different
   verticals each day of the week.

2. **Research + Scoring** — Uses Claude AI to research each company and score
   it across 11 factors (Founder-Market Fit, Technical Execution, PMF, TAM,
   Timing, Capital Efficiency, Investor Signal, etc.)

3. **Filtering** — Only companies scoring ≥65% (Deep Dive threshold) are
   featured in the email. All evaluated companies appear in a summary table.

4. **Email Digest** — Delivered to your inbox at 7:00 AM EST every day.

---

## Setup (One-Time, ~10 minutes)

### Step 1: Fork / Create the GitHub Repo

Create a new GitHub repository and add these files:
```
your-repo/
├── sourcer.py
├── requirements.txt
└── .github/
    └── workflows/
        └── daily_pipeline.yml
```

### Step 2: Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these 4 secrets:

| Secret Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key from console.anthropic.com |
| `EMAIL_SENDER` | Your Gmail address (e.g. bryan@gmail.com) |
| `EMAIL_PASSWORD` | Gmail **App Password** (NOT your regular password) |
| `EMAIL_RECIPIENT` | Where to send the digest (can be same as sender) |

### Step 3: Get a Gmail App Password

Regular Gmail passwords won't work. You need an App Password:

1. Go to your Google Account → **Security**
2. Enable **2-Step Verification** if not already on
3. Go to **Security → App passwords**
4. Select app: **Mail**, device: **Other** → name it "VC Pipeline"
5. Copy the 16-character password → use as `EMAIL_PASSWORD`

### Step 4: Test It

Go to your repo → **Actions → Second Layer VC Pipeline → Run workflow**

This manually triggers a run so you can verify the email arrives before
waiting for the scheduled run.

---

## Customization

### Change the score threshold
In `daily_pipeline.yml`, change `MIN_SCORE_PCT: "65"` to any value:
- `"75"` = only YES and above (stricter)
- `"55"` = include Probably Pass companies too (more volume)

### Change the run time
In `daily_pipeline.yml`, edit the cron schedule:
```yaml
- cron: '0 12 * * *'   # 12:00 UTC = 7:00 AM EST
- cron: '0 13 * * *'   # 13:00 UTC = 8:00 AM EST
- cron: '0 14 * * 1-5' # Weekdays only at 9:00 AM EST
```

### Add your own search queries
Edit the `DAILY_QUERIES` list in `sourcer.py` to add verticals
you want to prioritize or rotate through.

---

## Cost Estimate

- **Claude API**: ~15 companies/day × ~1,500 tokens each ≈ 22,500 tokens/day
  → ~$0.07–0.10/day on claude-sonnet, ~$500–700/year
  → Use `claude-haiku-4-5-20251001` in sourcer.py for ~$0.01/day if cost is a concern

- **GitHub Actions**: Free tier includes 2,000 minutes/month; this job runs
  in ~5 minutes, so well within free limits

---

## Vertical Rotation Schedule

| Day | Verticals Searched |
|---|---|
| Monday | Fintech / AML / KYC compliance |
| Tuesday | Healthcare / HIPAA |
| Wednesday | Cybersecurity / AI threats |
| Thursday | Legal Tech / RegTech |
| Friday | Energy / Supply Chain |
| Saturday | Insurance / Data Privacy |
| Sunday | AI Governance / Emerging |
