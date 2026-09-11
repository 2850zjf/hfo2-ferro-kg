from backend.services import progress_monitor
from backend.services.progress_monitor import (
    _current_run_log_text,
    _parse_posix_process_output,
    parse_progress_from_log,
)


def test_parse_posix_process_output_detects_extraction_process():
    output = (
        "  21659 Fri Jul 11 17:07:28 2026 "
        "python pipelines/05_run_extraction.py --chunk-list phase_a.csv --max-workers 2\n"
        "  21660 Fri Jul 11 17:07:29 2026 python unrelated.py\n"
    )

    rows = _parse_posix_process_output(output)

    assert len(rows) == 1
    assert rows[0]["pid"] == 21659
    assert rows[0]["task"] == "05_run_extraction.py"
    assert rows[0]["started_at"] == "2026-07-11T17:07:28"


def test_parse_progress_uses_latest_resumed_run(tmp_path):
    log_path = tmp_path / "phase.log"
    log_path.write_text(
        "\n".join(
            [
                "===== publication_v23:a_primary_dense started 2026-07-11T16:00:00 =====",
                "processed=20 candidates=19 llm_used=19 llm_failed=0 empty=0 errors=1",
                "===== publication_v23:a_primary_dense started 2026-07-11T17:24:18 =====",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_progress_from_log(log_path) == {"type": "unknown"}

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\nprocessed=5 candidates=5 llm_used=5 llm_failed=0 empty=0 errors=0\n")

    assert parse_progress_from_log(log_path) == {
        "type": "structured_llm",
        "processed": 5,
        "candidates": 5,
        "llm_used": 5,
        "llm_failed": 0,
        "empty": 0,
        "errors": 0,
    }


def test_current_run_log_text_hides_interrupted_history():
    text = "\n".join(
        [
            "===== publication_v23:a_primary_dense started 2026-07-11T16:00:00 =====",
            "Traceback: old failure",
            "===== publication_v23:a_primary_dense started 2026-07-11T17:24:18 =====",
            "processed=5 candidates=5 llm_used=5 llm_failed=0 empty=0 errors=0",
        ]
    )

    current = _current_run_log_text(text)

    assert "old failure" not in current
    assert current.startswith("===== publication_v23:a_primary_dense started 2026-07-11T17:24:18 =====")


def test_extraction_log_selection_ignores_all_phase_controller(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs" / "publication_v23"
    log_dir.mkdir(parents=True)
    (log_dir / "all_phases.log").write_text("controller", encoding="utf-8")
    (log_dir / "retry_sweep.log").write_text("retry controller", encoding="utf-8")
    phase_log = log_dir / "a_primary_dense.log"
    phase_log.write_text("processed=5", encoding="utf-8")
    monkeypatch.setattr(progress_monitor, "PROJECT_ROOT", tmp_path)

    logs = progress_monitor._logs_for_task("05_run_extraction.py")

    assert logs == [phase_log]
