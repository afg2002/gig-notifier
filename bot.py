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

# Auto-load .env file
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                os.environ.setdefault(_key.strip(), _val.strip())
    del _line, _key, _val
del _env_path, _f

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import aiohttp
from aiohttp import web

from scraper import (
    scrape_listing,
    CATEGORIES,
    get_category_by_id,
    Project,
    scrape_project_detail,
    ProjectDetail,
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

from trend_analysis import (
    record_project,
    get_trend_stats,
    get_category_trend,
    get_peak_hours,
    get_budget_trend,
    format_trend_report,
)

from proposal_generator import (
    generate_proposal,
    extract_project_info_from_url,
    format_proposal_for_display,
    _check_rate_limit,
    _increment_proposal_count,
    get_user_cv_text,
    save_user_cv,
    extract_text_from_pdf,
    get_user_profile,
    save_user_profile,
    delete_user_profile,
)

# ⚔️ Out-of-the-box features
from intel.ghost_detector import detect_ghost, format_ghost_report
from analytics.skill_radar import (
    extract_skills,
    extract_skills_from_projects,
    compare_with_cv,
    analyze_trends,
    format_skill_radar,
    format_trend_compact,
    get_projects_for_period,
)
from analytics.bid_timing import (
    TimingOracle,
    format_timing_report,
    format_timing_compact,
    get_projects_with_dates,
)
from ai.proposal_battle import (
    generate_all_personas,
    format_battle_intro,
    format_persona_result,
    compare_personas,
    PERSONAS,
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

# Webhook configuration
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://compliant-chief-flavor-stickers.trycloudflare.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET=os.getenv("WEBHOOK_SECRET", "afghan_secret_2026")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8082"))
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEBHOOK_ENABLED = os.getenv("WEBHOOK_ENABLED", "false").lower() == "true"


def get_chat_ids() -> list[str]:
    """Parse TELEGRAM_CHAT_ID env var (comma-separated) into a list of chat IDs."""
    raw = os.getenv("TELEGRAM_CHAT_ID", "")
    if not raw:
        return []
    return [cid.strip() for cid in raw.split(",") if cid.strip()]

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
    """Make a request to Telegram Bot API using aiohttp (non-blocking)."""
    url = f"{TG_API}{token}/{method}"

    try:
        timeout_obj = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    logger.error(f"Telegram API error: {text[:200]}")
                    try:
                        return json.loads(text)
                    except Exception:
                        return {"ok": False, "error_code": resp.status, "description": text[:200]}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout"}
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
    """Send a message with optional inline keyboard.

    When chat_id equals TELEGRAM_CHAT_ID (which may contain comma-separated IDs),
    automatically broadcasts to all configured chat IDs.
    """
    # Check if this is the broadcast target (env var)
    broadcast_ids = get_chat_ids()
    target_ids = [cid.strip() for cid in chat_id.split(",") if cid.strip()]

    # If the target matches the env var pattern, broadcast to all
    if chat_id == TELEGRAM_CHAT_ID and len(broadcast_ids) > 1:
        results = []
        for cid in broadcast_ids:
            payload = {
                "chat_id": cid,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
            try:
                result = await tg_request(token, "sendMessage", payload)
                if not result.get("ok"):
                    logger.error(f"Telegram broadcast error to {cid}: {result}")
                results.append(result.get("ok", False))
            except Exception as e:
                logger.error(f"Failed to send to {cid}: {e}")
                results.append(False)
            await asyncio.sleep(0.3)
        return {"ok": all(results), "broadcast": True, "results": results}

    # Single recipient
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


async def broadcast(token: str, chat_ids: list[str], text: str, reply_markup: dict | None = None, parse_mode: str = "HTML") -> dict:
    """Send a message to multiple chat IDs. Returns aggregated results.
    
    Directly sends to each recipient using tg_request to avoid any dual-mode
    logic that could cause sends to only reach some users.
    """
    results = []
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            result = await tg_request(token, "sendMessage", payload)
            ok = result.get("ok", False)
            if ok:
                logger.info(f"✅ Broadcast sent to {chat_id}")
            else:
                logger.error(f"❌ Broadcast failed to {chat_id}: {result}")
            results.append({"chat_id": chat_id, "ok": ok, "result": result})
        except Exception as e:
            logger.error(f"❌ Exception sending to {chat_id}: {e}")
            results.append({"chat_id": chat_id, "ok": False, "error": str(e)})
        await asyncio.sleep(0.3)
    
    success_count = sum(1 for r in results if r.get("ok", False))
    logger.info(f"Broadcast complete: {success_count}/{len(results)} successful")
    return {"ok": success_count == len(results), "results": results}


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
    projects: list[Project], category: dict, page: int, total_pages: int,
    start_index: int = 0
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
            f"<b>#{start_index + i + 1}</b> {p.title}\n"
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
    """Build the main menu inline keyboard — all features organized by section."""
    return {
        "inline_keyboard": [
            # ── Browse Projects ──
            [{"text": "🌐 Projects.co.id  📋", "callback_data": "src:projects"}],
            [{"text": "⚡ Fastwork.id    ⚡", "callback_data": "src:fastwork"}],
            [{"text": "🎨 Sribu.com     🎨", "callback_data": "src:sribu"}],

            # ── AI Tools ──
            [{"text": "📝 Generate AI Proposal", "callback_data": "menu:proposal"}],
            [{"text": "📄 Upload CV PDF", "callback_data": "menu:uploadcv"},
             {"text": "👁️ Lihat CV Saya", "callback_data": "menu:mycv"}],

            # ── Info & Stats ──
            [{"text": "🔄 Refresh Projects", "callback_data": "menu:refresh"},
             {"text": "📈 Daily Digest", "callback_data": "menu:digest"}],
            [{"text": "📉 Trend Analysis", "callback_data": "menu:trends"},
             {"text": "🏆 Top Clients", "callback_data": "menu:topclients"}],

            # ── Help ──
            [{"text": "📖 Bantuan & Guide", "callback_data": "menu:help"}],
        ]
    }


def build_platform_submenu(source: str) -> dict:
    """Build sub-menu for a specific platform."""
    if source == "projects":
        return {
            "inline_keyboard": [
                [{"text": "📋 Browse Projects", "callback_data": "menu:browse"}],
                [{"text": "🔔 Monitor Settings", "callback_data": "menu:monitor"}],
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
    contests: list[SribuContest], category: str, page: int, total_pages: int,
    start_index: int = 0
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
            f"<b>#{start_index + i + 1}</b> {c.title}\n"
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
    jobs: list[FastworkJob], category: str, page: int, total_pages: int,
    start_index: int = 0
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
            f"<b>#{start_index + i + 1}</b> {job.title}\n"
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
        # Short-key URL cache for AI proposal from detail buttons (bypasses 64-byte callback limit)
        self._proposal_url_cache: dict[str, str] = {}
        self._proposal_url_counter = 0

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
        """Handle regular text messages and document uploads."""
        chat_id = str(message["chat"]["id"])

        # Handle document upload (CV PDF)
        if "document" in message:
            doc = message["document"]
            file_name = doc.get("file_name", "")
            mime_type = doc.get("mime_type", "")

            if mime_type == "application/pdf" or file_name.lower().endswith(".pdf"):
                await self._handle_cv_upload(chat_id, doc)
            else:
                await send_message(
                    TELEGRAM_BOT_TOKEN, chat_id,
                    "❌ File bukan PDF. Silakan upload file berekstensi .pdf"
                )
            return

        # Handle text messages
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
        elif text in ("/trends", "/trend"):
            await self._cmd_trends(chat_id)
        elif text.startswith("/proposal") or text.startswith("/apply"):
            await self._cmd_proposal(chat_id, text)
        elif text.startswith("/uploadcv"):
            await self._cmd_upload_cv(chat_id, text)
        elif text.startswith("/mycv"):
            await self._cmd_my_cv(chat_id)
        elif text.startswith("/setprofile"):
            await self._cmd_setprofile(chat_id, text)
        elif text.startswith("/myprofile"):
            await self._cmd_myprofile(chat_id)
        elif text.startswith("/ghost"):
            await self._cmd_ghost(chat_id, text)
        elif text in ("/radar", "/skillgap"):
            await self._cmd_skill_radar(chat_id)
        elif text in ("/timing", "/oracle"):
            await self._cmd_timing(chat_id)
        elif text in ("/battle", "/battleroyale"):
            await self._cmd_battle(chat_id, text)
        elif text == "/personas":
            await self._cmd_personas(chat_id)
        else:
            await send_message(
                TELEGRAM_BOT_TOKEN,
                chat_id,
                "🤖 <b>Command tidak dikenali</b>\n\n"
                "Gunakan menu di bawah atau ketik /help untuk bantuan.",
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
            elif action == "proposal":
                # proposal:projects:<key> OR proposal:fw:<key> OR proposal:sribu:<key>
                source = parts[1]
                cache_key = parts[2] if len(parts) > 2 else ""
                proj_url = self._proposal_url_cache.get(cache_key, "")
                await self._cb_ai_proposal(chat_id, message_id, source, proj_url, callback_id)
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
            "Selamat datang! Saya bantu Anda menemukan dan melamar project freelance.\n\n"
            "📦 <b>3 Platform:</b>\n"
            "🌐 <b>Projects.co.id</b> — Web dev, mobile, data entry, dll\n"
            "⚡ <b>Fastwork.id</b> — Desain, UX/UI, fotografi, dll\n"
            "🎨 <b>Sribu.com</b> — Logo, branding, kemasan, desain\n\n"
            "🚀 <b>Cara Kerja:</b>\n"
            "1️⃣ Pilih platform → Browse project → Lihat detail\n"
            "2️⃣ Klik <b>AI Proposal</b> di halaman detail project\n"
            "3️⃣ Salin proposal → Kirim ke client\n\n"
            "💡 <b>Tips:</b> Upload CV dulu dengan 📄 <code>/uploadcv</code> "
            "agar proposal lebih personal!\n\n"
            "Pilih menu di bawah untuk mulai 👇",
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

    async def _cmd_refresh(self, chat_id: str, menu_message_id: int = None):
        """Refresh projects and show new ones. Updates menu message if message_id provided."""
        if menu_message_id:
            # Called from menu button — update the menu message with loading
            try:
                await edit_message(
                    TELEGRAM_BOT_TOKEN,
                    int(chat_id),
                    menu_message_id,
                    "🔄 <b>Refreshing...</b>\nSedang mengambil project terbaru...",
                    reply_markup={"inline_keyboard": [[{"text": "⏳ Loading...", "callback_data": "noop"}]]},
                )
            except Exception:
                pass  # Ignore if edit fails
        else:
            # Called from /refresh command — send new message
            await send_message(
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

            if menu_message_id:
                await edit_message(
                    TELEGRAM_BOT_TOKEN,
                    int(chat_id),
                    menu_message_id,
                    text,
                    reply_markup=build_main_menu_keyboard(),
                )
            else:
                await send_message(
                    TELEGRAM_BOT_TOKEN,
                    chat_id,
                    text,
                    reply_markup=build_main_menu_keyboard(),
                )
        else:
            msg = "✅ <b>Tidak ada project baru</b>\nSemua project sudah di-notified."
            if menu_message_id:
                await edit_message(
                    TELEGRAM_BOT_TOKEN,
                    int(chat_id),
                    menu_message_id,
                    msg,
                    reply_markup=build_main_menu_keyboard(),
                )
            else:
                await send_message(
                    TELEGRAM_BOT_TOKEN,
                    chat_id,
                    msg,
                    reply_markup=build_main_menu_keyboard(),
                )

    async def _cmd_help(self, chat_id: str):
        await self._cmd_help_text(chat_id)

    async def _cmd_help_text(self, chat_id: str):
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            "📖 <b>Panduan Lengkap</b>\n\n"
            "<b>🚀 Quick Start:</b>\n"
            "1️⃣ Pilih platform → 2️⃣ Browse project → 3️⃣ Lihat detail → 4️⃣ AI Proposal\n\n"
            "<b>🌐 Platform Commands:</b>\n"
            "/browse — Browse Projects.co.id per kategori\n"
            "/fw — Browse Fastwork.id jobs\n"
            "/sribu — Browse Sribu.com contests\n\n"
            "<b>🔔 Monitoring Commands:</b>\n"
            "/monitor — Atur kategori yang dipantau\n"
            "/status — Cek status monitoring aktif\n"
            "/refresh — Cek project baru sekarang\n"
            "/digest — Ringkasan project hari ini\n\n"
            "<b>📊 Analytics Commands:</b>\n"
            "/trends — Analisis trend per kategori\n"
            "/topclients — Top 10 client terbanyak\n"
            "/radar — Skill Gap Radar: trending skill vs CV kamu\n"
            "/timing — Bid Timing Oracle: waktu optimal untuk bid\n\n"
            "<b>📝 AI Proposal Commands:</b>\n"
            "/proposal <url> — Generate AI proposal dari URL project\n"
            "/battle <project> — Proposal Battle Royale: 3 persona bertarung\n"
            "/personas — Bandingkan 3 persona proposal\n"
            "/uploadcv — Upload CV PDF (untuk proposal personal)\n"
            "/mycv — Lihat CV yang sudah diupload\n"
            "/setprofile — Set profil freelancer (multi-user support)\n"
            "/myprofile — Lihat profil yang sudah di-set\n\n"
            "<b>🔍 Intel Commands:</b>\n"
            "/ghost <project> — Ghost Detector: deteksi project mencurigakan\n\n"
            "<b>🛠️ Other:</b>\n"
            "/start — Menu utama\n"
            "/help — Panduan ini\n\n"
            "<b>💡 Tips:</b>\n"
            "• Set profil dulu dengan /setprofile agar proposal sesuai data Anda\n"
            "• Upload CV dengan /uploadcv untuk proposal lebih personal\n"
            "• Aktifkan monitoring dengan /monitor agar tidak ada project terlewat\n"
            "• AI Proposal ada di setiap halaman detail project — klik tombol langsung!",
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

    # ---- Trend Analysis ----

    async def _cmd_trends(self, chat_id: str):
        """Show weekly trend analysis dashboard."""
        report = format_trend_report()
        if report:
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, report)
        else:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "📊 <b>Trend Analysis</b>\n\n"
                "Butuh data 3+ hari untuk tampilkan trend. "
                "Kumpulkan data dulu ya! \n\n"
                "Pantau project secara rutin dan data trend akan "
                "tersimpan otomatis."
            )

    # ---- AI Proposal Generator ----

    async def _cmd_proposal(self, chat_id: str, text: str):
        """Generate a proposal for a project URL using real scraped data + CV context."""
        allowed, remaining = _check_rate_limit(chat_id)
        if not allowed:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "⏳ <b>Batas proposal harian tercapai</b>\n\n"
                f"Sisa: {remaining}/5 proposal hari ini.\n"
                "Coba lagi besok!"
            )
            return

        parts = text.split(" ", 1)
        project_arg = parts[1].strip() if len(parts) > 1 else ""

        if not project_arg:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "📝 <b>AI Proposal Generator</b>\n\n"
                "Usage:\n"
                "<code>/proposal &lt;project_url&gt;</code>\n\n"
                "Contoh:\n"
                "<code>/proposal https://projects.co.id/view/abc123/project-title</code>\n\n"
                "Platform supported:\n"
                "• projects.co.id\n"
                "• fastwork.id\n"
                "• sribu.com\n\n"
                f"Sisa proposal hari ini: {remaining}/5"
            )
            return

        # Show typing indicator
        await tg_request(
            TELEGRAM_BOT_TOKEN, "sendChatAction",
            {"chat_id": int(chat_id), "action": "typing"}
        )

        # Extract project info from URL
        project_info = extract_project_info_from_url(project_arg)

        if not project_info["source"]:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "❌ <b>URL tidak valid</b>\n\n"
                "Platform supported:\n"
                "• projects.co.id\n"
                "• fastwork.id\n"
                "• sribu.com"
            )
            return

        # Scrape real project details
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            f"🔍 <b>Mengambil detail project...</b>\n\n"
            f"🌐 {project_info['source']}\n"
            f"🔗 {project_arg}\n\n"
            "Mohon tunggu sebentar..."
        )

        # Scrape actual project data
        detail: ProjectDetail | None = None
        try:
            loop = asyncio.get_event_loop()
            detail = await loop.run_in_executor(
                None, scrape_project_detail, project_arg
            )
        except Exception as e:
            logger.error(f"Project scrape error: {e}")

        # Use real data or fall back to partial info
        if detail:
            project_title = detail.title
            project_budget = detail.budget
            project_description = detail.description
            client_name = detail.client_name
            display_source = detail.source
        else:
            pid = project_info["project_id"] or "Unknown"
            project_title = f"Project {pid}"
            project_budget = "-"
            project_description = "Project freelance"
            client_name = "Client"
            display_source = project_info["source"]

        # Get user's CV if uploaded
        cv_text = get_user_cv_text(chat_id)
        user_profile = get_user_profile(chat_id)  # Per-user profile

        # Update typing
        await tg_request(
            TELEGRAM_BOT_TOKEN, "sendChatAction",
            {"chat_id": int(chat_id), "action": "typing"}
        )

        try:
            proposal, was_cached = await generate_proposal(
                project_title=project_title,
                project_budget=project_budget,
                project_description=project_description,
                client_name=client_name,
                project_url=project_arg,
                cv_text=cv_text,
                user_profile=user_profile,
                chat_id=chat_id,
            )
            _increment_proposal_count(chat_id)

            # Format clean copyable proposal (no buttons)
            display = format_proposal_for_display(
                proposal,
                project_title,
                display_source,
            )
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, display)

            if cv_text:
                await send_message(
                    TELEGRAM_BOT_TOKEN, chat_id,
                    f"✅ Proposal {'' if was_cached else 'di-generate '}dengan CV Anda.\n"
                    f"Sisa proposal hari ini: {remaining - 1}/5"
                )
            else:
                await send_message(
                    TELEGRAM_BOT_TOKEN, chat_id,
                    f"💡 <b>Tips:</b> Upload CV dulu dengan <code>/uploadcv</code> "
                    f"untuk proposal yang lebih personal.\n"
                    f"Sisa proposal: {remaining - 1}/5"
                )

        except Exception as e:
            logger.error(f"Proposal generation error: {e}")
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "❌ <b>Gagal generate proposal</b>\n\n"
                "Coba lagi nanti."
            )

    async def _cmd_send_proposal(self, chat_id: str, text: str):
        """Send proposal to client (after user confirmation)."""
        # Extract proposal text after "/send "
        proposal_text = text[6:].strip()

        if not proposal_text:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "📤 <b>Send Proposal</b>\n\n"
                "Usage: <code>/send [proposal_text]</code>\n\n"
                "Ini akan menampilkan proposal yang bisa Anda copy ke client."
            )
            return

        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            "✅ <b>Proposal siap digunakan!</b>\n\n"
            "Berikut proposal Anda:\n\n"
            "──────────────────────────────────\n\n"
            f"{proposal_text}\n\n"
            "──────────────────────────────────\n\n"
            "💡 <b>Tips:</b> Copy proposal di atas dan kirimkan ke client "
            "melalui platform terkait."
        )

    async def _cmd_cancel_proposal(self, chat_id: str):
        """Cancel current proposal operation."""
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            "❌ <b>Proposal dibatalkan</b>\n\n"
            "Jika butuh bantuan, ketik /help"
        )

    # ---- CV Upload ----

    async def _cmd_upload_cv(self, chat_id: str, text: str):
        """Handle /uploadcv command - instruct user to send PDF."""
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            "📄 <b>Upload CV PDF</b>\n\n"
            "Kirim file PDF CV Anda sebagai document (bukan foto).\n\n"
            "Cara:\n"
            "1. Klik ikon lampiran (📎) di chat\n"
            "2. Pilih 'Document'\n"
            "3. Pilih file PDF CV Anda\n\n"
            "CV akan disimpan secara private dan digunakan "
            "untuk membuat proposal yang lebih personal.\n\n"
            "Supported: PDF only"
        )

    async def _handle_cv_upload(self, chat_id: str, doc: dict):
        """Download PDF, extract text, and save as user's CV."""
        file_id = doc.get("file_id")
        file_name = doc.get("file_name", "cv.pdf")

        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            f"📥 <b>Mendownload CV...</b>\n\n"
            f"File: {file_name}"
        )

        try:
            # Get file path from Telegram
            from urllib.request import urlopen
            import json as _json

            # Get file info
            file_info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
            with urlopen(file_info_url, timeout=10) as resp:
                file_info = _json.loads(resp.read())

            if not file_info.get("ok"):
                raise Exception("Failed to get file info")

            file_path = file_info["result"]["file_path"]

            # Download file
            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"

            # Download to temp file
            import tempfile
            temp_path = os.path.join(tempfile.gettempdir(), f"cv_{chat_id}.pdf")

            with urlopen(file_url, timeout=30) as resp:
                with open(temp_path, "wb") as f:
                    f.write(resp.read())

            # Extract text from PDF
            cv_text = extract_text_from_pdf(temp_path)

            # Clean up temp file
            try:
                os.remove(temp_path)
            except Exception:
                pass

            if not cv_text or len(cv_text.strip()) < 20:
                await send_message(
                    TELEGRAM_BOT_TOKEN, chat_id,
                    "⚠️ <b>PDF tidak bisa dibaca</b>\n\n"
                    "Tidak ada teks yang bisa dibaca dari file ini. "
                    "Pastikan CV Anda adalah file PDF text-based, bukan scanned image."
                )
                return

            # Save CV text
            saved_text = save_user_cv(chat_id, cv_text.strip())

            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                f"✅ <b>CV Berhasil Disimpan!</b>\n\n"
                f"File: {file_name}\n"
                f"Text length: {len(saved_text)} karakter\n\n"
                f"Preview:\n"
                f"<code>{saved_text[:200]}...</code>\n\n"
                "Sekarang pakai <code>/proposal &lt;url&gt;</code> untuk "
                "generate proposal personal menggunakan CV Anda!"
            )

        except Exception as e:
            logger.error(f"CV upload error: {e}")
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "❌ <b>Gagal upload CV</b>\n\n"
                f"Error: {str(e)[:100]}\n\n"
                "Coba lagi atau gunakan format PDF lain."
            )

    async def _cmd_my_cv(self, chat_id: str):
        """Handle /mycv - show CV status."""
        cv_text = get_user_cv_text(chat_id)
        if cv_text:
            preview = cv_text[:300] + "..." if len(cv_text) > 300 else cv_text
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                f"✅ <b>CV Tersimpan</b>\n\n"
                f"Panjang: {len(cv_text)} karakter\n\n"
                f"Preview:\n"
                f"<code>{preview}</code>\n\n"
                "CV ini akan digunakan untuk generate proposal personal.\n"
                "Kirim file baru dengan <code>/uploadcv</code> untuk update."
            )
        else:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "📭 <b>Belum Ada CV</b>\n\n"
                "Anda belum upload CV. Kirim file PDF dengan "
                "<code>/uploadcv</code> untuk upload."
            )

    async def _cmd_myprofile(self, chat_id: str):
        """Handle /myprofile - show user's profile."""
        profile = get_user_profile(chat_id)
        if profile:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                f"👤 <b>Profil Anda</b>\n\n"
                f"<b>Nama:</b> {profile.get('name', '-')}\n"
                f"<b>Title:</b> {profile.get('title', '-')}\n"
                f"<b>Email:</b> {profile.get('email', '-')}\n"
                f"<b>Skills:</b> {profile.get('skills', '-')}\n"
                f"<b>Experience:</b> {profile.get('experience_years', 0)} tahun\n"
                f"<b>Portfolio:</b> {profile.get('portfolio', '-')}\n"
                f"<b>GitHub:</b> {profile.get('github', '-')}\n\n"
                f"Di-set: {profile.get('set_at', 'N/A')[:10]}\n\n"
                "Update dengan <code>/setprofile</code>"
            )
        else:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "📭 <b>Belum Ada Profil</b>\n\n"
                "Anda belum mengatur profil. Gunakan:\n"
                "<code>/setprofile</code> untuk instruksi cara set profil.\n\n"
                "Atau langsung dengan format:\n"
                "<code>/setprofile Nama|Title|Skills|Years|Portfolio</code>\n\n"
                "Contoh:\n"
                "<code>/setprofile Budi Santoso|Web Developer|JavaScript, React, Node|3|https://budi.dev</code>"
            )

    async def _cmd_setprofile(self, chat_id: str, text: str):
        """Handle /setprofile - set user's profile.
        
        Format: /setprofile Nama|Title|Skills|Years|Portfolio
        Or: /setprofile (to see instructions)
        """
        parts = text.split("|")
        
        if len(parts) < 5:
            # Show instructions
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "📝 <b>Set Profil Freelancer</b>\n\n"
                "Format: <code>/setprofile Nama|Title|Skills|Years|Portfolio</code>\n\n"
                "Contoh:\n"
                "<code>/setprofile Budi Santoso|Web Developer|JavaScript, React, Node|3|https://budi.dev</code>\n\n"
                "Field:\n"
                "• <b>Nama</b> - Nama lengkap Anda\n"
                "• <b>Title</b> - Judul profesional (Web Developer, Designer, dll)\n"
                "• <b>Skills</b> - Skill dipisahkan koma\n"
                "• <b>Years</b> - Tahun pengalaman (angka)\n"
                "• <b>Portfolio</b> - Link portfolio/GitHub\n\n"
                "Opsional (kosongkan dengan -):\n"
                "• Email, GitHub, LinkedIn, Phone\n\n"
                "Setelah set profil, upload CV dengan <code>/uploadcv</code> untuk proposal lebih personal!"
            )
            return
        
        try:
            name = parts[0].strip() or "-"
            title = parts[1].strip() or "-"
            skills = parts[2].strip() or "-"
            experience_years = int(parts[3].strip()) if parts[3].strip().isdigit() else 0
            portfolio = parts[4].strip() or "-"
            email = parts[5].strip() if len(parts) > 5 and parts[5].strip() != "-" else ""
            github = parts[6].strip() if len(parts) > 6 and parts[6].strip() != "-" else ""
            
            profile = save_user_profile(chat_id, {
                "name": name,
                "title": title,
                "skills": skills,
                "experience_years": experience_years,
                "portfolio": portfolio,
                "email": email,
                "github": github,
            })
            
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                f"✅ <b>Profil Berhasil Disimpan!</b>\n\n"
                f"<b>Nama:</b> {profile['name']}\n"
                f"<b>Title:</b> {profile['title']}\n"
                f"<b>Skills:</b> {profile['skills']}\n"
                f"<b>Experience:</b> {profile['experience_years']} tahun\n"
                f"<b>Portfolio:</b> {profile['portfolio']}\n\n"
                "Profil ini akan digunakan untuk generate AI proposal.\n"
                "Upload CV dengan <code>/uploadcv</code> untuk hasil lebih personal!"
            )
        except Exception as e:
            logger.error(f"Set profile error: {e}")
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "❌ <b>Gagal menyimpan profil</b>\n\n"
                f"Error: {str(e)[:100]}\n\n"
                "Pastikan format benar:\n"
                "<code>/setprofile Nama|Title|Skills|Years|Portfolio</code>"
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
        """Show Fastwork job categories — ALL categories, not just monitored."""
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
            f"⚡ <b>Fastwork Categories</b> (semua)\n\n{cat_text}\n\nPilih kategori:",
            reply_markup={"inline_keyboard": buttons},
        )

    async def _fw_show_page(
        self, chat_id: str, message_id: int, tag_id: str, page: int
    ):
        """Show a page of Fastwork jobs for a given tag (local pagination).

        Jobs are cached in fw_cache for detail view resolution.
        Each job gets a 'View' URL button and a 'Detail' callback button.
        Only shows jobs from monitored tags.
        """
        PER_PAGE = 8
        monitored = self.fw_monitor.monitored_tags

        # "all" → only monitored tags; specific tag → verify it's monitored
        if tag_id == "all":
            all_jobs, _ = get_jobs_by_tag(tag_id=None, max_pages=10)
            # Filter to only monitored tags
            all_jobs = [j for j in all_jobs if j.tag_id in monitored]
        else:
            if tag_id not in monitored:
                await edit_message(
                    TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                    "⚠️ Kategori ini tidak kamu monitor.\n\nGunakan /fw setup untuk menambahkan.",
                    reply_markup={"inline_keyboard": [
                        [{"text": "🔙 Back to Categories", "callback_data": "fw:browse"}],
                        [{"text": "🔙 Back to Fastwork", "callback_data": "src:fastwork"}],
                    ]},
                )
                return
            all_jobs, _ = get_jobs_by_tag(tag_id=tag_id, max_pages=10)

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

        text = format_fastwork_jobs_list(page_jobs, cat_name, page, total_pages, start)

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

        # Cache URL for AI proposal button
        proposal_key = self._cache_proposal_url(job.link)

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔗 View Full Job", "url": job.link},
                ],
                [
                    {
                        "text": "📝 Generate AI Proposal",
                        "callback_data": f"proposal:fw:{proposal_key}",
                    }
                ],
                [
                    {"text": "🔙 Back to Jobs", "callback_data": f"fwcat:{job.tag_id}:1"},
                ],
            ]
        }

        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            text,
            reply_markup=keyboard,
        )

    async def _fw_refresh(self, chat_id: str, message_id: int, callback_id: str):
        """Show latest Fastwork jobs from monitored categories only."""
        monitored = self.fw_monitor.monitored_tags
        if not monitored:
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id,
                text="⚠️ Kamu tidak memantau kategori Fastwork mana pun.\nGunakan /fw setup untuk menambahkan.")
            return

        all_jobs, _ = get_jobs_by_tag(max_pages=3)
        # Filter to only monitored tags
        all_jobs = [j for j in all_jobs if j.tag_id in monitored]
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

        text = format_sribu_contests_list(page_contests, cat_name, page, total_pages, start)

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

        # Cache URL for AI proposal button
        proposal_key = self._cache_proposal_url(contest.contest_url)

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔗 View Contest", "url": contest.contest_url},
                ],
                [
                    {
                        "text": "📝 Generate AI Proposal",
                        "callback_data": f"proposal:sribu:{proposal_key}",
                    }
                ],
                [
                    {"text": "🔙 Back to Contests", "callback_data": "sribu_cat:all:1"},
                ],
            ]
        }

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
            status_text = format_monitor_status(self.monitor)
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                status_text,
                reply_markup=build_monitor_keyboard(self.monitor),
            )
        elif action == "status":
            status_text = format_monitor_status(self.monitor)
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                status_text,
                reply_markup=build_monitor_keyboard(self.monitor),
            )
        elif action == "refresh":
            await self._cmd_refresh(chat_id, message_id)
        elif action == "digest":
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id, text="📈 Generating digest...")
            await self._cmd_digest(chat_id)
        elif action == "trends":
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id, text="📉 Loading trends...")
            await self._cmd_trends(chat_id)
        elif action == "topclients":
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id, text="🏆 Loading top clients...")
            await self._cmd_top_clients(chat_id)
        elif action == "proposal":
            # Send NEW message bubble instead of editing
            await send_message(
                TELEGRAM_BOT_TOKEN,
                chat_id,
                "📝 <b>Generate AI Proposal</b>\n\n"
                "Ada 2 cara untuk generate proposal:\n\n"
                "1️⃣ <b>Dari daftar project:</b>\n"
                "Buka project → Klik tombol <b>📝 Generate AI Proposal</b>\n\n"
                "2️⃣ <b>Dari URL:</b>\n"
                "Ketik: <code>/proposal [URL_project]</code>\n\n"
                "Contoh:\n"
                "<code>/proposal https://projects.co.id/view/abc123/project-title</code>\n\n"
                "Platform supported:\n"
                "• projects.co.id\n"
                "• fastwork.id\n"
                "• sribu.com",
            )
        elif action == "uploadcv":
            # Send NEW message bubble instead of editing
            await send_message(
                TELEGRAM_BOT_TOKEN,
                chat_id,
                "📄 <b>Upload CV PDF</b>\n\n"
                "Kirim file PDF CV Anda sebagai document (bukan foto).\n\n"
                "Cara:\n"
                "1. Klik ikon lampiran (📎) di chat\n"
                "2. Pilih 'Document'\n"
                "3. Pilih file PDF CV Anda\n\n"
                "CV akan disimpan secara private dan digunakan "
                "untuk membuat proposal yang lebih personal.",
            )
        elif action == "mycv":
            # Send NEW message bubble instead of editing
            cv_text = get_user_cv_text(chat_id)
            if cv_text:
                preview = cv_text[:300] + "..." if len(cv_text) > 300 else cv_text
                await send_message(
                    TELEGRAM_BOT_TOKEN,
                    chat_id,
                    f"✅ <b>CV Tersimpan</b>\n\n"
                    f"Panjang: {len(cv_text)} karakter\n\n"
                    f"Preview:\n"
                    f"<code>{preview}</code>\n\n"
                    "CV ini akan digunakan untuk generate proposal personal.",
                )
            else:
                await send_message(
                    TELEGRAM_BOT_TOKEN,
                    chat_id,
                    "📭 <b>Belum Ada CV</b>\n\n"
                    "Anda belum upload CV. Kirim file PDF dengan "
                    "<code>/uploadcv</code> untuk upload.",
                )
        elif action == "help":
            await self._cmd_help_text(chat_id)

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

        text = format_project_list(page_projects, category, page, total_pages, start)
        kb = self._build_project_keyboard(category_id, page, total_pages, page_projects)

        await edit_message(
            TELEGRAM_BOT_TOKEN, int(chat_id), message_id, text, reply_markup=kb
        )

    def _build_project_keyboard(
        self, category_id: str, page: int, total_pages: int, projects: list[Project]
    ) -> dict:
        """Build keyboard with pagination and project detail buttons."""
        buttons = []

        # Action buttons row (Browse + Monitor)
        buttons.append([
            {"text": "📂 Kategori Lain", "callback_data": "catlist"},
            {"text": "🔔 Monitor", "callback_data": "menu:monitor"},
        ])

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

        buttons.append([{"text": "📂 Browse Kategori", "callback_data": "catlist"}])
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

        text = format_project_list(page_projects, category, page, total_pages, start)
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

        # Cache URL for AI proposal button
        proposal_key = self._cache_proposal_url(project.link)

        # Keyboard with View Project + AI Proposal
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
                        "text": "📝 Generate AI Proposal",
                        "callback_data": f"proposal:projects:{proposal_key}",
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

    # ── Proposal URL Cache Helpers ─────────────────────────────────────────────

    def _cache_proposal_url(self, url: str) -> str:
        """Store a project URL and return a short cache key for AI proposal."""
        self._proposal_url_counter += 1
        key = str(self._proposal_url_counter)
        self._proposal_url_cache[key] = url
        # Keep cache bounded
        if len(self._proposal_url_cache) > 100:
            oldest = next(iter(self._proposal_url_cache))
            del self._proposal_url_cache[oldest]
        return key

    async def _cb_ai_proposal(
        self, chat_id: str, message_id: int, source: str,
        proj_url: str, callback_id: str
    ):
        """Handle AI Proposal button from any project detail view."""
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)

        if not proj_url:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "❌ <b>Link project tidak ditemukan</b>\n\n"
                "Coba buka detail project lagi dari daftar."
            )
            return

        # Show loading — EDIT existing message
        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            f"📝 <b>Generating AI Proposal...</b>\n\n"
            f"🌐 {proj_url}\n\n"
            "Mohon tunggu sebentar, AI sedang membuat proposal...",
            reply_markup={"inline_keyboard": [[{"text": "⏳ Processing...", "callback_data": "noop"}]]},
        )

        # Check rate limit
        allowed, remaining = _check_rate_limit(chat_id)
        if not allowed:
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                f"⏳ <b>Batas proposal harian tercapai</b>\n\n"
                f"Sisa: 0/5. Coba lagi besok!",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Main Menu", "callback_data": "menu:back"}]]},
            )
            return

        try:
            cv_text = get_user_cv_text(chat_id)
            user_profile = get_user_profile(chat_id)  # Per-user profile
            loop = asyncio.get_event_loop()

            # Scrape project detail in executor
            detail = await loop.run_in_executor(None, scrape_project_detail, proj_url)

            if detail:
                project_title = detail.title
                project_budget = detail.budget
                project_description = detail.description
                client_name = detail.client_name
                display_source = detail.source
            else:
                project_title = "Project"
                project_budget = "-"
                project_description = "Project freelance"
                client_name = "Client"
                display_source = source

            # Generate proposal (uses user_profile if set, otherwise falls back to default)
            proposal, was_cached = await generate_proposal(
                project_title=project_title,
                project_budget=project_budget,
                project_description=project_description,
                client_name=client_name,
                project_url=proj_url,
                cv_text=cv_text,
                user_profile=user_profile,
                chat_id=chat_id,
            )
            _increment_proposal_count(chat_id)

            display = format_proposal_for_display(proposal, project_title, display_source)
            # Send proposal as new message (it's long, edit may hit length limit)
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, display)

            tip_text = (
                f"✅ Proposal {'' if was_cached else 'di-generate '}dengan CV Anda.\n"
                f"Sisa proposal: {remaining - 1}/5\n\n"
                "Pilih menu di bawah:" if cv_text else (
                    f"💡 <b>Tips:</b> Upload CV dengan <code>/uploadcv</code> "
                    f"untuk proposal lebih personal.\n"
                    f"Sisa proposal: {remaining - 1}/5\n\n"
                    "Pilih menu di bawah:"
                )
            )

            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                tip_text,
                reply_markup=build_main_menu_keyboard(),
            )

        except Exception as e:
            logger.error(f"AI Proposal error: {e}")
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                "❌ <b>Gagal generate proposal</b>\n\n"
                "Coba lagi nanti.\n\n"
                "Pilih menu di bawah:",
                reply_markup=build_main_menu_keyboard(),
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

    async def _send_startup_notification(self):
        """Send startup notification to configured chat IDs."""
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

        chat_ids = get_chat_ids()
        if not chat_ids:
            logger.warning("No TELEGRAM_CHAT_ID configured, skipping startup notification")
        elif len(chat_ids) == 1:
            await send_message(
                TELEGRAM_BOT_TOKEN,
                chat_ids[0],
                "🤖 <b>Freelance Monitor Bot Active!</b>\n\n"
                "🌐 <b>Projects.co.id</b>\n"
                f"🔔 Monitoring <b>{len(monitored)}</b> kategori\n\n"
                "⚡ <b>Fastwork.id</b>\n"
                f"🔔 Monitoring <b>{len(fw_monitored)}</b> kategori\n"
                f"{fw_list or '  ⬜ Belum ada yang dimonitor'}\n\n"
                "🎨 <b>Sribu.com</b>\n"
                f"🔔 Monitoring <b>{len(sribu_monitored)}</b> kategori\n"
                f"{sribu_list or '  ✔ Belum ada yang dimonitor'}\n\n"
                f"Polling setiap <b>{POLL_INTERVAL_SECONDS}s</b>",
                reply_markup=build_main_menu_keyboard(),
            )
        else:
            # Multiple recipients — use broadcast for startup message
            await broadcast(
                TELEGRAM_BOT_TOKEN,
                chat_ids,
                "🤖 <b>Freelance Monitor Bot Active!</b>\n\n"
                "🌐 <b>Projects.co.id</b>\n"
                f"🔔 Monitoring <b>{len(monitored)}</b> kategori\n\n"
                "⚡ <b>Fastwork.id</b>\n"
                f"🔔 Monitoring <b>{len(fw_monitored)}</b> kategori\n"
                f"{fw_list or '  ⬜ Belum ada yang dimonitor'}\n\n"
                "🎨 <b>Sribu.com</b>\n"
                f"🔔 Monitoring <b>{len(sribu_monitored)}</b> kategori\n"
                f"{sribu_list or '  ⬜ Belum ada yang dimonitor'}\n\n"
                f"Polling setiap <b>{POLL_INTERVAL_SECONDS}s</b>",
                reply_markup=build_main_menu_keyboard(),
            )

    async def start_polling(self):
        """Start polling mode - handles both Telegram messages AND project monitoring.
        
        This combines Telegram polling via getUpdates with the monitoring loop.
        Use this when WEBHOOK_ENABLED=false (default).
        """
        self._running = True
        logger.info(f"Monitoring started. Polling every {POLL_INTERVAL_SECONDS}s")

        # Seed existing projects so we only notify truly new ones
        await self._seed_seen_projects()

        # Send startup notification
        await self._send_startup_notification()

        # Run monitoring loop (this includes all scraping logic)
        await self.monitoring_loop()

    async def monitoring_loop(self):
        """Monitoring loop - scrapes projects and sends notifications.
        
        This is the scraping-only loop (no Telegram polling).
        Used when webhook mode is enabled - Telegram messages are handled by webhook_server().
        """
        self._running = True
        logger.info(f"Monitoring loop started. Polling every {POLL_INTERVAL_SECONDS}s")

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
                            # Record trend stats
                            published_hour = None
                            if p.published_date:
                                try:
                                    time_part = p.published_date.split(" ")[1] if len(p.published_date.split(" ")) > 1 else None
                                    if time_part:
                                        published_hour = int(time_part.split(":")[0])
                                except (ValueError, IndexError):
                                    pass
                            record_project(category["id"], p.budget, published_hour, "projects")

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
                            chat_ids = get_chat_ids()
                            if len(chat_ids) > 1:
                                await broadcast(
                                    TELEGRAM_BOT_TOKEN, chat_ids,
                                    header + msg,
                                )
                            else:
                                await send_message(
                                    TELEGRAM_BOT_TOKEN, chat_ids[0],
                                    header + msg,
                                )
                            self.tracker.mark_seen(p.project_id)
                            await asyncio.sleep(0.5)

                        if len(new_projects) > 5:
                            chat_ids = get_chat_ids()
                            if len(chat_ids) > 1:
                                await broadcast(
                                    TELEGRAM_BOT_TOKEN, chat_ids,
                                    f"...dan <b>{len(new_projects) - 5}</b> project lainnya. "
                                    f"Gunakan /browse untuk lihat semua.",
                                )
                            else:
                                await send_message(
                                    TELEGRAM_BOT_TOKEN, chat_ids[0],
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
                                if tag_id not in self.fw_monitor.monitored_tags:
                                    continue  # skip unmonitored categories
                                cat_name = cats.get(tag_id, "Unknown")
                                # Deduplicate: some jobs may appear on multiple pages in API response
                                seen_in_batch: set[str] = set()
                                unique_jobs = [
                                    j for j in jobs
                                    if j.job_id not in seen_in_batch and not self.fw_tracker.is_seen(j.job_id)
                                ]
                                unique_jobs = unique_jobs[:5]
                                for i, job in enumerate(unique_jobs):
                                    text = format_fastwork_job_card(job, i)
                                    keyboard = _build_fastwork_detail_keyboard(job)
                                    chat_ids = get_chat_ids()
                                    if len(chat_ids) > 1:
                                        await broadcast(
                                            TELEGRAM_BOT_TOKEN, chat_ids,
                                            text, reply_markup=keyboard,
                                        )
                                    else:
                                        await send_message(
                                            TELEGRAM_BOT_TOKEN, chat_ids[0],
                                            text, reply_markup=keyboard,
                                        )
                                    self.fw_tracker.mark_seen(job.job_id)
                                    await asyncio.sleep(0.5)

                                if len(unique_jobs) < len(new_jobs):
                                    chat_ids = get_chat_ids()
                                    if len(chat_ids) > 1:
                                        await broadcast(
                                            TELEGRAM_BOT_TOKEN, chat_ids,
                                            f"...dan <b>{len(new_jobs) - 8}</b> job Fastwork lainnya. "
                                            f"Gunakan /fw untuk lihat semua.",
                                        )
                                    else:
                                        await send_message(
                                            TELEGRAM_BOT_TOKEN, chat_ids[0],
                                            f"...dan <b>{len(new_jobs) - 8}</b> job Fastwork lainnya. "
                                            f"Gunakan /fw untuk lihat semua.",
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
                                    first_msg = f"🎨 <b>Contest Baru!</b> di {cat_emoji} <b>{cat_name}</b>\n\n" + text if i == 0 else text
                                    chat_ids = get_chat_ids()
                                    if len(chat_ids) > 1:
                                        await broadcast(
                                            TELEGRAM_BOT_TOKEN, chat_ids,
                                            first_msg, reply_markup=keyboard,
                                        )
                                    else:
                                        await send_message(
                                            TELEGRAM_BOT_TOKEN, chat_ids[0],
                                            first_msg, reply_markup=keyboard,
                                        )
                                    self.sribu_tracker.mark_seen(contest.contest_id)
                                    await asyncio.sleep(0.5)

                            if len(new_contests) > 8:
                                chat_ids = get_chat_ids()
                                if len(chat_ids) > 1:
                                    await broadcast(
                                        TELEGRAM_BOT_TOKEN, chat_ids,
                                        f"...dan <b>{len(new_contests) - 8}</b> contest Sribu lainnya. "
                                        f"Gunakan /sribu untuk lihat semua.",
                                    )
                                else:
                                    await send_message(
                                        TELEGRAM_BOT_TOKEN, chat_ids[0],
                                        f"...dan <b>{len(new_contests) - 8}</b> contest Sribu lainnya. "
                                        f"Gunakan /sribu untuk lihat semua.",
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
    # ⚔️ Out-of-the-box Commands
    # ============================================================

    async def _cmd_ghost(self, chat_id: str, text: str):
        """Analyze project for ghosting probability."""
        # Parse: /ghost Project Title | Budget | Description
        args = text.replace("/ghost", "").strip()
        if not args:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "👻 <b>Ghost Detector</b>\n\n"
                "Kirim detail project untuk dianalisis:\n\n"
                "<code>/ghost Judul Project | Budget | Deskripsi singkat</code>\n\n"
                "Contoh:\n"
                "<code>/ghost Buat website Tokopedia | 1.5jt | ikutin saja fiturnya</code>",
            )
            return

        parts = args.split("|", 2)
        title = parts[0].strip() if len(parts) > 0 else "Project Unknown"
        budget_str = parts[1].strip() if len(parts) > 1 else ""
        desc = parts[2].strip() if len(parts) > 2 else ""

        # Parse budget
        budget = None
        if budget_str:
            budget = _parse_budget(budget_str)

        # Try to get client info from DB
        client_count = 0
        try:
            from database import get_db
            with get_db() as conn:
                cursor = conn.cursor()
                # Quick client lookup
                pass
        except Exception:
            pass

        result = detect_ghost(
            title=title,
            description=desc,
            budget=budget,
            client_project_count=client_count,
        )
        report = format_ghost_report(result, title)
        await send_message(TELEGRAM_BOT_TOKEN, chat_id, report)

    async def _cmd_skill_radar(self, chat_id: str):
        """Show skill gap radar — trending skills vs user CV."""
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            "📡 <b>Skill Gap Radar</b>\n\n"
            "🔍 Menganalisis trend skill dari semua platform...",
        )

        try:
            from database import get_db
            with get_db() as conn:
                current = get_projects_for_period(conn, days=30)
                previous = get_projects_for_period(conn, days=60)

            if not current:
                await send_message(
                    TELEGRAM_BOT_TOKEN, chat_id,
                    "⚠️ Belum cukup data project untuk analisis skill.\n"
                    "Tunggu beberapa hari sampai data terkumpul.",
                )
                return

            curr_skills = extract_skills_from_projects(current)
            prev_skills = extract_skills_from_projects(previous)

            cv_skills = []
            try:
                profile = get_user_profile(chat_id)
                if profile and profile.get("skills"):
                    cv_skills = [s.strip() for s in profile["skills"].split(",")]
            except Exception:
                pass

            if not cv_skills:
                cv_skills = ["Laravel", "PHP", "JavaScript", "MySQL", "Bootstrap", "HTML", "CSS"]

            analysis = compare_with_cv(curr_skills, cv_skills)
            trends = analyze_trends(curr_skills, prev_skills)

            report = format_skill_radar(analysis, trends)
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, report)

        except Exception as e:
            logger.error(f"Skill radar error: {e}")
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                f"❌ Gagal menganalisis skill: {e}",
            )

    async def _cmd_timing(self, chat_id: str):
        """Show bid timing oracle — optimal bid windows."""
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            "⏰ <b>Bid Timing Oracle</b>\n\n"
            "🔍 Menganalisis pola posting project...",
        )

        try:
            from database import get_db
            with get_db() as conn:
                projects = get_projects_with_dates(conn, days=90)

            if len(projects) < 10:
                await send_message(
                    TELEGRAM_BOT_TOKEN, chat_id,
                    "⚠️ Belum cukup data untuk analisis timing.\n"
                    f"Data tersedia: {len(projects)} project. Butuh minimal 10.",
                )
                return

            oracle = TimingOracle()
            analysis = oracle.analyze(projects)
            recs = oracle.recommend(analysis)
            report = format_timing_report(analysis, recs)

            await send_message(TELEGRAM_BOT_TOKEN, chat_id, report)

        except Exception as e:
            logger.error(f"Timing oracle error: {e}")
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                f"❌ Gagal menganalisis timing: {e}",
            )

    async def _cmd_battle(self, chat_id: str, text: str):
        """Generate 3 persona proposals for a project."""
        args = text.replace("/battle", "").replace("/battleroyale", "").strip()

        if not args:
            await send_message(
                TELEGRAM_BOT_TOKEN, chat_id,
                "⚔️ <b>Proposal Battle Royale</b>\n\n"
                "Generate 3 proposal dengan persona berbeda.\n\n"
                "<b>Format:</b>\n"
                "<code>/battle Judul Project | Budget | Deskripsi</code>",
            )
            return

        parts = args.split("|", 2)
        title = parts[0].strip() if len(parts) > 0 else ""
        budget = parts[1].strip() if len(parts) > 1 else ""
        desc = parts[2].strip() if len(parts) > 2 else ""

        if not title:
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, "❌ Judul project diperlukan.")
            return

        await send_message(TELEGRAM_BOT_TOKEN, chat_id, format_battle_intro(title))

        cv_skills = None
        try:
            profile = get_user_profile(chat_id)
            if profile and profile.get("skills"):
                cv_skills = [s.strip() for s in profile["skills"].split(",")]
        except Exception:
            pass

        battle = generate_all_personas(
            project_title=title,
            project_description=desc,
            project_budget=budget,
            cv_skills=cv_skills,
        )

        for key in ["agresif", "premium", "teknis"]:
            data = battle[key]
            try:
                proposal_text = await self._generate_battle_proposal(data["prompt"])
                formatted = format_persona_result(key, proposal_text)

                if len(formatted) > 4000:
                    chunks = [formatted[i:i+3800] for i in range(0, len(formatted), 3800)]
                    for chunk in chunks:
                        await send_message(TELEGRAM_BOT_TOKEN, chat_id, chunk)
                else:
                    await send_message(TELEGRAM_BOT_TOKEN, chat_id, formatted)

            except Exception as e:
                logger.error(f"Error generating {key} proposal: {e}")
                await send_message(
                    TELEGRAM_BOT_TOKEN, chat_id,
                    f"⚠️ Gagal generate proposal {PERSONAS[key]['name']}: {e}",
                )

    async def _generate_battle_proposal(self, prompt: str) -> str:
        """Generate a proposal using the existing LLM pipeline."""
        try:
            from proposal_generator import OPENROUTER_API_KEY, OPENROUTER_MODEL

            if not OPENROUTER_API_KEY:
                return (
                    "⚠️ <i>OpenRouter API key tidak ditemukan.</i>\n\n"
                    "Copy prompt ini ke ChatGPT/Claude:\n\n"
                    f"<code>{prompt[:500]}...</code>"
                )

            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "max_tokens": 1500,
            }

            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status != 200:
                        return f"⚠️ Gagal generate: HTTP {resp.status}"
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]

        except ImportError:
            return f"⚠️ <i>proposal_generator tidak tersedia.</i>\n\n<code>{prompt[:800]}...</code>"
        except Exception as e:
            return f"⚠️ Error: {e}"

    async def _cmd_personas(self, chat_id: str):
        """Show persona comparison."""
        await send_message(
            TELEGRAM_BOT_TOKEN, chat_id,
            compare_personas(),
        )


async def fetch_updates(token: str, offset: int = 0, timeout: int = 30) -> dict:
    """Fetch updates via long polling."""
    payload = {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }

    try:
        result = await tg_request(token, "getUpdates", payload)
        return result
    except Exception as e:
        logger.error(f"fetch_updates error: {e}")
        await asyncio.sleep(2)
        return {"ok": False, "result": []}


# ============================================================
# Webhook Server
# ============================================================


async def set_webhook(token: str, url: str, secret: str) -> dict:
    """Register the webhook URL with Telegram."""
    # First delete any existing webhook
    await tg_request(token, "deleteWebhook", {})
    # Set new webhook with secret token
    payload = {
        "url": url,
        "secret_token": secret,
    }
    result = await tg_request(token, "setWebhook", payload)
    if result.get("ok"):
        logger.info(f"Webhook set to {url}")
    else:
        logger.error(f"Failed to set webhook: {result}")
    return result


async def webhook_server(bot: 'ProjectsBot'):
    """Run the aiohttp webhook server on port 8082."""
    async def handle_webhook(request):
        """Handle incoming Telegram webhook updates."""
        # Validate secret token
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != WEBHOOK_SECRET:
            logger.warning(f"Invalid webhook secret from {request.remote}")
            return web.Response(status=401, text="Unauthorized")

        try:
            update = await request.json()
            logger.info(f"Webhook update received: {update.get('update_id', 'unknown')}")
            # Process the update using the bot's handler
            await bot.handle_update(update)
            return web.Response(status=200, text="OK")
        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
            return web.Response(status=500, text="Internal Server Error")

    async def handle_health(request):
        """Health check endpoint."""
        return web.Response(status=200, text="OK")

    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server started on 0.0.0.0:{WEBHOOK_PORT}")

    # Keep server running
    while True:
        await asyncio.sleep(3600)


# ============================================================
# Main Entry Point
# ============================================================


async def main():
    bot = ProjectsBot()

    if WEBHOOK_ENABLED:
        # Webhook mode: handle Telegram via webhook, monitoring loop runs separately
        logger.info(f"Starting in WEBHOOK mode")
        logger.info(f"Webhook URL: {WEBHOOK_URL}")

        # Set webhook with Telegram
        await set_webhook(TELEGRAM_BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET)

        # Seed existing projects so we only notify truly new ones
        await bot._seed_seen_projects()

        # Send startup notification
        await bot._send_startup_notification()

        # Start webhook server (handles Telegram messages) and monitoring loop (scrapes projects)
        # These run in parallel - webhook handles user commands, monitoring_loop handles scraping
        webhook_task = asyncio.create_task(webhook_server(bot))
        monitor_task = asyncio.create_task(bot.monitoring_loop())

        logger.info("Running with webhook + monitoring loop")
        await asyncio.gather(webhook_task, monitor_task)
    else:
        # Polling mode (default): monitoring loop + Telegram polling in parallel
        logger.info("Starting in POLLING mode (WEBHOOK_ENABLED=false)")

        # Seed existing projects so we only notify truly new ones
        await bot._seed_seen_projects()

        # Send startup notification
        await bot._send_startup_notification()

        # Start monitoring loop in background task
        monitor_task = asyncio.create_task(bot.monitoring_loop())

        # Start Telegram long-polling loop (handles /start, /browse, etc.)
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
