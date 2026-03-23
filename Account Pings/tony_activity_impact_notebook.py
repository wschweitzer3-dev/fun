# Databricks notebook source
# Tony Activity -> AI DBU Impact

from datetime import date
import pandas as pd

# COMMAND ----------
# Widgets

dbutils.widgets.text("owner_email", "tony.dang@databricks.com")
dbutils.widgets.text("fiscal_year", "FY26,FY27")
dbutils.widgets.text("run_date", "")
dbutils.widgets.text("output_base", "dbfs:/FileStore/account_pings")

OWNER_EMAIL = dbutils.widgets.get("owner_email").strip().lower()
FISCAL_YEAR = dbutils.widgets.get("fiscal_year").strip()
RUN_DATE_INPUT = dbutils.widgets.get("run_date").strip()
OUTPUT_BASE = dbutils.widgets.get("output_base").rstrip("/")

if RUN_DATE_INPUT:
    RUN_DATE_EXPR = f"DATE('{RUN_DATE_INPUT}')"
    RUN_DATE_STR = RUN_DATE_INPUT
else:
    RUN_DATE_EXPR = "current_date()"
    RUN_DATE_STR = date.today().isoformat()

FY_LIST = [x.strip().upper() for x in FISCAL_YEAR.split(",") if x.strip()]
FY_VALUES_SQL = ", ".join(["'" + x.replace("'", "''") + "'" for x in FY_LIST])

# COMMAND ----------
# Core query:
# 1) Pull Will's activity rows from users.jairo_ammirati.csm_dash
# 2) Parse activity_date1 safely
# 3) Compute AI DBU T28D at touch date (touch_date-29 .. touch_date-2)
# 4) Compute current AI DBU T28D (run_date-29 .. run_date-2)
# 5) Compare touch T28D vs current T28D

impact_sql = f"""
WITH activity_base AS (
  SELECT
    csm.AccountId AS account_id,
    csm.sfdc_account_name AS account_name,
    csm.Activity_Type_CSE__c AS activity_type,
    csm.BDR_PSM_CSM AS owner_email,
    csm.fiscalQuarter,
    CAST(csm.fy_year AS STRING) AS fy_year,
    COALESCE(
      TRY_TO_DATE(TRIM(csm.activity_date1), 'M/d/yyyy'),
      TRY_TO_DATE(TRIM(csm.activity_date1), 'MM/dd/yyyy'),
      TRY_TO_DATE(TRIM(csm.activity_date1), 'yyyy-MM-dd'),
      TRY_TO_DATE(TRIM(csm.activity_date1))
    ) AS activity_date
  FROM users.jairo_ammirati.csm_dash csm
  WHERE lower(csm.BDR_PSM_CSM) = '{OWNER_EMAIL}'
    AND csm.Activity_Type_CSE__c IS NOT NULL
    AND (
      '{FISCAL_YEAR}' = ''
      OR upper(CAST(csm.fy_year AS STRING)) IN ({FY_VALUES_SQL})
      OR EXISTS (
        SELECT 1
        FROM (
          SELECT explode(array({FY_VALUES_SQL})) AS fy_token
        ) fy_tokens
        WHERE upper(csm.fiscalQuarter) LIKE concat('%', fy_tokens.fy_token, '%')
      )
    )
),
activity_rows AS (
  SELECT
    row_number() OVER (
      ORDER BY account_id, activity_date, activity_type, account_name
    ) AS activity_row_id,
    account_id,
    account_name,
    activity_type,
    owner_email,
    fiscalQuarter,
    fy_year,
    activity_date
  FROM activity_base
),
daily_ai AS (
  SELECT
    account_id,
    usage_date,
    SUM(COALESCE(ai_dbu_dollars,0) + COALESCE(genai_mct_dbu_dollars,0)) AS ai_dbu
  FROM main.gtm_gold.account_consumption_daily
  GROUP BY account_id, usage_date
),
current_t28 AS (
  SELECT
    account_id,
    SUM(ai_dbu) AS current_t28d_ai_dbu
  FROM daily_ai
  WHERE usage_date BETWEEN date_sub({RUN_DATE_EXPR}, 29) AND date_sub({RUN_DATE_EXPR}, 2)
  GROUP BY account_id
),
touch_t28 AS (
  SELECT
    a.activity_row_id,
    SUM(d.ai_dbu) AS touch_t28d_ai_dbu
  FROM activity_rows a
  LEFT JOIN daily_ai d
    ON a.account_id = d.account_id
   AND d.usage_date BETWEEN date_sub(a.activity_date, 29) AND date_sub(a.activity_date, 2)
  GROUP BY a.activity_row_id
),
impact AS (
  SELECT
    a.activity_row_id,
    a.account_id,
    a.account_name,
    a.activity_type,
    a.owner_email,
    a.activity_date,
    a.fiscalQuarter,
    a.fy_year,
    t.touch_t28d_ai_dbu,
    c.current_t28d_ai_dbu,
    COALESCE(c.current_t28d_ai_dbu, 0) - COALESCE(t.touch_t28d_ai_dbu, 0) AS ai_dbu_delta,
    CASE
      WHEN COALESCE(t.touch_t28d_ai_dbu, 0) > 0 THEN
        (
          (COALESCE(c.current_t28d_ai_dbu, 0) - COALESCE(t.touch_t28d_ai_dbu, 0))
          / COALESCE(t.touch_t28d_ai_dbu, 0)
        ) * 100
      ELSE NULL
    END AS ai_dbu_pct_change
  FROM activity_rows a
  LEFT JOIN touch_t28 t ON a.activity_row_id = t.activity_row_id
  LEFT JOIN current_t28 c ON a.account_id = c.account_id
),
first_touch_per_account AS (
  SELECT *
  FROM (
    SELECT
      i.*,
      row_number() OVER (
        PARTITION BY i.account_id
        ORDER BY i.activity_date ASC NULLS LAST, i.activity_row_id ASC
      ) AS rn
    FROM impact i
  )
  WHERE rn = 1
)
SELECT *
FROM impact
ORDER BY activity_date DESC, account_name
"""

first_touch_sql = f"""
WITH base AS ({impact_sql})
SELECT *
FROM (
  SELECT
    b.*,
    row_number() OVER (
      PARTITION BY b.account_id
      ORDER BY b.activity_date ASC, b.activity_row_id ASC
    ) AS rn
  FROM base b
)
WHERE rn = 1
ORDER BY activity_date ASC, account_name
"""

# COMMAND ----------
# Execute and export

try:
    impact_pdf = spark.sql(impact_sql).toPandas()
    first_touch_pdf = spark.sql(first_touch_sql).toPandas()
except Exception as e:
    err = str(e)
    if "users.jairo_ammirati" in err or "INSUFFICIENT_PERMISSIONS" in err or "PERMISSION" in err.upper():
        raise RuntimeError(
            "Missing access to users.jairo_ammirati.csm_dash. "
            "Please grant USE SCHEMA + SELECT or provide an accessible mirror view/table. "
            f"Original error: {err}"
        )
    raise

for col in ["touch_t28d_ai_dbu", "current_t28d_ai_dbu", "ai_dbu_delta", "ai_dbu_pct_change"]:
    if col in impact_pdf.columns:
        impact_pdf[col] = pd.to_numeric(impact_pdf[col], errors="coerce")
        if col != "ai_dbu_pct_change":
            impact_pdf[col] = impact_pdf[col].round(2)
        else:
            impact_pdf[col] = impact_pdf[col].round(2)

for col in ["touch_t28d_ai_dbu", "current_t28d_ai_dbu", "ai_dbu_delta", "ai_dbu_pct_change"]:
    if col in first_touch_pdf.columns:
        first_touch_pdf[col] = pd.to_numeric(first_touch_pdf[col], errors="coerce").round(2)

out_dir = f"{OUTPUT_BASE}/activity_impact"
dbutils.fs.mkdirs(out_dir)

impact_csv = f"{out_dir}/tony_activity_impact_all_touches_{RUN_DATE_STR}.csv"
impact_md = f"{out_dir}/tony_activity_impact_all_touches_{RUN_DATE_STR}.md"
first_csv = f"{out_dir}/tony_activity_impact_first_touch_{RUN_DATE_STR}.csv"
first_md = f"{out_dir}/tony_activity_impact_first_touch_{RUN_DATE_STR}.md"

def to_markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "No rows returned."
    headers = [str(c) for c in df.columns]
    rows = [[str(v) for v in row] for row in df.itertuples(index=False, name=None)]
    sep = ["---"] * len(headers)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)

# write csvs

dbutils.fs.put(impact_csv, impact_pdf.to_csv(index=False), True)
dbutils.fs.put(first_csv, first_touch_pdf.to_csv(index=False), True)

# write mds

impact_md_text = "\n".join([
    f"# Will Activity Impact - All Touches ({RUN_DATE_STR})",
    "",
    f"- owner_email: {OWNER_EMAIL}",
    f"- fiscal_year filter: {FISCAL_YEAR}",
    f"- current date comparison: {RUN_DATE_STR if RUN_DATE_INPUT else 'current_date()'}",
    "",
    to_markdown_table(impact_pdf.head(200)),
    "",
])

dbutils.fs.put(impact_md, impact_md_text, True)

first_md_text = "\n".join([
    f"# Will Activity Impact - First Touch Per Account ({RUN_DATE_STR})",
    "",
    f"- owner_email: {OWNER_EMAIL}",
    f"- fiscal_year filter: {FISCAL_YEAR}",
    f"- current date comparison: {RUN_DATE_STR if RUN_DATE_INPUT else 'current_date()'}",
    "",
    to_markdown_table(first_touch_pdf.head(200)),
    "",
])

dbutils.fs.put(first_md, first_md_text, True)

print(f"Wrote: {impact_csv}")
print(f"Wrote: {impact_md}")
print(f"Wrote: {first_csv}")
print(f"Wrote: {first_md}")
print(f"Rows (all touches): {len(impact_pdf)}")
print(f"Rows (first touch): {len(first_touch_pdf)}")

if impact_pdf.empty:
    print("No activity rows returned for the selected filters.")
else:
    spark.createDataFrame(impact_pdf).display()

if first_touch_pdf.empty:
    print("No first-touch rows returned for the selected filters.")
else:
    spark.createDataFrame(first_touch_pdf).display()
