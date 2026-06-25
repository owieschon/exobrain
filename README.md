# exobrain

Open the wiki, search a term, act on the line that comes back. Do that a hundred times and you stop re-checking — the notes have become settled fact in your head. So the day a model quietly writes one wrong line into that curated set, you won't catch it; you'll just trust it and act. That's the single failure this is built to prevent.

So the curated knowledge base — `wiki/` — is the one place a model is never allowed to write. A small stdlib Python pipeline captures notes from my work, classifies them, and audits the whole set for drift. The model drafts, sorts, and flags at the edges. The only path into the curated set is me saying yes.

The tool is useful and unreliable, so I build the trust around it instead of handing it the keys. exobrain is that stance applied to the thing I'd most regret getting silently wrong — what I think I know.

> Public, sanitized version of a tool I actually use. The bundled `example/` domain is a 3-page demonstration; swap in your own. SQLite for metrics, plain Markdown for everything else — no services to run.

## The invariant, and why I trust it

**Nothing reaches `wiki/` without a human yes.** Not a convention, not a prompt instruction — enforced two ways in code:

- The gate (`auto_ingest.py`) only ever writes proposals to `tools/staged/`. A test snapshots `wiki/` before and after a gate run and asserts it's **byte-identical** (`verify_auto_ingest.py`).
- The optional cloud consolidation path (`consolidate.py`) refuses any memory root that overlaps a `wiki/` directory (`_targets_wiki`), so the same guard holds even when a model is doing the reorganizing.

Anything can land in `raw/` (the junk drawer). The gate sorts each draft GREEN / YELLOW / RED and stages a proposal — clean, overlap-flagged, or a supersede-or-discard decision packet. I promote the ones I believe.

```
   raw/  ──►  gate  ──►  staged/  ──►  human yes  ──►  wiki/  ──►  drift audit
  (dump)   (classify)  (proposals)     (me)        (curated)   (health_check)
```

## Run it (~2 min, no API key)

```bash
BRAIN_DIR="$PWD" python3 tools/auto_ingest.py   # gate the sample draft → tools/staged/, never wiki/
cat tools/staged/*.md                            # the staged proposal awaiting approval
BRAIN_DIR="$PWD" python3 tools/health_check.py   # 7-stage drift audit over the example wiki
make test                                        # 6 stdlib harnesses, no install
```

`distill.py` is the one piece that needs `ANTHROPIC_API_KEY` — it reads Claude Code transcripts under `~/.claude` and writes capture drafts. Without a key it's a clean no-op, like every model call here: ambiguity and missing-key both resolve to "skip this step," never to a fabricated answer.

## What's real

- **Zero runtime dependencies.** Stdlib only — `urllib`, `sqlite3`, `hashlib`. One dev dependency (`ruff`). Runs wherever Python ≥ 3.9 does; CI is green on 3.9 / 3.11 / 3.13.
- **125 checks across 6 harnesses** (auto_ingest 16, health_check 21, memory_backend 44, observability 14, eval 21, distill 9), all via `make test`, each on a throwaway temp brain. The memory backend's path-traversal hardening (`.resolve()` collapses `..` and symlinks) has 44 of them, including the escape cases.
- **The gate is measured, not trusted.** `make eval` scores it against 35 cases: **0.60 keyless** — 10/10 where word overlap tracks meaning, near-zero where it doesn't. With a key, small-model escalation lifts it to **0.743** by closing the semantic-contradiction gap. The 35 case labels are model-generated (33/35 unanimous across raters), not human-truth — stated plainly because it bounds what the number means.
- **One JSON line per model call** (`common.trace_llm_call`): step, model, tokens, latency, outcome — metadata only, never the key, prompt, or response. Untrusted transcripts and pages are fenced as "data, not instructions" before they reach a prompt.

## Going deeper

- [EVALUATION.md](EVALUATION.md) — how the gate is scored, the failure analysis, and its honest ceiling.
- [DECISIONS.md](DECISIONS.md) — why files-and-Git over a database, why SQLite for metrics, why no Sentry/LangChain.
- [CONSOLIDATION.md](CONSOLIDATION.md) — the local heuristic vs. the cloud-optional memory-tool path, and its trust boundary.

One limitation, stated once: this is single-user, local-first software. No multi-tenant isolation, and the optional consolidation agent isn't sandboxed beyond the `wiki/` guard. That's the right size for a tool one person runs on one machine.

The plain-folders-and-Markdown idea is a common one for personal knowledge bases. The gate, the audit, and the tested no-write invariant are this project's own.
