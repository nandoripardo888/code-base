from __future__ import annotations

import html
import json
import re
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from code_harness.history import FileSnapshot, HistoryManager
from code_harness.review import ReviewManager


def _applied_transaction(
    project: Path,
    history: HistoryManager,
    *,
    before: bytes = b"before\n",
    after: bytes = b"after\n",
    description: str | None = None,
) -> str:
    target = project / "sample.txt"
    target.write_bytes(after)
    snapshot = FileSnapshot(
        path="sample.txt",
        operation="modify",
        existed_before=True,
        exists_after=True,
        before_sha256=history.store_object(before),
        after_sha256=history.store_object(after),
        before_object=history.store_object(before),
        after_object=history.store_object(after),
        encoding="utf-8",
        line_ending="LF",
    )
    manifest = history.begin(
        "patch",
        (snapshot,),
        git_version="git version test",
        description=description,
    )
    history.update(manifest, status="applied")
    return manifest.transaction_id


def _open_review(manager: ReviewManager, transaction_id: str) -> tuple[object, str, str]:
    result = manager.open(transaction_id)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    response = opener.open(str(result["url"]))
    page = response.read().decode()
    csrf_match = re.search(r'name="review-csrf" content="([^"]+)"', page)
    assert csrf_match is not None
    return opener, page, html.unescape(csrf_match.group(1))


def test_review_server_serves_summary_file_and_security_headers(
    project: Path,
    tmp_path: Path,
) -> None:
    history = HistoryManager(project, history_root=tmp_path / "history")
    transaction_id = _applied_transaction(
        project,
        history,
        description="Ajusta o arquivo de exemplo.",
    )
    manager = ReviewManager(history, port=0)
    try:
        opener, page, _csrf = _open_review(manager, transaction_id)
        group_id = f"legacy-group-{transaction_id}"
        assert "Revisão de alterações" in page
        assert 'name="review-csrf"' in page
        assert 'name="group-id"' not in page
        assert 'href="./review.css"' in page
        assert 'id="root"' in page
        assert 'src="./review.js"' in page
        assert 'id="review-select"' not in page
        assert (
            opener.open(f"{manager.origin}/review.css")
            .headers["Content-Type"]
            .startswith("text/css")
        )
        response = opener.open(f"{manager.origin}/api/groups/{group_id}")
        summary = json.loads(response.read())
        assert summary["files_changed"] == 1
        assert summary["additions"] == 1
        assert summary["deletions"] == 1
        assert summary["group_title"] == "Ajusta o arquivo de exemplo."
        assert summary["legacy"] is True
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]

        patch_response = opener.open(
            f"{manager.origin}/api/groups/{group_id}/patches/{transaction_id}"
        )
        patch_summary = json.loads(patch_response.read())
        assert patch_summary["description"] == "Ajusta o arquivo de exemplo."
        file_response = opener.open(
            f"{manager.origin}/api/groups/{group_id}/patches/{transaction_id}/files/0"
        )
        file_diff = json.loads(file_response.read())
        assert file_diff["path"] == "sample.txt"
        assert file_diff["rows"][0]["kind"] == "replacement"
    finally:
        manager.shutdown()


def test_review_portal_lists_and_opens_transactions_from_previous_session(
    project: Path,
    tmp_path: Path,
) -> None:
    history_root = tmp_path / "history"
    first_history = HistoryManager(project, history_root=history_root)
    first_id = _applied_transaction(
        project,
        first_history,
        before=b"initial\n",
        after=b"first\n",
        description="Primeira alteração.",
    )
    first_manager = ReviewManager(first_history, port=0)
    first_manager.shutdown()

    second_history = HistoryManager(project, history_root=history_root)
    second_id = _applied_transaction(
        project,
        second_history,
        before=b"first\n",
        after=b"second\n",
        description="Segunda alteração.",
    )
    second_manager = ReviewManager(second_history, port=0)
    try:
        opener, page, _csrf = _open_review(second_manager, second_id)
        assert "review-csrf" in page
        assert f"group=legacy-group-{second_id}" in str(
            second_manager.open(second_id)["url"]
        )

        listing = json.loads(opener.open(f"{second_manager.origin}/api/groups").read())
        assert listing["total"] == 2
        assert [item["group_id"] for item in listing["items"]] == [
            f"legacy-group-{second_id}",
            f"legacy-group-{first_id}",
        ]
        assert listing["items"][0]["group_title"] == "Segunda alteração."
        assert listing["items"][0]["patches"][0]["files"][0]["path"] == "sample.txt"
        assert listing["retained_limit"] == second_history.policy.keep_last_per_workspace

        previous = json.loads(
            opener.open(
                f"{second_manager.origin}/api/groups/legacy-group-{first_id}"
            ).read()
        )
        assert previous["group_id"] == f"legacy-group-{first_id}"
        assert previous["files_changed"] == 1
    finally:
        second_manager.shutdown()


def test_review_root_is_stable_without_one_use_token(project: Path, tmp_path: Path) -> None:
    history = HistoryManager(project, history_root=tmp_path / "history")
    transaction_id = _applied_transaction(project, history)
    manager = ReviewManager(history, port=0)
    try:
        origin = manager.ensure_started()
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        first = opener.open(f"{origin}/").read().decode()
        second = opener.open(f"{origin}/").read().decode()
        assert 'name="review-csrf"' in first
        assert 'name="review-csrf"' in second
        result = manager.open(transaction_id)
        assert str(result["url"]).startswith(f"{origin}/?")
        assert "group=" in str(result["url"])
        assert "patch=" in str(result["url"])
        assert result["origin"] == origin
    finally:
        manager.shutdown()


def test_review_api_requires_session_cookie(project: Path, tmp_path: Path) -> None:
    history = HistoryManager(project, history_root=tmp_path / "history")
    _applied_transaction(project, history)
    manager = ReviewManager(history, port=0)
    try:
        with pytest.raises(HTTPError) as raised:
            build_opener().open(f"{manager.origin}/api/groups")
        assert raised.value.code == 401
    finally:
        manager.shutdown()


def test_review_complete_and_rollback_restore_snapshot(
    project: Path,
    tmp_path: Path,
) -> None:
    history = HistoryManager(project, history_root=tmp_path / "history")
    transaction_id = _applied_transaction(project, history)
    manager = ReviewManager(history, port=0)
    try:
        opener, _page, csrf = _open_review(manager, transaction_id)
        group_id = f"legacy-group-{transaction_id}"
        headers = {"Origin": manager.origin, "X-CSRF-Token": csrf}
        complete_group = Request(
            f"{manager.origin}/api/groups/{group_id}/complete",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        completed_group = json.loads(opener.open(complete_group).read())
        assert completed_group["reviewed_count"] == 1
        assert completed_group["pending_count"] == 0

        complete = Request(
            f"{manager.origin}/api/groups/{group_id}/patches/{transaction_id}/complete",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        assert json.loads(opener.open(complete).read())["review_state"] == "reviewed"

        rollback = Request(
            f"{manager.origin}/api/groups/{group_id}/patches/{transaction_id}/rollback",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        assert json.loads(opener.open(rollback).read())["status"] == "rolled_back"
        assert (project / "sample.txt").read_bytes() == b"before\n"
    finally:
        manager.shutdown()


def test_review_rollback_rejects_later_file_changes(project: Path, tmp_path: Path) -> None:
    history = HistoryManager(project, history_root=tmp_path / "history")
    transaction_id = _applied_transaction(project, history)
    manager = ReviewManager(history, port=0)
    try:
        opener, _page, csrf = _open_review(manager, transaction_id)
        group_id = f"legacy-group-{transaction_id}"
        (project / "sample.txt").write_bytes(b"later\n")
        rollback = Request(
            f"{manager.origin}/api/groups/{group_id}/rollback",
            data=b"{}",
            headers={"Origin": manager.origin, "X-CSRF-Token": csrf},
            method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            opener.open(rollback)
        payload = json.loads(raised.value.read())
        assert raised.value.code == 409
        assert payload["code"] == "rollback_conflict"
        assert payload["conflicts"] == ["sample.txt"]
        assert (project / "sample.txt").read_bytes() == b"later\n"
    finally:
        manager.shutdown()
