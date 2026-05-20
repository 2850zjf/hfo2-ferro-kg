from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


HFO2_KEYWORDS = [
    "hfo2",
    "hzo",
    "hfzro",
    "hf0.5zr0.5o2",
    "hf1-xzrxo2",
    "hafnia",
    "hafnium oxide",
    "ferroelectric",
]
PROPERTY_KEYWORDS = [
    "2pr",
    "2.pr",
    " pr ",
    "remanent",
    "polarization",
    "ec",
    "coercive",
    "endurance",
    "retention",
    "wake-up",
    "fatigue",
    "leakage",
    "memory window",
    "switching",
    "breakdown",
    "dielectric constant",
    "pund",
]
PROCESS_KEYWORDS = [
    "ald",
    "atomic layer deposition",
    "sputter",
    "sputtering",
    "pld",
    "mocvd",
    "sol-gel",
    "anneal",
    "annealing",
    "rta",
    "pma",
    "pda",
    "tin",
    "electrode",
    "substrate",
    "thickness",
    "nm",
    "orthorhombic",
    "pca21",
    "tetragonal",
    "monoclinic",
    "xrd",
]


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    paper_id: str
    pdf_id: str
    page_number: int
    section: str
    chunk_index: int
    text: str
    char_count: int
    token_count: int
    contains_table: bool
    contains_formula: bool
    contains_hfo2_keyword: bool
    contains_process_keyword: bool
    contains_property_keyword: bool
    is_high_value: bool


def infer_section(text: str) -> str:
    head = text[:160].lower()
    if "abstract" in head:
        return "abstract"
    if "introduction" in head:
        return "introduction"
    if "method" in head or "experimental" in head:
        return "method"
    if "result" in head or "discussion" in head:
        return "results"
    if "conclusion" in head or "summary" in head:
        return "conclusion"
    if "references" in head:
        return "references"
    if head.startswith("fig.") or "figure" in head[:40]:
        return "figure_caption"
    return "unknown"


def has_any(text: str, keywords: list[str]) -> bool:
    padded = f" {text.lower()} "
    return any(keyword in padded for keyword in keywords)


def normalize_page_text(text: str) -> str:
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(
    text: str,
    target_min: int = 900,
    target_max: int = 1800,
    overlap_chars: int = 260,
) -> list[str]:
    text = normalize_page_text(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z])", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if not current:
            current = para
            continue
        if len(current) < target_min or len(current) + len(para) <= target_max:
            current = f"{current}\n{para}"
        else:
            chunks.append(current)
            overlap = current[-overlap_chars:].strip() if overlap_chars else ""
            current = f"{overlap}\n{para}" if overlap else para
    if current:
        chunks.append(current)
    return chunks


def make_chunks_for_page(
    paper_id: str, pdf_id: str, page_number: int, text: str, start_index: int
) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    for chunk_text in split_text(text):
        section = infer_section(chunk_text)
        if section == "references":
            continue
        offset = start_index + len(chunks)
        hfo2 = has_any(chunk_text, HFO2_KEYWORDS)
        process = has_any(chunk_text, PROCESS_KEYWORDS)
        prop = has_any(chunk_text, PROPERTY_KEYWORDS)
        contains_formula = bool(re.search(r"Hf[\w\.\-\s]*O\s*2|Hf0\.?5Zr0\.?5O2", chunk_text, re.I))
        chunks.append(
            DocumentChunk(
                chunk_id=f"{pdf_id}_c{offset:05d}",
                paper_id=paper_id,
                pdf_id=pdf_id,
                page_number=page_number,
                section=section,
                chunk_index=offset,
                text=chunk_text,
                char_count=len(chunk_text),
                token_count=max(1, len(chunk_text.split())),
                contains_table=False,
                contains_formula=contains_formula,
                contains_hfo2_keyword=hfo2,
                contains_process_keyword=process,
                contains_property_keyword=prop,
                is_high_value=(hfo2 or contains_formula) and (process or prop),
            )
        )
    return chunks


def make_table_chunk(
    paper_id: str,
    pdf_id: str,
    page_number: int,
    table_index: int,
    table_text: str,
    chunk_index: int,
) -> DocumentChunk:
    text = f"Table {table_index} on page {page_number}\n{table_text}"
    hfo2 = has_any(text, HFO2_KEYWORDS)
    process = has_any(text, PROCESS_KEYWORDS)
    prop = has_any(text, PROPERTY_KEYWORDS)
    contains_formula = bool(re.search(r"Hf[\w\.\-\s]*O\s*2|Hf0\.?5Zr0\.?5O2", text, re.I))
    return DocumentChunk(
        chunk_id=f"{pdf_id}_table_p{page_number:04d}_{table_index:03d}",
        paper_id=paper_id,
        pdf_id=pdf_id,
        page_number=page_number,
        section="table",
        chunk_index=chunk_index,
        text=text,
        char_count=len(text),
        token_count=max(1, len(text.split())),
        contains_table=True,
        contains_formula=contains_formula,
        contains_hfo2_keyword=hfo2,
        contains_process_keyword=process,
        contains_property_keyword=prop,
        is_high_value=(hfo2 or contains_formula) and (process or prop),
    )


def build_chunks(
    limit_pdfs: int | None = None,
    db_path: Path | None = None,
    reset_existing: bool = True,
) -> dict[str, int]:
    output_path = PROJECT_ROOT / "data" / "chunks" / "document_chunks.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats = {"pdfs": 0, "chunks": 0, "table_chunks": 0, "high_value_chunks": 0, "skipped_existing": 0}

    with connect(db_path) as conn:
        pdf_rows = conn.execute(
            """
            SELECT DISTINCT pp.pdf_id
            FROM parsed_pages pp
            WHERE (? = 1 OR NOT EXISTS (
                SELECT 1 FROM document_chunks dc WHERE dc.pdf_id = pp.pdf_id
            ))
            ORDER BY pp.pdf_id
            """,
            (int(reset_existing),),
        ).fetchall()
        if limit_pdfs is not None:
            pdf_rows = pdf_rows[:limit_pdfs]

        if reset_existing:
            conn.execute("DELETE FROM document_chunks")
        else:
            existing_count = conn.execute("SELECT COUNT(DISTINCT pdf_id) FROM document_chunks").fetchone()[0]
            stats["skipped_existing"] = int(existing_count)
        file_mode = "w" if reset_existing else "a"
        with output_path.open(file_mode, encoding="utf-8") as fh:
            for pdf_row in pdf_rows:
                stats["pdfs"] += 1
                pages = conn.execute(
                    """
                    SELECT paper_id, pdf_id, page_number, text
                    FROM parsed_pages
                    WHERE pdf_id = ?
                    ORDER BY page_number
                    """,
                    (pdf_row["pdf_id"],),
                ).fetchall()
                chunk_index = 0
                for page in pages:
                    chunks = make_chunks_for_page(
                        page["paper_id"],
                        page["pdf_id"],
                        page["page_number"],
                        page["text"],
                        chunk_index,
                    )
                    chunk_index += len(chunks)
                    conn.executemany(
                        """
                        INSERT INTO document_chunks (
                            chunk_id, paper_id, pdf_id, page_number, section, chunk_index,
                            text, char_count, token_count, contains_table, contains_formula,
                            contains_hfo2_keyword, contains_process_keyword,
                            contains_property_keyword, is_high_value
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                c.chunk_id,
                                c.paper_id,
                                c.pdf_id,
                                c.page_number,
                                c.section,
                                c.chunk_index,
                                c.text,
                                c.char_count,
                                c.token_count,
                                int(c.contains_table),
                                int(c.contains_formula),
                                int(c.contains_hfo2_keyword),
                                int(c.contains_process_keyword),
                                int(c.contains_property_keyword),
                                int(c.is_high_value),
                            )
                            for c in chunks
                        ],
                    )
                    for chunk in chunks:
                        fh.write(json.dumps(chunk.__dict__, ensure_ascii=False) + "\n")
                    stats["chunks"] += len(chunks)
                    stats["high_value_chunks"] += sum(1 for chunk in chunks if chunk.is_high_value)

                tables = conn.execute(
                    """
                    SELECT paper_id, pdf_id, page_number, table_index, table_text
                    FROM pdf_tables
                    WHERE pdf_id = ?
                    ORDER BY page_number, table_index
                    """,
                    (pdf_row["pdf_id"],),
                ).fetchall()
                table_chunks = [
                    make_table_chunk(
                        table["paper_id"],
                        table["pdf_id"],
                        table["page_number"],
                        table["table_index"],
                        table["table_text"],
                        chunk_index + offset,
                    )
                    for offset, table in enumerate(tables)
                ]
                if table_chunks:
                    conn.executemany(
                        """
                        INSERT INTO document_chunks (
                            chunk_id, paper_id, pdf_id, page_number, section, chunk_index,
                            text, char_count, token_count, contains_table, contains_formula,
                            contains_hfo2_keyword, contains_process_keyword,
                            contains_property_keyword, is_high_value
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                c.chunk_id,
                                c.paper_id,
                                c.pdf_id,
                                c.page_number,
                                c.section,
                                c.chunk_index,
                                c.text,
                                c.char_count,
                                c.token_count,
                                int(c.contains_table),
                                int(c.contains_formula),
                                int(c.contains_hfo2_keyword),
                                int(c.contains_process_keyword),
                                int(c.contains_property_keyword),
                                int(c.is_high_value),
                            )
                            for c in table_chunks
                        ],
                    )
                    for chunk in table_chunks:
                        fh.write(json.dumps(chunk.__dict__, ensure_ascii=False) + "\n")
                    stats["table_chunks"] += len(table_chunks)
                    stats["chunks"] += len(table_chunks)
                    stats["high_value_chunks"] += sum(1 for chunk in table_chunks if chunk.is_high_value)
            conn.commit()

    record_pipeline_run("04_chunk_documents", "ok", stats, db_path=db_path)
    return stats
