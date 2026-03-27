# Databricks notebook source
# MAGIC %md
# MAGIC # HLS Payer Agent Bricks Demo
# MAGIC
# MAGIC End-to-end notebook that builds:
# MAGIC 1) Structured healthcare payer demo tables
# MAGIC 2) Genie Space over structured data
# MAGIC 3) Unstructured healthcare corpus + chunk table + Vector Search index
# MAGIC 4) Knowledge Assistant over document volume
# MAGIC 5) Supervisor Agent that routes across Genie + KA
# MAGIC 6) Validation queries + demo run summary table

# COMMAND ----------

from __future__ import annotations

import json
import random
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn,
    EndpointType,
    PipelineType,
    VectorIndexType,
)
from pyspark.sql import types as T

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1) Configuration

# COMMAND ----------

CATALOG = "main"
SCHEMA = "hls_payer_demo"
WAREHOUSE_ID = "1e1f63a1a14d1f34"

MEMBERS_TABLE = f"{CATALOG}.{SCHEMA}.members"
CLAIMS_TABLE = f"{CATALOG}.{SCHEMA}.claims"
LABS_TABLE = f"{CATALOG}.{SCHEMA}.labs"
GAPS_TABLE = f"{CATALOG}.{SCHEMA}.gaps_in_care"
DOCS_TABLE = f"{CATALOG}.{SCHEMA}.unstructured_documents"
CHUNKS_TABLE = f"{CATALOG}.{SCHEMA}.unstructured_document_chunks"
DEMO_RESULTS_TABLE = f"{CATALOG}.{SCHEMA}.demo_run_results"

VOLUME_NAME = "healthcare_docs"
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME_NAME}"
VOLUME_DBFS_PATH = f"dbfs:{VOLUME_PATH}"

VS_ENDPOINT_NAME = "hls-payer-vs-endpoint"
VS_INDEX_NAME = f"{CATALOG}.{SCHEMA}.hls_payer_docs_index"

GENIE_DISPLAY_NAME = "HLS Payer Structured Genie"
KA_NAME = "HLS_Payer_Knowledge_Assistant"
MAS_NAME = "HLS_Payer_Supervisor"

SEED = 42
N_MEMBERS = 1500
N_DOCS_TOTAL = 240
N_CLINICAL_DOCS = 120
N_CARE_MGR_DOCS = 80
N_POLICY_DOCS = 40

TARGET_EMBEDDING_ENDPOINTS = [
    "gte-large-en-v1-5-systemai",
    "databricks-gte-large-en",
    "gte-large-en-v1-5",
]

assert N_CLINICAL_DOCS + N_CARE_MGR_DOCS + N_POLICY_DOCS == N_DOCS_TOTAL

random.seed(SEED)
np.random.seed(SEED)

w = WorkspaceClient()

print(
    json.dumps(
        {
            "catalog": CATALOG,
            "schema": SCHEMA,
            "warehouse_id": WAREHOUSE_ID,
            "volume_path": VOLUME_PATH,
            "vs_endpoint": VS_ENDPOINT_NAME,
            "vs_index": VS_INDEX_NAME,
            "genie_space": GENIE_DISPLAY_NAME,
            "ka_name": KA_NAME,
            "mas_name": MAS_NAME,
        },
        indent=2,
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2) Helpers (REST + Agent Bricks + Validation)

# COMMAND ----------


def _auth_headers() -> Dict[str, str]:
    headers = w.config.authenticate()
    headers["Content-Type"] = "application/json"
    return headers


def _api_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resp = requests.get(
        f"{w.config.host}{path}",
        headers=_auth_headers(),
        params=params or {},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"GET {path} failed: {resp.status_code} {resp.text}")
    return resp.json() if resp.text else {}


def _api_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.post(
        f"{w.config.host}{path}",
        headers=_auth_headers(),
        json=body,
        timeout=300,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"POST {path} failed: {resp.status_code} {resp.text}")
    return resp.json() if resp.text else {}


def _api_patch(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.patch(
        f"{w.config.host}{path}",
        headers=_auth_headers(),
        json=body,
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"PATCH {path} failed: {resp.status_code} {resp.text}")
    return resp.json() if resp.text else {}


def _sanitize_name(name: str) -> str:
    name = name.replace(" ", "_")
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    name = re.sub(r"[_-]{2,}", "_", name)
    return name.strip("_-") or "agent_brick"


def _find_tile_by_name(name: str, tile_type: str) -> Optional[Dict[str, Any]]:
    payload = _api_get(
        "/api/2.0/tiles",
        params={"filter": f"name_contains={name}&&tile_type={tile_type}"},
    )
    for tile in payload.get("tiles", []):
        if tile.get("name") == name:
            return tile
    return None


def _wait_for_status(
    fetch_fn,
    status_path: Tuple[str, ...],
    ready_values: Iterable[str],
    timeout_s: int = 900,
    poll_s: float = 10.0,
) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    last_status = None
    while True:
        obj = fetch_fn()
        status = obj
        for key in status_path:
            status = status.get(key, {}) if isinstance(status, dict) else {}
        if isinstance(status, dict):
            status = None
        if status != last_status:
            print(f"Status update: {status}")
            last_status = status
        if status in ready_values:
            return obj
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for status in {set(ready_values)}; last={status}")
        time.sleep(poll_s)


def _ka_endpoint_name(tile_id: str) -> str:
    return f"ka-{tile_id.split('-')[0]}-endpoint"


def _mas_endpoint_name(tile_id: str) -> str:
    return f"mas-{tile_id.split('-')[0]}-endpoint"


def call_agent_endpoint(endpoint_name: str, question: str) -> Dict[str, Any]:
    payload = {"input": [{"role": "user", "content": question}]}
    resp = requests.post(
        f"{w.config.host}/serving-endpoints/{endpoint_name}/invocations",
        headers=_auth_headers(),
        json=payload,
        timeout=180,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Serving query failed for {endpoint_name}: {resp.status_code} {resp.text}")
    return resp.json() if resp.text else {}


def ask_genie_and_extract(space_id: str, question: str, timeout_seconds: int = 180) -> Dict[str, Any]:
    result = w.genie.start_conversation_and_wait(
        space_id=space_id,
        content=question,
        timeout=timedelta(seconds=timeout_seconds),
    )

    formatted: Dict[str, Any] = {
        "question": question,
        "conversation_id": result.conversation_id,
        "message_id": result.id,
        "status": str(result.status.value) if result.status else "UNKNOWN",
    }

    if result.attachments:
        for att in result.attachments:
            if att.query:
                formatted["sql"] = att.query.query or ""
                formatted["description"] = att.query.description or ""
                if att.query.query_result_metadata:
                    formatted["row_count"] = att.query.query_result_metadata.row_count
                if att.attachment_id:
                    try:
                        data_result = w.genie.get_message_query_result_by_attachment(
                            space_id=space_id,
                            conversation_id=result.conversation_id,
                            message_id=result.id,
                            attachment_id=att.attachment_id,
                        )
                        sr = data_result.statement_response
                        if sr and sr.manifest and sr.manifest.schema and sr.manifest.schema.columns:
                            formatted["columns"] = [c.name for c in sr.manifest.schema.columns]
                        if sr and sr.result and sr.result.data_array:
                            formatted["data"] = sr.result.data_array
                    except Exception as e:
                        formatted["data_fetch_error"] = str(e)
            if att.text:
                formatted["text_response"] = att.text.content or ""
    return formatted


def create_or_update_genie(
    display_name: str,
    warehouse_id: str,
    table_identifiers: List[str],
    description: str,
    sample_questions: List[str],
) -> Dict[str, Any]:
    existing = None
    rooms = _api_get("/api/2.0/data-rooms")
    for room in rooms.get("data_rooms", []):
        if room.get("display_name") == display_name:
            existing = room
            break

    if existing:
        space_id = existing["space_id"]
        current = _api_get(f"/api/2.0/data-rooms/{space_id}")
        patch_payload = {
            "id": space_id,
            "space_id": current.get("space_id", space_id),
            "display_name": display_name,
            "description": description,
            "warehouse_id": warehouse_id,
            "table_identifiers": table_identifiers,
            "run_as_type": current.get("run_as_type", "VIEWER"),
        }
        for field in ("created_timestamp", "last_updated_timestamp", "user_id", "folder_node_internal_name"):
            if current.get(field):
                patch_payload[field] = current[field]
        _api_patch(f"/api/2.0/data-rooms/{space_id}", patch_payload)
        operation = "updated"
    else:
        created = _api_post(
            "/api/2.0/data-rooms/",
            {
                "display_name": display_name,
                "description": description,
                "warehouse_id": warehouse_id,
                "table_identifiers": table_identifiers,
                "run_as_type": "VIEWER",
            },
        )
        space_id = created["space_id"]
        operation = "created"

    existing_questions = _api_get(
        f"/api/2.0/data-rooms/{space_id}/curated-questions",
        params={"question_type": "SAMPLE_QUESTION"},
    ).get("curated_questions", [])

    existing_ids: List[str] = []
    for q in existing_questions:
        qid = q.get("curated_question_id") or q.get("id") or q.get("question_id")
        if isinstance(qid, str) and qid.strip():
            existing_ids.append(qid)

    # Delete current sample questions first. Keep best-effort behavior so reruns are resilient.
    if existing_ids:
        delete_actions = [{"action_type": "DELETE", "curated_question_id": qid} for qid in existing_ids]
        try:
            _api_post(
                f"/api/2.0/data-rooms/{space_id}/curated-questions/batch-actions",
                {"actions": delete_actions},
            )
        except Exception as e:
            print(
                "Warning: sample-question delete via batch-actions failed; continuing with create. "
                f"Details: {e}"
            )

    # Create desired sample questions one-by-one using curated-question create endpoint.
    for text in sample_questions:
        _api_post(
            f"/api/2.0/data-rooms/{space_id}/curated-questions",
            {
                "curated_question": {
                    "data_space_id": space_id,
                    "question_text": text,
                    "question_type": "SAMPLE_QUESTION",
                    "is_deprecated": False,
                },
                "data_space_id": space_id,
            },
        )

    return {"space_id": space_id, "operation": operation, "display_name": display_name}


def create_or_update_ka(
    name: str,
    volume_path: str,
    description: str,
    instructions: str,
) -> Dict[str, Any]:
    safe_name = _sanitize_name(name)
    existing = _find_tile_by_name(safe_name, "KA")

    source = {
        "files_source": {
            "name": f"source_{safe_name.lower()}",
            "type": "files",
            "files": {"path": volume_path},
        }
    }

    if existing:
        tile_id = existing["tile_id"]
        current = _api_get(f"/api/2.0/knowledge-assistants/{tile_id}")
        ka_obj = current.get("knowledge_assistant", {})
        current_sources = ka_obj.get("knowledge_sources", [])

        existing_source_paths = set()
        for s in current_sources:
            files_path = (
                s.get("files_source", {})
                .get("files", {})
                .get("path")
            )
            if files_path:
                existing_source_paths.add(files_path)

        # Some workspaces do not expose PATCH for KA. Reuse existing KA on rerun.
        if volume_path not in existing_source_paths:
            print(
                "Warning: existing KA does not include requested volume path. "
                "This workspace does not support KA PATCH in this notebook path, so reusing existing KA as-is."
            )
        if ka_obj.get("instructions") != instructions or ka_obj.get("description") != description:
            print("Info: existing KA metadata differs from notebook config; reusing existing KA as-is.")

        result = current
        operation = "reused"
    else:
        result = _api_post(
            "/api/2.0/knowledge-assistants",
            {
                "name": safe_name,
                "description": description,
                "instructions": instructions,
                "knowledge_sources": [source],
            },
        )
        tile_id = result.get("knowledge_assistant", {}).get("tile", {}).get("tile_id")
        operation = "created"

    if not tile_id:
        tile_id = result.get("knowledge_assistant", {}).get("tile", {}).get("tile_id")

    current_state = _api_get(f"/api/2.0/knowledge-assistants/{tile_id}")
    status = current_state.get("knowledge_assistant", {}).get("status", {}).get("endpoint_status")

    if status != "ONLINE":
        try:
            ready = _wait_for_status(
                lambda: _api_get(f"/api/2.0/knowledge-assistants/{tile_id}"),
                ("knowledge_assistant", "status", "endpoint_status"),
                ready_values=("ONLINE",),
                timeout_s=3600,
                poll_s=20.0,
            )
            status = ready.get("knowledge_assistant", {}).get("status", {}).get("endpoint_status")
        except TimeoutError:
            latest = _api_get(f"/api/2.0/knowledge-assistants/{tile_id}")
            status = latest.get("knowledge_assistant", {}).get("status", {}).get("endpoint_status")
            print(
                "Warning: KA endpoint did not reach ONLINE within timeout. "
                f"Current status={status}. You can rerun Section 10 later."
            )

    return {
        "tile_id": tile_id,
        "name": safe_name,
        "operation": operation,
        "endpoint_status": status,
        "endpoint_name": _ka_endpoint_name(tile_id),
    }


def create_or_update_mas(
    name: str,
    genie_space_id: str,
    ka_tile_id: str,
    description: str,
    instructions: str,
    examples: List[Dict[str, str]],
) -> Dict[str, Any]:
    safe_name = _sanitize_name(name)
    existing = _find_tile_by_name(safe_name, "MAS")

    agents = [
        {
            "name": "structured_payer_analytics",
            "description": "Handles structured payer analytics questions over members, claims, labs, and gaps_in_care.",
            "agent_type": "genie",
            "genie_space": {"id": genie_space_id},
        },
        {
            "name": "unstructured_care_insights",
            "description": "Handles narrative questions about barriers, outreach notes, and guideline text using document retrieval.",
            "agent_type": "serving_endpoint",
            "serving_endpoint": {"name": _ka_endpoint_name(ka_tile_id)},
        },
    ]

    if existing:
        tile_id = existing["tile_id"]
        try:
            result = _api_patch(
                f"/api/2.0/multi-agent-supervisors/{tile_id}",
                {
                    "tile_id": tile_id,
                    "name": safe_name,
                    "description": description,
                    "instructions": instructions,
                    "agents": agents,
                },
            )
            operation = "updated"
        except RuntimeError as e:
            msg = str(e)
            if "ENDPOINT_NOT_FOUND" in msg:
                print(
                    "Warning: MAS PATCH endpoint not available in this workspace. "
                    "Reusing existing MAS as-is."
                )
                result = _api_get(f"/api/2.0/multi-agent-supervisors/{tile_id}")
                operation = "reused"
            else:
                raise
    else:
        result = _api_post(
            "/api/2.0/multi-agent-supervisors",
            {
                "name": safe_name,
                "description": description,
                "instructions": instructions,
                "agents": agents,
            },
        )
        tile_id = result.get("multi_agent_supervisor", {}).get("tile", {}).get("tile_id")
        operation = "created"

    if not tile_id:
        tile_id = result.get("multi_agent_supervisor", {}).get("tile", {}).get("tile_id")

    for ex in examples:
        try:
            _api_post(
                f"/api/2.0/multi-agent-supervisors/{tile_id}/examples",
                {"tile_id": tile_id, "question": ex["question"], "guidelines": [ex["guideline"]]},
            )
        except Exception:
            # Example creation can race with provisioning; keep demo resilient.
            pass

    current_state = _api_get(f"/api/2.0/multi-agent-supervisors/{tile_id}")
    status = current_state.get("multi_agent_supervisor", {}).get("status", {}).get("endpoint_status")

    if status != "ONLINE":
        try:
            ready = _wait_for_status(
                lambda: _api_get(f"/api/2.0/multi-agent-supervisors/{tile_id}"),
                ("multi_agent_supervisor", "status", "endpoint_status"),
                ready_values=("ONLINE",),
                timeout_s=3600,
                poll_s=20.0,
            )
            status = ready.get("multi_agent_supervisor", {}).get("status", {}).get("endpoint_status")
        except TimeoutError:
            latest = _api_get(f"/api/2.0/multi-agent-supervisors/{tile_id}")
            status = latest.get("multi_agent_supervisor", {}).get("status", {}).get("endpoint_status")
            print(
                "Warning: MAS endpoint did not reach ONLINE within timeout. "
                f"Current status={status}. You can rerun Section 10 later."
            )

    return {
        "tile_id": tile_id,
        "name": safe_name,
        "operation": operation,
        "endpoint_status": status,
        "endpoint_name": _mas_endpoint_name(tile_id),
    }


def pick_embedding_endpoint() -> str:
    serving_eps = {ep.name for ep in w.serving_endpoints.list()}
    for candidate in TARGET_EMBEDDING_ENDPOINTS:
        if candidate in serving_eps:
            return candidate
    raise RuntimeError(f"No supported embedding endpoint found. Tried: {TARGET_EMBEDDING_ENDPOINTS}")


def build_embedding_source_column(name: str, endpoint_name: str) -> EmbeddingSourceColumn:
    """
    Build EmbeddingSourceColumn compatibly across Databricks SDK versions.

    Some runtimes expose `embedding_model_endpoint_name`; older ones may expose
    `model_endpoint_name` (or only query-specific endpoint fields).
    """
    field_names = set(getattr(EmbeddingSourceColumn, "__dataclass_fields__", {}).keys())
    payload: Dict[str, Any] = {"name": name}

    if "embedding_model_endpoint_name" in field_names:
        payload["embedding_model_endpoint_name"] = endpoint_name
    elif "model_endpoint_name" in field_names:
        payload["model_endpoint_name"] = endpoint_name
    elif "model_endpoint_name_for_query" in field_names:
        payload["model_endpoint_name_for_query"] = endpoint_name
    else:
        raise RuntimeError(
            f"Unsupported EmbeddingSourceColumn fields {sorted(field_names)}; "
            "cannot infer embedding endpoint argument."
        )

    return EmbeddingSourceColumn(**payload)


def chunk_text(text: str, max_len: int = 900, overlap: int = 150) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return [text]

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_len)
        chunk = text[start:end]
        if end < len(text):
            last_period = chunk.rfind(". ")
            if last_period > int(max_len * 0.6):
                end = start + last_period + 1
                chunk = text[start:end]
        chunks.append(chunk.strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


# COMMAND ----------

# MAGIC %md
# MAGIC ## 3) Preflight (UC schema/volume + baseline checks)

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(
    f"""
    CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME_NAME}
    COMMENT 'Synthetic healthcare payer unstructured docs for KA demo'
    """
)

print("Preflight complete.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4) Generate structured synthetic data (`members`, `claims`, `labs`)

# COMMAND ----------

end_date = date.today()
start_date = end_date - timedelta(days=365)

member_ids = [f"M{idx:06d}" for idx in range(1, N_MEMBERS + 1)]
ages = np.clip(np.random.normal(loc=52, scale=17, size=N_MEMBERS).round().astype(int), 18, 90)
genders = np.random.choice(["F", "M"], size=N_MEMBERS, p=[0.53, 0.47])
plan_types = np.random.choice(
    ["Commercial_HMO", "Commercial_PPO", "Medicare_Advantage", "Medicaid_Managed_Care"],
    size=N_MEMBERS,
    p=[0.26, 0.24, 0.30, 0.20],
)
risk_scores = np.clip(np.random.lognormal(mean=-0.10, sigma=0.55, size=N_MEMBERS), 0.20, 4.75).round(3)

members_pdf = pd.DataFrame(
    {
        "member_id": member_ids,
        "age": ages,
        "gender": genders,
        "plan_type": plan_types,
        "risk_score": risk_scores,
    }
)

diagnosis_non_diabetes = [
    "I10",    # Hypertension
    "E78.5",  # Hyperlipidemia
    "J45.909",
    "I25.10",
    "N18.9",
    "M54.5",
    "J44.9",
    "F41.9",
]
procedure_codes = [
    "83036",  # A1C
    "80061",  # Lipid panel
    "99213",
    "99214",
    "93000",
    "82043",
    "81001",
    "85025",
]

claims_rows: List[Tuple[str, str, str, str, date, float]] = []
labs_rows: List[Tuple[str, str, float, date]] = []

claim_counter = 1
for row in members_pdf.itertuples(index=False):
    member_id = row.member_id
    risk = float(row.risk_score)

    diabetic_prob = min(0.12 + 0.22 * risk, 0.80)
    is_diabetic_member = random.random() < diabetic_prob

    claim_cnt = int(np.clip(np.random.poisson(lam=4.6 + 1.9 * risk), 2, 24))
    for _ in range(claim_cnt):
        if is_diabetic_member and random.random() < 0.62:
            diag = "E11"
        else:
            diag = random.choice(diagnosis_non_diabetes)
        proc = random.choice(procedure_codes)
        days_ago = int(np.random.randint(0, 365))
        claim_dt = end_date - timedelta(days=days_ago)

        # Skewed right distribution for healthcare claims.
        base = float(np.random.lognormal(mean=5.45, sigma=0.72))
        if diag == "E11":
            base *= 1.18
        cost = round(min(base * (0.80 + 0.25 * risk), 25000.0), 2)

        claims_rows.append(
            (
                f"C{claim_counter:08d}",
                member_id,
                diag,
                proc,
                claim_dt,
                cost,
            )
        )
        claim_counter += 1

    # Labs: include A1C behavior pattern with compliance imbalance.
    # For diabetic members, most have A1C but not all are within 6 months.
    if is_diabetic_member:
        compliance_prob = max(0.42, min(0.82, 0.72 - 0.09 * (risk > 2.2)))
        has_recent_a1c = random.random() < compliance_prob
        has_old_a1c = random.random() < 0.80

        if has_recent_a1c:
            recent_days = int(np.random.randint(7, 175))
            labs_rows.append(
                (
                    member_id,
                    "A1C",
                    round(float(np.random.normal(7.6, 1.2)), 2),
                    end_date - timedelta(days=recent_days),
                )
            )
        elif has_old_a1c:
            old_days = int(np.random.randint(190, 350))
            labs_rows.append(
                (
                    member_id,
                    "A1C",
                    round(float(np.random.normal(8.1, 1.4)), 2),
                    end_date - timedelta(days=old_days),
                )
            )

        extra_lab_count = int(np.random.randint(1, 4))
    else:
        extra_lab_count = int(np.random.randint(1, 3))

    for _ in range(extra_lab_count):
        lt = random.choice(["LIPID_PANEL", "BMP", "CBC"])
        if lt == "LIPID_PANEL":
            lv = round(float(np.random.normal(178, 42)), 2)
        elif lt == "BMP":
            lv = round(float(np.random.normal(97, 11)), 2)
        else:
            lv = round(float(np.random.normal(7.2, 1.1)), 2)
        lab_days = int(np.random.randint(0, 365))
        labs_rows.append((member_id, lt, lv, end_date - timedelta(days=lab_days)))

claims_pdf = pd.DataFrame(
    claims_rows,
    columns=["claim_id", "member_id", "diagnosis_code", "procedure_code", "claim_date", "cost"],
)
labs_pdf = pd.DataFrame(labs_rows, columns=["member_id", "lab_type", "lab_value", "lab_date"])

print(
    json.dumps(
        {
            "members_rows": int(len(members_pdf)),
            "claims_rows": int(len(claims_pdf)),
            "labs_rows": int(len(labs_pdf)),
            "diabetes_claims_rows": int((claims_pdf["diagnosis_code"] == "E11").sum()),
        },
        indent=2,
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5) Persist structured tables + derived `gaps_in_care`

# COMMAND ----------

members_schema = T.StructType(
    [
        T.StructField("member_id", T.StringType(), False),
        T.StructField("age", T.IntegerType(), False),
        T.StructField("gender", T.StringType(), False),
        T.StructField("plan_type", T.StringType(), False),
        T.StructField("risk_score", T.FloatType(), False),
    ]
)
claims_schema = T.StructType(
    [
        T.StructField("claim_id", T.StringType(), False),
        T.StructField("member_id", T.StringType(), False),
        T.StructField("diagnosis_code", T.StringType(), False),
        T.StructField("procedure_code", T.StringType(), False),
        T.StructField("claim_date", T.DateType(), False),
        T.StructField("cost", T.FloatType(), False),
    ]
)
labs_schema = T.StructType(
    [
        T.StructField("member_id", T.StringType(), False),
        T.StructField("lab_type", T.StringType(), False),
        T.StructField("lab_value", T.FloatType(), False),
        T.StructField("lab_date", T.DateType(), False),
    ]
)

spark.createDataFrame(members_pdf, schema=members_schema).write.mode("overwrite").format("delta").saveAsTable(MEMBERS_TABLE)
spark.createDataFrame(claims_pdf, schema=claims_schema).write.mode("overwrite").format("delta").saveAsTable(CLAIMS_TABLE)
spark.createDataFrame(labs_pdf, schema=labs_schema).write.mode("overwrite").format("delta").saveAsTable(LABS_TABLE)

spark.sql(
    f"""
    CREATE OR REPLACE TABLE {GAPS_TABLE} AS
    WITH diabetic_members AS (
      SELECT DISTINCT member_id
      FROM {CLAIMS_TABLE}
      WHERE diagnosis_code = 'E11'
    ),
    latest_a1c AS (
      SELECT member_id, MAX(lab_date) AS last_test_date
      FROM {LABS_TABLE}
      WHERE upper(lab_type) = 'A1C'
      GROUP BY member_id
    )
    SELECT
      d.member_id AS member_id,
      'Diabetes (E11)' AS condition,
      'Missing A1C in last 6 months' AS gap_type,
      a.last_test_date AS last_test_date,
      CASE
        WHEN a.last_test_date IS NULL THEN TRUE
        WHEN a.last_test_date < date_sub(current_date(), 180) THEN TRUE
        ELSE FALSE
      END AS gap_flag
    FROM diabetic_members d
    LEFT JOIN latest_a1c a
      ON d.member_id = a.member_id
    """
)

spark.sql(f"COMMENT ON TABLE {MEMBERS_TABLE} IS 'Synthetic payer member master table for healthcare demo.'")
spark.sql(f"COMMENT ON TABLE {CLAIMS_TABLE} IS 'Synthetic medical claims with ICD-10 diagnosis and claim costs.'")
spark.sql(f"COMMENT ON TABLE {LABS_TABLE} IS 'Synthetic lab result events including A1C observations.'")
spark.sql(f"COMMENT ON TABLE {GAPS_TABLE} IS 'Derived diabetes care gaps based on absence of recent A1C test.'")

spark.sql(f"ALTER TABLE {MEMBERS_TABLE} ALTER COLUMN risk_score COMMENT 'Synthetic risk score, higher means higher expected utilization.'")
spark.sql(f"ALTER TABLE {CLAIMS_TABLE} ALTER COLUMN diagnosis_code COMMENT 'ICD-10 diagnosis code (E11 indicates Type 2 diabetes).'")
spark.sql(f"ALTER TABLE {CLAIMS_TABLE} ALTER COLUMN cost COMMENT 'Allowed claim cost amount in USD for this synthetic claim.'")
spark.sql(f"ALTER TABLE {GAPS_TABLE} ALTER COLUMN gap_flag COMMENT 'TRUE if member has no A1C in the last 180 days.'")

data_quality = spark.sql(
    f"""
    SELECT
      (SELECT COUNT(*) FROM {MEMBERS_TABLE}) AS members_cnt,
      (SELECT COUNT(*) FROM {CLAIMS_TABLE}) AS claims_cnt,
      (SELECT COUNT(*) FROM {LABS_TABLE}) AS labs_cnt,
      (SELECT COUNT(*) FROM {GAPS_TABLE} WHERE gap_flag = true) AS gap_true_cnt,
      (SELECT COUNT(*) FROM {GAPS_TABLE} WHERE gap_flag = false) AS gap_false_cnt
    """
).collect()[0].asDict()
print(json.dumps(data_quality, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6) Create Genie Space (structured analytics agent)

# COMMAND ----------

genie_description = (
    "Structured healthcare payer analytics space over members, claims, labs, and gaps_in_care. "
    "Join keys: all core tables join by member_id. Use diagnosis_code='E11' for diabetic cohort logic. "
    "Use gaps_in_care.gap_flag=true for non-compliant diabetes members missing recent A1C."
)
genie_questions = [
    "Find diabetic members who haven't had an A1C test in 6 months.",
    "Show high-risk members with gaps in care.",
    "What is the total cost of non-compliant diabetic members?",
    "How many diabetic members are compliant versus non-compliant on A1C?",
]

genie_info = create_or_update_genie(
    display_name=GENIE_DISPLAY_NAME,
    warehouse_id=WAREHOUSE_ID,
    table_identifiers=[MEMBERS_TABLE, CLAIMS_TABLE, LABS_TABLE, GAPS_TABLE],
    description=genie_description,
    sample_questions=genie_questions,
)
print(json.dumps(genie_info, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7) Generate unstructured docs (`unstructured_documents`)

# COMMAND ----------

gaps_pdf = spark.sql(
    f"""
    SELECT g.member_id, m.plan_type, m.risk_score, g.gap_flag
    FROM {GAPS_TABLE} g
    INNER JOIN {MEMBERS_TABLE} m
      ON g.member_id = m.member_id
    """
).toPandas()

overdue_member_ids = gaps_pdf[gaps_pdf["gap_flag"] == True]["member_id"].tolist()
compliant_member_ids = gaps_pdf[gaps_pdf["gap_flag"] == False]["member_id"].tolist()
all_member_ids = members_pdf["member_id"].tolist()

barriers = [
    "transportation constraints",
    "work schedule conflicts",
    "caregiving responsibilities",
    "limited appointment availability",
    "low perceived urgency",
    "cost-sharing concerns",
]
interventions = [
    "evening phlebotomy slots",
    "proactive reminder outreach",
    "mobile lab coordination",
    "care manager motivational interviewing",
    "benefit education for preventive services",
]

doc_rows: List[Tuple[str, str, str, Optional[str]]] = []

for i in range(1, N_CLINICAL_DOCS + 1):
    if overdue_member_ids and random.random() < 0.70:
        member_id = random.choice(overdue_member_ids)
        status_text = "overdue for A1C follow-up"
    else:
        member_id = random.choice(all_member_ids)
        status_text = "currently on chronic care follow-up"
    barrier = random.choice(barriers)
    text = (
        f"Clinical progress note {i}. Member {member_id} with Type 2 diabetes remains {status_text}. "
        f"Clinician observed inconsistent lab completion driven by {barrier}. "
        f"Recommended intervention: {random.choice(interventions)} and repeat A1C in 8 to 12 weeks."
    )
    doc_rows.append((f"DOC_CLIN_{i:04d}", text, "clinical_note", member_id))

for i in range(1, N_CARE_MGR_DOCS + 1):
    if overdue_member_ids and random.random() < 0.78:
        member_id = random.choice(overdue_member_ids)
        outcome = "did not complete scheduled A1C draw"
    else:
        member_id = random.choice(compliant_member_ids or all_member_ids)
        outcome = "completed prior A1C and is monitoring next due date"
    barrier = random.choice(barriers)
    text = (
        f"Care manager outreach note {i} for member {member_id}. Member {outcome}. "
        f"Primary barrier theme: {barrier}. "
        f"Action plan includes {random.choice(interventions)} plus PCP scheduling support."
    )
    doc_rows.append((f"DOC_CM_{i:04d}", text, "care_manager_note", member_id))

policy_texts = [
    "Policy: Adult members with diabetes should receive A1C monitoring at least twice per year. "
    "If treatment changes or control worsens, testing should be quarterly.",
    "Guideline: Members with A1C above target should receive intensified outreach and timely follow-up.",
    "Policy: Preventive lab compliance workflows should prioritize high-risk diabetic members with no A1C in 6 months.",
    "Guideline: Barrier-informed interventions should include transportation support, flexible scheduling, and reminder programs.",
]
for i in range(1, N_POLICY_DOCS + 1):
    text = (
        f"Diabetes policy guidance memo {i}. {random.choice(policy_texts)} "
        f"Operational recommendation: use care gap registry and outreach segmentation by risk score."
    )
    doc_rows.append((f"DOC_POL_{i:04d}", text, "policy_guideline", None))

docs_pdf = pd.DataFrame(doc_rows, columns=["doc_id", "content", "doc_type", "member_id"])
assert len(docs_pdf) == N_DOCS_TOTAL

docs_schema = T.StructType(
    [
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("content", T.StringType(), False),
        T.StructField("doc_type", T.StringType(), False),
        T.StructField("member_id", T.StringType(), True),
    ]
)
spark.createDataFrame(docs_pdf, schema=docs_schema).write.mode("overwrite").format("delta").saveAsTable(DOCS_TABLE)

print(
    docs_pdf.groupby("doc_type", dropna=False)["doc_id"].count().rename("count").to_dict()
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8) Chunk docs (`unstructured_document_chunks`) + materialize to volume

# COMMAND ----------

chunk_rows: List[Tuple[str, str, Optional[str], str, str, int]] = []
for row in docs_pdf.itertuples(index=False):
    chunks = chunk_text(row.content, max_len=900, overlap=150)
    for idx, chunk in enumerate(chunks, start=1):
        chunk_rows.append(
            (
                f"{row.doc_id}_C{idx:03d}",
                row.doc_id,
                row.member_id,
                row.doc_type,
                chunk,
                idx,
            )
        )

chunks_pdf = pd.DataFrame(
    chunk_rows,
    columns=["chunk_id", "doc_id", "member_id", "doc_type", "chunk_text", "chunk_seq"],
)

chunks_schema = T.StructType(
    [
        T.StructField("chunk_id", T.StringType(), False),
        T.StructField("doc_id", T.StringType(), False),
        T.StructField("member_id", T.StringType(), True),
        T.StructField("doc_type", T.StringType(), False),
        T.StructField("chunk_text", T.StringType(), False),
        T.StructField("chunk_seq", T.IntegerType(), False),
    ]
)
spark.createDataFrame(chunks_pdf, schema=chunks_schema).write.mode("overwrite").format("delta").saveAsTable(CHUNKS_TABLE)

spark.sql(f"ALTER TABLE {CHUNKS_TABLE} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

for subdir in ["clinical_note", "care_manager_note", "policy_guideline"]:
    dbutils.fs.mkdirs(f"{VOLUME_DBFS_PATH}/{subdir}")

for row in docs_pdf.itertuples(index=False):
    file_name = f"{row.doc_id}.txt"
    dbutils.fs.put(
        f"{VOLUME_DBFS_PATH}/{row.doc_type}/{file_name}",
        row.content,
        overwrite=True,
    )

print(
    json.dumps(
        {
            "docs_rows": int(len(docs_pdf)),
            "chunk_rows": int(len(chunks_pdf)),
            "volume_root": VOLUME_DBFS_PATH,
        },
        indent=2,
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9) Provision Vector Search endpoint + Delta Sync index

# COMMAND ----------

embedding_endpoint = pick_embedding_endpoint()
print(f"Using embedding endpoint: {embedding_endpoint}")

existing_eps = list(w.vector_search_endpoints.list_endpoints())
if not any(ep.name == VS_ENDPOINT_NAME for ep in existing_eps):
    w.vector_search_endpoints.create_endpoint(
        name=VS_ENDPOINT_NAME,
        endpoint_type=EndpointType.STANDARD,
    )


def get_vs_endpoint_state(endpoint_name: str) -> str:
    ep = w.vector_search_endpoints.get_endpoint(endpoint_name=endpoint_name)
    if ep.endpoint_status and ep.endpoint_status.state:
        return ep.endpoint_status.state.value
    return "UNKNOWN"


_ = _wait_for_status(
    lambda: {"state": get_vs_endpoint_state(VS_ENDPOINT_NAME)},
    ("state",),
    ready_values=("ONLINE",),
    timeout_s=1200,
    poll_s=20.0,
)

existing_indexes = [idx.name for idx in w.vector_search_indexes.list_indexes(endpoint_name=VS_ENDPOINT_NAME)]
if VS_INDEX_NAME not in existing_indexes:
    w.vector_search_indexes.create_index(
        name=VS_INDEX_NAME,
        endpoint_name=VS_ENDPOINT_NAME,
        primary_key="chunk_id",
        index_type=VectorIndexType.DELTA_SYNC,
        delta_sync_vector_index_spec=DeltaSyncVectorIndexSpecRequest(
            source_table=CHUNKS_TABLE,
            pipeline_type=PipelineType.TRIGGERED,
            embedding_source_columns=[
                build_embedding_source_column(
                    name="chunk_text",
                    endpoint_name=embedding_endpoint,
                )
            ],
            columns_to_sync=["chunk_id", "doc_id", "member_id", "doc_type", "chunk_text", "chunk_seq"],
        ),
    )


def get_vs_index_ready(index_name: str) -> str:
    idx = w.vector_search_indexes.get_index(index_name=index_name)
    if idx.status and idx.status.ready is not None:
        return "ONLINE" if idx.status.ready else "NOT_READY"
    return "UNKNOWN"


_ = _wait_for_status(
    lambda: {"state": get_vs_index_ready(VS_INDEX_NAME)},
    ("state",),
    ready_values=("ONLINE",),
    timeout_s=1200,
    poll_s=20.0,
)

w.vector_search_indexes.sync_index(index_name=VS_INDEX_NAME)

print(
    json.dumps(
        {
            "vs_endpoint": VS_ENDPOINT_NAME,
            "vs_index": VS_INDEX_NAME,
            "embedding_endpoint": embedding_endpoint,
            "sync_mode": "TRIGGERED",
        },
        indent=2,
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10) Provision Knowledge Assistant + Supervisor Agent

# COMMAND ----------

ka_description = "Answers questions from synthetic clinical notes, care manager notes, and diabetes policy documents."
ka_instructions = (
    "Answer concisely using only indexed documents. Always include source doc_id citations. "
    "If the answer is not supported by retrieved documents, explicitly say so."
)

ka_info = create_or_update_ka(
    name=KA_NAME,
    volume_path=VOLUME_PATH,
    description=ka_description,
    instructions=ka_instructions,
)
print("KA:", json.dumps(ka_info, indent=2))

mas_description = "Supervisor that routes structured payer analytics to Genie and narrative barrier/guideline questions to Knowledge Assistant."
mas_instructions = (
    "Routing policy: "
    "1) Structured KPI/cohort/cost questions -> structured_payer_analytics. "
    "2) Narrative barriers, outreach themes, guideline language -> unstructured_care_insights. "
    "3) For blended questions, decompose into structured + unstructured subtasks, then return a unified response "
    "that includes cohort summary, likely barriers, and recommended actions."
)
mas_examples = [
    {
        "question": "Find diabetic members missing A1C and summarize the main outreach barriers.",
        "guideline": "Use both agents, then combine cohort and barrier synthesis.",
    },
    {
        "question": "What are the clinical guidelines for A1C monitoring in diabetes?",
        "guideline": "Route to unstructured_care_insights.",
    },
]

mas_info = create_or_update_mas(
    name=MAS_NAME,
    genie_space_id=genie_info["space_id"],
    ka_tile_id=ka_info["tile_id"],
    description=mas_description,
    instructions=mas_instructions,
    examples=mas_examples,
)
print("MAS:", json.dumps(mas_info, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11) Validation queries (Genie, KA, Supervisor) + save run summary

# COMMAND ----------

genie_test_queries = [
    "Find diabetic members who haven't had an A1C test in 6 months.",
    "Show high-risk members with gaps in care.",
    "What is the total cost of non-compliant diabetic members?",
]
ka_test_queries = [
    "Why are members missing A1C tests?",
    "What barriers are care managers seeing?",
    "What are the clinical guidelines for diabetes care?",
]
wow_query = (
    "Which diabetic members are overdue for A1C, why are they missing care, and what should we do?"
)

run_rows: List[Dict[str, Any]] = []

for q in genie_test_queries:
    res = ask_genie_and_extract(genie_info["space_id"], q)
    run_rows.append(
        {
            "run_ts": datetime.utcnow().isoformat(),
            "system_name": "genie",
            "query_text": q,
            "sql_text": res.get("sql"),
            "response_json": json.dumps(res),
        }
    )

for q in ka_test_queries:
    res = call_agent_endpoint(ka_info["endpoint_name"], q)
    run_rows.append(
        {
            "run_ts": datetime.utcnow().isoformat(),
            "system_name": "knowledge_assistant",
            "query_text": q,
            "sql_text": None,
            "response_json": json.dumps(res),
        }
    )

mas_res = call_agent_endpoint(mas_info["endpoint_name"], wow_query)
run_rows.append(
    {
        "run_ts": datetime.utcnow().isoformat(),
        "system_name": "supervisor",
        "query_text": wow_query,
        "sql_text": None,
        "response_json": json.dumps(mas_res),
    }
)

results_pdf = pd.DataFrame(run_rows)
results_schema = T.StructType(
    [
        T.StructField("run_ts", T.StringType(), False),
        T.StructField("system_name", T.StringType(), False),
        T.StructField("query_text", T.StringType(), False),
        T.StructField("sql_text", T.StringType(), True),
        T.StructField("response_json", T.StringType(), False),
    ]
)
spark.createDataFrame(results_pdf, schema=results_schema).write.mode("overwrite").format("delta").saveAsTable(DEMO_RESULTS_TABLE)

print(f"Saved validation output table: {DEMO_RESULTS_TABLE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 12) Executive demo script (copy/paste)

# COMMAND ----------

print(
    f"""
DEMO CHECKLIST
1) Structured analytics (Genie):
   - Ask: "Find diabetic members who haven't had an A1C test in 6 months."
   - Ask: "Show high-risk members with gaps in care."
   - Ask: "What is the total cost of non-compliant diabetic members?"

2) Unstructured retrieval (Knowledge Assistant endpoint: {ka_info['endpoint_name']}):
   - Ask: "Why are members missing A1C tests?"
   - Ask: "What barriers are care managers seeing?"
   - Ask: "What are the clinical guidelines for diabetes care?"

3) Unified orchestration (Supervisor endpoint: {mas_info['endpoint_name']}):
   - WOW: "{wow_query}"

OBJECTS CREATED
- Tables:
  {MEMBERS_TABLE}
  {CLAIMS_TABLE}
  {LABS_TABLE}
  {GAPS_TABLE}
  {DOCS_TABLE}
  {CHUNKS_TABLE}
  {DEMO_RESULTS_TABLE}
- Volume: {VOLUME_PATH}
- Vector Search endpoint/index: {VS_ENDPOINT_NAME} / {VS_INDEX_NAME}
- Genie space: {GENIE_DISPLAY_NAME} ({genie_info['space_id']})
- KA: {KA_NAME} ({ka_info['tile_id']})
- Supervisor: {MAS_NAME} ({mas_info['tile_id']})
"""
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 13) Optional practice section (intentionally fails)
# MAGIC
# MAGIC This section is **not required** for the main demo build.
# MAGIC It intentionally contains one easy-to-fix bug so Genie Code can repair it in one pass.

# COMMAND ----------

broken_practice_query = f"""
SELECT
  member_id,
  risk_scor,
  plan_type
FROM {MEMBERS_TABLE}
WHERE risk_scor >= 2.5
ORDER BY risk_scor DESC
LIMIT 10
"""

print("Running optional practice query (expected to fail)...")
display(spark.sql(broken_practice_query))
