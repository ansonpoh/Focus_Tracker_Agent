from pathlib import Path

from classifier import RuleBasedClassifier


def test_classifier_uses_loaded_rules_without_rebuilding_sets(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        """
        {
          "productive_apps": ["Code.exe"],
          "distracting_keywords": ["youtube"],
          "productive_keywords": ["docs"],
          "neutral_apps": ["explorer.exe"]
        }
        """.strip(),
        encoding="utf-8",
    )

    classifier = RuleBasedClassifier(rules_path)

    assert classifier.classify("Code.exe", "anything") == "productive"
    assert classifier.classify("chrome.exe", "YouTube - Google Chrome") == "distracting"
    assert classifier.classify("chrome.exe", "Project Docs") == "productive"
    assert classifier.classify("explorer.exe", "Files") == "neutral"
    assert classifier.classify("unknown.exe", "Other") == "unknown"
