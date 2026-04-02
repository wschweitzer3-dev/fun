#!/usr/bin/env python3
"""Create Genie + KA assets for Invisalign provider growth and run validation."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import timedelta
from typing import Any

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql as sql_svc


PROFILE = "DEFAULT"
SCHEMA_FQN = "ai_specialist.invisalign_demo"
GENIE_NAME = "Provider Growth Analytics"
KA_NAME = "Provider Growth Playbook KA"
KA_VOLUME_PATH = "/Volumes/ai_dev_kit/provider_growth_demo/raw_data"
WAREHOUSE_NAME = "Serverless Starter Warehouse"


GENIE_SAMPLE_QUESTIONS = [
    "Which Midwest providers are Tier 1 targets for Invisalign Comprehensive, and what revenue lift could that drive?",
    "Which GP Dentists are declining over the last 60 days, and how severe is the drop?",
    "What is the average case volume and revenue by provider type and region?",
    "Which providers have high engagement but are not using Comprehensive?",
    "What is the projected revenue impact of upgrading top 100 providers to Comprehensive?",
    "Which providers are outperforming benchmarks, and what do they have in common?",
    "Where is case volume declining the most by region and provider type?",
]

KA_SAMPLE_QUESTIONS = [
    "How should a rep upsell a GP Dentist from Invisalign Lite to Comprehensive?",
    "What is the best way to reactivate declining Midwest providers?",
    "How do we handle cost objections from providers?",
    "What messaging works for high-engagement providers who haven’t upgraded?",
    "What campaign should we run to recover GP Dentist case volume?",
]

VALIDATION_Q1 = "Which Midwest providers should we target for Comprehensive expansion, and what should reps say?"
VALIDATION_Q2 = "GP Dentist case volume is declining — how do we fix it?"


class BuilderError(RuntimeError):
    pass


def id32(seed: str) -> str:
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def split_sql_lines(sql: str) -> list[str]:
    return [f"{line.rstrip()}\n" for line in sql.strip().splitlines() if line.strip()]


def sanitize_name(name: str) -> str:
    s = name.replace(" ", "_")
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", s)
    s = re.sub(r"[_-]{2,}", "_", s).strip("_-")
    return s or "knowledge_assistant"


def choose_warehouse_id(w: WorkspaceClient, preferred_name: str = WAREHOUSE_NAME) -> str:
    whs = list(w.warehouses.list())
    if not whs:
        raise BuilderError("No SQL warehouses are available.")
    for wh in whs:
        if wh.name == preferred_name:
            return wh.id
    return whs[0].id


def http_json(
    w: WorkspaceClient,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    url = f"{w.config.host}{path}"
    resp = requests.request(method, url, headers=headers, json=body, params=params, timeout=timeout)
    if resp.status_code >= 400:
        raise BuilderError(
            f"{method} {path} failed with status {resp.status_code}: {resp.text}"
        )
    if not resp.text:
        return {}
    try:
        return resp.json()
    except Exception as e:
        raise BuilderError(f"{method} {path} returned non-JSON response: {e}") from e


def list_tiles(w: WorkspaceClient, tile_type: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        if tile_type:
            params["filter"] = f"tile_type={tile_type}"
        resp = http_json(w, "GET", "/api/2.0/tiles", params=params)
        out.extend(resp.get("tiles", []))
        page_token = resp.get("next_page_token")
        if not page_token:
            break
    return out


def quote_fqn(fqn: str) -> str:
    return ".".join(f"`{p}`" for p in fqn.split("."))


def provider_metrics_cte(schema_fqn: str) -> str:
    providers = quote_fqn(f"{schema_fqn}.providers")
    products = quote_fqn(f"{schema_fqn}.products")
    provider_products = quote_fqn(f"{schema_fqn}.provider_products")
    cases = quote_fqn(f"{schema_fqn}.cases")
    propensity = quote_fqn(f"{schema_fqn}.propensity_scores")
    return f"""
WITH case_rollup AS (
  SELECT
    c.provider_id,
    COUNT(c.case_id) AS total_cases,
    SUM(c.revenue) AS total_revenue,
    AVG(c.revenue) AS avg_case_value,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -29) THEN 1 ELSE 0 END) AS cases_last_30d,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -59) AND c.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END) AS cases_prior_30d,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -89) THEN 1 ELSE 0 END) AS cases_last_90d,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -29) THEN c.revenue ELSE 0 END) AS revenue_last_30d,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -89) THEN c.revenue ELSE 0 END) AS revenue_last_90d,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -59) THEN 1 ELSE 0 END) AS cases_last_60d,
    SUM(CASE WHEN c.case_date >= date_add(current_date(), -119) AND c.case_date < date_add(current_date(), -59) THEN 1 ELSE 0 END) AS cases_prior_60d,
    SUM(CASE WHEN pr.product_tier = 'Comprehensive' THEN 1 ELSE 0 END) AS comprehensive_cases,
    SUM(CASE WHEN pr.product_tier = 'Lite' THEN 1 ELSE 0 END) AS lite_cases,
    SUM(CASE WHEN pr.product_tier = 'Express' THEN 1 ELSE 0 END) AS express_cases
  FROM {cases} c
  JOIN {products} pr ON pr.product_id = c.product_id
  GROUP BY c.provider_id
),
provider_product_rollup AS (
  SELECT
    pp.provider_id,
    COUNT(*) AS product_count,
    MAX(CASE WHEN pr.product_tier = 'Comprehensive' THEN 1 ELSE 0 END) AS has_comprehensive_int,
    MAX(CASE WHEN pp.is_primary THEN pr.product_name ELSE NULL END) AS primary_product
  FROM {provider_products} pp
  JOIN {products} pr ON pr.product_id = pp.product_id
  GROUP BY pp.provider_id
),
base_metrics AS (
  SELECT
    p.provider_id,
    p.provider_type,
    p.region,
    p.digital_engagement_score,
    COALESCE(cr.total_cases, 0) AS total_cases,
    COALESCE(cr.total_revenue, 0) AS total_revenue,
    COALESCE(cr.avg_case_value, 0) AS avg_case_value,
    COALESCE(cr.cases_last_30d, 0) AS cases_last_30d,
    COALESCE(cr.cases_prior_30d, 0) AS cases_prior_30d,
    COALESCE(cr.cases_last_90d, 0) AS cases_last_90d,
    COALESCE(cr.revenue_last_30d, 0) AS revenue_last_30d,
    COALESCE(cr.revenue_last_90d, 0) AS revenue_last_90d,
    COALESCE(cr.cases_last_60d, 0) AS cases_last_60d,
    COALESCE(cr.cases_prior_60d, 0) AS cases_prior_60d,
    CAST(COALESCE(cr.comprehensive_cases, 0) AS DOUBLE) / NULLIF(COALESCE(cr.total_cases, 0), 0) AS comprehensive_case_share,
    CAST(COALESCE(cr.lite_cases, 0) AS DOUBLE) / NULLIF(COALESCE(cr.total_cases, 0), 0) AS lite_case_share,
    CAST(COALESCE(cr.express_cases, 0) AS DOUBLE) / NULLIF(COALESCE(cr.total_cases, 0), 0) AS express_case_share,
    COALESCE(ppr.product_count, 0) AS product_count,
    CASE WHEN COALESCE(ppr.has_comprehensive_int, 0) = 1 THEN TRUE ELSE FALSE END AS has_comprehensive,
    ppr.primary_product,
    COALESCE(ps.propensity_score, 0) AS propensity_score,
    COALESCE(ps.expected_incremental_revenue, 0) AS expected_incremental_revenue
  FROM {providers} p
  LEFT JOIN case_rollup cr ON cr.provider_id = p.provider_id
  LEFT JOIN provider_product_rollup ppr ON ppr.provider_id = p.provider_id
  LEFT JOIN {propensity} ps ON ps.provider_id = p.provider_id
),
provider_metrics AS (
  SELECT
    *,
    (cases_last_30d - cases_prior_30d) / NULLIF(CAST(cases_prior_30d AS DOUBLE), 0) AS case_growth_rate_30d,
    (cases_last_60d - cases_prior_60d) / NULLIF(CAST(cases_prior_60d AS DOUBLE), 0) AS case_growth_rate_60d,
    CASE WHEN has_comprehensive THEN FALSE ELSE TRUE END AS upgrade_opportunity_flag,
    CASE WHEN propensity_score >= 0.6 THEN TRUE ELSE FALSE END AS high_propensity_flag,
    CASE WHEN provider_type = 'Orthodontist' AND cases_last_30d > 50 THEN TRUE ELSE FALSE END AS orthodontist_high_performer,
    CASE WHEN provider_type = 'GP Dentist' AND cases_last_30d > 15 THEN TRUE ELSE FALSE END AS gp_dentist_high_performer,
    CASE WHEN ((cases_last_30d - cases_prior_30d) / NULLIF(CAST(cases_prior_30d AS DOUBLE), 0)) > 0.05 THEN TRUE ELSE FALSE END AS growing_provider,
    CASE WHEN ((cases_last_60d - cases_prior_60d) / NULLIF(CAST(cases_prior_60d AS DOUBLE), 0)) < -0.15 THEN TRUE ELSE FALSE END AS declining_provider
  FROM base_metrics
),
provider_metrics_with_flags AS (
  SELECT
    *,
    declining_provider AS at_risk_flag,
    CASE
      WHEN high_propensity_flag AND (NOT has_comprehensive) AND growing_provider THEN TRUE
      ELSE FALSE
    END AS tier_1_target,
    CASE
      WHEN high_propensity_flag AND (NOT has_comprehensive) THEN TRUE
      ELSE FALSE
    END AS tier_2_target
  FROM provider_metrics
)
"""


def build_genie_config(schema_fqn: str) -> dict[str, Any]:
    cte = provider_metrics_cte(schema_fqn)
    q1_sql = f"""
{cte}
SELECT
  provider_id,
  provider_type,
  region,
  cases_last_30d,
  case_growth_rate_30d,
  propensity_score,
  expected_incremental_revenue AS estimated_revenue_lift
FROM provider_metrics_with_flags
WHERE region = 'Midwest' AND tier_1_target
ORDER BY estimated_revenue_lift DESC, propensity_score DESC
LIMIT 100
"""
    q2_sql = f"""
{cte}
SELECT
  provider_id,
  region,
  cases_prior_60d,
  cases_last_60d,
  case_growth_rate_60d
FROM provider_metrics_with_flags
WHERE provider_type = 'GP Dentist' AND declining_provider
ORDER BY case_growth_rate_60d ASC, cases_last_60d DESC
LIMIT 100
"""
    q3_sql = f"""
{cte}
SELECT
  provider_type,
  region,
  AVG(total_cases) AS avg_case_volume,
  AVG(total_revenue) AS avg_revenue
FROM provider_metrics_with_flags
GROUP BY provider_type, region
ORDER BY avg_revenue DESC
"""
    q4_sql = f"""
{cte}
SELECT
  provider_id,
  provider_type,
  region,
  digital_engagement_score,
  cases_last_30d,
  propensity_score,
  expected_incremental_revenue
FROM provider_metrics_with_flags
WHERE digital_engagement_score >= 70 AND NOT has_comprehensive
ORDER BY propensity_score DESC, expected_incremental_revenue DESC
LIMIT 200
"""
    q5_sql = f"""
{cte}
SELECT
  SUM(expected_incremental_revenue) AS projected_revenue_impact_top_100,
  AVG(expected_incremental_revenue) AS avg_revenue_impact_per_provider,
  COUNT(*) AS provider_count
FROM (
  SELECT provider_id, expected_incremental_revenue
  FROM provider_metrics_with_flags
  WHERE NOT has_comprehensive
  ORDER BY expected_incremental_revenue DESC, propensity_score DESC
  LIMIT 100
) t
"""
    q6_sql = f"""
{cte}
SELECT
  provider_type,
  region,
  primary_product,
  COUNT(*) AS provider_count,
  AVG(digital_engagement_score) AS avg_digital_engagement,
  AVG(propensity_score) AS avg_propensity,
  AVG(case_growth_rate_30d) AS avg_growth_30d
FROM provider_metrics_with_flags
WHERE orthodontist_high_performer OR gp_dentist_high_performer
GROUP BY provider_type, region, primary_product
ORDER BY provider_count DESC, avg_growth_30d DESC
"""
    q7_sql = f"""
{cte}
SELECT
  provider_type,
  region,
  SUM(cases_prior_60d) AS cases_prior_60d,
  SUM(cases_last_60d) AS cases_last_60d,
  (SUM(cases_last_60d) - SUM(cases_prior_60d)) / NULLIF(CAST(SUM(cases_prior_60d) AS DOUBLE), 0) AS case_growth_rate_60d
FROM provider_metrics_with_flags
GROUP BY provider_type, region
ORDER BY case_growth_rate_60d ASC, cases_last_60d DESC
"""

    tables = [
        f"{schema_fqn}.cases",
        f"{schema_fqn}.products",
        f"{schema_fqn}.propensity_scores",
        f"{schema_fqn}.provider_products",
        f"{schema_fqn}.providers",
    ]
    tables.sort()

    text_instruction = (
        "SCOPE:\n"
        "- Use ONLY ai_specialist.invisalign_demo.providers, products, provider_products, cases, propensity_scores.\n"
        "- Build answers at provider grain using separate rollups (case_rollup, provider_product_rollup) before joining to avoid duplicate counting.\n"
        "SEMANTIC METRICS:\n"
        "- total_cases=COUNT(case_id), total_revenue=SUM(revenue), avg_case_value=AVG(revenue).\n"
        "- cases_last_30d / cases_prior_30d / cases_last_90d / revenue_last_30d / revenue_last_90d from case_date windows.\n"
        "- case_growth_rate_30d=(cases_last_30d-cases_prior_30d)/cases_prior_30d.\n"
        "- case_growth_rate_60d=(cases_last_60d-cases_prior_60d)/cases_prior_60d.\n"
        "- comprehensive_case_share, lite_case_share, express_case_share are tier-specific case ratios.\n"
        "- product_count from provider_products; has_comprehensive from any Comprehensive adoption; primary_product from is_primary=true row.\n"
        "- upgrade_opportunity_flag is true when has_comprehensive is false.\n"
        "- high_propensity_flag is true when propensity_score>=0.6.\n"
        "BENCHMARK FLAGS:\n"
        "- orthodontist_high_performer: provider_type='Orthodontist' and cases_last_30d>50.\n"
        "- gp_dentist_high_performer: provider_type='GP Dentist' and cases_last_30d>15.\n"
        "- growing_provider: case_growth_rate_30d>0.05.\n"
        "- declining_provider: case_growth_rate_60d<-0.15.\n"
        "- at_risk_flag=declining_provider.\n"
        "- tier_1_target: high_propensity_flag AND not has_comprehensive AND growing_provider.\n"
        "- tier_2_target: high_propensity_flag AND not has_comprehensive.\n"
        "OUTPUT RULES:\n"
        "- Return ranked outputs with explicit numeric metrics.\n"
        "- Include expected_incremental_revenue when discussing upgrade opportunities.\n"
        "- Prefer provider-level prioritization with clear sort order and limits.\n"
    )

    example_sqls = [
        (GENIE_SAMPLE_QUESTIONS[0], q1_sql, "Use for Tier 1 Midwest upgrade prioritization with estimated revenue lift."),
        (GENIE_SAMPLE_QUESTIONS[1], q2_sql, "Use for GP Dentist decline diagnosis over the last 60 days."),
        (GENIE_SAMPLE_QUESTIONS[2], q3_sql, "Use for provider-type and region performance benchmarking."),
        (GENIE_SAMPLE_QUESTIONS[3], q4_sql, "Use for high-engagement non-Comprehensive expansion targeting."),
        (GENIE_SAMPLE_QUESTIONS[4], q5_sql, "Use for top-100 Comprehensive upgrade revenue forecasting."),
        (GENIE_SAMPLE_QUESTIONS[5], q6_sql, "Use for benchmark outperformer pattern mining."),
        (GENIE_SAMPLE_QUESTIONS[6], q7_sql, "Use for region/provider_type decline severity ranking."),
    ]

    measures = [
        ("total_cases", "COUNT(cases.case_id)", "Total case count."),
        ("total_revenue", "SUM(cases.revenue)", "Total case revenue."),
        ("avg_case_value", "AVG(cases.revenue)", "Average case value."),
        ("cases_last_30d", "SUM(CASE WHEN cases.case_date >= date_add(current_date(), -29) THEN 1 ELSE 0 END)", "Cases in last 30 days."),
        ("cases_prior_30d", "SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) AND cases.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END)", "Cases in prior 30 days."),
        ("cases_last_90d", "SUM(CASE WHEN cases.case_date >= date_add(current_date(), -89) THEN 1 ELSE 0 END)", "Cases in last 90 days."),
        ("revenue_last_30d", "SUM(CASE WHEN cases.case_date >= date_add(current_date(), -29) THEN cases.revenue ELSE 0 END)", "Revenue in last 30 days."),
        ("revenue_last_90d", "SUM(CASE WHEN cases.case_date >= date_add(current_date(), -89) THEN cases.revenue ELSE 0 END)", "Revenue in last 90 days."),
        ("case_growth_rate_30d", "(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -29) THEN 1 ELSE 0 END) - SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) AND cases.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END)) / NULLIF(CAST(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) AND cases.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END) AS DOUBLE), 0)", "30-day case growth."),
        ("case_growth_rate_60d", "(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) THEN 1 ELSE 0 END) - SUM(CASE WHEN cases.case_date >= date_add(current_date(), -119) AND cases.case_date < date_add(current_date(), -59) THEN 1 ELSE 0 END)) / NULLIF(CAST(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -119) AND cases.case_date < date_add(current_date(), -59) THEN 1 ELSE 0 END) AS DOUBLE), 0)", "60-day case growth."),
        ("comprehensive_case_share", "SUM(CASE WHEN products.product_tier = 'Comprehensive' THEN 1 ELSE 0 END) / NULLIF(CAST(COUNT(cases.case_id) AS DOUBLE), 0)", "Comprehensive case share."),
        ("lite_case_share", "SUM(CASE WHEN products.product_tier = 'Lite' THEN 1 ELSE 0 END) / NULLIF(CAST(COUNT(cases.case_id) AS DOUBLE), 0)", "Lite case share."),
        ("express_case_share", "SUM(CASE WHEN products.product_tier = 'Express' THEN 1 ELSE 0 END) / NULLIF(CAST(COUNT(cases.case_id) AS DOUBLE), 0)", "Express case share."),
        ("product_count", "COUNT(DISTINCT provider_products.product_id)", "Adopted product count."),
        ("has_comprehensive", "MAX(CASE WHEN products.product_tier = 'Comprehensive' THEN 1 ELSE 0 END)", "Binary Comprehensive adoption indicator."),
    ]

    expressions = [
        ("upgrade_opportunity_flag", "CASE WHEN MAX(CASE WHEN products.product_tier = 'Comprehensive' THEN 1 ELSE 0 END) = 0 THEN TRUE ELSE FALSE END"),
        ("high_propensity_flag", "CASE WHEN propensity_scores.propensity_score >= 0.6 THEN TRUE ELSE FALSE END"),
        ("orthodontist_high_performer", "CASE WHEN providers.provider_type = 'Orthodontist' AND SUM(CASE WHEN cases.case_date >= date_add(current_date(), -29) THEN 1 ELSE 0 END) > 50 THEN TRUE ELSE FALSE END"),
        ("gp_dentist_high_performer", "CASE WHEN providers.provider_type = 'GP Dentist' AND SUM(CASE WHEN cases.case_date >= date_add(current_date(), -29) THEN 1 ELSE 0 END) > 15 THEN TRUE ELSE FALSE END"),
        ("growing_provider", "CASE WHEN ((SUM(CASE WHEN cases.case_date >= date_add(current_date(), -29) THEN 1 ELSE 0 END) - SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) AND cases.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END)) / NULLIF(CAST(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) AND cases.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END) AS DOUBLE), 0)) > 0.05 THEN TRUE ELSE FALSE END"),
        ("declining_provider", "CASE WHEN ((SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) THEN 1 ELSE 0 END) - SUM(CASE WHEN cases.case_date >= date_add(current_date(), -119) AND cases.case_date < date_add(current_date(), -59) THEN 1 ELSE 0 END)) / NULLIF(CAST(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -119) AND cases.case_date < date_add(current_date(), -59) THEN 1 ELSE 0 END) AS DOUBLE), 0)) < -0.15 THEN TRUE ELSE FALSE END"),
        ("at_risk_flag", "CASE WHEN ((SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) THEN 1 ELSE 0 END) - SUM(CASE WHEN cases.case_date >= date_add(current_date(), -119) AND cases.case_date < date_add(current_date(), -59) THEN 1 ELSE 0 END)) / NULLIF(CAST(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -119) AND cases.case_date < date_add(current_date(), -59) THEN 1 ELSE 0 END) AS DOUBLE), 0)) < -0.15 THEN TRUE ELSE FALSE END"),
        ("tier_1_target", "CASE WHEN propensity_scores.propensity_score >= 0.6 AND MAX(CASE WHEN products.product_tier = 'Comprehensive' THEN 1 ELSE 0 END) = 0 AND ((SUM(CASE WHEN cases.case_date >= date_add(current_date(), -29) THEN 1 ELSE 0 END) - SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) AND cases.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END)) / NULLIF(CAST(SUM(CASE WHEN cases.case_date >= date_add(current_date(), -59) AND cases.case_date < date_add(current_date(), -29) THEN 1 ELSE 0 END) AS DOUBLE), 0)) > 0.05 THEN TRUE ELSE FALSE END"),
        ("tier_2_target", "CASE WHEN propensity_scores.propensity_score >= 0.6 AND MAX(CASE WHEN products.product_tier = 'Comprehensive' THEN 1 ELSE 0 END) = 0 THEN TRUE ELSE FALSE END"),
    ]

    join_specs = [
        {
            "id": id32("join:cases->providers"),
            "left": {"identifier": f"{schema_fqn}.cases", "alias": "cases"},
            "right": {"identifier": f"{schema_fqn}.providers", "alias": "providers"},
            "sql": ["`cases`.`provider_id` = `providers`.`provider_id`", "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"],
            "instruction": ["Join cases to providers by provider_id for provider-grain analytics."],
        },
        {
            "id": id32("join:provider_products->providers"),
            "left": {"identifier": f"{schema_fqn}.provider_products", "alias": "provider_products"},
            "right": {"identifier": f"{schema_fqn}.providers", "alias": "providers"},
            "sql": ["`provider_products`.`provider_id` = `providers`.`provider_id`", "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"],
            "instruction": ["Join provider_products to providers for adoption and primary product analytics."],
        },
        {
            "id": id32("join:cases->products"),
            "left": {"identifier": f"{schema_fqn}.cases", "alias": "cases"},
            "right": {"identifier": f"{schema_fqn}.products", "alias": "products"},
            "sql": ["`cases`.`product_id` = `products`.`product_id`", "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"],
            "instruction": ["Join cases to products for tier and pricing analytics."],
        },
        {
            "id": id32("join:provider_products->products"),
            "left": {"identifier": f"{schema_fqn}.provider_products", "alias": "provider_products"},
            "right": {"identifier": f"{schema_fqn}.products", "alias": "products"},
            "sql": ["`provider_products`.`product_id` = `products`.`product_id`", "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"],
            "instruction": ["Join provider_products to products for adoption by tier."],
        },
        {
            "id": id32("join:propensity->providers"),
            "left": {"identifier": f"{schema_fqn}.propensity_scores", "alias": "propensity_scores"},
            "right": {"identifier": f"{schema_fqn}.providers", "alias": "providers"},
            "sql": ["`propensity_scores`.`provider_id` = `providers`.`provider_id`", "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"],
            "instruction": ["Join propensity_scores to providers for account prioritization."],
        },
    ]

    config = {
        "version": 2,
        "config": {
            "sample_questions": sorted(
                [{"id": id32(f"sq:{q}"), "question": [q]} for q in GENIE_SAMPLE_QUESTIONS],
                key=lambda x: x["id"],
            )
        },
        "data_sources": {
            "tables": [{"identifier": t} for t in tables]
        },
        "instructions": {
            "text_instructions": [
                {"id": id32("text:invisalign:instruction"), "content": [f"{line}\n" for line in text_instruction.splitlines() if line]}
            ],
            "example_question_sqls": sorted(
                [
                    {
                        "id": id32(f"eq:{q}"),
                        "question": [q],
                        "sql": split_sql_lines(sql),
                        "usage_guidance": [guidance],
                    }
                    for q, sql, guidance in example_sqls
                ],
                key=lambda x: x["id"],
            ),
            "join_specs": sorted(join_specs, key=lambda x: x["id"]),
            "sql_snippets": {
                "measures": sorted(
                    [
                        {
                            "id": id32(f"measure:{alias}"),
                            "alias": alias,
                            "display_name": alias,
                            "sql": [sql_expr],
                            "instruction": [comment],
                        }
                        for alias, sql_expr, comment in measures
                    ],
                    key=lambda x: x["id"],
                ),
                "expressions": sorted(
                    [
                        {
                            "id": id32(f"expr:{alias}"),
                            "alias": alias,
                            "display_name": alias,
                            "sql": [expr_sql],
                        }
                        for alias, expr_sql in expressions
                    ],
                    key=lambda x: x["id"],
                ),
            },
        },
        "benchmarks": {
            "questions": sorted(
                [
                    {
                        "id": id32(f"bm:{q}"),
                        "question": [q],
                        "answer": [{"format": "SQL", "content": split_sql_lines(sql)}],
                    }
                    for q, sql, _ in example_sqls
                ],
                key=lambda x: x["id"],
            )
        },
    }
    return config


def validate_genie_config(config: dict[str, Any]) -> None:
    import sys
    sys.path.insert(0, "databricks-genie-workbench/packages/genie-space-optimizer/src")
    from genie_space_optimizer.common.genie_schema import validate_serialized_space

    ok, errors = validate_serialized_space(config, strict=True)
    if not ok:
        raise BuilderError(f"Serialized Genie config failed strict validation: {errors[:10]}")


def ensure_genie_space(w: WorkspaceClient, warehouse_id: str, config: dict[str, Any]) -> str:
    serialized = json.dumps(config)
    existing_id: str | None = None
    page_token: str | None = None
    while True:
        resp = w.genie.list_spaces(page_size=100, page_token=page_token)
        for s in resp.spaces or []:
            if s.title == GENIE_NAME:
                existing_id = s.space_id
                break
        if existing_id or not resp.next_page_token:
            break
        page_token = resp.next_page_token

    if existing_id:
        w.genie.update_space(
            space_id=existing_id,
            title=GENIE_NAME,
            description="Structured analytics for Invisalign provider expansion, decline diagnosis, and revenue forecasting.",
            warehouse_id=warehouse_id,
            serialized_space=serialized,
        )
        return existing_id

    me = w.current_user.me()
    parent_path = f"/Users/{me.user_name}/"
    created = w.genie.create_space(
        warehouse_id=warehouse_id,
        serialized_space=serialized,
        title=GENIE_NAME,
        description="Structured analytics for Invisalign provider expansion, decline diagnosis, and revenue forecasting.",
        parent_path=parent_path,
    )
    return created.space_id


def get_genie_space_config(w: WorkspaceClient, space_id: str) -> dict[str, Any]:
    space = w.genie.get_space(space_id=space_id, include_serialized_space=True)
    raw = space.serialized_space
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    raise BuilderError(f"Unable to parse serialized_space for Genie space {space_id}")


def ensure_ka(w: WorkspaceClient) -> tuple[str, str]:
    ka_tile_id: str | None = None
    for tile in list_tiles(w, tile_type="KA"):
        if tile.get("name") == KA_NAME:
            ka_tile_id = tile.get("tile_id")
            break

    if not ka_tile_id:
        safe = sanitize_name(KA_NAME)
        payload = {
            "name": KA_NAME,
            "description": "Unstructured sales playbook assistant for Invisalign provider growth workflows.",
            "instructions": (
                "You are a world-class sales enablement leader for Invisalign provider growth.\n"
                "Always respond in exactly this structure:\n"
                "1. Recommendation\n"
                "2. Why it works (tie to provider behavior)\n"
                "3. Talk track (verbatim script)\n"
                "4. Objection handling\n"
                "5. Outreach strategy (channel + cadence)\n"
                "6. Compliance note\n"
                "Requirements:\n"
                "- Always include at least 2 objections with concise rebuttals.\n"
                "- Include channel + cadence in outreach strategy.\n"
                "- Reference Invisalign product tiers when relevant.\n"
                "- Include healthcare marketing sensitivity, avoid misleading claims, and include patient-outcome disclaimer language when relevant.\n"
                "- Keep outputs rep-ready and practical.\n"
            ),
            "knowledge_sources": [
                {
                    "files_source": {
                        "name": f"source_{safe.lower()}",
                        "type": "files",
                        "files": {"path": KA_VOLUME_PATH},
                    }
                }
            ],
        }
        created = http_json(w, "POST", "/api/2.0/knowledge-assistants", body=payload, timeout=300)
        ka_tile_id = created.get("knowledge_assistant", {}).get("tile", {}).get("tile_id")
        if not ka_tile_id:
            raise BuilderError("KA create response did not include tile_id.")

    detail = http_json(w, "GET", f"/api/2.0/knowledge-assistants/{ka_tile_id}")
    endpoint_status = detail.get("knowledge_assistant", {}).get("status", {}).get("endpoint_status", "UNKNOWN")
    endpoint_name = detail.get("knowledge_assistant", {}).get("tile", {}).get("serving_endpoint_name")
    if not endpoint_name:
        endpoint_name = f"ka-{ka_tile_id.split('-')[0]}-endpoint"

    timeout_s = 1800
    deadline = time.time() + timeout_s
    while endpoint_status != "ONLINE":
        if time.time() > deadline:
            raise BuilderError(
                f"KA endpoint did not reach ONLINE within {timeout_s}s. Last status={endpoint_status}"
            )
        time.sleep(20)
        detail = http_json(w, "GET", f"/api/2.0/knowledge-assistants/{ka_tile_id}")
        endpoint_status = detail.get("knowledge_assistant", {}).get("status", {}).get("endpoint_status", "UNKNOWN")

    # Replace examples with required set.
    examples_resp = http_json(w, "GET", f"/api/2.0/knowledge-assistants/{ka_tile_id}/examples")
    for ex in examples_resp.get("examples", []):
        ex_id = ex.get("example_id") or ex.get("id")
        if ex_id:
            try:
                http_json(w, "DELETE", f"/api/2.0/knowledge-assistants/{ka_tile_id}/examples/{ex_id}")
            except Exception:
                pass

    for q in KA_SAMPLE_QUESTIONS:
        guideline = (
            "Return rep-ready output in the 6 required sections, include talk track, outreach cadence, "
            "two objections, and compliance note."
        )
        http_json(
            w,
            "POST",
            f"/api/2.0/knowledge-assistants/{ka_tile_id}/examples",
            body={"tile_id": ka_tile_id, "question": q, "guidelines": [guideline]},
        )

    return ka_tile_id, endpoint_name


def ask_genie(w: WorkspaceClient, space_id: str, question: str) -> dict[str, Any]:
    msg = w.genie.start_conversation_and_wait(
        space_id=space_id,
        content=question,
        timeout=timedelta(minutes=15),
    )
    out: dict[str, Any] = {
        "question": question,
        "conversation_id": msg.conversation_id,
        "message_id": msg.id,
        "status": str(msg.status.value) if msg.status else "UNKNOWN",
        "sql": None,
        "columns": [],
        "row_count": None,
        "rows": [],
        "text_response": None,
    }

    for att in msg.attachments or []:
        if att.query:
            out["sql"] = att.query.query
            if att.query.query_result_metadata:
                out["row_count"] = att.query.query_result_metadata.row_count
            if att.attachment_id:
                result = w.genie.get_message_query_result_by_attachment(
                    space_id=space_id,
                    conversation_id=msg.conversation_id,
                    message_id=msg.id,
                    attachment_id=att.attachment_id,
                )
                sr = result.statement_response
                if sr and sr.manifest and sr.manifest.schema:
                    out["columns"] = [c.name for c in sr.manifest.schema.columns]
                if sr and sr.result and sr.result.data_array:
                    out["rows"] = sr.result.data_array[:25]
        if att.text:
            out["text_response"] = att.text.content
    return out


def extract_ka_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("output_text", "text", "answer", "response"):
            if key in response and isinstance(response[key], str):
                return response[key]
        if "output" in response:
            o = response["output"]
            if isinstance(o, str):
                return o
            if isinstance(o, list):
                chunks: list[str] = []
                for item in o:
                    if isinstance(item, str):
                        chunks.append(item)
                    elif isinstance(item, dict):
                        c = item.get("content")
                        if isinstance(c, str):
                            chunks.append(c)
                        elif isinstance(c, list):
                            for p in c:
                                if isinstance(p, dict) and isinstance(p.get("text"), str):
                                    chunks.append(p["text"])
                if chunks:
                    return "\n".join(chunks)
        if "predictions" in response and isinstance(response["predictions"], list) and response["predictions"]:
            return extract_ka_text(response["predictions"][0])
    if isinstance(response, list):
        return "\n".join(extract_ka_text(x) for x in response)
    return str(response)


def ask_ka(w: WorkspaceClient, endpoint_name: str, question: str) -> dict[str, Any]:
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    url = f"{w.config.host}/serving-endpoints/{endpoint_name}/invocations"
    payload = {"input": [{"role": "user", "content": question}]}
    resp = requests.post(url, headers=headers, json=payload, timeout=300)
    if resp.status_code >= 400:
        raise BuilderError(
            f"POST /serving-endpoints/{endpoint_name}/invocations failed with status {resp.status_code}: {resp.text}"
        )
    raw = resp.json() if resp.text else {}
    return {"question": question, "response_text": extract_ka_text(raw), "raw_response": raw}


def verify_tables_exist(w: WorkspaceClient, warehouse_id: str) -> None:
    sql_text = f"""
SELECT COUNT(*) AS table_count
FROM system.information_schema.tables
WHERE table_catalog = '{SCHEMA_FQN.split('.')[0]}'
  AND table_schema = '{SCHEMA_FQN.split('.')[1]}'
  AND table_name IN ('providers','products','provider_products','cases','propensity_scores')
"""
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql_text,
        wait_timeout="40s",
        format=sql_svc.Format.JSON_ARRAY,
    )
    if resp.status.state != sql_svc.StatementState.SUCCEEDED:
        raise BuilderError("Failed to validate source tables via information_schema.")
    rows = resp.result.data_array if resp.result and resp.result.data_array else []
    table_count = int(rows[0][0]) if rows else 0
    if table_count != 5:
        raise BuilderError(f"Expected 5 source tables in {SCHEMA_FQN}, found {table_count}.")


def main() -> None:
    w = WorkspaceClient(profile=PROFILE)
    warehouse_id = choose_warehouse_id(w)
    verify_tables_exist(w, warehouse_id)

    genie_config = build_genie_config(SCHEMA_FQN)
    validate_genie_config(genie_config)
    genie_space_id = ensure_genie_space(w, warehouse_id, genie_config)
    live_genie_config = get_genie_space_config(w, genie_space_id)

    ka_tile_id, ka_endpoint_name = ensure_ka(w)
    ka_examples = http_json(w, "GET", f"/api/2.0/knowledge-assistants/{ka_tile_id}/examples").get("examples", [])

    genie_q1 = ask_genie(w, genie_space_id, VALIDATION_Q1)
    ka_q1 = ask_ka(w, ka_endpoint_name, VALIDATION_Q1)
    genie_q2 = ask_genie(w, genie_space_id, VALIDATION_Q2)
    ka_q2 = ask_ka(w, ka_endpoint_name, VALIDATION_Q2)

    required_measure_aliases = {
        "total_cases", "total_revenue", "avg_case_value",
        "cases_last_30d", "cases_prior_30d", "cases_last_90d",
        "revenue_last_30d", "revenue_last_90d",
        "case_growth_rate_30d", "case_growth_rate_60d",
        "comprehensive_case_share", "lite_case_share", "express_case_share",
        "product_count", "has_comprehensive",
    }
    required_expr_aliases = {
        "upgrade_opportunity_flag", "high_propensity_flag",
        "orthodontist_high_performer", "gp_dentist_high_performer",
        "growing_provider", "declining_provider", "at_risk_flag",
        "tier_1_target", "tier_2_target",
    }
    snippets = (live_genie_config.get("instructions", {}) or {}).get("sql_snippets", {}) or {}
    measure_aliases = {m.get("alias") for m in snippets.get("measures", []) if isinstance(m, dict)}
    expr_aliases = {e.get("alias") for e in snippets.get("expressions", []) if isinstance(e, dict)}
    sample_questions_live = [
        q.get("question", [""])[0]
        for q in (live_genie_config.get("config", {}) or {}).get("sample_questions", [])
        if isinstance(q, dict) and q.get("question")
    ]

    result = {
        "assets": {
            "genie_space_id": genie_space_id,
            "ka_tile_id": ka_tile_id,
            "ka_endpoint_name": ka_endpoint_name,
            "warehouse_id": warehouse_id,
        },
        "genie_validation": {
            "q1": genie_q1,
            "q2": genie_q2,
        },
        "ka_validation": {
            "q1": ka_q1,
            "q2": ka_q2,
        },
        "quality_check": {
            "benchmarks_implemented": {
                "measures_present": sorted(required_measure_aliases - (required_measure_aliases - measure_aliases)),
                "missing_measures": sorted(required_measure_aliases - measure_aliases),
                "expressions_present": sorted(required_expr_aliases - (required_expr_aliases - expr_aliases)),
                "missing_expressions": sorted(required_expr_aliases - expr_aliases),
            },
            "sample_questions_working": {
                "genie_sample_questions_count": len(sample_questions_live),
                "genie_required_questions_present": set(GENIE_SAMPLE_QUESTIONS).issubset(set(sample_questions_live)),
                "ka_sample_questions_count": len(ka_examples),
            },
            "outputs_actionable": {
                "genie_q1_has_sql": bool(genie_q1.get("sql")),
                "genie_q1_has_rows": bool(genie_q1.get("rows")),
                "genie_q2_has_sql": bool(genie_q2.get("sql")),
                "genie_q2_has_rows": bool(genie_q2.get("rows")),
                "ka_q1_has_talk_track_keyword": "Talk track" in (ka_q1.get("response_text") or ""),
                "ka_q2_has_objection_keyword": "Objection" in (ka_q2.get("response_text") or ""),
            },
        },
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
