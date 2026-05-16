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


def test_rules_extract_2pr_with_dot_as_double_remanent():
    row = Row(
        {
            "paper_id": "paper_1",
            "pdf_id": "pdf_1",
            "chunk_id": "chunk_1",
            "page_number": 1,
            "text": "The HZO capacitor showed 2.Pr values of 60 uC/cm2 after optimization.",
        }
    )

    result = extract_chunk(row)

    assert result.properties[0].property_name == "double_remanent_polarization_2Pr"
    assert result.properties[0].value == 60


def test_rules_do_not_attach_parenthetical_2pr_value_to_pr():
    row = Row(
        {
            "paper_id": "paper_1",
            "pdf_id": "pdf_1",
            "chunk_id": "chunk_1",
            "page_number": 1,
            "text": "The paper reports record-high Pr (2Pr > 40 uC/cm2) in a thin HZO film.",
        }
    )

    result = extract_chunk(row)

    assert [prop.property_name for prop in result.properties] == [
        "double_remanent_polarization_2Pr"
    ]
