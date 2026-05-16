from __future__ import annotations

from pathlib import Path

import fitz

from backend.services.pdf_parser import (
    classify_paper_type,
    extract_initial_metadata,
    normalize_doi,
    parse_pdf,
    title_from_filename,
    year_from_filename,
)


def make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_extract_initial_metadata_finds_doi_and_year():
    metadata = extract_initial_metadata(
        "Fast Switching in Hf0.5Zr0.5O2 Films\nDOI: 10.1234/ABC.def. 2025",
        "fallback",
    )

    assert metadata["doi"] == "10.1234/abc.def"
    assert metadata["year"] == 2025
    assert metadata["title"] == "Fast Switching in Hf0.5Zr0.5O2 Films"


def test_filename_helpers():
    path = Path("2026_Nat_Commun_Precise_structure_polarization.pdf")

    assert year_from_filename(path) == 2026
    assert title_from_filename(path) == "Nat Commun Precise structure polarization"
    assert normalize_doi("10.1000/XYZ.)") == "10.1000/xyz"


def test_parse_pdf_quality_and_paper_type(tmp_path):
    pdf_path = tmp_path / "2025_review_hafnia.pdf"
    make_pdf(
        pdf_path,
        "Review of HfO2 ferroelectric films\n10.5555/HFO2.TEST\nThis 2025 review discusses HZO.",
    )

    pages, metadata = parse_pdf("pdf_1", "paper_1", pdf_path)

    assert len(pages) == 1
    assert metadata["doi"] == "10.5555/hfo2.test"
    assert metadata["year"] == 2025
    assert metadata["parse_quality_score"] == 1
    assert metadata["paper_type"] == "review"
    assert metadata["is_review"] is True


def test_classify_paper_type():
    assert classify_paper_type("A theoretical perspective on hafnia", "")[0] == "theoretical"
    assert classify_paper_type("Phase-field simulation of HZO", "")[0] == "computational"
