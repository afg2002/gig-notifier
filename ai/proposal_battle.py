"""
Duel Gaya Proposal ⚔️
Generate 3 proposal variants with different personas:
- Si Agresif: harga miring, timeline ngebut
- Si Premium: kualitas enterprise, harga premium
- Si Teknis: detail arsitektur, tech stack solid

Extends proposal_generator.py with persona-based prompt engineering.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# Persona Definitions
# ============================================================

PERSONAS = {
    "agresif": {
        "name": "🔥 Si Agresif",
        "emoji": "🔥",
        "style": "Harga kompetitif, timeline agresif, closing cepat",
        "tone": "Confident, direct, urgency-driven",
        "pricing_strategy": "Below market average, emphasize speed",
        "timeline_multiplier": 0.7,  # 30% faster than normal
        "pricing_multiplier": 0.8,  # 20% cheaper
        "tagline": "Selesai cepat, harga bersaing, hasil maksimal!",
    },
    "premium": {
        "name": "💎 Si Premium",
        "emoji": "💎",
        "style": "Kualitas enterprise, value-based pricing, long-term partnership",
        "tone": "Professional, authoritative, quality-first",
        "pricing_strategy": "Above market, emphasize ROI & quality",
        "timeline_multiplier": 1.2,  # Slightly longer
        "pricing_multiplier": 1.5,  # 50% more expensive
        "tagline": "Investasi kualitas, hasil premium, partner jangka panjang.",
    },
    "teknis": {
        "name": "🤓 Si Teknis",
        "emoji": "🤓",
        "style": "Detail teknis, arsitektur jelas, stack modern",
        "tone": "Analytical, thorough, tech-nerd credible",
        "pricing_strategy": "Market rate, justify with technical depth",
        "timeline_multiplier": 1.0,
        "pricing_multiplier": 1.0,
        "tagline": "Arsitektur solid, kode bersih, skalabel sejak hari pertama.",
    },
}


def build_persona_prompt(
    persona_key: str,
    project_title: str,
    project_description: str = "",
    project_budget: str = "",
    freelancer_name: str = "Afghan Eka Pangestu",
    freelancer_title: str = "Full-Stack Developer",
    cv_skills: Optional[list[str]] = None,
    years_exp: int = 3,
) -> str:
    """
    Build a prompt for generating a proposal with a specific persona.
    """

    persona = PERSONAS[persona_key]
    skills_str = ", ".join(cv_skills) if cv_skills else "Laravel, PHP, JavaScript, React, MySQL"

    prompt = f"""Kamu adalah freelance {freelancer_title} dengan persona "{persona['name']}".

*Gaya:* {persona['style']}
*Nada:* {persona['tone']}
*Strategi Harga:* {persona['pricing_strategy']}

*Profile Kamu:*
- Nama: {freelancer_name}
- Title: {freelancer_title}
- Pengalaman: {years_exp} tahun
- Skills: {skills_str}

*Project yang Dilamar:*
- Judul: {project_title}
- Budget: {project_budget}
- Deskripsi: {project_description}

---

Buat proposal freelance profesional dalam Bahasa Indonesia dengan format berikut:

📋 *PROPOSAL FREELANCE*

**Pendahuluan** (2-3 kalimat pembuka yang show interest & relevansi)
**Pemahaman Project** (parafrase kebutuhan client, tunjukkan kamu paham)
**Approach / Metodologi** (4-5 poin teknis — sesuaikan dengan persona)
**Timeline** (estimasi realistis dalam minggu)
**Budget** (sebutkan angka atau range)
**Penutup** (call to action, ajak diskusi/meeting)

*Persona rules:*
- Kalau "{persona['name']}", maka: {persona['tone']}
- Budget harus sesuai strategi: {persona['pricing_strategy']}
- Tagline: {persona['tagline']}

Tulis langsung proposalnya, tanpa preamble, tanpa "tentu, ini proposalnya".
Gunakan Bahasa Indonesia profesional yang natural, bukan template kaku.
"""
    return prompt


def generate_all_personas(
    project_title: str,
    project_description: str = "",
    project_budget: str = "",
    cv_skills: Optional[list[str]] = None,
    **kwargs,
) -> dict[str, dict]:
    """
    Generate prompts for all 3 personas.
    Returns dict of persona_key -> {prompt, persona_info}.
    In production, you feed these prompts to an LLM one by one.

    Usage in bot:
        battle = generate_all_personas(title, desc, budget, skills)
        # Then for each persona:
        for key, data in battle.items():
            proposal_text = await call_llm(data["prompt"])
            # Store / display
    """

    results = {}
    for key in PERSONAS:
        prompt = build_persona_prompt(
            persona_key=key,
            project_title=project_title,
            project_description=project_description,
            project_budget=project_budget,
            cv_skills=cv_skills,
            **kwargs,
        )
        results[key] = {
            "persona": PERSONAS[key],
            "prompt": prompt,
        }

    return results


def format_battle_intro(project_title: str) -> str:
    """Generate the battle intro message."""
    return (
        f"⚔️ *PROPOSAL BATTLE ROYALE* ⚔️\n"
        f"\n"
        f"📋 Project: {project_title[:60]}\n"
        f"\n"
        f"3 persona akan bertarung:\n"
        f"🔥 *Si Agresif* — cepat, murah, closep\n"
        f"💎 *Si Premium* — quality, confident, premium\n"
        f"🤓 *Si Teknis* — detail, arsitektur, solid\n"
        f"\n"
        f"_Generate proposal..._"
    )


def format_persona_result(persona_key: str, proposal_text: str) -> str:
    """Format a single persona proposal for Telegram display."""
    persona = PERSONAS[persona_key]
    return (
        f"{persona['emoji']} *{persona['name']}*\n"
        f"_{persona['style']}_\n"
        f"\n"
        f"{proposal_text}"
    )


def compare_personas() -> str:
    """Return a comparison table of all personas."""
    lines = [
        "⚔️ *Persona Comparison*",
        "",
        "| Persona | Harga | Timeline | Gaya |",
        "|---------|-------|----------|------|",
    ]
    for key, p in PERSONAS.items():
        price = (
            "⬇️ Murah" if p["pricing_multiplier"] < 1 else
            "⬆️ Mahal" if p["pricing_multiplier"] > 1 else
            "➡️ Normal"
        )
        timeline = (
            "⚡ Cepat" if p["timeline_multiplier"] < 1 else
            "🐢 Santai" if p["timeline_multiplier"] > 1 else
            "➡️ Normal"
        )
        lines.append(f"| {p['emoji']} {p['name']} | {price} | {timeline} | {p['style'].split(',')[0]} |")

    lines.append("")
    lines.append("💡 *Tips:* Gunakan Si Agresif untuk project budget rendah, Si Premium untuk client corporate, Si Teknis untuk project kompleks.")
    return "\n".join(lines)


# ============================================================
# Integration with proposal_generator
# ============================================================

def integrate_with_existing_generator(
    project_title: str,
    project_description: str = "",
    project_budget: str = "",
    existing_generator_func=None,
    **kwargs,
) -> dict[str, str]:
    """
    Drop-in integration if proposal_generator.generate_proposal() exists.
    Returns persona_key -> proposal_text.
    """

    if existing_generator_func is None:
        # Try to import
        try:
            from proposal_generator import generate_proposal
            existing_generator_func = generate_proposal
        except ImportError:
            logger.warning("proposal_generator not available, returning prompts only")
            return {}

    battle = generate_all_personas(project_title, project_description, project_budget, **kwargs)

    results = {}
    for key, data in battle.items():
        try:
            # Use existing generator with persona-override prompt
            result = existing_generator_func(
                project_title=project_title,
                project_description=project_description,
                project_budget=project_budget,
                persona_override=data["prompt"],  # If supported
                **kwargs,
            )
            if isinstance(result, str):
                results[key] = result
            elif isinstance(result, dict):
                results[key] = result.get("proposal", str(result))
        except Exception as e:
            logger.error(f"Error generating {key} proposal: {e}")
            results[key] = f"[Error generating {key} proposal: {e}]"

    return results


# ============================================================
# Self-test
# ============================================================

if __name__ == "__main__":
    test_project = {
        "title": "Sistem Manajemen Keuangan UKM",
        "description": "Butuh aplikasi web untuk manajemen keuangan UKM dengan fitur: "
                       "pencatatan pemasukan/pengeluaran, laporan bulanan, dashboard grafik, "
                       "multi-user, export Excel/PDF.",
        "budget": "5-10 juta",
    }

    print("=" * 60)
    print("PROPOSAL BATTLE ROYALE — PROMPT GENERATION TEST")
    print("=" * 60)

    battle = generate_all_personas(
        project_title=test_project["title"],
        project_description=test_project["description"],
        project_budget=test_project["budget"],
    )

    for key, data in battle.items():
        print(f"\n--- {data['persona']['name']} ---")
        print(f"Prompt length: {len(data['prompt'])} chars")
        print(f"First 200 chars: {data['prompt'][:200]}...")

    print("\n" + "=" * 60)
    print(compare_personas())
