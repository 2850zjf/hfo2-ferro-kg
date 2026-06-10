# MP HfO2 Phase-Stability Smoke Test

## Purpose

This is the simplest defensible computational entry point for HfO2-FerroKG.

It checks whether the cloud calculation environment can reproduce a basic HfO2 polymorph energy comparison before we attempt dopants, interfaces, oxygen vacancies, phase-field models, or ML-potential MD.

## Scientific Question

Can the chosen VASP setup produce a reasonable relative-energy ranking for HfO2 polymorphs using structures retrieved from Materials Project?

This is a method smoke test. It does not claim to predict experimental Pr/2Pr.

## Why This Fits The KG Story

The literature KG and Pr/2Pr benchmark identify sample-level correlations between composition, thickness, annealing, electrodes, phase, and polarization. The first physics gate behind those correlations is phase stability: whether the orthorhombic ferroelectric phase is energetically plausible relative to monoclinic, tetragonal, and cubic alternatives.

So the first calculation should not be a large speculative HZO device model. It should be:

```text
Materials Project HfO2 structures
-> consistent VASP relaxation/static settings
-> relative phase energies
-> computed phase descriptors
-> KG / benchmark write-back fields
```

## Structure Source

Structures are fetched with the official Materials Project API through `mp-api` and `pymatgen`.

The script:

- searches Materials Project for `HfO2`;
- filters by symmetry/crystal system to select monoclinic, tetragonal, cubic, and orthorhombic candidates when available;
- writes `POSCAR` files with `pymatgen.io.vasp.inputs.Poscar`;
- records MP material IDs, formula, symmetry, energy above hull, and selection criteria in a manifest.

It does not hand-build crystal structures.

## Dependencies

Install these on the cloud or a separate compute environment:

```bash
python -m pip install -r computations/mp_hfo2_phase_smoke_test/requirements-compute.txt
```

Set the Materials Project key as an environment variable:

```bash
export MP_API_KEY="..."
```

Do not write the key into code, `.env`, reports, or Git.

## Generate VASP Folders

```bash
python computations/mp_hfo2_phase_smoke_test/fetch_mp_structures.py \
  --output-dir data/computation/mp_hfo2_phase_smoke_test \
  --max-energy-above-hull 0.35
```

Outputs are written under `data/computation/`, which is ignored by Git:

```text
data/computation/mp_hfo2_phase_smoke_test/
  candidates_manifest.csv
  selected_manifest.csv
  monoclinic__mp-.../
    POSCAR
    INCAR.relax
    INCAR.static
    KPOINTS
    POTCAR_NOT_INCLUDED.txt
    job_notes.md
  ...
```

## VASP Execution Pattern

For each selected polymorph:

1. Copy the correct `POTCAR` on the cloud system according to your VASP license and institutional setup.
2. Run relaxation with `INCAR.relax`.
3. Run a static energy calculation with `INCAR.static` using the relaxed structure.
4. Record total energy, number of formula units, convergence status, and notes in `results_template.csv`.

## Quality Gate

Before using any result in the paper:

- same functional, PAW potentials, ENCUT, KPOINTS density, smearing, and convergence thresholds across phases;
- relaxed structures converge in force and electronic SCF;
- total energy normalized per formula unit;
- energy ranking robust to a tighter k-point/ENCUT check for at least the closest competing phases;
- no claim that DFT relative energy directly equals experimental Pr/2Pr.

## Next Step After Smoke Test

Only after the HfO2 phase smoke test is stable:

- add neutral oxygen vacancy calculations on selected phases;
- search MP for ordered Hf-Zr-O structures if available;
- if ordered HZO is missing from MP, explicitly choose a published structure, enumlib/SQS workflow, or dopant-site sampling plan rather than silently substituting atoms by hand.
