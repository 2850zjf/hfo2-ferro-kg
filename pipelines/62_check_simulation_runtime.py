from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.simulation_runtime import check_simulation_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the local JAX and FerroX simulation runtime.")
    parser.add_argument("--jax-smoke", action="store_true")
    parser.add_argument("--ferrox-smoke", action="store_true")
    parser.add_argument("--status-path", type=Path, default=None)
    args = parser.parse_args()

    kwargs = {
        "run_jax_smoke": args.jax_smoke,
        "run_ferrox_smoke": args.ferrox_smoke,
    }
    if args.status_path:
        kwargs["status_path"] = args.status_path
    status = check_simulation_runtime(**kwargs)
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
