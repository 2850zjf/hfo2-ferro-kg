from __future__ import annotations

import csv
import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


BAD_TITLE_RE = re.compile(
    r"("
    r"^abstract\b|^keywords:|^received:|^accepted:|^revised:|^published\b|^copyright\b|"
    r"^doi\b|https?://|^www\.|^article$|^research article$|^article in press$|"
    r"^check for updates$|^advanced functional materials$|^nature communications$|"
    r"^communications materials$|^npj\s*\|?\s*computational materials$|"
    r"^mrs bulletin|^information sciences$|^hal id:|^open access$|"
    r"^cite this article|^we are providing an unedited version"
    r")",
    re.IGNORECASE,
)

SOURCE_TOKEN_PREFIXES = {
    "acs",
    "adv",
    "advanced",
    "aelm",
    "appl",
    "applied",
    "composites",
    "commun",
    "communications",
    "computational",
    "electron",
    "electronics",
    "funct",
    "functional",
    "hal",
    "ieee",
    "iedm",
    "int",
    "iranica",
    "j",
    "journal",
    "lett",
    "mater",
    "materials",
    "mrs",
    "nano",
    "nanolett",
    "nat",
    "nature",
    "npj",
    "phys",
    "scientia",
    "proceedings",
    "sci",
    "science",
    "small",
    "solid",
    "state",
}


@dataclass(frozen=True)
class TitleCandidate:
    title: str
    source: str
    score: float


@dataclass(frozen=True)
class TitleRepairRow:
    paper_id: str
    pdf_id: str
    file_name: str
    old_title: str | None
    new_title: str
    source: str
    score: float
    changed: bool


def clean_title(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<\s*sub\s*>(.*?)<\s*/\s*sub\s*>", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/?\s*i\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\\text\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", text)
    text = re.sub(r"_\{([^}]*)\}", r"\1", text)
    text = text.replace("$", "")
    text = re.sub(
        r"\s+at\s+arxiv:\S+\s+\[[^\]]+\]\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+",
        " at ",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("\x00", " ")
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
    text = re.sub(r"\bfi\s+lm(s?)\b", r"film\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bfl\s+exible\b", "flexible", text, flags=re.IGNORECASE)
    text = re.sub(r"\barti\s+fi\s+cial\b", "artificial", text, flags=re.IGNORECASE)
    text = re.sub(r"\be\s+ff\s+ect(s?)\b", r"effect\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdetention insights\b", "retention insights", text, flags=re.IGNORECASE)
    text = text.replace("\u00a0", " ").replace("\u2002", " ").replace("\u2003", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([:;,.)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    text = re.sub(r"\b(Hf|Zr|Ce|Si|Al|La|Y|Gd|Sr|Ti)\s+([0-9]+(?:\.[0-9]+)?)\b", r"\1\2", text)
    text = re.sub(r"\b(Hf|Zr|Ce|Si|Al|La|Y|Gd|Sr|Ti)O\s+([0-9])\b", r"\1O\2", text)
    text = re.sub(r"\bO\s+([0-9])\b", r"O\1", text)
    text = re.sub(r"\bWO\s*3\s*[–−-]\s*x\b", "WO3-x", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHf0\s+5Zr0\s+5O2\b", "Hf0.5Zr0.5O2", text)
    text = re.sub(r"\bHf0\.5\s+Zr0\.5\s+O2\b", "Hf0.5Zr0.5O2", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return text.strip(" .,\t\n\r")


def title_score(title: str | None) -> float:
    if not title:
        return 0
    title = clean_title(title)
    if not (18 <= len(title) <= 260):
        return 0
    if BAD_TITLE_RE.search(title):
        return 0
    if title[:1].islower():
        return 0
    if re.search(r"\b(of|for|in|with|and|to|from|by|via|toward|towards|using|on|the|a|an|or|as|at|for the|:)$", title, re.I):
        return 0
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]*", title)
    if len(words) < 4:
        return 0
    score = min(len(title), 180) / 3 + min(len(words), 24) * 2
    lower = title.lower()
    for keyword in ["ferroelectric", "hfo", "hfo2", "hafnium", "hafnia", "hzo", "zro", "memory", "thin film"]:
        if keyword in lower:
            score += 8
    if re.search(r"\b(et al|received|accepted|correspondence|copyright|volume|issue)\b", lower):
        score -= 25
    if re.search(r"\d{4}", title) and len(title) < 80:
        score -= 20
    return max(score, 0)


def _line_records(page: fitz.Page) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = clean_title(" ".join(span.get("text", "") for span in spans))
            if not text:
                continue
            bbox = line.get("bbox", [0, 0, 0, 0])
            records.append(
                {
                    "text": text,
                    "x0": float(bbox[0]),
                    "y0": float(bbox[1]),
                    "size": max(float(span.get("size", 0)) for span in spans),
                }
            )
    return sorted(records, key=lambda row: (row["y0"], row["x0"]))


def _is_title_line(text: str) -> bool:
    text = clean_title(text)
    if not (6 <= len(text) <= 180):
        return False
    if BAD_TITLE_RE.search(text):
        return False
    if not re.search(r"[A-Za-z]", text):
        return False
    if re.search(r"\b[A-Z][a-z]+,\s+[A-Z]\.", text):
        return False
    return True


def title_from_layout(pdf_path: Path) -> TitleCandidate | None:
    try:
        with fitz.open(pdf_path) as doc:
            if doc.page_count == 0:
                return None
            page = doc[0]
            lines = [
                row
                for row in _line_records(page)
                if row["y0"] < page.rect.height * 0.72 and _is_title_line(row["text"])
            ]
    except Exception:
        return None

    if not lines:
        return None
    plausible_sizes = [row["size"] for row in lines if not BAD_TITLE_RE.search(row["text"])]
    if not plausible_sizes:
        return None
    max_size = max(plausible_sizes)
    threshold = max(12.0, max_size - (1.2 if max_size >= 16 else 2.5))

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_y: float | None = None
    for row in lines:
        if row["size"] < threshold:
            if current:
                groups.append(current)
                current = []
            continue
        if current and previous_y is not None and row["y0"] - previous_y > 48:
            groups.append(current)
            current = []
        current.append(row)
        previous_y = row["y0"]
    if current:
        groups.append(current)

    candidates: list[TitleCandidate] = []
    for group in groups:
        title = clean_title(" ".join(row["text"] for row in group))
        score = title_score(title) + sum(row["size"] for row in group) / max(len(group), 1)
        if score > 0:
            candidates.append(TitleCandidate(title=title, source="layout", score=score))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item.score)


def title_from_metadata(pdf_path: Path) -> TitleCandidate | None:
    try:
        with fitz.open(pdf_path) as doc:
            raw = (doc.metadata or {}).get("title")
    except Exception:
        return None
    title = clean_title(raw or "")
    score = title_score(title)
    if score == 0:
        return None
    return TitleCandidate(title=title, source="metadata", score=score + 5)


def title_from_filename(file_name: str) -> TitleCandidate | None:
    stem = Path(file_name).stem
    parts = [part for part in re.split(r"[_\s]+", stem) if part]
    parts = [part for part in parts if not re.fullmatch(r"20[12]\d", part)]
    while parts and parts[0].lower() in SOURCE_TOKEN_PREFIXES:
        parts.pop(0)
    title = clean_title(" ".join(parts))
    score = title_score(title)
    if score == 0:
        return None
    return TitleCandidate(title=title, source="filename", score=score - 8)


def title_from_doi(doi: str | None, timeout: int = 12) -> TitleCandidate | None:
    if not doi:
        return None
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HfO2-FerroKG local metadata repair (mailto:local@example.invalid)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except Exception:
        return None
    titles = payload.get("message", {}).get("title") or []
    if not titles:
        return None
    title = clean_title(str(titles[0]))
    score = title_score(title)
    if score == 0:
        return None
    return TitleCandidate(title=title, source="doi", score=score + 25)


def best_title_for_pdf(
    pdf_path: Path,
    file_name: str,
    doi: str | None = None,
    use_doi: bool = False,
) -> TitleCandidate | None:
    if use_doi:
        doi_candidate = title_from_doi(doi)
        if doi_candidate and doi_candidate.score >= 45:
            return doi_candidate
    layout = title_from_layout(pdf_path)
    if layout and layout.score >= 60:
        return layout
    metadata = title_from_metadata(pdf_path)
    if metadata and metadata.score >= 45:
        return metadata
    if layout and layout.score >= 45:
        return layout
    return title_from_filename(file_name)


def repair_paper_titles(
    output_path: Path | None = None,
    db_path: Path | None = None,
    dry_run: bool = False,
    use_doi: bool = False,
) -> dict[str, int | str]:
    target = output_path or PROJECT_ROOT / "data" / "exports" / "paper_title_repair_report.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    repairs: list[TitleRepairRow] = []

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.paper_id, p.title, p.doi, pf.pdf_id, pf.file_name, pf.file_path
            FROM papers p
            JOIN pdf_files pf ON pf.paper_id = p.paper_id
            ORDER BY pf.file_name
            """
        ).fetchall()
        for row in rows:
            candidate = best_title_for_pdf(
                Path(row["file_path"]),
                row["file_name"],
                doi=row["doi"],
                use_doi=use_doi,
            )
            if candidate is None:
                continue
            old_title = row["title"]
            old_score = title_score(old_title)
            changed = (old_title or "") != candidate.title and (
                candidate.source != "filename" or old_score == 0
            ) and candidate.score >= 45
            repairs.append(
                TitleRepairRow(
                    paper_id=row["paper_id"],
                    pdf_id=row["pdf_id"],
                    file_name=row["file_name"],
                    old_title=old_title,
                    new_title=candidate.title,
                    source=candidate.source,
                    score=round(candidate.score, 2),
                    changed=changed,
                )
            )
            if changed and not dry_run:
                conn.execute(
                    "UPDATE papers SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE paper_id = ?",
                    (candidate.title, row["paper_id"]),
                )
        if not dry_run:
            conn.commit()

    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(TitleRepairRow.__dataclass_fields__))
        writer.writeheader()
        for row in repairs:
            writer.writerow(row.__dict__)

    stats = {
        "papers_seen": len(repairs),
        "titles_changed": sum(row.changed for row in repairs),
        "report_path": str(target),
        "used_doi_metadata": int(use_doi),
    }
    if not dry_run:
        record_pipeline_run("11_repair_paper_titles", "ok", stats, db_path=db_path)
    return stats
