"""Tests for the internal multi-project registry and startup configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_harness.errors import ExecutionError, InvalidArgumentError
from code_harness.projects import (
    ProjectRegistry,
    load_project_config,
    parse_project_specs,
    resolve_project_config_path,
)
from code_harness.tools.write import write


def _project_roots(tmp_path: Path, *aliases: str) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for alias in aliases:
        root = tmp_path / alias
        root.mkdir()
        roots[alias] = root
    return roots


def _write_config(
    config_path: Path,
    *,
    allowed_root: Path,
    projects: dict[str, Path],
    default: str,
) -> None:
    lines = [
        f"default_project = '{default}'",
        f"allowed_project_roots = ['{allowed_root.as_posix()}']",
        "",
    ]
    for alias, root in projects.items():
        lines.extend((f"[projects.{alias}]", f"path = '{root.as_posix()}'", ""))
    config_path.write_text("\n".join(lines), encoding="utf-8")


def test_registry_with_one_project_resolves_default(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "crmservice")
    registry = ProjectRegistry.create(roots)
    try:
        assert registry.default_project == "crmservice"
        assert registry.list_projects() == ("crmservice",)
        assert registry.resolve() is registry.resolve("crmservice")
        assert registry.resolve().root == roots["crmservice"].resolve(strict=False)
    finally:
        registry.shutdown()


def test_registry_resolves_three_aliases_without_changing_default(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "crmservice", "banco", "ear")
    registry = ProjectRegistry.create(roots, default_project="banco")
    try:
        assert registry.list_projects() == ("crmservice", "banco", "ear")
        assert registry.resolve() is registry.resolve("banco")
        assert registry.resolve("crmservice").root == roots["crmservice"].resolve(strict=False)
        assert registry.resolve("ear").root == roots["ear"].resolve(strict=False)
        assert len({registry.resolve(alias).reviews.port for alias in roots}) == 1
        assert len({registry.resolve(alias).reviews.origin for alias in roots}) == 1
    finally:
        registry.shutdown()


def test_registry_rejects_unknown_alias_without_default_fallback(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "default")
    registry = ProjectRegistry.create(roots)
    try:
        with pytest.raises(InvalidArgumentError, match="Unknown project alias: 'missing'"):
            registry.resolve("missing")
    finally:
        registry.shutdown()


def test_registry_rejects_unregistered_default(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "crmservice")

    with pytest.raises(InvalidArgumentError, match="Default project alias is not registered"):
        ProjectRegistry.create(roots, default_project="missing")


def test_registry_rejects_case_ambiguous_aliases(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    with pytest.raises(InvalidArgumentError, match="aliases are ambiguous"):
        ProjectRegistry.create({"CRM": first, "crm": second}, default_project="CRM")


def test_project_sessions_keep_guards_and_histories_isolated(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "first", "second")
    registry = ProjectRegistry.create(roots)
    try:
        first = registry.resolve("first")
        second = registry.resolve("second")

        assert first is not second
        assert first.guard is not second.guard
        assert first.guard.root != second.guard.root
        assert first.history is not second.history
        assert first.history.workspace_id != second.history.workspace_id
        assert first.history.project_root == roots["first"].resolve(strict=False)
        assert second.history.project_root == roots["second"].resolve(strict=False)
    finally:
        registry.shutdown()


def test_registry_shutdown_closes_every_project_job_registry(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "crm", "banco", "ear")
    registry = ProjectRegistry.create(roots, default_project="crm")
    sessions = [registry.resolve(alias) for alias in registry.list_projects()]
    job_dirs = [session.jobs.directory for session in sessions]
    for session in sessions:
        session.jobs.prepare_output_path().write_text("probe", encoding="utf-8")

    registry.shutdown()

    assert all(not directory.exists() for directory in job_dirs)
    for session in sessions:
        with pytest.raises(ExecutionError, match="already closed"):
            session.jobs.prepare_output_path()


def test_parse_project_specs_preserves_legacy_path() -> None:
    startup = parse_project_specs([r"H:\NBS\39317\FREEDOM\crmservice"])

    assert startup.legacy_project == r"H:\NBS\39317\FREEDOM\crmservice"
    assert not startup.uses_registry
    assert startup.default_project is None


def test_parse_project_specs_uses_single_named_project_as_default() -> None:
    startup = parse_project_specs(["crm=/projects/crm"])

    assert startup.uses_registry
    assert dict(startup.projects) == {"crm": "/projects/crm"}
    assert startup.default_project == "crm"


def test_parse_project_specs_requires_default_for_multiple_named_projects() -> None:
    with pytest.raises(InvalidArgumentError, match="--default-project is required"):
        parse_project_specs(["crm=/projects/crm", "banco=/projects/db"])


def test_parse_project_specs_builds_named_projects_with_explicit_default() -> None:
    startup = parse_project_specs(
        ["crm=/projects/crm", "banco=/projects/db"],
        default_project="banco",
    )

    assert startup.legacy_project is None
    assert dict(startup.projects) == {"crm": "/projects/crm", "banco": "/projects/db"}
    assert startup.default_project == "banco"


def test_parse_project_specs_rejects_mixed_and_ambiguous_aliases() -> None:
    with pytest.raises(InvalidArgumentError, match="Cannot mix legacy"):
        parse_project_specs(["/legacy/project", "crm=/projects/crm"], default_project="crm")

    with pytest.raises(InvalidArgumentError, match="aliases are ambiguous"):
        parse_project_specs(
            ["CRM=/projects/one", "crm=/projects/two"],
            default_project="CRM",
        )


def test_parse_project_specs_rejects_unknown_default() -> None:
    with pytest.raises(InvalidArgumentError, match="Default project alias is not registered"):
        parse_project_specs(["crm=/projects/crm"], default_project="banco")


def test_parse_project_specs_rejects_default_without_named_project() -> None:
    with pytest.raises(InvalidArgumentError, match="only valid with named projects"):
        parse_project_specs(["/legacy/project"], default_project="crm")


def test_project_config_loads_toml_and_environment_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _project_roots(tmp_path, "crm", "banco")
    config_path = tmp_path / "projects.toml"
    _write_config(config_path, allowed_root=tmp_path, projects=roots, default="banco")

    config = load_project_config(config_path)
    monkeypatch.setenv("CODE_HARNESS_PROJECT_CONFIG", str(config_path))

    assert config.default_project == "banco"
    assert dict(config.projects) == {
        "crm": roots["crm"].resolve(strict=False),
        "banco": roots["banco"].resolve(strict=False),
    }
    assert resolve_project_config_path() == config_path.resolve(strict=False)


def test_project_config_rejects_project_outside_allowed_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    config_path = tmp_path / "projects.toml"
    _write_config(
        config_path,
        allowed_root=allowed,
        projects={"outside": outside},
        default="outside",
    )

    with pytest.raises(InvalidArgumentError, match="outside allowed_project_roots"):
        load_project_config(config_path)


def test_config_reload_adds_project_changes_default_and_reuses_session(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "crm", "banco")
    config_path = tmp_path / "projects.toml"
    _write_config(
        config_path,
        allowed_root=tmp_path,
        projects={"crm": roots["crm"]},
        default="crm",
    )
    registry = ProjectRegistry.from_config(config_path)
    try:
        crm_session = registry.resolve("crm")
        _write_config(config_path, allowed_root=tmp_path, projects=roots, default="banco")

        result = registry.reload_from_config()

        assert result["added"] == ["banco"]
        assert result["reused"] == ["crm"]
        assert registry.default_project == "banco"
        assert registry.resolve("crm") is crm_session
        assert registry.resolve("banco").root == roots["banco"].resolve(strict=False)
    finally:
        registry.shutdown()


def test_config_reload_is_transactional_when_new_config_is_invalid(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "crm")
    config_path = tmp_path / "projects.toml"
    _write_config(config_path, allowed_root=tmp_path, projects=roots, default="crm")
    registry = ProjectRegistry.from_config(config_path)
    try:
        original = registry.resolve("crm")
        _write_config(
            config_path,
            allowed_root=tmp_path,
            projects={"crm": roots["crm"], "missing": tmp_path / "missing"},
            default="crm",
        )

        with pytest.raises(InvalidArgumentError, match=r"'missing'.*not an existing directory"):
            registry.reload_from_config()

        assert registry.list_projects() == ("crm",)
        assert registry.default_project == "crm"
        assert registry.resolve("crm") is original
    finally:
        registry.shutdown()


def test_config_reload_rejects_retiring_a_leased_session(tmp_path: Path) -> None:
    roots = _project_roots(tmp_path, "crm", "banco")
    config_path = tmp_path / "projects.toml"
    _write_config(config_path, allowed_root=tmp_path, projects=roots, default="crm")
    registry = ProjectRegistry.from_config(config_path)
    try:
        _write_config(
            config_path,
            allowed_root=tmp_path,
            projects={"banco": roots["banco"]},
            default="banco",
        )
        with registry.lease("crm"), pytest.raises(
            InvalidArgumentError,
            match="request is active",
        ):
            registry.reload_from_config()
        assert registry.list_projects() == ("crm", "banco")
    finally:
        registry.shutdown()


def test_config_reload_rejects_retiring_session_with_running_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _project_roots(tmp_path, "crm", "banco")
    config_path = tmp_path / "projects.toml"
    _write_config(config_path, allowed_root=tmp_path, projects=roots, default="crm")
    registry = ProjectRegistry.from_config(config_path)
    try:
        monkeypatch.setattr(registry.resolve("crm").jobs, "has_running_jobs", lambda: True)
        _write_config(
            config_path,
            allowed_root=tmp_path,
            projects={"banco": roots["banco"]},
            default="banco",
        )
        with pytest.raises(InvalidArgumentError, match="shell job is running"):
            registry.reload_from_config()
        assert "crm" in registry.list_projects()
    finally:
        registry.shutdown()


def test_config_reload_blocks_pending_review_then_removes_reviewed_project(
    tmp_path: Path,
) -> None:
    roots = _project_roots(tmp_path, "crm", "banco")
    config_path = tmp_path / "projects.toml"
    _write_config(config_path, allowed_root=tmp_path, projects=roots, default="crm")
    registry = ProjectRegistry.from_config(config_path)
    try:
        crm = registry.resolve("crm")
        changed = write(
            crm.guard,
            crm.history,
            path="pending.txt",
            contents="pending",
            description="Create pending review for reload test",
            group_title="Reload pending review",
        )
        assert isinstance(changed, dict)
        _write_config(
            config_path,
            allowed_root=tmp_path,
            projects={"banco": roots["banco"]},
            default="banco",
        )

        with pytest.raises(InvalidArgumentError, match="reviews are pending"):
            registry.reload_from_config()

        crm.history.mark_reviewed(str(changed["transaction_id"]))
        result = registry.reload_from_config()
        assert result["removed"] == ["crm"]
        with pytest.raises(ExecutionError, match="already closed"):
            crm.jobs.prepare_output_path()
    finally:
        registry.shutdown()
