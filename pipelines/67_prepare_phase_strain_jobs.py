from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.phase_strain_job_builder import (  # noqa: E402
    DEFAULT_SOURCE_ROOT,
    prepare_phase_strain_jobs,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare the frozen three-point HfO2 m/o/t biaxial-strain VASP dry-run package. "
            "This command never copies POTCAR, invokes VASP, submits a job, reads .env, or reads a database."
        )
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help="Read-only directory containing monoclinic/orthorhombic/tetragonal/CONTCAR.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "computations" / "phase_strain_pilot" / "generated_v0_1",
        help="New output directory inside this research worktree; existing directories are never overwritten.",
    )
    args = parser.parse_args()
    summary = prepare_phase_strain_jobs(
        args.source_root,
        args.output_dir,
        allowed_output_root=PROJECT_ROOT,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["status"] != "prepared_dry_run_not_executed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

