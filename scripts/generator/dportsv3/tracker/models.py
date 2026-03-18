"""Request and response models for the tracker API."""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Literal

_pydantic = (
    importlib.import_module("pydantic")
    if importlib.util.find_spec("pydantic")
    else None
)

if _pydantic is not None:
    BaseModel = _pydantic.BaseModel
    ConfigDict = _pydantic.ConfigDict
    Field = _pydantic.Field
else:  # pragma: no cover - exercised only before tracker deps exist

    class BaseModel:
        """Minimal fallback so tracker modules stay importable without extras."""

        model_config: dict[str, Any] = {}

        def __init__(self, **data: Any) -> None:
            annotations = getattr(self, "__annotations__", {})
            for key in annotations:
                if key in data:
                    setattr(self, key, data[key])
                elif hasattr(type(self), key):
                    setattr(self, key, getattr(type(self), key))
                else:
                    setattr(self, key, None)

        def model_dump(self) -> dict[str, Any]:
            return {
                key: getattr(self, key) for key in getattr(self, "__annotations__", {})
            }

    def Field(default: Any = None, **kwargs: Any) -> Any:
        if "default_factory" in kwargs:
            factory = kwargs["default_factory"]
            return factory()
        return default

    def ConfigDict(**kwargs: Any) -> dict[str, Any]:
        return kwargs


BuildResultLiteral = Literal["success", "failure", "skipped", "ignored"]


class TrackerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartBuildRequest(TrackerModel):
    target: str
    build_type: str
    started_at: str | None = None
    total_expected: int | None = None


class StartBuildResponse(TrackerModel):
    id: int


class FinishBuildRequest(TrackerModel):
    finished_at: str | None = None
    commit_sha: str | None = None
    commit_branch: str | None = None
    commit_pushed_at: str | None = None


class ResultItem(TrackerModel):
    origin: str
    version: str
    result: BuildResultLiteral
    log_url: str | None = None


class RecordResultsRequest(TrackerModel):
    results: list[ResultItem] = Field(default_factory=list)


class RecordResultsResponse(TrackerModel):
    recorded: int


class BuildRunOut(TrackerModel):
    id: int
    target: str
    build_type: str
    started_at: str
    finished_at: str | None = None
    commit_sha: str | None = None
    commit_branch: str | None = None
    commit_pushed_at: str | None = None
    total_expected: int | None = None
    result_count: int | None = None
    success_count: int | None = None
    failure_count: int | None = None
    skipped_count: int | None = None
    ignored_count: int | None = None
    queued_count: int | None = None
    building_count: int | None = None


class PortStatusOut(TrackerModel):
    target: str
    origin: str
    last_attempt_version: str | None = None
    last_attempt_result: str | None = None
    last_attempt_at: str | None = None
    last_attempt_run_id: int | None = None
    last_success_version: str | None = None
    last_success_at: str | None = None
    last_success_run_id: int | None = None


class DiffEntry(TrackerModel):
    origin: str
    version_a: str | None = None
    result_a: str | None = None
    version_b: str | None = None
    result_b: str | None = None


class DiffSideEntry(TrackerModel):
    origin: str
    target: str
    version: str | None = None
    result: str | None = None


class DiffOut(TrackerModel):
    only_a: list[DiffSideEntry]
    only_b: list[DiffSideEntry]
    differ: list[DiffEntry]


class BuildCompareEntry(TrackerModel):
    origin: str
    version_a: str | None = None
    result_a: str | None = None
    version_b: str | None = None
    result_b: str | None = None


class BuildCompareSummary(TrackerModel):
    new_successes: int
    new_failures: int
    still_failing: int
    still_succeeding: int
    added: int
    removed: int
    version_changes: int


class BuildCompareOut(TrackerModel):
    run_a: BuildRunOut
    run_b: BuildRunOut
    summary: BuildCompareSummary
    new_successes: list[BuildCompareEntry]
    new_failures: list[BuildCompareEntry]
    still_failing: list[BuildCompareEntry]
    added: list[BuildCompareEntry]
    removed: list[BuildCompareEntry]
    version_changes: list[BuildCompareEntry]


class QueueItem(TrackerModel):
    origin: str
    version: str


class EnqueueRequest(TrackerModel):
    ports: list[QueueItem]
    total_expected: int | None = None


class EnqueueResponse(TrackerModel):
    queued: int


class UpdatePortStatusRequest(TrackerModel):
    status: Literal["building"]
