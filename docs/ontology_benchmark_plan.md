# HfO2-FerroKG Ontology and Benchmark Plan

## Scope

The paper prototype focuses on HfO2, HZO, and doped HfO2 thin-film ferroelectrics. BaTiO3, PZT, BiFeO3, and unrelated ferroelectrics are treated as background only unless a hafnia sample is explicitly reported.

## Ontology Backbone

The sample is the central modeling unit:

`Paper -> PDF -> Chunk/Table/Caption -> HafniaMaterial -> ThinFilmSample -> Process/Phase/Device/Property -> Evidence`

Core node classes:

- `Paper`: title, DOI, year, paper type, review flag.
- `PDFFile`: local parse status, hash, page count, parse quality.
- `HafniaMaterial`: HfO2/HZO/doped HfO2 family, formula, dopants, Zr fraction.
- `ThinFilmSample`: thickness, deposition, anneal, electrodes, stack, substrate, sample/device form.
- `FabricationProcess`: deposition method, annealing method, temperature, time, atmosphere.
- `PhaseStructure`: phase, space group, phase fraction, characterization method.
- `Device`: FeCAP, FeFET, FTJ, capacitor, FeRAM, related stack context.
- `FerroelectricProperty`: Pr, 2Pr, Ec, endurance, retention, leakage, memory window, secondary targets.
- `Evidence`: page-level text/table/caption/manual evidence.

## Primary Benchmark Targets

The first benchmark reports only:

- `remanent_polarization_Pr`
- `double_remanent_polarization_2Pr`

Secondary targets stay available for KG/RAG and supplemental analysis:

- `coercive_field_Ec`
- `endurance_cycles`
- `retention_time`
- `memory_window`
- `leakage_current_density`

## Benchmark Tiers

- `strong_only`: sample-level link, strong context, page evidence, valid normalized target, not rejected by AI audit.
- `strong_partial`: strong or partial sample-level/context rows that remain traceable and are not rejected.
- `all_traceable`: any row with paper, page, chunk, evidence, and a normalizable target.

Paper results should prioritize `strong_only` and use `strong_partial` as sensitivity analysis.

## Strong-Relevance Screening

Weakly related information is not deleted from the raw corpus. It is excluded from modeling slices using a non-destructive filter:

- keep HfO2/HZO/doped HfO2 sample-level rows;
- keep only primary Pr/2Pr targets for the first benchmark;
- require paper ID, page number, chunk ID, and evidence text;
- require `model_include = 1` after unit and range normalization;
- require selected benchmark tier, default `strong_only`;
- exclude AI audit statuses `reject`, `needs_human_review`, and `usable_for_rag_only`.

Outputs:

- `data/design/hfo2_design_dataset_strong_relevant.csv`
- `data/extraction_queues/strong_relevant_papers.csv`
- `data/extraction_queues/strong_relevant_chunks.csv`

## Train/Validation Protocol

Prediction performance is evaluated only on a validation holdout split, not on training rows.

- Split: fixed random seed train/validation holdout.
- Main metrics: MAE, RMSE, R2.
- Accuracy-style metrics: validation tolerance hit rate within 5 μC/cm², within 10 μC/cm², and within 10 percent relative error.
- Baseline: training-set mean predictor.
- Models: RandomForest, ExtraTrees, GradientBoosting, Ridge, ElasticNet, SVR-RBF.
- Graph models: planned GNN extension after tensorizing the design graph. Candidate models are GCN, GAT, and GraphSAGE; they should be evaluated with the same validation split and reported separately from the current tabular model comparison.

## Computational Feedback Layer

Computational validation is added after evidence-constrained design recommendation. It is not part of the gold truth for extraction and does not replace experimental literature evidence.

The first implementation only plans tasks:

- phase stability with VASP/DFT;
- oxygen-vacancy energetics and optional migration barriers;
- electrode/interface screening with slab DFT or surrogate descriptors;
- phase-field or compact switching models for thickness and boundary effects;
- ML-potential MD or kinetic surrogates for annealing and defect dynamics.

The planner writes:

- `data/computation/computational_feedback_tasks.csv`
- `data/computation/computational_feedback_tasks.json`
- `data/computation/computational_feedback_plan.md`

All tasks are marked `planned_only_no_local_execution` until a human approves cloud submission. Computed descriptors should be written back as additional KG and benchmark features, such as `deltaE_o_m_meV_fu`, `oxygen_vacancy_formation_energy_eV`, `interface_energy_proxy`, and `phase_field_pr_trend`.

## Gold Set

The gold set should cover 30 papers across:

- HZO
- pure HfO2
- doped HfO2
- FeCAP
- FeFET
- FTJ

Annotation fields:

- material system
- thickness
- deposition method
- annealing temperature/time/atmosphere
- electrode stack
- phase structure
- Pr
- 2Pr
- evidence page

Evaluation:

- entity extraction precision/recall/F1;
- sample-property linking accuracy;
- Pr/2Pr confusion rate;
- unit normalization error rate.

## Full Re-Extraction Plan

Use the strong-relevant paper queue for scoped full extraction:

```bash
python pipelines/33_filter_and_compare_models.py
python pipelines/05_run_extraction.py --paper-list data/extraction_queues/strong_relevant_papers.csv --incremental --commit-every 25 --progress-every 20 --max-workers 1
```

For a zero-cost reproducibility check:

```bash
python pipelines/05_run_extraction.py --paper-list data/extraction_queues/strong_relevant_papers.csv --no-llm --dry-run --limit-chunks 20
```

When LLM quota is available, enable DashScope from `.env`. The real key must stay local and must not be committed.
