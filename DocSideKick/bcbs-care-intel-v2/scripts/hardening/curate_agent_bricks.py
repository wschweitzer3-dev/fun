from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests
from databricks.sdk import WorkspaceClient


GENIE_SPACE_ID = "01f126e12bda187fb0fa9f49d1c6e585"
KA_TILE_ID = "c7630b94-d330-49bd-8d4c-0b6e3a2d6a03"
MAS_TILE_ID = "30532bb0-8393-4887-a91b-44e22f8c039b"

GENIE_QUESTIONS = [
    "Find diabetic members who haven't had an A1C test in the last 6 months.",
    "Show high-risk members with open gaps in care and their plan type.",
    "What is total claim cost for non-compliant diabetic members?",
    "Trend monthly claim cost for diagnosis code E11 over the last 12 months.",
    "Compare compliant vs non-compliant diabetic cohorts by average risk score and member count.",
]

KA_EXAMPLES = [
    {
        "question": "Why are members missing A1C testing?",
        "guideline": "Summarize the top documented barriers from notes and policy docs, cite doc_id values, and avoid speculation.",
    },
    {
        "question": "What barrier themes are care managers reporting most often?",
        "guideline": "Group barriers into concise themes, include supporting citations, and call out confidence based on source density.",
    },
    {
        "question": "Do the notes show transportation or cost-sharing barriers?",
        "guideline": "Confirm only with explicit evidence from notes, include citations, and state 'not found' if evidence is missing.",
    },
    {
        "question": "What do the clinical guidelines say about diabetes follow-up frequency?",
        "guideline": "Provide guideline frequency language directly grounded in policy content with doc_id citations.",
    },
    {
        "question": "What interventions are recommended for members overdue for A1C?",
        "guideline": "Recommend interventions strictly tied to documented barriers and guideline evidence; include citations.",
    },
]

MAS_INSTRUCTIONS = """You are the BCBS care intelligence supervisor.
Route structured analytics questions to the Genie agent.
Route barrier, narrative, and clinical guideline questions to the Knowledge Assistant.
For blended prompts, decompose into at most 2 tool calls by default.
Return one unified answer with three sections in order: cohort, barriers, actions.
Do not use meta language about being a supervisor or about internal routing.
Keep responses concise and executive-ready.
"""

MAS_EXAMPLES = [
    {
        "question": "Show high-risk members with open gaps in care by plan type.",
        "guideline": "Route to structured analytics and return a concise cohort summary with key counts.",
    },
    {
        "question": "What barriers are care managers seeing for members missing A1C?",
        "guideline": "Route to unstructured retrieval and cite relevant notes/guidelines with doc_id references.",
    },
    {
        "question": "Which diabetic members are overdue for A1C, why are they missing care, and what should we do?",
        "guideline": "Use both agents and return one integrated answer with cohort, barriers, and actions.",
    },
    {
        "question": "What is total cost for non-compliant diabetic members and how does it trend monthly?",
        "guideline": "Prioritize structured analytics; include concise cost totals and trend interpretation.",
    },
    {
        "question": "Explain this BCBS payer dataset in plain English.",
        "guideline": "Give a concise scope explanation of structured and unstructured assets without internal agent mechanics.",
    },
]


@dataclass
class ApiClient:
    workspace: WorkspaceClient

    @property
    def base_url(self) -> str:
        host = (self.workspace.config.host or "").strip().rstrip("/")
        if host.startswith("https://") or host.startswith("http://"):
            return host
        return f"https://{host}"

    @property
    def headers(self) -> dict[str, str]:
        headers = self.workspace.config.authenticate()
        headers["Content-Type"] = "application/json"
        return headers

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.request(
            method=method,
            url=f"{self.base_url}{path}",
            headers=self.headers,
            json=payload,
            timeout=90,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} failed: {response.status_code} {response.text}")
        return response.json() if response.text else {}

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload)

    def patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", path, payload)

    def delete(self, path: str) -> None:
        self._request("DELETE", path)


def build_workspace_client() -> WorkspaceClient:
    try:
        return WorkspaceClient()
    except Exception:
        host = os.getenv("DATABRICKS_HOST")
        token = os.getenv("DATABRICKS_TOKEN")
        if host and token:
            return WorkspaceClient(host=host, token=token)
        return WorkspaceClient(profile="DEFAULT")


def replace_genie_questions(api: ApiClient) -> None:
    current = api.get(
        f"/api/2.0/data-rooms/{GENIE_SPACE_ID}/curated-questions?question_type=SAMPLE_QUESTION"
    ).get("curated_questions", [])
    for q in current:
        qid = q.get("curated_question_id") or q.get("id") or q.get("question_id")
        if isinstance(qid, str) and qid:
            api.delete(f"/api/2.0/data-rooms/{GENIE_SPACE_ID}/curated-questions/{qid}")

    for text in GENIE_QUESTIONS:
        api.post(
            f"/api/2.0/data-rooms/{GENIE_SPACE_ID}/curated-questions",
            {
                "curated_question": {
                    "data_space_id": GENIE_SPACE_ID,
                    "question_text": text,
                    "question_type": "SAMPLE_QUESTION",
                    "is_deprecated": False,
                },
                "data_space_id": GENIE_SPACE_ID,
            },
        )


def _delete_examples_if_visible(api: ApiClient, collection_path: str) -> int:
    try:
        payload = api.get(collection_path)
    except RuntimeError:
        return 0
    deleted = 0
    for key in ("examples", "data", "items"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            example_id = item.get("example_id") or item.get("id")
            if isinstance(example_id, str) and example_id:
                try:
                    api.delete(f"{collection_path}/{example_id}")
                    deleted += 1
                except RuntimeError:
                    continue
    return deleted


def add_ka_examples(api: ApiClient) -> None:
    _delete_examples_if_visible(api, f"/api/2.0/knowledge-assistants/{KA_TILE_ID}/examples")
    for item in KA_EXAMPLES:
        api.post(
            f"/api/2.0/knowledge-assistants/{KA_TILE_ID}/examples",
            {"tile_id": KA_TILE_ID, "question": item["question"], "guidelines": [item["guideline"]]},
        )


def update_mas_instructions_and_examples(api: ApiClient) -> None:
    current = api.get(f"/api/2.0/multi-agent-supervisors/{MAS_TILE_ID}")
    mas = current.get("multi_agent_supervisor", {})
    agents = mas.get("agents", [])
    api.patch(
        f"/api/2.0/multi-agent-supervisors/{MAS_TILE_ID}",
        {
            "tile_id": MAS_TILE_ID,
            "name": mas.get("tile", {}).get("name") or "HLS_Payer_Supervisor",
            "description": mas.get("tile", {}).get("description") or "Supervisor for structured and unstructured payer analytics",
            "instructions": MAS_INSTRUCTIONS,
            "agents": agents,
        },
    )
    _delete_examples_if_visible(api, f"/api/2.0/multi-agent-supervisors/{MAS_TILE_ID}/examples")
    for item in MAS_EXAMPLES:
        api.post(
            f"/api/2.0/multi-agent-supervisors/{MAS_TILE_ID}/examples",
            {"tile_id": MAS_TILE_ID, "question": item["question"], "guidelines": [item["guideline"]]},
        )


def main() -> None:
    api = ApiClient(build_workspace_client())
    replace_genie_questions(api)
    add_ka_examples(api)
    update_mas_instructions_and_examples(api)

    genie_verify = api.get(
        f"/api/2.0/data-rooms/{GENIE_SPACE_ID}/curated-questions?question_type=SAMPLE_QUESTION"
    ).get("curated_questions", [])
    print(
        json.dumps(
            {
                "genie_sample_questions": len(genie_verify),
                "ka_examples_attempted": len(KA_EXAMPLES),
                "mas_examples_attempted": len(MAS_EXAMPLES),
                "status": "curation_applied",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
