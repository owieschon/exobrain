# exobrain

A plain-text, human-gated knowledge base with a small Python pipeline that
captures notes from your work, classifies them, and audits them for drift.
There is no database, no vector store, and no web app — just markdown files in
folders, with Git as the store and the audit trail. An LLM (Claude) does the
librarian work of drafting and comparing pages; a human approves everything that
becomes "knowledge".

> **Status:** a working personal tool, shared as a portfolio example. It runs and
> its two harnesses pass (see [Tests](#tests)), but it is single-user software,
> not a packaged product. The bundled `example/` domain is a demonstration —
> replace it with your own.

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
curated set) without a human saying yes. That single invariant is the point of
the design.

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
│   ├── session-start-hook.sh  surfaces the backlog into a Claude Code session
│   └── verify_*.py            the test harnesses
├── eval/cases.jsonl           labeled evaluation set (blind 3-rater consensus)
├── example/                   a demonstration domain (replace with your own)
│   ├── CLAUDE.md              the domain's operating manual
│   ├── writing-rules.md       how pages in this domain are written
│   ├── wiki/                  curated, cross-linked pages (the output)
│   ├── raw/                   the immutable junk drawer + session-captures/
│   └── outputs/               answers + health-checks/
├── EVALUATION.md              how the gate is measured, and its honest ceiling
├── Makefile                   `make check` = lint + tests; `make eval` = score
└── pyproject.toml             ruff config; no runtime dependencies
```

A **domain** is any top-level folder containing both a `wiki/` and a `raw/`
directory. The tools discover domains by scanning the repo, so adding one is
just creating folders — no code changes.

## Quickstart (about two minutes, no API key needed)

```bash
# 1. See the domains the tools discover (just `example` out of the box)
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

# 5. Run the tests (both harnesses; no extra install needed)
make test
```

(`make check` also runs `ruff`, which needs the dev extra:
`pip install -e '.[dev]'`.)

`distill.py` is the one tool that needs a Claude Code transcript history and an
API key, so it is not part of the keyless quickstart. With
`ANTHROPIC_API_KEY` set, `python3 tools/distill.py` reads your session
transcripts under `~/.claude` and writes capture drafts.

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

## Evaluation

The gate's tiering is a heuristic standing in for a semantic judgment, so it is
measured, not trusted. `make eval` scores it against `eval/cases.jsonl` — 35
cases labeled by three independent blind raters (consensus = ground truth).

The result is a clean boundary: **0.60 overall, but 10/10 where word overlap
tracks meaning (lexical overlap and contradiction) and 1/10 where it doesn't
(the semantic versions of each).** When a draft duplicates a page in different
words — "dogpile" vs. "thundering herd" — the matcher sees no overlap and waves
it through.

That is the ceiling of bag-of-words, not a tuning bug: a stemming variant was
measured and *regressed* it (0.60 → 0.57). The real fix is a semantic backend
(embeddings, or always-on LLM escalation), and the harness is already wired to
quantify it. The point isn't the score — it's that the score exists, surfaces
the real failure mode, and drove a decision on evidence.

Full write-up: **[EVALUATION.md](EVALUATION.md)**.

## Tests

Two assertion harnesses, run together by `make test`:

- `verify_auto_ingest.py` — builds a throwaway temp brain and asserts the
  GREEN/YELLOW/RED routing and the **no-wiki-write invariant**.
- `verify_health_check.py` — exercises the audit's deterministic and judgment
  stages, including degradation with no API key.

`make test` runs just the harnesses (no extra install). `make check` is
`make test` plus a `ruff` lint, so it additionally needs the dev extra
(`pip install -e '.[dev]'`). A GitHub Actions workflow
(`.github/workflows/ci.yml`) runs the tests and the lint on Python 3.9, 3.11,
and 3.13.

## Acknowledgements

The plain-folders-and-markdown approach is a common pattern for personal
knowledge bases; the pipeline, gate, and audit here are this project's own.
