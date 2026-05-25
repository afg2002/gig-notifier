"""
Generic persistent tracker for seen project/job/contest IDs.

Usage:
    from tracking.seen import SeenTracker
    tracker = SeenTracker("data/seen_projects.json")
    if not tracker.is_seen(project_id):
        tracker.mark_seen(project_id)
"""
import json
import os
import logging

logger = logging.getLogger(__name__)


class SeenTracker:
    """Persistently tracks which IDs have been notified. Works for any platform."""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self.seen_ids: set[str] = set()
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                self.seen_ids = set(json.load(f))
            logger.info(f"[{self.data_file}] Loaded {len(self.seen_ids)} seen IDs")

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump(list(self.seen_ids), f, indent=2)

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.seen_ids

    def mark_seen(self, item_id: str):
        self.seen_ids.add(item_id)
        self._save()

    def __len__(self) -> int:
        return len(self.seen_ids)
