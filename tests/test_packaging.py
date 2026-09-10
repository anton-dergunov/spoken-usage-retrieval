"""What a host pins, and what it gets.

A consumer names one version and expects the wheel, the npm package and the running service to
agree about what that version is. Nothing enforced that before: the version was written out three
times by hand, and `__version__` — which `/api/v1/health/live` reports and which is stamped into
the search index as `meta.package_version` — was the copy most likely to be forgotten.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from speech_retrieval import __version__, packaged_catalogues, seed_catalogues
from speech_retrieval.catalogue import CatalogueError, load_catalogue_directory

ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


def test_the_reported_version_is_the_one_in_pyproject() -> None:
    assert __version__ == _project_version()


def test_the_npm_package_carries_the_same_version() -> None:
    manifest = json.loads(
        (ROOT / "packages" / "react" / "package.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == _project_version(), (
        "packages/react/package.json and pyproject.toml describe one release and must agree; "
        "a host pins a single version for both artifacts."
    )


def test_the_repository_catalogues_are_the_ones_the_wheel_will_carry() -> None:
    """`pyproject.toml` force-includes `config/channels`, which is not visible from a source tree.

    So this asserts the source of that copy rather than the copy, and the seeding test below
    exercises the mechanism against an explicit source. Together they mean a catalogue added to the
    repository reaches a wheel without anyone remembering to list it.
    """
    tracked = sorted(path.name for path in (ROOT / "config" / "channels").glob("*.json"))
    assert tracked, "the repository must ship at least one default catalogue"
    assert load_catalogue_directory(ROOT / "config" / "channels")


def test_seeding_fills_an_empty_directory(tmp_path: Path) -> None:
    destination = tmp_path / "channels"
    written = seed_catalogues(destination, source=ROOT / "config" / "channels")

    assert written
    assert sorted(path.name for path in written) == sorted(
        path.name for path in (ROOT / "config" / "channels").glob("*.json")
    )
    # The whole point: an empty catalogue directory cannot be filled through the API, because the
    # repository only rewrites a `<language>.json` that already exists.
    assert load_catalogue_directory(destination)


def test_seeding_never_overwrites_an_operator_edit(tmp_path: Path) -> None:
    destination = tmp_path / "channels"
    seed_catalogues(destination, source=ROOT / "config" / "channels")

    existing = sorted(destination.glob("*.json"))[0]
    document = json.loads(existing.read_text(encoding="utf-8"))
    document["description"] = "edited by the operator"
    existing.write_text(json.dumps(document), encoding="utf-8")

    assert seed_catalogues(destination, source=ROOT / "config" / "channels") == ()
    assert (
        json.loads(existing.read_text(encoding="utf-8"))["description"] == "edited by the operator"
    )


def test_seeding_is_safe_to_run_on_every_start(tmp_path: Path) -> None:
    destination = tmp_path / "channels"
    first = seed_catalogues(destination, source=ROOT / "config" / "channels")
    second = seed_catalogues(destination, source=ROOT / "config" / "channels")

    assert first and second == ()


def test_seeding_adds_a_language_a_later_version_introduced(tmp_path: Path) -> None:
    """Per file, not per directory: a directory holding `es.json` still receives a new `fr.json`.

    That asymmetry is deliberate. A channel the operator added to `es.json` is theirs and is left
    alone; a language they have never seen is not a list they have edited.
    """
    spanish = (ROOT / "config" / "channels" / "es.json").read_text(encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "es.json").write_text(spanish, encoding="utf-8")
    (source / "fr.json").write_text(
        spanish.replace('"language": "es"', '"language": "fr"'), "utf-8"
    )

    destination = tmp_path / "channels"
    destination.mkdir()
    (destination / "es.json").write_text(spanish, encoding="utf-8")

    assert [path.name for path in seed_catalogues(destination, source=source)] == ["fr.json"]


def test_a_corrupt_seed_is_refused_before_it_lands(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "es.json").write_text("{}", encoding="utf-8")
    destination = tmp_path / "channels"

    with pytest.raises(CatalogueError):
        seed_catalogues(destination, source=source)
    assert not list(destination.glob("*.json"))


def test_the_packaged_location_is_inside_the_distribution() -> None:
    """An editable install has no `catalogues/` directory; a built wheel does. Either way the path
    must point inside the package rather than at the working tree, or a container would seed from
    somewhere that is not shipped."""
    assert packaged_catalogues().name == "catalogues"
    assert packaged_catalogues().parent.name == "speech_retrieval"
