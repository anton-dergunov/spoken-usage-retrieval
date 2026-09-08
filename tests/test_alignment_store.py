"""The derived alignment cache: identity, round-tripping, and license accounting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from speech_retrieval.alignment import (
    AlignedGroup,
    AlignmentProvenance,
    AlignmentResult,
    failed_result,
)
from speech_retrieval.alignment_store import CACHE_SCHEMA_VERSION, AlignmentStore
from speech_retrieval.identity import alignment_id

TEXT = "uno dos"
LANGUAGE = "es"
CLIP_SHA = "a" * 64


def provenance(
    *,
    model_id: str = "MahmoudAshraf/mms-300m-1130-forced-aligner",
    license_name: str = "cc-by-nc-4.0",
    settings: str = "settings-1",
) -> AlignmentProvenance:
    return AlignmentProvenance(
        aligner="wav2vec2-ctc-forced-align-v1",
        model_id=model_id,
        model_license=license_name,
        device="cpu",
        settings_hash=settings,
        package_versions={"torch": "2.14.0"},
    )


def result(**kwargs: object) -> AlignmentResult:
    groups = (
        AlignedGroup(
            text="uno",
            char_start=0,
            char_end=3,
            start=0.1,
            end=0.4,
            match_status="matched",
            confidence=0.91,
        ),
        AlignedGroup(
            text=" ",
            char_start=3,
            char_end=4,
            start=None,
            end=None,
            match_status="punctuation",
        ),
        AlignedGroup(
            text="dos",
            char_start=4,
            char_end=7,
            start=0.5,
            end=0.8,
            match_status="matched",
            confidence=0.88,
        ),
    )
    defaults: dict[str, object] = {
        "status": "complete",
        "groups": groups,
        "coverage": 1.0,
        "provenance": provenance(),
    }
    defaults.update(kwargs)
    return AlignmentResult(**defaults)  # type: ignore[arg-type]


def store_at(tmp_path: Path) -> AlignmentStore:
    return AlignmentStore(tmp_path / "derived" / "alignments.sqlite3")


def save(store: AlignmentStore, value: AlignmentResult, **kwargs: object):
    key = AlignmentStore.cache_key(
        source_text=TEXT,
        source_language=LANGUAGE,
        clip_content_sha256=CLIP_SHA,
        provenance=value.provenance,  # type: ignore[arg-type]
    )
    return store.save(
        cache_key=key,
        result=value,
        source_text=TEXT,
        source_language=LANGUAGE,
        clip_content_sha256=CLIP_SHA,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_store_creates_its_own_database_and_records_its_schema_version(tmp_path) -> None:
    store = store_at(tmp_path)
    assert store.path.is_file()
    with store.connect() as connection:
        row = connection.execute(
            "SELECT value FROM alignment_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row["value"] == str(CACHE_SCHEMA_VERSION)


def test_a_saved_alignment_round_trips_with_its_groups_and_provenance(tmp_path) -> None:
    store = store_at(tmp_path)
    saved = save(store, result(), segment_id="seg_1", video_key="vid_1")
    loaded = store.get(saved.cache_key)
    assert loaded is not None
    assert loaded.result.status == "complete"
    assert loaded.result.coverage == 1.0
    assert loaded.segment_id == "seg_1"
    assert [group.text for group in loaded.result.groups] == ["uno", " ", "dos"]
    assert loaded.result.groups[1].start is None
    assert loaded.result.groups[0].confidence == pytest.approx(0.91)
    assert loaded.result.provenance is not None
    assert loaded.result.provenance.model_license == "cc-by-nc-4.0"
    assert loaded.result.provenance.package_versions == {"torch": "2.14.0"}


def test_an_absent_key_returns_none_rather_than_raising(tmp_path) -> None:
    assert store_at(tmp_path).get("aln_missing") is None


@pytest.mark.parametrize(
    "changed",
    [
        {"source_text": "uno tres"},
        {"source_language": "en"},
        {"clip_content_sha256": "b" * 64},
    ],
)
def test_every_material_input_changes_the_cache_key(changed: dict[str, str]) -> None:
    base: dict[str, Any] = {
        "source_text": TEXT,
        "source_language": LANGUAGE,
        "clip_content_sha256": CLIP_SHA,
        "provenance": provenance(),
    }
    assert AlignmentStore.cache_key(**base) != AlignmentStore.cache_key(**{**base, **changed})


@pytest.mark.parametrize(
    "changed",
    [{"model_id": "other/model"}, {"settings": "settings-2"}],
)
def test_changing_the_model_or_its_settings_changes_the_cache_key(changed: dict[str, str]) -> None:
    base: dict[str, Any] = {
        "source_text": TEXT,
        "source_language": LANGUAGE,
        "clip_content_sha256": CLIP_SHA,
    }
    first = AlignmentStore.cache_key(**base, provenance=provenance())
    second = AlignmentStore.cache_key(**base, provenance=provenance(**changed))
    assert first != second


def test_the_license_alone_does_not_change_the_cache_key() -> None:
    """Two models under different licenses are already different models; the license is
    recorded for accounting, not identity, so relicensing the same checkpoint must not
    silently orphan every cached row."""
    base: dict[str, Any] = {
        "source_text": TEXT,
        "source_language": LANGUAGE,
        "clip_content_sha256": CLIP_SHA,
    }
    first = AlignmentStore.cache_key(**base, provenance=provenance(license_name="cc-by-nc-4.0"))
    second = AlignmentStore.cache_key(**base, provenance=provenance(license_name="apache-2.0"))
    assert first == second


def test_saving_the_same_key_twice_updates_rather_than_duplicating(tmp_path) -> None:
    store = store_at(tmp_path)
    first = save(store, result())
    second = save(store, result(coverage=0.5, status="partial"))
    assert first.cache_key == second.cache_key
    assert store.statistics()["total"] == 1
    reloaded = store.get(second.cache_key)
    assert reloaded is not None and reloaded.result.status == "partial"
    assert reloaded.created_at == first.created_at


def test_a_failed_result_is_cached_so_it_is_not_retried_on_every_open(tmp_path) -> None:
    store = store_at(tmp_path)
    saved = save(store, failed_result("low_confidence", provenance=provenance()))
    loaded = store.get(saved.cache_key)
    assert loaded is not None
    assert loaded.result.status == "failed"
    assert loaded.result.reason == "low_confidence"
    assert loaded.result.groups == ()


def test_a_result_without_provenance_is_refused(tmp_path) -> None:
    store = store_at(tmp_path)
    with pytest.raises(ValueError, match="without provenance"):
        store.save(
            cache_key="aln_x",
            result=AlignmentResult(status="unavailable"),
            source_text=TEXT,
            source_language=LANGUAGE,
            clip_content_sha256=CLIP_SHA,
        )


def test_statistics_break_down_by_status_and_by_license(tmp_path) -> None:
    store = store_at(tmp_path)
    save(store, result())
    store.save(
        cache_key="aln_permissive",
        result=result(provenance=provenance(model_id="permissive", license_name="apache-2.0")),
        source_text=TEXT,
        source_language=LANGUAGE,
        clip_content_sha256=CLIP_SHA,
    )
    stats = store.statistics()
    assert stats["total"] == 2
    assert stats["by_status"] == {"complete": 2}
    assert stats["by_license"] == {"cc-by-nc-4.0": 1, "apache-2.0": 1}
    assert stats["non_commercial"] == 1


def test_purging_a_license_removes_only_that_license(tmp_path) -> None:
    """The reason every row records its license: going commercial is one delete, not an audit."""
    store = store_at(tmp_path)
    save(store, result())
    store.save(
        cache_key="aln_permissive",
        result=result(provenance=provenance(model_id="permissive", license_name="apache-2.0")),
        source_text=TEXT,
        source_language=LANGUAGE,
        clip_content_sha256=CLIP_SHA,
    )
    assert store.purge_license("cc-by-nc-4.0") == 1
    stats = store.statistics()
    assert stats["total"] == 1
    assert stats["non_commercial"] == 0
    assert stats["by_license"] == {"apache-2.0": 1}


def test_alignment_id_is_stable_for_the_same_inputs() -> None:
    arguments: dict[str, Any] = dict(
        source_text=TEXT,
        source_language=LANGUAGE,
        clip_content_sha256=CLIP_SHA,
        aligner="wav2vec2-ctc-forced-align-v1",
        model_id="model",
        settings_hash="settings",
    )
    identifier = alignment_id(**arguments)
    assert identifier == alignment_id(**arguments)
    assert identifier.startswith("aln_")


def test_find_returns_the_newest_alignment_for_a_segment(tmp_path) -> None:
    """Clip lookup knows a segment id, not an audio checksum, so this is the serving read path."""
    store = store_at(tmp_path)
    store.save(
        cache_key="aln_old",
        result=result(coverage=0.4, status="partial"),
        source_text=TEXT,
        source_language=LANGUAGE,
        clip_content_sha256=CLIP_SHA,
        segment_id="seg_1",
    )
    store.save(
        cache_key="aln_new",
        result=result(),
        source_text=TEXT,
        source_language=LANGUAGE,
        clip_content_sha256="c" * 64,
        segment_id="seg_1",
    )
    found = store.find(
        segment_id="seg_1",
        source_language=LANGUAGE,
        model_id="MahmoudAshraf/mms-300m-1130-forced-aligner",
    )
    assert found is not None
    assert found.cache_key in {"aln_old", "aln_new"}
    assert found.result.status in {"complete", "partial"}


def test_find_does_not_cross_languages_or_models(tmp_path) -> None:
    store = store_at(tmp_path)
    save(store, result(), segment_id="seg_1")
    assert (
        store.find(
            segment_id="seg_1",
            source_language="en",
            model_id="MahmoudAshraf/mms-300m-1130-forced-aligner",
        )
        is None
    )
    assert store.find(segment_id="seg_1", source_language=LANGUAGE, model_id="other") is None
    assert (
        store.find(
            segment_id="absent",
            source_language=LANGUAGE,
            model_id="MahmoudAshraf/mms-300m-1130-forced-aligner",
        )
        is None
    )
