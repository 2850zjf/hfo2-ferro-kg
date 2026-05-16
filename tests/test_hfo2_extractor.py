from __future__ import annotations

from backend.services.hfo2_extractor import evidence_sentence, extract_chunk, preaudit_status


class Row(dict):
    def __getitem__(self, item):
        return super().__getitem__(item)


def test_evidence_sentence_expands_short_sentence():
    text = "The HZO capacitor was measured after wake-up cycling. Pr is 30 uC/cm2."

    start = text.index("Pr")
    evidence = evidence_sentence(text, start, start + 2)

    assert len(evidence) >= 40
    assert "wake-up" in evidence


def test_rules_preaudit_requires_contextual_evidence():
    row = Row(
        {
            "paper_id": "paper_1",
            "pdf_id": "pdf_1",
            "chunk_id": "chunk_1",
            "page_number": 1,
            "text": "The Hf0.5Zr0.5O2 capacitor was measured after wake-up cycling. Pr is 30 uC/cm2.",
        }
    )

    result = extract_chunk(row)
    status, confidence, warnings = preaudit_status(result, "rules")

    assert status == "preapproved_machine"
    assert confidence < 0.7
    assert warnings
