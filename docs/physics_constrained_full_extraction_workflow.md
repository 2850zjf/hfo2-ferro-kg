# Physics-Constrained Full Extraction Workflow

Date: 2026-06-10

## Purpose

This workflow turns HfO2-FerroKG from a one-off extraction workspace into a reusable pipeline for:

1. expanding the global HfO2/HZO literature library,
2. extracting comprehensive sample-level facts with Qwen/DashScope,
3. enforcing physics constraints during dataset/model/recommendation stages,
4. feeding benchmark/model/computation workflows.

## Reusable Commands

### 1. Expand Global Literature

```bash
.venv/bin/python pipelines/35_expand_global_hzo_literature.py \
  --rows-per-source 40 \
  --download-limit 30 \
  --min-score 0.35
```

This calls OpenAlex/Crossref with broad HfO2/HZO queries and downloads only verified open-access PDFs.

### 2. Rebuild Local Library

```bash
.venv/bin/python pipelines/01_build_manifest.py
.venv/bin/python pipelines/02_parse_pdfs.py
.venv/bin/python pipelines/03_extract_tables.py --incremental --timeout-seconds 30 --progress-every 50
.venv/bin/python pipelines/04_chunk_documents.py
```

The table step is important because process/property benchmarks often appear in tables.

### 3. Prepare Full LLM Extraction Queues

Recommended first pass:

```bash
.venv/bin/python pipelines/36_prepare_full_llm_extraction.py \
  --include-existing \
  --limit-chunks 3000
```

More exhaustive pass:

```bash
.venv/bin/python pipelines/36_prepare_full_llm_extraction.py \
  --all-chunks \
  --include-existing \
  --limit-chunks 8000
```

Outputs:

- `data/extraction_queues/full_hzo_llm_high_value_chunks.csv`
- `data/extraction_queues/full_hzo_llm_all_chunks.csv`
- corresponding paper queues, JSON plans, and Markdown runbooks.

### 4. Dry Run Qwen Extraction

```bash
.venv/bin/python pipelines/05_run_extraction.py \
  --chunk-list data/extraction_queues/full_hzo_llm_high_value_chunks.csv \
  --model qwen3.7-max \
  --commit-every 10 \
  --progress-every 10 \
  --max-workers 1 \
  --dry-run \
  --limit-chunks 5
```

Dry-run calls the LLM but does not write database rows.

### 5. Run a Resumable Batch

Small validation batch:

```bash
.venv/bin/python pipelines/05_run_extraction.py \
  --chunk-list data/extraction_queues/full_hzo_llm_high_value_chunks.csv \
  --model qwen3.7-max \
  --commit-every 5 \
  --progress-every 5 \
  --max-workers 3 \
  --limit-chunks 12
```

Full high-value re-extraction:

```bash
.venv/bin/python pipelines/05_run_extraction.py \
  --chunk-list data/extraction_queues/full_hzo_llm_high_value_chunks.csv \
  --model qwen3.7-max \
  --commit-every 10 \
  --progress-every 10 \
  --max-workers 3
```

Use `--incremental` only when you want to skip existing extracted chunks. For schema/prompt upgrades, omit `--incremental` so selected chunks are re-extracted.

### 6. Rebuild Datasets and Apply Physics Constraints

```bash
.venv/bin/python pipelines/21_build_design_dataset.py
.venv/bin/python pipelines/29_build_benchmark_tiers.py
.venv/bin/python pipelines/37_apply_physical_constraints.py \
  --input data/design/hfo2_design_dataset.csv \
  --output data/design/hfo2_design_dataset_physics_constrained.csv
```

### 7. Model Validation

```bash
.venv/bin/python pipelines/33_filter_and_compare_models.py
.venv/bin/python pipelines/10_validate_results.py
```

## Physics Constraint Layer

Source:

- `ontology/physical_constraints.yaml`
- `backend/services/physical_constraints.py`

Key outputs added to design datasets:

- `physical_consistency_score`
- `physical_recommendation_allowed`
- `phase_stability_score`
- `oxygen_vacancy_risk`
- `interface_oxygen_affinity`
- `process_window_score`
- `evidence_completeness_score`
- `physical_hard_violations`
- `physical_soft_warnings`
- `physical_risk_flags`
- `physical_descriptor_json`

The same constraint layer is callable from extraction prompts, design datasets, model features, and active-learning recommendations.

## Current Smoke-Test Result

The first Qwen write-path batch processed 12 high-value chunks:

- chunks: 12
- candidates: 12
- llm_used: 12
- llm_failed: 0
- errors: 0
- preapproved_machine: 3
- needs_human_review: 9

This proves the upgraded schema, physical prompt context, Qwen call, and database write path are functioning.

## Current Corpus Snapshot

After global expansion and parsing:

- PDF records: 1172
- parsed pages: 14808
- document chunks: 40258
- high-value chunks: 27715
- table chunks: 2269
- literature candidates: 549
- downloaded OA candidates: 122

## Current Benchmark Snapshot

After rebuilding datasets:

- design dataset rows: 12302
- strong_only rows: 992
- strong_partial rows: 4731
- all_traceable rows: 12173
- physics-constrained rows: 12302
- recommendation-allowed rows: 12257
- mean physical consistency score: 0.698

Strong relevant modeling slice:

- kept rows: 362
- Pr rows: 219
- 2Pr rows: 143
- Pr best model: RandomForest, validation MAE 10.39, RMSE 14.31, R2 0.160
- 2Pr best model: ExtraTrees, validation MAE 13.57, RMSE 17.79, R2 0.318

