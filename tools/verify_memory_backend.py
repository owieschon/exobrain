#!/usr/bin/env python3
"""
Verification for memory_backend.py — the client-side handler for Claude's
memory tool (memory_20250818). No API key needed: the backend is pure file
operations against the documented command contract.

Tests every command (view/create/str_replace/insert/delete/rename), the
documented response/error strings, and the path-traversal protection the docs
require.
"""
import shutil
import sys
import tempfile
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from memory_backend import MemoryBackend  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}")


def main():
    print("Verifying memory_backend.py (Claude memory tool, memory_20250818)")
    print("=" * 60)
    tmp = Path(tempfile.mkdtemp(prefix="mem-verify-"))
    try:
        mb = MemoryBackend(tmp)

        # create
        r = mb.handle({"command": "create", "path": "/memories/notes.txt", "file_text": "a\nb\n"})
        check("create returns documented success", r == "File created successfully at: /memories/notes.txt", r)
        check("create actually wrote the file", (tmp / "notes.txt").read_text() == "a\nb\n")
        r = mb.handle({"command": "create", "path": "/memories/notes.txt", "file_text": "x"})
        check("create on existing file errors", r == "Error: File /memories/notes.txt already exists", r)

        # view (file, with line numbers)
        r = mb.handle({"command": "view", "path": "/memories/notes.txt"})
        check("view file has the documented header", r.startswith("Here's the content of /memories/notes.txt with line numbers:"), r[:40])
        check("view file numbers lines (6-wide, tab)", "\n     1\ta" in r, repr(r))
        r = mb.handle({"command": "view", "path": "/memories/notes.txt", "view_range": [2, 2]})
        check("view honors view_range", "\n     2\tb" in r and "\n     1\t" not in r, repr(r))

        # view (directory listing)
        r = mb.handle({"command": "view", "path": "/memories"})
        check("view dir uses documented header", r.startswith("Here're the files and directories up to 2 levels deep in /memories"), r[:50])
        check("view dir lists the file", "/memories/notes.txt" in r, r)

        # view missing
        r = mb.handle({"command": "view", "path": "/memories/nope.txt"})
        check("view missing path errors", r == "The path /memories/nope.txt does not exist. Please provide a valid path.", r)

        # str_replace
        r = mb.handle({"command": "str_replace", "path": "/memories/notes.txt", "old_str": "a", "new_str": "A"})
        check("str_replace edits and confirms", r.startswith("The memory file has been edited."), r[:40])
        check("str_replace applied the change", (tmp / "notes.txt").read_text().startswith("A"))
        r = mb.handle({"command": "str_replace", "path": "/memories/notes.txt", "old_str": "zzz", "new_str": "q"})
        check("str_replace not-found message", "did not appear verbatim" in r, r)
        mb.handle({"command": "create", "path": "/memories/dup.txt", "file_text": "x\nx\n"})
        r = mb.handle({"command": "str_replace", "path": "/memories/dup.txt", "old_str": "x", "new_str": "y"})
        check("str_replace duplicate is rejected", "Multiple occurrences" in r and "lines: 1, 2" in r, r)

        # insert
        r = mb.handle({"command": "insert", "path": "/memories/notes.txt", "insert_line": 1, "insert_text": "MID\n"})
        check("insert confirms edit", r == "The file /memories/notes.txt has been edited.", r)
        check("insert placed the line", (tmp / "notes.txt").read_text().splitlines()[1] == "MID")
        r = mb.handle({"command": "insert", "path": "/memories/notes.txt", "insert_line": 999, "insert_text": "z"})
        check("insert invalid line errors", "Invalid `insert_line`" in r, r)

        # rename
        r = mb.handle({"command": "rename", "old_path": "/memories/dup.txt", "new_path": "/memories/renamed.txt"})
        check("rename confirms", r == "Successfully renamed /memories/dup.txt to /memories/renamed.txt", r)
        check("rename moved the file", (tmp / "renamed.txt").exists() and not (tmp / "dup.txt").exists())
        r = mb.handle({"command": "rename", "old_path": "/memories/renamed.txt", "new_path": "/memories/notes.txt"})
        check("rename onto existing dest errors", r == "Error: The destination /memories/notes.txt already exists", r)

        # delete
        r = mb.handle({"command": "delete", "path": "/memories/renamed.txt"})
        check("delete confirms", r == "Successfully deleted /memories/renamed.txt", r)
        check("delete removed the file", not (tmp / "renamed.txt").exists())
        r = mb.handle({"command": "delete", "path": "/memories/gone.txt"})
        check("delete missing path errors", r == "Error: The path /memories/gone.txt does not exist", r)

        # --- security: path-traversal protection (the docs require this) ---
        for bad in ["/memories/../secret.txt", "/etc/passwd", "/memories/../../etc/hosts", "../escape"]:
            r = mb.handle({"command": "view", "path": bad})
            check(f"rejects traversal: {bad}", "outside the allowed /memories" in r, r)
        # a create that tries to escape must not write outside root
        mb.handle({"command": "create", "path": "/memories/../escaped.txt", "file_text": "nope"})
        check("traversal create did not write outside root", not (tmp.parent / "escaped.txt").exists())

        # unknown command
        r = mb.handle({"command": "frobnicate", "path": "/memories"})
        check("unknown command is reported", r.startswith("Error: Unknown memory command"), r)

        # consolidate.py degrades to a clean no-op without an API key (no network call)
        import consolidate
        consolidate.get_api_key = lambda: None
        check("consolidate degrades to None without a key",
              consolidate.consolidate(tmp, "noop") is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL:
        print("VERIFICATION FAILED")
        sys.exit(1)
    print("ALL TESTS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
