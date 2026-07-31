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


@requires_git
def test_apply_patch_exposes_optional_local_review(project: Path) -> None:
    session = Session.create(project)
    try:
        result = apply_patch(
            session.guard,
            session.history,
            patch=MODIFY_PATCH,
            reviews=session.reviews,
        )

        review = result["review"]
        assert isinstance(review, dict)
        assert review["available"] is True
        assert review["files"] == 1
        assert review["additions"] == 1
        assert review["deletions"] == 1
        assert str(review["url"]).startswith("http://127.0.0.1:")
    finally:
        session.shutdown()
