from __future__ import annotations

import csv
import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.services.phase_benchmark import validate_annotation_row
from backend.services.phase_llm_preannotation import (
    AliyunJSONClient,
    PhasePreannotationError,
    RuntimeLLMConfig,
    load_aliyun_config_from_env,
    preannotate_phase_queue,
)


def _queue(path: Path) -> None:
    fields = [
        "annotation_id",
        "title",
        "material_system",
        "material_name",
        "zr_fraction",
        "phase_raw",
        "phase_labels",
        "characterization_method",
        "phase_evidence_source",
        "phase_evidence_text",
        "property_evidence_text",
        "current_conditions_json",
        "condition_suggestions_json",
        "quality_flags",
        "review_phase_label",
        "review_phase_scope",
        "review_sample_conditions",
        "review_source_type",
        "adjudication_status",
        "annotator",
        "review_notes",
        "paper_group",
        "doi",
        "paper_id",
        "pdf_id",
        "page_number",
        "chunk_id",
    ]
    row = {
        "annotation_id": "a1",
        "title": "Fixture paper",
        "material_system": "HZO",
        "material_name": "Hf0.5Zr0.5O2",
        "zr_fraction": "0.5",
        "phase_raw": "orthorhombic",
        "phase_labels": "o",
        "characterization_method": "GIXRD",
        "phase_evidence_source": "direct",
        "phase_evidence_text": "GIXRD identifies an orthorhombic phase in the 10 nm HZO film.",
        "property_evidence_text": "The film has a remanent polarization of 20 microC/cm2.",
        "current_conditions_json": json.dumps({"film_thickness_nm": 10}),
        "condition_suggestions_json": "{}",
        "quality_flags": "",
        "review_phase_label": "",
        "review_phase_scope": "",
        "review_sample_conditions": "",
        "review_source_type": "",
        "adjudication_status": "pending_human_annotation",
        "annotator": "",
        "review_notes": "",
        "paper_group": "doi:10.1000/fixture",
        "doi": "10.1000/fixture",
        "paper_id": "p1",
        "pdf_id": "pdf1",
        "page_number": "3",
        "chunk_id": "c1",
    }
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)


class _FakeClient:
    def __init__(self, data: dict) -> None:
        self.data = data

    def complete_json(self, record: dict) -> dict:
        assert "annotation_id" not in record
        assert "title" not in record
        assert "phase_labels" not in record
        assert "quality_flags" not in record
        return {
            "data": self.data,
            "model": "qwen-fixture",
            "request_id": "request-fixture",
            "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
        }


def _suggestion(*, quote: str | None = None) -> dict:
    phase_quote = quote or "GIXRD identifies an orthorhombic phase"
    return {
        "status": "suggested",
        "phase_label": "o",
        "phase_scope": "dominant_bulk_average",
        "source_type": "primary_experiment",
        "evidence_support": "direct_explicit",
        "binding_assessment": "consistent",
        "uncertainty": "low",
        "supporting_quote": phase_quote,
        "condition_support": [
            {
                "field": "film_thickness_nm",
                "value": 10,
                "supporting_quote": "10 nm HZO film",
            }
        ],
        "reason_codes": ["explicit_gixrd_phase"],
        "abstention_reason": "",
    }


def test_runtime_env_config_is_allowlisted_and_secret_repr_is_redacted() -> None:
    secret = "sk-fixture-secret-value-123456"
    config = load_aliyun_config_from_env({"DASHSCOPE_API_KEY": secret})
    assert config is not None
    assert config.api_key == secret
    assert config.model == "qwen3.7-max"
    assert secret not in repr(config)
    assert secret not in json.dumps(config.public_metadata())


def test_runtime_env_accepts_qwen38_and_rejects_unlisted_qwen() -> None:
    accepted = load_aliyun_config_from_env({"DASHSCOPE_API_KEY": "secret", "HFO2_FERROKG_LLM_MODEL": "qwen3.8-max"})
    assert accepted is not None and accepted.model == "qwen3.8-max"
    with pytest.raises(ValueError, match="model_not_allowlisted"):
        load_aliyun_config_from_env({"DASHSCOPE_API_KEY": "secret", "HFO2_FERROKG_LLM_MODEL": "qwen-future-unknown"})


def test_runtime_env_rejects_non_dashscope_endpoint() -> None:
    with pytest.raises(ValueError, match="not_allowlisted"):
        load_aliyun_config_from_env({"DASHSCOPE_API_KEY": "secret", "HFO2_FERROKG_LLM_BASE_URL": "https://example.invalid/v1"})


def test_runtime_env_missing_key_disables_optional_llm() -> None:
    assert load_aliyun_config_from_env({}) is None


def test_client_uses_json_contract_without_exposing_secret() -> None:
    secret = "sk-fixture-secret-value-123456"
    observed: dict = {}

    def transport(request, timeout):
        observed["authorization"] = request.headers["Authorization"]
        observed["timeout"] = timeout
        response = {
            "model": "qwen-fixture",
            "choices": [{"message": {"content": json.dumps(_suggestion())}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return 200, json.dumps(response).encode(), {"X-Request-Id": "req-1"}

    client = AliyunJSONClient(
        RuntimeLLMConfig(
            api_key=secret,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-fixture",
        ),
        transport=transport,
    )
    result = client.complete_json({"annotation_id": "a1"})
    assert result["data"]["phase_label"] == "o"
    assert result["request_id"] == "req-1"
    assert observed["authorization"] == "Bearer " + secret

    def unauthorized(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    failing = AliyunJSONClient(client.config, transport=unauthorized, max_retries=0)
    with pytest.raises(PhasePreannotationError) as exc_info:
        failing.complete_json({"annotation_id": "a1"})
    assert exc_info.value.code == "provider_http_401"
    assert secret not in str(exc_info.value)


def test_preannotation_preserves_human_gate_and_stays_out_of_benchmark(tmp_path: Path) -> None:
    queue = tmp_path / "queue.csv"
    _queue(queue)
    summary = preannotate_phase_queue(
        queue,
        tmp_path / "output",
        _FakeClient(_suggestion()),
        max_rows=1,
        generated_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert summary["status"] == "completed"
    assert summary["human_gate_fields_modified"] == 0
    assert summary["benchmark_eligible_rows_created"] == 0
    assert summary["production_database_accessed"] is False
    assert summary["machine_status_counts"] == {"suggested": 1}
    with Path(summary["output_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["machine_phase_label"] == "o"
    assert row["machine_supporting_quote"] == "GIXRD identifies an orthorhombic phase"
    assert row["machine_only"] == "True"
    assert row["gold_eligible"] == "False"
    assert "review_phase_label" not in row
    assert "adjudication_status" not in row
    normalized, reasons = validate_annotation_row(row)
    assert normalized is None
    assert "not_adjudicated" in reasons


def test_unsupported_machine_quote_forces_abstention(tmp_path: Path) -> None:
    queue = tmp_path / "queue.csv"
    _queue(queue)
    summary = preannotate_phase_queue(
        queue,
        tmp_path / "output",
        _FakeClient(_suggestion(quote="A fabricated quote that is absent")),
        max_rows=1,
    )
    with Path(summary["output_csv"]).open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["machine_preannotation_status"] == "abstained"
    assert row["machine_phase_label"] == "unresolved"
    assert "unsupported_machine_quote" in row["machine_validation_flags"]


def test_preannotation_cli_entrypoint_can_be_executed_directly() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "pipelines" / "65_preannotate_phase_queue.py"), "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--credential-docx" not in result.stdout
