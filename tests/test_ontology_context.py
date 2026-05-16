from __future__ import annotations

from backend.services.ontology_context import build_ontology_context


def test_build_ontology_context_tracks_requested_review_fields():
    context = build_ontology_context(
        material={"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        sample={
            "material_ref": "Hf0.5Zr0.5O2",
            "film_thickness_nm": 10,
            "deposition_method": "ALD",
            "top_electrode": "TiN",
            "bottom_electrode": "TiN",
            "annealing_temperature_c": 500,
            "annealing_time_s": 30,
            "annealing_atmosphere": "N2",
            "device_stack": "TiN/HZO/TiN",
            "sample_form": "capacitor",
            "evidence_text": "The TiN/HZO/TiN capacitor was annealed at 500 C for 30 s in N2.",
        },
        prop={
            "property_name": "double_remanent_polarization_2Pr",
            "value": 40,
            "unit": "uC/cm2",
            "evidence_text": "After wake-up cycling, the 2Pr value reached 40 uC/cm2.",
        },
        phases=[{"phase_name": "orthorhombic", "space_group": "Pca21"}],
        devices=[{"device_type": "FeCAP"}],
    )

    requested = context["requested_review_fields"]
    assert requested["材料体系"] == "HZO"
    assert requested["Pr 或 2Pr"] == "double_remanent_polarization_2Pr"
    assert requested["薄膜厚度"] == 10
    assert requested["退火温度"] == 500
    assert requested["电极 stack"] == "TiN/HZO/TiN"
    assert requested["沉积方法"] == "ALD"
    assert requested["器件类型"] == "capacitor, FeCAP"
    assert requested["相结构"] == "orthorhombic, Pca21"
    assert requested["wake-up 状态"] == "wake-up mentioned"
    assert context["comparison_ready"] is True


def test_build_ontology_context_rejects_garbage_device_stack():
    context = build_ontology_context(
        material={"canonical_name": "Hf0.5Zr0.5O2", "material_family": "HZO"},
        sample={
            "material_ref": "Hf0.5Zr0.5O2",
            "device_stack": "tingthereductionofEcinHZOcouldbetriggeredw",
            "sample_form": "thin_film",
            "evidence_text": "The HZO thin film was measured.",
        },
        prop={
            "property_name": "coercive_field_Ec",
            "value": 2.6,
            "unit": "MV/cm",
            "evidence_text": "The Ec value was 2.6 MV/cm.",
        },
        phases=[],
        devices=[],
    )

    assert context["requested_review_fields"]["电极 stack"] == ""
    assert "electrode, substrate, or device stack" in context["missing_context_labels"]
