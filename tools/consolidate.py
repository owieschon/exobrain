#!/usr/bin/env python3
"""Cloud-optional consolidation via Claude's memory tool (memory_20250818).

This is the model-driven analogue of distill.py: instead of the local heuristic,
a Claude turn reviews material and reorganizes it, using the memory tool to read
and write files through ``memory_backend.MemoryBackend``. It is OPTIONAL — the
stdlib pipeline is the zero-dependency default and exobrain runs fully without
this.

Two guarantees keep it consistent with the rest of the project:
  * It uses only the standard library (urllib), like every other tool here.
  * It NEVER targets a domain's wiki/. consolidate() refuses a memory root that
    overlaps a wiki/ (see _targets_wiki), so the human-gate invariant ("nothing
    reaches wiki/ without a human") is enforced, not just asserted.

Requires ANTHROPIC_API_KEY and a model that supports the memory tool. The live
agent loop is not exercised by the test suite (no key in CI); what *is* tested is
the memory backend it drives (see verify_memory_backend.py), the wiki guard
below, and that this degrades to a clear no-op without a key.

Reference: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from common import BRAIN_DIR, DOMAINS, get_api_key  # noqa: E402
from memory_backend import MemoryBackend  # noqa: E402

MEMORY_TOOL = {"type": "memory_20250818", "name": "memory"}
MODEL = "claude-opus-4-8"
MAX_TURNS = 12


def _targets_wiki(root) -> bool:
    """True if a memory root overlaps any domain's wiki/ (inside it, equal to it,
    or an ancestor that could create it). Used to enforce the human-gate
    invariant: consolidation must never write curated pages."""
    root = Path(root).resolve()
    for domain_path in DOMAINS.values():
        wiki = (domain_path / "wiki").resolve()
        if root == wiki or wiki in root.parents or root in wiki.parents:
            return True
    return False


def consolidate(root, prompt: str, model: str = MODEL, max_turns: int = MAX_TURNS):
    """Run one consolidation conversation, executing memory tool calls over ``root``.

    Returns the model's final text, or None if no API key is available (degrade,
    never crash — the same contract as common.call_anthropic).
    """
    if _targets_wiki(root):
        print(f"  Refusing: {root} overlaps a domain's wiki/. Consolidation never "
              "writes curated pages; point it at a staging directory.", file=sys.stderr)
        return None

    api_key = get_api_key()
    if not api_key:
        print("  No API key; skipping cloud consolidation (the local pipeline is the default).",
              file=sys.stderr)
        return None

    backend = MemoryBackend(root)
    messages = [{"role": "user", "content": prompt}]

    for _ in range(max_turns):
        resp = _post(api_key, model, messages)
        if resp is None:
            return None
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})

        if resp.get("stop_reason") != "tool_use":
            return "".join(b.get("text", "") for b in content if b.get("type") == "text")

        # Execute each memory tool call locally and feed the results back.
        results = []
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "memory":
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": backend.handle(block.get("input", {})),
                })
        if not results:
            return "".join(b.get("text", "") for b in content if b.get("type") == "text")
        messages.append({"role": "user", "content": results})

    print(f"  Reached the {max_turns}-turn cap without an end-of-turn.", file=sys.stderr)
    return None


def _post(api_key: str, model: str, messages: list):
    payload = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "messages": messages,
        "tools": [MEMORY_TOOL],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except Exception as exc:  # degrade, never crash
        print(f"  consolidation API error: {exc}", file=sys.stderr)
        return None


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Cloud-optional consolidation via the memory tool.")
    ap.add_argument("--root", default=str(BRAIN_DIR / "tools" / "staged"),
                    help="memory directory (a staging area; never a wiki/)")
    ap.add_argument("--prompt", default=(
        "Review the files in your memory directory. Merge duplicates and tidy them into "
        "clear, atomic notes. Do not invent content; only reorganize what is there."))
    args = ap.parse_args()
    out = consolidate(args.root, args.prompt)
    if out:
        print(out)


if __name__ == "__main__":
    main()
