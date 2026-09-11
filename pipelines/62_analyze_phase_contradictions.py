from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.phase_contradiction_analysis import (
    build_locked_split_manifest,
    build_paper_group_inventory,
    build_phase_annotation_queue,
    export_candidate_analysis,
    find_phase_contradiction_candidates,
    load_phase_observations,
)


def _read_locked_groups(path: Path | None) -> list[str]:
    if path is None:
        return []
    values = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if text and not text.startswith("#") and text.lower() not in {"doi", "paper_group"}:
            values.append(text.split(",", 1)[0].strip())
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build review-only cross-paper HfO2/HZO phase contradiction candidates from a read-only SQLite database."
    )
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-similarity", type=float, default=0.80)
    parser.add_argument("--min-comparable-coverage", type=float, default=0.25)
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument("--row-limit", type=int)
    parser.add_argument("--max-annotation-rows", type=int, default=500)
    parser.add_argument(
        "--locked-validation-groups",
        type=Path,
        help="Text/CSV first column containing curated DOI values or paper_group keys. No automatic validation selection is made.",
    )
    args = parser.parse_args()

    observations = load_phase_observations(args.db_path, limit=args.row_limit)
    candidates = find_phase_contradiction_candidates(
        observations,
        min_similarity=args.min_similarity,
        min_comparable_coverage=args.min_comparable_coverage,
        max_candidates=args.max_candidates,
    )
    annotation_queue = build_phase_annotation_queue(
        observations,
        max_rows=args.max_annotation_rows,
    )
    paper_group_inventory = build_paper_group_inventory(observations)
    locked_groups = _read_locked_groups(args.locked_validation_groups)
    split_manifest = (
        build_locked_split_manifest(observations, locked_groups)
        if args.locked_validation_groups is not None
        else []
    )
    summary = export_candidate_analysis(
        candidates,
        args.output_dir,
        observations=observations,
        split_manifest=split_manifest,
        annotation_queue=annotation_queue,
        paper_group_inventory=paper_group_inventory,
        parameters={
            "min_similarity": args.min_similarity,
            "min_comparable_coverage": args.min_comparable_coverage,
            "max_candidates": args.max_candidates,
            "row_limit": args.row_limit,
            "max_annotation_rows": args.max_annotation_rows,
            "database_access": "SQLite mode=ro&immutable=1; PRAGMA query_only=ON",
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
