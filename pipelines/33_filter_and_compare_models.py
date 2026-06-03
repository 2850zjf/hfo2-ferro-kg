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
    compare_regression_models,
    export_strong_relevance_extraction_queue,
    filter_strong_relevance_rows,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter weakly relevant rows and compare common regressors on strong HfO2/HZO data."
    )
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--filtered-output", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--queue-output-dir", type=Path, default=None)
    parser.add_argument("--targets", default=",".join(PRIMARY_TARGETS))
    parser.add_argument("--tiers", default="strong_only")
    parser.add_argument("--min-rows", type=int, default=30)
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
    model_stats = compare_regression_models(
        dataset_path=Path(filter_stats["output_path"]),
        output_dir=args.output_dir,
        targets=targets,
        min_rows=args.min_rows,
    )
    queue_stats = export_strong_relevance_extraction_queue(
        strong_dataset_path=Path(filter_stats["output_path"]),
        output_dir=args.queue_output_dir,
    )
    print(
        json.dumps(
            {
                "filter": filter_stats,
                "extraction_queue": queue_stats,
                "model_comparison": {
                    "metrics_csv": model_stats["metrics_csv"],
                    "metrics_json": model_stats["metrics_json"],
                    "report_md": model_stats["report_md"],
                    "best_by_target": model_stats["best_by_target"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
