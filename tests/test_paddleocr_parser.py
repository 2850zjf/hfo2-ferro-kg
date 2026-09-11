from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.services.paddleocr_parser import (
    PaddleOCRConfig,
    download_paddleocr_result,
    is_url,
    safe_source_stem,
    submit_paddleocr_job,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, text: str = "", content: bytes | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text)


class FakeSession:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return FakeResponse(200, {"data": {"jobId": "job_test"}})

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith("result.jsonl"):
            line = {
                "result": {
                    "layoutParsingResults": [
                        {
                            "markdown": {
                                "text": "# Page 1\n\nParsed text",
                                "images": {"figures/fig1.jpg": "https://example.test/fig1.jpg"},
                            },
                            "outputImages": {"layout": "https://example.test/layout.jpg"},
                        }
                    ]
                }
            }
            return FakeResponse(200, text=json.dumps(line), content=(json.dumps(line) + "\n").encode("utf-8"))
        if url.endswith(".jpg"):
            return FakeResponse(200, content=b"image-bytes")
        return FakeResponse(404, text="not found")


def test_url_helpers():
    assert is_url("https://example.com/paper.pdf")
    assert not is_url("/tmp/paper.pdf")
    assert safe_source_stem("https://example.com/a/b/My Paper.pdf?x=1") == "My_Paper"


def test_submit_local_file_uses_multipart(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    session = FakeSession()
    config = PaddleOCRConfig(token="secret")

    job_id = submit_paddleocr_job(pdf, config=config, session=session)

    assert job_id == "job_test"
    assert session.posts[0]["data"]["model"] == "PaddleOCR-VL-1.6"
    assert "file" in session.posts[0]["files"]
    assert "secret" in session.posts[0]["headers"]["Authorization"]


def test_download_result_writes_markdown_and_images(tmp_path: Path):
    session = FakeSession()
    config = PaddleOCRConfig(token="secret")
    payload = {
        "data": {
            "state": "done",
            "extractProgress": {"extractedPages": 1, "startTime": "s", "endTime": "e"},
            "resultUrl": {"jsonUrl": "https://example.test/result.jsonl"},
        }
    }

    result = download_paddleocr_result("paper.pdf", "job_test", payload, config, tmp_path, session=session)

    assert result.pages == 1
    assert (tmp_path / "markdown" / "doc_0000.md").read_text(encoding="utf-8").startswith("# Page 1")
    assert (tmp_path / "images" / "figures" / "fig1.jpg").read_bytes() == b"image-bytes"
    assert (tmp_path / "output_images" / "layout_0000.jpg").read_bytes() == b"image-bytes"
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["job_id"] == "job_test"
