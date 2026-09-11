from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import fitz

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


EQUATION_LABEL_RE = re.compile(r"(?:^|\s)(\(\d+[A-Za-z]?\))\s*$")
MATH_SYMBOL_RE = re.compile(r"[=≈≃≤≥∝∑∫∂√∆Δ±×·→←↔]|\b(?:exp|ln|log|sin|cos|tan)\s*\(", re.IGNORECASE)
VARIABLE_RE = re.compile(r"[A-Za-zΑ-Ωα-ω][A-Za-zΑ-Ωα-ω0-9_′']{0,8}\s*=")
RELATION_RE = re.compile(r"[=≈≃≤≥∝]")
ADVANCED_MATH_RE = re.compile(r"[∑∫∂√∇]|\b(?:exp|ln|log|sin|cos|tan)\s*\(", re.IGNORECASE)
PARAMETER_ASSIGNMENT_RE = re.compile(
    r"[A-Za-zΑ-Ωα-ω𝜀𝜕][A-Za-zΑ-Ωα-ω𝜀𝜕0-9_′']{0,10}\s*(?:=|≈|≃|≤|≥)"
)
UNIT_RE = re.compile(
    r"(?<![A-Za-z])(?:Å|°C|°|μC/cm2|µC/cm2|μC/cm²|µC/cm²|MV/cm|kV/cm|V/nm|nm|μm|µm|mm|cm|"
    r"mV|kV|MV|V|mA|μA|µA|nA|A|Hz|kHz|MHz|GHz|ns|μs|µs|ms|s|K|eV|meV|GPa|MPa)"
    r"(?=$|[\s,.;:)\]])",
)
FORMULA_CUE_RE = re.compile(
    r"\b(?:equation|eq\.?|relation|relationship|defined|expressed|described|given|calculated|written)\b",
    re.IGNORECASE,
)
PARAMETER_CUE_RE = re.compile(
    r"\b(?:thickness|sample|samples|applied fields?|lattice constants?|cycling stages?|anneal(?:ed|ing)?|"
    r"deposited|pulse width|voltage|temperature)\b",
    re.IGNORECASE,
)


def equation_candidate_kind(text: str) -> str | None:
    """Classify equation text while excluding prose-like experimental parameter lists."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) < 4 or len(cleaned) > 320:
        return None
    if "http://" in cleaned.lower() or "https://" in cleaned.lower() or "@" in cleaned:
        return None

    prose_words = re.findall(r"\b[A-Za-z]{3,}\b", cleaned)
    assignments = PARAMETER_ASSIGNMENT_RE.findall(cleaned)
    units = UNIT_RE.findall(cleaned)
    has_relation = bool(RELATION_RE.search(cleaned))
    has_advanced_math = bool(ADVANCED_MATH_RE.search(cleaned))
    has_formula_cue = bool(FORMULA_CUE_RE.search(cleaned))
    has_label = bool(EQUATION_LABEL_RE.search(cleaned))
    math_symbols = len(MATH_SYMBOL_RE.findall(cleaned))

    parameter_list = (
        len(assignments) >= 2
        and len(units) >= 1
        and (cleaned.count(",") >= 1 or len(prose_words) >= 5)
    ) or (
        bool(PARAMETER_CUE_RE.search(cleaned))
        and len(units) >= 1
        and len(prose_words) >= 2
        and not has_advanced_math
    )
    if parameter_list and not has_formula_cue:
        return None

    if not (has_relation or has_advanced_math or has_label):
        return None
    if len(prose_words) > 35:
        return None
    if len(prose_words) > 20 and not (has_formula_cue or has_advanced_math):
        return None

    compact_formula = len(prose_words) <= 8 and (has_relation or has_advanced_math)
    dense_math = math_symbols >= 2 and len(prose_words) <= 14
    if has_label or has_advanced_math or compact_formula or dense_math:
        return "display_equation"
    if has_formula_cue and has_relation:
        return "inline_equation"
    if len(prose_words) <= 14 and has_relation and len(assignments) <= 2:
        return "inline_equation"
    return None


def equation_candidate_score(text: str) -> float:
    cleaned = " ".join(str(text or "").split())
    if equation_candidate_kind(cleaned) is None:
        return 0.0
    score = 0.0
    if "=" in cleaned:
        score += 2.0
    score += min(2.0, 0.5 * len(MATH_SYMBOL_RE.findall(cleaned)))
    if VARIABLE_RE.search(cleaned):
        score += 1.0
    if re.search(r"[Α-Ωα-ω∆Δεϵμσλκρτφψω]", cleaned):
        score += 0.75
    if re.search(r"\b[A-Za-z][A-Za-z0-9_′']*\s*[\/^]\s*[A-Za-z0-9(]", cleaned):
        score += 0.75
    if EQUATION_LABEL_RE.search(cleaned):
        score += 0.75
    return max(0.0, min(1.0, score / 5.0)) if score >= 2.0 else 0.0


def _equation_id(pdf_id: str, page_number: int, index: int) -> str:
    value = f"{pdf_id}:{page_number}:equation:{index}"
    return f"eq_{uuid.uuid5(uuid.NAMESPACE_URL, value).hex[:16]}"


def _normalized_equation(text: str) -> str:
    value = " ".join(str(text or "").split())
    return value.replace("−", "-").replace("–", "-").replace("µ", "μ")


def _formula_cluster_bbox(
    page: fitz.Page,
    seed_bbox: fitz.Rect,
    lines: list[dict[str, Any]],
    y_min: float | None = None,
    y_max: float | None = None,
) -> fitz.Rect:
    """Join nearby formula fragments such as integral limits, fractions, and equation labels."""
    cluster = fitz.Rect(seed_bbox)
    vertical_padding = max(10.0, seed_bbox.height * 0.8)
    for item in lines:
        bbox = fitz.Rect(item["bbox"])
        center_y = (bbox.y0 + bbox.y1) / 2
        if y_min is not None and center_y <= y_min:
            continue
        if y_max is not None and center_y >= y_max:
            continue
        if bbox.x0 < seed_bbox.x0 - 72 or bbox.x0 > page.rect.width - 18:
            continue
        if bbox.y1 < seed_bbox.y0 - vertical_padding or bbox.y0 > seed_bbox.y1 + vertical_padding:
            continue
        text = item["text"].strip()
        words = re.findall(r"\b[A-Za-z]{3,}\b", text)
        is_equation_label = bool(re.fullmatch(r"\(?\d+(?:\.\d+)?[A-Za-z]?\)?", text))
        if len(words) > 8 and not is_equation_label:
            continue
        cluster |= bbox
    return cluster


def _page_equation_candidates(page: fitz.Page) -> list[dict[str, Any]]:
    page_dict = page.get_text("dict")
    lines: list[dict[str, Any]] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            raw_text = "".join(str(span.get("text") or "") for span in spans).strip()
            if raw_text:
                lines.append({"text": raw_text, "bbox": line.get("bbox") or block.get("bbox")})

    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        raw_text = line["text"]
        normalized = _normalized_equation(raw_text)
        candidate_kind = equation_candidate_kind(normalized)
        score = equation_candidate_score(normalized)
        if not candidate_kind or not score or normalized in seen:
            continue
        seen.add(normalized)
        seed_bbox = fitz.Rect(line["bbox"])
        seeds.append(
            {
                "raw_text": raw_text,
                "normalized_text": normalized,
                "candidate_kind": candidate_kind,
                "score": score,
                "seed_bbox": seed_bbox,
            }
        )

    candidates: list[dict[str, Any]] = []
    for seed in seeds:
        seed_bbox = seed["seed_bbox"]
        seed_center = (seed_bbox.y0 + seed_bbox.y1) / 2
        lane_neighbors = [
            other
            for other in seeds
            if other is not seed
            and abs(other["seed_bbox"].x0 - seed_bbox.x0) <= max(100.0, page.rect.width * 0.2)
        ]
        previous_centers = [
            (other["seed_bbox"].y0 + other["seed_bbox"].y1) / 2
            for other in lane_neighbors
            if (other["seed_bbox"].y0 + other["seed_bbox"].y1) / 2 < seed_center
        ]
        next_centers = [
            (other["seed_bbox"].y0 + other["seed_bbox"].y1) / 2
            for other in lane_neighbors
            if (other["seed_bbox"].y0 + other["seed_bbox"].y1) / 2 > seed_center
        ]
        y_min = (max(previous_centers) + seed_center) / 2 if previous_centers else None
        y_max = (min(next_centers) + seed_center) / 2 if next_centers else None
        bbox = (
            _formula_cluster_bbox(page, seed_bbox, lines, y_min=y_min, y_max=y_max)
            if seed["candidate_kind"] == "display_equation"
            else seed_bbox
        )
        label_match = EQUATION_LABEL_RE.search(seed["normalized_text"])
        candidates.append(
            {
                "raw_text": seed["raw_text"],
                "normalized_text": seed["normalized_text"],
                "candidate_kind": seed["candidate_kind"],
                "equation_label": label_match.group(1) if label_match else None,
                "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
                "confidence": seed["score"],
            }
        )
    return candidates


def _save_equation_crop(
    page: fitz.Page,
    bbox: list[float],
    target: Path,
) -> str | None:
    rect = fitz.Rect(bbox)
    rect.x0 = max(0, rect.x0 - 8)
    rect.y0 = max(0, rect.y0 - 2)
    rect.x1 = min(page.rect.width, rect.x1 + 8)
    rect.y1 = min(page.rect.height, rect.y1 + 2)
    if rect.width < 8 or rect.height < 5:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=rect, alpha=False)
    pixmap.save(target)
    return str(target)


def extract_equation_assets(
    limit_pdfs: int | None = None,
    reset_existing: bool = False,
    save_crops: bool = True,
    db_path: Path | None = None,
    progress_every: int = 20,
) -> dict[str, int]:
    output_root = PROJECT_ROOT / "data" / "equations"
    stats = {
        "pdfs": 0,
        "pages": 0,
        "equations": 0,
        "display_equations": 0,
        "inline_equations": 0,
        "failed_pdfs": 0,
        "crop_errors": 0,
    }
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pdf_equations (
                equation_id TEXT PRIMARY KEY, paper_id TEXT, pdf_id TEXT NOT NULL,
                page_number INTEGER NOT NULL, equation_index INTEGER NOT NULL,
                equation_label TEXT, raw_text TEXT NOT NULL, normalized_text TEXT NOT NULL,
                latex_text TEXT, variables_json TEXT NOT NULL DEFAULT '[]',
                candidate_kind TEXT NOT NULL DEFAULT 'display_equation',
                equation_role TEXT DEFAULT 'unknown', bbox_json TEXT, image_path TEXT,
                confidence REAL, extraction_method TEXT NOT NULL,
                extraction_status TEXT DEFAULT 'candidate', error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(pdf_id, page_number, equation_index)
            );
            CREATE TABLE IF NOT EXISTS pdf_equation_scans (
                pdf_id TEXT PRIMARY KEY, equation_count INTEGER NOT NULL DEFAULT 0,
                scan_status TEXT NOT NULL, error_message TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_pdf_equations_pdf_page
            ON pdf_equations(pdf_id, page_number);
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pdf_equations)")}
        if "candidate_kind" not in columns:
            conn.execute(
                "ALTER TABLE pdf_equations ADD COLUMN candidate_kind TEXT NOT NULL DEFAULT 'display_equation'"
            )
        rows = conn.execute(
            """
            SELECT pf.pdf_id, pf.paper_id, pf.file_path
            FROM pdf_files pf
            WHERE pf.parse_status = 'parsed' AND COALESCE(pf.is_duplicate, 0) = 0
              AND (? = 1 OR NOT EXISTS (
                  SELECT 1 FROM pdf_equation_scans es WHERE es.pdf_id = pf.pdf_id AND es.scan_status = 'complete'
              ))
            ORDER BY pf.file_name
            """,
            (int(reset_existing),),
        ).fetchall()
        if limit_pdfs is not None:
            rows = rows[:limit_pdfs]
        for row in rows:
            stats["pdfs"] += 1
            if reset_existing:
                conn.execute("DELETE FROM pdf_equations WHERE pdf_id = ?", (row["pdf_id"],))
                shutil.rmtree(output_root / row["pdf_id"], ignore_errors=True)
            equation_count = 0
            try:
                with fitz.open(row["file_path"]) as doc:
                    for page_offset, page in enumerate(doc, start=1):
                        stats["pages"] += 1
                        for index, candidate in enumerate(_page_equation_candidates(page), start=1):
                            image_path = None
                            if save_crops:
                                try:
                                    image_path = _save_equation_crop(
                                        page,
                                        candidate["bbox"],
                                        output_root / row["pdf_id"] / f"p{page_offset:04d}_eq{index:03d}.png",
                                    )
                                except Exception:
                                    stats["crop_errors"] += 1
                            equation_id = _equation_id(row["pdf_id"], page_offset, index)
                            conn.execute(
                                """
                                INSERT INTO pdf_equations (
                                    equation_id, paper_id, pdf_id, page_number, equation_index,
                                    equation_label, raw_text, normalized_text, variables_json,
                                    candidate_kind, equation_role, bbox_json, image_path, confidence,
                                    extraction_method, extraction_status
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(pdf_id, page_number, equation_index) DO UPDATE SET
                                    equation_label=excluded.equation_label,
                                    raw_text=excluded.raw_text,
                                    normalized_text=excluded.normalized_text,
                                    candidate_kind=excluded.candidate_kind,
                                    bbox_json=excluded.bbox_json,
                                    image_path=excluded.image_path,
                                    confidence=excluded.confidence,
                                    extraction_status='candidate',
                                    error_message=NULL
                                """,
                                (
                                    equation_id, row["paper_id"], row["pdf_id"], page_offset, index,
                                    candidate["equation_label"], candidate["raw_text"], candidate["normalized_text"],
                                    "[]", candidate["candidate_kind"], "unknown", json.dumps(candidate["bbox"]), image_path,
                                    candidate["confidence"], "pymupdf_math_candidate_v2", "candidate",
                                ),
                            )
                            equation_count += 1
                            stats["equations"] += 1
                            stats[candidate["candidate_kind"] + "s"] += 1
                conn.execute(
                    """
                    INSERT INTO pdf_equation_scans (pdf_id, equation_count, scan_status, error_message, updated_at)
                    VALUES (?, ?, 'complete', NULL, CURRENT_TIMESTAMP)
                    ON CONFLICT(pdf_id) DO UPDATE SET equation_count=excluded.equation_count,
                        scan_status='complete', error_message=NULL, updated_at=CURRENT_TIMESTAMP
                    """,
                    (row["pdf_id"], equation_count),
                )
            except Exception as exc:
                stats["failed_pdfs"] += 1
                conn.execute(
                    """
                    INSERT INTO pdf_equation_scans (pdf_id, equation_count, scan_status, error_message, updated_at)
                    VALUES (?, 0, 'error', ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(pdf_id) DO UPDATE SET scan_status='error', error_message=excluded.error_message,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (row["pdf_id"], str(exc)[:500]),
                )
            conn.commit()
            if progress_every and stats["pdfs"] % progress_every == 0:
                print(
                    "equations processed={pdfs} pages={pages} candidates={equations} failed={failed_pdfs}".format(**stats),
                    flush=True,
                )
    record_pipeline_run("55_extract_equation_assets", "ok", stats, db_path=db_path)
    return stats
