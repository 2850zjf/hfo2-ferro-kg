from __future__ import annotations

import argparse
import csv
import json
from math import ceil
from pathlib import Path


def split_queue(
    input_path: Path,
    output_dir: Path,
    prefix: str,
    skip_rows: int = 0,
    limit_rows: int | None = None,
    shard_size: int = 200,
) -> dict[str, object]:
    if shard_size <= 0:
        raise ValueError("shard_size must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header found in {input_path}")
        rows = list(reader)

    selected = rows[max(0, skip_rows) :]
    if limit_rows is not None:
        selected = selected[: max(0, limit_rows)]

    shards: list[dict[str, object]] = []
    total_shards = ceil(len(selected) / shard_size) if selected else 0
    for index in range(total_shards):
        start = index * shard_size
        end = min(start + shard_size, len(selected))
        shard_rows = selected[start:end]
        shard_path = output_dir / f"{prefix}_shard{index + 1:03d}.csv"
        with shard_path.open("w", encoding="utf-8", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(shard_rows)
        shards.append(
            {
                "path": str(shard_path),
                "rows": len(shard_rows),
                "source_start_row_1_based": skip_rows + start + 1,
                "source_end_row_1_based": skip_rows + end,
            }
        )

    manifest = {
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "prefix": prefix,
        "skip_rows": skip_rows,
        "limit_rows": limit_rows,
        "shard_size": shard_size,
        "selected_rows": len(selected),
        "shards": shards,
    }
    manifest_path = output_dir / f"{prefix}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="extraction_queue")
    parser.add_argument("--skip-rows", type=int, default=0, help="Data rows to skip, excluding the CSV header.")
    parser.add_argument("--limit-rows", type=int, default=None)
    parser.add_argument("--shard-size", type=int, default=200)
    args = parser.parse_args()
    result = split_queue(
        input_path=args.input,
        output_dir=args.output_dir,
        prefix=args.prefix,
        skip_rows=args.skip_rows,
        limit_rows=args.limit_rows,
        shard_size=args.shard_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
