import json
from pathlib import Path
from typing import Any

import pytest

from speech_retrieval.catalogue import CatalogueError, canonical_language, load_catalogue


@pytest.mark.parametrize(
    ("value", "expected"),
    [("es", "es"), ("pt-br", "pt-BR"), ("ZH-hans", "zh-Hans"), ("de-CH-1901", "de-CH-1901")],
)
def test_common_bcp47_tags_are_canonicalized(value, expected):
    assert canonical_language(value) == expected


@pytest.mark.parametrize("value", ["", "e", "english", "pt_BR", "es-", "en-a", "en-US-US"])
def test_invalid_bcp47_tags_are_rejected(value):
    with pytest.raises(ValueError, match="invalid BCP-47"):
        canonical_language(value)


def test_catalogue_round_trip_preserves_optional_editorial_content(tmp_path):
    payload: dict[str, Any] = {
        "schema_version": 1,
        "language": "pt-BR",
        "description": "Editorial overview",
        "sections": [
            {
                "id": "conversation_sources",
                "name": "Conversation",
                "description": "Section notes",
                "channels": [
                    {
                        "id": "example-channel",
                        "name": "Example",
                        "url": "https://example.test/channel",
                        "enabled": True,
                        "varieties": ["São Paulo"],
                        "speech_style": ["conversation"],
                        "description": "Channel notes",
                    },
                    {
                        "id": "minimal-channel",
                        "name": "Minimal",
                        "url": "https://minimal.test/channel",
                        "enabled": False,
                    },
                ],
            }
        ],
    }
    path = tmp_path / "pt-BR.json"
    path.write_text(json.dumps(payload))
    assert load_catalogue(path).as_dict() == payload


def test_catalogue_errors_include_field_paths_and_reject_duplicates(tmp_path):
    payload: dict[str, Any] = {
        "schema_version": 1,
        "language": "es",
        "sections": [
            {
                "id": "one",
                "name": "One",
                "channels": [
                    {
                        "id": "duplicate",
                        "name": "One",
                        "url": "https://example.test/one",
                        "enabled": True,
                    },
                    {
                        "id": "duplicate",
                        "name": "Two",
                        "url": "https://example.test/two",
                        "enabled": False,
                    },
                ],
            }
        ],
    }
    path = tmp_path / "es.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(CatalogueError, match=r"\$\.sections\[0\]\.channels\[1\]\.id"):
        load_catalogue(path)
    payload["sections"][0]["channels"][1]["id"] = "another-channel"
    payload["sections"][0]["channels"][1]["url"] = "https://example.test/one"
    path.write_text(json.dumps(payload))
    with pytest.raises(CatalogueError, match=r"\$\.sections\[0\]\.channels\[1\]\.url"):
        load_catalogue(path)


def test_migrated_spanish_catalogue_preserves_breadth_and_mvp_activation():
    path = Path(__file__).parents[1] / "config" / "channels" / "es.json"
    catalogue = load_catalogue(path)
    assert len(catalogue.sections) == 7
    assert len(catalogue.channels) == 24
    assert {channel.id for channel in catalogue.enabled_channels} == {
        "easy-spanish",
        "spanish-after-hours",
        "luisito-comunica",
        "luzu-tv",
    }
