from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.paddleocr_parser import (
    DEFAULT_MODEL,
    DEFAULT_OPTIONAL_PAYLOAD,
    parse_document_with_paddleocr,
    result_to_dict,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse one document with PaddleOCR-VL.")
    parser.add_argument("source", help="Local file path or http(s) file URL.")
    parser.add_argument("--token", default=None, help="Temporary token. Prefer env PADDLEOCR_TOKEN instead.")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-wait", type=float, default=1800)
    parser.add_argument("--doc-orientation", action="store_true")
    parser.add_argument("--doc-unwarping", action="store_true")
    parser.add_argument("--chart-recognition", action="store_true")
    args = parser.parse_args()

    optional_payload = dict(DEFAULT_OPTIONAL_PAYLOAD)
    optional_payload["useDocOrientationClassify"] = bool(args.doc_orientation)
    optional_payload["useDocUnwarping"] = bool(args.doc_unwarping)
    optional_payload["useChartRecognition"] = bool(args.chart_recognition)

    result = parse_document_with_paddleocr(
        source=args.source,
        token=args.token,
        output_root=args.output_root,
        model=args.model,
        optional_payload=optional_payload,
        poll_interval_seconds=args.poll_interval,
        max_wait_seconds=args.max_wait,
    )
    print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
