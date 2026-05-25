"""
Intel Klien 🔍
Analisis track record client berdasarkan data historis dari database.
Menjawab: "Client ini serius apa cuma window shopping?"

Data sources: client_stats table + projects/fastwork_jobs/sribu_contests
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class ClientIntel:
    """Container for client intelligence results."""

    def __init__(self, client_name: str, stats: dict, recent_projects: list[dict]):
        self.client_name = client_name
        self.project_count = stats.get("project_count", 0)
        self.avg_budget = stats.get("avg_budget", 0)
        self.total_budget = stats.get("total_budget", 0)
        self.last_seen = stats.get("last_seen", "")
        self.first_seen = stats.get("first_seen", "")
        self.recent_projects = recent_projects

    @property
    def is_new(self) -> bool:
        return self.project_count <= 1

    @property
    def is_established(self) -> bool:
        return self.project_count >= 5

    @property
    def activity_level(self) -> str:
        if self.project_count >= 10:
            return "🔥 Sangat Aktif"
        elif self.project_count >= 5:
            return "✅ Aktif"
        elif self.project_count >= 2:
            return "🙂 Kadang Posting"
        else:
            return "🆕 Client Baru"

    @property
    def budget_tier(self) -> str:
        if not self.avg_budget:
            return "❓ Unknown"
        if self.avg_budget >= 10_000_000:
            return "💎 High Budget"
        elif self.avg_budget >= 3_000_000:
            return "💰 Medium Budget"
        else:
            return "💸 Low Budget"

    @property
    def verdict(self) -> str:
        if self.is_new:
            return "🆕 Client baru — belum ada track record. Hati-hati."
        if self.project_count >= 5 and self.avg_budget >= 5_000_000:
            return "🏆 Client serius — established, budget oke. Layak dikejar!"
        if self.project_count >= 3:
            return "✅ Client cukup aktif — patut dicoba."
        return "🤔 Client jarang posting — bisa jadi project iseng."


def analyze_client(db_conn, client_name: str) -> Optional[ClientIntel]:
    """Analyze a client from database history."""
    if not client_name:
        return None

    cursor = db_conn.cursor()

    # Get from client_stats
    cursor.execute(
        "SELECT project_count, avg_budget, total_budget, last_seen, first_seen "
        "FROM client_stats WHERE client_name = ?",
        (client_name,)
    )
    row = cursor.fetchone()

    if not row:
        # Try querying across all tables
        stats = _calculate_client_stats(cursor, client_name)
    else:
        stats = {
            "project_count": row["project_count"],
            "avg_budget": row["avg_budget"],
            "total_budget": row["total_budget"],
            "last_seen": row["last_seen"] or "",
            "first_seen": row["first_seen"] or "",
        }

    # Get recent projects
    recent = _get_recent_projects(cursor, client_name)

    return ClientIntel(client_name, stats, recent)


def _calculate_client_stats(cursor, client_name: str) -> dict:
    """Calculate client stats from raw project tables."""
    count = 0
    budgets = []
    dates = []

    for table in ["projects", "fastwork_jobs", "sribu_contests"]:
        try:
            cursor.execute(
                f"SELECT budget_raw, posted_date FROM {table} WHERE client_name = ?",
                (client_name,)
            )
            for row in cursor.fetchall():
                count += 1
                if row["budget_raw"]:
                    budgets.append(row["budget_raw"])
                if row["posted_date"]:
                    dates.append(row["posted_date"])
        except Exception:
            pass

    dates.sort()
    return {
        "project_count": count,
        "avg_budget": sum(budgets) / len(budgets) if budgets else 0,
        "total_budget": sum(budgets),
        "last_seen": dates[-1] if dates else "",
        "first_seen": dates[0] if dates else "",
    }


def _get_recent_projects(cursor, client_name: str, limit: int = 5) -> list[dict]:
    """Get client's most recent project titles."""
    recent = []
    for table in ["projects", "fastwork_jobs", "sribu_contests"]:
        try:
            cursor.execute(
                f"SELECT title, budget, posted_date FROM {table} "
                f"WHERE client_name = ? ORDER BY posted_date DESC LIMIT ?",
                (client_name, limit)
            )
            for row in cursor.fetchall():
                recent.append({
                    "title": row["title"] or "-",
                    "budget": row["budget"] or "-",
                    "date": (row["posted_date"] or "")[:10],
                    "source": table,
                })
        except Exception:
            pass

    recent.sort(key=lambda p: p["date"], reverse=True)
    return recent[:limit]


def format_client_intel(intel: ClientIntel) -> str:
    """Generate Telegram-friendly client intel report."""
    lines = [
        f"🔍 <b>Intel Klien: {intel.client_name[:40]}</b>",
        "",
        f"📊 <b>Activity:</b> {intel.activity_level}",
        f"📦 <b>Total Project:</b> {intel.project_count}",
        f"💰 <b>Avg Budget:</b> Rp{intel.avg_budget:,.0f}" if intel.avg_budget else "💰 <b>Avg Budget:</b> -",
        f"💵 <b>Total Budget:</b> Rp{intel.total_budget:,.0f}" if intel.total_budget else "",
        f"🏷️ <b>Tier:</b> {intel.budget_tier}",
        "",
        f"📅 <b>Pertama:</b> {intel.first_seen[:10] if intel.first_seen else '-'}",
        f"📅 <b>Terakhir:</b> {intel.last_seen[:10] if intel.last_seen else '-'}",
        "",
        f"📋 <b>Verdict:</b> {intel.verdict}",
    ]

    if intel.recent_projects:
        lines.append("")
        lines.append("<b>Project Terbaru:</b>")
        for i, p in enumerate(intel.recent_projects, 1):
            lines.append(f"  {i}. {p['title'][:50]} — {p['budget']} ({p['date']})")

    lines.append("")
    if intel.is_new:
        lines.append("⚠️ <i>Client baru — pastikan lo cek ghost score sebelum bid.</i>")
    elif intel.is_established:
        lines.append("💡 <i>Client established — proposal lo harus standout dari kompetitor.</i>")

    return "\n".join([l for l in lines if l])  # Remove empty lines


# ================================================================
# Self-test
# ================================================================

if __name__ == "__main__":
    from database import get_db

    with get_db() as conn:
        # Get a random client name
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT client_name FROM projects LIMIT 1")
        row = cursor.fetchone()
        if row:
            name = row["client_name"]
            print(f"=== Client Intel: {name} ===")
            intel = analyze_client(conn, name)
            if intel:
                print(format_client_intel(intel))
            else:
                print("No data found")
        else:
            print("No clients in database")
