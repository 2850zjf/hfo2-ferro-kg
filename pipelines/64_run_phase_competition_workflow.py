from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.phase_benchmark import BenchmarkThresholds
from backend.services.phase_competition_workflow import run_phase_competition_workflow


def _read_groups(path: Path | None) -> list[str]:
    if path is None:
        return []
    groups = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if text and not text.startswith("#") and text.lower() not in {"doi", "paper_group"}:
            groups.append(text.split(",", 1)[0].strip())
    return groups


def _worktree_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise ValueError(f"Output must stay inside the research worktree: {PROJECT_ROOT}")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the read-only HfO2/HZO phase-competition workflow through its "
            "human-annotation and benchmark readiness gates."
        )
    )
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--locked-validation-groups", type=Path)
    parser.add_argument("--min-similarity", type=float, default=0.80)
    parser.add_argument("--min-comparable-coverage", type=float, default=0.25)
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--max-annotation-rows", type=int, default=1000)
    parser.add_argument("--row-limit", type=int)
    parser.add_argument("--min-total-paper-groups", type=int, default=30)
    parser.add_argument("--min-train-groups-per-phase", type=int, default=15)
    parser.add_argument("--min-validation-groups-per-phase", type=int, default=3)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()

    summary = run_phase_competition_workflow(
        args.db_path,
        _worktree_output(args.output_dir),
        annotations_path=args.annotations,
        locked_validation_groups=_read_groups(args.locked_validation_groups),
        min_similarity=args.min_similarity,
        min_comparable_coverage=args.min_comparable_coverage,
        max_candidates=args.max_candidates,
        max_annotation_rows=args.max_annotation_rows,
        row_limit=args.row_limit,
        thresholds=BenchmarkThresholds(
            min_total_paper_groups=args.min_total_paper_groups,
            min_train_groups_per_phase=args.min_train_groups_per_phase,
            min_validation_groups_per_phase=args.min_validation_groups_per_phase,
        ),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.require_ready and summary["status"] != "ready_for_training":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

