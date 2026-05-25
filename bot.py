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

# ── External ──
import aiohttp
from aiohttp import web

# ── AI ──
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

# ── Analytics ──
from trend_analysis import (
    record_project,
    get_trend_stats,
    get_category_trend,
    get_peak_hours,
    get_budget_trend,
    format_trend_report,
)

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


# ============================================================
# ProjectsBot — Main bot class (from ai-proposal-cv)
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
            "/topclients — Top 10 client terbanyak\n\n"
            "<b>📝 AI Proposal Commands:</b>\n"
            "/proposal <url> — Generate AI proposal dari URL project\n"
            "/uploadcv — Upload CV PDF (untuk proposal personal)\n"
            "/mycv — Lihat CV yang sudah diupload\n"
            "/setprofile — Set profil freelancer (multi-user support)\n"
            "/myprofile — Lihat profil yang sudah di-set\n\n"
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
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
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
                reply_markup=build_main_menu_keyboard(),
            )
        elif action == "uploadcv":
            await edit_message(
                TELEGRAM_BOT_TOKEN,
                int(chat_id),
                message_id,
                "📄 <b>Upload CV PDF</b>\n\n"
                "Kirim file PDF CV Anda sebagai document (bukan foto).\n\n"
                "Cara:\n"
                "1. Klik ikon lampiran (📎) di chat\n"
                "2. Pilih 'Document'\n"
                "3. Pilih file PDF CV Anda\n\n"
                "CV akan disimpan secara private dan digunakan "
                "untuk membuat proposal yang lebih personal.",
                reply_markup=build_main_menu_keyboard(),
            )
        elif action == "mycv":
            cv_text = get_user_cv_text(chat_id)
            if cv_text:
                preview = cv_text[:300] + "..." if len(cv_text) > 300 else cv_text
                await edit_message(
                    TELEGRAM_BOT_TOKEN,
                    int(chat_id),
                    message_id,
                    f"✅ <b>CV Tersimpan</b>\n\n"
                    f"Panjang: {len(cv_text)} karakter\n\n"
                    f"Preview:\n"
                    f"<code>{preview}</code>\n\n"
                    "CV ini akan digunakan untuk generate proposal personal.",
                    reply_markup=build_main_menu_keyboard(),
                )
            else:
                await edit_message(
                    TELEGRAM_BOT_TOKEN,
                    int(chat_id),
                    message_id,
                    "📭 <b>Belum Ada CV</b>\n\n"
                    "Anda belum upload CV. Kirim file PDF dengan "
                    "<code>/uploadcv</code> untuk upload.",
                    reply_markup=build_main_menu_keyboard(),
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
