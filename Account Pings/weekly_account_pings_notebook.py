# Databricks notebook source
# Weekly Strategic Account Pings

from datetime import date
from difflib import SequenceMatcher
import re

import pandas as pd
from pyspark.sql import functions as F

# COMMAND ----------
# Widgets for job/runtime configuration

dbutils.widgets.text("top_n", "20")
dbutils.widgets.text("min_t28d_spend", "300")
dbutils.widgets.text("output_base", "dbfs:/FileStore/account_pings")
dbutils.widgets.text("run_date", "")
dbutils.widgets.text("segment_focus", "Strategic")

TOP_N = int(dbutils.widgets.get("top_n"))
MIN_T28D_SPEND = float(dbutils.widgets.get("min_t28d_spend"))
OUTPUT_BASE = dbutils.widgets.get("output_base").rstrip("/")
RUN_DATE_INPUT = dbutils.widgets.get("run_date").strip()
SEGMENT_FOCUS_RAW = dbutils.widgets.get("segment_focus").strip()

if SEGMENT_FOCUS_RAW.lower() not in {"strategic", "named"}:
    SEGMENT_FOCUS = "strategic"
else:
    SEGMENT_FOCUS = SEGMENT_FOCUS_RAW.lower()
SEGMENT_FOCUS_TITLE = SEGMENT_FOCUS.title()

if RUN_DATE_INPUT:
    RUN_DATE_EXPR = f"DATE('{RUN_DATE_INPUT}')"
else:
    RUN_DATE_EXPR = "current_date()"

OVERSIGHT_ACCOUNTS_RAW = """
Abbott Laboratories
Accenture (HQ)
ADP
Alimentation Couche-Tard
Amgen
Becton Dickinson
Canadian Imperial Bank of Commerce
Canadian National Railway
Canadian Natural Resources
Capital Group
Capital One Financial
Cencora
Centene Corporation
Cigna
CVS Health
Deloitte Consulting (HQ)
DHS-FEMA
E&Y (HQ)
Elevance Health
Eli Lilly and Company
Enbridge
Ensemble Health Partners
Experian
FIS Global
Fiserv
Gainwell Technologies
GE Healthcare
General Services Administration (GSA)
Gilead Sciences
Humana
Intermountain Health
Johnson & Johnson
JPMorgan Chase
Kenvue
Liberty Mutual Insurance
ManuLife Financial
Marsh McLennan
Mastercard
McKesson
McKinsey & Company (HQ)
Medtronic
Merck & Co.
Nasdaq OMX Group
Nationwide
New York Life Insurance Company
Northwestern Mutual
Pfizer
Providence Health
PWC (HQ)
RBC
RGA Enterprise Services Company
RTX Corporation
S&P Global
Scotiabank-PRIMARY(FY27)
State Street
StoneX Group Inc.
Stryker
Takeda Pharmaceuticals - USA
Thermo Fisher Scientific
Travelers
U.S. Department of Energy
United States Postal Service
UnitedHealth Group (UHG)
US Department of State
Atlassian Pty Ltd.
Banco Bradesco
Banco do Brasil
Caixa Economica Federal
Cielo S.A. – Instituição de Pagamento
DoorDash
Dropbox
Grammarly, Inc.
Grupo Bimbo
iFood
Instacart
Match Group
Microsoft AI (CoPilot)
Natura Cosmeticos S.A.
Nubank
OpenAI
PEMEX
Perplexity AI
Petrobras
PicPay
Samsara, Inc
Sicredi
The Trade Desk
Workday
XP Investimentos
Yahoo!
Zillow
Zoom
Adobe Systems
Anysphere
AT&T
Comcast
Concentrix
Discovery
Electronic Arts
Epic Games
Epsilon
FanDuel
Fox Corporation
Live Nation Entertainment
Riot Games
Rivian Automotive
SiriusXM
Sony Interactive Entertainment - SIE
T-Mobile
"""

OVERSIGHT_ACCOUNTS = [x.strip() for x in OVERSIGHT_ACCOUNTS_RAW.splitlines() if x.strip()]

# COMMAND ----------
# Base extraction and outputs for BOTH Strategic + Named tables

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


def requested_match_score(actual_account_name: str, requested_name: str) -> float:
    actual_norm = normalize_name(actual_account_name)
    requested_norm = normalize_name(requested_name)
    if actual_norm == requested_norm:
        return 1.5
    actual_simple = simplify_name(actual_account_name)
    requested_simple = simplify_name(requested_name)
    seq = SequenceMatcher(None, actual_simple, requested_simple).ratio()
    actual_tokens = token_set(actual_account_name)
    requested_tokens = token_set(requested_name)
    jac = 0.0
    if actual_tokens or requested_tokens:
        jac = len(actual_tokens & requested_tokens) / max(1, len(actual_tokens | requested_tokens))
    contains = 0.15 if (actual_simple in requested_simple or requested_simple in actual_simple) else 0.0
    return (0.65 * seq) + (0.20 * jac) + contains


def best_requested_match(actual_account_name: str):
    best_name = None
    best_score = -1.0
    for requested in OVERSIGHT_ACCOUNTS:
        score = requested_match_score(actual_account_name, requested)
        if score > best_score:
            best_score = score
            best_name = requested
    if best_score >= 0.72:
        return best_name, round(float(best_score), 4), True
    return "", round(float(best_score), 4), False


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

run_date = RUN_DATE_INPUT if RUN_DATE_INPUT else date.today().isoformat()
weekly_dir = f"{OUTPUT_BASE}/weekly"
dbutils.fs.mkdirs(weekly_dir)

sku_delta_cols = {
    "GPU Model Serving": "delta_gpu_model_serving_dollars",
    "Throughput Serving": "delta_throughput_model_serving_dollars",
    "Foundational Model API": "delta_foundation_model_api_dollars",
    "Vector Search": "delta_vector_search_dollars",
    "CPU Model Serving": "delta_cpu_model_serving_dollars",
    "Foundational Model Training": "delta_foundation_model_training_dollars",
    "Batch Inference Model Serving": "delta_batch_inference_dollars",
    "Agent Eval": "delta_agent_evals_dollars",
    "Anthropic": "delta_anthropic_dollars",
    "AI Gateway": "delta_ai_gateway_dollars",
    "AI Runtime": "delta_ai_runtime_dollars",
}

segment_exports = {}
for segment_focus in ["strategic", "named"]:
    segment_title = segment_focus.title()
    sql = f"""
    WITH current_28 AS (
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
      WHERE usage_date <= date_sub({RUN_DATE_EXPR}, 2) AND usage_date >= date_sub({RUN_DATE_EXPR}, 29)
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
      WHERE usage_date <= date_sub({RUN_DATE_EXPR}, 31) AND usage_date >= date_sub({RUN_DATE_EXPR}, 59)
      GROUP BY account_id
    ),
    workshop_flags AS (
      SELECT
        account_id,
        COUNT(DISTINCT usecase_id) AS workshop_count,
        MAX(
          CASE
            WHEN poc_status IN ('Workshop Run', 'Workshop Run - Databricks') THEN 1
            ELSE 0
          END
        ) AS workshop_flag_int
      FROM main.gtm_silver.use_case_detail_history
      GROUP BY account_id
    ),
    caio_flags AS (
      SELECT
        account_id,
        COUNT(*) AS caio_meeting_count,
        MAX(TO_DATE(date)) AS last_caio_meeting_date
      FROM main.gtm_governance_solutions.chief_ai_officer_meetings
      GROUP BY account_id
    ),
    joined AS (
      SELECT a.account_id,
             a.account_name,
             a.account_executive AS ae_name,
             a.last_solution_architect_engaged AS sa_name,
             a.account_segment,
             a.sales_subregion_level_1,
             a.sales_subregion_level_2,
             a.sales_subregion_level_3,
             COALESCE(c.t28d_spend, 0) AS total_t28d_spend,
             COALESCE((TRY_DIVIDE(COALESCE(c.t28d_spend,0), COALESCE(p.prior_t28d_spend,0)) - 1) * 100, 0) AS pop_growth_pct,
             COALESCE(c.t28d_foundation_model_api_dollars,0) - COALESCE(p.prior_t28d_foundation_model_api_dollars,0) AS delta_foundation_model_api_dollars,
             COALESCE(c.t28d_gpu_model_serving_dollars,0) - COALESCE(p.prior_t28d_gpu_model_serving_dollars,0) AS delta_gpu_model_serving_dollars,
             COALESCE(c.t28d_cpu_model_serving_dollars,0) - COALESCE(p.prior_t28d_cpu_model_serving_dollars,0) AS delta_cpu_model_serving_dollars,
             COALESCE(c.t28d_throughput_model_serving_dollars,0) - COALESCE(p.prior_t28d_throughput_model_serving_dollars,0) AS delta_throughput_model_serving_dollars,
             COALESCE(c.t28d_foundation_model_training_dollars,0) - COALESCE(p.prior_t28d_foundation_model_training_dollars,0) AS delta_foundation_model_training_dollars,
             COALESCE(c.t28d_vector_search_dollars,0) - COALESCE(p.prior_t28d_vector_search_dollars,0) AS delta_vector_search_dollars,
             COALESCE(c.t28d_batch_inference_dollars,0) - COALESCE(p.prior_t28d_batch_inference_dollars,0) AS delta_batch_inference_dollars,
             COALESCE(c.t28d_agent_evals_dollars,0) - COALESCE(p.prior_t28d_agent_evals_dollars,0) AS delta_agent_evals_dollars,
             COALESCE(c.t28d_anthropic_dollars,0) - COALESCE(p.prior_t28d_anthropic_dollars,0) AS delta_anthropic_dollars,
             COALESCE(c.t28d_ai_runtime_dollars,0) - COALESCE(p.prior_t28d_ai_runtime_dollars,0) AS delta_ai_runtime_dollars,
             COALESCE(c.t28d_ai_gateway_dollars,0) - COALESCE(p.prior_t28d_ai_gateway_dollars,0) AS delta_ai_gateway_dollars,
             COALESCE(w.workshop_flag_int, 0) AS workshop_flag_int,
             COALESCE(w.workshop_count, 0) AS workshop_count,
             CASE WHEN COALESCE(cf.caio_meeting_count, 0) > 0 THEN 1 ELSE 0 END AS caio_meeting_flag_int,
             COALESCE(cf.caio_meeting_count, 0) AS caio_meeting_count,
             cf.last_caio_meeting_date AS last_caio_meeting_date
      FROM main.gtm_silver.account_dim a
      LEFT JOIN current_28 c ON a.account_id = c.account_id
      LEFT JOIN prior_28 p ON a.account_id = p.account_id
      LEFT JOIN workshop_flags w ON a.account_id = w.account_id
      LEFT JOIN caio_flags cf ON a.account_id = cf.account_id
      WHERE lower(COALESCE(a.account_segment, '')) LIKE '%{segment_focus}%'
        AND a.sales_subregion_level_1 IN ('DNB','LATAM','CMEG','HLS','FINS','PS','MFG')
    )
    SELECT * FROM joined
    WHERE total_t28d_spend > {MIN_T28D_SPEND}
      AND (pop_growth_pct >= 10 OR pop_growth_pct <= -10)
    """
    pdf = spark.sql(sql).toPandas()
    for _, col in sku_delta_cols.items():
        if col not in pdf.columns:
            pdf[col] = 0.0

    growers = pdf[pdf["pop_growth_pct"] >= 10].sort_values(["pop_growth_pct", "total_t28d_spend"], ascending=[False, False]).head(TOP_N).copy()
    growers["signal_type"] = "grower"
    decliners = pdf[pdf["pop_growth_pct"] <= -10].sort_values(["pop_growth_pct", "total_t28d_spend"], ascending=[True, False]).head(TOP_N).copy()
    decliners["signal_type"] = "decliner"
    result = pd.concat([growers, decliners], ignore_index=True)
    if not result.empty:
        result["rank_within_signal"] = result.groupby("signal_type")["pop_growth_pct"].rank(method="first", ascending=False)
        result.loc[result["signal_type"] == "decliner", "rank_within_signal"] = (
            result[result["signal_type"] == "decliner"]["pop_growth_pct"].rank(method="first", ascending=True)
        )
        result["rank_within_signal"] = result["rank_within_signal"].astype(int)
    else:
        result["rank_within_signal"] = []

    result[["oversight_requested_account", "oversight_match_score", "is_oversight_account"]] = result.apply(
        lambda r: pd.Series(best_requested_match(r.get("account_name"))), axis=1
    )

    def sku_drivers(row):
        deltas = {label: float(row[col] if pd.notna(row[col]) else 0.0) for label, col in sku_delta_cols.items()}
        signal = row.get("signal_type")
        if signal == "grower":
            ranked = [(k, v) for k, v in deltas.items() if v > 0]
            ranked.sort(key=lambda x: x[1], reverse=True)
        else:
            ranked = [(k, v) for k, v in deltas.items() if v < 0]
            ranked.sort(key=lambda x: x[1])
        if not ranked:
            return pd.Series(["No clear driver", 0.0, "flat", "No clear driver"])
        top_label, top_delta = ranked[0]
        direction = "growing" if top_delta > 0 else "declining"
        summary = ", ".join([f"{label} ({delta:+.2f})" for label, delta in ranked[:3]])
        return pd.Series([top_label, round(top_delta, 2), direction, summary])

    result[["top_ai_sku", "top_ai_sku_delta", "top_ai_sku_direction", "driving_skus"]] = result.apply(sku_drivers, axis=1)

    def first_name(full_name: str) -> str:
        if not full_name or str(full_name).strip() == "":
            return "there"
        return re.split(r"\s+", str(full_name).strip())[0]

    def build_message(row):
        who = first_name(row.get("ae_name"))
        trend = "increase" if row.get("signal_type") == "grower" else "decrease"
        return (
            f"Hey {who} 👋\n\n"
            f"I'm supporting {segment_title} accounts from a GenAI GTM standpoint.\n\n"
            f"Noticed {row.get('account_name')} spent {round(float(row.get('total_t28d_spend', 0)), 2)} "
            f"GenAI DBUs in the past 28 days — a {round(float(row.get('pop_growth_pct', 0)), 2)}% {trend}.\n"
            f"Driver SKU(s): {row.get('driving_skus')}.\n\n"
            f"Because of this signal we've prioritized this account for specialized GenAI resources.\n\n"
            f"Happy to help with:\n"
            f"• Use case ideation\n"
            f"• SSA/EPL support\n"
            f"• Architecture review\n"
            f"• Competitive strategy\n\n"
            f"Let me know if you'd like help driving GenAI adoption here."
        )

    result["slack_message"] = result.apply(build_message, axis=1)
    result["segment_focus"] = segment_title
    result["workshop_flag"] = result["workshop_flag_int"].astype(int).astype(bool)
    result["caio_meeting_flag"] = result["caio_meeting_flag_int"].astype(int).astype(bool)

    export_cols = [
        "segment_focus", "signal_type", "rank_within_signal", "account_id", "account_name",
        "is_oversight_account", "oversight_requested_account", "oversight_match_score",
        "sales_subregion_level_1", "sales_subregion_level_2", "sales_subregion_level_3",
        "workshop_flag", "workshop_count",
        "caio_meeting_flag", "caio_meeting_count", "last_caio_meeting_date",
        "ae_name", "sa_name", "total_t28d_spend", "pop_growth_pct",
        "top_ai_sku", "top_ai_sku_delta", "top_ai_sku_direction", "driving_skus", "slack_message",
    ]
    export_df = result[export_cols].sort_values(["signal_type", "rank_within_signal"], ascending=[True, True])
    segment_exports[segment_focus] = export_df

    csv_uri = f"{weekly_dir}/weekly_account_pings_{segment_focus}_{run_date}.csv"
    md_uri = f"{weekly_dir}/weekly_account_pings_{segment_focus}_{run_date}.md"
    dbutils.fs.put(csv_uri, export_df.to_csv(index=False), True)

    md_parts = [
        f"# Weekly Account Pings - {segment_title} ({run_date})\n",
        f"- Segment focus: {segment_title}",
        f"- Top N per signal: {TOP_N}",
        f"- Minimum T28D spend: {MIN_T28D_SPEND}\n",
    ]
    for signal in ["grower", "decliner"]:
        subset = export_df[export_df["signal_type"] == signal].copy()
        md_parts.append(f"## Top {TOP_N} {signal.title()}s\n")
        if subset.empty:
            md_parts.append("No accounts matched.\n")
        else:
            view = subset[
                [
                    "rank_within_signal", "account_name", "is_oversight_account", "oversight_requested_account",
                    "sales_subregion_level_1", "ae_name", "sa_name", "total_t28d_spend",
                    "workshop_flag", "caio_meeting_flag", "caio_meeting_count", "last_caio_meeting_date",
                    "pop_growth_pct", "top_ai_sku", "top_ai_sku_delta", "driving_skus",
                ]
            ].rename(columns={"sales_subregion_level_1": "sales_subregion"})
            md_parts.append(to_markdown_table(view))
            md_parts.append("")
    dbutils.fs.put(md_uri, "\n".join(md_parts).strip() + "\n", True)
    print(f"Wrote CSV: {csv_uri}")
    print(f"Wrote MD: {md_uri}")
    print(f"Total rows ({segment_title}): {len(export_df)}")
    if export_df.empty:
        print(f"No rows matched current filters for {segment_title}.")
    else:
        spark.createDataFrame(export_df).display()

# COMMAND ----------
# Oversight output for a fixed account subset (T7D + T28D side-by-side)

def sql_quote_lower(s: str) -> str:
    return "'" + s.lower().replace("'", "''") + "'"


if OVERSIGHT_ACCOUNTS:
    candidate_sql = """
    WITH latest AS (
      SELECT
        account_id,
        account_name,
        account_executive AS ae_name,
        last_solution_architect_engaged AS sa_name,
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
      WHERE lower(COALESCE(account_segment, '')) LIKE '%strategic%'
         OR lower(COALESCE(account_segment, '')) LIKE '%named%'
    )
    SELECT
      account_id,
      account_name,
      ae_name,
      sa_name,
      account_segment,
      sales_subregion_level_1,
      sales_subregion_level_2,
      sales_subregion_level_3
    FROM latest
    WHERE rn = 1
    """
    candidate_df = spark.sql(candidate_sql).toPandas()

    def segment_bonus(seg: str) -> float:
        seg_l = (seg or "").lower()
        if "strategic" in seg_l:
            return 0.20
        if "named" in seg_l:
            return 0.12
        return 0.0

    def name_score(req_name: str, cand_name: str, cand_segment: str) -> float:
        req_norm = normalize_name(req_name)
        cand_norm = normalize_name(cand_name)
        if req_norm == cand_norm:
            return 1.5 + segment_bonus(cand_segment)

        req_simple = simplify_name(req_name)
        cand_simple = simplify_name(cand_name)
        seq = SequenceMatcher(None, req_simple, cand_simple).ratio()
        req_tokens = token_set(req_name)
        cand_tokens = token_set(cand_name)
        jac = 0.0
        if req_tokens or cand_tokens:
            jac = len(req_tokens & cand_tokens) / max(1, len(req_tokens | cand_tokens))
        contains = 0.15 if (req_simple in cand_simple or cand_simple in req_simple) else 0.0
        return (0.65 * seq) + (0.20 * jac) + contains + segment_bonus(cand_segment)

    matches = []
    for requested in OVERSIGHT_ACCOUNTS:
        best = None
        best_score = -1.0
        for _, c in candidate_df.iterrows():
            score = name_score(requested, c["account_name"], c["account_segment"])
            if score > best_score:
                best_score = score
                best = c
        if best is not None and best_score >= 0.55:
            matches.append(
                {
                    "requested_account": requested,
                    "matched_account_id": best["account_id"],
                    "matched_account_name": best["account_name"],
                    "match_score": round(float(best_score), 4),
                }
            )

    match_df = pd.DataFrame(matches)
    matched_account_ids = sorted(match_df["matched_account_id"].dropna().astype(str).unique().tolist()) if not match_df.empty else []

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
          d.account_executive AS ae_name,
          d.last_solution_architect_engaged AS sa_name,
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
                match_df,
                left_on="account_id",
                right_on="matched_account_id",
                how="left",
            )
        oversight_df["requested_account"] = oversight_df["requested_account"].fillna(oversight_df["account_name"])
    else:
        oversight_df = pd.DataFrame(
            columns=[
                "requested_account",
                "account_name",
                "ae_name",
                "sa_name",
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
            ]
        )

    oversight_cols = [
        "requested_account",
        "account_name",
        "match_score",
        "ae_name",
        "sa_name",
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
    ]
    oversight_export = oversight_df[oversight_cols].copy()
    oversight_export = oversight_export.sort_values(["requested_account", "t28d_spend"], ascending=[True, False]).drop_duplicates(
        subset=["requested_account"], keep="first"
    )

    oversight_csv_uri = f"{weekly_dir}/oversight_t7d_t28d_{run_date}.csv"
    oversight_md_uri = f"{weekly_dir}/oversight_t7d_t28d_{run_date}.md"
    dbutils.fs.put(oversight_csv_uri, oversight_export.to_csv(index=False), True)

    matched_requested = set(oversight_export["requested_account"].tolist()) if not oversight_export.empty else set()
    unmatched = [x for x in OVERSIGHT_ACCOUNTS if x not in matched_requested]
    oversight_md = [
        f"# Oversight Accounts T7D + T28D ({run_date})",
        "",
        f"- Requested accounts: {len(OVERSIGHT_ACCOUNTS)}",
        f"- Matched accounts: {len(oversight_export)}",
        f"- Unmatched accounts: {len(unmatched)}",
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

    if oversight_export.empty:
        print("No oversight accounts matched Strategic/Named candidates.")
    else:
        spark.createDataFrame(oversight_export).display()
