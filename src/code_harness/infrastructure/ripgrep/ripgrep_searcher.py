import subprocess
from pathlib import Path

from code_harness.domain.enums import ErrorCode, MatchType
from code_harness.domain.errors import (
    CodeHarnessError,
    InvalidQueryError,
    RipgrepUnavailableError,
)
from code_harness.domain.models.search_hit import SearchHit, SearchOutcome
from code_harness.domain.protocols.source_reader import SourceReader
from code_harness.infrastructure.ripgrep.command_builder import RipgrepCommandBuilder
from code_harness.infrastructure.ripgrep.output_parser import parse_output


class RipgrepSearcher:
    def __init__(
        self,
        root: Path,
        reader: SourceReader,
        *,
        executable: str = "rg",
        max_file_size_bytes: int = 2_000_000,
    ) -> None:
        self._root = root
        self._reader = reader
        self._executable = executable
        self._builder = RipgrepCommandBuilder(
            executable,
            max_file_size_bytes=max_file_size_bytes,
        )

    def search(
        self,
        *,
        query: str,
        regex: bool,
        include_globs: tuple[str, ...],
        exclude_globs: tuple[str, ...],
        case_sensitive: bool,
        max_results: int,
        context_lines: int,
        timeout_seconds: float,
    ) -> SearchOutcome:
        command = self._builder.build(
            query=query,
            regex=regex,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
        try:
            completed = subprocess.run(
                command,
                cwd=self._root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise RipgrepUnavailableError(self._executable) from error
        except subprocess.TimeoutExpired as error:
            raise InvalidQueryError(
                "Ripgrep search exceeded timeout_seconds.",
                timeout_seconds=timeout_seconds,
            ) from error

        stderr = completed.stderr.strip().replace(str(self._root), ".")
        if completed.returncode not in (0, 1):
            message = "Invalid regular expression." if regex else "Ripgrep search failed."
            raise CodeHarnessError(
                ErrorCode.INVALID_QUERY,
                message,
                details={"returncode": completed.returncode, "diagnostic": stderr},
            )

        records, truncated = parse_output(completed.stdout, limit=max_results)
        hits: list[SearchHit] = []
        warnings: list[str] = []
        if stderr:
            warnings.append(stderr)
        for record in records:
            start_line = max(1, record.line_number - context_lines)
            end_line = record.line_number + context_lines
            try:
                source = self._reader.read_range(
                    record.path,
                    start_line=start_line,
                    end_line=end_line,
                    max_chars=50_000,
                )
            except CodeHarnessError as error:
                warnings.append(f"Skipped stale or unreadable result {record.path}: {error.code}")
                continue
            hits.append(
                SearchHit(
                    snippet=source.snippet,
                    score=1.0 if not regex else 0.9,
                    match_type=MatchType.EXACT if not regex else MatchType.REGEX,
                    matched_terms=(query,),
                    reason=(
                        "Current file contains the exact requested text."
                        if not regex
                        else "Current file line matches the requested regular expression."
                    ),
                )
            )
            warnings.extend(source.warnings)
        return SearchOutcome(
            tuple(hits),
            truncated=truncated,
            warnings=tuple(dict.fromkeys(warnings)),
        )
