from __future__ import annotations

from pathlib import Path

from code_harness.session import Session
from code_harness.tools import apply_patch
from tests.conftest import requires_git

MODIFY_PATCH = """diff --git a/src/hello.py b/src/hello.py
--- a/src/hello.py
+++ b/src/hello.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'world'
+    return 'reviewed'
"""

SECOND_PATCH = """diff --git a/src/hello.py b/src/hello.py
--- a/src/hello.py
+++ b/src/hello.py
@@ -1,2 +1,2 @@
 def hello():
-    return 'reviewed'
+    return 'reviewed again'
"""


@requires_git
def test_apply_patch_exposes_optional_local_review(project: Path) -> None:
    session = Session.create(project)
    try:
        result = apply_patch(
            session.guard,
            session.history,
            patch=MODIFY_PATCH,
            description="Atualiza o retorno de hello para revisão.",
            group_title="Retorno de hello",
            reviews=session.reviews,
        )

        review = result["review"]
        assert isinstance(review, dict)
        assert review["available"] is True
        assert result["review_available"] is True
        assert review["files"] == 1
        assert review["additions"] == 1
        assert review["deletions"] == 1
        assert str(review["url"]).startswith("http://127.0.0.1:")
        assert result["review_url"] == review["url"]
        assert result["review_message"] == "Abra o portal local para revisar esta alteração."
        assert result["description"] == "Atualiza o retorno de hello para revisão."
        assert result["group_title"] == "Retorno de hello"
        assert review["group_id"] == result["group_id"]
        manifest = session.history.load(str(result["transaction_id"]))
        assert manifest.description == "Atualiza o retorno de hello para revisão."
        summary = session.reviews.service.get_summary(manifest.transaction_id)
        assert summary.description == manifest.description
    finally:
        session.shutdown()

@requires_git
def test_apply_patches_reuse_or_create_groups_explicitly(project: Path) -> None:
    session = Session.create(project)
    try:
        first = apply_patch(
            session.guard,
            session.history,
            patch=MODIFY_PATCH,
            description="Primeira atualização da conversa.",
            group_title="Primeiro grupo",
            reviews=session.reviews,
        )
        second = apply_patch(
            session.guard,
            session.history,
            patch=SECOND_PATCH,
            description="Segunda atualização da conversa.",
            group_id=str(first["group_id"]),
            reviews=session.reviews,
        )

        assert first["group_id"] == second["group_id"]
        assert first["transaction_id"] != second["transaction_id"]
        first_manifest = session.history.load(str(first["transaction_id"]))
        second_manifest = session.history.load(str(second["transaction_id"]))
        assert first_manifest.group_id == second_manifest.group_id == first["group_id"]
        assert session.history.load_group(str(first["group_id"])).group_title == "Primeiro grupo"

        reopened = session.reviews.open(str(first["transaction_id"]))
        assert reopened["group_id"] == first["group_id"]
    finally:
        session.shutdown()
