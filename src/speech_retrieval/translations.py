from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote

import httpx

from .analysis import UnsupportedAnalysisError, get_analyzer
from .captions import manual_units
from .catalogue import canonical_language
from .contracts import (
    AlignmentQuality,
    AlignmentToken,
    CharacterRange,
    Clip,
    SemanticAlignmentGroup,
    TranslationBatch,
    TranslationErrorInfo,
    TranslationJob,
    TranslationResult,
    TranslationServiceStatus,
    WordAlignmentEdge,
    WordAlignmentGraph,
)
from .prompt_registry import (
    ALIGNMENT_PROMPT,
    TRANSLATION_PROMPT,
    TRANSLATION_SCHEMA,
    alignment_schema,
)
from .search import Corpus
from .settings import Settings
from .text import join_text, tokens_with_spans
from .translation_store import TranslationStore

PROMPT_VERSION = TRANSLATION_PROMPT.version
TRANSLATION_SCHEMA_VERSION = TRANSLATION_PROMPT.schema_version
ALIGNMENT_PROMPT_VERSION = ALIGNMENT_PROMPT.version
ALIGNMENT_SCHEMA_VERSION = ALIGNMENT_PROMPT.schema_version
INSTRUCTIONS = TRANSLATION_PROMPT.text
GEMINI_SCHEMA = TRANSLATION_SCHEMA


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TranslationProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class InvalidProviderOutput(TranslationProviderError):
    def __init__(self, message: str, raw_output: str = ""):
        super().__init__("invalid_output", message, retryable=True)
        self.raw_output = raw_output[:100_000]


@dataclass(frozen=True)
class ProviderTranslationRequest:
    source_text: str
    source_language: str
    target_language: str
    authored_reference: str | None = None


@dataclass(frozen=True)
class ProviderAlignmentRequest:
    source_text: str
    target_text: str
    source_language: str
    target_language: str
    source_tokens: tuple[AlignmentToken, ...]
    target_tokens: tuple[AlignmentToken, ...]


@dataclass(frozen=True)
class ProviderResponse:
    payload: dict[str, Any]
    latency_ms: float
    usage: dict[str, int] | None
    raw_output: str
    provider_metadata: dict[str, str] | None = None


ProviderTranslationResponse = ProviderResponse
ProviderAlignmentResponse = ProviderResponse


class TranslationProvider(Protocol):
    provider: str
    model: str

    async def translate(
        self, request: ProviderTranslationRequest
    ) -> ProviderTranslationResponse: ...

    async def aclose(self) -> None: ...


class WordAlignmentProvider(Protocol):
    provider: str
    model: str

    async def align(self, request: ProviderAlignmentRequest) -> ProviderAlignmentResponse: ...

    async def aclose(self) -> None: ...


class GeminiTranslationProvider:
    """Small REST adapter implementing both provider stages without an SDK dependency."""

    provider = "gemini"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float = 30.0):
        self.model = model
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def _generate(
        self,
        *,
        instructions: str,
        user_text: str,
        schema: dict[str, Any],
        temperature: float,
    ) -> ProviderResponse:
        started = time.perf_counter()
        try:
            response = await self._client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{quote(self.model, safe='')}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
                json={
                    "systemInstruction": {"parts": [{"text": instructions}]},
                    "contents": [{"role": "user", "parts": [{"text": user_text}]}],
                    "generationConfig": {
                        "temperature": temperature,
                        "responseMimeType": "application/json",
                        "responseSchema": schema,
                    },
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise TranslationProviderError(
                "temporarily_unavailable",
                "The language provider is temporarily unreachable.",
                retryable=True,
            ) from error
        if response.status_code in {401, 403}:
            raise TranslationProviderError(
                "provider_unavailable",
                "The language provider rejected its credentials.",
                retryable=False,
            )
        if response.status_code == 429:
            raise TranslationProviderError(
                "rate_limited", "The language provider is rate limited.", retryable=True
            )
        if response.status_code == 408 or response.status_code >= 500:
            raise TranslationProviderError(
                "temporarily_unavailable",
                "The language provider is temporarily unavailable.",
                retryable=True,
            )
        if not response.is_success:
            raise TranslationProviderError(
                "provider_unavailable",
                "The language provider rejected the request.",
                retryable=False,
            )
        try:
            body = response.json()
            raw = "".join(
                str(part.get("text", "")) for part in body["candidates"][0]["content"]["parts"]
            )
            payload = json.loads(raw)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise InvalidProviderOutput(
                "Provider returned unreadable structured output.", response.text
            ) from error
        usage_payload = body.get("usageMetadata") or {}
        usage = {
            name: int(value)
            for name, value in {
                "input_tokens": usage_payload.get("promptTokenCount"),
                "output_tokens": usage_payload.get("candidatesTokenCount"),
                "total_tokens": usage_payload.get("totalTokenCount"),
            }.items()
            if isinstance(value, int)
        }
        candidate = body.get("candidates", [{}])[0]
        metadata = {
            name: str(value)
            for name, value in {
                "response_id": body.get("responseId"),
                "model_version": body.get("modelVersion"),
                "finish_reason": candidate.get("finishReason")
                if isinstance(candidate, dict)
                else None,
            }.items()
            if value is not None
        }
        return ProviderResponse(
            payload=payload,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage=usage or None,
            raw_output=raw,
            provider_metadata=metadata or None,
        )

    async def translate(self, request: ProviderTranslationRequest) -> ProviderTranslationResponse:
        reference = (
            "\nAuthored target-language reference (possibly free or incomplete):\n"
            + request.authored_reference
            if request.authored_reference
            else ""
        )
        return await self._generate(
            instructions=TRANSLATION_PROMPT.text,
            user_text=(
                f"Source language: {request.source_language}\n"
                f"Target language: {request.target_language}\n"
                f"Source text:\n{request.source_text}{reference}"
            ),
            schema=TRANSLATION_SCHEMA,
            temperature=TRANSLATION_PROMPT.temperature,
        )

    async def align(self, request: ProviderAlignmentRequest) -> ProviderAlignmentResponse:
        def token_lines(tokens: tuple[AlignmentToken, ...]) -> str:
            return "\n".join(
                f"{token.id}: {json.dumps(token.text, ensure_ascii=False)}" for token in tokens
            )

        return await self._generate(
            instructions=ALIGNMENT_PROMPT.text,
            user_text=(
                f"Source language: {request.source_language}\n"
                f"Target language: {request.target_language}\n"
                f"Source text: {request.source_text}\n"
                f"Target text: {request.target_text}\n\n"
                f"Source tokens:\n{token_lines(request.source_tokens)}\n\n"
                f"Target tokens:\n{token_lines(request.target_tokens)}"
            ),
            schema=alignment_schema(
                [token.id for token in request.source_tokens],
                [token.id for token in request.target_tokens],
            ),
            temperature=ALIGNMENT_PROMPT.temperature,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class ValidatedTranslation:
    target_text: str
    warnings: list[str]


@dataclass(frozen=True)
class ValidatedAlignment:
    graph: WordAlignmentGraph
    groups: list[SemanticAlignmentGroup]
    quality: AlignmentQuality
    warnings: list[str]


def _warnings(payload: dict[str, Any], raw_output: str) -> list[str]:
    value = payload.get("warnings")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidProviderOutput("warnings must be an array of strings.", raw_output)
    return list(value)


def validate_translation_output(response: ProviderTranslationResponse) -> ValidatedTranslation:
    target_text = response.payload.get("target_text")
    if not isinstance(target_text, str) or not target_text.strip():
        raise InvalidProviderOutput(
            "Translation output contains no target text.", response.raw_output
        )
    return ValidatedTranslation(target_text, _warnings(response.payload, response.raw_output))


def alignment_tokens(
    text: str, prefix: str, *, analyzed: list[Any] | None = None
) -> tuple[AlignmentToken, ...]:
    """Create stable lexical nodes and collapse expanded words sharing one source span."""
    raw = analyzed if analyzed is not None else tokens_with_spans(text)
    seen: set[tuple[int, int]] = set()
    result: list[AlignmentToken] = []
    for value in raw:
        if isinstance(value, dict):
            start = int(value["start"])
            end = int(value["end"])
        else:
            start = int(value.start)
            end = int(value.end)
        if (start, end) in seen or not (0 <= start < end <= len(text)):
            continue
        seen.add((start, end))
        result.append(
            AlignmentToken(
                id=f"{prefix}{len(result) + 1}",
                text=text[start:end],
                range=CharacterRange(start=start, end=end),
            )
        )
    return tuple(result)


def validate_alignment_output(
    response: ProviderAlignmentResponse, request: ProviderAlignmentRequest
) -> ValidatedAlignment:
    payload = response.payload
    rows = payload.get("alignments")
    unaligned_target = payload.get("unaligned_target_ids")
    if not isinstance(rows, list) or not isinstance(unaligned_target, list):
        raise InvalidProviderOutput(
            "Alignment rows and unaligned targets must be arrays.", response.raw_output
        )
    if not all(isinstance(item, str) for item in unaligned_target):
        raise InvalidProviderOutput("Unaligned target IDs must be strings.", response.raw_output)

    source_ids = [token.id for token in request.source_tokens]
    target_ids = {token.id for token in request.target_tokens}
    returned_sources: list[str] = []
    unaligned_source: list[str] = []
    edges: list[WordAlignmentEdge] = []
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source_id"), str):
            raise InvalidProviderOutput(
                "Alignment contains an invalid source row.", response.raw_output
            )
        targets = row.get("target_ids")
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            raise InvalidProviderOutput(
                "Alignment target IDs must be string arrays.", response.raw_output
            )
        source_id = row["source_id"]
        returned_sources.append(source_id)
        if not targets:
            unaligned_source.append(source_id)
        for target_id in targets:
            pair = (source_id, target_id)
            if target_id not in target_ids or pair in pairs:
                raise InvalidProviderOutput(
                    "Alignment contains an unknown or duplicate edge.", response.raw_output
                )
            pairs.add(pair)
            edges.append(WordAlignmentEdge(source_token_id=source_id, target_token_id=target_id))
    if returned_sources != source_ids:
        raise InvalidProviderOutput(
            "Alignment must contain every source token exactly once and in order.",
            response.raw_output,
        )
    if (
        len(unaligned_target) != len(set(unaligned_target))
        or not set(unaligned_target) <= target_ids
    ):
        raise InvalidProviderOutput(
            "Alignment contains invalid unaligned targets.", response.raw_output
        )
    linked_targets = {edge.target_token_id for edge in edges}
    if (
        linked_targets & set(unaligned_target)
        or linked_targets | set(unaligned_target) != target_ids
    ):
        raise InvalidProviderOutput(
            "Every target token must be linked or explicitly unaligned.", response.raw_output
        )
    if not edges:
        raise InvalidProviderOutput("Alignment contains no semantic links.", response.raw_output)

    graph = WordAlignmentGraph(
        source_tokens=list(request.source_tokens),
        target_tokens=list(request.target_tokens),
        edges=edges,
        unaligned_source_token_ids=unaligned_source,
        unaligned_target_token_ids=list(unaligned_target),
    )
    target_by_id = {token.id: token for token in request.target_tokens}
    targets_by_source: dict[str, list[CharacterRange]] = defaultdict(list)
    for edge in edges:
        targets_by_source[edge.source_token_id].append(target_by_id[edge.target_token_id].range)
    groups = [
        SemanticAlignmentGroup(
            group_id=index,
            source_ranges=[token.range],
            target_ranges=targets_by_source[token.id],
        )
        for index, token in enumerate(request.source_tokens, 1)
        if token.id in targets_by_source
    ]
    linked_sources = {edge.source_token_id for edge in edges}
    quality = AlignmentQuality(
        source_character_coverage=round(
            sum(
                token.range.end - token.range.start
                for token in request.source_tokens
                if token.id in linked_sources
            )
            / max(1, len(request.source_text)),
            4,
        ),
        target_character_coverage=round(
            sum(
                token.range.end - token.range.start
                for token in request.target_tokens
                if token.id in linked_targets
            )
            / max(1, len(request.target_text)),
            4,
        ),
        edge_count=len(edges),
        source_token_coverage=round(len(linked_sources) / len(request.source_tokens), 4),
        target_token_coverage=round(len(linked_targets) / len(request.target_tokens), 4),
    )
    return ValidatedAlignment(graph, groups, quality, _warnings(payload, response.raw_output))


def _authored_reference(
    settings: Settings, clip: Clip, target_language: str
) -> tuple[str, dict[str, str]] | None:
    video_dir = settings.data_dir / "raw" / "corpora" / clip.source_language / clip.video.video_key
    try:
        manifest = json.loads((video_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    candidates: list[tuple[int, str, str, dict[str, Any]]] = []
    for track in manifest.get("tracks", []):
        if (
            track.get("kind") != "authored"
            or track.get("is_source")
            or track.get("status") not in {"downloaded", "cached"}
        ):
            continue
        try:
            language = canonical_language(str(track.get("language")).removesuffix("-orig"))
        except ValueError:
            continue
        primary_match = language.split("-")[0] == target_language.split("-")[0]
        priority = 0 if language == target_language else 1 if primary_match else 2
        if priority < 2:
            candidates.append((priority, str(track.get("track_id")), language, track))
    if not candidates:
        return None
    _, track_id, language, track = min(candidates, key=lambda item: (item[0], item[1]))
    try:
        payload = json.loads(
            (video_dir / track_id / "subtitles.raw.json3").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    text = ""
    for unit in manual_units(payload):
        if unit.end > clip.sentence_start and unit.start < clip.sentence_end:
            text = join_text(text, unit.text)
    if not text:
        return None
    return text, {
        "track_id": track_id,
        "language": language,
        "checksum": str(track.get("content_sha256") or ""),
    }


@dataclass
class _SharedOperation:
    task: asyncio.Task[None]
    subscribers: set[str]


class TranslationService:
    """Persistent translation followed by independently validated semantic alignment."""

    def __init__(
        self,
        settings: Settings,
        corpus: Corpus,
        translation_provider: TranslationProvider | None = None,
        alignment_provider: WordAlignmentProvider | None = None,
    ):
        self.settings = settings
        self.corpus = corpus
        self.translation_provider = translation_provider
        self.alignment_provider = alignment_provider or (
            cast(WordAlignmentProvider, translation_provider)
            if translation_provider is not None and hasattr(translation_provider, "align")
            else None
        )
        self.store = TranslationStore(
            settings.data_dir / "derived" / "translations.sqlite3", recover_unfinished=True
        )
        self._semaphore = asyncio.Semaphore(settings.translation_concurrency)
        self._operations: dict[str, _SharedOperation] = {}
        self._stage_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._job_keys: dict[str, str] = {}

    @classmethod
    def configured(
        cls,
        settings: Settings,
        corpus: Corpus,
        translation_provider: TranslationProvider | None = None,
        alignment_provider: WordAlignmentProvider | None = None,
    ) -> TranslationService:
        if translation_provider is None and settings.gemini_api_key:
            translation_provider = GeminiTranslationProvider(
                settings.gemini_api_key,
                settings.translation_model,
                timeout_seconds=settings.translation_timeout_seconds,
            )
        return cls(settings, corpus, translation_provider, alignment_provider)

    def validate(self, segment_id: str, target_language: str) -> tuple[Clip, str]:
        target = canonical_language(target_language)
        clip = self.corpus.clip(segment_id)
        if target == clip.source_language:
            raise ValueError("target language must differ from the source language")
        return clip, target

    def translation_key(
        self,
        clip: Clip,
        target: str,
        authored: tuple[str, dict[str, str]] | None,
        supplied: str | None = None,
    ) -> str:
        """What identifies this translation for caching.

        `supplied` is the caller's own target text, which is not this service's output at all: it
        keys the *alignment* work done for a sentence somebody else wrote. It has to be part of the
        key or two callers aligning different translations of one segment would collide, and the
        second would silently be handed the first one's word graph over its own text.

        With a supplied text no translation provider is involved, so its identity is not in the key
        either — there is nothing of ours in the answer to attribute.
        """
        if supplied is not None:
            return _hash(
                "\0".join(
                    (
                        _hash(clip.source_text),
                        clip.source_language,
                        target,
                        "supplied",
                        _hash(supplied),
                    )
                )
            )
        assert self.translation_provider is not None
        return _hash(
            "\0".join(
                (
                    _hash(clip.source_text),
                    clip.source_language,
                    target,
                    self.translation_provider.provider,
                    self.translation_provider.model,
                    PROMPT_VERSION,
                    TRANSLATION_PROMPT.sha256,
                    str(TRANSLATION_SCHEMA_VERSION),
                    authored[1]["checksum"] if authored else "",
                )
            )
        )

    @staticmethod
    def _operation_key(clip: Clip, translation_key: str) -> str:
        anchors = json.dumps(
            {
                "segment_id": clip.segment_id,
                "tokens": [
                    token.model_dump(mode="json")
                    for token in alignment_tokens(
                        clip.source_text, "S", analyzed=clip.token_analysis
                    )
                ],
                "analyzer": clip.analyzer.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return f"{translation_key}:{_hash(anchors)}"

    def _tokenize(
        self, clip: Clip, target: str, target_text: str
    ) -> tuple[
        tuple[AlignmentToken, ...],
        tuple[AlignmentToken, ...],
        dict[str, Any],
        dict[str, Any],
    ]:
        source_tokens = alignment_tokens(clip.source_text, "S", analyzed=clip.token_analysis)
        source_provenance = clip.analyzer.model_dump(mode="json")
        analyzer = get_analyzer(target, "auto", str(self.settings.resolved_models_dir))
        target_analysis = analyzer.analyze(target_text)
        target_tokens = alignment_tokens(target_text, "T", analyzed=list(target_analysis.tokens))
        target_provenance = target_analysis.provenance.as_dict()
        if not source_tokens or not target_tokens:
            raise UnsupportedAnalysisError("No lexical tokens are available for semantic alignment")

        def unreliable_cjk(text: str, provenance: dict[str, Any], count: int) -> bool:
            contains_cjk = any(
                "\u3400" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff" for char in text
            )
            return contains_cjk and provenance["name"] == "unicode" and count <= 1

        if unreliable_cjk(
            clip.source_text, source_provenance, len(source_tokens)
        ) or unreliable_cjk(target_text, target_provenance, len(target_tokens)):
            raise UnsupportedAnalysisError("Reliable CJK tokenization is unavailable")
        return source_tokens, target_tokens, source_provenance, target_provenance

    def alignment_key(
        self,
        clip: Clip,
        target: str,
        target_text: str,
        source_tokens: tuple[AlignmentToken, ...],
        target_tokens: tuple[AlignmentToken, ...],
        source_tokenizer: dict[str, Any],
        target_tokenizer: dict[str, Any],
    ) -> str:
        assert self.alignment_provider is not None
        anchors = json.dumps(
            {
                "source": [token.model_dump(mode="json") for token in source_tokens],
                "target": [token.model_dump(mode="json") for token in target_tokens],
                "source_tokenizer": source_tokenizer,
                "target_tokenizer": target_tokenizer,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return _hash(
            "\0".join(
                (
                    _hash(clip.source_text),
                    _hash(target_text),
                    clip.source_language,
                    target,
                    self.alignment_provider.provider,
                    self.alignment_provider.model,
                    _hash(anchors),
                    ALIGNMENT_PROMPT_VERSION,
                    ALIGNMENT_PROMPT.sha256,
                    str(ALIGNMENT_SCHEMA_VERSION),
                )
            )
        )

    async def request(
        self,
        segment_id: str,
        target_language: str,
        *,
        retry_failed: bool = False,
        target_text: str | None = None,
    ) -> TranslationJob:
        """Translate this clip, or align a translation the caller already has.

        `target_text` is the second of those, and it exists because a host may already hold a
        translation of this passage that it wants aligned rather than replaced. Translating afresh
        would give it a *different* sentence for the same clip, and there is no honest way to show
        two — so a host that has one supplies it, the translate stage is skipped entirely, and what
        comes back is the word alignment of the sentence it sent.

        Half the cost, and the halves that remain are the right ones: one provider call instead of
        two, no translation provider needed at all, and the result carries the caller's own text.
        """
        clip, target = self.validate(segment_id, target_language)
        authored = _authored_reference(self.settings, clip, target)
        if target_text is not None:
            supplied = target_text.strip()
            if not supplied:
                raise ValueError("target_text must not be empty")
            return await self._align_supplied(clip, target, supplied, retry_failed=retry_failed)
        if self.translation_provider is None:
            return self._authored_or_unavailable(clip, target, authored)
        translation_key = self.translation_key(clip, target, authored)
        cached_translation = self.store.stage_cached("translation", translation_key)
        if cached_translation and cached_translation[0] == "failed" and not retry_failed:
            return self.store.create_job(
                clip.segment_id,
                target,
                "failed",
                cache_key=translation_key,
                cache_hit=True,
                error=_public_error("invalid_output", retryable=True),
            )
        translation_value: dict[str, Any] | None = None
        alignment_cache_checked = False
        if cached_translation and cached_translation[0] == "complete":
            translation_value = cast(dict[str, Any], cached_translation[1])
            immediate = self._cached_result(
                clip,
                target,
                translation_key,
                translation_value,
                retry_failed=retry_failed,
            )
            if immediate:
                return immediate
            alignment_cache_checked = True
        job = self.store.create_job(clip.segment_id, target, "queued", cache_key=translation_key)
        operation_key = self._operation_key(clip, translation_key)
        self._job_keys[job.job_id] = operation_key
        if operation := self._operations.get(operation_key):
            operation.subscribers.add(job.job_id)
        else:
            task = asyncio.create_task(
                self._run(
                    operation_key,
                    translation_key,
                    clip,
                    target,
                    authored,
                    retry_failed,
                    translation_value,
                    alignment_cache_checked,
                )
            )
            self._operations[operation_key] = _SharedOperation(task, {job.job_id})
        return job

    async def _align_supplied(
        self, clip: Clip, target: str, supplied: str, *, retry_failed: bool
    ) -> TranslationJob:
        """The caller's own translation, aligned and handed straight back.

        Shaped like `_translate`'s return so `_run` cannot tell the difference: it already skips the
        translate stage when it is handed a translation, so this needs no new branch there. Nothing
        is written to the translation cache — there is nothing of ours to cache, and the alignment
        stage keys itself on both texts already.
        """
        value: dict[str, Any] = {
            "target_text": supplied,
            "warnings": [],
            "latency_ms": 0.0,
            "usage": None,
            "provider_metadata": {"source": "supplied"},
        }
        key = self.translation_key(clip, target, None, supplied=supplied)
        if self.alignment_provider is None:
            # A perfectly good answer: the caller gets its own sentence back and knows there is no
            # word graph for it. Deliberately not an error — the text was never ours to fail at.
            return self.store.create_job(
                clip.segment_id,
                target,
                "complete",
                cache_key=None,
                result=self._compose(clip, target, value, None, alignment_status="unavailable"),
            )
        job = self.store.create_job(clip.segment_id, target, "queued", cache_key=key)
        operation_key = self._operation_key(clip, key)
        self._job_keys[job.job_id] = operation_key
        if operation := self._operations.get(operation_key):
            operation.subscribers.add(job.job_id)
        else:
            task = asyncio.create_task(
                self._run(operation_key, key, clip, target, None, retry_failed, value, False)
            )
            self._operations[operation_key] = _SharedOperation(task, {job.job_id})
        return job

    def _cached_result(
        self,
        clip: Clip,
        target: str,
        translation_key: str,
        translation: dict[str, Any],
        *,
        retry_failed: bool,
    ) -> TranslationJob | None:
        if self.alignment_provider is None:
            result = self._compose(clip, target, translation, None, alignment_status="unavailable")
            return self.store.create_job(
                clip.segment_id,
                target,
                "complete",
                cache_key=translation_key,
                cache_hit=True,
                result=result,
            )
        try:
            source_tokens, target_tokens, source_tokenizer, target_tokenizer = self._tokenize(
                clip, target, translation["target_text"]
            )
        except UnsupportedAnalysisError:
            result = self._compose(clip, target, translation, None, alignment_status="unavailable")
            return self.store.create_job(
                clip.segment_id,
                target,
                "complete",
                cache_key=translation_key,
                cache_hit=True,
                result=result,
            )
        key = self.alignment_key(
            clip,
            target,
            translation["target_text"],
            source_tokens,
            target_tokens,
            source_tokenizer,
            target_tokenizer,
        )
        cached = self.store.stage_cached("alignment", key)
        if cached and cached[0] == "complete":
            result = self._compose(
                clip,
                target,
                translation,
                cast(dict[str, Any], cached[1]),
                alignment_status="complete",
                source_tokenizer=source_tokenizer,
                target_tokenizer=target_tokenizer,
            )
        elif cached and cached[0] == "failed" and not retry_failed:
            result = self._compose(
                clip,
                target,
                translation,
                None,
                alignment_status="failed",
                alignment_error_code="invalid_output",
                source_tokenizer=source_tokenizer,
                target_tokenizer=target_tokenizer,
            )
        else:
            return None
        return self.store.create_job(
            clip.segment_id, target, "complete", cache_key=key, cache_hit=True, result=result
        )

    async def _run(
        self,
        operation_key: str,
        translation_key: str,
        clip: Clip,
        target: str,
        authored: tuple[str, dict[str, str]] | None,
        retry_failed: bool,
        cached_translation: dict[str, Any] | None,
        alignment_cache_checked: bool,
    ) -> None:
        operation = self._operations[operation_key]
        try:
            for job_id in list(operation.subscribers):
                self.store.update_job(job_id, "running")
            if cached_translation is None:
                translation = await self._shared_stage(
                    f"translation:{translation_key}",
                    lambda: self._translate(
                        clip,
                        target,
                        authored,
                        translation_key,
                        next(iter(operation.subscribers), None),
                    ),
                )
            else:
                translation = cached_translation
            result = await self._resolve_alignment(
                clip,
                target,
                translation_key,
                translation,
                retry_failed,
                next(iter(operation.subscribers), None),
                alignment_cache_checked,
            )
            for job_id in list(operation.subscribers):
                if self.store.job(job_id).status != "cancelled":
                    self.store.update_job(job_id, "complete", result=result)
        except asyncio.CancelledError:
            for job_id in list(operation.subscribers):
                if self.store.job(job_id).status not in {"cancelled", "complete"}:
                    self.store.update_job(job_id, "interrupted")
            raise
        except TranslationProviderError as error:
            public = _public_error(error.code, retryable=error.retryable)
            for job_id in list(operation.subscribers):
                if self.store.job(job_id).status != "cancelled":
                    self.store.update_job(job_id, "failed", error=public)
        except Exception:
            public = _public_error("temporarily_unavailable", retryable=True)
            for job_id in list(operation.subscribers):
                if self.store.job(job_id).status != "cancelled":
                    self.store.update_job(job_id, "failed", error=public)
        finally:
            if self._operations.get(operation_key) is operation:
                self._operations.pop(operation_key, None)
            for job_id in operation.subscribers:
                self._job_keys.pop(job_id, None)

    async def _translate(
        self,
        clip: Clip,
        target: str,
        authored: tuple[str, dict[str, str]] | None,
        key: str,
        job_id: str | None,
    ) -> dict[str, Any]:
        assert self.translation_provider is not None
        response: ProviderResponse | None = None
        try:
            async with self._semaphore:
                response = await self.translation_provider.translate(
                    ProviderTranslationRequest(
                        clip.source_text,
                        clip.source_language,
                        target,
                        authored[0] if authored else None,
                    )
                )
            validated = validate_translation_output(response)
            value = {
                "target_text": validated.target_text,
                "warnings": validated.warnings,
                "latency_ms": response.latency_ms,
                "usage": response.usage,
                "provider_metadata": response.provider_metadata,
            }
            self.store.save_translation(
                key,
                clip,
                target,
                self.translation_provider.provider,
                self.translation_provider.model,
                PROMPT_VERSION,
                TRANSLATION_SCHEMA_VERSION,
                value,
            )
            self._attempt("translation", key, job_id, "complete", response)
            return value
        except InvalidProviderOutput as error:
            self.store.save_translation(
                key,
                clip,
                target,
                self.translation_provider.provider,
                self.translation_provider.model,
                PROMPT_VERSION,
                TRANSLATION_SCHEMA_VERSION,
                None,
                status="invalid",
                error=_public_error("invalid_output", retryable=True),
            )
            self._attempt("translation", key, job_id, "invalid", response, str(error))
            raise
        except TranslationProviderError as error:
            self._attempt("translation", key, job_id, "failed", response, error.code)
            raise

    async def _shared_stage(
        self,
        stage_key: str,
        start: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
    ) -> dict[str, Any]:
        task = self._stage_tasks.get(stage_key)
        if task is None:
            task = asyncio.create_task(start())
            self._stage_tasks[stage_key] = task

            def discard(completed: asyncio.Task[dict[str, Any]]) -> None:
                if self._stage_tasks.get(stage_key) is completed:
                    self._stage_tasks.pop(stage_key, None)

            task.add_done_callback(discard)
        # Cancelling one job must not propagate into work shared with another job.
        # If no subscriber remains, the late valid result may still warm the cache.
        return await asyncio.shield(task)

    async def _resolve_alignment(
        self,
        clip: Clip,
        target: str,
        translation_key: str,
        translation: dict[str, Any],
        retry_failed: bool,
        job_id: str | None,
        cache_checked: bool,
    ) -> TranslationResult:
        if self.alignment_provider is None:
            return self._compose(clip, target, translation, None, alignment_status="unavailable")
        try:
            source_tokens, target_tokens, source_tokenizer, target_tokenizer = self._tokenize(
                clip, target, translation["target_text"]
            )
        except UnsupportedAnalysisError:
            return self._compose(clip, target, translation, None, alignment_status="unavailable")
        key = self.alignment_key(
            clip,
            target,
            translation["target_text"],
            source_tokens,
            target_tokens,
            source_tokenizer,
            target_tokenizer,
        )
        cached = None if cache_checked else self.store.stage_cached("alignment", key)
        if cached and cached[0] == "complete":
            alignment = cast(dict[str, Any], cached[1])
            status: Literal["complete", "failed", "unavailable"] = "complete"
            error_code = None
        elif cached and cached[0] == "failed" and not retry_failed:
            alignment = None
            status = "failed"
            error_code = "invalid_output"
        else:
            try:
                alignment = await self._shared_stage(
                    f"alignment:{key}",
                    lambda: self._align(
                        clip,
                        target,
                        translation_key,
                        translation["target_text"],
                        source_tokens,
                        target_tokens,
                        source_tokenizer,
                        target_tokenizer,
                        key,
                        job_id,
                    ),
                )
                status = "complete"
                error_code = None
            except TranslationProviderError as error:
                alignment = None
                status = "failed"
                error_code = error.code
        return self._compose(
            clip,
            target,
            translation,
            alignment,
            alignment_status=status,
            alignment_error_code=error_code,
            source_tokenizer=source_tokenizer,
            target_tokenizer=target_tokenizer,
        )

    async def _align(
        self,
        clip: Clip,
        target: str,
        translation_key: str,
        target_text: str,
        source_tokens: tuple[AlignmentToken, ...],
        target_tokens: tuple[AlignmentToken, ...],
        source_tokenizer: dict[str, Any],
        target_tokenizer: dict[str, Any],
        key: str,
        job_id: str | None,
    ) -> dict[str, Any]:
        assert self.alignment_provider is not None
        request = ProviderAlignmentRequest(
            clip.source_text,
            target_text,
            clip.source_language,
            target,
            source_tokens,
            target_tokens,
        )
        response: ProviderResponse | None = None
        try:
            async with self._semaphore:
                response = await self.alignment_provider.align(request)
            validated = validate_alignment_output(response, request)
            value = {
                "graph": validated.graph.model_dump(mode="json"),
                "groups": [group.model_dump(mode="json") for group in validated.groups],
                "quality": validated.quality.model_dump(mode="json"),
                "warnings": validated.warnings,
                "latency_ms": response.latency_ms,
                "usage": response.usage,
                "provider_metadata": response.provider_metadata,
            }
            self.store.save_alignment(
                key,
                translation_key,
                clip,
                target,
                _hash(target_text),
                self.alignment_provider.provider,
                self.alignment_provider.model,
                ALIGNMENT_PROMPT_VERSION,
                ALIGNMENT_SCHEMA_VERSION,
                source_tokenizer,
                target_tokenizer,
                value,
            )
            self._attempt("alignment", key, job_id, "complete", response)
            return value
        except InvalidProviderOutput as error:
            self.store.save_alignment(
                key,
                translation_key,
                clip,
                target,
                _hash(target_text),
                self.alignment_provider.provider,
                self.alignment_provider.model,
                ALIGNMENT_PROMPT_VERSION,
                ALIGNMENT_SCHEMA_VERSION,
                source_tokenizer,
                target_tokenizer,
                None,
                status="invalid",
                error=_public_error("invalid_output", retryable=True),
            )
            self._attempt("alignment", key, job_id, "invalid", response, str(error))
            raise
        except TranslationProviderError as error:
            self._attempt("alignment", key, job_id, "failed", response, error.code)
            raise

    def _attempt(
        self,
        stage: str,
        key: str,
        job_id: str | None,
        status: str,
        response: ProviderResponse | None,
        internal_error: str | None = None,
    ) -> None:
        self.store.save_attempt(
            stage,
            key,
            job_id=job_id,
            status=status,
            latency_ms=response.latency_ms if response else None,
            usage=response.usage if response else None,
            provider_metadata=response.provider_metadata if response else None,
            raw_output=response.raw_output if response else None,
            internal_error=internal_error,
        )

    def _compose(
        self,
        clip: Clip,
        target: str,
        translation: dict[str, Any],
        alignment: dict[str, Any] | None,
        *,
        alignment_status: Literal["complete", "failed", "unavailable"],
        alignment_error_code: str | None = None,
        source_tokenizer: dict[str, Any] | None = None,
        target_tokenizer: dict[str, Any] | None = None,
    ) -> TranslationResult:
        graph = WordAlignmentGraph.model_validate(alignment["graph"]) if alignment else None
        groups = (
            [SemanticAlignmentGroup.model_validate(item) for item in alignment["groups"]]
            if alignment
            else []
        )
        quality = AlignmentQuality.model_validate(alignment["quality"]) if alignment else None
        usage: dict[str, int] = defaultdict(int)
        for candidate in (translation.get("usage"), alignment.get("usage") if alignment else None):
            if candidate:
                for name, count in candidate.items():
                    usage[name] += int(count)
        return TranslationResult(
            source_language=clip.source_language,
            target_language=target,
            source_text_hash=_hash(clip.source_text),
            target_text=translation["target_text"],
            alignment_groups=groups,
            alignment_graph=graph,
            alignment_status=alignment_status,
            alignment_error_code=alignment_error_code,
            alignment_quality=quality,
            provenance="llm",
            provider=self.translation_provider.provider if self.translation_provider else "unknown",
            model=self.translation_provider.model if self.translation_provider else None,
            prompt_version=PROMPT_VERSION,
            schema_version=TRANSLATION_SCHEMA_VERSION,
            alignment_prompt_version=ALIGNMENT_PROMPT_VERSION,
            alignment_schema_version=ALIGNMENT_SCHEMA_VERSION,
            alignment_provider=self.alignment_provider.provider
            if self.alignment_provider
            else None,
            alignment_model=self.alignment_provider.model if self.alignment_provider else None,
            source_tokenizer=source_tokenizer,
            target_tokenizer=target_tokenizer,
            warnings=[
                *translation.get("warnings", []),
                *(alignment.get("warnings", []) if alignment else []),
            ],
            latency_ms=round(
                float(translation.get("latency_ms", 0))
                + float(alignment.get("latency_ms", 0) if alignment else 0),
                2,
            ),
            usage=dict(usage) or None,
            provider_metadata=translation.get("provider_metadata"),
        )

    def _authored_or_unavailable(
        self, clip: Clip, target: str, authored: tuple[str, dict[str, str]] | None
    ) -> TranslationJob:
        if authored is None:
            return self.store.create_job(
                clip.segment_id,
                target,
                "unavailable",
                cache_key=None,
                error=TranslationErrorInfo(
                    code="provider_unavailable", message="Translation is unavailable."
                ),
            )
        text, metadata = authored
        result = TranslationResult(
            source_language=clip.source_language,
            target_language=target,
            source_text_hash=_hash(clip.source_text),
            target_text=text,
            alignment_groups=[],
            alignment_status="unavailable",
            provenance="authored_track",
            provider="youtube",
            model=None,
            prompt_version="authored-track-v1",
            schema_version=1,
            authored_track_language=metadata["language"],
            authored_track_id=metadata["track_id"],
            warnings=["Authored caption fallback has no semantic alignment."],
            provider_metadata={"video_id": clip.video.id},
        )
        return self.store.create_job(
            clip.segment_id, target, "complete", cache_key=None, result=result
        )

    def job(self, job_id: str) -> TranslationJob:
        return self.store.job(job_id)

    async def cancel(self, job_id: str) -> TranslationJob:
        job = self.store.job(job_id)
        if job.status not in {"queued", "running"}:
            return job
        self.store.update_job(job_id, "cancelled")
        operation_key = self._job_keys.pop(job_id, "")
        operation = self._operations.get(operation_key)
        if operation:
            operation.subscribers.discard(job_id)
            if not operation.subscribers:
                self._operations.pop(operation_key, None)
                operation.task.cancel()
        return self.store.job(job_id)

    async def create_batch(
        self, segment_ids: list[str], target_language: str, *, retry_failed: bool = False
    ) -> TranslationBatch:
        if not 1 <= len(segment_ids) <= 50:
            raise ValueError("translation batches require between 1 and 50 segment_ids")
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("segment_ids must be unique")
        validated = [self.validate(segment_id, target_language) for segment_id in segment_ids]
        target = validated[0][1]
        jobs = [
            await self.request(clip.segment_id, target, retry_failed=retry_failed)
            for clip, _ in validated
        ]
        return self.store.batch(self.store.create_batch(target, jobs))

    def batch(self, batch_id: str) -> TranslationBatch:
        return self.store.batch(batch_id)

    async def cancel_batch(self, batch_id: str) -> TranslationBatch:
        batch = self.store.batch(batch_id)
        for item in batch.jobs:
            await self.cancel(item.job_id)
        return self.store.batch(batch_id)

    def status(self) -> TranslationServiceStatus:
        return TranslationServiceStatus(
            provider_available=self.translation_provider is not None,
            provider=self.translation_provider.provider if self.translation_provider else None,
            model=self.translation_provider.model if self.translation_provider else None,
            target_languages=list(self.settings.translation_target_languages),
            default_target_language=self.settings.default_target_language,
            cache=self.store.statistics(self.settings.translation_concurrency),
        )

    async def aclose(self) -> None:
        tasks = [
            *[operation.task for operation in self._operations.values()],
            *self._stage_tasks.values(),
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.translation_provider:
            await self.translation_provider.aclose()
        if self.alignment_provider and self.alignment_provider is not self.translation_provider:
            await self.alignment_provider.aclose()


def _public_error(code: str, *, retryable: bool) -> TranslationErrorInfo:
    return TranslationErrorInfo(
        code=code, message="Translation could not be generated.", retryable=retryable
    )
