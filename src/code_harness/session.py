"""A session binds one project to tools, jobs, history, and local reviews."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from code_harness.history import HistoryManager
from code_harness.paths import PathGuard
from code_harness.review import ReviewManager
from code_harness.shell.background import JobRegistry


def resolve_project_root(project: Path | str | None = None) -> Path:
    """Resolve the project root from the argument, CODE_HARNESS_PROJECT, then cwd."""
    candidate = project or os.environ.get("CODE_HARNESS_PROJECT") or Path.cwd()
    return Path(candidate).expanduser().resolve(strict=False)


@dataclass(frozen=True, slots=True)
class Session:
    guard: PathGuard
    jobs: JobRegistry
    history: HistoryManager
    reviews: ReviewManager

    @classmethod
    def create(cls, project: Path | str | None = None) -> Session:
        jobs = JobRegistry()
        guard = PathGuard(resolve_project_root(project))
        history = HistoryManager(guard.root)
        reviews = ReviewManager(history)
        reviews.ensure_started()
        return cls(guard, jobs, history, reviews)

    @property
    def root(self) -> Path:
        return self.guard.root

    def shutdown(self) -> None:
        self.reviews.shutdown()
        self.jobs.cleanup()
