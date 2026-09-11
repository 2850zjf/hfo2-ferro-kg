from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.core.config import PROJECT_ROOT


CONSTRAINTS_PATH = PROJECT_ROOT / "ontology" / "physical_constraints.yaml"


@dataclass(frozen=True)
class PhysicalAssessment:
    physical_consistency_score: float
    recommendation_allowed: bool
    hard_violations: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    descriptors: dict[str, Any] = field(default_factory=dict)

    def row_updates(self) -> dict[str, Any]:
        return {
            "physical_consistency_score": round(self.physical_consistency_score, 3),
            "physical_recommendation_allowed": int(self.recommendation_allowed),
            "physical_hard_violations": ",".join(self.hard_violations),
            "physical_soft_warnings": ",".join(self.soft_warnings),
            "physical_risk_flags": ",".join(self.risk_flags),
            "physical_descriptor_json": json.dumps(self.descriptors, ensure_ascii=False, sort_keys=True),
            "phase_stability_score": self.descriptors.get("phase_stability_score", ""),
            "oxygen_vacancy_risk": self.descriptors.get("oxygen_vacancy_risk", ""),
            "interface_oxygen_affinity": self.descriptors.get("interface_oxygen_affinity", ""),
            "process_window_score": self.descriptors.get("process_window_score", ""),
            "evidence_completeness_score": self.descriptors.get("evidence_completeness_score", ""),
        }


@lru_cache(maxsize=4)
def load_physical_constraints(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else CONSTRAINTS_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {target}")
    return data


def assess_design_row(row: dict[str, Any], constraints: dict[str, Any] | None = None) -> PhysicalAssessment:
    rules = constraints or load_physical_constraints()
    hard: list[str] = []
    warnings: list[str] = []
    risks: list[str] = []

    property_score = _property_score(row, rules, hard, warnings)
    process_score = _process_score(row, rules, hard, warnings)
    phase_score = _phase_score(row, rules)
    oxygen_risk, interface_affinity = _oxygen_vacancy_risk(row, rules)
    oxygen_balance_score = _oxygen_balance_score(oxygen_risk, rules, warnings, risks)
    evidence_score = _evidence_completeness(row)
    scope_score = _scope_relevance(row, warnings, risks)

    _phase_property_consistency(row, rules, phase_score, warnings, risks)

    weights = rules.get("scoring_weights") or {}
    total_weight = 0.0
    weighted_score = 0.0
    for key, value in [
        ("property_range", property_score),
        ("process_window", process_score),
        ("phase_prior", phase_score),
        ("oxygen_vacancy_balance", oxygen_balance_score),
        ("evidence_completeness", evidence_score),
        ("scope_relevance", scope_score),
    ]:
        weight = float(weights.get(key, 0))
        total_weight += weight
        weighted_score += weight * value
    score = weighted_score / total_weight if total_weight else min(
        property_score, process_score, phase_score, oxygen_balance_score
    )
    if hard:
        score = min(score, 0.2)

    descriptors = {
        "property_range_score": round(property_score, 3),
        "process_window_score": round(process_score, 3),
        "phase_stability_score": round(phase_score, 3),
        "oxygen_vacancy_risk": round(oxygen_risk, 3),
        "interface_oxygen_affinity": round(interface_affinity, 3),
        "oxygen_balance_score": round(oxygen_balance_score, 3),
        "evidence_completeness_score": round(evidence_score, 3),
        "scope_relevance_score": round(scope_score, 3),
        "electrode_oxygen_affinity_class": _electrode_affinity_class(row, rules),
    }
    allowed = not hard and score >= 0.35
    return PhysicalAssessment(
        physical_consistency_score=max(0.0, min(1.0, score)),
        recommendation_allowed=allowed,
        hard_violations=hard,
        soft_warnings=warnings,
        risk_flags=risks,
        descriptors=descriptors,
    )


def assessment_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return assess_design_row(row).row_updates()


def compact_prompt_constraints(max_lines: int = 36) -> str:
    rules = load_physical_constraints()
    prop_lines = []
    for name, rule in (rules.get("property_ranges") or {}).items():
        prop_lines.append(
            f"- {name}: canonical {rule.get('canonical_unit')}, hard range "
            f"{rule.get('hard_min')}..{rule.get('hard_max')}; expected "
            f"{rule.get('expected_min')}..{rule.get('expected_max')}"
        )
    mechanism = rules.get("mechanism_rules") or {}
    lines = [
        "# Physics constraints for HfO2/HZO extraction",
        "Extract interface, oxygen-vacancy, phase/orientation, process, and Pr/2Pr evidence when present.",
        "Do not merge Pr and 2Pr. Only derive Pr=2Pr/2 if the text explicitly allows derivation.",
        "Oxygen vacancies are non-monotonic: moderate vacancy/redox conditions can stabilize polar phases, excessive vacancies raise leakage/fatigue/imprint risk.",
        "High Pr/2Pr claims with only monoclinic/amorphous phase evidence need warnings and strong evidence.",
        "Electrode stack, interface layer/termination, annealing atmosphere, and oxygen reservoir are mechanism variables, not optional decoration.",
        "Property hard ranges:",
        *prop_lines,
        "Required interface-story fields when present: "
        + ", ".join((mechanism.get("interface") or {}).get("required_for_interface_story", [])),
    ]
    return "\n".join(lines[:max_lines])


def _property_score(
    row: dict[str, Any], rules: dict[str, Any], hard: list[str], warnings: list[str]
) -> float:
    target = str(row.get("target_property") or row.get("property_name") or "").strip()
    if not target:
        return 0.55
    prop_rule = (rules.get("property_ranges") or {}).get(target)
    if not prop_rule:
        return 0.65
    value = _safe_float(
        row.get("model_target_value", row.get("target_value", row.get("value")))
    )
    if value is None:
        return 0.50
    hard_min = _safe_float(prop_rule.get("hard_min"))
    hard_max = _safe_float(prop_rule.get("hard_max"))
    expected_min = _safe_float(prop_rule.get("expected_min"))
    expected_max = _safe_float(prop_rule.get("expected_max"))
    if hard_min is not None and value <= hard_min:
        hard.append(f"{target}_below_or_equal_hard_min")
        return 0.0
    if hard_max is not None and value > hard_max:
        hard.append(f"{target}_above_hard_max")
        return 0.0
    if expected_min is not None and value < expected_min:
        warnings.append(f"{target}_below_expected_range")
        return 0.65
    if expected_max is not None and value > expected_max:
        warnings.append(f"{target}_above_expected_range")
        return 0.70
    return 1.0


def _process_score(
    row: dict[str, Any], rules: dict[str, Any], hard: list[str], warnings: list[str]
) -> float:
    windows = rules.get("process_windows") or {}
    scores: list[float] = []
    mapping = {
        "film_thickness_nm": "film_thickness_nm",
        "annealing_temperature_c": "annealing_temperature_c",
        "annealing_time_s": "annealing_time_s",
        "zr_fraction": "zr_fraction",
    }
    for field_name, row_key in mapping.items():
        value = _safe_float(row.get(row_key))
        if value is None:
            continue
        rule = windows.get(field_name) or {}
        score = _range_score(value, rule)
        scores.append(score)
        if score == 0.0:
            hard.append(f"{field_name}_outside_hard_window")
        elif score < 1.0:
            warnings.append(f"{field_name}_outside_expected_window")
    return sum(scores) / len(scores) if scores else 0.62


def _range_score(value: float, rule: dict[str, Any]) -> float:
    hard_min = _safe_float(rule.get("hard_min"))
    hard_max = _safe_float(rule.get("hard_max"))
    expected_min = _safe_float(rule.get("expected_min"))
    expected_max = _safe_float(rule.get("expected_max"))
    if hard_min is not None and value < hard_min:
        return 0.0
    if hard_max is not None and value > hard_max:
        return 0.0
    if expected_min is not None and value < expected_min:
        return 0.6
    if expected_max is not None and value > expected_max:
        return 0.65
    return 1.0


def _phase_score(row: dict[str, Any], rules: dict[str, Any]) -> float:
    raw = " ".join(
        str(row.get(key) or "")
        for key in ["phase_name", "space_group", "crystal_orientation", "domain_orientation"]
    )
    phase = _canonical_phase(raw)
    priors = rules.get("phase_priors") or {}
    return float((priors.get(phase) or priors.get("unknown") or {}).get("ferroelectric_phase_score", 0.45))


def _oxygen_vacancy_risk(row: dict[str, Any], rules: dict[str, Any]) -> tuple[float, float]:
    atmosphere = _clean_key(row.get("annealing_atmosphere"))
    oxygen_context = _clean_key(
        row.get("oxygen_reservoir") or row.get("oxygen_vacancy_context") or row.get("interface_layer")
    )
    atmosphere_map = rules.get("annealing_atmosphere_oxygen_potential") or {}
    atmosphere_score = _match_named_score(atmosphere, atmosphere_map, default=0.55)
    if "oxygen" in oxygen_context or "oxide" in oxygen_context or "wo3" in oxygen_context:
        atmosphere_score = min(atmosphere_score, 0.42)
    if any(token in oxygen_context for token in ["vacancy", "deficient", "scaveng", "reduc"]):
        atmosphere_score = max(atmosphere_score, 0.72)

    interface_affinity = _electrode_affinity_score(row, rules)
    risk = 0.55 * atmosphere_score + 0.45 * interface_affinity
    return max(0.0, min(1.0, risk)), interface_affinity


def _oxygen_balance_score(
    risk: float, rules: dict[str, Any], warnings: list[str], risks: list[str]
) -> float:
    rule = ((rules.get("mechanism_rules") or {}).get("oxygen_vacancy") or {})
    optimal = rule.get("optimal_risk_window") or [0.35, 0.70]
    low, high = float(optimal[0]), float(optimal[1])
    if low <= risk <= high:
        return 1.0
    if risk > float(rule.get("high_risk_threshold", 0.78)):
        warnings.append("oxygen_vacancy_or_leakage_risk_high")
        risks.append("oxygen_vacancy_overdose_risk")
        return 0.55
    if risk < float(rule.get("low_risk_threshold", 0.20)):
        warnings.append("oxygen_vacancy_stabilization_may_be_insufficient")
        risks.append("oxygen_vacancy_under_supply_risk")
        return 0.65
    return 0.78


def _evidence_completeness(row: dict[str, Any]) -> float:
    groups = [
        ["material_name", "formula", "material_family"],
        ["film_thickness_nm"],
        ["deposition_method"],
        ["annealing_temperature_c", "annealing_atmosphere"],
        ["electrode_stack", "top_electrode", "bottom_electrode"],
        ["phase_name", "space_group"],
        ["evidence_text", "page_number"],
    ]
    present = 0
    for group in groups:
        if any(str(row.get(key) or "").strip() for key in group):
            present += 1
    return present / len(groups)


def _scope_relevance(row: dict[str, Any], warnings: list[str], risks: list[str]) -> float:
    text = " ".join(
        str(row.get(key) or "")
        for key in ["material_name", "material_family", "formula", "title", "evidence_text"]
    ).lower()
    if any(token in text for token in ["hfo2", "hafnia", "hafnium oxide", "hzo", "hfzro", "hf0.", "hf1"]):
        return 1.0
    if "zro2" in text or "zirconia" in text:
        warnings.append("zirconia_without_explicit_hafnia_context")
        return 0.65
    warnings.append("outside_hafnia_scope_or_missing_material")
    risks.append("scope_relevance_risk")
    return 0.35


def _phase_property_consistency(
    row: dict[str, Any],
    rules: dict[str, Any],
    phase_score: float,
    warnings: list[str],
    risks: list[str],
) -> None:
    target = str(row.get("target_property") or "")
    value = _safe_float(row.get("model_target_value", row.get("target_value")))
    thresholds = (((rules.get("mechanism_rules") or {}).get("phase_property_consistency") or {}).get("high_polarization_thresholds") or {})
    threshold = _safe_float(thresholds.get(target))
    suspicious_below = _safe_float(
        (((rules.get("mechanism_rules") or {}).get("phase_property_consistency") or {}).get("suspicious_low_phase_scores_below"))
    )
    if value is None or threshold is None or suspicious_below is None:
        return
    if value >= threshold and phase_score <= suspicious_below:
        warnings.append("high_polarization_with_low_ferroelectric_phase_prior")
        risks.append("phase_property_consistency_risk")


def _electrode_affinity_score(row: dict[str, Any], rules: dict[str, Any]) -> float:
    mapping = rules.get("electrode_oxygen_affinity") or {}
    tokens = _electrode_tokens(row)
    if not tokens:
        return 0.50
    values = []
    for token in tokens:
        entry = _match_electrode(token, mapping)
        if entry:
            values.append(float(entry.get("score", 0.50)))
    return sum(values) / len(values) if values else 0.50


def _electrode_affinity_class(row: dict[str, Any], rules: dict[str, Any]) -> str:
    mapping = rules.get("electrode_oxygen_affinity") or {}
    classes = []
    for token in _electrode_tokens(row):
        entry = _match_electrode(token, mapping)
        if entry and entry.get("class"):
            classes.append(str(entry["class"]))
    return "+".join(dict.fromkeys(classes))


def _match_electrode(token: str, mapping: dict[str, Any]) -> dict[str, Any] | None:
    token_clean = _clean_key(token)
    for key, entry in mapping.items():
        key_clean = _clean_key(key)
        if token_clean == key_clean or key_clean in token_clean or token_clean in key_clean:
            return entry if isinstance(entry, dict) else None
    return None


def _electrode_tokens(row: dict[str, Any]) -> list[str]:
    values = [row.get("top_electrode"), row.get("bottom_electrode"), row.get("electrode_stack")]
    tokens: list[str] = []
    for value in values:
        text = str(value or "")
        for token in re.split(r"[/,;|\s()]+", text):
            token = token.strip()
            if token and token.lower() not in {"hzo", "hfo2", "zro2", "si", "sio2"}:
                tokens.append(token)
    return list(dict.fromkeys(tokens))


def _match_named_score(text: str, mapping: dict[str, Any], default: float) -> float:
    if not text:
        return default
    for key, value in mapping.items():
        if _clean_key(key) in text or text in _clean_key(key):
            return float(value)
    return default


def _canonical_phase(value: str) -> str:
    text = value.lower()
    if "pca" in text or "orth" in text:
        return "orthorhombic"
    if "rhombo" in text or " r3" in text:
        return "rhombohedral"
    if "mixed" in text or "/" in text:
        return "mixed"
    if "tetra" in text or "p42" in text:
        return "tetragonal"
    if "cubic" in text:
        return "cubic"
    if "mono" in text or "p21/c" in text:
        return "monoclinic"
    if "amorph" in text:
        return "amorphous"
    return "unknown"


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _clean_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
