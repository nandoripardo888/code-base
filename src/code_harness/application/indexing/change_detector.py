from dataclasses import dataclass

from code_harness.domain.enums import IndexMode
from code_harness.domain.models.index_report import StoredFile
from code_harness.domain.models.source_file import SourceFile


@dataclass(frozen=True, slots=True)
class ChangePlan:
    new: tuple[SourceFile, ...]
    candidates: tuple[tuple[SourceFile, StoredFile], ...]
    removed: tuple[StoredFile, ...]
    unchanged: tuple[StoredFile, ...]


def detect_changes(
    discovered: tuple[SourceFile, ...],
    stored: tuple[StoredFile, ...],
    mode: IndexMode,
    *,
    parser_version: str | None = None,
    chunking_version: str | None = None,
) -> ChangePlan:
    current_by_path = {item.path: item for item in discovered}
    stored_by_path = {item.path: item for item in stored}
    new = tuple(item for item in discovered if item.path not in stored_by_path)
    removed = tuple(item for item in stored if item.path not in current_by_path)
    candidates: list[tuple[SourceFile, StoredFile]] = []
    unchanged: list[StoredFile] = []

    for source in discovered:
        previous = stored_by_path.get(source.path)
        if previous is None:
            continue
        metadata_changed = (
            source.size_bytes != previous.size_bytes
            or source.modified_at_ns != previous.modified_at_ns
        )
        strategy_changed = (
            parser_version is not None
            and previous.language in {"java", "python", "plsql"}
            and previous.parser_version != parser_version
        ) or (chunking_version is not None and previous.chunking_version != chunking_version)
        if mode in (IndexMode.FULL, IndexMode.VERIFY) or metadata_changed or strategy_changed:
            candidates.append((source, previous))
        else:
            unchanged.append(previous)

    return ChangePlan(new, tuple(candidates), removed, tuple(unchanged))
