from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from conftest import run_with_mcp, structured_result, text_result
from mcp import ClientSession


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_patch_tools_are_discoverable_with_the_public_contract(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    async def inspect(session: ClientSession) -> dict[str, Any]:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert "ApplyPatch" in tools
        assert "RollbackPatch" in tools
        return {
            "apply": tools["ApplyPatch"].inputSchema,
            "rollback": tools["RollbackPatch"].inputSchema,
        }

    schemas = run_with_mcp(project, tmp_path / "history", inspect)
    assert schemas["apply"]["required"] == ["patch"]
    assert set(schemas["apply"]["properties"]) == {
        "patch",
        "dry_run",
        "expected_hashes",
    }
    assert schemas["rollback"]["required"] == ["transaction_id"]
    assert set(schemas["rollback"]["properties"]) == {"transaction_id", "force"}


def test_dry_run_validates_without_mutating_the_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    original = b"alpha\r\nbeta\r\n"
    target = project / "sample.txt"
    target.write_bytes(original)
    patch = """diff --git a/sample.txt b/sample.txt
--- a/sample.txt
+++ b/sample.txt
@@ -1,2 +1,2 @@
 alpha
-beta
+gamma
"""

    async def apply(session: ClientSession) -> dict[str, Any]:
        result = await session.call_tool("ApplyPatch", {"patch": patch, "dry_run": True})
        return structured_result(result)

    result = run_with_mcp(project, tmp_path / "history", apply)
    assert result["status"] == "validated"
    assert result["dry_run"] is True
    assert result["files"] == ["sample.txt"]
    assert target.read_bytes() == original


def test_apply_patch_preserves_bytes_and_handles_all_text_operations(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "utf8.txt").write_bytes("ação\nvalor antigo\n".encode())
    (project / "legacy.txt").write_bytes(b"configura\xe7\xe3o\r\nvalor antigo\r\n")
    (project / "no-newline.txt").write_bytes(b"first\nlast")
    (project / "deleted.txt").write_bytes(b"remove me\n")
    expected_hash = sha256((project / "utf8.txt").read_bytes())
    patch = """diff --git a/utf8.txt b/utf8.txt
--- a/utf8.txt
+++ b/utf8.txt
@@ -1,2 +1,2 @@
 ação
-valor antigo
+valor novo
diff --git a/legacy.txt b/legacy.txt
--- a/legacy.txt
+++ b/legacy.txt
@@ -1,2 +1,2 @@
 configuração
-valor antigo
+valor novo
diff --git a/no-newline.txt b/no-newline.txt
--- a/no-newline.txt
+++ b/no-newline.txt
@@ -1,2 +1,2 @@
 first
-last
\\ No newline at end of file
+final
\\ No newline at end of file
diff --git a/created.txt b/created.txt
new file mode 100644
--- /dev/null
+++ b/created.txt
@@ -0,0 +1,2 @@
+new
+file
diff --git a/deleted.txt b/deleted.txt
deleted file mode 100644
--- a/deleted.txt
+++ /dev/null
@@ -1 +0,0 @@
-remove me
"""

    async def apply(session: ClientSession) -> dict[str, Any]:
        result = await session.call_tool(
            "ApplyPatch",
            {"patch": patch, "expected_hashes": {"utf8.txt": expected_hash}},
        )
        return structured_result(result)

    result = run_with_mcp(project, tmp_path / "history", apply)
    assert result["status"] == "applied"
    assert result["history_saved"] is True
    assert result["files_changed"] == 5
    assert (project / "utf8.txt").read_bytes() == "ação\nvalor novo\n".encode()
    assert (project / "legacy.txt").read_bytes() == b"configura\xe7\xe3o\r\nvalor novo\r\n"
    assert (project / "no-newline.txt").read_bytes() == b"first\nfinal"
    assert (project / "created.txt").read_bytes() == b"new\nfile\n"
    assert not (project / "deleted.txt").exists()


def test_expected_hash_conflict_is_reported_without_mutation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "sample.txt"
    target.write_bytes(b"before\n")
    patch = """--- a/sample.txt
+++ b/sample.txt
@@ -1 +1 @@
-before
+after
"""

    async def apply(session: ClientSession) -> str:
        result = await session.call_tool(
            "ApplyPatch",
            {"patch": patch, "expected_hashes": {"sample.txt": "0" * 64}},
        )
        return text_result(result)

    message = run_with_mcp(project, tmp_path / "history", apply)
    assert message.startswith("patch_conflict:")
    assert "sample.txt" in message
    assert target.read_bytes() == b"before\n"


def test_unsafe_path_is_rejected_before_writing_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "escaped.txt"
    patch = """--- /dev/null
+++ b/../escaped.txt
@@ -0,0 +1 @@
+escaped
"""

    async def apply(session: ClientSession) -> str:
        return text_result(await session.call_tool("ApplyPatch", {"patch": patch}))

    message = run_with_mcp(project, tmp_path / "history", apply)
    assert message.startswith("invalid_patch:")
    assert "unsafe path" in message
    assert not outside.exists()


def test_failed_multi_file_patch_is_atomic(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = project / "first.txt"
    second = project / "second.txt"
    first.write_bytes(b"one\n")
    second.write_bytes(b"two\n")
    patch = """--- a/first.txt
+++ b/first.txt
@@ -1 +1 @@
-one
+changed
--- a/second.txt
+++ b/second.txt
@@ -1 +1 @@
-content that is not present
+changed too
"""

    async def apply(session: ClientSession) -> str:
        return text_result(await session.call_tool("ApplyPatch", {"patch": patch}))

    message = run_with_mcp(project, tmp_path / "history", apply)
    assert message.startswith("patch_apply_failed:")
    assert first.read_bytes() == b"one\n"
    assert second.read_bytes() == b"two\n"


def test_rollback_restores_byte_exact_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    original = {
        "legacy.txt": b"configura\xe7\xe3o\r\nantigo\r\n",
        "deleted.txt": b"delete\n",
    }
    for name, content in original.items():
        (project / name).write_bytes(content)
    patch = """diff --git a/legacy.txt b/legacy.txt
--- a/legacy.txt
+++ b/legacy.txt
@@ -1,2 +1,2 @@
 configuração
-antigo
+novo
diff --git a/deleted.txt b/deleted.txt
deleted file mode 100644
--- a/deleted.txt
+++ /dev/null
@@ -1 +0,0 @@
-delete
diff --git a/created.txt b/created.txt
new file mode 100644
--- /dev/null
+++ b/created.txt
@@ -0,0 +1 @@
+created
"""

    async def apply_and_rollback(session: ClientSession) -> tuple[dict[str, Any], dict[str, Any]]:
        applied = structured_result(await session.call_tool("ApplyPatch", {"patch": patch}))
        rolled_back = structured_result(
            await session.call_tool(
                "RollbackPatch",
                {"transaction_id": applied["transaction_id"]},
            )
        )
        return applied, rolled_back

    applied, rolled_back = run_with_mcp(
        project,
        tmp_path / "history",
        apply_and_rollback,
    )
    assert applied["status"] == "applied"
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["forced"] is False
    assert (project / "legacy.txt").read_bytes() == original["legacy.txt"]
    assert (project / "deleted.txt").read_bytes() == original["deleted.txt"]
    assert not (project / "created.txt").exists()


def test_rollback_refuses_later_edits_unless_forced(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    target = project / "sample.txt"
    target.write_bytes(b"before\n")
    patch = """--- a/sample.txt
+++ b/sample.txt
@@ -1 +1 @@
-before
+after
"""

    async def exercise(session: ClientSession) -> tuple[str, dict[str, Any]]:
        applied = structured_result(await session.call_tool("ApplyPatch", {"patch": patch}))
        target.write_bytes(b"later edit\n")
        conflict = text_result(
            await session.call_tool(
                "RollbackPatch",
                {"transaction_id": applied["transaction_id"]},
            )
        )
        forced = structured_result(
            await session.call_tool(
                "RollbackPatch",
                {"transaction_id": applied["transaction_id"], "force": True},
            )
        )
        return conflict, forced

    conflict, forced = run_with_mcp(project, tmp_path / "history", exercise)
    assert conflict.startswith("rollback_conflict:")
    assert "sample.txt" in conflict
    assert forced["status"] == "rolled_back"
    assert forced["forced"] is True
    assert target.read_bytes() == b"before\n"
