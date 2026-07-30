"""Apply unified diffs through Git in an isolated temporary workspace."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from code_harness.encoding import DecodedText, atomic_write_bytes, decode_bytes, encode_text
from code_harness.errors import (
    GitUnavailableError,
    PatchApplyError,
    PatchConflictError,
    PatchInvalidError,
)
from code_harness.history import FileSnapshot, HistoryManager, TransactionManifest
from code_harness.paths import PathGuard

_FORBIDDEN_PATCH_MARKERS = (
    "GIT binary patch",
    "Binary files ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "new file mode 120000",
    "old mode 120000",
    "new file mode 160000",
    "old mode 160000",
)


@dataclass(frozen=True, slots=True)
class PatchTarget:
    old_path: str | None
    new_path: str | None
    operation: str

    @property
    def path(self) -> str:
        value = self.new_path or self.old_path
        if value is None:
            raise PatchInvalidError("Patch target has no path.")
        return value


@dataclass(frozen=True, slots=True)
class PreparedFile:
    target: PatchTarget
    resolved: Path
    before: bytes | None
    decoded: DecodedText | None
    after: bytes | None = None


def apply_patch(
    guard: PathGuard,
    history: HistoryManager,
    *,
    patch: str,
    dry_run: bool = False,
    expected_hashes: dict[str, str] | None = None,
) -> dict[str, object]:
    """Validate and apply a unified diff without requiring a Git repository."""
    with history.exclusive():
        return _apply_patch_locked(
            guard,
            history,
            patch=patch,
            dry_run=dry_run,
            expected_hashes=expected_hashes,
        )


def _apply_patch_locked(
    guard: PathGuard,
    history: HistoryManager,
    *,
    patch: str,
    dry_run: bool,
    expected_hashes: dict[str, str] | None,
) -> dict[str, object]:
    normalized_patch = _normalize_patch(patch)
    git = _find_git()
    git_version = _git_version(git)
    targets = _parse_targets(normalized_patch)
    prepared = _prepare_files(guard, targets, expected_hashes or {})
    temporary_root = history.make_temporary_workspace()
    workspace = temporary_root / "workspace"
    workspace.mkdir()
    patch_file = temporary_root / "change.patch"
    patch_file.write_text(normalized_patch, encoding="utf-8", newline="\n")
    manifest: TransactionManifest | None = None
    try:
        _populate_workspace(workspace, prepared)
        _run_git_apply(git, workspace, patch_file, check=True)
        _run_git_apply(git, workspace, patch_file, check=False)
        completed = _collect_results(workspace, prepared)
        _assert_expected_workspace(workspace, completed)
        if dry_run:
            return {
                "status": "validated",
                "dry_run": True,
                "git_version": git_version,
                "files_changed": len(completed),
                "files": [item.target.path for item in completed],
            }

        snapshots = _store_snapshots(history, completed)
        manifest = history.begin(
            normalized_patch,
            snapshots,
            git_version=git_version,
        )
        manifest = history.update(manifest, status="ready", files=snapshots)
        _assert_current_files(completed)
        manifest = history.update(manifest, status="applying")
        try:
            _commit_files(completed)
        except OSError as error:
            restored = _restore_prepared(completed)
            status = "failed_and_restored" if restored else "recovery_required"
            history.update(manifest, status=status, error=str(error))
            raise PatchApplyError(f"Could not commit patch to real files: {error}") from error
        history.update(manifest, status="applied")
        return {
            "status": "applied",
            "transaction_id": manifest.transaction_id,
            "git_version": git_version,
            "files_changed": len(completed),
            "files": [
                {"path": item.target.path, "operation": item.target.operation} for item in completed
            ],
            "history_saved": True,
        }
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def rollback_patch(
    history: HistoryManager,
    *,
    transaction_id: str,
    force: bool = False,
) -> dict[str, object]:
    """Restore byte snapshots for a previously applied patch."""
    with history.exclusive():
        return history.rollback(transaction_id, force=force)


def _normalize_patch(patch: str) -> str:
    if not patch or not patch.strip():
        raise PatchInvalidError("Patch must not be empty.")
    normalized = patch.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    for marker in _FORBIDDEN_PATCH_MARKERS:
        if marker in normalized:
            raise PatchInvalidError(f"Unsupported patch operation: {marker.strip()}")
    return normalized


def _find_git() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise GitUnavailableError()
    return executable


def _git_version(git: str) -> str:
    try:
        result = subprocess.run(
            [git, "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitUnavailableError() from error
    return result.stdout.strip()


def _parse_targets(patch: str) -> tuple[PatchTarget, ...]:
    lines = patch.splitlines()
    targets: list[PatchTarget] = []
    old_path: str | None = None
    for line in lines:
        if line.startswith("--- "):
            old_path = _parse_header_path(line[4:])
            continue
        if line.startswith("+++ "):
            new_path = _parse_header_path(line[4:])
            if old_path is None and new_path is None:
                raise PatchInvalidError("Patch contains an empty file target.")
            if old_path is None:
                operation = "create"
            elif new_path is None:
                operation = "delete"
            elif old_path == new_path:
                operation = "modify"
            else:
                raise PatchInvalidError(
                    f"Renames are not supported in this version: {old_path} -> {new_path}"
                )
            targets.append(PatchTarget(old_path, new_path, operation))
            old_path = None
    if not targets:
        raise PatchInvalidError("Patch does not contain unified diff file headers.")
    unique: dict[str, PatchTarget] = {}
    for target in targets:
        if target.path in unique:
            raise PatchInvalidError(f"Patch repeats the file target: {target.path}")
        unique[target.path] = target
    return tuple(unique.values())


def _parse_header_path(raw: str) -> str | None:
    value = raw.split("\t", 1)[0].strip()
    if value == "/dev/null":
        return None
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = _decode_git_quoted_path(value)
    if value.startswith(("a/", "b/")):
        value = value[2:]
    value = value.replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise PatchInvalidError(f"Patch contains an unsafe path: {value!r}")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise PatchInvalidError(f"Patch contains an unsafe path: {value!r}")
    return Path(*parts).as_posix()


def _decode_git_quoted_path(value: str) -> str:
    body = value[1:-1]
    output = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            output.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise PatchInvalidError("Patch contains an invalid quoted path.")
        escaped = body[index]
        translations = {"n": 10, "t": 9, "r": 13, "\\": 92, '"': 34}
        if escaped in translations:
            output.append(translations[escaped])
            index += 1
            continue
        if escaped in "01234567":
            digits = escaped
            index += 1
            while index < len(body) and len(digits) < 3 and body[index] in "01234567":
                digits += body[index]
                index += 1
            output.append(int(digits, 8))
            continue
        output.extend(escaped.encode("utf-8"))
        index += 1
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PatchInvalidError("Patch path is not valid UTF-8.") from error


def _prepare_files(
    guard: PathGuard,
    targets: tuple[PatchTarget, ...],
    expected_hashes: dict[str, str],
) -> tuple[PreparedFile, ...]:
    normalized_expected = {
        Path(path).as_posix(): value.lower() for path, value in expected_hashes.items()
    }
    prepared: list[PreparedFile] = []
    for target in targets:
        must_exist = target.operation != "create"
        resolved = guard.resolve(target.path, kind="file", must_exist=must_exist)
        if target.operation == "create" and resolved.exists():
            raise PatchInvalidError(f"Patch tries to create an existing file: {target.path}")
        before = resolved.read_bytes() if resolved.exists() else None
        if before is not None and b"\0" in before:
            raise PatchInvalidError(f"Binary patch targets are not supported: {target.path}")
        decoded = decode_bytes(before) if before is not None else None
        expected = normalized_expected.get(target.path)
        if expected is not None:
            actual = _sha256(before) if before is not None else None
            if actual != expected:
                raise PatchConflictError([target.path])
        prepared.append(PreparedFile(target, resolved, before, decoded))
    unknown_hashes = sorted(set(normalized_expected) - {item.target.path for item in prepared})
    if unknown_hashes:
        raise PatchInvalidError(
            "expected_hashes contains paths not present in the patch: " + ", ".join(unknown_hashes)
        )
    return tuple(prepared)


def _populate_workspace(workspace: Path, prepared: tuple[PreparedFile, ...]) -> None:
    for item in prepared:
        if item.before is None or item.decoded is None:
            continue
        target = workspace / Path(item.target.old_path or item.target.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized = item.decoded.text.replace("\r\n", "\n").replace("\r", "\n")
        target.write_text(normalized, encoding="utf-8", newline="\n")


def _run_git_apply(git: str, workspace: Path, patch_file: Path, *, check: bool) -> None:
    command = [git, "apply", "--recount", "--whitespace=nowarn"]
    if check:
        command.append("--check")
    command.append(str(patch_file))
    environment = os.environ.copy()
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PatchApplyError(f"Git could not process the patch: {error}") from error
    if result.returncode != 0:
        detail = (
            result.stderr or result.stdout
        ).strip() or "git apply returned a non-zero exit code"
        phase = "validation" if check else "application"
        raise PatchApplyError(f"Git patch {phase} failed: {detail}")


def _collect_results(
    workspace: Path,
    prepared: tuple[PreparedFile, ...],
) -> tuple[PreparedFile, ...]:
    completed: list[PreparedFile] = []
    for item in prepared:
        target_path = workspace / Path(item.target.new_path or item.target.path)
        if not target_path.exists():
            if item.target.operation != "delete":
                raise PatchApplyError(
                    f"Patch did not produce the expected file: {item.target.path}"
                )
            completed.append(replace(item, after=None))
            continue
        if target_path.is_symlink() or not target_path.is_file():
            raise PatchInvalidError(f"Patch produced an unsupported file type: {item.target.path}")
        try:
            updated_text = target_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise PatchInvalidError(f"Patch produced non-UTF-8 text: {item.target.path}") from error
        if item.decoded is None:
            after = updated_text.encode("utf-8")
        else:
            restored = _restore_line_endings(item.decoded.text, updated_text)
            try:
                after = encode_text(
                    restored,
                    item.decoded.encoding,
                    has_bom=item.decoded.has_bom,
                )
            except UnicodeEncodeError as error:
                raise PatchInvalidError(
                    f"Patch adds characters that cannot be encoded as {item.decoded.encoding}: "
                    f"{item.target.path}"
                ) from error
        completed.append(replace(item, after=after))
    return tuple(completed)


def _restore_line_endings(original: str, updated: str) -> str:
    original_lines = _split_content_and_endings(original)
    updated_lines = _split_content_and_endings(updated)
    original_content = [line for line, _ending in original_lines]
    updated_content = [line for line, _ending in updated_lines]
    preferred = _preferred_ending(original_lines)
    matcher = difflib.SequenceMatcher(a=original_content, b=updated_content, autojunk=False)
    rendered: list[str] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            for offset, new_index in enumerate(range(new_start, new_end)):
                old_index = old_start + offset
                new_line, new_ending = updated_lines[new_index]
                ending = original_lines[old_index][1] if new_ending else ""
                rendered.append(new_line + ending)
            continue
        if tag == "delete":
            continue
        local_ending = _local_ending(original_lines, old_start, old_end, preferred)
        for new_index in range(new_start, new_end):
            new_line, new_ending = updated_lines[new_index]
            rendered.append(new_line + (local_ending if new_ending else ""))
    return "".join(rendered)


def _split_content_and_endings(text: str) -> list[tuple[str, str]]:
    if not text:
        return []
    result: list[tuple[str, str]] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            result.append((line[:-2], "\r\n"))
        elif line.endswith(("\n", "\r")):
            result.append((line[:-1], line[-1]))
        else:
            result.append((line, ""))
    return result


def _preferred_ending(lines: list[tuple[str, str]]) -> str:
    counts: dict[str, int] = {"\r\n": 0, "\n": 0, "\r": 0}
    for _content, ending in lines:
        if ending in counts:
            counts[ending] += 1
    return max(counts, key=lambda value: counts[value]) if any(counts.values()) else "\n"


def _local_ending(
    lines: list[tuple[str, str]],
    start: int,
    end: int,
    fallback: str,
) -> str:
    for index in range(start, min(end, len(lines))):
        if lines[index][1]:
            return lines[index][1]
    if start > 0 and lines[start - 1][1]:
        return lines[start - 1][1]
    if end < len(lines) and lines[end][1]:
        return lines[end][1]
    return fallback


def _assert_expected_workspace(
    workspace: Path,
    completed: tuple[PreparedFile, ...],
) -> None:
    expected = {
        Path(item.target.new_path or item.target.path).as_posix()
        for item in completed
        if item.after is not None
    }
    actual = {
        path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file()
    }
    unexpected = sorted(actual - expected)
    if unexpected:
        raise PatchInvalidError("Patch produced unexpected files: " + ", ".join(unexpected))


def _store_snapshots(
    history: HistoryManager,
    completed: tuple[PreparedFile, ...],
) -> tuple[FileSnapshot, ...]:
    snapshots: list[FileSnapshot] = []
    for item in completed:
        before_object = history.store_object(item.before) if item.before is not None else None
        after_object = history.store_object(item.after) if item.after is not None else None
        snapshots.append(
            FileSnapshot(
                path=item.target.path,
                operation=item.target.operation,
                existed_before=item.before is not None,
                exists_after=item.after is not None,
                before_sha256=_sha256(item.before) if item.before is not None else None,
                after_sha256=_sha256(item.after) if item.after is not None else None,
                before_object=before_object,
                after_object=after_object,
                encoding=item.decoded.encoding if item.decoded else "utf-8",
                has_bom=item.decoded.has_bom if item.decoded else False,
                line_ending=item.decoded.line_ending if item.decoded else "LF",
            )
        )
    return tuple(snapshots)


def _assert_current_files(completed: tuple[PreparedFile, ...]) -> None:
    conflicts: list[str] = []
    for item in completed:
        exists = item.resolved.is_file()
        if exists != (item.before is not None):
            conflicts.append(item.target.path)
            continue
        if item.before is not None:
            try:
                current = item.resolved.read_bytes()
            except OSError:
                conflicts.append(item.target.path)
                continue
            if _sha256(current) != _sha256(item.before):
                conflicts.append(item.target.path)
    if conflicts:
        raise PatchConflictError(conflicts)


def _commit_files(completed: tuple[PreparedFile, ...]) -> None:
    for item in completed:
        if item.after is None:
            item.resolved.unlink()
        else:
            atomic_write_bytes(item.resolved, item.after)


def _restore_prepared(completed: tuple[PreparedFile, ...]) -> bool:
    try:
        for item in completed:
            if item.before is None:
                item.resolved.unlink(missing_ok=True)
            else:
                atomic_write_bytes(item.resolved, item.before)
    except OSError:
        return False
    return True


def _sha256(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None
