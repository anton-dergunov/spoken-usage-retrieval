from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from .catalogue import Catalogue, CatalogueSection, Channel, canonical_language, load_catalogue
from .contracts import ChannelCreate, ChannelRecord, ChannelUpdate


class ChannelRepositoryError(ValueError):
    pass


class ChannelNotFoundError(ChannelRepositoryError):
    pass


class ChannelConflictError(ChannelRepositoryError):
    pass


class ChannelRepository:
    """Validated, atomic access to versioned channel catalogue files."""

    def __init__(self, catalogue_dir: str | Path):
        self.catalogue_dir = Path(catalogue_dir)
        self._lock = threading.RLock()

    def _path(self, language: str) -> Path:
        language = canonical_language(language)
        path = self.catalogue_dir / f"{language}.json"
        if not path.is_file():
            raise ChannelNotFoundError(f"Channel catalogue does not exist: {language}")
        return path

    def _load(self, language: str) -> tuple[Path, Catalogue]:
        path = self._path(language)
        return path, load_catalogue(path)

    @staticmethod
    def _record(language: str, section: CatalogueSection, channel: Channel) -> ChannelRecord:
        return ChannelRecord(
            source_language=language,
            section_id=section.id,
            section_name=section.name,
            **channel.as_dict(),
        )

    def list(self, source_language: str | None = None) -> list[ChannelRecord]:
        with self._lock:
            if source_language is not None:
                catalogues = [self._load(source_language)[1]]
            elif self.catalogue_dir.exists():
                catalogues = [
                    load_catalogue(path) for path in sorted(self.catalogue_dir.glob("*.json"))
                ]
            else:
                catalogues = []
            return [
                self._record(catalogue.language, section, channel)
                for catalogue in catalogues
                for section in catalogue.sections
                for channel in section.channels
            ]

    def _write(self, path: Path, catalogue: Catalogue) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(catalogue.as_dict(), ensure_ascii=False, indent=2) + "\n"
        with tempfile.TemporaryDirectory(prefix=".catalogue-validation-", dir=path.parent) as root:
            validation_path = Path(root) / path.name
            validation_path.write_text(serialized, encoding="utf-8")
            load_catalogue(validation_path)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        try:
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def add(self, request: ChannelCreate) -> ChannelRecord:
        with self._lock:
            path, catalogue = self._load(request.source_language)
            if any(channel.id == request.id for channel in catalogue.channels):
                raise ChannelConflictError(f"Channel ID already exists: {request.id}")
            if any(
                channel.url.rstrip("/").casefold() == request.url.rstrip("/").casefold()
                for channel in catalogue.channels
            ):
                raise ChannelConflictError(f"Channel URL already exists: {request.url}")
            selected: CatalogueSection | None = None
            sections = []
            channel = Channel(
                id=request.id,
                name=request.name,
                url=request.url,
                enabled=request.enabled,
                varieties=tuple(request.varieties) if request.varieties is not None else None,
                speech_style=tuple(request.speech_style)
                if request.speech_style is not None
                else None,
                description=request.description,
            )
            for section in catalogue.sections:
                if section.id == request.section_id:
                    selected = replace(section, channels=(*section.channels, channel))
                    sections.append(selected)
                else:
                    sections.append(section)
            if selected is None:
                raise ChannelNotFoundError(
                    f"Catalogue section does not exist: {request.section_id}"
                )
            self._write(path, replace(catalogue, sections=tuple(sections)))
            return self._record(catalogue.language, selected, channel)

    def update(
        self, source_language: str, channel_id: str, request: ChannelUpdate
    ) -> ChannelRecord:
        with self._lock:
            path, catalogue = self._load(source_language)
            changed: ChannelRecord | None = None
            sections = []
            supplied = request.model_fields_set
            if not supplied:
                raise ChannelRepositoryError("At least one channel field must be supplied")
            for section in catalogue.sections:
                channels = []
                for channel in section.channels:
                    if channel.id != channel_id:
                        channels.append(channel)
                        continue
                    name = request.name if "name" in supplied else channel.name
                    url = request.url if "url" in supplied else channel.url
                    if name is None or url is None:
                        raise ChannelRepositoryError("Channel name and URL cannot be null")
                    varieties = (
                        (tuple(request.varieties) if request.varieties is not None else None)
                        if "varieties" in supplied
                        else channel.varieties
                    )
                    speech_style = (
                        (tuple(request.speech_style) if request.speech_style is not None else None)
                        if "speech_style" in supplied
                        else channel.speech_style
                    )
                    description = (
                        request.description if "description" in supplied else channel.description
                    )
                    channel = replace(
                        channel,
                        name=name,
                        url=url,
                        varieties=varieties,
                        speech_style=speech_style,
                        description=description,
                    )
                    changed = self._record(catalogue.language, section, channel)
                    channels.append(channel)
                sections.append(replace(section, channels=tuple(channels)))
            if changed is None:
                raise ChannelNotFoundError(f"Channel does not exist: {channel_id}")
            self._write(path, replace(catalogue, sections=tuple(sections)))
            return changed

    def set_enabled(self, source_language: str, channel_id: str, enabled: bool) -> ChannelRecord:
        with self._lock:
            path, catalogue = self._load(source_language)
            changed: ChannelRecord | None = None
            sections = []
            for section in catalogue.sections:
                channels = []
                for channel in section.channels:
                    if channel.id == channel_id:
                        channel = replace(channel, enabled=enabled)
                        changed = self._record(catalogue.language, section, channel)
                    channels.append(channel)
                sections.append(replace(section, channels=tuple(channels)))
            if changed is None:
                raise ChannelNotFoundError(f"Channel does not exist: {channel_id}")
            self._write(path, replace(catalogue, sections=tuple(sections)))
            return changed

    def enable(self, source_language: str, channel_id: str) -> ChannelRecord:
        return self.set_enabled(source_language, channel_id, True)

    def disable(self, source_language: str, channel_id: str) -> ChannelRecord:
        return self.set_enabled(source_language, channel_id, False)
