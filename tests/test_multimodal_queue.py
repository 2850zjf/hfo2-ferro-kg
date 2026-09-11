from __future__ import annotations

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.multimodal_queue import (
    build_multimodal_queue,
    resolve_local_asset_path,
    table_asset_quality,
)


def test_build_multimodal_queue_keeps_three_asset_types(tmp_path):
    db_path = tmp_path / "multimodal.sqlite3"
    image_path = tmp_path / "figure.png"
    equation_path = tmp_path / "equation.png"
    image_path.write_bytes(b"figure")
    equation_path.write_bytes(b"equation")
    init_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, paper_type, is_review) VALUES ('paper_1', 'HZO study', 'experimental', 0)"
        )
        conn.execute(
            """
            INSERT INTO pdf_files (
                pdf_id, paper_id, file_name, file_path, sha256, file_size,
                parse_status, is_duplicate, ocr_needed
            ) VALUES ('pdf_1', 'paper_1', 'study.pdf', ?, 'sha', 1, 'parsed', 0, 0)
            """,
            (str(tmp_path / "study.pdf"),),
        )
        conn.execute(
            """
            INSERT INTO parsed_pages (page_id, paper_id, pdf_id, page_number, text, char_count)
            VALUES ('page_1', 'paper_1', 'pdf_1', 1,
                    'HZO remanent polarization and endurance measured after annealing.', 66)
            """
        )
        conn.execute(
            """
            INSERT INTO pdf_visual_assets (
                asset_id, paper_id, pdf_id, page_number, asset_type, asset_index,
                file_path, width, height, extraction_status
            ) VALUES ('fig_1', 'paper_1', 'pdf_1', 1, 'image', 1, ?, 600, 400, 'ok')
            """,
            (str(image_path),),
        )
        conn.execute(
            """
            INSERT INTO pdf_tables (
                table_id, paper_id, pdf_id, page_number, table_index, row_count,
                col_count, table_json, table_text
            ) VALUES ('table_1', 'paper_1', 'pdf_1', 1, 1, 3, 3, '[]',
                      'sample | thickness | Pr\nHZO-A | 10 nm | 20 μC/cm²\nHZO-B | 8 nm | 25 μC/cm²')
            """
        )
        conn.execute(
            """
            INSERT INTO pdf_equations (
                equation_id, paper_id, pdf_id, page_number, equation_index,
                raw_text, normalized_text, candidate_kind, image_path,
                extraction_method, extraction_status
            ) VALUES ('eq_1', 'paper_1', 'pdf_1', 1, 1, 'F = U - TS',
                      'F = U - TS', 'display_equation', ?, 'test', 'candidate')
            """,
            (str(equation_path),),
        )
        conn.commit()

    stats = build_multimodal_queue(output_dir=tmp_path / "queue", db_path=db_path)

    assert stats["source_counts"] == {"figure": 1, "table": 1, "equation": 1}
    assert stats["status_counts"]["queued"] == 3
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM multimodal_asset_queue").fetchone()[0] == 3


def test_resolve_local_asset_path_maps_historical_windows_path(monkeypatch, tmp_path):
    figure = tmp_path / "data" / "figures" / "pdf_1" / "figure.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"figure")
    monkeypatch.setattr("backend.services.multimodal_queue.PROJECT_ROOT", tmp_path)

    resolved = resolve_local_asset_path(r"D:\old\project\data\figures\pdf_1\figure.png")

    assert resolved == str(figure.resolve())


def test_table_asset_quality_rejects_layout_fragments():
    quality, reason = table_asset_quality(1, 2, "Journal of Applied Physics | PERSPECTIVE")
    assert quality < 0.35
    assert reason == "degenerate_dimensions"

    quality, reason = table_asset_quality(4, 3, "Sample | thickness | Pr HZO | 10 | 20 HZO | 8 | 25")
    assert quality >= 0.35
    assert reason == "structured_table"
