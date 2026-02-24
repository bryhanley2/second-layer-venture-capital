"""
sheets_logger.py — Appends daily pipeline results to a Google Sheet.

Setup (one-time, ~10 minutes):
1. Go to console.cloud.google.com
2. Create a new project (or use existing)
3. Enable the Google Sheets API and Google Drive API
4. Create a Service Account → download the JSON key file
5. Share your Google Sheet with the service account email (Editor access)
6. Add the JSON key contents as a GitHub Secret named GOOGLE_SERVICE_ACCOUNT_JSON
7. Add your Sheet ID as a GitHub Secret named GOOGLE_SHEET_ID
"""

import os
import json
import datetime
import requests

# ─────────────────────────────────────────────────────────────────────────────
# COLUMN DEFINITIONS — order matches the sheet header row
# ─────────────────────────────────────────────────────────────────────────────
COLUMNS = [
    "Date",
    "Company",
    "Founded",
    "Stage",
    "Raise",
    "Vertical",
    "Source",
    "Second Layer Logic",
    "What They Do",
    "Second Layer Aligned",
    # 11 factor scores
    "1A Founder-Mkt Fit",
    "1B Tech Execution",
    "1C Commitment",
    "2A Early PMF",
    "2B Revenue",
    "3A TAM",
    "3B Timing",
    "4 Traction Q",
    "5 Traction Qual",
    "6 Cap Efficiency",
    "7 Investor Signal",
    # Summary
    "Weighted Score",
    "Score %",
    "Decision",
    "Key Strength",
    "Key Weakness",
]

SCORE_KEYS = ["1A","1B","1C","2A","2B","3A","3B","4","5","6","7"]


def _get_access_token(service_account_json: str) -> str:
    """
    Gets a short-lived OAuth2 access token from a Google Service Account JSON key.
    Uses the google-auth library if available, otherwise falls back to manual JWT.
    """
    try:
        import google.auth
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request

        info = json.loads(service_account_json)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        creds.refresh(Request())
        return creds.token

    except ImportError:
        # Manual JWT approach if google-auth not installed
        import base64
        import hmac
        import hashlib
        import time
        import struct

        info   = json.loads(service_account_json)
        now    = int(time.time())
        claims = {
            "iss":   info["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive",
            "aud":   "https://oauth2.googleapis.com/token",
            "exp":   now + 3600,
            "iat":   now,
        }

        def b64(data):
            if isinstance(data, str):
                data = data.encode()
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        header    = b64(json.dumps({"alg": "RS256", "typ": "JWT"}))
        payload   = b64(json.dumps(claims))
        sign_input = f"{header}.{payload}"

        # Sign with RSA private key
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        private_key = serialization.load_pem_private_key(
            info["private_key"].encode(),
            password=None,
            backend=default_backend(),
        )
        signature = private_key.sign(sign_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        jwt = f"{sign_input}.{b64(signature)}"

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt,
            },
        )
        return resp.json()["access_token"]


def ensure_header_row(sheet_id: str, token: str):
    """
    Checks if row 1 has headers. If the sheet is empty, writes the header row.
    """
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
        f"/values/Pipeline!A1:Z1"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    data = resp.json()
    existing = data.get("values", [])

    if not existing or existing[0][0] != "Date":
        # Write header row
        write_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
            f"/values/Pipeline!A1:Z1?valueInputOption=RAW"
        )
        requests.put(
            write_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"values": [COLUMNS]},
        )
        print("✅ Sheet header row created")


def company_to_row(result: dict, date_str: str) -> list:
    """Converts a scored company dict into a flat row matching COLUMNS."""
    scores = result.get("scores", {})
    return [
        date_str,
        result.get("company_name", ""),
        result.get("founded", ""),
        result.get("stage", ""),
        result.get("raise", ""),
        result.get("vertical", ""),
        result.get("source", ""),
        result.get("second_layer_logic", ""),
        result.get("what_they_do", ""),
        "Yes" if result.get("second_layer_alignment") else "No",
        scores.get("1A", ""),
        scores.get("1B", ""),
        scores.get("1C", ""),
        scores.get("2A", ""),
        scores.get("2B", ""),
        scores.get("3A", ""),
        scores.get("3B", ""),
        scores.get("4", ""),
        scores.get("5", ""),
        scores.get("6", ""),
        scores.get("7", ""),
        result.get("weighted_score", ""),
        result.get("score_pct", ""),
        result.get("decision", ""),
        result.get("key_strength", ""),
        result.get("key_weakness", ""),
    ]



def get_previously_seen_companies() -> set:
    """
    Reads the Company column from the Pipeline sheet and returns
    a set of lowercase company names already evaluated.
    Returns empty set if sheet not configured or empty.
    """
    sa_json  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")

    if not sa_json or not sheet_id:
        return set()

    try:
        token = _get_access_token(sa_json)
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
               f"/values/Pipeline!B2:B10000")
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
        rows = resp.json().get("values", [])
        seen = {row[0].lower().strip() for row in rows if row}
        print(f"Loaded {len(seen)} previously seen companies from sheet")
        return seen
    except Exception as e:
        print(f"Could not load previous companies: {e}")
        return set()


def append_results_to_sheet(results: list[dict], date_str: str):
    """
    Main function — appends all scored companies from today's run to the sheet.
    Call this from sourcer.py main() after scoring is complete.
    """
    sa_json  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")

    if not sa_json or not sheet_id:
        print("⚠️  Google Sheets logging skipped — secrets not configured.")
        print("    Add GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID to GitHub Secrets.")
        return

    if not results:
        print("No results to log.")
        return

    try:
        print("Logging to Google Sheets...")
        token = _get_access_token(sa_json)

        # Ensure the tab exists — create "Pipeline" tab if needed
        sheet_meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
        meta = requests.get(
            sheet_meta_url,
            headers={"Authorization": f"Bearer {token}"}
        ).json()

        sheet_names = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if "Pipeline" not in sheet_names:
            requests.post(
                f"{sheet_meta_url}:batchUpdate",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"requests": [{"addSheet": {"properties": {"title": "Pipeline"}}}]},
            )
            print("✅ Created 'Pipeline' tab")

        ensure_header_row(sheet_id, token)

        # Append rows
        rows = [company_to_row(r, date_str) for r in results]
        append_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
            f"/values/Pipeline!A:Z:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
        )
        resp = requests.post(
            append_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"values": rows},
        )

        if resp.status_code == 200:
            updates = resp.json().get("updates", {})
            print(f"✅ Logged {len(rows)} companies to Google Sheets "
                  f"({updates.get('updatedRows', '?')} rows added)")
        else:
            print(f"⚠️  Sheets API error: {resp.status_code} — {resp.text[:200]}")

    except Exception as e:
        print(f"⚠️  Google Sheets logging failed: {e}")
        print("    Pipeline will continue — email digest unaffected.")
