"""
Inline keyboard builders for Telegram bot.
"""
from typing import Optional

from scraper import CATEGORIES
from tracking.monitor import MonitorConfig, FastworkMonitorConfig

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

