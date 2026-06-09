from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import json
import re


DEFAULT_RULES: dict[str, object] = {
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
    "browser_apps": [
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
    ],
    "productive_domains": [
        "github.com",
        "stackoverflow.com",
        "leetcode.com",
        "docs.python.org",
        "developer.mozilla.org",
        "docs.google.com",
        "figma.com",
        "localhost",
        "linkedin.com",
    ],
    "distracting_domains": [
        "youtube.com",
        "reddit.com",
        "tiktok.com",
        "instagram.com",
        "netflix.com",
        "x.com",
        "twitter.com",
    ],
    "neutral_domains": [
        "mail.google.com",
        "calendar.google.com",
        "drive.google.com",
    ],
    "domain_aliases": {
        "github": "github.com",
        "stack overflow": "stackoverflow.com",
        "stackoverflow": "stackoverflow.com",
        "leetcode": "leetcode.com",
        "youtube": "youtube.com",
        "reddit": "reddit.com",
        "tiktok": "tiktok.com",
        "instagram": "instagram.com",
        "netflix": "netflix.com",
        "twitter": "twitter.com",
        "x ": "x.com",
        "x.com": "x.com",
        "gmail": "mail.google.com",
        "google calendar": "calendar.google.com",
        "calendar": "calendar.google.com",
        "google docs": "docs.google.com",
        "docs": "docs.google.com",
        "figma": "figma.com",
        "linkedin": "linkedin.com",
        "localhost": "localhost",
    },
    "app_context_tags": {
        "code.exe": ["work", "coding"],
        "pycharm64.exe": ["work", "coding"],
        "idea64.exe": ["work", "coding"],
        "notion.exe": ["work", "planning"],
        "obsidian.exe": ["study", "notes"],
        "excel.exe": ["work", "analysis"],
        "winword.exe": ["work", "writing"],
        "discord.exe": ["communication"],
        "telegram.exe": ["communication"],
        "whatsapp.exe": ["communication"],
    },
    "keyword_context_tags": {
        "documentation": ["work", "research"],
        "docs": ["research"],
        "github": ["work", "coding"],
        "leetcode": ["study", "interview_prep"],
        "portfolio": ["job_search", "writing"],
        "resume": ["job_search", "writing"],
        "stackoverflow": ["work", "research"],
    },
    "domain_context_tags": {
        "github.com": ["work", "coding"],
        "stackoverflow.com": ["work", "research"],
        "leetcode.com": ["study", "interview_prep"],
        "linkedin.com": ["job_search", "networking"],
        "docs.google.com": ["work", "writing"],
        "docs.python.org": ["work", "research"],
        "developer.mozilla.org": ["work", "research"],
        "mail.google.com": ["communication"],
        "calendar.google.com": ["planning"],
        "drive.google.com": ["work", "docs"],
        "youtube.com": ["video", "learning"],
        "reddit.com": ["social"],
        "instagram.com": ["social"],
        "localhost": ["work", "testing"],
    },
}


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    context_tags: list[str]
    site_hint: str = ""


class RuleBasedClassifier:
    def __init__(self, rules_path: Path) -> None:
        self.rules_path = rules_path
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.rules = self._load_rules()
        self._refresh_matchers()

    def _load_rules(self) -> dict[str, object]:
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
            for key, default_value in rules.items():
                value = raw.get(key)
                if isinstance(default_value, list) and isinstance(value, list):
                    rules[key] = [str(item) for item in value]
                elif isinstance(default_value, dict) and isinstance(value, dict):
                    normalized: dict[str, object] = {}
                    for nested_key, nested_value in value.items():
                        key_text = str(nested_key).strip().lower()
                        if not key_text:
                            continue
                        if isinstance(nested_value, list):
                            normalized[key_text] = [str(item).strip().lower() for item in nested_value if str(item).strip()]
                        elif isinstance(nested_value, str):
                            normalized[key_text] = nested_value.strip().lower()
                    rules[key] = normalized
        return rules

    def _write_default_rules(self) -> None:
        self.rules_path.write_text(
            json.dumps(DEFAULT_RULES, indent=2),
            encoding="utf-8",
        )

    def _refresh_matchers(self) -> None:
        self.productive_apps = {str(value).lower() for value in self.rules["productive_apps"]}
        self.distracting_keywords = [str(value).lower() for value in self.rules["distracting_keywords"]]
        self.productive_keywords = [str(value).lower() for value in self.rules["productive_keywords"]]
        self.neutral_apps = {str(value).lower() for value in self.rules["neutral_apps"]}
        self.browser_apps = {str(value).lower() for value in self.rules["browser_apps"]}
        self.productive_domains = {str(value).lower() for value in self.rules["productive_domains"]}
        self.distracting_domains = {str(value).lower() for value in self.rules["distracting_domains"]}
        self.neutral_domains = {str(value).lower() for value in self.rules["neutral_domains"]}
        self.domain_aliases = {
            str(key).lower(): str(value).lower()
            for key, value in dict(self.rules["domain_aliases"]).items()
            if str(key).strip() and str(value).strip()
        }
        self.app_context_tags = {
            str(key).lower(): list(value)
            for key, value in dict(self.rules["app_context_tags"]).items()
            if isinstance(value, list)
        }
        self.keyword_context_tags = {
            str(key).lower(): list(value)
            for key, value in dict(self.rules["keyword_context_tags"]).items()
            if isinstance(value, list)
        }
        self.domain_context_tags = {
            str(key).lower(): list(value)
            for key, value in dict(self.rules["domain_context_tags"]).items()
            if isinstance(value, list)
        }

    def _domain_matches(self, site_hint: str, candidates: set[str]) -> bool:
        if not site_hint:
            return False

        normalized = site_hint.lower()
        return any(
            normalized == candidate or normalized.endswith(f".{candidate}") or candidate == "localhost" and normalized.startswith("localhost")
            for candidate in candidates
        )

    def _extract_site_hint(self, app: str, title: str) -> str:
        if app not in self.browser_apps:
            return ""

        direct_matches = re.findall(r"(localhost(?::\d+)?|(?:[a-z0-9-]+\.)+[a-z]{2,})", title)
        if direct_matches:
            first_match = direct_matches[0].lower()
            return "localhost" if first_match.startswith("localhost") else first_match

        for alias, domain in self.domain_aliases.items():
            if alias in title:
                return domain

        return ""

    def _collect_context_tags(self, app: str, title: str, site_hint: str) -> list[str]:
        tags: list[str] = []

        def _extend(items: list[str]) -> None:
            for item in items:
                tag = str(item).strip().lower()
                if tag and tag not in tags:
                    tags.append(tag)

        if app in self.app_context_tags:
            _extend(self.app_context_tags[app])

        if site_hint:
            for domain, domain_tags in self.domain_context_tags.items():
                if self._domain_matches(site_hint, {domain}):
                    _extend(domain_tags)

        for keyword, keyword_tags in self.keyword_context_tags.items():
            if keyword in title:
                _extend(keyword_tags)

        if not tags:
            if site_hint:
                tags.append("browsing")
            elif app in self.browser_apps:
                tags.append("web")
            else:
                tags.append("general")

        return tags

    def classify(self, app_name: str | None, window_title: str | None) -> ClassificationResult:
        app = (app_name or "").strip().lower()
        title = (window_title or "").strip().lower()
        site_hint = self._extract_site_hint(app, title)

        if app in self.productive_apps:
            return ClassificationResult("productive", self._collect_context_tags(app, title, site_hint), site_hint)

        if self._domain_matches(site_hint, self.distracting_domains):
            return ClassificationResult("distracting", self._collect_context_tags(app, title, site_hint), site_hint)

        if self._domain_matches(site_hint, self.productive_domains):
            return ClassificationResult("productive", self._collect_context_tags(app, title, site_hint), site_hint)

        if self._domain_matches(site_hint, self.neutral_domains):
            return ClassificationResult("neutral", self._collect_context_tags(app, title, site_hint), site_hint)

        if any(keyword in title for keyword in self.distracting_keywords):
            return ClassificationResult("distracting", self._collect_context_tags(app, title, site_hint), site_hint)

        if any(keyword in title for keyword in self.productive_keywords):
            return ClassificationResult("productive", self._collect_context_tags(app, title, site_hint), site_hint)

        if app in self.neutral_apps:
            return ClassificationResult("neutral", self._collect_context_tags(app, title, site_hint), site_hint)

        return ClassificationResult("unknown", self._collect_context_tags(app, title, site_hint), site_hint)


def load_classifier(rules_path: Path) -> RuleBasedClassifier:
    return RuleBasedClassifier(rules_path)
