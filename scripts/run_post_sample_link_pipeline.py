from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _active_sample_linking() -> bool:
    from backend.services.progress_monitor import active_pipeline_processes

    return any(row.get("task") == "23_link_sample_facts.py" for row in active_pipeline_processes())


def run_step(name: str, command: list[str], log_fh, allow_fail: bool = False) -> int:
    started = datetime.now().isoformat(timespec="seconds")
    line = f"\n===== {name} started {started} =====\ncommand: {' '.join(command)}\n"
    print(line, flush=True)
    log_fh.write(line)
    log_fh.flush()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for output_line in process.stdout:
        print(output_line, end="", flush=True)
        log_fh.write(output_line)
        log_fh.flush()
    code = process.wait()
    ended = datetime.now().isoformat(timespec="seconds")
    footer = f"\n===== {name} exit_code={code} ended {ended} =====\n"
    print(footer, flush=True)
    log_fh.write(footer)
    log_fh.flush()
    if code != 0 and not allow_fail:
        raise SystemExit(code)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description="Run downstream design tasks after sample-level linking finishes.")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--with-ai-audit", action="store_true")
    parser.add_argument("--ai-workers", type=int, default=16)
    parser.add_argument("--design-min-rows", type=int, default=12)
    args = parser.parse_args()

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"post_sample_link_pipeline_{stamp}.log"
    with log_path.open("w", encoding="utf-8") as log_fh:
        while _active_sample_linking():
            message = (
                f"{datetime.now().isoformat(timespec='seconds')} "
                f"waiting for 23_link_sample_facts.py to finish..."
            )
            print(message, flush=True)
            log_fh.write(message + "\n")
            log_fh.flush()
            time.sleep(max(5, args.poll_seconds))

        steps: list[tuple[str, list[str], bool]] = [
            ("build_design_graph", [sys.executable, "pipelines/24_build_design_graph.py"], False),
        ]
        if args.with_ai_audit:
            steps.append(
                (
                    "llm_ai_secondary_audit",
                    [
                        sys.executable,
                        "pipelines/28_ai_audit_sample_links.py",
                        "--max-workers",
                        str(args.ai_workers),
                        "--commit-every",
                        "50",
                        "--progress-every",
                        "25",
                        "--force-llm-when-paused",
                    ],
                    True,
                )
            )
        steps.extend(
            [
                ("build_design_dataset", [sys.executable, "pipelines/21_build_design_dataset.py"], False),
                ("build_benchmark_tiers", [sys.executable, "pipelines/29_build_benchmark_tiers.py"], False),
                (
                    "train_tiered_design_models",
                    [
                        sys.executable,
                        "pipelines/30_train_tiered_design_models.py",
                        "--min-rows",
                        str(args.design_min_rows),
                    ],
                    False,
                ),
                ("recommend_active_learning", [sys.executable, "pipelines/25_recommend_active_learning.py"], False),
                ("export_design_report", [sys.executable, "pipelines/31_export_design_report.py"], False),
            ]
        )
        for name, command, allow_fail in steps:
            run_step(name, command, log_fh, allow_fail=allow_fail)
        print(f"post sample-link pipeline done log={log_path}", flush=True)


if __name__ == "__main__":
    main()
