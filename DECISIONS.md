# Design decisions

Short rationale for the choices most likely to raise an eyebrow on a read-through.
Each names the tradeoff honestly rather than only the upside.

## Files + Git as the store, not a database

The data is prose, and the access pattern is "read a page" and "compare two
pages." Markdown files plus Git already provide the store, the history, the diff,
and the audit trail with nothing to run or operate. A relational database would
add an operational dependency and a schema to maintain without making any of those
operations better. The cost of this choice is that aggregate queries over the
*content* aren't free — but the content isn't queried that way; it's read and
edited by a human. (The one place a relational shape *is* the right tool is the
evaluation metrics — see below — which is a different access pattern, not the
knowledge itself.)

## A SQLite metrics store *alongside* the file store

The "no database" decision above is about the knowledge. Evaluation metrics are a
different kind of data with a different access pattern: many runs over time, each
with per-case predictions, asked questions like "did accuracy move between the
last two runs?" and "what's the per-tier precision now?" Those are joins and
window functions — exactly what SQL is for, and awkward to express over flat
files. So eval runs are recorded to a small normalized SQLite store
(`eval/schema.sql`: `runs` × `cases` × `predictions`) and queried with CTEs and a
`LAG` window function (`eval/queries.sql`).

Honest tradeoff: in this repo the trend query is usually fed only the two runs
`make eval-db` records (baseline vs the stemming variant), so it's a small table
in practice. It earns its place for two reasons: the schema and queries are the
*right* shape for the question (they'd scale to many runs unchanged), and the
metrics path is genuinely separate from the file-based knowledge store — "files
for the knowledge, SQL for the measurements." It is also, deliberately, a place to
show SQL that isn't contrived: the natural eval questions are natively relational.

## The cloud-optional consolidation path is opt-in, not the default

`distill.py` (heuristic, offline, zero-dependency) is the default consolidation
path. `consolidate.py` + `memory_backend.py` are the model-driven analogue: a
Claude turn reorganizes material through the **memory tool**, with the application
executing the tool's file operations. It is opt-in (needs an API key), and exists
because the heuristic path is useful but doesn't *understand* the material.

Why it's ~330 lines for an optional path: most of it is `memory_backend.py`, a
faithful implementation of the memory tool's documented command contract
(view / create / str_replace / insert / delete / rename, the exact response and
error strings, and path-traversal protection) — that contract is non-trivial, and
implementing it correctly is the point. It carries no third-party dependencies
(stdlib `urllib`, like the rest of the repo), and it is tested without an API key
via an injected transport, so the optional path isn't an untested limb. The
tradeoff: it's real code on a path many readers won't run; it's kept honest by
being dependency-free, contract-faithful, and covered by `make test`.

## Pure standard library, no Anthropic SDK

The pipeline talks to the API over `urllib`. The dependency surface is then just
Python itself, so the tools run wherever Python ≥ 3.9 does, with nothing to
install. The cost is a little more code at the call site (manual request building,
no SDK helpers) — a worthwhile trade for a small single-user tool whose headline
property is "clone and run."

## Human-gated, with no auto-promotion path

No tool writes to `wiki/`. The gate classifies and *stages* proposals; a person
promotes them. This is enforced, not just intended: the gate has no wiki-write
code path, and the consolidation loop refuses a memory root that overlaps a
`wiki/`. The tradeoff is that the system can't curate itself end-to-end — which is
the point. The model's judgment is treated as a suggestion, never an action.
