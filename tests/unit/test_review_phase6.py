from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_harness.application.dto.review_requests import (
    ApplyReviewFixRequest,
    CreateReviewCommitRequest,
    PublishReviewRequest,
    ValidateChangeSetRequest,
)
from code_harness.application.execution.approval_digest import compute_approval_digest
from code_harness.application.review.apply_review_fix import ApplyReviewFixTool
from code_harness.application.review.create_review_commit import CreateReviewCommitTool
from code_harness.application.review.publish_review import PublishReviewTool
from code_harness.application.review.review_action_authorizer import ReviewActionAuthorizer
from code_harness.application.review.review_action_digest import compute_review_action_digest
from code_harness.application.review.validate_change_set import ValidateChangeSetTool
from code_harness.domain.enums import (
    ApprovalState,
    CommandKind,
    ExecutionCapability,
    ExecutionState,
    PolicyDecision,
)
from code_harness.domain.errors import (
    ExecutionApprovalRequiredError,
    ReviewActionNotAllowedError,
    ReviewActionsDisabledError,
    WorkspaceSnapshotMismatchError,
)
from code_harness.domain.models.change_set import ChangeSetRequest
from code_harness.domain.models.execution import BackendGuarantees, ExecutionResult
from code_harness.domain.models.tool_result import ToolResult
from code_harness.infrastructure.git import (
    GitReviewCommitter,
    GitWorkspaceSnapshotProvider,
    LocalGitChangeProvider,
    UnifiedPatchApplier,
)
from code_harness.infrastructure.review import FileReviewPublisher

pytestmark = [
    pytest.mark.skipif(shutil.which("git") is None, reason="Git is unavailable"),
]


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(root), *args),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "init")
    return root


class _FakeRunProcess:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def execute(self, request: object) -> ToolResult[ExecutionResult]:
        from code_harness.domain.models.execution import ApprovalDigest, CommandInspection

        self.requests.append(request)
        inspection = CommandInspection(
            kind=CommandKind.PROCESS,
            decision=PolicyDecision.ALLOW,
            requested_capabilities=(),
            required_capabilities=(),
            approval_required=False,
            reasons=(),
            risks=(),
            blocks=(),
            approval_digest=ApprovalDigest(value="d" * 64),
            cwd=".",
            timeout_seconds=60.0,
            max_output_bytes=200_000,
            executable="python",
            resolved_executable="python",
            args=(),
        )
        return ToolResult(
            ExecutionResult(
                execution_id="exec-1",
                state=ExecutionState.COMPLETED,
                inspection=inspection,
                backend_guarantees=BackendGuarantees(
                    backend="host_supervised",
                    execution_available=True,
                    process_tree_containment=True,
                    timeout_enforced=True,
                    output_limit_enforced=True,
                    filesystem_isolated=False,
                    network_isolated=False,
                    credentials_isolated=False,
                ),
                exit_code=0,
                stdout="ok",
                stderr="",
                stdout_bytes=2,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
                elapsed_ms=1,
            ),
            1,
        )


class _FakeApprovals:
    def __init__(self) -> None:
        self.requested: list[dict[str, object]] = []
        self.consumed: list[dict[str, object]] = []

    def request_approval(self, **kwargs: object) -> SimpleNamespace:
        self.requested.append(kwargs)
        return SimpleNamespace(
            approval_id="apr-1",
            state=ApprovalState.PENDING,
            expires_at="2099-01-01T00:00:00+00:00",
        )

    def consume_approval(self, project_id: str, approval_id: str, **kwargs: object) -> SimpleNamespace:
        self.consumed.append({"project_id": project_id, "approval_id": approval_id, **kwargs})
        return SimpleNamespace(approval_id=approval_id, state=ApprovalState.CONSUMED)


def test_digest_context_bumps_canonical_version(tmp_path: Path) -> None:
    base = dict(
        project_id="proj",
        kind=CommandKind.PROCESS,
        executable="pytest",
        args=("-q",),
        script=None,
        cwd=str(tmp_path),
        timeout_seconds=60.0,
        max_output_bytes=200_000,
        capabilities=(ExecutionCapability.EXECUTE_REPOSITORY_CODE,),
        backend="host_supervised",
        policy_version="1",
        policy_name="deterministic_v1",
    )
    v1 = compute_approval_digest(**base)
    v2 = compute_approval_digest(
        **base,
        digest_context=(("change_set_id", "abc"), ("workspace_snapshot_digest", "def")),
    )
    assert v1.value != v2.value


def test_validate_change_set_binds_snapshot_digest(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "app.py").write_text("print('changed')\n", encoding="utf-8")
    provider = LocalGitChangeProvider(root, repository_id="proj")
    change_set = provider.create_change_set(ChangeSetRequest())
    snapshots = GitWorkspaceSnapshotProvider(root)
    runner = _FakeRunProcess()
    tool = ValidateChangeSetTool(
        provider=provider,
        snapshots=snapshots,
        run_process=runner,  # type: ignore[arg-type]
        project_root=root,
        enabled=True,
    )
    result = tool.execute(
        ValidateChangeSetRequest(
            change_set_id=change_set.change_set_id,
            executable="python",
            args=("-c", "print(1)"),
        )
    ).data
    assert result.change_set_id == change_set.change_set_id
    assert result.workspace_snapshot_digest
    request = runner.requests[0]
    assert ("change_set_id", change_set.change_set_id) in request.digest_context
    assert ("workspace_snapshot_digest", result.workspace_snapshot_digest) in request.digest_context


def test_apply_review_fix_aborts_on_hash_mismatch(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    provider = LocalGitChangeProvider(root, repository_id="proj")
    change_set = provider.create_change_set(ChangeSetRequest())
    tool = ApplyReviewFixTool(
        provider=provider,
        applier=UnifiedPatchApplier(root, change_provider=provider),
        authorizer=None,
        enabled=True,
        allowed=True,
        project_id="proj",
    )
    with pytest.raises(WorkspaceSnapshotMismatchError):
        tool.execute(
            ApplyReviewFixRequest(
                change_set_id=change_set.change_set_id,
                patch_text=(
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -1 +1 @@\n"
                    "-print('hello')\n"
                    "+print('patched')\n"
                ),
                expected_file_hashes=(("app.py", "deadbeef"),),
            )
        )


def test_apply_review_fix_applies_when_hashes_match(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    provider = LocalGitChangeProvider(root, repository_id="proj")
    change_set = provider.create_change_set(ChangeSetRequest())
    current = hashlib.sha256((root / "app.py").read_bytes()).hexdigest()
    patch = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-print('hello')\n"
        "+print('patched')\n"
    )
    tool = ApplyReviewFixTool(
        provider=provider,
        applier=UnifiedPatchApplier(root, change_provider=provider),
        authorizer=None,
        enabled=True,
        allowed=True,
        project_id="proj",
    )
    result = tool.execute(
        ApplyReviewFixRequest(
            change_set_id=change_set.change_set_id,
            patch_text=patch,
            expected_file_hashes=(("app.py", current),),
        )
    ).data
    assert "print('patched')" in (root / "app.py").read_text(encoding="utf-8")
    assert result.new_change_set.change_set_id != change_set.change_set_id


def test_publish_and_commit_opt_in_gates(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "app.py").write_text("print('ready')\n", encoding="utf-8")
    provider = LocalGitChangeProvider(root, repository_id="proj")
    change_set = provider.create_change_set(ChangeSetRequest())
    snapshots = GitWorkspaceSnapshotProvider(root)
    publish = PublishReviewTool(
        provider=provider,
        publisher=None,
        authorizer=None,
        enabled=True,
        allowed=False,
        project_id="proj",
    )
    with pytest.raises(ReviewActionNotAllowedError):
        publish.execute(
            PublishReviewRequest(change_set_id=change_set.change_set_id, title="Review")
        )
    publisher = FileReviewPublisher(tmp_path / "out")
    publish_ok = PublishReviewTool(
        provider=provider,
        publisher=publisher,
        authorizer=None,
        enabled=True,
        allowed=True,
        project_id="proj",
    )
    published = publish_ok.execute(
        PublishReviewRequest(
            change_set_id=change_set.change_set_id,
            title="Review",
            comments=(("app.py", "looks good", 1, 1, "RIGHT"),),
        )
    ).data
    assert Path(published.path).is_file()

    commit_tool = CreateReviewCommitTool(
        provider=provider,
        snapshots=snapshots,
        committer=GitReviewCommitter(root),
        authorizer=None,
        enabled=True,
        allowed=True,
        project_id="proj",
    )
    committed = commit_tool.execute(
        CreateReviewCommitRequest(
            change_set_id=change_set.change_set_id,
            message="review commit",
            paths=("app.py",),
        )
    ).data
    assert committed.commit_sha


def test_review_action_authorizer_requires_approval() -> None:
    store = _FakeApprovals()
    authorizer = ReviewActionAuthorizer(
        project_id="proj",
        store=store,  # type: ignore[arg-type]
        require_approval=True,
        approval_ttl_seconds=600,
    )
    digest = compute_review_action_digest(
        project_id="proj",
        action_kind="publish_review",
        payload={"title": "t"},
    )
    with pytest.raises(ExecutionApprovalRequiredError) as raised:
        authorizer.ensure_authorized(
            digest=digest,
            action_kind="publish_review",
            summary="publish",
            approval_id=None,
        )
    assert raised.value.details.get("approval_id") == "apr-1"
    authorizer.ensure_authorized(
        digest=digest,
        action_kind="publish_review",
        summary="publish",
        approval_id="apr-1",
    )
    assert store.consumed


def test_disabled_review_actions(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    provider = LocalGitChangeProvider(root, repository_id="proj")
    change_set = provider.create_change_set(ChangeSetRequest())
    tool = ValidateChangeSetTool(
        provider=provider,
        snapshots=GitWorkspaceSnapshotProvider(root),
        run_process=_FakeRunProcess(),  # type: ignore[arg-type]
        project_root=root,
        enabled=False,
    )
    with pytest.raises(ReviewActionsDisabledError):
        tool.execute(
            ValidateChangeSetRequest(
                change_set_id=change_set.change_set_id,
                executable="python",
            )
        )
