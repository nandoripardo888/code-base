import shutil
from pathlib import Path

import pytest


@pytest.fixture
def fixture_repository() -> Path:
    return Path(__file__).parent / "fixtures" / "sample_repository"


@pytest.fixture
def copied_repository(tmp_path: Path, fixture_repository: Path) -> Path:
    destination = tmp_path / "repository"
    shutil.copytree(fixture_repository, destination)
    return destination
