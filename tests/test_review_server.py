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
    manifest = history.begin("patch", (snapshot,), git_version="git version test")
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
    transaction_id = _applied_transaction(project, history)
    manager = ReviewManager(history)
    try:
        opener, page, _csrf = _open_review(manager, transaction_id)
        assert "Revisão de alterações" in page
        assert 'href="./review.css"' in page
        assert (
            opener.open(f"{manager.origin}/review.css")
            .headers["Content-Type"]
            .startswith("text/css")
        )
        response = opener.open(f"{manager.origin}/api/reviews/{transaction_id}")
        summary = json.loads(response.read())
        assert summary["files_changed"] == 1
        assert summary["additions"] == 1
        assert summary["deletions"] == 1
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]

        file_response = opener.open(f"{manager.origin}/api/reviews/{transaction_id}/files/0")
        file_diff = json.loads(file_response.read())
        assert file_diff["path"] == "sample.txt"
        assert file_diff["rows"][0]["kind"] == "replacement"
    finally:
        manager.shutdown()


def test_review_page_token_is_single_use(project: Path, tmp_path: Path) -> None:
    history = HistoryManager(project, history_root=tmp_path / "history")
    transaction_id = _applied_transaction(project, history)
    manager = ReviewManager(history)
    try:
        result = manager.open(transaction_id)
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        opener.open(str(result["url"])).read()
        with pytest.raises(HTTPError) as raised:
            opener.open(str(result["url"]))
        assert raised.value.code == 404
    finally:
        manager.shutdown()


def test_review_complete_and_rollback_restore_snapshot(
    project: Path,
    tmp_path: Path,
) -> None:
    history = HistoryManager(project, history_root=tmp_path / "history")
    transaction_id = _applied_transaction(project, history)
    manager = ReviewManager(history)
    try:
        opener, _page, csrf = _open_review(manager, transaction_id)
        headers = {"Origin": manager.origin, "X-CSRF-Token": csrf}
        complete = Request(
            f"{manager.origin}/api/reviews/{transaction_id}/complete",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        assert json.loads(opener.open(complete).read())["review_state"] == "reviewed"

        rollback = Request(
            f"{manager.origin}/api/reviews/{transaction_id}/rollback",
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
    manager = ReviewManager(history)
    try:
        opener, _page, csrf = _open_review(manager, transaction_id)
        (project / "sample.txt").write_bytes(b"later\n")
        rollback = Request(
            f"{manager.origin}/api/reviews/{transaction_id}/rollback",
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
