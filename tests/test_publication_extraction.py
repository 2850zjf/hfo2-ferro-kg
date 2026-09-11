from __future__ import annotations

import pandas as pd

from backend.db.init_db import init_database
from backend.db.session import connect
from backend.services.literature_relevance_screener import init_relevance_screening_tables
from backend.services.publication_extraction import _extraction_phase, build_publication_extraction_queue


def test_cited_comparison_table_is_routed_to_secondary_phase():
    row = {
        "section": "table",
        "is_review": 0,
        "text": (
            "HfO2 | 10 nm | orthorhombic | ALD | 21\n"
            "HZO | 12 nm | tetragonal | sputtering | 22\n"
            "Si:HfO2 | 9 nm | Pca21 | ALD | 23"
        ),
    }

    assert _extraction_phase(row, ["measurement"], "P0", 95) == "d_review_secondary"


def test_publication_queue_prioritizes_measurement_and_mechanism_evidence(tmp_path):
    db_path = tmp_path / "publication.sqlite3"
    init_database(db_path)
    init_relevance_screening_tables(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO papers (paper_id, title, paper_type, is_review) VALUES (?, ?, ?, ?)",
            ("paper_1", "HZO reliability", "experimental", 0),
        )
        conn.execute(
            """
            INSERT INTO pdf_files (
                pdf_id, file_name, file_path, sha256, file_size, parse_status,
                is_duplicate, paper_id, parse_quality_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("pdf_1", "paper.pdf", "/tmp/paper.pdf", "abc", 1000, "parsed", 0, "paper_1", 1.0),
        )
        conn.execute(
            """
            INSERT INTO literature_relevance_screenings (
                screening_id, paper_id, pdf_id, rule_tier, rule_reasons_json,
                llm_reasons_json, final_tier, extraction_policy,
                evidence_roles_json, risk_flags_json, llm_usage_json,
                screening_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("screen_1", "paper_1", "pdf_1", "core", "[]", "[]", "core", "benchmark_full", "[]", "[]", "{}", "v1"),
        )
        chunks = [
            ("chunk_measurement", 0, "results", "The HZO capacitor retained 2Pr of 35 uC/cm2 after 10^9 endurance cycles. The pulse width, stress voltage, and measurement frequency were reported for the same sample.", 1, 1, 1, 1),
            ("chunk_mechanism", 1, "discussion", "DFT indicates that oxygen vacancies alter orthorhombic phase stability and the energy barrier. The calculation compares the polar and monoclinic reference structures under the same boundary conditions.", 0, 1, 0, 1),
        ]
        for chunk_id, index, section, text, table, hfo2, process, prop in chunks:
            conn.execute(
                """
                INSERT INTO document_chunks (
                    chunk_id, paper_id, pdf_id, page_number, section, chunk_index,
                    text, char_count, token_count, contains_table,
                    contains_hfo2_keyword, contains_process_keyword,
                    contains_property_keyword, is_high_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (chunk_id, "paper_1", "pdf_1", 1, section, index, text, len(text), len(text.split()), table, hfo2, process, prop, 1),
            )
        conn.commit()

    stats = build_publication_extraction_queue(
        output_dir=tmp_path / "queue",
        shard_size=1,
        smoke_per_lane=1,
        db_path=db_path,
    )
    queue = pd.read_csv(stats["queue_path"])

    assert stats["chunks"] == 2
    assert queue.iloc[0]["chunk_id"] == "chunk_measurement"
    assert set(queue["lane"]) == {"measurement_reliability", "mechanism_computation"}
    assert stats["shards"] == 2
