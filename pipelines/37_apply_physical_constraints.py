from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import PROJECT_ROOT
from backend.services.physical_constraints import assessment_from_row
from backend.services.pipeline_log import record_pipeline_run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "data" / "design" / "hfo2_design_dataset.csv",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output_path = args.output or args.input.with_name(args.input.stem + "_physics_constrained.csv")
    stats = apply_physical_constraints_to_csv(args.input, output_path)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def apply_physical_constraints_to_csv(input_path: Path, output_path: Path) -> dict[str, object]:
    rows: list[dict[str, str]] = []
    with input_path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            row.update(assessment_from_row(row))
            rows.append(row)
    extra_fields = [
        "physical_consistency_score",
        "physical_recommendation_allowed",
        "physical_hard_violations",
        "physical_soft_warnings",
        "physical_risk_flags",
        "physical_descriptor_json",
        "phase_stability_score",
        "oxygen_vacancy_risk",
        "interface_oxygen_affinity",
        "process_window_score",
        "evidence_completeness_score",
    ]
    for field in extra_fields:
        if field not in fieldnames:
            fieldnames.append(field)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    scores = [
        float(row["physical_consistency_score"])
        for row in rows
        if str(row.get("physical_consistency_score") or "").strip()
    ]
    stats = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "rows": len(rows),
        "allowed_rows": sum(int(row.get("physical_recommendation_allowed") or 0) for row in rows),
        "mean_physical_consistency_score": sum(scores) / len(scores) if scores else None,
    }
    record_pipeline_run("37_apply_physical_constraints", "ok", stats)
    return stats


if __name__ == "__main__":
    main()
