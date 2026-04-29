"""
Crustdata Main Pipeline Refresh
================================
Runs weekly to pull broad seed-stage candidates into the main pipeline cache.
Writes to "Crustdata Cache - Main" tab.

Uses Crustdata's realtime company search API (correct format per docs).
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone
from pipeline_utils import get_sheet_client, SHEET_ID
import gspread

CRUSTDATA_ENDPOINT = "https://api.crustdata.com/screener/company/search"
CACHE_TAB = "Crustdata Cache - Main"


def build_query(page=1) -> dict:
    """
    Uses the correct Crustdata filter format:
    filter_type, type, value — per official docs.
    """
    return {
        "filters": [
            {"filter_type": "REGION", "type": "in", "value": ["United States"]},
            {"filter_type": "COMPANY_HEADCOUNT", "type": "in", "value": ["1-10", "11-50"]},
        ],
        "page": page,
    }


def call_crustdata() -> list:
    api_key = os.environ.get("CRUSTDATA_API_KEY")
    if not api_key:
        raise RuntimeError("CRUSTDATA_API_KEY not set")

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    all_companies = []
    page = 1
    max_pages = 3  # Stay within credit budget

    while page <= max_pages:
        payload = build_query(page)
        print(f"Fetching page {page}...")
        try:
            response = requests.post(
                CRUSTDATA_ENDPOINT, headers=headers, json=payload, timeout=60
            )
            print(f"Status: {response.status_code}")
            if response.status_code != 200:
                print(f"Error: {response.text[:500]}")
                break
            data = response.json()
            # Response key may be 'companies' or 'records'
            companies = data.get("companies", data.get("records", []))
            print(f"Page {page}: {len(companies)} companies")
            if not companies:
                break
            all_companies.extend(companies)
            page += 1
        except Exception as e:
            print(f"Exception on page {page}: {e}")
            break

    print(f"Total fetched: {len(all_companies)} companies")
    return all_companies


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
        "name": g("name", default=g("company_name", default="")),
        "website": g("website", default=g("company_website_domain", default=g("domain", default=""))),
        "hq_city": g("hq_city", default=""),
        "hq_country": g("hq_country", default=g("largest_headcount_country", default="")),
        "founded_date": str(g("founded_date", default=g("year_founded", default=""))),
        "headcount": g("headcount", default=g("employee_count", default=0)),
        "total_funding_usd": g("total_funding_usd", default=g("total_funding_raised_usd", default=0)),
        "last_funding_round": g("last_funding_round", default=g("last_funding_round_type", default="")),
        "last_funding_date": str(g("last_funding_date", default=g("last_funding_round_date", default=""))),
        "last_funding_amount_usd": g("last_funding_amount_usd", default=g("last_funding_round_amount", default=0)),
        "industry": g("industry", default=g("company_type", default="")),
        "description": str(g("description", default=g("short_description", default="")))[:500],
        "linkedin_url": g("linkedin_url", default=g("linkedin_profile_url", default="")),
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
    raw = call_crustdata()
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
