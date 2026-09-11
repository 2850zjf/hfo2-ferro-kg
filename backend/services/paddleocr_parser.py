from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from backend.core.config import PROJECT_ROOT


DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"
DEFAULT_OPTIONAL_PAYLOAD = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}


class RequestSession(Protocol):
    def post(self, url: str, **kwargs: Any) -> requests.Response: ...

    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


@dataclass(frozen=True)
class PaddleOCRConfig:
    token: str
    job_url: str = DEFAULT_JOB_URL
    model: str = DEFAULT_MODEL
    optional_payload: dict[str, Any] | None = None
    poll_interval_seconds: float = 5.0
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class PaddleOCRResult:
    job_id: str
    state: str
    source: str
    output_dir: str
    pages: int
    markdown_files: list[str]
    image_files: list[str]
    jsonl_path: str
    manifest_path: str
    started_at: str | None = None
    completed_at: str | None = None


def get_paddleocr_token(explicit_token: str | None = None) -> str | None:
    token = explicit_token or os.getenv("PADDLEOCR_TOKEN") or os.getenv("PADDLEOCR_API_TOKEN")
    if token:
        return token.strip()
    return None


def default_output_root() -> Path:
    return PROJECT_ROOT / "data" / "paddleocr"


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"}


def safe_source_stem(source: str) -> str:
    if is_url(source):
        parsed = urlparse(source)
        stem = Path(parsed.path).stem or parsed.netloc or "url_document"
    else:
        stem = Path(source).stem
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in stem).strip("_")
    return safe[:80] or "document"


def output_dir_for_source(source: str, output_root: Path | None = None) -> Path:
    root = output_root or default_output_root()
    return root / f"{safe_source_stem(source)}_{uuid.uuid4().hex[:8]}"


def _headers(token: str, json_mode: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"bearer {token}"}
    if json_mode:
        headers["Content-Type"] = "application/json"
    return headers


def submit_paddleocr_job(
    source: str | Path,
    config: PaddleOCRConfig,
    session: RequestSession | None = None,
) -> str:
    session = session or requests.Session()
    source_str = str(source)
    optional_payload = config.optional_payload or DEFAULT_OPTIONAL_PAYLOAD

    if is_url(source_str):
        payload = {
            "fileUrl": source_str,
            "model": config.model,
            "optionalPayload": optional_payload,
        }
        response = session.post(
            config.job_url,
            json=payload,
            headers=_headers(config.token, json_mode=True),
            timeout=config.timeout_seconds,
        )
    else:
        path = Path(source_str).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        data = {
            "model": config.model,
            "optionalPayload": json.dumps(optional_payload, ensure_ascii=False),
        }
        with path.open("rb") as handle:
            response = session.post(
                config.job_url,
                headers=_headers(config.token),
                data=data,
                files={"file": handle},
                timeout=config.timeout_seconds,
            )

    if response.status_code != 200:
        raise RuntimeError(f"PaddleOCR submit failed: HTTP {response.status_code}: {response.text[:800]}")
    payload = response.json()
    try:
        return str(payload["data"]["jobId"])
    except KeyError as exc:
        raise RuntimeError(f"PaddleOCR submit response missing jobId: {payload}") from exc


def poll_paddleocr_job(
    job_id: str,
    config: PaddleOCRConfig,
    session: RequestSession | None = None,
    max_wait_seconds: float = 1800,
) -> dict[str, Any]:
    session = session or requests.Session()
    deadline = time.monotonic() + max_wait_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = session.get(
            f"{config.job_url}/{job_id}",
            headers=_headers(config.token),
            timeout=config.timeout_seconds,
        )
        if response.status_code != 200:
            raise RuntimeError(f"PaddleOCR poll failed: HTTP {response.status_code}: {response.text[:800]}")
        payload = response.json()
        last_payload = payload
        data = payload.get("data", {})
        state = data.get("state")
        if state == "done":
            return payload
        if state == "failed":
            error_msg = data.get("errorMsg", "unknown PaddleOCR failure")
            raise RuntimeError(f"PaddleOCR job failed: {error_msg}")
        time.sleep(config.poll_interval_seconds)
    raise TimeoutError(f"PaddleOCR job {job_id} did not finish within {max_wait_seconds} seconds. Last payload: {last_payload}")


def _download_bytes(url: str, session: RequestSession, timeout_seconds: float) -> bytes:
    response = session.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    return response.content


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def download_paddleocr_result(
    source: str | Path,
    job_id: str,
    job_payload: dict[str, Any],
    config: PaddleOCRConfig,
    output_dir: Path,
    session: RequestSession | None = None,
) -> PaddleOCRResult:
    session = session or requests.Session()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_url = job_payload["data"]["resultUrl"]["jsonUrl"]
    jsonl_text = _download_bytes(json_url, session, config.timeout_seconds).decode("utf-8")
    jsonl_path = output_dir / "result.jsonl"
    _write_text(jsonl_path, jsonl_text)

    markdown_files: list[str] = []
    image_files: list[str] = []
    page_num = 0
    for line_num, line in enumerate(jsonl_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        result = record.get("result", {})
        for res in result.get("layoutParsingResults", []):
            md_path = output_dir / "markdown" / f"doc_{page_num:04d}.md"
            markdown_text = res.get("markdown", {}).get("text", "")
            _write_text(md_path, markdown_text)
            markdown_files.append(str(md_path))

            for img_path, img_url in res.get("markdown", {}).get("images", {}).items():
                image_path = output_dir / "images" / img_path
                _write_bytes(image_path, _download_bytes(img_url, session, config.timeout_seconds))
                image_files.append(str(image_path))

            for img_name, img_url in res.get("outputImages", {}).items():
                image_path = output_dir / "output_images" / f"{img_name}_{page_num:04d}.jpg"
                _write_bytes(image_path, _download_bytes(img_url, session, config.timeout_seconds))
                image_files.append(str(image_path))
            page_num += 1

    data = job_payload.get("data", {})
    progress = data.get("extractProgress", {})
    manifest = {
        "job_id": job_id,
        "state": data.get("state"),
        "source": str(source),
        "model": config.model,
        "optional_payload": config.optional_payload or DEFAULT_OPTIONAL_PAYLOAD,
        "json_url_recorded": bool(json_url),
        "pages": page_num,
        "markdown_files": markdown_files,
        "image_files": image_files,
        "started_at": progress.get("startTime"),
        "completed_at": progress.get("endTime"),
        "raw_job_payload": job_payload,
    }
    manifest_path = output_dir / "manifest.json"
    _write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    return PaddleOCRResult(
        job_id=job_id,
        state=str(data.get("state", "done")),
        source=str(source),
        output_dir=str(output_dir),
        pages=page_num,
        markdown_files=markdown_files,
        image_files=image_files,
        jsonl_path=str(jsonl_path),
        manifest_path=str(manifest_path),
        started_at=progress.get("startTime"),
        completed_at=progress.get("endTime"),
    )


def parse_document_with_paddleocr(
    source: str | Path,
    token: str | None = None,
    output_root: Path | None = None,
    model: str = DEFAULT_MODEL,
    optional_payload: dict[str, Any] | None = None,
    poll_interval_seconds: float = 5.0,
    max_wait_seconds: float = 1800,
    session: RequestSession | None = None,
) -> PaddleOCRResult:
    resolved_token = get_paddleocr_token(token)
    if not resolved_token:
        raise RuntimeError("Missing PaddleOCR token. Set PADDLEOCR_TOKEN/PADDLEOCR_API_TOKEN or pass token explicitly.")
    config = PaddleOCRConfig(
        token=resolved_token,
        model=model,
        optional_payload=optional_payload or DEFAULT_OPTIONAL_PAYLOAD,
        poll_interval_seconds=poll_interval_seconds,
    )
    output_dir = output_dir_for_source(str(source), output_root=output_root)
    job_id = submit_paddleocr_job(source, config=config, session=session)
    payload = poll_paddleocr_job(
        job_id,
        config=config,
        session=session,
        max_wait_seconds=max_wait_seconds,
    )
    return download_paddleocr_result(source, job_id, payload, config, output_dir, session=session)


def result_to_dict(result: PaddleOCRResult) -> dict[str, Any]:
    return asdict(result)
