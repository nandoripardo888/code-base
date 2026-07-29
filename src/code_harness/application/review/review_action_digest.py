from __future__ import annotations

import json
from hashlib import sha256


def compute_review_action_digest(
    *,
    project_id: str,
    action_kind: str,
    payload: dict[str, object],
) -> str:
    canonical_payload = {
        "action_kind": action_kind,
        "canonical_version": 2,
        "payload": payload,
        "project_id": project_id,
    }
    canonical = json.dumps(canonical_payload, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def patch_sha256(patch_text: str) -> str:
    return sha256(patch_text.encode("utf-8")).hexdigest()
