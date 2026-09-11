from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.llm_extractor import (
    estimate_chunk_cost_units,
    infer_extraction_profiles,
    is_cited_comparison_table,
    llm_status,
    non_evidence_reason,
)
from backend.services.pipeline_log import record_pipeline_run


QUEUE_VERSION = "publication-evidence-packet-v2.3"


def build_publication_extraction_queue(
    output_dir: Path | None = None,
    shard_size: int = 200,
    smoke_per_lane: int = 2,
    include_low_value: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    out = output_dir or PROJECT_ROOT / "data" / "extraction_queues" / "publication_v3"
    shard_dir = out / "shards"
    out.mkdir(parents=True, exist_ok=True)
    shard_dir.mkdir(parents=True, exist_ok=True)

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT dc.chunk_id, dc.paper_id, dc.pdf_id, dc.page_number, dc.section,
                   dc.chunk_index, dc.text, dc.char_count, dc.contains_table,
                   dc.contains_hfo2_keyword, dc.contains_process_keyword,
                   dc.contains_property_keyword, dc.is_high_value,
                   p.title, p.doi, p.year, p.paper_type, p.is_review,
                   pf.parse_quality_score, pf.low_text_page_count, pf.blank_page_count,
                   COALESCE(lrs.final_tier, 'unscreened') AS relevance_tier,
                   COALESCE(lrs.extraction_policy, 'benchmark_full') AS extraction_policy
            FROM document_chunks dc
            JOIN pdf_files pf ON pf.pdf_id = dc.pdf_id
            LEFT JOIN papers p ON p.paper_id = dc.paper_id
            LEFT JOIN literature_relevance_screenings lrs ON lrs.pdf_id = dc.pdf_id
            WHERE pf.parse_status = 'parsed'
              AND COALESCE(pf.is_duplicate, 0) = 0
              AND COALESCE(lrs.final_tier, 'core') != 'irrelevant'
              AND COALESCE(lrs.extraction_policy, 'benchmark_full') != 'exclude'
              AND dc.section != 'references'
              AND dc.char_count >= 120
              AND (? = 1 OR dc.is_high_value = 1 OR dc.contains_table = 1)
            ORDER BY dc.pdf_id, dc.chunk_index
            """,
            (int(include_low_value),),
        ).fetchall()

    records: list[dict[str, Any]] = []
    excluded_records: list[dict[str, Any]] = []
    for row in rows:
        raw = dict(row)
        reason = non_evidence_reason(str(raw.get("text") or ""))
        if reason:
            excluded_records.append({**raw, "exclusion_reason": reason})
            continue
        records.append(_queue_record(raw))
    records.sort(
        key=lambda row: (
            row["priority_rank"],
            -float(row["priority_score"]),
            row["paper_id"],
            int(row["chunk_index"]),
        )
    )
    fields = [
        "chunk_id",
        "paper_id",
        "pdf_id",
        "page_number",
        "section",
        "chunk_index",
        "priority_tier",
        "priority_score",
        "lane",
        "content_kind",
        "extraction_phase",
        "evidence_scope_hint",
        "profiles",
        "paper_type",
        "is_review",
        "relevance_tier",
        "parse_quality_score",
        "rough_tokens",
        "title",
        "doi",
    ]
    queue_path = out / "publication_all_chunks.csv"
    _write_csv(queue_path, records, fields)
    excluded_path = out / "publication_excluded_chunks.csv"
    _write_csv(
        excluded_path,
        excluded_records,
        ["chunk_id", "paper_id", "pdf_id", "page_number", "section", "chunk_index", "exclusion_reason", "title", "doi"],
    )

    lanes: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        lanes.setdefault(str(record["lane"]), []).append(record)
    lane_paths: dict[str, str] = {}
    for lane, lane_records in lanes.items():
        path = out / f"lane_{lane}.csv"
        _write_csv(path, lane_records, fields)
        lane_paths[lane] = str(path)

    phases: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        phases.setdefault(str(record["extraction_phase"]), []).append(record)
    phase_paths: dict[str, str] = {}
    for phase, phase_records in phases.items():
        path = out / f"phase_{phase}.csv"
        _write_csv(path, phase_records, fields)
        phase_paths[phase] = str(path)

    smoke_records: list[dict[str, Any]] = []
    seen_papers: set[str] = set()
    for lane in sorted(lanes):
        selected = _representative_smoke_records(
            lanes[lane],
            limit=smoke_per_lane,
            excluded_papers=seen_papers,
        )
        smoke_records.extend(selected)
        seen_papers.update(str(record["paper_id"]) for record in selected)
    smoke_path = out / "publication_smoke_chunks.csv"
    _write_csv(smoke_path, smoke_records, fields)

    shard_paths: list[str] = []
    shard_size = max(1, shard_size)
    for index, start in enumerate(range(0, len(records), shard_size), start=1):
        path = shard_dir / f"publication_v3_shard{index:03d}.csv"
        _write_csv(path, records[start : start + shard_size], fields)
        shard_paths.append(str(path))

    stats = {
        "queue_version": QUEUE_VERSION,
        "chunks": len(records),
        "papers": len({row["paper_id"] for row in records}),
        "excluded_chunks": len(excluded_records),
        "exclusion_counts": dict(Counter(str(row["exclusion_reason"]) for row in excluded_records)),
        "rough_tokens": sum(int(row["rough_tokens"]) for row in records),
        "priority_counts": dict(Counter(str(row["priority_tier"]) for row in records)),
        "lane_counts": dict(Counter(str(row["lane"]) for row in records)),
        "phase_counts": dict(Counter(str(row["extraction_phase"]) for row in records)),
        "shard_size": shard_size,
        "shards": len(shard_paths),
        "queue_path": str(queue_path),
        "excluded_path": str(excluded_path),
        "smoke_path": str(smoke_path),
        "lane_paths": lane_paths,
        "phase_paths": phase_paths,
        "shard_paths": shard_paths,
        "llm_status": llm_status(),
        "strategy": [
            "P0: numeric measurement, reliability, tables and figure captions",
            "P1: mechanism and computation evidence",
            "P2: process, phase and device context",
            "P3: remaining in-scope evidence",
            "Every claim requires focal-chunk evidence and preserves primary/secondary scope",
        ],
    }
    manifest_path = out / "publication_queue_manifest.json"
    manifest_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    stats["manifest_path"] = str(manifest_path)
    record_pipeline_run("52_build_publication_extraction_queue", "ok", stats, db_path=db_path)
    return stats


def _queue_record(row: dict[str, Any]) -> dict[str, Any]:
    text = str(row.get("text") or "")
    profiles = infer_extraction_profiles(text, row.get("section"))
    lane = _primary_lane(profiles)
    score = _priority_score(row, profiles)
    section = str(row.get("section") or "").lower()
    if (
        "reliability" in profiles
        or (row.get("contains_table") and row.get("contains_property_keyword"))
        or (section == "figure_caption" and row.get("contains_property_keyword"))
    ):
        tier, rank = "P0", 0
    elif (
        "mechanism_computation" in profiles
        or (row.get("contains_property_keyword") and row.get("contains_process_keyword"))
        or (section == "results" and row.get("contains_property_keyword"))
    ):
        tier, rank = "P1", 1
    elif row.get("contains_property_keyword") or row.get("contains_process_keyword") or "process_phase" in profiles:
        tier, rank = "P2", 2
    else:
        tier, rank = "P3", 3
    extraction_phase = _extraction_phase(row, profiles, tier, score)
    secondary_table = is_cited_comparison_table(text, row.get("section"))
    return {
        **row,
        "priority_tier": tier,
        "priority_rank": rank,
        "priority_score": round(score, 2),
        "lane": lane,
        "content_kind": _content_kind(row),
        "extraction_phase": extraction_phase,
        "evidence_scope_hint": (
            "cited_secondary"
            if secondary_table
            else "review_secondary"
            if row.get("is_review")
            else "primary_computation"
            if str(row.get("paper_type") or "") in {"computational", "theoretical"}
            else "primary_experiment"
        ),
        "profiles": "|".join(profiles),
        "rough_tokens": estimate_chunk_cost_units(text)["rough_tokens"],
    }


def _extraction_phase(
    row: dict[str, Any],
    profiles: list[str],
    priority_tier: str,
    priority_score: float,
) -> str:
    is_review = bool(row.get("is_review"))
    if is_cited_comparison_table(str(row.get("text") or ""), row.get("section")):
        return "d_review_secondary"
    if not is_review and priority_score >= 80:
        return "a_primary_dense"
    if not is_review and (
        priority_tier == "P0"
        or any(profile in profiles for profile in ["reliability", "mechanism_computation", "device_application"])
    ):
        return "b_targeted_science"
    if is_review:
        return "d_review_secondary"
    return "c_primary_context"


def _content_kind(row: dict[str, Any]) -> str:
    section = str(row.get("section") or "").lower()
    if row.get("contains_table") or section == "table":
        return "table"
    if section == "figure_caption":
        return "figure_caption"
    return "body_text"


def _representative_smoke_records(
    records: list[dict[str, Any]],
    limit: int,
    excluded_papers: set[str],
) -> list[dict[str, Any]]:
    """Prefer mixed content types so the smoke test covers more than tables."""
    if limit <= 0:
        return []

    selected: list[dict[str, Any]] = []
    selected_papers = set(excluded_papers)
    preferred_kinds = ["body_text", "table", "figure_caption"]

    for kind in preferred_kinds:
        if len(selected) >= limit:
            break
        for record in records:
            paper_id = str(record["paper_id"])
            if record["content_kind"] != kind or paper_id in selected_papers:
                continue
            selected.append(record)
            selected_papers.add(paper_id)
            break

    if len(selected) < limit:
        for record in records:
            paper_id = str(record["paper_id"])
            if paper_id in selected_papers:
                continue
            selected.append(record)
            selected_papers.add(paper_id)
            if len(selected) >= limit:
                break
    return selected


def _priority_score(row: dict[str, Any], profiles: list[str]) -> float:
    score = 0.0
    score += 5 if row.get("contains_hfo2_keyword") else 0
    score += 20 if row.get("contains_property_keyword") else 0
    score += 10 if row.get("contains_process_keyword") else 0
    score += 20 if row.get("contains_table") else 0
    score += 5 if row.get("is_high_value") else 0
    section = str(row.get("section") or "").lower()
    section_scores = {
        "table": 18,
        "figure_caption": 16,
        "results": 15,
        "methods": 12,
        "experimental": 12,
        "discussion": 8,
        "abstract": 5,
    }
    score += section_scores.get(section, 4)
    score += 15 if "reliability" in profiles else 0
    score += 10 if "mechanism_computation" in profiles else 0
    score += 5 if "device_application" in profiles else 0
    score += 5 if str(row.get("paper_type") or "") in {"experimental", "computational", "theoretical"} else 0
    score -= 10 if row.get("is_review") else 0
    score += 3 if row.get("relevance_tier") == "core" else 0
    score -= 12 if (row.get("parse_quality_score") or 1) < 0.8 else 0
    return score


def _primary_lane(profiles: list[str]) -> str:
    if "reliability" in profiles or "measurement" in profiles:
        return "measurement_reliability"
    if "mechanism_computation" in profiles:
        return "mechanism_computation"
    if "process_phase" in profiles:
        return "process_phase"
    if "device_application" in profiles:
        return "device_application"
    return "general"


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
