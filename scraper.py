"""
Token usage scraper: Claude.ai + Ollama → Google Sheets
- Fetches usage data from internal JSON APIs using session cookies
- Writes a row to Google Sheets every run
- Rolls the updated cookies back to GitHub Secrets (prevents expiry drift)
"""

import os
import json
import re
import sys
from datetime import datetime, timezone, timedelta

from camoufox.sync_api import Camoufox
import gspread
from google.oauth2.service_account import Credentials
from github import Github

# ── Config from environment ────────────────────────────────────────────────
CLAUDE_COOKIE = os.environ["CLAUDE_COOKIE"]
OLLAMA_COOKIE = os.environ["OLLAMA_COOKIE"]
GOOGLE_SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]

# For cookie rolling-update (optional — set to skip if not provided)
GH_PAT = os.environ.get("GH_PAT", "")
GH_REPO = os.environ.get("GH_REPO", "")  # e.g. "username/token-logger"

CLAUDE_SHEET = os.environ.get("CLAUDE_SHEET_NAME", "Claude")
OLLAMA_SHEET = os.environ.get("OLLAMA_SHEET_NAME", "Ollama")


# ── Claude.ai ──────────────────────────────────────────────────────────────

def fetch_claude_usage(cookie: str) -> dict:
    """
    Uses Playwright to call claude.ai internal API via a real browser context,
    bypassing Cloudflare. Intercepts /api/organizations/{org_id}/usage JSON response.
    """
    cookie_items = []
    for part in cookie.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or any(c in v for c in '\n\r'):
            continue
        item = {"name": k, "value": v, "domain": "claude.ai", "path": "/"}
        if k.startswith("__Secure-"):
            item["secure"] = True
        cookie_items.append(item)

    usage_data = {}
    with Camoufox(headless=True) as browser:
        ctx = browser.new_context()
        ctx.add_cookies(cookie_items)
        page = ctx.new_page()
        page.goto("https://claude.ai/settings/usage", wait_until="networkidle", timeout=30000)
        print(f"[DEBUG] Final URL: {page.url}", file=sys.stderr)

        orgs = page.evaluate("fetch('/api/organizations').then(r=>r.json())")
        if not orgs:
            raise ValueError("Claude: no organizations returned")
        org_id = orgs[0]["uuid"]
        usage_data = page.evaluate(f"fetch('/api/organizations/{org_id}/usage').then(r=>r.json())")
        credits_data = page.evaluate(f"fetch('/api/organizations/{org_id}/prepaid/credits').then(r=>r.json())")

    if not usage_data:
        raise ValueError("Claude: could not intercept usage API response — cookie may be invalid")

    five_hour = usage_data.get("five_hour") or {}
    seven_day = usage_data.get("seven_day") or {}
    omelette = usage_data.get("seven_day_omelette") or {}
    iguana = usage_data.get("iguana_necktie") or {}
    extra = usage_data.get("extra_usage") or {}

    return {
        "claude_session_pct": five_hour.get("utilization", -1),
        "claude_weekly_pct": seven_day.get("utilization", -1),
        "claude_design_pct": omelette.get("utilization", -1),
        "claude_routine_used": iguana.get("used", 0) if iguana else 0,
        "claude_extra_spent_usd": extra.get("used_credits", 0) / 100,
        "claude_extra_limit_usd": extra.get("monthly_limit", 0) / 100,
        "claude_balance_usd": credits_data.get("amount", 0) / 100 if credits_data else 0,
    }


# ── Ollama ─────────────────────────────────────────────────────────────────

def fetch_ollama_usage(cookie: str) -> dict:
    """
    ollama.com/settings is SSR — usage percentages are embedded in HTML.
    Uses Playwright to load the page with the session cookie, then extracts
    the two <span class="text-sm"> elements containing "X% used".
    """
    cookie_items = []
    for part in cookie.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        item = {"name": k, "value": v, "domain": "ollama.com", "path": "/"}
        if k.startswith("__Secure-"):
            item["secure"] = True
        cookie_items.append(item)

    with Camoufox(headless=True) as browser:
        ctx = browser.new_context()
        ctx.add_cookies(cookie_items)
        page = ctx.new_page()
        page.goto("https://ollama.com/settings", wait_until="networkidle", timeout=30000)
        spans = page.locator("span.text-sm").all_inner_texts()

    pct_values = [s for s in spans if "% used" in s]
    if len(pct_values) < 2:
        raise ValueError(f"Ollama: expected 2 '% used' spans, got: {pct_values}")

    def parse_pct(s):
        return float(s.replace("% used", "").strip())

    return {
        "ollama_session_pct": parse_pct(pct_values[0]),
        "ollama_weekly_pct": parse_pct(pct_values[1]),
    }


# ── Google Sheets ──────────────────────────────────────────────────────────

def _get_gc():
    sa_info = json.loads(GOOGLE_SA_JSON)
    creds = Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)


def append_claude_row(timestamp: str, claude: dict):
    ws = _get_gc().open_by_key(SPREADSHEET_ID).worksheet(CLAUDE_SHEET)
    headers = ["Timestamp", "Current session used(%)", "Weekly limits - All models used(%)", "Weekly limits - Claude Design used(%)",
               "Routine runs", "Extra spent ($)", "Extra limit ($)", "Current balance ($)"]
    if not ws.row_values(1):
        ws.append_row(headers)
    ws.append_row([
        timestamp,
        claude.get("claude_session_pct", ""),
        claude.get("claude_weekly_pct", ""),
        claude.get("claude_design_pct", ""),
        claude.get("claude_routine_used", ""),
        claude.get("claude_extra_spent_usd", ""),
        claude.get("claude_extra_limit_usd", ""),
        claude.get("claude_balance_usd", ""),
    ])


def append_ollama_row(timestamp: str, ollama: dict):
    ws = _get_gc().open_by_key(SPREADSHEET_ID).worksheet(OLLAMA_SHEET)
    headers = ["Timestamp", "Session usage(%)", "Weekly usage(%)"]
    if not ws.row_values(1):
        ws.append_row(headers)
    ws.append_row([
        timestamp,
        ollama.get("ollama_session_pct", ""),
        ollama.get("ollama_weekly_pct", ""),
    ])


# ── Cookie rolling update ──────────────────────────────────────────────────

def roll_cookie_secret(secret_name: str, new_value: str):
    """Updates a GitHub Actions secret with the latest cookie value."""
    if not GH_PAT or not GH_REPO:
        return
    g = Github(GH_PAT)
    repo = g.get_repo(GH_REPO)
    repo.create_secret(secret_name, new_value)


def extract_updated_cookie(response_headers: dict, original_cookie: str) -> str:
    """Merge Set-Cookie headers back into the cookie string."""
    set_cookie = response_headers.get("set-cookie", "")
    if not set_cookie:
        return original_cookie

    updates = {}
    for part in set_cookie.split(","):
        m = re.match(r"\s*([^=]+)=([^;]*)", part.strip())
        if m:
            updates[m.group(1)] = m.group(2)

    parts = dict(
        p.split("=", 1) for p in original_cookie.split("; ") if "=" in p
    )
    parts.update(updates)
    return "; ".join(f"{k}={v}" for k, v in parts.items())


# ── Helpers ────────────────────────────────────────────────────────────────

def _pct(used, limit) -> float:
    if used is None or limit is None or limit == 0:
        return -1.0
    return round(used / limit * 100, 1)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    tz_taipei = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz_taipei).strftime("%Y-%m-%d %H:%M:%S")
    errors = []

    # Claude
    try:
        claude = fetch_claude_usage(CLAUDE_COOKIE)
        print(f"Claude: session={claude['claude_session_pct']}% "
              f"weekly={claude['claude_weekly_pct']}% "
              f"spent=${claude['claude_extra_spent_usd']}")
    except Exception as e:
        print(f"[ERROR] Claude fetch failed: {e}", file=sys.stderr)
        claude = {"claude_session_pct": "ERROR", "claude_weekly_pct": "ERROR",
                  "claude_extra_spent_usd": "ERROR"}
        errors.append(f"Claude: {e}")

    # Ollama
    try:
        ollama = fetch_ollama_usage(OLLAMA_COOKIE)
        print(f"Ollama: session={ollama['ollama_session_pct']}% "
              f"weekly={ollama['ollama_weekly_pct']}%")
    except Exception as e:
        print(f"[ERROR] Ollama fetch failed: {e}", file=sys.stderr)
        ollama = {"ollama_session_pct": "ERROR", "ollama_weekly_pct": "ERROR"}
        errors.append(f"Ollama: {e}")

    # Write to sheets
    append_claude_row(timestamp, claude)
    print(f"Written to Claude sheet")
    append_ollama_row(timestamp, ollama)
    print(f"Written to Ollama sheet")

    if errors:
        # Exit with error code so GitHub Actions marks the run as failed
        print("\nErrors encountered:\n" + "\n".join(errors), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
