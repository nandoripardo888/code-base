"""Local file-backed review publisher (opt-in stub, no remote PR APIs)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from code_harness.domain.models.change_set import ChangeSet
from code_harness.domain.models.review_actions import PublishedReview, ReviewComment


class FileReviewPublisher:
    def __init__(self, publish_dir: str | Path) -> None:
        self._publish_dir = Path(publish_dir).resolve(strict=False)
        self._publish_dir.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        *,
        change_set: ChangeSet,
        title: str,
        body: str,
        comments: tuple[ReviewComment, ...],
    ) -> PublishedReview:
        publication_id = uuid4().hex
        payload = {
            "publication_id": publication_id,
            "title": title,
            "body": body,
            "change_set_id": change_set.change_set_id,
            "repository_id": change_set.repository_id,
            "head_sha": change_set.head_sha,
            "base_sha": change_set.base_sha,
            "diff_sha256": change_set.diff_sha256,
            "created_at": datetime.now(UTC).isoformat(),
            "comments": [
                {
                    "path": item.path,
                    "body": item.body,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "side": item.side,
                }
                for item in comments
            ],
        }
        path = self._publish_dir / f"{publication_id}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return PublishedReview(
            publication_id=publication_id,
            path=str(path),
            change_set_id=change_set.change_set_id,
            repository_id=change_set.repository_id,
            head_sha=change_set.head_sha,
            comments=comments,
        )
