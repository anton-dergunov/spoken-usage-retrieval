import json
from pathlib import Path

from speech_retrieval.acquisition import acquire


def test_acquisition_is_round_robin_limited_and_resumable(tmp_path):
    config = {
        "channels": [
            {
                "id": "one",
                "name": "One",
                "url": "https://one.example/videos",
                "varieties": ["Spain"],
            },
            {
                "id": "two",
                "name": "Two",
                "url": "https://two.example/videos",
                "varieties": ["Mexico"],
            },
        ]
    }
    config_path = tmp_path / "channels.json"
    config_path.write_text(json.dumps(config))
    info_calls = []

    def runner(arguments):
        if "--flat-playlist" in arguments:
            channel = "one" if "https://one.example/videos" in arguments else "two"
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

    report = acquire(config_path=config_path, data_dir=tmp_path / "data", limit=2, runner=runner)
    assert report["complete"] is True
    assert [item["channel"] for item in report["videos"]] == ["one", "two"]
    assert all(item["status"] == "downloaded" for item in report["videos"])

    rerun = acquire(config_path=config_path, data_dir=tmp_path / "data", limit=2, runner=runner)
    assert all(item["status"] == "cached" for item in rerun["videos"])
    assert info_calls == ["video-one", "video-two"]
