"""The forced-alignment experiment: references, baselines, statistics, and the review page."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).parents[1] / "experiments/forced-alignment"
sys.path.insert(0, str(EXPERIMENT))

from alignment_eval import (  # noqa: E402
    CUE_INTERPOLATED,
    CUE_START,
    ExperimentConfig,
    ResultRow,
    ReviewItem,
    Sampling,
    SystemResult,
    WordComparison,
    bootstrap_interval,
    choose_review_clips,
    cue_interpolated_words,
    cue_start_words,
    engine_agreement,
    kendall_tau,
    match_words,
    quantile,
    reference_starts,
    review_agreement,
    summarize,
)

from speech_retrieval.models import TimedUnit  # noqa: E402


def row(
    segment_id: str = "seg_1",
    *,
    video_key: str = "vid_1",
    source_class: str = "authored",
    deltas: dict[str, list[float]] | None = None,
    text: str = "uno dos tres",
) -> ResultRow:
    systems = []
    for system, values in (deltas or {}).items():
        systems.append(
            SystemResult(
                system=system,
                status="complete",
                coverage=1.0,
                mean_confidence=0.9,
                comparisons=[
                    WordComparison(text=f"w{index}", aligned_start=1.0 + value, reference_start=1.0)
                    for index, value in enumerate(values)
                ],
            )
        )
    return ResultRow(
        run_id="test",
        segment_id=segment_id,
        video_key=video_key,
        language="es",
        source_class=source_class,  # type: ignore[arg-type]
        text=text,
        clip=f"/tmp/{segment_id}.wav",
        clip_start=0.0,
        clip_end=4.0,
        systems=systems,
    )


# --- Reference ---------------------------------------------------------------------------


def test_reference_starts_are_relative_to_the_clip() -> None:
    units = [
        TimedUnit(text="hola", start=10.5, end=10.8),
        TimedUnit(text="mundo", start=11.0, end=11.3),
    ]
    assert reference_starts(units, 10.0) == [("hola", 0.5), ("mundo", 1.0)]


def test_reference_starts_skip_blank_units() -> None:
    units = [TimedUnit(text="  ", start=1.0, end=1.1), TimedUnit(text="hola", start=1.5, end=1.7)]
    assert reference_starts(units, 0.0) == [("hola", 1.5)]


def test_match_words_pairs_only_words_that_agree_after_normalization() -> None:
    """A word the ASR heard differently is dropped, not scored as a timing error."""
    aligned = [("Qué", 0.1), ("tal", 0.5), ("amigo", 0.9)]
    reference = [("que", 0.0), ("tal", 0.4), ("amiga", 0.8)]
    pairs = match_words(aligned, reference)
    assert [pair.text for pair in pairs] == ["Qué", "tal"]
    assert pairs[0].delta == pytest.approx(0.1)


def test_match_words_survives_an_insertion_in_the_reference() -> None:
    aligned = [("uno", 0.0), ("tres", 1.0)]
    reference = [("uno", 0.0), ("dos", 0.5), ("tres", 0.9)]
    pairs = match_words(aligned, reference)
    assert [pair.text for pair in pairs] == ["uno", "tres"]
    assert pairs[1].delta == pytest.approx(0.1)


def test_match_words_returns_nothing_when_no_word_agrees() -> None:
    assert match_words([("aaa", 0.0)], [("bbb", 0.0)]) == []


# --- Baselines ---------------------------------------------------------------------------


def test_cue_start_gives_every_word_the_same_time() -> None:
    assert cue_start_words("uno dos tres", 2.0) == [("uno", 2.0), ("dos", 2.0), ("tres", 2.0)]


def test_cue_interpolation_spreads_words_across_the_cue_in_order() -> None:
    words = cue_interpolated_words("uno dos tres", 0.0, 3.0)
    starts = [start for _, start in words]
    assert starts == sorted(starts)
    assert starts[0] == 0.0
    assert starts[-1] < 3.0


def test_cue_interpolation_degrades_to_cue_start_for_a_zero_length_cue() -> None:
    assert cue_interpolated_words("uno dos", 1.0, 1.0) == [("uno", 1.0), ("dos", 1.0)]


# --- Statistics --------------------------------------------------------------------------


def test_quantile_interpolates_between_neighbours() -> None:
    assert quantile([0.0, 1.0], 0.5) == pytest.approx(0.5)
    assert quantile([1.0, 2.0, 3.0], 0.0) == 1.0
    assert quantile([], 0.5) != quantile([], 0.5)  # nan


def test_summarize_separates_signed_from_absolute_error() -> None:
    """A system that is uniformly late must show it in the signed median, not hide in |delta|."""
    rows = [
        row(f"seg_{index}", video_key=f"vid_{index}", deltas={"mms": [0.05] * 6})
        for index in range(4)
    ]
    summary = summarize(rows, "mms", minimum_per_cell=2)
    assert summary is not None
    assert summary.median_abs == pytest.approx(0.05)
    assert summary.median_signed == pytest.approx(0.05)
    assert summary.within["200ms"] == 1.0
    assert summary.segments == 4
    assert summary.videos == 4
    assert not summary.indicative_only


def test_summarize_flags_a_cell_below_the_minimum_sample() -> None:
    rows = [row("seg_1", deltas={"mms": [0.01]})]
    summary = summarize(rows, "mms", minimum_per_cell=50)
    assert summary is not None and summary.indicative_only


def test_summarize_counts_failures_without_letting_them_skew_the_error() -> None:
    good = row("seg_1", video_key="vid_1", deltas={"mms": [0.02, 0.02]})
    bad = row("seg_2", video_key="vid_2")
    bad.systems = [SystemResult(system="mms", status="failed", reason="low_confidence")]
    summary = summarize([good, bad], "mms", minimum_per_cell=1)
    assert summary is not None
    assert summary.failures == 1
    assert summary.segments == 1
    assert summary.median_abs == pytest.approx(0.02)


def test_summarize_returns_none_when_nothing_could_be_scored() -> None:
    assert summarize([row("seg_1")], "mms") is None


def test_bootstrap_resamples_videos_so_one_video_cannot_look_like_many() -> None:
    """Words inside a video fail together, so the interval must reflect four videos, not 80 words.

    Three well-aligned videos and one badly-aligned one. Resampling whole videos can easily draw
    two or three bad ones, so the interval is wide. Resampling the 80 individual words treats the
    correlated failure as 20 independent observations and reports near-certainty instead.
    """
    clusters = [[0.01] * 20, [0.01] * 20, [0.01] * 20, [0.5] * 20]
    clustered = bootstrap_interval(clusters, "median_abs", resamples=500)
    per_word = bootstrap_interval(
        [[value] for cluster in clusters for value in cluster], "median_abs", resamples=500
    )
    assert clustered[1] - clustered[0] > 0.1
    assert per_word[1] - per_word[0] == pytest.approx(0.0, abs=1e-9)


def test_bootstrap_needs_at_least_two_clusters() -> None:
    low, high = bootstrap_interval([[0.1, 0.2]], "median_abs")
    assert low != low and high != high  # nan


def test_engine_agreement_measures_the_noise_floor_between_two_models() -> None:
    entry = row("seg_1", deltas={"mms": [0.0, 0.0], "permissive": [0.0, 0.0]})
    result = engine_agreement([entry], "mms", "permissive")
    assert result["pairs"] == 2
    assert result["median_abs"] == 0.0
    assert result["within_200ms"] == 1.0


def test_engine_agreement_reports_no_pairs_when_a_system_is_missing() -> None:
    assert engine_agreement([row("seg_1", deltas={"mms": [0.0]})], "mms", "permissive") == {
        "pairs": 0
    }


def test_kendall_tau_detects_agreement_and_disagreement() -> None:
    assert kendall_tau([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
    assert kendall_tau([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == -1.0


# --- Review ----------------------------------------------------------------------------------


def sampling(**kwargs: object) -> Sampling:
    return Sampling(review_clips=4, review_repeats=2, seed=7, **kwargs)  # type: ignore[arg-type]


def review_rows() -> list[ResultRow]:
    return [
        row(
            f"seg_{index}",
            video_key=f"vid_{index}",
            source_class="authored" if index % 2 else "automatic",
            deltas={"mms": [0.02], "permissive": [0.03], CUE_INTERPOLATED: [0.3]},
        )
        for index in range(8)
    ]


def test_review_selection_blinds_and_shuffles_the_systems() -> None:
    systems = ["mms", "permissive", CUE_INTERPOLATED]
    items = choose_review_clips(review_rows(), systems, sampling())
    graded = [item for item in items if item.repeat_of is None]
    assert len(graded) == 4
    for item in items:
        assert sorted(item.assignment) == ["A", "B", "C"]
        assert sorted(item.assignment.values()) == sorted(systems)


def test_review_selection_includes_repeats_for_self_consistency() -> None:
    items = choose_review_clips(review_rows(), ["mms", "permissive", CUE_INTERPOLATED], sampling())
    repeats = [item for item in items if item.repeat_of]
    assert len(repeats) == 2
    originals = {item.review_id for item in items if item.repeat_of is None}
    for repeat in repeats:
        assert repeat.repeat_of in originals


def test_review_selection_covers_both_source_classes() -> None:
    items = choose_review_clips(review_rows(), ["mms", "permissive", CUE_INTERPOLATED], sampling())
    assert {item.source_class for item in items} == {"authored", "automatic"}


def test_review_selection_is_deterministic_for_a_seed() -> None:
    systems = ["mms", "permissive", CUE_INTERPOLATED]
    first = choose_review_clips(review_rows(), systems, sampling())
    second = choose_review_clips(review_rows(), systems, sampling())
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]


def test_review_selection_skips_rows_a_system_could_not_align() -> None:
    rows = review_rows()
    rows[0].systems = [SystemResult(system="mms", status="failed")]
    items = choose_review_clips(rows, ["mms", "permissive", CUE_INTERPOLATED], sampling())
    assert rows[0].segment_id not in {item.segment_id for item in items}


def test_review_agreement_reports_hit_rate_and_self_consistency() -> None:
    rows = [row("seg_1", deltas={"mms": [0.02], CUE_INTERPOLATED: [0.4]})]
    graded = ReviewItem(
        review_id="r01",
        segment_id="seg_1",
        language="es",
        source_class="authored",
        text="uno",
        clip="/tmp/x.wav",
        duration=1.0,
        assignment={"A": "mms", "B": CUE_INTERPOLATED},
        ratings={"A": "in_sync", "B": "broken"},
    )
    repeat = graded.model_copy(
        update={
            "review_id": "x01",
            "repeat_of": "r01",
            "assignment": {"A": CUE_INTERPOLATED, "B": "mms"},
            "ratings": {"A": "broken", "B": "in_sync"},
        }
    )
    result = review_agreement([graded, repeat], rows)
    assert result["clips_judged"] == 1
    assert result["top_choice_hit_rate"] == 1.0
    assert result["repeats"] == 1
    assert result["self_consistency_rate"] == 1.0


def test_review_agreement_detects_an_inconsistent_repeat() -> None:
    rows = [row("seg_1", deltas={"mms": [0.02], CUE_INTERPOLATED: [0.4]})]
    graded = ReviewItem(
        review_id="r01",
        segment_id="seg_1",
        language="es",
        source_class="authored",
        text="uno",
        clip="/tmp/x.wav",
        duration=1.0,
        assignment={"A": "mms", "B": CUE_INTERPOLATED},
        ratings={"A": "in_sync", "B": "broken"},
    )
    repeat = graded.model_copy(
        update={
            "review_id": "x01",
            "repeat_of": "r01",
            "assignment": {"A": CUE_INTERPOLATED, "B": "mms"},
            "ratings": {"A": "in_sync", "B": "in_sync"},
        }
    )
    assert review_agreement([graded, repeat], rows)["self_consistency_rate"] == 0.0


# --- Review page ---------------------------------------------------------------------------------


def worksheet_for(items: list[ReviewItem]) -> dict[str, object]:
    return {
        "run_id": "test",
        "rubric_version": "alignment-review-v1",
        "systems": ["mms", CUE_INTERPOLATED],
        "items": [item.model_dump() for item in items],
    }


def test_the_review_page_is_self_contained_and_withholds_the_assignment() -> None:
    from alignment_review_app import render_review_app

    rows = {"seg_1": row("seg_1", deltas={"mms": [0.02], CUE_INTERPOLATED: [0.4]})}
    item = ReviewItem(
        review_id="r01",
        segment_id="seg_1",
        language="es",
        source_class="authored",
        text="uno dos",
        clip=None,
        duration=2.0,
        assignment={"A": "mms", "B": CUE_INTERPOLATED},
    )
    html = render_review_app(worksheet_for([item]), rows, embed_audio=False)
    assert html.startswith("<!doctype html>")
    assert "<script src=" not in html  # no network, no CDN
    assert "prefers-color-scheme: dark" in html
    assert "Reveal which system is which" in html
    payload = json.loads(
        html.split('<script type="application/json" id="payload">')[1]
        .split("</script>")[0]
        .replace("<\\/", "</")
    )
    assert payload["timing"]["r01"]["A"] == [{"text": "w0", "start": 1.02}]
    assert payload["timing"]["r01"]["B"] == [{"text": "w0", "start": 1.4}]


def test_the_review_page_escapes_a_closing_script_tag_in_caption_text() -> None:
    from alignment_review_app import render_review_app

    item = ReviewItem(
        review_id="r01",
        segment_id="seg_1",
        language="es",
        source_class="authored",
        text="</script><b>x</b>",
        clip=None,
        duration=1.0,
        assignment={"A": "mms"},
    )
    html = render_review_app(worksheet_for([item]), {}, embed_audio=False)
    body = html.split('id="payload">')[1].split("</script>")[0]
    assert "</script>" not in body


def test_the_review_page_tolerates_a_row_whose_clip_is_missing(tmp_path) -> None:
    from alignment_review_app import collect_audio

    worksheet = {"items": [{"segment_id": "seg_1", "clip": str(tmp_path / "absent.wav")}]}
    audio, total = collect_audio(worksheet)
    assert audio == {} and total == 0


def test_the_review_page_embeds_each_clip_once(tmp_path) -> None:
    from alignment_review_app import collect_audio
    from audio_fixtures import write_wave

    clip = write_wave(tmp_path / "clip.wav", seconds=0.2)
    worksheet = {
        "items": [
            {"segment_id": "seg_1", "clip": str(clip)},
            {"segment_id": "seg_1", "clip": str(clip)},
        ]
    }
    audio, total = collect_audio(worksheet)
    assert list(audio) == ["seg_1"]
    assert total == clip.stat().st_size


# --- Configuration ---------------------------------------------------------------------------------


def test_the_checked_in_configuration_is_valid_and_pre_declares_the_sample() -> None:
    config = ExperimentConfig.model_validate_json(
        (EXPERIMENT / "config-v1.json").read_text(encoding="utf-8")
    )
    assert config.sampling.target_per_cell >= config.sampling.minimum_per_cell
    assert {model.profile for model in config.models} == {"mms", "permissive"}
    assert config.sampling.review_clips == 20
    assert config.sampling.review_repeats == 3


def test_the_configuration_rejects_an_unknown_field() -> None:
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate({"models": [], "unexpected": 1})


def test_the_baselines_are_named_consistently() -> None:
    assert CUE_START == "cue_start"
    assert CUE_INTERPOLATED == "cue_interpolated"


def test_the_checked_in_schemas_match_the_models() -> None:
    """Generated from the Pydantic models, so a silent contract drift fails here."""
    for name, model in (
        ("config-schema-v1.json", ExperimentConfig),
        ("result-schema-v1.json", ResultRow),
    ):
        checked_in = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
        assert checked_in == model.model_json_schema(), f"{name} is out of date"
