from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).parents[1] / "experiments/target-language-speech-spans"
sys.path.insert(0, str(EXPERIMENT))

from speech_spans import (  # noqa: E402
    BUCKETS,
    Candidate,
    ExperimentConfig,
    GateSettings,
    Interval,
    LabelRecord,
    MetricSettings,
    SelectionRule,
    TruthInterval,
    compose_lesson,
    gate,
    merge_intervals,
    normalise_language,
    probability_of,
    runs_from_windows,
    score_against_labels,
    score_against_truth,
    script_share,
    script_units,
    select_operating_point,
    sliding_windows,
    target_runs,
    union_length,
    viterbi_two_state,
)

METRICS = MetricSettings(
    long_run_seconds=3.0,
    run_join_gap_seconds=1.0,
    correct_span_min_target_fraction=0.8,
    hard_failure_max_target_fraction=0.5,
)


def load_config() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        json.loads((EXPERIMENT / "config-v1.json").read_text(encoding="utf-8"))
    )


def load_bank() -> dict:
    return json.loads((EXPERIMENT / "synthetic/phrasebank-v1.json").read_text(encoding="utf-8"))


def test_the_committed_configuration_is_valid():
    config = load_config()

    assert config.config_version == 1
    assert config.selection.tuned_on == "synthetic"
    assert {clip.target_language for clip in config.clips} == {"zh", "es"}


def test_merge_joins_short_gaps_but_never_exceeds_the_cap():
    merged = merge_intervals([(0.0, 1.0), (1.2, 2.0), (3.0, 4.0)], merge_gap=0.3, max_seconds=10)
    assert merged == [Interval(0.0, 2.0), Interval(3.0, 4.0)]

    capped = merge_intervals([(0.0, 4.0), (4.1, 8.0)], merge_gap=0.3, max_seconds=6)
    assert capped == [Interval(0.0, 4.0), Interval(4.1, 8.0)]


def test_a_single_piece_longer_than_the_cap_is_split_evenly():
    merged = merge_intervals([(0.0, 25.0)], merge_gap=0.3, max_seconds=10)

    assert [item.duration for item in merged] == pytest.approx([25 / 3] * 3)
    assert merged[0].start == 0.0 and merged[-1].end == 25.0


def test_sliding_windows_cover_the_end_without_truncation():
    windows = sliding_windows(0.0, 5.2, window=2.0, hop=0.5)

    assert windows[0] == Interval(0.0, 2.0)
    assert windows[-1] == Interval(3.2, 5.2)
    assert all(abs(item.duration - 2.0) < 1e-9 for item in windows)
    assert sliding_windows(1.0, 2.5, window=2.0, hop=0.5) == [Interval(1.0, 2.5)]


def test_script_units_count_han_per_character_and_spaced_scripts_per_word():
    assert script_units("我不认识他") == 5
    assert script_units("no lo sé") == 3
    assert script_units("わかりません") == 3
    assert script_units("잘 모르겠어요") == 2
    assert script_units("The word 认识 means") == 5


def test_script_share_measures_letters_in_the_target_script():
    assert script_share("我不认识他", "zh") == 1.0
    assert script_share("I do not know 他", "zh") == pytest.approx(1 / 11)
    assert script_share("", "zh") is None
    assert script_share("やっぱり昨日", "ja") == 1.0


@pytest.mark.parametrize(
    ("label", "code"),
    [("es: Spanish", "es"), ("zh-CN", "zh"), ("pt_BR", "pt"), ("yue", "zh"), ("en", "en")],
)
def test_detector_labels_normalise_to_bare_codes(label, code):
    assert normalise_language(label) == code


def test_probability_sums_every_alias_of_the_target():
    assert probability_of([("zh", 0.5), ("yue", 0.2), ("en", 0.3)], "zh") == pytest.approx(0.7)


def test_viterbi_ignores_one_noisy_window_but_follows_a_sustained_switch():
    noisy = [0.95, 0.95, 0.3, 0.95, 0.95]
    assert viterbi_two_state(noisy, switch_penalty=3.0) == [True] * 5

    switch = [0.95, 0.95, 0.95, 0.05, 0.05, 0.05]
    assert viterbi_two_state(switch, switch_penalty=3.0) == [True] * 3 + [False] * 3
    assert viterbi_two_state(noisy, switch_penalty=0.0)[2] is False
    assert viterbi_two_state([], switch_penalty=1.0) == []


def test_runs_from_overlapping_windows_split_at_midpoints():
    windows = [Interval(0, 2), Interval(1, 3), Interval(2, 4), Interval(3, 5)]
    runs = runs_from_windows(windows, [True, True, False, False], [0.9, 0.8, 0.1, 0.1])

    assert len(runs) == 1
    span, mean = runs[0]
    assert span == Interval(0, 2.5)
    assert mean == pytest.approx(0.85)


def test_gate_reports_every_failed_check():
    settings = GateSettings(
        detector="agree_min",
        threshold=0.9,
        min_seconds=2.0,
        min_units=3,
        max_compression_ratio=2.4,
        min_logprob_margin=0.0,
        text_lid_veto=True,
    )
    good = Candidate(0, 3, p_target=0.95, units=6, compression_ratio=1.2, logprob_margin=0.4)
    assert gate(good, settings) == (True, [])

    bad = Candidate(
        0,
        1,
        p_target=0.5,
        units=1,
        compression_ratio=3.0,
        logprob_margin=-0.2,
        text_lid_is_target=False,
    )
    accepted, reasons = gate(bad, settings)
    assert not accepted
    assert reasons == [
        "below_threshold",
        "too_short",
        "too_few_units",
        "repetitive_transcript",
        "likelihood_prefers_other",
        "transcript_not_target",
    ]
    assert gate(Candidate(0, 5, p_target=None), settings)[1] == ["no_detector_output"]


def truth_rows() -> list[TruthInterval]:
    return [
        TruthInterval(start=0, end=2, language="en", is_target=False),
        TruthInterval(start=2.5, end=4.5, language="zh", is_target=True),
        TruthInterval(start=5.0, end=6.5, language="zh", is_target=True),
        TruthInterval(start=7, end=9, language="en", is_target=False),
        TruthInterval(start=9.1, end=9.6, language="zh", is_target=True),
    ]


def test_target_runs_join_across_silence_and_break_at_other_speech():
    runs = target_runs(truth_rows(), join_gap=1.0)

    assert runs == [Interval(2.5, 6.5), Interval(9.1, 9.6)]


def test_scoring_counts_only_speech_time_and_classifies_spans():
    spans = [(2.4, 6.6), (0.0, 3.0)]
    result = score_against_truth(spans, truth_rows(), METRICS)

    assert result["spans"] == 2
    assert result["correct_spans"] == 1
    assert result["hard_failures"] == 1
    assert result["span_precision"] == 0.5
    # Overlapping spans count once: 2.5-3.0 is covered by both but is 0.5 s of target speech.
    assert result["time_precision"] == pytest.approx(3.5 / 5.5)
    assert result["long_runs"] == 1
    # The run 2.5-6.5 holds 3.5 s of speech and 0.5 s of silence; recall measures the speech.
    assert result["long_run_recall"] == pytest.approx(1.0)
    assert result["time_recall"] == pytest.approx(3.5 / 4.0)


def test_empty_predictions_have_no_precision_rather_than_perfect_precision():
    result = score_against_truth([], truth_rows(), METRICS)

    assert result["span_precision"] is None
    assert result["time_precision"] is None
    assert result["long_run_recall"] == 0.0


def test_label_scoring_sets_mixed_and_unsure_time_aside():
    labels = [
        LabelRecord(clip_id="c", unit_id="u1", start=0, end=3, label="target"),
        LabelRecord(clip_id="c", unit_id="u2", start=3, end=6, label="mixed"),
        LabelRecord(clip_id="c", unit_id="u3", start=6, end=9, label="other"),
        LabelRecord(clip_id="c", unit_id="u4", start=9, end=10, label=None),
    ]
    result = score_against_labels([(0, 4)], labels, METRICS)

    assert result["time_precision"] == 1.0
    assert result["unjudgeable_seconds_in_spans"] == 1.0
    assert result["spans_touching_mixed_or_unsure"] == 1
    assert result["labelled_units"] == 3
    assert result["label_counts"]["mixed"] == 1


def test_union_length_merges_overlaps():
    assert union_length([(0, 2), (1, 3), (5, 6), (6, 6)]) == 4


def test_selection_maximises_recall_at_the_precision_floor_and_prefers_strictness():
    rule = SelectionRule(min_span_precision=0.97)
    sweep = [
        {"threshold": 0.5, "min_seconds": 1, "span_precision": 0.9, "long_run_recall": 0.99},
        {"threshold": 0.8, "min_seconds": 2, "span_precision": 0.98, "long_run_recall": 0.8},
        {"threshold": 0.9, "min_seconds": 2, "span_precision": 0.99, "long_run_recall": 0.8},
        {"threshold": 0.95, "min_seconds": 3, "span_precision": 1.0, "long_run_recall": 0.6},
    ]

    chosen = select_operating_point(sweep, rule)
    assert chosen is not None and chosen["threshold"] == 0.9
    assert select_operating_point(sweep[:1], rule) is None


def test_lessons_are_deterministic_and_cover_every_bucket_and_switch_kind():
    bank = load_bank()
    first = compose_lesson(bank, "en", "zh", seed=17, blocks=8)
    again = compose_lesson(bank, "en", "zh", seed=17, blocks=8)
    other = compose_lesson(bank, "en", "zh", seed=18, blocks=8)

    assert first == again
    assert first != other
    assert {row.bucket for row in first if row.language == "zh"} >= set(BUCKETS)
    assert any(row.role == "inline_item" for row in first)
    inline = [row for row in first if row.role.startswith("inline")]
    assert all(row.gap_before < 0.1 for row in inline if row.role != "inline_prefix")
    assert first[0].gap_before == 0.0
    assert {row.language for row in first} == {"en", "zh"}


def test_every_configured_synthetic_pair_has_frames_and_items():
    bank = load_bank()
    config = load_config()

    for base, target in config.synth.pairs:
        assert base in bank["frames"], base
        assert set(bank["items"][target]) == set(BUCKETS), target
        compose_lesson(bank, base, target, seed=config.synth.seed, blocks=8)


def test_the_method_never_receives_evaluation_only_metadata():
    """The span decision may see audio features and the target language, nothing else."""
    import inspect

    from speech_spans import Candidate as CandidateType

    fields = set(CandidateType.__dataclass_fields__)
    assert not fields & {"other_language", "base_language", "evaluation_only", "captions"}
    assert "evaluation_only" not in inspect.signature(gate).parameters


def test_caption_proxy_types_cues_by_script_and_treats_gaps_as_target():
    from speech_spans import caption_proxy_labels, parse_vtt

    vtt = (
        "WEBVTT\nKind: captions\n\n"
        "00:00:00.000 --> 00:00:01.000\n哈囉，大家好！\nHello, everyone!\n\n"
        "00:00:01.000 --> 00:00:04.000\nIf you have been wondering\n\n"
        "00:00:04.000 --> 00:00:06.000\nThat's why 果\n\n"
    )
    cues = parse_vtt(vtt)
    assert [cue[:2] for cue in cues] == [(0.0, 1.0), (1.0, 4.0), (4.0, 6.0)]
    assert cues[0][2] == ["哈囉，大家好！", "Hello, everyone!"]

    units = [
        {"unit_id": "a", "start": 0.0, "end": 1.0},
        {"unit_id": "b", "start": 1.2, "end": 3.8},
        {"unit_id": "c", "start": 4.2, "end": 5.5},
        {"unit_id": "d", "start": 8.0, "end": 10.0},
    ]
    labels = caption_proxy_labels(units, cues, "zh", clip_id="clip")
    assert [label.label for label in labels] == ["target", "other", "mixed", "target"]
    assert {label.reviewer for label in labels} == {"caption-proxy"}


def test_closed_set_probability_discards_mass_on_languages_the_corpus_cannot_contain():
    from speech_spans import closed_set_probability

    distribution = [("es: Spanish", 0.3), ("ca: Catalan", 0.5), ("la", 0.1), ("en", 0.1)]
    languages = ["en", "es", "pt"]

    assert closed_set_probability(distribution, "es", languages) == pytest.approx(0.75)
    assert closed_set_probability(distribution, "en", languages) == pytest.approx(0.25)
    assert closed_set_probability([("ca", 1.0)], "es", languages) is None


def test_pairwise_probability_lets_any_single_rival_veto():
    from speech_spans import pairwise_probability

    top = [("es", 0.3), ("ca", 0.5), ("en", 0.1)]
    assert pairwise_probability(top, 0.3, "es") == pytest.approx(0.3 / 0.8)
    assert pairwise_probability([("es", 0.9), ("pt", 0.05)], 0.9, "es") == pytest.approx(0.9 / 0.95)
    assert pairwise_probability(top, None, "es") is None


def test_review_page_is_blind_offline_and_offers_exactly_the_label_schema():
    import re
    from typing import get_args

    from language_review_app import LABEL_ANCHORS, render_language_review
    from speech_spans import HumanLabel

    worksheet = {
        "run_id": "run",
        "rubric_version": 1,
        "items_checksum": "abc",
        "clips": [
            {
                "clip_id": "c",
                "target_language": "zh",
                "target_name": "Chinese",
                "duration": 10.0,
                "audio": "/nonexistent.m4a",
                "p_target": 0.9,
            }
        ],
        "items": [
            {
                "clip_id": "c",
                "unit_id": "c:u0",
                "start": 0.0,
                "end": 2.0,
                "text": "我们开始之前",
                "forced": {"text": "leak"},
            }
        ],
    }
    html = render_language_review(worksheet)

    assert [anchor[0] for anchor in LABEL_ANCHORS] == list(get_args(HumanLabel))
    assert all(len(anchor[3]) > 30 for anchor in LABEL_ANCHORS)
    for leaked in ("我们开始之前", "leak", "p_target", "forced"):
        assert leaked not in html
    assert not re.search(r"https?://", html)


def test_label_server_saves_every_label_to_disk_and_serves_audio_ranges(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    from label_server import build_server

    audio = tmp_path / "c.m4a"
    audio.write_bytes(bytes(range(256)) * 4)
    worksheet = {
        "run_id": "run",
        "rubric_version": 1,
        "items_checksum": "abc",
        "clips": [
            {
                "clip_id": "c",
                "target_language": "es",
                "target_name": "Spanish",
                "duration": 4.0,
                "audio": str(audio),
            }
        ],
        "items": [
            {"clip_id": "c", "unit_id": "c:u0000", "start": 0.0, "end": 2.0},
            {"clip_id": "c", "unit_id": "c:u0001", "start": 2.0, "end": 4.0},
        ],
    }
    store_path = tmp_path / "labels.json"
    server, _labels = build_server(worksheet, store_path, {"vad": {}}, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body):
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            post("/api/label", {"unit_id": "c:u0000", "label": "target"})
        assert refused.value.code == 409

        post("/api/reviewer", {"reviewer": "Anton"})
        assert post("/api/label", {"unit_id": "c:u0000", "label": "target"})["labelled"] == 1
        on_disk = json.loads(store_path.read_text())
        assert on_disk["items"][0]["label"] == "target"
        assert on_disk["items"][0]["reviewer"] == "Anton"
        assert on_disk["provenance"] == {"vad": {}}
        assert "audio" not in on_disk["clips"][0]

        for bad in ({"unit_id": "c:u0000", "label": "spanish"}, {"unit_id": "x", "label": "other"}):
            with pytest.raises(urllib.error.HTTPError) as error:
                post("/api/label", bad)
            assert error.value.code == 400

        request = urllib.request.Request(base + "/audio/c", headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 10-19/1024"
            assert response.read() == bytes(range(10, 20))

        with urllib.request.urlopen(base + "/api/state") as response:
            assert json.loads(response.read())["labels"] == {"c:u0000": "target"}
    finally:
        server.shutdown()
        server.server_close()

    # A restarted server resumes from the file and refuses a store for different units.
    restarted, labels = build_server(worksheet, store_path, {}, port=0)
    restarted.server_close()
    assert labels.state()["labels"] == {"c:u0000": "target"}
    with pytest.raises(SystemExit):
        build_server({**worksheet, "items_checksum": "other"}, store_path, {}, port=0)


def test_spans_over_unlabelled_speech_are_unjudged_not_failures():
    labels = [LabelRecord(clip_id="c", unit_id="u1", start=0, end=3, label="target")]
    result = score_against_labels([(0, 2), (10, 14)], labels, METRICS)

    assert result["spans"] == 2
    assert result["judged_spans"] == 1
    assert result["hard_failures"] == 0
    assert result["span_precision"] == 1.0


def test_selection_breaks_recall_ties_on_precision_before_strictness():
    rule = SelectionRule(min_span_precision=0.97)
    sweep = [
        {
            "method": "a",
            "threshold": 0.9,
            "min_seconds": 2,
            "span_precision": 0.99,
            "long_run_recall": 0.9,
        },
        {
            "method": "b",
            "threshold": 0.7,
            "min_seconds": 1,
            "span_precision": 1.0,
            "long_run_recall": 0.9,
        },
    ]

    chosen = select_operating_point(sweep, rule)
    assert chosen is not None and chosen["method"] == "b"


def test_committed_config_schema_does_not_drift_from_the_model():
    committed = json.loads((EXPERIMENT / "config-schema-v1.json").read_text(encoding="utf-8"))
    generated = ExperimentConfig.model_json_schema()

    assert committed["$schema"].startswith("https://json-schema.org/")
    assert {key: value for key, value in committed.items() if not key.startswith("$")} == {
        key: value for key, value in generated.items() if not key.startswith("$")
    }
