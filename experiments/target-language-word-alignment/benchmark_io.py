"""Optional reader for externally downloaded Pharaoh/XL-WA-style benchmark files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict


class PharaohExample(TypedDict):
    source_text: str
    target_text: str
    sure_links: list[tuple[int, int]]
    possible_links: list[tuple[int, int]]


def read_pharaoh(
    source_path: Path, target_path: Path, alignment_path: Path
) -> Iterator[PharaohExample]:
    """Read parallel text and zero-based ``i-j``/``i?j`` Pharaoh alignments.

    The repository intentionally does not download or vendor any benchmark. In particular, XL-WA
    is CC BY-NC-SA and some language pairs require separate access from its maintainers.
    """
    sources = source_path.read_text(encoding="utf-8").splitlines()
    targets = target_path.read_text(encoding="utf-8").splitlines()
    alignments = alignment_path.read_text(encoding="utf-8").splitlines()
    if not (len(sources) == len(targets) == len(alignments)):
        raise ValueError("parallel and alignment files must have the same number of lines")
    for source, target, alignment in zip(sources, targets, alignments, strict=True):
        sure: list[tuple[int, int]] = []
        possible: list[tuple[int, int]] = []
        for link in alignment.split():
            separator = "?" if "?" in link else "-"
            try:
                left, right = (int(value) for value in link.split(separator, 1))
            except ValueError as error:
                raise ValueError(f"invalid Pharaoh link: {link!r}") from error
            (possible if separator == "?" else sure).append((left, right))
        yield {
            "source_text": source,
            "target_text": target,
            "sure_links": sure,
            "possible_links": possible,
        }
