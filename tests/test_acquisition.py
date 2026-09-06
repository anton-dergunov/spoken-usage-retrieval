import json
from pathlib import Path
from typing import Any

from speech_retrieval.acquisition import (
    CaptionTrack,
    acquire,
    authored_tracks,
    select_caption_track,
)


def catalogue(channels):
    return {
        "schema_version": 1,
        "language": "es",
        "sections": [{"id": "test", "name": "Test", "channels": channels}],
    }


def test_caption_selection_is_language_aware_and_manual_first():
    info: dict[str, Any] = {
        "subtitles": {"en": [{}], "es-MX": [{}], "es": [{}]},
        "automatic_captions": {"es-orig": [{}]},
    }
    assert select_caption_track(info, "es") == CaptionTrack("manual", "es")
    assert select_caption_track(info, "es-AR") == CaptionTrack("manual", "es")
    assert select_caption_track(info, "pt-BR") is None
    assert select_caption_track(
        {"subtitles": {}, "automatic_captions": {"es": [{}], "es-orig": [{}]}}, "es"
    ) == CaptionTrack("automatic", "es-orig")
    assert [track.language for track in authored_tracks(info)] == ["en", "es", "es-MX"]


def test_acquisition_is_round_robin_limited_resumable_and_enabled_only(tmp_path):
    config = catalogue(
        [
            {
                "id": "one",
                "name": "One",
                "url": "https://one.example/videos",
                "enabled": True,
                "varieties": ["Spain"],
            },
            {
                "id": "disabled",
                "name": "Disabled",
                "url": "https://disabled.example/videos",
                "enabled": False,
            },
            {
                "id": "two",
                "name": "Two",
                "url": "https://two.example/videos",
                "enabled": True,
                "varieties": ["Mexico"],
            },
        ]
    )
    config_path = tmp_path / "es.json"
    config_path.write_text(json.dumps(config))
    info_calls = []
    discovery_calls = []

    def runner(arguments):
        if "--flat-playlist" in arguments:
            url = arguments[-3]
            discovery_calls.append(url)
            channel = "one" if "one.example" in url else "two"
            return json.dumps(
                {
                    "entries": [
                        {
                            "id": f"video-{channel}",
                            "url": f"https://youtube.test/video-{channel}",
                            "title": channel,
                            "duration": 120,
                            "live_status": "not_live",
                        }
                    ]
                }
            )
        if "--dump-single-json" in arguments:
            video_id = "video-one" if "video-one" in arguments[-3] else "video-two"
            info_calls.append(video_id)
            return json.dumps(
                {
                    "id": video_id,
                    "webpage_url": f"https://youtube.test/{video_id}",
                    "title": video_id,
                    "channel": "One" if video_id.endswith("one") else "Two",
                    "duration": 120,
                    "subtitles": {"es": [{"ext": "json3", "url": "https://captions.test"}]},
                    "automatic_captions": {},
                }
            )
        output = Path(arguments[arguments.index("-o") + 1].replace("%(ext)s", "es.json3"))
        output.write_text(
            json.dumps(
                {
                    "events": [
                        {
                            "tStartMs": 0,
                            "dDurationMs": 1000,
                            "segs": [{"utf8": "Una frase completa."}],
                        }
                    ]
                }
            )
        )
        return ""

    data_dir = tmp_path / "data"
    report = acquire(config_path=config_path, data_dir=data_dir, limit=2, runner=runner)
    assert report["complete"] is True
    assert report["source_language"] == "es"
    assert report["catalogue_schema_version"] == 1
    assert report["analyzer_id"] == "unicode-regex-v1"
    assert [item["channel"] for item in report["videos"]] == ["one", "two"]
    assert all(item["status"] == "downloaded" for item in report["videos"])
    assert all(item["source_language"] == "es" for item in report["videos"])
    assert all(item["video_key"].startswith("vid_") for item in report["videos"])
    assert all(item["track_id"].startswith("trk_") for item in report["videos"])
    assert all("disabled.example" not in url for url in discovery_calls)

    first_video_dir = data_dir / "raw" / "corpora" / "es" / report["videos"][0]["video_key"]
    source_payload = next(first_video_dir.glob("*/subtitles.raw.json3"))
    original_bytes = source_payload.read_bytes()
    (first_video_dir / "manifest.json").unlink()
    rerun = acquire(config_path=config_path, data_dir=data_dir, limit=2, runner=runner)
    assert all(item["status"] == "cached" for item in rerun["videos"])
    assert info_calls == ["video-one", "video-two", "video-one"]
    assert source_payload.read_bytes() == original_bytes
    recreated = json.loads((first_video_dir / "manifest.json").read_text())
    assert recreated["canonical_source_track_id"] == report["videos"][0]["track_id"]
    assert recreated["source_selection"] == "authored_source"
    assert (data_dir / "reports" / "acquisition-es.json").exists()


def test_acquisition_preserves_authored_secondary_tracks_and_excludes_generated(tmp_path):
    config_path = tmp_path / "es.json"
    config_path.write_text(
        json.dumps(
            catalogue(
                [
                    {
                        "id": "one",
                        "name": "One",
                        "url": "https://one.example/videos",
                        "enabled": True,
                    }
                ]
            )
        )
    )
    downloaded_languages = []
    fail_english = True

    def runner(arguments):
        if "--flat-playlist" in arguments:
            return json.dumps(
                {
                    "entries": [
                        {
                            "id": "video-one",
                            "url": "https://youtube.test/video-one",
                            "title": "One",
                            "duration": 120,
                        }
                    ]
                }
            )
        if "--dump-single-json" in arguments:
            return json.dumps(
                {
                    "id": "video-one",
                    "webpage_url": "https://youtube.test/video-one",
                    "title": "One",
                    "channel": "One",
                    "duration": 120,
                    "subtitles": {
                        "es": [{"name": "Spanish"}],
                        "en": [{"name": "English"}],
                        "fr": [{"name": "French"}],
                    },
                    "automatic_captions": {"es-orig": [{}], "ru": [{}]},
                }
            )
        language = arguments[arguments.index("--sub-langs") + 1]
        downloaded_languages.append(language)
        if language == "en" and fail_english:
            raise RuntimeError("temporary English subtitle failure")
        output = Path(arguments[arguments.index("-o") + 1].replace("%(ext)s", f"{language}.json3"))
        output.write_text(
            json.dumps(
                {
                    "events": [
                        {
                            "tStartMs": 0,
                            "dDurationMs": 1000,
                            "segs": [{"utf8": "A complete sentence."}],
                        }
                    ]
                }
            )
        )
        return ""

    data_dir = tmp_path / "data"
    report = acquire(config_path=config_path, data_dir=data_dir, limit=1, runner=runner)
    assert downloaded_languages == ["es", "en", "fr"]
    assert report["authored_secondary_downloaded"] == 1
    assert report["authored_secondary_failed"] == 1
    assert report["successful"] == 1
    assert report["complete"] is True
    video_dir = next((data_dir / "raw" / "corpora" / "es").iterdir())
    manifest = json.loads((video_dir / "manifest.json").read_text())
    assert manifest["complete"] is False
    assert manifest["source_selection"] == "authored_source"
    assert manifest["provenance"] == {"provider": "youtube", "acquisition": "yt-dlp"}
    assert [track["kind"] for track in manifest["tracks"]] == [
        "authored",
        "authored",
        "authored",
    ]
    assert [track["language"] for track in manifest["tracks"]] == ["es", "en", "fr"]
    assert [track["status"] for track in manifest["tracks"]] == [
        "downloaded",
        "failed",
        "downloaded",
    ]
    fail_english = False
    rerun = acquire(config_path=config_path, data_dir=data_dir, limit=1, runner=runner)
    assert downloaded_languages == ["es", "en", "fr", "en"]
    assert rerun["authored_secondary_cached"] == 1
    assert rerun["authored_secondary_downloaded"] == 1
    assert rerun["authored_secondary_failed"] == 0
    assert json.loads((video_dir / "manifest.json").read_text())["complete"] is True
