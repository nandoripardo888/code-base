from __future__ import annotations

from code_harness.history import TransactionManifest
from code_harness.review.diff_builder import build_side_by_side


def test_side_by_side_aligns_replacements_and_counts_lines() -> None:
    result = build_side_by_side(
        b"first\nold one\nold two\nlast\n",
        b"first\nnew one\nnew two\nnew three\nlast\n",
        collapse_context=False,
    )

    assert result.additions == 3
    assert result.deletions == 2
    changed = [row for row in result.rows if row.kind != "context"]
    assert [row.kind for row in changed] == ["replacement", "replacement", "addition"]
    assert changed[0].old_line_number == 2
    assert changed[0].new_line_number == 2
    assert changed[2].old_text is None
    assert changed[2].new_text == "new three"


def test_side_by_side_represents_created_deleted_and_empty_files() -> None:
    created = build_side_by_side(None, b"one\ntwo\n")
    deleted = build_side_by_side(b"old\n", None)
    empty = build_side_by_side(b"", b"")

    assert created.additions == 2
    assert {row.kind for row in created.rows} == {"addition"}
    assert deleted.deletions == 1
    assert {row.kind for row in deleted.rows} == {"deletion"}
    assert empty.rows == ()


def test_side_by_side_decodes_cp1252_crlf_and_utf8_bom() -> None:
    result = build_side_by_side(
        "configuração\r\nok\r\n".encode("cp1252"),
        b"\xef\xbb\xbf" + "alteração\r\nok\r\n".encode(),
        collapse_context=False,
    )

    assert result.binary is False
    replacement = next(row for row in result.rows if row.kind == "replacement")
    assert replacement.old_text == "configuração"
    assert replacement.new_text == "alteração"


def test_side_by_side_collapses_large_unchanged_regions() -> None:
    before = "\n".join(f"line {index}" for index in range(30)).encode()
    after = before.replace(b"line 15", b"changed 15")

    result = build_side_by_side(before, after)

    collapsed = [row for row in result.rows if row.kind == "collapsed"]
    assert len(collapsed) == 2
    assert all(row.hidden_lines and row.hidden_lines > 0 for row in collapsed)


def test_side_by_side_marks_binary_snapshots() -> None:
    result = build_side_by_side(b"\x00\x01binary", b"\x00\x02binary")

    assert result.binary is True
    assert result.rows == ()


def test_old_manifest_defaults_to_unreviewed_apply_patch() -> None:
    value = {
        "transaction_id": "tx",
        "workspace_id": "workspace",
        "workspace_root": "/project",
        "status": "applied",
        "created_at": "2026-07-30T00:00:00+00:00",
        "updated_at": "2026-07-30T00:00:00+00:00",
        "patch_sha256": "0" * 64,
        "files": [],
    }

    manifest = TransactionManifest.from_dict(value)

    assert manifest.source_tool == "apply_patch"
    assert manifest.review_state == "unreviewed"
    assert manifest.reviewed_at is None
