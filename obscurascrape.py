"""
Obscura headless browser scraper wrapper.
Uses the Obscura binary (https://github.com/h4ckf0r0day/obscura)
for stealthy, JavaScript-rendered web scraping.

Install:
  # Pre-built binary (fastest)
  curl -LO https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-x86_64-linux.tar.gz
  tar xzf obscura-x86_64-linux.tar.gz

  # Or build from source (Rust required)
  git clone https://github.com/h4ckf0r0day/obscura
  cd obscura && cargo build --release --features stealth
  # Binary ends up at target/release/obscura

Set OBSCURA_BIN env var if not in PATH.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Path to Obscura binary — set via OBSCURA_BIN env or auto-detect
OBSCURA_BIN = os.getenv("OBSCURA_BIN", "")

FALLBACK_BINS = [
    Path("/root/.cargo/bin/obscura"),
    Path("/usr/local/bin/obscura"),
    Path("./obscura"),
    Path("/tmp/obscura/target/release/obscura"),
    Path("/usr/bin/obscura"),
]


def _find_obscura() -> Optional[Path]:
    """Find obscura binary, checking env first then common paths."""
    if OBSCURA_BIN and Path(OBSCURA_BIN).exists():
        return Path(OBSCURA_BIN)
    for path in FALLBACK_BINS:
        if path.exists():
            return path
    return None


def _run_obscura(args: list[str], timeout: int = 30) -> str:
    """Run obscura CLI and return stdout. Silences stderr to suppress console noise."""
    bin_path = _find_obscura()
    if not bin_path:
        raise RuntimeError(
            "Obscura binary not found. Install via:\n"
            "  curl -LO https://github.com/h4ckf0r0day/obscura/releases/latest/download/obscura-x86_64-linux.tar.gz\n"
            "  tar xzf obscura-x86_64-linux.tar.gz && ./obscura --version\n"
            "Or build from source: git clone https://github.com/h4ckf0r0day/obscura && cd obscura && cargo build --release --features stealth"
        )

    cmd = [str(bin_path)] + args
    logger.debug(f"[Obscura] Running: {' '.join(cmd)}")
    # Obscura outputs HTML to stderr, not stdout — merge them so we capture HTML from result.stdout
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.warning(f"obscura returned {result.returncode}")
    return result.stdout


def fetch_html(url: str, wait_until: str = "load", stealth: bool = True, timeout: int = 60) -> str:
    """
    Fetch a URL and return rendered HTML after JavaScript execution.

    Args:
        url: Target URL
        wait_until: When to consider page loaded.
                    Options: 'load', 'domcontentloaded', 'networkidle0'
                    Use 'load' for most pages (faster, more reliable).
                    'networkidle0' may timeout on pages with analytics/telemetry pings.
        stealth: Enable anti-detection measures (recommended).
        timeout: Max seconds to wait per request (default 60 — Obscura is slow).

    Returns:
        Rendered HTML as string.
    """
    args = ["fetch", url, "--dump", "html"]
    if stealth:
        args.append("--stealth")
    args.extend(["--wait-until", wait_until])

    logger.info(f"[Obscura] Fetching {url} (stealth={stealth}, wait_until={wait_until})")
    html = _run_obscura(args, timeout=timeout)
    logger.info(f"[Obscura] Got {len(html)} bytes from {url}")
    return html


def fetch_text(url: str, selector: str, wait_until: str = "load", stealth: bool = True) -> Optional[str]:
    """
    Fetch a URL and extract text content from the first element matching CSS selector.

    Args:
        url: Target URL
        selector: CSS selector (e.g., 'h1', '.budget', '#price')
        wait_until: When to consider page loaded.

    Returns:
        Text content of first matching element, or None if not found.
    """
    # Build a JS expression that queries and returns text content
    js_expr = (
        f"(function(){{"
        f"var el=document.querySelector('{selector}');"
        f"return el?el.textContent.trim():null;"
        f"}})()"
    )

    args = [
        "fetch", url,
        "--eval", js_expr,
        "--stealth" if stealth else None,
        "--wait-until", wait_until,
    ]
    args = [a for a in args if a]

    output = _run_obscura(args).strip()
    if not output or output == "null":
        return None
    return output


def fetch_json(url: str, js_expr: str, wait_until: str = "load", stealth: bool = True) -> Optional[dict]:
    """
    Fetch a URL and evaluate a JS expression, parsing result as JSON.

    Useful for extracting structured data from page state (e.g., Next.js __NEXT_DATA__).

    Args:
        url: Target URL
        js_expr: JS expression that returns a value (will be wrapped)
        wait_until: When to consider page loaded.

    Returns:
        Parsed JSON result, or None if evaluation fails.
    """
    wrapped = f"(function(){{ return JSON.stringify({js_expr}); }})()"
    args = [
        "fetch", url,
        "--eval", wrapped,
        "--stealth" if stealth else None,
        "--wait-until", wait_until,
    ]
    args = [a for a in args if a]

    output = _run_obscura(args).strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        logger.warning(f"[Obscura] Failed to parse JSON from {url}: {output[:100]}")
        return None


def scrape_projects_detail(project_id: str, base_url: str = "https://projects.co.id") -> dict:
    """
    Scrape a projects.co.id detail page using Obscura.
    Used as fallback when curl_cffi gets blocked by Cloudflare.

    Args:
        project_id: The project ID (e.g., '902f1d')
        base_url: Base URL for projects.co.id

    Returns:
        Dict with project fields, or empty dict if failed.
    """
    url = f"{base_url}/public/browse_projects/show/{project_id}"
    html = fetch_html(url, wait_until="networkidle0", stealth=True)

    result = {}

    # Title
    title_match = re.search(r'<h[12][^>]*>([^<]+)</h[12]>', html, re.IGNORECASE)
    if title_match:
        result["title"] = title_match.group(1).strip()

    # Budget: "Published Budget:" pattern
    budget_match = re.search(
        r'Published\s+Budget:\s*([^\s<"\n]+)', html, re.IGNORECASE
    )
    if budget_match:
        result["budget"] = budget_match.group(1).strip()

    # Description
    desc_match = re.search(
        r'class=["\']description["\'][^>]*>([^<]+)', html, re.IGNORECASE
    )
    if desc_match:
        result["description"] = desc_match.group(1).strip()

    return result


def scrape_sribu_budget(contest_id: str) -> Optional[str]:
    """
    Scrape budget/prize from a sribu.com contest detail page using Obscura.
    The page is Next.js SSR so curl_cffi can't get budget — Obscura renders JS.

    Args:
        contest_id: Sribu contest UUID (e.g., '0e0796d9-e698-40a7-839a-84b3e7228d03')

    Returns:
        Budget string like 'Rp 5.000.000' or None if not found.
    """
    url = f"https://www.sribu.com/contests/detail/{contest_id}"

    # domcontentloaded fires after SSR HTML is received but before full JS hydration.
    # This is enough to capture budget data which is in the initial HTML payload.
    html = fetch_html(url, wait_until="domcontentloaded", stealth=False, timeout=90)

    # Strategy 1: Try __NEXT_DATA__ JSON embedded in page (contains prize)
    import re, json
    next_data_match = re.search(r'__NEXT_DATA__[^>]*>([^<]+)', html)
    if next_data_match:
        try:
            next_data = json.loads(next_data_match.group(1))
            prize = (
                next_data.get("props", {})
                .get("pageProps", {})
                .get("contest", {})
                .get("prize")
            )
            if prize:
                logger.info(f"[Obscura] Sribu budget for {contest_id} (from __NEXT_DATA__): {prize}")
                return prize
        except (json.JSONDecodeError, KeyError):
            pass

    # Strategy 2: Regex Rp patterns from rendered HTML (e.g., Rp3Jt, Rp5.000.000)
    rp_matches = re.findall(r'Rp(\d+(?:\.\d+)?\s*(?:Jt|Juta|Rp|rbu|Rb|000)\b)', html, re.IGNORECASE)
    if rp_matches:
        best = max(rp_matches, key=len)
        budget = f"Rp {best.replace('Rp', '').strip()}"
        if budget:
            logger.info(f"[Obscura] Sribu budget for {contest_id}: {budget}")
            return budget

    logger.warning(f"[Obscura] Could not find budget for Sribu contest {contest_id}")
    return None


def is_available() -> bool:
    """Check if Obscura binary is available on this system."""
    return _find_obscura() is not None
