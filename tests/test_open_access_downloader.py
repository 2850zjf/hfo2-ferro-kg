from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.open_access_downloader import download_open_access_pdfs


def test_downloader_skips_non_verified_oa_candidate(tmp_path):
    db_path = tmp_path / "hfo2.sqlite3"
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO literature_candidates (
                candidate_id, title, source, pdf_url, match_score, oa_status
            )
            VALUES ('lit_1', 'HfO2 ferroelectric test', 'crossref', 'https://example.org/a.pdf', 0.9, NULL)
            """
        )
        conn.commit()

    stats = download_open_access_pdfs(db_path=db_path, output_dir=tmp_path / "pdfs")

    assert stats["eligible"] == 1
    assert stats["skipped"] == 1
    with connect(db_path) as conn:
        status = conn.execute(
            "SELECT download_status FROM literature_candidates WHERE candidate_id = 'lit_1'"
        ).fetchone()[0]
    assert status == "skipped_not_verified_oa"
