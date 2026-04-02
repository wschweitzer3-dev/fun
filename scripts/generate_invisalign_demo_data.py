#!/usr/bin/env python3
"""Generate synthetic Invisalign provider-growth demo data in Databricks Delta tables."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql


DESTINATION = os.getenv("INVISALIGN_DESTINATION", "invisalign_demo")
PREFERRED_WAREHOUSE_NAME = os.getenv("DBSQL_WAREHOUSE_NAME", "Serverless Starter Warehouse")
WAREHOUSE_ID_OVERRIDE = os.getenv("DBSQL_WAREHOUSE_ID")
PROFILE = os.getenv("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
N_PROVIDERS = int(os.getenv("INVISALIGN_PROVIDER_COUNT", "1000"))


def parse_destination(dest: str) -> tuple[str | None, str]:
    if "." in dest:
        catalog, schema = dest.split(".", 1)
        return catalog, schema
    return None, dest


def pick_warehouse_id(w: WorkspaceClient) -> str:
    if WAREHOUSE_ID_OVERRIDE:
        return WAREHOUSE_ID_OVERRIDE

    warehouses = list(w.warehouses.list())
    if not warehouses:
        raise RuntimeError("No SQL warehouses are available for this Databricks workspace/profile.")

    for wh in warehouses:
        if wh.name == PREFERRED_WAREHOUSE_NAME:
            return wh.id

    return warehouses[0].id


def _collect_rows(resp: sql.StatementResponse) -> list[dict[str, Any]]:
    if not resp.result or not resp.result.data_array:
        return []
    columns = [c.name for c in resp.manifest.schema.columns]
    rows = []
    for row in resp.result.data_array:
        rows.append({columns[i]: row[i] for i in range(len(columns))})
    return rows


def run_sql(
    w: WorkspaceClient,
    warehouse_id: str,
    statement: str,
    title: str,
    max_wait_s: int = 600,
) -> list[dict[str, Any]]:
    print(f"\n== {title} ==")
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=statement,
        wait_timeout="50s",
        format=sql.Format.JSON_ARRAY,
    )

    deadline = time.time() + max_wait_s
    while resp.status and resp.status.state in {
        sql.StatementState.PENDING,
        sql.StatementState.RUNNING,
    }:
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for statement: {title}")
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)

    state = resp.status.state if resp.status else None
    if state != sql.StatementState.SUCCEEDED:
        error_msg = resp.status.error.message if resp.status and resp.status.error else "Unknown Databricks SQL error"
        raise RuntimeError(f"Statement failed ({title}): {error_msg}")

    rows = _collect_rows(resp)
    if rows:
        print(json.dumps(rows[:5], indent=2))
    else:
        print("OK")
    return rows


def main() -> None:
    w = WorkspaceClient(profile=PROFILE)
    warehouse_id = pick_warehouse_id(w)
    print(f"Using Databricks profile={PROFILE}, warehouse_id={warehouse_id}")

    catalog, schema = parse_destination(DESTINATION)
    if catalog is None:
        catalog_row = run_sql(
            w,
            warehouse_id,
            "SELECT current_catalog() AS current_catalog",
            "Resolve current catalog",
        )[0]
        catalog = catalog_row["current_catalog"]
    fq = f"{catalog}.{schema}"
    print(f"Target schema: {fq}")

    run_sql(w, warehouse_id, f"CREATE CATALOG IF NOT EXISTS {catalog}", "Create catalog if missing")
    run_sql(w, warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {fq}", "Create schema if missing")

    providers_sql = f"""
CREATE OR REPLACE TABLE {fq}.providers USING DELTA AS
WITH base AS (
  SELECT
    id AS provider_num,
    pmod(xxhash64(CAST(id AS STRING), 'r1'), 100000) / 100000.0 AS r1,
    pmod(xxhash64(CAST(id AS STRING), 'r2'), 100000) / 100000.0 AS r2,
    pmod(xxhash64(CAST(id AS STRING), 'r3'), 100000) / 100000.0 AS r3,
    pmod(xxhash64(CAST(id AS STRING), 'r4'), 100000) / 100000.0 AS r4,
    pmod(xxhash64(CAST(id AS STRING), 'r5'), 100000) / 100000.0 AS r5,
    pmod(xxhash64(CAST(id AS STRING), 'r6'), 100000) / 100000.0 AS r6
  FROM range(1, {N_PROVIDERS + 1})
),
typed AS (
  SELECT
    provider_num,
    CASE WHEN r1 < 0.40 THEN 'Orthodontist' ELSE 'GP Dentist' END AS provider_type,
    CASE
      WHEN r2 < 0.30 THEN 'Midwest'
      WHEN r2 < 0.55 THEN 'West'
      WHEN r2 < 0.80 THEN 'South'
      ELSE 'Northeast'
    END AS region,
    CASE
      WHEN r4 < 0.45 THEN 'Small'
      WHEN r4 < 0.80 THEN 'Medium'
      ELSE 'Large'
    END AS practice_size,
    r3,
    r5,
    r6
  FROM base
)
SELECT
  format_string('PRV%05d', provider_num) AS provider_id,
  provider_type,
  region,
  CASE
    WHEN provider_type = 'Orthodontist' THEN CAST(5 + floor(r3 * 26) AS INT)
    ELSE CAST(2 + floor(r3 * 22) AS INT)
  END AS years_experience,
  practice_size,
  CAST(
    ROUND(
      CASE
        WHEN provider_type = 'Orthodontist' THEN
          5300
          + (r5 * 2200)
          + CASE WHEN practice_size = 'Large' THEN 450 WHEN practice_size = 'Medium' THEN 250 ELSE 0 END
        ELSE
          3300
          + (r5 * 1700)
          + CASE WHEN practice_size = 'Large' THEN 300 WHEN practice_size = 'Medium' THEN 150 ELSE 0 END
      END,
      2
    ) AS DECIMAL(12, 2)
  ) AS avg_case_value,
  format_string('SR%03d', 1 + CAST(floor(r6 * 30) AS INT)) AS sales_rep_id,
  CAST(
    LEAST(
      100,
      GREATEST(
        0,
        ROUND(
          34
          + CASE WHEN provider_type = 'Orthodontist' THEN 9 ELSE 0 END
          + CASE WHEN practice_size = 'Large' THEN 12 WHEN practice_size = 'Medium' THEN 6 ELSE 0 END
          + CASE WHEN region = 'West' THEN 5 WHEN region = 'Northeast' THEN 3 WHEN region = 'South' THEN 2 ELSE 0 END
          + (r5 * 42),
          0
        )
      )
    ) AS INT
  ) AS digital_engagement_score
FROM typed
"""
    run_sql(w, warehouse_id, providers_sql, "Create providers")

    products_sql = f"""
CREATE OR REPLACE TABLE {fq}.products USING DELTA AS
SELECT
  product_id,
  product_name,
  product_tier,
  CAST(price_band AS DECIMAL(12, 2)) AS price_band,
  complexity_level
FROM VALUES
  ('P001', 'Invisalign Comprehensive', 'Comprehensive', 6500.00, 'High'),
  ('P002', 'Invisalign Lite',          'Lite',          4200.00, 'Medium'),
  ('P003', 'Invisalign Express',       'Express',       2500.00, 'Low'),
  ('P004', 'Invisalign Teen',          'Advanced',      5200.00, 'Medium-High'),
  ('P005', 'Invisalign Moderate',      'Standard',      3800.00, 'Medium')
AS t(product_id, product_name, product_tier, price_band, complexity_level)
"""
    run_sql(w, warehouse_id, products_sql, "Create products")

    provider_assignment_cte = f"""
provider_product_candidates AS (
  SELECT
    p.provider_id,
    pr.product_id,
    date_add(
      date_add(current_date(), -179),
      CAST(floor((pmod(xxhash64(p.provider_id, pr.product_id, 'adopt'), 100000) / 100000.0) * 60) AS INT)
    ) AS adoption_date,
    CASE
      WHEN p.provider_type = 'Orthodontist' AND pr.product_name = 'Invisalign Comprehensive' THEN 0.95
      WHEN p.provider_type = 'Orthodontist' AND pr.product_name = 'Invisalign Lite' THEN 0.72
      WHEN p.provider_type = 'Orthodontist' AND pr.product_name = 'Invisalign Express' THEN 0.33
      WHEN p.provider_type = 'Orthodontist' AND pr.product_name = 'Invisalign Teen' THEN 0.68
      WHEN p.provider_type = 'Orthodontist' AND pr.product_name = 'Invisalign Moderate' THEN 0.50
      WHEN p.provider_type = 'GP Dentist' AND pr.product_name = 'Invisalign Comprehensive' THEN 0.28
      WHEN p.provider_type = 'GP Dentist' AND pr.product_name = 'Invisalign Lite' THEN 0.81
      WHEN p.provider_type = 'GP Dentist' AND pr.product_name = 'Invisalign Express' THEN 0.72
      WHEN p.provider_type = 'GP Dentist' AND pr.product_name = 'Invisalign Teen' THEN 0.34
      ELSE 0.57
    END AS affinity,
    p.digital_engagement_score,
    pmod(xxhash64(p.provider_id, pr.product_id, 'noise'), 100000) / 100000.0 AS selection_noise,
    2 + CAST(floor((pmod(xxhash64(p.provider_id, 'target_count'), 100000) / 100000.0) * 4) AS INT) AS target_products
  FROM {fq}.providers p
  CROSS JOIN {fq}.products pr
),
provider_products_selected AS (
  SELECT provider_id, product_id, adoption_date
  FROM (
    SELECT
      provider_id,
      product_id,
      adoption_date,
      target_products,
      row_number() OVER (
        PARTITION BY provider_id
        ORDER BY (affinity + (digital_engagement_score / 100.0) * 0.12 + selection_noise * 0.18) DESC, product_id
      ) AS rn
    FROM provider_product_candidates
  )
  WHERE rn <= target_products
)
"""

    cases_sql = f"""
CREATE OR REPLACE TABLE {fq}.cases USING DELTA AS
WITH
{provider_assignment_cte},
day_span AS (
  SELECT explode(sequence(date_add(current_date(), -179), current_date(), interval 1 day)) AS case_date
),
base AS (
  SELECT
    pp.provider_id,
    pp.product_id,
    pp.adoption_date,
    ds.case_date,
    datediff(ds.case_date, date_add(current_date(), -179)) AS day_idx,
    p.provider_type,
    p.region,
    p.practice_size,
    p.years_experience,
    p.digital_engagement_score,
    pr.product_name,
    pr.product_tier,
    pr.price_band,
    pmod(xxhash64(pp.provider_id, pp.product_id, CAST(ds.case_date AS STRING), 'n1'), 100000) / 100000.0 AS n1,
    pmod(xxhash64(pp.provider_id, pp.product_id, CAST(ds.case_date AS STRING), 'n2'), 100000) / 100000.0 AS n2,
    pmod(xxhash64(pp.provider_id, pp.product_id, CAST(ds.case_date AS STRING), 'n3'), 100000) / 100000.0 AS n3
  FROM provider_products_selected pp
  JOIN {fq}.providers p ON p.provider_id = pp.provider_id
  JOIN {fq}.products pr ON pr.product_id = pp.product_id
  CROSS JOIN day_span ds
  WHERE ds.case_date >= pp.adoption_date
),
daily_expected AS (
  SELECT
    *,
    CASE WHEN provider_type = 'Orthodontist' THEN 0.16 ELSE 0.10 END AS base_rate,
    CASE practice_size WHEN 'Small' THEN 1.00 WHEN 'Medium' THEN 1.30 ELSE 1.70 END AS size_factor,
    CASE
      WHEN product_name = 'Invisalign Comprehensive' THEN 1.30
      WHEN product_name = 'Invisalign Lite' THEN 1.00
      WHEN product_name = 'Invisalign Express' THEN 0.75
      WHEN product_name = 'Invisalign Teen' THEN 1.08
      ELSE 0.90
    END AS product_factor,
    (0.82 + digital_engagement_score / 220.0) AS engagement_factor,
    (0.90 + least(years_experience, 30) / 90.0) AS experience_factor,
    CASE
      WHEN provider_type = 'GP Dentist' AND region = 'Midwest' THEN
        CASE
          WHEN day_idx < 120 THEN 1.00
          ELSE 0.88 - ((day_idx - 120) / 59.0) * 0.24
        END
      WHEN provider_type = 'Orthodontist' THEN 1.00 + (day_idx / 179.0) * 0.05
      ELSE 1.00 + (day_idx / 179.0) * 0.01
    END AS trend_factor,
    (0.94 + n3 * 0.12) AS seasonality
  FROM base
),
daily_counts AS (
  SELECT
    *,
    greatest(0.0, base_rate * size_factor * product_factor * engagement_factor * experience_factor * trend_factor * seasonality) AS expected_cases
  FROM daily_expected
),
daily_int AS (
  SELECT
    *,
    least(
      5,
      CAST(floor(expected_cases) AS INT)
      + CASE WHEN n1 < (expected_cases - floor(expected_cases)) THEN 1 ELSE 0 END
      + CASE WHEN n2 < least(0.12, expected_cases / 8.0) THEN 1 ELSE 0 END
    ) AS daily_cases
  FROM daily_counts
),
expanded AS (
  SELECT
    provider_id,
    product_id,
    case_date,
    product_name,
    product_tier,
    price_band,
    explode(sequence(1, daily_cases)) AS case_seq
  FROM daily_int
  WHERE daily_cases > 0
),
scored AS (
  SELECT
    *,
    pmod(xxhash64(provider_id, product_id, CAST(case_date AS STRING), CAST(case_seq AS STRING), 'age'), 100000) / 100000.0 AS r_age,
    pmod(xxhash64(provider_id, product_id, CAST(case_date AS STRING), CAST(case_seq AS STRING), 'cmp'), 100000) / 100000.0 AS r_cmp,
    pmod(xxhash64(provider_id, product_id, CAST(case_date AS STRING), CAST(case_seq AS STRING), 'sts'), 100000) / 100000.0 AS r_sts
  FROM expanded
),
labeled AS (
  SELECT
    provider_id,
    product_id,
    case_date,
    case_seq,
    price_band,
    CASE
      WHEN product_name = 'Invisalign Teen' THEN CASE WHEN r_age < 0.70 THEN 'Teen' ELSE 'Adult' END
      WHEN product_name = 'Invisalign Express' THEN CASE WHEN r_age < 0.15 THEN 'Teen' ELSE 'Adult' END
      WHEN product_name = 'Invisalign Comprehensive' THEN CASE WHEN r_age < 0.35 THEN 'Teen' ELSE 'Adult' END
      ELSE CASE WHEN r_age < 0.28 THEN 'Teen' ELSE 'Adult' END
    END AS patient_age_group,
    CASE
      WHEN product_name = 'Invisalign Comprehensive' THEN CASE WHEN r_cmp < 0.76 THEN 'High' ELSE 'Medium' END
      WHEN product_name = 'Invisalign Lite' THEN CASE WHEN r_cmp < 0.72 THEN 'Medium' ELSE 'Low' END
      WHEN product_name = 'Invisalign Express' THEN CASE WHEN r_cmp < 0.87 THEN 'Low' ELSE 'Medium' END
      WHEN product_name = 'Invisalign Teen' THEN CASE WHEN r_cmp < 0.80 THEN 'Medium' ELSE 'High' END
      ELSE CASE WHEN r_cmp < 0.82 THEN 'Medium' ELSE 'Low' END
    END AS complexity,
    CASE
      WHEN r_sts < 0.67 THEN 'Completed'
      WHEN r_sts < 0.93 THEN 'In Treatment'
      ELSE 'Refinement'
    END AS status
  FROM scored
)
SELECT
  format_string('CASE%08d', row_number() OVER (ORDER BY case_date, provider_id, product_id, case_seq)) AS case_id,
  provider_id,
  product_id,
  case_date,
  patient_age_group,
  complexity,
  status,
  CAST(price_band AS DECIMAL(12, 2)) AS revenue
FROM labeled
"""
    run_sql(w, warehouse_id, cases_sql, "Create cases")

    provider_products_sql = f"""
CREATE OR REPLACE TABLE {fq}.provider_products USING DELTA AS
WITH
{provider_assignment_cte},
case_90 AS (
  SELECT
    provider_id,
    product_id,
    SUM(CASE WHEN case_date >= date_add(current_date(), -89) THEN 1 ELSE 0 END) AS cases_last_90d
  FROM {fq}.cases
  GROUP BY provider_id, product_id
),
joined AS (
  SELECT
    pp.provider_id,
    pp.product_id,
    pp.adoption_date,
    COALESCE(c.cases_last_90d, 0) AS cases_last_90d,
    CAST(COALESCE(c.cases_last_90d, 0) * pr.price_band AS DECIMAL(14, 2)) AS revenue_last_90d
  FROM provider_products_selected pp
  JOIN {fq}.products pr ON pr.product_id = pp.product_id
  LEFT JOIN case_90 c
    ON c.provider_id = pp.provider_id
   AND c.product_id = pp.product_id
),
ranked AS (
  SELECT
    *,
    row_number() OVER (
      PARTITION BY provider_id
      ORDER BY revenue_last_90d DESC, cases_last_90d DESC, product_id
    ) AS revenue_rank
  FROM joined
)
SELECT
  provider_id,
  product_id,
  adoption_date,
  CASE
    WHEN cases_last_90d = 0 THEN 'Inactive'
    WHEN adoption_date >= date_add(current_date(), -45) THEN 'Pilot'
    ELSE 'Active'
  END AS status,
  cases_last_90d,
  revenue_last_90d,
  CASE WHEN revenue_rank = 1 THEN TRUE ELSE FALSE END AS is_primary
FROM ranked
"""
    run_sql(w, warehouse_id, provider_products_sql, "Create provider_products")

    propensity_sql = f"""
CREATE OR REPLACE TABLE {fq}.propensity_scores USING DELTA AS
WITH product_lookup AS (
  SELECT
    MAX(CASE WHEN product_name = 'Invisalign Comprehensive' THEN product_id END) AS comprehensive_id,
    MAX(CASE WHEN product_name = 'Invisalign Lite' THEN product_id END) AS lite_id,
    MAX(CASE WHEN product_name = 'Invisalign Teen' THEN product_id END) AS teen_id,
    MAX(CASE WHEN product_name = 'Invisalign Comprehensive' THEN price_band END) AS comprehensive_price,
    MAX(CASE WHEN product_name = 'Invisalign Lite' THEN price_band END) AS lite_price
  FROM {fq}.products
),
provider_stats AS (
  SELECT
    p.provider_id,
    p.provider_type,
    p.region,
    p.digital_engagement_score,
    pl.comprehensive_id,
    pl.teen_id,
    CAST(pl.comprehensive_price - pl.lite_price AS DECIMAL(12, 2)) AS lite_to_comp_price_diff,
    MAX(CASE WHEN pp.product_id = pl.comprehensive_id THEN 1 ELSE 0 END) AS has_comprehensive,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -59) THEN 1 ELSE 0 END) AS cases_recent_60d,
    SUM(
      CASE
        WHEN c.case_date >= date_add(current_date(), -119)
         AND c.case_date < date_add(current_date(), -59) THEN 1
        ELSE 0
      END
    ) AS cases_prior_60d,
    SUM(
      CASE
        WHEN c.case_date >= date_add(current_date(), -89)
         AND c.product_id = pl.lite_id THEN 1
        ELSE 0
      END
    ) AS lite_cases_90d
  FROM {fq}.providers p
  CROSS JOIN product_lookup pl
  LEFT JOIN {fq}.provider_products pp
    ON pp.provider_id = p.provider_id
  LEFT JOIN {fq}.cases c
    ON c.provider_id = p.provider_id
  GROUP BY
    p.provider_id,
    p.provider_type,
    p.region,
    p.digital_engagement_score,
    pl.comprehensive_id,
    pl.teen_id,
    pl.comprehensive_price,
    pl.lite_price
),
scored AS (
  SELECT
    *,
    ((cases_recent_60d - cases_prior_60d) / greatest(cases_prior_60d, 1.0)) AS trend_rate,
    greatest(
      0.01,
      least(
        0.99,
        0.18
        + (digital_engagement_score / 100.0) * 0.42
        + greatest(-0.3, least(0.5, ((cases_recent_60d - cases_prior_60d) / greatest(cases_prior_60d, 1.0)))) * 0.35
        + CASE WHEN has_comprehensive = 0 THEN 0.26 ELSE -0.14 END
        + CASE WHEN lite_cases_90d >= 18 THEN 0.09 WHEN lite_cases_90d >= 8 THEN 0.04 ELSE 0 END
        + CASE
            WHEN provider_type = 'GP Dentist' AND region = 'Midwest' AND cases_recent_60d < cases_prior_60d THEN -0.18
            ELSE 0
          END
        + CASE
            WHEN provider_type = 'Orthodontist' AND cases_recent_60d >= cases_prior_60d THEN 0.04
            ELSE 0
          END
      )
    ) AS propensity_score
  FROM provider_stats
)
SELECT
  provider_id,
  CASE WHEN has_comprehensive = 0 THEN comprehensive_id ELSE coalesce(teen_id, comprehensive_id) END AS next_best_product_id,
  CAST(round(propensity_score, 4) AS DECIMAL(6, 4)) AS propensity_score,
  CAST(
    round(
      greatest(0, round(lite_cases_90d * (0.08 + propensity_score * 0.42), 0)) * lite_to_comp_price_diff,
      2
    ) AS DECIMAL(14, 2)
  ) AS expected_incremental_revenue
FROM scored
"""
    run_sql(w, warehouse_id, propensity_sql, "Create propensity_scores")

    tables = ["providers", "products", "provider_products", "cases", "propensity_scores"]

    print("\n== Row counts ==")
    counts: dict[str, int] = {}
    for table in tables:
        rows = run_sql(
            w,
            warehouse_id,
            f"SELECT COUNT(*) AS row_count FROM {fq}.{table}",
            f"Count rows: {table}",
        )
        counts[table] = int(rows[0]["row_count"])
    print(json.dumps(counts, indent=2))

    print("\n== 5 sample rows per table ==")
    for table in tables:
        run_sql(
            w,
            warehouse_id,
            f"SELECT * FROM {fq}.{table} ORDER BY 1 LIMIT 5",
            f"Sample rows: {table}",
        )

    decline_check_sql = f"""
WITH base AS (
  SELECT
    p.provider_type,
    p.region,
    CASE
      WHEN c.case_date >= date_add(current_date(), -59) THEN 'last_60d'
      WHEN c.case_date >= date_add(current_date(), -119) AND c.case_date < date_add(current_date(), -59) THEN 'prior_60d'
      ELSE NULL
    END AS period
  FROM {fq}.cases c
  JOIN {fq}.providers p ON p.provider_id = c.provider_id
  WHERE c.case_date >= date_add(current_date(), -119)
),
agg AS (
  SELECT provider_type, region, period, COUNT(*) AS cases_cnt
  FROM base
  WHERE period IS NOT NULL
  GROUP BY provider_type, region, period
)
SELECT
  provider_type,
  region,
  SUM(CASE WHEN period = 'prior_60d' THEN cases_cnt ELSE 0 END) AS prior_60d_cases,
  SUM(CASE WHEN period = 'last_60d' THEN cases_cnt ELSE 0 END) AS last_60d_cases,
  ROUND(
    (
      SUM(CASE WHEN period = 'last_60d' THEN cases_cnt ELSE 0 END)
      - SUM(CASE WHEN period = 'prior_60d' THEN cases_cnt ELSE 0 END)
    ) / greatest(SUM(CASE WHEN period = 'prior_60d' THEN cases_cnt ELSE 0 END), 1) * 100.0,
    2
  ) AS pct_change
FROM agg
GROUP BY provider_type, region
ORDER BY provider_type, region
"""
    run_sql(w, warehouse_id, decline_check_sql, "Trend sanity check by provider_type + region")

    high_propensity_sql = f"""
WITH product_lookup AS (
  SELECT
    MAX(CASE WHEN product_name = 'Invisalign Comprehensive' THEN product_id END) AS comprehensive_id,
    MAX(CASE WHEN product_name = 'Invisalign Lite' THEN product_id END) AS lite_id
  FROM {fq}.products
),
provider_rollup AS (
  SELECT
    p.provider_id,
    p.provider_type,
    p.region,
    p.digital_engagement_score,
    MAX(CASE WHEN pp.product_id = pl.comprehensive_id THEN 1 ELSE 0 END) AS has_comprehensive,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -59) THEN 1 ELSE 0 END) AS recent_60d_cases,
    SUM(
      CASE
        WHEN c.case_date >= date_add(current_date(), -119)
         AND c.case_date < date_add(current_date(), -59) THEN 1
        ELSE 0
      END
    ) AS prior_60d_cases,
    SUM(
      CASE
        WHEN c.case_date >= date_add(current_date(), -89)
         AND c.product_id = pl.lite_id THEN 1
        ELSE 0
      END
    ) AS lite_cases_90d
  FROM {fq}.providers p
  CROSS JOIN product_lookup pl
  LEFT JOIN {fq}.provider_products pp ON pp.provider_id = p.provider_id
  LEFT JOIN {fq}.cases c ON c.provider_id = p.provider_id
  GROUP BY p.provider_id, p.provider_type, p.region, p.digital_engagement_score
)
SELECT
  ps.provider_id,
  r.provider_type,
  r.region,
  r.digital_engagement_score,
  r.has_comprehensive,
  r.recent_60d_cases,
  r.prior_60d_cases,
  ROUND((r.recent_60d_cases - r.prior_60d_cases) / greatest(r.prior_60d_cases, 1.0), 4) AS trend_rate,
  r.lite_cases_90d,
  ps.next_best_product_id,
  ps.propensity_score,
  ps.expected_incremental_revenue
FROM {fq}.propensity_scores ps
JOIN provider_rollup r ON r.provider_id = ps.provider_id
WHERE r.has_comprehensive = 0
ORDER BY ps.propensity_score DESC, ps.expected_incremental_revenue DESC
LIMIT 1
"""
    high = run_sql(w, warehouse_id, high_propensity_sql, "One high-propensity provider example")
    if high:
        example = high[0]
        print("\n== Why this provider is high propensity ==")
        reasons = []
        if int(example["has_comprehensive"]) == 0:
            reasons.append("Provider is not currently using Invisalign Comprehensive.")
        if float(example["digital_engagement_score"]) >= 70:
            reasons.append(
                f"Digital engagement is high ({example['digital_engagement_score']}/100), which raises expansion likelihood."
            )
        if float(example["trend_rate"]) > 0:
            reasons.append(
                f"Case volume is growing over the last 60 days vs prior 60 days (trend_rate={example['trend_rate']})."
            )
        if example["provider_type"] == "GP Dentist" and example["region"] == "Midwest":
            reasons.append("Provider is in the Midwest GP Dentist cohort, which receives a decline penalty.")
        else:
            reasons.append("Provider is outside the Midwest GP Dentist decline penalty cohort.")
        reasons.append(
            f"Lite case base in last 90 days ({example['lite_cases_90d']}) creates incremental revenue upside via Lite→Comprehensive conversion."
        )
        print(json.dumps(example, indent=2))
        for idx, reason in enumerate(reasons, start=1):
            print(f"{idx}. {reason}")


if __name__ == "__main__":
    main()
