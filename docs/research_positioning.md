# HfO2-FerroKG research positioning

## Problem to solve

HfO2-based ferroelectrics combine nanoscale ferroelectricity, CMOS compatibility,
and device relevance for FeCAP, FeFET, FTJ, negative-capacitance, and
neuromorphic-memory research. The literature is growing quickly, but key facts
are fragmented across PDFs, tables, figure captions, and supplementary contexts.

The bottleneck is preserving the exact material-process-structure-property chain:

- material chemistry: HfO2, HZO, doped hafnia, dopant level, Hf/Zr ratio
- process: ALD/sputtering/PLD, thickness, electrode, annealing temperature/time/atmosphere
- structure: orthorhombic Pca21 phase, mixed phases, oxygen-vacancy/interface context
- property: Pr, 2Pr, Ec, endurance, retention, leakage, memory window, wake-up and fatigue
- evidence: paper, DOI, page, chunk, original sentence or table row

This project therefore treats each extracted value as a traceable claim rather
than a clean number. The goal is a reusable evidence graph, not a one-off spreadsheet.

## Method direction

The first production strategy is an ontology-first hybrid extractor:

1. Build and version the HfO2-FerroKG ontology before every extraction run.
2. Use deterministic rules for high-precision units, formulas, phases, electrodes, and common metrics.
3. Use LLM structured extraction when an API key is available for mechanisms and cross-sentence relations.
4. Preserve machine pre-audit status and route suspicious claims to later human review.
5. Rebuild graph and retrieval indexes from reviewed or machine-preaudited facts only.

## Continuous ingestion

New literature should enter through a candidate pipeline:

1. Discover candidate papers from public scholarly metadata sources.
2. Download only verified open-access PDFs with clear PDF URLs.
3. Validate downloaded files by PDF signature, size, page count, hash, and parse quality.
4. Rebuild the manifest without moving or renaming existing PDFs.
5. Parse, table-extract, chunk, and extract in incremental mode.
6. Keep old candidates and facts tied to ontology and extractor versions.

This keeps the corpus expandable without breaking reproducibility.

## Current method upgrades planned

- Add DOI-level deduplication in addition to file-hash deduplication.
- Add relation-level confidence and contradiction checks for Pr/2Pr, Ec, sample matching, and review-vs-primary data.
- Add table-aware extraction because tables are a high-value source of materials facts.
- Add active-learning loops: later corrections become examples for prompt calibration or fine-tuning.
- Add graph-assisted RAG so answers combine chunk retrieval, fact tables, and graph paths.
