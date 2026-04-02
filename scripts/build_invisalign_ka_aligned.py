#!/usr/bin/env python3
"""Build a KA aligned to existing Invisalign Genie story."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import sql as sql_svc


PROFILE = "DEFAULT"
WAREHOUSE_NAME = "Serverless Starter Warehouse"
GENIE_SPACE_ID = "01f12ab84fe81e3690ea089379537b87"
SCHEMA_FQN = "ai_specialist.invisalign_demo"
VOLUME_FQN = f"{SCHEMA_FQN}.provider_growth_playbook"
VOLUME_DBFS_PATH = "dbfs:/Volumes/ai_specialist/invisalign_demo/provider_growth_playbook"
KA_NAME = "Provider Growth Playbook KA"
KA_TILE_NAME = re.sub(r"[_-]{2,}", "_", re.sub(r"[^a-zA-Z0-9_-]", "_", KA_NAME.replace(" ", "_"))).strip("_-")

KA_SAMPLE_QUESTIONS = [
    "How should a rep upsell a GP Dentist from Invisalign Lite to Comprehensive?",
    "What is the best way to reactivate declining Midwest providers?",
    "How do we handle cost objections from providers?",
    "What messaging works for high-engagement providers who haven’t upgraded?",
    "What campaign should we run to recover GP Dentist case volume?",
]

VALIDATION_Q1 = "Which Midwest providers should we target for Comprehensive expansion, and what should reps say?"
VALIDATION_Q2 = "GP Dentist case volume is declining — how do we fix it?"


class BuildError(RuntimeError):
    pass


@dataclass
class ProviderMetric:
    provider_id: str
    provider_type: str
    region: str
    digital_engagement_score: int
    propensity_score: float
    expected_incremental_revenue: float
    has_comprehensive: bool
    primary_product: str
    product_count: int
    cases_last_30d: int
    cases_prior_30d: int
    cases_last_60d: int
    cases_prior_60d: int
    case_growth_rate_30d: float
    case_growth_rate_60d: float
    comprehensive_case_share: float
    lite_case_share: float
    express_case_share: float
    tier_1_target: bool
    tier_2_target: bool
    declining_provider: bool
    growing_provider: bool
    at_risk_flag: bool


def run_cmd(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if p.returncode != 0:
        raise BuildError(f"Command failed: {cmd}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout.strip()


def quote_fqn(fqn: str) -> str:
    return ".".join(f"`{x}`" for x in fqn.split("."))


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def choose_warehouse(w: WorkspaceClient) -> str:
    whs = list(w.warehouses.list())
    if not whs:
        raise BuildError("No SQL warehouses available.")
    for wh in whs:
        if wh.name == WAREHOUSE_NAME:
            return wh.id
    return whs[0].id


def execute_sql_rows(w: WorkspaceClient, warehouse_id: str, sql_text: str) -> list[dict[str, Any]]:
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql_text,
        wait_timeout="50s",
        format=sql_svc.Format.JSON_ARRAY,
    )
    if resp.status.state != sql_svc.StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status and resp.status.error else "Unknown SQL error"
        raise BuildError(f"SQL failed: {err}\nSQL:\n{sql_text}")

    if not resp.result or not resp.result.data_array:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in resp.result.data_array]


def http_json(
    w: WorkspaceClient,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    url = f"{w.config.host}{path}"
    resp = requests.request(method, url, headers=headers, json=body, params=params, timeout=timeout)
    if resp.status_code >= 400:
        raise BuildError(f"{method} {path} failed ({resp.status_code}): {resp.text}")
    return resp.json() if resp.text else {}


def list_tiles(w: WorkspaceClient, tile_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": 100, "filter": f"tile_type={tile_type}"}
        if page_token:
            params["page_token"] = page_token
        payload = http_json(w, "GET", "/api/2.0/tiles", params=params)
        out.extend(payload.get("tiles", []))
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return out


def parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.lower() in {"1", "true", "t", "yes", "y"}
    return False


def pfloat(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def pint(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default


def derive_action(pm: ProviderMetric) -> tuple[str, str]:
    if pm.region == "Midwest" and pm.provider_type == "GP Dentist" and pm.declining_provider:
        return ("Stabilize and Recover", "Comprehensive Recovery Bundle")
    if pm.tier_1_target and pm.case_growth_rate_30d > 0:
        return ("Fast-track Upgrade", "Comprehensive")
    if pm.propensity_score >= 0.8:
        return ("Prioritized Upsell", "Comprehensive")
    return ("Nurture Upgrade", "Comprehensive")


def sanitize_file_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", s)


def wrap_lines(txt: str, width: int = 96) -> list[str]:
    lines: list[str] = []
    for raw in txt.splitlines():
        if not raw.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(raw, width=width) or [""])
    return lines


def pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf(path: Path, title: str, body: str) -> None:
    lines = [title, ""] + wrap_lines(body, width=90)
    max_lines = 58
    lines = lines[:max_lines]
    content_parts = [
        "BT",
        "/F1 10 Tf",
        "50 760 Td",
        "14 TL",
    ]
    for idx, line in enumerate(lines):
        escaped = pdf_escape(line)
        if idx == 0:
            content_parts.append(f"({escaped}) Tj")
        else:
            content_parts.append(f"T* ({escaped}) Tj")
    content_parts.append("ET")
    stream = "\n".join(content_parts).encode("latin-1", errors="replace")

    objs: list[bytes] = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
    )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(
        f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
        + stream
        + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.4\n")
    xref_offsets = [0]
    for idx, obj in enumerate(objs, start=1):
        xref_offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("latin-1"))
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_start = len(out)
    out.extend(f"xref\n0 {len(objs)+1}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for off in xref_offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("latin-1")
    )
    path.write_bytes(bytes(out))


def metric_query(schema_fqn: str) -> str:
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
    MAX(CASE WHEN pp.is_primary THEN pr.product_name END) AS primary_product
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
    COALESCE(ps.propensity_score, 0.0) AS propensity_score,
    COALESCE(ps.expected_incremental_revenue, 0.0) AS expected_incremental_revenue,
    COALESCE(ppr.has_comprehensive_int, 0) AS has_comprehensive_int,
    COALESCE(ppr.primary_product, 'Unknown') AS primary_product,
    COALESCE(ppr.product_count, 0) AS product_count,
    COALESCE(cr.cases_last_30d, 0) AS cases_last_30d,
    COALESCE(cr.cases_prior_30d, 0) AS cases_prior_30d,
    COALESCE(cr.cases_last_60d, 0) AS cases_last_60d,
    COALESCE(cr.cases_prior_60d, 0) AS cases_prior_60d,
    CAST(COALESCE(cr.comprehensive_cases, 0) AS DOUBLE) / NULLIF(COALESCE(cr.total_cases, 0), 0) AS comprehensive_case_share,
    CAST(COALESCE(cr.lite_cases, 0) AS DOUBLE) / NULLIF(COALESCE(cr.total_cases, 0), 0) AS lite_case_share,
    CAST(COALESCE(cr.express_cases, 0) AS DOUBLE) / NULLIF(COALESCE(cr.total_cases, 0), 0) AS express_case_share
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
    CASE WHEN has_comprehensive_int = 1 THEN FALSE ELSE TRUE END AS upgrade_opportunity_flag,
    CASE WHEN propensity_score >= 0.6 THEN TRUE ELSE FALSE END AS high_propensity_flag,
    CASE WHEN provider_type = 'Orthodontist' AND cases_last_30d > 50 THEN TRUE ELSE FALSE END AS orthodontist_high_performer,
    CASE WHEN provider_type = 'GP Dentist' AND cases_last_30d > 15 THEN TRUE ELSE FALSE END AS gp_dentist_high_performer,
    CASE WHEN ((cases_last_30d - cases_prior_30d) / NULLIF(CAST(cases_prior_30d AS DOUBLE), 0)) > 0.05 THEN TRUE ELSE FALSE END AS growing_provider,
    CASE WHEN ((cases_last_60d - cases_prior_60d) / NULLIF(CAST(cases_prior_60d AS DOUBLE), 0)) < -0.15 THEN TRUE ELSE FALSE END AS declining_provider
  FROM base_metrics
)
SELECT
  provider_id,
  provider_type,
  region,
  digital_engagement_score,
  propensity_score,
  expected_incremental_revenue,
  CASE WHEN has_comprehensive_int = 1 THEN TRUE ELSE FALSE END AS has_comprehensive,
  primary_product,
  product_count,
  cases_last_30d,
  cases_prior_30d,
  cases_last_60d,
  cases_prior_60d,
  case_growth_rate_30d,
  case_growth_rate_60d,
  comprehensive_case_share,
  lite_case_share,
  express_case_share,
  CASE WHEN high_propensity_flag AND has_comprehensive_int = 0 AND growing_provider THEN TRUE ELSE FALSE END AS tier_1_target,
  CASE WHEN high_propensity_flag AND has_comprehensive_int = 0 THEN TRUE ELSE FALSE END AS tier_2_target,
  declining_provider,
  growing_provider,
  declining_provider AS at_risk_flag
FROM provider_metrics
"""


def segment_summary_query(schema_fqn: str) -> str:
    return f"""
WITH m AS ({metric_query(schema_fqn)})
SELECT
  provider_type,
  region,
  COUNT(*) AS providers,
  SUM(CASE WHEN tier_1_target THEN 1 ELSE 0 END) AS tier_1_count,
  SUM(CASE WHEN tier_2_target THEN 1 ELSE 0 END) AS tier_2_count,
  AVG(propensity_score) AS avg_propensity_score,
  AVG(case_growth_rate_30d) AS avg_growth_30d,
  AVG(case_growth_rate_60d) AS avg_growth_60d,
  SUM(expected_incremental_revenue) AS total_expected_incremental_revenue
FROM m
GROUP BY provider_type, region
ORDER BY provider_type, region
"""


def metadata_block(data: dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in data.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            safe = str(v).replace('"', '\\"')
            lines.append(f'{k}: "{safe}"')
    lines.append("---")
    return "\n".join(lines)


def dossier_text(pm: ProviderMetric) -> str:
    action, product_focus = derive_action(pm)
    return f"""
# Provider Dossier: {pm.provider_id}

## Snapshot
- Provider type: {pm.provider_type}
- Region: {pm.region}
- Primary product: {pm.primary_product}
- Product count: {pm.product_count}
- Digital engagement score: {pm.digital_engagement_score}
- Propensity score: {pm.propensity_score:.4f}
- Expected incremental revenue: ${pm.expected_incremental_revenue:,.2f}
- Case growth 30d: {pm.case_growth_rate_30d:.2%}
- Case growth 60d: {pm.case_growth_rate_60d:.2%}
- Comprehensive adoption: {"Yes" if pm.has_comprehensive else "No"}

## Recommended Action
{action} with focus on {product_focus}.

## Why This Works
The recommendation is tied to provider behavior in the structured data: momentum trend, product mix, and expansion propensity.

## Rep Guidance
- Anchor conversation on ROI using expected incremental revenue.
- Position Comprehensive as an operational simplifier with predictable outcomes.
- Use engagement score to tailor channel intensity.
""".strip()


def outreach_text(pm: ProviderMetric) -> str:
    cadence = "Week 1 email + same-day call, Week 2 follow-up call, Week 3 case-study email"
    if pm.region == "Midwest" and pm.provider_type == "GP Dentist":
        cadence = "Week 1 recovery email + call, Week 2 CE webinar invite, Week 3 in-person lunch-and-learn"
    return f"""
# Outreach Kit: {pm.provider_id}

## Email Script
Subject: Unlock additional case value with a targeted Comprehensive pathway

Hi Dr. Team,
Based on your current Invisalign mix and recent case trajectory, we see a focused opportunity to expand into Comprehensive for suitable cases. Similar providers are using this pathway to increase case value while keeping treatment planning predictable.

Could we schedule 20 minutes to review where Comprehensive can fit your next 10 candidate cases?

Best,
Your Align Team

## Call Script
\"I reviewed your recent case profile and noticed a concrete opportunity to expand selected cases into Comprehensive. If we walk through your next candidate list together, we can prioritize where this will improve value and treatment flexibility without disrupting workflow.\"

## Cadence
{cadence}

## Objection Handling
1. Cost concern: \"We phase adoption on candidate cases first so ROI is visible before broad rollout.\"
2. Workflow burden: \"We provide protocol templates and rep support to reduce staff lift.\"
3. Outcome caution: \"Case selection criteria keep treatment appropriateness and patient suitability central.\"
""".strip()


def segment_playbook_text(title: str, bullets: list[str], top_accounts: list[ProviderMetric]) -> str:
    account_lines = []
    for pm in top_accounts[:5]:
        account_lines.append(
            f"- {pm.provider_id}: propensity={pm.propensity_score:.3f}, expected_lift=${pm.expected_incremental_revenue:,.0f}, growth_60d={pm.case_growth_rate_60d:.1%}"
        )
    return f"""
# {title}

## Situation
{" ".join(bullets)}

## Priority Accounts (Top 5)
{os.linesep.join(account_lines)}

## Action Framework
- Sequence outreach by expected incremental revenue and propensity.
- Prioritize Comprehensive fit assessment for non-Comprehensive providers.
- Use growth/decline context to adjust message tone (expansion vs recovery).
""".strip()


def campaign_brief_text(title: str, summary_rows: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", "", "## Segment Summary"]
    for r in summary_rows:
        lines.append(
            f"- {r['provider_type']} / {r['region']}: providers={r['providers']}, tier_1={r['tier_1_count']}, tier_2={r['tier_2_count']}, avg_growth_60d={pfloat(r['avg_growth_60d']):.2%}, expected_lift=${pfloat(r['total_expected_incremental_revenue']):,.0f}"
        )
    lines.extend(
        [
            "",
            "## Campaign KPIs",
            "- Target meetings booked",
            "- Comprehensive candidate cases identified",
            "- Incremental revenue lift pipeline",
            "- 30/60 day case trend stabilization",
        ]
    )
    return "\n".join(lines)


def ask_genie(w: WorkspaceClient, question: str) -> dict[str, Any]:
    msg = w.genie.start_conversation_and_wait(
        space_id=GENIE_SPACE_ID,
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
        "rows": [],
        "text_response": None,
    }
    for att in msg.attachments or []:
        if att.query:
            out["sql"] = att.query.query
            if att.attachment_id:
                result = w.genie.get_message_query_result_by_attachment(
                    space_id=GENIE_SPACE_ID,
                    conversation_id=msg.conversation_id,
                    message_id=msg.id,
                    attachment_id=att.attachment_id,
                )
                sr = result.statement_response
                if sr and sr.manifest and sr.manifest.schema and sr.manifest.schema.columns:
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
    if isinstance(response, list):
        return "\n".join(extract_ka_text(x) for x in response)
    if isinstance(response, dict):
        for key in ("output_text", "text", "answer", "response"):
            if key in response and isinstance(response[key], str):
                return response[key]
        if "predictions" in response and isinstance(response["predictions"], list) and response["predictions"]:
            return extract_ka_text(response["predictions"][0])
        if "output" in response:
            return extract_ka_text(response["output"])
        if "choices" in response and isinstance(response["choices"], list):
            chunks: list[str] = []
            for c in response["choices"]:
                if isinstance(c, dict):
                    m = c.get("message", {})
                    if isinstance(m, dict) and isinstance(m.get("content"), str):
                        chunks.append(m["content"])
            if chunks:
                return "\n".join(chunks)
    return str(response)


def ask_ka(w: WorkspaceClient, endpoint_name: str, question: str) -> dict[str, Any]:
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    resp = requests.post(
        f"{w.config.host}/serving-endpoints/{endpoint_name}/invocations",
        headers=headers,
        json={"input": [{"role": "user", "content": question}]},
        timeout=300,
    )
    if resp.status_code >= 400:
        raise BuildError(f"KA invocation failed ({resp.status_code}): {resp.text}")
    raw = resp.json() if resp.text else {}
    return {"question": question, "response_text": extract_ka_text(raw), "raw_response": raw}


def endpoint_invocable(w: WorkspaceClient, endpoint_name: str) -> bool:
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    try:
        resp = requests.post(
            f"{w.config.host}/serving-endpoints/{endpoint_name}/invocations",
            headers=headers,
            json={"input": [{"role": "user", "content": "Ping"}]},
            timeout=60,
        )
        return resp.status_code < 400
    except Exception:
        return False


def assert_talktrack_format(text: str) -> dict[str, bool]:
    keys = {
        "Recommendation": bool(re.search(r"(^|\n)\s*1\.\s*Recommendation", text, flags=re.IGNORECASE)),
        "WhyItWorks": bool(re.search(r"(^|\n)\s*2\.\s*Why", text, flags=re.IGNORECASE)),
        "TalkTrack": bool(re.search(r"(^|\n)\s*3\.\s*Talk track", text, flags=re.IGNORECASE)),
        "ObjectionHandling": bool(re.search(r"(^|\n)\s*4\.\s*Objection", text, flags=re.IGNORECASE)),
        "OutreachStrategy": bool(re.search(r"(^|\n)\s*5\.\s*Outreach", text, flags=re.IGNORECASE)),
        "ComplianceNote": bool(re.search(r"(^|\n)\s*6\.\s*Compliance", text, flags=re.IGNORECASE)),
    }
    objection_count = len(re.findall(r"objection", text, flags=re.IGNORECASE))
    keys["AtLeastTwoObjectionsMentioned"] = objection_count >= 2
    return keys


def ensure_volume_and_dirs(w: WorkspaceClient, warehouse_id: str) -> None:
    execute_sql_rows(w, warehouse_id, f"CREATE CATALOG IF NOT EXISTS {SCHEMA_FQN.split('.')[0]}")
    execute_sql_rows(w, warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_FQN}")
    execute_sql_rows(w, warehouse_id, f"CREATE VOLUME IF NOT EXISTS {VOLUME_FQN}")


def generate_docs(
    metrics: list[ProviderMetric],
    segment_rows: list[dict[str, Any]],
    out_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    out_root.mkdir(parents=True, exist_ok=True)
    subdirs = ["segment_playbooks", "provider_dossiers", "outreach_kits", "campaign_briefs", "manifest"]
    for d in subdirs:
        (out_root / d).mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    counts = {"segment_playbooks_md": 0, "segment_playbooks_pdf": 0, "provider_dossiers_md": 0, "outreach_kits_md": 0, "campaign_briefs_pdf": 0}
    generated_rows: dict[str, dict[str, Any]] = {}

    top100 = sorted(
        [m for m in metrics if not m.has_comprehensive],
        key=lambda x: (x.expected_incremental_revenue, x.propensity_score),
        reverse=True,
    )[:100]

    midwest_gp = [m for m in top100 if m.provider_type == "GP Dentist" and m.region == "Midwest"]
    high_engagement_no_comp = [m for m in top100 if m.digital_engagement_score >= 70]
    tier1_accounts = [m for m in top100 if m.tier_1_target]
    outperformers = [m for m in metrics if (m.provider_type == "Orthodontist" and m.cases_last_30d > 50) or (m.provider_type == "GP Dentist" and m.cases_last_30d > 15)]

    segment_defs = [
        (
            "Midwest GP Decline Response Playbook",
            "midwest_gp_decline_response_playbook",
            [
                "Midwest GP Dentists show a concentrated decline in 60-day case trend and need a recovery-first motion before aggressive expansion.",
                f"High-propensity non-Comprehensive Midwest GP accounts in focus: {len(midwest_gp)} providers.",
            ],
            midwest_gp,
        ),
        (
            "Lite to Comprehensive Upsell Playbook",
            "lite_to_comprehensive_upsell_playbook",
            [
                "Primary upsell path is Lite to Comprehensive for providers with strong expected incremental revenue and positive or stabilizing trends.",
                f"Top no-Comprehensive accounts analyzed: {len(top100)} providers.",
            ],
            top100,
        ),
        (
            "High-Engagement Non-Comprehensive Conversion Playbook",
            "high_engagement_non_comprehensive_conversion_playbook",
            [
                "High digital engagement is a reliable leading indicator for consultative expansion conversations.",
                f"High-engagement candidates in scope: {len(high_engagement_no_comp)} providers.",
            ],
            high_engagement_no_comp,
        ),
        (
            "Benchmark Outperformance Replication Playbook",
            "benchmark_outperformance_replication_playbook",
            [
                "Outperformers provide reusable traits for coaching and campaign design.",
                f"Benchmark outperformers detected: {len(outperformers)} providers.",
            ],
            tier1_accounts or top100,
        ),
    ]

    for title, slug, bullets, cohort in segment_defs:
        md_path = out_root / "segment_playbooks" / f"{slug}.md"
        pdf_path = out_root / "segment_playbooks" / f"{slug}.pdf"
        md_meta = {
            "doc_id": slug,
            "doc_type": "segment_playbook_markdown",
            "provider_id": None,
            "provider_type": "Mixed",
            "region": "Mixed",
            "propensity_score": 0.0,
            "expected_incremental_revenue": 0.0,
            "case_growth_rate_30d": 0.0,
            "case_growth_rate_60d": 0.0,
            "has_comprehensive": False,
            "recommended_action": "Segment playbook strategy",
            "product_focus": "Comprehensive",
            "created_at": now_iso(),
        }
        body = segment_playbook_text(title, bullets, cohort)
        md_path.write_text(metadata_block(md_meta) + "\n\n" + body + "\n", encoding="utf-8")
        write_simple_pdf(pdf_path, title, body)
        manifest.append({"path": str(md_path.relative_to(out_root)), **md_meta})
        manifest.append({"path": str(pdf_path.relative_to(out_root)), **{**md_meta, "doc_type": "segment_playbook_pdf"}})
        counts["segment_playbooks_md"] += 1
        counts["segment_playbooks_pdf"] += 1

    for pm in top100:
        action, product_focus = derive_action(pm)
        doc_id = f"dossier_{pm.provider_id.lower()}"
        p = out_root / "provider_dossiers" / f"{sanitize_file_name(pm.provider_id)}.md"
        meta = {
            "doc_id": doc_id,
            "doc_type": "provider_dossier_markdown",
            "provider_id": pm.provider_id,
            "provider_type": pm.provider_type,
            "region": pm.region,
            "propensity_score": round(pm.propensity_score, 4),
            "expected_incremental_revenue": round(pm.expected_incremental_revenue, 2),
            "case_growth_rate_30d": round(pm.case_growth_rate_30d, 4),
            "case_growth_rate_60d": round(pm.case_growth_rate_60d, 4),
            "has_comprehensive": pm.has_comprehensive,
            "recommended_action": action,
            "product_focus": product_focus,
            "created_at": now_iso(),
        }
        body = dossier_text(pm)
        p.write_text(metadata_block(meta) + "\n\n" + body + "\n", encoding="utf-8")
        manifest.append({"path": str(p.relative_to(out_root)), **meta})
        counts["provider_dossiers_md"] += 1
        generated_rows[pm.provider_id] = meta

    for pm in top100:
        action, product_focus = derive_action(pm)
        doc_id = f"outreach_{pm.provider_id.lower()}"
        p = out_root / "outreach_kits" / f"{sanitize_file_name(pm.provider_id)}_outreach.md"
        meta = {
            "doc_id": doc_id,
            "doc_type": "outreach_kit_markdown",
            "provider_id": pm.provider_id,
            "provider_type": pm.provider_type,
            "region": pm.region,
            "propensity_score": round(pm.propensity_score, 4),
            "expected_incremental_revenue": round(pm.expected_incremental_revenue, 2),
            "case_growth_rate_30d": round(pm.case_growth_rate_30d, 4),
            "case_growth_rate_60d": round(pm.case_growth_rate_60d, 4),
            "has_comprehensive": pm.has_comprehensive,
            "recommended_action": action,
            "product_focus": product_focus,
            "created_at": now_iso(),
        }
        body = outreach_text(pm)
        p.write_text(metadata_block(meta) + "\n\n" + body + "\n", encoding="utf-8")
        manifest.append({"path": str(p.relative_to(out_root)), **meta})
        counts["outreach_kits_md"] += 1

    brief_defs = [
        ("midwest_gp_decline_campaign_brief", "Midwest GP Decline Campaign Brief"),
        ("upsell_pipeline_campaign_brief", "Upsell Pipeline Campaign Brief"),
        ("high_engagement_conversion_campaign_brief", "High Engagement Conversion Campaign Brief"),
        ("benchmark_replication_campaign_brief", "Benchmark Replication Campaign Brief"),
    ]
    for slug, title in brief_defs:
        p = out_root / "campaign_briefs" / f"{slug}.pdf"
        meta = {
            "doc_id": slug,
            "doc_type": "campaign_brief_pdf",
            "provider_id": None,
            "provider_type": "Mixed",
            "region": "Mixed",
            "propensity_score": 0.0,
            "expected_incremental_revenue": 0.0,
            "case_growth_rate_30d": 0.0,
            "case_growth_rate_60d": 0.0,
            "has_comprehensive": False,
            "recommended_action": "Campaign orchestration",
            "product_focus": "Comprehensive",
            "created_at": now_iso(),
        }
        write_simple_pdf(p, title, campaign_brief_text(title, segment_rows))
        manifest.append({"path": str(p.relative_to(out_root)), **meta})
        counts["campaign_briefs_pdf"] += 1

    manifest_path = out_root / "manifest" / "provider_doc_index.jsonl"
    with manifest_path.open("w", encoding="utf-8") as f:
        for row in manifest:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")

    return manifest, counts, {"dossier_rows": generated_rows, "top100_provider_ids": [m.provider_id for m in top100]}


def sync_to_volume(local_root: Path) -> None:
    subdirs = ["segment_playbooks", "provider_dossiers", "outreach_kits", "campaign_briefs", "manifest"]
    for d in subdirs:
        try:
            run_cmd(f"databricks fs rm {shlex.quote(VOLUME_DBFS_PATH + '/' + d)} -r")
        except Exception:
            pass
        run_cmd(f"databricks fs mkdir {shlex.quote(VOLUME_DBFS_PATH + '/' + d)}")
        run_cmd(
            "databricks fs cp --overwrite --recursive "
            f"{shlex.quote(str((local_root / d).resolve()))} "
            f"{shlex.quote(VOLUME_DBFS_PATH + '/' + d)}"
        )


def ensure_ka(w: WorkspaceClient) -> tuple[str, str, dict[str, Any]]:
    existing: dict[str, Any] | None = None
    for t in list_tiles(w, "KA"):
        if t.get("name") in {KA_NAME, KA_TILE_NAME}:
            existing = t
            break

    if existing:
        detail = http_json(w, "GET", f"/api/2.0/knowledge-assistants/{existing['tile_id']}")
        srcs = detail.get("knowledge_assistant", {}).get("knowledge_sources", [])
        has_expected_source = False
        for s in srcs:
            path = (
                s.get("files_source", {})
                .get("files", {})
                .get("path")
            )
            if path == VOLUME_DBFS_PATH.replace("dbfs:", ""):
                has_expected_source = True
                break
        if not has_expected_source:
            http_json(w, "DELETE", f"/api/2.0/tiles/{existing['tile_id']}")
            existing = None

    if not existing:
        payload = {
            "name": KA_TILE_NAME,
            "description": "Unstructured Invisalign provider growth playbook assistant aligned to Provider Growth Analytics Genie space.",
            "instructions": (
                "You are a world-class sales enablement leader for Invisalign provider growth.\n"
                "Always output exactly this structure:\n"
                "1. Recommendation\n"
                "2. Why it works (tie to provider behavior)\n"
                "3. Talk track (verbatim script)\n"
                "4. Objection handling\n"
                "5. Outreach strategy (channel + cadence)\n"
                "6. Compliance note\n"
                "Hard requirements:\n"
                "- Include at least 2 objections with concise rebuttals.\n"
                "- Reference Invisalign product tiers when relevant.\n"
                "- For recovery scenarios, explicitly call out Midwest GP decline pattern when applicable.\n"
                "- Include healthcare marketing sensitivity, avoid misleading claims, and include patient outcome disclaimer framing.\n"
            ),
            "knowledge_sources": [
                {
                    "files_source": {
                        "name": "source_provider_growth_playbook",
                        "type": "files",
                        "files": {"path": "/Volumes/ai_specialist/invisalign_demo/provider_growth_playbook"},
                    }
                }
            ],
        }
        created = http_json(w, "POST", "/api/2.0/knowledge-assistants", body=payload, timeout=300)
        tile_id = created.get("knowledge_assistant", {}).get("tile", {}).get("tile_id")
        if not tile_id:
            raise BuildError("KA create response missing tile_id.")
    else:
        tile_id = existing["tile_id"]

    # Wait for endpoint ONLINE
    deadline = time.time() + 1800
    endpoint_name = ""
    last = "UNKNOWN"
    ready_by_invocation = False
    while time.time() < deadline:
        detail = http_json(w, "GET", f"/api/2.0/knowledge-assistants/{tile_id}")
        ka_obj = detail.get("knowledge_assistant", {})
        last = ka_obj.get("status", {}).get("endpoint_status", "UNKNOWN")
        endpoint_name = ka_obj.get("tile", {}).get("serving_endpoint_name", f"ka-{tile_id.split('-')[0]}-endpoint")
        if last == "ONLINE":
            break
        ks = (ka_obj.get("knowledge_sources") or [{}])[0]
        summary = (ks.get("file_source_index_info", {}) or {}).get("summary", {}) or {}
        total_files = int(summary.get("total_files", 0) or 0)
        done_files = int(summary.get("success_files", 0) or 0) + int(summary.get("failed_files", 0) or 0) + int(summary.get("skipped_files", 0) or 0)
        if total_files > 0 and done_files >= total_files and endpoint_invocable(w, endpoint_name):
            ready_by_invocation = True
            break
        time.sleep(15)
    if last != "ONLINE" and not ready_by_invocation:
        raise BuildError(f"KA endpoint did not become ONLINE. last={last}")

    # Replace examples (retry for transient control-plane errors).
    existing_examples: list[dict[str, Any]] = []
    example_api_available = False
    for _ in range(8):
        try:
            existing_examples = http_json(
                w,
                "GET",
                f"/api/2.0/knowledge-assistants/{tile_id}/examples",
            ).get("examples", [])
            example_api_available = True
            break
        except Exception:
            time.sleep(10)
    for ex in existing_examples:
        ex_id = ex.get("example_id") or ex.get("id")
        if ex_id:
            try:
                http_json(w, "DELETE", f"/api/2.0/knowledge-assistants/{tile_id}/examples/{ex_id}")
            except Exception:
                pass
    add_results: list[dict[str, Any]] = []
    for q in KA_SAMPLE_QUESTIONS:
        guideline = "Use the 6-section format exactly and keep output rep-ready with objections and compliance note."
        posted = False
        for _ in range(6):
            try:
                http_json(
                    w,
                    "POST",
                    f"/api/2.0/knowledge-assistants/{tile_id}/examples",
                    body={"tile_id": tile_id, "question": q, "guidelines": [guideline]},
                )
                posted = True
                break
            except Exception:
                time.sleep(8)
        add_results.append({"question": q, "added": posted})

    return tile_id, endpoint_name, {
        "example_api_available": example_api_available,
        "example_add_results": add_results,
    }


def main() -> None:
    w = WorkspaceClient(profile=PROFILE)
    warehouse_id = choose_warehouse(w)

    # Lock to existing Genie as source-of-truth (read only).
    genie_space = w.genie.get_space(space_id=GENIE_SPACE_ID, include_serialized_space=True)
    raw = genie_space.serialized_space
    serialized = json.loads(raw) if isinstance(raw, str) else (raw or {})
    snippets = ((serialized.get("instructions", {}) or {}).get("sql_snippets", {}) or {})
    genie_semantics = {
        "sample_questions": len((serialized.get("config", {}) or {}).get("sample_questions", [])),
        "measures": len(snippets.get("measures", [])),
        "expressions": len(snippets.get("expressions", [])),
        "join_specs": len((serialized.get("instructions", {}) or {}).get("join_specs", [])),
    }

    # Create volume and folders.
    ensure_volume_and_dirs(w, warehouse_id)

    # Build canonical extracts.
    rows = execute_sql_rows(w, warehouse_id, metric_query(SCHEMA_FQN))
    metrics: list[ProviderMetric] = []
    source_by_provider: dict[str, ProviderMetric] = {}
    for r in rows:
        pm = ProviderMetric(
            provider_id=str(r["provider_id"]),
            provider_type=str(r["provider_type"]),
            region=str(r["region"]),
            digital_engagement_score=pint(r["digital_engagement_score"]),
            propensity_score=pfloat(r["propensity_score"]),
            expected_incremental_revenue=pfloat(r["expected_incremental_revenue"]),
            has_comprehensive=parse_bool(r["has_comprehensive"]),
            primary_product=str(r["primary_product"]),
            product_count=pint(r["product_count"]),
            cases_last_30d=pint(r["cases_last_30d"]),
            cases_prior_30d=pint(r["cases_prior_30d"]),
            cases_last_60d=pint(r["cases_last_60d"]),
            cases_prior_60d=pint(r["cases_prior_60d"]),
            case_growth_rate_30d=pfloat(r["case_growth_rate_30d"]),
            case_growth_rate_60d=pfloat(r["case_growth_rate_60d"]),
            comprehensive_case_share=pfloat(r["comprehensive_case_share"]),
            lite_case_share=pfloat(r["lite_case_share"]),
            express_case_share=pfloat(r["express_case_share"]),
            tier_1_target=parse_bool(r["tier_1_target"]),
            tier_2_target=parse_bool(r["tier_2_target"]),
            declining_provider=parse_bool(r["declining_provider"]),
            growing_provider=parse_bool(r["growing_provider"]),
            at_risk_flag=parse_bool(r["at_risk_flag"]),
        )
        metrics.append(pm)
        source_by_provider[pm.provider_id] = pm

    segment_rows = execute_sql_rows(w, warehouse_id, segment_summary_query(SCHEMA_FQN))

    # Generate files locally then sync to volume.
    with tempfile.TemporaryDirectory(prefix="invisalign_ka_docs_") as td:
        local_root = Path(td)
        manifest_rows, file_counts, generated = generate_docs(metrics, segment_rows, local_root)
        sync_to_volume(local_root)

    # KA create/configure.
    ka_tile_id, ka_endpoint_name, example_write_status = ensure_ka(w)
    ka_status = http_json(w, "GET", f"/api/2.0/knowledge-assistants/{ka_tile_id}").get("knowledge_assistant", {}).get("status", {}).get("endpoint_status", "UNKNOWN")

    # Validation with existing Genie + KA.
    genie_q1 = ask_genie(w, VALIDATION_Q1)
    genie_q2 = ask_genie(w, VALIDATION_Q2)
    ka_q1 = ask_ka(w, ka_endpoint_name, VALIDATION_Q1)
    ka_q2 = ask_ka(w, ka_endpoint_name, VALIDATION_Q2)

    # Correlation QA.
    dossier_provider_ids = set(generated["top100_provider_ids"])
    provider_ids_in_source = set(source_by_provider.keys())
    orphan_provider_ids = sorted(dossier_provider_ids - provider_ids_in_source)

    # Metric parity on sample providers from generated dossiers.
    parity_samples: list[dict[str, Any]] = []
    mismatches = 0
    for pid in list(generated["dossier_rows"].keys())[:15]:
        meta = generated["dossier_rows"][pid]
        src = source_by_provider[pid]
        checks = {
            "propensity_match": abs(meta["propensity_score"] - round(src.propensity_score, 4)) < 1e-6,
            "expected_revenue_match": abs(meta["expected_incremental_revenue"] - round(src.expected_incremental_revenue, 2)) < 1e-6,
            "growth30_match": abs(meta["case_growth_rate_30d"] - round(src.case_growth_rate_30d, 4)) < 1e-6,
            "growth60_match": abs(meta["case_growth_rate_60d"] - round(src.case_growth_rate_60d, 4)) < 1e-6,
        }
        if not all(checks.values()):
            mismatches += 1
        parity_samples.append({"provider_id": pid, **checks})

    examples_count = None
    try:
        examples_count = len(http_json(w, "GET", f"/api/2.0/knowledge-assistants/{ka_tile_id}/examples").get("examples", []))
    except Exception:
        examples_count = None

    result = {
        "assets": {
            "genie_space_id": GENIE_SPACE_ID,
            "ka_tile_id": ka_tile_id,
            "ka_endpoint_name": ka_endpoint_name,
            "ka_endpoint_status": ka_status,
            "volume_path": "/Volumes/ai_specialist/invisalign_demo/provider_growth_playbook",
            "warehouse_id": warehouse_id,
        },
        "genie_source_of_truth": {
            "space_title": genie_space.title,
            "semantic_counts": genie_semantics,
        },
        "doc_generation": {
            "file_counts": file_counts,
            "manifest_rows": len(manifest_rows),
            "ka_examples_count": examples_count,
            "ka_example_write_status": example_write_status,
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
            "manifest_integrity": {
                "orphan_provider_ids_count": len(orphan_provider_ids),
                "orphan_provider_ids_sample": orphan_provider_ids[:10],
            },
            "metric_parity": {
                "sampled_providers_checked": len(parity_samples),
                "sample_mismatches": mismatches,
                "samples": parity_samples,
            },
            "response_format_checks": {
                "q1_sections": assert_talktrack_format(ka_q1["response_text"]),
                "q2_sections": assert_talktrack_format(ka_q2["response_text"]),
            },
            "story_alignment_checks": {
                "genie_q1_has_midwest_targets": bool(genie_q1.get("rows")),
                "genie_q2_has_decline_rows": bool(genie_q2.get("rows")),
                "ka_q1_mentions_comprehensive": "comprehensive" in (ka_q1["response_text"] or "").lower(),
                "ka_q2_mentions_decline_or_recovery": any(
                    token in (ka_q2["response_text"] or "").lower()
                    for token in ["declin", "recover", "midwest"]
                ),
                "ka_mentions_revenue_lift_concept": any(
                    token in ((ka_q1["response_text"] or "") + " " + (ka_q2["response_text"] or "")).lower()
                    for token in ["revenue", "incremental", "lift"]
                ),
            },
        },
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
