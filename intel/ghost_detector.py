"""
Detektor Ghosting 👻
Analisis kredibilitas project — deteksi project yang kemungkinan besar ghosting
(client hilang tanpa award, fake posting, atau scam).

Pure heuristics — no API needed. Score 0-100 (higher = more ghost).
"""

import re
import math
from datetime import datetime, timedelta
from typing import Optional

# ============================================================
# Constants & Thresholds
# ============================================================

# Budget reasonability (IDR) — budget below this for complex keyword = suspicious
MIN_REASONABLE_BUDGET = {
    "website": 3_000_000,
    "aplikasi": 5_000_000,
    "mobile": 5_000_000,
    "ecommerce": 10_000_000,
    "clone": 15_000_000,
    "system": 7_000_000,
    "dashboard": 3_000_000,
    "api": 2_000_000,
    "laravel": 5_000_000,
    "react": 5_000_000,
    "node": 3_000_000,
    "flutter": 5_000_000,
    "golang": 5_000_000,
    "nextjs": 3_000_000,
    "fullstack": 5_000_000,
    "backend": 3_000_000,
    "frontend": 2_000_000,
    "ai": 10_000_000,
    "machine learning": 15_000_000,
    "blockchain": 20_000_000,
}

# Suspicious description patterns
SUS_PATTERNS = [
    r"(?i)sama\s*(kayak|seperti|persis)\s*[a-z]*\.(com|id|co\.id)",
    r"(?i)ikutin\s*saja",
    r"(?i)tinggal\s*(copy|contek|tiru)",
    r"(?i)gampang\s*(kok|aja|banget)",
    r"(?i)cuma\s*(butuh|perlu|minta)",
    r"(?i)seadanya",
    r"(?i)yang\s*penting\s*(jalan|jadi|beres)",
    r"(?i)bisa\s*di\s*nego",
    r"(?i)negotiable",
]

    # Red flag keywords in title OR description
RED_FLAG_PATTERNS = [
    r"(?i)urgent",
    r"(?i)butuh\s*cepat",
    r"(?i)secepatnya",
    r"(?i)asap",
    r"(?i)deadline\s*\d+\s*(hari|jam|minggu)",
    r"(?i)murah",
    r"(?i)buat\s*pemula",
    r"(?i)cocok\s*untuk\s*(pemula|mahasiswa|anak\s*magang)",
    r"(?i)segera",
    r"(?i)molor\s*dikit",
    r"(?i)revisi\s*sampai\s*jadi",
]

# Good signals (reduce ghost score)
GOOD_SIGNALS = [
    r"(?i)detail",
    r"(?i)spesifikasi",
    r"(?i)requirement",
    r"(?i)tech\s*stack",
    r"(?i)wireframe",
    r"(?i)mockup",
    r"(?i)api\s*documentation",
    r"(?i)sprint",
    r"(?i)agile",
]


class GhostScore:
    """Container for ghost detection results."""

    def __init__(self, score: float, reasons: list[str], details: dict):
        self.score = min(100.0, max(0.0, score))
        self.reasons = reasons
        self.details = details

    @property
    def verdict(self) -> str:
        if self.score >= 80:
            return "👻 POLTERGEIST — hampir pasti ghosting"
        elif self.score >= 60:
            return "😱 HIGH RISK — waspada"
        elif self.score >= 40:
            return "🤔 MEDIUM — perlu due diligence"
        elif self.score >= 20:
            return "🙂 LOW RISK — cukup menjanjikan"
        else:
            return "✅ SAFE — project legit"

    @property
    def emoji(self) -> str:
        if self.score >= 80:
            return "👻"
        elif self.score >= 60:
            return "😱"
        elif self.score >= 40:
            return "🤔"
        elif self.score >= 20:
            return "🙂"
        else:
            return "✅"


def detect_ghost(
    title: str,
    description: str = "",
    budget: Optional[float] = None,
    client_name: str = "",
    posted_date: Optional[str] = None,
    client_project_count: int = 0,
) -> GhostScore:
    """
    Analyze a project for ghosting probability.

    Returns GhostScore with score 0-100 and list of reasons.
    """

    reasons = []
    details = {}
    total_weight = 0.0
    weighted_score = 0.0

    # ---- 1. Budget Reasonability (weight: 25) ----
    if budget is not None and budget > 0:
        w = 25
        total_weight += w
        desc_lower = (title + " " + description).lower()
        budget_issues = 0

        for keyword, min_budget in MIN_REASONABLE_BUDGET.items():
            if keyword in desc_lower and budget < min_budget:
                budget_issues += 1

        # Also check general underbudgeting
        word_count = len(description.split())
        if word_count > 50 and budget < 1_000_000:
            budget_issues += 2
        elif word_count > 100 and budget < 3_000_000:
            budget_issues += 1

        if budget <= 500_000:
            budget_issues += 1  # Micro-budget = suspicious

        sub_score = min(100, budget_issues * 25)
        weighted_score += sub_score * w
        details["budget_reasonability"] = sub_score
        if sub_score >= 50:
            reasons.append(f"💰 Budget {budget:,.0f} terlalu rendah untuk kompleksitas project")
        elif sub_score >= 25:
            reasons.append("💰 Budget agak mencurigakan untuk scope project")

    # ---- 2. Description Quality (weight: 25) ----
    w = 25
    total_weight += w
    sub_score = _score_description(title, description)
    weighted_score += sub_score * w
    details["description_quality"] = sub_score
    if sub_score >= 70:
        reasons.append("📝 Deskripsi terlalu singkat/vague — kurang serius")
    elif sub_score >= 40:
        reasons.append("📝 Deskripsi agak generik — minta detail lebih lanjut")

    # ---- 3. Suspicious Patterns (weight: 20) ----
    w = 20
    total_weight += w
    sub_score = _score_suspicious_patterns(title, description)
    weighted_score += sub_score * w
    details["suspicious_patterns"] = sub_score
    if sub_score >= 50:
        pattern_matches = _find_sus_patterns(title, description)
        if pattern_matches:
            reasons.append(f"🚩 Pola mencurigakan: {', '.join(pattern_matches[:2])}")

    # ---- 4. Client Reputation (weight: 15) ----
    if client_name:
        w = 15
        total_weight += w
        sub_score = _score_client(client_name, client_project_count)
        weighted_score += sub_score * w
        details["client_reputation"] = sub_score
        if client_project_count == 0:
            reasons.append("🆕 Client baru — belum ada track record")
        elif client_project_count < 3:
            reasons.append(f"🆕 Client hanya pernah posting {client_project_count} project")

    # ---- 5. Timing Red Flags (weight: 15) ----
    if posted_date:
        w = 15
        total_weight += w
        sub_score = _score_timing(posted_date)
        weighted_score += sub_score * w
        details["timing"] = sub_score
        if sub_score >= 40:
            reasons.append("🕐 Timing posting mencurigakan — extra caution")

    # Calculate final score
    final_score = weighted_score / total_weight if total_weight > 0 else 50.0

    return GhostScore(score=final_score, reasons=reasons, details=details)


def _score_description(title: str, description: str) -> float:
    """Score description quality. Higher = more ghost-like."""
    desc = description.strip()

    if not desc:
        return 100.0  # No description = max ghost

    word_count = len(desc.split())

    score = 0.0

    # Too short
    if word_count < 10:
        score += 60
    elif word_count < 20:
        score += 40
    elif word_count < 30:
        score += 20

    # Check for structured content
    has_bullets = bool(re.search(r'[-•*#]|^\d+\.', desc, re.MULTILINE))
    has_sections = bool(re.search(r'(?i)(requirement|spesifikasi|fitur|modul|task|deliverable)', desc))
    has_tech = bool(re.search(r'(?i)(laravel|react|node|python|php|java|flutter|golang|aws|docker|api|database)', desc))

    if has_bullets:
        score -= 20
    if has_sections:
        score -= 15
    if has_tech:
        score -= 15

    # Good signals
    for pattern in GOOD_SIGNALS:
        if re.search(pattern, desc):
            score -= 5

    # Copy-paste detection (just title repeated in description)
    if title.lower() == desc.lower().strip():
        score += 30

    # All caps title
    if title.isupper() and len(title) > 10:
        score += 10

    # Excessive punctuation
    if desc.count('!') > 5:
        score += 10

    return min(100.0, max(0.0, score))


def _score_suspicious_patterns(title: str, description: str) -> float:
    """Score suspicious patterns. Higher = more ghost."""
    full_text = f"{title} {description}"
    score = 0.0

    for pattern in SUS_PATTERNS:
        if re.search(pattern, full_text):
            score += 25

    for pattern in RED_FLAG_PATTERNS:
        if re.search(pattern, full_text):
            score += 20

    # Excessive promises
    if re.search(r'(?i)(bisa\s*(kaya|jadi))', description):
        score += 15

    # "Nanti detailnya menyusul" type
    if re.search(r'(?i)(nanti|menyusul|follow\s*up|detailnya\s*nanti)', description):
        score += 20

    return min(100.0, score)


def _score_client(name: str, project_count: int) -> float:
    """Score client trustworthiness. Higher = more ghost."""
    score = 0.0

    if project_count == 0:
        score += 50  # Unknown client
    elif project_count == 1:
        score += 30
    elif project_count < 5:
        score += 15
    else:
        score -= 20  # Established client

    # Anonymous-looking name
    if re.match(r'^[a-zA-Z0-9_]{1,8}$', name):
        score += 15
    if re.match(r'^user\d+', name, re.IGNORECASE):
        score += 20
    if 'guest' in name.lower():
        score += 25

    return min(100.0, max(0.0, score))


def _find_sus_patterns(title: str, description: str) -> list[str]:
    """Return list of matched suspicious patterns."""
    full_text = f"{title} {description}"
    found = []
    for pattern in SUS_PATTERNS:
        match = re.search(pattern, full_text)
        if match:
            found.append(match.group()[:50])
    return found


def _score_timing(posted_date: str) -> float:
    """Score posting time suspiciousness. Higher = more ghost."""
    try:
        # Try parsing various date formats
        for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
            try:
                dt = datetime.strptime(posted_date[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return 0.0  # Can't parse, skip

        score = 0.0
        hour = dt.hour
        weekday = dt.weekday()

        # Odd hours
        if 0 <= hour < 5:
            score += 30  # Midnight posting
        elif 22 <= hour < 24:
            score += 15

        # Weekend posting (less oversight)
        if weekday >= 5:  # Saturday = 5, Sunday = 6
            score += 15

        return score
    except Exception:
        return 0.0


# ============================================================
# Batch analysis
# ============================================================


def analyze_project(title: str, **kwargs) -> GhostScore:
    """Shorthand for detect_ghost with friendly output."""
    return detect_ghost(title=title, **kwargs)


def format_ghost_report(score: GhostScore, project_title: str) -> str:
    """Generate Telegram-friendly ghost report."""
    bar = _progress_bar(score.score)
    lines = [
        f"👻 *Ghost Report*",
        f"📋 Project: {project_title[:60]}",
        f"",
        f"Skor: {score.score:.0f}/100 {bar}",
        f"Status: {score.verdict}",
    ]

    if score.reasons:
        lines.append("")
        lines.append("*Alasan:*")
        for r in score.reasons:
            lines.append(f"  {r}")

    return "\n".join(lines)


def _progress_bar(score: float, width: int = 10) -> str:
    """Generate visual progress bar."""
    filled = int(score / 100 * width)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


# ============================================================
# Self-test
# ============================================================

if __name__ == "__main__":
    # Test cases
    test_cases = [
        {
            "title": "Buat website seperti Tokopedia",
            "description": "sama kayak tokopedia.com ikutin saja fiturnya",
            "budget": 1_500_000,
            "client_name": "user12345",
            "client_project_count": 0,
        },
        {
            "title": "URGENT!!! Butuh Developer Laravel CEPAT",
            "description": "butuh cepat deadline 3 hari",
            "budget": 800_000,
            "client_name": "fulan",
            "client_project_count": 1,
        },
        {
            "title": "Sistem Manajemen Inventory dengan Barcode",
            "description": "Kami membutuhkan sistem inventory terintegrasi dengan spesifikasi:\n"
                           "- Multi-warehouse support\n"
                           "- Barcode scanning integration\n"
                           "- Real-time stock tracking\n"
                           "- Reporting dashboard\n"
                           "- Role-based access control\n"
                           "Tech stack: Laravel + MySQL + Bootstrap",
            "budget": 12_000_000,
            "client_name": "PT Maju Bersama",
            "client_project_count": 5,
        },
        {
            "title": "gampang kok cuma butuh landing page",
            "description": "landing page seadanya aja yang penting jadi",
            "budget": 300_000,
            "client_name": "guest_8291",
            "client_project_count": 0,
        },
    ]

    print("=" * 60)
    print("GHOST DETECTOR — UNIT TESTS")
    print("=" * 60)

    for i, tc in enumerate(test_cases, 1):
        print(f"\n--- Test Case {i} ---")
        result = detect_ghost(**tc)
        print(format_ghost_report(result, tc["title"]))
        print(f"Details: {result.details}")
