#!/usr/bin/env python3
"""
Verification script for health_check.py.

Tests:
  1. Broken [[link-to-nowhere]] in a page -> delta check FAILS
  2. Clean net-new page -> delta check PASSES
  3. Pre-existing broken link on unrelated page -> does NOT block a clean page
  4. Full audit mode on one domain -> runs all 7 stages
  5. No API key -> deterministic stages still work, judgment stages degrade
  6. Full audit on the shipped example domain completes without error

Most tests run against a temporary brain so they do not depend on repo content.
All test artifacts are cleaned up after verification.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure tools/ is on the path
_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

PASS_COUNT = 0
FAIL_COUNT = 0


def report(name: str, passed: bool, detail: str = ""):
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if passed else "FAIL"
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    suffix = f" -- {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")


def setup_temp_brain(tmp_dir: Path):
    """Create a minimal wiki structure in a temp directory.

    Copies layout from the real brain and creates stub pages so that
    link resolution works for [[self-prompting-loops]] and
    [[evidence-not-assertion]].
    """
    domain_dir = tmp_dir / "demo"
    wiki_dir = domain_dir / "wiki"
    raw_dir = domain_dir / "raw"
    wiki_dir.mkdir(parents=True)
    raw_dir.mkdir(parents=True)

    # Stub pages for link resolution
    (wiki_dir / "self-prompting-loops.md").write_text(
        "# Self-prompting loops\n\nStub.\n\n"
        "## See also\n\n## Sources\n\n- `raw/stub.md`\n"
    )
    (wiki_dir / "evidence-not-assertion.md").write_text(
        "# Evidence, not assertion\n\nStub.\n\n"
        "## See also\n\n## Sources\n\n- `raw/stub.md`\n"
    )
    (wiki_dir / "verification-loops-and-passfail-gates.md").write_text(
        "# Verification loops\n\nStub.\n\n"
        "## See also\n\n## Sources\n\n- `raw/stub.md`\n"
    )

    # Minimal index
    (wiki_dir / "index.md").write_text(
        "# Wiki Index -- demo\n\n"
        "## Loops & verification\n\n"
        "_How loops close autonomously._\n\n"
        "- [[self-prompting-loops]] -- stub\n"
        "- [[evidence-not-assertion]] -- stub\n"
        "- [[verification-loops-and-passfail-gates]] -- stub\n"
    )

    # Stub raw file
    (raw_dir / "stub.md").write_text("stub source\n")

    return tmp_dir


# ---------------------------------------------------------------------------
# Test 1: Broken link fails delta gate
# ---------------------------------------------------------------------------

def test_broken_link_fails_delta(brain_dir: Path):
    import health_check as hc
    hc.BRAIN_DIR = brain_dir
    hc.DOMAINS = {"demo": brain_dir / "demo"}

    wiki_dir = brain_dir / "demo" / "wiki"
    test_page = wiki_dir / "_test-broken-link.md"

    test_page.write_text(
        "# Test broken link\n\n"
        "This references [[link-to-nowhere]] which does not exist.\n\n"
        "## See also\n\n"
        "- [[self-prompting-loops]]\n\n"
        "## Sources\n\n"
        "- `raw/test-source.md` -- test\n"
    )

    result = hc.run_health_check(str(test_page), mode="delta")

    det_pass = result["deterministic_pass"]
    report(
        "broken link fails delta gate",
        not det_pass,
        f"deterministic_pass={det_pass}",
    )

    broken_found = any(
        "link-to-nowhere" in issue.get("detail", "")
        for issue in result["issues"]
    )
    report("broken link is reported in issues", broken_found)

    test_page.unlink()


# ---------------------------------------------------------------------------
# Test 2: Clean page passes delta gate
# ---------------------------------------------------------------------------

def test_clean_page_passes_delta(brain_dir: Path):
    import health_check as hc
    hc.BRAIN_DIR = brain_dir
    hc.DOMAINS = {"demo": brain_dir / "demo"}

    wiki_dir = brain_dir / "demo" / "wiki"
    test_page = wiki_dir / "_test-clean-page.md"

    test_page.write_text(
        "# Test clean page\n\n"
        "This page has valid links to [[self-prompting-loops]].\n\n"
        "## See also\n\n"
        "- [[evidence-not-assertion]]\n\n"
        "## Sources\n\n"
        "- `raw/test-source.md` -- test\n"
    )

    result = hc.run_health_check(str(test_page), mode="delta")

    det_pass = result["deterministic_pass"]
    report(
        "clean page passes delta gate",
        det_pass,
        f"deterministic_pass={det_pass}",
    )

    test_page.unlink()


# ---------------------------------------------------------------------------
# Test 3: Unrelated broken link does NOT block clean draft
# ---------------------------------------------------------------------------

def test_unrelated_issue_no_block(brain_dir: Path):
    import health_check as hc
    hc.BRAIN_DIR = brain_dir
    hc.DOMAINS = {"demo": brain_dir / "demo"}

    wiki_dir = brain_dir / "demo" / "wiki"
    bad_page = wiki_dir / "_test-bad-unrelated.md"
    good_page = wiki_dir / "_test-good-page.md"

    bad_page.write_text(
        "# Bad unrelated page\n\n"
        "This references [[nonexistent-page-xyz]].\n\n"
        "## Sources\n\n"
        "- `raw/test.md` -- test\n"
    )
    good_page.write_text(
        "# Good page\n\n"
        "This has valid links to [[self-prompting-loops]].\n\n"
        "## See also\n\n"
        "- [[evidence-not-assertion]]\n\n"
        "## Sources\n\n"
        "- `raw/test.md` -- test\n"
    )

    # Delta gate on the GOOD page should pass
    result = hc.run_health_check(str(good_page), mode="delta")

    det_pass = result["deterministic_pass"]
    report(
        "unrelated broken link does NOT block clean page",
        det_pass,
        f"deterministic_pass={det_pass}",
    )

    bad_page.unlink()
    good_page.unlink()


# ---------------------------------------------------------------------------
# Test 4: Full audit runs all 7 stages
# ---------------------------------------------------------------------------

def test_full_audit(brain_dir: Path):
    import health_check as hc
    hc.BRAIN_DIR = brain_dir
    hc.DOMAINS = {"demo": brain_dir / "demo"}

    result = hc.run_health_check("demo", mode="full")

    stages_found = {s["stage"] for s in result["stages"]}
    expected = {
        "contradictions",
        "broken-links",
        "source-prudence",
        "coverage-gaps",
        "stale-pages",
        "suggested-pages",
        "connection-candidates",
    }
    report(
        "full audit runs all 7 stages",
        stages_found == expected,
        f"got {sorted(stages_found)}",
    )

    report("full audit has overall_pass field", "overall_pass" in result)
    report(
        "full audit has deterministic_pass field",
        "deterministic_pass" in result,
    )

    # Check stage type classification
    for s in result["stages"]:
        if s["stage"] in {"broken-links", "source-prudence", "stale-pages"}:
            report(
                f"  {s['stage']} classified as deterministic",
                s["type"] == "deterministic",
                f"type={s['type']}",
            )
        else:
            report(
                f"  {s['stage']} classified as judgment",
                s["type"] == "judgment",
                f"type={s['type']}",
            )


# ---------------------------------------------------------------------------
# Test 5: No API key -> deterministic gate works, judgment degrades
# ---------------------------------------------------------------------------

def test_no_api_key(brain_dir: Path):
    import health_check as hc
    hc.BRAIN_DIR = brain_dir
    hc.DOMAINS = {"demo": brain_dir / "demo"}

    saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)

    try:
        wiki_dir = brain_dir / "demo" / "wiki"
        test_page = wiki_dir / "_test-no-api.md"

        test_page.write_text(
            "# Test no API key\n\n"
            "Clean page with [[self-prompting-loops]].\n\n"
            "## See also\n\n"
            "- [[evidence-not-assertion]]\n\n"
            "## Sources\n\n"
            "- `raw/test.md` -- test\n"
        )

        result = hc.run_health_check(str(test_page), mode="delta")

        det_pass = result["deterministic_pass"]
        report(
            "no API key: deterministic gate still works",
            det_pass,
            f"deterministic_pass={det_pass}",
        )

        contra = next(
            (s for s in result["stages"] if s["stage"] == "contradictions"),
            None,
        )
        report("no API key: contradiction stage present", contra is not None)
        if contra:
            report(
                "no API key: contradiction stage is judgment type",
                contra["type"] == "judgment",
            )

        test_page.unlink()
    finally:
        if saved_key:
            os.environ["ANTHROPIC_API_KEY"] = saved_key


# ---------------------------------------------------------------------------
# Full audit on the shipped example domain (tests against real wiki content)
# ---------------------------------------------------------------------------

def test_real_full_audit():
    """Run a full audit on the shipped example domain to exercise real pages."""
    # Re-import with the repo's real paths (resets the temp-brain monkeypatching).
    import importlib

    import health_check as hc
    importlib.reload(hc)

    result = hc.run_health_check("example", mode="full")

    report(
        "real full audit completes without error",
        result is not None and "stages" in result,
        f"stages={len(result.get('stages', []))}",
    )

    # Print summary of findings
    for s in result.get("stages", []):
        issue_count = len(s.get("issues", []))
        status = "PASS" if s["passed"] else "FAIL"
        eval_note = ""
        if not s.get("evaluated", True):
            eval_note = " [partial]"
        print(
            f"    {s['stage']}: [{status}] "
            f"{issue_count} issue(s){eval_note}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Verifying health_check.py")
    print("=" * 55)
    print()

    # Create temp brain for isolated delta/full tests
    tmp_root = Path(tempfile.mkdtemp(prefix="hc-verify-"))
    try:
        brain_dir = setup_temp_brain(tmp_root)

        print("Delta gate tests (temp brain):")
        test_broken_link_fails_delta(brain_dir)
        test_clean_page_passes_delta(brain_dir)
        test_unrelated_issue_no_block(brain_dir)
        print()

        print("Full audit test (temp brain):")
        test_full_audit(brain_dir)
        print()

        print("No-API-key test (temp brain):")
        test_no_api_key(brain_dir)
        print()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print("Real full audit (example domain):")
    test_real_full_audit()
    print()

    print("=" * 55)
    print(f"Results: {PASS_COUNT} passed, {FAIL_COUNT} failed")

    if FAIL_COUNT > 0:
        print("VERIFICATION FAILED")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
