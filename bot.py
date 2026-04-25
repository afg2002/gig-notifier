"""
Interactive Telegram Bot for projects.co.id project notifications.
Features:
- Browse projects by category with inline keyboards
- Pagination (10-15 projects per page)
- Per-category monitoring configuration
- Real-time notifications for new projects
- Beautiful emoji UI
"""

import os
import sys
import json
import logging
import asyncio
import fcntl
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from scraper import (
    scrape_listing,
    CATEGORIES,
    get_category_by_id,
    Project,
)

from fastwork_scraper import (
    scrape_jobs as scrape_fastwork_jobs,
    get_categories as get_fastwork_categories,
    scrape_new_jobs,
    get_jobs_by_tag,
    FastworkJob,
)

from sribu_scraper import (
    scrape_sribu_listing,
    scrape_new_contests,
    get_sribu_categories,
    SribuContest,
    scrape_detail_budget,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "300"))  # default 5 min
PROJECTS_PER_PAGE = int(os.getenv("PROJECTS_PER_PAGE", "10"))
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SEEN_FILE = os.path.join(DATA_DIR, "seen_projects.json")
MONITOR_FILE = os.path.join(DATA_DIR, "monitor_config.json")
FW_SEEN_FILE = os.path.join(DATA_DIR, "fastwork_seen.json")
FW_MONITOR_FILE = os.path.join(DATA_DIR, "fastwork_monitor.json")
SRIBU_SEEN_FILE = os.path.join(DATA_DIR, "sribu_seen.json")
SRIBU_MONITOR_FILE = os.path.join(DATA_DIR, "sribu_monitor.json")

os.makedirs(DATA_DIR, exist_ok=True)

# Telegram API base
TG_API = "https://api.telegram.org/bot"


# ============================================================
# Data Persistence
# ============================================================


class SeenTracker:
    """Persistently tracks which project IDs have been notified."""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self.seen_ids: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                self.seen_ids = set(json.load(f))
            logger.info(f"Loaded {len(self.seen_ids)} seen project IDs")

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump(list(self.seen_ids), f, indent=2)

    def is_seen(self, project_id: str) -> bool:
        return project_id in self.seen_ids

    def mark_seen(self, project_id: str):
        self.seen_ids.add(project_id)
        self._save()


class FastworkSeenTracker:
    """Persistently tracks which Fastwork job IDs have been notified."""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self.seen_ids: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                self.seen_ids = set(json.load(f))
            logger.info(f"Loaded {len(self.seen_ids)} seen Fastwork job IDs")

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump(list(self.seen_ids), f, indent=2)

    def is_seen(self, job_id: str) -> bool:
        return job_id in self.seen_ids

    def mark_seen(self, job_id: str):
        self.seen_ids.add(job_id)
        self._save()


class FastworkMonitorConfig:
    """Manages per-category Fastwork monitoring configuration."""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self.monitored_tags: set[str] = set()  # tag IDs
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                data = json.load(f)
                self.monitored_tags = set(data.get("monitored_tags", []))
            logger.info(f"Loaded Fastwork monitor config: {len(self.monitored_tags)} tags")
        else:
            # Default: monitor all popular categories
            self.monitored_tags = {
                "3327d5e5-7b28-45c8-b552-9da38b3d585d",  # Pengembangan Aplikasi
                "eb7276d1-1b83-454e-83e6-c1ee68f80c0a",  # Pengembangan Website
                "a880a9d4-fe0c-4fad-908c-ca4050c5ebea",  # Pemasaran
                "81f7bcc2-1694-400c-87ec-055243de0e48",  # Bisnis & Keuangan
                "28956f70-de0f-4333-8da6-f8307489c5b5",  # Desain Grafis
            }
            self._save()

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump({"monitored_tags": list(self.monitored_tags)}, f, indent=2)

    def is_monitored(self, tag_id: str) -> bool:
        return tag_id in self.monitored_tags

    def toggle(self, tag_id: str):
        if tag_id in self.monitored_tags:
            self.monitored_tags.discard(tag_id)
        else:
            self.monitored_tags.add(tag_id)
        self._save()


class SribuSeenTracker:
    """Persistently tracks which Sribu contest IDs have been notified."""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self.seen_ids: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                self.seen_ids = set(json.load(f))
            logger.info(f"Loaded {len(self.seen_ids)} seen Sribu contest IDs")

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump(list(self.seen_ids), f, indent=2)

    def is_seen(self, contest_id: str) -> bool:
        return contest_id in self.seen_ids

    def mark_seen(self, contest_id: str):
        self.seen_ids.add(contest_id)
        self._save()


class SribuMonitorConfig:
    """Manages per-category Sribu monitoring configuration."""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self.monitored_categories: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                data = json.load(f)
                self.monitored_categories = set(data.get("categories", []))
            logger.info(f"Loaded Sribu monitor config: {len(self.monitored_categories)} categories")
        else:
            # Default: monitor website & programming + logo & branding
            self.monitored_categories = {
                "f9e36e5f-d6f9-4c1a-b5d4-e9f8c8a3b7d2",  # Website & Programming
                "1ef818a5-3a17-4dd1-80e2-685cf3da5946",  # Logo & Branding
            }
            self._save()

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump({"categories": list(self.monitored_categories)}, f, indent=2)

    def is_monitored(self, category_id: str) -> bool:
        return category_id in self.monitored_categories

    def toggle(self, category_id: str):
        if category_id in self.monitored_categories:
            self.monitored_categories.discard(category_id)
        else:
            self.monitored_categories.add(category_id)
        self._save()


class MonitorConfig:
    """Manages per-category monitoring configuration."""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self.monitored_categories: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                data = json.load(f)
                self.monitored_categories = set(data.get("categories", []))
            logger.info(f"Loaded monitor config: {self.monitored_categories}")

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump({"categories": list(self.monitored_categories)}, f, indent=2)

    def is_monitored(self, category_id: str) -> bool:
        return category_id in self.monitored_categories

    def toggle(self, category_id: str) -> bool:
        """Toggle monitoring for a category. Returns new state."""
        if category_id in self.monitored_categories:
            self.monitored_categories.discard(category_id)
            self._save()
            return False
        else:
            self.monitored_categories.add(category_id)
            self._save()
            return True

    def is_monitored(self, category_id: str) -> bool:
        return category_id in self.monitored_categories


# ============================================================
# Competitive Intel: Budget Comparison
# ============================================================

import re

BUDGET_FILE = os.path.join(DATA_DIR, "category_budget_stats.json")


def _parse_budget(budget_str: str) -> float | None:
    """Extract numeric budget value from string like 'Rp 500.000 - 1.000.000' or 'Rp 500rb'."""
    if not budget_str or budget_str == "-":
        return None
    # Take first number found
    nums = re.findall(r"[\d']+\.?\d*", budget_str.replace(".", "").replace("'", ""))
    if not nums:
        return None
    try:
        val = float("".join(nums[:3]))  # take up to 3 digit groups
        # Handle "500rb" -> 500000
        if "rb" in budget_str.lower() and val < 10000:
            val *= 1000
        return val
    except ValueError:
        return None


def _load_budget_stats() -> dict:
    if os.path.exists(BUDGET_FILE):
        with open(BUDGET_FILE) as f:
            return json.load(f)
    return {}


def _save_budget_stats(stats: dict):
    with open(BUDGET_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def update_budget_stats(project: Project):
    """Update rolling budget stats when new project is scraped."""
    if not project.budget or project.budget == "-":
        return
    stats = _load_budget_stats()
    cat_id = "general"
    budget_val = _parse_budget(project.budget)
    if not budget_val:
        return

    if cat_id not in stats:
        stats[cat_id] = {"values": [], "count": 0}
    stats[cat_id]["values"].append(budget_val)
    stats[cat_id]["count"] += 1
    # Keep rolling window of last 100 values
    if len(stats[cat_id]["values"]) > 100:
        stats[cat_id]["values"] = stats[cat_id]["values"][-100:]
    _save_budget_stats(stats)


def get_budget_comparison(budget_str: str) -> str:
    """Return emoji + text comparing budget to rolling category average."""
    val = _parse_budget(budget_str)
    if not val:
        return ""
    stats = _load_budget_stats()
    general = stats.get("general", {"values": []})
    if not general["values"]:
        return ""
    avg = sum(general["values"]) / len(general["values"])
    ratio = val / avg if avg > 0 else 1.0
    if ratio >= 1.5:
        return f"💎 <b>Above avg {ratio:.1f}x!</b>"
    elif ratio >= 1.2:
        return f"✅ Above avg ({ratio:.1f}x)"
    elif ratio >= 0.8:
        return f"📊 ~avg"
    else:
        return f"⚠️ Below avg ({ratio:.1f}x)"


# ============================================================
# Client Reputation Tracker
# ============================================================

CLIENT_FILE = os.path.join(DATA_DIR, "client_stats.json")


def _load_client_stats() -> dict:
    if os.path.exists(CLIENT_FILE):
        with open(CLIENT_FILE) as f:
            return json.load(f)
    return {}


def _save_client_stats(stats: dict):
    with open(CLIENT_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def update_client_stats(project: Project):
    """Record project from a client owner."""
    if not project.owner_name or project.owner_name == "Unknown":
        return
    stats = _load_client_stats()
    key = project.owner_name
    if key not in stats:
        stats[key] = {
            "project_count": 0,
            "total_budget": 0.0,
            "projects": [],
            "first_seen": project.published_date or "",
            "last_seen": "",
        }
    stats[key]["project_count"] += 1
    bval = _parse_budget(project.budget) or 0
    if bval > 0:
        stats[key]["total_budget"] += bval
    stats[key]["last_seen"] = project.published_date or ""
    # Keep last 20 projects per client
    stats[key]["projects"].append({
        "id": project.project_id,
        "title": project.title[:60],
        "budget": project.budget,
        "date": project.published_date or "",
    })
    if len(stats[key]["projects"]) > 20:
        stats[key]["projects"] = stats[key]["projects"][-20:]
    _save_client_stats(stats)


def get_client_reputation(owner_name: str) -> str:
    """Return emoji + short reputation line for client."""
    if not owner_name or owner_name == "Unknown":
        return "❓ New client"
    stats = _load_client_stats()
    client = stats.get(owner_name)
    if not client:
        return "❓ New client"
    count = client["project_count"]
    avg_budget = (client["total_budget"] / count) if count > 0 and client["total_budget"] > 0 else 0
    if count >= 10:
        return f"🏆 Veteran ({count} projects, avg {avg_budget:,.0f})"
    elif count >= 5:
        return f"⭐ Regular ({count} projects)"
    else:
        return f"👤 Known ({count} project{'s' if count > 1 else ''})"


# ============================================================
# Daily Digest Tracker
# ============================================================

DIGEST_FILE = os.path.join(DATA_DIR, "daily_digest.json")


def _load_digest() -> dict:
    if os.path.exists(DIGEST_FILE):
        with open(DIGEST_FILE) as f:
            return json.load(f)
    return {}


def _save_digest(digest: dict):
    with open(DIGEST_FILE, "w") as f:
        json.dump(digest, f, indent=2)


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def record_digest_project(project: Project, category_name: str):
    """Record a newly notified project in today's digest."""
    digest = _load_digest()
    today = _today_key()
    if today not in digest:
        digest[today] = {"projects": [], "sent": False}
    # Avoid duplicates
    existing = {p["id"] for p in digest[today]["projects"]}
    if project.project_id not in existing:
        budget_val = _parse_budget(project.budget) or 0
        digest[today]["projects"].append({
            "id": project.project_id,
            "title": project.title[:80],
            "description": project.description[:200],
            "budget": project.budget,
            "budget_val": budget_val,
            "category": category_name,
            "bid_count": project.bid_count or "0",
            "owner": project.owner_name,
            "link": project.link,
            "published": project.published_date or "",
        })
    _save_digest(digest)


def get_daily_digest_text() -> str:
    """Build formatted digest message for today."""
    digest = _load_digest()
    today = _today_key()
    today_data = digest.get(today, {"projects": []})
    projects = today_data.get("projects", [])

    if not projects:
        return None

    # Group by category
    by_cat: dict[str, list] = {}
    for p in projects:
        cat = p["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(p)

    lines = [
        f"📊 <b>Daily Digest</b> — {today}\n",
        f"🆕 {len(projects)} project baru hari ini\n",
    ]

    for cat, projs in by_cat.items():
        lines.append(f"\n{cat}:")
        for p in projs[:5]:
            bids = int(p["bid_count"]) if p["bid_count"].isdigit() else 0
            bid_emoji = "🔥" if bids > 20 else "👥" if bids > 5 else "🆕"
            budget_cmp = get_budget_comparison(p["budget"])
            cmp_txt = f" {budget_cmp}" if budget_cmp else ""
            lines.append(
                f"  ▸ {p['title']}\n"
                f"    💰 {p['budget']}{cmp_txt}  •  {bid_emoji} {p['bid_count']} bids"
            )

    return "\n".join(lines)
# Telegram API Helpers
# ============================================================


async def tg_request(token: str, method: str, payload: dict) -> dict:
    """Make a request to Telegram Bot API using stdlib urllib."""
    import urllib.request
    import urllib.error

    url = f"{TG_API}{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "TelegramBot/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error_code": e.code, "description": f"HTTP {e.code}"}
    except Exception as e:
        logger.error(f"Telegram request error: {e}")
        return {"ok": False, "error": str(e)}


async def send_message(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str = "HTML",
) -> dict:
    """Send a message with optional inline keyboard."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    result = await tg_request(token, "sendMessage", payload)
    if not result.get("ok"):
        logger.error(f"Telegram API error: {result}")
    return result


async def edit_message(
    token: str,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str = "HTML",
) -> dict:
    """Edit an existing message."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    result = await tg_request(token, "editMessageText", payload)
    if not result.get("ok"):
        logger.error(f"Telegram API edit error: {result}")
    return result


async def answer_callback(token: str, callback_query_id: str, text: str = "") -> dict:
    """Answer a callback query (show popup notification)."""
    payload = {
        "callback_query_id": callback_query_id,
    }
    if text:
        payload["text"] = text
    return await tg_request(token, "answerCallbackQuery", payload)


# ============================================================
# Message Formatters
# ============================================================


def format_project_card(project: Project, index: int = 0) -> str:
    """Format a single project as a beautiful card with emojis."""
    weekly_emoji = "✅" if project.need_weekly_report == "Yes" else "❌"
    bid_count = int(project.bid_count) if project.bid_count.isdigit() else 0
    bid_emoji = "🔥" if bid_count > 20 else "👥" if bid_count > 5 else "🆕"

    card = (
        f"{'─' * 30}\n"
        f"<b>#{index + 1} {project.title}</b>\n\n"
        f"📝 <i>{_truncate(project.description, 300)}</i>\n\n"
        f"💰 <b>Budget:</b> {project.budget or '-'}\n"
        f"📅 <b>Published:</b> {project.published_date or '-'}\n"
        f"⏰ <b>Deadline:</b> {project.deadline or '-'}\n"
        f"📆 <b>Finish:</b> {project.finish_days or '-'} hari\n"
        f"📊 <b>Status:</b> {project.status or '-'}\n"
        f"{bid_emoji} <b>Bids:</b> {project.bid_count or '0'}\n"
        f"📄 <b>Weekly Report:</b> {weekly_emoji} {project.need_weekly_report}\n"
    )

    if project.tags:
        card += f"🏷️ <b>Tags:</b> {', '.join(project.tags)}\n"

    card += f"👤 <b>Owner:</b> {project.owner_name}\n"

    return card


def format_project_list(
    projects: list[Project], category: dict, page: int, total_pages: int
) -> str:
    """Format a paginated list of projects."""
    cat_emoji = category.get("emoji", "📋")
    cat_name = category.get("name", "All")

    header = (
        f"{cat_emoji} <b>{cat_name}</b> — Page {page}/{total_pages}\n"
        f"📊 {len(projects)} projects ditemukan\n"
    )

    items = []
    for i, p in enumerate(projects):
        bid_count = int(p.bid_count) if p.bid_count and p.bid_count.isdigit() else 0
        bid_emoji = "🔥" if bid_count > 20 else "👥" if bid_count > 5 else "🆕"
        budget_short = p.budget or "N/A"
        items.append(
            f"<b>#{i + 1}</b> {p.title}\n"
            f"   💰 {budget_short}\n"
            f"   {bid_emoji} {p.bid_count or '0'} bids  •  "
            f"📅 {p.published_date or '-'}"
        )

    return header + "\n\n".join(items)


def format_monitor_status(monitor: MonitorConfig) -> str:
    """Format monitoring configuration status."""
    lines = ["🔔 <b>Monitoring Configuration</b>\n"]

    for cat in CATEGORIES:
        status = "✅ ON" if monitor.is_monitored(cat["id"]) else "⬜ OFF"
        lines.append(f"{cat['emoji']} {cat['name']}: <b>{status}</b>")

    lines.append(
        f"\n{'─' * 30}\n"
        f"⏱️ Polling interval: <b>{POLL_INTERVAL_SECONDS}s</b>\n"
        f"📊 Monitored: <b>{len(monitor.monitored_categories)}</b> categories"
    )

    return "\n".join(lines)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max length."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def _is_published_today(published_date: str) -> bool:
    """Check if a project was published today.
    Expected format: 'DD/MM/YYYY HH:MM:SS WIB'"""
    if not published_date:
        return False
    try:
        date_part = published_date.split(" ")[0]  # "05/04/2026"
        day, month, year = date_part.split("/")
        from datetime import date

        pub_date = date(int(year), int(month), int(day))
        return pub_date == date.today()
    except (ValueError, IndexError):
        return False


# ============================================================
# Inline Keyboard Builders
# ============================================================


def build_main_menu_keyboard() -> dict:
    """Build the main menu inline keyboard — source selector."""
    return {
        "inline_keyboard": [
            [{"text": "🌐 Projects.co.id", "callback_data": "src:projects"}],
            [{"text": "⚡ Fastwork.id", "callback_data": "src:fastwork"}],
            [{"text": "🎨 Sribu.com", "callback_data": "src:sribu"}],
            [{"text": "🔙 Back", "callback_data": "menu:back"}],
        ]
    }


def build_platform_submenu(source: str) -> dict:
    """Build sub-menu for a specific platform."""
    if source == "projects":
        return {
            "inline_keyboard": [
                [{"text": "📋 Browse Projects", "callback_data": "menu:browse"}],
                [{"text": "🔔 Monitor Settings", "callback_data": "menu:monitor"}],
                [{"text": "🔄 Refresh Now", "callback_data": "menu:refresh"}],
                [{"text": "ℹ️ Help", "callback_data": "menu:help"}],
                [{"text": "🔙 Back to Sources", "callback_data": "src:back"}],
            ]
        }
    elif source == "fastwork":
        return {
            "inline_keyboard": [
                [{"text": "📋 Browse Fastwork Jobs", "callback_data": "fw:browse"}],
                [{"text": "🔔 Monitor Fastwork", "callback_data": "fw:monitor"}],
                [{"text": "🔄 Refresh Fastwork", "callback_data": "fw:refresh"}],
                [{"text": "🔙 Back to Sources", "callback_data": "src:back"}],
            ]
        }
    elif source == "sribu":
        return {
            "inline_keyboard": [
                [{"text": "📋 Browse Contests", "callback_data": "sribu:browse"}],
                [{"text": "🔔 Monitor Sribu", "callback_data": "sribu:monitor"}],
                [{"text": "🔄 Refresh Sribu", "callback_data": "sribu:refresh"}],
                [{"text": "🔙 Back to Sources", "callback_data": "src:back"}],
            ]
        }


def build_category_keyboard() -> dict:
    """Build category selection keyboard (2 columns)."""
    buttons = []
    row = []
    for cat in CATEGORIES:
        row.append(
            {
                "text": f"{cat['emoji']} {cat['name']}",
                "callback_data": f"cat:{cat['id']}",
            }
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([{"text": "🔙 Back to Menu", "callback_data": "menu:back"}])

    return {"inline_keyboard": buttons}


def build_project_list_keyboard(category_id: str, page: int, total_pages: int) -> dict:
    """Build pagination + project detail keyboard."""
    buttons = []

    # Pagination row
    nav_row = []
    if page > 1:
        nav_row.append(
            {"text": "⬅️ Prev", "callback_data": f"page:{category_id}:{page - 1}"}
        )
    nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": "noop"})
    if page < total_pages:
        nav_row.append(
            {"text": "Next ➡️", "callback_data": f"page:{category_id}:{page + 1}"}
        )
    buttons.append(nav_row)

    # Project detail buttons (show first 5)
    # We use project index as identifier
    # Note: We'll store projects in memory for callback resolution

    buttons.append([{"text": "🔙 Categories", "callback_data": f"catlist"}])
    buttons.append([{"text": "🏠 Main Menu", "callback_data": "menu:back"}])

    return {"inline_keyboard": buttons}


def build_monitor_keyboard(monitor: MonitorConfig) -> dict:
    """Build monitoring toggle keyboard."""
    buttons = []
    row = []
    for cat in CATEGORIES:
        is_on = monitor.is_monitored(cat["id"])
        status = "✅" if is_on else "⬜"
        row.append(
            {
                "text": f"{status} {cat['emoji']} {cat['name']}",
                "callback_data": f"mon:{cat['id']}",
            }
        )
        if len(row) == 1:  # 1 column for readability
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([{"text": "🔙 Back to Menu", "callback_data": "menu:back"}])

    return {"inline_keyboard": buttons}


def build_fastwork_monitor_keyboard(fw_monitor: FastworkMonitorConfig) -> dict:
    """Build Fastwork monitoring toggle keyboard."""
    cats = get_fastwork_categories()
    buttons = []
    row = []
    for cat in cats:
        is_on = fw_monitor.is_monitored(cat["id"])
        status = "✅" if is_on else "⬜"
        row.append({
            "text": f"{status} {cat['name']}",
            "callback_data": f"fwmon:{cat['id']}",
        })
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([{"text": "🔙 Back to Fastwork", "callback_data": "src:fastwork"}])

    return {"inline_keyboard": buttons}


# ============================================================
# Sribu Message Formatters
# ============================================================


def _build_sribu_detail_keyboard(contest: SribuContest) -> dict:
    """Build a keyboard with a View button for a Sribu contest."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔗 View Contest",
                    "url": contest.contest_url,
                }
            ],
            [
                {"text": "🔙 Back to Contests", "callback_data": "sribu_cat:all:1"},
            ],
        ]
    }


def format_sribu_contest_card(contest: SribuContest, index: int = 0) -> str:
    """Format a single Sribu contest as a detailed card."""
    tags_str = ", ".join(contest.tags[:5]) if contest.tags else "-"
    budget_str = contest.budget or contest.budget_raw or "Scraping..."

    card_lines = [
        f"🎨 <b>#{index + 1} {contest.title}</b>\n",
        f"📝 <i>{_truncate(contest.description, 300) if contest.description else '(Tidak ada deskripsi)'}</i>\n",
        f"💰 <b>Budget:</b> {budget_str}\n",
        f"📂 <b>Kategori:</b> {contest.category_emoji} {contest.category_name}\n",
        f"📅 <b>Deadline:</b> {contest.deadline_formatted}\n",
        f"📊 <b>Status:</b> {contest.status_label}\n",
    ]

    if tags_str and tags_str != "-":
        card_lines.append(f"🏷️ <b>Tags:</b> {tags_str}\n")

    return "".join(card_lines)


def format_sribu_contests_list(
    contests: list[SribuContest], category: str, page: int, total_pages: int
) -> str:
    """Format a paginated list of Sribu contests."""
    header = (
        f"🎨 <b>{category}</b> — Page {page}/{total_pages}\n"
        f"📊 {len(contests)} contests ditemukan\n"
    )

    items = []
    for i, c in enumerate(contests):
        budget_str = c.budget or c.budget_raw or "-"
        items.append(
            f"<b>#{i + 1}</b> {c.title}\n"
            f"   💰 {budget_str}  •  📅 {c.deadline_formatted}  •  "
            f"{c.category_emoji} {c.category_name}\n"
            f"   📊 {c.status_label}  •  🏷️ {', '.join(c.tags[:2]) if c.tags else '-'}\n"
        )

    return header + "\n\n".join(items)


# ============================================================
# Fastwork Message Formatters
# ============================================================


def _truncate(text: str, length: int) -> str:
    """Truncate text to length chars, adding ellipsis if needed."""
    if not text:
        return ""
    return text[:length] + ("..." if len(text) > length else "")


def _build_fastwork_detail_keyboard(job: FastworkJob) -> dict:
    """Build a keyboard with a View button for a Fastwork job."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔗 View Full Job",
                    "url": job.link,
                }
            ],
            [
                {"text": "🔙 Back to Jobs", "callback_data": f"fwcat:{job.tag_id}:1"}
            ],
        ]
    }


def format_fastwork_job_card(job: FastworkJob, index: int = 0) -> str:
    """Format a single Fastwork job as a detailed card (matching Projects.co.id style)."""
    offers_emoji = "🔥" if job.offers_count > 10 else "👥" if job.offers_count > 0 else "🆕"
    type_emoji = "💻" if job.type == "freelance" else "⏰" if job.type == "contract" else "🌐"

    # Status badge
    status_map = {
        "open": "🟢 Open",
        "closed": "🔴 Closed",
        "in_progress": "🟡 In Progress",
        "completed": "✅ Completed",
    }
    status_text = status_map.get(job.status.lower() if job.status else "", f"📊 {job.status}") if job.status else "📊 Unknown"

    card_lines = [
        f"⚡ <b>#{index + 1} {job.title}</b>\n",
        f"📝 <i>{_truncate(job.description, 300)}</i>\n",
        f"💰 <b>Budget:</b> {job.budget}\n",
        f"📂 <b>Category:</b> {job.tag_name}\n",
        f"🏷️ <b>Type:</b> {type_emoji} {job.type.capitalize() if job.type else '-'}\n",
        f"📅 <b>Published:</b> {job.published_date}\n",
        f"📊 <b>Status:</b> {status_text}\n",
        f"{offers_emoji} <b>Offers:</b> {job.offers_count}\n",
    ]

    if job.skills:
        skills_str = ", ".join(job.skills[:8])
        if len(job.skills) > 8:
            skills_str += f" +{len(job.skills) - 8} more"
        card_lines.append(f"🛠️ <b>Skills:</b> {skills_str}\n")

    if job.client_name:
        card_lines.append(f"👤 <b>Client:</b> {job.client_name}\n")

    return "".join(card_lines)


def format_fastwork_jobs_list(
    jobs: list[FastworkJob], category: str, page: int, total_pages: int
) -> str:
    """Format a paginated list of Fastwork jobs (matching Projects.co.id list style)."""
    header = (
        f"⚡ <b>{category}</b> — Page {page}/{total_pages}\n"
        f"📊 {len(jobs)} jobs ditemukan\n"
    )

    items = []
    for i, job in enumerate(jobs):
        offers_emoji = "🔥" if job.offers_count > 10 else "👥" if job.offers_count > 0 else "🆕"
        type_emoji = "💻" if job.type == "freelance" else "⏰" if job.type == "contract" else "🌐"
        budget_short = job.budget or "N/A"
        items.append(
            f"<b>#{i + 1}</b> {job.title}\n"
            f"   💰 {budget_short}  •  {type_emoji} {job.type or '-'}  •  "
            f"{offers_emoji} {job.offers_count} offers\n"
            f"   📂 {job.tag_name}  •  📅 {job.published_date}"
        )

    return header + "\n\n".join(items)


def format_fastwork_jobs_notification(jobs: list[FastworkJob], category: str = None) -> str:
    """Format a Fastwork notification message for new jobs (rich card style)."""
    cat_emoji = "⚡"
    lines = [
        f"⚡ <b>{len(jobs)} Fastwork Job Baru</b>"
        f"{f' di {category}' if category else ''}!\n\n"
    ]
    for i, job in enumerate(jobs[:5]):
        lines.append(format_fastwork_job_card(job, i))
        lines.append("")

    return "\n".join(lines)


# Project Cache (for pagination callbacks)
# ============================================================


class FastworkJobCache:
    """Cache Fastwork jobs per tag+page for detail view resolution."""

    def __init__(self):
        self._cache: dict[str, list[FastworkJob]] = {}

    def store(self, tag_id: str, page: int, jobs: list[FastworkJob]):
        key = f"{tag_id}:{page}"
        self._cache[key] = jobs

    def get(self, tag_id: str, page: int) -> list[FastworkJob]:
        key = f"{tag_id}:{page}"
        return self._cache.get(key, [])

    def clear(self):
        self._cache.clear()


class SribuContestCache:
    """Cache Sribu contests per category+page for detail view resolution."""

    def __init__(self):
        self._cache: dict[str, list[SribuContest]] = {}

    def store(self, category_id: str, page: int, contests: list[SribuContest]):
        key = f"{category_id}:{page}"
        self._cache[key] = contests

    def get(self, category_id: str, page: int) -> list[SribuContest]:
        key = f"{category_id}:{page}"
        return self._cache.get(key, [])


class ProjectCache:
    """Cache projects per category+page for callback resolution."""

    def __init__(self):
        self._cache: dict[str, list[Project]] = {}

    def store(self, category_id: str, page: int, projects: list[Project]):
        key = f"{category_id}:{page}"
        self._cache[key] = projects

    def get(self, category_id: str, page: int) -> list[Project]:
        key = f"{category_id}:{page}"
        return self._cache.get(key, [])

    def clear(self):
        self._cache.clear()


# ============================================================
# Bot Handler
# ============================================================


class ProjectsBot:
    """Interactive Telegram bot for projects.co.id."""

    def __init__(self):
        self.tracker = SeenTracker(SEEN_FILE)
        self.monitor = MonitorConfig(MONITOR_FILE)
        self.fw_tracker = FastworkSeenTracker(FW_SEEN_FILE)
        self.fw_monitor = FastworkMonitorConfig(FW_MONITOR_FILE)
        self.sribu_tracker = SribuSeenTracker(SRIBU_SEEN_FILE)
        self.sribu_monitor = SribuMonitorConfig(SRIBU_MONITOR_FILE)
        self.sribu_cache = SribuContestCache()
        self.cache = ProjectCache()
        self.fw_cache = FastworkJobCache()
        self._running = False

    async def handle_update(self, update: dict):
        """Route incoming updates to appropriate handlers."""
        # Handle callback queries (inline button presses)
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return

        # Handle regular messages
        if "message" in update:
            await self._handle_message(update["message"])
            return

    async def _handle_message(self, message: dict):
        """Handle regular text messages."""
        chat_id = str(message["chat"]["id"])
        text = message.get("text", "").strip()

        if text == "/start":
            await self._cmd_start(chat_id)
        elif text == "/browse":
            await self._cmd_browse(chat_id)
        elif text == "/monitor":
            await self._cmd_monitor(chat_id)
        elif text == "/refresh":
            await self._cmd_refresh(chat_id)
        elif text == "/help":
            await self._cmd_help(chat_id)
        elif text == "/status":
            await self._cmd_status(chat_id)
        elif text == "/digest":
            await self._cmd_digest(chat_id)
        elif text == "/topclients":
            await self._cmd_top_clients(chat_id)
        elif text in ("/fw", "/fastwork"):
            await self._cmd_fastwork(chat_id)
        elif text in ("/sribu", "/contest"):
            await self._cmd_sribu(chat_id)
        else:
            await send_message(
                TELEGRAM_BOT_TOKEN,
                chat_id,
                "👋 Gunakan command berikut:\n\n"
                "/start — Menu utama\n"
                "/browse — Browse project per kategori\n"
                "/monitor — Atur monitoring kategori\n"
                "/refresh — Refresh project terbaru\n"
                "/status — Status monitoring\n"
                "/digest — Ringkasan project hari ini\n"
                "/topclients — Top 10 client terbanyak\n"
                "/fw — Browse Fastwork jobs\n"
                "/help — Bantuan",
                reply_markup=build_main_menu_keyboard(),
            )

    async def _handle_callback(self, callback: dict):
        """Handle inline keyboard callback queries."""
        chat_id = str(callback["message"]["chat"]["id"])
        message_id = callback["message"]["message_id"]
        data = callback.get("data", "")
        callback_id = callback["id"]

        # Parse callback data
        parts = data.split(":")
        action = parts[0]

        try:
            if action == "menu":
                await self._cb_menu(chat_id, message_id, parts[1], callback_id)
            elif action == "cat":
                await self._cb_category(chat_id, message_id, parts[1], callback_id)
            elif action == "page":
                category_id = parts[1]
                page = int(parts[2])
                await self._cb_page(chat_id, message_id, category_id, page, callback_id)
            elif action == "proj":
                index = int(parts[1])
                category_id = parts[2]
                page = int(parts[3])
                await self._cb_project_detail(
                    chat_id, message_id, index, category_id, page, callback_id
                )
            elif action == "mon":
                await self._cb_monitor_toggle(
                    chat_id, message_id, parts[1], callback_id
                )
            elif action == "catlist":
                await self._cb_category_list(chat_id, message_id, callback_id)
            elif action == "noop":
                await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)
            elif action == "fwcat":
                tag_id = parts[1]
                page = int(parts[2])
                await self._fw_show_page(chat_id, message_id, tag_id, page)
            elif action == "fwdetail":
                tag_id = parts[1]
                page = int(parts[2])
                job_idx = int(parts[3])
                await self._fw_show_detail(chat_id, message_id, tag_id, page, job_idx)
            elif action == "fw":
                await self._cb_fastwork(chat_id, message_id, parts[1], callback_id)
            elif action == "fwmon":
                tag_id = parts[1]
                self.fw_monitor.toggle(tag_id)
                await self._fw_monitor(chat_id, message_id, callback_id)
            elif action == "src":
                await self._cb_source_select(chat_id, message_id, parts[1], callback_id)
            elif action == "sribu":
                await self._cb_sribu(chat_id, message_id, parts[1], callback_id)
            elif action == "sribu_cat":
                cat_id = parts[1]
                page = int(parts[2])
                await self._sribu_show_page(chat_id, message_id, cat_id, page)
            elif action == "sribu_detail":
                cat_id = parts[1]
                page = int(parts[2])
                contest_idx = int(parts[3])
                await self._sribu_show_detail(chat_id, message_id, cat_id, page, contest_idx)
            elif action == "sribu_mon":
                cat_id = parts[1]
                self.sribu_monitor.toggle(cat_id)
                await self._sribu_monitor(chat_id, message_id, callback_id)
        except Exception as e:
            logger.error(f"Callback handler error: {e}")
            await answer_callback(
                TELEGRAM_BOT_TOKEN, callback_id, text="⚠️ Terjadi error, coba lagi."
            )

    # ---- Command Handlers ----

    async def _cmd_start(self, chat_id: str):
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            "🤖 <b>Freelance Monitor Bot</b>\n\n"
            "Pantau project freelance dari 3 sumber:\n"
            "🌐 <b>Projects.co.id</b> — Web dev, mobile, data entry, dll\n"
            "⚡ <b>Fastwork.id</b> — Desain, UX/UI, fotografi, dll\n"
            "🎨 <b>Sribu.com</b> — Logo, branding, kemasan, desain\n\n"
            "✨ <b>Fitur:</b>\n"
            "• 📋 Browse project per kategori (tiap sumber)\n"
            "• 🔔 Auto-notifikasi project baru (dengan intel)\n"
            "• 📄 Pagination (10 project/halaman)\n"
            "• ⚙️ Konfigurasi monitoring per kategori\n"
            "• 🧠 Competitive intel & client reputation\n\n"
            "Pilih sumber di bawah untuk mulai! 👇",
            reply_markup=build_main_menu_keyboard(),
        )

    async def _cmd_browse(self, chat_id: str):
        await self._cb_category_list(chat_id, None, None)

    async def _cmd_monitor(self, chat_id: str):
        status_text = format_monitor_status(self.monitor)
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            status_text,
            reply_markup=build_monitor_keyboard(self.monitor),
        )

    async def _cmd_refresh(self, chat_id: str):
        msg = await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            "🔄 <b>Refreshing...</b>\nSedang mengambil project terbaru...",
        )

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=2) as executor:
            projects = await loop.run_in_executor(executor, scrape_listing, "all", 1)

        new_projects = [p for p in projects if not self.tracker.is_seen(p.project_id)]

        if new_projects:
            text = f"🆕 <b>{len(new_projects)} Project Baru Ditemukan!</b>\n\n"
            for i, p in enumerate(reversed(new_projects[:10])):
                text += format_project_card(p, i)
                text += "\n"
                self.tracker.mark_seen(p.project_id)

            if len(new_projects) > 10:
                text += f"\n...dan {len(new_projects) - 10} project lainnya."

            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                msg["result"]["message_id"],
                text,
                reply_markup=build_main_menu_keyboard(),
            )
        else:
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                msg["result"]["message_id"],
                "✅ <b>Tidak ada project baru</b>\nSemua project sudah di-notifikasi.",
                reply_markup=build_main_menu_keyboard(),
            )

    async def _cmd_help(self, chat_id: str):
        await self._cmd_help_text(chat_id)

    async def _cmd_help_text(self, chat_id: str):
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            "📖 <b>Bantuan</b>\n\n"
            "<b>Commands:</b>\n"
            "/start — Menu utama\n"
            "/browse — Browse project per kategori\n"
            "/monitor — Atur monitoring kategori\n"
            "/refresh — Refresh & cek project baru\n"
            "/status — Status monitoring saat ini\n"
            "/digest — Ringkasan project hari ini\n"
            "/topclients — Top 10 client terbanyak\n"
            "/help — Bantuan ini\n\n"
            "<b>Fitur Cerdas:</b>\n"
            "🧠 Competitive Intel — bandingkan budget dengan rata-rata kategori\n"
            "👤 Client Reputation — info client sebelumnya (Veteran/Regular/Known)\n"
            "📊 Daily Digest — ringkasan harian project baru\n\n"
            "<b>Cara Pakai:</b>\n"
            "1️⃣ /browse → Pilih kategori → Lihat project\n"
            "2️⃣ /monitor → Toggle kategori yang mau dipantau\n"
            "3️⃣ Bot akan auto-notifikasi kalau ada project baru (dengan intel)\n\n"
            "<b>Config:</b>\n"
            "Set <code>POLL_INTERVAL</code> di .env untuk ubah frekuensi polling (default: 300s)\n"
            "Set <code>PROJECTS_PER_PAGE</code> untuk ubah jumlah project per halaman (default: 10)",
            reply_markup=build_main_menu_keyboard(),
        )

    async def _cmd_status(self, chat_id: str):
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            format_monitor_status(self.monitor),
            reply_markup=build_main_menu_keyboard(),
        )

    async def _cmd_fastwork(self, chat_id: str):
        """Handle /fw command — browse Fastwork jobs."""
        cats = get_fastwork_categories()
        buttons = []
        row = []
        for cat in cats:
            row.append({
                "text": cat["name"],
                "callback_data": f"fwcat:{cat['id']}:1",
            })
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([{"text": "🔙 Back to Sources", "callback_data": "src:back"}])

        cat_text = "\n".join([f"• {c['name']}" for c in cats[:14]])
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            f"⚡ <b>Fastwork Categories</b>\n\n{cat_text}\n\nPilih kategori:",
            reply_markup={"inline_keyboard": buttons},
        )

    async def _cmd_sribu(self, chat_id: str):
        """Handle /sribu command — browse Sribu contests."""
        cats = get_sribu_categories()
        if not cats:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "🎨 <b>Sribu Categories</b>\n\nTidak ada kategori ditemukan.",
                reply_markup=build_platform_submenu("sribu"),
            )
            return

        buttons = []
        row = []
        # Add "All" button
        row.append({"text": "🌐 Semua Kategori", "callback_data": "sribu_cat:all:1"})
        if len(row) == 2:
            buttons.append(row)
            row = []

        for cat in cats:
            row.append({
                "text": f"{cat['emoji']} {cat['name']}",
                "callback_data": f"sribu_cat:{cat['id']}:1",
            })
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([{"text": "🔙 Back to Sources", "callback_data": "src:back"}])

        cat_text = "\n".join([f"• {c['emoji']} {c['name']}" for c in cats[:8]])
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            f"🎨 <b>Sribu Categories</b>\n\n{cat_text}\n\nPilih kategori:",
            reply_markup={"inline_keyboard": buttons},
        )

    async def _cmd_digest(self, chat_id: str):
        """Send today's daily digest manually."""
        text = get_daily_digest_text()
        if text:
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, text)
        else:
            await send_message(
                TELEGRAM_BOT_TOKEN,
                chat_id,
                "📭 Belum ada project baru hari ini. Check lagi nanti!",
            )

    async def _cmd_top_clients(self, chat_id: str):
        """Show top clients by project count."""
        stats = _load_client_stats()
        if not stats:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id, "📭 Belum ada data client. Belum ada yang dimonitor."
            )
            return

        # Sort by project count desc
        sorted_clients = sorted(
            stats.items(), key=lambda x: x[1]["project_count"], reverse=True
        )[:10]

        lines = ["🏆 <b>Top Clients</b> (by project count)\n"]
        for name, data in sorted_clients:
            count = data["project_count"]
            avg = data["total_budget"] / count if count > 0 and data["total_budget"] > 0 else 0
            lines.append(
                f"• <b>{name}</b>\n"
                f"  {count} projects  •  avg budget Rp {avg:,.0f}"
            )

        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id, "\n".join(lines)
        )

    # ---- Source Selection (Projects vs Fastwork) ----

    async def _cb_source_select(
        self, chat_id: str, message_id: int, action: str, callback_id: str
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)
        if action == "back":
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                "🤖 <b>Freelance Monitor Bot</b>\n\n"
                "🌐 <b>Projects.co.id</b> — Web dev, mobile, data entry, dll\n"
                "⚡ <b>Fastwork.id</b> — Desain, UX/UI, fotografi, dll\n"
                "🎨 <b>Sribu.com</b> — Logo, branding, kemasan, desain\n\n"
                "Pilih sumber:",
                reply_markup=build_main_menu_keyboard(),
            )
        elif action in ("projects", "fastwork", "sribu"):
            platform_map = {"projects": "Projects.co.id", "fastwork": "Fastwork.id", "sribu": "Sribu.com"}
            platform = platform_map.get(action, action)
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                f"🎨 <b>{platform}</b> — Pilih aksi:",
                reply_markup=build_platform_submenu(action),
            )

    # ---- Fastwork Handlers ----

    async def _cb_fastwork(
        self, chat_id: str, message_id: int, action: str, callback_id: str
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)
        if action == "browse":
            await self._fw_browse(chat_id, message_id, callback_id)
        elif action == "refresh":
            await self._fw_refresh(chat_id, message_id, callback_id)
        elif action == "monitor":
            await self._fw_monitor(chat_id, message_id, callback_id)

    async def _fw_browse(self, chat_id: str, message_id: int, callback_id: str):
        """Show Fastwork job categories."""
        cats = get_fastwork_categories()
        if not cats:
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id, text="⚠️ Gagal load kategori Fastwork")
            return

        buttons = []
        row = []
        for cat in cats:
            row.append({
                "text": cat["name"],
                "callback_data": f"fwcat:{cat['id']}:1",
            })
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([{"text": "🔙 Back to Fastwork", "callback_data": "src:fastwork"}])

        cat_text = "\n".join([f"• {c['name']}" for c in cats[:14]])
        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            f"⚡ <b>Fastwork Categories</b>\n\n{cat_text}\n\nPilih kategori:",
            reply_markup={"inline_keyboard": buttons},
        )

    async def _fw_show_page(
        self, chat_id: str, message_id: int, tag_id: str, page: int
    ):
        """Show a page of Fastwork jobs for a given tag (local pagination).

        Jobs are cached in fw_cache for detail view resolution.
        Each job gets a 'View' URL button and a 'Detail' callback button.
        """
        PER_PAGE = 8
        all_jobs, _ = get_jobs_by_tag(tag_id=tag_id if tag_id != "all" else None, max_pages=10)
        total = len(all_jobs)
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = min(page, total_pages)
        start = (page - 1) * PER_PAGE
        end = start + PER_PAGE
        page_jobs = all_jobs[start:end]

        if not page_jobs:
            await edit_message(
                TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                "⚠️ Tidak ada job ditemukan di kategori ini."
            )
            return

        # Store in cache for detail resolution
        self.fw_cache.store(tag_id, page, page_jobs)

        cat_name = "All Jobs"
        if tag_id != "all":
            cats = {c["id"]: c["name"] for c in get_fastwork_categories()}
            cat_name = cats.get(tag_id, tag_id)

        text = format_fastwork_jobs_list(page_jobs, cat_name, page, total_pages)

        # Build per-job View buttons + navigation
        buttons = []
        # Per-job row: [View URL btn, Detail callback btn]
        for i, job in enumerate(page_jobs):
            global_idx = start + i
            btn_row = [
                {"text": "🔗 View", "url": job.link},
                {"text": f"📋 #{global_idx + 1}", "callback_data": f"fwdetail:{tag_id}:{page}:{global_idx}"},
            ]
            buttons.append(btn_row)

        # Navigation
        nav_row = []
        if page > 1:
            nav_row.append({"text": "⬅️ Prev", "callback_data": f"fwcat:{tag_id}:{page - 1}"})
        nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            nav_row.append({"text": "Next ➡️", "callback_data": f"fwcat:{tag_id}:{page + 1}"})
        if nav_row:
            buttons.append(nav_row)

        buttons.append([{"text": "🔙 Back to Categories", "callback_data": "fw:browse"}])
        buttons.append([{"text": "🔙 Back to Fastwork", "callback_data": "src:fastwork"}])

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            text,
            reply_markup={"inline_keyboard": buttons},
        )

    async def _fw_show_detail(
        self, chat_id: str, message_id: int, tag_id: str, page: int, job_idx: int
    ):
        """Show full detail card for a specific Fastwork job."""
        jobs = self.fw_cache.get(tag_id, page)
        if not jobs or job_idx < 0 or job_idx >= len(jobs):
            await edit_message(
                TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                "⚠️ Job tidak ditemukan. Silakan kembali ke daftar."
            )
            return

        job = jobs[job_idx]
        text = format_fastwork_job_card(job, job_idx)
        keyboard = _build_fastwork_detail_keyboard(job)

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            text,
            reply_markup=keyboard,
        )

    async def _fw_refresh(self, chat_id: str, message_id: int, callback_id: str):
        """Show latest Fastwork jobs."""
        all_jobs, _ = get_jobs_by_tag(max_pages=3)
        jobs = all_jobs[:10]
        total = len(all_jobs)

        if not jobs:
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id, text="⚠️ Gagal load Fastwork jobs")
            return

        self.fw_cache.store("all", 1, jobs)

        text = (
            f"⚡ <b>Fastwork Latest Jobs</b>\n"
            f"🆕 {total}+ jobs total\n"
        )
        # Compact inline list
        for i, job in enumerate(jobs):
            offers_emoji = "🔥" if job.offers_count > 10 else "👥" if job.offers_count > 0 else "🆕"
            type_emoji = "💻" if job.type == "freelance" else "⏰" if job.type == "contract" else "🌐"
            text += (
                f"\n<b>#{i + 1}</b> {job.title}\n"
                f"   💰 {job.budget}  •  {type_emoji} {job.type or '-'}  •  "
                f"{offers_emoji} {job.offers_count} offers\n"
                f"   📂 {job.tag_name}  •  📅 {job.published_date}"
            )

        buttons = []
        for i, job in enumerate(jobs):
            buttons.append([
                {"text": "🔗 View", "url": job.link},
                {"text": f"📋 #{i + 1}", "callback_data": f"fwdetail:all:1:{i}"},
            ])
        buttons.append([{"text": "🔙 Back to Fastwork", "callback_data": "src:fastwork"}])

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            text,
            reply_markup={"inline_keyboard": buttons},
        )

    async def _fw_monitor(self, chat_id: str, message_id: int, callback_id: str):
        """Show Fastwork monitoring settings."""
        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            "🔔 <b>Fastwork Monitor Settings</b>\n\n"
            "Pilih kategori untuk toggle monitoring:\n"
            "(Enabled = dapat notifikasi project baru)",
            reply_markup=build_fastwork_monitor_keyboard(self.fw_monitor),
        )

    # ---- Sribu Handlers ----

    async def _cb_sribu(
        self, chat_id: str, message_id: int, action: str, callback_id: str
    ):
        """Handle Sribu sub-menu callbacks."""
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)

        if action == "browse":
            await self._cmd_sribu(chat_id)
        elif action == "monitor":
            await self._sribu_monitor(chat_id, message_id, callback_id)
        elif action == "refresh":
            await self._sribu_refresh(chat_id, message_id)

    async def _sribu_show_page(
        self, chat_id: str, message_id: int, category_id: str, page: int
    ):
        """Show a page of Sribu contests for a given category."""
        PER_PAGE = 8

        # Fetch contests (API returns 10 per page, fetch 3 pages for local pagination)
        contests = scrape_sribu_listing(category_id, page, PER_PAGE)

        # For "all" we need to get more pages to handle pagination
        if category_id == "all":
            all_contests = []
            for p in range(1, 4):
                page_conts = scrape_sribu_listing("all", p, 10)
                all_contests.extend(page_conts)
                if not page_conts:
                    break
            contests = all_contests

        total = len(contests)
        total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = min(page, total_pages)
        start = (page - 1) * PER_PAGE
        end = start + PER_PAGE
        page_contests = contests[start:end]

        if not page_contests:
            await edit_message(
                TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                "🎨 Tidak ada contest ditemukan di kategori ini."
            )
            return

        # Store in cache for detail resolution
        self.sribu_cache.store(category_id, page, page_contests)

        cat_name = "Semua Kategori" if category_id == "all" else (
            next((c["name"] for c in get_sribu_categories() if c["id"] == category_id), category_id)
        )

        text = format_sribu_contests_list(page_contests, cat_name, page, total_pages)

        # Build per-contest View buttons + navigation
        buttons = []
        for i, contest in enumerate(page_contests):
            global_idx = start + i
            budget_str = contest.budget or contest.budget_raw or "-"
            btn_row = [
                {"text": "🔗 View", "url": contest.contest_url},
                {"text": f"📋 #{global_idx + 1}", "callback_data": f"sribu_detail:{category_id}:{page}:{global_idx}"},
            ]
            buttons.append(btn_row)

        # Navigation
        nav_row = []
        if page > 1:
            nav_row.append({"text": "⬅️ Prev", "callback_data": f"sribu_cat:{category_id}:{page - 1}"})
        nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            nav_row.append({"text": "Next ➡️", "callback_data": f"sribu_cat:{category_id}:{page + 1}"})
        if nav_row:
            buttons.append(nav_row)

        buttons.append([{"text": "🔙 Back to Categories", "callback_data": "sribu:browse"}])
        buttons.append([{"text": "🏠 Main Menu", "callback_data": "menu:back"}])

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            text,
            reply_markup={"inline_keyboard": buttons},
        )

    async def _sribu_show_detail(
        self, chat_id: str, message_id: int, category_id: str, page: int, contest_idx: int
    ):
        """Show full contest detail (card view) with View button."""
        # Get from cache
        if category_id == "all":
            all_contests = []
            for p in range(1, 4):
                page_conts = scrape_sribu_listing("all", p, 10)
                all_contests.extend(page_conts)
                if not page_conts:
                    break
            contests = all_contests
        else:
            contests = self.sribu_cache.get(category_id, page)

        if not contests or contest_idx >= len(contests):
            await edit_message(
                TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                "⚠️ Contest tidak ditemukan. Coba kembali ke halaman sebelumnya."
            )
            return

        contest = contests[contest_idx]

        text = format_sribu_contest_card(contest, 0)
        keyboard = _build_sribu_detail_keyboard(contest)

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            text,
            reply_markup=keyboard,
        )

    async def _sribu_monitor(self, chat_id: str, message_id: int, callback_id: str):
        """Show Sribu monitoring settings."""
        cats = get_sribu_categories()
        buttons = []

        for cat in cats:
            is_on = self.sribu_monitor.is_monitored(cat["id"])
            status = "✅" if is_on else "⬜"
            buttons.append([{
                "text": f"{status} {cat['emoji']} {cat['name']}",
                "callback_data": f"sribu_mon:{cat['id']}",
            }])

        buttons.append([{"text": "🔙 Back to Sribu", "callback_data": "src:sribu"}])

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            "🎨 <b>Sribu Monitor Settings</b>\n\n"
            "Pilih kategori untuk toggle monitoring:\n"
            "(Enabled = dapat notifikasi contest baru)",
            reply_markup={"inline_keyboard": buttons},
        )

    async def _sribu_refresh(self, chat_id: str, message_id: int):
        """Refresh Sribu contests and show new ones."""
        await edit_message(
            TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
            "🎨 <b>Refreshing Sribu...</b>\nSedang mengambil contest terbaru..."
        )

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            contests = await loop.run_in_executor(executor, lambda: scrape_sribu_listing("all", 1, 10))

        new_contests = [c for c in contests if not self.sribu_tracker.is_seen(c.contest_id)]

        if new_contests:
            text = f"🎨 <b>{len(new_contests)} Contest Baru Ditemukan!</b>\n\n"
            for i, c in enumerate(new_contests[:10]):
                text += format_sribu_contest_card(c, i)
                text += "\n"
                self.sribu_tracker.mark_seen(c.contest_id)

            if len(new_contests) > 10:
                text += f"\n...dan {len(new_contests) - 10} contest lainnya."

            await edit_message(
                TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                text,
                reply_markup=build_main_menu_keyboard(),
            )
        else:
            await edit_message(
                TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                "✅ <b>Tidak ada contest baru</b>\nSemua contest sudah di-notifikasi.",
                reply_markup=build_main_menu_keyboard(),
            )

    # ---- Callback Handlers ----

    async def _cb_menu(
        self, chat_id: str, message_id: int, action: str, callback_id: str
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)

        if action == "back":
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                "🏠 <b>Main Menu</b>\n\nPilih aksi di bawah:",
                reply_markup=build_main_menu_keyboard(),
            )
        elif action == "browse":
            await self._cb_category_list(chat_id, message_id, callback_id)
        elif action == "monitor":
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                format_monitor_status(self.monitor),
                reply_markup=build_monitor_keyboard(self.monitor),
            )
        elif action == "refresh":
            await self._cmd_refresh(chat_id)
        elif action == "help":
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                "📖 <b>Bantuan</b>\n\n"
                "/browse — Browse project per kategori\n"
                "/monitor — Atur monitoring\n"
                "/refresh — Cek project baru\n"
                "/status — Status monitoring",
                reply_markup=build_main_menu_keyboard(),
            )

    async def _cb_category(
        self, chat_id: str, message_id: int, category_id: str, callback_id: str
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)
        await self._show_category_page(chat_id, message_id, category_id, 1)

    async def _cb_category_list(
        self, chat_id: str, message_id: int | None, callback_id: str | None
    ):
        if callback_id:
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)

        text = "📂 <b>Pilih Kategori</b>\n\nPilih kategori project yang ingin dilihat:"

        if message_id:
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                text,
                reply_markup=build_category_keyboard(),
            )
        else:
            await send_message(
                TELEGRAM_BOT_TOKEN,
                chat_id,
                text,
                reply_markup=build_category_keyboard(),
            )

    async def _show_category_page(
        self, chat_id: str, message_id: int, category_id: str, page: int
    ):
        """Fetch ALL website pages with interactive emoji progress bar."""
        category = get_category_by_id(category_id)

        # Progress bar state
        progress_data = {"current": 0, "total": 0, "count": 0}

        def make_progress_bar(current: int, total: int) -> str:
            filled = min(current, total)
            empty = total - filled
            bar = "🟢" * filled + "⬜" * empty
            return bar

        def on_progress(current: int, total: int, count: int):
            progress_data["current"] = current
            progress_data["total"] = total
            progress_data["count"] = count

        # Show initial loading
        bar = make_progress_bar(0, 6)
        loading_text = (
            f"{category['emoji']} <b>{category['name']}</b>\n\n"
            f"🔍 <i>Fetching page 0/6...</i>\n"
            f"{bar}\n"
            f"📦 <i>0 projects found so far</i>"
        )
        loading_kb = {
            "inline_keyboard": [[{"text": "⏳ Loading...", "callback_data": "noop"}]]
        }

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            loading_text,
            reply_markup=loading_kb,
        )

        # Fetch ALL pages from website in background thread
        loop = asyncio.get_event_loop()

        async def scrape_with_progress():
            """Run scrape_all_pages with periodic message updates."""
            all_projects = []

            def scrape_one_page(pg):
                from scraper import scrape_listing

                projects = scrape_listing(category_id, pg)
                all_projects.extend(projects)
                return projects

            # Estimate total pages (start with 10, adjust after first fetch)
            max_pages = 10

            for pg in range(1, max_pages + 1):
                result = await loop.run_in_executor(None, scrape_one_page, pg)
                progress_data["current"] = pg
                progress_data["count"] = len(all_projects)

                # Update progress message
                bar = make_progress_bar(pg, max_pages)
                progress_text = (
                    f"{category['emoji']} <b>{category['name']}</b>\n\n"
                    f"🔍 <i>Fetching page {pg}/{max_pages}...</i>\n"
                    f"{bar}\n"
                    f"📦 <i>{len(all_projects)} projects found so far</i>"
                )
                try:
                    await edit_message(
                        TELEGRAM_BOT_TOKEN,
                        int(chat_id),
                        message_id,
                        progress_text,
                        reply_markup=loading_kb,
                    )
                except Exception:
                    pass  # Ignore edit failures (rate limit, same text, etc.)

                if not result:
                    progress_data["total"] = pg
                    break

                await asyncio.sleep(0.2)

            return all_projects

        all_projects = await scrape_with_progress()
        actual_pages = progress_data["current"]

        if not all_projects:
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                f"{category['emoji']} <b>{category['name']}</b>\n\n"
                "😔 Tidak ada project ditemukan.",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "🔙 Categories", "callback_data": "catlist"}],
                        [{"text": "🏠 Main Menu", "callback_data": "menu:back"}],
                    ]
                },
            )
            return

        total_pages = max(
            1, (len(all_projects) + PROJECTS_PER_PAGE - 1) // PROJECTS_PER_PAGE
        )

        start = (page - 1) * PROJECTS_PER_PAGE
        end = start + PROJECTS_PER_PAGE
        page_projects = all_projects[start:end]

        # Cache ALL projects for detail callbacks
        self.cache.store(category_id, 0, all_projects)

        text = format_project_list(page_projects, category, page, total_pages)
        kb = self._build_project_keyboard(category_id, page, total_pages, page_projects)

        await edit_message(
            TELEGRAM_BOT_TOKEN, int(chat_id), message_id, text, reply_markup=kb
        )

    def _build_project_keyboard(
        self, category_id: str, page: int, total_pages: int, projects: list[Project]
    ) -> dict:
        """Build keyboard with pagination and project detail buttons."""
        buttons = []

        # Project detail buttons — use absolute index for cache lookup
        abs_start = (page - 1) * PROJECTS_PER_PAGE
        for i, p in enumerate(projects):
            abs_index = abs_start + i
            short_title = _truncate(p.title, 25)
            buttons.append(
                [
                    {
                        "text": f"📋 #{abs_index + 1} {short_title}",
                        "callback_data": f"proj:{abs_index}:{category_id}:{page}",
                    }
                ]
            )

        # Pagination row
        nav_row = []
        if page > 1:
            nav_row.append(
                {"text": "⬅️ Prev", "callback_data": f"page:{category_id}:{page - 1}"}
            )
        nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            nav_row.append(
                {"text": "Next ➡️", "callback_data": f"page:{category_id}:{page + 1}"}
            )
        buttons.append(nav_row)

        buttons.append([{"text": "📂 Categories", "callback_data": "catlist"}])
        buttons.append([{"text": "🏠 Main Menu", "callback_data": "menu:back"}])

        return {"inline_keyboard": buttons}

    async def _cb_page(
        self,
        chat_id: str,
        message_id: int,
        category_id: str,
        page: int,
        callback_id: str,
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)

        # Get all projects from cache (stored at page 0)
        all_projects = self.cache.get(category_id, 0)
        if not all_projects:
            await answer_callback(
                TELEGRAM_BOT_TOKEN,
                callback_id,
                text="⚠️ Data expired, coba /browse lagi",
            )
            return

        category = get_category_by_id(category_id)
        total_pages = max(
            1, (len(all_projects) + PROJECTS_PER_PAGE - 1) // PROJECTS_PER_PAGE
        )

        start = (page - 1) * PROJECTS_PER_PAGE
        end = start + PROJECTS_PER_PAGE
        page_projects = all_projects[start:end]

        text = format_project_list(page_projects, category, page, total_pages)
        kb = self._build_project_keyboard(category_id, page, total_pages, page_projects)

        await edit_message(
            TELEGRAM_BOT_TOKEN, int(chat_id), message_id, text, reply_markup=kb
        )

    async def _cb_project_detail(
        self,
        chat_id: str,
        message_id: int,
        abs_index: int,
        category_id: str,
        page: int,
        callback_id: str,
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)

        # Get all projects from cache (stored at page 0)
        all_projects = self.cache.get(category_id, 0)
        if not all_projects or abs_index >= len(all_projects):
            await answer_callback(
                TELEGRAM_BOT_TOKEN,
                callback_id,
                text="⚠️ Data expired, coba /browse lagi",
            )
            return

        project = all_projects[abs_index]
        text = format_project_card(project, abs_index)

        # Keyboard with View Project button
        kb = {
            "inline_keyboard": [
                [
                    {
                        "text": "🔗 View Project",
                        "url": project.link,
                    }
                ],
                [
                    {
                        "text": "🔙 Kembali ke List",
                        "callback_data": f"page:{category_id}:{page}",
                    }
                ],
                [{"text": "🏠 Main Menu", "callback_data": "menu:back"}],
            ]
        }

        await edit_message(
            TELEGRAM_BOT_TOKEN, int(chat_id), message_id, text, reply_markup=kb
        )

    async def _cb_monitor_toggle(
        self, chat_id: str, message_id: int, category_id: str, callback_id: str
    ):
        is_now_on = self.monitor.toggle(category_id)
        category = get_category_by_id(category_id)

        status = "DIAKTIFKAN ✅" if is_now_on else "DINONAKTIFKAN ⬜"

        await answer_callback(
            TELEGRAM_BOT_TOKEN,
            callback_id,
            text=f"{category['emoji']} {category['name']}: {status}",
        )

        # Refresh the monitor display
        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            format_monitor_status(self.monitor),
            reply_markup=build_monitor_keyboard(self.monitor),
        )

    # ---- Polling Loop ----

    async def _seed_seen_projects(self):
        """Seed the tracker with existing projects on startup.
        Prevents spamming notifications for projects that already exist."""
        if self.tracker.seen_ids:
            logger.info(
                f"Tracker already has {len(self.tracker.seen_ids)} seen IDs, skipping seed"
            )
            return

        logger.info("Seeding tracker with existing projects...")
        categories_to_seed = self.monitor.monitored_categories or {"all"}

        for cat_id in categories_to_seed:
            try:
                category = get_category_by_id(cat_id)
                logger.info(f"  Seeding: {category['name']}")
                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor(max_workers=2) as executor:
                    # Fetch all pages for seeding
                    all_projects = []
                    for pg in range(1, 11):
                        page_projects = await loop.run_in_executor(
                            executor, scrape_listing, cat_id, pg
                        )
                        all_projects.extend(page_projects)
                        if not page_projects:
                            break
                for p in all_projects:
                    self.tracker.mark_seen(p.project_id)
                logger.info(
                    f"  Seeded {len(all_projects)} projects from {category['name']}"
                )
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"  Error seeding {cat_id}: {e}")

        logger.info(
            f"Seed complete. {len(self.tracker.seen_ids)} projects marked as seen."
        )

    async def start_polling(self):
        self._running = True
        logger.info(f"Monitoring started. Polling every {POLL_INTERVAL_SECONDS}s")

        # Seed existing projects so we only notify truly new ones
        await self._seed_seen_projects()

        # Send startup notification
        monitored = [
            c for c in CATEGORIES if c["id"] in self.monitor.monitored_categories
        ]
        cat_list = "\n".join(f"  {c['emoji']} {c['name']}" for c in monitored)

        fw_cats = {c["id"]: c["name"] for c in get_fastwork_categories()}
        fw_monitored = [fw_cats.get(t, t) for t in self.fw_monitor.monitored_tags]
        fw_list = "\n".join(f"  ⚡ {n}" for n in fw_monitored)

        sribu_cats = {c["id"]: c["name"] for c in get_sribu_categories()}
        sribu_monitored = [sribu_cats.get(t, t) for t in self.sribu_monitor.monitored_categories]
        sribu_list = "\n".join(f"  🎨 {n}" for n in sribu_monitored)

        await send_message(
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            "🤖 <b>Freelance Monitor Bot Active!</b>\n\n"
            "🌐 <b>Projects.co.id</b>\n"
            f"🔔 Monitoring <b>{len(monitored)}</b> kategori\n\n"
            "⚡ <b>Fastwork.id</b>\n"
            f"🔔 Monitoring <b>{len(fw_monitored)}</b> kategori\n"
            f"{fw_list or '  ⬜ Belum ada yang dimonitor'}\n\n"
            "🎨 <b>Sribu.com</b>\n"
            f"🔔 Monitoring <b>{len(sribu_monitored)}</b> kategori\n"
            f"{sribu_list or '  ⬜ Belum ada yang dimonitor'}\n\n"
            "Polling setiap <b>{POLL_INTERVAL_SECONDS}s</b>",
            reply_markup=build_main_menu_keyboard(),
        )

        while self._running:
            try:
                if not self.monitor.monitored_categories:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                # ── PARALLEL CATEGORY FETCHING ──────────────────────────────────
                # Server: 2 cores / ~1GB RAM → max 3 concurrent fetches (safe, fast)
                # All categories fetched simultaneously instead of sequential loop.
                # ~3x faster polling cycle (7 cats: 21s sequential → ~7s parallel)
                # ──────────────────────────────────────────────────────────────

                if not self._running:
                    break

                cat_ids = list(self.monitor.monitored_categories)
                loop = asyncio.get_event_loop()

                # Semaphore: max 5 concurrent category fetches (I/O bound, not CPU)
                # cloudscraper: ~0.2s for all 7 cats combined → safe to increase concurrency
                MAX_CONCURRENT = 5
                sem = asyncio.Semaphore(MAX_CONCURRENT)

                # Single shared executor for all concurrent category fetches
                # (avoids creating 5 separate pools with 5 workers each = 25 threads)
                with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as shared_executor:

                    async def fetch_one_category(cat_id: str) -> tuple[str, list, dict]:
                        """Fetch one category inside semaphore + shared thread executor."""
                        async with sem:
                            category = get_category_by_id(cat_id)
                            logger.info(f"Polling: {category['name']}")
                            projects = await loop.run_in_executor(
                                shared_executor, scrape_listing, cat_id, 1
                            )
                            # Filter unseen + published today
                            new_projects = [
                                p for p in projects
                                if not self.tracker.is_seen(p.project_id)
                                and _is_published_today(p.published_date)
                            ]
                            return cat_id, new_projects, category

                    # Launch all categories in parallel, respect semaphore limit
                    fetch_tasks = [fetch_one_category(c) for c in cat_ids]
                    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                # Process all results after parallel fetch completes
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Category fetch error: {result}")
                        continue

                    cat_id, new_projects, category = result

                    if new_projects:
                        logger.info(f"🆕 {len(new_projects)} new in {category['name']}")

                        cat_emoji = category["emoji"]
                        header = (
                            f"🆕 <b>{len(new_projects)} Project Baru</b> "
                            f"di {cat_emoji} <b>{category['name']}</b>!\n\n"
                        )

                        for i, p in enumerate(new_projects[:5]):
                            update_budget_stats(p)
                            update_client_stats(p)
                            record_digest_project(p, category["name"])

                            bid_count = (
                                int(p.bid_count)
                                if p.bid_count and p.bid_count.isdigit()
                                else 0
                            )
                            bid_emoji = (
                                "🔥" if bid_count > 20
                                else "👥" if bid_count > 5
                                else "🆕"
                            )
                            budget_cmp = get_budget_comparison(p.budget)
                            client_rep = get_client_reputation(p.owner_name)
                            desc_short = (p.description[:150] + "...") if len(p.description) > 150 else p.description
                            cmp_txt = f"\n   {budget_cmp}" if budget_cmp else ""
                            msg = (
                                f"<b>▸ {p.title}</b>\n"
                                f"   📝 {desc_short}\n\n"
                                f"   💰 {p.budget or '-'}{cmp_txt}\n"
                                f"   {bid_emoji} {p.bid_count or '0'} bids  •  "
                                f"👤 {p.owner_name} — {client_rep}\n"
                                f"   📅 {p.published_date or '-'}  •  "
                                f"🔗 <a href='{p.link}'>View →</a>\n"
                            )
                            await send_message(
                                TELEGRAM_BOT_TOKEN,
                                TELEGRAM_CHAT_ID,
                                header + msg if i == 0 else msg,
                            )
                            self.tracker.mark_seen(p.project_id)
                            await asyncio.sleep(0.5)

                        if len(new_projects) > 5:
                            await send_message(
                                TELEGRAM_BOT_TOKEN,
                                TELEGRAM_CHAT_ID,
                                f"...dan <b>{len(new_projects) - 5}</b> project lainnya. "
                                f"Gunakan /browse untuk lihat semua.",
                            )

                # No inter-category sleep needed — all ran in parallel

                # ---- Fastwork Polling ----
                if self.fw_monitor.monitored_tags:
                    try:
                        loop = asyncio.get_event_loop()
                        with ThreadPoolExecutor(max_workers=1) as executor:
                            new_jobs = await loop.run_in_executor(
                                executor, scrape_new_jobs, None, self.fw_tracker.seen_ids
                            )

                        if new_jobs:
                            logger.info(f"⚡ {len(new_jobs)} new Fastwork jobs")

                            # Group by tag for notification
                            by_tag: dict[str, list[FastworkJob]] = {}
                            for job in new_jobs:
                                if job.tag_id not in by_tag:
                                    by_tag[job.tag_id] = []
                                by_tag[job.tag_id].append(job)

                            cats = {c["id"]: c["name"] for c in get_fastwork_categories()}

                            for tag_id, jobs in by_tag.items():
                                cat_name = cats.get(tag_id, "Unknown")
                                for i, job in enumerate(jobs[:5]):
                                    text = format_fastwork_job_card(job, i)
                                    keyboard = _build_fastwork_detail_keyboard(job)
                                    await send_message(
                                        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                                        text, reply_markup=keyboard,
                                    )
                                    self.fw_tracker.mark_seen(job.job_id)
                                    await asyncio.sleep(0.5)

                            if len(new_jobs) > 8:
                                await send_message(
                                    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                                    f"...dan <b>{len(new_jobs) - 8}</b> job Fastwork lainnya. "
                                    f"Gunakan /fw untuk lihat semua."
                                )
                    except Exception as e:
                        logger.error(f"Fastwork polling error: {e}")

                # ---- Sribu Polling ----
                if self.sribu_monitor.monitored_categories:
                    try:
                        loop = asyncio.get_event_loop()
                        with ThreadPoolExecutor(max_workers=1) as executor:
                            new_contests = await loop.run_in_executor(
                                executor, scrape_new_contests, self.sribu_tracker.seen_ids
                            )

                        if new_contests:
                            logger.info(f"🎨 {len(new_contests)} new Sribu contests")

                            # Group by category
                            by_cat: dict[str, list[SribuContest]] = {}
                            for contest in new_contests:
                                if contest.category_id not in by_cat:
                                    by_cat[contest.category_id] = []
                                by_cat[contest.category_id].append(contest)

                            cats = {c["id"]: c for c in get_sribu_categories()}

                            for cat_id, contests in by_cat.items():
                                cat_info = cats.get(cat_id, {})
                                cat_name = cat_info.get("name", "Unknown")
                                cat_emoji = cat_info.get("emoji", "🎨")
                                for i, contest in enumerate(contests[:5]):
                                    text = format_sribu_contest_card(contest, i)
                                    keyboard = _build_sribu_detail_keyboard(contest)
                                    await send_message(
                                        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                                        f"🎨 <b>Contest Baru!</b> di {cat_emoji} <b>{cat_name}</b>\n\n" + text
                                        if i == 0 else text,
                                        reply_markup=keyboard,
                                    )
                                    self.sribu_tracker.mark_seen(contest.contest_id)
                                    await asyncio.sleep(0.5)

                            if len(new_contests) > 8:
                                await send_message(
                                    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                                    f"...dan <b>{len(new_contests) - 8}</b> contest Sribu lainnya. "
                                    f"Gunakan /sribu untuk lihat semua."
                                )
                    except Exception as e:
                        logger.error(f"Sribu polling error: {e}")

            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(30)  # Wait before retry

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def stop(self):
        """Stop the polling loop."""
        self._running = False


# ============================================================
# Long Polling Update Fetcher
# ============================================================


async def fetch_updates(token: str, offset: int = 0, timeout: int = 30) -> dict:
    """Fetch updates via long polling using stdlib urllib."""
    import urllib.request
    import urllib.error

    url = f"{TG_API}{token}/getUpdates"
    payload = {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "TelegramBot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "result": []}
    except Exception as e:
        logger.error(f"fetch_updates error: {e}")
        return {"ok": False, "result": []}


async def main():
    bot = ProjectsBot()

    # Start monitoring loop in background
    monitor_task = asyncio.create_task(bot.start_polling())

    # Start long-polling for user interactions
    offset = 0
    logger.info("Bot started. Waiting for messages...")

    try:
        while True:
            try:
                result = await fetch_updates(TELEGRAM_BOT_TOKEN, offset)

                if result.get("ok") and result.get("result"):
                    for update in result["result"]:
                        offset = update["update_id"] + 1
                        await bot.handle_update(update)
                else:
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Update fetch error: {e}")
                await asyncio.sleep(5)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        bot.stop()
        monitor_task.cancel()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        bot.stop()
        monitor_task.cancel()
        raise


if __name__ == "__main__":
    # Lock file to prevent duplicate instances
    lock_path = os.path.join(os.path.dirname(__file__), "bot.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ERROR: Bot is already running! Exiting.")
        sys.exit(1)

    asyncio.run(main())
