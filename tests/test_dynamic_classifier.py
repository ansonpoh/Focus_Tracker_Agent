from pathlib import Path

from classifier import RuleBasedClassifier
from database import FocusDatabase
from dynamic_classifier import DynamicClassificationEngine, classifier_settings_from_dict


class StubClient:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def classify(self, activity):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _build_engine(tmp_path: Path, *, client: StubClient):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text("{}", encoding="utf-8")
    database = FocusDatabase(tmp_path / "focus.db")
    database.initialize()
    settings = classifier_settings_from_dict(
        {
            "enabled": True,
            "mode": "hybrid",
            "model": "gpt-5-mini",
            "min_confidence_threshold": 0.75,
            "reuse_provisional": True,
        }
    )
    engine = DynamicClassificationEngine(
        database=database,
        heuristic_classifier=RuleBasedClassifier(rules_path),
        settings=settings,
        client=client,
    )
    return engine, database


def test_override_beats_cache_and_llm(tmp_path: Path) -> None:
    client = StubClient()
    engine, database = _build_engine(tmp_path, client=client)
    activity = engine.heuristic_classifier.normalize_activity("Spotify.exe", "Spotify Premium")

    database.upsert_classification_memory(
        scope="fingerprint",
        key=activity.fingerprint,
        app_name=activity.normalized_app_name,
        site_hint=activity.site_hint,
        normalized_title=activity.normalized_title,
        category="neutral",
        context_tags=["music"],
        confidence=0.6,
        source="cache",
        provisional=True,
        reason="cached",
    )
    database.upsert_classification_override(
        scope="app",
        key=activity.normalized_app_name,
        category="distracting",
        context_tags=["music"],
        reason="manual",
    )

    result = engine.classify("Spotify.exe", "Spotify Premium")

    assert result.source == "override"
    assert result.category == "distracting"
    assert client.calls == 0


def test_cache_hit_returns_without_llm(tmp_path: Path) -> None:
    client = StubClient()
    engine, database = _build_engine(tmp_path, client=client)
    activity = engine.heuristic_classifier.normalize_activity("UnknownApp.exe", "Mystery Tool")

    database.upsert_classification_memory(
        scope="fingerprint",
        key=activity.fingerprint,
        app_name=activity.normalized_app_name,
        site_hint=activity.site_hint,
        normalized_title=activity.normalized_title,
        category="productive",
        context_tags=["work"],
        confidence=0.88,
        source="llm",
        provisional=False,
        reason="cached",
    )

    result = engine.classify("UnknownApp.exe", "Mystery Tool")

    assert result.source == "cache"
    assert result.category == "productive"
    assert client.calls == 0


def test_unseen_app_uses_llm_and_persists_memory(tmp_path: Path) -> None:
    from classifier import ClassificationResult

    client = StubClient(
        result=ClassificationResult(
            category="productive",
            context_tags=["work", "analysis"],
            confidence=0.93,
            source="llm",
            reason="Looks like a work tool.",
        )
    )
    engine, database = _build_engine(tmp_path, client=client)

    result = engine.classify("MysterySuite.exe", "Mystery Dashboard")

    assert result.category == "productive"
    assert result.source == "llm"
    memory = database.get_classification_memory("app", "mysterysuite.exe")
    assert memory is not None
    assert memory["category"] == "productive"
    assert client.calls == 1


def test_low_confidence_or_failure_falls_back_to_provisional_neutral(tmp_path: Path) -> None:
    from classifier import ClassificationResult

    client = StubClient(
        result=ClassificationResult(
            category="distracting",
            context_tags=["video"],
            confidence=0.4,
            source="llm",
            reason="uncertain",
        )
    )
    engine, database = _build_engine(tmp_path, client=client)

    result = engine.classify("UnknownPlayer.exe", "Now Playing")

    assert result.category == "neutral"
    assert result.provisional is True
    assert result.source == "fallback"
    candidates = database.list_classification_review_candidates(limit=10, confidence_threshold=0.75)
    assert len(candidates) == 1


def test_heuristic_browser_classification_still_works(tmp_path: Path) -> None:
    client = StubClient()
    engine, _ = _build_engine(tmp_path, client=client)

    result = engine.classify("chrome.exe", "YouTube - Google Chrome")

    assert result.category == "distracting"
    assert result.source == "heuristic"
    assert client.calls == 0
