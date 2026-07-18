import json
from pathlib import Path

from code_harness.bootstrap.project_registry import register_active_project, resolve_active_project


def test_registry_round_trip(tmp_path: Path, monkeypatch: object) -> None:
    state = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CODE_HARNESS_HOME", str(state))  # type: ignore[attr-defined]

    registered = register_active_project(project)

    assert registered == project.resolve()
    assert resolve_active_project() == project.resolve()
    assert json.loads((state / "active-project.json").read_text())["root"] == str(project)


def test_registry_precedence_and_corrupt_fallback(tmp_path: Path, monkeypatch: object) -> None:
    explicit = tmp_path / "explicit"
    environment = tmp_path / "environment"
    fallback = tmp_path / "fallback"
    explicit.mkdir()
    environment.mkdir()
    fallback.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "active-project.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("CODE_HARNESS_HOME", str(state))  # type: ignore[attr-defined]
    monkeypatch.setenv("CODE_HARNESS_PROJECT", str(environment))  # type: ignore[attr-defined]

    assert resolve_active_project(explicit) == explicit.resolve()
    assert resolve_active_project() == environment.resolve()

    monkeypatch.delenv("CODE_HARNESS_PROJECT")  # type: ignore[attr-defined]
    monkeypatch.chdir(fallback)  # type: ignore[attr-defined]
    assert resolve_active_project() == fallback.resolve()
