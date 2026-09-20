#!/usr/bin/env python3
"""Staged runner for the target-language speech span spike.

Stages, in order:

- ``audio``      convert real clips to 16 kHz mono and register synthetic clips
- ``chunks``     Silero VAD, method chunks, and method-independent review units
- ``voxlingua``  acoustic language ID per chunk and per sliding window
- ``whisper``    Whisper language ID and a target-forced transcript per candidate span
- ``baseline``   naive whole-file Whisper runs (W0 multilingual, W1 forced target)
- ``decide``     sweep operating points on synthetic truth, select one, apply to every clip
- ``review-export`` / ``review-html`` / ``review-import``  blind human language labels
- ``report``     metrics, costs, and examples

The method only ever receives a clip's audio and its target language. Everything under a clip's
``evaluation_only`` key, and the synthetic truth files, is read by ``decide`` and ``report``
for scoring and nowhere else.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
sys.path.insert(0, str(HERE))

from speech_spans import (  # noqa: E402
    HUMAN_LABELS,
    LABEL_RUBRIC_VERSION,
    Candidate,
    ChunkRecord,
    DetectorOutput,
    ExperimentConfig,
    GateSettings,
    Interval,
    LabelRecord,
    TruthInterval,
    WindowRecord,
    canonical_checksum,
    caption_proxy_labels,
    closed_set_probability,
    covered,
    duration_breakdown,
    gate,
    merge_intervals,
    mixed_unit_report,
    normalise_language,
    pairwise_probability,
    parse_vtt,
    pool_duration_breakdowns,
    pool_mixed_units,
    pool_scores,
    probability_of,
    runs_from_windows,
    score_against_labels,
    score_against_truth,
    script_share,
    script_units,
    select_operating_point,
    sliding_windows,
    viterbi_two_state,
)

DEFAULT_CONFIG = HERE / "config-v1.json"
DEFAULT_RUN_ROOT = REPOSITORY / "data/experiments/target-language-speech-spans"
DEFAULT_MEDIA_DIR = Path.home() / "tmp"
LABELS_DIR = HERE / "labels"
RESULTS = HERE / "results.json"
RESULTS_NOTE = (
    "Aggregate metrics only; no transcript text. Full per-span outputs stay in the gitignored "
    "run directory."
)
SAMPLE_RATE = 16000
VOXLINGUA_BATCH = 4
NON_LATIN = {"zh", "ja", "ko", "ru", "hi"}
METHODS = (
    "chunk-vox",
    "chunk-voxset",
    "chunk-whisperset",
    "chunk-agree",
    "refined-vox",
    "chunk-voxpair",
    "refined-voxset",
    "refined-voxpair",
    "refined-agree",
)
RUN_SCORES = ("vox", "voxset", "voxpair")


# --------------------------------------------------------------------------------------------
# Small I/O helpers


def now() -> str:
    return datetime.now(UTC).isoformat()


def load_config(path: Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1048576 if platform.system() == "Darwin" else peak / 1024


def record_cost(run_root: Path, stage: str, key: str, payload: dict[str, Any]) -> None:
    path = run_root / "costs.json"
    costs = read_json(path) if path.is_file() else {}
    costs.setdefault(stage, {})[key] = {
        **payload,
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "machine": f"{platform.system()} {platform.machine()} {platform.processor()}",
        "recorded_at": now(),
    }
    write_json(path, costs)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_waveform(path: Path) -> Any:
    """16 kHz mono float32 numpy array."""
    import numpy as np

    from speech_retrieval.audio_features import read_prepared_waveform

    return np.asarray(read_prepared_waveform(path, sample_rate=SAMPLE_RATE).numpy())


def clips_in(run_root: Path, only: Sequence[str] | None = None) -> list[dict[str, Any]]:
    path = run_root / "clips.json"
    if not path.is_file():
        raise SystemExit("run the audio stage first")
    clips = read_json(path)["clips"]
    if only:
        clips = [clip for clip in clips if clip["clip_id"] in only or clip["source"] in only]
    return clips


def method_view(clip: dict[str, Any]) -> tuple[Path, str]:
    """The only two facts about a clip the method is allowed to see."""
    return Path(clip["path"]), clip["target_language"]


# --------------------------------------------------------------------------------------------
# audio


def command_audio(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / config.run_id
    audio_dir = run_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    clips: list[dict[str, Any]] = []
    for spec in config.clips:
        matches = sorted(args.media_dir.glob(spec.file_pattern))
        if len(matches) != 1:
            print(f"{spec.id}: expected one media file, found {len(matches)}", file=sys.stderr)
            clips.append(
                {
                    "clip_id": spec.id,
                    "source": "real",
                    "path": None,
                    "target_language": spec.target_language,
                    "error": f"{len(matches)} media files matched",
                    "evaluation_only": spec.evaluation_only,
                }
            )
            continue
        output = audio_dir / f"{spec.id}.wav"
        if not output.is_file():
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(matches[0]),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    str(SAMPLE_RATE),
                    "-sample_fmt",
                    "s16",
                    str(output),
                ],
                check=True,
            )
        duration = output.stat().st_size / (SAMPLE_RATE * 2)
        clips.append(
            {
                "clip_id": spec.id,
                "source": "real",
                "path": str(output),
                "target_language": spec.target_language,
                "duration": round(duration, 3),
                "sha256": sha256_file(output),
                "media_name": matches[0].name,
                "evaluation_only": spec.evaluation_only,
            }
        )
        print(f"{spec.id}: {duration / 60:.1f} min", flush=True)
    synthetic = args.synthetic_dir / "manifest.json"
    if synthetic.is_file():
        for entry in read_json(synthetic)["clips"]:
            path = Path(entry["path"])
            truth = path.with_suffix(".truth.json")
            clips.append(
                {
                    "clip_id": entry["clip_id"],
                    "source": "synthetic",
                    "path": str(path),
                    "target_language": entry["target_language"],
                    "duration": read_json(truth)["duration"],
                    "sha256": sha256_file(path),
                    "evaluation_only": {**entry["evaluation_only"], "truth_path": str(truth)},
                }
            )
        print(f"registered {len(read_json(synthetic)['clips'])} synthetic clips", flush=True)
    else:
        print(f"no synthetic manifest at {synthetic}; run synth_tts.py first", file=sys.stderr)
    write_json(run_root / "clips.json", {"created_at": now(), "clips": clips})
    write_json(run_root / "config.snapshot.json", config.model_dump(mode="json"))
    return 0


# --------------------------------------------------------------------------------------------
# chunks


def command_chunks(args: argparse.Namespace, config: ExperimentConfig) -> int:
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    run_root = args.run_root / config.run_id
    model = load_silero_vad(onnx=True)
    for clip in clips_in(run_root, args.clips):
        if not clip.get("path"):
            continue
        output = run_root / "chunks" / f"{clip['clip_id']}.json"
        if output.is_file() and not args.force:
            continue
        path, _target = method_view(clip)
        started = time.perf_counter()
        waveform = load_waveform(path)
        stamps = get_speech_timestamps(
            torch.from_numpy(waveform),
            model,
            sampling_rate=SAMPLE_RATE,
            threshold=config.vad.threshold,
            min_speech_duration_ms=config.vad.min_speech_ms,
            min_silence_duration_ms=config.vad.min_silence_ms,
            speech_pad_ms=config.vad.speech_pad_ms,
            return_seconds=True,
        )
        vad = [(float(item["start"]), float(item["end"])) for item in stamps]
        chunks = merge_intervals(
            vad, config.chunks.merge_gap_seconds, config.chunks.max_chunk_seconds
        )
        units = merge_intervals(
            vad, config.review_units.merge_gap_seconds, config.review_units.max_unit_seconds
        )
        write_json(
            output,
            {
                "clip_id": clip["clip_id"],
                "vad": vad,
                "chunks": [
                    {"chunk_id": f"{clip['clip_id']}:c{index:04d}", "start": c.start, "end": c.end}
                    for index, c in enumerate(chunks)
                ],
                "review_units": [
                    {"unit_id": f"{clip['clip_id']}:u{index:04d}", "start": u.start, "end": u.end}
                    for index, u in enumerate(units)
                ],
            },
        )
        record_cost(
            run_root,
            "chunks",
            clip["clip_id"],
            {"seconds": time.perf_counter() - started, "audio_seconds": clip["duration"]},
        )
        print(f"{clip['clip_id']}: {len(chunks)} chunks, {len(units)} review units", flush=True)
    return 0


# --------------------------------------------------------------------------------------------
# voxlingua


class VoxLingua:
    def __init__(self, source: str, savedir: Path) -> None:
        from speechbrain.inference.classifiers import EncoderClassifier

        self.classifier = EncoderClassifier.from_hparams(source=source, savedir=str(savedir))
        encoder = self.classifier.hparams.label_encoder
        self.labels = [encoder.ind2lab[index] for index in range(len(encoder.ind2lab))]
        self.provenance = {
            "model": source,
            "speechbrain_version": package_version("speechbrain"),
            "torch_version": package_version("torch"),
        }

    def classify(
        self, pieces: Sequence[Any], target: str, languages: Sequence[str]
    ) -> list[DetectorOutput]:
        """Classify equal-length or single pieces; returns a normalised distribution per piece."""
        import torch

        if not pieces:
            return []
        lengths = {len(piece) for piece in pieces}
        outputs: list[DetectorOutput] = []
        # Batches larger than a few windows are pathologically slow in torch's CPU convolution
        # path on this model (batch 4: 40 ms/window, batch 16: minutes), so keep them small.
        size = VOXLINGUA_BATCH if len(lengths) == 1 else 1
        batches = [list(pieces[i : i + size]) for i in range(0, len(pieces), size)]
        for batch in batches:
            tensor = torch.from_numpy(__import__("numpy").stack(batch))
            with torch.no_grad():
                log_probs, _score, _index, _label = self.classifier.classify_batch(tensor)
            probabilities = log_probs.exp()
            probabilities = probabilities / probabilities.sum(dim=-1, keepdim=True)
            for row in probabilities.tolist():
                distribution = list(zip(self.labels, row, strict=True))
                top = sorted(distribution, key=lambda item: -item[1])[:8]
                outputs.append(
                    DetectorOutput(
                        detector="voxlingua",
                        p_target=probability_of(distribution, target),
                        top=[(normalise_language(code), round(p, 5)) for code, p in top],
                        extra={
                            "p_target_closed": closed_set_probability(
                                distribution, target, languages
                            )
                        },
                    )
                )
        return outputs


def slice_audio(waveform: Any, start: float, end: float) -> Any:
    return waveform[
        int(start * SAMPLE_RATE) : max(int(start * SAMPLE_RATE) + 1, int(end * SAMPLE_RATE))
    ]


def command_voxlingua(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / config.run_id
    model: VoxLingua | None = None
    for clip in clips_in(run_root, args.clips):
        if not clip.get("path"):
            continue
        output = run_root / "voxlingua" / f"{clip['clip_id']}.jsonl"
        if output.is_file() and not args.force:
            continue
        if model is None:
            model = VoxLingua(config.voxlingua_model, run_root / "_models/voxlingua")
        path, target = method_view(clip)
        chunks = read_json(run_root / "chunks" / f"{clip['clip_id']}.json")["chunks"]
        waveform = load_waveform(path)
        started = time.perf_counter()
        rows: list[dict[str, Any]] = []
        window_count = 0
        for chunk in chunks:
            [chunk_output] = model.classify(
                [slice_audio(waveform, chunk["start"], chunk["end"])],
                target,
                config.closed_set_languages,
            )
            record = ChunkRecord(
                clip_id=clip["clip_id"],
                chunk_id=chunk["chunk_id"],
                start=chunk["start"],
                end=chunk["end"],
                detectors=[chunk_output],
            )
            if chunk["end"] - chunk["start"] >= config.refine.min_chunk_seconds:
                windows = sliding_windows(
                    chunk["start"],
                    chunk["end"],
                    config.refine.window_seconds,
                    config.refine.hop_seconds,
                )
                pieces = [slice_audio(waveform, w.start, w.end) for w in windows]
                size = min(len(piece) for piece in pieces)
                outputs = model.classify(
                    [piece[:size] for piece in pieces], target, config.closed_set_languages
                )
                record.windows = [
                    WindowRecord(start=w.start, end=w.end, detectors=[out])
                    for w, out in zip(windows, outputs, strict=True)
                ]
                window_count += len(windows)
            rows.append(record.model_dump(mode="json"))
            if len(rows) % 10 == 0:
                print(
                    f"  {clip['clip_id']}: {len(rows)}/{len(chunks)} chunks, "
                    f"{window_count} windows, {time.perf_counter() - started:.0f}s",
                    flush=True,
                )
        write_jsonl(output, rows)
        elapsed = time.perf_counter() - started
        record_cost(
            run_root,
            "voxlingua",
            clip["clip_id"],
            {
                "seconds": elapsed,
                "audio_seconds": clip["duration"],
                "rtf": elapsed / clip["duration"],
                "chunks": len(chunks),
                "windows": window_count,
                "provenance": model.provenance,
            },
        )
        print(
            f"{clip['clip_id']}: {len(chunks)} chunks, {window_count} windows, {elapsed:.0f}s",
            flush=True,
        )
        if clip["source"] == "synthetic":
            write_json(
                run_root / "voxlingua" / f"{clip['clip_id']}.tts-qc.json",
                tts_quality_check(model, waveform, load_truth(clip), config),
            )
    return 0


def tts_quality_check(
    model: VoxLingua, waveform: Any, truth: Sequence[TruthInterval], config: ExperimentConfig
) -> dict[str, Any]:
    """Evaluation-only: does each isolated synthetic utterance sound like its labelled language?

    This uses the detector under evaluation, so it is a sanity check on the voices rather than
    independent truth. A voice that fails systematically where the same detector succeeds on
    another voice points at the voice.
    """
    rows = []
    for item in truth:
        if item.end - item.start < 1.5:
            continue
        [output] = model.classify(
            [slice_audio(waveform, item.start, item.end)],
            item.language,
            config.closed_set_languages,
        )
        rows.append(
            {
                "start": item.start,
                "end": item.end,
                "language": item.language,
                "bucket": item.bucket,
                "top": output.top[:3],
                "top1_correct": output.top[0][0] == item.language,
            }
        )
    return {
        "checked": len(rows),
        "top1_correct": sum(r["top1_correct"] for r in rows),
        "rows": rows,
    }


# --------------------------------------------------------------------------------------------
# candidates (method-side, deterministic from VoxLingua output and config)


def _scores(output: DetectorOutput, target: str) -> dict[str, float | None]:
    return {
        "vox": output.p_target,
        "voxset": output.extra.get("p_target_closed"),
        "voxpair": pairwise_probability(output.top, output.p_target, target),
    }


def candidates_for_clip(
    rows: Sequence[dict[str, Any]], config: ExperimentConfig, target: str
) -> list[dict[str, Any]]:
    """Chunk candidates plus refined runs, one set of runs per window score.

    A refined method uses the runs built from its own score, plus every chunk too short to have
    windows. Runs from the two scores that coincide are still separate candidates, so each method
    is scored on exactly the segmentation it would produce.
    """
    candidates: list[dict[str, Any]] = []
    for row in rows:
        record = ChunkRecord.model_validate(row)
        scores = _scores(record.detectors[0], target)
        candidates.append(
            {
                "candidate_id": record.chunk_id,
                "kind": "chunk",
                "run_score": None,
                "chunk_id": record.chunk_id,
                "start": record.start,
                "end": record.end,
                **{f"{name}_p": value for name, value in scores.items()},
                "windowed": bool(record.windows),
            }
        )
        if not record.windows:
            continue
        windows = [Interval(w.start, w.end) for w in record.windows]
        window_scores = [_scores(w.detectors[0], target) for w in record.windows]
        for score in RUN_SCORES:
            probabilities = [item[score] or 0.0 for item in window_scores]
            labels = viterbi_two_state(probabilities, config.refine.switch_penalty)
            for index, (span, _mean) in enumerate(
                runs_from_windows(windows, labels, probabilities)
            ):
                inside = [
                    item
                    for window, item in zip(windows, window_scores, strict=True)
                    if window.start >= span.start - 1e-6 and window.end <= span.end + 1e-6
                ] or [
                    item
                    for window, item in zip(windows, window_scores, strict=True)
                    if window.start < span.end and window.end > span.start
                ]
                candidates.append(
                    {
                        "candidate_id": f"{record.chunk_id}:{score}:r{index}",
                        "kind": "run",
                        "run_score": score,
                        "chunk_id": record.chunk_id,
                        "start": span.start,
                        "end": span.end,
                        **{
                            f"{name}_p": sum(i[name] or 0.0 for i in inside) / len(inside)
                            for name in RUN_SCORES
                        },
                        "windowed": True,
                    }
                )
    return candidates


# --------------------------------------------------------------------------------------------
# whisper


class WhisperProbe:
    def __init__(self, settings: Any) -> None:
        from faster_whisper import WhisperModel

        started = time.perf_counter()
        self.settings = settings
        self.model = WhisperModel(
            settings.model,
            device="cpu",
            compute_type=settings.compute_type,
            cpu_threads=settings.cpu_threads,
        )
        self.provenance = {
            "backend": "faster-whisper",
            "backend_version": package_version("faster-whisper"),
            "ctranslate2_version": package_version("ctranslate2"),
            "model": settings.model,
            "compute_type": settings.compute_type,
            "cpu_threads": settings.cpu_threads,
            "beam_size": settings.beam_size,
            "model_load_seconds": round(time.perf_counter() - started, 2),
        }

    def detect(self, piece: Any, target: str, languages: Sequence[str]) -> DetectorOutput:
        _language, _probability, distribution = self.model.detect_language(piece)
        top = sorted(distribution, key=lambda item: -item[1])[:8]
        return DetectorOutput(
            detector="whisper_lid",
            p_target=probability_of(distribution, target),
            top=[(code, round(p, 5)) for code, p in top],
            extra={"p_target_closed": closed_set_probability(distribution, target, languages)},
        )

    def transcribe(self, piece: Any, language: str) -> dict[str, Any]:
        started = time.perf_counter()
        segments, _info = self.model.transcribe(
            piece,
            language=language,
            beam_size=self.settings.beam_size,
            condition_on_previous_text=False,
            temperature=0.0,
            word_timestamps=True,
            vad_filter=False,
        )
        rows = list(segments)
        tokens = sum(len(item.tokens) for item in rows) or 1
        return {
            "language": language,
            "text": "".join(item.text for item in rows).strip(),
            "avg_logprob": (
                sum(item.avg_logprob * len(item.tokens) for item in rows) / tokens if rows else None
            ),
            "compression_ratio": max((item.compression_ratio for item in rows), default=None),
            "no_speech_prob": max((item.no_speech_prob for item in rows), default=None),
            "words": [
                {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                for item in rows
                for w in (item.words or ())
            ],
            "seconds": round(time.perf_counter() - started, 3),
        }


class TextLid:
    def __init__(self) -> None:
        from lingua import LanguageDetectorBuilder

        self.detector = LanguageDetectorBuilder.from_all_languages().build()
        self.provenance = {"lingua_version": package_version("lingua-language-detector")}

    def detect(self, text: str) -> tuple[str | None, float | None]:
        if not text.strip():
            return None, None
        values = self.detector.compute_language_confidence_values(text)
        if not values:
            return None, None
        best = values[0]
        return best.language.iso_code_639_1.name.lower(), float(best.value)


def command_whisper(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / config.run_id
    settings = config.whisper.model_copy(update={"model": args.model} if args.model else {})
    folder = run_root / f"whisper-{settings.model}"
    probe: WhisperProbe | None = None
    text_lid: TextLid | None = None
    for clip in clips_in(run_root, args.clips):
        if not clip.get("path"):
            continue
        output = folder / f"{clip['clip_id']}.jsonl"
        done = {row["candidate_id"]: row for row in read_jsonl(output)} if not args.force else {}
        path, target = method_view(clip)
        rows = read_jsonl(run_root / "voxlingua" / f"{clip['clip_id']}.jsonl")
        candidates = candidates_for_clip(rows, config, target)
        pending = [item for item in candidates if item["candidate_id"] not in done]
        if not pending:
            continue
        if probe is None:
            probe = WhisperProbe(settings)
            text_lid = TextLid()
        assert text_lid is not None
        waveform = load_waveform(path)
        started = time.perf_counter()
        decoded_seconds = 0.0
        results = dict(done)
        for index, item in enumerate(pending):
            piece = slice_audio(waveform, item["start"], item["end"])
            lid = probe.detect(piece, target, config.closed_set_languages)
            row: dict[str, Any] = {**item, "whisper_lid": lid.model_dump(mode="json")}
            loose = max(
                *(item[f"{name}_p"] or 0.0 for name in RUN_SCORES),
                lid.extra.get("p_target_closed") or 0.0,
            )
            if loose >= args.decode_floor:
                forced = probe.transcribe(piece, target)
                decoded_seconds += item["end"] - item["start"]
                language, confidence = text_lid.detect(forced["text"])
                forced.update(
                    {
                        "script_share": script_share(forced["text"], target),
                        "units": script_units(forced["text"]),
                        "text_lid": language,
                        "text_lid_confidence": confidence,
                    }
                )
                row["forced"] = forced
            else:
                row["forced"] = None
                row["skipped_decode"] = f"max detector p_target {loose:.3f} < {args.decode_floor}"
            results[item["candidate_id"]] = row
            if index % 20 == 19:
                write_jsonl(output, results.values())
        write_jsonl(output, [results[item["candidate_id"]] for item in candidates])
        elapsed = time.perf_counter() - started
        record_cost(
            run_root,
            f"whisper-{settings.model}",
            clip["clip_id"],
            {
                "seconds": elapsed,
                "audio_seconds": clip["duration"],
                "rtf": elapsed / clip["duration"],
                "candidates": len(pending),
                "decoded_candidate_seconds": decoded_seconds,
                "decode_floor": args.decode_floor,
                "provenance": {**probe.provenance, **text_lid.provenance},
            },
        )
        print(f"{clip['clip_id']}: {len(pending)} candidates, {elapsed:.0f}s", flush=True)
    return 0


# --------------------------------------------------------------------------------------------
# baseline


def command_baseline(args: argparse.Namespace, config: ExperimentConfig) -> int:
    from faster_whisper import WhisperModel

    run_root = args.run_root / config.run_id
    settings = config.whisper.model_copy(update={"model": args.model} if args.model else {})
    model: Any = None
    for clip in clips_in(run_root, args.clips):
        if not clip.get("path"):
            continue
        output = run_root / f"baseline-{settings.model}" / f"{clip['clip_id']}.json"
        if output.is_file() and not args.force:
            continue
        if model is None:
            model = WhisperModel(
                settings.model,
                device="cpu",
                compute_type=settings.compute_type,
                cpu_threads=settings.cpu_threads,
            )
        path, target = method_view(clip)
        payload: dict[str, Any] = {"clip_id": clip["clip_id"], "model": settings.model}
        for name, options in (
            ("W0-multilingual", {"multilingual": True, "language": None}),
            ("W1-forced-target", {"language": target}),
        ):
            started = time.perf_counter()
            segments, info = model.transcribe(
                str(path),
                beam_size=settings.beam_size,
                vad_filter=True,
                condition_on_previous_text=False,
                **options,
            )
            rows = [
                {
                    "start": item.start,
                    "end": item.end,
                    "text": item.text,
                    "language": getattr(item, "language", None) or info.language,
                    "avg_logprob": item.avg_logprob,
                    "compression_ratio": item.compression_ratio,
                    "no_speech_prob": item.no_speech_prob,
                }
                for item in segments
            ]
            elapsed = time.perf_counter() - started
            payload[name] = {"segments": rows, "seconds": elapsed, "detected": info.language}
            record_cost(
                run_root,
                f"baseline-{settings.model}",
                f"{clip['clip_id']}:{name}",
                {
                    "seconds": elapsed,
                    "audio_seconds": clip["duration"],
                    "rtf": elapsed / clip["duration"],
                },
            )
            print(f"{clip['clip_id']} {name}: {len(rows)} segments, {elapsed:.0f}s", flush=True)
        write_json(output, payload)
    return 0


def baseline_spans(payload: dict[str, Any], name: str, target: str) -> list[tuple[float, float]]:
    rows = payload[name]["segments"]
    if name.startswith("W0"):
        rows = [row for row in rows if normalise_language(row["language"] or "") == target]
    return [(row["start"], row["end"]) for row in rows]


# --------------------------------------------------------------------------------------------
# decide


def feature_rows(
    run_root: Path, folder: Path, clip: dict[str, Any], config: ExperimentConfig
) -> list[dict[str, Any]]:
    """Candidates rebuilt from VoxLingua output, joined with Whisper evidence by candidate id.

    Rebuilding keeps scoring consistent with the current candidate logic; a candidate Whisper has
    not processed yet keeps ``whisper_lid``/``forced`` as ``None`` and is reported, not dropped.
    """
    vox = read_jsonl(run_root / "voxlingua" / f"{clip['clip_id']}.jsonl")
    if not vox:
        return []
    whisper = {row["candidate_id"]: row for row in read_jsonl(folder / f"{clip['clip_id']}.jsonl")}
    rows = []
    for candidate in candidates_for_clip(vox, config, clip["target_language"]):
        evidence = whisper.get(candidate["candidate_id"], {})
        rows.append(
            {
                **candidate,
                "whisper_lid": evidence.get("whisper_lid"),
                "forced": evidence.get("forced"),
                "whisper_missing": candidate["candidate_id"] not in whisper,
            }
        )
    return rows


def candidate_features(row: dict[str, Any], method: str, target: str) -> Candidate:
    lid = row.get("whisper_lid") or {}
    whisperset = (lid.get("extra") or {}).get("p_target_closed")
    detector = method.split("-", 1)[1]
    p: float | None
    if detector in RUN_SCORES:
        p = row.get(f"{detector}_p")
    elif detector == "whisperset":
        p = whisperset
    else:
        voxset = row.get("voxset_p")
        p = None if voxset is None or whisperset is None else min(voxset, whisperset)
    forced = row.get("forced") or {}
    text_language = forced.get("text_lid")
    return Candidate(
        start=row["start"],
        end=row["end"],
        p_target=p,
        units=forced.get("units"),
        compression_ratio=forced.get("compression_ratio"),
        script_share=forced.get("script_share") if target in NON_LATIN else None,
        text_lid_is_target=None if text_language is None else text_language == target,
    )


def method_candidates(rows: Sequence[dict[str, Any]], method: str) -> list[dict[str, Any]]:
    """The segmentation a method produces: whole chunks, or its own runs plus unwindowed chunks."""
    if method.startswith("chunk-"):
        return [row for row in rows if row["kind"] == "chunk"]
    score = method.split("-", 1)[1]
    if score not in RUN_SCORES:
        score = "voxset"
    return [
        row
        for row in rows
        if (row["kind"] == "chunk" and not row["windowed"]) or row["run_score"] == score
    ]


def decide_spans(
    rows: Sequence[dict[str, Any]], method: str, target: str, settings: GateSettings
) -> list[dict[str, Any]]:
    accepted = []
    for row in method_candidates(rows, method):
        candidate = candidate_features(row, method, target)
        ok, reasons = gate(candidate, settings)
        if ok:
            forced = row.get("forced") or {}
            accepted.append(
                {
                    "candidate_id": row["candidate_id"],
                    "start": row["start"],
                    "end": row["end"],
                    "p_target": candidate.p_target,
                    "text": forced.get("text"),
                    "words": forced.get("words", []),
                    "avg_logprob": forced.get("avg_logprob"),
                    "compression_ratio": forced.get("compression_ratio"),
                }
            )
    return accepted


def load_truth(clip: dict[str, Any]) -> list[TruthInterval]:
    payload = read_json(Path(clip["evaluation_only"]["truth_path"]))
    return [
        TruthInterval.model_validate({k: v for k, v in item.items() if k != "voice"})
        for item in payload["truth"]
    ]


def scored(
    spans: Sequence[tuple[float, float]], truth: Sequence[TruthInterval], config: ExperimentConfig
) -> dict[str, Any]:
    return score_against_truth(spans, truth, config.metrics)


def gate_for(
    config: ExperimentConfig, threshold: float, min_seconds: float, vetoes: bool
) -> GateSettings:
    return GateSettings(
        detector="sweep",
        threshold=threshold,
        min_seconds=min_seconds,
        min_units=0,
        max_compression_ratio=config.fallback_gate.max_compression_ratio if vetoes else None,
        min_script_share=0.5 if vetoes else None,
        text_lid_veto=False,
    )


def command_decide(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / config.run_id
    model = args.model or config.whisper.model
    folder = run_root / f"whisper-{model}"
    clips = [clip for clip in clips_in(run_root) if clip.get("path")]
    features = {clip["clip_id"]: feature_rows(run_root, folder, clip, config) for clip in clips}
    missing = [clip_id for clip_id, rows in features.items() if not rows]
    if missing:
        print(f"no whisper features for: {', '.join(missing)}", file=sys.stderr)
    synthetic = [c for c in clips if c["source"] == "synthetic" and features[c["clip_id"]]]
    if not synthetic:
        raise SystemExit("no synthetic clips with features; the operating point is tuned on them")
    truths = {clip["clip_id"]: load_truth(clip) for clip in synthetic}
    sweep: list[dict[str, Any]] = []
    for method in METHODS:
        for vetoes in (False, True):
            for threshold in config.sweep.thresholds:
                for min_seconds in config.sweep.min_seconds:
                    settings = gate_for(config, threshold, min_seconds, vetoes)
                    per_clip = []
                    for clip in synthetic:
                        spans = decide_spans(
                            features[clip["clip_id"]], method, clip["target_language"], settings
                        )
                        per_clip.append(
                            scored(
                                [(s["start"], s["end"]) for s in spans],
                                truths[clip["clip_id"]],
                                config,
                            )
                        )
                    sweep.append(
                        {
                            "method": method,
                            "vetoes": vetoes,
                            "threshold": threshold,
                            "min_seconds": min_seconds,
                            **pool_scores(per_clip),
                        }
                    )
    chosen = select_operating_point(sweep, config.selection)
    write_json(
        run_root / f"decide-{model}" / "sweep.json",
        {
            "whisper_model": model,
            "selection": config.selection.model_dump(),
            "rows": sweep,
            "chosen": chosen,
            "synthetic_clips": [c["clip_id"] for c in synthetic],
        },
    )
    if chosen is None:
        print("no operating point reaches the precision floor on synthetic data", file=sys.stderr)
        return 1
    settings = gate_for(config, chosen["threshold"], chosen["min_seconds"], chosen["vetoes"])
    for clip in clips:
        rows = features[clip["clip_id"]]
        if not rows:
            continue
        spans = decide_spans(rows, chosen["method"], clip["target_language"], settings)
        write_json(
            run_root / f"decide-{model}" / "spans" / f"{clip['clip_id']}.json",
            {"clip_id": clip["clip_id"], "operating_point": chosen, "spans": spans},
        )
    print(
        f"chosen: {chosen['method']} vetoes={chosen['vetoes']} p>={chosen['threshold']} "
        f"min={chosen['min_seconds']}s  span_precision={chosen['span_precision']:.3f} "
        f"long_run_recall={chosen['long_run_recall']:.3f}",
        flush=True,
    )
    return 0


# --------------------------------------------------------------------------------------------
# review


LANGUAGE_NAMES = {
    "zh": "Chinese",
    "es": "Spanish",
    "en": "English",
    "pt": "Portuguese",
    "fr": "French",
    "it": "Italian",
    "de": "German",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "hi": "Hindi",
}


def command_review_export(args: argparse.Namespace, config: ExperimentConfig) -> int:
    """Freeze every review unit of every real clip. No detector output enters the worksheet."""
    run_root = args.run_root / config.run_id
    review_dir = run_root / "review"
    items = []
    clips_meta = []
    for clip in clips_in(run_root):
        if clip["source"] != "real" or not clip.get("path"):
            continue
        units = read_json(run_root / "chunks" / f"{clip['clip_id']}.json")["review_units"]
        encoded = review_dir / "audio" / f"{clip['clip_id']}.m4a"
        if not encoded.is_file():
            encoded.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    clip["path"],
                    "-c:a",
                    "aac",
                    "-b:a",
                    "40k",
                    str(encoded),
                ],
                check=True,
            )
        clips_meta.append(
            {
                "clip_id": clip["clip_id"],
                "target_language": clip["target_language"],
                "target_name": LANGUAGE_NAMES.get(clip["target_language"], clip["target_language"]),
                "audio": str(encoded),
                "duration": clip["duration"],
            }
        )
        for unit in units:
            items.append(
                {
                    "clip_id": clip["clip_id"],
                    "unit_id": unit["unit_id"],
                    "start": round(unit["start"], 3),
                    "end": round(unit["end"], 3),
                    "label": None,
                    "note": None,
                    "reviewer": None,
                    "reviewed_at": None,
                }
            )
    worksheet = {
        "run_id": config.run_id,
        "rubric_version": LABEL_RUBRIC_VERSION,
        "labels": list(HUMAN_LABELS),
        "clips": clips_meta,
        "items": items,
        "items_checksum": canonical_checksum([[i["unit_id"], i["start"], i["end"]] for i in items]),
        "created_at": now(),
    }
    write_json(review_dir / "worksheet.json", worksheet)
    print(f"{len(items)} review units across {len(clips_meta)} clips", flush=True)
    return 0


def command_review_html(args: argparse.Namespace, config: ExperimentConfig) -> int:
    from language_review_app import render_language_review

    run_root = args.run_root / config.run_id
    worksheet = read_json(run_root / "review" / "worksheet.json")
    html = render_language_review(worksheet)
    output = run_root / "review" / "review.html"
    output.write_text(html, encoding="utf-8")
    print(f"wrote {output} ({output.stat().st_size / 1048576:.1f} MB)", flush=True)
    return 0


def label_store_path(config: ExperimentConfig) -> Path:
    """The committed label store: reusable beyond this run's gitignored artifacts."""
    return LABELS_DIR / f"{config.run_id}.language-labels.json"


def label_provenance(run_root: Path, config: ExperimentConfig) -> dict[str, Any]:
    """What a later task needs to rebuild or verify the labelled units without this run."""
    clips = {clip["clip_id"]: clip for clip in clips_in(run_root) if clip["source"] == "real"}
    return {
        "experiment": "experiments/target-language-speech-spans",
        "audio_preparation": f"ffmpeg -vn -ac 1 -ar {SAMPLE_RATE} -sample_fmt s16 (WAV)",
        "unit_definition": (
            "Silero VAD speech pieces (settings below), not merged, capped at "
            f"{config.review_units.max_unit_seconds} s by even splitting; times in seconds from "
            "the start of the prepared audio"
        ),
        "vad": config.vad.model_dump(),
        "review_units": config.review_units.model_dump(),
        "silero_vad_version": package_version("silero-vad"),
        "clip_audio": {
            clip_id: {
                "prepared_audio_sha256": clip.get("sha256"),
                "duration": clip.get("duration"),
                "media_name": clip.get("media_name"),
                "youtube_id": clip_id,
            }
            for clip_id, clip in clips.items()
        },
        "blind": "audio only; no transcript, detector score or method decision was shown",
    }


def local_network_addresses() -> list[str]:
    import socket

    addresses: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the LAN route
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(address for address in addresses if not address.startswith("127."))


def command_review_serve(args: argparse.Namespace, config: ExperimentConfig) -> int:
    from label_server import build_server

    run_root = args.run_root / config.run_id
    worksheet = read_json(run_root / "review" / "worksheet.json")
    store_path = args.labels or label_store_path(config)
    server, labels = build_server(
        worksheet, store_path, label_provenance(run_root, config), host=args.host, port=args.port
    )
    host, port = str(server.server_address[0]), int(server.server_address[1])
    labelled = sum(1 for value in labels.state()["labels"].values() if value)
    print(
        f"labelling page: http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/", flush=True
    )
    if host == "0.0.0.0":
        for address in local_network_addresses():
            print(f"  on the local network: http://{address}:{port}/", flush=True)
        print("  (no authentication: anyone on this network can open the page and add labels)")
    print(f"labels are saved on every key press to {store_path} ({labelled} so far)", flush=True)
    print("stop with Ctrl-C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def command_review_import(args: argparse.Namespace, config: ExperimentConfig) -> int:
    """Import a worksheet downloaded from the offline page into the committed label store."""
    from label_server import LabelStore, open_store

    run_root = args.run_root / config.run_id
    frozen = read_json(run_root / "review" / "worksheet.json")
    filled = read_json(args.worksheet)
    if filled.get("items_checksum") != frozen["items_checksum"]:
        raise SystemExit("worksheet does not match the frozen review units")
    frozen_ids = [item["unit_id"] for item in frozen["items"]]
    if [item["unit_id"] for item in filled["items"]] != frozen_ids:
        raise SystemExit("worksheet rows were added, removed or reordered")
    labels = [LabelRecord.model_validate(item) for item in filled["items"]]
    judged = [label for label in labels if label.label is not None]
    if any(label.reviewer is None for label in judged):
        raise SystemExit("every judged unit needs a reviewer")
    path = label_store_path(config)
    store = LabelStore(path, open_store(path, frozen, label_provenance(run_root, config)))
    for label in judged:
        assert label.reviewer is not None
        store.set_reviewer(label.reviewer)
        store.set_label(label.unit_id, label.label)
    print(f"imported {len(judged)} / {len(labels)} labelled units into {path}", flush=True)
    return 0


def load_labels(run_root: Path, config: ExperimentConfig) -> list[LabelRecord]:
    path = label_store_path(config)
    if not path.is_file():
        return []
    store = read_json(path)
    worksheet = run_root / "review" / "worksheet.json"
    if worksheet.is_file() and read_json(worksheet)["items_checksum"] != store["items_checksum"]:
        raise SystemExit(f"{path} does not match this run's review units")
    return [LabelRecord.model_validate(item) for item in store["items"]]


# --------------------------------------------------------------------------------------------
# report


def bucket_recall(
    spans: Sequence[tuple[float, float]], truth: Sequence[TruthInterval]
) -> dict[str, dict[str, int]]:
    """Per target utterance bucket: how many utterances are at least half inside accepted spans."""
    counts: dict[str, dict[str, int]] = {}
    for item in truth:
        if not item.is_target:
            continue
        key = f"{item.bucket}{'-inline' if item.role == 'inline_item' else ''}"
        entry = counts.setdefault(key, {"utterances": 0, "half_covered": 0})
        entry["utterances"] += 1
        if covered([(item.start, item.end)], spans) >= 0.5 * (item.end - item.start):
            entry["half_covered"] += 1
    return counts


def proxy_labels_for(clip: dict[str, Any], run_root: Path, captions_dir: Path) -> list[LabelRecord]:
    """Evaluation-only caption proxy labels, for non-Latin targets with an other-language track."""
    path = captions_dir / f"{clip['clip_id']}.en.vtt"
    if clip["source"] != "real" or clip["target_language"] not in NON_LATIN or not path.is_file():
        return []
    units = read_json(run_root / "chunks" / f"{clip['clip_id']}.json")["review_units"]
    cues = parse_vtt(path.read_text(encoding="utf-8"))
    return caption_proxy_labels(units, cues, clip["target_language"], clip_id=clip["clip_id"])


def real_sweep_rows(
    features: dict[str, list[dict[str, Any]]],
    clips: Sequence[dict[str, Any]],
    labels: Sequence[LabelRecord],
    config: ExperimentConfig,
) -> list[dict[str, Any]]:
    """The whole operating-point sweep scored against human labels instead of synthetic truth.

    Diagnostic only. The operating point is selected on synthetic clips in ``decide`` and never
    touches these numbers, so the labels stay an unbiased estimate of the selected method.
    """
    rows: list[dict[str, Any]] = []
    for method in METHODS:
        for vetoes in (False, True):
            for threshold in config.sweep.thresholds:
                for min_seconds in config.sweep.min_seconds:
                    settings = gate_for(config, threshold, min_seconds, vetoes)
                    per_clip = []
                    for clip in clips:
                        clip_labels = [x for x in labels if x.clip_id == clip["clip_id"]]
                        if not clip_labels or not features.get(clip["clip_id"]):
                            continue
                        spans = decide_spans(
                            features[clip["clip_id"]], method, clip["target_language"], settings
                        )
                        per_clip.append(
                            score_against_labels(
                                [(s["start"], s["end"]) for s in spans],
                                clip_labels,
                                config.metrics,
                            )
                        )
                    if not per_clip:
                        continue
                    rows.append(
                        {
                            "method": method,
                            "vetoes": vetoes,
                            "threshold": threshold,
                            "min_seconds": min_seconds,
                            **pool_scores(per_clip),
                        }
                    )
    return rows


def script_contamination(spans: Sequence[dict[str, Any]], target: str) -> dict[str, Any] | None:
    """Other-script share of the forced transcript of each accepted span.

    Independent of the human labels and of both detectors: for a non-Latin target, Latin letters in
    the transcript are lesson-language words the span swallowed. There is no equivalent check for a
    Latin-script target.
    """
    if target not in NON_LATIN:
        return None
    shares = [
        share
        for span in spans
        if (share := script_share(span.get("text") or "", target)) is not None
    ]
    if not shares:
        return None
    return {
        "spans_with_transcript": len(shares),
        "mean_target_script_share": sum(shares) / len(shares),
        "spans_below_0.9": sum(1 for share in shares if share < 0.9),
        "spans_below_0.7": sum(1 for share in shares if share < 0.7),
        "worst_target_script_share": min(shares),
    }


def command_report(args: argparse.Namespace, config: ExperimentConfig) -> int:
    run_root = args.run_root / config.run_id
    model = args.model or config.whisper.model
    decide_dir = run_root / f"decide-{model}"
    sweep = read_json(decide_dir / "sweep.json")
    chosen = sweep["chosen"]
    clips = [clip for clip in clips_in(run_root) if clip.get("path")]
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": config.run_id,
        "whisper_model": model,
        "created_at": now(),
        "chosen_operating_point": chosen,
        "best_per_method": {},
        "synthetic": {"per_clip": {}, "by_pair": {}, "by_voice": {}, "by_bucket": {}},
        "real": {},
        "baselines": {},
        "costs": read_json(run_root / "costs.json") if (run_root / "costs.json").is_file() else {},
    }
    for method in METHODS:
        rows = [row for row in sweep["rows"] if row["method"] == method]
        report["best_per_method"][method] = select_operating_point(rows, config.selection)
    labels = load_labels(run_root, config)
    proxies = {
        clip["clip_id"]: proxy_labels_for(clip, run_root, args.captions_dir) for clip in clips
    }
    by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_voice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    buckets: dict[str, dict[str, int]] = {}
    for clip in clips:
        spans_path = decide_dir / "spans" / f"{clip['clip_id']}.json"
        if not spans_path.is_file():
            continue
        spans = [(s["start"], s["end"]) for s in read_json(spans_path)["spans"]]
        if clip["source"] == "synthetic":
            truth = load_truth(clip)
            result = scored(spans, truth, config)
            evaluation = clip["evaluation_only"]
            report["synthetic"]["per_clip"][clip["clip_id"]] = result
            by_pair[f"{evaluation['base_language']}>{clip['target_language']}"].append(result)
            by_voice[evaluation["voice"].split(":")[0]].append(result)
            for key, counts in bucket_recall(spans, truth).items():
                total = buckets.setdefault(key, {"utterances": 0, "half_covered": 0})
                total["utterances"] += counts["utterances"]
                total["half_covered"] += counts["half_covered"]
        else:
            clip_labels = [label for label in labels if label.clip_id == clip["clip_id"]]
            real: dict[str, Any] = {
                "target_language": clip["target_language"],
                "accepted_spans": len(spans),
                "accepted_seconds": sum(e - s for s, e in spans),
                "script_contamination": script_contamination(
                    read_json(spans_path)["spans"], clip["target_language"]
                ),
            }
            if clip_labels:
                real["against_labels"] = score_against_labels(spans, clip_labels, config.metrics)
                real["against_labels_strict"] = score_against_labels(
                    spans, clip_labels, config.metrics, mixed_as_other=True
                )
                real["duration_breakdown"] = duration_breakdown(spans, clip_labels, config.metrics)
                real["mixed_units"] = mixed_unit_report(spans, clip_labels)
            proxy = proxies[clip["clip_id"]]
            if proxy:
                real["against_caption_proxy"] = score_against_labels(spans, proxy, config.metrics)
                if clip_labels:
                    real["caption_proxy_against_labels"] = score_against_labels(
                        [(x.start, x.end) for x in proxy if x.label == "target"],
                        clip_labels,
                        config.metrics,
                    )
            report["real"][clip["clip_id"]] = real
        for baseline in run_root.glob(f"baseline-{model}/{clip['clip_id']}.json"):
            payload = read_json(baseline)
            for name in ("W0-multilingual", "W1-forced-target"):
                base_spans = baseline_spans(payload, name, clip["target_language"])
                value: dict[str, Any] = {"accepted_spans": len(base_spans)}
                if clip["source"] == "synthetic":
                    value |= score_against_truth(base_spans, load_truth(clip), config.metrics)
                else:
                    clip_labels = [x for x in labels if x.clip_id == clip["clip_id"]]
                    if clip_labels:
                        value["against_labels"] = score_against_labels(
                            base_spans, clip_labels, config.metrics
                        )
                        value["against_labels_strict"] = score_against_labels(
                            base_spans, clip_labels, config.metrics, mixed_as_other=True
                        )
                    proxy = proxies[clip["clip_id"]]
                    if proxy:
                        value["against_caption_proxy"] = score_against_labels(
                            base_spans, proxy, config.metrics
                        )
                report["baselines"].setdefault(clip["clip_id"], {})[name] = value
    report["synthetic"]["by_pair"] = {
        key: pool_scores(rows) for key, rows in sorted(by_pair.items())
    }
    report["synthetic"]["by_voice"] = {
        key: pool_scores(rows) for key, rows in sorted(by_voice.items())
    }
    report["synthetic"]["by_bucket"] = {
        key: {**value, "recall": value["half_covered"] / value["utterances"]}
        for key, value in sorted(buckets.items())
    }
    if labels:
        real_rows: list[dict[str, Any]] = []
        strict_rows: list[dict[str, Any]] = []
        by_language: dict[str, list[dict[str, Any]]] = defaultdict(list)
        labelled_clips = []
        for clip in clips:
            if clip["source"] != "real":
                continue
            clip_labels = [label for label in labels if label.clip_id == clip["clip_id"]]
            spans_path = decide_dir / "spans" / f"{clip['clip_id']}.json"
            if not clip_labels or not spans_path.is_file():
                continue
            labelled_clips.append(clip)
            spans = [(s["start"], s["end"]) for s in read_json(spans_path)["spans"]]
            result = score_against_labels(spans, clip_labels, config.metrics)
            real_rows.append(result)
            strict_rows.append(
                score_against_labels(spans, clip_labels, config.metrics, mixed_as_other=True)
            )
            by_language[clip["target_language"]].append(result)
        if real_rows:
            report["real_pooled"] = pool_scores(real_rows)
            report["real_pooled_strict"] = pool_scores(strict_rows)
            report["real_pooled_by_language"] = {
                language: pool_scores(rows) for language, rows in sorted(by_language.items())
            }
            report["real_duration_breakdown"] = pool_duration_breakdowns(
                [report["real"][clip["clip_id"]]["duration_breakdown"] for clip in labelled_clips],
                config.metrics,
            )
            report["real_mixed_units"] = pool_mixed_units(
                [report["real"][clip["clip_id"]]["mixed_units"] for clip in labelled_clips]
            )
            features = {
                clip["clip_id"]: feature_rows(run_root, run_root / f"whisper-{model}", clip, config)
                for clip in labelled_clips
            }
            rows = real_sweep_rows(features, labelled_clips, labels, config)
            report["real_sweep"] = {
                "diagnostic_only": True,
                "note": (
                    "Scored against human labels after the operating point was selected on "
                    "synthetic truth. Never fed back into selection."
                ),
                "clips": [clip["clip_id"] for clip in labelled_clips],
                "rows": rows,
                "best_per_method": {
                    method: select_operating_point(
                        [row for row in rows if row["method"] == method], config.selection
                    )
                    for method in METHODS
                },
            }
    write_json(decide_dir / "report.json", report)
    if getattr(args, "write_results", False):
        write_json(RESULTS, {**report, "note": RESULTS_NOTE})
        print(f"wrote {RESULTS.relative_to(REPOSITORY)}", flush=True)
    print(
        json.dumps(
            {
                "chosen": chosen,
                "by_bucket": report["synthetic"]["by_bucket"],
                "real_pooled": report.get("real_pooled"),
            },
            indent=1,
        ),
        flush=True,
    )
    return 0


# --------------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    root.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    root.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    sub = root.add_subparsers(dest="stage", required=True)

    audio = sub.add_parser("audio")
    audio.add_argument("--media-dir", type=Path, default=DEFAULT_MEDIA_DIR)
    audio.add_argument("--synthetic-dir", type=Path, default=DEFAULT_RUN_ROOT / "synthetic")

    for name in ("chunks", "voxlingua", "whisper", "baseline"):
        stage = sub.add_parser(name)
        stage.add_argument("--clips", nargs="*", help="clip ids, or 'real' / 'synthetic'")
        stage.add_argument("--force", action="store_true")
        if name in ("whisper", "baseline"):
            stage.add_argument("--model", help="override the configured Whisper model")
        if name == "whisper":
            stage.add_argument(
                "--decode-floor",
                type=float,
                default=0.3,
                help="skip the forced decode when both detectors are below this",
            )

    for name in ("decide", "report"):
        stage = sub.add_parser(name)
        stage.add_argument("--model", help="which whisper-<model> features to use")
        stage.add_argument(
            "--captions-dir",
            type=Path,
            default=DEFAULT_RUN_ROOT / "captions",
            help="evaluation-only caption tracks named <clip>.en.vtt",
        )
        if name == "report":
            stage.add_argument(
                "--write-results",
                action="store_true",
                help=f"also refresh the committed {RESULTS.name}",
            )

    sub.add_parser("review-export")
    sub.add_parser("review-html")
    serve = sub.add_parser("review-serve", help="label in the browser; every label is saved")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--host", default="127.0.0.1", help="0.0.0.0 to open it from a phone on the local network"
    )
    serve.add_argument("--labels", type=Path, help="label store (default: committed labels/)")
    review_import = sub.add_parser("review-import")
    review_import.add_argument("--worksheet", type=Path, required=True)
    return root


COMMANDS = {
    "audio": command_audio,
    "chunks": command_chunks,
    "voxlingua": command_voxlingua,
    "whisper": command_whisper,
    "baseline": command_baseline,
    "decide": command_decide,
    "review-export": command_review_export,
    "review-html": command_review_html,
    "review-serve": command_review_serve,
    "review-import": command_review_import,
    "report": command_report,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_config(args.config)
    return COMMANDS[args.stage](args, config)


if __name__ == "__main__":
    raise SystemExit(main())
