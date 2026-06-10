# HfO2/HZO Ferroelectric Global Survey and Publication Story

Date: 2026-06-10

## 1. Scope and Search Strategy

This survey is intentionally broader than the current local KG snapshot. It combines:

- Global literature discovery through OpenAlex/Crossref queries from 2011-01-01 to 2026-06-10.
- A citation-ranked OpenAlex pool focused on HfO2/HZO ferroelectricity, phase stability, defects, interfaces, superlattices, AI, and computation.
- Open-access PDF downloads for representative papers and computational-method references.
- Cross-checks against the local HfO2-FerroKG tables to identify what the current dataset already covers and what should be supplemented.

The current global discovery run found 114 strong candidates, including 100 DOI-bearing records, 97 records with open-access landing pages, and 70 records with PDF links. A separate citation-ranked curation identified 26 representative papers spanning the historical discovery, phase-stability theory, oxygen-vacancy/interface mechanisms, superlattice scaling, AI dopant screening, and computational method risks.

Local generated files:

- `data/literature_candidates/literature_candidates.csv`
- `data/literature_candidates/global_hzo_survey_20260610/curated_representative_papers.md`
- `data/literature_candidates/global_hzo_survey_20260610/open_pdf_download_manifest.csv`
- `data/literature_candidates/global_hzo_survey_20260610/supplement_download_manifest.csv`

## 2. Research Landscape

### A. Phase Stability Is Still the Central Physics Problem

The field began from the discovery of ferroelectricity in Si-doped HfO2 thin films, followed by computational work showing that the ferroelectric orthorhombic phase is usually metastable relative to competing monoclinic, tetragonal, or antipolar phases. The classic 2015 HZO computational paper framed the problem through bulk energy, stress/strain, and surface-energy stabilization. Later work expanded the phase set to rhombohedral, antipolar, and field-induced phases.

Why this remains unresolved:

- Different polymorphs can be close in energy, so small changes in thickness, surface energy, electrode boundary condition, dopant, oxygen vacancy, and strain can flip the predicted stable/metastable phase.
- Recent computation papers warn that phase ordering can depend strongly on exchange-correlation functional choice, especially for antipolar Pbcn/Pbca-like phases.
- Room-temperature ferroelectric behavior is not a simple bulk equilibrium property; finite-temperature, domain-wall, surface, defect, and kinetic effects matter.

Representative papers:

- [Ferroelectricity in hafnium oxide thin films](https://doi.org/10.1063/1.3634052)
- [The origin of ferroelectricity in Hf1-xZrxO2](https://doi.org/10.1063/1.4916707)
- [Stabilizing the ferroelectric phase in doped hafnium oxide](https://doi.org/10.1063/1.4927805)
- [A rhombohedral ferroelectric phase in epitaxially strained Hf0.5Zr0.5O2 thin films](https://doi.org/10.1038/s41563-018-0196-0)
- [Progress in computational understanding of ferroelectric mechanisms in HfO2](https://doi.org/10.1038/s41524-024-01352-0)
- [Unexpected density functional dependence of the antipolar Pbcn phase in HfO2](https://doi.org/10.1038/s41524-025-01647-w)

### B. Interface, Oxygen Vacancy, and Electrochemical Boundary Conditions Are the Best Publication Handle

The most publishable single story for this project is not "a general KG for everything." It should be:

> AI-assisted literature-to-physics discovery of how interface/electrochemical boundary conditions govern HZO phase stability, Pr/2Pr, and reliability, with DFT/defect calculations as feedback validation.

This story is stronger because it connects:

- Process knobs: electrode stack, oxygen reservoir, annealing atmosphere, deposition method, thickness.
- Physical mechanisms: oxygen vacancy formation/migration, charge transfer, surface/interface screening, strain, and phase competition.
- Measurable outcomes: Pr, 2Pr, coercive field, wake-up/fatigue, endurance, retention.
- Computable descriptors: phase energy difference, oxygen-vacancy formation energy, migration barrier, charge transfer, interface strain, and phase-field parameters.

Why this is a good target:

- The local KG already has many interface/electrode-related rows, but key fields such as atmosphere, phase fraction, and orientation are often missing. This creates a clear extraction-improvement and benchmark angle.
- Recent high-impact work points to electrode/interface as a primary constraint on endurance and retention, while oxygen vacancies appear as both a stabilizer and degradation source.
- The computation loop can start small with Materials Project structures and HfO2 polymorph energetics, then scale to defect and interface calculations.

Representative papers:

- [Reversible oxygen migration and phase transitions in hafnia-based ferroelectric devices](https://doi.org/10.1126/science.abf3789)
- [Role of oxygen vacancies in ferroelectric or resistive switching hafnium oxide](https://doi.org/10.1186/s40580-023-00403-4)
- [Interface-engineered ferroelectricity of epitaxial Hf0.5Zr0.5O2 thin films](https://doi.org/10.1038/s41467-023-37560-3)
- [The Electrode-Ferroelectric Interface as the Primary Constraint on Endurance and Retention in HZO-Based Ferroelectric Capacitors](https://doi.org/10.1002/adfm.202303261)
- [Ferroelectricity in hafnia controlled via surface electrochemical state](https://doi.org/10.1038/s41563-023-01619-9)
- [Modulation of Oxygen Content and Ferroelectricity in Sputtered Hafnia-Zirconia by Engineering of Tungsten Oxide Bottom Electrodes](https://doi.org/10.1002/aelm.202300798)

### C. Domain Orientation and Hidden Phase Transition Is a High-Novelty Secondary Axis

Recent HZO work suggests that polarization can be engineered not only by increasing orthorhombic phase fraction, but also by controlling domain orientation through hidden tetragonal-to-orthorhombic pathways and interface dislocations.

Why it matters:

- Most literature-derived datasets record phase labels, but not domain orientation or orientation-dependent contribution to Pr.
- This creates an AI extraction opportunity: extract XRD/TEM orientation, substrate orientation, electrode orientation, epitaxial relation, and domain orientation evidence.
- It can become a supplement or second paper direction after the interface/oxygen-vacancy story is stable.

Representative papers:

- [Hidden structural phase transition assisted ferroelectric domain orientation engineering in Hf0.5Zr0.5O2 films](https://doi.org/10.1038/s41467-025-59519-2)
- [Mapping electric fields and observation of ferroelectric domain switching in hafnia-zirconia devices by electron holography](https://doi.org/10.1038/s41467-025-66807-4)

### D. Superlattice and Nanolaminate Engineering Is a Scalable Design Space

HfO2/ZrO2 superlattices and nanolaminates are important because they provide layer-period, interface-density, and strain/composition knobs that can be represented in both KG and computation.

Why it matters:

- Superlattice engineering gives a structured design space, rather than a loose list of dopants.
- It connects to advanced transistor gate stacks and ultra-thin scaling.
- It is suitable for phase-field or DFT-derived interface-energy descriptors.

Representative papers:

- [Ultrathin ferroic HfO2-ZrO2 superlattice gate stack for advanced transistors](https://doi.org/10.1038/s41586-022-04425-6)
- [Enhancing ferroelectric stability: wide-range of adaptive control in epitaxial HfO2/ZrO2 superlattices](https://doi.org/10.1038/s41467-025-61758-2)
- [Roadmap of phase transitions in hafnia-based superlattice films](https://doi.org/10.1038/s41467-026-71265-7)

### E. AI Is Useful When It Narrows a Physical Hypothesis, Not When It Replaces Mechanism

The most relevant AI roles are:

1. Literature-scale extraction of sample-level process/structure/property facts.
2. Weak-topic screening to remove papers outside HfO2/HZO ferroelectric mechanisms.
3. Active-learning candidate ranking for Pr/2Pr and reliability targets.
4. Image/text assistance for phase/orientation recognition.
5. Selection of DFT/MD/phase-field tasks that are small enough to validate.

Representative papers:

- [Artificial intelligence-driven phase stability evaluation and new dopants identification of hafnium oxide-based ferroelectric materials](https://doi.org/10.1038/s41524-024-01510-4)
- [Machine learning-powered recognition of crystalline phases and orientations in epitaxial Y-doped HfO2 via atomic-resolution STEM](https://doi.org/10.1038/s41524-025-01865-2)

## 3. Recommended Paper Story

### Proposed Title

Literature-Grounded AI and Computation for Interface-Controlled Ferroelectricity in HfO2-ZrO2 Thin Films

### Core Research Question

Can an evidence-traceable HfO2/HZO knowledge graph identify how electrode/interface and oxygen-vacancy boundary conditions control Pr/2Pr and reliability, and can targeted DFT/defect calculations validate the physical descriptors behind the AI-ranked design rules?

### One-Sentence Claim Boundary

The paper should claim a literature-grounded hypothesis and computational validation workflow, not a universal experimental recipe.

### Contributions

1. Ontology-first HfO2/HZO KG with sample-property links and evidence pages.
2. Global literature supplementation focused on interface, oxygen vacancy, phase stability, and superlattice mechanisms.
3. Tiered Pr/2Pr benchmark with paper-level train/validation splits and leakage control.
4. Multi-model validation using random forest/extra trees/SVM/gradient boosting and a graph-model track.
5. Computation feedback loop using Materials Project structures, DFT phase/defect descriptors, and later phase-field/ML-potential simulations.
6. Evidence-grounded RAG for explaining design suggestions and uncertainty.

## 4. Revised Workflow

```mermaid
flowchart LR
  A["Global literature discovery<br/>OpenAlex/Crossref + manual high-impact list"] --> B["OA PDF download<br/>manual queue for closed but important papers"]
  B --> C["PDF parsing<br/>text, tables, captions, figures"]
  C --> D["Ontology-guided extraction<br/>sample, process, phase, interface, defects, properties"]
  D --> E["Sample-property linking<br/>Pr/2Pr, Ec, endurance, evidence page"]
  E --> F["Benchmark tiers<br/>strong_only, strong_partial, all_traceable"]
  F --> G["Model validation<br/>paper-level split, validation MAE/RMSE/R2"]
  G --> H["AI hypothesis mining<br/>rank design knobs and uncertain regimes"]
  H --> I["Computation agent<br/>MP structures, VASP/defects/phase-field/ML potentials"]
  I --> J["Computed descriptors<br/>phase energy, vacancy energy, migration, strain, interface"]
  J --> E
```

## 5. Computation Loop: What to Calculate First

### Phase 0: Reproducible Structure Intake

- Source pristine polymorph structures from Materials Project using `mp-api` and `pymatgen`.
- Start with HfO2 and ZrO2 polymorphs: monoclinic, tetragonal, cubic, orthorhombic polar, and antipolar if available.
- Record Materials Project IDs, conventional/primitive cells, space groups, and energy above hull.
- Do not commit POTCAR, API keys, or proprietary files.

### Phase 1: Small DFT Smoke Test

Goal: prove the Tencent/TEFS computation environment is connected and reproducible.

Tasks:

- Relax MP-derived HfO2 polymorph structures with consistent VASP settings.
- Run static calculations.
- Compute relative energy ordering and compare to Materials Project and literature.
- Quality gates: convergence, force threshold, stress, k-point convergence, consistent functional/pseudopotentials.

### Phase 2: Defect Descriptors

Goal: connect oxygen vacancies to phase stability and reliability.

Tasks:

- Compute neutral and charged oxygen-vacancy formation energies in selected phases.
- Compare vacancy preference across monoclinic, tetragonal, polar orthorhombic, and antipolar phases.
- Later add migration barriers with NEB for high-priority paths.
- Quality gates: supercell size, charge correction, chemical potential convention, potential alignment, finite-size correction.

### Phase 3: Interface/Boundary-Condition Descriptors

Goal: validate the paper's main hypothesis.

Tasks:

- Compare simple HfO2/HZO surface terminations first.
- Then build a limited set of electrode-related models: TiN/HZO, W/WOx/HZO, and oxide-electrode-inspired boundary conditions.
- Compute oxygen-vacancy formation energy near the interface, charge transfer, strain, and dipole/band alignment where feasible.

### Phase 4: Mesoscale Feedback

Goal: translate microscopic descriptors into sample-scale behavior.

Options:

- Phase-field simulation using DFT-derived phase energy, wall energy, and electrostatic boundary conditions.
- ML-potential MD only after DFT data are sufficient or a universal potential is benchmarked on HfO2/HZO polymorphs.

## 6. Benchmark and Ontology Updates

The current ontology should be extended around the main story rather than expanded indiscriminately.

Add or strengthen these fields:

- `top_electrode`, `bottom_electrode`, `electrode_stack`, `electrode_oxygen_affinity_class`
- `interface_layer`, `interface_termination`, `surface_electrochemical_state`
- `oxygen_vacancy_context`, `oxygen_reservoir`, `oxygen_partial_pressure`, `annealing_atmosphere`
- `phase_name`, `space_group`, `phase_fraction`, `orientation`, `domain_orientation`
- `thickness_nm`, `deposition_method`, `annealing_temperature_c`, `annealing_time_s`
- `Pr_uC_cm2`, `two_Pr_uC_cm2`, `Ec_MV_cm`, `endurance_cycles`, `retention_s`
- `evidence_page`, `evidence_text`, `figure_or_table_id`, `extraction_confidence`

Benchmark split rules:

- Use paper-level train/validation split to avoid same-paper leakage.
- Keep `strong_only` as the main reported benchmark.
- Add mechanism holdout splits: unseen electrode class, unseen oxygen-reservoir class, unseen superlattice period.
- Report regression metrics on validation: MAE, RMSE, R2, within-10-uC/cm2 rate.
- For graph models, report node/property regression and link-prediction style auxiliary metrics separately.

## 7. What AI Can Accelerate

AI should accelerate the parts that are currently too slow for manual reading:

- Global discovery of high-impact and recent representative papers.
- Strong/weak relevance filtering before full extraction.
- Extraction of sample-level process/structure/property tuples from text, tables, and captions.
- Evidence traceability: every model row points to DOI/page/table/sentence.
- Active learning: find high-value but uncertain regimes, not just the highest predicted Pr.
- Computational task planning: choose small DFT tasks that test the highest-impact uncertainties.
- RAG explanation: answer why a recommendation is plausible and what evidence is missing.

## 8. Immediate Next Work

1. Finish manual acquisition of closed but essential papers, especially the 2011 discovery paper, 2015 doped-HfO2 stabilization paper, Science oxygen-migration paper if the OA PDF is unavailable locally, and interface/endurance papers.
2. Ingest the newly downloaded OA PDFs through the existing parse/chunk/extract pipeline.
3. Re-run strong relevance filtering with the updated global candidate pool.
4. Expand the ontology only around interface, oxygen vacancies, phase/orientation, and Pr/2Pr reliability outcomes.
5. Build a paper-level validation split and rerun model comparison.
6. Launch the Materials Project HfO2 polymorph smoke test on the compute environment.
7. Use DFT descriptors to create a small `computed_descriptors.csv` that joins back to KG samples by phase/interface/defect class.

