"""
Configuration — environment variables and constants.
"""
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Telegram ──
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Polling ──
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "300"))
PROJECTS_PER_PAGE = int(os.getenv("PROJECTS_PER_PAGE", "10"))

# ── Data directory ──
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
SEEN_FILE = os.path.join(DATA_DIR, "seen_projects.json")
MONITOR_FILE = os.path.join(DATA_DIR, "monitor_config.json")
FW_SEEN_FILE = os.path.join(DATA_DIR, "fastwork_seen.json")
FW_MONITOR_FILE = os.path.join(DATA_DIR, "fastwork_monitor.json")
SRIBU_SEEN_FILE = os.path.join(DATA_DIR, "sribu_seen.json")
SRIBU_MONITOR_FILE = os.path.join(DATA_DIR, "sribu_monitor.json")
BUDGET_STATS_FILE = os.path.join(DATA_DIR, "category_budget_stats.json")
CLIENT_STATS_FILE = os.path.join(DATA_DIR, "client_stats.json")
DIGEST_FILE = os.path.join(DATA_DIR, "daily_digest.json")

os.makedirs(DATA_DIR, exist_ok=True)

# ── Telegram API base ──
TG_API = "https://api.telegram.org/bot"


def get_chat_ids() -> list[str]:
    """Parse TELEGRAM_CHAT_ID env var (comma-separated) into a list of chat IDs."""
    raw = TELEGRAM_CHAT_ID
    if not raw:
        return []
    return [cid.strip() for cid in raw.split(",") if cid.strip()]
