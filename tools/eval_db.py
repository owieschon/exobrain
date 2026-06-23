#!/usr/bin/env python3
"""Run the analytical queries in eval/queries.sql against the metrics store and
print each result as a small table. Stdlib only (sqlite3), so no `sqlite3` CLI
is required.

Usage:
    python3 tools/eval_db.py [path/to/results.db]
"""
import re
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
QUERIES = _REPO_ROOT / "eval" / "queries.sql"
DEFAULT_DB = _REPO_ROOT / "eval" / "results.db"


def split_blocks(sql: str) -> list:
    """Split queries.sql into (title, statement) pairs on the '-- === Title ===' markers.

    A marker must be the whole line; a marker-shaped substring mid-line (e.g. inside
    a string literal) is left alone, because the match is anchored to the full line.
    """
    blocks = []
    title, lines = None, []
    for line in sql.splitlines():
        marker = re.match(r"--\s*===\s*(.+?)\s*===\s*$", line)
        if marker:
            if title:
                blocks.append((title, "\n".join(lines)))
            title, lines = marker.group(1), []
        elif title is not None:
            lines.append(line)
    if title:
        blocks.append((title, "\n".join(lines)))
    return blocks


def print_table(cols: list, rows: list) -> None:
    widths = [len(c) for c in cols]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print("  " + fmt.format(*cols))
    print("  " + fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print("  " + fmt.format(*[str(v) for v in row]))


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_DB)
    if not Path(db).exists():
        print(f"no metrics store at {db}; run `make eval-db` to build it", file=sys.stderr)
        sys.exit(1)
    con = sqlite3.connect(db)
    try:
        for title, statement in split_blocks(QUERIES.read_text()):
            if not statement.strip():
                continue
            cur = con.execute(statement)
            cols = [d[0] for d in cur.description]
            print(f"\n## {title}")
            print_table(cols, cur.fetchall())
    finally:
        con.close()


if __name__ == "__main__":
    main()
