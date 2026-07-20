"""Disposable worker for local embedding model loading and inference."""

import json
import sys
from pathlib import Path
from typing import Any

from code_harness.bootstrap.tls import configure_application_tls
from code_harness.infrastructure.embeddings.fastembed_provider import FastEmbedProvider


def _friendly_error(error: BaseException) -> str:
    current: BaseException | None = error
    messages: list[str] = []
    while current is not None:
        messages.append(str(current))
        current = current.__cause__
    combined = " | ".join(item for item in messages if item)
    if "CERTIFICATE_VERIFY_FAILED" in combined:
        return (
            "TLS certificate validation failed while downloading the embedding model. "
            "Enable the system trust store or configure CODE_HARNESS_CA_BUNDLE."
        )
    return combined or type(error).__name__


def _provider(payload: dict[str, Any]) -> FastEmbedProvider:
    configure_application_tls(
        use_system_trust=bool(payload.get("system_trust", True)),
        ca_bundle_path=(Path(str(payload["ca_bundle"])) if payload.get("ca_bundle") else None),
    )
    return FastEmbedProvider(
        str(payload["model"]),
        batch_size=int(payload["batch_size"]),
        window_chars=int(payload["window_chars"]),
        window_overlap_chars=int(payload["window_overlap_chars"]),
        cache_dir=Path(str(payload["cache_dir"])),
    )


def _execute(payload: dict[str, Any]) -> dict[str, object]:
    provider = _provider(payload)
    operation = str(payload.get("operation"))
    if operation == "identity":
        identity = provider.identity
        return {
            "identity": {
                "provider": identity.provider,
                "provider_version": identity.provider_version,
                "model_id": identity.model_id,
                "dimensions": identity.dimensions,
                "strategy": identity.strategy,
            }
        }
    if operation == "embed_documents":
        return {"vectors": provider.embed_documents(tuple(map(str, payload.get("texts", ()))))}
    if operation == "embed_query":
        return {"vector": provider.embed_query(str(payload.get("text", "")))}
    raise ValueError(f"Unsupported embedding worker operation: {operation}")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("Embedding worker payload must be an object.")
        response = _execute(payload)
    except BaseException as error:
        response = {
            "error": {
                "type": type(error).__name__,
                "message": _friendly_error(error),
            }
        }
    # Match the supervisor's encoding-neutral JSON protocol. This also keeps
    # localized error messages safe when stdout uses a legacy Windows code page.
    sys.stdout.write(json.dumps(response, ensure_ascii=True))


if __name__ == "__main__":
    main()
