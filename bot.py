"""
Gig Notifier — Telegram bot for freelance project monitoring.

Monitors Projects.co.id, Fastwork.id, and Sribu.com with real-time notifications,
category browsing via inline keyboards, per-category monitoring, budget intelligence,
client reputation tracking, and daily digest.

Run:
    python bot.py
"""
import asyncio
import logging

# ── Core ──
from core.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    POLL_INTERVAL_SECONDS,
    PROJECTS_PER_PAGE,
    DATA_DIR,
    SEEN_FILE,
    MONITOR_FILE,
    FW_SEEN_FILE,
    FW_MONITOR_FILE,
    SRIBU_SEEN_FILE,
    SRIBU_MONITOR_FILE,
    get_chat_ids,
)

from core.tg_api import (
    tg_request,
    send_message,
    broadcast,
    edit_message,
    answer_callback,
)

from core.cache import ProjectCache, FastworkJobCache, SribuContestCache

# ── Tracking ──
from tracking.seen import SeenTracker
from tracking.monitor import MonitorConfig
from tracking.digest import (
    record_digest_project,
    get_daily_digest_text,
)

# ── Intel ──
from intel.budget import (
    update_budget_stats,
    get_budget_comparison,
)
from intel.clients import (
    update_client_stats,
    get_client_reputation,
)

# ── Scrapers ──
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

# ── Bot UI ──
from bot.formatters import (
    format_project_card,
    format_project_list,
    format_monitor_status,
    format_sribu_contest_card,
    format_sribu_contests_list,
    format_fastwork_job_card,
    format_fastwork_jobs_list,
    format_fastwork_jobs_notification,
    _truncate,
    _is_published_today,
    _build_sribu_detail_keyboard,
    _build_fastwork_detail_keyboard,
)

from bot.keyboards import (
    build_main_menu_keyboard,
    build_platform_submenu,
    build_category_keyboard,
    build_project_list_keyboard,
    build_monitor_keyboard,
    build_fastwork_monitor_keyboard,
)

logger = logging.getLogger(__name__)

# ============================================================
# ProjectsBot — Main bot class
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
        """Show Fastwork job categories — only monitored ones."""
        cats = get_fastwork_categories()
        if not cats:
            await answer_callback(TELEGRAM_BOT_TOKEN, callback_id, text="⚠️ Gagal load kategori Fastwork")
            return

        monitored = self.fw_monitor.monitored_tags
        cats_to_show = [c for c in cats if c["id"] in monitored]

        if not cats_to_show:
            await edit_message(
                TELEGRAM_BOT_TOKEN, int(chat_id), message_id,
                "⚠️ Kamu tidak memantau kategori Fastwork mana pun.\n\nGunakan /fw setup untuk menambahkan kategori.",
                reply_markup={"inline_keyboard": [[{"text": "🔙 Back to Fastwork", "callback_data": "src:fastwork"}]]},
            )
            return

        buttons = []
        row = []
        for cat in cats_to_show:
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

        cat_text = "\n".join([f"• {c['name']}" for c in cats_to_show])
        await edit_message(
            TELEGRAM_BOT_TOKEN,
            int(chat_id),
            message_id,
            f"⚡ <b>Fastwork Categories</b> (yang kamu monitor)\n\n{cat_text}\n\nPilih kategori:",
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
        keyboard = _build_fastwork_detail_keyboard(job)

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
# Polling & Entry Point
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

