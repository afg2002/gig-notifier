"""
Fastwork Jobboard scraper using jobboard-api.fastwork.id
"""

import os
import re
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import urllib.request
import urllib.error

BASE_URL = "https://jobboard-api.fastwork.id"
API_JOBS = f"{BASE_URL}/api/jobs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class FastworkJob:
    job_id: str
    title: str
    description: str
    budget: str
    tag_name: str
    tag_id: str
    status: str
    type: str
    published_date: str
    link: str
    client_name: Optional[str] = None
    skills: list = None

    def __post_init__(self):
        if self.skills is None:
            self.skills = []


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _api_request(url: str, params: dict = None) -> dict:
    """Make GET request to Fastwork API."""
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"errors": {"detail": f"HTTP {e.code}"}}
    except Exception as e:
        logger.error(f"API request error: {e}")
        return {"errors": {"detail": str(e)}}


def get_categories() -> list[dict]:
    """Get available job categories/tags from API."""
    data = _api_request(f"{BASE_URL}/api/tags", {"page_size": 100})
    cats = []
    for item in data.get("data", []):
        cats.append({
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "sort": item.get("sort", 99),
        })
    return sorted(cats, key=lambda x: x["sort"])


def scrape_jobs(
    page: int = 1,
    page_size: int = 20,
    tag_id: str = None,
    status: str = "open",
) -> tuple[list[FastworkJob], dict]:
    """Scrape jobs from Fastwork API. Returns (jobs, meta)."""
    params = {
        "page": page,
        "page_size": page_size,
    }
    if tag_id:
        params["tag_id"] = tag_id
    if status:
        params["status"] = status

    result = _api_request(API_JOBS, params)

    if "errors" in result:
        logger.error(f"API error: {result['errors']}")
        return [], {}

    jobs = []
    for item in result.get("data", []):
        tag = item.get("tag", {}) or {}
        tag_name = tag.get("name", "Unknown")
        tag_id = tag.get("id", "")

        # Build description (truncate for display)
        raw_desc = _clean_text(item.get("description") or "")
        description = raw_desc[:500] + ("..." if len(raw_desc) > 500 else "")

        # Budget
        budget_raw = item.get("budget")
        if budget_raw:
            try:
                budget_val = int(budget_raw)
                budget_str = f"Rp {budget_val:,.0f}"
            except (ValueError, TypeError):
                budget_str = str(budget_raw)
        else:
            budget_str = "-"

        # Published date
        pub = item.get("published_at") or item.get("inserted_at") or ""
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                published = dt.strftime("%d %b %Y")
            except Exception:
                published = pub[:10]
        else:
            published = "-"

        job = FastworkJob(
            job_id=item.get("id", ""),
            title=_clean_text(item.get("title", "")),
            description=description,
            budget=budget_str,
            tag_name=tag_name,
            tag_id=tag_id,
            status=item.get("status", ""),
            type=item.get("type", "freelance"),
            published_date=published,
            link=f"https://jobboard.fastwork.id/jobs/{item.get('id', '')}",
            client_name=None,
            skills=item.get("skills") or [],
        )
        jobs.append(job)

    meta = result.get("meta", {})
    return jobs, meta


def scrape_all_pages(tag_id: str = None, max_pages: int = 3, progress_callback=None) -> list[FastworkJob]:
    """Scrape multiple pages of jobs."""
    all_jobs = []
    for page in range(1, max_pages + 1):
        jobs, meta = scrape_jobs(page=page, page_size=20, tag_id=tag_id)
        if not jobs:
            break
        all_jobs.extend(jobs)
        if progress_callback:
            progress_callback(page, max_pages, len(all_jobs))
        if meta.get("total_pages", 1) <= page:
            break
    return all_jobs


if __name__ == "__main__":
    jobs, meta = scrape_jobs(page=1, page_size=5)
    print(f"Total: {meta.get('total_count', '?')} jobs, {meta.get('total_pages', 1)} pages")
    print()
    for job in jobs:
        print(f"[{job.tag_name}] {job.title}")
        print(f"  Budget: {job.budget} | Published: {job.published_date}")
        print(f"  Description: {job.description[:100]}...")
        print(f"  Link: {job.link}")
        print()
