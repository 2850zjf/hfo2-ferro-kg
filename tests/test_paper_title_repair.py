from __future__ import annotations

import fitz

from backend.services.paper_title_repair import clean_title, title_from_layout, title_score


def test_clean_title_fixes_ligatures_and_formula_spacing():
    assert clean_title("Artiﬁcial HfO 2 / ZrO 2 superlattice") == "Artificial HfO2/ZrO2 superlattice"
    assert clean_title("Hf<sub>0.5</sub>Zr<sub>0.5</sub>O<sub>2</sub> film") == "Hf0.5Zr0.5O2 film"
    assert clean_title(r"$\text{Hf}_{0.5}\text{Zr}_{0.5}\mathrm{O}_{2}$ FeRAM") == "Hf0.5Zr0.5O2 FeRAM"
    assert clean_title("WO <sub> 3– <i>x</i> </sub> layer") == "WO3-x layer"


def test_title_score_rejects_journal_headers():
    assert title_score("Advanced Functional Materials") == 0
    assert title_score("Received: 1 April 2025") == 0
    assert title_score("Multibit Ferroelectric HfZrO Memcapacitor for Non-Volatile Memory") > 0


def test_title_from_layout_uses_large_title_lines(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 40), "Advanced Functional Materials", fontsize=10)
    page.insert_text((72, 120), "Multibit Ferroelectric HfZrO Memcapacitor", fontsize=22)
    page.insert_text((72, 150), "for Non-Volatile Analogue Memory", fontsize=22)
    page.insert_text((72, 220), "Deepika Yadav, Spyros Stathopoulos", fontsize=9)
    page.insert_text((72, 300), "ABSTRACT", fontsize=10)
    doc.save(pdf_path)
    doc.close()

    candidate = title_from_layout(pdf_path)

    assert candidate is not None
    assert candidate.title == "Multibit Ferroelectric HfZrO Memcapacitor for Non-Volatile Analogue Memory"


def test_title_score_rejects_incomplete_fragments():
    assert title_score("Effect of Annealing Temperature on Minimum Domain Size of") == 0
    assert title_score("Keywords: Ferroelectric; Field effect diode") == 0
