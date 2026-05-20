from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


DEFAULT_QUERIES = [
    "hafnium oxide ferroelectric HfO2",
    "HfO2 ferroelectric thin film",
    "Hf0.5Zr0.5O2 HZO ferroelectric",
    "doped hafnia ferroelectric",
    "HfO2 FeFET memory window",
]
DOMAIN_KEYWORDS = [
    "hfo2",
    "hafnium oxide",
    "hafnia",
    "hzo",
    "hfzro",
    "hfxzr",
    "ferroelectric",
    "orthorhombic",
    "pca21",
    "fefet",
    "pr",
    "2pr",
    "coercive",
    "remanent",
]


JsonFetcher = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class LiteratureCandidate:
    candidate_id: str
    doi: str | None
    title: str
    authors: str | None
    journal: str | None
    publisher: str | None
    year: int | None
    abstract: str | None
    source: str
    source_url: str | None
    oa_status: str | None
    oa_url: str | None
    pdf_url: str | None
    query: str
    match_score: float
    status: str = "discovered"
    notes: str | None = None


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "HfO2-FerroKG/0.1 (local literature discovery; mailto:local@example.com)"
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8", errors="replace")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return data


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = value[0] if isinstance(value, list) and value else value
    text = str(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = doi.strip()
    return doi or None


def _candidate_id(doi: str | None, title: str, year: int | None) -> str:
    key = doi or f"{title.lower()}::{year or ''}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"lit_{digest}"


def _extract_year(date_parts: Any) -> int | None:
    try:
        first = date_parts[0][0]
        return int(first)
    except Exception:
        return None


def _abstract_from_inverted_index(index: Any) -> str | None:
    if not isinstance(index, dict) or not index:
        return None
    positions: dict[int, str] = {}
    for token, token_positions in index.items():
        if not isinstance(token_positions, list):
            continue
        for position in token_positions:
            if isinstance(position, int):
                positions[position] = str(token)
    if not positions:
        return None
    return " ".join(positions[index] for index in sorted(positions))


def _domain_score(title: str, abstract: str | None) -> float:
    text = f"{title} {abstract or ''}".lower()
    hits = sum(1 for keyword in DOMAIN_KEYWORDS if keyword in text)
    score = min(1.0, hits / 6)
    if "ferroelectric" not in text:
        score *= 0.6
    if not any(keyword in text for keyword in ["hfo2", "hafnium oxide", "hafnia", "hzo", "hfzro"]):
        score *= 0.5
    return round(score, 3)


def _openalex_url(query: str, from_date: str, to_date: str, rows: int) -> str:
    params = {
        "search": query,
        "filter": f"from_publication_date:{from_date},to_publication_date:{to_date}",
        "per-page": str(rows),
        "sort": "publication_date:desc",
    }
    return "https://api.openalex.org/works?" + urllib.parse.urlencode(params)


def _crossref_url(query: str, from_date: str, to_date: str, rows: int) -> str:
    params = {
        "query.bibliographic": query,
        "filter": f"from-pub-date:{from_date},until-pub-date:{to_date},type:journal-article",
        "rows": str(rows),
        "sort": "published",
        "order": "desc",
    }
    return "https://api.crossref.org/works?" + urllib.parse.urlencode(params)


def _candidate_from_openalex(item: dict[str, Any], query: str) -> LiteratureCandidate | None:
    title = _clean_text(item.get("title") or item.get("display_name"))
    if not title:
        return None
    doi = _normalize_doi(item.get("doi"))
    abstract = _abstract_from_inverted_index(item.get("abstract_inverted_index"))
    authors = ", ".join(
        str(author.get("raw_author_name") or author.get("author", {}).get("display_name"))
        for author in item.get("authorships", [])[:12]
        if author.get("raw_author_name") or author.get("author", {}).get("display_name")
    )
    primary = item.get("primary_location") or {}
    source = primary.get("source") or {}
    open_access = item.get("open_access") or {}
    pdf_url = primary.get("pdf_url")
    return LiteratureCandidate(
        candidate_id=_candidate_id(doi, title, item.get("publication_year")),
        doi=doi,
        title=title,
        authors=authors or None,
        journal=_clean_text(source.get("display_name")),
        publisher=_clean_text(source.get("publisher")),
        year=item.get("publication_year"),
        abstract=abstract,
        source="openalex",
        source_url=item.get("id") or (f"https://doi.org/{doi}" if doi else None),
        oa_status=open_access.get("oa_status"),
        oa_url=open_access.get("oa_url"),
        pdf_url=pdf_url,
        query=query,
        match_score=_domain_score(title, abstract),
    )


def _candidate_from_crossref(item: dict[str, Any], query: str) -> LiteratureCandidate | None:
    title = _clean_text(item.get("title"))
    if not title:
        return None
    doi = _normalize_doi(item.get("DOI"))
    abstract = _clean_text(item.get("abstract"))
    authors = ", ".join(
        " ".join(
            part
            for part in [author.get("given"), author.get("family")]
            if isinstance(part, str) and part.strip()
        )
        for author in item.get("author", [])[:12]
    )
    pdf_url = None
    for link in item.get("link", []) or []:
        if link.get("content-type") == "application/pdf" and link.get("URL"):
            pdf_url = link["URL"]
            break
    year = _extract_year((item.get("published-print") or item.get("published-online") or {}).get("date-parts"))
    return LiteratureCandidate(
        candidate_id=_candidate_id(doi, title, year),
        doi=doi,
        title=title,
        authors=authors or None,
        journal=_clean_text(item.get("container-title")),
        publisher=_clean_text(item.get("publisher")),
        year=year,
        abstract=abstract,
        source="crossref",
        source_url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        oa_status=None,
        oa_url=None,
        pdf_url=pdf_url,
        query=query,
        match_score=_domain_score(title, abstract),
    )


def save_literature_candidates(
    candidates: list[LiteratureCandidate],
    output_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, str]:
    target_dir = output_dir or PROJECT_ROOT / "data" / "literature_candidates"
    target_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = target_dir / "literature_candidates.jsonl"
    csv_path = target_dir / "literature_candidates.csv"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for candidate in candidates:
            fh.write(json.dumps(asdict(candidate), ensure_ascii=False) + "\n")

    fieldnames = list(LiteratureCandidate.__dataclass_fields__)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(asdict(candidate))

    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO literature_candidates (
                candidate_id, doi, title, authors, journal, publisher, year, abstract,
                source, source_url, oa_status, oa_url, pdf_url, query, match_score, status, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                doi = excluded.doi,
                title = excluded.title,
                authors = excluded.authors,
                journal = excluded.journal,
                publisher = excluded.publisher,
                year = excluded.year,
                abstract = excluded.abstract,
                source = excluded.source,
                source_url = excluded.source_url,
                oa_status = excluded.oa_status,
                oa_url = excluded.oa_url,
                pdf_url = excluded.pdf_url,
                query = excluded.query,
                match_score = excluded.match_score,
                status = excluded.status,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                (
                    candidate.candidate_id,
                    candidate.doi,
                    candidate.title,
                    candidate.authors,
                    candidate.journal,
                    candidate.publisher,
                    candidate.year,
                    candidate.abstract,
                    candidate.source,
                    candidate.source_url,
                    candidate.oa_status,
                    candidate.oa_url,
                    candidate.pdf_url,
                    candidate.query,
                    candidate.match_score,
                    candidate.status,
                    candidate.notes,
                )
                for candidate in candidates
            ],
        )
        conn.commit()

    return {"jsonl": str(jsonl_path), "csv": str(csv_path)}


def discover_literature(
    queries: list[str] | None = None,
    from_date: str = "2024-01-01",
    to_date: str | None = None,
    rows_per_source: int = 25,
    include_openalex: bool = True,
    include_crossref: bool = True,
    min_score: float = 0.25,
    fetch_json: JsonFetcher = _fetch_json,
    output_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    selected_queries = queries or DEFAULT_QUERIES
    until = to_date or date.today().isoformat()
    candidates: dict[str, LiteratureCandidate] = {}
    errors: list[dict[str, str]] = []

    for query in selected_queries:
        if include_openalex:
            try:
                data = fetch_json(_openalex_url(query, from_date, until, rows_per_source))
                for item in data.get("results", []):
                    candidate = _candidate_from_openalex(item, query)
                    if candidate and candidate.match_score >= min_score:
                        key = candidate.doi or candidate.candidate_id
                        candidates[key] = candidate
            except Exception as exc:
                errors.append({"source": "openalex", "query": query, "error": str(exc)})
        if include_crossref:
            try:
                data = fetch_json(_crossref_url(query, from_date, until, rows_per_source))
                for item in data.get("message", {}).get("items", []):
                    candidate = _candidate_from_crossref(item, query)
                    if candidate and candidate.match_score >= min_score:
                        key = candidate.doi or candidate.candidate_id
                        candidates.setdefault(key, candidate)
            except Exception as exc:
                errors.append({"source": "crossref", "query": query, "error": str(exc)})

    ordered = sorted(
        candidates.values(),
        key=lambda item: (item.year or 0, item.match_score, item.title.lower()),
        reverse=True,
    )
    paths = save_literature_candidates(ordered, output_dir=output_dir, db_path=db_path)
    stats = {
        "queries": len(selected_queries),
        "from_date": from_date,
        "to_date": until,
        "candidates": len(ordered),
        "with_doi": sum(1 for item in ordered if item.doi),
        "with_oa_url": sum(1 for item in ordered if item.oa_url),
        "with_pdf_url": sum(1 for item in ordered if item.pdf_url),
        "errors": len(errors),
        "jsonl": paths["jsonl"],
        "csv": paths["csv"],
    }
    if errors:
        stats["error_samples"] = errors[:5]
    record_pipeline_run("12_discover_literature", "ok" if not errors else "partial", stats, db_path=db_path)
    return stats
