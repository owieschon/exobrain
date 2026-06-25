# exobrain

Notes rot for two reasons that pull in opposite directions: capturing is friction, so you don't; and curating is work nobody schedules, so the captures you *do* make never become knowledge. Lower the capture friction and the junk drawer fills with unsorted, half-true fragments. Raise the curation bar and nothing gets in at all.

**exobrain splits the two so they stop fighting.** Capture is cheap and automatic — anything can land in `raw/`, the junk drawer, no questions asked. But the curated set — `wiki/`, the pages you'll later search and trust as settled fact — has exactly one entrance, and a human stands in it. A small stdlib-only Python pipeline does the librarian work in between: it reads my Claude Code sessions, drafts lessons, sorts each draft against what's already filed, and audits the whole set for drift. It proposes; it never promotes. The only path from `raw/` to `wiki/` is me saying yes.

That asymmetry is the whole design. The reason it matters is downstream: you search the wiki, act on the line that comes back, do that a hundred times, and the pages quietly become fact in your head. So the day a model writes one wrong line into the curated set, you won't audit it — you'll just trust it. The human gate is the thing that failure can't get past.

> Public, sanitized version of a tool I run for myself. Single-user, local-first: no services, no database for the notes — SQLite holds eval metrics, plain Markdown holds everything else. The bundled `example/` domain (three pages on Git practice) is a demo so the pipeline has something to chew on; replace the folder with your own.

## The pipeline: capture → gate → curate → audit

```
   raw/  ──►  gate  ──►  staged/  ──►  human yes  ──►  wiki/  ──►  drift audit
  (dump)   (classify)  (proposals)      (me)        (curated)   (health_check)
```

Three tools, each doing one job:

- **`distill.py` — capture.** Reads Claude Code session transcripts from `~/.claude/projects/`, extracts durable, transferable lessons, and writes them as drafts into a domain's `raw/session-captures/`. Caps at 5 distillations per run, fences each transcript as untrusted data before it reaches a prompt, and marks what it's seen so it doesn't re-distill. This is the one tool that needs `ANTHROPIC_API_KEY`; without a key it's a clean no-op.

- **`auto_ingest.py` — the gate.** Classifies each new draft against the existing wiki and **stages a proposal in `tools/staged/`, writing nothing to `wiki/`.** Sorts every draft GREEN / YELLOW / RED — net-new with a clear home, overlaps-an-existing-page, or contradicts-an-existing-page — and produces a clean proposal, an overlap-flagged proposal, or a supersede-or-discard decision packet accordingly. The tier controls *how the draft is presented for review*, never whether it's published; a human sees all three.

- **`health_check.py` — the audit.** A 7-stage drift sweep over a domain's wiki: contradictions, broken links & orphans, source prudence, coverage gaps, stale pages (90-day threshold), suggested new pages, and connection candidates. Runs as a full audit across all pages, or in `--delta` mode against a single staged page to lint it before approval.

## The invariant

**Nothing reaches `wiki/` without a human yes.** Not a convention or a prompt instruction — enforced in code and pinned by tests:

- The gate only ever writes to `tools/staged/`. `verify_auto_ingest.py` snapshots a domain's `wiki/` before a full gate run and asserts it is **unchanged** afterward (`before == after`). The gate can stage, flag, and packet drafts all day; the curated set does not move.
- The optional cloud consolidation path (`consolidate.py`) refuses any memory root that resolves inside, equals, or contains a domain's `wiki/` (`_targets_wiki`), so the same guard holds even when a model is doing the reorganizing — it produces material for review, never curated pages.

The model's role is bounded the same way everywhere: deterministic stages always run; the judgment stages (contradiction checks, coverage gaps, connection candidates) degrade gracefully without a key — they return "not evaluated," never a false pass, never a hard block. A missing key and an ambiguous call both resolve to *skip this step*, never to a fabricated answer.

## Run it (~2 min, no API key)

```bash
BRAIN_DIR="$PWD" python3 tools/auto_ingest.py   # gate the sample draft → tools/staged/, never wiki/
cat tools/staged/*.md                            # the staged proposal awaiting your yes
BRAIN_DIR="$PWD" python3 tools/health_check.py   # 7-stage drift audit over the example wiki
make test                                        # the verification harnesses, no install
```

A session-start hook (`tools/session-start-hook.sh`) surfaces what's pending — unfinished sessions to distill, and drafts staged for review — into the next Claude Code session, file reads only, no API calls.

## What's real

- **Zero runtime dependencies.** Stdlib only — `urllib` (not `requests`, no Anthropic SDK), `sqlite3`, `hashlib`, `re`. One dev dependency, `ruff`. Runs anywhere Python ≥ 3.9 does; CI is green on 3.9 / 3.11 / 3.13.
- **125 checks across 6 harnesses** via `make test`, each on a throwaway temp brain: `auto_ingest` 16, `health_check` 21, `memory_backend` 44, `observability` 14, `eval` 21, `distill` 9. The memory backend carries 44 because it hardens path traversal — `.resolve()` collapsing `..` and symlinks — and the escape cases are tested explicitly.
- **The gate is measured, not assumed.** `make eval` scores it against 35 labeled cases: **0.60 keyless** — strong where word overlap tracks meaning (RED precision 1.00), at the floor where it doesn't (semantic-contradiction 0/5). With a key, small-model escalation on the ambiguous-coverage band lifts it to **0.743** by closing exactly that gap (semantic-contradiction 0/5 → 5/5). The case labels are model-generated (33/35 unanimous across raters), not human ground truth — stated plainly because it bounds what the number means.
- **One JSON line per model call** (`common.trace_llm_call`): step, model, tokens, latency, outcome — metadata only, never the key, prompt, or response.

## Going deeper

- [EVALUATION.md](EVALUATION.md) — how the gate is scored, the failure analysis, and its honest bag-of-words ceiling.
- [DECISIONS.md](DECISIONS.md) — why files-and-Git over a database, SQLite only for metrics, no Sentry/LangChain.
- [CONSOLIDATION.md](CONSOLIDATION.md) — the local heuristic loop vs. the cloud-optional Claude memory-tool path, and its `wiki/` trust boundary.

Git is the store and the audit trail; every change to the curated set is a commit I made. The plain-folders-and-Markdown shape is a common one for personal knowledge bases. The gate, the 7-stage audit, and the tested no-write invariant are this project's own.
