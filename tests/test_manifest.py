from __future__ import annotations

from pathlib import Path

import fitz

from backend.services.pdf_manifest import scan_pdfs, sha256_file


def make_pdf(path: Path, text: str = "HfO2 test PDF") -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_scan_pdfs_deduplicates_by_hash(tmp_path):
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.PDF"
    make_pdf(pdf_a)
    pdf_b.write_bytes(pdf_a.read_bytes())

    rows = scan_pdfs(tmp_path)

    assert len(rows) == 2
    assert rows[0].sha256 == rows[1].sha256
    assert sum(row.is_duplicate for row in rows) == 1
    assert any(row.duplicate_of_pdf_id for row in rows)


def test_sha256_file_is_stable(tmp_path):
    target = tmp_path / "sample.pdf"
    make_pdf(target, "stable content")

    assert sha256_file(target) == sha256_file(target)
