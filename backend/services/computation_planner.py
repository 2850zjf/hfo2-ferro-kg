from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from backend.core.config import PROJECT_ROOT
from backend.services.active_learning import recommend_active_learning_candidates
from backend.services.pipeline_log import record_pipeline_run


TASK_COLUMNS = [
    "task_id",
    "candidate_id",
    "task_family",
    "engine",
    "objective",
    "priority_score",
    "material_name",
    "formula",
    "dopant_elements",
    "zr_fraction",
    "film_thickness_nm",
    "deposition_method",
    "annealing_temperature_c",
    "annealing_time_s",
    "annealing_atmosphere",
    "electrode_stack",
    "device_type",
    "phase_name",
    "structure_model",
    "calculation_scope",
    "required_inputs",
    "expected_outputs",
    "method_quality_gate",
    "validation_checks",
    "claim_boundary",
    "kg_writeback",
    "benchmark_writeback",
    "cloud_execution_hint",
    "safety_status",
    "rationale",
]


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _text(value: Any) -> str:
    if value in (None, float("nan")):
        return ""
    return str(value).strip()


def _json_list(items: list[str]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _base_context(row: pd.Series) -> dict[str, Any]:
    return {
        "candidate_id": _text(row.get("candidate_id")),
        "material_name": _text(row.get("material_name")),
        "formula": _text(row.get("formula")),
        "dopant_elements": _text(row.get("dopant_elements")),
        "zr_fraction": _safe_float(row.get("zr_fraction")),
        "film_thickness_nm": _safe_float(row.get("film_thickness_nm")),
        "deposition_method": _text(row.get("deposition_method")),
        "annealing_temperature_c": _safe_float(row.get("annealing_temperature_c")),
        "annealing_time_s": _safe_float(row.get("annealing_time_s")),
        "annealing_atmosphere": _text(row.get("annealing_atmosphere")),
        "electrode_stack": _text(row.get("electrode_stack")),
        "device_type": _text(row.get("device_type")),
        "phase_name": _text(row.get("phase_name")),
    }


def _candidate_priority(row: pd.Series) -> float:
    base = _safe_float(row.get("active_learning_score")) or 0.0
    uncertainty = _safe_float(row.get("uncertainty")) or 0.0
    evidence = _safe_float(row.get("evidence_score")) or 0.0
    return round(0.72 * base + 0.18 * min(1.0, uncertainty / 20.0) + 0.10 * evidence, 4)


def _task(
    row: pd.Series,
    index: int,
    *,
    task_family: str,
    engine: str,
    objective: str,
    priority_bonus: float,
    structure_model: str,
    calculation_scope: str,
    required_inputs: list[str],
    expected_outputs: list[str],
    method_quality_gate: list[str],
    validation_checks: list[str],
    claim_boundary: str,
    kg_writeback: list[str],
    benchmark_writeback: list[str],
    cloud_execution_hint: str,
    rationale: str,
) -> dict[str, Any]:
    context = _base_context(row)
    priority = max(0.0, min(1.0, _candidate_priority(row) + priority_bonus))
    return {
        "task_id": f"calc_{index:05d}",
        **context,
        "task_family": task_family,
        "engine": engine,
        "objective": objective,
        "priority_score": round(priority, 4),
        "structure_model": structure_model,
        "calculation_scope": calculation_scope,
        "required_inputs": _json_list(required_inputs),
        "expected_outputs": _json_list(expected_outputs),
        "method_quality_gate": _json_list(method_quality_gate),
        "validation_checks": _json_list(validation_checks),
        "claim_boundary": claim_boundary,
        "kg_writeback": _json_list(kg_writeback),
        "benchmark_writeback": _json_list(benchmark_writeback),
        "cloud_execution_hint": cloud_execution_hint,
        "safety_status": "planned_only_no_local_execution",
        "rationale": rationale,
    }


def _needs_vacancy_task(row: pd.Series) -> bool:
    atmosphere = _text(row.get("annealing_atmosphere")).lower()
    stack = _text(row.get("electrode_stack")).lower()
    return any(token in atmosphere for token in ["forming", "h2", "vacuum", "n2"]) or any(
        token in stack for token in ["tin", "w", "ta", "ti"]
    )


def _needs_interface_task(row: pd.Series) -> bool:
    stack = _text(row.get("electrode_stack"))
    return "/" in stack or bool(_text(row.get("top_electrode"))) or bool(_text(row.get("bottom_electrode")))


def _needs_dynamics_task(row: pd.Series) -> bool:
    anneal_temp = _safe_float(row.get("annealing_temperature_c")) or 0.0
    thickness = _safe_float(row.get("film_thickness_nm")) or 0.0
    return anneal_temp >= 500 or thickness <= 8


def _build_tasks_for_candidate(row: pd.Series, start_index: int) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    material = _text(row.get("material_name")) or "HfO2/HZO candidate"
    phase = _text(row.get("phase_name")) or "orthorhombic target phase"
    thickness = _safe_float(row.get("film_thickness_nm"))

    tasks.append(
        _task(
            row,
            start_index + len(tasks),
            task_family="phase_stability",
            engine="VASP_DFT",
            objective="Compare orthorhombic, tetragonal, monoclinic, and cubic polymorph stability for the candidate composition.",
            priority_bonus=0.10,
            structure_model="bulk_or_strained_supercell",
            calculation_scope="composition_phase_energy",
            required_inputs=[
                "candidate formula or Hf/Zr/dopant fraction",
                "candidate phase label",
                "strain/thickness proxy when available",
                "reference HfO2/HZO polymorph structures",
            ],
            expected_outputs=[
                "relative_energy_orthorhombic_vs_monoclinic_meV_fu",
                "relative_energy_orthorhombic_vs_tetragonal_meV_fu",
                "polarization_estimate_uC_cm2",
                "phase_stability_label",
            ],
            method_quality_gate=[
                "converge ENCUT, k-point mesh, force, and stress thresholds",
                "compare orthorhombic, monoclinic, tetragonal, and cubic reference structures under the same settings",
                "record functional/pseudopotential choices and calibrate against literature or HSE06 when possible",
                "use Berry-phase polarization only after structural relaxation is stable",
            ],
            validation_checks=[
                "energy ranking reproducible under tighter numerical settings",
                "no imaginary or obviously unstable relaxed reference when phonon/elastic checks are available",
                "composition and dopant supercell match the candidate assumption",
            ],
            claim_boundary="DFT phase stability supports physical feasibility; it does not directly prove experimental Pr/2Pr.",
            kg_writeback=["ComputationalTask", "ComputedDescriptor", "PhaseStructure"],
            benchmark_writeback=[
                "deltaE_o_m_meV_fu",
                "deltaE_o_t_meV_fu",
                "computed_polarization_uC_cm2",
            ],
            cloud_execution_hint="Generate POSCAR/KPOINTS/INCAR for Tencent Cloud queue after manual structure confirmation.",
            rationale=f"{material} is a Pr/2Pr design candidate; phase stability is the first physics gate.",
        )
    )

    if _needs_vacancy_task(row):
        tasks.append(
            _task(
                row,
                start_index + len(tasks),
                task_family="oxygen_vacancy",
                engine="VASP_DFT_NEB_optional",
                objective="Estimate oxygen-vacancy formation and migration risk under the reported electrode/annealing context.",
                priority_bonus=0.06,
                structure_model="defective_supercell",
                calculation_scope="oxygen_defect_thermodynamics",
                required_inputs=[
                    "relaxed parent polymorph",
                    "oxygen chemical potential assumption",
                    "charge-state policy",
                    "electrode or atmosphere context",
                ],
                expected_outputs=[
                    "oxygen_vacancy_formation_energy_eV",
                    "migration_barrier_eV_optional",
                    "vacancy_stabilization_risk",
                    "possible_wake_up_or_fatigue_link",
                ],
                method_quality_gate=[
                    "state oxygen chemical potential and charge-state policy",
                    "sample inequivalent oxygen sites in the selected phase or interface model",
                    "apply finite-size and potential-alignment corrections for charged defects when used",
                    "run NEB only after stable endpoints are confirmed",
                ],
                validation_checks=[
                    "formation-energy trend robust across representative vacancy sites",
                    "migration path endpoints relax to expected local minima",
                    "defect concentration assumption is reported rather than hidden",
                ],
                claim_boundary="Vacancy calculations provide risk descriptors for wake-up, fatigue, leakage, and phase stabilization; they are not direct endurance predictions.",
                kg_writeback=["ComputationalTask", "DefectDescriptor", "RiskAssessment"],
                benchmark_writeback=[
                    "oxygen_vacancy_formation_energy_eV",
                    "oxygen_vacancy_migration_barrier_eV",
                    "defect_risk_score",
                ],
                cloud_execution_hint="Plan static defect calculations first; NEB only after parent and vacancy sites are validated.",
                rationale="Oxygen vacancies are central to HfO2/HZO wake-up, fatigue, leakage, and phase stabilization.",
            )
        )

    if _needs_interface_task(row):
        tasks.append(
            _task(
                row,
                start_index + len(tasks),
                task_family="interface_screening",
                engine="DFT_or_surrogate_descriptor",
                objective="Screen whether the electrode stack may stabilize or destabilize the ferroelectric phase through interface and oxygen-scavenging effects.",
                priority_bonus=0.03,
                structure_model="thin_film_interface_slab_or_descriptor",
                calculation_scope="electrode_interface",
                required_inputs=[
                    "electrode stack",
                    "surface orientation assumption",
                    "film composition",
                    "available work-function or oxygen-affinity descriptors",
                ],
                expected_outputs=[
                    "interface_energy_proxy",
                    "oxygen_scavenging_risk",
                    "electrode_phase_stabilization_label",
                ],
                method_quality_gate=[
                    "declare orientation, termination, slab thickness, and vacuum or boundary assumptions",
                    "start with literature/surrogate descriptors before expensive explicit slabs",
                    "use identical reference states when comparing electrode stacks",
                ],
                validation_checks=[
                    "interface descriptor trend is consistent with known electrode oxygen affinity or work-function context",
                    "explicit slab calculations are reserved for top-ranked stacks after descriptor screening",
                ],
                claim_boundary="Interface screening ranks likely electrode effects; it does not replace device-level electrical characterization.",
                kg_writeback=["ComputationalTask", "InterfaceDescriptor", "ElectrodeStack"],
                benchmark_writeback=[
                    "interface_energy_proxy",
                    "oxygen_scavenging_risk_score",
                    "electrode_descriptor_label",
                ],
                cloud_execution_hint="Start with descriptor screening; run explicit DFT slabs only for top-ranked stacks.",
                rationale="Electrode and interface conditions often explain Pr/2Pr spread between nominally similar HZO samples.",
            )
        )

    if thickness is not None and thickness > 0:
        tasks.append(
            _task(
                row,
                start_index + len(tasks),
                task_family="phase_field_switching",
                engine="phase_field_or_compact_switching_model",
                objective="Simulate thickness- and boundary-condition-dependent domain switching trend for the candidate.",
                priority_bonus=0.02,
                structure_model="thin_film_domain_model",
                calculation_scope="switching_and_domain_evolution",
                required_inputs=[
                    "film thickness",
                    "electrode boundary condition",
                    "phase-stability descriptors",
                    "target Pr or 2Pr benchmark value",
                ],
                expected_outputs=[
                    "simulated_Pr_or_2Pr_trend",
                    "domain_fraction_proxy",
                    "coercive_field_trend",
                    "wake_up_sensitivity_label",
                ],
                method_quality_gate=[
                    "state Landau, gradient, elastic, electrostatic, and boundary-condition assumptions",
                    "use thickness and electrode screening conditions consistent with the candidate",
                    "calibrate qualitative trends with available HZO/HfO2 thin-film literature",
                ],
                validation_checks=[
                    "trend direction remains stable under mesh/time-step refinement",
                    "reported outputs are trend descriptors, not absolute experimental Pr claims",
                ],
                claim_boundary="Phase-field output is a thin-film switching trend descriptor; it should not be reported as measured Pr/2Pr.",
                kg_writeback=["ComputationalTask", "SwitchingDescriptor", "DesignConstraint"],
                benchmark_writeback=[
                    "phase_field_pr_trend",
                    "domain_fraction_proxy",
                    "computed_Ec_trend",
                ],
                cloud_execution_hint="Run after DFT phase descriptors are available; use same validation split for downstream model comparison.",
                rationale=f"{phase} and {thickness:g} nm thickness should be checked at the thin-film switching level.",
            )
        )

    if _needs_dynamics_task(row):
        tasks.append(
            _task(
                row,
                start_index + len(tasks),
                task_family="annealing_dynamics",
                engine="ML_potential_MD_or_kinetic_surrogate",
                objective="Assess whether annealing temperature or ultrathin geometry may drive defect redistribution or phase conversion.",
                priority_bonus=0.01,
                structure_model="large_supercell_or_surrogate_dynamics",
                calculation_scope="annealing_path_dependence",
                required_inputs=[
                    "annealing temperature and time",
                    "composition and dopants",
                    "validated ML potential or literature-calibrated surrogate",
                    "defect concentration assumption",
                ],
                expected_outputs=[
                    "oxygen_diffusion_proxy",
                    "phase_conversion_risk",
                    "thermal_process_sensitivity",
                ],
                method_quality_gate=[
                    "use only a HfO2/HZO/dopant/defect potential validated on relevant DFT configurations",
                    "track out-of-domain uncertainty or disagreement when using ML potentials",
                    "report time/temperature scaling limitations for annealing surrogates",
                ],
                validation_checks=[
                    "benchmark potential energies/forces against held-out DFT structures",
                    "reject MD conclusions when the candidate chemistry is outside the potential training domain",
                ],
                claim_boundary="ML-potential MD or kinetic surrogates estimate process sensitivity; they are not direct experimental annealing outcomes.",
                kg_writeback=["ComputationalTask", "ProcessDescriptor", "RiskAssessment"],
                benchmark_writeback=[
                    "oxygen_diffusion_proxy",
                    "phase_conversion_risk_score",
                    "thermal_sensitivity_label",
                ],
                cloud_execution_hint="Only run with a validated HfO2/HZO potential; otherwise keep as surrogate descriptor task.",
                rationale="Annealing path dependence is a major unresolved source of sample-to-sample variation.",
            )
        )

    return tasks


def _load_candidates(candidates_path: Path | None = None) -> tuple[pd.DataFrame, Path]:
    target = candidates_path or PROJECT_ROOT / "data" / "design" / "active_learning_candidates.csv"
    if not target.exists():
        recommend_active_learning_candidates()
    if not target.exists():
        return pd.DataFrame(), target
    return pd.read_csv(target), target


def plan_computational_feedback_tasks(
    candidates_path: Path | None = None,
    output_dir: Path | None = None,
    max_candidates: int = 20,
    max_tasks: int = 80,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Plan computational validation tasks without launching local or cloud jobs."""

    candidates, source_path = _load_candidates(candidates_path)
    out_dir = output_dir or PROJECT_ROOT / "data" / "computation"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "computational_feedback_tasks.csv"
    json_path = out_dir / "computational_feedback_tasks.json"
    report_path = out_dir / "computational_feedback_plan.md"

    if candidates.empty:
        empty = pd.DataFrame(columns=TASK_COLUMNS)
        empty.to_csv(csv_path, index=False, encoding="utf-8-sig")
        json_path.write_text("[]\n", encoding="utf-8")
        report_path.write_text("# Computational Feedback Plan\n\nNo design candidates were available.\n", encoding="utf-8")
        stats = {
            "status": "no_candidates",
            "candidates_path": str(source_path),
            "tasks": 0,
            "output_csv": str(csv_path),
            "output_json": str(json_path),
            "report_path": str(report_path),
            "safety_note": "planning only; no local or cloud calculation was launched",
        }
        record_pipeline_run("34_plan_computational_feedback", "skipped", stats, db_path=db_path)
        return stats

    sort_col = "active_learning_score" if "active_learning_score" in candidates else "predicted_value"
    selected = candidates.sort_values(sort_col, ascending=False).head(max_candidates).copy()
    tasks: list[dict[str, Any]] = []
    next_index = 1
    for _, row in selected.iterrows():
        candidate_tasks = _build_tasks_for_candidate(row, next_index)
        for task in candidate_tasks:
            tasks.append(task)
            next_index += 1
            if len(tasks) >= max_tasks:
                break
        if len(tasks) >= max_tasks:
            break

    task_df = pd.DataFrame(tasks, columns=TASK_COLUMNS)
    task_df = task_df.sort_values("priority_score", ascending=False).reset_index(drop=True)
    task_df["task_id"] = [f"calc_{index + 1:05d}" for index in range(len(task_df))]
    task_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(task_df.to_dict(orient="records"), ensure_ascii=False, indent=2), encoding="utf-8")

    counts = task_df["task_family"].value_counts().to_dict() if not task_df.empty else {}
    lines = [
        "# Computational Feedback Plan",
        "",
        "This file is a planning artifact. It does not launch local VASP, molecular dynamics, phase-field, or cloud jobs.",
        "",
        "## Scope",
        "",
        "- Source: evidence-constrained HfO2/HZO design candidates.",
        "- Goal: convert literature-driven candidates into physics validation tasks.",
        "- Write-back: computed descriptors should return to the KG and benchmark as additional features, not replace experimental evidence.",
        "",
        "## Task Counts",
        "",
    ]
    if counts:
        lines.extend([f"- {family}: {count}" for family, count in counts.items()])
    else:
        lines.append("- No tasks generated.")
    lines.extend(
        [
            "",
            "## Safety Boundary",
            "",
            "All tasks are marked `planned_only_no_local_execution`. Manual review is required before cloud submission.",
            "",
            "## Top Tasks",
            "",
        ]
    )
    for _, row in task_df.head(12).iterrows():
        lines.append(
            f"- {row['task_id']} | {row['task_family']} | priority={row['priority_score']} | "
            f"{row['material_name']} | {row['objective']}"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stats = {
        "status": "ok",
        "candidates_path": str(source_path),
        "selected_candidates": int(len(selected)),
        "tasks": int(len(task_df)),
        "task_counts": {str(k): int(v) for k, v in counts.items()},
        "output_csv": str(csv_path),
        "output_json": str(json_path),
        "report_path": str(report_path),
        "safety_note": "planning only; no local or cloud calculation was launched",
    }
    record_pipeline_run("34_plan_computational_feedback", "ok", stats, db_path=db_path)
    return stats
