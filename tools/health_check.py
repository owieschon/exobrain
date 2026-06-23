#!/usr/bin/env python3
"""
Health check for exobrain wiki domains.

Implements a 7-stage health check over a domain's wiki pages as invocable
code with structured pass/fail output.

Two modes:
  FULL AUDIT  -- periodic sweep across all pages in a domain (or all domains)
  DELTA       -- check scoped to a single page (e.g. to lint a staged page
                 before a human approves it)

Each stage is classified as:
  DETERMINISTIC -- mechanical, no model needed, can hard-gate promotions
  JUDGMENT      -- requires semantic reasoning, degrades gracefully without
                   API key (returns "not evaluated," never false-passes,
                   never hard-blocks)

Stages:
  1. Contradictions          [JUDGMENT]
  2. Broken links & orphans  [DETERMINISTIC]
  3. Source prudence          [DETERMINISTIC]
  4. Coverage gaps            [JUDGMENT]
  5. Stale pages              [DETERMINISTIC]
  6. Suggested new pages      [JUDGMENT]
  7. Connection candidates    [JUDGMENT]

Usage:
    python3 health_check.py                          # full audit, all domains
    python3 health_check.py --domain example          # full audit, one domain
    python3 health_check.py --delta path/to/page.md  # delta gate on one page
    python3 health_check.py --json                   # output as JSON
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Shared infrastructure: paths, token-similarity primitives, signal lists,
# and the Anthropic key lookup all live in common.py.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent          # .../tools/
sys.path.insert(0, str(_SCRIPT_DIR))

from common import (  # noqa: E402  (local import after sys.path bootstrap above)
    DOMAINS,
    NEGATION_SIGNALS,
    SUPERSEDE_SIGNALS,
    draft_coverage,
    get_api_key,
    tokenize,
)

STALE_THRESHOLD_DAYS = 90

# Contradiction-detection thresholds and the Haiku escalation helper are shared
# with the gate, so import them rather than defining a second copy.
from auto_ingest import (  # noqa: E402  (local import after sys.path bootstrap above)
    COVERAGE_CONTRADICTION,
    COVERAGE_OVERLAP,
    COVERAGE_SUPERSEDE,
    HAIKU_AMBIGUITY_FLOOR,
    haiku_contradiction_check,
)

# Page-vs-page comparison thresholds (full audit mode). Two whole wiki pages
# share far more vocabulary than a short draft does against a page, so the
# draft-vs-page thresholds above would over-flag here; these higher thresholds
# compensate.
FULL_AUDIT_COVERAGE_CONTRADICTION = 0.35
FULL_AUDIT_COVERAGE_SUPERSEDE = 0.30


# ---------------------------------------------------------------------------
# Wiki helpers
# ---------------------------------------------------------------------------

def get_domain_for_page(page_path: Path) -> Optional[str]:
    """Determine which domain a page belongs to from its filesystem path.

    Uses path containment, not a string prefix, so domains whose names share a
    prefix (e.g. "example" and "example-notes") are not confused.
    """
    resolved = page_path.resolve()
    for domain, domain_path in DOMAINS.items():
        if resolved.is_relative_to(domain_path.resolve()):
            return domain
    return None


def get_wiki_dir(domain: str) -> Path:
    """Return the wiki/ directory for a domain."""
    return DOMAINS[domain] / "wiki"


def list_wiki_pages(domain: str) -> list[Path]:
    """List all .md files in a domain's wiki/ directory."""
    wiki_dir = get_wiki_dir(domain)
    if not wiki_dir.exists():
        return []
    return sorted(
        p for p in wiki_dir.iterdir()
        if p.suffix == ".md" and not p.name.startswith(".")
    )


def extract_wikilinks(text: str) -> list[str]:
    """Extract all [[wikilink]] targets from page text.

    Handles piped links: [[target|display text]] returns "target".
    """
    raw = re.findall(r"\[\[([^\]]+)\]\]", text)
    targets = []
    for link in raw:
        # Strip pipe syntax: [[target|display]] -> target
        if "|" in link:
            link = link.split("|", 1)[0]
        targets.append(link.strip())
    return targets


def is_synthesis_page(text: str) -> bool:
    """Detect synthesis pages by an optional banner of the form:

        > Synthesis -- derived across sources, not from any single one.

    A synthesis page aggregates many sources, so it has high token coverage
    against many other pages; recognizing it here avoids false-positive
    contradiction flags in full audit mode. The banner is a convention this
    check recognizes, not a required field.
    """
    return bool(re.search(
        r">\s*Synthesis\s*[-—]\s*derived across sources",
        text,
        re.IGNORECASE,
    ))


def resolve_wikilink(link: str, domain: str) -> Optional[Path]:
    """Resolve a [[wikilink]] to a filesystem path.

    Handles:
      [[page-name]]           -> domain/wiki/page-name.md
      [[other-domain/page]]   -> other-domain/wiki/page.md
    """
    if "/" in link:
        parts = link.split("/", 1)
        target_domain = parts[0]
        slug = parts[1]
        if target_domain in DOMAINS:
            target = DOMAINS[target_domain] / "wiki" / f"{slug}.md"
            return target if target.exists() else None
        return None
    else:
        target = DOMAINS[domain] / "wiki" / f"{link}.md"
        return target if target.exists() else None


def page_has_sources_section(text: str) -> bool:
    """Check if the page has a ## Sources section with content."""
    match = re.search(r"^## Sources\s*$", text, re.MULTILINE)
    if not match:
        return False
    remainder = text[match.end():].strip()
    if not remainder or remainder.startswith("## "):
        return False
    return True


def page_has_title(text: str) -> bool:
    """Check if the page starts with a # title (not ##)."""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith("# ") and not stripped.startswith("## ")
    return False


def page_has_see_also(text: str) -> bool:
    """Check if the page has a ## See also section."""
    return bool(re.search(r"^## See also\s*$", text, re.MULTILINE))


# ---------------------------------------------------------------------------
# Stage 1: Contradictions (JUDGMENT)
# ---------------------------------------------------------------------------

def stage_contradictions(scope: str, mode: str = "full") -> dict:
    """Check for contradictions between wiki pages.

    JUDGMENT stage. Reuses auto_ingest.py's coverage/signal analysis:
    token coverage, negation signals, supersede signals, Haiku escalation.
    Synthesis pages are excluded as comparison sources (they aggregate
    content from many pages and would generate false positives).

    Full mode: compares all non-synthesis page pairs within a domain.
    Delta mode: compares the new page against existing pages in its domain.

    Degrades to "not evaluated" without API key for ambiguous cases.
    Never false-passes, never hard-blocks.
    """
    result = {
        "stage": "contradictions",
        "type": "judgment",
        "passed": True,
        "issues": [],
        "evaluated": True,
    }

    if mode == "delta":
        page_path = Path(scope)
        if not page_path.exists():
            result["issues"].append({
                "page": scope,
                "detail": "Page not found, skipping contradiction check",
            })
            return result

        domain = get_domain_for_page(page_path)
        if not domain:
            result["evaluated"] = False
            result["issues"].append({
                "page": scope,
                "detail": "Cannot determine domain for contradiction check",
            })
            return result

        new_text = page_path.read_text()
        new_tokens = tokenize(new_text)
        new_text_lower = new_text.lower()
        slug = page_path.stem

        for existing_page in list_wiki_pages(domain):
            if existing_page.stem == slug or existing_page.stem == "index":
                continue

            existing_text = existing_page.read_text()
            if is_synthesis_page(existing_text):
                continue

            existing_tokens = tokenize(existing_text)
            cov = draft_coverage(new_tokens, existing_tokens)

            # Signal 1: negation language + topic coverage
            has_negation = any(
                neg in new_text_lower for neg in NEGATION_SIGNALS
            )
            if has_negation and cov > COVERAGE_CONTRADICTION:
                result["passed"] = False
                result["issues"].append({
                    "page": slug,
                    "detail": (
                        f"Negation language with coverage={cov:.2f} "
                        f"against [[{existing_page.stem}]]"
                    ),
                })
                continue

            # Signal 2: supersede language + topic coverage
            has_supersede = any(
                sig in new_text_lower for sig in SUPERSEDE_SIGNALS
            )
            if has_supersede and cov > COVERAGE_SUPERSEDE:
                result["passed"] = False
                result["issues"].append({
                    "page": slug,
                    "detail": (
                        f"Supersede language with coverage={cov:.2f} "
                        f"against [[{existing_page.stem}]]"
                    ),
                })
                continue

            # Signal 3: ambiguous range, consult Haiku if available
            if HAIKU_AMBIGUITY_FLOOR < cov < COVERAGE_OVERLAP:
                api_result = haiku_contradiction_check(
                    new_text, existing_page.stem, existing_text
                )
                if api_result is True:
                    result["passed"] = False
                    result["issues"].append({
                        "page": slug,
                        "detail": (
                            f"Haiku confirmed contradiction against "
                            f"[[{existing_page.stem}]] (coverage={cov:.2f})"
                        ),
                    })
                elif api_result is None:
                    # API unavailable: degrade, do not block
                    result["evaluated"] = False

    elif mode == "full":
        if scope in DOMAINS:
            domains_to_check = [scope]
        else:
            domains_to_check = list(DOMAINS.keys())

        has_api = get_api_key() is not None

        for domain in domains_to_check:
            pages = list_wiki_pages(domain)
            # Contradiction detection is inherently all-pairs (O(pages^2)
            # comparisons), but each page is tokenized once into page_data here and
            # the pair loop reuses those token sets — so tokenization is O(pages),
            # bounded by one domain's page count (tens, for a personal wiki). No
            # per-comparison re-tokenization.
            page_data = []
            for p in pages:
                if p.stem == "index":
                    continue
                text = p.read_text()
                page_data.append({
                    "path": p,
                    "stem": p.stem,
                    "text": text,
                    "text_lower": text.lower(),
                    "tokens": tokenize(text),
                    "is_synthesis": is_synthesis_page(text),
                })

            for i, page_a in enumerate(page_data):
                if page_a["is_synthesis"]:
                    continue

                for j, page_b in enumerate(page_data):
                    if i >= j or page_b["is_synthesis"]:
                        continue

                    cov_ab = draft_coverage(
                        page_a["tokens"], page_b["tokens"]
                    )
                    cov_ba = draft_coverage(
                        page_b["tokens"], page_a["tokens"]
                    )
                    cov = max(cov_ab, cov_ba)

                    # Page-vs-page uses higher coverage thresholds
                    # than draft-vs-page (pages share more domain
                    # vocabulary, so the draft-calibrated thresholds
                    # produce massive false positives).
                    has_negation = (
                        any(
                            neg in page_a["text_lower"]
                            for neg in NEGATION_SIGNALS
                        )
                        or any(
                            neg in page_b["text_lower"]
                            for neg in NEGATION_SIGNALS
                        )
                    )
                    if (has_negation
                            and cov > FULL_AUDIT_COVERAGE_CONTRADICTION):
                        result["passed"] = False
                        result["issues"].append({
                            "page": (
                                f"{page_a['stem']} <-> {page_b['stem']}"
                            ),
                            "detail": (
                                f"Negation language with coverage={cov:.2f}"
                            ),
                        })
                        continue

                    has_supersede = (
                        any(
                            sig in page_a["text_lower"]
                            for sig in SUPERSEDE_SIGNALS
                        )
                        or any(
                            sig in page_b["text_lower"]
                            for sig in SUPERSEDE_SIGNALS
                        )
                    )
                    if (has_supersede
                            and cov > FULL_AUDIT_COVERAGE_SUPERSEDE):
                        result["passed"] = False
                        result["issues"].append({
                            "page": (
                                f"{page_a['stem']} <-> {page_b['stem']}"
                            ),
                            "detail": (
                                f"Supersede language with "
                                f"coverage={cov:.2f}"
                            ),
                        })
                        continue

                    if HAIKU_AMBIGUITY_FLOOR < cov < COVERAGE_OVERLAP:
                        if has_api:
                            api_result = haiku_contradiction_check(
                                page_a["text"],
                                page_b["stem"],
                                page_b["text"],
                            )
                            if api_result is True:
                                result["passed"] = False
                                result["issues"].append({
                                    "page": (
                                        f"{page_a['stem']} <-> "
                                        f"{page_b['stem']}"
                                    ),
                                    "detail": (
                                        f"Haiku confirmed contradiction "
                                        f"(coverage={cov:.2f})"
                                    ),
                                })
                        else:
                            result["evaluated"] = False

    return result


# ---------------------------------------------------------------------------
# Stage 2: Broken links & orphans (DETERMINISTIC)
# ---------------------------------------------------------------------------

def stage_broken_links(scope: str, mode: str = "full") -> dict:
    """Check for broken [[wikilinks]] and orphan pages.

    DETERMINISTIC stage: purely mechanical link resolution.

    Full mode: checks all pages for broken outgoing links and finds
    orphan pages (pages with no incoming links, excluding index.md).
    Orphans are informational and do not set passed=False.

    Delta mode: checks only the new page's outgoing links. Orphan
    detection is skipped (a new page will not have inbound links yet).
    """
    result = {
        "stage": "broken-links",
        "type": "deterministic",
        "passed": True,
        "issues": [],
    }

    if mode == "delta":
        page_path = Path(scope)
        if not page_path.exists():
            result["passed"] = False
            result["issues"].append({
                "page": scope,
                "detail": "Page file not found",
            })
            return result

        domain = get_domain_for_page(page_path)
        if not domain:
            result["passed"] = False
            result["issues"].append({
                "page": scope,
                "detail": "Cannot determine domain for link resolution",
            })
            return result

        text = page_path.read_text()
        links = extract_wikilinks(text)

        for link in links:
            resolved = resolve_wikilink(link, domain)
            if resolved is None:
                result["passed"] = False
                result["issues"].append({
                    "page": page_path.stem,
                    "detail": (
                        f"Broken link: [[{link}]] "
                        f"does not resolve to any page"
                    ),
                })

    elif mode == "full":
        if scope in DOMAINS:
            domains_to_check = [scope]
        else:
            domains_to_check = list(DOMAINS.keys())

        for domain in domains_to_check:
            pages = list_wiki_pages(domain)
            all_linked: set[str] = set()

            # Pass 1: check outgoing links and collect link targets
            for page in pages:
                text = page.read_text()
                links = extract_wikilinks(text)

                for link in links:
                    all_linked.add(link)
                    if "/" in link:
                        # Cross-domain: also register the slug alone
                        parts = link.split("/", 1)
                        all_linked.add(parts[1])

                    resolved = resolve_wikilink(link, domain)
                    if resolved is None:
                        result["passed"] = False
                        result["issues"].append({
                            "page": page.stem,
                            "detail": f"Broken link: [[{link}]]",
                        })

            # Pass 2: orphan detection (informational, not a gate failure)
            for page in pages:
                if page.stem == "index":
                    continue
                slug = page.stem
                is_linked = (
                    slug in all_linked
                    or f"{domain}/{slug}" in all_linked
                )
                if not is_linked:
                    result["issues"].append({
                        "page": slug,
                        "detail": (
                            f"Orphan: [[{slug}]] has no incoming links "
                            f"from other pages in {domain}"
                        ),
                    })

    return result


# ---------------------------------------------------------------------------
# Stage 3: Source prudence (DETERMINISTIC)
# ---------------------------------------------------------------------------

def stage_source_prudence(scope: str, mode: str = "full") -> dict:
    """Check for missing ## Sources sections and format conformance.

    DETERMINISTIC stage. Checks:
    - Page has a # title line (format conformance)
    - Page has a non-empty ## Sources section (unsourced claims)
    - Page has a ## See also section (informational, does not gate)

    Full mode: checks all pages in a domain.
    Delta mode: checks the single new page.
    """
    result = {
        "stage": "source-prudence",
        "type": "deterministic",
        "passed": True,
        "issues": [],
    }

    def check_page(page_path: Path):
        text = page_path.read_text()
        slug = page_path.stem

        if slug == "index":
            return

        if not page_has_title(text):
            result["passed"] = False
            result["issues"].append({
                "page": slug,
                "detail": "Missing title: page should start with # Title",
            })

        if not page_has_sources_section(text):
            result["passed"] = False
            result["issues"].append({
                "page": slug,
                "detail": "Missing or empty ## Sources section",
            })

        if not page_has_see_also(text):
            # Informational only, does not gate
            result["issues"].append({
                "page": slug,
                "detail": (
                    "Missing ## See also section (informational)"
                ),
            })

    if mode == "delta":
        page_path = Path(scope)
        if not page_path.exists():
            result["passed"] = False
            result["issues"].append({
                "page": scope,
                "detail": "Page file not found",
            })
            return result

        check_page(page_path)

    elif mode == "full":
        if scope in DOMAINS:
            domains_to_check = [scope]
        else:
            domains_to_check = list(DOMAINS.keys())

        for domain in domains_to_check:
            for page in list_wiki_pages(domain):
                check_page(page)

    return result


# ---------------------------------------------------------------------------
# Stage 4: Coverage gaps (JUDGMENT)
# ---------------------------------------------------------------------------

def stage_coverage_gaps(scope: str, mode: str = "full") -> dict:
    """Check for raw/ material not yet pulled into wiki pages.

    JUDGMENT stage. Uses a mechanical proxy: checks whether each file
    in raw/ is cited by at least one wiki page's ## Sources section.
    True coverage-gap assessment (is this material important enough to
    warrant a page?) requires semantic reasoning.

    Full mode only. Delta mode returns "not applicable."
    """
    result = {
        "stage": "coverage-gaps",
        "type": "judgment",
        "passed": True,
        "issues": [],
        "evaluated": True,
    }

    if mode == "delta":
        result["evaluated"] = False
        return result

    if scope in DOMAINS:
        domains_to_check = [scope]
    else:
        domains_to_check = list(DOMAINS.keys())

    for domain in domains_to_check:
        domain_path = DOMAINS[domain]
        raw_dir = domain_path / "raw"
        wiki_dir = domain_path / "wiki"

        if not raw_dir.exists() or not wiki_dir.exists():
            continue

        # Collect all source citations from wiki pages
        all_citations: set[str] = set()
        for page in list_wiki_pages(domain):
            text = page.read_text()
            refs = re.findall(r"`?(raw/[^\s`]+)`?", text)
            all_citations.update(refs)

        # Walk raw/ and find uncited files
        for root, dirs, files in os.walk(raw_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                full = Path(root) / fname
                rel = str(full.relative_to(domain_path))
                if rel not in all_citations:
                    result["issues"].append({
                        "page": f"[{domain}]",
                        "detail": f"Uncited raw file: {rel}",
                    })

    return result


# ---------------------------------------------------------------------------
# Stage 5: Stale pages (DETERMINISTIC)
# ---------------------------------------------------------------------------

def stage_stale_pages(scope: str, mode: str = "full") -> dict:
    """Check for wiki pages last modified more than 90 days ago.

    DETERMINISTIC stage. Uses file modification time as the staleness signal.
    Stale pages are flagged for refresh but do not fail the gate (they are
    informational).

    Caveat: mtime is reset by a fresh clone or some checkout operations, so on a
    just-cloned repo this stage will report nothing stale until files age in
    place. For mtime-independent staleness, use `git log -1 --format=%cI` per
    page instead.

    Full mode: checks all pages.
    Delta mode: skipped (a newly created page cannot be stale).
    """
    result = {
        "stage": "stale-pages",
        "type": "deterministic",
        "passed": True,
        "issues": [],
    }

    if mode == "delta":
        return result

    if scope in DOMAINS:
        domains_to_check = [scope]
    else:
        domains_to_check = list(DOMAINS.keys())

    threshold = datetime.now(timezone.utc) - timedelta(
        days=STALE_THRESHOLD_DAYS
    )

    for domain in domains_to_check:
        for page in list_wiki_pages(domain):
            if page.stem == "index":
                continue
            mtime = datetime.fromtimestamp(
                page.stat().st_mtime, tz=timezone.utc
            )
            if mtime < threshold:
                age_days = (datetime.now(timezone.utc) - mtime).days
                result["issues"].append({
                    "page": page.stem,
                    "detail": (
                        f"Last modified {age_days} days ago "
                        f"(threshold: {STALE_THRESHOLD_DAYS})"
                    ),
                })

    return result


# ---------------------------------------------------------------------------
# Stage 6: Suggested new pages (JUDGMENT)
# ---------------------------------------------------------------------------

def stage_suggested_pages(scope: str, mode: str = "full") -> dict:
    """Suggest new pages from patterns in raw/ and cluster density.

    JUDGMENT stage. The mechanical proxy here is cluster page count:
    clusters with very few pages may warrant expansion. True topic-gap
    detection requires semantic reasoning and is marked "not evaluated."

    Full mode only. Delta mode returns "not applicable."
    """
    result = {
        "stage": "suggested-pages",
        "type": "judgment",
        "passed": True,
        "issues": [],
        "evaluated": False,
    }

    if mode == "delta":
        return result

    if scope in DOMAINS:
        domains_to_check = [scope]
    else:
        domains_to_check = list(DOMAINS.keys())

    for domain in domains_to_check:
        index_path = DOMAINS[domain] / "wiki" / "index.md"
        if not index_path.exists():
            continue

        text = index_path.read_text()
        current_cluster = None
        cluster_counts: dict[str, int] = {}

        for line in text.split("\n"):
            cluster_match = re.match(r"^##\s+(.+)$", line)
            if cluster_match:
                name = cluster_match.group(1).strip()
                if not name.lower().startswith("see also"):
                    current_cluster = name
                    cluster_counts[name] = 0
                else:
                    current_cluster = None
                continue

            if current_cluster and re.match(r"^-\s+\[\[", line):
                cluster_counts[current_cluster] = (
                    cluster_counts.get(current_cluster, 0) + 1
                )

        for cluster, count in cluster_counts.items():
            if count <= 1:
                result["issues"].append({
                    "page": f"[{domain}]",
                    "detail": (
                        f"Cluster '{cluster}' has only {count} page(s). "
                        f"Consider whether expansion is warranted."
                    ),
                })

    return result


# ---------------------------------------------------------------------------
# Stage 7: Connection candidates (JUDGMENT)
# ---------------------------------------------------------------------------

def stage_connection_candidates(scope: str, mode: str = "full") -> dict:
    """Find pages that share content but lack cross-links.

    JUDGMENT stage. Uses token overlap as a mechanical heuristic:
    pages with significant content overlap that do not link to each
    other are candidates. True connection relevance requires semantic
    understanding and is approximate.

    Full mode: checks all page pairs within each domain.
    Delta mode: not run (not part of the delta gate).
    """
    result = {
        "stage": "connection-candidates",
        "type": "judgment",
        "passed": True,
        "issues": [],
        "evaluated": True,
    }

    CONNECTION_THRESHOLD = 0.15

    if mode == "delta":
        # Not part of the delta gate
        result["evaluated"] = False
        return result

    if scope in DOMAINS:
        domains_to_check = [scope]
    else:
        domains_to_check = list(DOMAINS.keys())

    for domain in domains_to_check:
        pages = list_wiki_pages(domain)
        # Same shape as the contradiction stage: tokenize each page once, then run
        # the O(pages^2) all-pairs comparison over the cached token sets.
        page_data = []
        for p in pages:
            if p.stem == "index":
                continue
            text = p.read_text()
            page_data.append({
                "stem": p.stem,
                "tokens": tokenize(text),
                "links": set(extract_wikilinks(text)),
            })

        for i, pa in enumerate(page_data):
            for j, pb in enumerate(page_data):
                if i >= j:
                    continue

                if pb["stem"] in pa["links"] or pa["stem"] in pb["links"]:
                    continue

                cov = max(
                    draft_coverage(pa["tokens"], pb["tokens"]),
                    draft_coverage(pb["tokens"], pa["tokens"]),
                )
                if cov > CONNECTION_THRESHOLD:
                    result["issues"].append({
                        "page": f"{pa['stem']} <-> {pb['stem']}",
                        "detail": (
                            f"Consider linking [[{pa['stem']}]] and "
                            f"[[{pb['stem']}]] (coverage={cov:.2f})"
                        ),
                    })

    return result


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

# All 7 stages in the order defined by CLAUDE.md section 5
ALL_STAGES = [
    ("contradictions", stage_contradictions),
    ("broken-links", stage_broken_links),
    ("source-prudence", stage_source_prudence),
    ("coverage-gaps", stage_coverage_gaps),
    ("stale-pages", stage_stale_pages),
    ("suggested-pages", stage_suggested_pages),
    ("connection-candidates", stage_connection_candidates),
]

# Stages that run in delta mode (scoped to the changed page only).
# Per the spec: broken links, unsourced claims, format conformance,
# contradiction check. Coverage gaps, stale pages, suggested pages,
# and connection candidates are full-audit-only.
DELTA_STAGES = {"broken-links", "source-prudence", "contradictions"}

# Stages whose pass/fail can hard-gate a green promotion.
# Only deterministic stages gate; judgment stages degrade gracefully.
DETERMINISTIC_GATE_STAGES = {"broken-links", "source-prudence"}


def run_health_check(scope: str, mode: str = "full") -> dict:
    """Run the health check and return structured results.

    Args:
        scope: domain name or "all" (full mode), or page path (delta mode).
        mode: "full" for periodic whole-brain audit,
              "delta" for per-promotion gate on a single page.

    Returns:
        {
            "overall_pass": bool,
            "deterministic_pass": bool,  # the green gate signal
            "mode": str,
            "scope": str,
            "stages": [...],
            "issues": [...],
        }
    """
    if mode == "delta":
        stages_to_run = [
            (name, fn) for name, fn in ALL_STAGES
            if name in DELTA_STAGES
        ]
    else:
        stages_to_run = ALL_STAGES

    stage_results = []
    all_issues = []

    for stage_name, stage_fn in stages_to_run:
        stage_result = stage_fn(scope, mode=mode)
        stage_results.append(stage_result)
        for issue in stage_result.get("issues", []):
            issue["stage"] = stage_name
            all_issues.append(issue)

    # Deterministic pass: only deterministic gate stages that were run
    deterministic_results = [
        s for s in stage_results
        if s["stage"] in DETERMINISTIC_GATE_STAGES
    ]
    deterministic_pass = (
        all(s["passed"] for s in deterministic_results)
        if deterministic_results
        else True
    )

    overall_pass = all(s["passed"] for s in stage_results)

    return {
        "overall_pass": overall_pass,
        "deterministic_pass": deterministic_pass,
        "mode": mode,
        "scope": scope,
        "stages": stage_results,
        "issues": all_issues,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="7-stage health check for exobrain wiki domains"
    )
    parser.add_argument(
        "--domain",
        choices=list(DOMAINS.keys()),
        help="Run full audit on one domain (default: all domains)",
    )
    parser.add_argument(
        "--delta",
        metavar="PAGE_PATH",
        help="Run delta gate on a single page",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    if args.delta:
        mode = "delta"
        scope = args.delta
    else:
        mode = "full"
        scope = args.domain or "all"

    results = run_health_check(scope, mode=mode)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(f"Health Check ({mode} mode)")
        print(f"  Scope: {scope}")
        print(f"  Overall pass: {results['overall_pass']}")
        print(f"  Deterministic pass: {results['deterministic_pass']}")
        print()

        for stage in results["stages"]:
            status = "PASS" if stage["passed"] else "FAIL"
            evaluated = ""
            if not stage.get("evaluated", True):
                evaluated = " [not fully evaluated]"
            print(
                f"  [{status}] {stage['stage']} "
                f"({stage['type']}){evaluated}"
            )
            for issue in stage.get("issues", []):
                print(f"        {issue['page']}: {issue['detail']}")

        if results["issues"]:
            print(f"\n  Total issues: {len(results['issues'])}")

    sys.exit(0 if results["deterministic_pass"] else 1)


if __name__ == "__main__":
    main()
