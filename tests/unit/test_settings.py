from pathlib import Path

import pytest

from code_harness.bootstrap import settings as settings_module
from code_harness.bootstrap.settings import Settings


def test_settings_falls_back_to_ripgrep_from_path_when_override_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_RG", "C:/missing/rg.exe")
    monkeypatch.setattr(
        settings_module.shutil,
        "which",
        lambda executable: "C:/path/rg.exe" if executable == "rg" else None,
    )

    settings = Settings.for_root(tmp_path)

    assert settings.ripgrep_executable == "C:/path/rg.exe"


def test_settings_preserves_valid_ripgrep_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODE_HARNESS_RG", "custom-rg")
    monkeypatch.setattr(
        settings_module.shutil,
        "which",
        lambda executable: "C:/custom/rg.exe" if executable == "custom-rg" else None,
    )

    settings = Settings.for_root(tmp_path)

    assert settings.ripgrep_executable == "C:/custom/rg.exe"


def test_settings_reads_and_validates_semantic_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODE_HARNESS_SEMANTIC", "true")
    monkeypatch.setenv("CODE_HARNESS_EMBEDDING_MODEL", "custom-model")
    monkeypatch.setenv("CODE_HARNESS_EMBEDDING_BATCH_SIZE", "8")
    monkeypatch.setenv("CODE_HARNESS_EMBEDDING_WINDOW_CHARS", "900")
    monkeypatch.setenv("CODE_HARNESS_EMBEDDING_WINDOW_OVERLAP_CHARS", "90")
    monkeypatch.setenv("CODE_HARNESS_MODEL_CACHE", str(tmp_path / "models"))
    monkeypatch.setenv("CODE_HARNESS_EMBEDDING_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("CODE_HARNESS_SYSTEM_TRUST", "false")
    monkeypatch.setenv("CODE_HARNESS_CA_BUNDLE", str(tmp_path / "company.pem"))

    settings = Settings.for_root(tmp_path)

    assert settings.semantic_enabled
    assert settings.embedding_model == "custom-model"
    assert settings.embedding_batch_size == 8
    assert settings.embedding_window_chars == 900
    assert settings.embedding_window_overlap_chars == 90
    assert settings.embedding_cache_path == (tmp_path / "models").resolve()
    assert settings.embedding_timeout_seconds == 45
    assert not settings.system_trust_enabled
    assert settings.ca_bundle_path == (tmp_path / "company.pem").resolve()
