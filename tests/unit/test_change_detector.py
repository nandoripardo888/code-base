from code_harness.application.indexing.change_detector import detect_changes
from code_harness.domain.enums import IndexMode
from code_harness.domain.models.index_report import StoredFile
from code_harness.domain.models.source_file import SourceFile


def _stored(path: str, size: int, modified: int) -> StoredFile:
    return StoredFile(path, size, modified, "python", "utf-8", "hash", "now")


def test_incremental_change_detection_uses_metadata_fast_path() -> None:
    discovered = (
        SourceFile("same.py", 10, 1, "python"),
        SourceFile("changed.py", 20, 3, "python"),
        SourceFile("new.py", 30, 4, "python"),
    )
    stored = (_stored("same.py", 10, 1), _stored("changed.py", 20, 2), _stored("old.py", 1, 1))

    plan = detect_changes(discovered, stored, IndexMode.INCREMENTAL)

    assert tuple(item.path for item in plan.new) == ("new.py",)
    assert tuple(item[0].path for item in plan.candidates) == ("changed.py",)
    assert tuple(item.path for item in plan.removed) == ("old.py",)
    assert tuple(item.path for item in plan.unchanged) == ("same.py",)


def test_full_and_verify_consider_every_existing_file_a_candidate() -> None:
    discovered = (SourceFile("same.py", 10, 1, "python"),)
    stored = (_stored("same.py", 10, 1),)

    assert detect_changes(discovered, stored, IndexMode.FULL).candidates
    assert detect_changes(discovered, stored, IndexMode.VERIFY).candidates
