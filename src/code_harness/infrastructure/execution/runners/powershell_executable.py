from __future__ import annotations

import os
import shutil
from pathlib import Path

from code_harness.domain.errors import PowerShellUnavailableError


class PowerShell7ExecutableResolver:
    """Resolve the trusted PowerShell setting without consulting the workspace."""

    def resolve(self, executable: str) -> str:
        configured = Path(executable).expanduser()
        if configured.is_absolute():
            if configured.is_file():
                return str(configured.resolve())
            raise PowerShellUnavailableError(executable, reason="configured path does not exist")

        resolved = shutil.which(executable)
        if resolved is not None:
            return str(Path(resolved).resolve())
        if os.name == "nt" and executable.casefold() in {"pwsh", "pwsh.exe"}:
            app_alias = (
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "pwsh.exe"
            )
            if app_alias.is_file():
                return str(app_alias.resolve())
        raise PowerShellUnavailableError(executable)
