from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.computation_workflow import run_computation_workflow


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the safe automatic HfO2/HZO computation workflow."
    )
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--jobs-dir", type=Path, default=None)
    parser.add_argument("--workflow-dir", type=Path, default=None)
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--max-tasks", type=int, default=80)
    parser.add_argument("--max-jobs", type=int, default=12)
    parser.add_argument("--task-family", default="phase_stability")
    parser.add_argument("--all-task-families", action="store_true")
    parser.add_argument("--force-prepare", action="store_true")
    parser.add_argument(
        "--fetch-mp-structures",
        action="store_true",
        help="Fetch Materials Project structures only. Does not run VASP or submit cloud jobs.",
    )
    parser.add_argument("--max-structure-fetch-jobs", type=int, default=4)
    parser.add_argument("--import-results", type=Path, default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--no-cloud-template", action="store_true")
    args = parser.parse_args()

    stats = run_computation_workflow(
        candidates_path=args.candidates,
        output_dir=args.output_dir,
        jobs_dir=args.jobs_dir,
        workflow_dir=args.workflow_dir,
        max_candidates=args.max_candidates,
        max_tasks=args.max_tasks,
        max_jobs=args.max_jobs,
        task_family=None if args.all_task_families else args.task_family,
        force_prepare=args.force_prepare,
        fetch_mp_structures=args.fetch_mp_structures,
        max_structure_fetch_jobs=args.max_structure_fetch_jobs,
        import_results_path=args.import_results,
        import_job_id=args.job_id,
        write_cloud_template=not args.no_cloud_template,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
