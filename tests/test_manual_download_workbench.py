from __future__ import annotations

from pathlib import Path

import fitz

from backend.db.init_db import init_database
from backend.services.manual_download_workbench import (
    prepare_manual_download_workbench,
    scan_manual_downloads,
)


def _write_curation_csv(path: Path) -> None:
    path.write_text(
        "\ufeff"
        + "\n".join(
            [
                "priority,download_batch,theme,title,year,venue,doi,download_url,pdf_url,landing_page_url,recommended_action,kg_value,why_include",
                "S 必下,第一批：论文主线必读/必下,奠基/标志性论文,Ferroelectricity in hafnium oxide thin films,2011,Applied Physics Letters,10.1063/1.3634052,https://doi.org/10.1063/1.3634052,,,用DOI/出版社页手动下载,Pr/2Pr,foundation",
                "A 强推荐,第二批：强相关补库,计算机制/物理约束,The origin of ferroelectricity in Hf1-xZrxO2,2015,Journal of Applied Physics,10.1063/1.4916707,https://arxiv.org/pdf/1507.00588,https://arxiv.org/pdf/1507.00588,,优先直接下载OA PDF,DFT,mechanism",
            ]
        ),
        encoding="utf-8",
    )


def _make_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_prepare_manual_download_workbench_writes_links_and_inbox(tmp_path):
    curation = tmp_path / "curation.csv"
    _write_curation_csv(curation)

    stats = prepare_manual_download_workbench(
        curation_csv=curation,
        workbench_dir=tmp_path / "workbench",
        inbox_dir=tmp_path / "inbox",
    )

    assert stats["all_items"] == 2
    assert stats["first_batch_items"] == 1
    assert Path(stats["first_batch_html"]).exists()
    assert Path(stats["checklist_csv"]).exists()
    assert (tmp_path / "inbox").exists()


def test_scan_manual_downloads_matches_pdf_without_copying(tmp_path):
    curation = tmp_path / "curation.csv"
    _write_curation_csv(curation)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _make_pdf(
        inbox / "ferroelectricity_in_hafnium_oxide_thin_films.pdf",
        "Ferroelectricity in hafnium oxide thin films doi 10.1063/1.3634052",
    )
    db_path = tmp_path / "hfo2.sqlite3"
    init_database(db_path)

    stats = scan_manual_downloads(
        curation_csv=curation,
        inbox_dir=inbox,
        output_dir=tmp_path / "workbench",
        db_path=db_path,
    )

    assert stats["pdfs_scanned"] == 1
    assert stats["copied"] == 0
    assert stats["status_counts"]["matched_ready"] == 1
    report = Path(stats["report"]).read_text(encoding="utf-8")
    assert "dry-run" in report
