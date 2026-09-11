#!/usr/bin/env python3
"""Build a page-addressable full-text layer for the computation intake corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTAKE = ROOT / "data/literature_intake/computation_20260813"

SIGNAL_PATTERNS = {
    "method": re.compile(
        r"\b(methods?|computational details?|calculation details?|simulation details?|"
        r"first[- ]principles|density functional|DFT|phase[- ]field|molecular dynamics)\b",
        re.IGNORECASE,
    ),
    "software": re.compile(
        r"\b(VASP|Quantum ESPRESSO|ABINIT|LAMMPS|FerroX|JAX|DeePMD|Deep Potential|"
        r"MACE|GAP|CASTEP|CP2K|SIESTA|PHONOPY|Wannier90)\b",
        re.IGNORECASE,
    ),
    "electronic_structure": re.compile(
        r"\b(PBEsol|PBE|LDA|SCAN|HSE06?|PAW|pseudopotential|plane[- ]wave|"
        r"cut[- ]?off|k[- ]?point|Monkhorst|supercell)\b",
        re.IGNORECASE,
    ),
    "dynamics_path": re.compile(
        r"\b(NEB|nudged elastic band|minimum energy path|switching path|time step|"
        r"NVT|NPT|thermostat|barostat|domain wall)\b",
        re.IGNORECASE,
    ),
    "continuum": re.compile(
        r"\b(Landau|Ginzburg|Devonshire|LGD|gradient coefficient|kinetic coefficient|"
        r"finite element|finite difference|mesh|boundary condition)\b",
        re.IGNORECASE,
    ),
    "availability": re.compile(
        r"\b(data availability|code availability|supporting information|supplementary|"
        r"repository|GitHub|Zenodo|figshare|Materials Cloud|Materials Project|CIF|POSCAR)\b",
        re.IGNORECASE,
    ),
    "figures_tables": re.compile(r"\b(Fig(?:ure)?\.?|Table|Equation|Eq\.?)\s*[A-Z]?\d+", re.IGNORECASE),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_id(value: str) -> str:
    value = value.strip().replace("/", "_").replace(":", "_")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value.strip("_.") or "unknown_document"


def resolve_pdf(intake: Path, record: dict[str, Any]) -> Path:
    relative = Path(str(record["relative_path"]))
    direct = intake / relative
    if direct.is_file():
        return direct

    matches = sorted(intake.rglob(str(record["file_name"])))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Could not uniquely resolve {record['file_name']!r}; matches={matches}"
        )
    record["relative_path"] = matches[0].relative_to(intake).as_posix()
    return matches[0]


def extract_page_text(page: Any) -> str:
    try:
        text = page.extract_text(extraction_mode="layout") or ""
    except (TypeError, ValueError):
        text = page.extract_text() or ""
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def snippets(text: str, pattern: re.Pattern[str], radius: int = 360) -> list[str]:
    found: list[str] = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - radius)
        end = min(len(text), match.end() + radius)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        if snippet and all(snippet not in prior for prior in found):
            found.append(snippet)
        if len(found) == 3:
            break
    return found


def write_inventory(intake: Path, records: list[dict[str, Any]]) -> None:
    json_path = intake / "intake_inventory.json"
    csv_path = intake / "intake_inventory.csv"
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fieldnames = list(records[0]) if records else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def build(intake: Path) -> dict[str, Any]:
    records = json.loads((intake / "intake_inventory.json").read_text(encoding="utf-8-sig"))
    output_root = intake / "05_fulltext"
    if output_root.exists():
        shutil.rmtree(output_root)
    by_paper = output_root / "by_paper"
    by_paper.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    context_rows: list[dict[str, Any]] = []
    corpus_totals: defaultdict[str, int] = defaultdict(int)

    for record in records:
        pdf = resolve_pdf(intake, record)
        record["sha256"] = sha256(pdf)
        record["file_size_bytes"] = pdf.stat().st_size

        identifier = str(record.get("doi_or_id") or record.get("parent_doi") or pdf.stem)
        artifact_suffix = ""
        if record["record_type"] != "main_paper":
            artifact_suffix = f"__{record['record_type']}__{safe_id(pdf.stem)}"
        document_id = safe_id(identifier) + artifact_suffix
        document_dir = by_paper / document_id
        pages_dir = document_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        reader = PdfReader(str(pdf))
        page_records: list[dict[str, Any]] = []
        combined_parts: list[str] = []
        context: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

        for page_number, page in enumerate(reader.pages, start=1):
            text = extract_page_text(page)
            chars = len(text)
            word_count = len(re.findall(r"\b\w+\b", text))
            signals = [name for name, pattern in SIGNAL_PATTERNS.items() if pattern.search(text)]
            low_text = chars < 180
            page_header = (
                f"# {record.get('title') or pdf.stem}\n\n"
                f"- Source: `{record['relative_path']}`\n"
                f"- Page: {page_number}/{len(reader.pages)}\n\n"
            )
            page_body = text if text else "[No extractable text on this page.]"
            (pages_dir / f"page_{page_number:03d}.md").write_text(
                page_header + page_body + "\n", encoding="utf-8"
            )
            combined_parts.append(
                f"\n\n<!-- PAGE {page_number} -->\n\n## Page {page_number}\n\n{page_body}\n"
            )

            for signal_name, pattern in SIGNAL_PATTERNS.items():
                for snippet in snippets(text, pattern):
                    context[signal_name].append({"page": page_number, "text": snippet})

            page_records.append(
                {
                    "page": page_number,
                    "characters": chars,
                    "words": word_count,
                    "low_text": low_text,
                    "signals": signals,
                }
            )

        title = str(record.get("title") or pdf.stem)
        combined_header = (
            f"# {title}\n\n"
            f"- Record type: `{record['record_type']}`\n"
            f"- DOI/ID: `{identifier}`\n"
            f"- Source: `{record['relative_path']}`\n"
            f"- SHA256: `{record['sha256']}`\n"
        )
        (document_dir / "combined.md").write_text(
            combined_header + "".join(combined_parts), encoding="utf-8"
        )
        (document_dir / "reading_context.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        total_chars = sum(page["characters"] for page in page_records)
        pages_with_text = sum(page["characters"] > 0 for page in page_records)
        low_text_pages = [page["page"] for page in page_records if page["low_text"]]
        visual_candidates = [
            page["page"]
            for page in page_records
            if "figures_tables" in page["signals"] or page["low_text"]
        ]
        document_manifest = {
            "document_id": document_id,
            "record_type": record["record_type"],
            "priority": record.get("priority", ""),
            "title": title,
            "doi_or_id": identifier,
            "parent_doi": record.get("parent_doi", ""),
            "source_pdf": record["relative_path"],
            "sha256": record["sha256"],
            "page_count": len(page_records),
            "pages_with_text": pages_with_text,
            "total_characters": total_chars,
            "low_text_pages": low_text_pages,
            "visual_candidate_pages": sorted(set(visual_candidates)),
            "science_evidence_eligible": record["record_type"] != "peer_review",
            "page_records": page_records,
        }
        (document_dir / "extraction_manifest.json").write_text(
            json.dumps(document_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest.append(document_manifest)
        context_rows.append(
            {
                "document_id": document_id,
                "record_type": record["record_type"],
                "priority": record.get("priority", ""),
                "doi_or_id": identifier,
                "page_count": len(page_records),
                "pages_with_text": pages_with_text,
                "total_characters": total_chars,
                "low_text_pages": ";".join(map(str, low_text_pages)),
                "visual_candidate_pages": ";".join(map(str, sorted(set(visual_candidates)))),
                "science_evidence_eligible": record["record_type"] != "peer_review",
                "source_pdf": record["relative_path"],
            }
        )
        corpus_totals[record["record_type"]] += 1
        corpus_totals["pages"] += len(page_records)
        corpus_totals["characters"] += total_chars

    write_inventory(intake, records)
    (output_root / "fulltext_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_root / "fulltext_manifest.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(context_rows[0]))
        writer.writeheader()
        writer.writerows(context_rows)

    summary = {
        "documents": len(manifest),
        "record_type_counts": {
            key: value
            for key, value in sorted(corpus_totals.items())
            if key not in {"pages", "characters"}
        },
        "pages": corpus_totals["pages"],
        "characters": corpus_totals["characters"],
        "science_evidence_documents": sum(item["science_evidence_eligible"] for item in manifest),
        "peer_review_documents_excluded": sum(
            not item["science_evidence_eligible"] for item in manifest
        ),
    }
    (output_root / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intake", type=Path, default=DEFAULT_INTAKE)
    args = parser.parse_args()
    print(json.dumps(build(args.intake.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
