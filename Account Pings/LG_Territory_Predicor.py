# Databricks notebook source
# LG Territory Predicor (Oversight Core Only)

from datetime import date
from difflib import SequenceMatcher
import re

import pandas as pd

# COMMAND ----------

# Widgets

dbutils.widgets.text("accounts_raw", "")
dbutils.widgets.text("run_date", "")
dbutils.widgets.text("output_base", "dbfs:/FileStore/account_pings")
dbutils.widgets.text("match_threshold", "0.55")
dbutils.widgets.text("expected_sales_subregion", "CMEG")

ACCOUNTS_RAW_INPUT = dbutils.widgets.get("accounts_raw").strip()
RUN_DATE_INPUT = dbutils.widgets.get("run_date").strip()
OUTPUT_BASE = dbutils.widgets.get("output_base").rstrip("/")
MATCH_THRESHOLD = float(dbutils.widgets.get("match_threshold"))
EXPECTED_SALES_SUBREGION = dbutils.widgets.get("expected_sales_subregion").strip() or "CMEG"

if RUN_DATE_INPUT:
    run_date_anchor = RUN_DATE_INPUT
else:
    run_date_anchor = (
        spark.sql("SELECT CAST(MAX(usage_date) AS STRING) AS max_usage_date FROM main.gtm_gold.account_consumption_daily")
        .collect()[0]["max_usage_date"]
    )
if not run_date_anchor:
    run_date_anchor = date.today().isoformat()
RUN_DATE_EXPR = f"DATE('{run_date_anchor}')"

# COMMAND ----------

# LG account list (accounts only)

LG_ACCOUNTS_RAW = """
Publicis Groupe
Publicis North America
Epsilon
Starcom
Zenith
Spark Foundry
Digitas
Leo (Leo Burnett & Publicis Worldwide US)
Saatchi & Saatchi
BBH USA
Fallon
Publicis Sapient
Publicis Health (Digitas Health, Razorfish Health)
CJ
Lotame
Omnicom Group
OMD USA
PHD US
Hearts & Science
BBDO North America
DDB North America
TBWA\\Chiat\\Day
McCann Worldgroup
FCB
Initiative
UM (Universal McCann)
DAS Group of Companies
Weber Shandwick
Golin
Ketchum
FleishmanHillard
Flywheel Digital
The Martin Agency
Goodby Silverstein & Partners
Stagwell
Assembly
GALE
72andSunny
Anomaly
Horizon Media
Data Axle
Harris Poll
Constellation & Townhouse (C&T)
dentsu / Merkle
Edelman
Nielsen
Advantage Solutions
Catalina
Bridgestone
""".strip()


def parse_accounts_only(raw: str) -> list[str]:
    accounts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        candidate = line
        if "|" in line:
            candidate = line.split("|", 1)[1].strip()
        elif ":" in line:
            candidate = line.split(":", 1)[1].strip()
        elif " - " in line:
            candidate = line.split(" - ", 1)[1].strip()
        candidate = candidate.strip(" -:|")

        if candidate:
            accounts.append(candidate)

    deduped = []
    seen = set()
    for account in accounts:
        key = account.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(account)
    return deduped


ACCOUNTS_RAW = ACCOUNTS_RAW_INPUT if ACCOUNTS_RAW_INPUT else LG_ACCOUNTS_RAW
OVERSIGHT_ACCOUNTS = parse_accounts_only(ACCOUNTS_RAW)

if not OVERSIGHT_ACCOUNTS:
    raise ValueError("No accounts configured after parsing. Provide account lines in accounts_raw widget.")

run_date = run_date_anchor
weekly_dir = f"{OUTPUT_BASE}/weekly"
dbutils.fs.mkdirs(weekly_dir)

print(f"Requested LG accounts: {len(OVERSIGHT_ACCOUNTS)}")
print(f"Run date anchor: {run_date}")
print(f"Expected sales subregion: {EXPECTED_SALES_SUBREGION}")

# COMMAND ----------

# Oversight output for LG account subset (T7D + T28D side-by-side)

def normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def simplify_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\bfy\\d{2}\b", " ", s)
    s = re.sub(r"\b(hq|primary|inc|corp|corporation|company|co|group|services|service)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def token_set(s: str):
    return set([x for x in simplify_name(s).split(" ") if x])


def to_markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    headers = [str(c) for c in df.columns]
    rows = [[str(v) for v in row] for row in df.itertuples(index=False, name=None)]
    sep = ["---"] * len(headers)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def name_score(req_name: str, cand_name: str) -> float:
    req_norm = normalize_name(req_name)
    cand_norm = normalize_name(cand_name)
    if req_norm == cand_norm:
        return 1.5

    req_simple = simplify_name(req_name)
    cand_simple = simplify_name(cand_name)
    seq = SequenceMatcher(None, req_simple, cand_simple).ratio()
    req_tokens = token_set(req_name)
    cand_tokens = token_set(cand_name)
    jac = 0.0
    if req_tokens or cand_tokens:
        jac = len(req_tokens & cand_tokens) / max(1, len(req_tokens | cand_tokens))
    contains = 0.15 if (req_simple in cand_simple or cand_simple in req_simple) else 0.0
    return (0.65 * seq) + (0.20 * jac) + contains


candidate_sql = """
WITH latest AS (
  SELECT
    account_id,
    account_name,
    account_segment,
    sales_subregion_level_1,
    sales_subregion_level_2,
    sales_subregion_level_3,
    snapshot_date,
    ROW_NUMBER() OVER (
      PARTITION BY account_id
      ORDER BY snapshot_date DESC NULLS LAST
    ) AS rn
  FROM main.gtm_silver.account_dim
)
SELECT
  account_id,
  account_name,
  account_segment,
  sales_subregion_level_1,
  sales_subregion_level_2,
  sales_subregion_level_3
FROM latest
WHERE rn = 1
"""
candidate_df = spark.sql(candidate_sql).toPandas()

matches = []
for requested in OVERSIGHT_ACCOUNTS:
    best = None
    best_score = -1.0
    for _, c in candidate_df.iterrows():
        score = name_score(requested, c["account_name"])
        if score > best_score:
            best_score = score
            best = c
    if best is not None and best_score >= MATCH_THRESHOLD:
        matches.append(
            {
                "requested_account": requested,
                "matched_account_id": str(best["account_id"]),
                "matched_account_name": best["account_name"],
                "match_score": round(float(best_score), 4),
            }
        )

match_df = pd.DataFrame(matches)
if not match_df.empty:
    match_df["matched"] = match_df["match_score"] >= MATCH_THRESHOLD
    matched_filtered = match_df[match_df["matched"] == True].copy()
    matched_account_ids = sorted(matched_filtered["matched_account_id"].dropna().astype(str).unique().tolist())
else:
    matched_filtered = pd.DataFrame(columns=["requested_account", "matched_account_id", "matched_account_name", "match_score", "matched"])
    matched_account_ids = []

if matched_account_ids:
    oversight_account_ids = ", ".join(["'" + x.replace("'", "''") + "'" for x in matched_account_ids])
    oversight_sql = f"""
    WITH current_7 AS (
      SELECT account_id,
             SUM(COALESCE(genai_mct_dbu_dollars,0) + COALESCE(ai_dbu_dollars,0)) AS t7d_spend,
             SUM(COALESCE(genai_foundation_model_api_dbu_dollars,0)) AS t7d_foundation_model_api_dollars,
             SUM(COALESCE(genai_gpu_model_serving_dbu_dollars,0)) AS t7d_gpu_model_serving_dollars,
             SUM(COALESCE(genai_cpu_model_serving_dbu_dollars,0)) AS t7d_cpu_model_serving_dollars,
             SUM(COALESCE(genai_throughput_model_serving_dbu_dollars,0)) AS t7d_throughput_model_serving_dollars,
             SUM(COALESCE(genai_foundation_model_training_dbu_dollars,0)) AS t7d_foundation_model_training_dollars,
             SUM(COALESCE(genai_vector_search_dbu_dollars,0)) AS t7d_vector_search_dollars,
             SUM(COALESCE(batch_interference_model_serving_dbu_dollars,0)) AS t7d_batch_inference_dollars,
             SUM(COALESCE(agent_evals_dbu_dollars,0)) AS t7d_agent_evals_dollars,
             SUM(COALESCE(anthropic_dbu_dollars,0)) AS t7d_anthropic_dollars,
             SUM(COALESCE(ai_runtime_dbu_dollars,0)) AS t7d_ai_runtime_dollars,
             SUM(COALESCE(ai_gateway_dbu_dollars,0)) AS t7d_ai_gateway_dollars
      FROM main.gtm_gold.account_consumption_daily
      WHERE usage_date <= date_sub({RUN_DATE_EXPR}, 2)
        AND usage_date >= date_sub({RUN_DATE_EXPR}, 8)
        AND account_id IN ({oversight_account_ids})
      GROUP BY account_id
    ),
    prior_7 AS (
      SELECT account_id,
             SUM(COALESCE(genai_mct_dbu_dollars,0) + COALESCE(ai_dbu_dollars,0)) AS prior_t7d_spend,
             SUM(COALESCE(genai_foundation_model_api_dbu_dollars,0)) AS prior_t7d_foundation_model_api_dollars,
             SUM(COALESCE(genai_gpu_model_serving_dbu_dollars,0)) AS prior_t7d_gpu_model_serving_dollars,
             SUM(COALESCE(genai_cpu_model_serving_dbu_dollars,0)) AS prior_t7d_cpu_model_serving_dollars,
             SUM(COALESCE(genai_throughput_model_serving_dbu_dollars,0)) AS prior_t7d_throughput_model_serving_dollars,
             SUM(COALESCE(genai_foundation_model_training_dbu_dollars,0)) AS prior_t7d_foundation_model_training_dollars,
             SUM(COALESCE(genai_vector_search_dbu_dollars,0)) AS prior_t7d_vector_search_dollars,
             SUM(COALESCE(batch_interference_model_serving_dbu_dollars,0)) AS prior_t7d_batch_inference_dollars,
             SUM(COALESCE(agent_evals_dbu_dollars,0)) AS prior_t7d_agent_evals_dollars,
             SUM(COALESCE(anthropic_dbu_dollars,0)) AS prior_t7d_anthropic_dollars,
             SUM(COALESCE(ai_runtime_dbu_dollars,0)) AS prior_t7d_ai_runtime_dollars,
             SUM(COALESCE(ai_gateway_dbu_dollars,0)) AS prior_t7d_ai_gateway_dollars
      FROM main.gtm_gold.account_consumption_daily
      WHERE usage_date <= date_sub({RUN_DATE_EXPR}, 9)
        AND usage_date >= date_sub({RUN_DATE_EXPR}, 15)
        AND account_id IN ({oversight_account_ids})
      GROUP BY account_id
    ),
    current_28 AS (
      SELECT account_id,
             SUM(COALESCE(genai_mct_dbu_dollars,0) + COALESCE(ai_dbu_dollars,0)) AS t28d_spend,
             SUM(COALESCE(genai_foundation_model_api_dbu_dollars,0)) AS t28d_foundation_model_api_dollars,
             SUM(COALESCE(genai_gpu_model_serving_dbu_dollars,0)) AS t28d_gpu_model_serving_dollars,
             SUM(COALESCE(genai_cpu_model_serving_dbu_dollars,0)) AS t28d_cpu_model_serving_dollars,
             SUM(COALESCE(genai_throughput_model_serving_dbu_dollars,0)) AS t28d_throughput_model_serving_dollars,
             SUM(COALESCE(genai_foundation_model_training_dbu_dollars,0)) AS t28d_foundation_model_training_dollars,
             SUM(COALESCE(genai_vector_search_dbu_dollars,0)) AS t28d_vector_search_dollars,
             SUM(COALESCE(batch_interference_model_serving_dbu_dollars,0)) AS t28d_batch_inference_dollars,
             SUM(COALESCE(agent_evals_dbu_dollars,0)) AS t28d_agent_evals_dollars,
             SUM(COALESCE(anthropic_dbu_dollars,0)) AS t28d_anthropic_dollars,
             SUM(COALESCE(ai_runtime_dbu_dollars,0)) AS t28d_ai_runtime_dollars,
             SUM(COALESCE(ai_gateway_dbu_dollars,0)) AS t28d_ai_gateway_dollars
      FROM main.gtm_gold.account_consumption_daily
      WHERE usage_date <= date_sub({RUN_DATE_EXPR}, 2)
        AND usage_date >= date_sub({RUN_DATE_EXPR}, 29)
        AND account_id IN ({oversight_account_ids})
      GROUP BY account_id
    ),
    prior_28 AS (
      SELECT account_id,
             SUM(COALESCE(genai_mct_dbu_dollars,0) + COALESCE(ai_dbu_dollars,0)) AS prior_t28d_spend,
             SUM(COALESCE(genai_foundation_model_api_dbu_dollars,0)) AS prior_t28d_foundation_model_api_dollars,
             SUM(COALESCE(genai_gpu_model_serving_dbu_dollars,0)) AS prior_t28d_gpu_model_serving_dollars,
             SUM(COALESCE(genai_cpu_model_serving_dbu_dollars,0)) AS prior_t28d_cpu_model_serving_dollars,
             SUM(COALESCE(genai_throughput_model_serving_dbu_dollars,0)) AS prior_t28d_throughput_model_serving_dollars,
             SUM(COALESCE(genai_foundation_model_training_dbu_dollars,0)) AS prior_t28d_foundation_model_training_dollars,
             SUM(COALESCE(genai_vector_search_dbu_dollars,0)) AS prior_t28d_vector_search_dollars,
             SUM(COALESCE(batch_interference_model_serving_dbu_dollars,0)) AS prior_t28d_batch_inference_dollars,
             SUM(COALESCE(agent_evals_dbu_dollars,0)) AS prior_t28d_agent_evals_dollars,
             SUM(COALESCE(anthropic_dbu_dollars,0)) AS prior_t28d_anthropic_dollars,
             SUM(COALESCE(ai_runtime_dbu_dollars,0)) AS prior_t28d_ai_runtime_dollars,
             SUM(COALESCE(ai_gateway_dbu_dollars,0)) AS prior_t28d_ai_gateway_dollars
      FROM main.gtm_gold.account_consumption_daily
      WHERE usage_date <= date_sub({RUN_DATE_EXPR}, 31)
        AND usage_date >= date_sub({RUN_DATE_EXPR}, 59)
        AND account_id IN ({oversight_account_ids})
      GROUP BY account_id
    ),
    latest_dim AS (
      SELECT *,
             ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY snapshot_date DESC NULLS LAST) AS rn
      FROM main.gtm_silver.account_dim
    )
    SELECT
      d.account_id,
      d.account_name,
      d.account_segment,
      d.sales_subregion_level_1,
      d.sales_subregion_level_2,
      d.sales_subregion_level_3,
      COALESCE(c7.t7d_spend, 0) AS t7d_spend,
      COALESCE((TRY_DIVIDE(COALESCE(c7.t7d_spend,0), COALESCE(p7.prior_t7d_spend,0)) - 1) * 100, 0) AS t7d_pop_growth_pct,
      COALESCE(c7.t7d_foundation_model_api_dollars,0) - COALESCE(p7.prior_t7d_foundation_model_api_dollars,0) AS t7_delta_foundation_model_api_dollars,
      COALESCE(c7.t7d_gpu_model_serving_dollars,0) - COALESCE(p7.prior_t7d_gpu_model_serving_dollars,0) AS t7_delta_gpu_model_serving_dollars,
      COALESCE(c7.t7d_cpu_model_serving_dollars,0) - COALESCE(p7.prior_t7d_cpu_model_serving_dollars,0) AS t7_delta_cpu_model_serving_dollars,
      COALESCE(c7.t7d_throughput_model_serving_dollars,0) - COALESCE(p7.prior_t7d_throughput_model_serving_dollars,0) AS t7_delta_throughput_model_serving_dollars,
      COALESCE(c7.t7d_foundation_model_training_dollars,0) - COALESCE(p7.prior_t7d_foundation_model_training_dollars,0) AS t7_delta_foundation_model_training_dollars,
      COALESCE(c7.t7d_vector_search_dollars,0) - COALESCE(p7.prior_t7d_vector_search_dollars,0) AS t7_delta_vector_search_dollars,
      COALESCE(c7.t7d_batch_inference_dollars,0) - COALESCE(p7.prior_t7d_batch_inference_dollars,0) AS t7_delta_batch_inference_dollars,
      COALESCE(c7.t7d_agent_evals_dollars,0) - COALESCE(p7.prior_t7d_agent_evals_dollars,0) AS t7_delta_agent_evals_dollars,
      COALESCE(c7.t7d_anthropic_dollars,0) - COALESCE(p7.prior_t7d_anthropic_dollars,0) AS t7_delta_anthropic_dollars,
      COALESCE(c7.t7d_ai_runtime_dollars,0) - COALESCE(p7.prior_t7d_ai_runtime_dollars,0) AS t7_delta_ai_runtime_dollars,
      COALESCE(c7.t7d_ai_gateway_dollars,0) - COALESCE(p7.prior_t7d_ai_gateway_dollars,0) AS t7_delta_ai_gateway_dollars,
      COALESCE(c28.t28d_spend, 0) AS t28d_spend,
      COALESCE((TRY_DIVIDE(COALESCE(c28.t28d_spend,0), COALESCE(p28.prior_t28d_spend,0)) - 1) * 100, 0) AS t28d_pop_growth_pct,
      COALESCE(c28.t28d_foundation_model_api_dollars,0) - COALESCE(p28.prior_t28d_foundation_model_api_dollars,0) AS t28_delta_foundation_model_api_dollars,
      COALESCE(c28.t28d_gpu_model_serving_dollars,0) - COALESCE(p28.prior_t28d_gpu_model_serving_dollars,0) AS t28_delta_gpu_model_serving_dollars,
      COALESCE(c28.t28d_cpu_model_serving_dollars,0) - COALESCE(p28.prior_t28d_cpu_model_serving_dollars,0) AS t28_delta_cpu_model_serving_dollars,
      COALESCE(c28.t28d_throughput_model_serving_dollars,0) - COALESCE(p28.prior_t28d_throughput_model_serving_dollars,0) AS t28_delta_throughput_model_serving_dollars,
      COALESCE(c28.t28d_foundation_model_training_dollars,0) - COALESCE(p28.prior_t28d_foundation_model_training_dollars,0) AS t28_delta_foundation_model_training_dollars,
      COALESCE(c28.t28d_vector_search_dollars,0) - COALESCE(p28.prior_t28d_vector_search_dollars,0) AS t28_delta_vector_search_dollars,
      COALESCE(c28.t28d_batch_inference_dollars,0) - COALESCE(p28.prior_t28d_batch_inference_dollars,0) AS t28_delta_batch_inference_dollars,
      COALESCE(c28.t28d_agent_evals_dollars,0) - COALESCE(p28.prior_t28d_agent_evals_dollars,0) AS t28_delta_agent_evals_dollars,
      COALESCE(c28.t28d_anthropic_dollars,0) - COALESCE(p28.prior_t28d_anthropic_dollars,0) AS t28_delta_anthropic_dollars,
      COALESCE(c28.t28d_ai_runtime_dollars,0) - COALESCE(p28.prior_t28d_ai_runtime_dollars,0) AS t28_delta_ai_runtime_dollars,
      COALESCE(c28.t28d_ai_gateway_dollars,0) - COALESCE(p28.prior_t28d_ai_gateway_dollars,0) AS t28_delta_ai_gateway_dollars
    FROM latest_dim d
    LEFT JOIN current_7 c7 ON d.account_id = c7.account_id
    LEFT JOIN prior_7 p7 ON d.account_id = p7.account_id
    LEFT JOIN current_28 c28 ON d.account_id = c28.account_id
    LEFT JOIN prior_28 p28 ON d.account_id = p28.account_id
    WHERE d.rn = 1
      AND d.account_id IN ({oversight_account_ids})
    """
    oversight_df = spark.sql(oversight_sql).toPandas()
else:
    oversight_df = pd.DataFrame()

window_sku_cols = {
    "GPU Model Serving": "gpu_model_serving_dollars",
    "Throughput Serving": "throughput_model_serving_dollars",
    "Foundational Model API": "foundation_model_api_dollars",
    "Vector Search": "vector_search_dollars",
    "CPU Model Serving": "cpu_model_serving_dollars",
    "Foundational Model Training": "foundation_model_training_dollars",
    "Batch Inference Model Serving": "batch_inference_dollars",
    "Agent Eval": "agent_evals_dollars",
    "Anthropic": "anthropic_dollars",
    "AI Gateway": "ai_gateway_dollars",
    "AI Runtime": "ai_runtime_dollars",
}


def compute_window_drivers(row, window_prefix: str) -> str:
    growth = float(row.get(f"{window_prefix}d_pop_growth_pct", 0.0) or 0.0)
    signed = []
    for label, suffix in window_sku_cols.items():
        val = float(row.get(f"{window_prefix}_delta_{suffix}", 0.0) or 0.0)
        signed.append((label, val))
    if growth >= 0:
        ranked = [x for x in signed if x[1] > 0]
        ranked.sort(key=lambda x: x[1], reverse=True)
    else:
        ranked = [x for x in signed if x[1] < 0]
        ranked.sort(key=lambda x: x[1])
    if not ranked:
        return "No clear driver"
    return ", ".join([f"{label} ({delta:+.2f})" for label, delta in ranked[:3]])


if not oversight_df.empty:
    oversight_df["t7d_driving_skus"] = oversight_df.apply(lambda r: compute_window_drivers(r, "t7"), axis=1)
    oversight_df["t28d_driving_skus"] = oversight_df.apply(lambda r: compute_window_drivers(r, "t28"), axis=1)
    oversight_df["t7d_spend"] = oversight_df["t7d_spend"].round(2)
    oversight_df["t28d_spend"] = oversight_df["t28d_spend"].round(2)
    oversight_df["t7d_pop_growth_pct"] = oversight_df["t7d_pop_growth_pct"].round(2)
    oversight_df["t28d_pop_growth_pct"] = oversight_df["t28d_pop_growth_pct"].round(2)
    if not match_df.empty:
        oversight_df = oversight_df.merge(
            matched_filtered,
            left_on="account_id",
            right_on="matched_account_id",
            how="left",
        )
    oversight_df["requested_account"] = oversight_df["requested_account"].fillna(oversight_df["account_name"])
    oversight_df["is_expected_subregion"] = (
        oversight_df["sales_subregion_level_1"].astype(str).str.strip().str.upper()
        == EXPECTED_SALES_SUBREGION.strip().upper()
    )
else:
    oversight_df = pd.DataFrame(
        columns=[
            "requested_account",
            "account_name",
            "account_segment",
            "sales_subregion_level_1",
            "sales_subregion_level_2",
            "sales_subregion_level_3",
            "t7d_spend",
            "t7d_pop_growth_pct",
            "t7d_driving_skus",
            "t28d_spend",
            "t28d_pop_growth_pct",
            "t28d_driving_skus",
            "match_score",
            "is_expected_subregion",
        ]
    )

oversight_cols = [
    "requested_account",
    "account_name",
    "match_score",
    "account_segment",
    "sales_subregion_level_1",
    "sales_subregion_level_2",
    "sales_subregion_level_3",
    "is_expected_subregion",
    "t7d_spend",
    "t7d_pop_growth_pct",
    "t7d_driving_skus",
    "t28d_spend",
    "t28d_pop_growth_pct",
    "t28d_driving_skus",
]
oversight_export = oversight_df[oversight_cols].copy()
oversight_export = oversight_export.sort_values(["requested_account", "t28d_spend"], ascending=[True, False]).drop_duplicates(
    subset=["requested_account"], keep="first"
)

oversight_csv_uri = f"{weekly_dir}/lg_oversight_t7d_t28d_{run_date}.csv"
oversight_md_uri = f"{weekly_dir}/lg_oversight_t7d_t28d_{run_date}.md"
dbutils.fs.put(oversight_csv_uri, oversight_export.to_csv(index=False), True)

matched_requested = set(oversight_export["requested_account"].tolist()) if not oversight_export.empty else set()
unmatched = [x for x in OVERSIGHT_ACCOUNTS if x not in matched_requested]
subregion_mismatch_count = int((~oversight_export["is_expected_subregion"]).sum()) if not oversight_export.empty else 0
oversight_md = [
    f"# LG Oversight Accounts T7D + T28D ({run_date})",
    "",
    f"- Requested accounts: {len(OVERSIGHT_ACCOUNTS)}",
    f"- Matched accounts: {len(oversight_export)}",
    f"- Unmatched accounts: {len(unmatched)}",
    f"- Match threshold: {MATCH_THRESHOLD}",
    f"- Expected subregion: {EXPECTED_SALES_SUBREGION}",
    f"- Matched accounts outside expected subregion: {subregion_mismatch_count}",
    "",
]
if not oversight_export.empty:
    oversight_md.append(to_markdown_table(oversight_export.rename(columns={"sales_subregion_level_1": "sales_subregion"})))
    oversight_md.append("")
if unmatched:
    oversight_md.append("## Unmatched Requested Accounts")
    oversight_md.extend([f"- {name}" for name in unmatched])
    oversight_md.append("")

dbutils.fs.put(oversight_md_uri, "\n".join(oversight_md).strip() + "\n", True)
print(f"Wrote Oversight CSV: {oversight_csv_uri}")
print(f"Wrote Oversight MD: {oversight_md_uri}")

match_csv_uri = f"{weekly_dir}/lg_match_diagnostics_{run_date}.csv"
dbutils.fs.put(match_csv_uri, match_df.to_csv(index=False), True)
print(f"Wrote Match Diagnostics CSV: {match_csv_uri}")

if oversight_export.empty:
    print("No LG accounts matched current threshold/subregion filters.")
else:
    spark.createDataFrame(oversight_export).display()
