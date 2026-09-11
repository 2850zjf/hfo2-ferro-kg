from types import SimpleNamespace

from backend.services import hfo2_parallel_extractor


def test_process_row_marks_connection_failure_as_transient(monkeypatch):
    monkeypatch.setattr(
        hfo2_parallel_extractor,
        "extract_chunk_with_llm",
        lambda row, model=None: SimpleNamespace(
            result=None,
            error_message="Connection error.",
            used_llm=True,
            usage=None,
        ),
    )
    row = {
        "paper_id": "paper_1",
        "pdf_id": "pdf_1",
        "chunk_id": "chunk_1",
        "page_number": 1,
    }

    item = hfo2_parallel_extractor._process_row(
        row,
        should_use_llm=True,
        llm_model="qwen3.7-max",
        ontology_version="hfo2-ferrokg-v2.3",
        llm_strict=True,
    )

    assert item["kind"] == "error"
    assert item["status"] == "extraction_error"
    assert item["transient_error"] is True
