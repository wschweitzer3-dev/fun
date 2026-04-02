-- Replace placeholders at runtime:
-- __EVAL_RESULTS_TABLE__, __EVAL_RUNS_TABLE__, __EVAL_INFRA_TABLE__

CREATE OR REPLACE VIEW __EVAL_RESULTS_TABLE___quality_trend AS
SELECT
  date_trunc('day', timestamp) AS day,
  scorer_name,
  AVG(score) AS avg_score,
  COUNT(*) AS scored_rows,
  SUM(CASE WHEN failure_bucket <> 'pass' THEN 1 ELSE 0 END) AS failing_rows
FROM __EVAL_RESULTS_TABLE__
GROUP BY date_trunc('day', timestamp), scorer_name
;

CREATE OR REPLACE VIEW __EVAL_RESULTS_TABLE___failure_rate_by_intent AS
SELECT
  date_trunc('day', timestamp) AS day,
  intent_type,
  COUNT(*) AS scored_rows,
  SUM(CASE WHEN failure_bucket <> 'pass' THEN 1 ELSE 0 END) AS failing_rows,
  CASE
    WHEN COUNT(*) = 0 THEN 0.0
    ELSE SUM(CASE WHEN failure_bucket <> 'pass' THEN 1 ELSE 0 END) / COUNT(*)
  END AS failure_rate
FROM __EVAL_RESULTS_TABLE__
GROUP BY date_trunc('day', timestamp), intent_type
;

CREATE OR REPLACE VIEW __EVAL_RESULTS_TABLE___top_failing_prompts AS
SELECT
  question_id,
  MAX(question) AS question,
  MAX(intent_type) AS intent_type,
  COUNT(*) AS failure_events,
  COLLECT_SET(failure_bucket) AS failure_buckets,
  COLLECT_SET(scorer_name) AS scorer_names,
  MAX(timestamp) AS last_seen
FROM __EVAL_RESULTS_TABLE__
WHERE failure_bucket <> 'pass'
GROUP BY question_id
ORDER BY failure_events DESC, last_seen DESC
;

CREATE OR REPLACE VIEW __EVAL_RESULTS_TABLE___latency_quality_correlation AS
SELECT
  date_trunc('day', timestamp) AS day,
  CASE
    WHEN latency_ms < 3000 THEN 'fast'
    WHEN latency_ms < 10000 THEN 'medium'
    ELSE 'slow'
  END AS latency_bucket,
  AVG(score) AS avg_score,
  COUNT(*) AS scored_rows
FROM __EVAL_RESULTS_TABLE__
WHERE latency_ms IS NOT NULL
GROUP BY date_trunc('day', timestamp),
  CASE
    WHEN latency_ms < 3000 THEN 'fast'
    WHEN latency_ms < 10000 THEN 'medium'
    ELSE 'slow'
  END
;

CREATE OR REPLACE VIEW __EVAL_RESULTS_TABLE___weekly_fix_opportunities AS
SELECT
  e.question_id,
  MAX(e.question) AS question,
  MAX(e.intent_type) AS intent_type,
  COUNT(*) AS quality_failures,
  MAX(e.timestamp) AS latest_failure_ts,
  COLLECT_SET(e.failure_bucket) AS buckets,
  COLLECT_SET(e.scorer_name) AS failing_scorers
FROM __EVAL_RESULTS_TABLE__ e
WHERE e.failure_bucket <> 'pass'
  AND e.timestamp >= date_sub(current_date(), 7)
GROUP BY e.question_id
ORDER BY quality_failures DESC, latest_failure_ts DESC
;

CREATE OR REPLACE VIEW __EVAL_RESULTS_TABLE___weekly_fix_opportunities_with_infra AS
SELECT
  q.question_id,
  q.question,
  q.intent_type,
  q.quality_failures,
  q.latest_failure_ts,
  q.buckets,
  q.failing_scorers,
  COALESCE(i.infra_failures, 0) AS infra_failures_last_7d
FROM __EVAL_RESULTS_TABLE___weekly_fix_opportunities q
LEFT JOIN (
  SELECT
    question_id,
    COUNT(*) AS infra_failures
  FROM __EVAL_INFRA_TABLE__
  WHERE timestamp >= date_sub(current_date(), 7)
  GROUP BY question_id
) i
  ON q.question_id = i.question_id
ORDER BY q.quality_failures DESC, infra_failures_last_7d DESC, q.latest_failure_ts DESC
;

CREATE OR REPLACE VIEW __EVAL_RUNS_TABLE___daily_baseline_deltas AS
WITH run_scores AS (
  SELECT
    date_trunc('day', timestamp) AS day,
    AVG(avg_score) AS daily_avg_score
  FROM __EVAL_RUNS_TABLE__
  GROUP BY date_trunc('day', timestamp)
),
rolling AS (
  SELECT
    day,
    daily_avg_score,
    AVG(daily_avg_score) OVER (
      ORDER BY day
      ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
    ) AS prior_14d_avg
  FROM run_scores
)
SELECT
  day,
  daily_avg_score,
  prior_14d_avg,
  CASE WHEN prior_14d_avg IS NULL THEN NULL ELSE daily_avg_score - prior_14d_avg END AS delta_vs_prior_14d
FROM rolling
ORDER BY day DESC
;

