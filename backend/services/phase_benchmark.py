from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from backend.services.phase_contradiction_analysis import normalize_doi


BENCHMARK_VERSION = "phase-competition-benchmark-v0.1"
ALLOWED_PHASE_LABELS = {"m", "o", "t"}
ALLOWED_PHASE_SCOPES = {
    "dominant_bulk_average",
    "minority",
    "mixed_unquantified",
    "local_grain",
    "interface_local",
    "orientation_specific",
}
ACCEPTED_ADJUDICATION_STATUSES = {"adjudicated"}
PRIMARY_SOURCE_TYPE = "primary_experiment"


@dataclass(frozen=True)
class BenchmarkThresholds:
    min_total_paper_groups: int = 30
    min_train_groups_per_phase: int = 15
    min_validation_groups_per_phase: int = 3


def normalize_paper_group(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("doi:"):
        return f"doi:{normalize_doi(text.split(':', 1)[1])}"
    if text.startswith("paper:"):
        return text
    doi = normalize_doi(text)
    return f"doi:{doi}" if doi.startswith("10.") else f"paper:{text}"


def parse_review_phase_labels(value: Any) -> tuple[str, ...]:
    text = str(value or "").strip().lower().replace("/", "+").replace(",", "+")
    labels = tuple(sorted({part.strip() for part in text.split("+") if part.strip()}))
    if not labels or not set(labels).issubset(ALLOWED_PHASE_LABELS):
        return ()
    return labels


def build_phase_benchmark(
    annotation_path: str | Path,
    output_dir: str | Path,
    *,
    locked_validation_groups: Iterable[str] = (),
    thresholds: BenchmarkThresholds | None = None,
    paper_inventory_path: str | Path | None = None,
) -> dict[str, Any]:
    limits = thresholds or BenchmarkThresholds()
    source_path = Path(annotation_path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = _read_csv(source_path)

    accepted: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for row in raw_rows:
        normalized, reasons = validate_annotation_row(row)
        if reasons:
            exclusions.append(
                {
                    "annotation_id": row.get("annotation_id", ""),
                    "paper_group": normalize_paper_group(row.get("paper_group")),
                    "exclusion_reasons": ";".join(reasons),
                }
            )
        elif normalized is not None:
            accepted.append(normalized)

    accepted = _deduplicate_annotations(accepted)
    locked = {normalize_paper_group(value) for value in locked_validation_groups if str(value).strip()}
    observed_groups = {row["paper_group"] for row in accepted}
    unmatched_locked_groups = sorted(locked - observed_groups)
    for row in accepted:
        row["partition"] = (
            "locked_validation"
            if locked and row["paper_group"] in locked
            else "train"
            if locked
            else "unassigned"
        )
    assert_zero_group_leakage(accepted)

    train_rows = [row for row in accepted if row["partition"] == "train"]
    validation_rows = [row for row in accepted if row["partition"] == "locked_validation"]
    all_path = target_dir / "phase_benchmark_all.csv"
    train_path = target_dir / "phase_benchmark_train.csv"
    validation_path = target_dir / "phase_benchmark_locked_validation.csv"
    exclusions_path = target_dir / "phase_benchmark_exclusions.csv"
    readiness_path = target_dir / "phase_benchmark_readiness.json"
    protocol_path = target_dir / "phase_benchmark_metric_protocol.json"
    selection_frame_path = target_dir / "locked_validation_selection_frame.csv"

    _write_csv(all_path, accepted, _benchmark_fields())
    _write_csv(train_path, train_rows, _benchmark_fields())
    _write_csv(validation_path, validation_rows, _benchmark_fields())
    _write_csv(
        exclusions_path,
        exclusions,
        ["annotation_id", "paper_group", "exclusion_reasons"],
    )

    selection_rows = (
        build_validation_selection_frame(paper_inventory_path)
        if paper_inventory_path is not None
        else []
    )
    _write_csv(selection_frame_path, selection_rows, _selection_frame_fields())

    readiness = _readiness_report(
        raw_rows=raw_rows,
        accepted=accepted,
        exclusions=exclusions,
        train_rows=train_rows,
        validation_rows=validation_rows,
        locked_groups=locked,
        unmatched_locked_groups=unmatched_locked_groups,
        thresholds=limits,
    )
    readiness.update(
        {
            "benchmark_version": BENCHMARK_VERSION,
            "annotation_path": str(source_path),
            "all_rows_path": str(all_path),
            "train_path": str(train_path),
            "locked_validation_path": str(validation_path),
            "exclusions_path": str(exclusions_path),
            "selection_frame_path": str(selection_frame_path),
            "metric_protocol_path": str(protocol_path),
        }
    )
    readiness_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2), encoding="utf-8")
    metric_protocol = _metric_protocol()
    protocol_path.write_text(json.dumps(metric_protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    readiness["readiness_path"] = str(readiness_path)
    return readiness


def validate_annotation_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    paper_group = normalize_paper_group(row.get("paper_group"))
    if paper_group in {"paper:", "doi:"}:
        reasons.append("missing_paper_group")
    if str(row.get("adjudication_status") or "").strip() not in ACCEPTED_ADJUDICATION_STATUSES:
        reasons.append("not_adjudicated")
    if str(row.get("review_source_type") or "").strip() != PRIMARY_SOURCE_TYPE:
        reasons.append("not_primary_experiment")
    phase_labels = parse_review_phase_labels(row.get("review_phase_label"))
    if not phase_labels:
        reasons.append("invalid_or_unresolved_phase_label")
    phase_scope = str(row.get("review_phase_scope") or "").strip()
    if phase_scope not in ALLOWED_PHASE_SCOPES:
        reasons.append("invalid_or_unresolved_phase_scope")
    if not str(row.get("phase_evidence_text") or "").strip():
        reasons.append("missing_phase_evidence")
    if not str(row.get("page_number") or "").strip() and not str(row.get("chunk_id") or "").strip():
        reasons.append("missing_evidence_locator")
    annotator = str(row.get("annotator") or "").strip()
    if not annotator:
        reasons.append("missing_annotator")
    conditions = _json_mapping(row.get("review_sample_conditions"))
    if conditions is None:
        reasons.append("review_sample_conditions_must_be_json_object")
    if reasons:
        return None, sorted(set(reasons))

    normalized = {
        "annotation_id": str(row.get("annotation_id") or ""),
        "paper_group": paper_group,
        "doi": normalize_doi(row.get("doi")),
        "paper_id": str(row.get("paper_id") or ""),
        "title": str(row.get("title") or ""),
        "pdf_id": str(row.get("pdf_id") or ""),
        "page_number": str(row.get("page_number") or ""),
        "chunk_id": str(row.get("chunk_id") or ""),
        "material_system": str(row.get("material_system") or ""),
        "material_name": str(row.get("material_name") or ""),
        "zr_fraction": str(row.get("zr_fraction") or ""),
        "phase_labels": "+".join(phase_labels),
        "phase_scope": phase_scope,
        "characterization_method": str(row.get("characterization_method") or ""),
        "phase_evidence_text": str(row.get("phase_evidence_text") or "").strip(),
        "phase_evidence_source": str(row.get("phase_evidence_source") or ""),
        "sample_conditions_json": json.dumps(conditions, ensure_ascii=False, sort_keys=True),
        "source_type": PRIMARY_SOURCE_TYPE,
        "adjudication_status": "adjudicated",
        "annotator": annotator,
        "review_notes": str(row.get("review_notes") or ""),
        "partition": "unassigned",
    }
    return normalized, []


def assert_zero_group_leakage(rows: Sequence[dict[str, Any]]) -> None:
    partitions: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        partition = str(row.get("partition") or "")
        if partition in {"train", "locked_validation"}:
            partitions[str(row.get("paper_group") or "")].add(partition)
    leaked = sorted(group for group, values in partitions.items() if len(values) > 1)
    if leaked:
        raise ValueError(f"Paper-group leakage detected: {leaked}")


def build_validation_selection_frame(inventory_path: str | Path) -> list[dict[str, Any]]:
    rows = _read_csv(Path(inventory_path))
    output = []
    for row in rows:
        phase_stratum = "+".join(
            phase
            for phase, field in (("m", "m_observations"), ("o", "o_observations"), ("t", "t_observations"))
            if _safe_int(row.get(field)) > 0
        ) or "unresolved"
        observations = max(1, _safe_int(row.get("observations")))
        quality_pass = _safe_int(row.get("quality_gate_pass_observations"))
        output.append(
            {
                "paper_group": normalize_paper_group(row.get("paper_group")),
                "doi": str(row.get("doi") or ""),
                "paper_id": str(row.get("paper_id") or ""),
                "title": str(row.get("title") or ""),
                "material_systems": str(row.get("material_systems") or ""),
                "phase_stratum": phase_stratum,
                "observations": observations,
                "quality_gate_pass_observations": quality_pass,
                "quality_pass_fraction": round(quality_pass / observations, 4),
                "is_review": _safe_int(row.get("is_review")),
                "selection_status": "human_selection_required",
                "selected_partition": "",
                "selection_notes": "",
            }
        )
    output.sort(
        key=lambda row: (
            int(row["is_review"]),
            -float(row["quality_pass_fraction"]),
            -int(row["quality_gate_pass_observations"]),
            str(row["paper_group"]),
        )
    )
    return output


def _readiness_report(
    *,
    raw_rows: Sequence[dict[str, Any]],
    accepted: Sequence[dict[str, Any]],
    exclusions: Sequence[dict[str, Any]],
    train_rows: Sequence[dict[str, Any]],
    validation_rows: Sequence[dict[str, Any]],
    locked_groups: set[str],
    unmatched_locked_groups: Sequence[str],
    thresholds: BenchmarkThresholds,
) -> dict[str, Any]:
    accepted_groups = {row["paper_group"] for row in accepted}
    train_phase_groups = _phase_group_counts(train_rows)
    validation_phase_groups = _phase_group_counts(validation_rows)
    criteria = {
        "minimum_total_paper_groups": len(accepted_groups) >= thresholds.min_total_paper_groups,
        "locked_validation_groups_provided": bool(locked_groups),
        "all_locked_groups_resolved": not unmatched_locked_groups,
        "train_minimum_groups_per_phase": all(
            train_phase_groups[phase] >= thresholds.min_train_groups_per_phase
            for phase in sorted(ALLOWED_PHASE_LABELS)
        ),
        "validation_minimum_groups_per_phase": all(
            validation_phase_groups[phase] >= thresholds.min_validation_groups_per_phase
            for phase in sorted(ALLOWED_PHASE_LABELS)
        ),
        "zero_paper_group_leakage": True,
    }
    blockers = [name for name, passed in criteria.items() if not passed]
    exclusion_counts = Counter(
        reason
        for row in exclusions
        for reason in str(row.get("exclusion_reasons") or "").split(";")
        if reason
    )
    return {
        "status": "ready_for_training" if not blockers else "not_ready",
        "metrics_status": "not_computed",
        "raw_annotation_rows": len(raw_rows),
        "accepted_rows": len(accepted),
        "accepted_paper_groups": len(accepted_groups),
        "excluded_rows": len(exclusions),
        "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
        "train_rows": len(train_rows),
        "train_paper_groups": len({row["paper_group"] for row in train_rows}),
        "locked_validation_rows": len(validation_rows),
        "locked_validation_paper_groups": len({row["paper_group"] for row in validation_rows}),
        "train_phase_group_counts": train_phase_groups,
        "validation_phase_group_counts": validation_phase_groups,
        "unmatched_locked_groups": list(unmatched_locked_groups),
        "thresholds": {
            "min_total_paper_groups": thresholds.min_total_paper_groups,
            "min_train_groups_per_phase": thresholds.min_train_groups_per_phase,
            "min_validation_groups_per_phase": thresholds.min_validation_groups_per_phase,
        },
        "criteria": criteria,
        "blockers": blockers,
        "split_policy": "DOI/paper-group locked; row fallback prohibited",
        "interpretation_boundary": "This report contains no trained model and no performance metric.",
    }


def _phase_group_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    groups: dict[str, set[str]] = {phase: set() for phase in ALLOWED_PHASE_LABELS}
    for row in rows:
        for phase in parse_review_phase_labels(row.get("phase_labels")):
            groups[phase].add(str(row["paper_group"]))
    return {phase: len(groups[phase]) for phase in sorted(groups)}


def _deduplicate_annotations(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("annotation_id") or "") or "|".join(
            [
                str(row.get("paper_group") or ""),
                str(row.get("chunk_id") or ""),
                str(row.get("phase_labels") or ""),
            ]
        )
        selected.setdefault(key, row)
    return sorted(selected.values(), key=lambda row: (row["paper_group"], row["annotation_id"]))


def _json_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _benchmark_fields() -> list[str]:
    return [
        "annotation_id",
        "paper_group",
        "doi",
        "paper_id",
        "title",
        "pdf_id",
        "page_number",
        "chunk_id",
        "material_system",
        "material_name",
        "zr_fraction",
        "phase_labels",
        "phase_scope",
        "characterization_method",
        "phase_evidence_text",
        "phase_evidence_source",
        "sample_conditions_json",
        "source_type",
        "adjudication_status",
        "annotator",
        "review_notes",
        "partition",
    ]


def _selection_frame_fields() -> list[str]:
    return [
        "paper_group",
        "doi",
        "paper_id",
        "title",
        "material_systems",
        "phase_stratum",
        "observations",
        "quality_gate_pass_observations",
        "quality_pass_fraction",
        "is_review",
        "selection_status",
        "selected_partition",
        "selection_notes",
    ]


def _metric_protocol() -> dict[str, Any]:
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "status": "protocol_only_no_metrics_computed",
        "primary_task": "sample-level m/o/t multi-label phase outcome",
        "secondary_task": "evidence-consistency classification after pair adjudication",
        "auxiliary_targets": ["Pr", "2Pr"],
        "split": "locked DOI/paper-group holdout; no row fallback",
        "phase_metrics": [
            "macro_f1",
            "per_phase_precision_recall_f1",
            "multi_label_exact_match",
            "jaccard_score",
            "brier_score_or_calibration_error_when_probabilities_exist",
        ],
        "consistency_metrics": ["three_class_confusion_matrix", "macro_f1"],
        "prohibitions": [
            "No metric before a locked validation set exists.",
            "No threshold tuning on locked validation papers.",
            "No sample, chunk, figure, supplement, or duplicate PDF from one paper across partitions.",
            "No Pr/2Pr regression result presented as phase-stability validation.",
        ],
    }

