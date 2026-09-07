from dataclasses import dataclass
from pathlib import Path

import pytest
from audio_fixtures import write_wave

from speech_retrieval.audio_features import (
    CAPTION_AGREEMENT_VERSION,
    SPEECH_RATIO_VERSION,
    SileroSettings,
    SubjectiveReference,
    caption_agreement,
    merge_intervals,
    speaking_rate,
    speech_ratio,
    squim_objective,
    squim_subjective,
    union_seconds,
)
from speech_retrieval.audio_scoring import score_disagreement


@dataclass(frozen=True)
class FakeClip:
    path: Path
    clip_key: str = "clp_" + "a" * 20
    content_sha256: str = "b" * 64
    effective_start: float = 1.0
    effective_end: float = 3.0
    duration: float = 2.0
    sample_rate: int = 16_000
    channels: int = 1


@pytest.fixture
def clip(tmp_path):
    return FakeClip(path=write_wave(tmp_path / "clip.wav", seconds=2.0))


def test_caption_agreement_reuses_the_scorer_and_names_its_metric_family(clip):
    score = score_disagreement(
        reference_text="hola que tal", hypothesis_text="hola tal", language="es"
    )

    result = caption_agreement("seg_1", score, clip=clip)

    assert result.status == "complete" and result.usable
    assert result.version == CAPTION_AGREEMENT_VERSION
    assert result.values["error_rate"] == pytest.approx(1 / 3)
    assert result.values["agreement"] == pytest.approx(2 / 3)
    assert result.values["deletions"] == 1
    assert result.implementation["metric"] == "wer"
    assert result.implementation["normalization_version"] == "word-v1"
    assert "not confirmed caption error" in result.implementation["interpretation"]
    assert result.clip_key == clip.clip_key and result.clip_sha256 == clip.content_sha256
    assert result.payload()["feature"] == "caption_asr_agreement"


def test_caption_agreement_is_unavailable_rather_than_zero_for_an_empty_reference(clip):
    score = score_disagreement(reference_text="", hypothesis_text="hola", language="es")

    result = caption_agreement("seg_1", score, clip=clip)

    assert result.status == "unavailable"
    assert result.values == {}
    assert result.error is not None and result.error.code == "empty_reference"


def test_speaking_rate_records_both_voiced_and_clip_denominators(clip):
    result = speaking_rate(
        "seg_1",
        caption_tokens=10,
        asr_tokens=12,
        clip_duration=2.0,
        speech_seconds=1.6,
        clip=clip,
        tokenizer_version="word-v1",
        language="es",
    )

    assert result.status == "complete"
    assert result.values["caption_tokens_per_clip_second"] == pytest.approx(5.0)
    assert result.values["asr_tokens_per_speech_second"] == pytest.approx(7.5)
    assert result.units["caption_tokens_per_speech_second"] == "tokens_per_second"
    assert result.implementation["cross_language_comparable"] is False


def test_speaking_rate_leaves_voiced_rates_unset_when_no_vad_result_exists(clip):
    result = speaking_rate(
        "seg_1",
        caption_tokens=4,
        asr_tokens=4,
        clip_duration=2.0,
        clip=clip,
        tokenizer_version="word-v1",
        language="es",
    )

    assert result.values["caption_tokens_per_speech_second"] is None
    assert result.values["speech_seconds"] is None


def test_speaking_rate_fails_explicitly_on_a_nonpositive_duration(clip):
    result = speaking_rate(
        "seg_1",
        caption_tokens=1,
        asr_tokens=1,
        clip_duration=0.0,
        clip=clip,
        tokenizer_version="word-v1",
        language="es",
    )

    assert result.status == "failed"
    assert result.error is not None and result.error.code == "invalid_duration"


@pytest.mark.parametrize(
    ("intervals", "merged", "seconds"),
    [
        ([], (), 0.0),
        ([(0.0, 1.0)], ((0.0, 1.0),), 1.0),
        ([(0.0, 1.0), (0.5, 1.5)], ((0.0, 1.5),), 1.5),
        ([(1.0, 2.0), (0.0, 0.5)], ((0.0, 0.5), (1.0, 2.0)), 1.5),
        ([(0.0, 1.0), (1.0, 2.0)], ((0.0, 2.0),), 2.0),
        ([(0.0, 1.0), (0.2, 0.4)], ((0.0, 1.0),), 1.0),
        ([(1.0, 1.0)], (), 0.0),
    ],
)
def test_speech_intervals_are_merged_before_voiced_seconds_are_summed(intervals, merged, seconds):
    assert merge_intervals(intervals) == merged
    assert union_seconds(intervals) == pytest.approx(seconds)


def test_speech_ratio_uses_the_prepared_waveform_and_records_vad_settings(clip):
    settings = SileroSettings(threshold=0.4, min_speech_duration_ms=100)
    received = {}

    def detector(path, given):
        received["path"] = path
        received["settings"] = given
        return [(0.0, 0.5), (0.4, 1.2)]

    result = speech_ratio("seg_1", clip, settings=settings, detector=detector)

    assert result.status == "complete"
    assert result.values["speech_seconds"] == pytest.approx(1.2)
    assert result.values["speech_ratio"] == pytest.approx(0.6)
    assert result.values["interval_count"] == 1
    assert result.version == SPEECH_RATIO_VERSION
    assert result.implementation["settings"]["threshold"] == 0.4
    assert result.input["speech_intervals"] == [[0.0, 1.2]]
    assert received["path"] == clip.path and received["settings"] is settings
    assert result.runtime_ms is not None


def test_speech_ratio_is_unavailable_without_the_optional_dependency_or_media(clip, tmp_path):
    def missing(_path, _settings):
        raise ImportError("No module named 'silero_vad'")

    unavailable = speech_ratio("seg_1", clip, detector=missing)
    assert unavailable.status == "unavailable"
    assert unavailable.error is not None
    assert unavailable.error.code == "missing_dependency"
    assert unavailable.values == {}

    gone = speech_ratio("seg_1", FakeClip(path=tmp_path / "absent.wav"), detector=missing)
    assert gone.status == "unavailable"
    assert gone.error is not None and gone.error.code == "missing_media"


def test_speech_ratio_fails_on_inference_errors_and_unsupported_waveforms(clip):
    def broken(_path, _settings):
        raise RuntimeError("model forward failed")

    failed = speech_ratio("seg_1", clip, detector=broken)
    assert failed.status == "failed"
    assert failed.error is not None and failed.error.code == "inference_failed"

    stereo = speech_ratio(
        "seg_1",
        FakeClip(path=clip.path, channels=2),
        detector=lambda _path, _settings: [(0.0, 1.0)],
    )
    assert stereo.status == "failed"
    assert stereo.error is not None and stereo.error.code == "unsupported_waveform"


def test_squim_objective_reports_each_predicted_metric_with_its_own_units(clip):
    result = squim_objective(
        "seg_1",
        clip,
        estimator=lambda _path: {"stoi": 0.91, "pesq": 2.4, "si_sdr": 11.5},
    )

    assert result.status == "complete"
    assert result.values == {"stoi": 0.91, "pesq": 2.4, "si_sdr": 11.5}
    assert result.units["si_sdr"] == "predicted_db"
    assert result.implementation["reference_free"] is True
    assert "speech enhancement" in result.implementation["training_domain"]


def test_squim_objective_separates_a_missing_dependency_from_an_inference_failure(clip):
    unavailable = squim_objective(
        "seg_1", clip, estimator=lambda _path: (_ for _ in ()).throw(ImportError("no torchaudio"))
    )
    assert unavailable.status == "unavailable"
    assert unavailable.error is not None
    assert unavailable.error.code == "missing_dependency"

    failed = squim_objective(
        "seg_1", clip, estimator=lambda _path: (_ for _ in ()).throw(ValueError("bad rate"))
    )
    assert failed.status == "failed"
    assert failed.error is not None and failed.error.code == "inference_failed"


def test_squim_subjective_mos_is_unavailable_without_a_non_matching_reference(clip):
    result = squim_subjective("seg_1", clip, reference=None)

    assert result.status == "unavailable"
    assert result.error is not None
    assert result.error.code == "missing_non_matching_reference"
    assert result.implementation["requires_non_matching_reference"] is True
    assert result.values == {}


def test_squim_subjective_records_the_reference_provenance_it_used(clip, tmp_path):
    reference = SubjectiveReference(
        path=write_wave(tmp_path / "reference.wav", seconds=1.0),
        source="operator-provided clean speech",
        license="CC0-1.0",
        content_sha256="c" * 64,
    )

    result = squim_subjective(
        "seg_1", clip, reference=reference, estimator=lambda _clip, _ref: {"mos": 3.7}
    )

    assert result.status == "complete"
    assert result.values == {"mos": 3.7}
    assert result.implementation["reference"]["license"] == "CC0-1.0"
    assert result.implementation["reference"]["content_sha256"] == "c" * 64


def test_a_missing_reference_file_is_also_unavailable_rather_than_invented(clip, tmp_path):
    reference = SubjectiveReference(
        path=tmp_path / "absent.wav", source="s", license="l", content_sha256="d" * 64
    )

    result = squim_subjective("seg_1", clip, reference=reference)

    assert result.status == "unavailable"
    assert result.error is not None
    assert result.error.code == "missing_non_matching_reference"
