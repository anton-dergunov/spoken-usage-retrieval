"""Versioned text analysis. Importing this module never loads models or uses the network."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

from .catalogue import canonical_language
from .text import normalize_token, tokens_with_spans


class UnsupportedAnalysisError(ValueError):
    """The requested language has no usable local morphological analyzer."""


class IncompatibleAnalyzerError(RuntimeError):
    """Query analysis would differ from the analysis stored in the index."""


class InvalidAnalysisError(ValueError):
    """An analyzer returned annotations that cannot be mapped to source text."""


@dataclass(frozen=True)
class AnalyzerProvenance:
    name: str
    language: str
    package_version: str
    model_version: str | None
    settings: dict[str, Any]

    @property
    def identity(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return f"{self.name}:{hashlib.sha256(payload.encode()).hexdigest()[:20]}"

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "identity": self.identity}


@dataclass(frozen=True)
class AnalyzedToken:
    surface: str
    normalized: str
    start: int
    end: int
    lemma: str | None = None
    upos: str | None = None
    features: dict[str, str] | None = None
    # Expanded words can share the span of an indivisible source token (e.g. "al").
    word: str | None = None
    shared_span: bool = False


@dataclass(frozen=True)
class Analysis:
    tokens: tuple[AnalyzedToken, ...]
    provenance: AnalyzerProvenance

    def validate(self, text: str) -> Analysis:
        previous: AnalyzedToken | None = None
        for token in self.tokens:
            if not (0 <= token.start < token.end <= len(text)):
                raise InvalidAnalysisError(f"Invalid token span {token.start}:{token.end}")
            if token.normalized != normalize_token(token.surface):
                raise InvalidAnalysisError(f"Invalid normalized surface at {token.start}")
            if text[token.start : token.end] != token.surface:
                raise InvalidAnalysisError(f"Token surface differs from source at {token.start}")
            if previous and token.start < previous.end:
                if not (
                    token.shared_span
                    and previous.shared_span
                    and (token.start, token.end) == (previous.start, previous.end)
                ):
                    raise InvalidAnalysisError(f"Overlapping or unordered span at {token.start}")
            previous = token
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokens": [asdict(token) for token in self.tokens],
            "analyzer": self.provenance.as_dict(),
        }


class TextAnalyzer(Protocol):
    provenance: AnalyzerProvenance
    morphology_available: bool
    unavailable_reason: str | None

    def analyze(self, text: str) -> Analysis: ...


def usable_lemma(value: str | None) -> str | None:
    if not value or value == "_" or not value.strip():
        return None
    return normalize_token(value)


class UnicodeAnalyzer:
    morphology_available = False

    def __init__(self, language: str, reason: str | None = None):
        self.provenance = AnalyzerProvenance(
            "unicode", language, "1", None, {"tokenizer": "unicode-regex-v1"}
        )
        self.unavailable_reason: str | None = (
            reason or "This index uses surface-only Unicode analysis."
        )

    def analyze(self, text: str) -> Analysis:
        return Analysis(
            tuple(
                AnalyzedToken(token.text, token.normalized, token.start, token.end)
                for token in tokens_with_spans(text)
            ),
            self.provenance,
        ).validate(text)


class SimplemmaAnalyzer:
    morphology_available = True
    unavailable_reason: str | None = None

    def __init__(self, language: str):
        import simplemma

        self.language = language.split("-")[0]
        self._lemmatize = simplemma.lemmatize
        self.provenance = AnalyzerProvenance(
            "simplemma",
            language,
            version("simplemma"),
            None,
            {"tokenizer": "unicode-regex-v1", "greedy": False, "adapter_version": 1},
        )

    def analyze(self, text: str) -> Analysis:
        return Analysis(
            tuple(
                AnalyzedToken(
                    token.text,
                    token.normalized,
                    token.start,
                    token.end,
                    usable_lemma(
                        self._lemmatize(token.text.casefold(), lang=self.language, greedy=False)
                    ),
                )
                for token in tokens_with_spans(text)
            ),
            self.provenance,
        ).validate(text)


def _stanza_language(language: str) -> str:
    if language == "zh-Hant" or language.startswith("zh-Hant-"):
        return "zh-hant"
    return {"zh": "zh-hans"}.get(language.split("-")[0], language.split("-")[0])


def _stanza_resources(models_dir: Path, language: str) -> tuple[str, dict, dict]:
    try:
        resources = json.loads((models_dir / "resources.json").read_text())
        key = _stanza_language(language)
        entry = resources.get(key, {})
        while "alias" in entry:
            key = entry["alias"]
            entry = resources.get(key, {})
        # Use explicit processor packages: no parser, NER or transformer pipelines.
        available_packages = entry.get("packages", {})
        packages = available_packages.get("default_fast", available_packages.get("default", {}))
        processors = {
            name: packages[name] for name in ("tokenize", "mwt", "pos", "lemma") if name in packages
        }
        if not all(name in processors for name in ("tokenize", "pos", "lemma")):
            raise ValueError("No compatible tokenize/POS/lemma package")
        return key, entry, processors
    except (OSError, ValueError, KeyError) as error:
        raise UnsupportedAnalysisError(
            f"No local Stanza resources for {language}. Run: speech-retrieval models download {language}"
        ) from error


class StanzaAnalyzer:
    morphology_available = True
    unavailable_reason: str | None = None

    def __init__(self, language: str, models_dir: Path):
        try:
            import stanza
            from stanza.resources.common import DEFAULT_RESOURCES_VERSION
        except ImportError as error:
            raise UnsupportedAnalysisError(
                "Install the nlp extra to use Stanza: uv sync --extra nlp"
            ) from error
        key, resources, processors = _stanza_resources(models_dir, language)
        try:
            self.pipeline = stanza.Pipeline(
                lang=key,
                dir=str(models_dir),
                processors=processors,
                package=None,
                download_method=None,
                use_gpu=False,
                verbose=False,
                tokenize_batch_size=32,
                pos_batch_size=256,
                lemma_batch_size=256,
            )
        except (FileNotFoundError, OSError, ValueError) as error:
            raise UnsupportedAnalysisError(
                f"Stanza models for {language} are missing or unusable. "
                f"Run: speech-retrieval models download {language} --models-dir {models_dir}"
            ) from error
        self._lock = threading.Lock()
        # Record upstream resource metadata, including model checksums, without a new registry.
        model_resources = {
            name: resources.get(name, {}).get(package) for name, package in processors.items()
        }
        self.provenance = AnalyzerProvenance(
            "stanza",
            language,
            version("stanza"),
            DEFAULT_RESOURCES_VERSION,
            {
                "processors": processors,
                "resources": model_resources,
                "adapter_version": 1,
                "use_gpu": False,
                "tokenize_batch_size": 32,
                "pos_batch_size": 256,
                "lemma_batch_size": 256,
            },
        )

    def analyze(self, text: str) -> Analysis:
        with self._lock:
            document = self.pipeline(text)
        tokens = []
        for sentence in document.sentences:
            for parent in sentence.tokens:
                words = [word for word in parent.words if word.upos not in ("PUNCT", "SYM")]
                for word in words:
                    start, end = getattr(word, "start_char", None), getattr(word, "end_char", None)
                    shared = len(parent.words) > 1
                    if shared or start is None or end is None:
                        start, end = (
                            getattr(parent, "start_char", None),
                            getattr(parent, "end_char", None),
                        )
                    if not isinstance(start, int) or not isinstance(end, int):
                        raise InvalidAnalysisError("Stanza returned a token without source offsets")
                    if not shared and text[start:end] != word.text:
                        raise InvalidAnalysisError(
                            f"Stanza word differs from source span {start}:{end}"
                        )
                    features = (
                        dict(part.split("=", 1) for part in word.feats.split("|"))
                        if word.feats and word.feats != "_"
                        else None
                    )
                    tokens.append(
                        AnalyzedToken(
                            text[start:end],
                            normalize_token(text[start:end]),
                            start,
                            end,
                            usable_lemma(word.lemma),
                            word.upos,
                            features,
                            word.text,
                            shared,
                        )
                    )
        return Analysis(tuple(tokens), self.provenance).validate(text)


_initialization_lock = threading.RLock()


def get_analyzer(
    language: str, selection: str = "auto", models_dir: str = "data/models/stanza"
) -> TextAnalyzer:
    with _initialization_lock:
        result = _cached_analyzer(
            canonical_language(language), selection, str(Path(models_dir).resolve())
        )
    if isinstance(result, UnsupportedAnalysisError):
        raise UnsupportedAnalysisError(str(result))
    return result


def clear_analyzer_cache() -> None:
    with _initialization_lock:
        _cached_analyzer.cache_clear()


@lru_cache(maxsize=32)
def _cached_analyzer(
    language: str, selection: str = "auto", models_dir: str = "data/models/stanza"
) -> TextAnalyzer | UnsupportedAnalysisError:
    if selection not in ("auto", "unicode", "simplemma", "stanza"):
        raise ValueError(f"Unknown analyzer: {selection}")
    if selection == "unicode":
        return UnicodeAnalyzer(language)
    if selection in ("auto", "simplemma"):
        from simplemma.strategies.dictionaries.dictionary_factory import SUPPORTED_LANGUAGES

        if language.split("-")[0] in SUPPORTED_LANGUAGES:
            return (
                get_analyzer(language, "simplemma", models_dir)
                if selection == "auto"
                else SimplemmaAnalyzer(language)
            )
        if selection == "simplemma":
            return UnsupportedAnalysisError(f"simplemma does not support {language}")
    try:
        return (
            get_analyzer(language, "stanza", models_dir)
            if selection == "auto"
            else StanzaAnalyzer(language, Path(models_dir))
        )
    except UnsupportedAnalysisError as error:
        if selection == "stanza":
            return error
        return UnicodeAnalyzer(language, str(error))


def recorded_analyzer(record: dict[str, Any], models_dir: Path) -> TextAnalyzer:
    if record["name"] in ("simplemma", "stanza"):
        try:
            installed_version = version(record["name"])
        except PackageNotFoundError as error:
            raise UnsupportedAnalysisError(
                f"Install {record['name']} to use this index's morphology"
            ) from error
        if installed_version != record["package_version"]:
            raise IncompatibleAnalyzerError(
                "Analyzer package version changed; rebuild the corpus index"
            )
    analyzer = get_analyzer(record["language"], record["name"], str(models_dir))
    if analyzer.provenance.identity != record["identity"]:
        raise IncompatibleAnalyzerError(
            "Analyzer version or settings changed; rebuild the corpus index"
        )
    return analyzer


def download_models(language: str, models_dir: Path) -> dict[str, Any]:
    language = canonical_language(language)
    try:
        import stanza
        from stanza.resources.common import download_resources_json
    except ImportError as error:
        raise UnsupportedAnalysisError(
            "Install the nlp extra first: uv sync --extra nlp"
        ) from error
    models_dir.mkdir(parents=True, exist_ok=True)
    download_resources_json(str(models_dir))
    key, _, processors = _stanza_resources(models_dir, language)
    stanza.download(lang=key, model_dir=str(models_dir), processors=processors, package=None)
    clear_analyzer_cache()
    analyzer = get_analyzer(language, "stanza", str(models_dir.resolve()))
    return {"models_dir": str(models_dir), "analyzer": analyzer.provenance.as_dict()}
