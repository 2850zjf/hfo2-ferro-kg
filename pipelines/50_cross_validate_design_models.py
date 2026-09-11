from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.model_comparison import (
    PRIMARY_TARGETS,
    cross_validate_regression_models,
    filter_strong_relevance_rows,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run K-fold cross-validation stability checks on strong HfO2/HZO design data."
    )
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--filtered-output", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--targets", default=",".join(PRIMARY_TARGETS))
    parser.add_argument("--tiers", default="strong_only")
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--allow-non-sample-level", action="store_true")
    args = parser.parse_args()

    targets = [item.strip() for item in args.targets.split(",") if item.strip()]
    tiers = {item.strip() for item in args.tiers.split(",") if item.strip()}
    filter_stats = filter_strong_relevance_rows(
        dataset_path=args.dataset,
        output_path=args.filtered_output,
        targets=targets,
        tiers=tiers,
        require_sample_level=not args.allow_non_sample_level,
    )
    cv_stats = cross_validate_regression_models(
        dataset_path=Path(filter_stats["output_path"]),
        output_dir=args.output_dir,
        targets=targets,
        min_rows=args.min_rows,
        folds=args.folds,
    )
    print(
        json.dumps(
            {
                "filter": filter_stats,
                "cross_validation": {
                    "fold_metrics_csv": cv_stats["fold_metrics_csv"],
                    "summary_csv": cv_stats["summary_csv"],
                    "summary_json": cv_stats["summary_json"],
                    "report_md": cv_stats["report_md"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
