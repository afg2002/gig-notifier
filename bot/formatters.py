"""
Formatters — project/job/contest card formatters for Telegram messages.
"""
from typing import Optional

from scraper import Project
from fastwork_scraper import FastworkJob
from sribu_scraper import SribuContest
from tracking.monitor import MonitorConfig

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
