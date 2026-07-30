"""Optional change-session subsystem composition."""

from __future__ import annotations

from dataclasses import dataclass

from code_harness.application.changes import (
    AcceptChangeSessionTool,
    CreateChangeSessionTool,
    InspectChangeSessionTool,
    PrepareChangeSessionTool,
    RejectChangeSessionTool,
)
from code_harness.application.changes.approval_presenter import ChangeSessionApprovalPresenter
from code_harness.bootstrap.settings import Settings
from code_harness.domain.models.project import Project
from code_harness.infrastructure.changes.cleanup.change_session_cleaner import ChangeSessionCleaner
from code_harness.infrastructure.changes.cleanup.retention import (
    ChangeSessionRecovery,
    ChangeSessionRetentionCleaner,
)
from code_harness.infrastructure.changes.combined_integrator import CombinedChangeIntegrator
from code_harness.infrastructure.changes.git.git_integrator import GitChangeIntegrator
from code_harness.infrastructure.changes.git.workspace_patch_integrator import (
    GitWorkspacePatchIntegrator,
)
from code_harness.infrastructure.changes.git.worktree_manager import (
    GitBranchReaderAdapter,
    LocalGitWorktreeManager,
)
from code_harness.infrastructure.changes.locks.file_session_lock_manager import (
    FileSessionLockManager,
)
from code_harness.infrastructure.changes.mirror.mirror_integrator import MirrorChangeIntegrator
from code_harness.infrastructure.changes.mirror.workspace_mirror_manager import (
    WorkspaceMirrorManager,
)
from code_harness.infrastructure.changes.patch_engine import CodexPatchEngine
from code_harness.infrastructure.changes.persistence.content_addressed_blob_store import (
    ContentAddressedBlobStore,
)
from code_harness.infrastructure.changes.persistence.sqlite_change_session_store import (
    SqliteChangeSessionStore,
)
from code_harness.infrastructure.changes.topology.filesystem_topology_resolver import (
    FilesystemTopologyResolver,
)


@dataclass(frozen=True, slots=True)
class ChangeSessionContainer:
    create_session: CreateChangeSessionTool
    prepare_session: PrepareChangeSessionTool
    accept_session: AcceptChangeSessionTool
    reject_session: RejectChangeSessionTool
    inspect_session: InspectChangeSessionTool
    store: SqliteChangeSessionStore
    cleaner: ChangeSessionCleaner
    retention: ChangeSessionRetentionCleaner
    recovery: ChangeSessionRecovery
    approval_presenter: ChangeSessionApprovalPresenter
    patch_engine: CodexPatchEngine


def build_change_session_container(
    settings: Settings,
    project: Project,
    *,
    recover_on_start: bool = True,
) -> ChangeSessionContainer:
    sessions_home = settings.change_session_home
    sessions_home.mkdir(parents=True, exist_ok=True)
    store = SqliteChangeSessionStore(settings.change_sessions_db_path())
    store.initialize()
    blob_store = ContentAddressedBlobStore(
        settings.change_sessions_db_path(),
        settings.change_blobs_home(),
    )
    blob_store.initialize()
    worktrees = LocalGitWorktreeManager(
        require_clean=settings.change_require_clean_git,
        sessions_home=sessions_home,
        blob_store=blob_store,
    )
    mirrors = WorkspaceMirrorManager(blob_store=blob_store, sessions_home=sessions_home)
    locks = FileSessionLockManager(settings.change_locks_home())
    cleaner = ChangeSessionCleaner(
        store=store,
        blob_store=blob_store,
        sessions_home=sessions_home,
        worktree_manager=worktrees,
    )
    integrator = CombinedChangeIntegrator(
        git=GitChangeIntegrator(),
        workspace_patch=GitWorkspacePatchIntegrator(
            blob_store=blob_store,
            sessions_home=sessions_home,
        ),
        mirror=MirrorChangeIntegrator(blob_store=blob_store, sessions_home=sessions_home),
    )
    patch_engine = CodexPatchEngine(store=store, blob_store=blob_store, locks=locks)
    topology = FilesystemTopologyResolver()
    create_session = CreateChangeSessionTool(
        workspace_id=project.project_id,
        workspace_root=settings.root,
        store=store,
        topology=topology,
        sessions_home=sessions_home,
        locks=locks,
        worktrees=worktrees,
        branch_reader=GitBranchReaderAdapter(),
        mirrors=mirrors,
        isolation_mode=settings.change_isolation,
    )
    prepare_session = PrepareChangeSessionTool(
        store=store,
        sessions_home=sessions_home,
        worktrees=worktrees,
        mirrors=mirrors,
    )
    accept_session = AcceptChangeSessionTool(
        store=store,
        cleaner=cleaner,
        integrator=integrator,
    )
    reject_session = RejectChangeSessionTool(store=store, cleaner=cleaner)
    inspect_session = InspectChangeSessionTool(
        store=store,
        sessions_home=sessions_home,
        worktrees=worktrees,
        mirrors=mirrors,
    )
    retention = ChangeSessionRetentionCleaner(
        store=store,
        cleaner=cleaner,
        blob_store=blob_store,
        conflict_retention_hours=settings.change_conflict_retention_hours,
        abandoned_retention_hours=settings.change_abandoned_retention_hours,
        failed_preparation_retention_hours=settings.change_failed_preparation_retention_hours,
        audit_retention_days=settings.change_audit_retention_days,
    )
    recovery = ChangeSessionRecovery(
        store=store,
        cleaner=cleaner,
        sessions_home=sessions_home,
    )
    if recover_on_start:
        recovery.recover_all()
    return ChangeSessionContainer(
        create_session=create_session,
        prepare_session=prepare_session,
        accept_session=accept_session,
        reject_session=reject_session,
        inspect_session=inspect_session,
        store=store,
        cleaner=cleaner,
        retention=retention,
        recovery=recovery,
        approval_presenter=ChangeSessionApprovalPresenter(),
        patch_engine=patch_engine,
    )
