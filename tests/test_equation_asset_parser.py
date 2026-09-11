from backend.services.equation_asset_parser import equation_candidate_kind, equation_candidate_score


def test_equation_candidate_score_accepts_scientific_equations():
    assert equation_candidate_score("C = kappa epsilon_0 A / d") > 0
    assert equation_candidate_score("Delta OSMargin = A exp(-E_A/kT) t^n") > 0


def test_equation_candidate_score_rejects_plain_prose_and_urls():
    assert equation_candidate_score("The film was deposited and then annealed in nitrogen.") == 0
    assert equation_candidate_score("https://example.com?a=b") == 0


def test_equation_candidate_score_rejects_parameter_lists():
    assert equation_candidate_score("dFE = 10 nm, dPE = 1.2 nm, Ec = 1 MV/cm, Pr = 20 μC/cm2") == 0
    assert equation_candidate_score("lattice constants a = 5.21 Å and b = 5.08 Å") == 0


def test_equation_candidate_kind_splits_display_and_inline_equations():
    assert equation_candidate_kind("F = U - TS + ΩDE + Γ (2)") == "display_equation"
    assert equation_candidate_kind("The Maxwell relation is (∂S/∂E)T = (∂P/∂T)E.") == "display_equation"
