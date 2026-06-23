# Architecture notes (for working in `tools/`)

Notes for anyone — human or AI — editing the pipeline code. The knowledge-base
operating manuals are the per-domain `CLAUDE.md` files; this file is about the
code. See `README.md` for the user-facing overview.

## What this codebase is
A plain-text, human-gated knowledge base (`<domain>/{raw,wiki,outputs}`) plus a
small Python pipeline that captures, gates, and audits its contents. No
database, no app. Git is the store and the audit trail.

## How it's organized
- `<domain>/raw/` is the immutable junk drawer; `<domain>/wiki/` is the curated,
  human-approved output; `<domain>/outputs/` holds answers + health-check reports.
- A **domain** is any top-level folder with both a `wiki/` and a `raw/` dir.
  `common.discover_domains()` finds them by scanning `BRAIN_DIR`, so the set of
  domains is never hardcoded and cannot drift from what is on disk.
- `tools/` is a one-way pipeline:
  - `distill.py` — Claude Code session transcripts → capture drafts in
    `raw/session-captures/`.
  - `auto_ingest.py` — **the gate.** Classifies each draft GREEN/YELLOW/RED and
    stages a proposal in `tools/staged/`, surfaced via `pending-ingest.txt`. It
    writes nothing to `wiki/`.
  - `health_check.py` — a 7-stage drift audit over a domain's wiki. Also offers
    a `--delta` mode to check a single page (e.g. before a human approves it).
- `common.py` is the shared base every tool imports.

## Conventions
- **Pure stdlib only.** `urllib`, not `requests`/`httpx`. No `anthropic` SDK.
- **Paths derive from `common.BRAIN_DIR`** (this file's repo root, overridable
  with the `BRAIN_DIR` env var). Do not hardcode `Path.home()`.
- **API calls go through `common.call_anthropic` / `get_api_key`** and degrade to
  `None` on no-key/no-network. Every caller treats `None` as "skip this step".
- **Diagnostics go through `common.log`** (the stdlib `exobrain` logger), not
  `print(..., file=sys.stderr)`; product output (reports, tiers) stays on stdout.
  Every model call is recorded via `common.trace_llm_call`, called at each of the
  two API call sites (`call_anthropic` and `consolidate._post`).
- **Runtime state is written atomically** (`.tmp` then `os.replace`) and is
  gitignored (`ingest-state.json`, `pending-*.txt`, `tools/staged/*`,
  `distilled-sessions.json`). Never commit runtime state or `.pyc`.
- **Capture/draft files use the exact markdown shape `auto_ingest.parse_draft`
  expects** (`# title`, `**Why it matters:**`, `**Suggested domain:**`,
  `## Lesson` + blank line). A new producer must match it or parsing breaks.
- **Each tool ships a `verify_*.py` harness;** "done" means `make check` passes.

## Reuse these (don't re-solve)
- `common.py`: `BRAIN_DIR`, `DOMAINS`, `discover_domains()`, `domain_signature()`,
  `get_api_key()`, `call_anthropic()`, `tokenize`, `jaccard`, `draft_coverage`,
  `fence_untrusted()`, `log`, `trace_llm_call()`, `STOPWORDS`, `NEGATION_SIGNALS`,
  `SUPERSEDE_SIGNALS`. Don't write a second API client, logger, domain list, or
  token-similarity helper — for plain text calls, use `call_anthropic`.
  (`consolidate._post` is the one deliberate exception: the memory-tool loop needs
  `tools=[...]`, which `call_anthropic` doesn't send. Both trace via `trace_llm_call`.)
- The gate is `auto_ingest.py`. New intake should land drafts in
  `raw/session-captures/` and let the gate tier them — don't build a second gate.
- Idempotency markers already exist: `ingest-state.json` (keyed by draft
  basename), `distilled-sessions.json` (keyed by transcript stem).

## Invariants (do not violate without flagging)
- `raw/` is immutable: read it, never edit or delete it.
- **Nothing reaches `wiki/` without a human.** There is no auto-promotion path:
  the gate only ever stages proposals for review. A tool must never write
  curated content.
- One canonical home per concept; other domains link `[[domain/slug]]`, never
  copy.
