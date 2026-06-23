#!/usr/bin/env python3
"""
Evaluate the gate classifier against a labeled dataset.

Each case in eval/cases.jsonl is self-contained: it carries its own fixture
(one or more domains, each with wiki pages) and a draft with an expected tier.
The expected tier is the consensus of three independent blind raters who judged
by MEANING, so the dataset measures whether the lexical classifier's word-overlap
heuristics actually track the semantics they stand in for.

For each case the harness builds a throwaway brain on disk, points the classifier
at it, runs the real `auto_ingest.classify`, and compares the predicted tier to
the expected one. It reports a confusion matrix, per-tier precision/recall/F1,
per-axis accuracy, and every failure.

The classifier escalates ambiguous contradiction checks to a small model when an
API key is present; this harness reports which path it measured so the numbers
are never ambiguous about that.

Usage:
    python3 tools/eval.py                 # full report
    python3 tools/eval.py --json          # machine-readable summary
    python3 tools/eval.py --min-accuracy 0.8   # exit 1 if accuracy < 0.8 (CI gate)
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
sys.path.insert(0, str(_TOOLS_DIR))

import auto_ingest as ai  # noqa: E402
import common  # noqa: E402

TIERS = ["GREEN", "YELLOW", "RED"]
DEFAULT_CASES = _REPO_ROOT / "eval" / "cases.jsonl"


def load_cases(path: Path) -> list:
    cases = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


def build_case_brain(case: dict, root: Path) -> Path:
    """Write a case's fixture to disk and return its BRAIN_DIR."""
    brain = root / case["id"]
    for domain in case["domains"]:
        wiki = brain / domain["name"] / "wiki"
        wiki.mkdir(parents=True, exist_ok=True)
        (brain / domain["name"] / "raw" / "session-captures").mkdir(parents=True, exist_ok=True)
        index_lines = [f"# Index -- {domain['name']}", "", "## Pages", "", "_pages_", ""]
        for page in domain["pages"]:
            (wiki / f"{page['slug']}.md").write_text(page["text"])
            index_lines.append(f"- [[{page['slug']}]] -- {page['slug']}")
        (wiki / "index.md").write_text("\n".join(index_lines) + "\n")
    return brain


def point_classifier_at(brain: Path) -> None:
    """Rebind the module-level domain tables so classify() sees this case's brain."""
    domains = common.discover_domains(brain)
    common.DOMAINS = domains
    common.BRAIN_DIR = brain
    ai.DOMAINS = domains
    ai.BRAIN_DIR = brain


def predict_tier(case: dict, root: Path) -> str:
    brain = build_case_brain(case, root)
    point_classifier_at(brain)
    d = case["draft"]
    draft_md = (
        f"# {d['cluster']}\n\n"
        f"**Why it matters:** eval case {case['id']}.\n\n"
        f"**Suggested domain:** {d['domain']}\n\n"
        f"## Lesson\n\n{d['lesson']}\n"
    )
    draft_path = root / f"{case['id']}-draft.md"
    draft_path.write_text(draft_md)
    tier, _reason, _details = ai.classify(draft_path)
    return tier


def score(cases: list, results: list) -> dict:
    # confusion[expected][predicted]
    confusion = {e: {p: 0 for p in TIERS} for e in TIERS}
    per_axis = {}
    failures = []
    for case, pred in zip(cases, results):
        exp = case["expected_tier"]
        if exp not in TIERS:  # cases.jsonl is hand-authored; a typo should name itself
            raise ValueError(
                f"case {case.get('id')!r} has invalid expected_tier {exp!r}; must be one of {TIERS}")
        confusion[exp][pred] += 1
        ax = case.get("axis", "?")
        per_axis.setdefault(ax, {"correct": 0, "total": 0})
        per_axis[ax]["total"] += 1
        if pred == exp:
            per_axis[ax]["correct"] += 1
        else:
            failures.append({"id": case["id"], "axis": ax, "expected": exp, "predicted": pred})

    total = len(cases)
    correct = sum(confusion[t][t] for t in TIERS)
    per_tier = {}
    for t in TIERS:
        tp = confusion[t][t]
        predicted_t = sum(confusion[e][t] for e in TIERS)
        actual_t = sum(confusion[t][p] for p in TIERS)
        precision = tp / predicted_t if predicted_t else 0.0
        recall = tp / actual_t if actual_t else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_tier[t] = {"precision": precision, "recall": recall, "f1": f1, "support": actual_t}

    return {
        "n": total,
        "accuracy": correct / total if total else 0.0,
        "confusion": confusion,
        "per_tier": per_tier,
        "per_axis": per_axis,
        "failures": failures,
    }


def print_report(summary: dict, llm_active: bool) -> None:
    print("exobrain gate classifier — evaluation")
    print("=" * 60)
    print(f"  cases:          {summary['n']}")
    print(f"  accuracy:       {summary['accuracy']:.3f}")
    print(f"  LLM escalation: {'ACTIVE (API key present)' if llm_active else 'inactive (no key — deterministic path only)'}")
    print()
    print("  Confusion matrix (rows = expected, cols = predicted):")
    header = "            " + "".join(f"{p:>9}" for p in TIERS)
    print(header)
    for e in TIERS:
        row = "".join(f"{summary['confusion'][e][p]:>9}" for p in TIERS)
        print(f"    {e:<8}{row}")
    print()
    print("  Per-tier:")
    print(f"    {'tier':<8}{'prec':>8}{'recall':>8}{'f1':>8}{'support':>9}")
    for t in TIERS:
        m = summary["per_tier"][t]
        print(f"    {t:<8}{m['precision']:>8.2f}{m['recall']:>8.2f}{m['f1']:>8.2f}{m['support']:>9}")
    print()
    print("  Per-axis accuracy:")
    for ax in sorted(summary["per_axis"]):
        a = summary["per_axis"][ax]
        print(f"    {ax:<26}{a['correct']:>3}/{a['total']:<3}  {a['correct'] / a['total']:.2f}")
    if summary["failures"]:
        print()
        print(f"  Failures ({len(summary['failures'])}):")
        for f in summary["failures"]:
            print(f"    [{f['axis']}] {f['id']}: expected {f['expected']}, got {f['predicted']}")


DEFAULT_DB = _REPO_ROOT / "eval" / "results.db"


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def record_run(db_path: Path, summary: dict, cases: list, results: list) -> int:
    """Persist one run to the SQLite metrics store and return its run_id."""
    variant = "stem" if os.environ.get("EXOBRAIN_STEM") == "1" else "baseline"
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys = ON")  # SQLite ignores REFERENCES unless this is set
        con.executescript((_REPO_ROOT / "eval" / "schema.sql").read_text())
        # Upsert (not OR IGNORE): if cases.jsonl was relabeled between runs against
        # a persistent db, the stored expected_tier must refresh — otherwise the
        # confusion matrix (joined on the stale label) would silently disagree with
        # predictions.correct (computed from the fresh label). DO UPDATE rewrites
        # the row in place, so it can't trip the predictions foreign key the way
        # INSERT OR REPLACE (delete + reinsert) would.
        con.executemany(
            "INSERT INTO cases(case_id, axis, expected_tier) VALUES (?, ?, ?) "
            "ON CONFLICT(case_id) DO UPDATE SET axis=excluded.axis, expected_tier=excluded.expected_tier",
            [(c["id"], c.get("axis", "?"), c["expected_tier"]) for c in cases],
        )
        cur = con.execute(
            "INSERT INTO runs(created_at, git_sha, variant, llm_active, n_cases, accuracy) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), _git_sha(), variant,
             int(summary["llm_active"]), summary["n"], summary["accuracy"]),
        )
        run_id = cur.lastrowid
        con.executemany(
            "INSERT INTO predictions(run_id, case_id, predicted_tier, correct) VALUES (?, ?, ?, ?)",
            [(run_id, c["id"], pred, int(pred == c["expected_tier"]))
             for c, pred in zip(cases, results)],
        )
        con.commit()
        return run_id
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Evaluate the gate classifier against a labeled dataset.")
    ap.add_argument("--cases", default=str(DEFAULT_CASES), help="path to cases.jsonl")
    ap.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    ap.add_argument("--min-accuracy", type=float, default=None, help="exit 1 if accuracy is below this")
    ap.add_argument("--record", action="store_true", help="persist this run to the SQLite metrics store")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="metrics-store path for --record")
    args = ap.parse_args()

    cases = load_cases(Path(args.cases))
    if not cases:
        print("No eval cases found.", file=sys.stderr)
        sys.exit(1)

    llm_active = common.get_api_key() is not None
    root = Path(tempfile.mkdtemp(prefix="exobrain-eval-"))
    try:
        results = [predict_tier(c, root) for c in cases]
    finally:
        shutil.rmtree(root, ignore_errors=True)

    summary = score(cases, results)
    summary["llm_active"] = llm_active

    if args.record:
        run_id = record_run(Path(args.db), summary, cases, results)
        print(f"recorded run {run_id} ({summary['accuracy']:.3f}) to {args.db}", file=sys.stderr)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print_report(summary, llm_active)

    if args.min_accuracy is not None and summary["accuracy"] < args.min_accuracy:
        print(f"\nFAIL: accuracy {summary['accuracy']:.3f} < required {args.min_accuracy}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
