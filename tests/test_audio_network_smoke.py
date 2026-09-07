"""Opt-in real-provider smoke test. Never runs in the default offline suite.

Enable it by pointing ``SPEECH_RETRIEVAL_AUDIO_SMOKE_URL`` at one small video the operator
is authorized to download, and confirming that authorization explicitly:

    SPEECH_RETRIEVAL_AUDIO_SMOKE_URL=https://www.youtube.com/watch?v=... \\
    SPEECH_RETRIEVAL_AUDIO_SMOKE_AUTHORIZED=1 \\
    uv run pytest tests/test_audio_network_smoke.py -m network
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import wave

import pytest

from speech_retrieval.audio import audio_availability, prepare_clip, raw_audio_paths
from speech_retrieval.audio_acquisition import acquire_audio, yt_dlp_version
from speech_retrieval.identity import video_key

SMOKE_URL = os.environ.get("SPEECH_RETRIEVAL_AUDIO_SMOKE_URL", "").strip()
AUTHORIZED = os.environ.get("SPEECH_RETRIEVAL_AUDIO_SMOKE_AUTHORIZED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(not SMOKE_URL, reason="SPEECH_RETRIEVAL_AUDIO_SMOKE_URL is not set"),
    pytest.mark.skipif(
        not AUTHORIZED,
        reason="SPEECH_RETRIEVAL_AUDIO_SMOKE_AUTHORIZED must confirm the operator's basis",
    ),
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="the smoke test needs local ffmpeg and ffprobe",
    ),
]


def ytdlp(arguments):
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--ignore-config", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr else "failed")
    return result.stdout


def test_one_authorized_video_produces_audio_only_media_and_a_prepared_clip(tmp_path):
    info = json.loads(ytdlp(["--skip-download", "--dump-single-json", "--no-warnings", SMOKE_URL]))
    key = video_key("youtube", "es", info["id"])

    result = acquire_audio(
        data_dir=tmp_path,
        language="es",
        video_key=key,
        video_id=info["id"],
        url=info.get("webpage_url") or SMOKE_URL,
        info=info,
        runner=ytdlp,
        yt_dlp_version=yt_dlp_version(),
    )

    assert result.status == "downloaded", result.error
    assert result.format_id
    record = audio_availability(tmp_path, language="es", video_key=key, verify_checksum=True)
    assert record.ready
    assert record.source_path is not None and record.source_path.is_file()
    assert record.duration and record.duration > 0
    manifest = json.loads(
        raw_audio_paths(tmp_path, language="es", video_key=key).manifest.read_text()
    )
    assert manifest["status"] == "ready"
    assert manifest["raw_audio"]["provider_format_id"] == result.format_id
    assert manifest["format_selection"]["constraints"]["audio_only"] is True
    assert manifest["tool_versions"]["yt_dlp"]

    end = min(record.duration, 5.0)
    clip = prepare_clip(tmp_path, language="es", video_key=key, start=0.0, end=end)

    assert clip.sample_rate == 16_000 and clip.channels == 1
    assert clip.duration == pytest.approx(end, abs=0.1)
    assert clip.size_bytes > 0
    with wave.open(str(clip.path), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getframerate() == 16_000
        assert reader.getnframes() > 0
