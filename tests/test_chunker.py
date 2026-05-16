from __future__ import annotations

from backend.services.chunker import make_chunks_for_page


def test_make_chunks_marks_high_value_hfo2_content():
    text = (
        "The 10 nm Hf0.5Zr0.5O2 film was deposited by ALD and annealed at 500 C. "
        "The ferroelectric capacitor showed 2Pr of 40 uC/cm2 after wake-up cycling. "
    ) * 10

    chunks = make_chunks_for_page("paper_1", "pdf_1", 1, text, 0)

    assert chunks
    assert any(chunk.contains_hfo2_keyword for chunk in chunks)
    assert any(chunk.contains_process_keyword for chunk in chunks)
    assert any(chunk.contains_property_keyword for chunk in chunks)
    assert any(chunk.is_high_value for chunk in chunks)
