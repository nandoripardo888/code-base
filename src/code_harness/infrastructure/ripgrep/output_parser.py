import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RipgrepMatch:
    path: str
    line_number: int
    start_column: int | None
    end_column: int | None


def parse_match(line: str) -> RipgrepMatch | None:
    try:
        event: dict[str, Any] = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    if event.get("type") != "match":
        return None
    data = event.get("data", {})
    path = data.get("path", {}).get("text")
    line_number = data.get("line_number")
    if not isinstance(path, str) or not isinstance(line_number, int):
        return None
    submatches = data.get("submatches", [])
    start_column: int | None = None
    end_column: int | None = None
    if submatches and isinstance(submatches[0], dict):
        start = submatches[0].get("start")
        end = submatches[0].get("end")
        if isinstance(start, int) and isinstance(end, int):
            start_column = start + 1
            end_column = max(start_column, end)
    normalized_path = path.replace("\\", "/").removeprefix("./")
    return RipgrepMatch(normalized_path, line_number, start_column, end_column)


def parse_output(output: str, *, limit: int) -> tuple[tuple[RipgrepMatch, ...], bool]:
    matches: list[RipgrepMatch] = []
    seen: set[tuple[str, int]] = set()
    truncated = False
    for line in output.splitlines():
        match = parse_match(line)
        if match is None:
            continue
        identity = (match.path, match.line_number)
        if identity in seen:
            continue
        seen.add(identity)
        if len(matches) >= limit:
            truncated = True
            break
        matches.append(match)
    return tuple(matches), truncated
