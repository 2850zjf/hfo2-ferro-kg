from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import fitz

from backend.core.config import PROJECT_ROOT
from backend.db.session import connect
from backend.services.pipeline_log import record_pipeline_run


SAFE_OA_STATUSES = {"gold", "hybrid", "green", "bronze", "diamond"}


@dataclass(frozen=True)
class DownloadResult:
    candidate_id: str
    title: str
    pdf_url: str | None
    status: str
    file_path: str | None = None
    sha256: str | None = None
    file_size: int | None = None
    page_count: int | None = None
    quality_score: float = 0
    error: str | None = None


def _sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(title: str, doi: str | None, year: int | None) -> str:
    slug_source = doi or title
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug_source.strip())
    slug = re.sub(r"_+", "_", slug).strip("._-")
    slug = slug[:120] or "open_access_paper"
    prefix = f"{year}_" if year else ""
    return f"{prefix}{slug}.pdf"


def _looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def _page_count(path: Path) -> int:
    with fitz.open(path) as doc:
        return int(doc.page_count)


def _quality_score(file_size: int, page_count: int) -> float:
    score = 0.4
    if file_size >= 200_000:
        score += 0.25
    if file_size >= 800_000:
        score += 0.15
    if page_count >= 3:
        score += 0.1
    if page_count >= 6:
        score += 0.1
    return round(min(score, 1.0), 3)


def _download(url: str, target: Path, timeout: int = 60) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "HfO2-FerroKG/0.1 (open-access PDF downloader; local research use)",
            "Accept": "application/pdf,text/html;q=0.8,*/*;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        parsed = urllib.parse.urlparse(final_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http/https PDF URLs are supported.")
        content = response.read()
    target.write_bytes(content)


def _is_safe_open_access(row) -> bool:
    oa_status = (row["oa_status"] or "").lower()
    if row["source"] == "openalex" and oa_status in SAFE_OA_STATUSES:
        return True
    if row["oa_url"] and oa_status in SAFE_OA_STATUSES:
        return True
    return False


def download_open_access_pdfs(
    limit: int | None = None,
    min_score: float = 0.45,
    output_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    target_dir = output_dir or PROJECT_ROOT / "data" / "raw_pdfs" / "open_access"
    target_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        "eligible": 0,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "duplicate": 0,
        "low_quality": 0,
    }

    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT candidate_id, doi, title, year, source, oa_status, oa_url, pdf_url,
                   match_score, download_status
            FROM literature_candidates
            WHERE pdf_url IS NOT NULL
              AND match_score >= ?
              AND COALESCE(download_status, 'not_downloaded') = 'not_downloaded'
            ORDER BY match_score DESC, year DESC, title
            """,
            (min_score,),
        ).fetchall()
        if limit is not None:
            rows = rows[:limit]

        existing_hashes = {
            row["sha256"]
            for row in conn.execute("SELECT sha256 FROM pdf_files").fetchall()
            if row["sha256"]
        }

        for row in rows:
            stats["eligible"] += 1
            candidate_id = row["candidate_id"]
            if not _is_safe_open_access(row):
                stats["skipped"] += 1
                conn.execute(
                    """
                    UPDATE literature_candidates
                    SET download_status = 'skipped_not_verified_oa',
                        download_error = 'Open-access status is not strong enough for automatic PDF download.',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE candidate_id = ?
                    """,
                    (candidate_id,),
                )
                continue

            target = target_dir / _safe_filename(row["title"], row["doi"], row["year"])
            try:
                _download(row["pdf_url"], target)
                if not _looks_like_pdf(target):
                    target.unlink(missing_ok=True)
                    raise ValueError("Downloaded file is not a PDF.")
                file_size = target.stat().st_size
                page_count = _page_count(target)
                sha256 = _sha256_file(target)
                quality = _quality_score(file_size, page_count)
                if sha256 in existing_hashes:
                    target.unlink(missing_ok=True)
                    stats["duplicate"] += 1
                    conn.execute(
                        """
                        UPDATE literature_candidates
                        SET download_status = 'duplicate',
                            downloaded_pdf_sha256 = ?,
                            download_quality_score = ?,
                            download_error = 'PDF hash already exists in local corpus.',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE candidate_id = ?
                        """,
                        (sha256, quality, candidate_id),
                    )
                    continue
                if quality < 0.65:
                    stats["low_quality"] += 1
                    conn.execute(
                        """
                        UPDATE literature_candidates
                        SET download_status = 'downloaded_low_quality',
                            downloaded_pdf_path = ?,
                            downloaded_pdf_sha256 = ?,
                            downloaded_pdf_pages = ?,
                            downloaded_pdf_size = ?,
                            download_quality_score = ?,
                            download_error = 'PDF downloaded but quality score is below threshold.',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE candidate_id = ?
                        """,
                        (str(target.resolve()), sha256, page_count, file_size, quality, candidate_id),
                    )
                    continue

                existing_hashes.add(sha256)
                stats["downloaded"] += 1
                conn.execute(
                    """
                    UPDATE literature_candidates
                    SET download_status = 'downloaded',
                        downloaded_pdf_path = ?,
                        downloaded_pdf_sha256 = ?,
                        downloaded_pdf_pages = ?,
                        downloaded_pdf_size = ?,
                        download_quality_score = ?,
                        download_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE candidate_id = ?
                    """,
                    (str(target.resolve()), sha256, page_count, file_size, quality, candidate_id),
                )
            except (OSError, ValueError, urllib.error.URLError) as exc:
                target.unlink(missing_ok=True)
                stats["failed"] += 1
                conn.execute(
                    """
                    UPDATE literature_candidates
                    SET download_status = 'download_failed',
                        download_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE candidate_id = ?
                    """,
                    (str(exc)[:500], candidate_id),
                )
        conn.commit()

    record_pipeline_run("13_download_open_access_pdfs", "ok", stats, db_path=db_path)
    return stats
