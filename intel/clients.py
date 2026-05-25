"""
Client reputation — tracks client history across projects.
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

CLIENT_STATS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "client_stats.json"
)


def _load_client_stats() -> dict:
    if os.path.exists(CLIENT_STATS_FILE):
        with open(CLIENT_STATS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_client_stats(stats: dict):
    os.makedirs(os.path.dirname(CLIENT_STATS_FILE), exist_ok=True)
    with open(CLIENT_STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def update_client_stats(project):
    """Track client project count."""
    owner = getattr(project, "owner_name", None) or "unknown"
    if not owner or owner == "N/A":
        return

    stats = _load_client_stats()
    if owner not in stats:
        stats[owner] = {"count": 0}
    stats[owner]["count"] += 1
    _save_client_stats(stats)


def get_client_reputation(owner_name: str) -> str:
    """Return reputation label based on project count."""
    if not owner_name or owner_name == "N/A":
        return "[NEW]"

    stats = _load_client_stats()
    client = stats.get(owner_name, {})
    count = client.get("count", 0)

    if count >= 10:
        return "[VETERAN]"
    elif count >= 5:
        return "[REGULAR]"
    elif count >= 1:
        return "[KNOWN]"
    else:
        return "[NEW]"
