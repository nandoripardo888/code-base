import re
import subprocess
from pathlib import Path

from code_harness.domain.enums import ErrorCode, MatchType
from code_harness.domain.errors import (
    CodeHarnessError,
    RipgrepTimeoutError,
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
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise RipgrepUnavailableError(self._executable) from error
        except subprocess.TimeoutExpired as error:
            raise RipgrepTimeoutError(timeout_seconds) from error

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
            line_text = source.snippet.content.splitlines()[
                max(0, record.line_number - start_line)
            ] if source.snippet.content else ""
            if regex:
                start_column = record.start_column
                end_column = record.end_column
                if start_column is None and line_text:
                    # Recalculate when Ripgrep omitted submatches.
                    flags = 0 if case_sensitive else re.IGNORECASE
                    matched = re.search(query, line_text, flags)
                    if matched is not None:
                        start_column = matched.start() + 1
                        end_column = matched.end()
            else:
                haystack = line_text if case_sensitive else line_text.casefold()
                needle = query if case_sensitive else query.casefold()
                column = haystack.find(needle)
                start_column = column + 1 if column >= 0 else None
                end_column = (
                    start_column + len(query) - 1 if start_column is not None else None
                )
            hits.append(
                SearchHit(
                    snippet=source.snippet,
                    score=1.0 if not regex else 0.9,
                    match_type=MatchType.EXACT_LITERAL if not regex else MatchType.REGEX,
                    matched_terms=(query,),
                    reason=(
                        "Current file contains the exact requested text."
                        if not regex
                        else "Current file line matches the requested regular expression."
                    ),
                    match_line=record.line_number,
                    start_column=start_column,
                    end_column=end_column,
                    validated=True,
                    evidence={
                        "candidate_source": "ripgrep",
                        "validated": True,
                        "regex": regex,
                    },
                )
            )
            warnings.extend(source.warnings)
        return SearchOutcome(
            tuple(hits),
            truncated=truncated,
            warnings=tuple(dict.fromkeys(warnings)),
        )
