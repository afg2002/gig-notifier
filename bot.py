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
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import httpx
from scraper import (
    scrape_listing,
    CATEGORIES,
    get_category_by_id,
    Project,
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


# ============================================================
# Telegram API Helpers
# ============================================================


async def tg_request(token: str, method: str, payload: dict) -> dict:
    """Make a request to Telegram Bot API."""
    url = f"{TG_API}{token}/{method}"
    async with httpx.AsyncClient(timeout=30) as client:
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

    card += (
        f"👤 <b>Owner:</b> {project.owner_name}\n"
        f"🔗 <a href='{project.link}'>View Project →</a>\n"
    )

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
        f"{'─' * 30}\n"
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
            f"📅 {p.published_date or '-'}\n"
            f"   🔗 <a href='{p.link}'>View →</a>"
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
    """Interactive Telegram bot for projects.co.id."""

    def __init__(self):
        self.tracker = SeenTracker(SEEN_FILE)
        self.monitor = MonitorConfig(MONITOR_FILE)
        self.cache = ProjectCache()
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
        await send_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            format_monitor_status(self.monitor),
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

    async def _cb_category(
        self, chat_id: str, message_id: int, category_id: str, callback_id: str
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)
        await self._show_category_page(chat_id, message_id, category_id, 1)

    async def _show_category_page(
        self, chat_id: str, message_id: int, category_id: str, page: int
    ):
        """Fetch and display a category page. Scraping runs in background thread."""
        category = get_category_by_id(category_id)

        # Show loading state immediately
        loading_text = f"{category['emoji']} <b>{category['name']}</b>\n\n⏳ <i>Loading projects...</i>"
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

        # Run blocking scrape in background thread
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=2) as executor:
            projects = await loop.run_in_executor(
                executor, scrape_listing, category_id, page
            )

        if not projects:
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                f"{category['emoji']} <b>{category['name']}</b>\n\n"
                "😔 Tidak ada project di halaman ini.",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "🔙 Categories", "callback_data": "catlist"}],
                        [{"text": "🏠 Main Menu", "callback_data": "menu:back"}],
                    ]
                },
            )
            return

        total_pages = max(
            1, (len(projects) + PROJECTS_PER_PAGE - 1) // PROJECTS_PER_PAGE
        )

        start = (page - 1) * PROJECTS_PER_PAGE
        end = start + PROJECTS_PER_PAGE
        page_projects = projects[start:end]

        self.cache.store(category_id, page, page_projects)

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

        # Project detail buttons
        for i, p in enumerate(projects):
            short_title = _truncate(p.title, 25)
            buttons.append(
                [
                    {
                        "text": f"📋 #{i + 1} {short_title}",
                        "callback_data": f"proj:{i}:{category_id}:{page}",
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
        await self._show_category_page(chat_id, message_id, category_id, page)

    async def _cb_project_detail(
        self,
        chat_id: str,
        message_id: int,
        index: int,
        category_id: str,
        page: int,
        callback_id: str,
    ):
        await answer_callback(TELEGRAM_BOT_TOKEN, callback_id)

        projects = self.cache.get(category_id, page)
        if not projects or index >= len(projects):
            # Re-scrape if cache miss — in background thread
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=2) as executor:
                projects = await loop.run_in_executor(
                    executor, scrape_listing, category_id, page
                )
            self.cache.store(category_id, page, projects)

        if not projects or index >= len(projects):
            await answer_callback(
                TELEGRAM_BOT_TOKEN, callback_id, text="⚠️ Project tidak ditemukan"
            )
            return

        project = projects[index]
        text = format_project_card(project, index)

        # Back keyboard
        kb = {
            "inline_keyboard": [
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
                    projects = await loop.run_in_executor(
                        executor, scrape_listing, cat_id, 1
                    )
                for p in projects:
                    self.tracker.mark_seen(p.project_id)
                logger.info(
                    f"  Seeded {len(projects)} projects from {category['name']}"
                )
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"  Error seeding {cat_id}: {e}")

        logger.info(
            f"Seed complete. {len(self.tracker.seen_ids)} projects marked as seen."
        )

    async def start_polling(self):
        """Start the monitoring polling loop."""
        self._running = True
        logger.info(f"Monitoring started. Polling every {POLL_INTERVAL_SECONDS}s")

        # Seed existing projects so we only notify truly new ones
        await self._seed_seen_projects()

        # Send startup notification
        monitored = [
            c for c in CATEGORIES if c["id"] in self.monitor.monitored_categories
        ]
        cat_list = "\n".join(f"  {c['emoji']} {c['name']}" for c in monitored)

        await send_message(
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            "🤖 <b>Projects.co.id Bot Active!</b>\n\n"
            f"⏱️ Polling: setiap <b>{POLL_INTERVAL_SECONDS}s</b>\n"
            f"📄 Projects/page: <b>{PROJECTS_PER_PAGE}</b>\n"
            f"🔔 Monitoring <b>{len(monitored)}</b> kategori:\n\n"
            f"{cat_list or '  ⬜ Belum ada kategori yang dimonitor'}\n\n"
            "Gunakan /monitor untuk mengubah konfigurasi.",
            reply_markup=build_main_menu_keyboard(),
        )

        while self._running:
            try:
                if not self.monitor.monitored_categories:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                for cat_id in self.monitor.monitored_categories:
                    if not self._running:
                        break

                    category = get_category_by_id(cat_id)
                    logger.info(f"Polling: {category['name']}")

                    projects = scrape_listing(cat_id, 1)
                    new_projects = [
                        p for p in projects if not self.tracker.is_seen(p.project_id)
                    ]

                    if new_projects:
                        logger.info(f"🆕 {len(new_projects)} new in {category['name']}")

                        # Send notification
                        cat_emoji = category["emoji"]
                        header = (
                            f"🆕 <b>{len(new_projects)} Project Baru</b> "
                            f"di {cat_emoji} <b>{category['name']}</b>!\n\n"
                        )

                        for i, p in enumerate(new_projects[:5]):
                            bid_count = (
                                int(p.bid_count)
                                if p.bid_count and p.bid_count.isdigit()
                                else 0
                            )
                            bid_emoji = (
                                "🔥"
                                if bid_count > 20
                                else "👥"
                                if bid_count > 5
                                else "🆕"
                            )

                            msg = (
                                f"<b>▸ {p.title}</b>\n"
                                f"   💰 {p.budget or '-'}  •  "
                                f"{bid_emoji} {p.bid_count or '0'} bids  •  "
                                f"📅 {p.published_date or '-'}\n"
                                f"   🔗 <a href='{p.link}'>View →</a>\n"
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

                    await asyncio.sleep(2)  # Delay between categories

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
    """Fetch updates via long polling."""
    url = f"{TG_API}{token}/getUpdates"
    payload = {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    async with httpx.AsyncClient(timeout=timeout + 10) as client:
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
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        bot.stop()
        monitor_task.cancel()
        raise


if __name__ == "__main__":
    asyncio.run(main())
