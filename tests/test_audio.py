import hashlib
import json
import math
import shutil
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from speech_retrieval.audio import (
    AUDIO_PREPARATION_VERSION,
    AudioClipRange,
    AudioProbe,
    AudioProbeError,
    prepared_clip_paths,
    probe_audio,
    raw_audio_paths,
    validate_prepared_clip,
)

SOURCE_SHA256 = "a" * 64


def test_raw_audio_paths_resolves_the_canonical_video_cache_layout(tmp_path):
    paths = raw_audio_paths(tmp_path, language="ES-mx", video_key="vid_" + "a" * 20)

    expected = tmp_path / "raw/corpora/es-MX" / ("vid_" + "a" * 20) / "audio"
    assert paths.directory == expected
    assert paths.manifest == expected / "manifest.json"
    assert paths.source("webm") == expected / "source.webm"


@pytest.mark.parametrize("extension", [".wav", "WAV", "../wav", "web-m"])
def test_raw_audio_paths_rejects_unsafe_source_extensions(tmp_path, extension):
    paths = raw_audio_paths(tmp_path, language="es", video_key="vid_" + "a" * 20)

    with pytest.raises(ValueError, match="audio extension"):
        paths.source(extension)


def test_prepared_clip_paths_resolves_the_canonical_derived_layout(tmp_path):
    paths = prepared_clip_paths(
        tmp_path,
        language="es-MX",
        video_key="vid_" + "a" * 20,
        clip_key="clp_" + "b" * 20,
    )

    expected = tmp_path / "derived/audio/clips/es-MX" / ("vid_" + "a" * 20) / (
        "clp_" + "b" * 20
    )
    assert paths.directory == expected
    assert paths.clip == expected / "clip.wav"
    assert paths.manifest == expected / "manifest.json"


@pytest.mark.parametrize(
    ("field", "value"),
    [("language", "../es"), ("video_key", "../video"), ("clip_key", "clp_ABC")],
)
def test_prepared_clip_paths_rejects_unsafe_components(tmp_path, field, value):
    arguments = {
        "language": "es",
        "video_key": "vid_" + "a" * 20,
        "clip_key": "clp_" + "b" * 20,
        field: value,
    }

    with pytest.raises(ValueError):
        prepared_clip_paths(tmp_path, **arguments)


def test_audio_clip_range_canonicalizes_milliseconds_and_clamps_padding():
    clip_range = AudioClipRange.from_seconds(
        0.35,
        2.125,
        source_duration=2.4,
        padding=0.5,
    )

    assert clip_range == AudioClipRange(
        requested_start_ms=350,
        requested_end_ms=2125,
        padding_ms=500,
        effective_start_ms=0,
        effective_end_ms=2400,
    )
    assert clip_range.cache_key(SOURCE_SHA256).startswith("clp_")
    assert clip_range.cache_key(SOURCE_SHA256) == "clp_2e02c9674e29eb74c27b"


def test_audio_clip_cache_key_changes_with_every_material_input():
    baseline = AudioClipRange.from_seconds(1, 2, source_duration=10, padding=0.25)
    baseline_key = baseline.cache_key(SOURCE_SHA256)

    assert baseline.cache_key("b" * 64) != baseline_key
    assert baseline.cache_key(SOURCE_SHA256, preparation_version="pcm-v2") != baseline_key
    assert (
        AudioClipRange.from_seconds(1.001, 2, source_duration=10, padding=0.25).cache_key(
            SOURCE_SHA256
        )
        != baseline_key
    )
    assert (
        AudioClipRange.from_seconds(1, 2, source_duration=10, padding=0.251).cache_key(
            SOURCE_SHA256
        )
        != baseline_key
    )


@pytest.mark.parametrize(
    ("start", "end", "duration", "padding"),
    [
        (-0.001, 1, 2, 0),
        (1, 1, 2, 0),
        (2, 1, 3, 0),
        (2, 3, 1, 0),
        (0, 1, 0, 0),
        (0, 1, 2, -0.001),
        (0.0005, 1, 2, 0),
        (0, math.inf, 2, 0),
        (0, 1, math.nan, 0),
    ],
)
def test_audio_clip_range_rejects_invalid_or_ambiguous_ranges(start, end, duration, padding):
    with pytest.raises(ValueError):
        AudioClipRange.from_seconds(start, end, source_duration=duration, padding=padding)


@pytest.mark.parametrize("digest", ["", "A" * 64, "a" * 63, "g" * 64])
def test_audio_clip_cache_key_rejects_invalid_source_checksums(digest):
    clip_range = AudioClipRange.from_seconds(0, 1, source_duration=2)

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        clip_range.cache_key(digest)


def test_audio_clip_cache_key_rejects_empty_preparation_version():
    clip_range = AudioClipRange.from_seconds(0, 1, source_duration=2)

    with pytest.raises(ValueError, match="preparation_version"):
        clip_range.cache_key(SOURCE_SHA256, preparation_version=" ")


def test_audio_preparation_version_names_the_output_contract():
    assert AUDIO_PREPARATION_VERSION == "pcm-s16le-mono-16000-v1"


def probe_payload(**stream_overrides):
    return {
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "opus",
                "sample_rate": "48000",
                "channels": 2,
                "duration": "1.5",
                "bit_rate": "64000",
                **stream_overrides,
            }
        ],
        "format": {"format_name": "matroska,webm", "duration": "1.5"},
    }


def test_probe_audio_records_validated_metadata_checksum_and_command(tmp_path):
    path = tmp_path / "source.webm"
    path.write_bytes(b"provider audio bytes")
    calls = []

    def runner(arguments):
        calls.append(list(arguments))
        return json.dumps(probe_payload())

    probe = probe_audio(path, runner=runner)

    assert probe.path == path
    assert probe.duration == 1.5
    assert probe.size_bytes == len(b"provider audio bytes")
    assert probe.content_sha256 == hashlib.sha256(b"provider audio bytes").hexdigest()
    assert probe.format_name == "matroska,webm"
    assert probe.codec_name == "opus"
    assert probe.sample_rate == 48000
    assert probe.channels == 2
    assert probe.bit_rate == 64000
    assert calls[0][-1] == str(path)
    assert calls[0][:2] == ["-v", "error"]
    assert "-show_entries" in calls[0]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"streams": [], "format": {}}, "expected one audio stream, found 0"),
        (
            {
                **probe_payload(),
                "streams": [
                    *probe_payload()["streams"],
                    {"codec_type": "video", "codec_name": "vp9"},
                ],
            },
            "contains a video stream",
        ),
        (
            {**probe_payload(), "streams": probe_payload()["streams"] * 2},
            "expected one audio stream, found 2",
        ),
        ({**probe_payload(), "format": None}, "invalid format metadata"),
        (
            {
                **probe_payload(duration="0"),
                "format": {"format_name": "webm", "duration": "0"},
            },
            "duration is missing or invalid",
        ),
        (probe_payload(sample_rate="0"), "sample rate is missing or invalid"),
    ],
)
def test_probe_audio_rejects_invalid_or_non_audio_metadata(tmp_path, payload, message):
    path = tmp_path / "source.bin"
    path.write_bytes(b"not decoded by the injected runner")

    with pytest.raises(AudioProbeError, match=message):
        probe_audio(path, runner=lambda _arguments: json.dumps(payload))


def test_probe_audio_rejects_invalid_json_and_empty_files(tmp_path):
    path = tmp_path / "source.bin"
    path.write_bytes(b"content")
    with pytest.raises(AudioProbeError, match="invalid JSON"):
        probe_audio(path, runner=lambda _arguments: "not JSON")

    empty = tmp_path / "empty.wav"
    empty.touch()
    with pytest.raises(AudioProbeError, match="empty"):
        probe_audio(empty, runner=lambda _arguments: json.dumps(probe_payload()))


def test_probe_audio_rejects_missing_and_symlinked_files(tmp_path):
    missing = tmp_path / "missing.wav"
    with pytest.raises(AudioProbeError, match="does not exist or is not a regular file"):
        probe_audio(missing, runner=lambda _arguments: json.dumps(probe_payload()))

    target = tmp_path / "target.wav"
    target.write_bytes(b"content")
    link = tmp_path / "link.wav"
    link.symlink_to(target)
    with pytest.raises(AudioProbeError, match="does not exist or is not a regular file"):
        probe_audio(link, runner=lambda _arguments: json.dumps(probe_payload()))


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is not installed")
def test_probe_audio_inspects_a_generated_pcm_wave(tmp_path):
    path = tmp_path / "fixture.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)

    probe = probe_audio(path)

    assert probe.duration == pytest.approx(1.0, abs=0.001)
    assert probe.format_name == "wav"
    assert probe.codec_name == "pcm_s16le"
    assert probe.sample_rate == 16000
    assert probe.channels == 1
    assert probe.size_bytes == path.stat().st_size


def prepared_probe():
    return AudioProbe(
        path=Path("clip.wav"),
        duration=1.0,
        size_bytes=32044,
        content_sha256=SOURCE_SHA256,
        format_name="wav",
        codec_name="pcm_s16le",
        sample_rate=16000,
        channels=1,
        bit_rate=256000,
    )


def test_validate_prepared_clip_accepts_the_versioned_output_contract():
    clip_range = AudioClipRange.from_seconds(1, 2, source_duration=10)

    assert validate_prepared_clip(prepared_probe(), clip_range) is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"format_name": "webm"}, "WAV container"),
        ({"codec_name": "pcm_f32le"}, "signed 16-bit PCM"),
        ({"sample_rate": 48000}, "16 kHz"),
        ({"channels": 2}, "mono"),
        ({"duration": 1.2}, "duration differs"),
    ],
)
def test_validate_prepared_clip_rejects_contract_mismatches(changes, message):
    clip_range = AudioClipRange.from_seconds(1, 2, source_duration=10)

    with pytest.raises(AudioProbeError, match=message):
        validate_prepared_clip(replace(prepared_probe(), **changes), clip_range)
