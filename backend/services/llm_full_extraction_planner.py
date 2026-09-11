from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, get_settings
from backend.db.session import connect
from backend.services.llm_extractor import estimate_chunk_cost_units, llm_status
from backend.services.literature_relevance_screener import init_relevance_screening_tables
from backend.services.pipeline_log import record_pipeline_run


TOPIC_PATTERNS = {
    "hfo2_hzo_material": r"\b(HfO2|hafnia|hafnium oxide|HZO|Hf0\.5Zr0\.5O2|Hf1[-−]xZrxO2|HfZrO)\b",
    "property": r"\b(Pr|2Pr|remanent polarization|coercive|Ec|endurance|retention|memory window|leakage)\b",
    "phase": r"\b(orthorhombic|Pca2?1|monoclinic|tetragonal|rhombohedral|Pbca|Pbcn|phase fraction|orientation)\b",
    "interface": r"\b(interface|electrode|TiN|W|WO3|WOx|Pt|RuO2|ITO|LSMO|termination|interlayer|buffer layer)\b",
    "oxygen": r"\b(oxygen vacanc|oxygen-deficien|oxygen reservoir|redox|scaveng|forming gas|vacuum|oxygen pressure)\b",
    "process": r"\b(ALD|sputter|PLD|anneal|RTA|PDA|temperature|atmosphere|thickness)\b",
    "computation_ai": r"\b(DFT|first-principles|phase-field|machine learning|neural network|molecular dynamics|force field)\b",
}
UNRELATED_PDF_PATH_PATTERN = "%/data/unrelated_pdfs/%"


def _topic_hits(text: str) -> list[str]:
    return [
        name
        for name, pattern in TOPIC_PATTERNS.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]


def _topic_score(text: str, is_high_value: int) -> float:
    hits = _topic_hits(text)
    score = len(hits) / len(TOPIC_PATTERNS)
    if is_high_value:
        score += 0.20
    return round(min(score, 1.0), 3)


def build_full_llm_extraction_plan(
    output_dir: Path | None = None,
    high_value_only: bool = True,
    include_existing: bool = False,
    limit_chunks: int | None = None,
    relevance_tiers: list[str] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    out_dir = output_dir or PROJECT_ROOT / "data" / "extraction_queues"
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = out_dir / (
        "full_hzo_llm_high_value_chunks.csv" if high_value_only else "full_hzo_llm_all_chunks.csv"
    )
    paper_path = out_dir / (
        "full_hzo_llm_high_value_papers.csv" if high_value_only else "full_hzo_llm_all_papers.csv"
    )
    json_path = out_dir / (
        "full_hzo_llm_high_value_plan.json" if high_value_only else "full_hzo_llm_all_plan.json"
    )
    md_path = out_dir / (
        "full_hzo_llm_high_value_plan.md" if high_value_only else "full_hzo_llm_all_plan.md"
    )

    settings = get_settings()
    normalized_tiers = [tier.strip().lower() for tier in (relevance_tiers or []) if tier.strip()]
    init_relevance_screening_tables(db_path=db_path)
    with connect(db_path) as conn:
        query = """
            SELECT dc.chunk_id, dc.paper_id, dc.pdf_id, dc.page_number, dc.chunk_index,
                   dc.is_high_value, dc.text, p.title, p.doi, p.year,
                   rs.final_tier AS relevance_tier,
                   rs.extraction_policy AS relevance_policy,
                   rs.should_extract_llm_full AS relevance_should_extract
            FROM document_chunks dc
            LEFT JOIN pdf_files pf ON pf.pdf_id = dc.pdf_id
            LEFT JOIN papers p ON p.paper_id = dc.paper_id
            LEFT JOIN literature_relevance_screenings rs ON rs.pdf_id = dc.pdf_id
            WHERE (? = 0 OR dc.is_high_value = 1)
              AND (pf.file_path IS NULL OR pf.file_path NOT LIKE ?)
        """
        params: list[Any] = [int(high_value_only), UNRELATED_PDF_PATH_PATTERN]
        if normalized_tiers:
            placeholders = ",".join("?" for _ in normalized_tiers)
            query += f"""
              AND rs.final_tier IN ({placeholders})
              AND COALESCE(rs.extraction_policy, '') != 'exclude'
            """
            params.extend(normalized_tiers)
        if not include_existing:
            query += """
              AND NOT EXISTS (
                SELECT 1 FROM extraction_candidates ec
                WHERE ec.chunk_id = dc.chunk_id
              )
            """
        query += " ORDER BY dc.pdf_id, dc.chunk_index"
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]

    planned_rows: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text") or "")
        cost = estimate_chunk_cost_units(text)
        hits = _topic_hits(text)
        planned_rows.append(
            {
                "chunk_id": row["chunk_id"],
                "paper_id": row["paper_id"],
                "pdf_id": row["pdf_id"],
                "page_number": row["page_number"],
                "chunk_index": row["chunk_index"],
                "is_high_value": int(row["is_high_value"] or 0),
                "title": row.get("title") or "",
                "doi": row.get("doi") or "",
                "year": row.get("year") or "",
                "relevance_tier": row.get("relevance_tier") or "",
                "relevance_policy": row.get("relevance_policy") or "",
                "relevance_should_extract": int(row.get("relevance_should_extract") or 0),
                "topic_score": _topic_score(text, int(row["is_high_value"] or 0)),
                "topic_hits": ",".join(hits),
                "chars": cost["chars"],
                "rough_tokens": cost["rough_tokens"],
            }
        )
    planned_rows.sort(key=lambda item: (-float(item["topic_score"]), -int(item["is_high_value"]), item["pdf_id"], item["chunk_index"]))
    if limit_chunks is not None:
        planned_rows = planned_rows[:limit_chunks]

    with chunk_path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "chunk_id",
            "paper_id",
            "pdf_id",
            "page_number",
            "chunk_index",
            "is_high_value",
            "title",
            "doi",
            "year",
            "relevance_tier",
            "relevance_policy",
            "relevance_should_extract",
            "topic_score",
            "topic_hits",
            "chars",
            "rough_tokens",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(planned_rows)

    paper_ids = sorted({row["paper_id"] for row in planned_rows if row.get("paper_id")})
    paper_path.write_text("paper_id\n" + "\n".join(paper_ids) + ("\n" if paper_ids else ""), encoding="utf-8")

    incremental_flag = "" if include_existing else "--incremental "
    extract_command = (
        f".venv/bin/python pipelines/05_run_extraction.py {incremental_flag}"
        f"--chunk-list {chunk_path} --model {settings.llm_model} "
        "--commit-every 10 --progress-every 10 --max-workers 1"
    )
    if not high_value_only:
        extract_command += " --all-chunks"
    dry_run_command = extract_command + " --dry-run --limit-chunks 5"
    stats = {
        "status": "planned",
        "high_value_only": high_value_only,
        "include_existing": include_existing,
        "relevance_tiers": normalized_tiers,
        "chunks": len(planned_rows),
        "papers": len(paper_ids),
        "rough_tokens": int(sum(int(row["rough_tokens"]) for row in planned_rows)),
        "chunk_list_path": str(chunk_path),
        "paper_list_path": str(paper_path),
        "plan_json_path": str(json_path),
        "plan_md_path": str(md_path),
        "llm_status": llm_status(),
        "dry_run_command": dry_run_command,
        "extract_command": extract_command,
        "notes": _plan_notes(
            include_existing=include_existing,
            high_value_only=high_value_only,
            relevance_tiers=normalized_tiers,
        ),
    }
    json_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown_plan(stats), encoding="utf-8")
    record_pipeline_run("36_prepare_full_llm_extraction", "ok", stats, db_path=db_path)
    return stats


def _markdown_plan(stats: dict[str, Any]) -> str:
    lines = [
        "# Full HZO LLM Extraction Plan",
        "",
        f"- Status: {stats['status']}",
        f"- High-value only: {stats['high_value_only']}",
        f"- Include existing extracted chunks: {stats['include_existing']}",
        f"- Relevance tiers: {stats.get('relevance_tiers') or 'not filtered'}",
        f"- Planned chunks: {stats['chunks']}",
        f"- Planned papers: {stats['papers']}",
        f"- Rough token estimate: {stats['rough_tokens']}",
        f"- Chunk queue: `{stats['chunk_list_path']}`",
        f"- Paper queue: `{stats['paper_list_path']}`",
        "",
        "## Dry Run",
        "",
        "```bash",
        stats["dry_run_command"],
        "```",
        "",
        "## Resumable Extraction",
        "",
        "```bash",
        stats["extract_command"],
        "```",
        "",
        "## Notes",
        "",
    ]
    lines.extend(f"- {note}" for note in stats.get("notes", []))
    return "\n".join(lines) + "\n"


def _plan_notes(include_existing: bool, high_value_only: bool, relevance_tiers: list[str] | None = None) -> list[str]:
    notes = ["Use the dry-run command first to verify schema and API configuration."]
    if include_existing:
        notes.append(
            "This plan is for re-extraction after schema or prompt changes; the command intentionally omits --incremental for the selected chunk list."
        )
    else:
        notes.append("This plan is incremental and avoids re-calling chunks already present in extraction_candidates.")
    if not high_value_only:
        notes.append("This is an all-chunks plan. Use only when budget allows exhaustive extraction.")
    else:
        notes.append("This high-value plan is the recommended first full re-extraction pass.")
    if relevance_tiers:
        notes.append(
            "This plan is filtered by literature_relevance_screenings final_tier so benchmark extraction stays in-scope."
        )
    else:
        notes.append("No paper-level relevance filter was applied.")
    return notes
