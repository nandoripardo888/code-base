from __future__ import annotations

import os
import shutil
from pathlib import Path

from code_harness.domain.errors import ProcessStartError


class HostExecutableResolver:
    def resolve(self, executable: str, *, cwd: str) -> str:
        candidate = Path(executable)
        if candidate.is_absolute() or candidate.parent != Path("."):
            raise ProcessStartError(
                "run_process only accepts a bare executable name.",
                executable=executable,
            )
        cwd_path = Path(cwd).resolve(strict=False)
        path_entries = [
            entry
            for entry in os.environ.get("PATH", "").split(os.pathsep)
            if entry and Path(entry).is_absolute() and Path(entry).resolve() != cwd_path
        ]
        resolved = shutil.which(executable, path=os.pathsep.join(path_entries))
        if resolved is None:
            raise ProcessStartError(
                "Executable could not be resolved from the sanitized host PATH.",
                executable=executable,
            )
        return str(Path(resolved).resolve())
