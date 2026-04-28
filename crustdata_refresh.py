"""
Crustdata Main Pipeline Refresh
================================
Runs weekly to pull broad seed-stage candidates into the main pipeline cache.
One API credit per run.

Writes to "Crustdata Cache - Main" tab.
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone
from pipeline_utils import get_sheet_client, SHEET_ID

import gspread

CRUSTDATA_ENDPOINT = "https://api.crustdata.com/screener/screen/"
CACHE_TAB = "Crustdata Cache - Main"

# Hard filter constants (enforced at Crustdata API level)
MAX_TOTAL_FUNDING_USD = 15_000_000
MIN_HEADCOUNT = 1
MAX_HEADCOUNT = 30
MAX_DAYS_SINCE_LAST_ROUND = 730   # 24 months
MAX_COMPANY_AGE_DAYS = 5 * 365    # 5 years


def build_query() -> dict:
    """Broad seed-stage filter, US-focused."""
    return {
        "filters": {
            "op": "and",
            "conditions": [
                {"column": "headcount", "type": "in_between",
                 "value": [MIN_HEADCOUNT, MAX_HEADCOUNT]},
                {"column": "total_funding_usd", "type": "<=",
                 "value": MAX_TOTAL_FUNDING_USD},
                {"column": "days_since_last_funding_round", "type": "<=",
                 "value": MAX_DAYS_SINCE_LAST_ROUND},
                {"column": "hq_country", "type": "in",
                 "value": ["United States"]},
                {"column": "days_since_founded", "type": "<=",
                 "value": MAX_COMPANY_AGE_DAYS},
            ],
        },
        "page": 1,
        "limit": 100,
    }


def call_crustdata(query: dict) -> list:
    api_key = os.environ.get("CRUSTDATA_API_KEY")
    if not api_key:
        raise RuntimeError("CRUSTDATA_API_KEY not set")
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    print(f"Calling Crustdata /screener/screen/ ...")
    response = requests.post(CRUSTDATA_ENDPOINT, headers=headers, json=query, timeout=60)
    if response.status_code != 200:
        print(f"Crustdata API error {response.status_code}: {response.text[:500]}")
        return []
    data = response.json()
    companies = data.get("results", data.get("data", []))
    print(f"Returned {len(companies)} companies")
    return companies


def normalise(raw: dict) -> dict:
    def safe_get(d, *keys, default=""):
        for key in keys:
            if isinstance(d, dict) and key in d and d[key] is not None:
                d = d[key]
            else:
                return default
        return d

    return {
        "name": safe_get(raw, "company_name", default=safe_get(raw, "name")),
        "website": safe_get(raw, "website", default=safe_get(raw, "domain")),
        "hq_city": safe_get(raw, "hq_city", default=""),
        "hq_country": safe_get(raw, "hq_country", default=""),
        "founded_date": safe_get(raw, "founded_date", default=safe_get(raw, "year_founded")),
        "headcount": safe_get(raw, "headcount", default=0),
        "total_funding_usd": safe_get(raw, "total_funding_usd", default=0),
        "last_funding_round": safe_get(raw, "last_funding_round", default=""),
        "last_funding_date": safe_get(raw, "last_funding_date", default=""),
        "last_funding_amount_usd": safe_get(raw, "last_funding_amount_usd", default=0),
        "industry": safe_get(raw, "industry", default=""),
        "description": safe_get(raw, "short_description", default=safe_get(raw, "description", default="")),
        "linkedin_url": safe_get(raw, "linkedin_url", default=""),
    }


def write_cache(companies: list):
    client = get_sheet_client()
    sheet = client.open_by_key(SHEET_ID)
    try:
        tab = sheet.worksheet(CACHE_TAB)
    except gspread.WorksheetNotFound:
        tab = sheet.add_worksheet(title=CACHE_TAB, rows=500, cols=15)

    tab.clear()
    headers = [
        "refresh_date", "name", "website", "hq_city", "hq_country",
        "founded_date", "headcount", "total_funding_usd",
        "last_funding_round", "last_funding_date", "last_funding_amount_usd",
        "industry", "description", "linkedin_url",
    ]
    tab.append_row(headers)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for c in companies:
        rows.append([
            now, c["name"], c["website"], c["hq_city"], c["hq_country"],
            str(c["founded_date"]), c["headcount"], c["total_funding_usd"],
            c["last_funding_round"], str(c["last_funding_date"]),
            c["last_funding_amount_usd"], c["industry"],
            str(c["description"])[:500], c["linkedin_url"],
        ])
    if rows:
        tab.append_rows(rows)
        print(f"Wrote {len(rows)} rows to '{CACHE_TAB}' tab")


def main():
    print(f"Main pipeline Crustdata refresh — {datetime.now(timezone.utc).isoformat()}")
    query = build_query()
    raw = call_crustdata(query)
    if not raw:
        print("No results, cache not updated. Exiting.")
        return
    normalised = [normalise(c) for c in raw]
    write_cache(normalised)
    print("Refresh complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
