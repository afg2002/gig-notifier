"""
Fastwork Jobboard scraper using jobboard-api.fastwork.id
Note: API does NOT support server-side tag_id filtering.
      All filtering is done client-side by fetching all pages.
"""

import os
import re
import json
import logging
from dataclasses import dataclass, field
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
    skills: list = field(default_factory=list)
    offers_count: int = 0


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


def _raw_job_to_fastwork_job(item: dict) -> FastworkJob:
    """Convert raw API item to FastworkJob."""
    tag = item.get("tag") or {}
    tag_name = tag.get("name", "Unknown") if isinstance(tag, dict) else "Unknown"
    tag_id = tag.get("id", "") if isinstance(tag, dict) else ""

    # Description
    raw_desc = _clean_text(item.get("description") or "")
    description = raw_desc[:500] + ("..." if len(raw_desc) > 500 else "")

    # Budget (API returns integer like 600000 = Rp 600,000)
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

    # Offers count
    offers = item.get("freelance_offers_count", 0) or 0

    return FastworkJob(
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
        client_name=item.get("user_profile", {}).get("display_name") if isinstance(item.get("user_profile"), dict) else None,
        skills=item.get("skills") or [],
        offers_count=offers,
    )


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


def scrape_all_raw(page: int = 1, page_size: int = 20) -> tuple[list[dict], dict]:
    """Fetch raw API page without any tag filtering."""
    result = _api_request(API_JOBS, {"page": page, "page_size": page_size})
    if "errors" in result:
        logger.error(f"API error: {result['errors']}")
        return [], {}
    data = result.get("data", [])
    meta = result.get("meta", {})
    return data, meta


def scrape_jobs(
    page: int = 1,
    page_size: int = 20,
    tag_id: str = None,
) -> tuple[list[FastworkJob], dict]:
    """
    Fetch page of jobs from API.
    NOTE: tag_id filtering is done CLIENT-SIDE since API doesn't support it.
    For category-specific jobs, use get_jobs_by_tag() instead.
    """
    data, meta = scrape_all_raw(page=page, page_size=page_size)

    jobs = [_raw_job_to_fastwork_job(item) for item in data]

    # Client-side tag filtering (API doesn't support server-side tag_id)
    if tag_id:
        jobs = [j for j in jobs if j.tag_id == tag_id]

    return jobs, meta


def get_job_by_url(url: str) -> Optional[FastworkJob]:
    """
    Fetch a Fastwork job by its URL.
    Uses the API job detail endpoint to get full info including client name.

    Args:
        url: e.g. https://jobboard.fastwork.id/jobs/abc123-def456

    Returns:
        FastworkJob or None if not found.
    """
    # Extract job ID from URL
    job_id_match = re.search(r'/jobs/([a-f0-9-]+)', url)
    if not job_id_match:
        logger.warning(f"Cannot extract job ID from Fastwork URL: {url}")
        return None

    job_id = job_id_match.group(1)
    detail_url = f"{BASE_URL}/api/jobs/{job_id}"

    result = _api_request(detail_url)
    if "errors" in result:
        logger.error(f"Fastwork job detail API error: {result['errors']}")
        return None

    item = result.get("data")
    if not item:
        logger.warning(f"No data for Fastwork job {job_id}")
        return None

    return _raw_job_to_fastwork_job(item)


def get_jobs_by_tag(tag_id: str = None, max_pages: int = 10) -> tuple[list[FastworkJob], int]:
    """
    Get jobs filtered by tag_id, fetching multiple pages until we have enough.
    Returns (jobs, total_count).
    total_count is estimated from the first page's tag distribution.
    """
    all_tag_jobs = []
    total_count = 0

    for page in range(1, max_pages + 1):
        data, meta = scrape_all_raw(page=page, page_size=20)
        if not data:
            break

        if page == 1:
            # Count total for this tag from first page results
            all_jobs_page1 = [_raw_job_to_fastwork_job(item) for item in data]
            if tag_id:
                all_tag_jobs = [j for j in all_jobs_page1 if j.tag_id == tag_id]
                # Estimate total: count this tag in all available pages
                # Since we don't know total, use a heuristic
                total_count = len(all_tag_jobs)  # Will be refined
            else:
                all_tag_jobs = all_jobs_page1
                total_count = meta.get("total_count", 0)
        else:
            page_jobs = [_raw_job_to_fastwork_job(item) for item in data]
            if tag_id:
                page_jobs = [j for j in page_jobs if j.tag_id == tag_id]
            all_tag_jobs.extend(page_jobs)

        if meta.get("total_pages", 1) <= page:
            break

    # Get accurate total count for this tag by counting across first 3 pages
    if tag_id and total_count == 0:
        # Estimate from first page
        count = sum(1 for _ in all_tag_jobs)
        total_count = count * 5  # rough estimate

    return all_tag_jobs, total_count


def scrape_new_jobs(tag_id: str = None, seen_ids: set = None) -> list[FastworkJob]:
    """
    Scrape latest jobs for monitoring. Returns only truly NEW jobs not in seen_ids.
    Used by the polling monitor.
    """
    if seen_ids is None:
        seen_ids = set()

    new_jobs = []
    for page in range(1, 4):  # Check first 3 pages for new jobs
        data, meta = scrape_all_raw(page=page, page_size=20)
        if not data:
            break

        for item in data:
            job = _raw_job_to_fastwork_job(item)
            if tag_id and job.tag_id != tag_id:
                continue
            if job.job_id not in seen_ids:
                new_jobs.append(job)

        if meta.get("total_pages", 1) <= page:
            break

    return new_jobs


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
