from __future__ import annotations

import re

from backend.schemas.hfo2_extraction_schema import PropertyExtraction, PropertyName


UNIT_ALIASES = {
    "uc/cm2": "μC/cm²",
    "μc/cm2": "μC/cm²",
    "μc/cm²": "μC/cm²",
    "uc cm-2": "μC/cm²",
    "μc cm−2": "μC/cm²",
    "mv/cm": "MV/cm",
    "mv cm-1": "MV/cm",
    "mv cm−1": "MV/cm",
    "kv/cm": "MV/cm",
    "a/cm2": "A/cm²",
    "a/cm²": "A/cm²",
}


def normalize_material_name(raw: str) -> tuple[str, str]:
    lower = raw.lower().replace(" ", "")
    if "hfo2" in lower or "hafnia" in raw.lower() or "hafniumoxide" in lower:
        if "zr" in lower or "hzo" in lower:
            return "HZO", "HZO"
        return "HfO2", "HfO2"
    return raw, "unknown_hafnia"


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    compact = unit.strip().lower().replace("µ", "μ").replace(" ", " ")
    return UNIT_ALIASES.get(compact, unit.strip())


def normalize_property_alias(raw_name: str) -> PropertyName | None:
    raw = raw_name.strip().lower()
    if raw == "2pr" or "2pr" in raw or "double remanent" in raw:
        return PropertyName.double_remanent_polarization_2Pr
    if raw == "pr" or "remanent polarization" in raw:
        return PropertyName.remanent_polarization_Pr
    if raw == "ec" or "coercive" in raw:
        return PropertyName.coercive_field_Ec
    if "memory window" in raw:
        return PropertyName.memory_window
    if "endurance" in raw:
        return PropertyName.endurance_cycles
    if "retention" in raw:
        return PropertyName.retention_time
    if "leakage" in raw:
        return PropertyName.leakage_current_density
    return None


def normalize_property(prop: PropertyExtraction) -> tuple[PropertyExtraction, list[str]]:
    warnings: list[str] = []
    data = prop.model_dump()
    data["normalized_unit"] = normalize_unit(prop.unit)
    data["normalized_value"] = prop.value

    if prop.unit and normalize_unit(prop.unit) == "MV/cm" and "kv" in prop.unit.lower() and prop.value is not None:
        data["normalized_value"] = prop.value / 1000
    if prop.property_name == PropertyName.double_remanent_polarization_2Pr:
        warnings.append("Reported as 2Pr; do not treat as Pr without explicit derived flag.")
    check_value = data["normalized_value"] if data["normalized_value"] is not None else prop.value
    if check_value is not None:
        if prop.property_name in {
            PropertyName.remanent_polarization_Pr,
            PropertyName.double_remanent_polarization_2Pr,
        } and check_value > 100:
            warnings.append("needs_check: unusually high polarization value")
            data["review_status"] = "needs_human_review"
        if prop.property_name == PropertyName.coercive_field_Ec and check_value > 10:
            warnings.append("needs_check: unusually high coercive field")
            data["review_status"] = "needs_human_review"

    return PropertyExtraction(**data), warnings


def parse_numeric(text: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None
