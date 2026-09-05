from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CATALOGUE_SCHEMA_VERSION = 1
CHANNEL_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class CatalogueError(ValueError):
    pass


def canonical_language(value: str) -> str:
    """Return the canonical casing for the commonly used BCP-47 grammar."""
    if not isinstance(value, str) or not value or value != value.strip() or "_" in value:
        raise ValueError(f"invalid BCP-47 language tag: {value!r}")
    parts = value.split("-")
    if any(not part or not part.isascii() or not part.isalnum() for part in parts):
        raise ValueError(f"invalid BCP-47 language tag: {value!r}")
    if not (2 <= len(parts[0]) <= 3 and parts[0].isalpha()):
        raise ValueError(f"invalid BCP-47 language tag: {value!r}")

    result = [parts[0].lower()]
    index = 1
    if index < len(parts) and len(parts[index]) == 4 and parts[index].isalpha():
        result.append(parts[index].title())
        index += 1
    if index < len(parts) and (
        (len(parts[index]) == 2 and parts[index].isalpha())
        or (len(parts[index]) == 3 and parts[index].isdigit())
    ):
        result.append(parts[index].upper())
        index += 1

    variants: set[str] = set()
    while index < len(parts) and (
        5 <= len(parts[index]) <= 8 or (len(parts[index]) == 4 and parts[index][0].isdigit())
    ):
        variant = parts[index].lower()
        if variant in variants:
            raise ValueError(f"invalid BCP-47 language tag: {value!r}")
        variants.add(variant)
        result.append(variant)
        index += 1

    extensions: set[str] = set()
    while index < len(parts) and len(parts[index]) == 1 and parts[index].lower() != "x":
        singleton = parts[index].lower()
        if singleton in extensions:
            raise ValueError(f"invalid BCP-47 language tag: {value!r}")
        extensions.add(singleton)
        result.append(singleton)
        index += 1
        start = index
        while index < len(parts) and 2 <= len(parts[index]) <= 8:
            result.append(parts[index].lower())
            index += 1
        if index == start:
            raise ValueError(f"invalid BCP-47 language tag: {value!r}")

    if index < len(parts) and parts[index].lower() == "x":
        result.append("x")
        index += 1
        if index == len(parts):
            raise ValueError(f"invalid BCP-47 language tag: {value!r}")
        while index < len(parts) and 1 <= len(parts[index]) <= 8:
            result.append(parts[index].lower())
            index += 1
    if index != len(parts):
        raise ValueError(f"invalid BCP-47 language tag: {value!r}")
    return "-".join(result)


def _error(path: str, message: str) -> CatalogueError:
    return CatalogueError(f"{path}: {message}")


def _object(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, "expected an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        raise _error(f"{path}.{unknown[0]}", "unknown field")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "expected a non-empty string")
    return value


def _optional_strings(value: Any, path: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise _error(path, "expected an array of strings")
    result = tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise _error(path, "contains duplicate values")
    return result


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    url: str
    enabled: bool
    varieties: tuple[str, ...] | None = None
    speech_style: tuple[str, ...] | None = None
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "enabled": self.enabled,
        }
        if self.varieties is not None:
            result["varieties"] = list(self.varieties)
        if self.speech_style is not None:
            result["speech_style"] = list(self.speech_style)
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass(frozen=True)
class CatalogueSection:
    id: str
    name: str
    channels: tuple[Channel, ...]
    description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "channels": [channel.as_dict() for channel in self.channels],
        }
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass(frozen=True)
class Catalogue:
    schema_version: int
    language: str
    sections: tuple[CatalogueSection, ...]
    description: str | None = None

    @property
    def channels(self) -> tuple[Channel, ...]:
        return tuple(channel for section in self.sections for channel in section.channels)

    @property
    def enabled_channels(self) -> tuple[Channel, ...]:
        return tuple(channel for channel in self.channels if channel.enabled)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "language": self.language,
            "sections": [section.as_dict() for section in self.sections],
        }
        if self.description is not None:
            result["description"] = self.description
        return result


def load_catalogue(path: Path) -> Catalogue:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogueError(f"{path}: cannot read catalogue: {error}") from error
    root = _object(payload, "$", {"schema_version", "language", "description", "sections"})
    if root.get("schema_version") != CATALOGUE_SCHEMA_VERSION:
        raise _error("$.schema_version", f"expected {CATALOGUE_SCHEMA_VERSION}")
    raw_language = _string(root.get("language"), "$.language")
    try:
        language = canonical_language(raw_language)
    except ValueError as error:
        raise _error("$.language", str(error)) from error
    if language != raw_language:
        raise _error("$.language", f"expected canonical tag {language!r}")
    if path.stem != language:
        raise _error("$.language", f"does not match catalogue filename {path.name!r}")
    description = root.get("description")
    if description is not None:
        description = _string(description, "$.description")
    raw_sections = root.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise _error("$.sections", "expected a non-empty array")

    sections: list[CatalogueSection] = []
    channel_ids: set[str] = set()
    channel_urls: set[str] = set()
    section_ids: set[str] = set()
    for section_index, raw_section in enumerate(raw_sections):
        section_path = f"$.sections[{section_index}]"
        section = _object(raw_section, section_path, {"id", "name", "description", "channels"})
        section_id = _string(section.get("id"), f"{section_path}.id")
        if section_id in section_ids:
            raise _error(f"{section_path}.id", f"duplicate section ID {section_id!r}")
        section_ids.add(section_id)
        section_name = _string(section.get("name"), f"{section_path}.name")
        section_description = section.get("description")
        if section_description is not None:
            section_description = _string(section_description, f"{section_path}.description")
        raw_channels = section.get("channels")
        if not isinstance(raw_channels, list) or not raw_channels:
            raise _error(f"{section_path}.channels", "expected a non-empty array")
        channels: list[Channel] = []
        for channel_index, raw_channel in enumerate(raw_channels):
            channel_path = f"{section_path}.channels[{channel_index}]"
            item = _object(
                raw_channel,
                channel_path,
                {"id", "name", "url", "enabled", "varieties", "speech_style", "description"},
            )
            channel_id = _string(item.get("id"), f"{channel_path}.id")
            if not CHANNEL_ID_RE.fullmatch(channel_id):
                raise _error(f"{channel_path}.id", "expected a stable kebab-case ID")
            if channel_id in channel_ids:
                raise _error(f"{channel_path}.id", f"duplicate channel ID {channel_id!r}")
            channel_ids.add(channel_id)
            url = _string(item.get("url"), f"{channel_path}.url")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise _error(f"{channel_path}.url", "expected an absolute HTTP(S) URL")
            normalized_url = url.rstrip("/").casefold()
            if normalized_url in channel_urls:
                raise _error(f"{channel_path}.url", f"duplicate channel URL {url!r}")
            channel_urls.add(normalized_url)
            enabled = item.get("enabled")
            if not isinstance(enabled, bool):
                raise _error(f"{channel_path}.enabled", "expected a boolean")
            item_description = item.get("description")
            if item_description is not None:
                item_description = _string(item_description, f"{channel_path}.description")
            channels.append(
                Channel(
                    id=channel_id,
                    name=_string(item.get("name"), f"{channel_path}.name"),
                    url=url,
                    enabled=enabled,
                    varieties=_optional_strings(item.get("varieties"), f"{channel_path}.varieties"),
                    speech_style=_optional_strings(
                        item.get("speech_style"), f"{channel_path}.speech_style"
                    ),
                    description=item_description,
                )
            )
        sections.append(
            CatalogueSection(
                id=section_id,
                name=section_name,
                description=section_description,
                channels=tuple(channels),
            )
        )
    return Catalogue(
        schema_version=CATALOGUE_SCHEMA_VERSION,
        language=language,
        description=description,
        sections=tuple(sections),
    )


def load_catalogue_directory(path: Path) -> tuple[Catalogue, ...]:
    if not path.exists():
        return ()
    catalogues = tuple(load_catalogue(item) for item in sorted(path.glob("*.json")))
    languages = [catalogue.language for catalogue in catalogues]
    if len(set(languages)) != len(languages):
        raise CatalogueError(f"{path}: contains duplicate catalogue languages")
    return catalogues
