"""
Generic monitoring configuration — per-category toggles.

Usage:
    from tracking.monitor import MonitorConfig
    monitor = MonitorConfig("data/monitor_config.json")
    monitor.toggle("web_dev")
    if monitor.is_enabled("web_dev"):
        ...
"""
import json
import os
import logging

logger = logging.getLogger(__name__)


class MonitorConfig:
    """Per-category monitoring configuration. Generic — works for any platform."""

    def __init__(self, data_file: str, default_categories: list[str] | None = None):
        self.data_file = data_file
        self.enabled: set[str] = set()
        self._load()
        # Auto-enable default categories if file doesn't exist yet
        if default_categories and not self.enabled:
            self.enabled = set(default_categories)
            self._save()

    def _load(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                data = json.load(f)
                self.enabled = set(data.get("enabled", []))
            logger.info(
                f"[{self.data_file}] Loaded monitor config: {len(self.enabled)} enabled"
            )

    def _save(self):
        with open(self.data_file, "w") as f:
            json.dump({"enabled": list(self.enabled)}, f, indent=2)

    def is_enabled(self, category_id: str) -> bool:
        return category_id in self.enabled

    def toggle(self, category_id: str):
        if category_id in self.enabled:
            self.enabled.discard(category_id)
        else:
            self.enabled.add(category_id)
        self._save()

    def enable_all(self, categories: list[str]):
        self.enabled = set(categories)
        self._save()

    def __len__(self) -> int:
        return len(self.enabled)

    # ── Backward-compatible aliases ──
    @property
    def monitored_categories(self) -> list[str]:
        return list(self.enabled)

    def is_monitored(self, category_id: str) -> bool:
        return self.is_enabled(category_id)


# ── Type aliases for backward compatibility ──
FastworkMonitorConfig = MonitorConfig
SribuMonitorConfig = MonitorConfig
