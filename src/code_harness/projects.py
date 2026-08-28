"""Internal registry and startup parsing for named project sessions."""

from __future__ import annotations

import os
import threading
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from code_harness.errors import InvalidArgumentError
from code_harness.review import ReviewHub
from code_harness.session import Session

PROJECT_CONFIG_ENV = "CODE_HARNESS_PROJECT_CONFIG"


def _normalize_alias(alias: str) -> str:
    normalized = alias.strip()
    if not normalized:
        raise InvalidArgumentError("Project aliases cannot be blank.")
    return normalized


def _validate_aliases(aliases: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen_casefolded: dict[str, str] = {}
    for raw_alias in aliases:
        alias = _normalize_alias(raw_alias)
        folded = alias.casefold()
        previous = seen_casefolded.get(folded)
        if previous is not None:
            raise InvalidArgumentError(
                f"Project aliases are ambiguous: {previous!r} and {alias!r}."
            )
        seen_casefolded[folded] = alias
        normalized.append(alias)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class ProjectStartupConfig:
    """Normalized startup selection before project sessions are created."""

    legacy_project: str | None
    projects: Mapping[str, str]
    default_project: str | None

    @property
    def uses_registry(self) -> bool:
        return bool(self.projects)


def parse_project_specs(
    project_specs: Sequence[str] | None,
    *,
    default_project: str | None = None,
) -> ProjectStartupConfig:
    """Parse legacy ``--project PATH`` or repeatable ``--project alias=PATH`` values."""

    specs = tuple(project_specs or ())
    normalized_default = _normalize_alias(default_project) if default_project is not None else None

    if not specs:
        if normalized_default is not None:
            raise InvalidArgumentError("--default-project requires at least one named --project.")
        return ProjectStartupConfig(None, MappingProxyType({}), None)

    named_flags = tuple("=" in spec for spec in specs)
    if not any(named_flags):
        if len(specs) != 1:
            raise InvalidArgumentError(
                "Multiple --project values require alias=path syntax for every project."
            )
        if normalized_default is not None:
            raise InvalidArgumentError("--default-project is only valid with named projects.")
        legacy = specs[0].strip()
        if not legacy:
            raise InvalidArgumentError("Project path cannot be blank.")
        return ProjectStartupConfig(legacy, MappingProxyType({}), None)

    if not all(named_flags):
        raise InvalidArgumentError(
            "Cannot mix legacy --project PATH with named --project alias=path values."
        )

    parsed: list[tuple[str, str]] = []
    for spec in specs:
        raw_alias, raw_path = spec.split("=", 1)
        alias = _normalize_alias(raw_alias)
        path = raw_path.strip()
        if not path:
            raise InvalidArgumentError(f"Project path cannot be blank for alias {alias!r}.")
        parsed.append((alias, path))

    aliases = _validate_aliases(tuple(alias for alias, _path in parsed))
    projects = {
        alias: path
        for alias, (_raw_alias, path) in zip(aliases, parsed, strict=True)
    }

    if len(projects) > 1 and normalized_default is None:
        raise InvalidArgumentError(
            "--default-project is required when more than one named project is configured."
        )
    resolved_default = normalized_default or next(iter(projects))
    if resolved_default not in projects:
        raise InvalidArgumentError(
            f"Default project alias is not registered: {resolved_default!r}."
        )

    return ProjectStartupConfig(
        legacy_project=None,
        projects=MappingProxyType(projects),
        default_project=resolved_default,
    )


@dataclass(frozen=True, slots=True)
class ProjectFileConfig:
    """Validated persistent project configuration loaded from TOML."""

    config_path: Path
    projects: Mapping[str, Path]
    default_project: str
    allowed_project_roots: tuple[Path, ...]


def resolve_project_config_path(path: Path | str | None = None) -> Path | None:
    """Resolve an explicit project config or the environment override."""

    candidate = path or os.environ.get(PROJECT_CONFIG_ENV)
    if candidate is None or not str(candidate).strip():
        return None
    return Path(candidate).expanduser().resolve(strict=False)


def load_project_config(path: Path | str) -> ProjectFileConfig:
    """Load and validate a TOML project registry configuration."""

    config_path = Path(path).expanduser().resolve(strict=False)
    try:
        with config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError as error:
        raise InvalidArgumentError("Project config file does not exist.") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise InvalidArgumentError("Project config file could not be read as TOML.") from error

    projects_value = raw.get("projects")
    if not isinstance(projects_value, dict) or not projects_value:
        raise InvalidArgumentError("Project config must define a non-empty [projects] table.")

    raw_aliases = tuple(str(alias) for alias in projects_value)
    aliases = _validate_aliases(raw_aliases)
    base = config_path.parent

    roots_value = raw.get("allowed_project_roots")
    if not isinstance(roots_value, list) or not roots_value:
        raise InvalidArgumentError(
            "Project config must define at least one allowed_project_roots entry."
        )
    allowed_roots: list[Path] = []
    for index, value in enumerate(roots_value):
        if not isinstance(value, str) or not value.strip():
            raise InvalidArgumentError(
                f"allowed_project_roots[{index}] must be a non-empty path string."
            )
        root = _resolve_config_path(value, base=base)
        if not root.is_dir():
            raise InvalidArgumentError(
                f"allowed_project_roots[{index}] is not an existing directory."
            )
        allowed_roots.append(root)

    projects: dict[str, Path] = {}
    seen_roots: dict[Path, str] = {}
    for raw_alias, alias in zip(raw_aliases, aliases, strict=True):
        project_value: Any = projects_value[raw_alias]
        if isinstance(project_value, str):
            raw_path = project_value
        elif isinstance(project_value, dict) and isinstance(project_value.get("path"), str):
            raw_path = project_value["path"]
        else:
            raise InvalidArgumentError(
                f"Configured project {alias!r} must define a non-empty path string."
            )
        if not raw_path.strip():
            raise InvalidArgumentError(
                f"Configured project {alias!r} must define a non-empty path string."
            )
        project_root = _resolve_config_path(raw_path, base=base)
        if not project_root.is_dir():
            raise InvalidArgumentError(
                f"Configured project {alias!r} is not an existing directory."
            )
        if not any(_is_within(project_root, allowed) for allowed in allowed_roots):
            raise InvalidArgumentError(
                f"Configured project {alias!r} is outside allowed_project_roots."
            )
        previous = seen_roots.get(project_root)
        if previous is not None:
            raise InvalidArgumentError(
                f"Configured projects {previous!r} and {alias!r} resolve to the same root."
            )
        seen_roots[project_root] = alias
        projects[alias] = project_root

    default_value = raw.get("default_project")
    if default_value is None and len(projects) == 1:
        default_project = next(iter(projects))
    elif isinstance(default_value, str):
        default_project = _normalize_alias(default_value)
    else:
        raise InvalidArgumentError(
            "Project config must define default_project when multiple projects are configured."
        )
    if default_project not in projects:
        raise InvalidArgumentError(
            f"Default project alias is not registered: {default_project!r}."
        )

    return ProjectFileConfig(
        config_path=config_path,
        projects=MappingProxyType(projects),
        default_project=default_project,
        allowed_project_roots=tuple(allowed_roots),
    )


def _resolve_config_path(value: str, *, base: Path) -> Path:
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else base / supplied
    return candidate.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class ProjectRegistry:
    """Resolve aliases to isolated sessions and reload a config-backed registry safely."""

    def __init__(
        self,
        projects: Mapping[str, Session],
        *,
        default_project: str,
        review_hub: ReviewHub | None = None,
        config_path: Path | None = None,
        allowed_project_roots: tuple[Path, ...] = (),
    ) -> None:
        if not projects:
            raise InvalidArgumentError("At least one project must be registered.")

        raw_aliases = tuple(projects)
        aliases = _validate_aliases(raw_aliases)
        registered = {
            alias: projects[raw_alias]
            for raw_alias, alias in zip(raw_aliases, aliases, strict=True)
        }
        normalized_default = _normalize_alias(default_project)
        if normalized_default not in registered:
            raise InvalidArgumentError(
                f"Default project alias is not registered: {normalized_default!r}."
            )

        self._projects: Mapping[str, Session] = MappingProxyType(registered)
        self._default_project = normalized_default
        self._review_hub = review_hub
        self._config_path = config_path
        self._allowed_project_roots = allowed_project_roots
        self._lock = threading.RLock()
        self._active_leases: dict[int, int] = {}

    @classmethod
    def create(
        cls,
        projects: Mapping[str, Path | str],
        *,
        default_project: str | None = None,
    ) -> ProjectRegistry:
        """Create one isolated Session per alias and return their registry."""

        if not projects:
            raise InvalidArgumentError("At least one project must be registered.")
        raw_aliases = tuple(projects)
        aliases = _validate_aliases(raw_aliases)
        normalized_projects = {
            alias: projects[raw_alias]
            for raw_alias, alias in zip(raw_aliases, aliases, strict=True)
        }
        resolved_default = (
            next(iter(normalized_projects))
            if default_project is None
            else _normalize_alias(default_project)
        )
        if resolved_default not in normalized_projects:
            raise InvalidArgumentError(
                f"Default project alias is not registered: {resolved_default!r}."
            )
        return cls._create_sessions(normalized_projects, default_project=resolved_default)

    @classmethod
    def from_config(cls, path: Path | str) -> ProjectRegistry:
        """Create a reloadable registry from a validated persistent TOML config."""

        config = load_project_config(path)
        return cls._create_sessions(
            config.projects,
            default_project=config.default_project,
            config_path=config.config_path,
            allowed_project_roots=config.allowed_project_roots,
        )

    @classmethod
    def _create_sessions(
        cls,
        projects: Mapping[str, Path | str],
        *,
        default_project: str,
        config_path: Path | None = None,
        allowed_project_roots: tuple[Path, ...] = (),
    ) -> ProjectRegistry:
        review_hub = ReviewHub()
        sessions: dict[str, Session] = {}
        try:
            for alias, root in projects.items():
                sessions[alias] = Session.create(root, review_hub=review_hub)
            return cls(
                sessions,
                default_project=default_project,
                review_hub=review_hub,
                config_path=config_path,
                allowed_project_roots=allowed_project_roots,
            )
        except BaseException:
            for created in sessions.values():
                created.shutdown()
            review_hub.shutdown()
            raise

    @property
    def default_project(self) -> str:
        with self._lock:
            return self._default_project

    @property
    def projects(self) -> Mapping[str, Session]:
        """Return an immutable snapshot of the currently registered aliases."""

        with self._lock:
            return self._projects

    @property
    def config_backed(self) -> bool:
        return self._config_path is not None

    def resolve(self, project: str | None = None) -> Session:
        """Resolve an explicit alias or the configured default project."""

        with self._lock:
            _alias, target = self._resolve_locked(project)
            return target

    @contextmanager
    def lease(self, project: str | None = None) -> Iterator[tuple[str, Session]]:
        """Keep a selected session alive for the duration of one MCP operation."""

        with self._lock:
            alias, target = self._resolve_locked(project)
            identity = id(target)
            self._active_leases[identity] = self._active_leases.get(identity, 0) + 1
        try:
            yield alias, target
        finally:
            with self._lock:
                remaining = self._active_leases[identity] - 1
                if remaining:
                    self._active_leases[identity] = remaining
                else:
                    del self._active_leases[identity]

    def list_projects(self) -> tuple[str, ...]:
        """List registered aliases in registration order."""

        with self._lock:
            return tuple(self._projects)

    def project_listing(self) -> tuple[str, tuple[str, ...]]:
        """Return default and aliases from one consistent registry snapshot."""

        with self._lock:
            return self._default_project, tuple(self._projects)

    def reload_from_config(self) -> dict[str, object]:
        """Atomically replace config-backed aliases while preserving reusable sessions."""

        config_path = self._config_path
        if config_path is None:
            raise InvalidArgumentError(
                "ReloadProjects requires a server started with --project-config."
            )
        created: dict[str, Session] = {}
        retired: list[Session] = []
        with self._lock:
            config = load_project_config(config_path)
            current = self._projects
            reused = [
                alias
                for alias, root in config.projects.items()
                if alias in current and current[alias].root == root
            ]
            added = [alias for alias in config.projects if alias not in current]
            replaced = [
                alias
                for alias, root in config.projects.items()
                if alias in current and current[alias].root != root
            ]
            removed = [alias for alias in current if alias not in config.projects]

            for alias in (*replaced, *removed):
                self._assert_retirable(alias, current[alias])

            if self._review_hub is None:
                raise InvalidArgumentError("ReloadProjects requires a managed project registry.")
            try:
                for alias in (*added, *replaced):
                    created[alias] = Session.create(
                        config.projects[alias],
                        review_hub=self._review_hub,
                    )
            except BaseException:
                for candidate in created.values():
                    candidate.shutdown()
                raise

            updated: dict[str, Session] = {}
            for alias in config.projects:
                updated[alias] = current[alias] if alias in reused else created[alias]
            retired = [current[alias] for alias in (*replaced, *removed)]
            self._projects = MappingProxyType(updated)
            self._default_project = config.default_project
            self._allowed_project_roots = config.allowed_project_roots

        for old_session in retired:
            old_session.shutdown()

        return {
            "status": "reloaded",
            "default": config.default_project,
            "projects": list(config.projects),
            "added": added,
            "removed": removed,
            "replaced": replaced,
            "reused": reused,
        }

    def shutdown(self) -> None:
        """Release resources owned by every registered project session."""

        with self._lock:
            sessions = tuple(self._projects.values())
            review_hub = self._review_hub
            self._projects = MappingProxyType({})
        seen: set[int] = set()
        for project_session in sessions:
            identity = id(project_session)
            if identity in seen:
                continue
            seen.add(identity)
            project_session.shutdown()
        if review_hub is not None:
            review_hub.shutdown()

    def _resolve_locked(self, project: str | None) -> tuple[str, Session]:
        alias = self._default_project if project is None else _normalize_alias(project)
        try:
            return alias, self._projects[alias]
        except KeyError:
            raise InvalidArgumentError(f"Unknown project alias: {alias!r}.") from None

    def _assert_retirable(self, alias: str, session: Session) -> None:
        if self._active_leases.get(id(session), 0):
            raise InvalidArgumentError(
                f"Project {alias!r} cannot be removed or repointed while a request is active."
            )
        if session.jobs.has_running_jobs():
            raise InvalidArgumentError(
                f"Project {alias!r} cannot be removed or repointed while a shell job is running."
            )
        has_pending_review = any(
            item.status == "applied" and item.review_state != "reviewed"
            for item in session.history.list_transactions()
        )
        if has_pending_review:
            raise InvalidArgumentError(
                f"Project {alias!r} cannot be removed or repointed while reviews are pending."
            )
