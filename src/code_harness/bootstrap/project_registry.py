import json
import os
import tempfile
from pathlib import Path

from code_harness.domain.errors import ProjectNotFoundError


def _state_directory() -> Path:
    override = os.environ.get("CODE_HARNESS_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "code-harness"
    return Path.home() / ".code-harness"


def register_active_project(root: str | Path) -> Path:
    resolved = Path(root).expanduser().resolve(strict=False)
    if not resolved.is_dir():
        raise ProjectNotFoundError(str(root))
    state = _state_directory()
    state.mkdir(parents=True, exist_ok=True)
    target = state / "active-project.json"
    payload = json.dumps({"root": str(resolved)}, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix="active-project-", dir=state, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(payload)
        os.replace(temporary_name, target)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return resolved


def resolve_active_project(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        candidate = Path(explicit)
    elif os.environ.get("CODE_HARNESS_PROJECT"):
        candidate = Path(os.environ["CODE_HARNESS_PROJECT"])
    else:
        target = _state_directory() / "active-project.json"
        if target.is_file():
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                candidate = Path(payload["root"])
            except (json.JSONDecodeError, KeyError, TypeError):
                candidate = Path.cwd()
        else:
            candidate = Path.cwd()
    resolved = candidate.expanduser().resolve(strict=False)
    if not resolved.is_dir():
        raise ProjectNotFoundError(str(candidate))
    return resolved
