# HZO Literature Supplement and Computation Reading Pack

Date: 2026-06-10

## 1. What Was Downloaded Locally

All downloaded files are under ignored data directories and should not be committed.

Open-access representative papers:

- `data/literature_candidates/global_hzo_survey_20260610/open_pdfs/`
- `data/literature_candidates/global_hzo_survey_20260610/recent_open_pdfs/`

Computation references:

- `data/literature_candidates/global_hzo_survey_20260610/method_pdfs/`

Manifests:

- `data/literature_candidates/global_hzo_survey_20260610/open_pdf_download_manifest.csv`
- `data/literature_candidates/global_hzo_survey_20260610/supplement_download_manifest.csv`
- `data/literature_candidates/global_hzo_survey_20260610/pdf_text_snippets.md`

Current manual download status:

- 16 of 28 high-impact OA PDF attempts succeeded through the citation-ranked OpenAlex pool.
- Additional recent/method PDFs were downloaded, including computational HfO2 review, AI dopant-screening paper, hidden phase-transition paper, surface electrochemical-state paper, pymatgen-analysis-defects paper, atomate2 VASP workflow slides, CHGNet paper, and an ML-interatomic-potential guide.
- Some links returned 403 or HTML rather than PDF; these are retained in the manifests rather than forced.

Project downloader status after the global discovery run:

- `pipelines/12_discover_literature.py` found 114 candidates from 2011-01-01 to 2026-06-10.
- `pipelines/13_download_open_access_pdfs.py --limit 45 --min-score 0.45` found 19 eligible candidates, downloaded 8 new PDFs, detected 7 duplicates, skipped 3 not-verified OA records, and failed on 1 record.
- `data/raw_pdfs/open_access/` currently contains 120 PDF files.

## 2. Highest-Priority Papers to Ensure in the KG

### Must-Have Historical and Review Papers

| Priority | Paper | Why It Matters | Status |
|---:|---|---|---|
| 1 | [Ferroelectricity in hafnium oxide thin films](https://doi.org/10.1063/1.3634052) | Foundational 2011 discovery; high citation anchor for the introduction. | Manual/institutional download likely needed |
| 2 | [Roadmap on ferroelectric hafnia- and zirconia-based materials and devices](https://doi.org/10.1063/5.0148068) | Field roadmap; good for positioning unresolved problems. | OA PDF link found, automated download may need retry |
| 3 | [Review and perspective on ferroelectric HfO2-based thin films for memory applications](https://doi.org/10.1557/mrc.2018.175) | High-cited review; useful for historical mechanism framing. | OA/green; verify local copy |
| 4 | [Ferroelectric Hf0.5Zr0.5O2 Thin Films: A Review of Recent Advances](https://doi.org/10.1007/s11837-018-3140-5) | Broad HZO review for process/property vocabulary. | Manual/institutional download likely needed |

### Must-Have Mechanism Papers

| Priority | Paper | Theme | Status |
|---:|---|---|---|
| 1 | [The origin of ferroelectricity in Hf1-xZrxO2](https://doi.org/10.1063/1.4916707) | Phase stability, surface energy, DFT baseline | Downloaded |
| 2 | [Stabilizing the ferroelectric phase in doped hafnium oxide](https://doi.org/10.1063/1.4927805) | Doping and phase stabilization | Manual/institutional download likely needed |
| 3 | [Reversible oxygen migration and phase transitions in hafnia-based ferroelectric devices](https://doi.org/10.1126/science.abf3789) | Oxygen migration and phase transition | Verify local access |
| 4 | [Reversible transition between polar and antipolar phases](https://doi.org/10.1038/s41467-022-28236-5) | Wake-up/fatigue mechanism | Downloaded |
| 5 | [Ferroelectricity in hafnia controlled via surface electrochemical state](https://doi.org/10.1038/s41563-023-01619-9) | Electrochemical boundary conditions | Downloaded |
| 6 | [Role of oxygen vacancies in ferroelectric or resistive switching hafnium oxide](https://doi.org/10.1186/s40580-023-00403-4) | Oxygen-vacancy review | Downloaded |

### Must-Have Interface and Scaling Papers

| Priority | Paper | Theme | Status |
|---:|---|---|---|
| 1 | [The Electrode-Ferroelectric Interface as the Primary Constraint on Endurance and Retention](https://doi.org/10.1002/adfm.202303261) | Main story support: interface limits reliability | Manual/verify |
| 2 | [Interface-engineered ferroelectricity of epitaxial Hf0.5Zr0.5O2 thin films](https://doi.org/10.1038/s41467-023-37560-3) | Interface termination, charge transfer | Downloaded |
| 3 | [Dimensional Scaling of Ferroelectric Properties of Hafnia-Zirconia Thin Films](https://doi.org/10.1021/acsnano.4c01992) | Thickness and electrode interface | Manual/institutional download likely needed |
| 4 | [Modulation of Oxygen Content and Ferroelectricity by Tungsten Oxide Bottom Electrodes](https://doi.org/10.1002/aelm.202300798) | Oxygen reservoir/electrode design | OA; verify local copy |
| 5 | [Ultrathin ferroic HfO2-ZrO2 superlattice gate stack](https://doi.org/10.1038/s41586-022-04425-6) | Superlattice and transistor scaling | Downloaded |
| 6 | [Enhancing ferroelectric stability in epitaxial HfO2/ZrO2 superlattices](https://doi.org/10.1038/s41467-025-61758-2) | Superlattice phase control | OA PDF available |

### Must-Have AI and Computation Papers

| Priority | Paper | Theme | Status |
|---:|---|---|---|
| 1 | [Progress in computational understanding of ferroelectric mechanisms in HfO2](https://doi.org/10.1038/s41524-024-01352-0) | DFT, switching, ML-MD, phase-field review | Downloaded |
| 2 | [Artificial intelligence-driven phase stability evaluation and new dopants identification](https://doi.org/10.1038/s41524-024-01510-4) | AI + DFT dopant discovery | Downloaded |
| 3 | [Unexpected density functional dependence of the antipolar Pbcn phase in HfO2](https://doi.org/10.1038/s41524-025-01647-w) | DFT quality/risk gate | OA PDF available |
| 4 | [Machine learning-powered recognition of crystalline phases and orientations](https://doi.org/10.1038/s41524-025-01865-2) | AI characterization support | OA PDF available |
| 5 | [Deep learning of accurate force field of ferroelectric HfO2](https://doi.org/10.1103/physrevb.103.024108) | ML force-field precedent | Downloaded |

## 3. Computation Manuals and Tooling to Learn

### Structure and Reference Data

- [Materials Project API documentation](https://docs.materialsproject.org/downloading-data/using-the-api): use `mp-api`/`MPRester` to fetch HfO2 and ZrO2 polymorph structures by formula, space group, and energy-above-hull.
- [pymatgen documentation](https://pymatgen.org/): structure manipulation, symmetry, VASP input generation, phase diagram tooling.

### VASP Workflow Automation

- [VASP Wiki](https://www.vasp.at/wiki/index.php/The_VASP_Manual): official reference for INCAR/KPOINTS/POTCAR/POSCAR conventions and convergence controls.
- [atomate2 documentation](https://materialsproject.github.io/atomate2/): production-grade VASP workflows, input sets, error handling, task documents, and restart logic.
- Local downloaded reference: `data/literature_candidates/global_hzo_survey_20260610/method_pdfs/atomate2_intro_vasp_workflows.pdf`

### Defects and Oxygen Vacancies

- [pymatgen-analysis-defects](https://materialsproject.github.io/pymatgen-analysis-defects/): point-defect analysis, charge corrections, formation energy post-processing.
- [doped documentation](https://doped.readthedocs.io/): defect generation and finite-size charge correction workflows around VASP/pymatgen.
- Local downloaded reference: `data/literature_candidates/global_hzo_survey_20260610/method_pdfs/pymatgen_analysis_defects_joss.pdf`

### Machine-Learning Potentials and MD

- [CHGNet](https://chgnet.lbl.gov/): pretrained charge-informed graph neural network potential; useful for quick screening, but must be benchmarked against HfO2/HZO DFT before trusting phase ordering.
- [MACE](https://mace-docs.readthedocs.io/): equivariant machine-learning interatomic potentials; useful when training or fine-tuning on DFT trajectories.
- [MatGL](https://matgl.ai/): materials graph library, including M3GNet-style workflows.
- [DeepMD-kit](https://docs.deepmodeling.com/projects/deepmd/en/master/): mature deep-potential MD framework; useful only after a curated DFT training set exists.
- Local downloaded references:
  - `data/literature_candidates/global_hzo_survey_20260610/method_pdfs/chgnet_pretrained_universal_potential.pdf`
  - `data/literature_candidates/global_hzo_survey_20260610/method_pdfs/practical_guide_machine_learning_interatomic_potentials.pdf`

## 4. How to Calculate Well

Minimum quality gates for the first HfO2/HZO calculations:

- Use Materials Project structures as starting points and record MP IDs.
- Keep the functional, pseudopotential family, cutoff, k-point density, and convergence thresholds fixed across phases.
- Relax cells and ions before static energies unless the comparison intentionally fixes strain.
- Check force convergence, stress, and no imaginary modes when claiming local stability.
- Report relative energies per formula unit, not just total energies.
- Run at least one convergence check for k-point density and plane-wave cutoff.
- For oxygen vacancies, define the oxygen chemical potential and charge correction method.
- For HZO alloys or doped systems, distinguish true MP structures from MP-derived substitutions/SQS models.
- For ML potentials, benchmark phase energy ranking against DFT before running long MD.

## 5. Recommended First Compute Job

First job: MP-derived HfO2 polymorph phase smoke test.

Why this first:

- It is small, cheap, and scientifically central.
- It verifies the cloud environment, VASP setup, MP structure fetch, and parsing workflow.
- It creates the first computed descriptor table for the KG: `phase_energy_delta_meV_per_fu`.

Existing starter:

- `computations/mp_hfo2_phase_smoke_test/README.md`
- `computations/mp_hfo2_phase_smoke_test/fetch_mp_structures.py`
- `computations/mp_hfo2_phase_smoke_test/templates/INCAR.relax`
- `computations/mp_hfo2_phase_smoke_test/templates/INCAR.static`

Suggested cloud run:

```bash
python -m pip install -r computations/mp_hfo2_phase_smoke_test/requirements-compute.txt
export MP_API_KEY="your_materials_project_key"
python computations/mp_hfo2_phase_smoke_test/fetch_mp_structures.py \
  --output-dir data/computation/mp_hfo2_phase_smoke_test \
  --max-energy-above-hull 0.35
```

Then run relax/static VASP tasks on Tencent/TEFS, collect energies, and write:

- `data/computation/mp_hfo2_phase_smoke_test/results.csv`
- `data/computation/computed_descriptors.csv`

## 6. Next KG Ingestion Steps

1. Put any manually downloaded PDFs into `data/raw_pdfs/` or a dedicated open/manual subfolder.
2. Run the parse/chunk pipeline.
3. Run strong relevance filtering before full extraction.
4. Extract only the fields tied to the chosen story: interface, oxygen-vacancy context, phase/orientation, Pr/2Pr, reliability.
5. Add extracted rows to paper-level train/validation splits.
6. Re-run model comparison and report validation-only performance.
