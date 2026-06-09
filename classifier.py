from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json


DEFAULT_RULES: dict[str, list[str]] = {
    "productive_apps": [
        "Code.exe",
        "pycharm64.exe",
        "idea64.exe",
        "notion.exe",
        "obsidian.exe",
        "excel.exe",
        "winword.exe",
    ],
    "distracting_keywords": [
        "youtube",
        "tiktok",
        "instagram",
        "reddit",
        "netflix",
        "valorant",
        "steam",
    ],
    "productive_keywords": [
        "github",
        "leetcode",
        "stackoverflow",
        "documentation",
        "docs",
        "localhost",
        "portfolio",
        "resume",
    ],
    "neutral_apps": [
        "explorer.exe",
        "discord.exe",
        "telegram.exe",
        "whatsapp.exe",
    ],
}


class RuleBasedClassifier:
    def __init__(self, rules_path: Path) -> None:
        self.rules_path = rules_path
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.rules = self._load_rules()
        self._refresh_matchers()

    def _load_rules(self) -> dict[str, list[str]]:
        if not self.rules_path.exists():
            self._write_default_rules()
            return deepcopy(DEFAULT_RULES)

        try:
            raw = json.loads(self.rules_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return deepcopy(DEFAULT_RULES)
        except OSError:
            return deepcopy(DEFAULT_RULES)

        rules = deepcopy(DEFAULT_RULES)
        if isinstance(raw, dict):
            for key in rules:
                value = raw.get(key)
                if isinstance(value, list):
                    rules[key] = [str(item) for item in value]
        return rules

    def _write_default_rules(self) -> None:
        self.rules_path.write_text(
            json.dumps(DEFAULT_RULES, indent=2),
            encoding="utf-8",
        )

    def _refresh_matchers(self) -> None:
        self.productive_apps = {value.lower() for value in self.rules["productive_apps"]}
        self.distracting_keywords = [value.lower() for value in self.rules["distracting_keywords"]]
        self.productive_keywords = [value.lower() for value in self.rules["productive_keywords"]]
        self.neutral_apps = {value.lower() for value in self.rules["neutral_apps"]}

    def classify(self, app_name: str | None, window_title: str | None) -> str:
        app = (app_name or "").strip().lower()
        title = (window_title or "").strip().lower()

        if app in self.productive_apps:
            return "productive"

        if any(keyword in title for keyword in self.distracting_keywords):
            return "distracting"

        if any(keyword in title for keyword in self.productive_keywords):
            return "productive"

        if app in self.neutral_apps:
            return "neutral"

        return "unknown"


def load_classifier(rules_path: Path) -> RuleBasedClassifier:
    return RuleBasedClassifier(rules_path)
