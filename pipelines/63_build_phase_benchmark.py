from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.phase_benchmark import BenchmarkThresholds, build_phase_benchmark


def _read_groups(path: Path | None) -> list[str]:
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
        description="Build a leakage-safe phase benchmark from human-adjudicated annotations."
    )
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--locked-validation-groups", type=Path)
    parser.add_argument("--paper-inventory", type=Path)
    parser.add_argument("--min-total-paper-groups", type=int, default=30)
    parser.add_argument("--min-train-groups-per-phase", type=int, default=15)
    parser.add_argument("--min-validation-groups-per-phase", type=int, default=3)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()

    summary = build_phase_benchmark(
        args.annotations,
        args.output_dir,
        locked_validation_groups=_read_groups(args.locked_validation_groups),
        thresholds=BenchmarkThresholds(
            min_total_paper_groups=args.min_total_paper_groups,
            min_train_groups_per_phase=args.min_train_groups_per_phase,
            min_validation_groups_per_phase=args.min_validation_groups_per_phase,
        ),
        paper_inventory_path=args.paper_inventory,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.require_ready and summary["status"] != "ready_for_training":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

