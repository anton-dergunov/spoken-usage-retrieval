import asyncio
import json
from dataclasses import dataclass

from fastapi.testclient import TestClient
from test_index_search_api import indexed_data

from speech_retrieval import CharacterRange, Settings, create_app
from speech_retrieval import translations as translation_module
from speech_retrieval.translations import (
    ProviderTranslationRequest,
    ProviderTranslationResponse,
    TranslationProviderError,
    TranslationService,
    TranslationStore,
    validate_provider_output,
)


@dataclass
class FakeProvider:
    provider: str = "fake"
    model: str = "literal-v1"
    calls: int = 0
    delay: float = 0

    async def generate(self, request: ProviderTranslationRequest) -> ProviderTranslationResponse:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return ProviderTranslationResponse(
            payload={
                "source_chunks": [{"text": request.source_text, "group_id": 1}],
                "target_chunks": [
                    {"text": f"Translation {request.target_language}", "group_id": 1}
                ],
                "warnings": [],
            },
            latency_ms=2.5,
            usage={"total_tokens": 10},
            raw_output="{}",
        )

    async def aclose(self) -> None:
        return None


@dataclass
class InvalidProvider(FakeProvider):
    async def generate(self, request: ProviderTranslationRequest) -> ProviderTranslationResponse:
        self.calls += 1
        return ProviderTranslationResponse(
            payload={
                "source_chunks": [{"text": "changed source", "group_id": 1}],
                "target_chunks": [{"text": "translation", "group_id": 1}],
                "warnings": [],
            },
            latency_ms=1,
            usage=None,
            raw_output='{"source_chunks": "invalid fixture"}',
        )


@dataclass
class FlakyProvider(FakeProvider):
    failed_once: bool = False

    async def generate(self, request: ProviderTranslationRequest) -> ProviderTranslationResponse:
        if "buena idea" in request.source_text and not self.failed_once:
            self.calls += 1
            self.failed_once = True
            raise TranslationProviderError(
                "temporarily_unavailable", "temporary fixture failure", retryable=True
            )
        return await super().generate(request)


async def wait_for_job(service: TranslationService, job_id: str):
    for _ in range(100):
        job = service.job(job_id)
        if job.status not in {"queued", "running"}:
            return job
        await asyncio.sleep(0.005)
    raise AssertionError("translation job did not finish")


def test_chunk_output_derives_unicode_ranges_and_reordered_groups():
    request = ProviderTranslationRequest(
        source_text="🙂 uno dos", source_language="es", target_language="en"
    )
    response = ProviderTranslationResponse(
        payload={
            "source_chunks": [
                {"text": "🙂 ", "group_id": 0},
                {"text": "uno", "group_id": 1},
                {"text": " ", "group_id": 0},
                {"text": "dos", "group_id": 2},
            ],
            "target_chunks": [
                {"text": "two", "group_id": 2},
                {"text": " ", "group_id": 0},
                {"text": "one", "group_id": 1},
            ],
            "warnings": [],
        },
        latency_ms=1,
        usage=None,
        raw_output="{}",
    )
    result = validate_provider_output(response, request, provider="fake", model="test")
    assert result.target_text == "two one"
    assert result.alignment_groups[0].source_ranges[0].start == 2
    assert result.alignment_groups[0].target_ranges[0].start == 4
    assert result.alignment_groups[1].target_ranges[0].start == 0


def test_one_sided_groups_fail_without_a_repair_call():
    request = ProviderTranslationRequest(
        source_text="uno dos", source_language="es", target_language="en"
    )
    response = ProviderTranslationResponse(
        payload={
            "source_chunks": [
                {"text": "uno", "group_id": 1},
                {"text": " dos", "group_id": 2},
            ],
            "target_chunks": [
                {"text": "one", "group_id": 1},
                {"text": " extra", "group_id": 3},
            ],
            "warnings": [],
        },
        latency_ms=1,
        usage=None,
        raw_output="{}",
    )
    from speech_retrieval.translations import InvalidTranslationOutput

    try:
        validate_provider_output(response, request, provider="fake", model="test")
    except InvalidTranslationOutput as error:
        assert "both source and target" in str(error)
    else:
        raise AssertionError("one-sided semantic groups must be rejected")


def test_empty_chunks_are_ignored_without_changing_text_or_ranges():
    request = ProviderTranslationRequest(
        source_text="uno", source_language="es", target_language="en"
    )
    response = ProviderTranslationResponse(
        payload={
            "source_chunks": [
                {"text": "", "group_id": 0},
                {"text": "uno", "group_id": 1},
            ],
            "target_chunks": [
                {"text": "one", "group_id": 1},
                {"text": "", "group_id": 0},
            ],
            "warnings": [],
        },
        latency_ms=1,
        usage=None,
        raw_output="{}",
    )
    result = validate_provider_output(response, request, provider="fake", model="test")
    assert result.target_text == "one"
    assert result.alignment_groups[0].target_ranges[0] == CharacterRange(start=0, end=3)


def test_translation_service_coalesces_and_persists_cache(tmp_path):
    async def exercise():
        data_dir, catalogue_dir = indexed_data(tmp_path)
        settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
        from speech_retrieval.search import Corpus

        corpus = Corpus(settings)
        segment_id = corpus.search("la verdad", source_language="es").results[0].segment_id
        provider = FakeProvider(delay=0.02)
        service = TranslationService.configured(settings, corpus, provider)
        first = await service.request(segment_id, "en")
        batch = await service.create_batch([segment_id], "en")
        second = service.job(batch.jobs[0].job_id)
        first_done, second_done = await asyncio.gather(
            wait_for_job(service, first.job_id), wait_for_job(service, second.job_id)
        )
        assert provider.calls == 1
        assert first_done.status == second_done.status == "complete"
        assert service.batch(batch.batch_id).counts.complete == 1
        cached = await service.request(segment_id, "en")
        assert cached.status == "complete"
        assert cached.cache_hit is True
        assert provider.calls == 1
        assert service.status().cache.completed_entries == 1
        await service.aclose()
        corpus.close()

        from speech_retrieval.indexing import build_index

        build_index(data_dir=data_dir)
        second_corpus = Corpus(settings)
        second_provider = FakeProvider()
        second_service = TranslationService.configured(settings, second_corpus, second_provider)
        persisted = await second_service.request(segment_id, "en")
        assert persisted.status == "complete"
        assert persisted.cache_hit is True
        assert second_provider.calls == 0
        second_provider.model = "literal-v2"
        changed_model = await second_service.request(segment_id, "en")
        changed_model = await wait_for_job(second_service, changed_model.job_id)
        assert changed_model.status == "complete"
        assert changed_model.cache_hit is False
        assert second_provider.calls == 1
        listed = second_service.store.entries(model="literal-v1")
        assert len(listed) == 1
        assert listed[0]["target_language"] == "en"
        assert "result_json" not in listed[0]
        assert second_service.store.prune(model="literal-v1") == 1
        assert second_service.status().cache.completed_entries == 1
        assert second_service.store.prune(target_language="en") == 1
        assert second_service.status().cache.completed_entries == 0
        await second_service.aclose()
        second_corpus.close()

    asyncio.run(exercise())


def test_cache_key_changes_with_source_model_prompt_and_schema(tmp_path, monkeypatch):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
    from speech_retrieval.search import Corpus

    corpus = Corpus(settings)
    clip = corpus.clip(corpus.search("la verdad", source_language="es").results[0].segment_id)
    provider = FakeProvider()
    service = TranslationService.configured(settings, corpus, provider)
    baseline = service._cache_key(clip, "en")
    assert (
        service._cache_key(clip.model_copy(update={"source_text": clip.source_text + "!"}), "en")
        != baseline
    )
    provider.model = "literal-v2"
    assert service._cache_key(clip, "en") != baseline
    provider.model = "literal-v1"
    monkeypatch.setattr(translation_module, "PROMPT_VERSION", "next-prompt")
    assert service._cache_key(clip, "en") != baseline
    monkeypatch.setattr(translation_module, "PROMPT_VERSION", "literal-chunks-v3")
    monkeypatch.setattr(translation_module, "TRANSLATION_SCHEMA_VERSION", 2)
    assert service._cache_key(clip, "en") != baseline
    corpus.close()


def test_active_jobs_become_interrupted_when_the_store_restarts(tmp_path):
    path = tmp_path / "translations.sqlite3"
    first = TranslationStore(path)
    queued = first.create_job("seg_fixture", "en", "queued", cache_key="fixture")
    running = first.create_job("seg_fixture", "ru", "running", cache_key="fixture-ru")

    inspector = TranslationStore(path)
    assert inspector.job(queued.job_id).status == "queued"
    restarted = TranslationStore(path, recover_unfinished=True)

    assert restarted.job(queued.job_id).status == "interrupted"
    assert restarted.job(running.job_id).status == "interrupted"


def test_cancelling_one_subscriber_does_not_cancel_shared_generation(tmp_path):
    async def exercise():
        data_dir, catalogue_dir = indexed_data(tmp_path)
        settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
        from speech_retrieval.search import Corpus

        corpus = Corpus(settings)
        segment_id = corpus.search("la verdad", source_language="es").results[0].segment_id
        provider = FakeProvider(delay=0.03)
        service = TranslationService.configured(settings, corpus, provider)
        first = await service.request(segment_id, "ru")
        second = await service.request(segment_id, "ru")
        cancelled = await service.cancel(first.job_id)
        completed = await wait_for_job(service, second.job_id)
        assert cancelled.status == "cancelled"
        assert completed.status == "complete"
        assert provider.calls == 1
        await service.aclose()
        corpus.close()

    asyncio.run(exercise())


def test_batch_reports_partial_failure_and_retries_temporary_errors(tmp_path):
    async def exercise():
        data_dir, catalogue_dir = indexed_data(tmp_path)
        settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
        from speech_retrieval.search import Corpus

        corpus = Corpus(settings)
        results = corpus.search("la verdad", source_language="es").results
        segment_ids = [
            next(item.segment_id for item in results if "buena idea" in item.sentence),
            next(item.segment_id for item in results if "funciona" in item.sentence),
        ]
        provider = FlakyProvider()
        service = TranslationService.configured(settings, corpus, provider)
        batch = await service.create_batch(segment_ids, "en")
        jobs = [await wait_for_job(service, item.job_id) for item in batch.jobs]
        current = service.batch(batch.batch_id)
        assert current.counts.complete == 1
        assert current.counts.failed == 1
        failed = next(job for job in jobs if job.status == "failed")
        assert failed.error and failed.error.retryable is True
        retried = await service.request(failed.segment_id, "en")
        retried = await wait_for_job(service, retried.job_id)
        assert retried.status == "complete"
        assert retried.cache_hit is False
        assert provider.calls == 3
        await service.aclose()
        corpus.close()

    asyncio.run(exercise())


def test_invalid_provider_output_is_diagnosable_and_not_retried(tmp_path):
    async def exercise():
        data_dir, catalogue_dir = indexed_data(tmp_path)
        settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
        from speech_retrieval.search import Corpus

        corpus = Corpus(settings)
        segment_id = corpus.search("la verdad", source_language="es").results[0].segment_id
        provider = InvalidProvider()
        service = TranslationService.configured(settings, corpus, provider)
        first = await service.request(segment_id, "en")
        failed = await wait_for_job(service, first.job_id)
        assert failed.status == "failed"
        assert failed.error and failed.error.code == "invalid_output"
        repeated = await service.request(segment_id, "en")
        assert repeated.status == "failed"
        assert repeated.cache_hit is True
        assert provider.calls == 1
        assert service.status().cache.invalid_entries == 1
        await service.aclose()
        corpus.close()

    asyncio.run(exercise())


def test_api_translation_batch_cache_and_no_provider_fallback(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
    provider = FakeProvider()
    with TestClient(create_app(settings, translation_provider=provider)) as client:
        results = client.get("/api/v1/search", params={"q": "la verdad", "language": "es"}).json()[
            "results"
        ]
        segment_ids = [item["segment_id"] for item in results[:2]]
        batch = client.post(
            "/api/v1/translation-batches",
            json={"segment_ids": segment_ids, "target_language": "ru"},
        )
        assert batch.status_code == 202
        jobs = batch.json()["jobs"]
        for _ in range(100):
            current = client.get(f"/api/v1/translation-batches/{batch.json()['batch_id']}").json()
            if current["counts"]["complete"] == len(segment_ids):
                break
        assert current["counts"]["complete"] == len(segment_ids)
        assert current["counts"]["total"] == len(segment_ids)
        assert current["counts"]["cached"] == 0
        assert all(
            client.get(f"/api/v1/translations/{item['job_id']}").status_code == 200 for item in jobs
        )
        duplicate = client.post(
            "/api/v1/translation-batches",
            json={"segment_ids": [segment_ids[0], segment_ids[0]], "target_language": "ru"},
        )
        assert duplicate.status_code == 400

    from speech_retrieval.search import Corpus

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
        assert fallback["status"] == "complete"
        assert fallback["result"]["provenance"] == "authored_track"
        assert fallback["result"]["target_text"] == "The truth is useful."
        unavailable = client.post(
            f"/api/v1/clips/{segment_ids[0]}/translations", json={"target_language": "de"}
        )
        assert unavailable.status_code == 202
        assert unavailable.json()["status"] == "unavailable"
        status = client.get("/api/v1/status").json()["translation"]
        assert status["provider_available"] is False
        assert status["target_languages"] == ["en", "ru"]
