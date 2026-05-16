# HfO2 Extraction Prompt

Extract only HfO2-based ferroelectric material facts from the provided chunk.

Rules:
- Return JSON that matches `HfO2ExtractionResult`.
- Keep Pr and 2Pr separate.
- `double_remanent_polarization_2Pr` is not the same as `remanent_polarization_Pr`.
- Do not infer values without direct evidence.
- Preserve original units.
- Every property must include `evidence_text`.
- `evidence_text` must be copied from the provided chunk, not paraphrased.
- Use `warnings` when the chunk appears to be a review, a cited secondary value, a figure-estimated value, or a sample-condition mismatch.
- If no HfO2 material fact is present, return empty arrays.
- Mark uncertain or review-like claims in `warnings`.

Extraction focus:
- Material: HfO2, HZO, Hf1-xZrxO2, Hf0.5Zr0.5O2, doped hafnia.
- Process: ALD, sputtering, PLD, annealing temperature/time/atmosphere, electrodes, substrate.
- Phase: orthorhombic, Pca21, monoclinic, tetragonal, rhombohedral, amorphous.
- Property: Pr, 2Pr, Ec, Ps, endurance, retention, leakage, memory window.
