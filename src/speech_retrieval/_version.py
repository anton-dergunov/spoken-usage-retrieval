"""The one place the running version is determined.

Its own module rather than a literal in `__init__`, for two reasons. It is read from the installed
distribution, so `pyproject.toml` is the single source and a second literal cannot drift from the
wheel a host actually pinned. And `api` and `indexing` both need it while `__init__` is still
importing them, which a value defined in `__init__` after those imports cannot provide.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

try:
    #: Reported by `/api/v1/health/live`, used as the OpenAPI document version, and stamped into
    #: the search index as `meta.package_version`.
    __version__ = _installed_version("spoken-usage-retrieval")
except PackageNotFoundError:  # pragma: no cover - a source tree that was never installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
