"""
Scraper module for projects.co.id using Scrapling StealthyFetcher.
Handles Cloudflare bypass, adaptive element tracking, and category filtering.
"""

import re
import logging
from dataclasses import dataclass, asdict
from typing import Optional

from curl_cffi import requests
from scrapling import Selector
from scrapling.fetchers import StealthyFetcher

# Obscura headless browser (for Cloudflare bypass)
try:
    import obscurascrape as obscura
    OBSCURA_AVAILABLE = obscura.is_available()
except ImportError:
    obscura = None
    OBSCURA_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

BASE_URL = "https://projects.co.id"

# Category definitions: (id, display_name, url_slug, emoji)
CATEGORIES = [
    {
        "id": "all",
        "name": "All",
        "slug": "",
        "emoji": "📋",
        "url": f"{BASE_URL}/public/browse_projects/listing",
    },
    {
        "id": "3d_modeling",
        "name": "3D Modeling & Animation",
        "slug": "28_3d-modeling-and-animation",
        "emoji": "🎬",
    },
    {
        "id": "accounting",
        "name": "Accounting & Consultancy",
        "slug": "24_accounting-and-consultancy",
        "emoji": "💼",
    },
    {
        "id": "audio_video",
        "name": "Audio, Video & Photography",
        "slug": "16_audio-video-and-photography",
        "emoji": "📸",
    },
    {
        "id": "data_entry",
        "name": "Data Entry & Data Mining",
        "slug": "29_data-entry-and-data-mining",
        "emoji": "📊",
    },
    {
        "id": "desktop_prog",
        "name": "Desktop Programming",
        "slug": "2_desktop-programming",
        "emoji": "🖥️",
    },
    {
        "id": "electronics",
        "name": "Electronics & Robotics",
        "slug": "31_electronics-and-robotics",
        "emoji": "🤖",
    },
    {
        "id": "game_prog",
        "name": "Game Programming",
        "slug": "8_game-programming",
        "emoji": "🎮",
    },
    {
        "id": "internet_marketing",
        "name": "Internet Marketing & Social Media",
        "slug": "18_internet-marketing-and-social-media",
        "emoji": "📱",
    },
    {
        "id": "graphic_design",
        "name": "Layout, Logo & Graphic Design",
        "slug": "10_layout-logo-and-graphic-design",
        "emoji": "🎨",
    },
    {
        "id": "mobile_prog",
        "name": "Mobile Programming",
        "slug": "4_mobile-programming",
        "emoji": "📲",
    },
    {
        "id": "network_admin",
        "name": "Network & System Administration",
        "slug": "26_network-and-system-administration",
        "emoji": "🌐",
    },
    {
        "id": "seo",
        "name": "SEO & Website Maintenance",
        "slug": "14_seo-and-website-maintenance",
        "emoji": "🔍",
    },
    {
        "id": "web_dev",
        "name": "Website Development",
        "slug": "6_website-development",
        "emoji": "💻",
    },
    {
        "id": "writing",
        "name": "Writing & Translation",
        "slug": "30_writing-and-translation",
        "emoji": "✍️",
    },
    {"id": "others", "name": "Others", "slug": "32_others", "emoji": "📦"},
]


def get_category_url(category_id: str) -> str:
    """Get the listing URL for a category."""
    cat = next((c for c in CATEGORIES if c["id"] == category_id), CATEGORIES[0])
    if cat["slug"]:
        return f"{BASE_URL}/public/browse_projects/listing/{cat['slug']}"
    return cat["url"]


def get_category_by_id(category_id: str) -> dict:
    """Get category info by ID."""
    return next((c for c in CATEGORIES if c["id"] == category_id), CATEGORIES[0])


@dataclass
class Project:
    project_id: str
    title: str
    description: str
    link: str
    budget: str
    published_date: str
    deadline: str
    finish_days: str
    status: str
    bid_count: str
    need_weekly_report: str
    tags: list[str]
    owner_name: str
    owner_link: str

    @property
    def unique_key(self) -> str:
        return self.project_id

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_text(text: str) -> str:
    """Strip and normalize whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _extract_project_id_from_url(url: str) -> str:
    """Extract project ID from URL like /view/902f1d/pengolahan-data"""
    match = re.search(r"/view/([a-f0-9]+)/", url)
    return match.group(1) if match else ""


def _first(selector_list):
    """Get first element from a Selectors list, or None."""
    if selector_list:
        return selector_list[0]
    return None


def _fetch_html(url: str) -> Selector:
    """Fetch HTML using curl_cffi with Chrome impersonation.
    Falls back to Obscura headless browser (Cloudflare bypass), then StealthyFetcher."""
    try:
        r = requests.get(url, impersonate="chrome120", timeout=30)
        r.raise_for_status()
        # Detect Cloudflare challenge page
        if "cf-chl-bypass" in r.headers or "captcha" in r.text.lower()[:5000]:
            raise ValueError("Cloudflare challenge detected")
        return Selector(content=r.text, url=url)
    except Exception as e:
        logger.warning(f"curl_cffi failed ({e})")

    # Fallback 1: Obscura headless browser (preferred — better stealth)
    if OBSCURA_AVAILABLE:
        try:
            logger.info(f"Falling back to Obscura for {url}")
            html = obscura.fetch_html(url, wait_until="networkidle0", stealth=True)
            return Selector(content=html, url=url)
        except Exception as ex:
            logger.warning(f"Obscura failed ({ex})")

    # Fallback 2: StealthyFetcher (last resort)
    try:
        logger.warning(f"Falling back to StealthyFetcher for {url}")
        StealthyFetcher.adaptive = True
        fetched = StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            block_images=True,
        )
        return fetched
    except Exception as ex:
        logger.error(f"All fetchers failed for {url}: {ex}")
        raise


def scrape_listing(category_id: str = "all", page: int = 1) -> list[Project]:
    """
    Scrape projects.co.id listing page for a specific category.
    Uses curl_cffi for fast HTTP with TLS fingerprint impersonation.
    Returns list of Project dataclasses.
    """
    url = get_category_url(category_id)
    if page > 1:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}page={page}"

    logger.info(f"Fetching {url}")
    fetched = _fetch_html(url)

    projects = []

    # HTML structure per project:
    # <div class="row">
    #   <div class="col-md-2"> owner info </div>
    #   <div class="col-md-10 align-left">
    #     <h2><a href="...">Title</a></h2>
    #     <p>Description</p>
    #     <p><span class="tag">Tags</span></p>
    #     <div class="col-md-12 well img-rounded"> budget info </div>
    #   </div>
    # </div>
    #
    # Strategy: iterate over .col-md-10.align-left containers (each = one project)

    project_containers = fetched.css(".col-md-10.align-left")

    for container in project_containers:
        # Title
        title_els = container.css("h2 > a")
        title_el = _first(title_els)
        if not title_el:
            continue

        title = _clean_text(title_el.text)
        link = title_el.attrib.get("href", "")

        if not title or not link:
            continue

        project_id = _extract_project_id_from_url(link)

        # Description: first <p> after h2 that doesn't contain tags
        description = ""
        for p_el in container.css("h2 ~ p"):
            # Skip the p that contains tags (has .tag.label children)
            if p_el.css(".tag.label"):
                continue
            desc_text = p_el.get_all_text() if p_el.css("*") else p_el.text
            description = _clean_text(desc_text)
            break

        # Tags
        tags = []
        for tag_el in container.css(".tag.label a"):
            tag_text = _clean_text(tag_el.get_all_text())
            if tag_text:
                tags.append(tag_text)

        # Budget info from .well
        well_els = container.css(".well.img-rounded")
        well = _first(well_els)
        if not well:
            continue

        well_text = well.get_all_text()

        # Extract fields from well text
        budget = _extract_field(
            well_text, r"Published Budget:\s*(.+?)\s*Published Date:"
        )
        published_date = _extract_field(
            well_text, r"Published Date:\s*(.+?)\s*Select Deadline:"
        )
        deadline = _extract_field(well_text, r"Select Deadline:\s*(.+?)\s*Finish Days:")
        finish_days = _extract_field(
            well_text, r"Finish Days:\s*(.+?)\s*Project Status:"
        )
        status = _extract_field(well_text, r"Project Status:\s*(.+?)\s*Bid Count:")
        bid_count = _extract_field(
            well_text, r"Bid Count:\s*(.+?)\s*Need Weekly Report:"
        )
        weekly_report_text = _extract_field(well_text, r"Need Weekly Report:\s*(.+)$")
        need_weekly_report = "Yes" if "fa-check" in (weekly_report_text or "") else "No"

        # Owner info — from the sibling .col-md-2 in the parent .row
        owner_name = "Unknown"
        owner_link = ""
        parent_row = container.parent
        if parent_row:
            owner_els = parent_row.css(".user-info .short-username")
            owner_el = _first(owner_els)
            if owner_el:
                owner_name = _clean_text(owner_el.get_all_text())
                owner_link = owner_el.attrib.get("href", "")

        project = Project(
            project_id=project_id,
            title=title,
            description=description,
            link=link if link.startswith("http") else f"{BASE_URL}{link}",
            budget=budget,
            published_date=published_date,
            deadline=deadline,
            finish_days=finish_days,
            status=status,
            bid_count=bid_count,
            need_weekly_report=need_weekly_report,
            tags=tags,
            owner_name=owner_name,
            owner_link=owner_link
            if owner_link.startswith("http")
            else f"{BASE_URL}{owner_link}",
        )
        projects.append(project)

    logger.info(f"Scraped {len(projects)} projects from {category_id} page {page}")
    return projects


def scrape_categories(
    category_ids: list[str], page: int = 1
) -> dict[str, list[Project]]:
    """Scrape multiple categories and return dict of category_id -> projects."""
    results = {}
    for cat_id in category_ids:
        try:
            results[cat_id] = scrape_listing(cat_id, page)
        except Exception as e:
            logger.error(f"Error scraping category {cat_id}: {e}")
            results[cat_id] = []
    return results


def _extract_field(text: str, pattern: str) -> Optional[str]:
    """Extract a field value using regex from the well text."""
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return _clean_text(match.group(1))
    return None


def scrape_all_pages(
    category_id: str = "all",
    max_pages: int = 10,
    progress_callback=None,
) -> list[Project]:
    """Scrape all pages for a category and return combined projects.

    Args:
        category_id: Category to scrape
        max_pages: Maximum pages to fetch
        progress_callback: Optional callback(current, total, count) called after each page
    """
    all_projects = []
    for page in range(1, max_pages + 1):
        try:
            projects = scrape_listing(category_id, page)
            all_projects.extend(projects)
            if progress_callback:
                progress_callback(page, max_pages, len(all_projects))
            if not projects:
                break
        except Exception as e:
            logger.error(f"Error scraping page {page}: {e}")
            break
    return all_projects


if __name__ == "__main__":
    results = scrape_listing("all", 1)
    for p in results[:3]:
        print(f"\n{'=' * 60}")
        print(f"Title: {p.title}")
        print(f"Budget: {p.budget}")
        print(f"Published: {p.published_date}")
        print(f"Deadline: {p.deadline}")
        print(f"Finish Days: {p.finish_days}")
        print(f"Status: {p.status}")
        print(f"Bid Count: {p.bid_count}")
        print(f"Weekly Report: {p.need_weekly_report}")
        print(f"Tags: {', '.join(p.tags)}")
        print(f"Owner: {p.owner_name}")
        print(f"Link: {p.link}")
        print(f"Description: {p.description[:100]}...")
