#!/usr/bin/env python3
"""
Shared infrastructure for the exobrain tools.

Provides:
  - Path / domain resolution: BRAIN_DIR (the repo root, overridable with the
    BRAIN_DIR env var) and DOMAINS (discovered from the filesystem).
  - An Anthropic API client (get_api_key + call_anthropic) that returns None
    instead of raising when no key or network is available.
  - Token-similarity primitives (tokenize, jaccard, draft_coverage) and the
    NEGATION_SIGNALS / SUPERSEDE_SIGNALS phrase lists used by the gate.
"""

import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Paths. BRAIN_DIR defaults to the repo root (this file's grandparent) and can
# be overridden with the BRAIN_DIR env var, so the tools run from any checkout
# location or from a temporary directory under test.
# ---------------------------------------------------------------------------
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent          # .../tools/
_DEFAULT_BRAIN = _SCRIPT_DIR.parent                    # repo root

BRAIN_DIR = Path(os.environ.get("BRAIN_DIR", str(_DEFAULT_BRAIN)))


def discover_domains(brain_dir: Path = BRAIN_DIR) -> "dict[str, Path]":
    """Find knowledge domains by scanning BRAIN_DIR.

    A domain is any top-level subdirectory that contains both a ``wiki/`` and a
    ``raw/`` directory. Discovering domains from disk rather than hardcoding a
    list means the set can never drift from what is actually on disk, and the
    tools work for any domain a user creates without editing code.
    """
    found: "dict[str, Path]" = {}
    if not brain_dir.exists():
        return found
    for child in sorted(brain_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "wiki").is_dir() and (child / "raw").is_dir():
            found[child.name] = child
    return found


DOMAINS = discover_domains()


# ---------------------------------------------------------------------------
# Anthropic API client. Callers vary only in max_tokens and timeout, so those
# are parameters. Returns None (never raises) when no key or network is
# available, so every caller can treat None as "skip this step".
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = "claude-3-5-haiku-latest"


def get_api_key() -> Optional[str]:
    """Return the Anthropic API key from the ANTHROPIC_API_KEY env var, falling
    back to the macOS Keychain. Returns None if no key is available.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "anthropic-api-key", "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def call_anthropic(
    prompt: str,
    max_tokens: int,
    timeout: int = 30,
    error_prefix: str = "  API error",
) -> Optional[str]:
    """Call the Anthropic messages API. Returns the response text, or None on
    failure (no API key, or a transport/API error, which is logged to stderr
    with ``error_prefix``).

    Args:
        prompt: the user-message content.
        max_tokens: model max_tokens for the response.
        timeout: urlopen timeout in seconds.
        error_prefix: stderr label used when a transport error is logged.
    """
    api_key = get_api_key()
    if not api_key:
        return None

    import urllib.error
    import urllib.request

    payload = json.dumps(
        {
            "model": ANTHROPIC_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload.encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block["text"]
    except Exception as e:
        print(f"{error_prefix}: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Stopwords (filtered from token sets before comparison)
# ---------------------------------------------------------------------------

STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "just", "don", "now", "its", "it", "this",
    "that", "these", "those", "and", "but", "or", "if", "while", "about",
    "up", "out", "off", "over", "also", "which", "what", "who", "whom",
    "use", "used", "using", "one", "two", "new", "make", "like", "get",
})


# ---------------------------------------------------------------------------
# Contradiction / supersession signal lists
#
# IMPORTANT: generic negation words ("not", "don't", "never") are excluded
# because they appear in agreeing statements too ("should not assert" agrees
# with evidence-not-assertion). Only phrases that signal opposition or
# invalidation of an existing claim belong here.
# ---------------------------------------------------------------------------

NEGATION_SIGNALS = [
    "incorrect",
    "wrong",
    "false",
    "myth",
    "misconception",
    "outdated",
    "no longer",
    "superseded",
    "replaced by",
    "contrary to",
    "opposite of",
    "disproved",
    "insufficient",
    "fails to hold",
    "does not hold",
    "is not true",
    "is not correct",
]

SUPERSEDE_SIGNALS = [
    "actually sufficient",
    "in fact",
    "contrary to",
    "despite what",
    "turns out",
    "we found that",
    "correction:",
    "no longer true",
    "has changed since",
    "now we know",
    "replaces the earlier",
    "supersedes",
    "is outdated",
]


# ---------------------------------------------------------------------------
# Token similarity primitives
# ---------------------------------------------------------------------------

def tokenize(text: str) -> set:
    """Tokenize into a set of meaningful lowercase tokens (3+ chars, no stopwords)."""
    tokens = re.findall(r"[a-z][a-z0-9-]+", text.lower())
    return {t for t in tokens if t not in STOPWORDS and len(t) > 2}


def jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def draft_coverage(draft_tokens: set, page_tokens: set) -> float:
    """Fraction of the draft's tokens that also appear in the page.

    Asymmetric on purpose: it measures "is this draft about the same topic as
    this page" without being penalized when the page is much longer than the
    draft.
    """
    if not draft_tokens:
        return 0.0
    return len(draft_tokens & page_tokens) / len(draft_tokens)


def domain_signature(domain: str, max_chars: int = 20000) -> set:
    """Keyword signature for a domain, derived from its own wiki vocabulary.

    Tokenizes the domain's ``wiki/index.md`` plus its page filenames, so the
    question "which domain does this draft belong to" is answered from what the
    domain actually contains rather than a hardcoded keyword list. Returns an
    empty set for an unknown or empty domain.
    """
    path = DOMAINS.get(domain)
    if not path:
        return set()
    wiki = path / "wiki"
    if not wiki.is_dir():
        return set()
    parts = []
    index = wiki / "index.md"
    if index.exists():
        parts.append(index.read_text()[:max_chars])
    for page in sorted(wiki.glob("*.md")):
        parts.append(page.stem.replace("-", " "))
    return tokenize(" ".join(parts))
