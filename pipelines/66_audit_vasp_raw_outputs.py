from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.vasp_raw_audit import (  # noqa: E402
    DEFAULT_SOURCE_DIR,
    REPORTS_ROOT,
    audit_vasp_raw_outputs,
    export_vasp_raw_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read VASP POSCAR/CONTCAR/OUTCAR/vasprun.xml/relax.output directly, "
            "hash every audited artifact, and emit a non-publication-grade report. "
            "No database or external calculation is used."
        )
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Read-only HfO2 phase smoke-test run directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORTS_ROOT / "vasp_raw_output_audit",
        help="Output directory; must remain inside this worktree's reports/ directory.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit 2 unless every requested phase has parseable raw static and relaxation evidence.",
    )
    args = parser.parse_args()

    audit = audit_vasp_raw_outputs(args.source_dir)
    paths = export_vasp_raw_audit(audit, args.output_dir)
    response = {
        "status": audit["status"],
        "publication_grade": audit["publication_grade"],
        "all_requested_phases_auditable": audit["all_requested_phases_auditable"],
        "publication_blockers": audit["publication_blockers"],
        "outputs": paths,
    }
    print(json.dumps(response, ensure_ascii=False, indent=2))
    if args.require_complete and not audit["all_requested_phases_auditable"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
