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
import json
import logging
import asyncio
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

import httpx
from scraper import (
    scrape_listing,
    scrape_project_detail,
    CATEGORIES,
    get_category_by_id,
    Project,
)
from user_settings import UserSettings, parse_budget_rp
from proposal_gen import generate_proposal, format_proposal_short

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Config
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", "")
# Support multiple chat IDs (comma-separated)
_raw_chat_ids = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw_chat_ids.split(",") if cid.strip()]
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "300"))  # default 5 min
PROJECTS_PER_PAGE = int(os.getenv("PROJECTS_PER_PAGE", "10"))
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SEEN_FILE = os.path.join(DATA_DIR, "seen_projects.json")
MONITOR_FILE = os.path.join(DATA_DIR, "monitor_config.json")

os.makedirs(DATA_DIR, exist_ok=True)

# Telegram API base
TG_API = "https://api.telegram.org/bot"

# --- Shared HTTP client for connection pooling ---
_http_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared httpx AsyncClient with connection pooling."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client

async def close_http_client():
    """Close the shared HTTP client. Call on shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

# --- Cross-user category cache (avoids re-scraping same category for different users) ---
# Key: category_id, Value: (projects_list, timestamp)
_category_cache: dict[str, tuple[list, float]] = {}
CACHE_TTL_SECONDS = 120  # Cache valid for 2 minutes per poll cycle

def _get_cached_projects(category_id: str) -> list | None:
    """Return cached projects for a category if still fresh, else None."""
    if category_id in _category_cache:
        projects, ts = _category_cache[category_id]
        if time.time() - ts < CACHE_TTL_SECONDS:
            return projects
    return None

def _set_cached_projects(category_id: str, projects: list):
    """Cache projects for a category."""
    _category_cache[category_id] = (projects, time.time())

def _clear_category_cache():
    """Clear the cross-user category cache at the start of each poll cycle."""
    global _category_cache
    _category_cache = {}



# ============================================================
# Data Persistence
# ============================================================


class SeenTracker:
    """Persistently tracks which project IDs have been notified, per-user."""

    def __init__(self, chat_id: str | None = None, data_file: str | None = None):
        self.chat_id = chat_id
        if data_file:
            self.data_file = data_file
        elif chat_id:
            user_dir = os.path.join(DATA_DIR, str(chat_id))
            os.makedirs(user_dir, exist_ok=True)
            self.data_file = os.path.join(user_dir, "seen_projects.json")
        else:
            self.data_file = SEEN_FILE
        self.seen_ids: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r") as f:
                    self.seen_ids = set(json.load(f))
                logger.info(f"Loaded {len(self.seen_ids)} seen project IDs for {self.chat_id or 'global'}")
            except (json.JSONDecodeError,) as e:
                logger.error(f"Error loading seen projects: {e}")
                self.seen_ids = set()

    def _save(self):
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        with open(self.data_file, "w") as f:
            json.dump(list(self.seen_ids), f, indent=2)

    def is_seen(self, project_id: str) -> bool:
        return project_id in self.seen_ids

    def mark_seen(self, project_id: str):
        self.seen_ids.add(project_id)
        self._save()


class MonitorConfig:
    """Manages per-category monitoring configuration, per-user aware."""

    def __init__(self, chat_id: str | None = None, data_file: str | None = None):
        self.chat_id = chat_id
        if data_file:
            self.data_file = data_file
        elif chat_id:
            user_dir = os.path.join(DATA_DIR, str(chat_id))
            os.makedirs(user_dir, exist_ok=True)
            self.data_file = os.path.join(user_dir, "monitor_config.json")
        else:
            self.data_file = MONITOR_FILE  # global fallback
        self.monitored_categories: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r") as f:
                    data = json.load(f)
                    self.monitored_categories = set(data.get("categories", []))
                logger.info(f"Loaded monitor config for {self.chat_id or 'global'}: {self.monitored_categories}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Error loading monitor config: {e}")
                self.monitored_categories = set()

    def _save(self):
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
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

    def set_all(self, category_ids: list[str]):
        """Set all monitored categories at once."""
        self.monitored_categories = set(category_ids)
        self._save()


# ============================================================
# Telegram API Helpers
# ============================================================


async def tg_request(token: str, method: str, payload: dict) -> dict:
    """Make a request to Telegram Bot API using shared connection pool."""
    url = f"{TG_API}{token}/{method}"
    client = get_http_client()
    response = await client.post(url, json=payload)
    return response.json()


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


async def broadcast_message(
    token: str,
    chat_ids: list[str],
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str = "HTML",
    delay: float = 0.1,
) -> dict:
    """Send a message to multiple chat IDs."""
    results = {}
    for chat_id in chat_ids:
        try:
            result = await send_message(token, chat_id, text, reply_markup, parse_mode)
            results[chat_id] = result
            if delay > 0 and chat_id != chat_ids[-1]:
                await asyncio.sleep(delay)  # Rate limiting between sends
        except Exception as e:
            logger.error(f"Failed to send to {chat_id}: {e}")
            results[chat_id] = {"ok": False, "error": str(e)}
    return results


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
    """Build the main menu inline keyboard."""
    return {
        "inline_keyboard": [
            [{"text": "📋 Browse Projects", "callback_data": "menu:browse"}],
            [{"text": "🔔 Monitor Settings", "callback_data": "menu:monitor"}],
            [{"text": "🔄 Refresh Now", "callback_data": "menu:refresh"}],
            [{"text": "ℹ️ Help", "callback_data": "menu:help"}],
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


# ============================================================
# Project Cache (for pagination callbacks)
# ============================================================


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
    """Interactive Telegram bot for projects.co.id with per-user settings."""

    def __init__(self):
        # Global instances for startup/seeding only — not used for per-user ops
        self.tracker = SeenTracker()  # global seen tracker (legacy)
        self.monitor = MonitorConfig()  # global monitor config (legacy)
        self.cache = ProjectCache()
        self._running = False

    # Per-user instance factories
    def _settings(self, chat_id: str) -> UserSettings:
        return UserSettings(chat_id=chat_id)

    def _monitor(self, chat_id: str) -> MonitorConfig:
        return MonitorConfig(chat_id=chat_id)

    def _tracker(self, chat_id: str) -> SeenTracker:
        return SeenTracker(chat_id=chat_id)

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
        elif text.startswith("/keyword") or text.startswith("/keywords"):
            await self._cmd_keyword(chat_id, text)
        elif text.startswith("/skill") or text.startswith("/skills"):
            await self._cmd_skill(chat_id, text)
        elif text.startswith("/budget"):
            await self._cmd_budget(chat_id, text)
        elif text.startswith("/propose") or text.startswith("/proposal"):
            await self._cmd_propose(chat_id, text, message)
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
            else:
                await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)
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
            "🤖 <b>Projects.co.id Bot</b>\n\n"
            "Bot untuk memantau project freelance terbaru dari Projects.co.id\n\n"
            "✨ <b>Fitur:</b>\n"
            "• 📋 Browse project per kategori\n"
            "• 🔔 Auto-notifikasi project baru\n"
            "• 📄 Pagination (10 project/halaman)\n"
            "• ⚙️ Konfigurasi monitoring per kategori\n\n"
            "Gunakan menu di bawah untuk mulai! 👇",
            reply_markup=build_main_menu_keyboard(),
        )

    async def _cmd_browse(self, chat_id: str):
        await self._cb_category_list(chat_id, None, None)

    async def _cmd_monitor(self, chat_id: str):
        monitor = self._monitor(chat_id)
        status_text = format_monitor_status(monitor)
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            status_text,
            reply_markup=build_monitor_keyboard(monitor),
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

        tracker = self._tracker(chat_id)
        new_projects = [p for p in projects if not tracker.is_seen(p.project_id)]

        if new_projects:
            text = f"🆕 <b>{len(new_projects)} Project Baru Ditemukan!</b>\n\n"
            for i, p in enumerate(reversed(new_projects[:10])):
                text += format_project_card(p, i)
                text += "\n"
                tracker.mark_seen(p.project_id)

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
            "/help — Bantuan ini\n\n"
            "<b>Cara Pakai:</b>\n"
            "1️⃣ /browse → Pilih kategori → Lihat project\n"
            "2️⃣ /monitor → Toggle kategori yang mau dipantau\n"
            "3️⃣ Bot akan auto-notifikasi kalau ada project baru\n\n"
            "<b>Config:</b>\n"
            "Set <code>POLL_INTERVAL</code> di .env untuk ubah frekuensi polling (default: 300s)\n"
            "Set <code>PROJECTS_PER_PAGE</code> untuk ubah jumlah project per halaman (default: 10)",
            reply_markup=build_main_menu_keyboard(),
        )

    async def _cmd_status(self, chat_id: str):
        monitor = self._monitor(chat_id)
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            format_monitor_status(monitor),
            reply_markup=build_main_menu_keyboard(),
        )

    # ---- Smart Feature Commands ----

    async def _cmd_keyword(self, chat_id: str, text: str):
        """Manage keyword alerts: /keyword add Laravel /keyword remove Laravel /keyword list"""
        settings = self._settings(chat_id)
        parts = text.split(None, 2)
        if len(parts) < 2:
            kw_list = "\n".join(f"  🔖 {k}" for k in settings.keywords) if settings.keywords else "  ⬜ Belum ada keyword"
            await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                f"🔖 <b>Keyword Alerts</b>\n\n"
                f"Daftar keyword yang dipantau:\n\n{kw_list}\n\n"
                f"<b>Cara Pakai:</b>\n"
                f"/keyword add Laravel\n"
                f"/keyword remove Laravel\n"
                f"/keyword clear — Hapus semua\n"
                f"/keyword list — Lihat daftar",
            )
            return
        action = parts[1].lower()
        if action == "add" and len(parts) >= 3:
            kw = parts[2].strip()
            if settings.add_keyword(kw):
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"✅ Keyword ditambahkan: <b>{kw}</b>")
            else:
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"⚠️ Keyword sudah ada: <b>{kw}</b>")
        elif action == "remove" and len(parts) >= 3:
            kw = parts[2].strip()
            if settings.remove_keyword(kw):
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"🗑️ Keyword dihapus: <b>{kw}</b>")
            else:
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"❌ Keyword tidak ditemukan: <b>{kw}</b>")
        elif action == "clear":
            settings.clear_keywords()
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, "🗑️ Semua keyword dihapus")
        elif action == "list":
            kw_list = "\n".join(f"  🔖 {k}" for k in settings.keywords) if settings.keywords else "  ⬜ Belum ada keyword"
            await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                f"🔖 <b>Keyword Alerts</b>\n\n{kw_list}",
            )

    async def _cmd_skill(self, chat_id: str, text: str):
        """Manage skills matching: /skill add Laravel /skill remove Laravel /skill list"""
        settings = self._settings(chat_id)
        parts = text.split(None, 2)
        if len(parts) < 2:
            sk_list = "\n".join(f"  ⚡ {s}" for s in settings.skills) if settings.skills else "  ⬜ Belum ada skill"
            await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                f"⚡ <b>Skills Matching</b>\n\n"
                f"Skills yang kamu punya:\n\n{sk_list}\n\n"
                f"Bot akan memberi score match untuk setiap project.\n\n"
                f"<b>Cara Pakai:</b>\n"
                f"/skill add Laravel\n"
                f"/skill add Python\n"
                f"/skill remove Laravel\n"
                f"/skill clear — Hapus semua\n"
                f"/skill list — Lihat daftar",
            )
            return
        action = parts[1].lower()
        if action == "add" and len(parts) >= 3:
            sk = parts[2].strip()
            if settings.add_skill(sk):
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"✅ Skill ditambahkan: <b>{sk}</b>")
            else:
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"⚠️ Skill sudah ada: <b>{sk}</b>")
        elif action == "remove" and len(parts) >= 3:
            sk = parts[2].strip()
            if settings.remove_skill(sk):
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"🗑️ Skill dihapus: <b>{sk}</b>")
            else:
                await send_message(TELEGRAM_BOT_TOKEN, chat_id, f"❌ Skill tidak ditemukan: <b>{sk}</b>")
        elif action == "clear":
            settings.clear_skills()
            await send_message(TELEGRAM_BOT_TOKEN, chat_id, "🗑️ Semua skill dihapus")
        elif action == "list":
            sk_list = "\n".join(f"  ⚡ {s}" for s in settings.skills) if settings.skills else "  ⬜ Belum ada skill"
            await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                f"⚡ <b>Skills Matching</b>\n\n{sk_list}",
            )

    async def _cmd_budget(self, chat_id: str, text: str):
        """Set minimum budget filter: /budget set 1000000 /budget clear"""
        settings = self._settings(chat_id)
        parts = text.split(None, 2)
        if len(parts) < 2 or (len(parts) >= 2 and parts[1].lower() in ("clear", "off")):
            settings.clear_min_budget()
            await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                "💰 <b>Budget Filter</b>\n\n"
                "✅ Filter budget dinonaktifkan\n"
                "Semua project akan ditampilkan.\n\n"
                "Set minimum budget: /budget set 1000000",
            )
            return
        action = parts[1].lower()
        if action == "set" and len(parts) >= 3:
            try:
                amount = int(parts[2].replace(".", "").replace(",", ""))
                if amount <= 0:
                    raise ValueError
                settings.set_min_budget(amount)
                formatted = f"Rp {amount:,}".replace(",", ".")
                await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                    f"💰 <b>Budget Filter</b>\n\n"
                    f"✅ Minimum budget diset: <b>{formatted}</b>\n"
                    f"Project di bawah {formatted} akan diabaikan.",
                )
            except ValueError:
                await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                    "❌ Format salah. Gunakan angka murni.\n"
                    "Contoh: /budget set 1000000",
                )
        elif action == "status":
            if settings.min_budget > 0:
                formatted = f"Rp {settings.min_budget:,}".replace(",", ".")
                await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                    f"💰 <b>Budget Filter</b>\n\n"
                    f"Minimum budget: <b>{formatted}</b>",
                )
            else:
                await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                    "💰 <b>Budget Filter</b> — Nonaktif",
                )

    async def _cmd_propose(self, chat_id: str, text: str, message: dict):
        """Generate proposal: reply a project card with /propose"""
        parts = text.split(None, 1)
        lang = "id"
        if len(parts) >= 2:
            lang = "en" if parts[1].lower() in ("en", "english") else "id"

        # Check if replying to a project notification
        reply_to = message.get("reply_to_message", {})
        if not reply_to:
            await send_message(TELEGRAM_BOT_TOKEN, chat_id,
                "📝 <b>Proposal Generator</b>\n\n"
                "Reply project notification dengan:\n"
                "/propose — Bahasa Indonesia\n"
                "/propose en — English\n\n"
                f"Language aktif: {'English' if lang == 'en' else 'Bahasa Indonesia'}",
            )
            return

        reply_text = reply_to.get("text", "")

        # Extract project title from replied message
        title_match = re.search(r"▸?\s?\*?\*?(.+?)\*?\*?\s*\n", reply_text)
        if not title_match:
            title_match = re.search(r"<b>([^<]+)</b>", reply_text)
        project_title = title_match.group(1) if title_match else "Project Anda"

        # Extract budget
        budget_match = re.search(r"💰\s*(.+?)(?:\s*•|$)", reply_text)
        project_budget = budget_match.group(1).strip() if budget_match else ""

        # Extract skills from hashtags or tags
        skills_match = re.findall(r"#[\w]+", reply_text)
        project_skills = ", ".join(s.replace("#", "") for s in skills_match)

        # Extract deadline
        deadline_match = re.search(r"📅\s*(.+?)(?:\s*•|$)", reply_text)
        project_deadline = deadline_match.group(1).strip() if deadline_match else ""

        # Send placeholder
        await send_message(TELEGRAM_BOT_TOKEN, chat_id,
            f"📝 <b>Generating proposal...</b>\n\n"
            f"📌 {project_title}\n"
            f"💰 {project_budget or '-'}\n"
            f"🌐 {'English' if lang == 'en' else 'Bahasa Indonesia'}"
        )

        # Scrape full project detail
        link_match = re.search(r"https?://[^\s<>\"']+", reply_text)
        project_url = link_match.group(0) if link_match else None

        full_desc = ""
        full_skills = []
        if project_url:
            try:
                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor(max_workers=2) as executor:
                    detail = await loop.run_in_executor(
                        executor, scrape_project_detail, project_url
                    )
                full_desc = detail.get("description", "")
                full_skills = detail.get("skills", [])
                if full_skills:
                    project_skills = ", ".join(full_skills)
            except Exception as e:
                logger.warning(f"Proposal detail scrape failed: {e}")

        # Generate proposal
        proposal = generate_proposal(
            project_title=project_title,
            project_description=full_desc,
            project_budget=project_budget,
            project_deadline=project_deadline,
            project_skills=project_skills,
            language=lang,
        )

        # Send proposal
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            f"📝 <b>Proposal — {project_title}</b>\n\n"
            f"<pre>{proposal}</pre>",
        )

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
            monitor = self._monitor(chat_id)
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                format_monitor_status(monitor),
                reply_markup=build_monitor_keyboard(monitor),
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
        monitor = self._monitor(chat_id)
        is_now_on = monitor.toggle(category_id)
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
            format_monitor_status(monitor),
            reply_markup=build_monitor_keyboard(monitor),
        )

    # ---- Polling Loop ----

    async def _seed_seen_projects(self):
        """Seed the tracker with existing projects on startup.
        Prevents spamming notifications for projects that already exist.
        Uses parallel scraping for all categories simultaneously."""
        if self.tracker.seen_ids:
            logger.info(
                f"Tracker already has {len(self.tracker.seen_ids)} seen IDs, skipping seed"
            )
            return

        logger.info("Seeding tracker with existing projects...")
        categories_to_seed = self.monitor.monitored_categories or {"all"}

        loop = asyncio.get_event_loop()

        def seed_one_category(cat_id: str) -> tuple[str, list]:
            """Scrape all pages for a category and return (cat_id, project_list)."""
            from scraper import scrape_all_pages
            try:
                projects = scrape_all_pages(cat_id, max_pages=10)
                return (cat_id, projects)
            except Exception as e:
                logger.error(f"  Error seeding {cat_id}: {e}")
                return (cat_id, [])

        # Parallel seed all categories at once — scrape ONCE per category
        with ThreadPoolExecutor(max_workers=min(len(categories_to_seed), 8)) as executor:
            futures = [
                loop.run_in_executor(executor, seed_one_category, cat_id)
                for cat_id in categories_to_seed
            ]
            for fut in asyncio.as_completed(futures):
                cat_id, projects = await fut
                category = get_category_by_id(cat_id)
                logger.info(f"  Seeded {len(projects)} projects from {category['name']}")
                for p in projects:
                    self.tracker.mark_seen(p.project_id)

        logger.info(
            f"Seed complete. {len(self.tracker.seen_ids)} projects marked as seen."
        )

    async def start_polling(self):
        """Start the monitoring polling loop — per-user notifications.

        Optimizations applied:
        - Cross-user category cache: same category scraped once, shared across users
        - Parallel category scraping: all monitored categories scraped concurrently
        - ALL pages fetched per category (up to 10 pages), not just page 1
        - Deduplication per poll cycle: each project notified at most once per cycle
        - Per-user seen tracking and filtering
        """
        self._running = True
        logger.info(f"Monitoring started. Polling every {POLL_INTERVAL_SECONDS}s")

        # Seed existing projects so we only notify truly new ones (per-user)
        await self._seed_seen_projects()

        # Send startup notification to each user with their own categories
        for chat_id in TELEGRAM_CHAT_IDS:
            try:
                monitor = self._monitor(chat_id)
                settings = self._settings(chat_id)
                monitored = [
                    c for c in CATEGORIES if c["id"] in monitor.monitored_categories
                ]
                cat_list = "\n".join(f"  {c['emoji']} {c['name']}" for c in monitored)
                await send_message(
                    TELEGRAM_BOT_TOKEN,
                    chat_id,
                    "🤖 <b>Projects.co.id Bot Active!</b>\n\n"
                    f"⏱️ Polling: setiap <b>{POLL_INTERVAL_SECONDS}s</b>\n"
                    f"📄 Projects/page: <b>{PROJECTS_PER_PAGE}</b>\n"
                    f"🔔 Monitoring <b>{len(monitored)}</b> kategori:\n\n"
                    f"{cat_list or '  ⬜ Belum ada kategori yang dimonitor'}\n\n"
                    "Gunakan /monitor untuk mengubah konfigurasi.",
                    reply_markup=build_main_menu_keyboard(),
                )
            except Exception as e:
                logger.error(f"Failed startup notify to {chat_id}: {e}")

        while self._running:
            try:
                # ── Clear cross-user category cache at the start of each poll cycle ──
                _clear_category_cache()

                # ── Collect all unique monitored categories across ALL users ──
                all_monitored_cats: set[str] = set()
                for chat_id in TELEGRAM_CHAT_IDS:
                    monitor = self._monitor(chat_id)
                    if monitor.monitored_categories:
                        all_monitored_cats.update(monitor.monitored_categories)

                if not all_monitored_cats:
                    logger.info("No categories monitored, skipping poll cycle")
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                # ── Parallel scrape ALL categories using thread pool ──
                # Each category is scraped by ONE worker; result is cached for all users
                logger.info(f"Scraping {len(all_monitored_cats)} categories in parallel...")
                loop = asyncio.get_event_loop()

                def scrape_cat_sequential(cat_id: str) -> tuple[str, list]:
                    """Scrape ALL pages for a category sequentially (but categories run in parallel)."""
                    from scraper import scrape_all_pages
                    all_projects = scrape_all_pages(cat_id, max_pages=10)
                    return (cat_id, all_projects)

                with ThreadPoolExecutor(max_workers=min(len(all_monitored_cats), 8)) as executor:
                    cat_futures = [
                        loop.run_in_executor(executor, scrape_cat_sequential, cat_id)
                        for cat_id in all_monitored_cats
                    ]
                    cat_results: dict[str, list] = {}
                    for fut in asyncio.as_completed(cat_futures):
                        cat_id, projects = await fut
                        cat_results[cat_id] = projects
                        logger.info(f"  Scraped {len(projects)} projects from {cat_id}")

                # ── Now dispatch per-user notifications ──
                for chat_id in TELEGRAM_CHAT_IDS:
                    if not self._running:
                        break

                    monitor = self._monitor(chat_id)
                    tracker = self._tracker(chat_id)

                    if not monitor.monitored_categories:
                        continue

                    # Collect all new projects for this user across their categories
                    user_new_projects: list[tuple[str, Project]] = (
                        []
                    )  # (category_id, project)

                    for cat_id in monitor.monitored_categories:
                        if cat_id not in cat_results:
                            continue
                        projects = cat_results[cat_id]
                        # Filter: unseen by THIS user AND published today
                        for p in projects:
                            if not tracker.is_seen(p.project_id) and _is_published_today(
                                p.published_date
                            ):
                                user_new_projects.append((cat_id, p))

                    if not user_new_projects:
                        logger.info(f"[{chat_id}] No new projects")
                        continue

                    # Sort by publish date (newest first)
                    user_new_projects.sort(
                        key=lambda x: x[1].published_date or "", reverse=True
                    )

                    logger.info(
                        f"[{chat_id}] 🆕 {len(user_new_projects)} new projects across "
                        f"{len(monitor.monitored_categories)} categories"
                    )

                    # Notify in groups by category for clarity
                    by_category: dict[str, list[Project]] = {}
                    for cat_id, proj in user_new_projects:
                        by_category.setdefault(cat_id, []).append(proj)

                    for cat_id, projs in by_category.items():
                        category = get_category_by_id(cat_id)
                        cat_emoji = category["emoji"]
                        cat_name = category["name"]

                        header = (
                            f"🆕 <b>{len(projs)} Project Baru</b> "
                            f"di {cat_emoji} <b>{cat_name}</b>!\n\n"
                        )

                        for i, p in enumerate(projs[:5]):
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
                            text = (
                                f"{header if i == 0 else ''}"
                                f"<b>▸ {p.title}</b>\n"
                                f"   💰 {p.budget or '-'}  •  "
                                f"{bid_emoji} {p.bid_count or '0'} bids  •  "
                                f"📅 {p.published_date or '-'}\n"
                            )
                            # Inline keyboard with View Project button
                            reply_markup = {
                                "inline_keyboard": [
                                    [
                                        {
                                            "text": "🔗 View Project",
                                            "url": p.link,
                                        }
                                    ]
                                ]
                            }
                            await send_message(
                                TELEGRAM_BOT_TOKEN,
                                chat_id,
                                text,
                                reply_markup=reply_markup,
                            )
                            tracker.mark_seen(p.project_id)
                            await asyncio.sleep(0.3)

                        if len(projs) > 5:
                            await send_message(
                                TELEGRAM_BOT_TOKEN,
                                chat_id,
                                f"...dan <b>{len(projs) - 5}</b> project lainnya di "
                                f"{cat_emoji} {cat_name}. "
                                f"Gunakan /browse untuk lihat semua.",
                            )

                        await asyncio.sleep(1)

                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(30)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def stop(self):
        """Stop the polling loop."""
        self._running = False


# ============================================================
# Long Polling Update Fetcher
# ============================================================


async def fetch_updates(token: str, offset: int = 0, timeout: int = 30) -> dict:
    """Fetch updates via long polling using shared connection pool."""
    url = f"{TG_API}{token}/getUpdates"
    payload = {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    client = get_http_client()
    response = await client.post(url, json=payload)
    return response.json()


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
        await close_http_client()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        bot.stop()
        monitor_task.cancel()
        await close_http_client()
        raise


if __name__ == "__main__":
    asyncio.run(main())
