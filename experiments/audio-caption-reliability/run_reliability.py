#!/usr/bin/env python3
"""Preflight, sample, prepare, transcribe, score, review, and report Plan 09's benchmark.

Every stage checkpoints after each item, so an interrupted long run loses at most the item
in flight. Stage failures are recorded against the frozen sample row; the sample is never
silently resampled because media or a model was unavailable.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from caption_reliability import (
    ACOUSTIC_VOCABULARY,
    RECOMMENDATIONS,
    Candidate,
    ExperimentConfig,
    ResultRow,
    ReviewRecord,
    ScoreRecord,
    aggregate,
    canonical_checksum,
    load_inventory,
    review_subset,
    select_sample,
    stratum_of,
)
from review_app import render_review_app

from speech_retrieval.audio import (
    AudioCacheError,
    PreparedClip,
    audio_availability,
    audio_storage,
    prepare_clip,
    tool_version,
)
from speech_retrieval.audio_features import (
    SileroSettings,
    SubjectiveReference,
    caption_agreement,
    speaking_rate,
    speech_ratio,
    squim_objective,
    squim_subjective,
)
from speech_retrieval.audio_scoring import (
    DisagreementScore,
    get_normalizer,
    score_disagreement,
)

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
DEFAULT_CONFIG = HERE / "config-v1.json"
DEFAULT_DATA_DIR = REPOSITORY / "data"
DEFAULT_RUN_ROOT = REPOSITORY / "data/experiments/audio-caption-reliability"
DEFAULT_RESULTS = HERE / "results.json"
OPTIONAL_PACKAGES = ("faster_whisper", "silero_vad", "torch", "torchaudio", "jiwer")


def now() -> str:
    return datetime.now(UTC).isoformat()


def load_config(path: Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    ).validated()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_rows(path: Path, rows: Sequence[ResultRow]) -> None:
    """Atomic snapshot write so an interrupted run never leaves a half-written stage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row.model_dump(mode="json"), ensure_ascii=False) + "\n")
        stream.flush()
    temporary.replace(path)


def read_rows(path: Path) -> list[ResultRow]:
    if not Path(path).is_file():
        return []
    rows: list[ResultRow] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            row = ResultRow.model_validate(json.loads(line))
            if row.segment_id in seen:
                raise SystemExit(f"duplicate segment in {path}: {row.segment_id}")
            seen.add(row.segment_id)
            rows.append(row)
    return rows


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name.replace("_", "-"))
    except importlib.metadata.PackageNotFoundError:
        return None


def preflight(config: ExperimentConfig, data_dir: Path) -> dict[str, Any]:
    """Report readiness without hiding a missing stratum, tool, model, or authorization."""
    candidates = load_inventory(data_dir, config.source_languages)
    _selected, sample_report = select_sample(candidates, config.sampling)
    storage = audio_storage(data_dir, languages=config.source_languages)
    tools = {
        name: {
            "path": shutil.which(name),
            "version": tool_version(name) if shutil.which(name) else None,
        }
        for name in ("ffmpeg", "ffprobe")
    }
    packages = {
        name: {
            "installed": importlib.util.find_spec(name) is not None,
            "version": package_version(name),
        }
        for name in OPTIONAL_PACKAGES
    }
    blockers: list[str] = []
    if not candidates:
        blockers.append("no indexed segments were found; build the corpus index first")
    if not sample_report["complete"]:
        missing = [item["id"] for item in sample_report["strata"] if item["missing"]]
        blockers.append(f"strata below their requested count: {', '.join(missing)}")
    for name, entry in tools.items():
        if not entry["version"]:
            blockers.append(f"{name} is not available")
    if not packages["faster_whisper"]["installed"]:
        blockers.append("faster-whisper is not installed; install the audio-experiments extra")
    if storage.ready == 0:
        blockers.append("no video has ready source audio; run an audio-enabled update first")
    if config.authorization.required and not config.authorization.confirmed:
        blockers.append(
            "the operator has not recorded an authorization basis for downloading these sources"
        )
    return {
        "generated_at": now(),
        "ready": not blockers,
        "blockers": blockers,
        "sample": sample_report,
        "audio": {
            "videos": storage.videos,
            "ready": storage.ready,
            "missing": storage.missing,
            "failed": storage.failed,
            "raw_bytes": storage.raw_bytes,
            "derived_bytes": storage.derived_bytes,
            "issues": len(storage.issues),
        },
        "tools": tools,
        "packages": packages,
        "authorization": config.authorization.model_dump(mode="json"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }


def row_from_candidate(candidate: Candidate, config: ExperimentConfig, run_id: str) -> ResultRow:
    stratum = stratum_of(candidate, config.sampling)
    assert stratum is not None
    start, end = (
        (candidate.clip_start, candidate.clip_end)
        if config.audio.use_segment_clip_range
        else (candidate.start, candidate.end)
    )
    return ResultRow(
        run_id=run_id,
        stratum=stratum.id,
        segment_id=candidate.segment_id,
        video_key=candidate.video_key,
        video_id=candidate.video_id,
        track_id=candidate.track_id,
        source_language=candidate.source_language,
        channel=candidate.channel,
        caption_provenance=candidate.caption_provenance,
        caption_kind=candidate.caption_kind,
        caption_text=candidate.text,
        quality_score=candidate.quality_score,
        boundary_reason=candidate.boundary_reason,
        boundary_confidence=candidate.boundary_confidence,
        token_count=candidate.token_count,
        speech_style=list(candidate.speech_style),
        requested_start=start,
        requested_end=end,
        preparation_version=config.audio.preparation_version,
    )


def command_sample(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / args.run_id
    sample_path = run_root / "sample.jsonl"
    if sample_path.exists() and not args.force:
        raise SystemExit(
            f"{sample_path} already exists; a frozen sample is never silently replaced "
            "(pass --force only to create a deliberately new sample version)"
        )
    candidates = load_inventory(args.data_dir, config.source_languages)
    selected, report = select_sample(candidates, config.sampling)
    rows = [row_from_candidate(candidate, config, args.run_id) for candidate in selected]
    write_rows(sample_path, rows)
    snapshot = {
        "run_id": args.run_id,
        "created_at": now(),
        "config_sha256": canonical_checksum(config.model_dump(mode="json")),
        "config": config.model_dump(mode="json"),
        "selection": report,
        "segment_ids": [row.segment_id for row in rows],
    }
    snapshot["sample_sha256"] = canonical_checksum(snapshot["segment_ids"])
    write_json(run_root / "sample.json", snapshot)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


def stage_rows(run_root: Path, stage: str) -> list[ResultRow]:
    existing = read_rows(run_root / f"{stage}.jsonl")
    if existing:
        return existing
    previous = {
        "clips": "sample",
        "asr": "clips",
        "scored": "asr",
        "features": "scored",
    }[stage]
    rows = read_rows(run_root / f"{previous}.jsonl")
    if not rows:
        raise SystemExit(f"{run_root / (previous + '.jsonl')} is missing; run that stage first")
    return rows


def command_prepare(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / args.run_id
    rows = stage_rows(run_root, "clips")
    output = run_root / "clips.jsonl"
    for index, row in enumerate(rows):
        if row.clip_key and row.status != "pending" and not args.retry_failed:
            continue
        record = audio_availability(
            args.data_dir, language=row.source_language, video_key=row.video_key
        )
        if not record.ready:
            rows[index] = row.model_copy(
                update={
                    "status": "missing_audio",
                    "error": {"stage": "prepare", "message": record.error or record.status},
                }
            )
            write_rows(output, rows)
            continue
        try:
            clip: PreparedClip = prepare_clip(
                args.data_dir,
                language=row.source_language,
                video_key=row.video_key,
                start=row.requested_start,
                end=row.requested_end,
                padding=config.audio.padding_seconds,
                duration_tolerance_ms=config.audio.duration_tolerance_ms,
                preparation_version=config.audio.preparation_version,
            )
        except (AudioCacheError, ValueError) as error:
            rows[index] = row.model_copy(
                update={
                    "status": "clip_failed",
                    "error": {"stage": "prepare", "message": str(error)},
                }
            )
        else:
            rows[index] = row.model_copy(
                update={
                    "status": "pending",
                    "error": None,
                    "clip_key": clip.clip_key,
                    "clip_sha256": clip.content_sha256,
                    "effective_start": clip.effective_start,
                    "effective_end": clip.effective_end,
                    "padding_before": clip.padding_before,
                    "padding_after": clip.padding_after,
                    "preparation_version": clip.preparation_version,
                }
            )
        write_rows(output, rows)
    print(json.dumps(stage_counts(rows, "clip_key"), ensure_ascii=False, indent=2))
    return 0


def stage_counts(rows: Sequence[ResultRow], field_name: str) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for row in rows:
        statuses[row.status] = statuses.get(row.status, 0) + 1
    return {
        "rows": len(rows),
        "with_value": sum(1 for row in rows if getattr(row, field_name)),
        "statuses": dict(sorted(statuses.items())),
    }


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    words: list[dict[str, Any]] = field(default_factory=list)
    language: str | None = None
    language_probability: float | None = None


class Transcriber(Protocol):
    """The narrow seam experiment I/O and tests use instead of a heavyweight backend."""

    provenance: dict[str, Any]

    def transcribe(self, path: Path) -> Transcription: ...


class FasterWhisperTranscriber:
    """Whisper large-v3-class reference. Backend defaults are never inherited implicitly."""

    def __init__(self, settings: Any) -> None:
        from faster_whisper import WhisperModel

        started = time.perf_counter()
        self._settings = settings
        self._model = WhisperModel(
            settings.model,
            device=settings.device,
            compute_type=settings.compute_type,
            cpu_threads=settings.cpu_threads,
            num_workers=settings.num_workers,
        )
        self.provenance = {
            "backend": "faster-whisper",
            "backend_version": package_version("faster_whisper"),
            "ctranslate2_version": package_version("ctranslate2"),
            "torch_version": package_version("torch"),
            "model": settings.model,
            "revision": settings.revision,
            "device": settings.device,
            "compute_type": settings.compute_type,
            "settings": settings.model_dump(mode="json"),
            "model_load_seconds": time.perf_counter() - started,
            "note": (
                "faster-whisper decoding defaults differ from OpenAI Whisper; this is a "
                "large-v3-class reference implementation, not backend-equivalent output"
            ),
        }

    def transcribe(self, path: Path) -> Transcription:
        settings = self._settings
        segments, info = self._model.transcribe(
            str(path),
            language=settings.language,
            task=settings.task,
            beam_size=settings.beam_size,
            best_of=settings.best_of,
            patience=settings.patience,
            temperature=settings.temperature,
            condition_on_previous_text=settings.condition_on_previous_text,
            vad_filter=settings.vad_filter,
            word_timestamps=settings.word_timestamps,
            initial_prompt=settings.initial_prompt,
        )
        materialized = list(segments)
        words: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        for item in materialized:
            rows.append(
                {
                    "id": getattr(item, "id", None),
                    "start": item.start,
                    "end": item.end,
                    "text": item.text,
                    "avg_logprob": getattr(item, "avg_logprob", None),
                    "no_speech_prob": getattr(item, "no_speech_prob", None),
                    "compression_ratio": getattr(item, "compression_ratio", None),
                }
            )
            for word in getattr(item, "words", None) or ():
                words.append(
                    {
                        "word": word.word,
                        "start": word.start,
                        "end": word.end,
                        "probability": getattr(word, "probability", None),
                    }
                )
        return Transcription(
            text="".join(item.text for item in materialized).strip(),
            segments=rows,
            words=words,
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
        )


def command_transcribe(
    args: argparse.Namespace,
    config: ExperimentConfig,
    transcriber: Transcriber | None = None,
) -> int:
    run_root = args.run_root / args.run_id
    rows = stage_rows(run_root, "asr")
    output = run_root / "asr.jsonl"
    pending = [
        row
        for row in rows
        if row.clip_key
        and (row.asr_text is None or (args.retry_failed and row.status == "asr_failed"))
    ]
    if not pending:
        print(json.dumps(stage_counts(rows, "asr_text"), ensure_ascii=False, indent=2))
        return 0
    backend = transcriber if transcriber is not None else FasterWhisperTranscriber(config.asr)
    for index, row in enumerate(rows):
        if row not in pending:
            continue
        clip_path = clip_path_for(args.data_dir, row)
        if clip_path is None or not clip_path.is_file():
            rows[index] = row.model_copy(
                update={
                    "status": "clip_failed",
                    "error": {"stage": "transcribe", "message": "prepared clip is missing"},
                }
            )
            write_rows(output, rows)
            continue
        started = time.perf_counter()
        try:
            result = backend.transcribe(clip_path)
        except Exception as error:  # noqa: BLE001 - the failure is recorded, not swallowed
            rows[index] = row.model_copy(
                update={
                    "status": "asr_failed",
                    "asr_runtime_ms": (time.perf_counter() - started) * 1000,
                    "error": {"stage": "transcribe", "message": str(error)},
                }
            )
        else:
            rows[index] = row.model_copy(
                update={
                    "status": "pending",
                    "error": None,
                    "asr_text": result.text,
                    "asr_segments": result.segments,
                    "asr_words": result.words,
                    "asr_runtime_ms": (time.perf_counter() - started) * 1000,
                    "asr_provenance": {
                        **backend.provenance,
                        "detected_language": result.language,
                        "detected_language_probability": result.language_probability,
                    },
                }
            )
        write_rows(output, rows)
    print(json.dumps(stage_counts(rows, "asr_text"), ensure_ascii=False, indent=2))
    return 0


def clip_path_for(data_dir: Path, row: ResultRow) -> Path | None:
    if not row.clip_key:
        return None
    from speech_retrieval.audio import prepared_clip_paths

    return prepared_clip_paths(
        data_dir,
        language=row.source_language,
        video_key=row.video_key,
        clip_key=row.clip_key,
    ).clip


def to_score_record(score: DisagreementScore) -> ScoreRecord:
    return ScoreRecord(
        metric=score.metric,
        normalization_version=score.normalization_version,
        error_rate=score.error_rate,
        agreement=score.agreement,
        hits=score.hits,
        substitutions=score.substitutions,
        deletions=score.deletions,
        insertions=score.insertions,
        reference_length=score.reference_length,
        hypothesis_length=score.hypothesis_length,
        normalized_reference=score.normalized_reference,
        normalized_hypothesis=score.normalized_hypothesis,
        alignment=[chunk.payload() for chunk in score.alignment],
    )


def command_score(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / args.run_id
    rows = stage_rows(run_root, "scored")
    output = run_root / "scored.jsonl"
    for index, row in enumerate(rows):
        if row.asr_text is None:
            continue
        version = config.scoring.normalization[row.source_language]
        score = score_disagreement(
            reference_text=row.asr_text,
            hypothesis_text=row.caption_text,
            normalization_version=version,
        )
        update: dict[str, Any] = {
            "score": to_score_record(score),
            "status": "complete",
            "error": None,
            "reference_provenance": "asr",
        }
        sensitivity = config.scoring.sensitivity_normalization.get(row.source_language)
        if sensitivity:
            update["sensitivity_score"] = to_score_record(
                score_disagreement(
                    reference_text=row.asr_text,
                    hypothesis_text=row.caption_text,
                    normalization_version=sensitivity,
                )
            )
        rows[index] = row.model_copy(update=update)
        write_rows(output, rows)
    write_rows(output, rows)
    print(json.dumps(stage_counts(rows, "score"), ensure_ascii=False, indent=2))
    return 0


def command_features(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / args.run_id
    rows = stage_rows(run_root, "features")
    output = run_root / "features.jsonl"
    settings = config.features
    reference = None
    if settings.subjective_reference is not None:
        reference = SubjectiveReference(
            path=Path(settings.subjective_reference.path),
            source=settings.subjective_reference.source,
            license=settings.subjective_reference.license,
            content_sha256=settings.subjective_reference.content_sha256,
        )
    silero = SileroSettings(**settings.silero.model_dump(mode="python"))
    for index, row in enumerate(rows):
        clip = loaded_clip(args.data_dir, row)
        results: list[dict[str, Any]] = []
        if settings.caption_agreement and row.score is not None and row.asr_text is not None:
            results.append(
                caption_agreement(
                    row.segment_id,
                    score_disagreement(
                        reference_text=row.asr_text,
                        hypothesis_text=row.caption_text,
                        normalization_version=row.score.normalization_version,
                    ),
                    clip=clip,
                ).payload()
            )
        voiced: float | None = None
        if settings.speech_ratio and clip is not None:
            vad = speech_ratio(row.segment_id, clip, settings=silero)
            results.append(vad.payload())
            voiced = vad.values.get("speech_seconds") if vad.status == "complete" else None
        if settings.speaking_rate and clip is not None:
            normalizer = get_normalizer(config.scoring.normalization[row.source_language])
            results.append(
                speaking_rate(
                    row.segment_id,
                    caption_tokens=len(normalizer.tokenize(row.caption_text)),
                    asr_tokens=len(normalizer.tokenize(row.asr_text or "")),
                    clip_duration=clip.duration,
                    speech_seconds=voiced,
                    clip=clip,
                    tokenizer_version=normalizer.version,
                    language=row.source_language,
                ).payload()
            )
        if settings.squim_objective and clip is not None:
            results.append(squim_objective(row.segment_id, clip).payload())
        if settings.squim_subjective and clip is not None:
            results.append(squim_subjective(row.segment_id, clip, reference=reference).payload())
        rows[index] = row.model_copy(update={"features": results})
        write_rows(output, rows)
    write_rows(output, rows)
    print(json.dumps(stage_counts(rows, "features"), ensure_ascii=False, indent=2))
    return 0


def loaded_clip(data_dir: Path, row: ResultRow) -> Any:
    path = clip_path_for(data_dir, row)
    if path is None or not path.is_file() or row.effective_start is None:
        return None

    @dataclass(frozen=True)
    class LoadedClip:
        path: Path
        clip_key: str
        content_sha256: str
        effective_start: float
        effective_end: float
        duration: float
        sample_rate: int
        channels: int

    return LoadedClip(
        path=path,
        clip_key=row.clip_key or "",
        content_sha256=row.clip_sha256 or "",
        effective_start=row.effective_start,
        effective_end=row.effective_end or row.effective_start,
        duration=(row.effective_end or row.effective_start) - row.effective_start,
        sample_rate=16_000,
        channels=1,
    )


def command_review_export(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / args.run_id
    rows = read_rows(run_root / "scored.jsonl") or read_rows(run_root / "features.jsonl")
    if not rows:
        raise SystemExit("no scored rows are available to review")
    subset = set(review_subset(rows, config.review, config.sampling.seed))
    worksheet: list[dict[str, Any]] = [
        {
            "segment_id": row.segment_id,
            "stratum": row.stratum,
            "video_key": row.video_key,
            "channel": row.channel,
            "clip": str(clip_path_for(args.data_dir, row) or ""),
            "caption_text": row.caption_text,
            "asr_text": row.asr_text,
            "effective_start": row.effective_start,
            "effective_end": row.effective_end,
            "duration": (
                None
                if row.effective_start is None or row.effective_end is None
                else round(row.effective_end - row.effective_start, 3)
            ),
            "status": row.status,
            "error_rate": row.score.error_rate if row.score else None,
            "rubric_version": config.review.rubric_version,
            "caption_verdict": None,
            "reference_assessment": None,
            "tags": [],
            "acoustic_tags": [],
            "reviewer": None,
            "reviewed_at": None,
            "note": None,
            "corrected_transcript": None,
        }
        for row in rows
        if row.segment_id in subset
    ]
    path = args.output or run_root / "review-worksheet.json"
    write_json(
        path,
        {
            "run_id": args.run_id,
            "rubric_version": config.review.rubric_version,
            "review_tags": config.review_tags,
            "acoustic_vocabulary": config.acoustic_vocabulary,
            "reviewed": len(worksheet),
            "total": len(rows),
            "predeclared_subset": config.review.subset,
            "items": worksheet,
        },
    )
    print(json.dumps({"worksheet": str(path), "reviewed": len(worksheet), "total": len(rows)}))
    return 0


def command_review_html(args: argparse.Namespace, _config: ExperimentConfig) -> int:
    """Render the worksheet as one standalone HTML page with the clips embedded."""
    run_root = args.run_root / args.run_id
    source = Path(args.worksheet) if args.worksheet else run_root / "review-worksheet.json"
    if not source.is_file():
        raise SystemExit(f"{source} is missing; run review-export first")
    worksheet = json.loads(source.read_text(encoding="utf-8"))
    document = render_review_app(worksheet, embed_audio=not args.no_embed_audio)
    target = args.output or run_root / "review.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    reviewable = sum(1 for item in worksheet["items"] if Path(item.get("clip") or "").is_file())
    print(
        json.dumps(
            {
                "page": str(target),
                "bytes": target.stat().st_size,
                "items": len(worksheet["items"]),
                "reviewable": reviewable,
                "without_audio": len(worksheet["items"]) - reviewable,
            },
            ensure_ascii=False,
        )
    )
    return 0


def command_review_import(args: argparse.Namespace, _config: ExperimentConfig) -> int:
    run_root = args.run_root / args.run_id
    source = run_root / "features.jsonl"
    rows = read_rows(source) or read_rows(run_root / "scored.jsonl")
    if not rows:
        raise SystemExit("no rows are available to attach a review to")
    worksheet = json.loads(Path(args.worksheet).read_text(encoding="utf-8"))
    decisions = {
        item["segment_id"]: item
        for item in worksheet["items"]
        if item.get("caption_verdict") and item.get("reviewer")
    }
    imported = 0
    for index, row in enumerate(rows):
        decision = decisions.get(row.segment_id)
        if decision is None:
            continue
        rows[index] = row.model_copy(
            update={
                "review": ReviewRecord(
                    reviewer=decision["reviewer"],
                    rubric_version=decision.get("rubric_version", worksheet["rubric_version"]),
                    reviewed_at=decision.get("reviewed_at") or now(),
                    caption_verdict=decision["caption_verdict"],
                    reference_assessment=decision["reference_assessment"],
                    tags=list(decision.get("tags") or ()),
                    acoustic_tags=list(decision.get("acoustic_tags") or ()),
                    note=decision.get("note"),
                    corrected_transcript=decision.get("corrected_transcript"),
                ),
                "acoustic_tags": list(decision.get("acoustic_tags") or ()),
            }
        )
        imported += 1
    write_rows(run_root / "reviewed.jsonl", rows)
    print(
        json.dumps(
            {"imported": imported, "rows": len(rows), "output": str(run_root / "reviewed.jsonl")}
        )
    )
    return 0


def latest_rows(run_root: Path) -> list[ResultRow]:
    for stage in ("reviewed", "features", "scored", "asr", "clips", "sample"):
        rows = read_rows(run_root / f"{stage}.jsonl")
        if rows:
            return rows
    raise SystemExit(f"no stage output exists under {run_root}")


def command_report(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / args.run_id
    rows = latest_rows(run_root)
    snapshot = json.loads((run_root / "sample.json").read_text(encoding="utf-8"))
    summary = aggregate(rows, config)
    provenance = next((row.asr_provenance for row in rows if row.asr_provenance is not None), None)
    results = {
        "schema_version": 1,
        "experiment": "audio-caption-reliability",
        "run_id": args.run_id,
        "generated_at": now(),
        "config_sha256": snapshot["config_sha256"],
        "config_sha256_now": canonical_checksum(config.model_dump(mode="json")),
        "config_changed_since_sample": (
            canonical_checksum(config.model_dump(mode="json")) != snapshot["config_sha256"]
        ),
        "sample_sha256": snapshot["sample_sha256"],
        "configuration": config.model_dump(mode="json"),
        "selection": snapshot["selection"],
        "asr_provenance": provenance,
        "runtime": {
            "total_asr_ms": sum(row.asr_runtime_ms or 0 for row in rows),
            "transcribed": sum(1 for row in rows if row.asr_text is not None),
        },
        "summary": summary,
        "review": {
            "rubric_version": config.review.rubric_version,
            "predeclared_subset": config.review.subset,
            "reviewed": summary["overall"]["reviewed_segments"],
            "total": summary["overall"]["segments"],
        },
        "recommendations": {stratum: None for stratum in config.source_classes},
        "feature_decisions": {
            name: None
            for name in (
                "caption_asr_agreement",
                "speaking_rate",
                "speech_ratio",
                "squim_objective",
                "squim_subjective",
            )
        },
        "recommendation_vocabulary": list(RECOMMENDATIONS),
        "acoustic_vocabulary": list(ACOUSTIC_VOCABULARY),
        "artifacts": {
            "run_directory": str(run_root),
            "committed_results": str(args.results),
            "note": "per-item rows stay under the gitignored data directory",
        },
        "complete": all(row.status == "complete" for row in rows),
    }
    write_json(args.results, results)
    print(json.dumps(summary["overall"], ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "preflight",
            "sample",
            "prepare",
            "transcribe",
            "score",
            "features",
            "review-export",
            "review-html",
            "review-import",
            "report",
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--run-id", default="pilot-1")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--worksheet", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-embed-audio", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    if args.command == "preflight":
        status = preflight(config, args.data_dir)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["ready"] else 1
    if args.command in {"prepare", "transcribe"} and config.authorization.required:
        if not config.authorization.confirmed:
            raise SystemExit(
                "authorization.confirmed is false: record the operator's legal basis for "
                "downloading and locally retaining these sources before running this stage"
            )
    handlers = {
        "sample": command_sample,
        "prepare": command_prepare,
        "transcribe": command_transcribe,
        "score": command_score,
        "features": command_features,
        "review-export": command_review_export,
        "review-html": command_review_html,
        "review-import": command_review_import,
        "report": command_report,
    }
    if args.command == "review-import" and not args.worksheet:
        raise SystemExit("review-import requires --worksheet")
    return handlers[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main())
