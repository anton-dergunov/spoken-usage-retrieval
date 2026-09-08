"""Clip lookup serves cached alignment, and degrades honestly when there is none.

The roadmap's locked contract requires that every optional capability has a documented visible
state and never a fabricated value, so these assert the fallback as carefully as the success.
"""

from __future__ import annotations

from test_index_search_api import indexed_data

from speech_retrieval import Corpus, Settings
from speech_retrieval.alignment import (
    AlignedGroup,
    AlignmentProvenance,
    AlignmentResult,
    failed_result,
    resolve_model,
)
from speech_retrieval.alignment_store import AlignmentStore


def corpus_for(tmp_path):
    data_dir, catalogue_dir = indexed_data(tmp_path)
    settings = Settings(data_dir=data_dir, catalogue_dir=catalogue_dir)
    corpus = Corpus(settings)
    found = corpus.search("la verdad", source_language="es", match_mode="exact", limit=1)
    segment_id = found["results"][0]["segment_id"]
    return corpus, settings, corpus.clip(segment_id)


def provenance_for(language: str, settings: Settings) -> AlignmentProvenance:
    model = resolve_model(language, profile=settings.alignment_profile)
    assert model is not None
    return AlignmentProvenance(
        aligner="wav2vec2-ctc-forced-align-v1",
        model_id=model.model_id,
        model_license=model.license,
        device="cpu",
        settings_hash="s1",
    )


def store_alignment(settings: Settings, clip, result: AlignmentResult) -> None:
    store = AlignmentStore(settings.data_dir / "derived" / "alignments.sqlite3")
    store.save(
        cache_key="aln_test",
        result=result,
        source_text=clip.source_text,
        source_language=clip.source_language,
        clip_content_sha256="a" * 64,
        segment_id=clip.segment_id,
    )


def test_a_clip_with_no_cached_alignment_reports_unavailable_and_keeps_cue_timing(tmp_path):
    _, _, clip = corpus_for(tmp_path)
    assert clip.alignment_status == "unavailable"
    assert clip.alignment_groups is None
    assert clip.alignment_coverage is None
    assert clip.segments, "cue-level timing must survive so playback never breaks"


def test_a_cached_alignment_is_served_with_its_license_and_alongside_cue_timing(tmp_path):
    corpus, settings, clip = corpus_for(tmp_path)
    text = clip.source_text
    split = text.find(" ")
    groups = (
        AlignedGroup(
            text=text[:split],
            char_start=0,
            char_end=split,
            start=0.1,
            end=0.5,
            match_status="matched",
            confidence=0.9,
        ),
        AlignedGroup(
            text=text[split:],
            char_start=split,
            char_end=len(text),
            start=None,
            end=None,
            match_status="unmatched",
        ),
    )
    store_alignment(
        settings,
        clip,
        AlignmentResult(
            status="partial",
            groups=groups,
            coverage=0.5,
            provenance=provenance_for(clip.source_language, settings),
        ),
    )
    served = Corpus(settings).clip(clip.segment_id)
    assert served.alignment_status == "partial"
    assert served.alignment_coverage == 0.5
    assert served.alignment_provenance is not None
    assert served.alignment_provenance["model_license"] == "cc-by-nc-4.0"
    # The untimed group is omitted rather than given an invented time.
    assert served.alignment_groups is not None
    assert [group.text for group in served.alignment_groups] == [text[:split]]
    assert served.segments, "cue-level timing is still the fallback"


def test_a_failed_alignment_is_not_served_as_timing(tmp_path):
    corpus, settings, clip = corpus_for(tmp_path)
    store_alignment(
        settings, clip, failed_result("low_confidence", provenance=provenance_for("es", settings))
    )
    served = Corpus(settings).clip(clip.segment_id)
    assert served.alignment_status == "unavailable"
    assert served.alignment_groups is None


def test_refusing_non_commercial_models_hides_alignment_produced_by_one(tmp_path):
    """A host that must stay commercially clean must not be served CC-BY-NC timing."""
    corpus, settings, clip = corpus_for(tmp_path)
    store_alignment(
        settings,
        clip,
        AlignmentResult(
            status="complete",
            groups=(
                AlignedGroup(
                    text=clip.source_text,
                    char_start=0,
                    char_end=len(clip.source_text),
                    start=0.0,
                    end=1.0,
                    match_status="matched",
                ),
            ),
            coverage=1.0,
            provenance=provenance_for("es", settings),
        ),
    )
    strict = Settings(
        data_dir=settings.data_dir,
        catalogue_dir=settings.catalogue_dir,
        alignment_allow_non_commercial=False,
    )
    served = Corpus(strict).clip(clip.segment_id)
    assert served.alignment_status == "unavailable"


def test_a_missing_alignment_database_is_not_an_error(tmp_path):
    _, settings, clip = corpus_for(tmp_path)
    assert not (settings.data_dir / "derived" / "alignments.sqlite3").exists()
    assert Corpus(settings).clip(clip.segment_id).alignment_status == "unavailable"
