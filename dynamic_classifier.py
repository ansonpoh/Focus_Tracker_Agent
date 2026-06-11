from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Protocol
from urllib import error, request

from classifier import ClassificationResult, NormalizedActivity, RuleBasedClassifier
from database import FocusDatabase

CLASSIFICATION_CATEGORIES = {"productive", "neutral", "distracting", "unknown"}
REVIEW_REASON_FALLBACK = "Automatic fallback used because dynamic classification was unavailable or uncertain."


class OpenAIClassifierError(RuntimeError):
    pass


class ClassificationClient(Protocol):
    def classify(self, activity: NormalizedActivity) -> ClassificationResult:
        ...


@dataclass(frozen=True)
class ClassifierSettings:
    enabled: bool
    mode: str
    model: str
    api_base_url: str
    api_key_env: str
    api_timeout_seconds: int
    request_max_retries: int
    min_confidence_threshold: float
    reuse_provisional: bool
    max_output_tokens: int


def classifier_settings_from_dict(payload: dict[str, Any]) -> ClassifierSettings:
    return ClassifierSettings(
        enabled=bool(payload.get("enabled", True)),
        mode=str(payload.get("mode", "hybrid")),
        model=str(payload.get("model", "gpt-5-mini")),
        api_base_url=str(payload.get("api_base_url", "https://api.openai.com/v1/responses")),
        api_key_env=str(payload.get("api_key_env", "OPENAI_API_KEY")),
        api_timeout_seconds=max(1, int(payload.get("api_timeout_seconds", 10))),
        request_max_retries=max(0, int(payload.get("request_max_retries", 1))),
        min_confidence_threshold=max(0.0, min(1.0, float(payload.get("min_confidence_threshold", 0.75)))),
        reuse_provisional=bool(payload.get("reuse_provisional", True)),
        max_output_tokens=max(64, int(payload.get("max_output_tokens", 300))),
    )


class OpenAIResponsesClassifier:
    def __init__(self, settings: ClassifierSettings) -> None:
        self.settings = settings

    def _build_request_payload(self, activity: NormalizedActivity) -> dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["productive", "neutral", "distracting", "unknown"],
                },
                "context_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 6,
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "reason": {"type": "string"},
                "normalized_identity": {"type": "string"},
            },
            "required": ["category", "context_tags", "confidence", "reason", "normalized_identity"],
        }
        prompt = {
            "app_name": activity.app_name,
            "window_title": activity.window_title,
            "normalized_app_name": activity.normalized_app_name,
            "normalized_title": activity.normalized_title,
            "site_hint": activity.site_hint,
            "fingerprint": activity.fingerprint,
        }
        return {
            "model": self.settings.model,
            "instructions": (
                "You classify desktop activity into one of four categories: productive, neutral, distracting, or unknown. "
                "Use the app name, normalized title, and site hint. Prefer neutral over unknown when evidence is weak, "
                "and keep confidence low for uncertain cases."
            ),
            "input": json.dumps(prompt, ensure_ascii=True),
            "max_output_tokens": self.settings.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "activity_classification",
                    "strict": True,
                    "schema": schema,
                }
            },
        }

    def _extract_text(self, payload: dict[str, Any]) -> str:
        output = payload.get("output")
        if not isinstance(output, list):
            raise OpenAIClassifierError("Responses API returned no output.")
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    return str(part["text"])
        raise OpenAIClassifierError("Responses API returned no output_text content.")

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(self.settings.api_key_env, "").strip()
        if not api_key:
            raise OpenAIClassifierError(f"Missing {self.settings.api_key_env}.")

        req = request.Request(
            self.settings.api_base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.settings.api_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise OpenAIClassifierError(f"OpenAI HTTP {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise OpenAIClassifierError(f"OpenAI request failed: {exc.reason}") from exc

    def classify(self, activity: NormalizedActivity) -> ClassificationResult:
        payload = self._build_request_payload(activity)
        last_error: Exception | None = None
        for _ in range(self.settings.request_max_retries + 1):
            try:
                raw_response = self._request(payload)
                raw_text = self._extract_text(raw_response)
                parsed = json.loads(raw_text)
                category = str(parsed.get("category") or "").strip().lower()
                if category not in CLASSIFICATION_CATEGORIES:
                    raise OpenAIClassifierError("OpenAI returned an unsupported category.")
                tags = [
                    str(item).strip().lower()
                    for item in parsed.get("context_tags", [])
                    if str(item).strip()
                ]
                confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
                reason = str(parsed.get("reason") or "").strip() or "OpenAI classification."
                return ClassificationResult(
                    category=category,
                    context_tags=tags,
                    site_hint=activity.site_hint,
                    confidence=confidence,
                    source="llm",
                    provisional=False,
                    reason=reason,
                    fingerprint=activity.fingerprint,
                )
            except (ValueError, TypeError, OpenAIClassifierError) as exc:
                last_error = exc
        raise OpenAIClassifierError(str(last_error or "OpenAI classification failed."))


class DynamicClassificationEngine:
    def __init__(
        self,
        *,
        database: FocusDatabase,
        heuristic_classifier: RuleBasedClassifier,
        settings: ClassifierSettings,
        client: ClassificationClient | None = None,
    ) -> None:
        self.database = database
        self.heuristic_classifier = heuristic_classifier
        self.settings = settings
        self.client = client or OpenAIResponsesClassifier(settings)

    def _result_from_record(
        self,
        record: dict[str, Any],
        *,
        site_hint: str,
        fingerprint: str,
        source_override: str | None = None,
    ) -> ClassificationResult:
        tags = record.get("context_tags")
        if isinstance(tags, str):
            try:
                parsed_tags = json.loads(tags)
            except json.JSONDecodeError:
                parsed_tags = []
        else:
            parsed_tags = tags
        if not isinstance(parsed_tags, list):
            parsed_tags = []
        return ClassificationResult(
            category=str(record.get("category") or "neutral").lower(),
            context_tags=[str(item).strip().lower() for item in parsed_tags if str(item).strip()],
            site_hint=site_hint,
            confidence=float(record.get("confidence") or 0.0),
            source=source_override or str(record.get("source") or "cache"),
            provisional=bool(record.get("provisional")),
            reason=str(record.get("reason") or ""),
            fingerprint=fingerprint,
        )

    def _lookup_override(self, activity: NormalizedActivity) -> ClassificationResult | None:
        keys = [("fingerprint", activity.fingerprint)]
        if activity.site_hint:
            keys.append(("site", activity.site_hint))
        if activity.normalized_app_name and not activity.is_browser_app:
            keys.append(("app", activity.normalized_app_name))

        for scope, key in keys:
            record = self.database.get_classification_override(scope, key)
            if record is not None:
                return self._result_from_record(record, site_hint=activity.site_hint, fingerprint=activity.fingerprint, source_override="override")
        return None

    def _lookup_memory(self, activity: NormalizedActivity) -> ClassificationResult | None:
        keys = [("fingerprint", activity.fingerprint)]
        if activity.site_hint:
            keys.append(("site", activity.site_hint))
        if activity.normalized_app_name and not activity.is_browser_app:
            keys.append(("app", activity.normalized_app_name))

        for scope, key in keys:
            record = self.database.get_classification_memory(scope, key)
            if record is None:
                continue
            if bool(record.get("provisional")) and not self.settings.reuse_provisional:
                continue
            return self._result_from_record(record, site_hint=activity.site_hint, fingerprint=activity.fingerprint, source_override="cache")
        return None

    def _persist_memory(self, activity: NormalizedActivity, result: ClassificationResult) -> None:
        if not activity.fingerprint:
            return

        self.database.upsert_classification_memory(
            scope="fingerprint",
            key=activity.fingerprint,
            app_name=activity.normalized_app_name,
            site_hint=activity.site_hint,
            normalized_title=activity.normalized_title,
            category=result.category,
            context_tags=result.context_tags,
            confidence=result.confidence,
            source=result.source,
            provisional=result.provisional,
            reason=result.reason,
        )

        if result.provisional:
            return

        if activity.site_hint:
            self.database.upsert_classification_memory(
                scope="site",
                key=activity.site_hint,
                app_name=activity.normalized_app_name,
                site_hint=activity.site_hint,
                normalized_title=activity.normalized_title,
                category=result.category,
                context_tags=result.context_tags,
                confidence=result.confidence,
                source=result.source,
                provisional=False,
                reason=result.reason,
            )
        elif activity.normalized_app_name and not activity.is_browser_app:
            self.database.upsert_classification_memory(
                scope="app",
                key=activity.normalized_app_name,
                app_name=activity.normalized_app_name,
                site_hint="",
                normalized_title=activity.normalized_title,
                category=result.category,
                context_tags=result.context_tags,
                confidence=result.confidence,
                source=result.source,
                provisional=False,
                reason=result.reason,
            )

    def _fallback_result(self, activity: NormalizedActivity, heuristic: ClassificationResult, reason: str) -> ClassificationResult:
        tags = heuristic.context_tags or (["web"] if activity.is_browser_app else ["general"])
        return ClassificationResult(
            category="neutral",
            context_tags=tags,
            site_hint=activity.site_hint,
            confidence=0.2,
            source="fallback",
            provisional=True,
            reason=reason or REVIEW_REASON_FALLBACK,
            fingerprint=activity.fingerprint,
        )

    def classify(self, app_name: str | None, window_title: str | None) -> ClassificationResult:
        activity = self.heuristic_classifier.normalize_activity(app_name, window_title)

        override_result = self._lookup_override(activity)
        if override_result is not None:
            return override_result

        heuristic_result = self.heuristic_classifier.classify_activity(activity)
        if heuristic_result.category != "unknown":
            self._persist_memory(activity, heuristic_result)
            return heuristic_result

        memory_result = self._lookup_memory(activity)
        if memory_result is not None:
            return memory_result

        if not self.settings.enabled or self.settings.mode != "hybrid":
            fallback = self._fallback_result(activity, heuristic_result, "Dynamic classification disabled.")
            self._persist_memory(activity, fallback)
            return fallback

        try:
            llm_result = self.client.classify(activity)
        except Exception as exc:
            fallback = self._fallback_result(activity, heuristic_result, str(exc))
            self._persist_memory(activity, fallback)
            return fallback

        if llm_result.confidence < self.settings.min_confidence_threshold or llm_result.category == "unknown":
            fallback = self._fallback_result(
                activity,
                heuristic_result,
                llm_result.reason or "OpenAI confidence below threshold.",
            )
            self._persist_memory(activity, fallback)
            return fallback

        final_result = ClassificationResult(
            category=llm_result.category,
            context_tags=llm_result.context_tags or heuristic_result.context_tags,
            site_hint=activity.site_hint,
            confidence=llm_result.confidence,
            source="llm",
            provisional=False,
            reason=llm_result.reason,
            fingerprint=activity.fingerprint,
        )
        self._persist_memory(activity, final_result)
        return final_result
