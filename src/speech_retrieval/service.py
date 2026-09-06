from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .acquisition import Runner, acquire
from .catalogue import load_catalogue_directory
from .contracts import FailureRecord, LanguageUpdate, UpdateSummary
from .indexing import build_index
from .settings import Settings


def _now() -> str:
    return datetime.now(UTC).isoformat()


def activity_is_alive(activity: object) -> bool:
    if not isinstance(activity, dict):
        return False
    pid = activity.get("pid")
    if not isinstance(pid, (int, str)):
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


class Indexer:
    """Foreground acquisition and full-index operations."""

    def __init__(self, settings: Settings, *, runner: Runner | None = None):
        self.settings = settings
        self.runner = runner
        self.state_path = settings.data_dir / "reports" / "update-state.json"
        self.lock_path = settings.data_dir / "reports" / "corpus-operation.lock"

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.state_path)

    def _start(self, operation: str) -> tuple[str, dict[str, Any]]:
        started_at = _now()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as error:
            try:
                owner = int(self.lock_path.read_text(encoding="utf-8"))
                os.kill(owner, 0)
            except (OSError, TypeError, ValueError):
                self.lock_path.unlink(missing_ok=True)
                return self._start(operation)
            raise RuntimeError("another corpus operation is already running") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(str(os.getpid()))
        state = self._read_state()
        current = state.get("current_activity")
        if current:
            current_record = current if isinstance(current, dict) else {}
            if not activity_is_alive(current):
                stale = FailureRecord(
                    occurred_at=started_at,
                    operation=str(current_record.get("operation", "unknown")),
                    message="Previous corpus operation ended without clearing its activity marker",
                )
                state["recent_failures"] = [
                    stale.model_dump(mode="json"),
                    *state.get("recent_failures", []),
                ][: self.settings.recent_failure_limit]
            else:
                self.lock_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "another corpus operation is already recorded: "
                    f"{current_record.get('operation', 'unknown')}"
                )
        state["current_activity"] = {
            "operation": operation,
            "started_at": started_at,
            "pid": os.getpid(),
        }
        state.setdefault("recent_failures", [])
        try:
            self._write_state(state)
        except Exception:
            self.lock_path.unlink(missing_ok=True)
            raise
        return started_at, state

    def _finish(
        self,
        state: dict[str, Any],
        failures: list[FailureRecord],
        *,
        successful_update: str | None = None,
    ) -> None:
        state["current_activity"] = None
        if successful_update is not None:
            state["last_successful_update"] = successful_update
        previous = state.get("recent_failures", [])
        state["recent_failures"] = [
            *(item.model_dump(mode="json") for item in failures),
            *previous,
        ][: self.settings.recent_failure_limit]
        try:
            self._write_state(state)
        finally:
            self.lock_path.unlink(missing_ok=True)

    def reindex(self) -> dict[str, Any]:
        _started_at, state = self._start("reindex")
        failures: list[FailureRecord] = []
        try:
            return build_index(
                data_dir=self.settings.data_dir,
                max_ngram=self.settings.max_ngram,
                analyzer=self.settings.analyzer,
                models_dir=self.settings.resolved_models_dir,
            )
        except Exception as error:
            failures.append(
                FailureRecord(occurred_at=_now(), operation="reindex", message=str(error))
            )
            raise
        finally:
            self._finish(state, failures)

    def update_once(self) -> UpdateSummary:
        started_at, state = self._start("update")
        failures: list[FailureRecord] = []
        languages: list[LanguageUpdate] = []
        index_report: dict[str, Any] | None = None
        try:
            catalogues = load_catalogue_directory(self.settings.catalogue_dir)
        except Exception as error:
            failure = FailureRecord(occurred_at=_now(), operation="update", message=str(error))
            self._finish(state, [failure])
            return UpdateSummary(
                started_at=started_at,
                completed_at=_now(),
                successful=False,
                downloaded=0,
                cached=0,
                failures=1,
                languages=[],
                index=None,
            )
        enabled = [catalogue for catalogue in catalogues if catalogue.enabled_channels]
        if not enabled:
            failure = FailureRecord(
                occurred_at=_now(),
                operation="update",
                message="No enabled channel catalogues were found",
            )
            self._finish(state, [failure])
            return UpdateSummary(
                started_at=started_at,
                completed_at=_now(),
                successful=False,
                downloaded=0,
                cached=0,
                failures=1,
                languages=[],
                index=None,
            )

        for catalogue in enabled:
            try:
                arguments: dict[str, Any] = {
                    "config_path": self.settings.catalogue_dir / f"{catalogue.language}.json",
                    "data_dir": self.settings.data_dir,
                    "limit": self.settings.acquisition_limit,
                    "scan_limit": self.settings.scan_limit,
                }
                if self.runner is not None:
                    arguments["runner"] = self.runner
                report = acquire(**arguments)
                downloaded = sum(item.get("status") == "downloaded" for item in report["videos"])
                cached = sum(item.get("status") == "cached" for item in report["videos"])
                for item in report["failures"]:
                    failures.append(
                        FailureRecord(
                            occurred_at=report.get("completed_at", _now()),
                            operation="acquisition",
                            source_language=catalogue.language,
                            channel=item.get("channel"),
                            item=item.get("video_id"),
                            message=item.get("error", "Acquisition failed"),
                        )
                    )
                languages.append(
                    LanguageUpdate(
                        source_language=catalogue.language,
                        downloaded=downloaded,
                        cached=cached,
                        failures=len(report["failures"]),
                        complete=bool(report["complete"]),
                    )
                )
            except Exception as error:
                failures.append(
                    FailureRecord(
                        occurred_at=_now(),
                        operation="acquisition",
                        source_language=catalogue.language,
                        message=str(error),
                    )
                )
                languages.append(
                    LanguageUpdate(
                        source_language=catalogue.language,
                        downloaded=0,
                        cached=0,
                        failures=1,
                        complete=False,
                    )
                )

        try:
            index_report = build_index(
                data_dir=self.settings.data_dir,
                max_ngram=self.settings.max_ngram,
                analyzer=self.settings.analyzer,
                models_dir=self.settings.resolved_models_dir,
            )
        except Exception as error:
            failures.append(
                FailureRecord(occurred_at=_now(), operation="index", message=str(error))
            )

        completed_at = _now()
        successful = index_report is not None and all(item.complete for item in languages)
        self._finish(
            state,
            failures,
            successful_update=completed_at if successful else None,
        )
        return UpdateSummary(
            started_at=started_at,
            completed_at=completed_at,
            successful=successful,
            downloaded=sum(item.downloaded for item in languages),
            cached=sum(item.cached for item in languages),
            failures=sum(item.failures for item in languages) + (1 if index_report is None else 0),
            languages=languages,
            index=index_report,
        )


def read_update_state(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
