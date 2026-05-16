from __future__ import annotations

import re
from typing import Any


REQUIRED_CONTEXT_FIELDS = {
    "sample_identity": "sample/device identity",
    "geometry": "film thickness",
    "process": "deposition or annealing process",
    "electrode_or_substrate": "electrode, substrate, or device stack",
    "phase": "phase or space group",
    "device": "device type",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    return True


def _compact_list(values: list[Any]) -> list[str]:
    compacted: list[str] = []
    for value in values:
        if _has_value(value):
            text = str(value).strip()
            if text not in compacted:
                compacted.append(text)
    return compacted


def _credible_stack(value: Any) -> str | None:
    if not _has_value(value):
        return None
    text = str(value).strip()
    if len(text) > 120:
        return None
    if "/" in text:
        return text
    known_tokens = re.findall(
        r"\b(TiN|HZO|HfO2|HfZrO|Pt|W|RuO2|Ru|ITO|TaN|SiO2|Si)\b",
        text,
        flags=re.IGNORECASE,
    )
    if len(known_tokens) >= 2 and re.search(r"[-/]", text):
        return text
    return None


def _joined_evidence(*parts: Any) -> str:
    texts: list[str] = []
    for part in parts:
        if isinstance(part, str) and part.strip():
            texts.append(part.strip())
        elif isinstance(part, dict):
            text = part.get("evidence_text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        elif isinstance(part, list):
            for item in part:
                if isinstance(item, dict):
                    text = item.get("evidence_text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
    return " ".join(texts).lower()


def _measurement_state(prop: dict[str, Any], sample: dict[str, Any], devices: list[dict[str, Any]]) -> dict[str, str]:
    text = _joined_evidence(prop, sample, devices)
    property_name = str(prop.get("property_name") or "")
    if "wake-up" in text or "wake up" in text or "wakeup" in text:
        wake_up_state = "wake-up mentioned"
    elif "pristine" in text or "fresh" in text or "initial" in text:
        wake_up_state = "pristine/initial mentioned"
    else:
        wake_up_state = "not specified"

    if property_name == "endurance_cycles":
        endurance_state = "endurance metric"
    elif any(token in text for token in ["endurance", "fatigue", "cycle", "cycling", "cycled"]):
        endurance_state = "cycling/endurance mentioned"
    else:
        endurance_state = "not specified"
    return {
        "wake_up_state": wake_up_state,
        "endurance_state": endurance_state,
    }


def build_ontology_context(
    material: dict[str, Any] | None,
    sample: dict[str, Any] | None,
    prop: dict[str, Any] | None,
    phases: list[dict[str, Any]] | None,
    devices: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    material = material or {}
    sample = sample or {}
    prop = prop or {}
    phases = phases or []
    devices = devices or []

    phase_names = _compact_list([phase.get("phase_name") for phase in phases])
    space_groups = _compact_list([phase.get("space_group") for phase in phases])
    device_stack = _credible_stack(sample.get("device_stack"))
    device_types = _compact_list(
        [prop.get("device_type"), sample.get("sample_form")]
        + [device.get("device_type") for device in devices]
    )
    electrodes = _compact_list([sample.get("top_electrode"), sample.get("bottom_electrode")])
    electrode_stack = device_stack or "/".join(electrodes)
    process_parts = _compact_list(
        [
            sample.get("deposition_method"),
            f"{sample.get('annealing_temperature_c')} C"
            if _has_value(sample.get("annealing_temperature_c"))
            else None,
            f"{sample.get('annealing_time_s')} s"
            if _has_value(sample.get("annealing_time_s"))
            else None,
            sample.get("annealing_atmosphere"),
        ]
    )

    checks = {
        "sample_identity": any(
            _has_value(sample.get(key))
            for key in ["device_stack", "sample_form", "material_ref"]
        ),
        "geometry": _has_value(sample.get("film_thickness_nm")),
        "process": any(
            _has_value(sample.get(key))
            for key in [
                "deposition_method",
                "annealing_temperature_c",
                "annealing_time_s",
                "annealing_atmosphere",
            ]
        ),
        "electrode_or_substrate": any(
            _has_value(sample.get(key))
            for key in ["top_electrode", "bottom_electrode", "substrate"]
        )
        or _has_value(device_stack),
        "phase": bool(phase_names or space_groups),
        "device": bool(device_types or _has_value(device_stack)),
    }
    present = [key for key, ok in checks.items() if ok]
    missing = [key for key, ok in checks.items() if not ok]
    context_score = round(len(present) / len(REQUIRED_CONTEXT_FIELDS), 2)
    if context_score >= 0.75:
        quality = "strong"
    elif context_score >= 0.45:
        quality = "partial"
    else:
        quality = "weak"

    material_label = (
        material.get("canonical_name")
        or material.get("raw_name")
        or prop.get("material_ref")
        or "unknown material"
    )
    property_label = prop.get("property_name") or "unknown property"
    value = prop.get("normalized_value", prop.get("value"))
    unit = prop.get("normalized_unit") or prop.get("unit") or ""
    property_value = f"{value} {unit}".strip() if _has_value(value) else ""
    measurement_state = _measurement_state(prop, sample, devices)

    return {
        "schema_version": "ontology-context-v1",
        "ontology_path": [
            "Paper",
            "HafniaMaterial",
            "ThinFilmSample",
            "FabricationProcess",
            "PhaseStructure",
            "Device",
            "FerroelectricProperty",
            "Evidence",
        ],
        "material_label": material_label,
        "material_system": material.get("material_family") or "unknown_hafnia",
        "sample_label": device_stack
        or sample.get("sample_form")
        or f"{material_label} sample",
        "property_label": property_label,
        "property_value": property_value,
        "sample_context": {
            "film_thickness_nm": sample.get("film_thickness_nm"),
            "sample_form": sample.get("sample_form"),
            "device_stack": device_stack,
        },
        "process_context": {
            "deposition_method": sample.get("deposition_method"),
            "annealing_temperature_c": sample.get("annealing_temperature_c"),
            "annealing_time_s": sample.get("annealing_time_s"),
            "annealing_atmosphere": sample.get("annealing_atmosphere"),
            "summary": " + ".join(process_parts),
        },
        "device_context": {
            "device_types": device_types,
            "top_electrode": sample.get("top_electrode"),
            "bottom_electrode": sample.get("bottom_electrode"),
            "electrodes": electrodes,
            "electrode_stack": electrode_stack,
            "substrate": sample.get("substrate"),
            "device_stack": device_stack,
        },
        "structure_context": {
            "phase_names": phase_names,
            "space_groups": space_groups,
        },
        "measurement_state_context": measurement_state,
        "requested_review_fields": {
            "材料体系": material.get("material_family") or "unknown_hafnia",
            "Pr 或 2Pr": property_label if "polarization" in property_label else "",
            "薄膜厚度": sample.get("film_thickness_nm"),
            "退火温度": sample.get("annealing_temperature_c"),
            "退火时间": sample.get("annealing_time_s"),
            "退火气氛": sample.get("annealing_atmosphere"),
            "电极 stack": electrode_stack,
            "沉积方法": sample.get("deposition_method"),
            "器件类型": ", ".join(device_types),
            "相结构": ", ".join(phase_names + space_groups),
            "wake-up 状态": measurement_state["wake_up_state"],
            "endurance 状态": measurement_state["endurance_state"],
        },
        "present_context_fields": present,
        "missing_context_fields": missing,
        "missing_context_labels": [REQUIRED_CONTEXT_FIELDS[key] for key in missing],
        "context_score": context_score,
        "context_quality": quality,
        "comparison_ready": context_score >= 0.75,
    }
