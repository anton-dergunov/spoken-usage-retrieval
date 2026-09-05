import json
from pathlib import Path
from typing import Any

from speech_retrieval.acquisition import CaptionTrack, acquire, select_caption_track


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

    rerun = acquire(config_path=config_path, data_dir=data_dir, limit=2, runner=runner)
    assert all(item["status"] == "cached" for item in rerun["videos"])
    assert info_calls == ["video-one", "video-two"]
    assert (data_dir / "reports" / "acquisition-es.json").exists()
