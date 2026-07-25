from code_harness.domain.models.execution import BackendGuarantees


class HostSupervisedBackend:
    """Declare the guarantees actually available before E1 adds a runner."""

    def guarantees(self) -> BackendGuarantees:
        return BackendGuarantees(
            backend="host_supervised",
            execution_available=False,
            process_tree_containment=False,
            timeout_enforced=False,
            output_limit_enforced=False,
            filesystem_isolated=False,
            network_isolated=False,
            credentials_isolated=False,
        )
