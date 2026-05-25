"""
Skor Kecocokan 🎯
Score project-to-freelancer match based on:
- Skill overlap (berapa % requirement yg lo kuasain)
- Budget compatibility (cocok sama rate lo gak)
- Competition level (rame apa sepi bidder)
- Composite win probability

Pure heuristics from project data + user profile. Score 0-100.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ================================================================
# Skill extraction from project text
# ================================================================

# Quick skill keywords (simplified — skill_radar has the full CANONICAL_MAP)
SKILL_KEYWORDS = [
    "laravel", "react", "vue", "angular", "node", "express", "next",
    "php", "python", "django", "flask", "fastapi", "golang", "java",
    "spring", "flutter", "react native", "kotlin", "swift",
    "docker", "kubernetes", "aws", "gcp", "azure",
    "mysql", "postgresql", "mongodb", "redis", "firebase",
    "tailwind", "bootstrap", "sass", "css", "html",
    "typescript", "javascript", "jquery",
    "rest api", "graphql", "microservices",
    "figma", "git", "wordpress", "shopify",
    "blockchain", "solidity", "rust", "c#", ".net",
    "ai", "machine learning", "nlp", "langchain",
]


def extract_project_skills(title: str, description: str = "") -> set[str]:
    """Extract skill keywords from project text."""
    text = f"{title} {description}".lower()
    found = set()
    for skill in SKILL_KEYWORDS:
        if re.search(r'\b' + re.escape(skill) + r'\b', text):
            found.add(skill)
    return found


def calculate_skill_match(project_skills: set[str], freelancer_skills: list[str]) -> float:
    """Calculate % of project skills that freelancer has. Returns 0-100."""
    if not project_skills:
        return 50.0  # Neutral if project doesn't specify skills

    freelancer_lower = {s.lower() for s in freelancer_skills}
    matched = project_skills & freelancer_lower
    return len(matched) / len(project_skills) * 100


def calculate_budget_fit(project_budget: Optional[float], min_rate: float = 1_000_000,
                          max_rate: float = 10_000_000) -> float:
    """Score budget fit. Returns 0-100. 100 = perfect fit."""
    if project_budget is None:
        return 50.0

    if project_budget < min_rate * 0.5:
        return max(0, project_budget / min_rate * 50)  # Too cheap
    elif project_budget > max_rate * 2:
        return max(0, 100 - (project_budget - max_rate * 2) / (max_rate * 2) * 50)  # Too expensive
    elif min_rate <= project_budget <= max_rate:
        return 90 + (project_budget - min_rate) / (max_rate - min_rate + 1) * 10  # Sweet spot
    elif project_budget < min_rate:
        return 50 + (project_budget - min_rate * 0.5) / (min_rate * 0.5) * 40
    else:  # > max_rate but < 2*max_rate
        return 50 + (2 * max_rate - project_budget) / (max_rate) * 40


def estimate_competition(bid_count: int) -> float:
    """Estimate competition level. Returns 0-100 (lower = less competition = better)."""
    if bid_count == 0:
        return 20.0  # Very low competition
    elif bid_count <= 3:
        return 30.0
    elif bid_count <= 10:
        return 40 + bid_count * 2
    elif bid_count <= 30:
        return 60 + bid_count
    else:
        return 90.0  # Saturated


class MatchScore:
    """Composite match score for a project."""

    def __init__(self, skill_match: float, budget_fit: float,
                  competition: float, description_quality: float = 50.0):
        # Weighted composite: skills 40%, budget 25%, competition 20%, description 15%
        self.skill_match = skill_match
        self.budget_fit = budget_fit
        self.competition = competition  # Lower is better
        self.desc_quality = description_quality

        competition_score = 100 - competition  # Invert: less competition = better score
        self.composite = round(
            skill_match * 0.40 +
            budget_fit * 0.25 +
            competition_score * 0.20 +
            description_quality * 0.15
        )

    @property
    def emoji(self) -> str:
        if self.composite >= 80:
            return "🔥"
        elif self.composite >= 60:
            return "✅"
        elif self.composite >= 40:
            return "🙂"
        elif self.composite >= 20:
            return "🤔"
        else:
            return "👎"

    @property
    def label(self) -> str:
        if self.composite >= 80:
            return "Perfect Match"
        elif self.composite >= 60:
            return "Good Match"
        elif self.composite >= 40:
            return "Okay"
        elif self.composite >= 20:
            return "Meh"
        else:
            return "Skip"

    @property
    def verdict(self) -> str:
        if self.composite >= 80:
            return "🎯 Opportunity EMAS — lo cocok banget. Bid sekarang!"
        elif self.composite >= 60:
            return "✅ Lumayan — peluang bagus, siapin proposal."
        elif self.composite >= 40:
            return "🙂 Bisa dicoba — tapi cek dulu kompetitornya."
        else:
            return "👎 Mending skip — kecuali lo butuh portfolio."


def score_project(
    title: str,
    description: str = "",
    budget: Optional[float] = None,
    bid_count: int = 0,
    freelancer_skills: Optional[list[str]] = None,
    min_rate: float = 1_000_000,
    max_rate: float = 10_000_000,
) -> MatchScore:
    """Calculate match score for a project against freelancer profile."""

    if not freelancer_skills:
        freelancer_skills = ["laravel", "php", "javascript", "mysql", "bootstrap", "html", "css"]

    project_skills = extract_project_skills(title, description)
    skill_match = calculate_skill_match(project_skills, freelancer_skills)
    budget_fit = calculate_budget_fit(budget, min_rate, max_rate)
    competition = estimate_competition(bid_count)

    # Description quality bonus
    desc_quality = 100.0 if len(description) > 100 else len(description) if description else 20.0

    return MatchScore(skill_match, budget_fit, competition, desc_quality)


def format_match_badge(score: MatchScore) -> str:
    """Compact one-line badge for project list cards."""
    return f"{score.emoji} <b>Match:</b> {score.composite}% — {score.label}"


def format_match_detail(score: MatchScore, project_title: str) -> str:
    """Detailed match breakdown for project detail view."""
    return (
        f"🎯 <b>Skor Kecocokan — {project_title[:50]}</b>\n"
        f"\n"
        f"{score.emoji} <b>Composite:</b> {score.composite}/100 — {score.label}\n"
        f"\n"
        f"🧠 <b>Skill Match:</b> {score.skill_match:.0f}%\n"
        f"💰 <b>Budget Fit:</b> {score.budget_fit:.0f}%\n"
        f"👥 <b>Competition:</b> {score.competition:.0f}% (makin rendah makin bagus)\n"
        f"📝 <b>Desc Quality:</b> {score.desc_quality:.0f}%\n"
        f"\n"
        f"📋 <b>Verdict:</b> {score.verdict}"
    )


# ================================================================
# Self-test
# ================================================================

if __name__ == "__main__":
    # Test cases
    test_cases = [
        {
            "title": "Fullstack Developer Laravel + React",
            "description": "Butuh developer untuk sistem manajemen inventory dengan Laravel, React, MySQL. "
                          "Integrasi Docker, CI/CD, dan testing.",
            "budget": 8_000_000,
            "bid_count": 5,
            "skills": ["laravel", "php", "javascript", "react", "mysql", "docker"],
        },
        {
            "title": "UI/UX Designer untuk Mobile App",
            "description": "Desain UI mobile app fintech dengan Figma.",
            "budget": 2_000_000,
            "bid_count": 25,
            "skills": ["laravel", "php", "javascript"],
        },
        {
            "title": "Blockchain Developer — DeFi Project",
            "description": "Butuh Solidity developer untuk smart contract.",
            "budget": 30_000_000,
            "bid_count": 1,
            "skills": ["laravel", "php", "javascript", "mysql"],
        },
    ]

    print("=" * 60)
    print("MATCH SCORER — UNIT TESTS")
    print("=" * 60)

    for i, tc in enumerate(test_cases, 1):
        score = score_project(
            title=tc["title"],
            description=tc["description"],
            budget=tc["budget"],
            bid_count=tc["bid_count"],
            freelancer_skills=tc["skills"],
        )
        print(f"\n--- Test {i}: {tc['title'][:50]} ---")
        print(format_match_detail(score, tc["title"]))
        print(f"\nBadge: {format_match_badge(score)}")
