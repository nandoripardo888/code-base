from __future__ import annotations

from dataclasses import dataclass

from code_harness.domain.enums import ChangeSourceKind
from code_harness.domain.errors import InvalidChangeRequestError


@dataclass(frozen=True, slots=True)
class GetChangeSetRequest:
    source: str = ChangeSourceKind.WORKING_TREE.value
    base: str | None = "HEAD"
    include_untracked: bool = True

    def __post_init__(self) -> None:
        try:
            ChangeSourceKind(self.source)
        except ValueError as error:
            raise InvalidChangeRequestError(
                "source must be a supported change source kind",
                source=self.source,
            ) from error


@dataclass(frozen=True, slots=True)
class ListChangedFilesRequest:
    change_set_id: str

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")


@dataclass(frozen=True, slots=True)
class ReadDiffRequest:
    change_set_id: str
    path: str | None = None

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if self.path is not None and not self.path.strip():
            raise InvalidChangeRequestError("path must not be empty when provided")


@dataclass(frozen=True, slots=True)
class GetChangedSymbolsRequest:
    change_set_id: str
    path: str | None = None
    max_symbols: int = 200

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if self.path is not None and not self.path.strip():
            raise InvalidChangeRequestError("path must not be empty when provided")
        if not 1 <= self.max_symbols <= 2_000:
            raise InvalidChangeRequestError(
                "max_symbols must be between 1 and 2000",
                max_symbols=self.max_symbols,
            )


@dataclass(frozen=True, slots=True)
class FindChangeImpactsRequest:
    change_set_id: str
    path: str | None = None
    max_symbols: int = 50
    max_references_per_symbol: int = 30
    max_tests_per_symbol: int = 10
    include_config: bool = True

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if self.path is not None and not self.path.strip():
            raise InvalidChangeRequestError("path must not be empty when provided")
        if not 1 <= self.max_symbols <= 500:
            raise InvalidChangeRequestError(
                "max_symbols must be between 1 and 500",
                max_symbols=self.max_symbols,
            )
        if not 1 <= self.max_references_per_symbol <= 200:
            raise InvalidChangeRequestError(
                "max_references_per_symbol must be between 1 and 200",
                max_references_per_symbol=self.max_references_per_symbol,
            )
        if not 1 <= self.max_tests_per_symbol <= 50:
            raise InvalidChangeRequestError(
                "max_tests_per_symbol must be between 1 and 50",
                max_tests_per_symbol=self.max_tests_per_symbol,
            )


@dataclass(frozen=True, slots=True)
class BuildReviewContextRequest:
    change_set_id: str
    path: str | None = None
    max_tokens: int = 12_000
    reserved_tokens: int = 2_000
    max_files: int = 15
    max_snippets: int = 25

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if self.path is not None and not self.path.strip():
            raise InvalidChangeRequestError("path must not be empty when provided")
        if self.max_tokens <= 0:
            raise InvalidChangeRequestError("max_tokens must be greater than zero")
        if self.reserved_tokens < 0 or self.reserved_tokens >= self.max_tokens:
            raise InvalidChangeRequestError(
                "reserved_tokens must be non-negative and smaller than max_tokens"
            )
        if not 1 <= self.max_files <= 100:
            raise InvalidChangeRequestError("max_files must be between 1 and 100")
        if not 1 <= self.max_snippets <= 200:
            raise InvalidChangeRequestError("max_snippets must be between 1 and 200")


@dataclass(frozen=True, slots=True)
class SuggestValidationPlanRequest:
    change_set_id: str
    path: str | None = None
    max_test_files: int = 20

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if self.path is not None and not self.path.strip():
            raise InvalidChangeRequestError("path must not be empty when provided")
        if not 1 <= self.max_test_files <= 100:
            raise InvalidChangeRequestError(
                "max_test_files must be between 1 and 100",
                max_test_files=self.max_test_files,
            )


@dataclass(frozen=True, slots=True)
class ValidateChangeSetRequest:
    change_set_id: str
    executable: str
    args: tuple[str, ...] = ()
    cwd: str = "."
    timeout_seconds: float | None = None
    max_output_bytes: int | None = None
    requested_capabilities: tuple[str, ...] = ()
    reason: str | None = None
    approval_id: str | None = None
    approval_session_id: str | None = None
    wait: bool = True
    use_worktree: bool | None = None

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if not self.executable.strip():
            raise InvalidChangeRequestError("executable must not be empty")
        if not self.cwd.strip():
            raise InvalidChangeRequestError("cwd must not be empty")
        if self.approval_id is not None and not self.approval_id.strip():
            raise InvalidChangeRequestError("approval_id must not be empty")
        if self.approval_session_id is not None:
            if self.approval_id is None:
                raise InvalidChangeRequestError("approval_session_id requires approval_id")
            if not self.approval_session_id.strip():
                raise InvalidChangeRequestError("approval_session_id must not be empty")


@dataclass(frozen=True, slots=True)
class ApplyReviewFixRequest:
    change_set_id: str
    patch_text: str
    expected_file_hashes: tuple[tuple[str, str], ...]
    approval_id: str | None = None
    approval_session_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if not self.patch_text.strip():
            raise InvalidChangeRequestError("patch_text must not be empty")
        if not self.expected_file_hashes:
            raise InvalidChangeRequestError("expected_file_hashes must not be empty")
        for index, item in enumerate(self.expected_file_hashes):
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], str)
            ):
                raise InvalidChangeRequestError(
                    f"expected_file_hashes[{index}] must be a (path, sha256) pair"
                )
            if not item[0].strip() or not item[1].strip():
                raise InvalidChangeRequestError(
                    f"expected_file_hashes[{index}] path and hash must not be empty"
                )


@dataclass(frozen=True, slots=True)
class PublishReviewRequest:
    change_set_id: str
    title: str
    body: str = ""
    comments: tuple[tuple[str, str, int | None, int | None, str], ...] = ()
    approval_id: str | None = None
    approval_session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if not self.title.strip():
            raise InvalidChangeRequestError("title must not be empty")
        if len(self.title) > 500:
            raise InvalidChangeRequestError("title must not exceed 500 characters")
        if len(self.body) > 50_000:
            raise InvalidChangeRequestError("body must not exceed 50000 characters")


@dataclass(frozen=True, slots=True)
class CreateReviewCommitRequest:
    change_set_id: str
    message: str
    paths: tuple[str, ...] | None = None
    approval_id: str | None = None
    approval_session_id: str | None = None

    def __post_init__(self) -> None:
        if not self.change_set_id.strip():
            raise InvalidChangeRequestError("change_set_id must not be empty")
        if not self.message.strip():
            raise InvalidChangeRequestError("message must not be empty")
        if len(self.message) > 4_000:
            raise InvalidChangeRequestError("message must not exceed 4000 characters")
