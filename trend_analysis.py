"""
Trend Analysis Dashboard for gig-notifier bot.
Tracks daily project counts and budgets per category for trend analysis.
"""

import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TREND_STATS_FILE = os.path.join(DATA_DIR, "trend_stats.json")

# Category mapping for projects.co.id
CATEGORY_MAPPING = {
    "3d_modeling": "3D Modeling",
    "accounting": "Accounting",
    "audio_video": "Audio/Video",
    "data_entry": "Data Entry",
    "desktop_prog": "Desktop Programming",
    "electronics": "Electronics",
    "game_prog": "Game Programming",
    "internet_marketing": "Internet Marketing",
    "mobile_prog": "Mobile Programming",
    "multimedia": "Multimedia",
    "network_and_infra": "Network & Infra",
    "photography": "Photography",
    "seo": "SEO",
    "software": "Software",
    "translation": "Translation",
    "web_dev": "Web Development",
    "writing": "Writing",
    "all": "All Categories",
}

# Emoji mapping for categories
CATEGORY_EMOJI = {
    "3d_modeling": "🎬",
    "accounting": "💼",
    "audio_video": "📸",
    "data_entry": "📊",
    "desktop_prog": "🖥️",
    "electronics": "🤖",
    "game_prog": "🎮",
    "internet_marketing": "📱",
    "mobile_prog": "📱",
    "multimedia": "🎨",
    "network_and_infra": "🌐",
    "photography": "📷",
    "seo": "🔍",
    "software": "💿",
    "translation": "🌍",
    "web_dev": "🌐",
    "writing": "✍️",
    "all": "📋",
}


# ── Trend Stats Persistence ───────────────────────────────────────────────────

def _load_trend_stats() -> dict:
    """Load trend statistics from file."""
    if os.path.exists(TREND_STATS_FILE):
        try:
            with open(TREND_STATS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load trend stats: {e}")
    return {}


def _save_trend_stats(stats: dict):
    """Save trend statistics to file."""
    with open(TREND_STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def _today_key() -> str:
    """Get today's date key in YYYY-MM-DD format."""
    return date.today().strftime("%Y-%m-%d")


def _date_key(days_ago: int = 0) -> str:
    """Get date key for N days ago."""
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ── Record Stats ─────────────────────────────────────────────────────────────

def record_project(
    category_id: str,
    budget: Optional[str] = None,
    published_hour: Optional[int] = None,
    source: str = "projects",
):
    """Record a project in today's trend stats.
    
    Args:
        category_id: The category ID from projects.co.id
        budget: Budget string like "Rp 500.000 - 1.000.000"
        published_hour: Hour of day when project was posted (0-23)
        source: "projects", "fastwork", or "sribu"
    """
    stats = _load_trend_stats()
    today = _today_key()
    
    if today not in stats:
        stats[today] = {
            "categories": {},
            "hourly": {},
            "total_projects": 0,
            "total_budget": 0,
        }
    
    cat_key = category_id if category_id else "unknown"
    
    if cat_key not in stats[today]["categories"]:
        stats[today]["categories"][cat_key] = {
            "count": 0,
            "total_budget": 0,
            "budgets": [],
        }
    
    stats[today]["categories"][cat_key]["count"] += 1
    stats[today]["total_projects"] += 1
    
    # Parse and record budget
    if budget:
        budget_val = _parse_budget(budget)
        if budget_val:
            stats[today]["categories"][cat_key]["total_budget"] += budget_val
            stats[today]["categories"][cat_key]["budgets"].append(budget_val)
            stats[today]["total_budget"] += budget_val
    
    # Record hourly distribution
    if published_hour is not None:
        hour_key = str(published_hour)
        if hour_key not in stats[today]["hourly"]:
            stats[today]["hourly"][hour_key] = 0
        stats[today]["hourly"][hour_key] += 1
    
    _save_trend_stats(stats)


def record_fastwork_job(tag_id: str, budget: Optional[str] = None):
    """Record a Fastwork job in trend stats."""
    record_project(
        category_id=f"fw_{tag_id}",
        budget=budget,
        source="fastwork",
    )


def record_sribu_contest(category_id: str, budget: Optional[str] = None):
    """Record a Sribu contest in trend stats."""
    record_project(
        category_id=f"sribu_{category_id}",
        budget=budget,
        source="sribu",
    )


def _parse_budget(budget_str: str) -> Optional[float]:
    """Extract numeric budget value from string.
    
    Handles formats like:
    - "Rp 500.000"
    - "Rp 500.000 - 1.000.000"
    - "Rp 500rb"
    - "500.000"
    """
    import re
    if not budget_str or budget_str == "-":
        return None
    
    # Clean the string - remove "Rp", dots (thousand separators), quotes
    cleaned = budget_str.replace("Rp", "").replace(".", "").replace("'", "").strip()
    
    # Find all numbers (possibly with commas as thousand separators in some formats)
    # Handle formats like "500.000" or "500,000" 
    nums = re.findall(r"\d+", cleaned)
    if not nums:
        return None
    
    try:
        # Take the FIRST number found (that's the minimum budget in a range)
        val = float(nums[0])
        
        # Handle "500rb" style budgets (e.g., "500rb" -> 500000)
        if "rb" in budget_str.lower() and val < 10000:
            val *= 1000
        
        return val
    except ValueError:
        return None


# ── Query Stats ──────────────────────────────────────────────────────────────

def get_trend_stats(days: int = 7) -> dict:
    """Get trend statistics for the last N days.
    
    Returns dict with:
    - daily: {date: {category: {count, avg_budget, total_budget}}}
    - weekly_totals: {category: {total_count, avg_budget, total_budget}}
    - hourly: {hour: count} for peak hours analysis
    - top_categories: [{id, name, emoji, count, trend}] sorted by count
    """
    stats = _load_trend_stats()
    result = {
        "daily": {},
        "weekly_totals": {},
        "hourly": {},
        "top_categories": [],
        "date_range": {
            "start": _date_key(days - 1),
            "end": _today_key(),
        },
    }
    
    # Aggregate daily stats
    all_categories: dict = {}
    hourly_aggregate: dict = {}
    
    for day_idx in range(days):
        day_key = _date_key(day_idx)
        day_data = stats.get(day_key, {"categories": {}, "hourly": {}, "total_projects": 0})
        
        result["daily"][day_key] = day_data
        
        # Aggregate categories
        for cat_id, cat_data in day_data.get("categories", {}).items():
            if cat_id not in all_categories:
                all_categories[cat_id] = {
                    "count": 0,
                    "total_budget": 0,
                    "budgets": [],
                }
            all_categories[cat_id]["count"] += cat_data.get("count", 0)
            all_categories[cat_id]["total_budget"] += cat_data.get("total_budget", 0)
            all_categories[cat_id]["budgets"].extend(cat_data.get("budgets", []))
        
        # Aggregate hourly
        for hour, count in day_data.get("hourly", {}).items():
            hourly_aggregate[hour] = hourly_aggregate.get(hour, 0) + count
    
    # Calculate weekly totals with averages
    for cat_id, cat_data in all_categories.items():
        budgets = cat_data["budgets"]
        avg_budget = sum(budgets) / len(budgets) if budgets else 0
        result["weekly_totals"][cat_id] = {
            "total_count": cat_data["count"],
            "avg_budget": avg_budget,
            "total_budget": cat_data["total_budget"],
            "daily_avg": cat_data["count"] / days if days > 0 else 0,
        }
    
    result["hourly"] = hourly_aggregate
    
    # Get top categories
    sorted_cats = sorted(
        all_categories.items(),
        key=lambda x: x[1]["count"],
        reverse=True
    )
    
    for cat_id, cat_data in sorted_cats[:5]:
        name = CATEGORY_MAPPING.get(cat_id, cat_id.replace("fw_", "FW: ").replace("sribu_", "Sribu: "))
        emoji = CATEGORY_EMOJI.get(cat_id, "📋")
        
        # Calculate trend (compare last 3 days vs previous 3 days)
        trend = _calculate_trend(stats, cat_id, days)
        
        result["top_categories"].append({
            "id": cat_id,
            "name": name,
            "emoji": emoji,
            "count": cat_data["count"],
            "trend": trend,
            "avg_budget": sum(cat_data["budgets"]) / len(cat_data["budgets"]) if cat_data["budgets"] else 0,
        })
    
    return result


def get_category_trend(category_id: str, days: int = 7) -> dict:
    """Get trend for a specific category over N days.
    
    Returns dict with:
    - daily_counts: {date: count}
    - today_count: int
    - yesterday_count: int  
    - change_percent: float (positive = increase)
    - weekly_total: int
    - weekly_avg: float
    """
    stats = _load_trend_stats()
    
    daily_counts = {}
    total_budgets = []
    
    for day_idx in range(days):
        day_key = _date_key(day_idx)
        day_data = stats.get(day_key, {"categories": {}})
        cat_data = day_data.get("categories", {}).get(category_id, {})
        
        daily_counts[day_key] = cat_data.get("count", 0)
        total_budgets.extend(cat_data.get("budgets", []))
    
    today_count = daily_counts.get(_today_key(), 0)
    yesterday_count = daily_counts.get(_date_key(1), 0)
    
    # Calculate change
    if yesterday_count > 0:
        change_percent = ((today_count - yesterday_count) / yesterday_count) * 100
    else:
        change_percent = 100.0 if today_count > 0 else 0.0
    
    weekly_total = sum(daily_counts.values())
    weekly_avg = weekly_total / days if days > 0 else 0
    avg_budget = sum(total_budgets) / len(total_budgets) if total_budgets else 0
    
    return {
        "category_id": category_id,
        "daily_counts": daily_counts,
        "today_count": today_count,
        "yesterday_count": yesterday_count,
        "change_percent": change_percent,
        "weekly_total": weekly_total,
        "weekly_avg": weekly_avg,
        "avg_budget": avg_budget,
    }


def get_peak_hours() -> dict:
    """Find the busiest posting hours based on collected data.
    
    Returns dict with:
    - peak_hours: list of (hour, count) sorted by count desc
    - distribution: {hour: percentage}
    - best_hours: top 3 hours for posting
    """
    stats = _load_trend_stats()
    
    # Aggregate last 7 days
    hourly_counts: dict = {}
    total_count = 0
    
    for day_idx in range(7):
        day_key = _date_key(day_idx)
        day_data = stats.get(day_key, {"hourly": {}})
        
        for hour, count in day_data.get("hourly", {}).items():
            hour_int = int(hour)
            hourly_counts[hour_int] = hourly_counts.get(hour_int, 0) + count
            total_count += count
    
    if total_count == 0:
        return {
            "peak_hours": [],
            "distribution": {},
            "best_hours": [],
            "total_posts": 0,
        }
    
    # Sort by count
    sorted_hours = sorted(hourly_counts.items(), key=lambda x: x[1], reverse=True)
    
    # Calculate distribution percentages
    distribution = {
        hour: (count / total_count) * 100
        for hour, count in hourly_counts.items()
    }
    
    return {
        "peak_hours": sorted_hours,
        "distribution": distribution,
        "best_hours": [h for h, _ in sorted_hours[:3]],
        "total_posts": total_count,
    }


def _calculate_trend(stats: dict, category_id: str, days: int) -> float:
    """Calculate trend percentage for a category.
    
    Compares last 3 days vs previous 3 days.
    Returns positive value if increasing, negative if decreasing.
    """
    recent_count = 0
    older_count = 0
    
    # Last 3 days
    for i in range(3):
        day_key = _date_key(i)
        day_data = stats.get(day_key, {"categories": {}})
        cat_data = day_data.get("categories", {}).get(category_id, {})
        recent_count += cat_data.get("count", 0)
    
    # Previous 3 days
    for i in range(3, 6):
        day_key = _date_key(i)
        day_data = stats.get(day_key, {"categories": {}})
        cat_data = day_data.get("categories", {}).get(category_id, {})
        older_count += cat_data.get("count", 0)
    
    if older_count > 0:
        return ((recent_count - older_count) / older_count) * 100
    elif recent_count > 0:
        return 100.0
    else:
        return 0.0


def get_budget_trend(days: int = 7) -> dict:
    """Get budget trend compared to previous period.
    
    Returns dict with:
    - current_avg: average budget this period
    - previous_avg: average budget previous period
    - change_percent: percentage change
    - direction: "up", "down", or "stable"
    """
    stats = _load_trend_stats()
    
    current_budgets = []
    previous_budgets = []
    
    # This week
    for i in range(days):
        day_key = _date_key(i)
        day_data = stats.get(day_key, {})
        for cat_data in day_data.get("categories", {}).values():
            current_budgets.extend(cat_data.get("budgets", []))
    
    # Previous week
    for i in range(days, days * 2):
        day_key = _date_key(i)
        day_data = stats.get(day_key, {})
        for cat_data in day_data.get("categories", {}).values():
            previous_budgets.extend(cat_data.get("budgets", []))
    
    current_avg = sum(current_budgets) / len(current_budgets) if current_budgets else 0
    previous_avg = sum(previous_budgets) / len(previous_budgets) if previous_budgets else 0
    
    if previous_avg > 0:
        change_percent = ((current_avg - previous_avg) / previous_avg) * 100
    else:
        change_percent = 0.0
    
    if change_percent > 5:
        direction = "up"
    elif change_percent < -5:
        direction = "down"
    else:
        direction = "stable"
    
    return {
        "current_avg": current_avg,
        "previous_avg": previous_avg,
        "change_percent": change_percent,
        "direction": direction,
        "sample_size": len(current_budgets),
    }


# ── Formatters ─────────────────────────────────────────────────────────────────

def format_trend_report() -> str:
    """Format the full trend analysis report for Telegram.
    
    Returns formatted message or None if insufficient data (< 3 days).
    """
    # Check if we have enough data
    stats = _load_trend_stats()
    today = _today_key()
    
    # Check how many days of data we have
    days_with_data = 0
    for i in range(7):
        day_key = _date_key(i)
        if day_key in stats and stats[day_key].get("total_projects", 0) > 0:
            days_with_data += 1
    
    if days_with_data < 3:
        return None
    
    # Build report
    trend = get_trend_stats(7)
    budget_trend = get_budget_trend(7)
    peak_hours = get_peak_hours()
    
    # Date range
    start_date = datetime.strptime(trend["date_range"]["start"], "%Y-%m-%d")
    end_date = datetime.strptime(trend["date_range"]["end"], "%Y-%m-%d")
    date_range_str = f"{start_date.strftime('%b %-d')} - {end_date.strftime('%b %-d, %Y')}"
    
    lines = [
        f"📊 <b>TREND ANALYSIS</b> — Week of {date_range_str}\n",
    ]
    
    # Top categories section
    if trend["top_categories"]:
        lines.append("🔥 <b>Top Categories This Week</b>\n")
        
        # Show top 3 in detail
        for cat in trend["top_categories"][:3]:
            trend_emoji = "📈" if cat["trend"] > 0 else "📉" if cat["trend"] < 0 else "➡️"
            trend_sign = "+" if cat["trend"] > 0 else ""
            avg_budget_str = _format_currency(cat["avg_budget"])
            
            lines.append(
                f"{cat['emoji']} <b>{cat['name']}</b>\n"
                f"   This week: {cat['count']} projects ({trend_emoji} {trend_sign}{cat['trend']:.0f}%)\n"
                f"   Avg budget: {avg_budget_str}\n"
            )
        
        lines.append("")
    
    # Category breakdown for web_dev, mobile_prog, etc
    key_cats = ["web_dev", "mobile_prog", "desktop_prog", "game_prog", "data_entry"]
    
    for cat_id in key_cats:
        if cat_id in trend["weekly_totals"]:
            cat_trend = get_category_trend(cat_id, 7)
            
            emoji = CATEGORY_EMOJI.get(cat_id, "📋")
            name = CATEGORY_MAPPING.get(cat_id, cat_id)
            
            today_count = cat_trend["today_count"]
            yesterday_count = cat_trend["yesterday_count"]
            
            # Calculate short-term change
            if yesterday_count > 0:
                short_change = ((today_count - yesterday_count) / yesterday_count) * 100
            else:
                short_change = 100.0 if today_count > 0 else 0.0
            
            change_emoji = "↑" if short_change > 0 else "↓" if short_change < 0 else "→"
            change_sign = "+" if short_change > 0 else ""
            
            weekly_total = cat_trend["weekly_total"]
            avg_budget = cat_trend["avg_budget"]
            avg_budget_str = _format_currency(avg_budget) if avg_budget > 0 else "N/A"
            
            lines.append(
                f"{emoji} <b>{name}</b>\n"
                f"   Today: {today_count} projects ({change_sign}{short_change:.0f}% {change_emoji})\n"
                f"   This week: {weekly_total} total\n"
                f"   Avg budget: {avg_budget_str}\n"
            )
    
    lines.append("")
    
    # Peak hours
    if peak_hours["best_hours"]:
        hours_str = ", ".join([f"{h}:00" for h in peak_hours["best_hours"]])
        lines.append(f"⏰ <b>Peak Activity Hours:</b> {hours_str} WIB\n")
    
    # Budget trend
    if budget_trend["sample_size"] > 0:
        direction_emoji = "📈" if budget_trend["direction"] == "up" else "📉" if budget_trend["direction"] == "down" else "➡️"
        change_sign = "+" if budget_trend["change_percent"] > 0 else ""
        
        lines.append(
            f"{direction_emoji} <b>Budget Insight</b>\n"
            f"   Avg budget {change_sign}{budget_trend['change_percent']:.0f}% dari minggu lalu\n"
        )
        
        # Find highest budget category
        if trend["top_categories"]:
            top_cat = max(
                trend["top_categories"],
                key=lambda x: x.get("avg_budget", 0)
            )
            if top_cat["avg_budget"] > 0:
                lines.append(
                    f"   💎 {top_cat['name']} budget tertinggi minggu ini\n"
                )
    
    return "\n".join(lines)


def _format_currency(amount: float) -> str:
    """Format amount as Indonesian Rupiah string."""
    if amount >= 1_000_000:
        return f"Rp {amount / 1_000_000:.1f}jt"
    elif amount >= 1_000:
        return f"Rp {amount / 1_000:.0f}rb"
    else:
        return f"Rp {amount:.0f}"


if __name__ == "__main__":
    # Test trend report
    report = format_trend_report()
    if report:
        print(report)
    else:
        print("Butuh data 3+ hari untuk tampilkan trend. Kumpulkan data dulu ya!")
    
    print("\n--- Peak Hours ---")
    peaks = get_peak_hours()
    print(f"Best hours: {peaks['best_hours']}")
    print(f"Total posts: {peaks['total_posts']}")