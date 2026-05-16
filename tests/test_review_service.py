from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.review_service import (
    export_approved_facts,
    get_pdf_viewer_record,
    list_review_facts,
    open_pdf_in_default_browser,
    pdf_file_url,
    pdf_viewer_url,
    update_review_status,
)


def test_review_service_updates_status_and_exports_approved(tmp_path):
    db_path = tmp_path / "review.sqlite3"
    init_database(db_path)
    payload = {
        "material": {"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        "sample": {"device_stack": "TiN/HZO/TiN"},
        "property": {
            "property_name": "double_remanent_polarization_2Pr",
            "raw_property_name": "2Pr",
            "value": 40,
            "unit": "μC/cm²",
            "normalized_value": 40,
            "normalized_unit": "μC/cm²",
            "confidence": 0.8,
            "evidence_text": "The 2Pr value was 40 μC/cm².",
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO reviewed_facts (
                fact_id, paper_id, pdf_id, chunk_id, page_number, fact_type,
                payload_json, review_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fact_1",
                "paper_1",
                "pdf_1",
                "chunk_1",
                4,
                "ferroelectric_property",
                json.dumps(payload),
                "needs_human_review",
            ),
        )
        conn.commit()

    update_review_status("fact_1", "approved", "checked against source", db_path=db_path)
    facts = list_review_facts(status="approved", db_path=db_path)
    export_path = export_approved_facts(tmp_path / "approved.csv", db_path=db_path)

    assert facts[0]["review_status"] == "approved"
    assert facts[0]["property_name"] == "double_remanent_polarization_2Pr"
    assert facts[0]["material_system"] == "HZO"
    assert facts[0]["electrode_stack"] == "TiN/HZO/TiN"
    assert facts[0]["context_quality"] in {"weak", "partial", "strong"}
    assert "ontology_context" in facts[0]
    assert "pdf_path" in facts[0]
    assert "checked against source" in export_path.read_text(encoding="utf-8-sig")


def test_review_service_rejects_unknown_status(tmp_path):
    db_path = tmp_path / "review.sqlite3"
    init_database(db_path)

    with pytest.raises(ValueError):
        update_review_status("missing", "published", db_path=db_path)


def test_pdf_file_url_points_to_local_page(tmp_path):
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    url = pdf_file_url(str(pdf_path), page_number=3)

    assert url is not None
    assert url.startswith("file:///")
    assert url.endswith("#page=3")


def test_pdf_viewer_url_uses_pdf_id_not_local_path():
    url = pdf_viewer_url("pdf_1", page_number=3, fact_id="fact_1")

    assert url == "/PDF_原文预览?pdf_id=pdf_1&page=3&fact_id=fact_1"


def test_get_pdf_viewer_record_reports_safe_existing_pdf(tmp_path, monkeypatch):
    db_path = tmp_path / "review.sqlite3"
    pdf_root = tmp_path / "raw_pdfs"
    pdf_root.mkdir()
    pdf_path = pdf_root / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(
        "backend.services.review_service.get_settings",
        lambda: SimpleNamespace(pdf_root=pdf_root),
    )
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO papers (paper_id, title, year)
            VALUES (?, ?, ?)
            """,
            ("paper_1", "Ferroelectric HfO2 Test Paper", 2026),
        )
        conn.execute(
            """
            INSERT INTO pdf_files (
                pdf_id, file_name, file_path, sha256, file_size, paper_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("pdf_1", pdf_path.name, str(pdf_path), "abc", pdf_path.stat().st_size, "paper_1"),
        )
        conn.commit()

    record = get_pdf_viewer_record("pdf_1", db_path=db_path)

    assert record is not None
    assert record["exists"] is True
    assert record["is_safe_path"] is True
    assert record["paper_title"] == "Ferroelectric HfO2 Test Paper"


def test_open_pdf_in_default_browser_uses_local_file_url(tmp_path, monkeypatch):
    db_path = tmp_path / "review.sqlite3"
    pdf_root = tmp_path / "raw_pdfs"
    pdf_root.mkdir()
    pdf_path = pdf_root / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(
        "backend.services.review_service.get_settings",
        lambda: SimpleNamespace(pdf_root=pdf_root),
    )
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pdf_files (
                pdf_id, file_name, file_path, sha256, file_size
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            ("pdf_1", pdf_path.name, str(pdf_path), "abc", pdf_path.stat().st_size),
        )
        conn.commit()

    opened_urls: list[str] = []
    monkeypatch.setattr(
        "backend.services.review_service.webbrowser.open",
        lambda url, new=0, autoraise=True: opened_urls.append(url) or True,
    )

    result = open_pdf_in_default_browser("pdf_1", page_number=5, db_path=db_path)

    assert result["ok"] is True
    assert opened_urls == [f"{pdf_path.resolve().as_uri()}#page=5"]
