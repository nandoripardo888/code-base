from code_harness.domain.enums import FileChangeKind
from code_harness.infrastructure.git.unified_diff_parser import parse_unified_diff


def test_parse_unified_diff_extracts_hunks_and_kinds() -> None:
    text = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 def main():
+    print("hi")
     return 0
diff --git a/old.txt b/new.txt
similarity index 100%
rename from old.txt
rename to new.txt
--- a/old.txt
+++ b/new.txt
@@ -1 +1 @@
-alpha
+alpha
diff --git a/gone.txt b/gone.txt
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/fresh.txt b/fresh.txt
--- /dev/null
+++ b/fresh.txt
@@ -0,0 +1 @@
+hello
"""
    files = parse_unified_diff(text)
    by_path = {item.path: item for item in files}

    assert by_path["src/app.py"].kind is FileChangeKind.MODIFIED
    assert len(by_path["src/app.py"].hunks) == 1
    assert by_path["src/app.py"].hunks[0].new_start == 1
    assert '+    print("hi")' in by_path["src/app.py"].hunks[0].lines
    assert by_path["new.txt"].kind is FileChangeKind.RENAMED
    assert by_path["new.txt"].old_path == "old.txt"
    assert by_path["gone.txt"].kind is FileChangeKind.DELETED
    assert by_path["fresh.txt"].kind is FileChangeKind.ADDED
