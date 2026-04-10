"""
User Settings - Persistent per-user configuration for keywords, skills, budget.
Supports both global (legacy) and per-user storage via chat_id.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
GLOBAL_SETTINGS_FILE = os.path.join(DATA_DIR, "user_settings.json")

def _user_file(chat_id: str | None, filename: str) -> str:
    """Return path to per-user data file, or global if no chat_id."""
    if chat_id:
        user_dir = os.path.join(DATA_DIR, str(chat_id))
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, filename)
    return os.path.join(DATA_DIR, filename)

class UserSettings:
    """Manages per-user configuration: keywords, skills, budget filter.
    
    If chat_id is provided, settings are stored in data/<chat_id>/user_settings.json.
    Otherwise uses global data/user_settings.json (legacy).
    """

    def __init__(self, chat_id: str | None = None, data_file: str | None = None):
        self.chat_id = chat_id
        self.data_file = data_file or _user_file(chat_id, "user_settings.json")
        self.keywords: list[str] = []
        self.skills: list[str] = []
        self.min_budget: int = 0
        self.proposal_lang: str = "id"
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r") as f:
                    data = json.load(f)
                    self.keywords = data.get("keywords", [])
                    self.skills = data.get("skills", [])
                    self.min_budget = data.get("min_budget", 0)
                    self.proposal_lang = data.get("proposal_lang", "id")
                logger.info(
                    f"Settings loaded: {len(self.keywords)} keywords, "
                    f"{len(self.skills)} skills, min_budget={self.min_budget}"
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Error loading settings: {e}")

    def _save(self):
        data = {
            "keywords": self.keywords,
            "skills": self.skills,
            "min_budget": self.min_budget,
            "proposal_lang": self.proposal_lang,
        }
        with open(self.data_file, "w") as f:
            json.dump(data, f, indent=2)

    # --- Keyword management ---
    def add_keyword(self, keyword: str) -> bool:
        kw = keyword.lower().strip()
        if kw in [k.lower() for k in self.keywords]:
            return False
        self.keywords.append(keyword)
        self._save()
        return True

    def remove_keyword(self, keyword: str) -> bool:
        kw = keyword.lower().strip()
        for i, k in enumerate(self.keywords):
            if k.lower() == kw:
                self.keywords.pop(i)
                self._save()
                return True
        return False

    def clear_keywords(self):
        self.keywords = []
        self._save()

    def keyword_matches(self, title: str, description: str) -> list[str]:
        """Return list of keywords that match title/description."""
        text = (f"{title} {description}").lower()
        matches = []
        for kw in self.keywords:
            if kw.lower() in text:
                matches.append(kw)
        return matches

    # --- Skill management ---
    def add_skill(self, skill: str) -> bool:
        sk = skill.lower().strip()
        if sk in [s.lower() for s in self.skills]:
            return False
        self.skills.append(skill)
        self._save()
        return True

    def remove_skill(self, skill: str) -> bool:
        sk = skill.lower().strip()
        for i, s in enumerate(self.skills):
            if s.lower() == sk:
                self.skills.pop(i)
                self._save()
                return True
        return False

    def clear_skills(self):
        self.skills = []
        self._save()

    def skills_score(self, title: str, description: str) -> int:
        """Return match score 0-100 based on skills overlap."""
        if not self.skills:
            return 50  # neutral if no skills set
        text = f"{title} {description}".lower()
        matches = sum(
            1 for s in self.skills if s.lower() in text
        )
        return int((matches / len(self.skills)) * 100)

    def matched_skills(self, title: str, description: str) -> list[str]:
        """Return skills that appear in the project."""
        text = f"{title} {description}".lower()
        return [s for s in self.skills if s.lower() in text]

    # --- Budget filter ---
    def set_min_budget(self, amount: int):
        self.min_budget = amount
        self._save()

    def clear_min_budget(self):
        self.min_budget = 0
        self._save()

    def should_filter(self, project_budget_str: str) -> bool:
        """Check if project should be filtered based on min_budget."""
        if self.min_budget <= 0:
            return False
        budget = parse_budget_rp(project_budget_str)
        return budget < self.min_budget

    # --- Proposal language ---
    def set_proposal_lang(self, lang: str):
        self.proposal_lang = "id" if lang.lower() in ("id", "indonesian") else "en"
        self._save()

def parse_budget_rp(budget_str: str) -> int:
    """Parse Indonesian budget string like 'Rp 1.000.000 - Rp 2.000.000' to min amount."""
    import re
    # Find all number patterns
    amounts = re.findall(r'Rp\s*([\d,.]+)', budget_str)
    if not amounts:
        return 0
    def clean_num(s):
        return int(s.replace('.', '').replace(',', ''))
    return min(clean_num(a) for a in amounts)
