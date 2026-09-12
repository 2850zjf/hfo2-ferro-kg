# VASP Raw Output Audit

- status: `audited_with_publication_blockers`
- source: `/Users/<mac-user>/Codex/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/data/computation/tefs_hfo2_phase_smoke_20260622/runs/hfo2_phase_smoke`
- energy authority: `raw_vasprun.xml_and_OUTCAR_only`
- publication_grade: `false`
- database writes: none
- symmetry runtime: `spglib 2.7.0`

## Raw phase records

| phase | final SG | formula | E (eV/f.u.) | relative (meV/f.u.) | max force (eV/Å) | electronic converged | ionic converged | auditable |
|---|---|---|---:|---:|---:|---|---|---|
| monoclinic | P2_1/c #14 | Hf4O8 | -30.51766896 | 0.000 | 0.014675 | True | True | True |
| orthorhombic | Pca2_1 #29 | Hf4O8 | -30.43336845 | 84.301 | 0.013491 | True | True | True |
| tetragonal | P4_2/nmc #137 | Hf2O4 | -30.35133714 | 166.332 | 0.007980 | True | True | True |
| cubic | Fm-3m #225 | Hf4O8 | -30.24830056 | 269.368 | 0.000000 | True | True | True |

## Publication blockers

- `no_encut_convergence_study`
- `no_kpoint_density_convergence_study`
- `no_repeat_calculations_or_numerical_uncertainty`
- `one_or_more_final_forces_above_0p01_eV_per_A`
- `potcar_binary_sha256_unavailable`
- `relax_OUTCAR_and_relax_vasprun_xml_not_preserved`
- `zero_strain_smoke_test_only_no_biaxial_strain_matrix`

## Interpretation boundary

The audited values are zero-strain static smoke-test descriptors parsed from raw files. They do not establish publication-grade phase energetics, biaxial-strain response, a phase boundary, or any experimental annealing/Pr/2Pr claim.

`phase_energy_summary.csv` was used only for a consistency cross-check. No CSV value or pytest fixture was used as the energy authority.
