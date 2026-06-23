# exobrain

A plain-text, human-gated knowledge base with a small Python pipeline that
captures notes from your work, classifies them, and audits them for drift.
There is no database, no vector store, and no web app — just markdown files in
folders, with Git as the store and the audit trail. An LLM (Claude) does the
librarian work of drafting and comparing pages; a human approves everything that
becomes "knowledge".

> **Status:** a working personal tool, shared as a portfolio example — single-user
> software, not a packaged product. The core is the capture→gate→curate→audit
> pipeline below; the [evaluation](#evaluation), the SQL metrics store, and the
> optional cloud [consolidation](CONSOLIDATION.md) are smaller pieces that serve
> it. The bundled `example/` domain is a demonstration — replace it with your own.

## The idea

Most notes rot because capturing them is friction and curating them never
happens. This tool splits the two:

```
        capture (cheap, automatic)            curate (deliberate, human-gated)
   ┌───────────────────────────────┐     ┌──────────────────────────────────────┐
   raw/  ──►  classify  ──►  staged/  ──►  human approves  ──►  wiki/  ──►  audit
  (dump)      (the gate)   (proposals)      (you)            (curated)   (drift check)
```

Anything can land in `raw/` (the junk drawer). Nothing reaches `wiki/` (the
curated set) without a human saying yes. That one invariant is what the rest of
the design protects.

## Architecture

The system has four parts: **sources → pipeline → storage → review surface.**

```
 SOURCES                  PIPELINE (tools/, pure-stdlib Python)              STORAGE (markdown + git)
 ─────────────            ─────────────────────────────────────             ────────────────────────
 Claude Code      ─┐      distill.py     read transcripts, extract           <domain>/
 session            │      (capture)      durable lessons ───────────────►     raw/session-captures/
 transcripts        │
 (~/.claude)        ├──►   auto_ingest.py classify each draft GREEN/          <domain>/
                    │      (the GATE)      YELLOW/RED, write a proposal ──►      tools/staged/   (proposals)
 files you drop   ─┘                       — never to wiki/
 into raw/
                          health_check.py 7-stage drift audit over a         <domain>/
                          (the AUDIT)      domain's curated pages ────────►     outputs/health-checks/

 REVIEW SURFACE: session-start-hook.sh surfaces the staged backlog into a new Claude Code session.
 HUMAN GATE:     a person promotes a staged proposal ──► <domain>/wiki/  (the only path into wiki/).
```

The three tools form a one-way pipeline and share one module:

| Tool | Role | Needs an API key? |
|------|------|-------------------|
| `distill.py` | Reads Claude Code session transcripts and extracts durable, transferable lessons into `raw/session-captures/`. | Yes (calls the model). Degrades to a no-op without one. |
| `auto_ingest.py` | **The gate.** Classifies each draft by how it relates to the existing wiki and stages a proposal for review. Writes nothing to `wiki/`. | Optional — only to break ties on ambiguous contradictions; works without. |
| `health_check.py` | **The audit.** A 7-stage drift check (broken links, contradictions, stale pages, coverage gaps, …). | Optional — deterministic stages always run; judgment stages degrade without a key. |
| `common.py` | Shared base: filesystem-derived domain discovery, the Anthropic client, token-similarity helpers. | — |

### Classification tiers

`auto_ingest.py` sorts each draft into one of three tiers. All three are staged
for a human; the tier only changes how the proposal is *presented*:

- **GREEN** — net-new, clear home, no conflicts → staged as a clean proposal.
- **YELLOW** — overlaps an existing page or has an ambiguous home → staged with
  the overlap flagged.
- **RED** — contradicts an existing page → staged as a decision packet
  (supersede the old page, or discard the draft).

## Repo layout

```
exobrain/
├── tools/                     the pipeline (pure-stdlib Python)
│   ├── common.py              shared base: domains, API client, similarity
│   ├── distill.py             transcripts → capture drafts
│   ├── auto_ingest.py         the gate: classify + stage for review
│   ├── health_check.py        the 7-stage drift audit
│   ├── eval.py                 scores the gate against the labeled dataset
│   ├── eval_db.py             runs the analytical queries over the metrics store
│   ├── memory_backend.py      Claude memory-tool backend over a directory
│   ├── consolidate.py        cloud-optional consolidation via the memory tool
│   ├── session-start-hook.sh  surfaces the backlog into a Claude Code session
│   └── verify_*.py            the test harnesses
├── eval/
│   ├── cases.jsonl            labeled evaluation set (LLM 3-rater consensus)
│   ├── schema.sql            metrics-store schema (runs × cases × predictions)
│   └── queries.sql           analytical queries (window functions, JOINs)
├── example/                   a demonstration domain (replace with your own)
│   ├── CLAUDE.md              the domain's operating manual
│   ├── writing-rules.md       how pages in this domain are written
│   ├── wiki/                  curated, cross-linked pages (the output)
│   ├── raw/                   the immutable junk drawer + session-captures/
│   └── outputs/               answers + health-checks/
├── EVALUATION.md              how the gate is measured, and its honest ceiling
├── CONSOLIDATION.md           local vs. cloud-optional (memory-tool) consolidation
├── Makefile                   `make check` = lint + tests; `make eval` = score
└── pyproject.toml             ruff config; no runtime dependencies
```

A **domain** is any top-level folder containing both a `wiki/` and a `raw/`
directory. The tools discover domains by scanning the repo, so adding one is
just creating folders — no code changes.

## Quickstart (about two minutes, no API key needed)

```bash
# 1. See the domains the tools discover (just `example` in a fresh checkout)
BRAIN_DIR="$PWD" python3 -c "import sys; sys.path.insert(0,'tools'); \
  from common import DOMAINS; print(list(DOMAINS))"

# 2. Run the gate over the sample capture in example/raw/session-captures/.
#    It classifies the draft and writes a proposal to tools/staged/ —
#    and nothing to wiki/.
BRAIN_DIR="$PWD" python3 tools/auto_ingest.py

# 3. Look at the staged proposal awaiting human approval
ls tools/staged/ && cat tools/staged/*.md

# 4. Run the drift audit over the example wiki
BRAIN_DIR="$PWD" python3 tools/health_check.py

# 5. Run the tests (all six harnesses; no extra install needed)
make test
```

`distill.py` is the one tool that needs a Claude Code transcript history and an
API key, so it is not part of the keyless quickstart. With
`ANTHROPIC_API_KEY` set, `python3 tools/distill.py` reads your session
transcripts under `~/.claude` and writes capture drafts. For a model-driven
alternative — Claude reorganizing material through its memory tool, instead of
the local heuristic — see [CONSOLIDATION.md](CONSOLIDATION.md).

## Promoting a proposal

The gate never writes to `wiki/` — you do. To promote a staged proposal:

1. Read the staged page and its companion `.meta` in `tools/staged/`.
2. Optionally lint it first:
   `python3 tools/health_check.py --delta tools/staged/<slug>.md`.
3. If you approve it, move it into the domain's wiki, adjust its cross-links and
   sources, and commit:
   ```bash
   mv tools/staged/<slug>.md example/wiki/<slug>.md
   git add example/wiki/<slug>.md && git commit -m "wiki: add <slug>"
   ```
4. To reject it, just delete its files from `tools/staged/`.

## Adding your own domain

```bash
mkdir -p mydomain/{raw/session-captures,wiki,outputs/health-checks}
cp example/CLAUDE.md example/writing-rules.md mydomain/   # then edit them
```

The tools pick up `mydomain/` automatically. Each domain is self-contained: its
own operating manual (`CLAUDE.md`), its own wiki, no bleed between domains.

## Design choices

- **No database.** The data is prose; the access pattern is "read a page" and
  "compare two pages". Markdown files plus Git cover the store, history, diff,
  and audit trail with nothing to run. A relational store would add operational
  weight without buying anything here.
- **Pure standard library.** The pipeline uses `urllib` rather than `requests`
  and talks to the API over plain HTTP — no third-party runtime dependencies, so
  it runs wherever Python ≥ 3.9 does.
- **Degrades, never crashes.** Every model call returns `None` instead of
  raising when there is no key or network, and each caller treats that as "skip
  this step". The deterministic parts keep working offline.
- **Human-gated.** No tool writes to `wiki/`. The gate only ever stages
  proposals; a person promotes them.

Fuller rationale — including why the SQL metrics store and the cloud-optional
consolidation path are worth their weight — is in [DECISIONS.md](DECISIONS.md).

## Security and trust boundaries

This is single-user, local-first software, but it does feed untrusted text to an
LLM, so the boundaries are worth stating plainly:

- **Prompt injection is a real surface, and the human gate is the backstop.**
  Session transcripts and existing wiki pages are untrusted input, and they reach
  the model (in `distill.py` and the gate's contradiction check). A crafted
  transcript or page could try to steer a summary or flip a YES/NO verdict. The
  containment is structural: nothing the model produces is auto-applied — every
  result is *staged for human review* (the gate never writes `wiki/`; the
  consolidation path refuses a `wiki/` root). As defense-in-depth, untrusted text
  is fenced and labelled "data, not instructions" before it enters a prompt
  (`common.fence_untrusted`). Fencing lowers the odds; the gate is what makes a
  successful injection harmless.
- **No secrets in the repo or its history.** The tools only *read* the API key
  (from the env or the macOS Keychain) — they never write or log it. An optional
  local `.env` is gitignored and must never be committed. Runtime state (captures,
  staged proposals, the metrics DB) is gitignored too.
- **SQL is parameterized or static** (`?` placeholders; no string-built queries).
- **The memory-tool backend is path-traversal hardened** — every path must
  resolve inside its root; `.resolve()` collapses both `..` and symlinks, and
  both escape cases are tested (`verify_memory_backend.py`).

What this does *not* do: multi-tenant isolation (it's single-user, one local
filesystem), or sandbox the optional consolidation agent's file operations within
its staging root beyond the wiki guard.

## Observability

Sized to what this is: a single-user, local-first CLI you run yourself. The goal
is to diagnose a run after the fact, not to operate a fleet — so the tooling is
stdlib and local, and adds no services and no dependencies.

- **Structured logging.** Diagnostics (errors, skipped steps) go through one
  `exobrain` logger to stderr. `EXOBRAIN_LOG_LEVEL` sets the level (default
  `WARNING`); `EXOBRAIN_LOG_JSON=1` switches to one JSON object per line. Product
  output — the eval report, the audit, per-draft tiers — stays on stdout, so a
  report still pipes cleanly regardless of log settings.
- **An LLM-call trace.** Every model call records one JSON line — step, model,
  input/output tokens, latency, outcome — through one shared seam
  (`common.trace_llm_call`), called at each of the two API call sites
  (`call_anthropic` and the consolidation loop's `_post`). Default sink is
  `tools/llm-trace.jsonl` (gitignored); set `EXOBRAIN_TRACE=<path>` to relocate
  it or `EXOBRAIN_TRACE=off` to disable. It records metadata only — never the
  key, the prompt, or the response body, so the trace can't leak content.

**Deliberately not added.** Sentry, LangSmith, and LangChain/LangGraph are the
wrong tools here: cloud error/trace services would send a single user's data
off-machine (against the local-first design), and a framework would be heavyweight
scaffolding around a small stdlib pipeline. For richer agent tracing of the
optional consolidation loop, `trace_llm_call` is the extension point — a local
Arize **Phoenix** / OpenTelemetry exporter wired in there (manual spans, since the
client is raw `urllib`, not an SDK) keeps traces on your machine. That belongs
behind an optional extra, not in the zero-dependency core.

## Evaluation

The gate's tiering is a heuristic standing in for a semantic judgment, so it is
measured, not trusted. `make eval` scores it against 35 labeled cases. The result
is a clean boundary: **0.60 deterministic — 10/10 where word overlap tracks
meaning, 1/10 where it doesn't.** With an API key, the small-model escalation
lifts it to **0.743**, closing the semantic-contradiction gap. The methodology,
the failure analysis, and a rejected stemming experiment are in
**[EVALUATION.md](EVALUATION.md)**.

### Metrics store (SQL)

Eval runs are the one genuinely relational thing here — a time series over the
same fixed cases — so they go in SQLite, not another flat file. `make eval-db`
records runs and prints the analytical queries in
[`eval/queries.sql`](eval/queries.sql): per-axis accuracy, a run-over-run accuracy
trend (a `LAG` window function), per-tier precision/recall, and the confusion
matrix. Why SQL here but files for the knowledge: **[DECISIONS.md](DECISIONS.md)**.

## Tests

Six stdlib assertion harnesses (`tools/verify_*.py`, ~120 checks) run by
`make test`, each building a throwaway temp brain where relevant. Between them
they cover the gate's GREEN/YELLOW/RED routing and its **no-wiki-write
invariant**, the audit's deterministic and key-optional stages, the memory-tool
contract with its path-traversal and symlink guards, the eval scorer and its SQL
queries, the distill producer→gate-parser round-trip, and the observability seam —
all with no API key (model calls degrade to a no-op).

`make check` adds a `ruff` lint (needs the dev extra, `pip install -e '.[dev]'`);
CI runs both on Python 3.9, 3.11, and 3.13.

## Acknowledgements

The plain-folders-and-markdown approach is a common pattern for personal
knowledge bases; the pipeline, gate, and audit here are this project's own.
