from __future__ import annotations

import csv
import json
import re
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.llm_json_client import llm_json_chat
from backend.services.llm_quota_guard import is_llm_budget_error, read_llm_pause, write_llm_pause
from backend.services.pipeline_log import record_pipeline_run


SCREENING_VERSION = "literature-relevance-v0.1"
UNRELATED_PDF_PATH_PATTERN = "%/data/unrelated_pdfs/%"

TIERS = {"core", "adjacent", "irrelevant", "needs_review"}
POLICIES = {"benchmark_full", "context_design", "computation_only", "exclude", "review_manual"}

MATERIAL_RE = re.compile(
    r"\b(hfo2|hzo|hfzro|hf0\.?5zr0\.?5o2|hf1[-−]?xzrxo2|hafnia|hafnium oxide|hafnium zirconium oxide|doped hafnia)\b",
    re.I,
)
HZO_RE = re.compile(r"\b(hzo|hfzro|hf0\.?5zr0\.?5o2|hf1[-−]?xzrxo2|hafnium zirconium oxide)\b", re.I)
FERRO_RE = re.compile(r"\b(ferroelectric|ferroelectricity|remanent|polarization|coercive|wake[- ]?up|fatigue|pund|hysteresis)\b", re.I)
PROPERTY_RE = re.compile(r"\b(Pr|2Pr|remanent polarization|coercive|Ec|endurance|retention|memory window|leakage|PUND)\b", re.I)
PROCESS_RE = re.compile(r"\b(ALD|atomic layer deposition|sputter|sputtering|PLD|anneal|annealing|RTA|PMA|PDA|temperature|atmosphere|thickness|TiN|electrode)\b", re.I)
PHASE_RE = re.compile(r"\b(orthorhombic|Pca2?1|monoclinic|tetragonal|rhombohedral|Pbca|Pbcn|phase fraction|XRD|GIWAXS|TEM)\b", re.I)
DEVICE_RE = re.compile(r"\b(FeCAP|FeFET|FTJ|capacitor|memristor|memory|transistor|gate stack|tunnel junction)\b", re.I)
COMPUTATION_RE = re.compile(r"\b(DFT|first[- ]principles|ab initio|phase[- ]field|molecular dynamics|machine learning|neural network|force field|VASP)\b", re.I)
REVIEW_RE = re.compile(r"\b(review|perspective|roadmap|progress|overview|tutorial)\b", re.I)
OFF_TOPIC_RE = re.compile(r"\b(BaTiO3|barium titanate|PZT|PbZr|BiFeO3|PVDF|polymer|microglia|clinical|protein|genome)\b", re.I)


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _count(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text or ""))


def init_relevance_screening_tables(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS literature_relevance_screenings (
                screening_id TEXT PRIMARY KEY,
                paper_id TEXT,
                pdf_id TEXT NOT NULL,
                title TEXT,
                doi TEXT,
                year INTEGER,
                rule_tier TEXT NOT NULL,
                rule_score REAL,
                rule_reasons_json TEXT NOT NULL,
                llm_tier TEXT,
                llm_confidence REAL,
                llm_reasons_json TEXT NOT NULL,
                final_tier TEXT NOT NULL,
                extraction_policy TEXT NOT NULL,
                benchmark_eligible INTEGER DEFAULT 0,
                should_extract_llm_full INTEGER DEFAULT 0,
                material_scope TEXT,
                evidence_roles_json TEXT NOT NULL,
                evidence_text TEXT,
                risk_flags_json TEXT NOT NULL,
                needs_manual_review INTEGER DEFAULT 0,
                model_name TEXT,
                llm_usage_json TEXT NOT NULL,
                screening_version TEXT NOT NULL,
                status TEXT DEFAULT 'ok',
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(pdf_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relevance_screening_final_tier ON literature_relevance_screenings(final_tier)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relevance_screening_policy ON literature_relevance_screenings(extraction_policy)"
        )
        conn.commit()


def _rule_screen_context(context: dict[str, Any]) -> dict[str, Any]:
    text = "\n".join(
        [
            str(context.get("title") or ""),
            str(context.get("file_name") or ""),
            str(context.get("first_pages_text") or ""),
            str(context.get("high_value_chunks_text") or ""),
        ]
    )
    material_hits = _count(MATERIAL_RE, text) + int(context.get("hfo2_chunks") or 0)
    hzo_hits = _count(HZO_RE, text)
    ferro_hits = _count(FERRO_RE, text)
    property_hits = _count(PROPERTY_RE, text) + int(context.get("property_chunks") or 0)
    process_hits = _count(PROCESS_RE, text) + int(context.get("process_chunks") or 0)
    phase_hits = _count(PHASE_RE, text)
    device_hits = _count(DEVICE_RE, text)
    computation_hits = _count(COMPUTATION_RE, text)
    review_hits = _count(REVIEW_RE, text)
    off_topic_hits = _count(OFF_TOPIC_RE, text)
    high_value_chunks = int(context.get("high_value_chunks") or 0)
    table_chunks = int(context.get("table_chunks") or 0)

    axes = {
        "property": property_hits > 0,
        "process": process_hits > 0,
        "phase": phase_hits > 0,
        "device": device_hits > 0,
        "computation": computation_hits > 0,
    }
    axis_count = sum(1 for value in axes.values() if value)
    reasons: list[str] = []
    risk_flags: list[str] = []

    score = 0.0
    score += min(35, material_hits * 3)
    score += min(20, high_value_chunks * 1.5)
    score += min(12, property_hits * 1.5)
    score += min(12, process_hits * 1.2)
    score += min(8, phase_hits * 1.5)
    score += min(6, device_hits * 1.2)
    score += min(4, table_chunks)
    if computation_hits:
        score += min(6, computation_hits)
    if off_topic_hits and material_hits == 0:
        score -= min(24, off_topic_hits * 4)
        risk_flags.append("off_topic_without_hafnia")
    if review_hits:
        risk_flags.append("review_or_secondary_context")

    if material_hits <= 0:
        if ferro_hits >= 2 and (process_hits or property_hits):
            tier = "adjacent"
            policy = "context_design"
            confidence = 0.62
            reasons.append("Ferroelectric/process/property context exists, but explicit HfO2/HZO evidence is weak.")
        else:
            tier = "irrelevant"
            policy = "exclude"
            confidence = 0.9 if off_topic_hits else 0.8
            reasons.append("No explicit HfO2/HZO/doped-hafnia evidence in metadata, first pages, or high-value chunks.")
    elif high_value_chunks > 0 and axis_count >= 2:
        if review_hits and high_value_chunks < 3:
            tier = "adjacent"
            policy = "context_design"
            confidence = 0.72
            reasons.append("HfO2/HZO context is present, but the paper looks review/context-heavy.")
        elif computation_hits and property_hits == 0 and process_hits == 0:
            tier = "adjacent"
            policy = "computation_only"
            confidence = 0.72
            reasons.append("HfO2/HZO computation context is useful, but not a direct experimental benchmark.")
        else:
            tier = "core"
            policy = "benchmark_full"
            confidence = 0.82 if high_value_chunks >= 3 else 0.74
            reasons.append("HfO2/HZO material evidence co-occurs with process/phase/property/device signals.")
    elif material_hits > 0 and (ferro_hits or process_hits or property_hits or phase_hits or computation_hits):
        tier = "adjacent"
        policy = "computation_only" if computation_hits and not property_hits else "context_design"
        confidence = 0.66
        reasons.append("HfO2/HZO evidence exists, but sample-level benchmark signals are incomplete.")
    else:
        tier = "needs_review"
        policy = "review_manual"
        confidence = 0.5
        reasons.append("Weak material evidence with insufficient process/property/phase support.")

    material_scope = "HZO" if hzo_hits else ("HfO2/doped HfO2" if material_hits else "outside_scope")
    roles = [name for name, present in axes.items() if present]
    if high_value_chunks:
        roles.append("high_value_chunk")
    if review_hits:
        roles.append("review_context")
    benchmark_eligible = tier == "core" and bool(property_hits or device_hits)
    should_extract = tier == "core"

    return {
        "rule_tier": tier,
        "rule_score": round(score, 3),
        "rule_confidence": confidence,
        "rule_reasons": reasons,
        "final_tier": tier,
        "extraction_policy": policy,
        "benchmark_eligible": benchmark_eligible,
        "should_extract_llm_full": should_extract,
        "material_scope": material_scope,
        "evidence_roles": roles,
        "risk_flags": risk_flags,
        "needs_manual_review": tier == "needs_review" or confidence < 0.65,
    }


def _system_prompt() -> str:
    return """
You are screening papers for HfO2-FerroKG, a paper prototype about HfO2/HZO/doped-hafnia
ferroelectric knowledge graphs for sample-level extraction, Pr/2Pr benchmarks, and
materials-design modeling. Return exactly one JSON object.

Required JSON keys:
- relevance_tier: one of core, adjacent, irrelevant, needs_review.
- extraction_policy: one of benchmark_full, context_design, computation_only, exclude, review_manual.
- benchmark_eligible: boolean. True only for original HfO2/HZO/doped-hafnia experimental or directly usable sample-level data.
- should_extract_llm_full: boolean. True for core papers that should enter full LLM extraction for benchmark/KG.
- material_scope: short string such as HZO, HfO2, doped HfO2, hafnia computation, general ferroelectric, outside_scope.
- evidence_roles: array chosen from material, process, phase, property, device, interface, oxygen, computation, review_context.
- reasons: array of concise reasons.
- evidence_text: short text copied or tightly paraphrased from the provided context.
- risk_flags: array for review_secondary_value, no_hafnia_material, off_topic_material, insufficient_process_property_context, theory_only, ambiguous_scope.
- confidence: number from 0 to 1.

Screening policy:
- core: strongly relevant HfO2/HZO/doped-hafnia paper with original sample-level process/structure/property/device evidence.
- adjacent: useful background, review, theory, computation, methods, device context, or partial HfO2/HZO evidence. Keep for RAG/design context, not primary Pr/2Pr benchmark.
- irrelevant: outside HfO2/HZO/doped-hafnia ferroelectrics. Exclude from KG writing and benchmark.
- needs_review: conflicting or too ambiguous for automatic routing.

Do not classify BaTiO3, PZT, BiFeO3, PVDF, biology, or unrelated device papers as core unless there is explicit HfO2/HZO/doped-hafnia evidence.
Keep Pr and 2Pr benchmark eligibility strict.
""".strip()


def _user_prompt(context: dict[str, Any], rule_payload: dict[str, Any]) -> str:
    return f"""
metadata:
paper_id: {context.get('paper_id')}
pdf_id: {context.get('pdf_id')}
file_name: {context.get('file_name')}
title_from_db: {context.get('title')}
doi_from_db: {context.get('doi')}
year_from_db: {context.get('year')}

rule_screen:
{json.dumps(rule_payload, ensure_ascii=False)}

chunk_counts:
total_chunks: {context.get('total_chunks')}
hfo2_chunks: {context.get('hfo2_chunks')}
process_chunks: {context.get('process_chunks')}
property_chunks: {context.get('property_chunks')}
high_value_chunks: {context.get('high_value_chunks')}
table_chunks: {context.get('table_chunks')}

first_pages:
{context.get('first_pages_text', '')}

high_value_chunks:
{context.get('high_value_chunks_text', '')}
""".strip()


def _normalize_llm_payload(data: dict[str, Any]) -> dict[str, Any]:
    tier = str(data.get("relevance_tier") or data.get("tier") or "needs_review").strip().lower()
    if tier not in TIERS:
        tier = "needs_review"
    policy = str(data.get("extraction_policy") or "review_manual").strip().lower()
    if policy not in POLICIES:
        policy = {
            "core": "benchmark_full",
            "adjacent": "context_design",
            "irrelevant": "exclude",
        }.get(tier, "review_manual")
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "llm_tier": tier,
        "llm_confidence": confidence,
        "llm_reasons": [str(item) for item in _as_list(data.get("reasons")) if str(item).strip()],
        "extraction_policy": policy,
        "benchmark_eligible": bool(data.get("benchmark_eligible", False)),
        "should_extract_llm_full": bool(data.get("should_extract_llm_full", tier == "core")),
        "material_scope": str(data.get("material_scope") or "").strip(),
        "evidence_roles": [str(item) for item in _as_list(data.get("evidence_roles")) if str(item).strip()],
        "evidence_text": str(data.get("evidence_text") or "").strip(),
        "risk_flags": [str(item) for item in _as_list(data.get("risk_flags")) if str(item).strip()],
    }


def _merge_screening(
    context: dict[str, Any],
    rule: dict[str, Any],
    llm: dict[str, Any] | None,
    llm_usage: dict[str, Any] | None,
    model_name: str | None,
    error_message: str | None = None,
) -> dict[str, Any]:
    final_tier = rule["final_tier"]
    policy = rule["extraction_policy"]
    benchmark_eligible = bool(rule["benchmark_eligible"])
    should_extract = bool(rule["should_extract_llm_full"])
    material_scope = rule.get("material_scope") or ""
    evidence_roles = list(rule.get("evidence_roles") or [])
    risk_flags = list(rule.get("risk_flags") or [])
    reasons = list(rule.get("rule_reasons") or [])
    evidence_text = ""
    needs_review = bool(rule.get("needs_manual_review"))

    llm_tier = None
    llm_confidence = None
    llm_reasons: list[str] = []
    if llm:
        llm_tier = llm["llm_tier"]
        llm_confidence = float(llm["llm_confidence"])
        llm_reasons = list(llm.get("llm_reasons") or [])
        evidence_text = str(llm.get("evidence_text") or "")
        material_scope = llm.get("material_scope") or material_scope
        evidence_roles = sorted(set(evidence_roles + list(llm.get("evidence_roles") or [])))
        risk_flags = sorted(set(risk_flags + list(llm.get("risk_flags") or [])))
        strong_conflict = {rule["final_tier"], llm_tier} == {"core", "irrelevant"}
        if strong_conflict:
            final_tier = "needs_review"
            policy = "review_manual"
            benchmark_eligible = False
            should_extract = False
            needs_review = True
            risk_flags.append("rule_llm_conflict")
        elif llm_confidence >= 0.62:
            final_tier = llm_tier
            policy = llm["extraction_policy"]
            benchmark_eligible = bool(llm["benchmark_eligible"]) and final_tier == "core"
            should_extract = bool(llm["should_extract_llm_full"]) and final_tier == "core"
            needs_review = final_tier == "needs_review" or llm_confidence < 0.72
        else:
            risk_flags.append("low_llm_confidence")
            needs_review = True

    if final_tier == "irrelevant":
        policy = "exclude"
        benchmark_eligible = False
        should_extract = False
    elif final_tier == "adjacent" and policy == "benchmark_full":
        policy = "context_design"
        benchmark_eligible = False
        should_extract = False

    return {
        "screening_id": f"screen_{uuid.uuid5(uuid.NAMESPACE_URL, str(context.get('pdf_id')) + SCREENING_VERSION).hex[:16]}",
        "paper_id": context.get("paper_id"),
        "pdf_id": context.get("pdf_id"),
        "title": context.get("title") or "",
        "doi": context.get("doi") or "",
        "year": context.get("year"),
        "rule_tier": rule["rule_tier"],
        "rule_score": rule["rule_score"],
        "rule_reasons": reasons,
        "llm_tier": llm_tier,
        "llm_confidence": llm_confidence,
        "llm_reasons": llm_reasons,
        "final_tier": final_tier,
        "extraction_policy": policy,
        "benchmark_eligible": benchmark_eligible,
        "should_extract_llm_full": should_extract,
        "material_scope": material_scope,
        "evidence_roles": sorted(set(evidence_roles)),
        "evidence_text": evidence_text,
        "risk_flags": sorted(set(risk_flags)),
        "needs_manual_review": needs_review,
        "model_name": model_name or "",
        "llm_usage": llm_usage or {},
        "screening_version": SCREENING_VERSION,
        "status": "error" if error_message else "ok",
        "error_message": error_message,
    }


def _load_contexts(limit: int | None = None, db_path: Path | None = None, skip_existing: bool = False) -> list[dict[str, Any]]:
    init_relevance_screening_tables(db_path=db_path)
    with connect(db_path) as conn:
        sql = """
            SELECT
                pf.pdf_id, pf.file_name, pf.paper_id, p.title, p.doi, p.year, p.paper_type,
                COALESCE(dc_stats.total_chunks, 0) AS total_chunks,
                COALESCE(dc_stats.hfo2_chunks, 0) AS hfo2_chunks,
                COALESCE(dc_stats.process_chunks, 0) AS process_chunks,
                COALESCE(dc_stats.property_chunks, 0) AS property_chunks,
                COALESCE(dc_stats.high_value_chunks, 0) AS high_value_chunks,
                COALESCE(dc_stats.table_chunks, 0) AS table_chunks
            FROM pdf_files pf
            LEFT JOIN papers p ON p.paper_id = pf.paper_id
            LEFT JOIN (
                SELECT
                    pdf_id,
                    COUNT(*) AS total_chunks,
                    SUM(contains_hfo2_keyword) AS hfo2_chunks,
                    SUM(contains_process_keyword) AS process_chunks,
                    SUM(contains_property_keyword) AS property_chunks,
                    SUM(is_high_value) AS high_value_chunks,
                    SUM(contains_table) AS table_chunks
                FROM document_chunks
                GROUP BY pdf_id
            ) dc_stats ON dc_stats.pdf_id = pf.pdf_id
            WHERE pf.is_duplicate = 0
              AND COALESCE(pf.parse_status, '') != 'excluded_irrelevant'
              AND pf.file_path NOT LIKE ?
        """
        params: list[Any] = [UNRELATED_PDF_PATH_PATTERN]
        if skip_existing:
            sql += " AND NOT EXISTS (SELECT 1 FROM literature_relevance_screenings s WHERE s.pdf_id = pf.pdf_id)"
        sql += " ORDER BY COALESCE(p.year, 0) DESC, pf.file_name"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(sql, params).fetchall()

        contexts: list[dict[str, Any]] = []
        for row in rows:
            first_pages = conn.execute(
                """
                SELECT page_number, text
                FROM parsed_pages
                WHERE pdf_id = ?
                ORDER BY page_number
                LIMIT 3
                """,
                (row["pdf_id"],),
            ).fetchall()
            chunks = conn.execute(
                """
                SELECT page_number, section, text
                FROM document_chunks
                WHERE pdf_id = ?
                  AND (is_high_value = 1 OR contains_table = 1 OR section IN ('table', 'figure_caption'))
                ORDER BY is_high_value DESC, page_number, chunk_index
                LIMIT 10
                """,
                (row["pdf_id"],),
            ).fetchall()
            first_text = "\n\n".join(
                f"[page {item['page_number']}]\n{item['text'][:3000]}" for item in first_pages
            )[:8500]
            chunk_text = "\n\n".join(
                f"[page {item['page_number']} | {item['section']}]\n{item['text'][:1500]}" for item in chunks
            )[:13000]
            contexts.append(
                {
                    "paper_id": row["paper_id"],
                    "pdf_id": row["pdf_id"],
                    "file_name": row["file_name"],
                    "title": row["title"] or "",
                    "doi": row["doi"] or "",
                    "year": row["year"],
                    "paper_type": row["paper_type"] or "unknown",
                    "total_chunks": int(row["total_chunks"] or 0),
                    "hfo2_chunks": int(row["hfo2_chunks"] or 0),
                    "process_chunks": int(row["process_chunks"] or 0),
                    "property_chunks": int(row["property_chunks"] or 0),
                    "high_value_chunks": int(row["high_value_chunks"] or 0),
                    "table_chunks": int(row["table_chunks"] or 0),
                    "first_pages_text": first_text,
                    "high_value_chunks_text": chunk_text,
                }
            )
    return contexts


def _needs_llm(rule: dict[str, Any], llm_mode: str) -> bool:
    if llm_mode == "off":
        return False
    if llm_mode == "all":
        return True
    if llm_mode != "uncertain":
        raise ValueError("llm_mode must be one of off, uncertain, all")
    return bool(
        rule["final_tier"] in {"adjacent", "needs_review", "irrelevant"}
        or (rule["final_tier"] != "core" and rule.get("needs_manual_review"))
        or (rule["final_tier"] != "core" and float(rule.get("rule_confidence") or 0) < 0.76)
    )


def _screen_one(context: dict[str, Any], model: str | None, llm_mode: str) -> dict[str, Any]:
    rule = _rule_screen_context(context)
    if not _needs_llm(rule, llm_mode):
        return _merge_screening(context, rule, None, None, None)
    try:
        result = llm_json_chat(
            _system_prompt(),
            _user_prompt(context, rule),
            model=model,
            max_tokens=1800,
            enable_thinking=True,
        )
        llm_payload = _normalize_llm_payload(result.payload)
        return _merge_screening(context, rule, llm_payload, result.usage, result.model)
    except Exception as exc:
        error = str(exc)
        if is_llm_budget_error(error):
            return {"_pause_error": error, **context}
        return _merge_screening(context, rule, None, None, model, error_message=error[:1500])


def _write_screenings(rows: list[dict[str, Any]], db_path: Path | None = None) -> None:
    if not rows:
        return
    init_relevance_screening_tables(db_path=db_path)
    with connect(db_path) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO literature_relevance_screenings (
                    screening_id, paper_id, pdf_id, title, doi, year,
                    rule_tier, rule_score, rule_reasons_json,
                    llm_tier, llm_confidence, llm_reasons_json,
                    final_tier, extraction_policy, benchmark_eligible,
                    should_extract_llm_full, material_scope, evidence_roles_json,
                    evidence_text, risk_flags_json, needs_manual_review,
                    model_name, llm_usage_json, screening_version, status,
                    error_message, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(pdf_id) DO UPDATE SET
                    paper_id = excluded.paper_id,
                    title = excluded.title,
                    doi = excluded.doi,
                    year = excluded.year,
                    rule_tier = excluded.rule_tier,
                    rule_score = excluded.rule_score,
                    rule_reasons_json = excluded.rule_reasons_json,
                    llm_tier = excluded.llm_tier,
                    llm_confidence = excluded.llm_confidence,
                    llm_reasons_json = excluded.llm_reasons_json,
                    final_tier = excluded.final_tier,
                    extraction_policy = excluded.extraction_policy,
                    benchmark_eligible = excluded.benchmark_eligible,
                    should_extract_llm_full = excluded.should_extract_llm_full,
                    material_scope = excluded.material_scope,
                    evidence_roles_json = excluded.evidence_roles_json,
                    evidence_text = excluded.evidence_text,
                    risk_flags_json = excluded.risk_flags_json,
                    needs_manual_review = excluded.needs_manual_review,
                    model_name = excluded.model_name,
                    llm_usage_json = excluded.llm_usage_json,
                    screening_version = excluded.screening_version,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    row["screening_id"],
                    row.get("paper_id"),
                    row["pdf_id"],
                    row.get("title") or "",
                    row.get("doi") or "",
                    row.get("year"),
                    row["rule_tier"],
                    float(row.get("rule_score") or 0),
                    _json(row.get("rule_reasons") or []),
                    row.get("llm_tier"),
                    row.get("llm_confidence"),
                    _json(row.get("llm_reasons") or []),
                    row["final_tier"],
                    row["extraction_policy"],
                    int(bool(row.get("benchmark_eligible"))),
                    int(bool(row.get("should_extract_llm_full"))),
                    row.get("material_scope") or "",
                    _json(row.get("evidence_roles") or []),
                    row.get("evidence_text") or "",
                    _json(row.get("risk_flags") or []),
                    int(bool(row.get("needs_manual_review"))),
                    row.get("model_name") or "",
                    _json(row.get("llm_usage") or {}),
                    row.get("screening_version") or SCREENING_VERSION,
                    row.get("status") or "ok",
                    row.get("error_message"),
                ),
            )
        conn.commit()


def _export(rows: list[dict[str, Any]], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "literature_relevance_screening.csv"
    json_path = output_dir / "literature_relevance_screening.json"
    md_path = output_dir / "literature_relevance_screening_summary.md"
    flat_rows: list[dict[str, Any]] = []
    for row in rows:
        flat_rows.append(
            {
                "final_tier": row.get("final_tier"),
                "extraction_policy": row.get("extraction_policy"),
                "benchmark_eligible": int(bool(row.get("benchmark_eligible"))),
                "should_extract_llm_full": int(bool(row.get("should_extract_llm_full"))),
                "needs_manual_review": int(bool(row.get("needs_manual_review"))),
                "rule_tier": row.get("rule_tier"),
                "rule_score": row.get("rule_score"),
                "llm_tier": row.get("llm_tier") or "",
                "llm_confidence": row.get("llm_confidence") if row.get("llm_confidence") is not None else "",
                "material_scope": row.get("material_scope") or "",
                "pdf_id": row.get("pdf_id"),
                "paper_id": row.get("paper_id"),
                "year": row.get("year") or "",
                "doi": row.get("doi") or "",
                "title": row.get("title") or "",
                "rule_reasons": "; ".join(row.get("rule_reasons") or []),
                "llm_reasons": "; ".join(row.get("llm_reasons") or []),
                "risk_flags": "; ".join(row.get("risk_flags") or []),
                "status": row.get("status") or "ok",
                "error_message": row.get("error_message") or "",
            }
        )
    fieldnames = list(flat_rows[0].keys()) if flat_rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    policies: dict[str, int] = {}
    for row in rows:
        counts[row["final_tier"]] = counts.get(row["final_tier"], 0) + 1
        policies[row["extraction_policy"]] = policies.get(row["extraction_policy"], 0) + 1
    lines = [
        "# Literature Relevance Screening",
        "",
        f"- Version: {SCREENING_VERSION}",
        f"- Papers screened: {len(rows)}",
        "",
        "## Final tiers",
        "",
    ]
    lines.extend(f"- {key}: {counts[key]}" for key in sorted(counts))
    lines.extend(["", "## Extraction policies", ""])
    lines.extend(f"- {key}: {policies[key]}" for key in sorted(policies))
    lines.extend(
        [
            "",
            "## Operating rule",
            "",
            "- core enters full LLM extraction and Pr/2Pr benchmark construction.",
            "- adjacent is retained for RAG, design context, theory/computation guidance, and manual review.",
            "- irrelevant is excluded from graph writing and benchmark queues.",
            "- needs_review is not used for benchmark until manually approved.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path), "markdown": str(md_path)}


def screen_literature_relevance(
    limit: int | None = None,
    model: str | None = None,
    reset: bool = False,
    skip_existing: bool = False,
    llm_mode: str = "uncertain",
    max_workers: int = 1,
    commit_every: int = 25,
    progress_every: int = 25,
    force_llm_when_paused: bool = False,
    output_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    init_relevance_screening_tables(db_path=db_path)
    if reset:
        with connect(db_path) as conn:
            conn.execute("DELETE FROM literature_relevance_screenings")
            conn.commit()
    contexts = _load_contexts(limit=limit, db_path=db_path, skip_existing=skip_existing)
    out_dir = output_dir or PROJECT_ROOT / "data" / "relevance_screening"
    stats: dict[str, Any] = {
        "version": SCREENING_VERSION,
        "papers_seen": len(contexts),
        "screenings_written": 0,
        "llm_mode": llm_mode,
        "llm_failed": 0,
        "paused": 0,
        "outputs": {},
        "tier_counts": {},
        "policy_counts": {},
    }
    if read_llm_pause() and llm_mode != "off" and not force_llm_when_paused:
        llm_mode = "off"
        stats["llm_mode"] = "off_due_to_existing_pause"

    pending: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        _write_screenings(pending, db_path=db_path)
        all_rows.extend(pending)
        stats["screenings_written"] += len(pending)
        pending = []

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = set()
        index = 0

        def submit_next() -> None:
            nonlocal index
            if index < len(contexts):
                futures.add(pool.submit(_screen_one, contexts[index], model, llm_mode))
                index += 1

        for _ in range(min(max(1, max_workers), len(contexts))):
            submit_next()
        while futures:
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                result = future.result()
                if "_pause_error" in result:
                    stats["paused"] = 1
                    stats["llm_failed"] += 1
                    pause_path = write_llm_pause(result["_pause_error"], {"pipeline": "40_screen_literature_relevance"})
                    stats["pause_file"] = str(pause_path)
                    for pending_future in futures:
                        pending_future.cancel()
                    futures.clear()
                    break
                if result.get("status") == "error":
                    stats["llm_failed"] += 1
                pending.append(result)
                if progress_every and (stats["screenings_written"] + len(pending)) % progress_every == 0:
                    print(
                        f"relevance_screening processed={stats['screenings_written'] + len(pending)} "
                        f"written={stats['screenings_written']} errors={stats['llm_failed']} paused={stats['paused']}",
                        flush=True,
                    )
                if commit_every and len(pending) >= commit_every:
                    flush()
                if not stats.get("paused"):
                    submit_next()
            if stats.get("paused"):
                break
    flush()

    if all_rows:
        stats["outputs"] = _export(all_rows, out_dir)
    for row in all_rows:
        stats["tier_counts"][row["final_tier"]] = stats["tier_counts"].get(row["final_tier"], 0) + 1
        stats["policy_counts"][row["extraction_policy"]] = stats["policy_counts"].get(row["extraction_policy"], 0) + 1
    record_pipeline_run(
        "40_screen_literature_relevance",
        "paused" if stats.get("paused") else "ok",
        stats,
        db_path=db_path,
    )
    return stats
