# Consolidation: local-first, cloud-optional

Consolidation is the loop that turns scattered captures into organized notes —
reviewing recent material, merging duplicates, and tidying it. exobrain does this
two ways, and the local one is the default.

## Local (default, zero-dependency)

`distill.py` reads Claude Code session transcripts and extracts durable lessons
into `raw/session-captures/`; the gate (`auto_ingest.py`) then stages them for
review. This needs no third-party packages and degrades to a no-op without an API
key. It is heuristic: useful, but it doesn't *understand* the material.

## Cloud-optional (Claude's memory tool)

When an API key is present, a Claude turn can do the reorganizing itself through
the **memory tool** (`type: "memory_20250818"`). The memory tool is client-side:
the model issues `view` / `create` / `str_replace` / `insert` / `delete` /
`rename` calls against a `/memories` directory, and the application executes them
over storage it controls. exobrain is already a file store, so it implements that
backend directly:

- `tools/memory_backend.py` — a faithful implementation of the documented command
  contract, including the path-traversal protection the docs require. Pure stdlib,
  no network. Covered by `verify_memory_backend.py` (29 checks).
- `tools/consolidate.py` — the agent loop: it calls the Messages API (stdlib
  `urllib`, no SDK) with the memory tool enabled and runs each tool call through
  the backend until the model finishes.

```
python3 tools/consolidate.py --root <a staging dir>   # needs ANTHROPIC_API_KEY
```

### Two constraints that keep it consistent with the rest of the project

- **No new dependencies.** Like every other tool here, it uses `urllib`, not the
  Anthropic SDK.
- **It never targets a `wiki/`.** The memory directory is a staging area, so the
  human-gate invariant holds: consolidation produces material for review, never
  curated pages. Pointing the memory tool at a wiki would let the model auto-write
  curated content, which the gate exists to prevent.

## What is and isn't verified

The memory backend is tested end-to-end against the documented contract with no
API key (`make test`). The live agent loop in `consolidate.py` is **not** run by
the test suite — it needs a key and the memory-tool beta, which CI doesn't have —
so it is verified only against the documented request shape, and it degrades to a
clean no-op without a key (that path *is* tested). Running it against the live
API, and any tuning that follows, is left for an environment that has access.

## A note on "dreaming"

Anthropic's "dreaming" for managed agents is described as scheduled memory
consolidation between sessions. It is built on the same memory primitive used
here; the **memory tool** is the documented, stable API, so that is what this
integration targets rather than a separate "dreaming" endpoint (which the public
API docs do not expose as such). exobrain's local `distill` loop is the same idea
done heuristically and offline.

Reference:
[Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool).
