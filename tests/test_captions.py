import json
from pathlib import Path

from speech_retrieval.captions import automatic_units, segment_payload

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_manual_captions_become_complete_padded_sentences():
    segments = segment_payload(
        fixture("manual.json3"), video_id="manual-video", caption_kind="manual", video_duration=6
    )
    assert [segment.text for segment in segments] == [
        "Sí, la verdad es una buena idea.",
        "La verdad funciona muy bien.",
    ]
    assert segments[0].boundary_reason == "punctuation"
    assert segments[0].clip_start == 0.65
    assert segments[-1].clip_end == 5.65
    assert segments[0].quality_score > 0.8
    assert len(segments[0].segments) == 1
    assert segments[0].segments[0].text == segments[0].text
    assert segments[0].segments[0].char_start == 0
    assert segments[0].segments[0].char_end == len(segments[0].text)


def test_automatic_caption_tokens_are_timestamp_deduplicated():
    units = automatic_units(fixture("automatic.json3"))
    assert [unit.text for unit in units].count("Esto") == 1
    assert [unit.text for unit in units].count("funciona") == 1
    segments = segment_payload(
        fixture("automatic.json3"),
        video_id="auto-video",
        caption_kind="automatic",
        video_duration=5,
    )
    assert segments[0].text == "Esto funciona muy bien."
    assert segments[0].boundary_reason == "punctuation"
    assert [item.text for item in segments[0].segments] == ["Esto", "funciona", "muy", "bien."]
    assert [segments[0].text[item.char_start : item.char_end] for item in segments[0].segments] == [
        "Esto",
        "funciona",
        "muy",
        "bien.",
    ]
    assert [item.start for item in segments[0].segments] == [0.5, 0.85, 1.2, 1.5]
    assert segments[1].text == "Otra prueba"


def test_long_unpunctuated_caption_is_forced_to_split():
    payload = {
        "events": [
            {
                "tStartMs": index * 300,
                "dDurationMs": 300,
                "segs": [{"utf8": f" palabra{index}"}],
            }
            for index in range(40)
        ]
    }
    segments = segment_payload(payload, video_id="long", caption_kind="automatic")
    assert len(segments) >= 2
    assert segments[0].boundary_reason == "forced"
    assert segments[0].token_count == 32
