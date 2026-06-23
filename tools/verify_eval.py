#!/usr/bin/env python3
"""
Verification for eval.py (the gate-classifier scorer + SQLite metrics store) and
the analytical SQL in eval/queries.sql. No API key or network needed: score() is
a pure function and the metrics store is local sqlite3.

This is the harness for the repo's "measured, not asserted" centerpiece — it
checks that the reported metrics are actually computed correctly, and that the
window-function trend query runs against a populated store.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
import eval_db  # noqa: E402

import eval as ev  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}")


def approx(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) < eps


def main():
    print("Verifying eval.py (scorer + metrics store) and eval/queries.sql")
    print("=" * 60)

    # --- score(): hand-computed metrics on a tiny, fully-known fixture ---
    # c2 is the only miss (expected GREEN, predicted YELLOW); the rest are correct.
    cases = [
        {"id": "c1", "expected_tier": "GREEN", "axis": "a1"},
        {"id": "c2", "expected_tier": "GREEN", "axis": "a1"},
        {"id": "c3", "expected_tier": "YELLOW", "axis": "a2"},
        {"id": "c4", "expected_tier": "RED", "axis": "a2"},
    ]
    results = ["GREEN", "YELLOW", "YELLOW", "RED"]
    s = ev.score(cases, results)
    check("accuracy = 3/4", approx(s["accuracy"], 0.75), str(s["accuracy"]))
    check("n = 4", s["n"] == 4)
    check("confusion: GREEN row is {GREEN:1, YELLOW:1}",
          s["confusion"]["GREEN"]["GREEN"] == 1 and s["confusion"]["GREEN"]["YELLOW"] == 1)
    g = s["per_tier"]["GREEN"]
    check("GREEN precision=1.0, recall=0.5", approx(g["precision"], 1.0) and approx(g["recall"], 0.5),
          f"p={g['precision']} r={g['recall']}")
    check("GREEN f1 = harmonic mean (0.667)", approx(g["f1"], 2 * 1.0 * 0.5 / 1.5))
    check("GREEN support = 2 (actual count, not predicted)", g["support"] == 2)
    y = s["per_tier"]["YELLOW"]
    check("YELLOW precision=0.5 (2 predicted, 1 right), recall=1.0",
          approx(y["precision"], 0.5) and approx(y["recall"], 1.0), f"p={y['precision']} r={y['recall']}")
    check("per-axis: a1=1/2, a2=2/2",
          s["per_axis"]["a1"] == {"correct": 1, "total": 2}
          and s["per_axis"]["a2"] == {"correct": 2, "total": 2})
    check("failures lists exactly the one miss (c2)", [f["id"] for f in s["failures"]] == ["c2"])
    check("empty input is safe (accuracy 0.0, no ZeroDivision)", ev.score([], [])["accuracy"] == 0.0)

    # --- record_run(): persists normalized rows and returns a run_id ---
    tmp = Path(tempfile.mkdtemp(prefix="eval-verify-"))
    db = tmp / "results.db"
    summary = dict(s)
    summary["llm_active"] = False
    try:
        rid = ev.record_run(db, summary, cases, results)
        con = sqlite3.connect(str(db))
        try:
            runs = con.execute("SELECT run_id, n_cases, accuracy, variant FROM runs").fetchall()
            check("one run row, returned run_id matches the row",
                  len(runs) == 1 and runs[0][0] == rid and runs[0][1] == 4, str(runs))
            check("run accuracy persisted", approx(runs[0][2], 0.75))
            preds = con.execute(
                "SELECT case_id, correct FROM predictions ORDER BY case_id").fetchall()
            check("4 predictions stored with the right correct/incorrect flags",
                  dict(preds) == {"c1": 1, "c2": 0, "c3": 1, "c4": 1}, str(preds))
            check("cases upserted (4 rows)",
                  con.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 4)
        finally:
            con.close()

        # --- queries.sql: every analytical query runs against a populated 2-run store ---
        os.environ["EXOBRAIN_STEM"] = "1"  # a second, "stem"-variant run, so the trend query has a delta
        ev.record_run(db, summary, cases, results)
        os.environ.pop("EXOBRAIN_STEM", None)
        blocks = eval_db.split_blocks((_TOOLS_DIR.parent / "eval" / "queries.sql").read_text())
        check("queries.sql parses into analytical blocks", len(blocks) >= 3, str(len(blocks)))
        con = sqlite3.connect(str(db))
        try:
            for title, stmt in blocks:
                if not stmt.strip():
                    continue
                try:
                    cur = con.execute(stmt)
                    cols = [d[0] for d in cur.description]
                    cur.fetchall()
                    check(f"query executes and returns columns: {title}", len(cols) > 0)
                except sqlite3.Error as exc:
                    check(f"query executes: {title}", False, str(exc))
        finally:
            con.close()
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
