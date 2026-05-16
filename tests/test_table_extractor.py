from __future__ import annotations

from backend.services.chunker import make_table_chunk
from backend.services.table_extractor import normalize_table, table_dimensions, table_to_text


def test_normalize_table_and_text():
    raw = [[" Material ", " 2Pr "], ["HZO", " 40 uC/cm2 "], [None, ""]]

    table = normalize_table(raw)

    assert table == [["Material", "2Pr"], ["HZO", "40 uC/cm2"]]
    assert table_dimensions(table) == (2, 2)
    assert table_to_text(table) == "Material | 2Pr\nHZO | 40 uC/cm2"


def test_make_table_chunk_marks_high_value():
    chunk = make_table_chunk(
        "paper_1",
        "pdf_1",
        3,
        1,
        "Material | process | 2Pr\nHf0.5Zr0.5O2 | ALD annealing | 40 uC/cm2",
        10,
    )

    assert chunk.section == "table"
    assert chunk.contains_table is True
    assert chunk.contains_hfo2_keyword is True
    assert chunk.contains_property_keyword is True
    assert chunk.is_high_value is True
