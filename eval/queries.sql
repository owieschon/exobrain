-- Analytical queries over the evaluation metrics store (see schema.sql).
-- Each block is a single statement, run and printed by `tools/eval_db.py`,
-- which splits on the whole-line "-- === Title ===" markers.
--
-- Targets SQLite. The CTEs and window functions are standard SQL; a Postgres
-- port needs numeric casts on the round() calls (Postgres has no
-- round(double, int)) and the IDENTITY change noted in schema.sql.

-- === Per-axis accuracy (latest run) ===
-- Where does the classifier do well or badly? Join each prediction in the most
-- recent run to its case and group by difficulty axis. The semantic axes sit
-- near the bottom, since word overlap cannot see those relationships.
WITH latest AS (
    SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1
)
SELECT
    c.axis,
    COUNT(*)                               AS cases,
    SUM(p.correct)                         AS correct,
    ROUND(AVG(p.correct), 2)               AS accuracy
FROM predictions p
JOIN cases c   ON c.case_id = p.case_id
JOIN latest l  ON l.run_id  = p.run_id
GROUP BY c.axis
ORDER BY accuracy ASC, c.axis;

-- === Accuracy trend across runs ===
-- Track every run over time and show the run-over-run change, so a regression
-- shows up immediately. LAG() reads the previous run's accuracy without a
-- self-join.
SELECT
    run_id,
    variant,
    git_sha,
    ROUND(accuracy, 3)                                          AS accuracy,
    ROUND(accuracy - LAG(accuracy) OVER (ORDER BY run_id), 3)   AS delta_vs_prev
FROM runs
ORDER BY run_id;

-- === Per-tier precision and recall (latest run) ===
-- Precision = of the drafts we called tier T, how many were T. Recall = of the
-- drafts that truly were T, how many we caught. Computed in one pass with
-- conditional sums; RED typically shows high precision but low recall (it only
-- catches contradictions that share vocabulary).
WITH latest AS (
    SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1
),
scored AS (
    SELECT p.predicted_tier, c.expected_tier
    FROM predictions p
    JOIN cases c  ON c.case_id = p.case_id
    JOIN latest l ON l.run_id  = p.run_id
),
tiers AS (SELECT 'GREEN' AS tier UNION SELECT 'YELLOW' UNION SELECT 'RED')
SELECT
    t.tier,
    SUM(CASE WHEN s.predicted_tier = t.tier AND s.expected_tier = t.tier THEN 1 ELSE 0 END) AS true_pos,
    ROUND(
        1.0 * SUM(CASE WHEN s.predicted_tier = t.tier AND s.expected_tier = t.tier THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN s.predicted_tier = t.tier THEN 1 ELSE 0 END), 0), 2)          AS prec,
    ROUND(
        1.0 * SUM(CASE WHEN s.predicted_tier = t.tier AND s.expected_tier = t.tier THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN s.expected_tier = t.tier THEN 1 ELSE 0 END), 0), 2)           AS recall
FROM tiers t
CROSS JOIN scored s
GROUP BY t.tier
ORDER BY t.tier;

-- === Confusion matrix (latest run) ===
-- The raw expected-vs-predicted counts, as rows. Reads as a matrix once pivoted
-- by the caller; kept long here so it stays plain SQL.
WITH latest AS (
    SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1
)
SELECT
    c.expected_tier,
    p.predicted_tier,
    COUNT(*) AS n
FROM predictions p
JOIN cases c  ON c.case_id = p.case_id
JOIN latest l ON l.run_id  = p.run_id
GROUP BY c.expected_tier, p.predicted_tier
ORDER BY c.expected_tier, p.predicted_tier;

-- === Cases that flipped between the two most recent runs ===
-- When a change helps one case but breaks another, accuracy alone hides it.
-- Rank runs newest-first, keep the last two, and surface every case whose
-- correctness changed — the detail you actually act on when iterating.
WITH ranked AS (
    SELECT run_id, ROW_NUMBER() OVER (ORDER BY run_id DESC) AS rn
    FROM runs
),
prev AS (SELECT run_id FROM ranked WHERE rn = 2),
curr AS (SELECT run_id FROM ranked WHERE rn = 1)
SELECT
    c.case_id,
    c.axis,
    pp.correct AS was_correct,
    cp.correct AS now_correct
FROM predictions cp
JOIN curr               ON curr.run_id = cp.run_id
JOIN predictions pp     ON pp.case_id  = cp.case_id
JOIN prev               ON prev.run_id = pp.run_id
JOIN cases c            ON c.case_id   = cp.case_id
WHERE pp.correct <> cp.correct
ORDER BY c.axis, c.case_id;
