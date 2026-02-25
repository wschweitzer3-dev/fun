"""Doc Side Kick: synthetic multi-agent supervisor demo for healthcare questions.

This script coordinates:
- A supervisor router
- A structured expert (SQL-style filtering over patient CSV)
- An unstructured expert (keyword/semantic-style scanning over consult notes)

All data is strictly synthetic and HIPAA-safe.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PATIENTS_CSV = DATA_DIR / "patients.csv"
NOTES_DIR = DATA_DIR / "consult_notes"

TARGET_QUESTION = (
    "Which patients with heart conditions have specific mentions of fatigue "
    "in their consult notes?"
)


@dataclass
class PatientRecord:
    patient_id: str
    age: int
    sex: str
    heart_condition: str
    diagnosis: str
    medication: str
    last_visit_date: str


class StructuredExpert:
    """SQL-like filtering specialist over structured patient records."""

    def __init__(self, patients_csv: Path) -> None:
        self.patients_csv = patients_csv

    def _load_patients(self) -> List[PatientRecord]:
        rows: List[PatientRecord] = []
        with self.patients_csv.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append(
                    PatientRecord(
                        patient_id=row["patient_id"],
                        age=int(row["age"]),
                        sex=row["sex"],
                        heart_condition=row["heart_condition"],
                        diagnosis=row["diagnosis"],
                        medication=row["medication"],
                        last_visit_date=row["last_visit_date"],
                    )
                )
        return rows

    def heart_condition_patient_ids(self) -> Set[str]:
        """Equivalent SQL logic:

        SELECT patient_id
        FROM patients
        WHERE heart_condition = 'Yes';
        """
        return {
            patient.patient_id
            for patient in self._load_patients()
            if patient.heart_condition.strip().lower() == "yes"
        }


class UnstructuredExpert:
    """Consult-note scanner mimicking vector-search style symptom retrieval."""

    def __init__(self, notes_dir: Path) -> None:
        self.notes_dir = notes_dir

    def fatigue_mentions(self) -> Dict[str, List[str]]:
        """Find patient IDs with explicit fatigue mentions and capture snippets."""
        matches: Dict[str, List[str]] = {}

        for note_file in sorted(self.notes_dir.glob("*.txt")):
            text = note_file.read_text(encoding="utf-8")

            patient_match = re.search(r"Patient ID:\s*(P\d+)", text)
            if not patient_match:
                continue

            patient_id = patient_match.group(1)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            snippets = [line for line in lines if "fatigue" in line.lower()]

            if snippets:
                matches.setdefault(patient_id, []).extend(snippets)

        return matches


class SupervisorAgent:
    """Routes the clinician question and composes the final answer."""

    def __init__(self, structured: StructuredExpert, unstructured: UnstructuredExpert) -> None:
        self.structured = structured
        self.unstructured = unstructured

    def route(self, question: str) -> str:
        q = question.lower()
        needs_structured = any(token in q for token in ["heart condition", "patients", "which"])
        needs_unstructured = any(token in q for token in ["consult notes", "mentions", "fatigue"])

        if needs_structured and needs_unstructured:
            return "both"
        if needs_structured:
            return "structured"
        if needs_unstructured:
            return "unstructured"
        return "both"

    def answer(self, question: str) -> str:
        route = self.route(question)

        if route == "structured":
            ids = sorted(self.structured.heart_condition_patient_ids())
            return f"Structured-only result: {', '.join(ids)}"

        if route == "unstructured":
            note_hits = self.unstructured.fatigue_mentions()
            ids = sorted(note_hits.keys())
            return f"Unstructured-only fatigue mentions: {', '.join(ids)}"

        heart_ids = self.structured.heart_condition_patient_ids()
        fatigue_hits = self.unstructured.fatigue_mentions()

        final_ids = sorted(pid for pid in fatigue_hits if pid in heart_ids)

        if not final_ids:
            return "No patients matched heart-condition and fatigue-in-note criteria."

        details = []
        for pid in final_ids:
            snippets = " | ".join(fatigue_hits[pid])
            details.append(f"- {pid}: {snippets}")

        return (
            "Doc Side Kick Result\n"
            f"Question: {question}\n"
            "Matched patients with heart conditions and fatigue mentions in consult notes:\n"
            f"{', '.join(final_ids)}\n\n"
            "Supporting consult-note evidence:\n"
            + "\n".join(details)
            + "\n\n"
            "Data compliance: all records are synthetic and HIPAA-safe."
        )


def main() -> None:
    structured = StructuredExpert(PATIENTS_CSV)
    unstructured = UnstructuredExpert(NOTES_DIR)
    supervisor = SupervisorAgent(structured, unstructured)

    print(supervisor.answer(TARGET_QUESTION))


if __name__ == "__main__":
    main()
