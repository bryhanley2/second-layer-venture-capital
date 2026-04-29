"""
Crustdata Main Pipeline Refresh
================================
Runs weekly to pull broad seed-stage candidates into the main pipeline cache.
Writes to "Crustdata Cache - Main" tab.

Uses the current Crustdata in-database company discovery endpoint.
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone
from pipeline_utils import get_sheet_client, SHEET_ID
import gspread

# Current endpoint per Crustdata docs (replaces deprecated /screener/screen/)
CRUSTDATA_ENDPOINT = "https://api.crustdata.com/screener/companydb/search/"
CACHE_TAB = "Crustdata Cache - Main"

MAX_TOTAL_FUNDING_USD = 15_000_000
MIN_HEADCOUNT = 1
MAX_HEADCOUNT = 50
MAX_DAYS_SINCE_LAST_ROUND = 730
MAX_COMPANY_AGE_DAYS = 5 * 365


def build_query() -> dict:
    return {
        "filters": {
            "op": "and",
            "conditions": [
                {"column": "headcount", "type": "in_between", "value": [MIN_HEADCOUNT, MAX_HEADCOUNT], "allow_null": False},
                {"column": "total_funding_raised_usd", "type": "<=", "value": MAX_TOTAL_FUNDING_USD, "allow_null": True},
                {"column": "days_since_last_fundraise", "type": "<=", "value": MAX_DAYS_SINCE_LAST_ROUND, "allow_null": False},
                {"column": "largest_headcount_country", "type": "=", "value": "USA", "allow_null": False},
            ],
        },
        "offset": 0,
        "count": 100,
        "sorts": [],
    }


def call_crustdata(query: dict) -> list:
    api_key = os.environ.get("CRUSTDATA_API_KEY")
    if not api_key:
        raise RuntimeError("CRUSTDATA_API_KEY not set")

    # Try primary endpoint first, then fall back
    endpoints = [
        "https://api.crustdata.com/screener/companydb/search/",
        "https://api.crustdata.com/screener/screen/",
        "https://api.crustdata.com/data_lab/company_discovery/",
    ]

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    for endpoint in endpoints:
        print(f"Trying endpoint: {endpoint}")
        try:
            response = requests.post(endpoint, headers=headers, json=query, timeout=60)
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                companies = data.get("records", data.get("results", data.get("companies", data.get("data", []))))
                print(f"Returned {len(companies)} companies from {endpoint}")
                return companies
            elif response.status_code == 404:
                print(f"404 deprecated, trying next endpoint...")
                continue
            else:
                print(f"Error {response.status_code}: {response.text[:300]}")
                continue
        except Exception as e:
            print(f"Exception on {endpoint}: {e}")
            continue

    print("All endpoints failed.")
    return []


def normalise(raw: dict) -> dict:
    def g(*keys, default=""):
        d = raw
        for k in keys:
            if isinstance(d, dict) and k in d and d[k] is not None:
                d = d[k]
            else:
                return default
        return d

    return {
        "name": g("company_name", default=g("name")),
        "website": g("company_website_domain", default=g("website", default=g("domain"))),
        "hq_city": g("hq_city", default=""),
        "hq_country": g("largest_headcount_country", default=g("hq_country", default="")),
        "founded_date": str(g("founded_date", default=g("year_founded", default=""))),
        "headcount": g("headcount", default=0),
        "total_funding_usd": g("total_funding_raised_usd", default=g("total_funding_usd", default=0)),
        "last_funding_round": g("last_funding_round_type", default=g("last_funding_round", default="")),
        "last_funding_date": str(g("last_funding_round_date", default=g("last_funding_date", default=""))),
        "last_funding_amount_usd": g("last_funding_round_amount", default=0),
        "industry": g("company_type", default=g("industry", default="")),
        "description": str(g("short_description", default=g("description", default="")))[:500],
        "linkedin_url": g("linkedin_profile_url", default=g("linkedin_url", default="")),
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
    rows = [[
        now, c["name"], c["website"], c["hq_city"], c["hq_country"],
        c["founded_date"], c["headcount"], c["total_funding_usd"],
        c["last_funding_round"], c["last_funding_date"],
        c["last_funding_amount_usd"], c["industry"],
        c["description"], c["linkedin_url"],
    ] for c in companies]

    if rows:
        tab.append_rows(rows)
        print(f"Wrote {len(rows)} rows to '{CACHE_TAB}' tab")
    else:
        print("No rows to write.")


def main():
    print(f"Crustdata refresh — {datetime.now(timezone.utc).isoformat()}")
    query = build_query()
    print(f"Query: {json.dumps(query, indent=2)}")
    raw = call_crustdata(query)
    if not raw:
        print("No results. Exiting.")
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
