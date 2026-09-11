from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.computation_validation import prepare_computation_validation_jobs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare safe computation validation job packages without launching calculations."
    )
    parser.add_argument("--tasks", type=Path, default=None)
    parser.add_argument("--jobs-dir", type=Path, default=None)
    parser.add_argument("--max-jobs", type=int, default=12)
    parser.add_argument("--task-family", default=None)
    parser.add_argument("--execution-mode", default="prepare_only")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    stats = prepare_computation_validation_jobs(
        tasks_path=args.tasks,
        jobs_dir=args.jobs_dir,
        max_jobs=args.max_jobs,
        task_family=args.task_family,
        execution_mode=args.execution_mode,
        force=args.force,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
