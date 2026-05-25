"""
Radar Skill 📡
Track trending tech keywords across all platforms, compare with user CV,
and show rising/falling skills with actionable insights.

Data sources:
- Project titles & descriptions from database
- User CV (from proposal_generator's skills extraction)
"""

import re
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# Tech Keyword Canonical Map
# ============================================================

# Maps various keyword forms to canonical names
CANONICAL_MAP = {
    # JavaScript ecosystem
    "react": "React",
    "reactjs": "React",
    "react.js": "React",
    "next": "Next.js",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "vue": "Vue.js",
    "vuejs": "Vue.js",
    "vue.js": "Vue.js",
    "nuxt": "Nuxt.js",
    "nuxtjs": "Nuxt.js",
    "angular": "Angular",
    "angularjs": "Angular",
    "svelte": "Svelte",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "express": "Express.js",
    "expressjs": "Express.js",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "jquery": "jQuery",

    # PHP ecosystem
    "laravel": "Laravel",
    "codeigniter": "CodeIgniter",
    "symfony": "Symfony",
    "wordpress": "WordPress",
    "wp": "WordPress",
    "woocommerce": "WooCommerce",
    "php": "PHP",

    # Python ecosystem
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "python": "Python",
    "pandas": "Pandas",
    "numpy": "NumPy",
    "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",

    # Mobile
    "flutter": "Flutter",
    "react native": "React Native",
    "react-native": "React Native",
    "swift": "Swift",
    "kotlin": "Kotlin",
    "android": "Android",
    "ios": "iOS",
    "ionic": "Ionic",

    # Go
    "golang": "Go",

    # Java ecosystem
    "java": "Java",
    "spring": "Spring Boot",
    "springboot": "Spring Boot",
    "spring boot": "Spring Boot",
    "hibernate": "Hibernate",
    "kotlin": "Kotlin",
    "jsp": "JSP",

    # DevOps / Cloud
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    "ci/cd": "CI/CD",
    "jenkins": "Jenkins",
    "terraform": "Terraform",
    "ansible": "Ansible",
    "nginx": "Nginx",
    "linux": "Linux",

    # Databases
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mongodb": "MongoDB",
    "mongo": "MongoDB",
    "redis": "Redis",
    "sqlite": "SQLite",
    "elasticsearch": "Elasticsearch",
    "firebase": "Firebase",
    "supabase": "Supabase",

    # API / Backend
    "rest": "REST API",
    "restful": "REST API",
    "rest api": "REST API",
    "graphql": "GraphQL",
    "grpc": "gRPC",
    "microservice": "Microservices",
    "microservices": "Microservices",
    "webhook": "Webhooks",

    # Frontend
    "tailwind": "Tailwind CSS",
    "tailwindcss": "Tailwind CSS",
    "bootstrap": "Bootstrap",
    "sass": "Sass/SCSS",
    "scss": "Sass/SCSS",
    "css": "CSS",
    "html": "HTML",
    "responsive": "Responsive Design",

    # AI / ML
    "ai": "AI/ML",
    "artificial intelligence": "AI/ML",
    "machine learning": "AI/ML",
    "ml": "AI/ML",
    "deep learning": "AI/ML",
    "nlp": "NLP",
    "chatgpt": "ChatGPT/LLM",
    "openai": "ChatGPT/LLM",
    "llm": "ChatGPT/LLM",
    "langchain": "LangChain",

    # Other
    "figma": "Figma",
    "git": "Git",
    "github": "GitHub",
    "agile": "Agile/Scrum",
    "scrum": "Agile/Scrum",
    "jira": "Jira",
    "blockchain": "Blockchain",
    "web3": "Web3",
    "solidity": "Solidity",
    "rust": "Rust",
    "c#": "C#",
    "c++": "C++",
    "dotnet": ".NET",
    ".net": ".NET",
}


def extract_skills(text: str) -> list[str]:
    """Extract canonical tech skills from text."""
    text_lower = text.lower()
    found = set()

    # Match multi-word phrases first
    for pattern, canonical in sorted(CANONICAL_MAP.items(), key=lambda x: -len(x[0])):
        # Use word boundary matching
        if re.search(r'\b' + re.escape(pattern) + r'\b', text_lower):
            found.add(canonical)

    return sorted(found)


def extract_skills_from_projects(projects: list[dict]) -> Counter:
    """Extract and count skills from a list of project dicts.
    Each dict should have 'title' and 'description' keys.
    """
    counter = Counter()
    for proj in projects:
        text = f"{proj.get('title', '')} {proj.get('description', '')}"
        skills = extract_skills(text)
        for skill in skills:
            counter[skill] += 1
    return counter


def compare_with_cv(
    market_skills: Counter,
    cv_skills: list[str],
) -> dict:
    """
    Compare market demand with user's CV skills.
    Returns categorized skill gaps.
    """
    cv_set = set(cv_skills)
    market_set = set(market_skills.keys())

    # Skills you have that are in demand
    matched = sorted(market_set & cv_set, key=lambda s: -market_skills[s])

    # Skills in demand that you DON'T have
    gaps = sorted(market_set - cv_set, key=lambda s: -market_skills[s])

    # Skills you have that have no demand
    unused = sorted(cv_set - market_set)

    # Calculate coverage score
    total_demand = sum(market_skills.values())
    matched_demand = sum(market_skills[s] for s in matched)
    coverage = (matched_demand / total_demand * 100) if total_demand > 0 else 0

    return {
        "matched": matched,
        "gaps": gaps,
        "unused": unused,
        "coverage": round(coverage, 1),
        "total_unique_skills": len(market_set),
    }


def analyze_trends(
    current_skills: Counter,
    previous_skills: Counter,
) -> list[dict]:
    """Compare current vs previous period to find trending skills."""
    all_skills = set(list(current_skills.keys()) + list(previous_skills.keys()))
    trends = []

    for skill in all_skills:
        curr = current_skills.get(skill, 0)
        prev = previous_skills.get(skill, 0)
        if curr == 0 and prev == 0:
            continue

        if prev == 0:
            change_pct = 100
        elif curr == 0:
            change_pct = -100
        else:
            change_pct = round(((curr - prev) / prev) * 100)

        trend = {
            "skill": skill,
            "current": curr,
            "previous": prev,
            "change_pct": change_pct,
            "direction": "📈" if change_pct > 10 else "📉" if change_pct < -10 else "➡️",
        }
        trends.append(trend)

    return sorted(trends, key=lambda t: -t["change_pct"])


def format_skill_radar(
    analysis: dict,
    trends: list[dict],
    top_n: int = 10,
) -> str:
    """Generate Telegram-friendly skill radar report."""

    lines = [
        "📡 *Skill Gap Radar*",
        "",
        f"🎯 *Skill Coverage:* {analysis['coverage']}%",
        f"   ({len(analysis['matched'])} matched dari {analysis['total_unique_skills']} skill di market)",
        "",
    ]

    # Rising skills
    rising = [t for t in trends if t["direction"] == "📈"][:5]
    if rising:
        lines.append("*🔥 Sedang Naik:*")
        for t in rising:
            lines.append(f"  {t['skill']} (+{t['change_pct']}% → {t['current']} project)")
        lines.append("")

    # Falling skills
    falling = [t for t in trends if t["direction"] == "📉"][:5]
    if falling:
        lines.append("*📉 Sedang Turun:*")
        for t in falling:
            lines.append(f"  {t['skill']} ({t['change_pct']}% → {t['current']} project)")
        lines.append("")

    # Top market demand
    lines.append(f"*🔥 Top {min(top_n, len(analysis['matched']))} Skill Diminati:*")
    for i, skill in enumerate(analysis["matched"][:top_n], 1):
        lines.append(f"  {i}. {skill}")
    lines.append("")

    # Gaps — what you should learn
    if analysis["gaps"]:
        top_gaps = analysis["gaps"][:5]
        lines.append("*🕳️ Skill Gap (opportunity!):*")
        for skill in top_gaps:
            lines.append(f"  • {skill}")
        lines.append("")

    # Unused skills
    if analysis["unused"]:
        lines.append(f"*💤 Skill Tidak Diminati:* {', '.join(analysis['unused'][:5])}")

    lines.append("")
    lines.append("💡 *Rekomendasi:* Pelajari skill di section 🕳️ untuk naikin coverage & competitiveness.")

    return "\n".join(lines)


def format_trend_compact(trends: list[dict], top_n: int = 10) -> str:
    """Compact trend view for quick check."""
    lines = ["📡 *Skill Trends*", ""]

    for t in trends[:top_n]:
        bar = _mini_bar(t["change_pct"])
        lines.append(
            f"{t['direction']} {t['skill']}: {t['current']} proj "
            f"({'+' if t['change_pct'] > 0 else ''}{t['change_pct']}%) {bar}"
        )

    return "\n".join(lines)


def _mini_bar(pct: float, width: int = 8) -> str:
    """Generate sparkline-style mini bar."""
    half = width // 2
    if pct > 30:
        filled = half + min(half, int(pct / 10))
    elif pct < -30:
        filled = half - min(half, int(abs(pct) / 10))
    else:
        filled = half + int(pct / 10 * half / 3)

    filled = max(0, min(width, filled))
    return f"[{'█' * filled}{'░' * (width - filled)}]"


# ============================================================
# Database integration
# ============================================================


def get_projects_for_period(db_conn, days: int, source: Optional[str] = None) -> list[dict]:
    """Get projects from the last N days."""
    try:
        import sqlite3
        cursor = db_conn.cursor()
        since = (datetime.now() - timedelta(days=days)).isoformat()[:10]

        queries = []
        params = []

        if not source or source == "projects":
            cursor.execute(
                "SELECT title, description FROM projects WHERE posted_date >= ?",
                (since,)
            )
            for row in cursor.fetchall():
                queries.append({"title": row["title"], "description": row["description"] or ""})

        if not source or source == "fastwork":
            cursor.execute(
                "SELECT title, description FROM fastwork_jobs WHERE posted_date >= ?",
                (since,)
            )
            for row in cursor.fetchall():
                queries.append({"title": row["title"], "description": row["description"] or ""})

        if not source or source == "sribu":
            cursor.execute(
                "SELECT title, description FROM sribu_contests WHERE posted_date >= ?",
                (since,)
            )
            for row in cursor.fetchall():
                queries.append({"title": row["title"], "description": row["description"] or ""})

        return queries
    except Exception as e:
        logger.error(f"Error fetching projects for skill radar: {e}")
        return []


# ============================================================
# Self-test
# ============================================================

if __name__ == "__main__":
    # Test skill extraction
    test_text = """
    Dicari Fullstack Developer Laravel + React untuk sistem manajemen.
    Butuh pengalaman Docker, Kubernetes, AWS.
    Familiar dengan CI/CD pipeline dan testing.
    Plus: pengalaman Flutter untuk mobile app.
    """
    print("=== Skill Extraction ===")
    skills = extract_skills(test_text)
    print(f"Found: {skills}")
    print()

    # Test trend analysis
    current = Counter({"React": 45, "Laravel": 30, "Go": 25, "Flutter": 20, "PHP": 10})
    previous = Counter({"React": 40, "Laravel": 35, "Go": 10, "Flutter": 5, "PHP": 20})
    trends = analyze_trends(current, previous)
    print("=== Trends ===")
    print(format_trend_compact(trends))
    print()

    # Test CV comparison
    cv = ["Laravel", "PHP", "MySQL", "Bootstrap", "jQuery", "JavaScript"]
    analysis = compare_with_cv(current, cv)
    print("=== CV Comparison ===")
    print(f"Coverage: {analysis['coverage']}%")
    print(f"Matched: {analysis['matched']}")
    print(f"Gaps: {analysis['gaps']}")
    print(f"Unused: {analysis['unused']}")
