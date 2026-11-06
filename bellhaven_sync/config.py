"""Central configuration. Everything can be overridden with environment variables."""
import os

BASE_URL = os.environ.get("BH_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
API_URL = f"{BASE_URL}/api/v1"
TOKEN = os.environ.get("BH_TOKEN", "")
DB_PATH = os.environ.get("BH_DB_PATH", "state/bellhaven.db")
REVIEWER = os.environ.get("BH_REVIEWER", "reviewer")

# The Bellhaven parent account. Detected by name unless overridden.
PARENT_NAME = "Bellhaven Senior Living (Parent Account)"
PARENT_ID_OVERRIDE = os.environ.get("BH_PARENT_ID", "")

# Match scoring (see matcher.score for the signals and their weights).
MATCH_THRESHOLD = 50    # >= this: same facility
REVIEW_THRESHOLD = 30   # between REVIEW and MATCH: ask a human, write nothing structural

# Safety guard: if the website suddenly lists far fewer locations than last run
# (outage, redesign, partial scrape), skip "no longer on website" proposals.
MIN_LOCATION_RATIO = 0.8

NOTE_TAG = "[bellhaven-sync]"


def crm_browser_url(account_id: str) -> str:
    """Deep link into the read-only CRM browser for a given account."""
    if not TOKEN or not account_id:
        return ""
    return f"{BASE_URL}/crm/{TOKEN}/accounts/{account_id}"
