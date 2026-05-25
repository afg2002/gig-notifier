"""
Budget intelligence — category budget stats and comparison.
"""
import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BUDGET_STATS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "category_budget_stats.json"
)


def _parse_budget(budget_str: str) -> Optional[float]:
    """Parse budget string like 'Rp 5.000.000 - Rp 10.000.000' to average."""
    import re

    nums = re.findall(r"[\d.]+", budget_str.replace(",", ""))
    if not nums:
        return None
    values = [float(n) for n in nums]
    return sum(values) / len(values)


def _load_budget_stats() -> dict:
    if os.path.exists(BUDGET_STATS_FILE):
        with open(BUDGET_STATS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_budget_stats(stats: dict):
    os.makedirs(os.path.dirname(BUDGET_STATS_FILE), exist_ok=True)
    with open(BUDGET_STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def update_budget_stats(project):
    """Update category budget averages with a new project."""
    category = getattr(project, "category_name", None) or getattr(
        project, "category_id", "unknown"
    )
    budget = _parse_budget(getattr(project, "budget", ""))
    if budget is None:
        return

    stats = _load_budget_stats()
    if category not in stats:
        stats[category] = {"total": 0, "count": 0}
    stats[category]["total"] += budget
    stats[category]["count"] += 1
    _save_budget_stats(stats)


def get_budget_comparison(budget_str: str) -> str:
    """Compare a budget against category average. Returns label like '[PREMIUM]'."""
    budget = _parse_budget(budget_str)
    if budget is None:
        return ""

    stats = _load_budget_stats()
    # Find the category with the closest average
    all_avgs = []
    for cat, s in stats.items():
        if s["count"] > 0:
            all_avgs.append(s["total"] / s["count"])

    if not all_avgs:
        return ""

    avg = sum(all_avgs) / len(all_avgs)
    ratio = budget / avg if avg > 0 else 1

    if ratio >= 1.5:
        return "[PREMIUM]"
    elif ratio >= 1.2:
        return "[ABOVE AVG]"
    elif ratio >= 0.8:
        return "[AVG]"
    else:
        return "[BELOW AVG]"
