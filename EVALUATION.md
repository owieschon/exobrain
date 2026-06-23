# Evaluating the gate classifier

The gate (`auto_ingest.py`) decides whether a new draft is net-new (GREEN),
duplicates an existing page (YELLOW), or contradicts one (RED). It decides using
token-overlap heuristics (Jaccard + asymmetric coverage) with an optional LLM
escalation for ambiguous contradiction checks. Heuristics stand in for a
semantic judgment, so the question worth answering is: **where does the word
overlap stop tracking the meaning?**

This document is the answer, measured rather than asserted. Reproduce it with
`make eval`.

## Dataset

`eval/cases.jsonl` — 35 self-contained cases. Each carries its own fixture (one
or more domains with wiki pages) and a draft with an expected tier, so the
relationship under test is unambiguous.

The cases were built and labeled to avoid the obvious trap — me writing labels
that flatter my own classifier:

- **Generated** across seven difficulty axes (5 each): lexical vs. semantic
  overlap, lexical vs. semantic contradiction, clear net-new, net-new-with-shared-
  vocabulary noise, and ambiguous-domain.
- **Labeled blind by three independent raters** who judged each case by *meaning*
  (not word overlap) and never saw the proposed label. The ground-truth tier is
  their consensus. 33 of 35 were unanimous.
- **Adversarially vetted**: a final pass re-checked every label and looked for
  rigged, malformed, or duplicate cases. None were dropped.

Tier distribution: 16 YELLOW, 10 RED, 9 GREEN.

The semantic cases are the point of the set: e.g. a draft on "dogpiles" against a
page on "thundering herd", or "penalizing bushy models" against "regularization"
— the same concept with near-zero shared vocabulary.

## Baseline result (deterministic path, no API key)

**Accuracy: 0.60 (21/35).**

Confusion matrix (rows = expected, cols = predicted):

```
              GREEN   YELLOW      RED
   GREEN          8        1        0
   YELLOW         8        8        0
   RED            4        1        5
```

| tier | precision | recall | f1 | support |
|------|-----------|--------|----|---------|
| GREEN | 0.40 | 0.89 | 0.55 | 9 |
| YELLOW | 0.80 | 0.50 | 0.62 | 16 |
| RED | 1.00 | 0.50 | 0.67 | 10 |

Accuracy by axis:

| axis | accuracy |
|------|----------|
| lexical-overlap | **5/5** |
| lexical-contradiction | **5/5** |
| net-new-clear | **5/5** |
| ambiguous-domain | 3/5 |
| net-new-noise | 2/5 |
| **semantic-overlap** | **1/5** |
| **semantic-contradiction** | **0/5** |

## What this says

The heuristics are not weak — they are *exactly as strong as word overlap allows*:

- **Perfect on lexical relationships** (15/15). When two texts about the same
  thing share vocabulary, coverage + Jaccard nail it.
- **Near-blind to semantic ones** (1/10). When the same concept is phrased with
  different words, the matcher sees no overlap and defaults to GREEN. Every
  semantic contradiction is missed.
- This is why **RED precision is 1.00 but recall is 0.50**: when the classifier
  says "contradiction" it is always right, but it only catches the contradictions
  that announce themselves with shared words and negation language. And why
  **GREEN precision is only 0.40**: "no word overlap" is a reliable signal of
  "no *lexical* overlap", not of "genuinely new" — semantic duplicates and
  contradictions fall into the GREEN bucket.

The failure isn't a tuning problem. It's the ceiling of bag-of-words.

## Experiment: does lexical normalization help?

Hypothesis: if word *forms* are the issue, light suffix stemming
(`caching/cached → cach`) should recover some cases. Measured it (env-gated
variant, run with `EXOBRAIN_STEM=1 make eval`):

| variant | accuracy | net-new-noise | semantic-overlap | semantic-contradiction |
|---------|----------|---------------|------------------|------------------------|
| baseline | **0.60** | 2/5 | 1/5 | 0/5 |
| + stemming | 0.57 | 1/5 | 1/5 | 0/5 |

Stemming **regressed** it: collapsing word forms created spurious overlap (a
net-new draft now looked like a duplicate) while doing nothing for the semantic
cases, whose vocabularies don't overlap in any form. Decision: **not adopted.**
The shipped tokenizer stays simple, on the evidence — not on taste.

For reference, the variant tested was:

```python
def _stem(t):
    if len(t) > 5 and t.endswith("ing"): t = t[:-3]
    elif len(t) > 4 and t.endswith("ed"): t = t[:-2]
    elif len(t) > 4 and t.endswith("es"): t = t[:-2]
    elif len(t) > 4 and t.endswith("s") and not t.endswith("ss"): t = t[:-1]
    if len(t) > 4 and t.endswith("e"): t = t[:-1]
    return t
```

## The real lever, and why it isn't in this PR

Closing the semantic gap needs *semantic* similarity, which bag-of-words cannot
provide. Two paths, both real:

1. **Embeddings.** Replace (or back up) token overlap with cosine similarity over
   sentence embeddings. This breaks the project's zero-dependency, offline
   default, so it belongs behind an optional backend, not in the core.
2. **Always-available LLM escalation.** The classifier already escalates to a
   small model — but only in a *middle* band of lexical coverage
   (`HAIKU_AMBIGUITY_FLOOR < coverage < COVERAGE_OVERLAP`). Semantic cases have
   coverage ≈ 0, which is *below* the floor, so they never reach the model. The
   gate is keyed on the very signal that is failing. Making the GREEN-candidate
   path consult a semantic backend is the structural fix.

Both are deliberately out of scope here: I have no API key in this environment,
so I could write that path but not *measure* it, and an unmeasured "fix" is the
thing this whole document exists to avoid. The harness already reports whether
the LLM path was active, and the dataset is ready to quantify either approach the
moment a backend is wired in. (This is also where an optional
[Claude Dreaming](https://www.anthropic.com/engineering/managed-agents) /
managed-agent memory backend would plug in — see the project's local-first,
cloud-optional direction.)

## Reproduce

```bash
make eval                      # baseline, deterministic path
EXOBRAIN_STEM=1 make eval       # the stemming variant above (regression)
python3 tools/eval.py --json    # machine-readable summary
```

With `ANTHROPIC_API_KEY` set, the same command exercises the LLM-escalation path,
and the report's header says so — the numbers are never ambiguous about which
path produced them.
