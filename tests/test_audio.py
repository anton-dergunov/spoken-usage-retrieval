import hashlib
import json
import math
import shutil
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from audio_fixtures import (
    fake_conversion,
    fake_probe_runner,
    install_caption_video,
    install_raw_audio,
    write_wave,
)

from speech_retrieval.audio import (
    AUDIO_PREPARATION_VERSION,
    AudioCacheError,
    AudioClipRange,
    AudioProbe,
    AudioProbeError,
    AudioPruneRequest,
    audio_availability,
    audio_storage,
    execute_audio_prune,
    plan_audio_prune,
    prepare_clip,
    prepared_clip_paths,
    probe_audio,
    raw_audio_paths,
    validate_audio_integrity,
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

    expected = tmp_path / "derived/audio/clips/es-MX" / ("vid_" + "a" * 20) / ("clp_" + "b" * 20)
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


def test_validate_audio_integrity_accepts_matching_manifest_fields():
    probe = prepared_probe()

    validate_audio_integrity(
        probe,
        expected_size_bytes=probe.size_bytes,
        expected_sha256=probe.content_sha256,
    )


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"expected_size_bytes": 1}, "size"),
        ({"expected_sha256": "b" * 64}, "checksum"),
    ],
)
def test_validate_audio_integrity_rejects_manifest_mismatches(fields, message):
    probe = prepared_probe()
    expected = {
        "expected_size_bytes": probe.size_bytes,
        "expected_sha256": probe.content_sha256,
        **fields,
    }

    with pytest.raises(AudioProbeError, match=message):
        validate_audio_integrity(probe, **expected)


def test_validate_prepared_clip_accepts_the_versioned_output_contract():
    clip_range = AudioClipRange.from_seconds(1, 2, source_duration=10)

    validate_prepared_clip(prepared_probe(), clip_range)


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


def real_probe_or_fake(duration):
    return None if shutil.which("ffprobe") else fake_probe_runner(duration)


def install_ready_audio(tmp_path, *, seconds=2.0, provider_video_id="video-1", channel="channel-a"):
    key = install_caption_video(tmp_path, provider_video_id=provider_video_id, channel=channel)
    source = write_wave(tmp_path / f"{provider_video_id}-source.wav", seconds=seconds)
    install_raw_audio(
        tmp_path,
        key=key,
        provider_video_id=provider_video_id,
        source=source,
        duration=seconds,
    )
    return key


def test_audio_availability_reports_missing_ready_and_failed_states(tmp_path):
    key = install_caption_video(tmp_path)
    assert audio_availability(tmp_path, language="es", video_key=key).status == "missing"

    install_raw_audio(tmp_path, key=key, status="failed")
    failed = audio_availability(tmp_path, language="es", video_key=key)
    assert failed.status == "failed"
    assert "429" in (failed.error or "")

    source = write_wave(tmp_path / "source.wav", seconds=2.0)
    install_raw_audio(tmp_path, key=key, source=source, duration=2.0)
    ready = audio_availability(tmp_path, language="es", video_key=key)
    assert ready.status == "ready" and ready.ready
    assert ready.source_path is not None and ready.source_path.name == "source.wav"
    assert ready.duration == 2.0
    assert ready.content_sha256 is not None


@pytest.mark.parametrize("verify_checksum", [False, True])
def test_audio_availability_rejects_a_payload_that_lost_its_bytes(tmp_path, verify_checksum):
    key = install_ready_audio(tmp_path)
    paths = raw_audio_paths(tmp_path, language="es", video_key=key)
    paths.source("wav").unlink()

    record = audio_availability(
        tmp_path, language="es", video_key=key, verify_checksum=verify_checksum
    )
    assert record.status == "failed"
    assert "missing" in (record.error or "")


def test_audio_availability_detects_corruption_only_when_the_checksum_is_verified(tmp_path):
    key = install_ready_audio(tmp_path)
    source = raw_audio_paths(tmp_path, language="es", video_key=key).source("wav")
    content = bytearray(source.read_bytes())
    content[-1] ^= 0xFF
    source.write_bytes(bytes(content))

    assert audio_availability(tmp_path, language="es", video_key=key).status == "ready"
    verified = audio_availability(tmp_path, language="es", video_key=key, verify_checksum=True)
    assert verified.status == "failed"
    assert "checksum" in (verified.error or "")


def test_prepare_clip_generates_validates_and_then_reuses_a_cached_clip(tmp_path):
    key = install_ready_audio(tmp_path)
    convert = fake_conversion()

    clip = prepare_clip(
        tmp_path,
        language="es",
        video_key=key,
        start=0.5,
        end=1.5,
        conversion_runner=convert,
        probe_runner=real_probe_or_fake(1.0),
        ffmpeg_version="ffmpeg version fixture",
    )

    assert clip.cache_hit is False
    assert clip.path.is_file() and clip.path.name == "clip.wav"
    assert clip.sample_rate == 16000 and clip.channels == 1
    assert clip.sample_format == "s16le"
    assert (clip.requested_start, clip.requested_end) == (0.5, 1.5)
    assert (clip.effective_start, clip.effective_end) == (0.5, 1.5)
    assert clip.padding_before == 0 and clip.padding_after == 0
    assert clip.preparation_version == AUDIO_PREPARATION_VERSION
    assert clip.ffmpeg_version == "ffmpeg version fixture"
    assert clip.content_sha256 == hashlib.sha256(clip.path.read_bytes()).hexdigest()
    assert json.loads(clip.manifest_path.read_text())["clip_key"] == clip.clip_key

    again = prepare_clip(
        tmp_path,
        language="es",
        video_key=key,
        start=0.5,
        end=1.5,
        conversion_runner=convert,
        probe_runner=real_probe_or_fake(1.0),
    )
    assert again.cache_hit is True
    assert again.clip_key == clip.clip_key
    assert len(convert.calls) == 1


def test_prepare_clip_clamps_padding_and_records_the_effective_range(tmp_path):
    key = install_ready_audio(tmp_path, seconds=2.0)

    clip = prepare_clip(
        tmp_path,
        language="es",
        video_key=key,
        start=0.2,
        end=1.9,
        padding=0.5,
        conversion_runner=fake_conversion(),
        probe_runner=real_probe_or_fake(2.0),
    )

    assert (clip.effective_start, clip.effective_end) == (0.0, 2.0)
    assert clip.padding_before == 0.2
    assert clip.padding_after == 0.1
    assert clip.clip_range.padding_ms == 500


def test_prepare_clip_uses_accurate_seek_and_the_named_pcm_contract(tmp_path):
    key = install_ready_audio(tmp_path)
    convert = fake_conversion()

    prepare_clip(
        tmp_path,
        language="es",
        video_key=key,
        start=0.25,
        end=0.75,
        conversion_runner=convert,
        probe_runner=real_probe_or_fake(0.5),
    )

    arguments = convert.calls[0]
    assert arguments[:5] == ["-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    assert "-accurate_seek" in arguments
    assert arguments[arguments.index("-ss") + 1] == "0.250"
    assert arguments[arguments.index("-t") + 1] == "0.500"
    for flag, value in (("-ac", "1"), ("-ar", "16000"), ("-c:a", "pcm_s16le"), ("-f", "wav")):
        assert arguments[arguments.index(flag) + 1] == value
    assert "-vn" in arguments


def test_prepare_clip_keys_differ_for_different_requests_and_preparation_versions(tmp_path):
    key = install_ready_audio(tmp_path)
    common = {
        "language": "es",
        "video_key": key,
        "conversion_runner": fake_conversion(),
        "probe_runner": real_probe_or_fake(0.5),
    }

    first = prepare_clip(tmp_path, start=0.0, end=0.5, **common)
    second = prepare_clip(tmp_path, start=0.5, end=1.0, **common)
    versioned = prepare_clip(
        tmp_path,
        start=0.0,
        end=0.5,
        preparation_version="pcm-s16le-mono-16000-v2",
        **common,
    )

    assert len({first.clip_key, second.clip_key, versioned.clip_key}) == 3
    assert first.path != second.path != versioned.path
    assert all(item.path.is_file() for item in (first, second, versioned))


def test_prepare_clip_rebuilds_when_a_cached_clip_is_corrupted(tmp_path):
    key = install_ready_audio(tmp_path)
    convert = fake_conversion()
    arguments = {
        "language": "es",
        "video_key": key,
        "start": 0.0,
        "end": 0.5,
        "conversion_runner": convert,
        "probe_runner": real_probe_or_fake(0.5),
    }
    clip = prepare_clip(tmp_path, **arguments)
    clip.path.write_bytes(clip.path.read_bytes() + b"corrupt")

    rebuilt = prepare_clip(tmp_path, **arguments)

    assert rebuilt.cache_hit is False
    assert len(convert.calls) == 2
    assert rebuilt.content_sha256 == hashlib.sha256(rebuilt.path.read_bytes()).hexdigest()


def test_prepare_clip_cleans_up_and_reports_conversion_and_contract_failures(tmp_path):
    key = install_ready_audio(tmp_path)
    clip_root = tmp_path / "derived/audio/clips/es" / key

    with pytest.raises(AudioCacheError, match="ffmpeg"):
        prepare_clip(
            tmp_path,
            language="es",
            video_key=key,
            start=0.0,
            end=0.5,
            conversion_runner=fake_conversion(fail=True),
        )
    assert not clip_root.exists() or not any(clip_root.iterdir())

    with pytest.raises(AudioCacheError, match="produced no audio"):
        prepare_clip(
            tmp_path,
            language="es",
            video_key=key,
            start=0.0,
            end=0.5,
            conversion_runner=fake_conversion(produce=False),
        )

    with pytest.raises(AudioProbeError, match="duration differs"):
        prepare_clip(
            tmp_path,
            language="es",
            video_key=key,
            start=0.0,
            end=0.5,
            conversion_runner=fake_conversion(duration_override=1.4),
            probe_runner=real_probe_or_fake(1.4),
        )
    assert not clip_root.exists() or not any(clip_root.iterdir())


def test_prepare_clip_requires_ready_source_audio(tmp_path):
    key = install_caption_video(tmp_path)

    with pytest.raises(AudioCacheError, match="source audio is not available"):
        prepare_clip(tmp_path, language="es", video_key=key, start=0.0, end=0.5)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="local ffmpeg integration requires ffmpeg and ffprobe",
)
def test_prepare_clip_runs_real_ffmpeg_on_generated_audio(tmp_path):
    key = install_caption_video(tmp_path)
    source = write_wave(tmp_path / "stereo.wav", seconds=3.0, sample_rate=44_100, channels=2)
    install_raw_audio(
        tmp_path,
        key=key,
        source=source,
        duration=3.0,
        format_name="wav",
        sample_rate=44_100,
        channels=2,
    )

    clip = prepare_clip(tmp_path, language="es", video_key=key, start=1.0, end=2.0)

    assert clip.channels == 1
    assert clip.sample_rate == 16_000
    assert clip.duration == pytest.approx(1.0, abs=0.02)
    assert clip.size_bytes > 0
    with wave.open(str(clip.path), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getframerate() == 16_000
        assert reader.getsampwidth() == 2


def build_audio_corpus(tmp_path):
    """Two Spanish videos on different channels plus one English video, all with clips."""
    keys = {}
    for provider_video_id, language, channel, seconds in (
        ("video-1", "es", "channel-a", 2.0),
        ("video-2", "es", "channel-b", 2.0),
        ("video-3", "en", "channel-c", 2.0),
    ):
        key = install_caption_video(
            tmp_path,
            provider_video_id=provider_video_id,
            language=language,
            channel=channel,
        )
        source = write_wave(tmp_path / f"{provider_video_id}.wav", seconds=seconds)
        install_raw_audio(
            tmp_path,
            key=key,
            language=language,
            provider_video_id=provider_video_id,
            source=source,
            duration=seconds,
        )
        prepare_clip(
            tmp_path,
            language=language,
            video_key=key,
            start=0.0,
            end=0.5,
            conversion_runner=fake_conversion(),
            probe_runner=real_probe_or_fake(0.5),
        )
        keys[provider_video_id] = (language, key)
    return keys


def recognized_bytes(tmp_path):
    total = 0
    for path in tmp_path.rglob("*"):
        if not path.is_file():
            continue
        if path.parent.name == "audio" and path.name in {"manifest.json", "source.wav"}:
            total += path.stat().st_size
        elif path.parent.name.startswith("clp_") and path.name in {"clip.wav", "manifest.json"}:
            total += path.stat().st_size
    return total


def test_audio_storage_totals_match_disk_and_group_by_language_channel_and_video(tmp_path):
    build_audio_corpus(tmp_path)

    summary = audio_storage(tmp_path)

    assert summary.videos == 3
    assert summary.ready == 3 and summary.missing == 0 and summary.failed == 0
    assert summary.derived_clips == 3
    assert summary.raw_bytes + summary.derived_bytes == recognized_bytes(tmp_path)
    assert [item.language for item in summary.languages] == ["en", "es"]
    spanish = next(item for item in summary.languages if item.language == "es")
    assert spanish.videos == 2 and spanish.derived_clips == 2
    assert sorted(channel.channel or "" for channel in spanish.channels) == [
        "channel-a",
        "channel-b",
    ]
    assert all(channel.videos == 1 for channel in spanish.channels)
    assert sum(item.raw_bytes for item in summary.video_details) == summary.raw_bytes
    assert summary.issues == ()


def test_audio_storage_counts_missing_and_failed_videos_without_hiding_them(tmp_path):
    install_caption_video(tmp_path, provider_video_id="plain")
    failed_key = install_caption_video(tmp_path, provider_video_id="broken")
    install_raw_audio(tmp_path, key=failed_key, provider_video_id="broken", status="failed")

    summary = audio_storage(tmp_path)

    assert (summary.videos, summary.ready, summary.missing, summary.failed) == (2, 0, 1, 1)
    assert summary.raw_bytes > 0
    statuses = {item.video_key: item.status for item in summary.video_details}
    assert statuses[failed_key] == "failed"


def test_audio_storage_reports_unrecognized_and_orphaned_artifacts_separately(tmp_path):
    keys = build_audio_corpus(tmp_path)
    language, key = keys["video-1"]
    (
        raw_audio_paths(tmp_path, language=language, video_key=key).directory / "stray.bin"
    ).write_bytes(b"leftover")
    orphan = tmp_path / "derived/audio/clips/es" / ("vid_" + "f" * 20) / ("clp_" + "f" * 20)
    orphan.mkdir(parents=True)
    (orphan / "clip.wav").write_bytes(b"orphaned clip")

    summary = audio_storage(tmp_path)

    kinds = sorted(item.kind for item in summary.issues)
    assert kinds == ["orphan", "unrecognized"]
    unrecognized = next(item for item in summary.issues if item.kind == "unrecognized")
    assert unrecognized.path.name == "stray.bin" and unrecognized.bytes == len(b"leftover")
    assert summary.derived_clips == 3


def test_audio_storage_accepts_a_language_filter(tmp_path):
    build_audio_corpus(tmp_path)

    summary = audio_storage(tmp_path, languages=["en"])

    assert summary.videos == 1
    assert [item.language for item in summary.languages] == ["en"]


def test_audio_prune_requires_an_explicit_selector():
    with pytest.raises(ValueError, match="at least one prune selector"):
        AudioPruneRequest()

    with pytest.raises(ValueError, match="older_than_days"):
        AudioPruneRequest(older_than_days=-1)

    assert AudioPruneRequest(select_all=True).select_all
    assert AudioPruneRequest(languages=("ES",)).languages == ("es",)


def snapshot(tmp_path):
    return {
        str(path.relative_to(tmp_path)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    }


def test_audio_prune_dry_run_changes_nothing_and_previews_the_same_plan(tmp_path):
    build_audio_corpus(tmp_path)
    before = snapshot(tmp_path)

    plan = plan_audio_prune(tmp_path, AudioPruneRequest(select_all=True))

    assert plan.dry_run is True
    assert plan.derived_clips == 3
    assert plan.raw_videos == 0
    assert plan.derived_bytes > 0
    assert all(item.deleted is False for item in plan.entries)
    assert snapshot(tmp_path) == before


def test_audio_prune_removes_only_derived_clips_by_default(tmp_path):
    keys = build_audio_corpus(tmp_path)
    language, key = keys["video-1"]

    result = execute_audio_prune(tmp_path, AudioPruneRequest(select_all=True))

    assert result.dry_run is False
    assert result.derived_clips == 3
    assert all(item.deleted for item in result.entries)
    assert not (tmp_path / "derived/audio/clips/es" / key).exists()
    assert audio_availability(tmp_path, language=language, video_key=key).ready
    assert (tmp_path / "raw/corpora/es" / key / "manifest.json").is_file()
    summary = audio_storage(tmp_path)
    assert summary.derived_clips == 0 and summary.ready == 3

    repeated = execute_audio_prune(tmp_path, AudioPruneRequest(select_all=True))
    assert repeated.entries == ()


def test_audio_prune_needs_an_explicit_flag_to_touch_raw_audio(tmp_path):
    keys = build_audio_corpus(tmp_path)
    language, key = keys["video-1"]
    track_directories = sorted(
        path.name
        for path in (tmp_path / "raw/corpora/es" / key).iterdir()
        if path.is_dir() and path.name != "audio"
    )

    result = execute_audio_prune(
        tmp_path,
        AudioPruneRequest(video_keys=(key,), include_raw_audio=True),
    )

    assert result.raw_videos == 1 and result.raw_bytes > 0
    assert result.derived_clips == 1
    assert audio_availability(tmp_path, language=language, video_key=key).status == "missing"
    assert not raw_audio_paths(tmp_path, language=language, video_key=key).directory.exists()
    assert (
        sorted(
            path.name
            for path in (tmp_path / "raw/corpora/es" / key).iterdir()
            if path.is_dir() and path.name != "audio"
        )
        == track_directories
    )
    assert (tmp_path / "raw/corpora/es" / key / "manifest.json").is_file()
    remaining = audio_storage(tmp_path)
    assert remaining.videos == 3 and remaining.ready == 2 and remaining.missing == 1


def test_audio_prune_reports_clips_left_orphaned_by_a_raw_deletion(tmp_path):
    keys = build_audio_corpus(tmp_path)
    language, key = keys["video-1"]

    plan = plan_audio_prune(
        tmp_path,
        AudioPruneRequest(
            video_keys=(key,),
            include_raw_audio=True,
            preparation_versions=("pcm-s16le-mono-16000-v9",),
        ),
    )

    assert plan.derived_clips == 0
    assert plan.raw_videos == 1
    assert len(plan.orphaned_clips) == 1
    assert plan.orphaned_clips[0].parent.name == key


def test_audio_prune_honors_language_channel_and_age_selectors(tmp_path):
    keys = build_audio_corpus(tmp_path)

    by_language = plan_audio_prune(tmp_path, AudioPruneRequest(languages=("en",)))
    assert {item.language for item in by_language.entries} == {"en"}

    by_channel = plan_audio_prune(tmp_path, AudioPruneRequest(channels=("channel-b",)))
    assert [item.video_key for item in by_channel.entries] == [keys["video-2"][1]]

    fresh = plan_audio_prune(tmp_path, AudioPruneRequest(older_than_days=30))
    assert fresh.entries == ()


def test_audio_prune_ignores_symlinked_clip_directories(tmp_path):
    keys = build_audio_corpus(tmp_path)
    _language, key = keys["video-1"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "clip.wav").write_bytes(b"not ours")
    link = tmp_path / "derived/audio/clips/es" / key / ("clp_" + "e" * 20)
    link.symlink_to(outside, target_is_directory=True)

    result = execute_audio_prune(tmp_path, AudioPruneRequest(select_all=True))

    assert all(item.path != link for item in result.entries)
    assert (outside / "clip.wav").is_file()
