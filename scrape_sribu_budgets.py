"""
Sribu Budget Scraper - fetches budget/prize from contest detail pages using browser.
This is a separate script that can be run periodically to update budget cache.
It stores budget data in a JSON file that the main bot reads from.

Usage:
    python3 scrape_sribu_budgets.py              # scrape top contests
    python3 scrape_sribu_budgets.py --contest-id <id>  # scrape single contest
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from sribu_scraper import scrape_listing, SribuContest

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
BUDGET_CACHE = DATA_DIR / "sribu_budget_cache.json"


def load_budget_cache() -> dict:
    if BUDGET_CACHE.exists():
        with open(BUDGET_CACHE) as f:
            return json.load(f)
    return {}


def save_budget_cache(cache: dict):
    with open(BUDGET_CACHE, "w") as f:
        json.dump(cache, f, indent=2)


def scrape_budget_via_browser(contest_id: str) -> str:
    """
    Use the hermes-agent's browser tool to fetch budget from detail page.
    This reads from the mcp_browser_* tool output to get page content.
    
    Since we can't call MCP tools from a standalone script directly,
    we'll use the browser session that should already be running.
    
    For standalone use, we try curl-based approaches first.
    """
    # Try the API first to see if budget is accessible via API now
    from sribu_scraper import _api_request
    
    # Try to get budget via GraphQL with different field names
    query = f"""
    query GetContestBudget {{
        getContest(id: "{contest_id}") {{
            prize
            budget
            amount
            price
            min_budget
            max_budget
            contest_draft {{
                prize
                price
                budget
            }}
        }}
    }}
    """
    result = _api_request(query, {})
    
    # Check for errors or data
    if "errors" not in result or not result["errors"]:
        data = result.get("data", {}).get("getContest", {})
        if data:
            for field in ["prize", "budget", "amount", "price", "min_budget"]:
                val = data.get(field)
                if val:
                    return str(val)
            draft = data.get("contest_draft", {})
            if draft:
                for field in ["prize", "price", "budget"]:
                    val = draft.get(field)
                    if val:
                        return str(val)
    
    return None


def parse_budget_from_html(html: str, contest_url: str) -> str:
    """Parse budget from HTML content of contest detail page."""
    import re
    
    # Try to find price patterns
    # Pattern 1: "Rp 5.000.000" or "IDR 5.000.000"
    patterns = [
        r'Rp\s*([\d.]+)',
        r'IDR\s*([\d.]+)',
        r'\$\s*([\d.]+)',
        r'price["\s:]+([^"<]{5,20})',
        r'prize["\s:]+([^"<]{5,20})',
        r'budget["\s:]+([^"<]{5,20})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            # Clean up and format
            value = re.sub(r'[^\d.]', '', value)
            if value:
                try:
                    num = float(value.replace('.', '').replace(',', ''))
                    if num > 1000:
                        return f"Rp {num:,.0f}"
                except:
                    pass
    
    return None


async def scrape_budget_with_browser(session, contest_id: str) -> str:
    """
    Scrape budget using an active browser session.
    session: a mcp_browser_* compatible session object
    """
    url = f"https://www.sribu.com/contests/detail/{contest_id}"
    try:
        await session.navigate(url)
        await asyncio.sleep(3)  # Wait for JS to render
        
        # Try to extract price from page
        script = """
        () => {
            // Look for price in common elements
            const pricePatterns = [
                document.querySelector('[class*="price"]'),
                document.querySelector('[class*="prize"]'),
                document.querySelector('[class*="budget"]'),
                document.querySelector('[data-price]'),
                document.querySelector('[data-prize]'),
            ];
            for (const el of pricePatterns) {
                if (el) return el.textContent.trim();
            }
            // Look in JSON-LD
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            for (const s of scripts) {
                try {
                    const d = JSON.parse(s.textContent);
                    if (d.price) return String(d.price);
                    if (d.offers?.price) return String(d.offers.price);
                } catch(e) {}
            }
            // Look in meta tags
            const meta = document.querySelector('meta[property="og:price:amount"]');
            if (meta) return meta.content;
            return null;
        }
        """
        # This would need the actual session API - placeholder
        return None
    except Exception as e:
        logger.debug(f"Browser scrape error for {contest_id}: {e}")
        return None


def update_cache_for_contests(contests: list[SribuContest], force: bool = False) -> dict:
    """Update budget cache for given contests."""
    cache = load_budget_cache()
    updated = 0
    
    for contest in contests:
        cid = contest.contest_id
        if cid in cache and not force:
            continue
        
        budget = scrape_budget_via_browser(cid)
        if budget:
            cache[cid] = {
                "budget": budget,
                "title": contest.title,
                "updated": contest.deadline,
            }
            updated += 1
            logger.info(f"Budget scraped: {contest.title[:50]} -> {budget}")
        else:
            # Mark as checked even if no budget found
            cache[cid] = {
                "budget": None,
                "title": contest.title,
                "updated": contest.deadline,
                "checked": True,
            }
    
    save_budget_cache(cache)
    logger.info(f"Updated {updated} budgets, total cached: {len(cache)}")
    return cache


def get_cached_budget(contest_id: str) -> str:
    """Get cached budget for a contest ID."""
    cache = load_budget_cache()
    entry = cache.get(contest_id, {})
    return entry.get("budget") or entry.get("budget_raw") or None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Sribu contest budgets")
    parser.add_argument("--contest-id", help="Scrape single contest ID")
    parser.add_argument("--force", action="store_true", help="Force re-scrape cached entries")
    parser.add_argument("--pages", type=int, default=3, help="Number of pages to scrape")
    args = parser.parse_args()

    import asyncio

    if args.contest_id:
        contests = [type('Contest', (), {
            'contest_id': args.contest_id,
            'title': args.contest_id,
            'deadline': '',
        })()]
    else:
        print("Scraping latest contests to update budget cache...")
        contests = []
        for page in range(1, args.pages + 1):
            page_contests = scrape_listing("all", page, 10)
            contests.extend(page_contests)
            if not page_contests:
                break

    print(f"Got {len(contests)} contests, updating budget cache...")
    cache = update_cache_for_contests(contests, force=args.force)
    
    # Show current cache summary
    with_budget = sum(1 for v in cache.values() if v.get("budget"))
    print(f"Cache: {len(cache)} total, {with_budget} with budget")