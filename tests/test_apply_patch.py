from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from code_harness.errors import PatchConflictError, PatchInvalidError, PatchRollbackConflictError
from code_harness.session import Session
from code_harness.tools import apply_patch, rollback_patch
from tests.conftest import requires_git

MODIFY_PATCH = """diff --git a/src/hello.py b/src/hello.py
--- a/src/hello.py
+++ b/src/hello.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'world'
+    return 'terra'
"""


@requires_git
def test_apply_patch_without_git_repository(project: Path) -> None:
    assert not (project / ".git").exists()
    session = Session.create(project)
    try:
        result = apply_patch(session.guard, session.history, patch=MODIFY_PATCH)
    finally:
        session.shutdown()

    assert result["status"] == "applied"
    assert result["history_saved"] is True
    assert "terra" in (project / "src" / "hello.py").read_text(encoding="utf-8")


@requires_git
def test_apply_patch_dry_run_does_not_change_files(project: Path) -> None:
    session = Session.create(project)
    try:
        result = apply_patch(
            session.guard,
            session.history,
            patch=MODIFY_PATCH,
            dry_run=True,
        )
    finally:
        session.shutdown()

    assert result["status"] == "validated"
    assert "world" in (project / "src" / "hello.py").read_text(encoding="utf-8")
    assert list(session.history.transactions_dir.glob("*/manifest.json")) == []


@requires_git
def test_apply_patch_preserves_cp1252_and_crlf(project: Path) -> None:
    target = project / "legado.txt"
    original = "linha1: configuração\r\nlinha2: ok\r\n"
    target.write_bytes(original.encode("cp1252"))
    patch = """diff --git a/legado.txt b/legado.txt
--- a/legado.txt
+++ b/legado.txt
@@ -1,2 +1,2 @@
-linha1: configuração
+linha1: alteração
 linha2: ok
"""
    session = Session.create(project)
    try:
        apply_patch(session.guard, session.history, patch=patch)
    finally:
        session.shutdown()

    raw = target.read_bytes()
    assert "alteração".encode("cp1252") in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert raw.endswith(b"\r\n")


@requires_git
def test_apply_patch_creates_and_deletes_files(project: Path) -> None:
    patch = """diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1 @@
+VALUE = 'new'
diff --git a/src/util.py b/src/util.py
deleted file mode 100644
--- a/src/util.py
+++ /dev/null
@@ -1,2 +0,0 @@
-VALUE = 42
-name = 'hello'
"""
    session = Session.create(project)
    try:
        result = apply_patch(session.guard, session.history, patch=patch)
    finally:
        session.shutdown()

    assert result["files_changed"] == 2
    assert (project / "src" / "new.py").read_text(encoding="utf-8") == "VALUE = 'new'\n"
    assert not (project / "src" / "util.py").exists()


@requires_git
def test_rollback_patch_restores_byte_snapshots(project: Path) -> None:
    original = (project / "src" / "hello.py").read_bytes()
    session = Session.create(project)
    try:
        result = apply_patch(session.guard, session.history, patch=MODIFY_PATCH)
        transaction_id = str(result["transaction_id"])
        rollback = rollback_patch(session.history, transaction_id=transaction_id)
    finally:
        session.shutdown()

    assert rollback["status"] == "rolled_back"
    assert (project / "src" / "hello.py").read_bytes() == original


@requires_git
def test_rollback_detects_changes_after_patch(project: Path) -> None:
    session = Session.create(project)
    try:
        result = apply_patch(session.guard, session.history, patch=MODIFY_PATCH)
        transaction_id = str(result["transaction_id"])
        (project / "src" / "hello.py").write_text("changed again\n", encoding="utf-8")
        with pytest.raises(PatchRollbackConflictError):
            rollback_patch(session.history, transaction_id=transaction_id)
    finally:
        session.shutdown()


@requires_git
def test_apply_patch_checks_expected_hash(project: Path) -> None:
    session = Session.create(project)
    try:
        with pytest.raises(PatchConflictError):
            apply_patch(
                session.guard,
                session.history,
                patch=MODIFY_PATCH,
                expected_hashes={"src/hello.py": "0" * 64},
            )
    finally:
        session.shutdown()

    current = (project / "src" / "hello.py").read_bytes()
    assert hashlib.sha256(current).hexdigest() != "0" * 64
    assert b"world" in current


@requires_git
def test_apply_patch_rejects_unsafe_paths(project: Path) -> None:
    patch = """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-old
+new
"""
    session = Session.create(project)
    try:
        with pytest.raises(PatchInvalidError, match="unsafe path"):
            apply_patch(session.guard, session.history, patch=patch)
    finally:
        session.shutdown()
