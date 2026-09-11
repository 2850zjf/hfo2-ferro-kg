from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-chunks", type=int, default=None)
    parser.add_argument("--no-llm", action="store_true", help="Use only deterministic rule extraction.")
    parser.add_argument("--model", default=None, help="Override HFO2_FERROKG_LLM_MODEL.")
    parser.add_argument("--dry-run", action="store_true", help="Run extraction without writing candidates/facts.")
    parser.add_argument("--incremental", action="store_true", help="Only extract chunks without candidates for the current ontology.")
    parser.add_argument("--commit-every", type=int, default=25, help="Commit database writes every N processed chunks.")
    parser.add_argument("--progress-every", type=int, default=20, help="Print progress every N processed chunks.")
    parser.add_argument("--max-workers", type=int, default=1, help="Concurrent LLM calls for extraction.")
    parser.add_argument("--paper-list", type=Path, default=None, help="CSV/TXT file with paper_id values to extract.")
    parser.add_argument("--chunk-list", type=Path, default=None, help="CSV/TXT file with chunk_id values to extract.")
    parser.add_argument("--all-chunks", action="store_true", help="Extract every chunk, not only high-value chunks.")
    parser.add_argument(
        "--llm-strict",
        action="store_true",
        help="Do not fall back to rule extraction when LLM calls fail; pause for retry instead.",
    )
    parser.add_argument(
        "--llm-thinking",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override reasoning mode for this run. Structured extraction should normally use --no-llm-thinking.",
    )
    parser.add_argument("--llm-max-tokens", type=int, default=None, help="Override maximum completion tokens for this run.")
    parser.add_argument("--llm-timeout-seconds", type=float, default=None, help="Override per-request timeout for this run.")
    parser.add_argument("--llm-context-chars", type=int, default=None, help="Override adjacent context characters on each side.")
    args = parser.parse_args()
    if args.llm_thinking is not None:
        os.environ["HFO2_FERROKG_LLM_ENABLE_THINKING"] = "true" if args.llm_thinking else "false"
    if args.llm_max_tokens is not None:
        os.environ["HFO2_FERROKG_LLM_MAX_TOKENS"] = str(max(256, args.llm_max_tokens))
    if args.llm_timeout_seconds is not None:
        os.environ["HFO2_FERROKG_LLM_TIMEOUT_SECONDS"] = str(max(5.0, args.llm_timeout_seconds))
    if args.llm_context_chars is not None:
        os.environ["HFO2_FERROKG_LLM_CONTEXT_CHARS"] = str(max(0, args.llm_context_chars))

    # Import after runtime overrides so Settings sees the per-run extraction profile.
    from backend.services.hfo2_extractor import run_extraction
    from backend.services.hfo2_parallel_extractor import run_parallel_extraction

    paper_ids = _read_id_list(args.paper_list)
    chunk_ids = _read_id_list(args.chunk_list)
    runner = run_parallel_extraction if args.max_workers and args.max_workers > 1 else run_extraction
    result = runner(
        limit_chunks=args.limit_chunks,
        use_llm=not args.no_llm,
        llm_model=args.model,
        dry_run=args.dry_run,
        reset_existing=not args.incremental,
        commit_every=args.commit_every,
        progress_every=args.progress_every,
        paper_ids=paper_ids,
        chunk_ids=chunk_ids,
        high_value_only=not args.all_chunks,
        llm_strict=args.llm_strict,
        **({"max_workers": args.max_workers} if runner is run_parallel_extraction else {}),
    )
    print(result)
    if result.get("paused"):
        raise SystemExit(75)
    if result.get("transient_paused"):
        raise SystemExit(76)


def _read_id_list(path: Path | None) -> list[str] | None:
    if not path:
        return None
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        first = raw.split(",", 1)[0].strip()
        if first and first not in {"paper_id", "chunk_id"}:
            ids.append(first)
    return ids


if __name__ == "__main__":
    main()
