#!/usr/bin/env python3
"""Optimize an existing Genie space for the Invisalign demo."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import timedelta
from typing import Any

from databricks.sdk import WorkspaceClient


SPACE_ID = "01f12ab84fe81e3690ea089379537b87"


TABLE_DESCRIPTIONS: dict[str, str] = {
    "ai_specialist.invisalign_demo.providers": (
        "Provider master dimension at provider grain. Contains specialty, geography, experience, "
        "practice profile, account ownership, and digital engagement used for segmentation and prioritization."
    ),
    "ai_specialist.invisalign_demo.products": (
        "Invisalign product dimension with tier, complexity, and list price attributes. "
        "Used to map case mix and upgrade paths (Express/Lite -> Comprehensive)."
    ),
    "ai_specialist.invisalign_demo.provider_products": (
        "Provider-to-product adoption bridge. Captures adoption timing, active status, "
        "product-level recent volume/revenue, and primary product assignment."
    ),
    "ai_specialist.invisalign_demo.cases": (
        "Case-level fact table (one row per submitted case). Source of volume, revenue, "
        "trend windows, and complexity/age-group mix analytics."
    ),
    "ai_specialist.invisalign_demo.propensity_scores": (
        "Provider-level next-best-product predictions and expected incremental revenue. "
        "Used to prioritize upsell targets and quantify projected lift."
    ),
}


COLUMN_UPDATES: dict[str, dict[str, dict[str, Any]]] = {
    "ai_specialist.invisalign_demo.providers": {
        "provider_id": {
            "description": "Unique provider account key used across all Invisalign analytics tables.",
            "synonyms": ["account id", "doctor id", "provider account"],
        },
        "provider_type": {
            "description": "Provider specialty segment (Orthodontist or GP Dentist).",
            "synonyms": ["specialty", "dentist type", "doctor type"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "region": {
            "description": "Sales geography for the provider account.",
            "synonyms": ["market region", "territory", "geography"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "years_experience": {
            "description": "Provider tenure in years, used for maturity and productivity segmentation.",
            "synonyms": ["experience years", "tenure years"],
        },
        "practice_size": {
            "description": "Practice scale bucket (Small, Medium, Large).",
            "synonyms": ["clinic size", "office size"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "avg_case_value": {
            "description": "Average revenue per case for the provider in USD.",
            "synonyms": ["average revenue per case", "avg revenue"],
        },
        "sales_rep_id": {
            "description": "Assigned Invisalign seller/owner for this account.",
            "synonyms": ["account rep", "territory rep", "owner rep"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "digital_engagement_score": {
            "description": "0-100 engagement score indicating portal/program interaction depth.",
            "synonyms": ["engagement score", "digital score", "platform engagement"],
        },
    },
    "ai_specialist.invisalign_demo.products": {
        "product_id": {
            "description": "Product key used by cases, adoptions, and propensity recommendations.",
            "synonyms": ["sku", "product code"],
        },
        "product_name": {
            "description": "Commercial Invisalign product name.",
            "synonyms": ["offering name", "sku name"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "product_tier": {
            "description": "Business tier grouping for mix/share and upgrade analysis.",
            "synonyms": ["tier", "package tier", "plan tier"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "price_band": {
            "description": "Standard product list price in USD used for revenue projections.",
            "synonyms": ["price", "list price", "price usd"],
        },
        "complexity_level": {
            "description": "Relative treatment complexity class for the product.",
            "synonyms": ["clinical complexity", "difficulty level"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
    },
    "ai_specialist.invisalign_demo.provider_products": {
        "provider_id": {
            "description": "Provider key for this adoption row.",
            "synonyms": ["account id", "doctor id"],
        },
        "product_id": {
            "description": "Adopted Invisalign product key.",
            "synonyms": ["sku", "product code"],
        },
        "adoption_date": {
            "description": "Date the provider adopted this product.",
            "synonyms": ["go-live date", "activation date"],
        },
        "status": {
            "description": "Current adoption status (typically Active).",
            "synonyms": ["adoption status", "product status"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "cases_last_90d": {
            "description": "90-day case count for this provider-product combination.",
            "synonyms": ["last 90 day volume", "recent case volume"],
        },
        "revenue_last_90d": {
            "description": "90-day revenue in USD for this provider-product combination.",
            "synonyms": ["recent product revenue", "last 90 day revenue"],
        },
        "is_primary": {
            "description": "True if this is the provider's top-revenue product in recent period.",
            "synonyms": ["primary product flag", "main product"],
        },
    },
    "ai_specialist.invisalign_demo.cases": {
        "case_id": {
            "description": "Unique case transaction key.",
            "synonyms": ["treatment case id", "case key"],
        },
        "provider_id": {
            "description": "Provider account key that submitted the case.",
            "synonyms": ["account id", "doctor id"],
        },
        "product_id": {
            "description": "Invisalign product key used for this case.",
            "synonyms": ["sku", "product code"],
        },
        "case_date": {
            "description": "Case submission date used for trend windows and period rollups.",
            "synonyms": ["submission date", "submit date", "case start date"],
        },
        "patient_age_group": {
            "description": "Patient segment for the case (Teen or Adult).",
            "synonyms": ["age segment", "patient segment"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "complexity": {
            "description": "Case complexity class (Low/Medium/High).",
            "synonyms": ["case difficulty", "treatment complexity"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "status": {
            "description": "Case lifecycle state (In Treatment, Completed, Refinement).",
            "synonyms": ["case status", "treatment status"],
            "enable_entity_matching": True,
            "enable_format_assistance": True,
        },
        "revenue": {
            "description": "Recognized case revenue in USD.",
            "synonyms": ["case revenue", "booked revenue"],
        },
    },
    "ai_specialist.invisalign_demo.propensity_scores": {
        "provider_id": {
            "description": "Provider account key for the propensity prediction.",
            "synonyms": ["account id", "doctor id"],
        },
        "next_best_product_id": {
            "description": "Recommended next product key for upsell.",
            "synonyms": ["recommended product", "next best offer", "nbp product"],
        },
        "propensity_score": {
            "description": "0-1 likelihood score that provider adopts recommended product.",
            "synonyms": ["likelihood score", "upgrade likelihood", "propensity"],
        },
        "expected_incremental_revenue": {
            "description": "Projected incremental USD revenue if recommended product is adopted.",
            "synonyms": ["expected lift", "projected revenue lift", "incremental revenue"],
        },
    },
}


def id32(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def update_table_and_columns(space: dict[str, Any]) -> dict[str, int]:
    changed_tables = 0
    changed_columns = 0
    changed_metric_view_columns = 0
    sources = space.setdefault("data_sources", {})
    for group_key in ("tables", "metric_views"):
        for table in sources.get(group_key, []) or []:
            ident = table.get("identifier")
            if ident in TABLE_DESCRIPTIONS:
                table["description"] = [TABLE_DESCRIPTIONS[ident]]
                changed_tables += 1
            col_map = COLUMN_UPDATES.get(ident, {})
            for cc in table.get("column_configs", []) or []:
                name = cc.get("column_name")
                upd = col_map.get(name)
                if not upd:
                    continue
                cc["description"] = [upd["description"]]
                if "synonyms" in upd:
                    cc["synonyms"] = upd["synonyms"]
                if "enable_entity_matching" in upd:
                    cc["enable_entity_matching"] = bool(upd["enable_entity_matching"])
                if "enable_format_assistance" in upd:
                    cc["enable_format_assistance"] = bool(upd["enable_format_assistance"])
                changed_columns += 1
                if group_key == "metric_views":
                    changed_metric_view_columns += 1
    return {
        "tables_updated": changed_tables,
        "columns_updated": changed_columns,
        "metric_view_columns_updated": changed_metric_view_columns,
    }


def prune_underperforming_tvfs(space: dict[str, Any]) -> dict[str, int]:
    sources = space.setdefault("data_sources", {})
    fn_tables = list(sources.get("function_tables", []) or [])
    if not fn_tables:
        return {"tvf_before": 0, "tvf_removed": 0, "tvf_after": 0}

    instr = space.get("instructions", {})
    ref_text_parts: list[str] = []
    for item in instr.get("example_question_sqls", []) or []:
        ref_text_parts.extend(item.get("sql", []) or [])
    for item in instr.get("join_specs", []) or []:
        ref_text_parts.extend(item.get("sql", []) or [])
    snippets = instr.get("sql_snippets", {}) or {}
    for bucket in ("measures", "expressions", "snippets"):
        for item in snippets.get(bucket, []) or []:
            ref_text_parts.extend(item.get("sql", []) or [])
    ref_blob = "\n".join(ref_text_parts).lower()

    keep: list[dict[str, Any]] = []
    removed = 0
    for fn in fn_tables:
        ident = (fn.get("identifier") or "").lower()
        if ident and ident in ref_blob:
            keep.append(fn)
        else:
            removed += 1

    sources["function_tables"] = keep
    return {"tvf_before": len(fn_tables), "tvf_removed": removed, "tvf_after": len(keep)}


def rewrite_join_specs(space: dict[str, Any]) -> int:
    join_specs = [
        {
            "id": id32("join:cases->providers"),
            "left": {"identifier": "ai_specialist.invisalign_demo.cases", "alias": "cases"},
            "right": {"identifier": "ai_specialist.invisalign_demo.providers", "alias": "providers"},
            "sql": [
                "`cases`.`provider_id` = `providers`.`provider_id`",
                "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--",
            ],
            "instruction": [
                "Primary provider-grain relationship for case volume/revenue and provider segmentation."
            ],
        },
        {
            "id": id32("join:cases->products"),
            "left": {"identifier": "ai_specialist.invisalign_demo.cases", "alias": "cases"},
            "right": {"identifier": "ai_specialist.invisalign_demo.products", "alias": "products"},
            "sql": [
                "`cases`.`product_id` = `products`.`product_id`",
                "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--",
            ],
            "instruction": [
                "Connect case facts to product tier/price attributes for mix and value analytics."
            ],
        },
        {
            "id": id32("join:provider_products->providers"),
            "left": {"identifier": "ai_specialist.invisalign_demo.provider_products", "alias": "provider_products"},
            "right": {"identifier": "ai_specialist.invisalign_demo.providers", "alias": "providers"},
            "sql": [
                "`provider_products`.`provider_id` = `providers`.`provider_id`",
                "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--",
            ],
            "instruction": [
                "Use for product adoption breadth, primary product, and provider-level adoption context."
            ],
        },
        {
            "id": id32("join:provider_products->products"),
            "left": {"identifier": "ai_specialist.invisalign_demo.provider_products", "alias": "provider_products"},
            "right": {"identifier": "ai_specialist.invisalign_demo.products", "alias": "products"},
            "sql": [
                "`provider_products`.`product_id` = `products`.`product_id`",
                "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--",
            ],
            "instruction": [
                "Use for adoption by product tier and product-level recent revenue/volume context."
            ],
        },
        {
            "id": id32("join:propensity->providers"),
            "left": {"identifier": "ai_specialist.invisalign_demo.propensity_scores", "alias": "propensity_scores"},
            "right": {"identifier": "ai_specialist.invisalign_demo.providers", "alias": "providers"},
            "sql": [
                "`propensity_scores`.`provider_id` = `providers`.`provider_id`",
                "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--",
            ],
            "instruction": [
                "Attach provider-level propensity and expected incremental revenue for target prioritization."
            ],
        },
        {
            "id": id32("join:propensity->products"),
            "left": {"identifier": "ai_specialist.invisalign_demo.propensity_scores", "alias": "propensity_scores"},
            "right": {"identifier": "ai_specialist.invisalign_demo.products", "alias": "products"},
            "sql": [
                "`propensity_scores`.`next_best_product_id` = `products`.`product_id`",
                "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--",
            ],
            "instruction": [
                "Resolve next-best product metadata so recommendations can return product names/tiers."
            ],
        },
    ]
    space.setdefault("instructions", {})["join_specs"] = sorted(join_specs, key=lambda x: x.get("id", ""))
    return len(join_specs)


def rewrite_global_instructions(space: dict[str, Any]) -> None:
    text = (
        "ROUTING:\n"
        "- Opportunity targeting questions: prioritize provider-level ranking with propensity_score, "
        "expected_incremental_revenue, has_comprehensive, and growth flags.\n"
        "- Decline diagnosis questions: compare last 30/60 day windows vs prior windows and quantify absolute + percent drop.\n"
        "- Forecast/impact questions: provide explicit revenue math and ranked cohorts.\n"
        "JOIN SAFETY:\n"
        "- Build case_rollup and provider_product_rollup CTEs before joining to providers to avoid duplicate counting.\n"
        "- Use one row per provider for prioritization outputs unless the user explicitly asks for product- or case-grain detail.\n"
        "OUTPUT RULES:\n"
        "- Return ranked, quantified outputs (counts, rates, USD impact).\n"
        "- Include assumptions and formulas when calculating lift projections.\n"
        "- If a user asks for campaign/action wording, keep analytics in Genie and recommend handing execution messaging to KA.\n"
    )
    content = [f"{line}\n" for line in text.splitlines() if line]
    space.setdefault("instructions", {})["text_instructions"] = [
        {
            "id": id32("text:provider_growth:routing:v2"),
            "content": content,
        }
    ]


def validate_schema(space: dict[str, Any]) -> None:
    sys.path.insert(
        0,
        "/Users/will.schweitzer/Documents/Coding/fun-demo/databricks-genie-workbench/packages/genie-space-optimizer/src",
    )
    from genie_space_optimizer.common.genie_schema import validate_serialized_space

    ok, errors = validate_serialized_space(space, strict=True)
    if not ok:
        raise RuntimeError(f"Serialized space validation failed: {errors[:10]}")


def normalize_sorted_sections(space: dict[str, Any]) -> None:
    config = space.setdefault("config", {})
    sq = config.get("sample_questions")
    if isinstance(sq, list):
        config["sample_questions"] = sorted(sq, key=lambda x: x.get("id", ""))

    instr = space.setdefault("instructions", {})
    js = instr.get("join_specs")
    if isinstance(js, list):
        instr["join_specs"] = sorted(js, key=lambda x: x.get("id", ""))

    benchmarks = space.setdefault("benchmarks", {})
    bq = benchmarks.get("questions")
    if isinstance(bq, list):
        benchmarks["questions"] = sorted(bq, key=lambda x: x.get("id", ""))


def main() -> None:
    w = WorkspaceClient()
    live = w.genie.get_space(space_id=SPACE_ID, include_serialized_space=True)
    raw = live.serialized_space
    if not raw:
        raise RuntimeError(f"Genie space {SPACE_ID} has no serialized_space payload.")
    space = json.loads(raw) if isinstance(raw, str) else raw

    tstats = update_table_and_columns(space)
    tvf_stats = prune_underperforming_tvfs(space)
    join_count = rewrite_join_specs(space)
    rewrite_global_instructions(space)
    normalize_sorted_sections(space)
    validate_schema(space)

    w.genie.update_space(
        space_id=SPACE_ID,
        title=live.title or "Provider Growth Analytics",
        description=live.description or "",
        warehouse_id=live.warehouse_id,
        serialized_space=json.dumps(space),
    )

    # Smoke-test with two production questions.
    q1 = "Which Midwest providers are Tier 1 targets for Invisalign Comprehensive, and what revenue lift could that drive?"
    q2 = "Which GP Dentists are declining over the last 60 days, and how severe is the drop?"
    m1 = w.genie.start_conversation_and_wait(space_id=SPACE_ID, content=q1, timeout=timedelta(minutes=10))
    m2 = w.genie.start_conversation_and_wait(space_id=SPACE_ID, content=q2, timeout=timedelta(minutes=10))

    out = {
        "space_id": SPACE_ID,
        "updated": {
            "tables_and_columns": tstats,
            "metric_views_present": len((space.get("data_sources", {}) or {}).get("metric_views", []) or []),
            "tvf_update": tvf_stats,
            "join_specs_count": join_count,
            "text_instructions_count": len((space.get("instructions", {}) or {}).get("text_instructions", []) or []),
        },
        "smoke_test": {
            "q1_status": str(m1.status.value) if m1.status else "UNKNOWN",
            "q2_status": str(m2.status.value) if m2.status else "UNKNOWN",
            "q1_has_sql": any((a.query is not None) for a in (m1.attachments or [])),
            "q2_has_sql": any((a.query is not None) for a in (m2.attachments or [])),
        },
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
