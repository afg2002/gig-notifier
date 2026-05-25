"""
Daily digest — aggregates new projects across platforms for a daily summary.
"""
import json
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DIGEST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "daily_digest.json"
)


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_digest() -> dict:
    if os.path.exists(DIGEST_FILE):
        with open(DIGEST_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_digest(digest: dict):
    os.makedirs(os.path.dirname(DIGEST_FILE), exist_ok=True)
    with open(DIGEST_FILE, "w") as f:
        json.dump(digest, f, indent=2)


def record_digest_project(project, category_name: str):
    """Record a project in today's digest."""
    digest = _load_digest()
    key = _today_key()
    if key not in digest:
        digest[key] = {"projects": [], "total": 0}

    pid = getattr(project, "project_id", str(project))
    if pid not in [p.get("id") for p in digest[key]["projects"]]:
        digest[key]["projects"].append(
            {
                "id": pid,
                "title": getattr(project, "title", str(project)),
                "category": category_name,
                "budget": getattr(project, "budget", ""),
            }
        )
        digest[key]["total"] = len(digest[key]["projects"])
    _save_digest(digest)


def get_daily_digest_text() -> str:
    """Get today's digest as formatted text."""
    digest = _load_digest()
    key = _today_key()
    today = digest.get(key, {})

    if not today or not today.get("projects"):
        return "📋 Tidak ada project baru hari ini."

    lines = [
        f"📊 <b>Daily Digest — {key}</b>",
        f"🆕 <b>{today['total']} project baru</b>",
        "",
    ]

    # Group by category
    by_category = {}
    for p in today["projects"]:
        cat = p.get("category", "Other")
        by_category.setdefault(cat, []).append(p)

    for cat, projects in sorted(by_category.items()):
        lines.append(f"<b>📁 {cat}</b> ({len(projects)})")
        for p in projects:
            budget = p.get("budget", "")
            budget_str = f" — {budget}" if budget else ""
            lines.append(f"  • {p['title']}{budget_str}")
        lines.append("")

    return "\n".join(lines)
