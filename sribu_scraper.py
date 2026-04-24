"""
Sribu scraper using GraphQL API at app.api.v2.sribu.com (no auth required).
Budget scraped from detail pages via browser (HTML rendering required).
"""

import os
import re
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

import urllib.request
import urllib.error

BASE_URL = "https://www.sribu.com"
API_BASE = "https://app.api.v2.sribu.com/graphql"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Hardcoded category map (id -> {name, emoji, slug})
# These are discovered from API usage; unknown categories use raw ID
CATEGORY_MAP = {
    "1ef818a5-3a17-4dd1-80e2-685cf3da5946": {
        "name": "Logo & Branding",
        "emoji": "🎨",
        "slug": "logo-branding",
    },
    "4f69e2a5-4f25-48fa-914c-4bca1aa66301": {
        "name": "Kemasan",
        "emoji": "📦",
        "slug": "kemasan",
    },
    "7c0786ec-1122-4186-9e7c-e85984de772a": {
        "name": "Desain Logo",
        "emoji": "🎨",
        "slug": "desain-logo",
    },
    "f9e36e5f-d6f9-4c1a-b5d4-e9f8c8a3b7d2": {
        "name": "Website & Programming",
        "emoji": "💻",
        "slug": "website-programming",
    },
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890": {
        "name": "Video & Audio",
        "emoji": "🎬",
        "slug": "video-audio",
    },
    "b2c3d4e5-f6a7-8901-bcde-f23456789012": {
        "name": "Writing & Translation",
        "emoji": "✍️",
        "slug": "writing-translation",
    },
    "c3d4e5f6-a7b8-9012-cdef-345678901234": {
        "name": "Digital Marketing",
        "emoji": "📱",
        "slug": "digital-marketing",
    },
}


def _get_category_info(category_id: str) -> dict:
    return CATEGORY_MAP.get(category_id, {"name": category_id, "emoji": "📂", "slug": ""})


def _status_label(status: int) -> str:
    return {2: "Aktif", 3: "Ditutup", 4: "Selesai", 1: "Draft"}.get(status, f"Status {status}")


def _format_deadline(deadline_str: str) -> str:
    """Parse ISO deadline string to dd Mmm YYYY format."""
    if not deadline_str:
        return "-"
    try:
        dt = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return deadline_str[:10] if deadline_str else "-"


def _format_date(date_str: str) -> str:
    """Parse ISO date string to dd Mmm YYYY format."""
    if not date_str:
        return "-"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return date_str[:10] if date_str else "-"


@dataclass
class SribuContest:
    contest_id: str
    title: str
    description: str
    category_id: str
    category_name: str
    category_emoji: str
    status: int
    status_label: str
    budget: Optional[str]
    budget_raw: Optional[str]
    tags: list
    created_at: str
    deadline: str
    deadline_formatted: str
    client_id: str
    contest_url: str

    @property
    def unique_key(self) -> str:
        return self.contest_id

    def to_dict(self) -> dict:
        return asdict(self)


def _api_request(query: str, variables: dict = None) -> dict:
    payload = {"query": query, "variables": variables or {}}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_BASE,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/147.0.7727.15 Safari/537.36",
            "Origin": "https://www.sribu.com",
            "Referer": "https://www.sribu.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"errors": [{"message": f"HTTP {e.code}"}]}
    except Exception as e:
        return {"errors": [{"message": str(e)}]}


def scrape_listing(
    category_id: str = "all", page: int = 1, per_page: int = 10
) -> list[SribuContest]:
    """
    Scrape Sribu contests from GraphQL API.
    Uses inline query values to bypass GraphQL variable null issues.
    """
    per_page = min(per_page, 20)

    if category_id == "all":
        query = (
            f"query GetContests {{ getContests(page: {page}, per_page: {per_page}) {{ "
            f"data {{ id title status category_id client_id description createdAt closedAt tags }} }} }}"
        )
    else:
        query = (
            f"query GetContests {{ getContests(page: {page}, per_page: {per_page}, category_id: \"{category_id}\") {{ "
            f"data {{ id title status category_id client_id description createdAt closedAt tags }} }} }}"
        )

    data = _api_request(query, {})

    contests = []
    errors = data.get("errors", [])
    if errors:
        logger.error(f"API errors: {[e['message'] for e in errors]}")
        return []

    raw_data = data.get("data", {}).get("getContests", {}).get("data", [])
    for item in raw_data:
        cat_info = _get_category_info(item.get("category_id", ""))
        cat_name = cat_info["name"]
        cat_emoji = cat_info["emoji"]

        deadline_raw = item.get("closedAt") or ""
        contest = SribuContest(
            contest_id=item["id"],
            title=item["title"],
            description=item.get("description", "") or "",
            category_id=item.get("category_id", ""),
            category_name=cat_name,
            category_emoji=cat_emoji,
            status=item.get("status", 0),
            status_label=_status_label(item.get("status", 0)),
            budget=None,  # Budget scraped separately via detail page
            budget_raw=None,
            tags=item.get("tags") or [],
            created_at=_format_date(item.get("createdAt", "")),
            deadline=deadline_raw,
            deadline_formatted=_format_deadline(deadline_raw),
            client_id=item.get("client_id", ""),
            contest_url=f"https://www.sribu.com/contests/detail/{item['id']}",
        )
        contests.append(contest)

    logger.info(f"Scraped {len(contests)} contests (cat={category_id}, page={page})")
    return contests


def scrape_detail_budget(contest_id: str) -> Optional[str]:
    """
    Scrape budget from contest detail page using browser.
    Requires headless browser (Playwright/Puppeteer) since page is Next.js SSR.
    Returns budget string like "Rp 5.000.000" or None if not found.
    """
    # This would be called by bot.py which has browser access
    # For now, return None - bot.py will handle browser scraping
    return None


def scrape_new_contests(
    seen_ids: set = None, category_id: str = "all", max_pages: int = 3
) -> list[SribuContest]:
    """
    Scrape latest contests for monitoring.
    Returns only contests not in seen_ids.
    """
    if seen_ids is None:
        seen_ids = set()

    new_contests = []
    for page in range(1, max_pages + 1):
        contests = scrape_listing(category_id, page, per_page=10)
        if not contests:
            break

        for c in contests:
            if c.contest_id not in seen_ids:
                new_contests.append(c)

    return new_contests


def get_categories() -> list[dict]:
    """Return available categories."""
    cats = []
    for cid, info in CATEGORY_MAP.items():
        cats.append({"id": cid, "name": info["name"], "emoji": info["emoji"], "slug": info["slug"]})
    return cats


# Aliases for consistency with bot.py naming conventions
def get_sribu_categories() -> list[dict]:
    """Alias for get_categories()."""
    return get_categories()


def scrape_sribu_listing(category_id: str = "all", page: int = 1, per_page: int = 10) -> list[SribuContest]:
    """Alias for scrape_listing()."""
    return scrape_listing(category_id, page, per_page)


if __name__ == "__main__":
    print("=== Sribu Scraper ===")
    print("\n--- Latest Contests (page 1) ---")
    contests = scrape_listing("all", 1, 10)
    for i, c in enumerate(contests):
        print(f"\n[{i+1}] {c.title}")
        print(f"  Category: {c.category_emoji} {c.category_name}")
        print(f"  Status: {c.status_label} | Deadline: {c.deadline_formatted}")
        print(f"  Tags: {', '.join(c.tags)}")
        print(f"  URL: {c.contest_url}")
        print(f"  Created: {c.created_at}")