from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


def _candidate_key(candidate: dict[str, Any]) -> tuple[str, str]:
    return (
        str(candidate.get("entity_or_relation") or "unknown"),
        str(candidate.get("label") or "").strip(),
    )


def build_benchmark_ontology_extension(
    db_path: Path | None = None,
    min_support: int = 2,
) -> dict[str, Any]:
    support: Counter[tuple[str, str]] = Counter()
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    design_rules: list[dict[str, Any]] = []
    mechanisms: list[dict[str, Any]] = []
    theoretical_insights: list[dict[str, Any]] = []

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT be.paper_id, be.pdf_id, be.chunk_id, be.page_number,
                   be.payload_json, p.title, p.doi
            FROM benchmark_extractions be
            LEFT JOIN papers p ON p.paper_id = be.paper_id
            WHERE be.status = 'ok'
            """
        ).fetchall()

    for row in rows:
        payload = json.loads(row["payload_json"])
        source = {
            "paper_id": row["paper_id"],
            "pdf_id": row["pdf_id"],
            "chunk_id": row["chunk_id"],
            "page_number": row["page_number"],
            "title": row["title"],
            "doi": row["doi"],
        }
        for candidate in payload.get("ontology_candidates") or []:
            key = _candidate_key(candidate)
            if not key[1]:
                continue
            support[key] += 1
            if len(evidence[key]) < 5:
                evidence[key].append({**source, "evidence_text": candidate.get("evidence_text")})
        for item in payload.get("design_rules") or []:
            design_rules.append({**source, **item})
        for item in payload.get("mechanisms") or []:
            mechanisms.append({**source, **item})
        for item in payload.get("theoretical_insights") or []:
            theoretical_insights.append({**source, **item})

    candidates = [
        {
            "kind": kind,
            "label": label,
            "support_count": count,
            "evidence": evidence[(kind, label)],
        }
        for (kind, label), count in support.most_common()
        if count >= min_support
    ]

    extension = {
        "name": "HfO2-FerroKG benchmark-derived ontology extension",
        "source": "benchmark_extractions",
        "min_support": min_support,
        "ontology_candidates": candidates,
        "design_rule_count": len(design_rules),
        "mechanism_count": len(mechanisms),
        "theoretical_insight_count": len(theoretical_insights),
    }
    output_dir = PROJECT_ROOT / "ontology"
    output_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = output_dir / "benchmark_extension.yaml"
    json_path = PROJECT_ROOT / "data" / "benchmark" / "benchmark_ontology_extension.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml.safe_dump(extension, allow_unicode=True, sort_keys=False), encoding="utf-8")
    json_path.write_text(json.dumps(extension, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = {
        "ontology_candidates": len(candidates),
        "raw_candidate_labels": len(support),
        "design_rules": len(design_rules),
        "mechanisms": len(mechanisms),
        "theoretical_insights": len(theoretical_insights),
        "yaml_path": str(yaml_path),
        "json_path": str(json_path),
    }
    record_pipeline_run("20_update_ontology_from_benchmark", "ok", stats, db_path=db_path)
    return stats

