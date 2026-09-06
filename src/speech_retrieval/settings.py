from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any


def _boolean(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"expected a boolean, got {value!r}")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration shared by the library, CLI, and HTTP service."""

    data_dir: Path = Path("data")
    catalogue_dir: Path = Path("config/channels")
    models_dir: Path | None = None
    web_dist: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    enable_channel_mutations: bool = False
    operator_token: str | None = field(default=None, repr=False)
    acquisition_limit: int = 10
    scan_limit: int = 25
    max_ngram: int = 5
    analyzer: str = "auto"
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    max_search_limit: int = 50
    max_suggestion_limit: int = 30
    max_query_length: int = 200
    max_json_body_bytes: int = 65_536
    recent_failure_limit: int = 20
    gemini_api_key: str | None = field(default=None, repr=False)
    translation_model: str = "gemini-3.1-flash-lite"
    translation_timeout_seconds: float = 30.0
    translation_concurrency: int = 4
    translation_target_languages: tuple[str, ...] = ("en", "ru")
    default_target_language: str = "en"

    def __post_init__(self) -> None:
        for name in ("data_dir", "catalogue_dir"):
            object.__setattr__(self, name, Path(getattr(self, name)))
        if self.models_dir is not None:
            object.__setattr__(self, "models_dir", Path(self.models_dir))
        if self.web_dist is not None:
            object.__setattr__(self, "web_dist", Path(self.web_dist))
        if not 1 <= self.port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        if self.acquisition_limit < 1 or self.scan_limit < 1:
            raise ValueError("acquisition and scan limits must be at least 1")
        if not 1 <= self.max_ngram <= 8:
            raise ValueError("max_ngram must be between 1 and 8")
        if self.analyzer not in {"auto", "unicode", "simplemma", "stanza"}:
            raise ValueError("analyzer must be auto, unicode, simplemma, or stanza")
        if not 1 <= self.max_search_limit <= 50:
            raise ValueError("max_search_limit must be between 1 and 50")
        if not 1 <= self.max_suggestion_limit <= 30:
            raise ValueError("max_suggestion_limit must be between 1 and 30")
        if self.max_query_length < 1 or self.max_json_body_bytes < 1:
            raise ValueError("request limits must be positive")
        if self.recent_failure_limit < 1:
            raise ValueError("recent_failure_limit must be positive")
        if not self.translation_model.strip():
            raise ValueError("translation_model must not be empty")
        if self.translation_timeout_seconds <= 0:
            raise ValueError("translation_timeout_seconds must be positive")
        if not 1 <= self.translation_concurrency <= 32:
            raise ValueError("translation_concurrency must be between 1 and 32")
        from .catalogue import canonical_language

        languages = tuple(canonical_language(item) for item in self.translation_target_languages)
        if not languages or len(set(languages)) != len(languages):
            raise ValueError("translation_target_languages must contain unique language tags")
        object.__setattr__(self, "translation_target_languages", languages)
        default_language = canonical_language(self.default_target_language)
        if default_language not in languages:
            raise ValueError("default_target_language must be advertised")
        object.__setattr__(self, "default_target_language", default_language)

    @property
    def resolved_models_dir(self) -> Path:
        return Path(self.models_dir or self.data_dir / "models" / "stanza").resolve()

    @classmethod
    def from_env(cls, prefix: str = "SPEECH_RETRIEVAL_") -> Settings:
        converters: dict[str, Any] = {
            "data_dir": Path,
            "catalogue_dir": Path,
            "models_dir": Path,
            "web_dist": Path,
            "host": str,
            "port": int,
            "enable_channel_mutations": _boolean,
            "operator_token": str,
            "acquisition_limit": int,
            "scan_limit": int,
            "max_ngram": int,
            "analyzer": str,
            "cors_origins": lambda value: tuple(
                item.strip() for item in value.split(",") if item.strip()
            ),
            "max_search_limit": int,
            "max_suggestion_limit": int,
            "max_query_length": int,
            "max_json_body_bytes": int,
            "recent_failure_limit": int,
            "gemini_api_key": str,
            "translation_model": str,
            "translation_timeout_seconds": float,
            "translation_concurrency": int,
            "translation_target_languages": lambda value: tuple(
                item.strip() for item in value.split(",") if item.strip()
            ),
            "default_target_language": str,
        }
        values: dict[str, Any] = {}
        for item in fields(cls):
            variable = prefix + item.name.upper()
            if variable not in os.environ:
                continue
            raw = os.environ[variable]
            if item.name in {"models_dir", "web_dist"} and not raw.strip():
                values[item.name] = None
            else:
                try:
                    values[item.name] = converters[item.name](raw)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"invalid {variable}: {error}") from error
        if "gemini_api_key" not in values and os.environ.get("GEMINI_API_KEY"):
            values["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
        if "translation_model" not in values and os.environ.get("GEMINI_MODEL"):
            values["translation_model"] = os.environ["GEMINI_MODEL"]
        return cls(**values)

    def with_overrides(self, **values: Any) -> Settings:
        return replace(self, **values)
