from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


ANALYZER_VERSION = "phase-contradiction-candidates-v0.2"
TRACEABLE_CONTEXT_QUALITIES = {"strong", "partial"}
TARGET_PHASES = {"m", "o", "t"}

_CONDITION_WEIGHTS = {
    "film_thickness_nm": 1.4,
    "deposition_method": 0.8,
    "substrate": 0.9,
    "top_electrode": 0.7,
    "bottom_electrode": 0.7,
    "device_stack": 1.0,
    "annealing_temperature_c": 1.2,
    "annealing_time_s": 0.7,
    "annealing_atmosphere": 0.8,
    "strain_state": 1.2,
    "sample_form": 0.5,
    "device_type": 0.5,
}
_TOTAL_CONDITION_WEIGHT = sum(_CONDITION_WEIGHTS.values())


@dataclass(frozen=True)
class PhaseObservation:
    observation_id: str
    paper_id: str
    paper_group: str
    doi: str
    title: str
    pdf_id: str
    chunk_id: str
    page_number: int | None
    source_kind: str
    source_id: str
    sample_id: str
    material_system: str
    material_name: str
    zr_fraction: float | None
    conditions: dict[str, Any]
    phase_raw: str
    phase_labels: tuple[str, ...]
    characterization_method: str
    property_name: str
    property_value: Any
    property_unit: str
    property_condition: str
    evidence_text: str
    context_quality: str
    context_score: float
    quality_flags: tuple[str, ...] = field(default_factory=tuple)
    paper_is_review: bool = False
    phase_evidence_text: str = ""
    phase_evidence_source: str = "none"
    condition_suggestions: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseContradictionCandidate:
    candidate_id: str
    classification: str
    priority_score: float
    nominal_similarity: float
    comparable_coverage: float
    phase_a: tuple[str, ...]
    phase_b: tuple[str, ...]
    differing_fields: tuple[str, ...]
    comparison_reasons: tuple[str, ...]
    observation_a: PhaseObservation
    observation_b: PhaseObservation


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi\s*:\s*", "", text)
    return text.rstrip("./ ")


def canonical_phase_labels(value: Any) -> tuple[str, ...]:
    text = str(value or "").strip().lower()
    if not text:
        return ()
    text = text.replace("pca2₁", "pca21").replace("pca2_1", "pca21")
    for phase_word in ("monoclinic", "orthorhombic", "tetragonal"):
        text = re.sub(
            rf"\b(?:non[- ]|no\s+|without\s+|absence\s+of\s+){phase_word}\b",
            " ",
            text,
        )
        text = re.sub(rf"\b{phase_word}[- ]free\b", " ", text)
    labels: set[str] = set()
    patterns = {
        "m": [r"\bmonoclinic\b", r"\bm[- ]?phase\b", r"\bp2\s*1/c\b"],
        "o": [
            r"\borthorhombic\b",
            r"\bo[- ]?phase\b",
            r"\bpca21\b",
            r"\bpbc21\b",
            r"\bpbca\b",
            r"\boiii\b",
        ],
        "t": [r"\btetragonal\b", r"\bt[- ]?phase\b", r"\bp4\s*2/nmc\b"],
    }
    for label, label_patterns in patterns.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in label_patterns):
            labels.add(label)
    return tuple(sorted(labels))


def canonical_material_system(material: dict[str, Any]) -> str:
    text = " ".join(
        str(material.get(key) or "")
        for key in ("material_family", "canonical_name", "formula", "raw_name")
    ).lower()
    zr_fraction = _safe_float(material.get("zr_fraction"))
    if "hzo" in text or "hafnium zirconium" in text or "hfzro" in text or zr_fraction is not None:
        return "HZO"
    if any(token in text for token in ("hfo2", "hfo₂", "hafnia", "hafnium oxide")):
        return "HfO2"
    return ""


def select_phase_evidence(
    phase_labels: Sequence[str],
    evidence_text: str,
    chunk_text: str,
) -> tuple[str, str]:
    """Select direct or same-chunk phase evidence without inventing a new fact."""

    target = set(phase_labels) & TARGET_PHASES
    if not target:
        return "", "none"
    if target.issubset(set(canonical_phase_labels(evidence_text))):
        return evidence_text.strip(), "direct"

    candidates: list[tuple[int, int, str]] = []
    for fragment in _evidence_fragments(chunk_text):
        labels = set(canonical_phase_labels(fragment)) & TARGET_PHASES
        if target.issubset(labels):
            candidates.append((0 if labels == target else 1, len(fragment), fragment))
    if not candidates:
        return "", "none"
    _, _, selected = min(candidates)
    return selected.strip(), "chunk_recovered"


def suggest_missing_conditions(
    sample: dict[str, Any],
    chunk_text: str,
) -> dict[str, dict[str, Any]]:
    """Return conservative annotation suggestions; never merge them into sample truth."""

    suggestions: dict[str, dict[str, Any]] = {}
    fragments = _evidence_fragments(chunk_text)

    if _missing(sample.get("film_thickness_nm")):
        patterns = [
            re.compile(
                r"(?i)(?:film\s+thickness|thickness\s+of\s+(?:the\s+)?(?:hzo|hfo2|film)|"
                r"(?:hzo|hfo2)\s+film(?:s)?[^.;]{0,30}?thickness)\D{0,45}?(\d+(?:\.\d+)?)\s*nm"
            ),
            re.compile(r"(?i)(\d+(?:\.\d+)?)\s*nm[- ]thick\s+(?:hzo|hfo2|hafnia)\s+film"),
        ]
        match, fragment = _first_fragment_match(fragments, patterns)
        if match:
            _add_condition_suggestion(suggestions, "film_thickness_nm", float(match.group(1)), fragment)

    anneal_fragments = [
        fragment
        for fragment in fragments
        if re.search(r"(?i)\b(?:anneal(?:ed|ing)?|rta|pma|pda|rapid thermal)\b", fragment)
    ]
    if _missing(sample.get("annealing_temperature_c")):
        match, fragment = _first_fragment_match(
            anneal_fragments,
            [re.compile(r"(?i)(\d{3,4}(?:\.\d+)?)\s*(?:°|o)?\s*c\b")],
        )
        if match:
            _add_condition_suggestion(
                suggestions,
                "annealing_temperature_c",
                float(match.group(1)),
                fragment,
            )

    if _missing(sample.get("annealing_time_s")):
        match, fragment = _first_fragment_match(
            anneal_fragments,
            [
                re.compile(
                    r"(?i)\b(?:for|duration\s+of)\s+(\d+(?:\.\d+)?)\s*"
                    r"(s|sec(?:ond)?s?|min(?:ute)?s?|h(?:our)?s?)\b"
                )
            ],
        )
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith("min"):
                value *= 60.0
            elif unit.startswith("h"):
                value *= 3600.0
            _add_condition_suggestion(suggestions, "annealing_time_s", value, fragment)

    if _missing(sample.get("annealing_atmosphere")):
        match, fragment = _first_fragment_match(
            anneal_fragments,
            [re.compile(r"(?i)\b(forming\s+gas|vacuum|air|n2|o2|ar)\b")],
        )
        if match:
            value = match.group(1)
            normalized = {"n2": "N2", "o2": "O2", "ar": "Ar"}.get(value.lower(), value)
            _add_condition_suggestion(suggestions, "annealing_atmosphere", normalized, fragment)

    if _missing(sample.get("deposition_method")):
        deposition_fragments = [
            fragment
            for fragment in fragments
            if re.search(r"(?i)\b(?:hzo|hfo2|hafnia|hafnium\s+(?:zirconium\s+)?oxide)\b", fragment)
        ]
        deposition_patterns = [
            ("ALD", re.compile(r"(?i)\b(?:atomic layer deposition|ald)\b")),
            ("PLD", re.compile(r"(?i)\b(?:pulsed laser deposition|pld)\b")),
            ("sputtering", re.compile(r"(?i)\b(?:magnetron\s+)?sputter(?:ed|ing)?\b")),
            ("CSD", re.compile(r"(?i)\b(?:chemical solution deposition|csd)\b")),
        ]
        for value, pattern in deposition_patterns:
            match, fragment = _first_fragment_match(deposition_fragments, [pattern])
            if match:
                _add_condition_suggestion(suggestions, "deposition_method", value, fragment)
                break

    if _missing(sample.get("strain_state")):
        match, fragment = _first_fragment_match(
            fragments,
            [re.compile(r"(?i)\b((?:in[- ]plane\s+)?(?:compressive|tensile)\s+(?:strain|stress))\b")],
        )
        if match:
            _add_condition_suggestion(
                suggestions,
                "strain_state",
                _normalized_text(match.group(1)),
                fragment,
            )
    return suggestions


@contextmanager
def open_readonly_database(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open SQLite without calling the project's schema-initializing connection helper."""

    target = Path(db_path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    uri = f"file:{target.as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def load_phase_observations(
    db_path: str | Path,
    *,
    accepted_context_qualities: set[str] | None = None,
    limit: int | None = None,
) -> list[PhaseObservation]:
    qualities = accepted_context_qualities or TRACEABLE_CONTEXT_QUALITIES
    with open_readonly_database(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        required = {"sample_property_links", "papers", "document_chunks"}
        missing = required - tables
        if missing:
            raise ValueError(f"Database is missing required tables: {sorted(missing)}")
        sql = """
            SELECT spl.link_id, spl.source_kind, spl.source_id, spl.paper_id,
                   spl.pdf_id, spl.chunk_id, spl.page_number, spl.sample_id,
                   spl.material_json, spl.sample_json, spl.phase_json,
                   spl.property_json, spl.evidence_text, spl.context_quality,
                   spl.context_score, p.doi, p.title, p.is_review,
                   dc.text AS chunk_text
            FROM sample_property_links AS spl
            LEFT JOIN papers AS p ON p.paper_id = spl.paper_id
            LEFT JOIN document_chunks AS dc ON dc.chunk_id = spl.chunk_id
            WHERE spl.status = 'linked'
            ORDER BY spl.paper_id, spl.link_id
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(0, int(limit)),)
        rows = conn.execute(sql, params).fetchall()

    observations: list[PhaseObservation] = []
    for row in rows:
        material = _json_dict(row["material_json"])
        sample = _json_dict(row["sample_json"])
        phase = _json_dict(row["phase_json"])
        prop = _json_dict(row["property_json"])
        material_system = canonical_material_system(material)
        phase_raw = str(phase.get("phase_name") or phase.get("phase") or "").strip()
        phase_labels = canonical_phase_labels(phase_raw + " " + str(phase.get("space_group") or ""))
        if not material_system or not phase_raw:
            continue
        context_quality = str(row["context_quality"] or "").strip().lower()
        if qualities and context_quality not in qualities:
            continue
        doi = normalize_doi(row["doi"])
        paper_id = str(row["paper_id"] or "")
        paper_group = f"doi:{doi}" if doi else f"paper:{paper_id}"
        evidence_text = str(row["evidence_text"] or prop.get("evidence_text") or "").strip()
        chunk_text = str(row["chunk_text"] or "").strip()
        phase_evidence_text, phase_evidence_source = select_phase_evidence(
            phase_labels,
            evidence_text,
            chunk_text,
        )
        conditions = {
            key: sample.get(key)
            for key in _CONDITION_WEIGHTS
        }
        conditions["wake_up_or_endurance_state"] = sample.get("wake_up_or_endurance_state")
        condition_suggestions = suggest_missing_conditions(sample, chunk_text)
        flags = _quality_flags(
            evidence_text=evidence_text,
            phase_evidence_text=phase_evidence_text,
            page_number=row["page_number"],
            chunk_id=row["chunk_id"],
            context_quality=context_quality,
            phase_labels=phase_labels,
            characterization_method=phase.get("characterization_method"),
            property_condition=prop.get("condition"),
            paper_is_review=bool(row["is_review"]),
        )
        observations.append(
            PhaseObservation(
                observation_id=str(row["link_id"]),
                paper_id=paper_id,
                paper_group=paper_group,
                doi=doi,
                title=str(row["title"] or ""),
                pdf_id=str(row["pdf_id"] or ""),
                chunk_id=str(row["chunk_id"] or ""),
                page_number=int(row["page_number"]) if row["page_number"] is not None else None,
                source_kind=str(row["source_kind"] or ""),
                source_id=str(row["source_id"] or ""),
                sample_id=str(row["sample_id"] or ""),
                material_system=material_system,
                material_name=str(
                    material.get("canonical_name")
                    or material.get("formula")
                    or material.get("material_family")
                    or ""
                ),
                zr_fraction=_safe_float(material.get("zr_fraction")),
                conditions=conditions,
                phase_raw=phase_raw,
                phase_labels=phase_labels,
                characterization_method=str(phase.get("characterization_method") or ""),
                property_name=str(prop.get("property_name") or ""),
                property_value=prop.get("value"),
                property_unit=str(prop.get("unit") or ""),
                property_condition=str(prop.get("condition") or ""),
                evidence_text=evidence_text,
                context_quality=context_quality,
                context_score=float(row["context_score"] or 0.0),
                quality_flags=flags,
                paper_is_review=bool(row["is_review"]),
                phase_evidence_text=phase_evidence_text,
                phase_evidence_source=phase_evidence_source,
                condition_suggestions=condition_suggestions,
            )
        )
    return observations


def compare_conditions(a: PhaseObservation, b: PhaseObservation) -> tuple[float, float, tuple[str, ...]]:
    compared_weight = 0.0
    matched_weight = 0.0
    differences: list[str] = []
    for field_name, weight in _CONDITION_WEIGHTS.items():
        value_a = a.conditions.get(field_name)
        value_b = b.conditions.get(field_name)
        if _missing(value_a) or _missing(value_b):
            continue
        compared_weight += weight
        if _condition_matches(field_name, value_a, value_b):
            matched_weight += weight
        else:
            differences.append(field_name)

    if a.material_system != b.material_system:
        return 0.0, compared_weight / _TOTAL_CONDITION_WEIGHT, tuple(differences + ["material_system"])
    if a.material_system == "HZO" and a.zr_fraction is not None and b.zr_fraction is not None:
        compared_weight += 1.5
        if abs(a.zr_fraction - b.zr_fraction) <= 0.05:
            matched_weight += 1.5
        else:
            differences.append("zr_fraction")

    similarity = matched_weight / compared_weight if compared_weight else 0.0
    coverage = min(1.0, compared_weight / (_TOTAL_CONDITION_WEIGHT + 1.5))
    return round(similarity, 4), round(coverage, 4), tuple(sorted(set(differences)))


def find_phase_contradiction_candidates(
    observations: Sequence[PhaseObservation],
    *,
    min_similarity: float = 0.80,
    min_comparable_coverage: float = 0.25,
    max_candidates: int | None = None,
) -> list[PhaseContradictionCandidate]:
    by_material: dict[str, list[PhaseObservation]] = {"HfO2": [], "HZO": []}
    for observation in observations:
        if observation.material_system in by_material:
            by_material[observation.material_system].append(observation)

    deduplicated: dict[str, PhaseContradictionCandidate] = {}
    for material_observations in by_material.values():
        for index, a in enumerate(material_observations):
            for b in material_observations[index + 1 :]:
                if a.paper_group == b.paper_group:
                    continue
                if not _phases_conflict(a.phase_labels, b.phase_labels):
                    continue
                similarity, coverage, differing_fields = compare_conditions(a, b)
                if similarity < min_similarity or coverage < min_comparable_coverage:
                    continue
                classification, reasons = _classify_pair(a, b, differing_fields, coverage)
                candidate_id = _candidate_id(a, b)
                score = _priority_score(a, b, similarity, coverage, classification)
                candidate = PhaseContradictionCandidate(
                    candidate_id=candidate_id,
                    classification=classification,
                    priority_score=score,
                    nominal_similarity=similarity,
                    comparable_coverage=coverage,
                    phase_a=a.phase_labels,
                    phase_b=b.phase_labels,
                    differing_fields=differing_fields,
                    comparison_reasons=reasons,
                    observation_a=a,
                    observation_b=b,
                )
                key = _deduplication_key(candidate)
                previous = deduplicated.get(key)
                if previous is None or candidate.priority_score > previous.priority_score:
                    deduplicated[key] = candidate

    ranked = sorted(
        deduplicated.values(),
        key=lambda item: (-item.priority_score, item.candidate_id),
    )
    return ranked[:max_candidates] if max_candidates is not None else ranked


def build_locked_split_manifest(
    observations: Sequence[PhaseObservation],
    locked_validation_groups: Iterable[str],
) -> list[dict[str, str]]:
    """Assign whole DOI/paper groups to train or a user-curated locked validation set."""

    normalized_locked = {_normalize_group_key(value) for value in locked_validation_groups if str(value).strip()}
    group_metadata: dict[str, tuple[str, str, str]] = {}
    for observation in observations:
        group_metadata.setdefault(
            observation.paper_group,
            (observation.doi, observation.paper_id, observation.title),
        )
    manifest = []
    for group in sorted(group_metadata):
        doi, paper_id, title = group_metadata[group]
        manifest.append(
            {
                "paper_group": group,
                "doi": doi,
                "paper_id": paper_id,
                "title": title,
                "partition": "locked_validation" if group in normalized_locked else "train",
            }
        )
    assert_no_group_leakage(manifest)
    return manifest


def assert_no_group_leakage(manifest: Sequence[dict[str, str]]) -> None:
    partitions_by_group: dict[str, set[str]] = {}
    for row in manifest:
        partitions_by_group.setdefault(str(row["paper_group"]), set()).add(str(row["partition"]))
    leaked = sorted(group for group, partitions in partitions_by_group.items() if len(partitions) > 1)
    if leaked:
        raise ValueError(f"Paper-group leakage detected: {leaked}")


def build_phase_annotation_queue(
    observations: Sequence[PhaseObservation],
    *,
    max_rows: int | None = 500,
) -> list[dict[str, Any]]:
    """Create a deduplicated human queue; no row is treated as adjudicated truth."""

    selected: dict[str, tuple[float, PhaseObservation]] = {}
    for observation in observations:
        if not (set(observation.phase_labels) & TARGET_PHASES):
            continue
        key = "|".join(
            [
                observation.paper_group,
                observation.pdf_id,
                observation.chunk_id,
                observation.material_system,
                "+".join(observation.phase_labels),
            ]
        )
        score = _annotation_priority(observation)
        previous = selected.get(key)
        if previous is None or score > previous[0]:
            selected[key] = (score, observation)

    rows = [_annotation_row(observation, score) for score, observation in selected.values()]
    rows.sort(key=lambda row: (-float(row["annotation_priority"]), str(row["annotation_id"])))
    return rows[:max_rows] if max_rows is not None else rows


def build_paper_group_inventory(
    observations: Sequence[PhaseObservation],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[PhaseObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.paper_group, []).append(observation)

    rows = []
    for paper_group, items in sorted(grouped.items()):
        representative = items[0]
        labels = Counter(label for item in items for label in item.phase_labels)
        evidence_sources = Counter(item.phase_evidence_source for item in items)
        nonblocking = sum(1 for item in items if not item.quality_flags)
        rows.append(
            {
                "paper_group": paper_group,
                "doi": representative.doi,
                "paper_id": representative.paper_id,
                "title": representative.title,
                "material_systems": ";".join(sorted({item.material_system for item in items})),
                "observations": len(items),
                "o_observations": labels.get("o", 0),
                "t_observations": labels.get("t", 0),
                "m_observations": labels.get("m", 0),
                "direct_phase_evidence": evidence_sources.get("direct", 0),
                "recovered_phase_evidence": evidence_sources.get("chunk_recovered", 0),
                "quality_gate_pass_observations": nonblocking,
                "is_review": int(any(item.paper_is_review for item in items)),
                "partition": "unassigned",
                "selection_note": "human_stratification_required",
            }
        )
    return rows


def export_candidate_analysis(
    candidates: Sequence[PhaseContradictionCandidate],
    output_dir: str | Path,
    *,
    observations: Sequence[PhaseObservation] = (),
    split_manifest: Sequence[dict[str, str]] = (),
    annotation_queue: Sequence[dict[str, Any]] = (),
    paper_group_inventory: Sequence[dict[str, Any]] = (),
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    jsonl_path = target / "phase_contradiction_candidates.jsonl"
    csv_path = target / "phase_contradiction_candidates.csv"
    summary_path = target / "phase_contradiction_summary.json"
    split_path = target / "paper_group_split_manifest.csv"
    annotation_path = target / "phase_observation_annotation_queue.csv"
    inventory_path = target / "paper_group_inventory.csv"

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(_candidate_dict(candidate), ensure_ascii=False, default=str) + "\n")

    csv_rows = [_candidate_csv_row(candidate) for candidate in candidates]
    fieldnames = list(csv_rows[0]) if csv_rows else _candidate_csv_fieldnames()
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    if split_manifest:
        with split_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["paper_group", "doi", "paper_id", "title", "partition"],
            )
            writer.writeheader()
            writer.writerows(split_manifest)

    _write_dict_csv(annotation_path, annotation_queue, _annotation_fieldnames())
    _write_dict_csv(inventory_path, paper_group_inventory, _inventory_fieldnames())

    class_counts = Counter(candidate.classification for candidate in candidates)
    reason_counts = Counter(
        reason
        for candidate in candidates
        for reason in candidate.comparison_reasons
    )
    observation_flag_counts = Counter(
        flag
        for observation in observations
        for flag in observation.quality_flags
    )
    phase_counts = Counter(
        phase
        for observation in observations
        for phase in observation.phase_labels
    )
    evidence_source_counts = Counter(
        observation.phase_evidence_source for observation in observations
    )
    suggestion_counts = Counter(
        field_name
        for observation in observations
        for field_name in observation.condition_suggestions
    )
    summary = {
        "analyzer_version": ANALYZER_VERSION,
        "observations": len(observations),
        "paper_groups": len({observation.paper_group for observation in observations}),
        "material_counts": dict(sorted(Counter(observation.material_system for observation in observations).items())),
        "phase_label_counts": dict(sorted(phase_counts.items())),
        "phase_evidence_source_counts": dict(sorted(evidence_source_counts.items())),
        "condition_suggestion_counts": dict(sorted(suggestion_counts.items())),
        "observation_quality_flag_counts": dict(sorted(observation_flag_counts.items())),
        "candidates": len(candidates),
        "classification_counts": dict(sorted(class_counts.items())),
        "candidate_reason_counts": dict(sorted(reason_counts.items())),
        "parameters": parameters or {},
        "candidate_jsonl": str(jsonl_path),
        "candidate_csv": str(csv_path),
        "split_manifest": str(split_path) if split_manifest else None,
        "annotation_queue_rows": len(annotation_queue),
        "annotation_queue": str(annotation_path),
        "paper_group_inventory_rows": len(paper_group_inventory),
        "paper_group_inventory": str(inventory_path),
        "interpretation_boundary": (
            "Candidates are review queues, not adjudicated scientific contradictions. "
            "No candidate is a VASP result or an experimental truth label."
        ),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def _quality_flags(
    *,
    evidence_text: str,
    phase_evidence_text: str,
    page_number: Any,
    chunk_id: Any,
    context_quality: str,
    phase_labels: tuple[str, ...],
    characterization_method: Any,
    property_condition: Any,
    paper_is_review: bool,
) -> tuple[str, ...]:
    flags = []
    if not evidence_text:
        flags.append("missing_evidence_text")
    if page_number is None and not str(chunk_id or "").strip():
        flags.append("missing_source_locator")
    if context_quality not in TRACEABLE_CONTEXT_QUALITIES:
        flags.append("weak_or_unknown_context")
    if not phase_labels:
        flags.append("unresolved_phase_label")
    evidence_phases = set(canonical_phase_labels(phase_evidence_text))
    if phase_labels and not set(phase_labels).issubset(evidence_phases):
        flags.append("phase_not_supported_by_evidence_text")
    if len(evidence_phases & TARGET_PHASES) > len(set(phase_labels) & TARGET_PHASES):
        flags.append("multi_phase_evidence_collapsed_to_single_label")
    method_context = _normalized_text(
        f"{characterization_method or ''} {property_condition or ''} {phase_evidence_text}"
    )
    if any(token in method_context for token in ("dft", "density functional", "calculated", "simulation", "model")):
        flags.append("computed_or_modeled_phase_not_sample_observation")
    if paper_is_review:
        flags.append("secondary_or_review_source")
    return tuple(flags)


def _classify_pair(
    a: PhaseObservation,
    b: PhaseObservation,
    differing_fields: tuple[str, ...],
    comparable_coverage: float,
) -> tuple[str, tuple[str, ...]]:
    flags = sorted(set(a.quality_flags) | set(b.quality_flags))
    if comparable_coverage < 0.50:
        flags.append("insufficient_comparable_condition_coverage")
    if flags:
        return "extraction_review_needed", tuple(sorted(set(flags)))

    reasons: list[str] = []
    if differing_fields:
        reasons.extend(f"sample_condition_diff:{field}" for field in differing_fields)
    method_a = _normalized_text(a.characterization_method)
    method_b = _normalized_text(b.characterization_method)
    if method_a and method_b and method_a != method_b:
        reasons.append("characterization_scope_difference")
    state_a = _measurement_state(a)
    state_b = _measurement_state(b)
    if state_a and state_b and state_a != state_b:
        reasons.append("measurement_or_cycling_state_difference")
    if reasons:
        return "condition_explained_difference", tuple(sorted(set(reasons)))
    return "scientific_contradiction_candidate", ("incompatible_phase_outcomes_under_recorded_similar_conditions",)


def _measurement_state(observation: PhaseObservation) -> str:
    text = _normalized_text(
        " ".join(
            [
                str(observation.conditions.get("wake_up_or_endurance_state") or ""),
                observation.property_condition,
            ]
        )
    )
    labels = []
    for label, tokens in {
        "pristine": ("pristine", "fresh", "initial", "before cycling"),
        "wakeup": ("wake up", "wake-up", "wakeup"),
        "cycled": ("cycle", "cycled", "fatigue", "endurance"),
    }.items():
        if any(token in text for token in tokens):
            labels.append(label)
    return "+".join(labels)


def _phases_conflict(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    phases_a = set(a) & TARGET_PHASES
    phases_b = set(b) & TARGET_PHASES
    return bool(phases_a and phases_b and phases_a.isdisjoint(phases_b))


def _condition_matches(field_name: str, a: Any, b: Any) -> bool:
    if field_name == "film_thickness_nm":
        return _numeric_close(a, b, absolute=2.0, relative=0.20)
    if field_name == "annealing_temperature_c":
        return _numeric_close(a, b, absolute=25.0, relative=0.05)
    if field_name == "annealing_time_s":
        x, y = _safe_float(a), _safe_float(b)
        if x is None or y is None or min(x, y) <= 0:
            return False
        return max(x, y) / min(x, y) <= 2.0
    return _normalized_text(a) == _normalized_text(b)


def _numeric_close(a: Any, b: Any, *, absolute: float, relative: float) -> bool:
    x, y = _safe_float(a), _safe_float(b)
    if x is None or y is None:
        return False
    return math.isclose(x, y, abs_tol=absolute, rel_tol=relative)


def _safe_float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _evidence_fragments(text: str) -> list[str]:
    compact = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if not compact:
        return []
    fragments = []
    for fragment in re.split(r"(?<=[.!?])\s+|\n+", compact):
        cleaned = fragment.strip(" ;")
        if 15 <= len(cleaned) <= 1200:
            fragments.append(cleaned)
    return fragments


def _first_fragment_match(
    fragments: Sequence[str],
    patterns: Sequence[re.Pattern[str]],
) -> tuple[re.Match[str] | None, str]:
    for fragment in fragments:
        for pattern in patterns:
            match = pattern.search(fragment)
            if match:
                return match, fragment
    return None, ""


def _add_condition_suggestion(
    suggestions: dict[str, dict[str, Any]],
    field_name: str,
    value: Any,
    evidence_text: str,
) -> None:
    suggestions[field_name] = {
        "value": value,
        "source": "rule_from_same_chunk",
        "confidence": "candidate_needs_human_review",
        "evidence_text": evidence_text,
    }


def _missing(value: Any) -> bool:
    return value is None or not str(value).strip()


def _normalized_text(value: Any) -> str:
    text = str(value or "").lower().replace("₂", "2")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _candidate_id(a: PhaseObservation, b: PhaseObservation) -> str:
    pair = sorted([a.observation_id, b.observation_id])
    digest = hashlib.sha256("|".join(pair).encode("utf-8")).hexdigest()[:20]
    return f"phase_conflict_{digest}"


def _priority_score(
    a: PhaseObservation,
    b: PhaseObservation,
    similarity: float,
    coverage: float,
    classification: str,
) -> float:
    evidence_score = min(a.context_score, b.context_score, 1.0)
    class_weight = {
        "scientific_contradiction_candidate": 1.0,
        "condition_explained_difference": 0.85,
        "extraction_review_needed": 0.65,
    }[classification]
    return round(similarity * coverage * evidence_score * class_weight, 4)


def _deduplication_key(candidate: PhaseContradictionCandidate) -> str:
    groups = sorted([candidate.observation_a.paper_group, candidate.observation_b.paper_group])
    phase_pair = sorted(["+".join(candidate.phase_a), "+".join(candidate.phase_b)])
    return "|".join(groups + phase_pair + [candidate.classification])


def _normalize_group_key(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(("doi:", "paper:")):
        prefix, raw = text.split(":", 1)
        return f"{prefix}:{normalize_doi(raw) if prefix == 'doi' else raw}"
    doi = normalize_doi(text)
    return f"doi:{doi}" if doi.startswith("10.") else f"paper:{text}"


def _candidate_dict(candidate: PhaseContradictionCandidate) -> dict[str, Any]:
    return asdict(candidate)


def _annotation_priority(observation: PhaseObservation) -> float:
    source_score = {"direct": 1.0, "chunk_recovered": 0.82, "none": 0.2}.get(
        observation.phase_evidence_source,
        0.2,
    )
    primary_score = 0.6 if observation.paper_is_review else 1.0
    quality_penalty = min(0.6, 0.12 * len(observation.quality_flags))
    if "computed_or_modeled_phase_not_sample_observation" in observation.quality_flags:
        quality_penalty = max(quality_penalty, 0.55)
    if "secondary_or_review_source" in observation.quality_flags:
        quality_penalty = max(quality_penalty, 0.45)
    suggestion_bonus = min(0.15, 0.03 * len(observation.condition_suggestions))
    score = (
        0.40 * source_score
        + 0.30 * observation.context_score
        + 0.20 * primary_score
        + suggestion_bonus
        - quality_penalty
    )
    return round(max(0.0, min(1.0, score)), 4)


def _annotation_row(observation: PhaseObservation, score: float) -> dict[str, Any]:
    digest = hashlib.sha256(
        "|".join(
            [
                observation.paper_group,
                observation.chunk_id,
                observation.material_system,
                "+".join(observation.phase_labels),
            ]
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "annotation_id": f"phase_annotation_{digest}",
        "annotation_priority": score,
        "paper_group": observation.paper_group,
        "doi": observation.doi,
        "paper_id": observation.paper_id,
        "title": observation.title,
        "pdf_id": observation.pdf_id,
        "page_number": observation.page_number,
        "chunk_id": observation.chunk_id,
        "material_system": observation.material_system,
        "material_name": observation.material_name,
        "zr_fraction": observation.zr_fraction,
        "phase_raw": observation.phase_raw,
        "phase_labels": "+".join(observation.phase_labels),
        "characterization_method": observation.characterization_method,
        "phase_evidence_source": observation.phase_evidence_source,
        "phase_evidence_text": observation.phase_evidence_text,
        "property_evidence_text": observation.evidence_text,
        "current_conditions_json": json.dumps(observation.conditions, ensure_ascii=False, sort_keys=True),
        "condition_suggestions_json": json.dumps(
            observation.condition_suggestions,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "quality_flags": ";".join(observation.quality_flags),
        "review_phase_label": "",
        "review_phase_scope": "",
        "review_sample_conditions": "",
        "review_source_type": "",
        "adjudication_status": "pending_human_annotation",
        "annotator": "",
        "review_notes": "",
    }


def _annotation_fieldnames() -> list[str]:
    return [
        "annotation_id",
        "annotation_priority",
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
        "phase_raw",
        "phase_labels",
        "characterization_method",
        "phase_evidence_source",
        "phase_evidence_text",
        "property_evidence_text",
        "current_conditions_json",
        "condition_suggestions_json",
        "quality_flags",
        "review_phase_label",
        "review_phase_scope",
        "review_sample_conditions",
        "review_source_type",
        "adjudication_status",
        "annotator",
        "review_notes",
    ]


def _inventory_fieldnames() -> list[str]:
    return [
        "paper_group",
        "doi",
        "paper_id",
        "title",
        "material_systems",
        "observations",
        "o_observations",
        "t_observations",
        "m_observations",
        "direct_phase_evidence",
        "recovered_phase_evidence",
        "quality_gate_pass_observations",
        "is_review",
        "partition",
        "selection_note",
    ]


def _write_dict_csv(
    path: Path,
    rows: Sequence[dict[str, Any]],
    fieldnames: Sequence[str],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _candidate_csv_fieldnames() -> list[str]:
    return [
        "candidate_id",
        "classification",
        "priority_score",
        "nominal_similarity",
        "comparable_coverage",
        "paper_group_a",
        "paper_group_b",
        "doi_a",
        "doi_b",
        "paper_id_a",
        "paper_id_b",
        "phase_raw_a",
        "phase_raw_b",
        "phase_a",
        "phase_b",
        "differing_fields",
        "comparison_reasons",
        "page_a",
        "page_b",
        "chunk_a",
        "chunk_b",
        "evidence_a",
        "evidence_b",
    ]


def _candidate_csv_row(candidate: PhaseContradictionCandidate) -> dict[str, Any]:
    a, b = candidate.observation_a, candidate.observation_b
    return {
        "candidate_id": candidate.candidate_id,
        "classification": candidate.classification,
        "priority_score": candidate.priority_score,
        "nominal_similarity": candidate.nominal_similarity,
        "comparable_coverage": candidate.comparable_coverage,
        "paper_group_a": a.paper_group,
        "paper_group_b": b.paper_group,
        "doi_a": a.doi,
        "doi_b": b.doi,
        "paper_id_a": a.paper_id,
        "paper_id_b": b.paper_id,
        "phase_raw_a": a.phase_raw,
        "phase_raw_b": b.phase_raw,
        "phase_a": "+".join(candidate.phase_a),
        "phase_b": "+".join(candidate.phase_b),
        "differing_fields": ";".join(candidate.differing_fields),
        "comparison_reasons": ";".join(candidate.comparison_reasons),
        "page_a": a.page_number,
        "page_b": b.page_number,
        "chunk_a": a.chunk_id,
        "chunk_b": b.chunk_id,
        "evidence_a": a.evidence_text,
        "evidence_b": b.evidence_text,
    }
