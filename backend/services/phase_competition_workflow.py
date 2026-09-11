from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from backend.services.phase_benchmark import BenchmarkThresholds, build_phase_benchmark
from backend.services.phase_contradiction_analysis import (
    build_locked_split_manifest,
    build_paper_group_inventory,
    build_phase_annotation_queue,
    export_candidate_analysis,
    find_phase_contradiction_candidates,
    load_phase_observations,
)


WORKFLOW_VERSION = "phase-competition-workflow-v0.1"


def run_phase_competition_workflow(
    db_path: str | Path,
    output_dir: str | Path,
    *,
    annotations_path: str | Path | None = None,
    locked_validation_groups: Iterable[str] = (),
    min_similarity: float = 0.80,
    min_comparable_coverage: float = 0.25,
    max_candidates: int = 100,
    max_annotation_rows: int = 1000,
    row_limit: int | None = None,
    thresholds: BenchmarkThresholds | None = None,
) -> dict[str, Any]:
    """Run every automated phase-competition stage without bypassing human gates.

    The source database is opened only through the read-only loader. This
    workflow does not invoke an LLM, read API credentials, train a model, or run
    a VASP calculation.
    """

    source_db = Path(db_path).expanduser().resolve()
    target_dir = Path(output_dir).expanduser().resolve()
    candidate_dir = target_dir / "01_candidate_analysis"
    benchmark_dir = target_dir / "02_benchmark_readiness"
    target_dir.mkdir(parents=True, exist_ok=True)

    locked_groups = [str(value).strip() for value in locked_validation_groups if str(value).strip()]
    observations = load_phase_observations(source_db, limit=row_limit)
    candidates = find_phase_contradiction_candidates(
        observations,
        min_similarity=min_similarity,
        min_comparable_coverage=min_comparable_coverage,
        max_candidates=max_candidates,
    )
    annotation_queue = build_phase_annotation_queue(
        observations,
        max_rows=max_annotation_rows,
    )
    paper_inventory = build_paper_group_inventory(observations)
    split_manifest = (
        build_locked_split_manifest(observations, locked_groups)
        if locked_groups
        else []
    )
    candidate_summary = export_candidate_analysis(
        candidates,
        candidate_dir,
        observations=observations,
        split_manifest=split_manifest,
        annotation_queue=annotation_queue,
        paper_group_inventory=paper_inventory,
        parameters={
            "min_similarity": min_similarity,
            "min_comparable_coverage": min_comparable_coverage,
            "max_candidates": max_candidates,
            "max_annotation_rows": max_annotation_rows,
            "row_limit": row_limit,
            "database_access": "SQLite mode=ro&immutable=1; PRAGMA query_only=ON",
        },
    )

    generated_annotations = candidate_dir / "phase_observation_annotation_queue.csv"
    benchmark_annotations = (
        Path(annotations_path).expanduser().resolve()
        if annotations_path is not None
        else generated_annotations
    )
    inventory_path = candidate_dir / "paper_group_inventory.csv"
    benchmark_summary = build_phase_benchmark(
        benchmark_annotations,
        benchmark_dir,
        locked_validation_groups=locked_groups,
        thresholds=thresholds,
        paper_inventory_path=inventory_path,
    )

    benchmark_status = str(benchmark_summary["status"])
    if benchmark_status == "ready_for_training":
        workflow_status = "ready_for_training"
        next_action = "Train only the preregistered baseline on the emitted train partition."
    elif int(benchmark_summary.get("accepted_rows", 0)) == 0:
        workflow_status = "completed_waiting_for_human_annotation"
        next_action = (
            "Human-adjudicate the generated annotation queue, then rerun with "
            "--annotations and --locked-validation-groups."
        )
    else:
        workflow_status = "completed_waiting_for_benchmark_thresholds"
        next_action = "Resolve the readiness blockers without changing the locked validation set."

    workflow_summary = {
        "workflow_version": WORKFLOW_VERSION,
        "status": workflow_status,
        "database_path": str(source_db),
        "database_access": "read_only_immutable_query_only",
        "api_credentials_used": False,
        "llm_invoked": False,
        "model_trained": False,
        "vasp_invoked": False,
        "stages": {
            "candidate_analysis": {
                "status": "completed",
                "observations": candidate_summary["observations"],
                "paper_groups": candidate_summary["paper_groups"],
                "candidates": candidate_summary["candidates"],
                "classification_counts": candidate_summary["classification_counts"],
                "summary_path": candidate_summary["summary_path"],
            },
            "annotation_queue": {
                "status": "generated_pending_human_annotation",
                "rows": candidate_summary["annotation_queue_rows"],
                "path": candidate_summary["annotation_queue"],
            },
            "benchmark_readiness": {
                "status": benchmark_status,
                "accepted_rows": benchmark_summary["accepted_rows"],
                "accepted_paper_groups": benchmark_summary["accepted_paper_groups"],
                "blockers": benchmark_summary["blockers"],
                "readiness_path": benchmark_summary["readiness_path"],
            },
        },
        "annotations_input": str(benchmark_annotations),
        "locked_validation_groups_provided": len(locked_groups),
        "next_action": next_action,
        "interpretation_boundary": (
            "Automated workflow completion is not scientific adjudication and does not "
            "authorize model metrics or physical claims."
        ),
    }
    summary_path = target_dir / "phase_competition_workflow_summary.json"
    summary_path.write_text(
        json.dumps(workflow_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    workflow_summary["summary_path"] = str(summary_path)
    return workflow_summary

