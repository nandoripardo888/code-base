import os

from code_harness.domain.models.execution import BackendGuarantees


class HostSupervisedBackend:
    """Declare E1 guarantees; this backend is supervision, never a sandbox."""

    def guarantees(self) -> BackendGuarantees:
        return BackendGuarantees(
            backend="host_supervised",
            execution_available=os.name == "nt",
            process_tree_containment=os.name == "nt",
            timeout_enforced=os.name == "nt",
            output_limit_enforced=os.name == "nt",
            filesystem_isolated=False,
            network_isolated=False,
            credentials_isolated=False,
        )
