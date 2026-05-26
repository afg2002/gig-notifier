"""
Quick Pitch Generator 💬
Generate 3-4 kalimat pitch singkat dalam Bahasa Indonesia.
Cocok untuk fast response ke client — gak perlu proposal panjang.

Uses LLM with user profile context.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_pitch_prompt(
    project_title: str,
    project_description: str = "",
    project_budget: str = "",
    freelancer_name: str = "Saya",
    freelancer_title: str = "Freelancer",
    freelancer_skills: Optional[list[str]] = None,
    years_exp: int = 3,
) -> str:
    """Build a prompt for quick pitch generation."""

    skills_str = ", ".join(freelancer_skills[:8]) if freelancer_skills else "web development"
    
    # Find matching skills from project
    matching = []
    desc_lower = project_description.lower()
    if freelancer_skills:
        for skill in freelancer_skills:
            if skill.lower() in desc_lower:
                matching.append(skill)
    
    skill_match_note = ""
    if matching:
        skill_match_note = f"Sebutkan skill {', '.join(matching[:4])} yang relevan dengan project."
    
    prompt = f"""Kamu adalah {freelancer_name}, seorang {freelancer_title} dengan pengalaman {years_exp} tahun.
Skills: {skills_str}.

Client posting project ini:
- Judul: {project_title}
- Budget: {project_budget}
- Deskripsi: {project_description[:400]}

Buat PITCH SINGKAT dalam Bahasa Indonesia (3-4 kalimat aja, MAKSIMAL 100 kata):

Format:
1. Kalimat pembuka — tunjukkan kamu paham projectnya
2. Kenapa kamu cocok — {skill_match_note if skill_match_note else 'singgung pengalaman relevan'}
3. Call to action — ajak diskusi/meeting

ATURAN PENTING:
- JANGAN pakai template kaku. Natural, conversational.
- JANGAN sebut harga spesifik (kecuali diminta).
- GUNAKAN Bahasa Indonesia santai tapi profesional.
- MAKSIMAL 4 kalimat. Singkat, padat, nendang.
- TULIS langsung pitch-nya, tanpa preamble, tanpa "ini pitch-nya".

Pitch:"""

    return prompt


def build_pitch_from_profile(
    project_title: str,
    project_description: str = "",
    project_budget: str = "",
    profile: Optional[dict] = None,
) -> str:
    """Build pitch prompt using user profile data."""
    
    name = "Afghan Eka Pangestu"
    title = "Full-Stack Developer"
    skills = ["Laravel", "PHP", "JavaScript", "React", "MySQL", "Bootstrap"]
    years = 3
    
    if profile:
        name = profile.get("name") or name
        title = profile.get("title") or title
        if profile.get("skills"):
            skills = [s.strip() for s in profile["skills"].split(",")]
        years = profile.get("experience_years", years)
    
    return build_pitch_prompt(
        project_title=project_title,
        project_description=project_description,
        project_budget=project_budget,
        freelancer_name=name,
        freelancer_title=title,
        freelancer_skills=skills,
        years_exp=years,
    )


def format_pitch_result(pitch_text: str, project_title: str) -> str:
    """Format pitch for Telegram display."""
    return (
        f"💬 <b>Quick Pitch — {project_title[:50]}</b>\n"
        f"\n"
        f"{pitch_text}\n"
        f"\n"
        f"📋 <i>Tinggal copy-paste ke chat client. Gak perlu edit.</i>"
    )


# ================================================================
# Self-test
# ================================================================

if __name__ == "__main__":
    test_project = {
        "title": "Sistem Manajemen Inventory UKM",
        "description": "Butuh aplikasi web inventory dengan fitur: barcode scanning, "
                       "multi-warehouse, reporting dashboard, role-based access. "
                       "Tech stack: Laravel + MySQL + Bootstrap.",
        "budget": "8-12 juta",
    }
    
    profile = {
        "name": "Budi Santoso",
        "title": "Full-Stack Laravel Developer",
        "skills": "Laravel, PHP, JavaScript, MySQL, Bootstrap, Docker, REST API",
        "experience_years": 4,
    }
    
    prompt = build_pitch_from_profile(
        project_title=test_project["title"],
        project_description=test_project["description"],
        project_budget=test_project["budget"],
        profile=profile,
    )
    
    print("=" * 60)
    print("QUICK PITCH — PROMPT GENERATION")
    print("=" * 60)
    print(f"\nPrompt length: {len(prompt)} chars")
    print(f"\n{prompt[:500]}...")
    
    print("\n" + "=" * 60)
    print("Without profile (defaults):")
    default_prompt = build_pitch_prompt(**test_project)
    print(f"\n{default_prompt[:400]}...")
