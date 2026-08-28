from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from code_harness import tools as tools_module
from code_harness.cli import app
from code_harness.errors import PathOutsideProjectError
from code_harness.mcp.server import create_server
from code_harness.projects import ProjectRegistry
from code_harness.session import Session, resolve_project_root
from tests.conftest import requires_git, requires_ripgrep

runner = CliRunner()

EXPECTED_TOOLS = {
    "ServerInfo",
    "ListProjects",
    "ProjectInfo",
    "ReloadProjects",
    "Shell",
    "GetJobStatus",
    "Grep",
    "Glob",
    "Read",
    "Write",
    "StrReplace",
    "ApplyPatch",
    "OpenPatchReview",
    "RollbackPatch",
    "Delete",
}


def _tool_json(result: object) -> dict[str, Any]:
    content = result[0] if isinstance(result, tuple) else result
    assert isinstance(content, list)
    assert content
    text = getattr(content[0], "text", None)
    assert isinstance(text, str)
    value = json.loads(text)
    assert isinstance(value, dict)
    return value


def _write_registry_config(
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


def test_server_exposes_shell_status_and_file_tools(session: Session) -> None:
    server = create_server(session=session)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == EXPECTED_TOOLS


def test_list_projects_and_project_info_expose_aliases_without_roots(tmp_path: Path) -> None:
    crm = tmp_path / "crm-secret-root"
    banco = tmp_path / "banco-secret-root"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="banco")
    try:
        server = create_server(registry=registry)
        listing = _tool_json(asyncio.run(server.call_tool("ListProjects", {})))
        default_info = _tool_json(asyncio.run(server.call_tool("ProjectInfo", {})))
        crm_info = _tool_json(
            asyncio.run(server.call_tool("ProjectInfo", {"project": "crm"}))
        )

        assert listing == {
            "mode": "named",
            "default": "banco",
            "projects": [
                {"name": "crm", "default": False},
                {"name": "banco", "default": True},
            ],
        }
        assert default_info == {"name": "banco", "default": True, "mode": "named"}
        assert crm_info == {"name": "crm", "default": False, "mode": "named"}
        rendered = json.dumps(listing) + json.dumps(default_info) + json.dumps(crm_info)
        assert str(crm.resolve(strict=False)) not in rendered
        assert str(banco.resolve(strict=False)) not in rendered
    finally:
        registry.shutdown()


def test_project_discovery_preserves_legacy_single_project_mode(session: Session) -> None:
    server = create_server(session=session)

    listing = _tool_json(asyncio.run(server.call_tool("ListProjects", {})))
    info = _tool_json(asyncio.run(server.call_tool("ProjectInfo", {})))
    explicit = _tool_json(
        asyncio.run(server.call_tool("ProjectInfo", {"project": "default"}))
    )

    assert listing == {
        "mode": "legacy",
        "default": "default",
        "projects": [{"name": "default", "default": True}],
    }
    assert info == {"name": "default", "default": True, "mode": "legacy"}
    assert explicit["code"] == "invalid_argument"
    assert "legacy single project" in str(explicit["error"])
    assert str(session.root) not in json.dumps(listing) + json.dumps(info)


def test_project_info_rejects_unknown_alias_without_root_metadata(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    crm.mkdir()
    registry = ProjectRegistry.create({"crm": crm})
    try:
        server = create_server(registry=registry)
        result = _tool_json(
            asyncio.run(server.call_tool("ProjectInfo", {"project": "missing"}))
        )
        assert result["code"] == "invalid_argument"
        assert "Unknown project alias: 'missing'" in str(result["error"])
        assert str(crm.resolve(strict=False)) not in json.dumps(result)
    finally:
        registry.shutdown()


def test_reload_projects_adds_alias_without_restarting_server(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    (crm / "which.txt").write_text("crm", encoding="utf-8")
    (banco / "which.txt").write_text("banco", encoding="utf-8")
    config_path = tmp_path / "projects.toml"
    _write_registry_config(
        config_path,
        allowed_root=tmp_path,
        projects={"crm": crm},
        default="crm",
    )
    registry = ProjectRegistry.from_config(config_path)
    try:
        server = create_server(registry=registry)
        _write_registry_config(
            config_path,
            allowed_root=tmp_path,
            projects={"crm": crm, "banco": banco},
            default="banco",
        )

        reloaded = _tool_json(asyncio.run(server.call_tool("ReloadProjects", {})))
        listing = _tool_json(asyncio.run(server.call_tool("ListProjects", {})))
        banco_read = asyncio.run(
            server.call_tool("Read", {"path": "which.txt", "project": "banco"})
        )

        assert reloaded["status"] == "reloaded"
        assert reloaded["added"] == ["banco"]
        assert reloaded["default"] == "banco"
        assert listing["default"] == "banco"
        assert [item["name"] for item in listing["projects"]] == ["crm", "banco"]
        assert "banco" in str(banco_read)
        assert str(config_path.resolve(strict=False)) not in json.dumps(reloaded)
    finally:
        registry.shutdown()


def test_reload_projects_rejects_non_config_registry_without_path_input(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    crm.mkdir()
    registry = ProjectRegistry.create({"crm": crm})
    try:
        server = create_server(registry=registry)
        result = _tool_json(asyncio.run(server.call_tool("ReloadProjects", {})))
        assert result["code"] == "invalid_argument"
        assert "--project-config" in str(result["error"])
    finally:
        registry.shutdown()


def test_server_with_registry_uses_configured_default_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="banco")
    seen_roots: list[Path] = []

    def fake_read(guard: Any, **_kwargs: Any) -> str:
        seen_roots.append(guard.root)
        return "ok"

    monkeypatch.setattr(tools_module, "read", fake_read)
    try:
        server = create_server(registry=registry)
        asyncio.run(server.call_tool("Read", {"path": "anything.txt"}))
        assert seen_roots == [banco.resolve(strict=False)]
    finally:
        registry.shutdown()


def test_project_aware_reads_route_concurrently_to_distinct_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    seen_roots: list[Path] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_read(guard: Any, **_kwargs: Any) -> str:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            seen_roots.append(guard.root)
        time.sleep(0.1)
        with lock:
            active -= 1
        return "ok"

    monkeypatch.setattr(tools_module, "read", fake_read)
    try:
        server = create_server(registry=registry)

        async def exercise() -> None:
            await asyncio.gather(
                server.call_tool("Read", {"path": "same.txt", "project": "crm"}),
                server.call_tool("Read", {"path": "same.txt", "project": "banco"}),
            )

        asyncio.run(exercise())
        assert set(seen_roots) == {crm.resolve(strict=False), banco.resolve(strict=False)}
        assert max_active >= 2
    finally:
        registry.shutdown()


def test_same_relative_path_is_resolved_inside_the_selected_project(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    (crm / "same.txt").write_text("crm-value", encoding="utf-8")
    (banco / "same.txt").write_text("banco-value", encoding="utf-8")
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    try:
        server = create_server(registry=registry)
        crm_result = asyncio.run(
            server.call_tool("Read", {"path": "same.txt", "project": "crm"})
        )
        banco_result = asyncio.run(
            server.call_tool("Read", {"path": "same.txt", "project": "banco"})
        )
        assert "crm-value" in str(crm_result)
        assert "banco-value" not in str(crm_result)
        assert "banco-value" in str(banco_result)
        assert "crm-value" not in str(banco_result)
    finally:
        registry.shutdown()


def test_project_aware_read_still_blocks_cross_project_traversal(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    (crm / "secret.txt").write_text("crm-secret", encoding="utf-8")
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    try:
        server = create_server(registry=registry)
        result = asyncio.run(
            server.call_tool(
                "Read",
                {"path": "../crm/secret.txt", "project": "banco"},
            )
        )
        rendered = str(result)
        assert "path_outside_project" in rendered
        assert "crm-secret" not in rendered
        assert str(crm.resolve(strict=False)) not in rendered
        assert str(banco.resolve(strict=False)) not in rendered
    finally:
        registry.shutdown()


def test_project_aware_write_mutates_only_selected_project(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    try:
        server = create_server(registry=registry)
        result = asyncio.run(
            server.call_tool(
                "Write",
                {
                    "path": "selected.txt",
                    "contents": "banco",
                    "description": "Test project-aware write",
                    "group_title": "Phase 3 routing",
                    "project": "banco",
                },
            )
        )
        assert _tool_json(result)["project"] == "banco"
        assert not (crm / "selected.txt").exists()
        assert (banco / "selected.txt").read_text(encoding="utf-8") == "banco"
    finally:
        registry.shutdown()


def test_project_metadata_is_returned_for_str_replace_and_delete(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    target = banco / "selected.txt"
    target.write_text("old\n", encoding="utf-8")
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    try:
        server = create_server(registry=registry)
        replaced = _tool_json(
            asyncio.run(
                server.call_tool(
                    "StrReplace",
                    {
                        "path": "selected.txt",
                        "old_string": "old",
                        "new_string": "new",
                        "description": "Replace in banco",
                        "group_title": "Phase 7 metadata",
                        "project": "banco",
                    },
                )
            )
        )
        deleted = _tool_json(
            asyncio.run(
                server.call_tool(
                    "Delete",
                    {
                        "path": "selected.txt",
                        "description": "Delete in banco",
                        "group_id": replaced["group_id"],
                        "project": "banco",
                    },
                )
            )
        )

        assert replaced["project"] == "banco"
        assert deleted["project"] == "banco"
        assert not target.exists()
        assert not (crm / "selected.txt").exists()
    finally:
        registry.shutdown()


def test_project_aware_tool_rejects_unknown_alias_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    crm.mkdir()
    registry = ProjectRegistry.create({"crm": crm})
    called = False

    def fake_read(_guard: Any, **_kwargs: Any) -> str:
        nonlocal called
        called = True
        return "unexpected"

    monkeypatch.setattr(tools_module, "read", fake_read)
    try:
        server = create_server(registry=registry)
        result = asyncio.run(
            server.call_tool("Read", {"path": "anything.txt", "project": "missing"})
        )
        assert called is False
        assert "Unknown project alias: 'missing'" in str(result)
    finally:
        registry.shutdown()


def test_shell_returns_selected_project_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    seen_roots: list[Path] = []

    def fake_shell(guard: Any, _jobs: Any, **_kwargs: Any) -> dict[str, Any]:
        seen_roots.append(guard.root)
        return {"status": "completed", "exit_code": 0}

    monkeypatch.setattr(tools_module, "shell", fake_shell)
    try:
        server = create_server(registry=registry)
        result = _tool_json(
            asyncio.run(
                server.call_tool(
                    "Shell",
                    {"command": "echo test", "project": "banco"},
                )
            )
        )
        assert result["project"] == "banco"
        assert seen_roots == [banco.resolve(strict=False)]
    finally:
        registry.shutdown()


def test_project_aware_shell_calls_overlap_for_distinct_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    seen_roots: list[Path] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_shell(guard: Any, _jobs: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            seen_roots.append(guard.root)
        time.sleep(0.1)
        with lock:
            active -= 1
        return {"status": "completed", "exit_code": 0}

    monkeypatch.setattr(tools_module, "shell", fake_shell)
    try:
        server = create_server(registry=registry)

        async def exercise() -> None:
            await asyncio.gather(
                server.call_tool("Shell", {"command": "crm", "project": "crm"}),
                server.call_tool("Shell", {"command": "banco", "project": "banco"}),
            )

        asyncio.run(exercise())
        assert set(seen_roots) == {crm.resolve(strict=False), banco.resolve(strict=False)}
        assert max_active >= 2
    finally:
        registry.shutdown()


def test_get_job_status_routes_to_selected_project_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    seen_directories: list[Path] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_get_job_status(job_registry: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            seen_directories.append(job_registry.directory)
        time.sleep(0.1)
        with lock:
            active -= 1
        return {"job_id": kwargs["job_id"], "status": "unknown"}

    monkeypatch.setattr(tools_module, "get_job_status", fake_get_job_status)
    try:
        server = create_server(registry=registry)

        async def exercise() -> list[Any]:
            return await asyncio.gather(
                server.call_tool("GetJobStatus", {"job_id": "job-crm"}),
                server.call_tool(
                    "GetJobStatus",
                    {"job_id": "job-banco", "project": "banco"},
                ),
            )

        crm_result, banco_result = asyncio.run(exercise())
        assert _tool_json(crm_result)["project"] == "crm"
        assert _tool_json(banco_result)["project"] == "banco"
        assert set(seen_directories) == {
            registry.resolve("crm").jobs.directory,
            registry.resolve("banco").jobs.directory,
        }
        assert max_active >= 2
    finally:
        registry.shutdown()


def test_get_job_status_wrong_project_does_not_search_other_registries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    seen_directories: list[Path] = []

    def fake_get_job_status(job_registry: Any, **kwargs: Any) -> dict[str, Any]:
        seen_directories.append(job_registry.directory)
        return {"job_id": kwargs["job_id"], "status": "unknown"}

    monkeypatch.setattr(tools_module, "get_job_status", fake_get_job_status)
    try:
        server = create_server(registry=registry)
        asyncio.run(
            server.call_tool(
                "GetJobStatus",
                {"job_id": "job-owned-by-banco", "project": "crm"},
            )
        )
        assert seen_directories == [registry.resolve("crm").jobs.directory]
    finally:
        registry.shutdown()


def test_open_patch_review_routes_to_selected_project_on_shared_hub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    seen_roots: list[Path] = []

    def fake_open(manager: Any, identifier: str = "latest", **_kwargs: Any) -> dict[str, Any]:
        seen_roots.append(manager.service.history.project_root)
        return {"url": f"{manager.origin}/?group={identifier}", "origin": manager.origin}

    manager_type = type(registry.resolve("crm").reviews)
    monkeypatch.setattr(manager_type, "open", fake_open)
    try:
        server = create_server(registry=registry)
        result = asyncio.run(
            server.call_tool(
                "OpenPatchReview",
                {"transaction_id": "tx-banco", "project": "banco", "open_browser": False},
            )
        )
        assert seen_roots == [banco.resolve(strict=False)]
        assert _tool_json(result)["project"] == "banco"
        assert registry.resolve("crm").reviews.origin == registry.resolve("banco").reviews.origin
        assert "tx-banco" in str(result)
    finally:
        registry.shutdown()


def test_rollback_patch_routes_to_selected_project_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    seen_roots: list[Path] = []

    def fake_rollback_patch(history: Any, **kwargs: Any) -> dict[str, Any]:
        seen_roots.append(history.project_root)
        return {"transaction_id": kwargs["transaction_id"], "status": "rolled_back"}

    monkeypatch.setattr(tools_module, "rollback_patch", fake_rollback_patch)
    try:
        server = create_server(registry=registry)
        result = asyncio.run(
            server.call_tool(
                "RollbackPatch",
                {"transaction_id": "tx-banco", "project": "banco"},
            )
        )
        assert seen_roots == [banco.resolve(strict=False)]
        assert _tool_json(result)["project"] == "banco"
    finally:
        registry.shutdown()


@requires_git
def test_apply_patch_runs_concurrently_with_isolated_project_histories(tmp_path: Path) -> None:
    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    for root in (crm, banco):
        (root / "src").mkdir(parents=True)
        (root / "src" / "hello.py").write_text("VALUE = 'old'\n", encoding="utf-8")
    registry = ProjectRegistry.create({"crm": crm, "banco": banco}, default_project="crm")
    patch = """diff --git a/src/hello.py b/src/hello.py
--- a/src/hello.py
+++ b/src/hello.py
@@ -1 +1 @@
-VALUE = 'old'
+VALUE = 'new'
"""
    try:
        server = create_server(registry=registry)

        async def exercise() -> list[Any]:
            return await asyncio.gather(
                server.call_tool(
                    "ApplyPatch",
                    {
                        "patch": patch,
                        "description": "Patch crm",
                        "group_title": "Phase 5 crm",
                        "project": "crm",
                    },
                ),
                server.call_tool(
                    "ApplyPatch",
                    {
                        "patch": patch,
                        "description": "Patch banco",
                        "group_title": "Phase 5 banco",
                        "project": "banco",
                    },
                ),
            )

        crm_result, banco_result = asyncio.run(exercise())
        assert _tool_json(crm_result)["project"] == "crm"
        assert _tool_json(banco_result)["project"] == "banco"
        assert (crm / "src" / "hello.py").read_text(encoding="utf-8") == "VALUE = 'new'\n"
        assert (banco / "src" / "hello.py").read_text(encoding="utf-8") == "VALUE = 'new'\n"
        assert len(registry.resolve("crm").history.list_transactions(status="applied")) == 1
        assert len(registry.resolve("banco").history.list_transactions(status="applied")) == 1
        assert (
            registry.resolve("crm").history.workspace_id
            != registry.resolve("banco").history.workspace_id
        )
    finally:
        registry.shutdown()


@requires_ripgrep
def test_mcp_grep_calls_overlap(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read tools run in worker threads so concurrent MCP Grep calls overlap."""
    active = 0
    max_active = 0
    lock = threading.Lock()
    original = tools_module.grep

    def slow_grep(*args: object, **kwargs: object) -> str:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.2)
        try:
            return original(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(tools_module, "grep", slow_grep)
    server = create_server(session=session)

    async def exercise() -> None:
        await asyncio.gather(
            server.call_tool("Grep", {"pattern": "hello"}),
            server.call_tool("Grep", {"pattern": "world"}),
        )

    asyncio.run(exercise())
    assert max_active >= 2


def test_tool_schemas_match_the_cursor_contract(session: Session) -> None:
    server = create_server(session=session)
    schemas = {tool.name: tool.inputSchema for tool in asyncio.run(server.list_tools())}

    assert schemas["ListProjects"].get("properties", {}) == {}
    assert "project" in schemas["ProjectInfo"]["properties"]
    assert "required" not in schemas["ProjectInfo"] or not schemas["ProjectInfo"]["required"]
    assert schemas["ReloadProjects"].get("properties", {}) == {}
    assert schemas["Shell"]["required"] == ["command"]
    assert set(schemas["Shell"]["properties"]) == {
        "command",
        "working_directory",
        "block_until_ms",
        "description",
        "shell",
        "project",
    }
    assert schemas["GetJobStatus"]["required"] == ["job_id"]
    assert set(schemas["GetJobStatus"]["properties"]) == {
        "job_id",
        "wait_ms",
        "tail_lines",
        "project",
    }
    assert "required" not in schemas["Grep"] or "pattern" not in schemas["Grep"]["required"]
    assert "type" in schemas["Grep"]["properties"]
    assert "exclude" in schemas["Grep"]["properties"]
    assert "reference_kind" in schemas["Grep"]["properties"]
    assert "exclude_reference_kind" in schemas["Grep"]["properties"]
    assert "project" in schemas["Grep"]["properties"]
    assert schemas["Grep"]["properties"]["output_mode"]["enum"] == [
        "content",
        "files_with_matches",
        "count",
        "symbols",
        "references",
    ]
    assert schemas["Glob"]["required"] == ["glob_pattern"]
    assert {"exclude", "project"} <= set(schemas["Glob"]["properties"])
    assert schemas["Read"]["required"] == ["path"]
    assert "project" in schemas["Read"]["properties"]
    assert set(schemas["Write"]["required"]) == {"path", "contents"}
    assert {"description", "group_id", "group_title", "project"} <= set(
        schemas["Write"]["properties"]
    )
    assert set(schemas["StrReplace"]["required"]) == {"path", "old_string", "new_string"}
    assert set(schemas["StrReplace"]["properties"]) == {
        "path",
        "old_string",
        "new_string",
        "replace_all",
        "ignore_line_endings",
        "expected_occurrences",
        "expected_sha256",
        "dry_run",
        "description",
        "group_id",
        "group_title",
        "project",
    }
    assert schemas["ApplyPatch"]["required"] == ["patch"]
    assert set(schemas["ApplyPatch"]["properties"]) == {
        "patch",
        "description",
        "group_id",
        "group_title",
        "dry_run",
        "expected_hashes",
        "project",
    }
    assert schemas["OpenPatchReview"]["properties"]["transaction_id"]["default"] == "latest"
    assert schemas["OpenPatchReview"]["properties"]["open_browser"]["default"] is True
    assert "project" in schemas["OpenPatchReview"]["properties"]
    assert schemas["RollbackPatch"]["required"] == ["transaction_id"]
    assert set(schemas["RollbackPatch"]["properties"]) == {
        "transaction_id",
        "force",
        "project",
    }
    assert schemas["Delete"]["required"] == ["path"]
    assert "project" in schemas["Delete"]["properties"]


def test_session_resolves_root_from_environment(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_PROJECT", str(project))
    assert resolve_project_root() == project.resolve()


def test_session_does_not_expose_background_output_to_read(project: Path) -> None:
    session = Session.create(project)
    try:
        target = session.jobs.directory / "probe.txt"
        target.write_text("visible", encoding="utf-8")
        with pytest.raises(PathOutsideProjectError, match="outside the project root"):
            session.guard.resolve(str(target), kind="file")
    finally:
        session.shutdown()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0


def test_cli_mcp_serve_is_registered() -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "serve" in result.stdout


def test_cli_serve_routes_named_projects_and_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_harness.mcp import server as server_module

    crm = tmp_path / "crm"
    banco = tmp_path / "banco"
    crm.mkdir()
    banco.mkdir()
    captured: dict[str, object] = {}

    def fake_run_server(project: object = None, **kwargs: object) -> None:
        captured["project"] = project
        captured.update(kwargs)

    monkeypatch.setattr(server_module, "run_server", fake_run_server)
    result = runner.invoke(
        app,
        [
            "serve",
            "--project",
            f"crm={crm}",
            "--project",
            f"banco={banco}",
            "--default-project",
            "banco",
        ],
    )

    assert result.exit_code == 0
    assert captured["project"] is None
    assert dict(captured["projects"]) == {"crm": str(crm), "banco": str(banco)}  # type: ignore[arg-type]
    assert captured["default_project"] == "banco"


def test_cli_serve_forwards_persistent_project_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_harness.mcp import server as server_module

    config_path = tmp_path / "projects.toml"
    config_path.write_text("# parsed by run_server\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_server(project: object = None, **kwargs: object) -> None:
        captured["project"] = project
        captured.update(kwargs)

    monkeypatch.setattr(server_module, "run_server", fake_run_server)
    result = runner.invoke(app, ["serve", "--project-config", str(config_path)])

    assert result.exit_code == 0
    assert captured["project"] is None
    assert captured["projects"] is None
    assert captured["default_project"] is None
    assert captured["project_config"] == config_path


def test_run_server_uses_project_config_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_harness.mcp import server as server_module

    crm = tmp_path / "crm"
    crm.mkdir()
    config_path = tmp_path / "projects.toml"
    _write_registry_config(
        config_path,
        allowed_root=tmp_path,
        projects={"crm": crm},
        default="crm",
    )
    monkeypatch.setenv("CODE_HARNESS_PROJECT_CONFIG", str(config_path))
    captured: dict[str, object] = {}

    class FakeServer:
        def run(self, *, transport: str) -> None:
            captured["transport"] = transport

    def fake_create_server(
        project: object = None,
        *,
        registry: ProjectRegistry | None = None,
        **_kwargs: object,
    ) -> FakeServer:
        captured["project"] = project
        captured["registry"] = registry
        return FakeServer()

    monkeypatch.setattr(server_module, "create_server", fake_create_server)
    server_module.run_server(transport="stdio")
    registry = captured["registry"]
    assert isinstance(registry, ProjectRegistry)
    try:
        assert registry.config_backed is True
        assert registry.list_projects() == ("crm",)
        assert captured["transport"] == "stdio"
    finally:
        registry.shutdown()


def test_run_server_rejects_project_config_mixed_with_project(tmp_path: Path) -> None:
    from code_harness.mcp.server import run_server

    with pytest.raises(ValueError, match="--project-config cannot be combined"):
        run_server(
            str(tmp_path),
            project_config=tmp_path / "projects.toml",
            transport="stdio",
        )


def test_cli_serve_preserves_legacy_project_argument(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_harness.mcp import server as server_module

    captured: dict[str, object] = {}

    def fake_run_server(project_arg: object = None, **kwargs: object) -> None:
        captured["project"] = project_arg
        captured.update(kwargs)

    monkeypatch.setattr(server_module, "run_server", fake_run_server)
    result = runner.invoke(app, ["serve", "--project", str(project)])

    assert result.exit_code == 0
    assert captured["project"] == str(project)
    assert captured["projects"] is None
    assert captured["default_project"] is None


def test_cli_serve_requires_default_for_multiple_named_projects(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    result = runner.invoke(
        app,
        ["serve", "--project", f"first={first}", "--project", f"second={second}"],
    )

    assert result.exit_code == 1
    assert "--default-project is required" in result.stderr


def test_cli_read(project: Path) -> None:
    result = runner.invoke(app, ["read", "src/hello.py", "--project", str(project)])
    assert result.exit_code == 0
    assert "1|def hello():" in result.stdout


def test_cli_symbols_outline_omits_pattern(project: Path) -> None:
    result = runner.invoke(
        app,
        ["grep", "--path", "src/hello.py", "--output-mode", "symbols", "--project", str(project)],
    )
    assert result.exit_code == 0
    assert "function hello" in result.stdout


def test_cli_reports_errors(project: Path) -> None:
    result = runner.invoke(app, ["read", "missing.txt", "--project", str(project)])
    assert result.exit_code == 1


def test_cli_write_and_delete(project: Path) -> None:
    written = runner.invoke(
        app,
        [
            "write",
            "tmp.txt",
            "body",
            "--description",
            "Cria temporário",
            "--group-title",
            "Teste CLI",
            "--project",
            str(project),
        ],
    )
    assert written.exit_code == 0
    assert (project / "tmp.txt").exists()

    group_id = json.loads(written.stdout)["group_id"]
    removed = runner.invoke(
        app,
        [
            "delete",
            "tmp.txt",
            "--description",
            "Remove temporário",
            "--group-id",
            group_id,
            "--project",
            str(project),
        ],
    )
    assert removed.exit_code == 0
    assert not (project / "tmp.txt").exists()


def test_cli_str_replace(project: Path) -> None:
    result = runner.invoke(
        app,
        [
            "str-replace",
            "src/hello.py",
            "world",
            "terra",
            "--description",
            "Troca saudação",
            "--group-title",
            "Teste CLI",
            "--project",
            str(project),
        ],
    )
    assert result.exit_code == 0
    assert "terra" in (project / "src" / "hello.py").read_text(encoding="utf-8")


@requires_git
def test_cli_apply_and_rollback_patch(project: Path, tmp_path: Path) -> None:
    patch_file = tmp_path / "change.patch"
    patch_file.write_text(
        """diff --git a/src/hello.py b/src/hello.py
--- a/src/hello.py
+++ b/src/hello.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'world'
+    return 'terra'
""",
        encoding="utf-8",
    )
    applied = runner.invoke(
        app,
        [
            "apply-patch",
            str(patch_file),
            "--description",
            "Aplica saudação",
            "--group-title",
            "Teste CLI",
            "--project",
            str(project),
        ],
    )
    assert applied.exit_code == 0
    transaction_id = json.loads(applied.stdout)["transaction_id"]
    rolled_back = runner.invoke(
        app,
        ["rollback-patch", transaction_id, "--project", str(project)],
    )
    assert rolled_back.exit_code == 0
    assert "world" in (project / "src" / "hello.py").read_text(encoding="utf-8")


def test_cli_shell(project: Path) -> None:
    result = runner.invoke(
        app,
        ["shell", "python -c \"print('cli-shell')\"", "--project", str(project)],
    )
    assert result.exit_code == 0
    assert "cli-shell" in result.stdout


@requires_ripgrep
def test_cli_grep_and_glob(project: Path) -> None:
    grep_result = runner.invoke(app, ["grep", "hello", "--project", str(project)])
    assert grep_result.exit_code == 0
    assert "src/hello.py" in grep_result.stdout

    glob_result = runner.invoke(app, ["glob", "*.py", "--project", str(project)])
    assert glob_result.exit_code == 0
    assert "src/hello.py" in glob_result.stdout
