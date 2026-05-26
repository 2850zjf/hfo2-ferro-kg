from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-workers", type=int, default=32)
    parser.add_argument("--structured-workers", type=int, default=32)
    parser.add_argument("--table-timeout", type=int, default=90)
    parser.add_argument("--benchmark-reset", action="store_true")
    parser.add_argument("--skip-structured", action="store_true")
    parser.add_argument("--reset-structured", action="store_true")
    parser.add_argument("--skip-design-models", action="store_true")
    parser.add_argument("--skip-sample-linking", action="store_true")
    parser.add_argument("--skip-active-learning", action="store_true")
    parser.add_argument("--design-min-rows", type=int, default=12)
    args = parser.parse_args()

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"full_benchmark_pipeline_{stamp}.log"
    summary: dict[str, int | str] = {"log_path": str(log_path), "started": stamp}
    with log_path.open("w", encoding="utf-8") as log_fh:
        log_fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
        steps = [
            ("init_db", [sys.executable, "backend/db/init_db.py"]),
            ("manifest", [sys.executable, "pipelines/01_build_manifest.py"]),
            ("parse_pdfs_incremental", [sys.executable, "pipelines/02_parse_pdfs.py"]),
            (
                "extract_tables_incremental",
                [
                    sys.executable,
                    "pipelines/03_extract_tables.py",
                    "--incremental",
                    "--timeout-seconds",
                    str(args.table_timeout),
                    "--progress-every",
                    "5",
                ],
            ),
            ("extract_visual_assets_incremental", [sys.executable, "pipelines/15_extract_visual_assets.py"]),
            ("rebuild_chunks", [sys.executable, "pipelines/04_chunk_documents.py"]),
        ]
        if not args.skip_structured:
            structured_command = [
                sys.executable,
                "pipelines/05_run_extraction.py",
                "--model",
                os.getenv("HFO2_FERROKG_LLM_MODEL", "qwen3.7-max"),
                "--commit-every",
                "20",
                "--progress-every",
                "10",
                "--max-workers",
                str(args.structured_workers),
            ]
            if not args.reset_structured:
                structured_command.append("--incremental")
            steps.append(
                (
                    "structured_hfo2_extraction",
                    structured_command,
                )
            )
        benchmark_command = [
            sys.executable,
            "pipelines/19_open_benchmark_extraction.py",
            "--max-workers",
            str(args.llm_workers),
            "--commit-every",
            "20",
            "--progress-every",
            "10",
        ]
        if args.benchmark_reset:
            benchmark_command.append("--reset")
        steps.extend(
            [
                ("open_benchmark_llm_extraction", benchmark_command),
                ("benchmark_ontology_extension", [sys.executable, "pipelines/20_update_ontology_from_benchmark.py"]),
                ("enrich_fact_relations", [sys.executable, "pipelines/16_enrich_fact_relations.py"]),
                ("build_graph", [sys.executable, "pipelines/07_build_graph.py"]),
                ("export_graph_html", [sys.executable, "pipelines/17_export_graph_html.py"]),
                ("build_vector_index", [sys.executable, "pipelines/08_build_vector_index.py"]),
            ]
        )
        if not args.skip_sample_linking:
            steps.extend(
                [
                    (
                        "link_sample_level_facts",
                        [
                            sys.executable,
                            "pipelines/23_link_sample_facts.py",
                            "--max-workers",
                            str(args.llm_workers),
                            "--progress-every",
                            "25",
                            "--commit-every",
                            "100",
                            "--force-llm-when-paused",
                        ],
                    ),
                    ("build_design_graph", [sys.executable, "pipelines/24_build_design_graph.py"]),
                ]
            )
        steps.append(("build_design_dataset", [sys.executable, "pipelines/21_build_design_dataset.py"]))
        if not args.skip_design_models:
            steps.append(
                (
                    "train_design_models",
                    [
                        sys.executable,
                        "pipelines/22_train_design_models.py",
                        "--min-rows",
                        str(args.design_min_rows),
                    ],
                )
            )
        if not args.skip_active_learning:
            steps.append(("recommend_active_learning", [sys.executable, "pipelines/25_recommend_active_learning.py"]))
        for name, command in steps:
            run_step(name, command, log_fh)
        log_fh.write("\n===== full benchmark pipeline done =====\n")
        print(f"done log={log_path}", flush=True)


if __name__ == "__main__":
    main()
