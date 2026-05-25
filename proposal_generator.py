"""
AI Proposal Generator for gig-notifier bot.
Generates professional freelance proposals using LLM (OpenRouter) or template fallback.
Supports per-user CV storage for personalized proposals.
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
USER_CV_FILE = os.path.join(DATA_DIR, "user_cvs.json")  # {chat_id: {text, uploaded_at}}
USER_PROFILE_FILE = os.path.join(DATA_DIR, "user_profiles.json")  # {chat_id: {name, title, email, github, skills, experience_years, portfolio, linkedin, phone, set_at}}
MAX_PROPOSALS_PER_DAY = 5

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL",
    "google/gemma-4-26b-a4b-it:free"
)

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

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
    if os.path.exists(PROPOSAL_RATE_FILE):
        try:
            with open(PROPOSAL_RATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_rate_limits(data: dict):
    with open(PROPOSAL_RATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _check_rate_limit(chat_id: str) -> tuple[bool, int]:
    today = date.today().isoformat()
    limits = _load_rate_limits()
    user_data = limits.get(chat_id, {"date": None, "count": 0})
    if user_data.get("date") != today:
        user_data = {"date": today, "count": 0}
    remaining = MAX_PROPOSALS_PER_DAY - user_data["count"]
    return remaining > 0, max(0, remaining)


def _increment_proposal_count(chat_id: str):
    today = date.today().isoformat()
    limits = _load_rate_limits()
    user_data = limits.get(chat_id, {"date": None, "count": 0})
    if user_data.get("date") != today:
        user_data = {"date": today, "count": 0}
    user_data["count"] += 1
    limits[chat_id] = user_data
    _save_rate_limits(limits)


# ── User CV Storage ────────────────────────────────────────────────────────────

def _load_user_cvs() -> dict:
    if os.path.exists(USER_CV_FILE):
        try:
            with open(USER_CV_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_user_cvs(data: dict):
    with open(USER_CV_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user_cv_text(chat_id: str) -> Optional[str]:
    """Get stored CV text for a user."""
    cvs = _load_user_cvs()
    entry = cvs.get(str(chat_id))
    if entry and entry.get("text"):
        return entry["text"]
    return None


def save_user_cv(chat_id: str, cv_text: str) -> str:
    """Save CV text for a user. Returns the saved text."""
    cvs = _load_user_cvs()
    cvs[str(chat_id)] = {
        "text": cv_text,
        "uploaded_at": datetime.now().isoformat(),
    }
    _save_user_cvs(cvs)
    return cv_text


# ── User Profile Storage ───────────────────────────────────────────────────────

def _load_user_profiles() -> dict:
    if os.path.exists(USER_PROFILE_FILE):
        try:
            with open(USER_PROFILE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_user_profiles(data: dict):
    with open(USER_PROFILE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user_profile(chat_id: str) -> Optional[dict]:
    """Get stored profile for a user. Returns dict or None if not set."""
    profiles = _load_user_profiles()
    entry = profiles.get(str(chat_id))
    if entry and entry.get("name"):
        return entry
    return None


def save_user_profile(chat_id: str, profile: dict) -> dict:
    """Save profile for a user. Returns the saved profile."""
    profiles = _load_user_profiles()
    profiles[str(chat_id)] = {
        "name": profile.get("name", ""),
        "title": profile.get("title", ""),
        "email": profile.get("email", ""),
        "github": profile.get("github", ""),
        "skills": profile.get("skills", ""),
        "experience_years": profile.get("experience_years", 0),
        "portfolio": profile.get("portfolio", ""),
        "linkedin": profile.get("linkedin", ""),
        "phone": profile.get("phone", ""),
        "set_at": datetime.now().isoformat(),
    }
    _save_user_profiles(profiles)
    return profiles[str(chat_id)]


def delete_user_profile(chat_id: str):
    """Delete profile for a user."""
    profiles = _load_user_profiles()
    if str(chat_id) in profiles:
        del profiles[str(chat_id)]
        _save_user_profiles(profiles)


def extract_text_from_pdf(pdf_path: str) -> Optional[str]:
    """Extract text from a PDF file using PyPDF2.
    
    Returns the extracted text or None if extraction fails.
    """
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        logger.warning("PyPDF2 not available for PDF extraction")
        return None

    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        full_text = "\n".join(text_parts)
        # Clean up whitespace
        full_text = re.sub(r"\s+", " ", full_text).strip()
        return full_text if full_text else None
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return None


# ── Proposal Cache ────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if os.path.exists(PROPOSAL_CACHE_FILE):
        try:
            with open(PROPOSAL_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_cache(cache: dict):
    with open(PROPOSAL_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_cached_proposal(project_key: str) -> Optional[str]:
    cache = _load_cache()
    entry = cache.get(project_key)
    if entry:
        cached_time = datetime.fromisoformat(entry["cached_at"])
        if (datetime.now() - cached_time).total_seconds() < 86400:
            return entry["proposal"]
    return None


def cache_proposal(project_key: str, proposal: str):
    cache = _load_cache()
    cache[project_key] = {
        "proposal": proposal,
        "cached_at": datetime.now().isoformat(),
    }
    _save_cache(cache)


# ── Budget Parser ─────────────────────────────────────────────────────────────

def _parse_budget(budget_str: str) -> Optional[float]:
    if not budget_str or budget_str == "-":
        return None
    cleaned = budget_str.replace("Rp", "").replace(".", "").replace("'", "").strip()
    nums = re.findall(r"\d+", cleaned)
    if not nums:
        return None
    try:
        val = float(nums[0])
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
    user_profile: Optional[dict] = None,
) -> str:
    budget_val = _parse_budget(project_budget)
    budget_formatted = project_budget if project_budget else "sesuai budget"

    # Use user profile if set, otherwise fall back to default
    profile = user_profile if user_profile else FREELANCER_PROFILE
    
    my_name = profile["name"]
    my_title = profile["title"]
    years = profile["experience_years"]
    portfolio = profile["portfolio"]
    email = profile["email"]

    experience = (
        f"Saya telah berpengalaman {years} tahun dalam pengembangan web dan backend, "
        f"dengan fokus pada teknologi Java, Golang, dan framework modern. "
        f"Saya telah berhasil menyelesaikan berbagai project serupa dengan kepuasan client."
    )

    approach = (
        "Saya akan mengerjakan project ini dengan langkah-langkah terstruktur: "
        "1) Analisis kebutuhan, 2) Desain arsitektur, 3) Development dengan code quality tinggi, "
        "4) Testing menyeluruh, 5) Deployment dan maintenance. "
        "Saya berkomitmen memberikan hasil terbaik dalam timeline yang disepakati."
    )

    if budget_val and budget_val > 5000000:
        timeline = "2-4 minggu"
    elif budget_val and budget_val > 1000000:
        timeline = "1-2 minggu"
    else:
        timeline = "3-7 hari"

    return f"""Kepada Yth. {client_name},

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


# ── LLM Proposal Generation ───────────────────────────────────────────────────

async def _call_openrouter_llm(prompt: str, timeout: int = 45) -> Optional[str]:
    """Call OpenRouter LLM to generate proposal."""
    if not OPENROUTER_API_KEY:
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
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200,
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
        resp = await loop.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=timeout)
        )
        result = json.loads(resp.read().decode())

        if result.get("choices") and len(result["choices"]) > 0:
            content = result["choices"][0].get("message", {}).get("content", "")
            # Clean up any thinking tags if present
            content = re.sub(r"<\|.*?\|>", "", content, flags=re.DOTALL).strip()
            return content if content else None
        return None

    except asyncio.TimeoutError:
        logger.error("OpenRouter API timeout")
        return None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()
            logger.error(f"OpenRouter HTTP {e.code}: {body[:200]}")
        except Exception:
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
    cv_text: Optional[str] = None,
    user_profile: Optional[dict] = None,
) -> str:
    """Build the LLM prompt for proposal generation.
    
    Uses user_profile if provided, otherwise falls back to FREELANCER_PROFILE.
    cv_text provides supplemental info from uploaded CV.
    """
    # Use user profile if set, otherwise fall back to default
    profile = user_profile if user_profile else FREELANCER_PROFILE
    
    cv_section = ""
    if cv_text and len(cv_text) > 20:
        # Include first 1500 chars of CV for context
        cv_snippet = cv_text[:1500]
        cv_section = f"\nCV / Background Freelancer:\n{cv_snippet}\n\n( Gunakan info dari CV di atas untuk memperkuat proposal )"

    return f"""Buatkan proposal freelance profesional dalam Bahasa Indonesia untuk project ini:

PROJECT:
- Judul: {project_title}
- Budget: {project_budget}
- Deskripsi: {project_description}
- Client: {client_name}

FREELANCER:
- Nama: {profile['name']}
- Title: {profile['title']}
- Skills: {profile['skills']}
- Experience: {profile['experience_years']}+ tahun
- Portofolio: {profile['portfolio']}{cv_section}

FORMAT:
- Bahasa: Indonesia formal profesional
- Panjang: 150-250 kata
- Include: perkenalan, highlight relevan skill, pendekatan kerja, timeline estimasi, closing dengan kontak
- Jangan gunakan tanda pemisah seperti ----- atau ###

Tulis langsung proposal-nya tanpa preamble:"""


# ── Main Generator Function ───────────────────────────────────────────────────

async def generate_proposal(
    project_title: str,
    project_budget: str,
    project_description: str,
    client_name: str,
    project_url: str = "",
    cv_text: Optional[str] = None,
    use_cache: bool = True,
    user_profile: Optional[dict] = None,
    chat_id: str = "",
) -> tuple[str, bool]:
    """Generate a freelance proposal.

    Returns (proposal_text, was_cached).
    Uses LLM if available, falls back to template.
    
    user_profile: Per-user profile dict (from get_user_profile).
                  If not provided, uses hardcoded FREELANCER_PROFILE.
    chat_id: User's chat ID, included in cache key for multi-user isolation.
    """
    # Include chat_id in cache key for multi-user support
    cache_key = f"{chat_id}_{project_title[:50]}_{project_budget[:20]}".replace(" ", "_")

    if use_cache:
        cached = get_cached_proposal(cache_key)
        if cached:
            return cached, True

    # Try LLM generation
    prompt = _build_proposal_prompt(
        project_title,
        project_budget,
        project_description,
        client_name,
        cv_text,
        user_profile,
    )

    llm_proposal = await _call_openrouter_llm(prompt)

    if llm_proposal and len(llm_proposal) > 50:
        cache_proposal(cache_key, llm_proposal)
        return llm_proposal, False

    # Fall back to template-based generation
    logger.info("LLM generation failed, using template fallback")
    template_proposal = _generate_template_proposal(
        project_title,
        project_budget,
        project_description,
        client_name,
        user_profile,
    )
    cache_proposal(cache_key, template_proposal)
    return template_proposal, False


# ── Project URL Parser ─────────────────────────────────────────────────────────

def extract_project_info_from_url(url: str) -> dict:
    """Extract project info from URL.

    Supports:
    - projects.co.id: /projects/ID, /view/ID/title, /show/ID
    - fastwork.id: /job/ID or direct job URLs
    - sribu.com: /contests/ID or direct contest URLs
    """
    url = url.strip()
    info = {
        "source": None,
        "project_id": None,
        "url": url,
    }

    if "projects.co.id" in url:
        info["source"] = "projects.co.id"
        # projects.co.id uses 6-char hex IDs in various URL formats
        for pattern in [
            r'/projects?/([a-f0-9]{6})',
            r'/view/([a-f0-9]{6})/',
            r'/show/([a-f0-9]{6})',
        ]:
            m = re.search(pattern, url)
            if m:
                info["project_id"] = m.group(1)
                break

    elif "fastwork.id" in url:
        info["source"] = "fastwork.id"
        m = re.search(r'/job/([a-zA-Z0-9_-]+)', url)
        if m:
            info["project_id"] = m.group(1)

    elif "sribu.com" in url:
        info["source"] = "sribu.com"
        m = re.search(r'/contests?/([a-zA-Z0-9_-]+)', url)
        if m:
            info["project_id"] = m.group(1)

    return info


# ── Proposal Display Formatter (copyable, no buttons) ─────────────────────────

def format_proposal_for_display(
    proposal: str,
    project_title: str,
    source: str,
) -> str:
    """Format proposal for display. Returns clean copyable text with header.
    
    No buttons, no separators, clean copy-paste friendly format.
    """
    return f"""📝 Proposal untuk: {project_title}
🌐 Sumber: {source}

{proposal}

💡 Salin teks di atas dan kirimkan ke client."""


if __name__ == "__main__":
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
