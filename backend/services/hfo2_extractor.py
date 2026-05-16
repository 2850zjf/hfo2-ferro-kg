from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from backend.core.config import PROJECT_ROOT
from backend.core.config import get_settings
from backend.db.session import connect
from backend.schemas.hfo2_extraction_schema import (
    DeviceExtraction,
    EvidenceExtraction,
    HfO2ExtractionResult,
    MaterialExtraction,
    MaterialFamily,
    PhaseExtraction,
    PhaseName,
    PropertyExtraction,
    PropertyName,
    SampleExtraction,
)
from backend.services.fact_normalizer import normalize_property
from backend.services.llm_extractor import extract_chunk_with_llm
from backend.services.pipeline_log import record_pipeline_run


EXTRACTOR_VERSION = "rules-preaudit-0.1"
LLM_EXTRACTOR_VERSION = "llm-structured-preaudit-0.1"
ONTOLOGY_VERSION = "hfo2-ferrokg-v1"

PROPERTY_PATTERNS = [
    (
        PropertyName.double_remanent_polarization_2Pr,
        r"\b2\s*P\s*r\b[^.;,\n]{0,80}?([-+]?\d+(?:\.\d+)?)\s*(?:μ|µ|u)?C\s*/?\s*cm(?:\^?2|²|−2|-2)",
        "2Pr",
        "μC/cm²",
    ),
    (
        PropertyName.remanent_polarization_Pr,
        r"\bP\s*r\b[^.;,\n]{0,80}?([-+]?\d+(?:\.\d+)?)\s*(?:μ|µ|u)?C\s*/?\s*cm(?:\^?2|²|−2|-2)",
        "Pr",
        "μC/cm²",
    ),
    (
        PropertyName.coercive_field_Ec,
        r"\bE\s*c\b[^.;,\n]{0,80}?([-+]?\d+(?:\.\d+)?)\s*(M|k)?V\s*/?\s*cm",
        "Ec",
        "MV/cm",
    ),
    (
        PropertyName.memory_window,
        r"memory window[^.;,\n]{0,80}?([-+]?\d+(?:\.\d+)?)\s*V\b",
        "memory window",
        "V",
    ),
    (
        PropertyName.endurance_cycles,
        r"endurance[^.;,\n]{0,80}?10\^?(\d+)\s*cycles?",
        "endurance",
        "cycles",
    ),
]

MATERIAL_PATTERNS = [
    (r"Hf\s*0?\.?5\s*Zr\s*0?\.?5\s*O\s*2|Hf0\.5Zr0\.5O2|HZO", "Hf0.5Zr0.5O2", MaterialFamily.HZO, ["Zr"], 0.5),
    (r"Hf\s*1\s*[-−]\s*x\s*Zr\s*x\s*O\s*2|Hf1-xZrxO2|HfZrO", "Hf1-xZrxO2", MaterialFamily.HZO, ["Zr"], None),
    (r"La[-\s]?doped\s+HfO2|La:HfO2", "La:HfO2", MaterialFamily.La_HfO2, ["La"], None),
    (r"Si[-\s]?doped\s+HfO2|Si:HfO2", "Si:HfO2", MaterialFamily.Si_HfO2, ["Si"], None),
    (r"Al[-\s]?doped\s+HfO2|Al:HfO2", "Al:HfO2", MaterialFamily.Al_HfO2, ["Al"], None),
    (r"Y[-\s]?doped\s+HfO2|Y:HfO2", "Y:HfO2", MaterialFamily.Y_HfO2, ["Y"], None),
    (r"Gd[-\s]?doped\s+HfO2|Gd:HfO2", "Gd:HfO2", MaterialFamily.Gd_HfO2, ["Gd"], None),
    (r"Sr[-\s]?doped\s+HfO2|Sr:HfO2", "Sr:HfO2", MaterialFamily.Sr_HfO2, ["Sr"], None),
    (r"\bHfO2\b|hafnia|hafnium oxide", "HfO2", MaterialFamily.HfO2, [], None),
]

PHASE_PATTERNS = [
    (r"orthorhombic|Pca\s*2\s*1|Pca21", PhaseName.orthorhombic, "Pca21"),
    (r"monoclinic|P2\s*1/c|P21/c", PhaseName.monoclinic, "P21/c"),
    (r"tetragonal|P42/nmc", PhaseName.tetragonal, "P42/nmc"),
    (r"rhombohedral", PhaseName.rhombohedral, None),
    (r"cubic", PhaseName.cubic, None),
    (r"amorphous", PhaseName.amorphous, None),
]


def evidence_sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start))
    right_candidates = [pos for pos in [text.find(".", end), text.find("\n", end)] if pos != -1]
    right = min(right_candidates) if right_candidates else min(len(text), end + 220)
    sentence = text[left + 1 : right + 1].strip()
    sentence = re.sub(r"\s+", " ", sentence)
    if len(sentence) < 40:
        left = max(0, start - 160)
        right = min(len(text), end + 220)
        sentence = re.sub(r"\s+", " ", text[left:right]).strip()
    return sentence[:700]


def extract_materials(text: str) -> list[MaterialExtraction]:
    materials: dict[str, MaterialExtraction] = {}
    for pattern, canonical, family, dopants, zr_fraction in MATERIAL_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            ev = evidence_sentence(text, match.start(), match.end())
            materials[canonical] = MaterialExtraction(
                raw_name=match.group(0),
                canonical_name=canonical,
                formula=canonical,
                material_family=family,
                dopant_elements=dopants,
                zr_fraction=zr_fraction,
                evidence_text=ev,
            )
    return list(materials.values())


def extract_samples(text: str, material_ref: str) -> list[SampleExtraction]:
    thickness = None
    thickness_match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*nm[^.;,\n]{0,50}(film|HZO|HfO2|hafnia)", text, re.I)
    if thickness_match:
        thickness = float(thickness_match.group(1))

    deposition = None
    for candidate in ["ALD", "sputtering", "sputter", "PLD", "CSD", "sol-gel", "MOCVD"]:
        if re.search(rf"\b{re.escape(candidate)}\b", text, re.I):
            deposition = "sputtering" if candidate == "sputter" else candidate
            break

    anneal_temp = None
    temp_match = re.search(r"anneal[^.;\n]{0,80}?([-+]?\d+(?:\.\d+)?)\s*(?:°\s*)?C", text, re.I)
    if temp_match:
        anneal_temp = float(temp_match.group(1))

    electrodes = re.findall(r"\b(TiN|W|Pt|RuO2|Ru|ITO|TaN)\b", text, re.I)
    normalized_electrodes = [e.upper() if e.lower() != "ruo2" else "RuO2" for e in electrodes]
    device_stack = None
    stack_match = re.search(r"((?:TiN|W|Pt|RuO2|Ru|ITO|TaN|Si|SiO2|HZO|HfO2)[/\-\w\. ]{5,80}(?:TiN|W|Pt|RuO2|Ru|ITO|TaN|Si))", text, re.I)
    if stack_match:
        device_stack = re.sub(r"\s+", "", stack_match.group(1))

    if not any([thickness, deposition, anneal_temp, normalized_electrodes, device_stack]):
        return []

    return [
        SampleExtraction(
            material_ref=material_ref,
            film_thickness_nm=thickness,
            deposition_method=deposition,
            top_electrode=normalized_electrodes[0] if normalized_electrodes else None,
            bottom_electrode=normalized_electrodes[-1] if len(normalized_electrodes) > 1 else None,
            substrate="Si" if re.search(r"\bSi\b|SiO2/Si", text) else None,
            annealing_temperature_c=anneal_temp,
            device_stack=device_stack,
            sample_form="thin_film",
            evidence_text=text[:700],
        )
    ]


def extract_phases(text: str, material_ref: str) -> list[PhaseExtraction]:
    phases: dict[str, PhaseExtraction] = {}
    for pattern, phase, space_group in PHASE_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            ev = evidence_sentence(text, match.start(), match.end())
            phases[phase.value] = PhaseExtraction(
                material_ref=material_ref,
                phase_name=phase,
                space_group=space_group,
                characterization_method="XRD" if re.search(r"\bXRD\b", text, re.I) else None,
                evidence_text=ev,
            )
    return list(phases.values())


def extract_properties(text: str, material_ref: str) -> tuple[list[PropertyExtraction], list[str]]:
    properties: list[PropertyExtraction] = []
    warnings: list[str] = []
    for prop_name, pattern, raw_name, default_unit in PROPERTY_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            if prop_name == PropertyName.endurance_cycles:
                value = 10 ** int(match.group(1))
            else:
                value = float(match.group(1))
            unit = default_unit
            if raw_name == "Ec" and len(match.groups()) >= 2 and match.group(2):
                unit = f"{match.group(2)}V/cm"
            ev = evidence_sentence(text, match.start(), match.end())
            prop = PropertyExtraction(
                material_ref=material_ref,
                property_name=prop_name,
                raw_property_name=raw_name,
                value=value,
                unit=unit,
                confidence=0.72,
                evidence_text=ev,
                review_status="pending",
            )
            normalized, prop_warnings = normalize_property(prop)
            warnings.extend(prop_warnings)
            properties.append(normalized)
    return properties, warnings


def extract_devices(text: str) -> list[DeviceExtraction]:
    devices: list[DeviceExtraction] = []
    for device in ["FeFET", "FTJ", "FeRAM", "capacitor", "memristor", "ReFET"]:
        match = re.search(rf"\b{device}\b", text, re.I)
        if match:
            devices.append(
                DeviceExtraction(
                    device_type=device,
                    evidence_text=evidence_sentence(text, match.start(), match.end()),
                )
            )
    return devices


def extract_chunk(row) -> HfO2ExtractionResult:
    text = row["text"]
    materials = extract_materials(text)
    material_ref = materials[0].canonical_name if materials else "HfO2"
    samples = extract_samples(text, material_ref)
    phases = extract_phases(text, material_ref)
    properties, warnings = extract_properties(text, material_ref)
    devices = extract_devices(text)
    evidences = [
        EvidenceExtraction(
            paper_id=row["paper_id"],
            pdf_id=row["pdf_id"],
            chunk_id=row["chunk_id"],
            page_number=row["page_number"],
            evidence_text=prop.evidence_text,
            source_type="text",
        )
        for prop in properties
    ]
    return HfO2ExtractionResult(
        paper_id=row["paper_id"],
        pdf_id=row["pdf_id"],
        chunk_id=row["chunk_id"],
        page_number=row["page_number"],
        materials=materials,
        samples=samples,
        phases=phases,
        properties=properties,
        devices=devices,
        evidences=evidences,
        warnings=warnings,
    )


def preaudit_status(result: HfO2ExtractionResult, source: str) -> tuple[str, float, list[str]]:
    warnings = list(result.warnings)
    if source == "rules":
        warnings.append("Rule-based machine preaudit; human review is required before publication use.")
    if not result.properties:
        return "needs_human_review", 0.35, warnings + ["No property value extracted."]
    if not result.materials:
        return "needs_human_review", 0.45, warnings + ["No explicit material entity extracted."]
    if any(len(prop.evidence_text.strip()) < 40 for prop in result.properties):
        return "needs_human_review", 0.5, warnings + ["Evidence sentence is too short for machine preapproval."]
    if any(prop.review_status == "needs_human_review" for prop in result.properties):
        return "needs_human_review", 0.55, warnings
    if all(prop.evidence_text and prop.unit for prop in result.properties):
        base_confidence = 0.8 if source == "llm" else 0.68
        return "preapproved_machine", base_confidence, warnings
    return "needs_human_review", 0.5, warnings + ["Missing unit or evidence."]


def run_extraction(
    limit_chunks: int | None = None,
    db_path: Path | None = None,
    use_llm: bool | None = None,
    llm_model: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    output_path = PROJECT_ROOT / "data" / "extraction_candidates" / "hfo2_candidates.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    should_use_llm = settings.use_llm if use_llm is None else use_llm
    stats = {
        "chunks": 0,
        "candidates": 0,
        "llm_used": 0,
        "llm_skipped": 0,
        "llm_failed": 0,
        "rules_used": 0,
        "preapproved": 0,
        "needs_human_review": 0,
        "empty": 0,
    }

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT chunk_id, paper_id, pdf_id, page_number, text
            FROM document_chunks
            WHERE is_high_value = 1
            ORDER BY pdf_id, chunk_index
            """
        ).fetchall()
        if limit_chunks is not None:
            rows = rows[:limit_chunks]

        if not dry_run:
            conn.execute("DELETE FROM extraction_candidates")
            conn.execute("DELETE FROM reviewed_facts")
        with output_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                stats["chunks"] += 1
                source = "rules"
                extractor_version = EXTRACTOR_VERSION
                llm_error = None
                if should_use_llm:
                    outcome = extract_chunk_with_llm(row, model=llm_model)
                    if outcome.result is not None:
                        result = outcome.result
                        source = "llm"
                        extractor_version = LLM_EXTRACTOR_VERSION
                        stats["llm_used"] += 1
                    else:
                        llm_error = outcome.error_message
                        if outcome.used_llm:
                            stats["llm_failed"] += 1
                        else:
                            stats["llm_skipped"] += 1
                        result = extract_chunk(row)
                        stats["rules_used"] += 1
                else:
                    result = extract_chunk(row)
                    stats["rules_used"] += 1
                if not any([result.materials, result.samples, result.phases, result.properties, result.devices]):
                    stats["empty"] += 1
                    continue
                status, confidence, warnings = preaudit_status(result, source)
                if llm_error:
                    warnings.append(f"LLM fallback used: {llm_error[:240]}")
                payload = result.model_dump(mode="json")
                payload["preaudit"] = {
                    "status": status,
                    "confidence": confidence,
                    "warnings": warnings,
                    "extractor_version": extractor_version,
                    "extraction_source": source,
                    "ontology_version": ONTOLOGY_VERSION,
                }
                candidate_id = f"cand_{uuid.uuid5(uuid.NAMESPACE_URL, row['chunk_id'] + json.dumps(payload, sort_keys=True, ensure_ascii=False)).hex[:16]}"
                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO extraction_candidates (
                            candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
                            extractor_version, ontology_version, confidence, status
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            candidate_id,
                            result.paper_id,
                            result.pdf_id,
                            result.chunk_id,
                            result.page_number,
                            json.dumps(payload, ensure_ascii=False),
                            extractor_version,
                            ONTOLOGY_VERSION,
                            confidence,
                            status,
                        ),
                    )
                if result.properties:
                    for index, prop in enumerate(result.properties):
                        fact_payload = {
                            "material": result.materials[0].model_dump(mode="json") if result.materials else None,
                            "sample": result.samples[0].model_dump(mode="json") if result.samples else None,
                            "property": prop.model_dump(mode="json"),
                            "phases": [phase.model_dump(mode="json") for phase in result.phases],
                            "devices": [device.model_dump(mode="json") for device in result.devices],
                            "preaudit": payload["preaudit"],
                        }
                        fact_id = f"fact_{uuid.uuid5(uuid.NAMESPACE_URL, candidate_id + str(index)).hex[:16]}"
                        if not dry_run:
                            conn.execute(
                                """
                                INSERT INTO reviewed_facts (
                                    fact_id, candidate_id, paper_id, pdf_id, chunk_id, page_number,
                                    fact_type, payload_json, review_status, reviewer_notes
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    fact_id,
                                    candidate_id,
                                    result.paper_id,
                                    result.pdf_id,
                                    result.chunk_id,
                                    result.page_number,
                                    "ferroelectric_property",
                                    json.dumps(fact_payload, ensure_ascii=False),
                                    status,
                                    "Machine pre-audit only. Requires later human review before publication claims.",
                                ),
                            )
                fh.write(json.dumps({"candidate_id": candidate_id, **payload}, ensure_ascii=False) + "\n")
                stats["candidates"] += 1
                if status == "preapproved_machine":
                    stats["preapproved"] += 1
                else:
                    stats["needs_human_review"] += 1
            if not dry_run:
                conn.commit()

    if not dry_run:
        record_pipeline_run("05_run_extraction", "ok", stats, db_path=db_path)
    return stats
