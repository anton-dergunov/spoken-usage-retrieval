import hashlib
import json
import wave
from pathlib import Path

import pytest
from audio_fixtures import (
    install_caption_video,
    install_raw_audio,
    probe_payload,
    write_wave,
)

from speech_retrieval.acquisition import acquire
from speech_retrieval.audio import audio_availability, raw_audio_paths, read_raw_audio_manifest
from speech_retrieval.audio_acquisition import (
    AUDIO_FORMAT_POLICY_VERSION,
    AudioAcquisitionError,
    AudioFormatPolicy,
    acquire_audio,
    audio_format_candidates,
    classify_failure,
    select_audio_format,
)


def formats(*entries):
    return {"id": "video-1", "duration": 120.0, "formats": list(entries)}


def audio_format(format_id, **overrides):
    return {
        "format_id": format_id,
        "vcodec": "none",
        "acodec": "opus",
        "audio_ext": "webm",
        "ext": "webm",
        "abr": 130.0,
        "asr": 48000,
        "audio_channels": 2,
        "protocol": "https",
        **overrides,
    }


def test_format_selection_prefers_the_smallest_advertised_audio_only_file():
    info = formats(
        {"format_id": "137", "vcodec": "vp9", "acodec": "none", "filesize": 10},
        audio_format("251", filesize=2_000_000),
        audio_format("140", acodec="mp4a.40.2", ext="m4a", abr=128.0, filesize_approx=1_500_000),
    )

    selection = select_audio_format(info)

    assert selection.format_id == "140"
    assert selection.reason == "smallest_known_size"
    assert selection.policy_version == AUDIO_FORMAT_POLICY_VERSION
    assert selection.candidate.codec_family == "mp4a"
    assert selection.constraints["minimum_bitrate_kbps"] == 48.0
    assert selection.constraints["audio_only"] is True
    assert {item.format_id for item in selection.considered} == {"251", "140"}
    payload = selection.payload()
    assert payload["chosen"]["filesize_approx"] == 1_500_000
    assert payload["constraints"]["policy_version"] == AUDIO_FORMAT_POLICY_VERSION


def test_format_selection_records_every_policy_rejection_reason():
    info = formats(
        audio_format("low", abr=24.0),
        audio_format("narrow", asr=8000),
        audio_format("drm", has_drm=True),
        audio_format("weird", acodec="ec-9"),
        audio_format("silent", acodec="none"),
        audio_format("good", filesize=1),
    )

    candidates = {item.format_id: item for item in audio_format_candidates(info)}

    assert "below the 48.0 floor" in (candidates["low"].rejected or "")
    assert "below the 16000 floor" in (candidates["narrow"].rejected or "")
    assert candidates["drm"].rejected == "digital restrictions"
    assert "not allowed by the policy" in (candidates["weird"].rejected or "")
    assert candidates["silent"].rejected == "no audio codec"
    assert candidates["good"].rejected is None
    assert select_audio_format(info).format_id == "good"


def test_format_selection_falls_back_deterministically_when_sizes_are_unknown():
    info = formats(
        audio_format("high", abr=160.0),
        audio_format("mid", abr=96.0),
        audio_format("also-mid", abr=96.0, acodec="mp4a.40.2"),
    )

    selection = select_audio_format(info)

    assert selection.reason == "quality_fallback"
    assert selection.format_id == "mid"
    assert selection.candidate.abr == 96.0


def test_format_selection_deprioritizes_fragmented_manifest_protocols():
    info = formats(
        audio_format("hls", protocol="m3u8_native", filesize=1_000),
        audio_format("progressive", filesize=5_000),
    )

    assert select_audio_format(info).format_id == "progressive"


def test_format_selection_rejects_a_video_only_or_empty_format_list():
    with pytest.raises(AudioAcquisitionError, match="no audio-only provider format"):
        select_audio_format(formats({"format_id": "137", "vcodec": "vp9", "acodec": "none"}))
    with pytest.raises(AudioAcquisitionError) as raised:
        select_audio_format(formats())
    assert raised.value.code == "no_audio_format"
    assert raised.value.retryable is False


def test_format_policy_floor_is_configurable_and_recorded():
    info = formats(audio_format("251", abr=64.0, filesize=1))
    policy = AudioFormatPolicy(minimum_bitrate_kbps=96.0)

    with pytest.raises(AudioAcquisitionError):
        select_audio_format(info, policy=policy)
    assert select_audio_format(info).constraints["minimum_bitrate_kbps"] == 48.0


@pytest.mark.parametrize(
    ("message", "code", "retryable"),
    [
        ("HTTP Error 429: Too Many Requests", "provider_temporary", True),
        ("Read timed out", "provider_temporary", True),
        ("Private video. Sign in if you've been granted access", "provider_rejected", False),
        ("Something unexpected", "audio_acquisition_failed", False),
    ],
)
def test_failure_classification_separates_temporary_from_permanent(message, code, retryable):
    assert classify_failure(message) == (code, retryable)


def media_runner(*, extension="webm", seconds=120.0, errors=None, payload=None):
    """A yt-dlp stand-in that writes one media file into the staging directory."""
    calls = []
    queue = list(errors or ())

    def run(arguments):
        arguments = list(arguments)
        calls.append(arguments)
        if queue:
            raise RuntimeError(queue.pop(0))
        template = Path(arguments[arguments.index("-o") + 1])
        target = Path(str(template).replace("%(ext)s", extension))
        if payload is not None:
            target.write_bytes(payload)
        else:
            write_wave(target, seconds=seconds)
        return ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def probe_runner(**overrides):
    def run(arguments):
        return probe_payload(**overrides)

    return run


def acquire_one(tmp_path, *, runner, info=None, probe=None, **kwargs):
    key = kwargs.pop("key", None) or install_caption_video(tmp_path)
    return key, acquire_audio(
        data_dir=tmp_path,
        language="es",
        video_key=key,
        video_id="video-1",
        url="https://www.youtube.com/watch?v=video-1",
        info=info if info is not None else formats(audio_format("251", filesize=1_000)),
        runner=runner,
        probe_runner=probe or probe_runner(format_name="matroska,webm", duration=120.0),
        sleep=lambda _seconds: None,
        yt_dlp_version="2026.8.19",
        **kwargs,
    )


def test_acquire_audio_publishes_an_immutable_payload_with_full_provenance(tmp_path):
    runner = media_runner()

    key, result = acquire_one(tmp_path, runner=runner)

    assert result.status == "downloaded" and result.ready
    assert result.format_id == "251"
    paths = raw_audio_paths(tmp_path, language="es", video_key=key)
    source = paths.source("webm")
    assert source.is_file()
    manifest = read_raw_audio_manifest(paths)
    assert manifest is not None and manifest["status"] == "ready"
    raw = manifest["raw_audio"]
    assert raw["filename"] == "source.webm" and raw["extension"] == "webm"
    assert raw["provider_format_id"] == "251"
    assert raw["provider_abr"] == 130.0
    assert raw["provider_duration"] == 120.0
    assert raw["size_bytes"] == source.stat().st_size
    assert raw["content_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert raw["codec_name"] == "pcm_s16le"
    assert manifest["tool_versions"]["yt_dlp"] == "2026.8.19"
    assert manifest["format_selection"]["reason"] == "smallest_known_size"
    assert manifest["attempts"] == []
    arguments = runner.calls[0]
    assert arguments[:2] == ["-f", "251"]
    for flag in ("--no-playlist", "--no-overwrites", "--no-part"):
        assert flag in arguments
    assert arguments[arguments.index("--retries") + 1] == "3"
    assert "exp=1:20" in arguments
    assert audio_availability(tmp_path, language="es", video_key=key).ready


def test_acquire_audio_reuses_a_valid_payload_without_any_provider_call(tmp_path):
    runner = media_runner()
    key, first = acquire_one(tmp_path, runner=runner)

    reuse = media_runner()
    _key, second = acquire_one(tmp_path, runner=reuse, key=key)

    assert first.status == "downloaded"
    assert second.status == "cached"
    assert second.format_id == "251"
    assert reuse.calls == []


def test_acquire_audio_redownloads_when_the_cached_checksum_no_longer_matches(tmp_path):
    key = install_caption_video(tmp_path)
    source = write_wave(tmp_path / "source.wav", seconds=2.0)
    install_raw_audio(tmp_path, key=key, source=source, duration=2.0)
    stored = raw_audio_paths(tmp_path, language="es", video_key=key).source("wav")
    stored.write_bytes(stored.read_bytes() + b"tampered")
    runner = media_runner(extension="wav", seconds=120.0)

    _key, result = acquire_one(
        tmp_path,
        runner=runner,
        key=key,
        probe=probe_runner(format_name="wav", duration=120.0),
    )

    assert result.status == "downloaded"
    assert len(runner.calls) == 1
    assert not stored.read_bytes().endswith(b"tampered")


def test_acquire_audio_rejects_media_that_is_not_audio_only(tmp_path):
    key, result = acquire_one(
        tmp_path,
        runner=media_runner(),
        probe=probe_runner(include_video=True, duration=120.0),
    )

    assert result.status == "failed"
    assert result.error_code == "invalid_media"
    paths = raw_audio_paths(tmp_path, language="es", video_key=key)
    manifest = read_raw_audio_manifest(paths)
    assert manifest is not None and manifest["status"] == "failed"
    assert manifest["raw_audio"] is None
    assert manifest["attempts"][-1]["stage"] == "probe"
    assert list(paths.directory.glob("source.*")) == []
    assert audio_availability(tmp_path, language="es", video_key=key).status == "failed"


def test_acquire_audio_rejects_a_duration_that_disagrees_with_the_provider(tmp_path):
    _key, result = acquire_one(
        tmp_path,
        runner=media_runner(seconds=5.0),
        probe=probe_runner(format_name="wav", duration=5.0),
        attempts=1,
    )

    assert result.status == "failed"
    assert result.error_code == "duration_mismatch"
    assert result.attempts[-1]["stage"] == "validation"


def test_acquire_audio_retries_temporary_failures_with_bounded_backoff(tmp_path):
    delays: list[float] = []
    runner = media_runner(errors=["HTTP Error 503: Service Unavailable", "Read timed out"])

    key = install_caption_video(tmp_path)
    result = acquire_audio(
        data_dir=tmp_path,
        language="es",
        video_key=key,
        video_id="video-1",
        url="https://www.youtube.com/watch?v=video-1",
        info=formats(audio_format("251", filesize=1_000)),
        runner=runner,
        probe_runner=probe_runner(format_name="matroska,webm", duration=120.0),
        attempts=3,
        backoff_seconds=1.5,
        sleep=delays.append,
    )

    assert result.status == "downloaded"
    assert len(runner.calls) == 3
    assert delays == [1.5, 3.0]
    manifest = result.manifest or {}
    assert [item["error_code"] for item in manifest["attempts"]] == [
        "provider_temporary",
        "provider_temporary",
    ]
    assert all(item["exhausted"] is False for item in manifest["attempts"])


def test_acquire_audio_stops_retrying_permanent_failures_and_sanitizes_diagnostics(tmp_path):
    runner = media_runner(errors=["Private video: see https://youtube.test/watch?v=x&token=secret"])

    key, result = acquire_one(tmp_path, runner=runner, attempts=3)

    assert result.status == "failed"
    assert result.error_code == "provider_rejected"
    assert len(runner.calls) == 1
    assert "token=secret" not in (result.error or "")
    assert "<url>" in (result.error or "")
    manifest = read_raw_audio_manifest(raw_audio_paths(tmp_path, language="es", video_key=key))
    assert manifest is not None
    assert manifest["attempts"][-1]["exhausted"] is True
    assert manifest["attempts"][-1]["retryable"] is False


def test_acquire_audio_bounds_recorded_attempt_history(tmp_path):
    key = install_caption_video(tmp_path)
    for _index in range(3):
        result = acquire_audio(
            data_dir=tmp_path,
            language="es",
            video_key=key,
            video_id="video-1",
            url="https://www.youtube.com/watch?v=video-1",
            info=formats(audio_format("251", filesize=1_000)),
            runner=media_runner(errors=["Read timed out", "Read timed out"]),
            probe_runner=probe_runner(duration=120.0),
            attempts=2,
            sleep=lambda _seconds: None,
        )
        assert result.status == "failed"

    manifest = read_raw_audio_manifest(raw_audio_paths(tmp_path, language="es", video_key=key))
    assert manifest is not None
    assert len(manifest["attempts"]) == 5
    assert manifest["attempts"][-1]["exhausted"] is True


def test_acquire_audio_records_a_failure_when_no_format_qualifies(tmp_path):
    runner = media_runner()

    key, result = acquire_one(tmp_path, runner=runner, info=formats(audio_format("251", abr=16.0)))

    assert result.status == "failed"
    assert result.error_code == "no_audio_format"
    assert runner.calls == []
    manifest = read_raw_audio_manifest(raw_audio_paths(tmp_path, language="es", video_key=key))
    assert manifest is not None and manifest["attempts"][-1]["stage"] == "selection"


def test_acquire_audio_rejects_more_than_one_downloaded_media_file(tmp_path):
    def runner(arguments):
        template = Path(list(arguments)[list(arguments).index("-o") + 1])
        for extension in ("webm", "m4a"):
            write_wave(Path(str(template).replace("%(ext)s", extension)), seconds=0.2)
        return ""

    _key, result = acquire_one(tmp_path, runner=runner)

    assert result.status == "failed"
    assert result.error_code == "unexpected_download_output"


def caption_runner(tmp_path, *, audio_calls, audio_error=None):
    """Serve discovery, metadata, captions, and audio through one recorded runner."""

    def runner(arguments):
        arguments = list(arguments)
        if "--flat-playlist" in arguments:
            return json.dumps(
                {
                    "entries": [
                        {
                            "id": "video-1",
                            "url": "https://youtube.test/video-1",
                            "title": "Fixture",
                            "duration": 120,
                            "live_status": "not_live",
                        }
                    ]
                }
            )
        if "--dump-single-json" in arguments:
            return json.dumps(
                {
                    "id": "video-1",
                    "webpage_url": "https://youtube.test/video-1",
                    "title": "Fixture",
                    "channel": "One",
                    "duration": 120,
                    "license": "Standard YouTube License",
                    "subtitles": {"es": [{"ext": "json3"}]},
                    "automatic_captions": {},
                    "formats": [audio_format("251", filesize=1_000)],
                }
            )
        if "-f" in arguments:
            audio_calls.append(arguments)
            if audio_error is not None:
                raise RuntimeError(audio_error)
            template = Path(arguments[arguments.index("-o") + 1])
            write_wave(Path(str(template).replace("%(ext)s", "webm")), seconds=120.0)
            return ""
        output = Path(arguments[arguments.index("-o") + 1].replace("%(ext)s", "es.json3"))
        output.write_text(
            json.dumps(
                {"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "Una frase."}]}]}
            )
        )
        return ""

    return runner


def channel_config(tmp_path):
    path = tmp_path / "es.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "language": "es",
                "sections": [
                    {
                        "id": "fixtures",
                        "name": "Fixtures",
                        "channels": [
                            {
                                "id": "one",
                                "name": "One",
                                "url": "https://one.example/videos",
                                "enabled": True,
                            }
                        ],
                    }
                ],
            }
        )
    )
    return path


def test_default_acquisition_makes_no_audio_calls_and_writes_no_audio_files(tmp_path):
    audio_calls: list[list[str]] = []
    data_dir = tmp_path / "data"

    report = acquire(
        config_path=channel_config(tmp_path),
        data_dir=data_dir,
        limit=1,
        runner=caption_runner(tmp_path, audio_calls=audio_calls),
    )

    assert report["complete"] is True
    assert report["audio_requested"] is False
    assert report["audio_complete"] is True
    assert report["audio_downloaded"] == report["audio_cached"] == report["audio_failed"] == 0
    assert report["videos"][0]["audio"]["status"] == "not_requested"
    assert audio_calls == []
    assert list(data_dir.rglob("audio")) == []
    assert list(data_dir.rglob("source.*")) == []


def test_audio_enabled_acquisition_adds_audio_without_redownloading_captions(tmp_path):
    audio_calls: list[list[str]] = []
    data_dir = tmp_path / "data"
    config_path = channel_config(tmp_path)
    first = acquire(
        config_path=config_path,
        data_dir=data_dir,
        limit=1,
        runner=caption_runner(tmp_path, audio_calls=audio_calls),
    )
    caption_payload = next((data_dir / "raw" / "corpora" / "es").rglob("subtitles.raw.json3"))
    original = caption_payload.read_bytes()

    second = acquire(
        config_path=config_path,
        data_dir=data_dir,
        limit=1,
        runner=caption_runner(tmp_path, audio_calls=audio_calls),
        with_audio=True,
        # Injected, like every other test in this file. It used to reach the real ffprobe, which
        # made the test pass only where ffmpeg happens to be installed — green on a developer's
        # machine and red on CI, which has no ffmpeg. What this test is about is that adding audio
        # does not re-download captions; the probe is scaffolding, not the subject.
        probe_runner=probe_runner(format_name="wav", duration=120.0),
    )

    assert first["videos"][0]["status"] == "downloaded"
    assert second["videos"][0]["status"] == "cached"
    assert second["audio_requested"] is True
    assert second["audio_downloaded"] == 1
    assert second["audio_complete"] is True
    assert len(audio_calls) == 1
    assert caption_payload.read_bytes() == original
    key = second["videos"][0]["video_key"]
    assert audio_availability(data_dir, language="es", video_key=key).ready

    third = acquire(
        config_path=config_path,
        data_dir=data_dir,
        limit=1,
        runner=caption_runner(tmp_path, audio_calls=audio_calls),
        with_audio=True,
    )
    assert third["audio_cached"] == 1
    assert len(audio_calls) == 1


def test_audio_failure_leaves_captions_usable_and_only_marks_audio_incomplete(tmp_path):
    audio_calls: list[list[str]] = []
    data_dir = tmp_path / "data"

    report = acquire(
        config_path=channel_config(tmp_path),
        data_dir=data_dir,
        limit=1,
        runner=caption_runner(tmp_path, audio_calls=audio_calls, audio_error="Private video"),
        with_audio=True,
    )

    assert report["complete"] is True
    assert report["failures"] == []
    assert report["audio_complete"] is False
    assert report["audio_failed"] == 1
    assert report["audio_failures"][0]["error_code"] == "provider_rejected"
    assert report["videos"][0]["status"] == "downloaded"
    caption_payload = next((data_dir / "raw" / "corpora" / "es").rglob("subtitles.raw.json3"))
    assert json.loads(caption_payload.read_text())["events"]
    key = report["videos"][0]["video_key"]
    assert audio_availability(data_dir, language="es", video_key=key).status == "failed"


def test_source_audio_is_never_rewritten_by_a_later_audio_enabled_run(tmp_path):
    key = install_caption_video(tmp_path)
    source = write_wave(tmp_path / "source.wav", seconds=2.0)
    install_raw_audio(tmp_path, key=key, source=source, duration=2.0)
    stored = raw_audio_paths(tmp_path, language="es", video_key=key).source("wav")
    before = (stored.read_bytes(), stored.stat().st_mtime_ns)

    _key, result = acquire_one(tmp_path, runner=media_runner(), key=key)

    assert result.status == "cached"
    assert (stored.read_bytes(), stored.stat().st_mtime_ns) == before
    with wave.open(str(stored), "rb") as reader:
        assert reader.getnframes() > 0


def dubbed(format_id, language, preference, **overrides):
    return audio_format(
        format_id,
        language=language,
        language_preference=preference,
        format_note=f"{language}{' original (default)' if preference >= 0 else ''}",
        **overrides,
    )


def multilingual_info():
    """A video dubbed into several languages, with the original marginally larger."""
    return formats(
        dubbed("249-0", "ja", -1, filesize=7_700_000),
        dubbed("139-0", "fr", -1, filesize=7_800_000),
        dubbed("139-5", "es", 10, filesize=7_810_000),
        dubbed("251-5", "es", 10, filesize=18_600_000),
    )


def test_a_smaller_dubbed_track_never_wins_over_the_original_language():
    selection = select_audio_format(multilingual_info(), source_language="es")

    assert selection.format_id == "139-5"
    assert selection.candidate.language == "es"
    assert selection.candidate.is_original_language is True
    assert selection.candidate.language_preference == 10
    rejected = {item.format_id: item.rejected for item in selection.considered}
    assert "dubbed ja audio, not the es source language" == rejected["249-0"]
    assert "dubbed fr audio, not the es source language" == rejected["139-0"]
    assert selection.constraints["require_source_language"] is True
    assert "dubbed tracks are rejected outright" in selection.constraints["selection_rule"]


def test_the_source_language_falls_back_to_the_provider_declaration():
    info = {**multilingual_info(), "language": "es"}

    assert select_audio_format(info).format_id == "139-5"


def test_a_video_with_no_track_in_the_source_language_is_refused_not_substituted():
    info = formats(
        dubbed("249-0", "ja", 10, filesize=1_000),
        dubbed("139-0", "fr", -1, filesize=900),
    )

    with pytest.raises(AudioAcquisitionError, match="carries the source language") as raised:
        select_audio_format(info, source_language="es")
    assert raised.value.code == "no_audio_in_source_language"
    assert raised.value.retryable is False


def test_single_language_videos_are_unaffected_by_the_language_rule():
    info = formats(audio_format("251", filesize=2_000), audio_format("140", filesize=1_000))

    selection = select_audio_format(info, source_language="es")

    assert selection.format_id == "140"
    assert selection.candidate.language is None
    assert all(item.rejected is None for item in selection.considered)


def test_acquisition_records_which_language_track_it_downloaded(tmp_path):
    key, result = acquire_one(tmp_path, runner=media_runner(), info=multilingual_info())

    assert result.format_id == "139-5"
    manifest = read_raw_audio_manifest(raw_audio_paths(tmp_path, language="es", video_key=key))
    assert manifest is not None
    raw = manifest["raw_audio"]
    assert raw["provider_language"] == "es"
    assert raw["provider_language_preference"] == 10
    assert raw["provider_is_original_language"] is True
    assert "original" in raw["provider_format_note"]


def test_a_payload_chosen_by_a_superseded_policy_is_reacquired_not_reused(tmp_path):
    key = install_caption_video(tmp_path)
    source = write_wave(tmp_path / "old.wav", seconds=2.0)
    install_raw_audio(
        tmp_path,
        key=key,
        source=source,
        duration=2.0,
        overrides={
            "format_selection": {
                "format_id": "249-0",
                "reason": "smallest_known_size",
                "policy_version": "smallest-audio-only-v1",
            }
        },
    )
    assert audio_availability(tmp_path, language="es", video_key=key).ready
    runner = media_runner(extension="wav", seconds=120.0)

    _key, result = acquire_one(
        tmp_path,
        runner=runner,
        key=key,
        info=multilingual_info(),
        probe=probe_runner(format_name="wav", duration=120.0),
    )

    assert result.status == "downloaded"
    assert result.format_id == "139-5"
    assert len(runner.calls) == 1
    manifest = read_raw_audio_manifest(raw_audio_paths(tmp_path, language="es", video_key=key))
    assert manifest is not None
    assert manifest["format_selection"]["policy_version"] == AUDIO_FORMAT_POLICY_VERSION


def test_a_payload_chosen_by_the_current_policy_is_still_reused_without_network(tmp_path):
    key, first = acquire_one(tmp_path, runner=media_runner(), info=multilingual_info())
    reuse = media_runner()

    _key, second = acquire_one(tmp_path, runner=reuse, key=key, info=multilingual_info())

    assert first.status == "downloaded" and second.status == "cached"
    assert reuse.calls == []
