"""
Proposal Generator - Generate professional project proposals.
Creates well-formatted proposals in Indonesian/English based on project details.
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class ProposalTemplate:
    """Template configuration for proposal generation."""
    greeting: str = "Halo,"
    opening: str = (
        "Saya tertarik dengan proyek '{title}' yang Anda posting. "
        "Saya memiliki pengalaman di bidang yang relevan dan yakin dapat "
        "menyelesaikan pekerjaan ini dengan baik."
    )
    experience: str = (
        "Beberapa pengalaman saya:\n"
        "• Mengerjakan berbagai proyek {stack}\n"
        "• Memahami best practices dan clean code\n"
        "• Komunikatif dan responsif selama pengerjaan"
    )
    approach: str = (
        "Pendekatan yang akan saya ambil:\n"
        "1. Analisis requirement dan konsultasi detail\n"
        "2. Implementasi utama dengan update berkala\n"
        "3. Testing dan revisi\n"
        "4. Deployment dan dokumentasi"
    )
    tech_stack: str = "Full-Stack Development (Laravel, Python, Go, JavaScript)"
    timeline: str = (
        "Untuk timeline, saya akan menyesuaikan dengan deadline proyek "
        "dan memastikan kualitas tetap terjaga."
    )
    closing: str = (
        "Saya terbuka untuk diskusi lebih lanjut mengenai detail proyek. "
        "Silakan hubungi saya untuk konsultasi gratis.\n\n"
        "Terima kasih.\n\n"
        "Best regards,\n"
        "Afghan Eka Pangestu\n"
        "Full-Stack Developer"
    )


def generate_proposal(
    project_title: str = "",
    project_description: str = "",
    project_budget: str = "",
    project_deadline: str = "",
    project_skills: str = "",
    language: str = "id",
    template: Optional[ProposalTemplate] = None,
) -> str:
    """Generate a professional proposal based on project details."""
    t = template if template else ProposalTemplate()

    if language == "id":
        return _generate_indonesian(project_title, project_description,
                                     project_budget, project_deadline,
                                     project_skills, t)
    else:
        return _generate_english(project_title, project_description,
                                  project_budget, project_deadline,
                                  project_skills, t)


def _extract_tech_from_skills(skills_str: str) -> str:
    """Extract tech stack keywords from skills string."""
    common_stacks = [
        "Laravel", "PHP", "Python", "Django", "Flask", "Go", "Golang",
        "React", "Vue.js", "Angular", "Next.js", "Node.js", "Express",
        "MySQL", "PostgreSQL", "MongoDB", "Redis",
        "Docker", "AWS", "GCP", "Firebase",
        "Mobile", "React Native", "Flutter", "Android", "iOS",
        "API", "REST", "GraphQL", "WebSocket",
        "JavaScript", "TypeScript", "HTML", "CSS", "Bootstrap",
        "Java", "Spring", "Kotlin",
    ]
    found = []
    for tech in common_stacks:
        if tech.lower() in skills_str.lower():
            found.append(tech)
    return ", ".join(found) if found else "Web Development"


def _generate_indonesian(title, desc, budget, deadline, skills, t) -> str:
    tech = _extract_tech_from_skills(skills)

    # Smart opening based on project type
    opening = t.opening.replace("{title}", title or "Anda")

    experience = t.experience.replace("{stack}", tech)

    # Add budget reference if available
    budget_line = ""
    if budget:
        budget_line = f"\n💰 Estimasi budget: {budget}\n"

    # Add deadline reference if available
    deadline_line = ""
    if deadline and deadline != "-":
        deadline_line = f"📅 Deadline: {deadline}\n"

    budget_comment = ""
    if budget and budget not in ("-", "", "Rp 0"):
        budget_comment = (
            f"Terkait budget {budget}, saya terbuka untuk negosiasi "
            f"dan akan memberikan penawaran terbaik.\n\n"
        )

    deadline_comment = ""
    if deadline and deadline != "-":
        deadline_comment = (
            f"Dengan deadline {deadline}, saya akan membuat timeline "
            f"yang realistis dan memberikan update rutin.\n\n"
        )

    proposal = (
        f"{t.greeting}\n\n"
        f"{opening}\n\n"
        f"{experience}\n\n"
        f"{t.approach}\n\n"
        f"{budget_comment}"
        f"{deadline_comment}"
        f"{t.closing}"
    )
    return proposal


def _generate_english(title, desc, budget, deadline, skills, t) -> str:
    tech = _extract_tech_from_skills(skills)
    opening = f"I'm interested in your project \"{title or 'you posted'}\"."

    experience = (
        f"I have strong experience in:\n"
        f"• Working with various projects using {tech}\n"
        f"• Following best practices and clean code principles\n"
        f"• Being communicative and responsive during development"
    )

    approach = (
        f"My approach:\n"
        f"1. Requirement analysis and detailed consultation\n"
        f"2. Main implementation with regular updates\n"
        f"3. Testing and revisions\n"
        f"4. Deployment and documentation"
    )

    budget_comment = ""
    if budget and budget not in ("-", "", "Rp 0"):
        budget_comment = (
            f"Regarding the budget of {budget}, I'm open to negotiation "
            f"and will provide competitive pricing for quality work.\n\n"
        )

    deadline_comment = ""
    if deadline and deadline != "-":
        deadline_comment = (
            f"With the deadline of {deadline}, I'll create a realistic "
            f"timeline and keep you updated throughout.\n\n"
        )

    proposal = (
        f"Hello,\n\n"
        f"{opening}\n\n"
        f"{experience}\n\n"
        f"{approach}\n\n"
        f"{budget_comment}"
        f"{deadline_comment}"
        f"Thank you for considering my application.\n\n"
        f"Best regards,\n"
        f"Afghan Eka Pangestu\n"
        f"Full-Stack Developer"
    )
    return proposal


# Quick format for inline keyboard reply
def format_proposal_short(project_title: str, skills: str = "") -> str:
    """Short proposal snippet shown before generating full proposal."""
    tech = _extract_tech_from_skills(skills) if skills else "relevant tech"
    return (
        f"📝 Draft proposal untuk:\n"
        f"<b>{project_title}</b>\n\n"
        f"Buat dalam Bahasa Indonesia atau English?"
    )
