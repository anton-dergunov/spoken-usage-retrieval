from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, get_args

import pytest
from audio_fixtures import install_caption_video, install_raw_audio, write_wave

EXPERIMENT = Path(__file__).parents[1] / "experiments/audio-caption-reliability"
sys.path.insert(0, str(EXPERIMENT))

import review_app  # noqa: E402
import run_reliability  # noqa: E402
from caption_reliability import (  # noqa: E402
    ACOUSTIC_VOCABULARY,
    RECOMMENDATIONS,
    REVIEW_TAGS,
    Candidate,
    ExperimentConfig,
    ResultRow,
    ReviewRecord,
    ScoreRecord,
    aggregate,
    canonical_checksum,
    filter_candidates,
    load_inventory,
    order_key,
    quantiles,
    review_subset,
    select_sample,
)
from review_app import (  # noqa: E402
    ACOUSTIC_ANCHORS,
    CAPTION_VERDICTS,
    REFERENCE_ASSESSMENTS,
    TAG_ANCHORS,
    render_review_app,
)


def load_committed_config() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        json.loads((EXPERIMENT / "config-v1.json").read_text(encoding="utf-8"))
    ).validated()


def test_the_committed_configuration_is_valid_and_freezes_the_reference_settings():
    config = load_committed_config()

    assert config.config_version == 1
    assert config.sampling.algorithm == "sha256-hash-order-v1"
    assert config.sampling.requested_total == 48
    assert config.asr.model == "large-v3"
    assert config.asr.initial_prompt is None
    assert config.asr.condition_on_previous_text is False
    assert config.asr.language == "es"
    assert config.scoring.orientation == "reference=asr,hypothesis=caption"
    assert config.scoring.normalization["es"] == "word-v1"
    assert config.audio.padding_seconds == 0.0
    assert config.audio.use_segment_clip_range is True
    assert config.authorization.required is True
    assert list(config.recommendations) == list(RECOMMENDATIONS)


def test_confirming_authorization_without_recording_a_basis_is_rejected():
    payload = json.loads((EXPERIMENT / "config-v1.json").read_text(encoding="utf-8"))
    unrecorded = {
        **payload,
        "authorization": {
            "required": True,
            "confirmed": True,
            "basis": None,
            "allowlist_path": None,
        },
    }
    with pytest.raises(ValueError, match="requires a recorded basis"):
        ExperimentConfig.model_validate(unrecorded).validated()

    recorded = {
        **payload,
        "authorization": {
            "required": True,
            "confirmed": True,
            "basis": "operator assessment recorded 2026-09-07",
            "allowlist_path": None,
        },
    }
    assert ExperimentConfig.model_validate(recorded).validated().authorization.confirmed is True

    ExperimentConfig.model_validate(
        {
            **payload,
            "authorization": {
                "required": True,
                "confirmed": False,
                "basis": None,
                "allowlist_path": None,
            },
        }
    ).validated()


@pytest.mark.parametrize(
    ("name", "model"),
    [("config-schema-v1.json", ExperimentConfig), ("result-schema-v1.json", ResultRow)],
)
def test_committed_schemas_do_not_drift_from_the_validated_models(name, model):
    committed = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
    generated = model.model_json_schema()

    assert committed["$schema"].startswith("https://json-schema.org/")
    assert {key: value for key, value in committed.items() if not key.startswith("$")} == {
        key: value for key, value in generated.items() if not key.startswith("$")
    }


def test_configuration_rejects_unknown_normalizers_and_mismatched_source_classes():
    payload = json.loads((EXPERIMENT / "config-v1.json").read_text(encoding="utf-8"))
    broken = {**payload, "scoring": {**payload["scoring"], "normalization": {"es": "made-up"}}}
    with pytest.raises(ValueError, match="unknown normalization"):
        ExperimentConfig.model_validate(broken).validated()

    mismatched = {**payload, "source_classes": ["es/authored"]}
    with pytest.raises(ValueError, match="predeclared source classes"):
        ExperimentConfig.model_validate(mismatched).validated()


def candidate(index: int, *, video="vid_a", provenance="automatic", start=0.0, tokens=8):
    return Candidate(
        segment_id=f"seg_{index:04d}",
        video_key=video,
        video_id="video-1",
        track_id="trk_a",
        source_language="es",
        channel="channel-a",
        caption_provenance=provenance,
        caption_kind="automatic" if provenance == "automatic" else "manual",
        text=f"frase numero {index}",
        token_count=tokens,
        start=start,
        end=start + 3.0,
        clip_start=start,
        clip_end=start + 3.0,
        quality_score=0.8,
        boundary_reason="punctuation",
        boundary_confidence=1.0,
    )


def sampling_config(**overrides):
    payload = json.loads((EXPERIMENT / "config-v1.json").read_text(encoding="utf-8"))
    payload["sampling"] = {**payload["sampling"], **overrides}
    return ExperimentConfig.model_validate(payload).validated().sampling


def test_stable_hash_ordering_does_not_depend_on_iteration_order():
    keys = [order_key(7, f"seg_{index}") for index in range(5)]

    assert keys == [order_key(7, f"seg_{index}") for index in range(5)]
    assert order_key(8, "seg_0") != order_key(7, "seg_0")
    assert len(set(keys)) == 5


def test_sampling_is_deterministic_for_a_seed_and_changes_with_it():
    pool = [candidate(index, start=index * 20.0) for index in range(200)]
    sampling = sampling_config(per_video_cap=50)
    other_seed = sampling_config(seed=999, per_video_cap=50)

    first, report = select_sample(pool, sampling)
    again, _ = select_sample(pool, sampling)
    other, _ = select_sample(pool, other_seed)

    assert [item.segment_id for item in first] == [item.segment_id for item in again]
    assert [item.segment_id for item in first] != [item.segment_id for item in other]
    assert sorted(item.segment_id for item in first) != sorted(item.segment_id for item in other)
    assert report["seed"] == sampling.seed
    assert report["selected_total"] == 24


def test_sampling_reports_missing_strata_instead_of_silently_substituting():
    pool = [candidate(index, start=index * 20.0) for index in range(3)]
    sampling = sampling_config(per_video_cap=50)

    selected, report = select_sample(pool, sampling)

    assert len(selected) == 3
    assert report["complete"] is False
    authored = next(item for item in report["strata"] if item["id"] == "es/authored")
    assert authored["selected"] == 0 and authored["missing"] == 24
    assert authored["eligible_segments"] == 0
    automatic = next(item for item in report["strata"] if item["id"] == "es/automatic")
    assert automatic["missing"] == 21


def test_sampling_enforces_the_per_video_cap_and_rejects_near_duplicate_neighbours():
    pool = [candidate(index, start=index * 20.0) for index in range(50)]
    capped, report = select_sample(pool, sampling_config(per_video_cap=3))
    assert len(capped) == 3
    assert report["funnel"]["rejected_per_video_cap"] > 0

    clustered = [candidate(index, start=index * 1.0) for index in range(50)]
    spaced, spaced_report = select_sample(clustered, sampling_config(per_video_cap=10))
    spans = sorted((item.clip_start, item.clip_end) for item in spaced)
    assert spaced_report["funnel"]["rejected_overlapping_neighbour"] > 0
    assert all(
        spans[index + 1][0] >= spans[index][1] + 5.0 - 3.0 for index in range(len(spans) - 1)
    )


def test_eligibility_funnel_counts_every_rejection_reason():
    pool = [
        candidate(1, tokens=1),
        candidate(2, start=100.0),
        candidate(3, start=200.0),
    ]
    pool[1] = replace(pool[1], clip_end=pool[1].clip_start + 0.5)
    pool[2] = replace(pool[2], clip_end=pool[2].clip_start + 60.0)

    eligible, funnel = filter_candidates(pool, sampling_config())

    assert eligible == []
    assert funnel["rejected_too_few_tokens"] == 1
    assert funnel["rejected_too_short"] == 1
    assert funnel["rejected_too_long"] == 1
    assert funnel["candidates"] == 3 and funnel["eligible"] == 0


def build_corpus(tmp_path: Path, *, videos=2, segments_per_video=3):
    """A tiny caption index plus ready source audio, entirely generated."""
    rows = []
    for video_index in range(videos):
        provider_video_id = f"video-{video_index}"
        caption_kind = "manual" if video_index == 0 else "automatic"
        key = install_caption_video(
            tmp_path,
            provider_video_id=provider_video_id,
            channel=f"channel-{video_index}",
            caption_kind=caption_kind,
        )
        source = write_wave(tmp_path / f"{provider_video_id}.wav", seconds=60.0)
        install_raw_audio(
            tmp_path,
            key=key,
            provider_video_id=provider_video_id,
            source=source,
            duration=60.0,
        )
        from speech_retrieval.identity import segment_id, track_id

        track = track_id(key, caption_kind, "es")
        for index in range(segments_per_video):
            start = 1.0 + index * 10.0
            text = f"esta es la frase numero {index} del video {video_index}"
            rows.append(
                {
                    "id": segment_id(
                        provider_video_id=provider_video_id,
                        source_language="es",
                        track=track,
                        start=start,
                        end=start + 3.0,
                        text=text,
                    ),
                    "video_key": key,
                    "video_id": provider_video_id,
                    "source_language": "es",
                    "track_id": track,
                    "text": text,
                    "start": start,
                    "end": start + 3.0,
                    "clip_start": start,
                    "clip_end": start + 3.0,
                    "boundary_reason": "punctuation",
                    "boundary_confidence": 1.0,
                    "quality_score": 0.9,
                    "token_count": len(text.split()),
                }
            )
    path = tmp_path / "derived" / "corpora" / "es" / "segments.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    return rows


def test_inventory_joins_indexed_segments_with_their_caption_provenance(tmp_path):
    build_corpus(tmp_path)

    candidates = load_inventory(tmp_path, ["es"])

    assert len(candidates) == 6
    assert {item.caption_provenance for item in candidates} == {"authored", "automatic"}
    assert {item.channel for item in candidates} == {"channel-0", "channel-1"}
    assert all(item.token_count > 0 for item in candidates)


@dataclass
class FakeTranscriber:
    """A deterministic stand-in so experiment I/O is testable without a model."""

    provenance: dict[str, Any] = field(
        default_factory=lambda: {"backend": "fake", "model": "large-v3-fixture"}
    )
    failures: set[str] = field(default_factory=set)
    calls: list[Path] = field(default_factory=list)

    def transcribe(self, path: Path):
        self.calls.append(path)
        if path.name in self.failures:
            raise RuntimeError("model forward failed")
        return run_reliability.Transcription(
            text="esta es la frase numero 0 del video mal transcrito",
            segments=[{"start": 0.0, "end": 3.0, "text": "esta es la frase"}],
            words=[{"word": "esta", "start": 0.0, "end": 0.2, "probability": 0.9}],
            language="es",
            language_probability=0.99,
        )


def experiment_args(tmp_path, **overrides):
    from types import SimpleNamespace

    defaults = {
        "config": EXPERIMENT / "config-v1.json",
        "data_dir": tmp_path,
        "run_root": tmp_path / "runs",
        "run_id": "test-run",
        "results": tmp_path / "results.json",
        "worksheet": None,
        "output": None,
        "force": False,
        "retry_failed": False,
        "no_embed_audio": False,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def small_config(tmp_path, **sampling_overrides):
    payload = json.loads((EXPERIMENT / "config-v1.json").read_text(encoding="utf-8"))
    payload["sampling"].update(
        {"per_video_cap": 3, "minimum_gap_seconds": 1.0, "minimum_tokens": 4, **sampling_overrides}
    )
    for stratum in payload["sampling"]["strata"]:
        stratum["requested"] = 3
    payload["authorization"]["confirmed"] = True
    payload["authorization"]["basis"] = "test fixture, generated audio only"
    payload["features"]["squim_objective"] = False
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_frozen_sample_is_never_silently_replaced(tmp_path, capsys):
    build_corpus(tmp_path)
    config_path = small_config(tmp_path)
    config = run_reliability.load_config(config_path)
    args = experiment_args(tmp_path, config=config_path)

    assert run_reliability.command_sample(args, config) == 0
    capsys.readouterr()
    first = (tmp_path / "runs" / "test-run" / "sample.jsonl").read_text()

    with pytest.raises(SystemExit, match="never silently replaced"):
        run_reliability.command_sample(args, config)
    assert (tmp_path / "runs" / "test-run" / "sample.jsonl").read_text() == first

    snapshot = json.loads((tmp_path / "runs" / "test-run" / "sample.json").read_text())
    assert snapshot["sample_sha256"] == canonical_checksum(snapshot["segment_ids"])
    assert snapshot["config_sha256"] == canonical_checksum(config.model_dump(mode="json"))
    assert len(snapshot["segment_ids"]) == 6


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="the end-to-end experiment run uses local ffmpeg",
)
def test_the_pipeline_runs_end_to_end_and_records_every_stage(tmp_path, capsys):
    build_corpus(tmp_path)
    config_path = small_config(tmp_path)
    config = run_reliability.load_config(config_path)
    args = experiment_args(tmp_path, config=config_path)
    run_root = tmp_path / "runs" / "test-run"
    transcriber = FakeTranscriber()

    assert run_reliability.command_sample(args, config) == 0
    assert run_reliability.command_prepare(args, config) == 0
    assert run_reliability.command_transcribe(args, config, transcriber) == 0
    assert run_reliability.command_score(args, config) == 0
    assert run_reliability.command_features(args, config) == 0
    capsys.readouterr()

    rows = run_reliability.read_rows(run_root / "features.jsonl")
    assert len(rows) == 6
    assert all(row.clip_key and row.clip_sha256 for row in rows)
    assert all(row.status == "complete" for row in rows)
    assert all(row.score is not None for row in rows)
    assert all(row.sensitivity_score is not None for row in rows)
    assert {row.reference_provenance for row in rows} == {"asr"}
    features = {item["feature"] for row in rows for item in row.features}
    assert {"caption_asr_agreement", "speaking_rate", "speech_ratio"} <= features
    vad = [item for row in rows for item in row.features if item["feature"] == "speech_ratio"]
    assert all(item["status"] in {"complete", "unavailable", "failed"} for item in vad)

    assert run_reliability.command_report(args, config) == 0
    capsys.readouterr()
    results = json.loads(args.results.read_text())
    assert results["run_id"] == "test-run"
    assert results["summary"]["overall"]["segments"] == 6
    assert results["summary"]["overall"]["videos"] == 2
    assert set(results["recommendations"]) == set(config.source_classes)
    assert results["asr_provenance"]["model"] == "large-v3-fixture"
    assert results["complete"] is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requires local ffmpeg")
def test_a_failed_stage_is_recorded_against_the_same_row_and_can_be_resumed(tmp_path, capsys):
    build_corpus(tmp_path)
    config_path = small_config(tmp_path)
    config = run_reliability.load_config(config_path)
    args = experiment_args(tmp_path, config=config_path)
    run_root = tmp_path / "runs" / "test-run"

    run_reliability.command_sample(args, config)
    run_reliability.command_prepare(args, config)
    prepared = run_reliability.read_rows(run_root / "clips.jsonl")
    doomed = Path(prepared[0].clip_key or "")
    failing = FakeTranscriber(failures={"clip.wav"})
    run_reliability.command_transcribe(args, config, failing)
    capsys.readouterr()

    failed = run_reliability.read_rows(run_root / "asr.jsonl")
    assert [row.segment_id for row in failed] == [row.segment_id for row in prepared]
    assert all(row.status == "asr_failed" for row in failed)
    assert all(row.error and row.error["stage"] == "transcribe" for row in failed)
    assert doomed.name != ""

    run_reliability.command_transcribe(
        experiment_args(tmp_path, config=config_path, retry_failed=True),
        config,
        FakeTranscriber(),
    )
    capsys.readouterr()
    recovered = run_reliability.read_rows(run_root / "asr.jsonl")
    assert [row.segment_id for row in recovered] == [row.segment_id for row in prepared]
    assert all(row.asr_text for row in recovered)


def test_prepare_records_missing_audio_without_dropping_the_row(tmp_path, capsys):
    build_corpus(tmp_path, videos=1, segments_per_video=2)
    config_path = small_config(tmp_path)
    config = run_reliability.load_config(config_path)
    args = experiment_args(tmp_path, config=config_path)
    run_reliability.command_sample(args, config)
    for path in (tmp_path / "raw" / "corpora" / "es").rglob("audio"):
        shutil.rmtree(path)

    assert run_reliability.command_prepare(args, config) == 0
    capsys.readouterr()

    rows = run_reliability.read_rows(tmp_path / "runs" / "test-run" / "clips.jsonl")
    assert len(rows) == 2
    assert all(row.status == "missing_audio" for row in rows)
    assert all(row.clip_key is None for row in rows)


def scored_row(index, *, stratum, error_rate, verdict=None, status="complete"):
    return ResultRow(
        run_id="run",
        stratum=stratum,
        segment_id=f"seg_{index:04d}",
        video_key=f"vid_{index % 3}",
        video_id="video",
        track_id="trk",
        source_language="es",
        channel=f"channel-{index % 2}",
        caption_provenance="authored" if stratum.endswith("authored") else "automatic",
        caption_kind="manual",
        caption_text="texto",
        requested_start=0.0,
        requested_end=2.0,
        status=status,
        score=None
        if error_rate is None
        else ScoreRecord(
            metric="wer",
            normalization_version="word-v1",
            error_rate=error_rate,
            agreement=max(0.0, 1 - error_rate),
            hits=5,
            substitutions=1,
            deletions=0,
            insertions=0,
            reference_length=6,
            hypothesis_length=6,
            normalized_reference="a",
            normalized_hypothesis="b",
        ),
        review=None
        if verdict is None
        else ReviewRecord(
            reviewer="reviewer-1",
            rubric_version="caption-review-v1",
            reviewed_at="2026-09-07T00:00:00+00:00",
            caption_verdict=verdict,
            reference_assessment="equivalent",
        ),
    )


def test_aggregation_keeps_denominators_and_distributions_per_source_class():
    config = load_committed_config()
    rows = [
        scored_row(index, stratum="es/authored", error_rate=0.05 * index) for index in range(5)
    ] + [
        scored_row(10 + index, stratum="es/automatic", error_rate=0.4, verdict="incorrect")
        for index in range(3)
    ]
    rows.append(scored_row(99, stratum="es/automatic", error_rate=None, status="missing_audio"))

    summary = aggregate(rows, config)

    assert summary["overall"]["segments"] == 9
    assert summary["overall"]["scored_segments"] == 8
    assert summary["overall"]["statuses"]["missing_audio"] == 1
    authored = summary["by_source_class"]["es/authored"]
    assert authored["segments"] == 5 and authored["videos"] == 3
    assert authored["disagreement"]["median"] == pytest.approx(0.1)
    automatic = summary["by_source_class"]["es/automatic"]
    assert automatic["segments"] == 4 and automatic["scored_segments"] == 3
    assert automatic["reviewed_segments"] == 3
    assert automatic["confirmed_caption_errors"] == 3
    assert automatic["at_or_above"]["0.3"] == 3
    assert authored["at_or_above"]["0.3"] == 0
    assert set(summary["by_channel"]) == {"channel-0", "channel-1"}
    assert "declared ASR reference" in summary["interpretation"]


def test_quantiles_report_a_full_distribution_rather_than_a_mean_alone():
    assert quantiles([])["count"] == 0
    assert quantiles([])["median"] is None
    values = [0.0, 0.1, 0.2, 0.3, 0.4]
    result = quantiles(values)
    assert (result["min"], result["median"], result["max"]) == (0.0, 0.2, 0.4)
    assert result["p90"] == pytest.approx(0.36)
    assert result["mean"] == pytest.approx(0.2)


def test_the_review_subset_is_predeclared_and_always_includes_failures():
    config = load_committed_config()
    rows = [
        scored_row(index, stratum="es/authored", error_rate=0.01 * index) for index in range(20)
    ]
    rows.append(scored_row(50, stratum="es/authored", error_rate=None, status="asr_failed"))

    subset = review_subset(rows, config.review, config.sampling.seed)

    assert "seg_0050" in subset
    assert len(subset) == 2 * config.review.per_bin_per_stratum + 1
    assert subset == review_subset(rows, config.review, config.sampling.seed)
    assert review_subset(rows, config.review.model_copy(update={"subset": "all"}), 1) == sorted(
        row.segment_id for row in rows
    )


def embedded_payload(document: str) -> str:
    match = re.search(
        r'<script type="application/json" id="payload">(.*?)</script>', document, re.S
    )
    assert match is not None, "the review page must embed its worksheet payload"
    return match.group(1)


def worksheet_fixture(tmp_path, *, with_clip=True):
    clip = write_wave(tmp_path / "clip.wav", seconds=0.5) if with_clip else tmp_path / "gone.wav"
    return {
        "run_id": "test-run",
        "rubric_version": "caption-review-v1",
        "review_tags": list(REVIEW_TAGS),
        "acoustic_vocabulary": list(ACOUSTIC_VOCABULARY),
        "reviewed": 1,
        "total": 2,
        "predeclared_subset": "failed_plus_stratified_bins",
        "items": [
            {
                "segment_id": "seg_0001",
                "stratum": "es/authored",
                "video_key": "vid_a",
                "channel": "channel-a",
                "clip": str(clip),
                "caption_text": "hola <b>qué</b> tal",
                "asr_text": "hola que tal",
                "error_rate": 0.33,
                "status": "complete",
                "caption_verdict": None,
                "reference_assessment": None,
                "tags": [],
                "acoustic_tags": [],
                "reviewer": None,
                "reviewed_at": None,
                "note": None,
                "corrected_transcript": None,
            }
        ],
    }


def test_every_review_vocabulary_term_has_an_anchor_a_reviewer_can_act_on():
    assert set(TAG_ANCHORS) == set(REVIEW_TAGS)
    assert set(ACOUSTIC_ANCHORS) == set(ACOUSTIC_VOCABULARY)
    assert all(len(anchor) > 30 for anchor in TAG_ANCHORS.values())
    assert all(len(anchor) > 30 for anchor in ACOUSTIC_ANCHORS.values())


def test_review_options_match_the_recorded_review_schema():
    verdicts = get_args(ReviewRecord.model_fields["caption_verdict"].annotation)
    assessments = get_args(ReviewRecord.model_fields["reference_assessment"].annotation)

    assert [value for value, _label, _anchor in CAPTION_VERDICTS] == list(verdicts)
    assert [value for value, _label, _anchor in REFERENCE_ASSESSMENTS] == list(assessments)
    assert all(anchor for _value, _label, anchor in CAPTION_VERDICTS)
    assert all(anchor for _value, _label, anchor in REFERENCE_ASSESSMENTS)


def test_the_review_page_is_one_self_contained_document_with_its_clips_embedded(tmp_path):
    worksheet = worksheet_fixture(tmp_path)

    document = render_review_app(worksheet)

    assert document.startswith("<!doctype html>")
    assert document.rstrip().endswith("</html>")
    payload = json.loads(embedded_payload(document))
    assert payload["audio"]["seg_0001"].startswith("data:audio/wav;base64,")
    assert payload["embeddedBytes"] == (tmp_path / "clip.wav").stat().st_size
    assert [item["id"] for item in payload["tags"]] == list(REVIEW_TAGS)
    assert [item["id"] for item in payload["acoustics"]] == list(ACOUSTIC_VOCABULARY)
    assert "</script>" not in payload["worksheet"]["items"][0]["caption_text"]
    assert "http://" not in document and "https://" not in document


def test_a_row_without_a_prepared_clip_is_rendered_but_not_reviewable(tmp_path):
    document = render_review_app(worksheet_fixture(tmp_path, with_clip=False))

    payload = json.loads(embedded_payload(document))
    assert payload["audio"] == {}
    assert payload["embeddedBytes"] == 0
    assert payload["worksheet"]["items"][0]["segment_id"] == "seg_0001"


def test_embedding_stops_at_the_size_cap_instead_of_producing_an_unusable_page(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(review_app, "MAX_EMBEDDED_BYTES", 10)

    audio, total = review_app.collect_audio(worksheet_fixture(tmp_path))

    assert audio == {} and total == 0


def test_the_review_page_round_trips_into_an_importable_worksheet(tmp_path, capsys):
    build_corpus(tmp_path, videos=1, segments_per_video=2)
    config_path = small_config(tmp_path)
    config = run_reliability.load_config(config_path)
    args = experiment_args(tmp_path, config=config_path)
    run_root = tmp_path / "runs" / "test-run"
    run_reliability.command_sample(args, config)
    rows = run_reliability.read_rows(run_root / "sample.jsonl")
    scored = [
        row.model_copy(
            update={
                "status": "complete",
                "asr_text": "esta es la frase",
                "clip_key": "clp_" + "a" * 20,
                "effective_start": 1.0,
                "effective_end": 4.0,
                "score": ScoreRecord(
                    metric="wer",
                    normalization_version="word-v1",
                    error_rate=0.25,
                    agreement=0.75,
                    hits=3,
                    substitutions=1,
                    deletions=0,
                    insertions=0,
                    reference_length=4,
                    hypothesis_length=4,
                    normalized_reference="a",
                    normalized_hypothesis="b",
                ),
            }
        )
        for row in rows
    ]
    run_reliability.write_rows(run_root / "scored.jsonl", scored)

    assert run_reliability.command_review_export(args, config) == 0
    assert run_reliability.command_review_html(args, config) == 0
    capsys.readouterr()
    assert (run_root / "review.html").is_file()

    worksheet = json.loads((run_root / "review-worksheet.json").read_text())
    assert worksheet["items"][0]["duration"] == 3.0
    assert worksheet["items"][0]["status"] == "complete"
    for item in worksheet["items"]:
        item.update(
            caption_verdict="incorrect",
            reference_assessment="asr_better",
            tags=["meaning_change"],
            acoustic_tags=["fast_speech"],
            reviewer="reviewer-1",
            reviewed_at="2026-09-07T00:00:00+00:00",
        )
    filled = run_root / "filled.json"
    filled.write_text(json.dumps(worksheet), encoding="utf-8")

    assert (
        run_reliability.command_review_import(
            experiment_args(tmp_path, config=config_path, worksheet=filled), config
        )
        == 0
    )
    capsys.readouterr()

    reviewed = run_reliability.read_rows(run_root / "reviewed.jsonl")
    assert all(row.review is not None for row in reviewed)
    assert {row.review.caption_verdict for row in reviewed if row.review} == {"incorrect"}
    assert {tuple(row.acoustic_tags) for row in reviewed} == {("fast_speech",)}
    summary = aggregate(reviewed, config)
    assert summary["overall"]["confirmed_caption_errors"] == len(reviewed)
    assert summary["by_acoustic_tag"]["fast_speech"]["segments"] == len(reviewed)
