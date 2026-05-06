"""
AI Proposal Generator for gig-notifier bot.
Generates professional freelance proposals using LLM (OpenRouter) or template fallback.
"""

import os
import re
import json
import logging
import asyncio
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PROPOSAL_CACHE_FILE = os.path.join(DATA_DIR, "proposal_cache.json")
PROPOSAL_RATE_FILE = os.path.join(DATA_DIR, "proposal_rate.json")
MAX_PROPOSALS_PER_DAY = 5

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "qwen/qwen3-next-80b-a3b-instruct:free"  # Free tier

# ── Freelancer Profile ────────────────────────────────────────────────────────

FREELANCER_PROFILE = {
    "name": "Afghan Eka Pangestu",
    "title": "Full-Stack Developer",
    "email": "afghanekapangestu@gmail.com",
    "github": "https://github.com/afg2002",
    "skills": "Java, Spring Boot, Golang, React, PostgreSQL, MongoDB, Web Development",
    "experience_years": 5,
    "portfolio": "https://github.com/afg2002",
    "linkedin": "",
    "phone": "",
}

# ── Rate Limiting ─────────────────────────────────────────────────────────────

def _load_rate_limits() -> dict:
    """Load rate limit tracking from file."""
    if os.path.exists(PROPOSAL_RATE_FILE):
        try:
            with open(PROPOSAL_RATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_rate_limits(data: dict):
    """Save rate limit tracking to file."""
    with open(PROPOSAL_RATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _check_rate_limit(chat_id: str) -> tuple[bool, int]:
    """Check if user has reached daily proposal limit.
    Returns (allowed, remaining_count).
    """
    today = date.today().isoformat()
    limits = _load_rate_limits()
    
    user_data = limits.get(chat_id, {"date": None, "count": 0})
    
    # Reset if new day
    if user_data.get("date") != today:
        user_data = {"date": today, "count": 0}
    
    remaining = MAX_PROPOSALS_PER_DAY - user_data["count"]
    allowed = remaining > 0
    
    return allowed, max(0, remaining)


def _increment_proposal_count(chat_id: str):
    """Increment proposal count for user today."""
    today = date.today().isoformat()
    limits = _load_rate_limits()
    
    user_data = limits.get(chat_id, {"date": None, "count": 0})
    
    if user_data.get("date") != today:
        user_data = {"date": today, "count": 0}
    
    user_data["count"] += 1
    limits[chat_id] = user_data
    _save_rate_limits(limits)


# ── Proposal Cache ────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    """Load cached proposals from file."""
    if os.path.exists(PROPOSAL_CACHE_FILE):
        try:
            with open(PROPOSAL_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_cache(cache: dict):
    """Save proposal cache to file."""
    with open(PROPOSAL_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_cached_proposal(project_key: str) -> Optional[str]:
    """Get cached proposal if exists."""
    cache = _load_cache()
    entry = cache.get(project_key)
    if entry:
        # Check if less than 24 hours old
        cached_time = datetime.fromisoformat(entry["cached_at"])
        if (datetime.now() - cached_time).total_seconds() < 86400:
            return entry["proposal"]
    return None


def cache_proposal(project_key: str, proposal: str):
    """Cache a generated proposal."""
    cache = _load_cache()
    cache[project_key] = {
        "proposal": proposal,
        "cached_at": datetime.now().isoformat(),
    }
    _save_cache(cache)


# ── Budget Parser ─────────────────────────────────────────────────────────────

def _parse_budget(budget_str: str) -> Optional[float]:
    """Extract numeric budget value from string.
    
    Handles formats like:
    - "Rp 500.000"
    - "Rp 500.000 - 1.000.000"
    - "Rp 500rb"
    - "500.000"
    """
    if not budget_str or budget_str == "-":
        return None
    
    # Clean the string - remove "Rp", dots (thousand separators), quotes
    cleaned = budget_str.replace("Rp", "").replace(".", "").replace("'", "").strip()
    
    # Find all numbers
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


# ── Template-based Proposal ────────────────────────────────────────────────────

def _generate_template_proposal(
    project_title: str,
    project_budget: str,
    project_description: str,
    client_name: str,
) -> str:
    """Generate proposal using template fallback."""
    budget_val = _parse_budget(project_budget)
    budget_formatted = project_budget if project_budget else "sesuai budget"

    my_name = FREELANCER_PROFILE["name"]
    my_title = FREELANCER_PROFILE["title"]
    years = FREELANCER_PROFILE["experience_years"]
    portfolio = FREELANCER_PROFILE["portfolio"]
    email = FREELANCER_PROFILE["email"]

    # Simple experience paragraph
    experience = (
        f"Saya telah berpengalaman {years} tahun dalam pengembangan web dan backend, "
        f"dengan fokus pada teknologi Java, Golang, dan framework modern. "
        f"Saya telah berhasil menyelesaikan berbagai project serupa dengan kepuasan client."
    )

    # Simple approach paragraph
    approach = (
        "Saya akan mengerjakan project ini dengan langkah-langkah terstruktur: "
        "1) Analisis kebutuhan, 2) Desain arsitektur, 3) Development dengan code quality tinggi, "
        "4) Testing menyeluruh, 5) Deployment dan maintenance. "
        "Saya berkomitmen memberikan hasil terbaik dalam timeline yang disepakati."
    )

    # Estimate timeline based on budget
    if budget_val and budget_val > 5000000:
        timeline = "2-4 minggu"
    elif budget_val and budget_val > 1000000:
        timeline = "1-2 minggu"
    else:
        timeline = "3-7 hari"

    proposal = f"""Kepada Yth. {client_name},

Dengan hormat,

Perkenalkan saya {my_name}, seorang {my_title} dengan pengalaman {years} tahun.

Setelah membaca deskripsi project "{project_title}", saya sangat tertarik untuk mengerjakan project ini.

{experience}

{approach}

Estimasi timeline: {timeline}
Budget yang saya tawarkan: {budget_formatted}

Berikut portfolio saya: {portfolio}

Saya siap mendiskusikan lebih lanjut mengenai project ini.

Terima kasih,
{my_name}
Contact: {email}"""

    return proposal


# ── LLM Proposal Generation ───────────────────────────────────────────────────

async def _call_openrouter_llm(prompt: str, timeout: int = 30) -> Optional[str]:
    """Call OpenRouter LLM to generate proposal."""
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.startswith("***"):
        logger.warning("OpenRouter API key not configured")
        return None

    import urllib.request
    import urllib.error

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://gig-notifier.bot",
        "X-Title": "Gig Notifier Proposal Generator",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.7,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_API_URL,
        data=data,
        headers=headers,
        method="POST"
    )

    try:
        loop = asyncio.get_event_loop()
        with loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=timeout)) as resp:
            result = json.loads(resp.read().decode())
            
        if result.get("choices") and len(result["choices"]) > 0:
            content = result["choices"][0].get("message", {}).get("content", "")
            # Clean up any thinking tags if present
            content = re.sub(r"<\|.*?\|>", "", content, flags=re.DOTALL).strip()
            return content
        return None
        
    except asyncio.TimeoutError:
        logger.error("OpenRouter API timeout")
        return None
    except urllib.error.HTTPError as e:
        logger.error(f"OpenRouter HTTP error: {e.code} - {e.reason}")
        return None
    except Exception as e:
        logger.error(f"OpenRouter API error: {e}")
        return None


def _build_proposal_prompt(
    project_title: str,
    project_budget: str,
    project_description: str,
    client_name: str,
) -> str:
    """Build the LLM prompt for proposal generation."""
    return f"""Buatkan proposal freelance dalam Bahasa Indonesia untuk project:
- Judul: {project_title}
- Budget: {project_budget}
- Deskripsi: {project_description}
- Client: {client_name}

Profil freelancer:
- Nama: {FREELANCER_PROFILE["name"]}
- Title: {FREELANCER_PROFILE["title"]}
- Skills: {FREELANCER_PROFILE["skills"]}
- Experience: {FREELANCER_PROFILE["experience_years"]}+ tahun
- Portofolio: {FREELANCER_PROFILE["portfolio"]}

Format: formal, profesional, dalam Bahasa Indonesia
Panjang: 150-250 kata

Proposal:"""


# ── Main Generator Function ───────────────────────────────────────────────────

async def generate_proposal(
    project_title: str,
    project_budget: str,
    project_description: str,
    client_name: str,
    project_url: str = "",
    use_cache: bool = True,
) -> tuple[str, bool]:
    """Generate a freelance proposal.
    
    Returns (proposal_text, was_cached).
    If LLM fails, falls back to template generation.
    """
    # Create cache key from project title + budget
    cache_key = f"{project_title[:50]}_{project_budget[:20]}".replace(" ", "_")
    
    # Check cache first
    if use_cache:
        cached = get_cached_proposal(cache_key)
        if cached:
            return cached, True
    
    # Try LLM generation first
    prompt = _build_proposal_prompt(
        project_title,
        project_budget,
        project_description,
        client_name,
    )
    
    llm_proposal = await _call_openrouter_llm(prompt)
    
    if llm_proposal and len(llm_proposal) > 50:
        # Cache the successful LLM generation
        cache_proposal(cache_key, llm_proposal)
        return llm_proposal, False
    
    # Fall back to template-based generation
    logger.info("LLM generation failed, using template fallback")
    template_proposal = _generate_template_proposal(
        project_title,
        project_budget,
        project_description,
        client_name,
    )
    
    # Cache the template proposal too
    cache_proposal(cache_key, template_proposal)
    return template_proposal, False


# ── Project URL Parser ─────────────────────────────────────────────────────────

def extract_project_info_from_url(url: str) -> dict:
    """Extract project info from URL.
    
    Supports:
    - projects.co.id/projects/xxx
    - fastwork.id/...
    - sribu.com/...
    """
    info = {
        "source": None,
        "project_id": None,
        "url": url,
    }
    
    if "projects.co.id" in url:
        info["source"] = "projects"
        # Extract ID from URL like /projects/12345 or /public/browse_projects/show/12345
        match = re.search(r'/projects?/(\d+)', url)
        if match:
            info["project_id"] = match.group(1)
    elif "fastwork.id" in url:
        info["source"] = "fastwork"
        # Fastwork URLs typically have job ID in path
        match = re.search(r'/job/(\w+)', url)
        if match:
            info["project_id"] = match.group(1)
    elif "sribu.com" in url:
        info["source"] = "sribu"
        # Sribu contest URLs
        match = re.search(r'/contests?/(\w+)', url)
        if match:
            info["project_id"] = match.group(1)
    
    return info


# ── Proposal Formatter ────────────────────────────────────────────────────────

def format_proposal_for_display(proposal: str, project_title: str, source: str) -> tuple[str, dict]:
    """Format proposal with header for display to user.
    
    Returns (display_text, reply_markup).
    """
    text = f"""📝 <b>Proposal Draft</b>

<b>Project:</b> {project_title}
<b>Sumber:</b> {source}

──────────────────────────────────

{proposal}

──────────────────────────────────

Gunakan tombol di bawah untuk action."""

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📤 Kirim ke Client", "callback_data": f"proposal:send:{source}"},
                {"text": "✏️ Edit Proposal", "callback_data": f"proposal:edit:{source}"},
            ],
            [
                {"text": "❌ Batalkan", "callback_data": "proposal:cancel"},
            ]
        ]
    }
    
    return text, keyboard


if __name__ == "__main__":
    # Test proposal generation
    async def test():
        proposal, cached = await generate_proposal(
            project_title="Website Toko Online dengan React dan Node.js",
            project_budget="Rp 5.000.000 - 10.000.000",
            project_description="Dibutuhkan developer untuk membuat website e-commerce lengkap dengan payment gateway dan admin panel.",
            client_name="Budi Santoso",
        )
        print(f"Cached: {cached}")
        print("=" * 60)
        print(proposal)
    
    asyncio.run(test())