#!/usr/bin/env python3
"""
Verification script for auto_ingest.py (the gate).

Tests, against a throwaway temp brain:
  1. A net-new draft classifies GREEN.
  2. A draft that duplicates an existing page classifies YELLOW.
  3. A draft that contradicts an existing page classifies RED.
  4. A draft whose suggested domain does not exist classifies YELLOW.
  5. THE INVARIANT: running the gate stages proposals but writes NOTHING to
     any wiki/ directory.
  6. The gate is idempotent: a second run does not re-process staged drafts.

The temp brain is created BEFORE auto_ingest is imported, so common.discover_domains
picks it up. No API key is needed; the contradiction check degrades to the
deterministic signal-word path. All artifacts are cleaned up afterwards.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent

PASS_COUNT = 0
FAIL_COUNT = 0


def report(name: str, passed: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    suffix = f" -- {detail}" if detail else ""
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}{suffix}")


CACHING_PAGE = """# Cache expensive deterministic results

A cache stores the output of an expensive computation keyed by its input, so
repeated calls with the same input return the stored value instead of
recomputing it.

## Detail
Caching pays off when a function is pure and deterministic and is called
repeatedly with the same arguments. Keep the cache keyed by the full input and
invalidate entries when the underlying data changes.

## See also

## Sources
- Established practice.
"""


def draft(cluster: str, domain: str, lesson: str) -> str:
    return (
        f"# {cluster}\n\n"
        f"**Why it matters:** test fixture.\n\n"
        f"**Source session:** verify-0001\n"
        f"**Source turn:** n/a\n"
        f"**Suggested domain:** {domain}\n\n"
        f"## Lesson\n\n{lesson}\n"
    )


def setup_temp_brain(tmp: Path):
    """Create a one-domain temp brain with a single wiki page to compare against."""
    wiki = tmp / "demo" / "wiki"
    captures = tmp / "demo" / "raw" / "session-captures"
    wiki.mkdir(parents=True)
    captures.mkdir(parents=True)
    (wiki / "index.md").write_text(
        "# Index -- demo\n\n## Caching\n\n_How to cache._\n\n"
        "- [[caching]] -- cache expensive results\n"
    )
    (wiki / "caching.md").write_text(CACHING_PAGE)
    return captures


def wiki_snapshot(domain_dir: Path) -> set:
    return {p.name for p in (domain_dir / "wiki").glob("*.md")}


def main():
    print("Verifying auto_ingest.py (the gate)")
    print("=" * 55)
    print()

    tmp = Path(tempfile.mkdtemp(prefix="ai-verify-"))
    os.environ["BRAIN_DIR"] = str(tmp)
    sys.path.insert(0, str(_TOOLS_DIR))

    try:
        captures = setup_temp_brain(tmp)

        # Import AFTER the temp brain exists and BRAIN_DIR is set, so the domain
        # is discovered from the temp filesystem.
        import auto_ingest as ai

        # --- Classification tier tests (call classify directly) ---
        green = captures / "green.md"
        green.write_text(draft(
            "Localize user-facing strings",
            "demo",
            "Put each user-facing string behind a translation key so the "
            "interface can be localized without touching application logic.",
        ))
        tier, _, _ = ai.classify(green)
        report("net-new draft -> GREEN", tier == ai.GREEN, f"got {tier}")

        yellow = captures / "yellow.md"
        yellow.write_text(draft(
            "Caching results",
            "demo",
            "Caching stores the output of an expensive deterministic computation "
            "keyed by its input so repeated calls return the stored value instead "
            "of recomputing. Keep the cache keyed by the full input and invalidate "
            "entries when the underlying data changes.",
        ))
        tier, _, _ = ai.classify(yellow)
        report("duplicate draft -> YELLOW", tier == ai.YELLOW, f"got {tier}")

        red = captures / "red.md"
        red.write_text(draft(
            "Caching is wrong",
            "demo",
            "It is a misconception that caching expensive deterministic "
            "computations helps; keying a cache by input is incorrect for pure "
            "functions and the claim that repeated calls return a stored value "
            "is wrong.",
        ))
        tier, _, _ = ai.classify(red)
        report("contradicting draft -> RED", tier == ai.RED, f"got {tier}")

        unknown = captures / "unknown.md"
        unknown.write_text(draft(
            "Some lesson", "no-such-domain", "A lesson with no real home.",
        ))
        tier, _, _ = ai.classify(unknown)
        report("unknown domain -> YELLOW", tier == ai.YELLOW, f"got {tier}")

        # --- Invariant: the gate writes nothing to wiki/ ---
        before = wiki_snapshot(tmp / "demo")
        ai.auto_ingest(dry_run=False, verbose=False)
        after = wiki_snapshot(tmp / "demo")
        report("gate writes NOTHING to wiki/", before == after,
               f"before={sorted(before)} after={sorted(after)}")

        staged = list((tmp / "tools" / "staged").glob("*.md"))
        report("gate stages proposals for review", len(staged) >= 1,
               f"{len(staged)} staged page(s)")
        report("gate writes the pending surface file",
               (tmp / "tools" / "pending-ingest.txt").exists())

        # --- Idempotency: a second run re-processes nothing ---
        staged_before = {p.name for p in (tmp / "tools" / "staged").glob("*")}
        ai.auto_ingest(dry_run=False, verbose=False)
        staged_after = {p.name for p in (tmp / "tools" / "staged").glob("*")}
        report("second run is idempotent (no re-processing)",
               staged_before == staged_after)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 55)
    print(f"Results: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    if FAIL_COUNT:
        print("VERIFICATION FAILED")
        sys.exit(1)
    print("ALL TESTS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
