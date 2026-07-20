import os
from importlib import import_module
from pathlib import Path


def configure_application_tls(
    *,
    use_system_trust: bool = True,
    ca_bundle_path: Path | None = None,
) -> str | None:
    """Configure TLS for a code-harness application process.

    This is deliberately called by CLI/worker entry points, never when the public
    Python library is imported, because truststore injection is process-global.
    """

    if ca_bundle_path is not None:
        os.environ["SSL_CERT_FILE"] = str(ca_bundle_path)
    if not use_system_trust:
        return None
    try:
        truststore = import_module("truststore")
    except ImportError:
        return "System trust support is unavailable; install code-harness[semantic]."
    truststore.inject_into_ssl()
    return None
