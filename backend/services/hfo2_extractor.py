from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
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
from backend.services.llm_quota_guard import clear_llm_pause, is_llm_budget_error, write_llm_pause
from backend.services.ontology_context import build_ontology_context
from backend.services.ontology_builder import build_ontology
from backend.services.pipeline_log import record_pipeline_run


EXTRACTOR_VERSION = "rules-preaudit-0.3"
LLM_EXTRACTOR_VERSION = "llm-evidence-packet-0.3"
LLM_DEFERRED_EXTRACTOR_VERSION = "llm-deferred-0.3"
ONTOLOGY_VERSION = "hfo2-ferrokg-v2.3"
UNRELATED_PDF_PATH_PATTERN = "%/data/unrelated_pdfs/%"
LLM_RETRY_STATE_PATH = PROJECT_ROOT / "data" / "runtime" / "llm_retry_state.json"
LLM_CURRENT_CHUNK_PATH = PROJECT_ROOT / "data" / "runtime" / "llm_current_chunk.json"

PROPERTY_PATTERNS = [
    (
        PropertyName.double_remanent_polarization_2Pr,
        r"\b2\s*\.?\s*P\s*\.?\s*r\b[^.;,\n]{0,80}?([-+]?\d+(?:\.\d+)?)\s*(?:μ|µ|u)?C\s*/?\s*cm(?:\^?2|²|−2|-2)",
        "2Pr",
        "μC/cm²",
    ),
    (
        PropertyName.remanent_polarization_Pr,
        r"\bP\s*\.?\s*r\b[^.;,\n]{0,80}?([-+]?\d+(?:\.\d+)?)\s*(?:μ|µ|u)?C\s*/?\s*cm(?:\^?2|²|−2|-2)",
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
    thickness_match = re.search(r"\b(\d+(?:\.\d+)?)\s*nm[^.;,\n]{0,50}(film|HZO|HfO2|hafnia)", text, re.I)
    if thickness_match:
        thickness = float(thickness_match.group(1))
        if thickness <= 0:
            thickness = None

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
            if prop_name == PropertyName.remanent_polarization_Pr:
                prefix = text[max(0, match.start() - 6) : match.start()]
                if re.search(r"2\s*\.?\s*$", prefix) or re.search(r"\b2\s*\.?\s*P\s*\.?\s*r\b", match.group(0), re.I):
                    continue
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
    knowledge_items = [
        *result.properties,
        *result.process_steps,
        *result.reliability_events,
        *result.mechanisms,
        *result.computations,
        *result.applications,
        *result.relations,
    ]
    if not knowledge_items:
        return "needs_human_review", 0.35, warnings + ["No reviewable knowledge item extracted."]
    if not result.materials:
        return "needs_human_review", 0.45, warnings + ["No explicit material entity extracted."]
    if any(len(item.evidence_text.strip()) < 40 for item in knowledge_items):
        return "needs_human_review", 0.5, warnings + ["Evidence sentence is too short for machine preapproval."]
    if any(prop.review_status == "needs_human_review" for prop in result.properties):
        return "needs_human_review", 0.55, warnings
    if result.properties and all(prop.evidence_text and (prop.unit or prop.value is None) for prop in result.properties):
        base_confidence = 0.8 if source == "llm" else 0.68
        return "preapproved_machine", base_confidence, warnings
    if source == "llm" and all(item.evidence_text for item in knowledge_items):
        return "preapproved_machine", 0.72, warnings
    return "needs_human_review", 0.5, warnings + ["Missing unit, context, or evidence."]


def build_reviewed_fact_records(
    result: HfO2ExtractionResult,
    candidate_id: str,
    preaudit: dict,
    review_status: str,
) -> list[dict]:
    material = result.materials[0].model_dump(mode="json") if result.materials else None
    sample = result.samples[0].model_dump(mode="json") if result.samples else None
    phases = [phase.model_dump(mode="json") for phase in result.phases]
    devices = [device.model_dump(mode="json") for device in result.devices]
    records: list[dict] = []

    for index, prop in enumerate(result.properties):
        fact_payload = {
            "material": material,
            "sample": sample,
            "property": prop.model_dump(mode="json"),
            "phases": phases,
            "devices": devices,
            "preaudit": preaudit,
        }
        fact_payload["ontology_context"] = build_ontology_context(
            material,
            sample,
            fact_payload["property"],
            phases,
            devices,
        )
        records.append(_fact_record(result, candidate_id, "ferroelectric_property", index, fact_payload, review_status))

    typed_items = [
        ("process_step", "process_step", result.process_steps),
        ("reliability_event", "reliability_event", result.reliability_events),
        ("mechanism_claim", "mechanism", result.mechanisms),
        ("computational_observation", "computation", result.computations),
        ("application_claim", "application", result.applications),
        ("directional_relation", "relation", result.relations),
    ]
    for fact_type, payload_key, items in typed_items:
        for index, item in enumerate(items):
            fact_payload = {
                "material": material,
                "sample": sample,
                "phases": phases,
                "devices": devices,
                payload_key: item.model_dump(mode="json"),
                "preaudit": preaudit,
            }
            records.append(_fact_record(result, candidate_id, fact_type, index, fact_payload, review_status))
    return records


def _fact_record(
    result: HfO2ExtractionResult,
    candidate_id: str,
    fact_type: str,
    index: int,
    payload: dict,
    review_status: str,
) -> dict:
    fact_id = f"fact_{uuid.uuid5(uuid.NAMESPACE_URL, candidate_id + fact_type + str(index)).hex[:16]}"
    return {
        "fact_id": fact_id,
        "candidate_id": candidate_id,
        "paper_id": result.paper_id,
        "pdf_id": result.pdf_id,
        "chunk_id": result.chunk_id,
        "page_number": result.page_number,
        "fact_type": fact_type,
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "review_status": review_status,
    }


def empty_result_payload(
    row,
    source: str,
    extractor_version: str,
    ontology_version: str,
    llm_error: str | None = None,
) -> dict:
    warnings = [
        "Chunk was checked, but no ontology-aligned HfO2 fact was extracted.",
        "This marker prevents repeated LLM calls for the same empty chunk.",
    ]
    if llm_error:
        warnings.append(f"LLM fallback used before empty result: {llm_error[:240]}")
    return {
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "chunk_id": row["chunk_id"],
        "page_number": row["page_number"],
        "materials": [],
        "samples": [],
        "phases": [],
        "properties": [],
        "devices": [],
        "process_steps": [],
        "reliability_events": [],
        "mechanisms": [],
        "computations": [],
        "applications": [],
        "relations": [],
        "evidences": [],
        "warnings": warnings,
        "preaudit": {
            "status": "empty_result",
            "confidence": 0.0,
            "warnings": warnings,
            "extractor_version": extractor_version,
            "extraction_source": source,
            "ontology_version": ontology_version,
        },
    }


def _max_llm_chunk_retries() -> int:
    raw = os.getenv("HFO2_FERROKG_LLM_MAX_CHUNK_RETRIES", "3")
    try:
        value = int(raw)
    except ValueError:
        return 3
    return max(0, value)


def _read_llm_retry_state() -> dict[str, dict[str, object]]:
    if not LLM_RETRY_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(LLM_RETRY_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def _write_llm_retry_state(state: dict[str, dict[str, object]]) -> None:
    LLM_RETRY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LLM_RETRY_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _record_llm_retry_failure(row, reason: str | None) -> int:
    chunk_id = row["chunk_id"]
    state = _read_llm_retry_state()
    entry = state.get(chunk_id, {})
    failures = int(entry.get("failures", 0) or 0) + 1
    entry.update(
        {
            "chunk_id": chunk_id,
            "paper_id": row["paper_id"],
            "pdf_id": row["pdf_id"],
            "page_number": row["page_number"],
            "failures": failures,
            "last_reason": str(reason or "")[:1000],
            "last_failed_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    state[chunk_id] = entry
    _write_llm_retry_state(state)
    return failures


def _clear_llm_retry_failure(chunk_id: str) -> None:
    state = _read_llm_retry_state()
    if chunk_id not in state:
        return
    state.pop(chunk_id, None)
    _write_llm_retry_state(state)


def write_llm_current_chunk(row, ontology_version: str, model: str | None) -> Path:
    LLM_CURRENT_CHUNK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "chunk_id": row["chunk_id"],
        "page_number": row["page_number"],
        "ontology_version": ontology_version,
        "model": model,
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    LLM_CURRENT_CHUNK_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return LLM_CURRENT_CHUNK_PATH


def clear_llm_current_chunk(chunk_id: str | None = None) -> None:
    try:
        if chunk_id and LLM_CURRENT_CHUNK_PATH.exists():
            payload = json.loads(LLM_CURRENT_CHUNK_PATH.read_text(encoding="utf-8"))
            if payload.get("chunk_id") != chunk_id:
                return
        LLM_CURRENT_CHUNK_PATH.unlink()
    except FileNotFoundError:
        return
    except json.JSONDecodeError:
        try:
            LLM_CURRENT_CHUNK_PATH.unlink()
        except FileNotFoundError:
            return


def llm_deferred_payload(
    row,
    ontology_version: str,
    reason: str | None,
    failure_count: int,
    max_retries: int,
) -> dict:
    warnings = [
        "LLM extraction was deferred after repeated transient failures.",
        "No rule-based fallback was written for this chunk.",
        "Retry this deferred chunk later after model capacity or network conditions recover.",
    ]
    return {
        "paper_id": row["paper_id"],
        "pdf_id": row["pdf_id"],
        "chunk_id": row["chunk_id"],
        "page_number": row["page_number"],
        "materials": [],
        "samples": [],
        "phases": [],
        "properties": [],
        "devices": [],
        "process_steps": [],
        "reliability_events": [],
        "mechanisms": [],
        "computations": [],
        "applications": [],
        "relations": [],
        "evidences": [],
        "warnings": warnings,
        "llm_retry": {
            "status": "deferred",
            "failure_count": failure_count,
            "max_retries": max_retries,
            "reason": str(reason or "")[:1000],
            "deferred_at": datetime.now().isoformat(timespec="seconds"),
        },
        "preaudit": {
            "status": "llm_deferred",
            "confidence": 0.0,
            "warnings": warnings,
            "extractor_version": LLM_DEFERRED_EXTRACTOR_VERSION,
            "extraction_source": "llm_deferred",
            "ontology_version": ontology_version,
        },
    }


def insert_llm_deferred_candidate(
    conn,
    row,
    ontology_version: str,
    reason: str | None,
    failure_count: int,
    max_retries: int,
) -> tuple[str, dict]:
    payload = llm_deferred_payload(row, ontology_version, reason, failure_count, max_retries)
    candidate_id = f"cand_deferred_{uuid.uuid5(uuid.NAMESPACE_URL, row['chunk_id'] + ontology_version).hex[:16]}"
    conn.execute(
        """
        INSERT INTO extraction_candidates (
            candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
            extractor_version, ontology_version, confidence, status, error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            payload_json = excluded.payload_json,
            extractor_version = excluded.extractor_version,
            ontology_version = excluded.ontology_version,
            confidence = excluded.confidence,
            status = excluded.status,
            error_message = excluded.error_message
        """,
        (
            candidate_id,
            row["paper_id"],
            row["pdf_id"],
            row["chunk_id"],
            row["page_number"],
            json.dumps(payload, ensure_ascii=False),
            LLM_DEFERRED_EXTRACTOR_VERSION,
            ontology_version,
            0.0,
            "llm_deferred",
            str(reason or "")[:2000],
        ),
    )
    return candidate_id, payload


def run_extraction(
    limit_chunks: int | None = None,
    db_path: Path | None = None,
    use_llm: bool | None = None,
    llm_model: str | None = None,
    dry_run: bool = False,
    reset_existing: bool = True,
    commit_every: int = 25,
    progress_every: int | None = None,
    paper_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
    high_value_only: bool = True,
    llm_strict: bool = False,
    output_path: Path | None = None,
    ontology_output_dir: Path | None = None,
) -> dict[str, int]:
    output_path = output_path or (
        PROJECT_ROOT / "data" / "extraction_candidates" / "hfo2_candidates.jsonl"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    should_use_llm = settings.use_llm if use_llm is None else use_llm
    ontology_build = build_ontology(
        db_path=db_path,
        record_run=not dry_run,
        output_dir=ontology_output_dir,
    )
    ontology_version = ontology_build["version"]
    stats = {
        "ontology_version": ontology_version,
        "chunks": 0,
        "candidates": 0,
        "llm_used": 0,
        "llm_skipped": 0,
        "llm_failed": 0,
        "llm_deferred": 0,
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "llm_total_tokens": 0,
        "llm_specialist_calls": 0,
        "rules_used": 0,
        "preapproved": 0,
        "needs_human_review": 0,
        "empty": 0,
        "empty_recorded": 0,
        "errors": 0,
        "skipped_existing": 0,
        "paused": 0,
        "high_value_only": int(high_value_only),
        "llm_strict": int(llm_strict),
    }
    if should_use_llm and not dry_run:
        clear_llm_pause()

    with connect(db_path) as conn:
        query = """
        SELECT dc.chunk_id, dc.paper_id, dc.pdf_id, dc.page_number, dc.section,
               dc.chunk_index, dc.text, p.title, p.doi, p.year, p.paper_type,
               p.is_review,
               (SELECT prev.text FROM document_chunks prev
                WHERE prev.pdf_id = dc.pdf_id AND prev.chunk_index = dc.chunk_index - 1
                LIMIT 1) AS context_before,
               (SELECT nxt.text FROM document_chunks nxt
                WHERE nxt.pdf_id = dc.pdf_id AND nxt.chunk_index = dc.chunk_index + 1
                LIMIT 1) AS context_after
        FROM document_chunks dc
        LEFT JOIN pdf_files pf ON pf.pdf_id = dc.pdf_id
        LEFT JOIN papers p ON p.paper_id = dc.paper_id
        WHERE (? = 0 OR dc.is_high_value = 1)
          AND (pf.file_path IS NULL OR pf.file_path NOT LIKE ?)
          AND (? = 1 OR NOT EXISTS (
              SELECT 1
              FROM extraction_candidates ec
              WHERE ec.chunk_id = dc.chunk_id
                AND ec.ontology_version = ?
          ))
        """
        params: list[object] = [int(high_value_only), UNRELATED_PDF_PATH_PATTERN, int(reset_existing), ontology_version]
        if paper_ids:
            placeholders = ",".join("?" for _ in paper_ids)
            query += f" AND dc.paper_id IN ({placeholders})"
            params.extend(paper_ids)
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            query += f" AND dc.chunk_id IN ({placeholders})"
            params.extend(chunk_ids)
        query += " ORDER BY dc.pdf_id, dc.chunk_index"
        rows = conn.execute(query, params).fetchall()
        if limit_chunks is not None:
            rows = rows[:limit_chunks]

        if not dry_run:
            if reset_existing:
                selected_chunk_ids = [row["chunk_id"] for row in rows]
                if paper_ids or chunk_ids or limit_chunks is not None:
                    if selected_chunk_ids:
                        placeholders = ",".join("?" for _ in selected_chunk_ids)
                        conn.execute(
                            f"DELETE FROM extraction_candidates WHERE chunk_id IN ({placeholders})",
                            selected_chunk_ids,
                        )
                        conn.execute(
                            f"DELETE FROM reviewed_facts WHERE chunk_id IN ({placeholders})",
                            selected_chunk_ids,
                        )
                else:
                    conn.execute("DELETE FROM extraction_candidates")
                    conn.execute("DELETE FROM reviewed_facts")
            else:
                stats["skipped_existing"] = conn.execute(
                    "SELECT COUNT(DISTINCT chunk_id) FROM extraction_candidates WHERE ontology_version = ?",
                    (ontology_version,),
                ).fetchone()[0]
        file_mode = "w" if reset_existing else "a"
        with output_path.open(file_mode, encoding="utf-8") as fh:
            for row in rows:
                stats["chunks"] += 1
                source = "rules"
                extractor_version = EXTRACTOR_VERSION
                llm_error = None
                llm_usage = None
                try:
                    if should_use_llm:
                        write_llm_current_chunk(row, ontology_version, llm_model)
                        try:
                            outcome = extract_chunk_with_llm(row, model=llm_model)
                        finally:
                            clear_llm_current_chunk(row["chunk_id"])
                        llm_usage = outcome.usage or None
                        if llm_usage:
                            stats["llm_prompt_tokens"] += int(llm_usage.get("prompt_tokens") or 0)
                            stats["llm_completion_tokens"] += int(llm_usage.get("completion_tokens") or 0)
                            stats["llm_total_tokens"] += int(llm_usage.get("total_tokens") or 0)
                            stats["llm_specialist_calls"] += int(llm_usage.get("specialist_calls") or 0)
                        if outcome.result is not None:
                            _clear_llm_retry_failure(row["chunk_id"])
                            result = outcome.result
                            source = "llm"
                            extractor_version = LLM_EXTRACTOR_VERSION
                            stats["llm_used"] += 1
                        else:
                            llm_error = outcome.error_message
                            if llm_strict and should_use_llm:
                                if (not outcome.used_llm) or is_llm_budget_error(llm_error):
                                    stats["llm_failed"] += int(outcome.used_llm)
                                    stats["llm_skipped"] += int(not outcome.used_llm)
                                    failure_count = (
                                        _record_llm_retry_failure(row, llm_error)
                                        if outcome.used_llm
                                        else 0
                                    )
                                    max_retries = _max_llm_chunk_retries()
                                    if outcome.used_llm and max_retries and failure_count >= max_retries:
                                        if not dry_run:
                                            candidate_id, payload = insert_llm_deferred_candidate(
                                                conn,
                                                row,
                                                ontology_version,
                                                llm_error,
                                                failure_count,
                                                max_retries,
                                            )
                                            fh.write(
                                                json.dumps(
                                                    {"candidate_id": candidate_id, **payload},
                                                    ensure_ascii=False,
                                                )
                                                + "\n"
                                            )
                                            fh.flush()
                                            if commit_every > 0:
                                                conn.commit()
                                        stats["llm_deferred"] += 1
                                        clear_llm_current_chunk(row["chunk_id"])
                                        continue
                                    stats["paused"] = 1
                                    pause_path = write_llm_pause(
                                        llm_error or "Strict LLM extraction failed before returning a structured result.",
                                        {
                                            "pipeline": "05_run_extraction",
                                            "strict_llm": True,
                                            "paper_id": row["paper_id"],
                                            "pdf_id": row["pdf_id"],
                                            "chunk_id": row["chunk_id"],
                                            "page_number": row["page_number"],
                                            "processed_chunks_in_this_run": stats["chunks"],
                                        },
                                    )
                                    stats["pause_file"] = str(pause_path)
                                    if not dry_run:
                                        conn.commit()
                                    break
                                raise ValueError(f"Strict LLM extraction produced an unusable response: {llm_error}")
                            if outcome.used_llm and is_llm_budget_error(llm_error):
                                stats["llm_failed"] += 1
                                stats["paused"] = 1
                                pause_path = write_llm_pause(
                                    llm_error or "LLM quota/authentication/rate-limit error",
                                    {
                                        "pipeline": "05_run_extraction",
                                        "paper_id": row["paper_id"],
                                        "pdf_id": row["pdf_id"],
                                        "chunk_id": row["chunk_id"],
                                        "page_number": row["page_number"],
                                        "processed_chunks_in_this_run": stats["chunks"],
                                    },
                                )
                                stats["pause_file"] = str(pause_path)
                                if not dry_run:
                                    conn.commit()
                                break
                            if outcome.used_llm:
                                stats["llm_failed"] += 1
                            else:
                                stats["llm_skipped"] += 1
                            result = extract_chunk(row)
                            stats["rules_used"] += 1
                    else:
                        result = extract_chunk(row)
                        stats["rules_used"] += 1
                except Exception as exc:
                    stats["errors"] += 1
                    error_payload = {
                        "paper_id": row["paper_id"],
                        "pdf_id": row["pdf_id"],
                        "chunk_id": row["chunk_id"],
                        "page_number": row["page_number"],
                        "error": str(exc),
                        "extractor_version": extractor_version,
                        "ontology_version": ontology_version,
                    }
                    candidate_id = f"cand_error_{uuid.uuid5(uuid.NAMESPACE_URL, row['chunk_id'] + str(exc)).hex[:16]}"
                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO extraction_candidates (
                                candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
                                extractor_version, ontology_version, confidence, status, error_message
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                candidate_id,
                                row["paper_id"],
                                row["pdf_id"],
                                row["chunk_id"],
                                row["page_number"],
                                json.dumps(error_payload, ensure_ascii=False),
                                extractor_version,
                                ontology_version,
                                0,
                                "extraction_error",
                                str(exc),
                            ),
                        )
                    fh.write(json.dumps({"candidate_id": candidate_id, **error_payload}, ensure_ascii=False) + "\n")
                    fh.flush()
                    if not dry_run and commit_every > 0 and stats["chunks"] % commit_every == 0:
                        conn.commit()
                    if progress_every and stats["chunks"] % progress_every == 0:
                        print(
                            "processed={chunks} candidates={candidates} llm_used={llm_used} "
                            "llm_failed={llm_failed} llm_deferred={llm_deferred} empty={empty} "
                            "empty_recorded={empty_recorded} errors={errors}".format(**stats),
                            flush=True,
                        )
                    continue
                if not any(
                    [
                        result.materials,
                        result.samples,
                        result.phases,
                        result.properties,
                        result.devices,
                        result.process_steps,
                        result.reliability_events,
                        result.mechanisms,
                        result.computations,
                        result.applications,
                        result.relations,
                    ]
                ):
                    stats["empty"] += 1
                    payload = empty_result_payload(
                        row,
                        source,
                        extractor_version,
                        ontology_version,
                        llm_error=llm_error,
                    )
                    if llm_usage:
                        payload["preaudit"]["llm_usage"] = llm_usage
                    candidate_id = f"cand_empty_{uuid.uuid5(uuid.NAMESPACE_URL, row['chunk_id'] + ontology_version).hex[:16]}"
                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO extraction_candidates (
                                candidate_id, paper_id, pdf_id, chunk_id, page_number, payload_json,
                                extractor_version, ontology_version, confidence, status
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(candidate_id) DO UPDATE SET
                                payload_json = excluded.payload_json,
                                extractor_version = excluded.extractor_version,
                                ontology_version = excluded.ontology_version,
                                confidence = excluded.confidence,
                                status = excluded.status
                            """,
                            (
                                candidate_id,
                                row["paper_id"],
                                row["pdf_id"],
                                row["chunk_id"],
                                row["page_number"],
                                json.dumps(payload, ensure_ascii=False),
                                extractor_version,
                                ontology_version,
                                0.0,
                                "empty_result",
                            ),
                        )
                        stats["empty_recorded"] += 1
                        if commit_every > 0 and stats["chunks"] % commit_every == 0:
                            conn.commit()
                    fh.write(json.dumps({"candidate_id": candidate_id, **payload}, ensure_ascii=False) + "\n")
                    fh.flush()
                    if progress_every and stats["chunks"] % progress_every == 0:
                        print(
                            "processed={chunks} candidates={candidates} llm_used={llm_used} "
                            "llm_failed={llm_failed} llm_deferred={llm_deferred} empty={empty} "
                            "empty_recorded={empty_recorded} errors={errors}".format(**stats),
                            flush=True,
                        )
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
                    "ontology_version": ontology_version,
                }
                if llm_usage:
                    payload["preaudit"]["llm_usage"] = llm_usage
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
                            ontology_version,
                            confidence,
                            status,
                        ),
                    )
                for fact in build_reviewed_fact_records(result, candidate_id, payload["preaudit"], status):
                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO reviewed_facts (
                                fact_id, candidate_id, paper_id, pdf_id, chunk_id, page_number,
                                fact_type, payload_json, review_status, reviewer_notes
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(fact_id) DO UPDATE SET
                                payload_json = excluded.payload_json,
                                review_status = excluded.review_status,
                                reviewer_notes = excluded.reviewer_notes,
                                updated_at = CURRENT_TIMESTAMP
                            """,
                            (
                                fact["fact_id"],
                                fact["candidate_id"],
                                fact["paper_id"],
                                fact["pdf_id"],
                                fact["chunk_id"],
                                fact["page_number"],
                                fact["fact_type"],
                                fact["payload_json"],
                                fact["review_status"],
                                "Machine pre-audit only. Requires later human review before publication claims.",
                            ),
                        )
                fh.write(json.dumps({"candidate_id": candidate_id, **payload}, ensure_ascii=False) + "\n")
                fh.flush()
                stats["candidates"] += 1
                if status == "preapproved_machine":
                    stats["preapproved"] += 1
                else:
                    stats["needs_human_review"] += 1
                if not dry_run and commit_every > 0 and stats["chunks"] % commit_every == 0:
                    conn.commit()
                if progress_every and stats["chunks"] % progress_every == 0:
                    print(
                        "processed={chunks} candidates={candidates} llm_used={llm_used} "
                        "llm_failed={llm_failed} llm_deferred={llm_deferred} empty={empty} "
                        "empty_recorded={empty_recorded} errors={errors}".format(**stats),
                        flush=True,
                    )
            if not dry_run:
                conn.commit()

    if not dry_run:
        record_pipeline_run("05_run_extraction", "paused" if stats.get("paused") else "ok", stats, db_path=db_path)
    return stats
