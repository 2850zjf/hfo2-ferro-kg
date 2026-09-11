from __future__ import annotations

from backend.services.physical_constraints import assess_design_row, compact_prompt_constraints


def test_physical_constraints_score_good_hzo_row():
    row = {
        "material_name": "Hf0.5Zr0.5O2",
        "material_family": "HZO",
        "target_property": "double_remanent_polarization_2Pr",
        "model_target_value": 52,
        "film_thickness_nm": 10,
        "annealing_temperature_c": 500,
        "annealing_time_s": 30,
        "annealing_atmosphere": "N2",
        "electrode_stack": "TiN/HZO/TiN",
        "phase_name": "orthorhombic",
        "space_group": "Pca21",
        "evidence_text": "The TiN/HZO/TiN capacitor shows 2Pr of 52 uC/cm2.",
        "page_number": 3,
    }

    assessment = assess_design_row(row)

    assert assessment.recommendation_allowed is True
    assert assessment.physical_consistency_score > 0.7
    assert assessment.descriptors["phase_stability_score"] > 0.8


def test_physical_constraints_flag_implausible_polarization():
    row = {
        "material_name": "Hf0.5Zr0.5O2",
        "target_property": "remanent_polarization_Pr",
        "model_target_value": 180,
        "phase_name": "monoclinic",
        "evidence_text": "Pr was reported as 180 uC/cm2.",
        "page_number": 1,
    }

    assessment = assess_design_row(row)

    assert assessment.recommendation_allowed is False
    assert "remanent_polarization_Pr_above_hard_max" in assessment.hard_violations


def test_physical_constraints_prompt_summary_mentions_pr_2pr():
    prompt = compact_prompt_constraints()

    assert "Do not merge Pr and 2Pr" in prompt
    assert "oxygen vacancies" in prompt.lower()
