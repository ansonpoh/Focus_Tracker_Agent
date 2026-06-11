from pathlib import Path

from classifier import RuleBasedClassifier


def test_classifier_uses_loaded_rules_without_rebuilding_sets(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        """
        {
          "productive_apps": ["Code.exe"],
          "distracting_apps": ["Spotify.exe"],
          "distracting_keywords": ["youtube"],
          "productive_keywords": ["docs"],
          "neutral_apps": ["explorer.exe"],
          "browser_apps": ["chrome.exe"],
          "productive_domains": ["github.com"],
          "distracting_domains": ["youtube.com"],
          "neutral_domains": ["calendar.google.com", "chatgpt.com", "chess.com"],
          "domain_aliases": {
            "github": "github.com",
            "youtube": "youtube.com",
            "chatgpt": "chatgpt.com",
            "chess": "chess.com"
          },
          "app_context_tags": {
            "code.exe": ["work", "coding"],
            "spotify.exe": ["music"]
          },
          "keyword_context_tags": {
            "docs": ["research"]
          },
          "domain_context_tags": {
            "github.com": ["work", "coding"],
            "youtube.com": ["video"],
            "chatgpt.com": ["work", "ai_tool"],
            "chess.com": ["game", "learning"]
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    classifier = RuleBasedClassifier(rules_path)

    code_result = classifier.classify("Code.exe", "anything")
    spotify_result = classifier.classify("Spotify.exe", "Spotify Premium")
    youtube_result = classifier.classify("chrome.exe", "YouTube - Google Chrome")
    github_result = classifier.classify("chrome.exe", "openai/openai-python pull request - GitHub - Google Chrome")
    docs_result = classifier.classify("chrome.exe", "Project Docs")
    chatgpt_result = classifier.classify("chrome.exe", "ChatGPT - Google Chrome")
    chess_result = classifier.classify("chrome.exe", "Chess Puzzles - Chess.com - Google Chrome")
    neutral_result = classifier.classify("explorer.exe", "Files")
    unknown_result = classifier.classify("unknown.exe", "Other")

    assert code_result.category == "productive"
    assert "coding" in code_result.context_tags
    assert spotify_result.category == "distracting"
    assert "music" in spotify_result.context_tags
    assert youtube_result.category == "distracting"
    assert youtube_result.site_hint == "youtube.com"
    assert github_result.category == "productive"
    assert github_result.site_hint == "github.com"
    assert "work" in github_result.context_tags
    assert docs_result.category == "productive"
    assert "research" in docs_result.context_tags
    assert chatgpt_result.category == "neutral"
    assert chatgpt_result.site_hint == "chatgpt.com"
    assert "ai_tool" in chatgpt_result.context_tags
    assert chess_result.category == "neutral"
    assert chess_result.site_hint == "chess.com"
    assert "learning" in chess_result.context_tags
    assert neutral_result.category == "neutral"
    assert unknown_result.category == "unknown"
