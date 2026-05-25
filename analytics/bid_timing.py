"""
Waktu Emas Bid ⏰
Analyze historical project posting times to find optimal bid windows.
Answers: "Kapan waktu terbaik untuk bid?" based on actual data.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# Day/Time Constants
# ============================================================

DAY_NAMES_ID = {
    0: "Senin",
    1: "Selasa",
    2: "Rabu",
    3: "Kamis",
    4: "Jumat",
    5: "Sabtu",
    6: "Minggu",
}

DAY_EMOJI = {
    0: "📊",  # Monday — work day
    1: "📊",
    2: "📊",
    3: "📊",
    4: "📊",
    5: "😴",  # Weekend
    6: "😴",
}

HOUR_LABELS = {
    (0, 6): ("🌙 Dini Hari", "00:00-06:00 — sepi, proyek jarang"),
    (6, 9): ("🌅 Pagi", "06:00-09:00 — client mulai posting"),
    (9, 12): ("☀️ Pagi Menjelang Siang", "09:00-12:00 — prime time!"),
    (12, 14): ("🍽️ Siang / Istirahat", "12:00-14:00 — agak sepi"),
    (14, 17): ("🔥 Sore", "14:00-17:00 — prime time kedua!"),
    (17, 20): ("🌆 Sore Menjelang Malam", "17:00-20:00 — masih rame"),
    (20, 24): ("🌃 Malam", "20:00-00:00 — client lembur"),
}


class TimingOracle:
    """Analyze posting patterns and recommend optimal bid windows."""

    @staticmethod
    def analyze(projects: list[dict]) -> dict:
        """
        Analyze project posting times.

        Each project dict should have 'posted_date' (ISO format string).
        Returns aggregated statistics.
        """
        if not projects:
            return {"error": "No project data available"}

        day_counter = Counter()
        hour_counter = Counter()
        weekday_hour = defaultdict(Counter)

        parsed_count = 0
        for proj in projects:
            dt = _parse_date(proj.get("posted_date", ""))
            if dt:
                day_counter[dt.weekday()] += 1
                hour_counter[dt.hour] += 1
                weekday_hour[dt.weekday()][dt.hour] += 1
                parsed_count += 1

        if parsed_count == 0:
            return {"error": "Could not parse any posting dates"}

        # Find best day
        best_day = day_counter.most_common(1)[0] if day_counter else (0, 0)

        # Find best hours
        top_hours = hour_counter.most_common(6)

        # Find best windows (day + hour combos)
        best_windows = []
        for day in range(7):
            for hour in range(24):
                count = weekday_hour[day][hour]
                if count > 0:
                    best_windows.append({
                        "day": day,
                        "day_name": DAY_NAMES_ID[day],
                        "hour": hour,
                        "count": count,
                    })

        best_windows.sort(key=lambda w: -w["count"])

        return {
            "total_projects": parsed_count,
            "by_day": {DAY_NAMES_ID[k]: v for k, v in day_counter.items()},
            "best_day": {"day": DAY_NAMES_ID[best_day[0]], "count": best_day[1]},
            "by_hour": {f"{h:02d}:00": c for h, c in sorted(hour_counter.items())},
            "top_hours": [(f"{h:02d}:00", c) for h, c in top_hours],
            "best_windows": best_windows[:10],
            "worst_day": {
                "day": DAY_NAMES_ID[day_counter.most_common()[-1][0]],
                "count": day_counter.most_common()[-1][1],
            } if day_counter else None,
        }

    @staticmethod
    def recommend(analysis: dict) -> list[str]:
        """Generate human-readable recommendations from analysis."""
        if "error" in analysis:
            return ["⚠️ Tidak cukup data untuk analisis bid timing."]

        tips = []
        best_windows = analysis.get("best_windows", [])

        if best_windows:
            top = best_windows[0]
            tips.append(
                f"⏰ *Waktu terbaik:* {top['day_name']} jam {top['hour']:02d}:00 "
                f"({top['count']} project diposting di slot ini)"
            )

        # Day-based advice
        best_day = analysis.get("best_day", {})
        worst_day = analysis.get("worst_day", {})
        if best_day and worst_day:
            tips.append(
                f"📅 *Hari terbaik:* {best_day['day']} ({best_day['count']} project)  "
                f"vs *terburuk:* {worst_day['day']} ({worst_day['count']} project)"
            )

        # Time-based advice
        top_hours = analysis.get("top_hours", [])
        if len(top_hours) >= 2:
            hour1, hour2 = top_hours[0][0], top_hours[1][0]
            tips.append(f"🕐 *Jam prime:* {hour1} dan {hour2}")

        # Strategic tips
        tips.append("")
        tips.append("*Strategi Bid:*")
        tips.append("• Bid dalam 2-4 jam setelah project posting — responsiveness matters")
        tips.append("• Hindari bid Jumat >16:00 — client udah weekend mode")
        tips.append("• Senin pagi banyak project baru — pantau 08:00-10:00")
        tips.append("• Project weekend biasanya lebih serius (client kerja di luar jam kantor)")

        return tips


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse various date formats into datetime."""
    if not date_str:
        return None
    try:
        # Try common formats
        for fmt in [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d",
        ]:
            try:
                return datetime.strptime(date_str[:19], fmt)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def get_projects_with_dates(db_conn, days: int = 90) -> list[dict]:
    """Fetch projects with posted dates from all sources."""
    all_projects = []
    try:
        cursor = db_conn.cursor()
        since = (datetime.now() - timedelta(days=days)).isoformat()[:10]

        for table in ["projects", "fastwork_jobs", "sribu_contests"]:
            try:
                cursor.execute(
                    f"SELECT title, posted_date FROM {table} WHERE posted_date >= ? AND posted_date IS NOT NULL",
                    (since,)
                )
                for row in cursor.fetchall():
                    all_projects.append({
                        "title": row["title"],
                        "posted_date": row["posted_date"],
                    })
            except Exception:
                pass

        return all_projects
    except Exception as e:
        logger.error(f"Error fetching project dates: {e}")
        return []


def format_timing_report(analysis: dict, recommendations: list[str]) -> str:
    """Generate Telegram-friendly timing report."""

    if "error" in analysis:
        return f"⚠️ {analysis['error']}"

    lines = [
        "⏰ *Bid Timing Oracle*",
        f"",
        f"📊 Menganalisis {analysis['total_projects']} project",
        f"",
    ]

    # Day distribution
    lines.append("*Distribusi per Hari:*")
    for day_name, count in analysis["by_day"].items():
        bar = "█" * max(1, int(count / max(analysis["by_day"].values()) * 10))
        lines.append(f"  {day_name}: {bar} ({count})")
    lines.append("")

    # Hour distribution
    lines.append("*Distribusi per Jam:*")
    by_hour = analysis["by_hour"]
    max_hour = max(by_hour.values()) if by_hour else 1
    for hour_label, count in sorted(by_hour.items()):
        bar = "█" * max(1, int(count / max_hour * 10))
        lines.append(f"  {hour_label}: {bar} ({count})")
    lines.append("")

    # Recommendations
    lines.append("*Rekomendasi:*")
    for rec in recommendations:
        lines.append(rec)

    return "\n".join(lines)


def format_timing_compact(analysis: dict) -> str:
    """Compact view — best/worst only."""
    if "error" in analysis:
        return f"⚠️ {analysis['error']}"

    top = analysis["best_windows"][:3] if analysis["best_windows"] else []
    lines = ["⏰ *Quick Timing Intel*", ""]

    if top:
        lines.append(f"🎯 *Top 3 Window:*")
        for w in top:
            lines.append(f"  {w['day_name']} {w['hour']:02d}:00 — {w['count']} project")

    return "\n".join(lines)


# ============================================================
# Self-test
# ============================================================

if __name__ == "__main__":
    import random

    # Generate synthetic test data
    test_projects = []
    for _ in range(500):
        day = random.choices(range(7), weights=[2, 2.5, 2.5, 2, 2, 0.5, 0.3])[0]
        hour = random.choices(
            range(24),
            weights=[
                0.1, 0.05, 0.02, 0.01, 0.01, 0.02,  # 0-5
                0.5, 1.5, 2.5, 3.0, 2.5, 2.0,  # 6-11
                1.0, 0.5, 2.0, 2.5, 2.0,  # 12-16
                1.5, 1.0, 0.8, 0.5, 0.3, 0.2, 0.1,  # 17-23
            ]
        )[0]
        base = datetime(2026, 5, 1) + timedelta(days=random.randint(0, 90))
        dt = base.replace(hour=hour, minute=random.randint(0, 59))
        test_projects.append({"posted_date": dt.strftime("%Y-%m-%d %H:%M:%S")})

    oracle = TimingOracle()
    analysis = oracle.analyze(test_projects)
    recs = oracle.recommend(analysis)
    print(format_timing_report(analysis, recs))
