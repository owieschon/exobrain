-- Metrics store for the gate classifier's evaluation runs.
--
-- The knowledge base itself is plain files (see the README); there is nothing
-- relational about a folder of markdown. Evaluation *results* are a
-- different shape: a time series of runs, each scoring the same fixed set of
-- cases, which you want to slice and trend. That is a relational workload, so it
-- lives here in SQL.
--
-- Targets SQLite (the project ships no services). The structure is standard SQL;
-- a Postgres port needs the INTEGER PRIMARY KEY changed to an IDENTITY column and
-- numeric casts on the round() calls in queries.sql. Foreign keys are declared
-- and enforced at write time (the writer sets PRAGMA foreign_keys=ON).

-- The dataset, one row per labeled case. Static across runs; the expected tier
-- is the blind-rater consensus (see EVALUATION.md).
CREATE TABLE IF NOT EXISTS cases (
    case_id       TEXT PRIMARY KEY,
    axis          TEXT NOT NULL,   -- difficulty axis, e.g. 'semantic-overlap'
    expected_tier TEXT NOT NULL    -- GREEN | YELLOW | RED
);

-- One row per evaluation run.
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY,
    created_at TEXT    NOT NULL,           -- ISO-8601 UTC
    git_sha    TEXT,                       -- commit the classifier was at
    variant    TEXT    NOT NULL DEFAULT 'baseline',  -- e.g. 'baseline', 'stem'
    llm_active INTEGER NOT NULL DEFAULT 0, -- 1 if the LLM-escalation path ran
    n_cases    INTEGER NOT NULL,
    accuracy   REAL    NOT NULL  -- cached aggregate of predictions.correct (kept so a run's
                                 -- headline survives even if its predictions are pruned)
);

-- One row per (run, case): what the classifier predicted, and whether it matched.
CREATE TABLE IF NOT EXISTS predictions (
    run_id         INTEGER NOT NULL REFERENCES runs(run_id),
    case_id        TEXT    NOT NULL REFERENCES cases(case_id),
    predicted_tier TEXT    NOT NULL,
    correct        INTEGER NOT NULL,  -- 1 if predicted_tier = expected_tier
    PRIMARY KEY (run_id, case_id)
);

CREATE INDEX IF NOT EXISTS idx_predictions_case ON predictions(case_id);
