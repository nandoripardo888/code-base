from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from code_harness.errors import (
    InvalidArgumentError,
    PatchHistoryError,
    PatchRollbackConflictError,
)
from code_harness.history import HistoryManager, HistoryPolicy
from code_harness.paths import PathGuard
from code_harness.session import Session
from code_harness.tools import delete, str_replace, write


def test_all_mutating_tools_share_one_group_and_rollback_in_reverse(project: Path) -> None:
    target = project / "grouped.txt"
    session = Session.create(project)
    try:
        created = write(
            session.guard,
            session.history,
            path="grouped.txt",
            contents="alpha\n",
            description="Cria arquivo agrupado.",
            group_title="Fluxo agrupado",
        )
        assert isinstance(created, dict)
        group_id = str(created["group_id"])
        replaced = str_replace(
            session.guard,
            session.history,
            path="grouped.txt",
            old_string="alpha",
            new_string="beta",
            description="Atualiza conteúdo.",
            group_id=group_id,
        )
        removed = delete(
            session.guard,
            session.history,
            path="grouped.txt",
            description="Remove arquivo temporário.",
            group_id=group_id,
        )
        assert isinstance(replaced, dict)
        assert isinstance(removed, dict)

        listing = session.reviews.service.list_groups().to_dict()
        assert listing["total"] == 1
        group = listing["items"][0]
        assert group["group_id"] == group_id
        assert group["group_title"] == "Fluxo agrupado"
        assert [item["source_tool"] for item in group["patches"]] == [
            "write",
            "str_replace",
            "delete",
        ]

        rolled_back = session.reviews.service.rollback_group(group_id)
        assert rolled_back.rolled_back_count == 3
        assert not target.exists()
    finally:
        session.shutdown()


def test_complete_group_marks_every_applied_patch_reviewed(project: Path) -> None:
    session = Session.create(project)
    try:
        created = write(
            session.guard,
            session.history,
            path="reviewed-group.txt",
            contents="alpha\n",
            description="Cria arquivo para revisão.",
            group_title="Review completo",
        )
        assert isinstance(created, dict)
        group_id = str(created["group_id"])
        replaced = str_replace(
            session.guard,
            session.history,
            path="reviewed-group.txt",
            old_string="alpha",
            new_string="beta",
            description="Atualiza arquivo para revisão.",
            group_id=group_id,
        )
        assert isinstance(replaced, dict)

        completed = session.reviews.service.complete_group(group_id)

        assert completed.reviewed_count == 2
        assert completed.pending_count == 0
        assert completed.rolled_back_count == 0
        assert all(item.review_state == "reviewed" for item in completed.patches)
        assert session.history.load(str(created["transaction_id"])).review_state == "reviewed"
        assert session.history.load(str(replaced["transaction_id"])).review_state == "reviewed"
    finally:
        session.shutdown()


def test_persisted_mutation_requires_note_and_new_group_title(project: Path) -> None:
    session = Session.create(project)
    try:
        with pytest.raises(InvalidArgumentError, match="description is required"):
            write(session.guard, session.history, path="missing.txt", contents="value")
        with pytest.raises(InvalidArgumentError, match="group_title is required"):
            write(
                session.guard,
                session.history,
                path="missing.txt",
                contents="value",
                description="Cria arquivo.",
            )
        assert not (project / "missing.txt").exists()
        assert session.history.list_groups() == ()
    finally:
        session.shutdown()


def test_group_ids_are_unique_and_existing_title_cannot_be_replaced(project: Path) -> None:
    session = Session.create(project)
    try:
        first = write(
            session.guard,
            session.history,
            path="first.txt",
            contents="first",
            description="Cria primeiro arquivo.",
            group_title="Primeiro grupo",
        )
        second = write(
            session.guard,
            session.history,
            path="second.txt",
            contents="second",
            description="Cria segundo arquivo.",
            group_title="Segundo grupo",
        )
        assert isinstance(first, dict)
        assert isinstance(second, dict)
        assert first["group_id"] != second["group_id"]
        with pytest.raises(InvalidArgumentError, match="only accepted when creating"):
            write(
                session.guard,
                session.history,
                path="third.txt",
                contents="third",
                description="Não deve alterar título.",
                group_id=str(first["group_id"]),
                group_title="Título substituto",
            )
        with pytest.raises(InvalidArgumentError, match="at most 120"):
            write(
                session.guard,
                session.history,
                path="long-title.txt",
                contents="value",
                description="Título inválido.",
                group_title="x" * 121,
            )
    finally:
        session.shutdown()


def test_blank_group_id_creates_group_and_unknown_id_is_rejected(project: Path) -> None:
    session = Session.create(project)
    try:
        created = write(
            session.guard,
            session.history,
            path="blank-group.txt",
            contents="created",
            description="Cria grupo com identificador vazio.",
            group_id="   ",
            group_title="Grupo criado",
        )
        assert isinstance(created, dict)
        assert str(created["group_id"]).startswith("group-")

        with pytest.raises(PatchHistoryError, match="Patch group was not found"):
            write(
                session.guard,
                session.history,
                path="unknown-group.txt",
                contents="not written",
                description="Não deve criar grupo implícito.",
                group_id="group-does-not-exist",
            )
        assert not (project / "unknown-group.txt").exists()

        other_project = project.parent / "other-project"
        other_project.mkdir()
        other_session = Session.create(other_project)
        try:
            with pytest.raises(PatchHistoryError, match="Patch group was not found"):
                write(
                    other_session.guard,
                    other_session.history,
                    path="cross-workspace.txt",
                    contents="not written",
                    description="Não aceita grupo de outro workspace.",
                    group_id=str(created["group_id"]),
                )
            assert not (other_project / "cross-workspace.txt").exists()
        finally:
            other_session.shutdown()
    finally:
        session.shutdown()


def test_patch_group_survives_session_restart(project: Path) -> None:
    first_session = Session.create(project)
    try:
        changed = write(
            first_session.guard,
            first_session.history,
            path="persistent.txt",
            contents="saved",
            description="Persiste atualização.",
            group_title="Grupo persistente",
        )
        assert isinstance(changed, dict)
        group_id = str(changed["group_id"])
    finally:
        first_session.shutdown()

    second_session = Session.create(project)
    try:
        group = second_session.reviews.service.get_group(group_id)
        assert group.group_title == "Grupo persistente"
        assert group.patches_count == 1
        assert group.patches[0].description == "Persiste atualização."
    finally:
        second_session.shutdown()


def test_legacy_review_manifest_is_read_as_patch_group(project: Path) -> None:
    first_session = Session.create(project)
    try:
        changed = write(
            first_session.guard,
            first_session.history,
            path="legacy-group.txt",
            contents="saved",
            description="Persiste formato para compatibilidade.",
            group_title="Grupo legado",
        )
        assert isinstance(changed, dict)
        group_id = str(changed["group_id"])
        transaction_id = str(changed["transaction_id"])
        current_dir = first_session.history.groups_dir / group_id
        legacy_dir = first_session.history.legacy_reviews_dir / group_id
        shutil.move(str(current_dir), str(legacy_dir))

        group_manifest_path = legacy_dir / "manifest.json"
        group_manifest = json.loads(group_manifest_path.read_text(encoding="utf-8"))
        group_manifest["review_id"] = group_manifest.pop("group_id")
        group_manifest["title"] = group_manifest.pop("group_title")
        group_manifest["chat_id"] = "legacy-chat"
        group_manifest_path.write_text(json.dumps(group_manifest), encoding="utf-8")

        transaction_path = (
            first_session.history.transactions_dir / transaction_id / "manifest.json"
        )
        transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
        transaction["review_id"] = transaction.pop("group_id")
        transaction["chat_id"] = "legacy-chat"
        transaction_path.write_text(json.dumps(transaction), encoding="utf-8")
    finally:
        first_session.shutdown()

    second_session = Session.create(project)
    try:
        group = second_session.history.load_group(group_id)
        summary = second_session.reviews.service.get_group(group_id)
        assert group.group_title == "Grupo legado"
        assert summary.group_id == group_id
        assert summary.patches[0].transaction_id == transaction_id
        assert "chat_id" not in summary.to_dict()
        assert "review_id" not in summary.to_dict()
    finally:
        second_session.shutdown()


def test_group_rollback_rejects_interleaved_group_history(project: Path) -> None:
    target = project / "interleaved.txt"
    target.write_text("a\n", encoding="utf-8")
    session = Session.create(project)
    try:
        first = str_replace(
            session.guard,
            session.history,
            path="interleaved.txt",
            old_string="a",
            new_string="b",
            description="Primeira alteração.",
            group_title="Grupo um",
        )
        assert isinstance(first, dict)
        second = str_replace(
            session.guard,
            session.history,
            path="interleaved.txt",
            old_string="b",
            new_string="c",
            description="Alteração intercalada.",
            group_title="Grupo dois",
        )
        assert isinstance(second, dict)
        str_replace(
            session.guard,
            session.history,
            path="interleaved.txt",
            old_string="c",
            new_string="d",
            description="Retoma primeiro review.",
            group_id=str(first["group_id"]),
        )

        with pytest.raises(PatchRollbackConflictError) as raised:
            session.history.rollback_group(str(first["group_id"]))
        assert raised.value.paths == ("interleaved.txt",)
        assert target.read_text(encoding="utf-8") == "d\n"
    finally:
        session.shutdown()


def test_legacy_transaction_is_projected_without_creating_group_manifest(
    project: Path,
) -> None:
    session = Session.create(project)
    try:
        before = (project / "src" / "hello.py").read_bytes()
        object_id = session.history.store_object(before)
        from code_harness.history import FileSnapshot

        snapshot = FileSnapshot(
            path="src/hello.py",
            operation="modify",
            existed_before=True,
            exists_after=True,
            before_sha256=object_id,
            after_sha256=object_id,
            before_object=object_id,
            after_object=object_id,
        )
        manifest = session.history.begin(
            "legacy patch",
            (snapshot,),
            git_version="git version test",
            description="Alteração legada.",
        )
        session.history.update(manifest, status="applied")

        listing = session.reviews.service.list_groups().to_dict()
        assert listing["items"][0]["group_id"] == f"legacy-group-{manifest.transaction_id}"
        assert listing["items"][0]["legacy"] is True
        assert session.history.list_groups() == ()
    finally:
        session.shutdown()


def test_retention_removes_complete_patch_groups(
    project: Path,
    tmp_path: Path,
) -> None:
    history = HistoryManager(
        project,
        history_root=tmp_path / "history",
        policy=HistoryPolicy(retention_days=1, keep_last_per_workspace=1),
    )
    guard = PathGuard(project)
    first = write(
        guard,
        history,
        path="old.txt",
        contents="one\n",
        description="Primeiro patch antigo.",
        group_title="Grupo antigo",
    )
    assert isinstance(first, dict)
    second = str_replace(
        guard,
        history,
        path="old.txt",
        old_string="one",
        new_string="two",
        description="Segundo patch antigo.",
        group_id=str(first["group_id"]),
    )
    assert isinstance(second, dict)
    latest = write(
        guard,
        history,
        path="latest.txt",
        contents="latest\n",
        description="Mantém review recente.",
        group_title="Grupo recente",
    )
    assert isinstance(latest, dict)

    old_group = history.load_group(str(first["group_id"]))
    old_date = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    history.save_group(replace(old_group, created_at=old_date, updated_at=old_date))
    for transaction_id in (str(first["transaction_id"]), str(second["transaction_id"])):
        manifest = history.load(transaction_id)
        history.save(replace(manifest, created_at=old_date, updated_at=old_date))

    result = history.maintain()

    assert result["removed_transactions"] == 2
    assert not (history.transactions_dir / str(first["transaction_id"])).exists()
    assert not (history.transactions_dir / str(second["transaction_id"])).exists()
    assert not (history.groups_dir / str(first["group_id"])).exists()
    assert (history.transactions_dir / str(latest["transaction_id"])).exists()


def test_group_rollback_compensates_an_io_failure(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = project / "compensate.txt"
    target.write_text("before\n", encoding="utf-8")
    session = Session.create(project)
    try:
        changed = str_replace(
            session.guard,
            session.history,
            path="compensate.txt",
            old_string="before",
            new_string="after",
            description="Prepara rollback com compensação.",
            group_title="Teste de compensação",
        )
        assert isinstance(changed, dict)
        original_write = session.history._write_project_state
        attempts = 0

        def fail_once(path: str, content: bytes | None) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("simulated write failure")
            original_write(path, content)

        monkeypatch.setattr(session.history, "_write_project_state", fail_once)
        with pytest.raises(PatchHistoryError, match="Could not roll back patch group"):
            session.history.rollback_group(str(changed["group_id"]))

        assert target.read_text(encoding="utf-8") == "after\n"
        assert session.history.load(str(changed["transaction_id"])).status == "applied"
        journal = session.history.groups_dir / str(changed["group_id"]) / "rollback-journal.json"
        assert not journal.exists()
    finally:
        session.shutdown()
