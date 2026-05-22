# HfO2 Extraction Prompt

Extract only HfO2-based ferroelectric material facts from the provided chunk.

Rules:
- Return one JSON object that matches `HfO2ExtractionResult`.
- Use exactly the field names below. Do not invent aliases such as `material_name`, `thickness`, or `property`.
- Keep Pr and 2Pr separate.
- `double_remanent_polarization_2Pr` is not the same as `remanent_polarization_Pr`.
- Do not infer values without direct evidence.
- Preserve original units.
- Every property must include `evidence_text`.
- `evidence_text` must be copied from the provided chunk, not paraphrased.
- Use `warnings` when the chunk appears to be a review, a cited secondary value, a figure-estimated value, or a sample-condition mismatch.
- If no HfO2 material fact is present, return empty arrays.
- Mark uncertain or review-like claims in `warnings`.
- Prefer returning fewer high-confidence facts with complete context over many isolated values.
- When a property value is reported, also extract the matching sample/process/device context from the same chunk if present.
- Return at most 6 property records per chunk. Prioritize facts with material, property value, unit, evidence_text, and sample/process/device context.
- Do not copy the example below. It is only a schema template.

Required JSON shape:

```json
{
  "paper_id": "string",
  "pdf_id": "string",
  "chunk_id": "string",
  "page_number": 1,
  "materials": [
    {
      "raw_name": "Hf0.5Zr0.5O2",
      "canonical_name": "Hf0.5Zr0.5O2",
      "formula": "Hf0.5Zr0.5O2",
      "material_family": "HZO",
      "base_material": "HfO2",
      "dopant_elements": ["Zr"],
      "dopant_concentration": null,
      "zr_fraction": 0.5,
      "evidence_text": "copied source sentence"
    }
  ],
  "samples": [
    {
      "material_ref": "Hf0.5Zr0.5O2",
      "film_thickness_nm": 10,
      "deposition_method": "ALD",
      "top_electrode": "TiN",
      "bottom_electrode": "TiN",
      "substrate": "Si",
      "annealing_temperature_c": 500,
      "annealing_time_s": 30,
      "annealing_atmosphere": "N2",
      "device_stack": "TiN/HZO/TiN",
      "sample_form": "capacitor",
      "evidence_text": "copied source sentence"
    }
  ],
  "phases": [
    {
      "material_ref": "Hf0.5Zr0.5O2",
      "phase_name": "orthorhombic",
      "space_group": "Pca21",
      "characterization_method": "XRD",
      "evidence_text": "copied source sentence"
    }
  ],
  "properties": [
    {
      "material_ref": "Hf0.5Zr0.5O2",
      "property_name": "double_remanent_polarization_2Pr",
      "raw_property_name": "2Pr",
      "value": 40,
      "unit": "μC/cm²",
      "normalized_value": null,
      "normalized_unit": null,
      "measurement_temperature": null,
      "measurement_frequency": null,
      "electric_field": null,
      "device_type": "capacitor",
      "confidence": 0.85,
      "evidence_text": "copied source sentence",
      "is_reported_value": true,
      "is_derived_value": false,
      "derivation_rule": null,
      "review_status": "pending"
    }
  ],
  "devices": [
    {
      "device_type": "capacitor",
      "device_stack": "TiN/HZO/TiN",
      "evidence_text": "copied source sentence"
    }
  ],
  "evidences": [
    {
      "paper_id": "string",
      "pdf_id": "string",
      "chunk_id": "string",
      "page_number": 1,
      "evidence_text": "copied source sentence",
      "source_type": "text"
    }
  ],
  "warnings": []
}
```

Extraction focus:
- Material: HfO2, HZO, Hf1-xZrxO2, Hf0.5Zr0.5O2, doped hafnia.
- Process: ALD, sputtering, PLD, annealing temperature/time/atmosphere, electrodes, substrate.
- Phase: orthorhombic, Pca21, monoclinic, tetragonal, rhombohedral, amorphous.
- Property: Pr, 2Pr, Ec, Ps, endurance, retention, leakage, memory window.
