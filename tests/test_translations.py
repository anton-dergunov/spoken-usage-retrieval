import asyncio
import json
import sqlite3
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from test_index_search_api import indexed_data

from speech_retrieval import CharacterRange, Settings, create_app
from speech_retrieval import translations as translation_module
from speech_retrieval.search import Corpus
from speech_retrieval.translation_store import TranslationStore
from speech_retrieval.translations import (
    InvalidProviderOutput,
    ProviderAlignmentRequest,
    ProviderAlignmentResponse,
    ProviderTranslationRequest,
    ProviderTranslationResponse,
    TranslationProviderError,
    TranslationService,
    alignment_tokens,
    validate_alignment_output,
    validate_translation_output,
)


@dataclass
class FakeProvider:
    provider: str = "fake"
    model: str = "fixed-token-v1"
    translation_calls: int = 0
    alignment_calls: int = 0
    delay: float = 0
    invalid_translation: bool = False
    invalid_alignment: bool = False

    async def translate(self, request: ProviderTranslationRequest) -> ProviderTranslationResponse:
        self.translation_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return ProviderTranslationResponse(
            payload={
                "target_text": ""
                if self.invalid_translation
                else f"Translation {request.target_language}",
                "warnings": [],
            },
            latency_ms=2.5,
            usage={"total_tokens": 10},
            raw_output='{"target_text":"fixture"}',
        )

    async def align(self, request: ProviderAlignmentRequest) -> ProviderAlignmentResponse:
        self.alignment_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        rows = [
            {
                "source_id": source.id,
                "target_ids": [
                    request.target_tokens[min(index, len(request.target_tokens) - 1)].id
                ],
            }
            for index, source in enumerate(request.source_tokens)
        ]
        if self.invalid_alignment:
            rows = rows[:-1]
        linked = {target for row in rows for target in row["target_ids"]}
        return ProviderAlignmentResponse(
            payload={
                "alignments": rows,
                "unaligned_target_ids": [
                    token.id for token in request.target_tokens if token.id not in linked
                ],
                "warnings": [],
            },
            latency_ms=3.5,
            usage={"total_tokens": 12},
            raw_output='{"alignments":[]}',
        )

    async def aclose(self) -> None:
        return None


async def wait_for_job(service: TranslationService, job_id: str):
    for _ in range(200):
        job = service.job(job_id)
        if job.status not in {"queued", "running"}:
            return job
        await asyncio.sleep(0.005)
    raise AssertionError("translation job did not finish")


def alignment_request() -> ProviderAlignmentRequest:
    source = 'Digo, "disfrutemos, disfrutemos."'
    target = "I say, \"let's enjoy, let's enjoy.\""
    return ProviderAlignmentRequest(
        source, target, "es", "en", alignment_tokens(source, "S"), alignment_tokens(target, "T")
    )


def test_stage_validators_build_fixed_token_graph_with_unicode_ranges():
    translation = ProviderTranslationResponse(
        payload={"target_text": "A useful translation.", "warnings": []},
        latency_ms=1,
        usage=None,
        raw_output="{}",
    )
    assert validate_translation_output(translation).target_text == "A useful translation."
    with pytest.raises(InvalidProviderOutput):
        validate_translation_output(
            translation.__class__(
                payload={"target_text": "", "warnings": []},
                latency_ms=1,
                usage=None,
                raw_output="{}",
            )
        )

    request = alignment_request()
    source_ids = [token.id for token in request.source_tokens]
    target_ids = [token.id for token in request.target_tokens]
    rows = [
        {"source_id": source_ids[0], "target_ids": target_ids[0:2]},
        {"source_id": source_ids[1], "target_ids": target_ids[2:4]},
        {"source_id": source_ids[2], "target_ids": target_ids[4:6]},
    ]
    result = validate_alignment_output(
        ProviderAlignmentResponse(
            payload={"alignments": rows, "unaligned_target_ids": [], "warnings": []},
            latency_ms=1,
            usage=None,
            raw_output="{}",
        ),
        request,
    )
    assert result.graph.edges[-2].source_token_id == source_ids[2]
    assert result.graph.edges[-2].target_token_id == target_ids[4]
    assert result.groups[1].target_ranges == [
        request.target_tokens[2].range,
        request.target_tokens[3].range,
    ]
    assert result.quality.source_token_coverage == 1
    assert result.quality.target_token_coverage == 1


def test_alignment_validation_rejects_incomplete_rows_and_token_accounting():
    request = alignment_request()
    rows = [{"source_id": token.id, "target_ids": []} for token in request.source_tokens]
    with pytest.raises(InvalidProviderOutput):
        validate_alignment_output(
            ProviderAlignmentResponse(
                payload={"alignments": rows[:-1], "unaligned_target_ids": [], "warnings": []},
                latency_ms=1,
                usage=None,
                raw_output="{}",
            ),
            request,
        )
    with pytest.raises(InvalidProviderOutput):
        validate_alignment_output(
            ProviderAlignmentResponse(
                payload={
                    "alignments": [
                        *rows[:-1],
                        {"source_id": rows[-1]["source_id"], "target_ids": ["T999"]},
                    ],
                    "unaligned_target_ids": [],
                    "warnings": [],
                },
                latency_ms=1,
                usage=None,
                raw_output="{}",
            ),
            request,
        )


def test_alignment_tokens_deduplicate_shared_spans():
    tokens = alignment_tokens(
        "🙂 uno dos",
        "S",
        analyzed=[
            {"start": 2, "end": 5},
            {"start": 2, "end": 5},
            {"start": 6, "end": 9},
        ],
    )
    assert [(token.id, token.text, token.range) for token in tokens] == [
        ("S1", "uno", CharacterRange(start=2, end=5)),
        ("S2", "dos", CharacterRange(start=6, end=9)),
    ]


def service_fixture(tmp_path, provider):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
    corpus = Corpus(settings)
    segment_id = corpus.search("la verdad", source_language="es").results[0].segment_id
    return settings, corpus, segment_id, TranslationService.configured(settings, corpus, provider)


def test_service_coalesces_stages_and_persists_across_restart(tmp_path):
    async def exercise():
        provider = FakeProvider(delay=0.02)
        settings, corpus, segment_id, service = service_fixture(tmp_path, provider)
        first = await service.request(segment_id, "en")
        second = await service.request(segment_id, "en")
        done = await asyncio.gather(
            wait_for_job(service, first.job_id), wait_for_job(service, second.job_id)
        )
        assert all(job.result and job.result.alignment_status == "complete" for job in done)
        assert (provider.translation_calls, provider.alignment_calls) == (1, 1)
        cached = await service.request(segment_id, "en")
        assert cached.status == "complete" and cached.cache_hit
        assert service.status().cache.provider_attempts == 2
        await service.aclose()
        corpus.close()

        from speech_retrieval.indexing import build_index

        build_index(data_dir=settings.data_dir)
        restarted_provider = FakeProvider()
        corpus = Corpus(settings)
        restarted = TranslationService.configured(settings, corpus, restarted_provider)
        persisted = await restarted.request(segment_id, "en")
        assert persisted.status == "complete" and persisted.cache_hit
        assert (restarted_provider.translation_calls, restarted_provider.alignment_calls) == (0, 0)
        assert {entry["stage"] for entry in restarted.store.entries(model="fixed-token-v1")} == {
            "translation",
            "alignment",
        }
        assert restarted.store.prune(target_language="en") == 2
        assert restarted.status().cache.completed_entries == 0
        await restarted.aclose()
        corpus.close()

    asyncio.run(exercise())


def test_stage_keys_change_with_model_prompt_tokens_and_tokenizer(tmp_path, monkeypatch):
    provider = FakeProvider()
    _, corpus, _, service = service_fixture(tmp_path, provider)
    clip = corpus.clip(corpus.search("la verdad", source_language="es").results[0].segment_id)
    translation_key = service.translation_key(clip, "en", None)
    assert (
        service.translation_key(
            clip.model_copy(update={"source_text": clip.source_text + "!"}), "en", None
        )
        != translation_key
    )
    provider.model = "fixed-token-v2"
    assert service.translation_key(clip, "en", None) != translation_key
    provider.model = "fixed-token-v1"
    monkeypatch.setattr(translation_module, "PROMPT_VERSION", "literal-translation-v2")
    assert service.translation_key(clip, "en", None) != translation_key
    monkeypatch.setattr(translation_module, "PROMPT_VERSION", "literal-translation-v1")
    monkeypatch.setattr(translation_module, "TRANSLATION_SCHEMA_VERSION", 2)
    assert service.translation_key(clip, "en", None) != translation_key
    monkeypatch.setattr(translation_module, "TRANSLATION_SCHEMA_VERSION", 1)

    source = alignment_tokens(clip.source_text, "S")
    target = alignment_tokens("A translation", "T")
    source_tokenizer = {"identity": "source-v1"}
    target_tokenizer = {"identity": "target-v1"}
    baseline = service.alignment_key(
        clip,
        "en",
        "A translation",
        source,
        target,
        source_tokenizer,
        target_tokenizer,
    )
    provider.model = "fixed-token-v2"
    assert (
        service.alignment_key(
            clip,
            "en",
            "A translation",
            source,
            target,
            source_tokenizer,
            target_tokenizer,
        )
        != baseline
    )
    provider.model = "fixed-token-v1"
    monkeypatch.setattr(translation_module, "ALIGNMENT_PROMPT_VERSION", "alignment-v2")
    assert (
        service.alignment_key(
            clip,
            "en",
            "A translation",
            source,
            target,
            source_tokenizer,
            target_tokenizer,
        )
        != baseline
    )
    monkeypatch.setattr(translation_module, "ALIGNMENT_PROMPT_VERSION", "fixed-token-alignment-v1")
    monkeypatch.setattr(translation_module, "ALIGNMENT_SCHEMA_VERSION", 2)
    assert (
        service.alignment_key(
            clip,
            "en",
            "A translation",
            source,
            target,
            source_tokenizer,
            target_tokenizer,
        )
        != baseline
    )
    monkeypatch.setattr(translation_module, "ALIGNMENT_SCHEMA_VERSION", 1)
    assert (
        service.alignment_key(
            clip,
            "en",
            "A translation",
            source,
            target,
            source_tokenizer,
            {"identity": "target-v2"},
        )
        != baseline
    )
    assert (
        service.alignment_key(
            clip,
            "en",
            "A translation!",
            source,
            alignment_tokens("A translation!", "T"),
            source_tokenizer,
            target_tokenizer,
        )
        != baseline
    )
    corpus.close()


def test_failed_alignment_keeps_text_and_retry_only_realigns(tmp_path):
    async def exercise():
        provider = FakeProvider(invalid_alignment=True)
        _, corpus, segment_id, service = service_fixture(tmp_path, provider)
        job = await wait_for_job(service, (await service.request(segment_id, "en")).job_id)
        assert job.status == "complete" and job.result
        assert job.result.target_text == "Translation en"
        assert job.result.alignment_status == "failed"
        assert job.result.alignment_error_code == "invalid_output"
        with sqlite3.connect(service.store.path) as connection:
            attempt = connection.execute(
                "SELECT raw_output, internal_error FROM provider_attempts WHERE stage = 'alignment'"
            ).fetchone()
        assert attempt and attempt[0] == '{"alignments":[]}'
        assert "every source token" in attempt[1].lower()
        cached = await service.request(segment_id, "en")
        assert cached.cache_hit and cached.result and cached.result.alignment_status == "failed"
        provider.invalid_alignment = False
        retried = await wait_for_job(
            service, (await service.request(segment_id, "en", retry_failed=True)).job_id
        )
        assert retried.result and retried.result.alignment_status == "complete"
        assert (provider.translation_calls, provider.alignment_calls) == (1, 2)
        await service.aclose()
        corpus.close()

    asyncio.run(exercise())


def test_invalid_translation_is_cached_until_explicit_retry(tmp_path):
    async def exercise():
        provider = FakeProvider(invalid_translation=True)
        _, corpus, segment_id, service = service_fixture(tmp_path, provider)
        failed = await wait_for_job(service, (await service.request(segment_id, "en")).job_id)
        assert failed.status == "failed" and failed.error and failed.error.code == "invalid_output"
        repeated = await service.request(segment_id, "en")
        assert repeated.status == "failed" and repeated.cache_hit
        provider.invalid_translation = False
        retried = await wait_for_job(
            service, (await service.request(segment_id, "en", retry_failed=True)).job_id
        )
        assert retried.status == "complete"
        assert (provider.translation_calls, provider.alignment_calls) == (2, 1)
        await service.aclose()
        corpus.close()

    asyncio.run(exercise())


def test_temporary_failure_is_retryable_and_never_leaks_provider_text(tmp_path):
    @dataclass
    class FlakyProvider(FakeProvider):
        fail_once: bool = True

        async def translate(self, request):
            if self.fail_once:
                self.fail_once = False
                self.translation_calls += 1
                raise TranslationProviderError(
                    "temporarily_unavailable", "private fixture", retryable=True
                )
            return await super().translate(request)

    async def exercise():
        provider = FlakyProvider()
        _, corpus, segment_id, service = service_fixture(tmp_path, provider)
        failed = await wait_for_job(service, (await service.request(segment_id, "en")).job_id)
        assert failed.status == "failed" and failed.error and "private" not in failed.error.message
        completed = await wait_for_job(service, (await service.request(segment_id, "en")).job_id)
        assert completed.status == "complete"
        assert (provider.translation_calls, provider.alignment_calls) == (2, 1)
        await service.aclose()
        corpus.close()

    asyncio.run(exercise())


def test_cancellation_isolated_and_restart_interrupts_jobs(tmp_path):
    async def exercise():
        provider = FakeProvider(delay=0.02)
        _, corpus, segment_id, service = service_fixture(tmp_path, provider)
        first = await service.request(segment_id, "ru")
        second = await service.request(segment_id, "ru")
        assert (await service.cancel(first.job_id)).status == "cancelled"
        assert (await wait_for_job(service, second.job_id)).status == "complete"
        assert (provider.translation_calls, provider.alignment_calls) == (1, 1)
        await service.aclose()
        corpus.close()

    asyncio.run(exercise())
    store = TranslationStore(tmp_path / "standalone.sqlite3")
    queued = store.create_job("segment", "en", "queued", cache_key="key")
    restarted = TranslationStore(tmp_path / "standalone.sqlite3", recover_unfinished=True)
    assert restarted.job(queued.job_id).status == "interrupted"


def test_joint_cache_schema_is_cleanly_invalidated(tmp_path):
    path = tmp_path / "translations.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE translation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO translation_meta VALUES ('schema_version', '1');
            CREATE TABLE cache_entries (cache_key TEXT PRIMARY KEY);
            INSERT INTO cache_entries VALUES ('obsolete');
        """)
    TranslationStore(path)
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = connection.execute(
            "SELECT value FROM translation_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "2"
    assert "cache_entries" not in tables


def test_api_batch_and_authored_fallback(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
    provider = FakeProvider()
    with TestClient(create_app(settings, translation_provider=provider)) as client:
        results = client.get("/api/v1/search", params={"q": "la verdad", "language": "es"}).json()[
            "results"
        ]
        segment_ids = [item["segment_id"] for item in results[:2]]
        response = client.post(
            "/api/v1/translation-batches",
            json={
                "segment_ids": segment_ids,
                "target_language": "ru",
                "retry_failed": False,
            },
        )
        assert response.status_code == 202
        for _ in range(100):
            batch = client.get(f"/api/v1/translation-batches/{response.json()['batch_id']}").json()
            if batch["counts"]["complete"] == len(segment_ids):
                break
        assert batch["counts"]["complete"] == len(segment_ids)
        assert (
            client.post(
                "/api/v1/translation-batches",
                json={
                    "segment_ids": [segment_ids[0], segment_ids[0]],
                    "target_language": "ru",
                },
            ).status_code
            == 400
        )

    with Corpus(settings) as corpus:
        clip = corpus.clip(segment_ids[0])
    video_dir = data_dir / "raw" / "corpora" / "es" / clip.video.video_key
    target_track = video_dir / "authored-en"
    target_track.mkdir()
    (target_track / "subtitles.raw.json3").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "tStartMs": int(clip.sentence_start * 1000),
                        "dDurationMs": int((clip.sentence_end - clip.sentence_start) * 1000),
                        "segs": [{"utf8": "The truth is useful."}],
                    }
                ]
            }
        )
    )
    (video_dir / "manifest.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "track_id": "authored-en",
                        "language": "en",
                        "kind": "authored",
                        "is_source": False,
                        "status": "downloaded",
                        "content_sha256": "fixture",
                    }
                ]
            }
        )
    )
    with TestClient(create_app(settings)) as client:
        fallback = client.post(
            f"/api/v1/clips/{segment_ids[0]}/translations", json={"target_language": "en"}
        ).json()
        assert fallback["result"]["provenance"] == "authored_track"
        assert fallback["result"]["alignment_status"] == "unavailable"
        unavailable = client.post(
            f"/api/v1/clips/{segment_ids[0]}/translations", json={"target_language": "de"}
        ).json()
        assert unavailable["status"] == "unavailable"
