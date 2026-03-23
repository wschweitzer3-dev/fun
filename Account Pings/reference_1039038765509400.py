# Databricks notebook source
# MAGIC %md
# MAGIC
# MAGIC ### Getting data
# MAGIC
# MAGIC Child rollup etc

# COMMAND ----------

df_out = spark.sql(f"""
          with
deployables as (
select distinct 
  case when deployable_account_id in ('0016100000U5Dm2AAF') then '0018Y00002fql6EQAQ' else deployable_account_id end as account_id,  --manual override for Biogen data issue
  case when deployable_account_id in ('0014N00002BBPlRQAX') then 'Femsa' else deployable_account_name end as account_name,
  deployable_account_id,
  case when deployable_account_id in ('0014N00002BBPlRQAX') then 'Femsa' else deployable_account_name end as deployable_account_name,
  business_unit,
  sales_subregion_level_1,
  ultimate_parent_dbx,
  target_account
from main.gtm_silver.account_dim
where dbx in ('Strategic')
and sales_subregion_level_1 in ('HLS', 'FINS', 'PS', 'MFG')
group by all
),
child_accounts as (
select distinct
  a.account_id,
  a.account_name,
  a.deployable_account_id,
  a.deployable_account_name,
  a.business_unit,
  a.sales_subregion_level_1,
  a.ultimate_parent_dbx,
  target_account
from main.gtm_silver.account_dim a 
where a.account_id not in (select deployable_account_id from deployables)
--and target_account like '%Tier 1 - Lighthouse%' or target_account like '%Tier 2 - Reactive%'
and account_name is not null
and dbx in ('Strategic')
and sales_subregion_level_1 in ('HLS', 'FINS', 'PS', 'MFG')
group by all
),
base_account_table_prep as (
select distinct
  *
from (
  select 
    account_id,
    account_name,
    business_unit,
    sales_subregion_level_1,
    ultimate_parent_dbx,
    case when deployable_account_id in ('0016100000U5Dm2AAF') then '0018Y00002fql6EQAQ' else deployable_account_id end as deployable_account_id, --manual override for Biogen data issue
    deployable_account_name
    -- CASE 
    --     WHEN MAX(CASE WHEN target_account LIKE '%Lighthouse%' THEN 1 ELSE 0 END) = 1 THEN 'Tier 1 - Lighthouse'
    --     ELSE 'Tier 2 - Reactive' 
    -- END AS target_account_tier
  from deployables
  group by all
  union all
  select
    account_id,
    account_name,
     business_unit,
  sales_subregion_level_1,
  ultimate_parent_dbx,
    case when deployable_account_id in ('0016100000U5Dm2AAF') then '0018Y00002fql6EQAQ' else deployable_account_id end as deployable_account_id, --manual override for Biogen data issue
    deployable_account_name
    -- CASE 
    --     WHEN MAX(CASE WHEN target_account LIKE '%Lighthouse%' THEN 1 ELSE 0 END) = 1 THEN 'Tier 1 - Lighthouse'
    --     ELSE 'Tier 2 - Reactive' 
    -- END AS target_account_tier
  from child_accounts
  group by all
))
,
base_account_table_unique as (
select 
distinct deployable_Account_id,
deployable_account_name,
business_unit,
sales_subregion_level_1
from base_account_table_prep
where sales_subregion_level_1 in ('HLS', 'FINS', 'PS', 'MFG')
),

childs as (
select 
coalesce(a.parent_account_id, a.account_id) as parent_account_id,
a.parent_account_name,
a.account_id as account_id,
a.account_name,
a.account_status,
a.business_unit as region_grain,
a.business_unit,
a.sales_subregion_level_1,
a.sales_subregion_level_2,
a.sales_subregion_level_3,
a.t3m_annualized,
a.industry,
a.account_executive,
a.last_solution_architect_engaged,
a.dbx,
a.global_top_account,
a.is_t200GenAI_ind
from main.gtm_silver.account_dim a 
)

select * from base_account_table_unique 
left join childs on base_account_table_unique.deployable_account_id = childs.parent_account_id
""")

# COMMAND ----------

weekly_consumption = spark.sql(f"""

with consumption as (
select 
account_id as customer, 
account_name as customer_name,
min(usage_date) as week_ts,
floor(datediff(current_date()-2, min(usage_date)) / 7) as week_number,
approx_count_distinct(usage_date) as n_days,
sum(dbu_dollars) as dbu_dollars,
sum(uc_dbu_dollars) as uc_dbu_dollars,
sum(serverless_dbu_dollars) as serverless_dbu_dollars,
sum(ml_dbu_dollars) as ml_dbu_dollars,
sum(genai_mct_dbu_dollars) as genai_mct_dbu_dollars,
sum(ai_dbu_dollars) as sales,
sum(genai_foundation_model_api_dbu_dollars) as genai_foundation_model_api_dbu_dollars,
sum(genai_gpu_model_serving_dbu_dollars) as genai_gpu_model_serving_dbu_dollars,
sum(genai_cpu_model_serving_dbu_dollars) as genai_cpu_model_serving_dbu_dollars,
sum(genai_throughput_model_serving_dbu_dollars) as genai_throughput_model_serving_dbu_dollars,
sum(genai_foundation_model_training_dbu_dollars) as genai_foundation_model_training_dbu_dollars,
sum(genai_vector_search_dbu_dollars) as genai_vector_search_dbu_dollars,
sum(genai_gpu_model_serving_dbu_dollars)+sum(genai_cpu_model_serving_dbu_dollars)+sum(genai_throughput_model_serving_dbu_dollars)+sum(genai_foundation_model_api_dbu_dollars) as all_serving_dbu_dollars
from main.gtm_gold.account_consumption_daily
where usage_date >= '2025-06-01'
group by floor(datediff(current_date()-2, usage_date) / 7), account_id, account_name
),

filtered_consumption as (
select *,
row_number() over (partition by customer order by week_number) as rn
from consumption
)

select * from filtered_consumption 
          """)

daily_consumption = spark.sql(f"""

with consumption as (
select 
account_id as customer, 
account_name as customer_name,
usage_date,
floor(datediff(current_date()-2, usage_date) / 7) as week_number,
sum(dbu_dollars) as dbu_dollars,
sum(serverless_dbu_dollars) as serverless_dbu_dollars,
sum(ml_dbu_dollars) as ml_dbu_dollars,
sum(genai_mct_dbu_dollars) as genai_mct_dbu_dollars,
sum(ai_dbu_dollars) as genai_non_mct_dbu_dollars,
sum(genai_foundation_model_api_dbu_dollars) as genai_foundation_model_api_dbu_dollars,
sum(genai_gpu_model_serving_dbu_dollars) as genai_gpu_model_serving_dbu_dollars,
sum(genai_cpu_model_serving_dbu_dollars) as genai_cpu_model_serving_dbu_dollars,
sum(genai_throughput_model_serving_dbu_dollars) as genai_throughput_model_serving_dbu_dollars,
sum(genai_foundation_model_training_dbu_dollars) as genai_foundation_model_training_dbu_dollars,
sum(genai_vector_search_dbu_dollars) as genai_vector_search_dbu_dollars,
sum(genai_gpu_model_serving_dbu_dollars)+sum(genai_cpu_model_serving_dbu_dollars)+sum(genai_throughput_model_serving_dbu_dollars)+sum(genai_foundation_model_api_dbu_dollars) as all_serving_dbu_dollars
from main.gtm_gold.account_consumption_daily
where usage_date >= '2025-06-01'
group by usage_date, account_id, account_name
)

select * from consumption 
          """)

# COMMAND ----------

#joining child consumption

specified_list = [
'Banco do Brasil [Deleted or Merged]',
'Samara Brothers Inc.',
'Box',
'Okta, Inc.',
'Mercado Libre',
'Chime Bank',
'Yelp',
'iFood',
'Santander Brazil'
]

df_weekly_b = df_out.join(
    weekly_consumption,
    (df_out.account_id == weekly_consumption.customer) |
    ((df_out.deployable_account_name.isin(specified_list)) & (df_out.deployable_Account_id == weekly_consumption.customer)),
    'inner'
)

df_daily = df_out.join(
    daily_consumption,
    (df_out.account_id == daily_consumption.customer) |
    ((df_out.deployable_account_name.isin(specified_list)) & (df_out.deployable_Account_id == daily_consumption.customer)),
    'inner'
)

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ### Get child account name mappings - who is driving consumption?

# COMMAND ----------

import pyspark.sql.functions as F
from pyspark.sql.window import Window

# Step 1: Aggregate total spend per account under each deployable_account_name
account_spend = df_daily.groupBy("deployable_account_name", "account_name", "account_executive", "last_solution_architect_engaged") \
    .agg(F.sum("genai_non_mct_dbu_dollars").alias("total_sales"))

# Step 2: Find top spender per deployable_account_name
window_spec = Window.partitionBy("deployable_account_name").orderBy(F.desc("total_sales"))

top_spender = account_spend.withColumn("rank", F.rank().over(window_spec)) \
    .filter((F.col("rank") == 1) | (F.col("rank") == 2)| (F.col("rank") == 3)) \
    .select("deployable_account_name", 
            F.col("account_name").alias("top_spender"),  "account_executive", "last_solution_architect_engaged",
            "total_sales")

top_spender.display()

# Step 3: Aggregate child account names and other fields
account_details = df_daily.groupBy("deployable_account_name").agg(
    F.concat_ws(", ", F.collect_set("account_name")).alias("account_names"),
    F.collect_set("account_executive").alias("account_executives"),
    F.collect_set("last_solution_architect_engaged").alias("last_solution_architects_engaged")
)

# Step 4: Join both tables
result = account_details.join(top_spender, on="deployable_account_name", how="left")\
    .filter(F.col('total_sales')>0)

# Step 5: Show result
result.display()


# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ### Plotting and forecasting weekly consumption

# COMMAND ----------

import pyspark.sql.functions as F
df = df_weekly_b.groupby(F.col('deployable_account_name'), F.col('week_ts'), F.col('rn')).agg(F.sum('sales').alias('sales'), F.sum('uc_dbu_dollars').alias('uc_dbu_dollars'), F.sum('serverless_dbu_dollars').alias('serverless_dbu_dollars')).orderBy(F.col('week_ts'), F.col('rn'), F.col('deployable_account_name'))

# COMMAND ----------

df.display()

# COMMAND ----------

#building forecasts

import numpy as np
from pyspark.sql.functions import udf
from pyspark.sql.types import FloatType
from sklearn.linear_model import LinearRegression

# Define a UDF to fit a regression line and return the slope
def fit_regression_line(weeks, sales, n_weeks):
    if len(weeks) < 2 or n_weeks <= 0:
        return float('nan')
    
    # Convert to numpy arrays
    weeks = np.array(weeks).reshape(-1, 1)
    sales = np.array(sales)

    # Ensure we do not slice more than available data
    n_weeks = min(n_weeks, len(weeks))
    
    model = LinearRegression().fit(weeks[1:n_weeks+1], sales[1:n_weeks+1][::-1])
    return float(model.coef_[0])

# Register UDF
fit_regression_line_udf = udf(fit_regression_line, FloatType())

# Group by account_name and collect weeks & sales into lists
df_grouped = df.groupBy("deployable_account_name").agg(
    F.sort_array(F.collect_list(F.struct("rn", "week_ts", "sales", "uc_dbu_dollars", "serverless_dbu_dollars"))).alias("sorted_data")
)

df_grouped = df_grouped.withColumn("weeks", F.expr("slice(transform(sorted_data, x -> x.rn), 1, size(sorted_data) - 1)")) \
                       .withColumn("weeks_ts", F.expr("slice(transform(sorted_data, x -> x.week_ts), 1, size(sorted_data) - 1)")) \
                       .withColumn("sales", F.expr("slice(transform(sorted_data, x -> x.sales), 1, size(sorted_data) - 1)")) \
                       .withColumn("uc_dbu_dollars", F.expr("slice(transform(sorted_data, x -> x.uc_dbu_dollars), 1, size(sorted_data) - 1)")) \
                        .withColumn("serverless_dbu_dollars", F.expr("slice(transform(sorted_data, x -> x.serverless_dbu_dollars), 1, size(sorted_data) - 1)")) \
                       .drop("sorted_data")

# Apply the UDF with `F.lit(n_weeks)` to avoid indexing issues
df_with_slope = df_grouped.withColumn("sales_slope_2", fit_regression_line_udf("weeks", "sales", F.lit(2)))
df_with_slope = df_with_slope.withColumn("sales_slope_4", fit_regression_line_udf("weeks", "sales", F.lit(4)))
df_with_slope = df_with_slope.withColumn("sales_slope_6", fit_regression_line_udf("weeks", "sales", F.lit(6)))

# Filter out "Banco do Brasil [Deleted or Merged]"
df_with_slope = df_with_slope.filter(F.col("deployable_account_name") != "Banco do Brasil [Deleted or Merged]")

# Display result
df_with_slope.display()

# COMMAND ----------

df_with_slope.display()

# COMMAND ----------

import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from datetime import timedelta
 
# Define thresholds
thresholds = [1000, 5000, 10000, 50000]

# Convert PySpark DataFrame to Pandas
df_pandas = df_with_slope.toPandas()

# Get current date for comparison
current_date = pd.Timestamp.now()

unvalidated_hit_list = {}

# Loop through each account
for _, row in df_pandas.iterrows():
    account_name = row["deployable_account_name"]
    sales = np.array(row["sales"])
    uc_dbus = np.array(row["uc_dbu_dollars"])
    serverless_dbu_dollars = np.array(row["serverless_dbu_dollars"])
    if uc_dbus[:5].sum() < 1000 or serverless_dbu_dollars[:5].sum() < 1000 or sales[:5].sum() < 1:
      print(f"\nUC unactivated: {account_name}")
      if sales[:5].sum() > 0:
        print(f"\nUC spend: {uc_dbus[:5].sum()} T28D")
        print(f"\nServerless spend: {serverless_dbu_dollars[:5].sum()} T28D")
        print(f"\nbut has GenAI spend: {sales[:5].sum()} T28D")
      continue

    
    print(f"\nProcessing: {account_name}")

    # Convert weeks_ts from string to datetime format
    weeks_ts = np.array([pd.to_datetime(ts) for ts in row["weeks_ts"]])  
    

    

    # Get last actual week timestamp
    last_week_ts = weeks_ts[1]

    # Generate future timestamps (5 weeks ahead)
    future_weeks_ts = np.array([last_week_ts + timedelta(weeks=i) for i in range(0, 5)], dtype="datetime64")

    # Compute projected sales using regression slopes
    future_sales_6 = sales[1] + row["sales_slope_6"] * np.arange(0, 5)
    future_sales_4 = sales[1] + row["sales_slope_4"] * np.arange(0, 5)
    future_sales_2 = sales[1] + row["sales_slope_2"] * np.arange(0, 5)
    future_sales_avg = (row["sales_slope_6"] + row["sales_slope_4"] + row["sales_slope_2"]) / 3
    future_sales = sales[1] + future_sales_avg * np.arange(0, 5)

    # Combine actual and forecasted data
    full_weeks = np.concatenate((future_weeks_ts[1:][::-1], weeks_ts[1:]))  # Avoid duplicate last actual week
    full_sales_4 = np.concatenate((future_sales[1:][::-1], sales[1:]))

    # Create DataFrame for rolling average
    df = pd.DataFrame({"week": full_weeks, "sales_4w": full_sales_4})
    df.sort_values("week", inplace=True)

    # Compute 4-week rolling average
    df["rolling_sum_4w"] = df["sales_4w"].rolling(window=4, min_periods=1).sum()

    # Detect FIRST time the rolling avg crosses each threshold
    print(f"Current T28D: {sales[:5].sum()}")
    print(f"Current T7D: {sales[:2].sum()}")

    has_cross = False
    for threshold in thresholds:
        prev_below = df["rolling_sum_4w"].shift(1) < threshold  # Previous value below threshold
        curr_above = df["rolling_sum_4w"] >= threshold  # Current value at/above threshold
        
        crossing_weeks = df.loc[prev_below & curr_above, "week"]
        
        if not crossing_weeks.empty:            
            print(f"🟢 {account_name} crosses ABOVE {threshold} at week(s): {crossing_weeks.dt.strftime('%Y-%m-%d').tolist()}")
            # Check if any crossing dates are more than 9 days old
            days_ago = (current_date - crossing_weeks).dt.days
            if (days_ago < 9).any():
                has_cross = True
                if threshold == 10000:
                  unvalidated_hit_list[account_name] = "ABOVE {}".format(threshold)
                
    # Detect FIRST time the rolling avg crosses each threshold
    for threshold in thresholds:
        prev_below = df["rolling_sum_4w"].shift(1) > threshold  # Previous value above threshold
        curr_above = df["rolling_sum_4w"] <= threshold  # Current value at/below threshold
        
        crossing_weeks = df.loc[prev_below & curr_above, "week"]
        
        if not crossing_weeks.empty:
            print(f"🔴 {account_name} crosses BELOW {threshold} at week(s): {crossing_weeks.dt.strftime('%Y-%m-%d').tolist()}")
            # Check if any crossing dates are more than 9 days old
            days_ago = (current_date - crossing_weeks).dt.days
            if (days_ago < 9).any():
                has_cross = True
                if threshold == 10000:
                  unvalidated_hit_list[account_name] = "BELOW {}".format(threshold)

    
    if has_cross:
      # --- Plot Only Actual & Forecast Data ---
      plt.figure(figsize=(10, 6))
      plt.plot(weeks_ts[1:], sales[1:], marker="o", linestyle="-", label="Actual Sales")
      plt.plot(future_weeks_ts, future_sales_4, "r--", label="Forecast 4W")
      plt.plot(future_weeks_ts, future_sales_6, "g--", label="Forecast 6W")
      plt.plot(future_weeks_ts, future_sales_2, "b--", label="Forecast 2W")

      # Customize the plot
      plt.xlabel("Week Start Date")
      plt.ylabel("Non-MCT Spend")
      plt.title(f"{account_name} Sales Forecast")
      plt.legend()
      plt.grid()

      # Show the plot
      plt.show()

# COMMAND ----------

customers_df = spark.createDataFrame(
    [(name,) for name in unvalidated_hit_list.keys()], 
    ["customer_name"]
)

unvalidated_str = ", ".join([f"'{name}'" for name in unvalidated_hit_list.keys()])

# COMMAND ----------

def simple_conditional_join(daily_consumption, customers_df):
    """
    Simple and practical - uses left_anti to find unmatched records
    """
    # Join 1: sfdc_account_name matches
    sfdc_joined = daily_consumption.join(
        customers_df,
        how='inner',
        on=(daily_consumption["sfdc_account_name"] == customers_df["customer_name"])
    )
    
    # Find records that didn't match in first join
    not_matched_sfdc = daily_consumption.join(
        customers_df,
        how='left_anti', 
        on=(daily_consumption["sfdc_account_name"] == customers_df["customer_name"])
    )
    
    # Join 2: parent_account_name matches for unmatched records
    deployable_joined = not_matched_sfdc.join(
        customers_df,
        how='inner',
        on=(not_matched_sfdc["deployable_account_name"] == customers_df["customer_name"])
    )
    
    # Combine results
    filt_daily = sfdc_joined.union(deployable_joined)
    return filt_daily

# COMMAND ----------

customers_df.display()

# COMMAND ----------

daily_consumption = spark.sql(
  f"""
  SELECT 
    paid_usage_metering.bu,
    paid_usage_metering.sub_bu,
    paid_usage_metering.date,
    paid_usage_metering.sfdc_account_id,
    paid_usage_metering.sfdc_account_name,
    account_dim.deployable_account_name,
    account_dim.parent_account_name,
    account_dim.ultimate_parent_dbx,
    account_dim.account_executive,
    account_dim.sales_manager,
    account_dim.last_solution_architect_engaged,
    sum(sales_comp_metric.foundation_model_training_dollars) as fine_tuning_api_dollars,
    sum(sales_comp_metric.cpu_model_serving_dollars) as ml_serving_serverless_cpu_dollars,
    sum(sales_comp_metric.gpu_model_serving_dollars) as ml_serving_serverless_gpu_dollars,
    sum(sales_comp_metric.foundation_model_api_dollars) as foundation_model_api_dollars,
    sum(sales_comp_metric.throughput_model_serving_dollars) as foundation_model_api_pt_dollars,
    sum(sales_comp_metric.anthropic_foundation_model_api_dollars) as anthropic_foundation_model_api_dollars,
    sum(sales_comp_metric.anthropic_throughput_model_serving_dollars) as anthropic_foundation_model_api_pt_dollars,
    SUM(sales_comp_metric.batch_inference_model_serving_dollars) AS batch_inference_model_serving_dollars,
    sum(sales_comp_metric.vector_search_dollars) as vector_search_dollars,
    sum(sales_comp_metric.agent_evaluation_dollars) as agent_evaluation_dollars,
    sum(sales_comp_metric.agent_eval_synthetic_data_gen_dollars) as agent_eval_synthetic_data_gen_dollars,
    sum(sales_comp_metric.agent_framework_dollars) as agent_framework_dollars,
    sum(sales_comp_metric.ai_runtime_dollars) as ai_runtime_dollars,
    sum(case when product_type = 'AI_GATEWAY' then usage_dollars else 0 end) as ai_gateway_dollars,
    sum(sales_comp_metric.fy26_ai_dollars + sales_comp_metric.ai_runtime_dollars + sales_comp_metric.anthropic_foundation_model_api_dollars + sales_comp_metric.anthropic_throughput_model_serving_dollars) as ai_dollars
  FROM
    main.fin_live_gold.paid_usage_metering
  LEFT JOIN main.gtm_silver.account_dim 
    ON account_dim.account_id = paid_usage_metering.sfdc_account_id
  WHERE
    paid_usage_metering.date BETWEEN date_sub(current_date(), 40) AND current_date()
  GROUP BY ALL
  """
)

filt_daily = simple_conditional_join(daily_consumption, customers_df)
# filt_daily.display()

# COMMAND ----------

# DBTITLE 1,Cell 20
import pyspark.sql.functions as F
import matplotlib.pyplot as plt
import pandas as pd

# Columns that contain "dollars" (case‑insensitive)
dollar_cols = [c for c in filt_daily.columns if c != 'ai_dollars' and "dollars" in c.lower()]

# Use a more distinct color palette (e.g., tab20, which has more unique colors and less yellow)
color_palette = plt.cm.get_cmap('tab20', len(dollar_cols))
color_map = {col: color_palette(i) for i, col in enumerate(dollar_cols)}

# List of distinct parent accounts
parent_accounts = unvalidated_hit_list.keys()

# for parent in df_pandas["deployable_account_name"]:
for parent in parent_accounts:
    # trend = unvalidated_hit_list[parent]
    trend = ""
    print(f"Breakdown for {parent}: {trend}")
    # Filter to the current parent account
    df_parent = filt_daily.filter(F.col("deployable_account_name") == parent)
    # df_parent = filt_daily.filter(F.col("sfdc_account_name") == parent)

    # Convert to pandas for plotting
    pdf = df_parent.toPandas()
    
    if pdf.empty:
        # print(f"{parent} lost the plot")
        # Filter to the current parent account
        df_parent = filt_daily.filter(F.col("sfdc_account_name") == parent)
        # df_parent = filt_daily.filter(F.col("parent_account_name") == parent)
        pdf = df_parent.toPandas()
        if pdf.empty:
            print(f"{parent} really lost the plot")
            continue
    print(pdf['account_executive'].iloc[0])
    print(pdf['last_solution_architect_engaged'].iloc[0])

    # Calculate and print T28D total consumption
    pdf['date'] = pd.to_datetime(pdf['date'])
    cutoff_28d = pdf['date'].max() - pd.Timedelta(days=27)
    t28d_total = pdf.loc[pdf['date'] >= cutoff_28d, 'ai_dollars'].sum()
    print(f"T28D AI Consumption: ${t28d_total:,.2f}")
    
    # Determine which dollar columns have a total > 0 for this parent
    cols_to_plot = [c for c in dollar_cols if pdf[c].sum() > 0]

    if not cols_to_plot:
        continue

    # Date already converted above
    pdf = pdf.sort_values('date')

    # Create stacked bar chart
    plt.figure(figsize=(12, 6))
    
    # Set up the bottom values for stacking
    bottom = None
    
    for col in cols_to_plot:
        if bottom is None:
            # First layer - no bottom offset
            plt.bar(pdf["date"], pdf[col], label=col, color=color_map[col], alpha=0.8)
            bottom = pdf[col]
        else:
            # Subsequent layers - stack on top
            plt.bar(pdf["date"], pdf[col], bottom=bottom, label=col, color=color_map[col], alpha=0.8)
            bottom += pdf[col]

    plt.title(f"Dollar Metrics for {parent}")
    plt.xlabel("Date")
    plt.ylabel("Dollars")
    plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')  # Legend outside plot
    plt.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()

# COMMAND ----------

