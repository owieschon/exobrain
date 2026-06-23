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

A live run needs a normal ANTHROPIC_API_KEY (the memory tool is a standard API
tool, not a gated beta). The agent loop is injectable (``post_fn``) and is tested
against a simulated API in verify_memory_backend.py, so its orchestration is
verified with no key or network; a key only adds the real model round-trip. The
memory backend, the wiki guard, and the no-key degradation are tested too.

Reference: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
from common import (  # noqa: E402
    BRAIN_DIR,
    DOMAINS,
    LARGE_MODEL,
    get_api_key,
    log,
    trace_llm_call,
)
from memory_backend import MemoryBackend  # noqa: E402

MEMORY_TOOL = {"type": "memory_20250818", "name": "memory"}
MODEL = LARGE_MODEL
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


def consolidate(root, prompt: str, model: str = MODEL, max_turns: int = MAX_TURNS, post_fn=None):
    """Run one consolidation conversation, executing memory tool calls over ``root``.

    Returns the model's final text, or None if no API key is available (degrade,
    never crash — the same contract as common.call_anthropic).

    ``post_fn(api_key, model, messages) -> response_dict | None`` is injectable so
    the agent loop can be driven by a simulated API in tests, without a key or a
    network call (see verify_memory_backend.py). It defaults to the real client.
    """
    if _targets_wiki(root):
        log.warning("refusing: %s overlaps a domain's wiki/. Consolidation never "
                    "writes curated pages; point it at a staging directory.", root)
        return None

    api_key = get_api_key()
    if not api_key:
        log.warning("no API key; skipping cloud consolidation (the local pipeline is the default).")
        return None

    post = post_fn or _post
    backend = MemoryBackend(root)
    messages = [{"role": "user", "content": prompt}]

    for _ in range(max_turns):
        resp = post(api_key, model, messages)
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

    log.warning("reached the %d-turn cap without an end-of-turn.", max_turns)
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
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except Exception as exc:  # degrade, never crash
        log.warning("consolidation API error: %s", exc)
        trace_llm_call("consolidate", model, None,
                       (time.perf_counter() - start) * 1000, f"error:{type(exc).__name__}")
        return None
    trace_llm_call("consolidate", model, data.get("usage"),
                   (time.perf_counter() - start) * 1000, data.get("stop_reason", "ok"))
    return data


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
