from __future__ import annotations

from typing import Any, Iterable


REQUIRED_FIELDS = (
    "doi", "paper_id", "pdf_id", "page_number", "evidence_text",
    "thickness_nm", "voltage_or_frequency", "pr_or_2pr", "ec",
)


def benchmark_eligibility(row: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [name for name in REQUIRED_FIELDS if not str(row.get(name, "")).strip()]
    material = str(row.get("material", "")).lower().replace(" ", "")
    stack = str(row.get("stack", "")).lower().replace(" ", "")
    paper_type = str(row.get("paper_type", "")).lower()
    loop = str(row.get("pe_loop", row.get("full_pe_loop", ""))).lower()
    reasons = [f"missing:{name}" for name in missing]
    if paper_type not in {"experimental", "original_experimental"}:
        reasons.append("not_original_experimental")
    if not any(token in material for token in ("hf0.5zr0.5o2", "hzo50", "hzo_50")):
        reasons.append("not_hzo_50_50")
    if not all(token in stack for token in ("tin", "hzo")) or stack.count("tin") < 2:
        reasons.append("not_tin_hzo_tin")
    if loop not in {"1", "true", "yes", "full", "complete"}:
        reasons.append("full_pe_loop_not_confirmed")
    return not reasons, reasons


def select_benchmark(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    eligible: list[tuple[tuple[float, float, str], dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        ok, reasons = benchmark_eligibility(row)
        if not ok:
            rejected.append({"paper_id": row.get("paper_id", ""), "reasons": reasons})
            continue
        thickness_count = int(row.get("thickness_condition_count") or 1)
        thickness_distance = abs(float(row["thickness_nm"]) - 10.0)
        key = (-float(thickness_count >= 2), thickness_distance, str(row["doi"]).lower())
        eligible.append((key, row))
    eligible.sort(key=lambda item: item[0])
    if not eligible:
        return {
            "status": "no_evidence_complete_benchmark",
            "quantitative_calibration_allowed": False,
            "selected": None,
            "rejected": rejected,
        }
    return {
        "status": "benchmark_selected",
        "quantitative_calibration_allowed": True,
        "selected": eligible[0][1],
        "eligible_count": len(eligible),
        "rejected": rejected,
    }

