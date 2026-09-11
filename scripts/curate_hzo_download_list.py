from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "hfo2_ferrokg.sqlite3"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "literature"
OPENALEX_URL = "https://api.openalex.org/works"


SEARCH_QUERIES: list[dict[str, str]] = [
    {
        "query": "ferroelectric hafnium oxide HfO2 HZO",
        "theme": "core HfO2/HZO ferroelectricity",
    },
    {
        "query": "Hf0.5Zr0.5O2 ferroelectric thin film remanent polarization",
        "theme": "HZO Pr benchmark",
    },
    {
        "query": "hafnium zirconium oxide ferroelectric annealing electrode oxygen vacancy",
        "theme": "process interface oxygen",
    },
    {
        "query": "HfO2 ferroelectric orthorhombic phase Pca21 phase stability",
        "theme": "phase stability mechanism",
    },
    {
        "query": "ferroelectric HfO2 density functional theory oxygen vacancy phase transition",
        "theme": "computational mechanism",
    },
    {
        "query": "hafnia based ferroelectrics review HfO2 HZO",
        "theme": "review roadmap",
    },
    {
        "query": "HfO2 ferroelectric FeFET HZO transistor memory",
        "theme": "FeFET device",
    },
    {
        "query": "HfO2 ferroelectric tunnel junction HZO FTJ",
        "theme": "FTJ device",
    },
    {
        "query": "wake up fatigue retention HfO2 HZO ferroelectric",
        "theme": "reliability physics",
    },
    {
        "query": "epitaxial Hf0.5Zr0.5O2 ferroelectric rhombohedral orthorhombic",
        "theme": "epitaxy phase",
    },
    {
        "query": "doped hafnium oxide ferroelectric silicon yttrium aluminum gadolinium",
        "theme": "doped HfO2",
    },
    {
        "query": "machine learning ferroelectric hafnium oxide HZO",
        "theme": "AI data model",
    },
    {
        "query": "Ferroelectricity in hafnium oxide thin films",
        "theme": "classic foundation",
    },
    {
        "query": "Ferroelectric hafnium oxide CMOS-compatible highly scalable future ferroelectric memories",
        "theme": "classic foundation",
    },
    {
        "query": "Review and perspective on ferroelectric HfO2-based thin films for memory applications",
        "theme": "review roadmap",
    },
    {
        "query": "Roadmap on ferroelectric hafnia- and zirconia-based materials and devices",
        "theme": "review roadmap",
    },
    {
        "query": "Reversible oxygen migration and phase transitions in hafnia-based ferroelectric devices",
        "theme": "oxygen vacancy mechanism",
    },
    {
        "query": "A rhombohedral ferroelectric phase in epitaxially strained Hf0.5Zr0.5O2 thin films",
        "theme": "epitaxy phase",
    },
    {
        "query": "Intrinsic ferroelectricity in Y-doped HfO2 thin films",
        "theme": "intrinsic ferroelectricity",
    },
    {
        "query": "Contribution of oxygen vacancies to the ferroelectric behavior of Hf0.5Zr0.5O2 thin films",
        "theme": "oxygen vacancy mechanism",
    },
    {
        "query": "Phase-Exchange-Driven Wake-Up and Fatigue in Ferroelectric Hafnium Zirconium Oxide Films",
        "theme": "reliability physics",
    },
]


MATERIAL_PATTERNS = [
    r"\bhfo2\b",
    r"hf[o0][2₂]",
    r"hafnium oxide",
    r"hafnia",
    r"\bhzo\b",
    r"hf0\.",
    r"hf\.?5zr",
    r"hf[- ]?zr",
    r"hafnium zirconium oxide",
    r"zirconium[- ]doped hafnium",
]
TITLE_MATERIAL_PATTERNS = [
    r"\bhfo2\b",
    r"hf[o0][2₂]",
    r"hafnium oxide",
    r"hafnium[- ]?zirconium",
    r"hafnia",
    r"\bhzo\b",
    r"\bhf0\.",
    r"\bhf1",
    r"\bhf[- ]?zr",
    r"hf[zx]r",
    r"hfxzr",
    r"zr0\.5hf0\.5o2",
    r"hafnia[- ]and[- ]zirconia",
    r"hafnia.*zirconia",
]
TITLE_FERRO_OR_PHASE_PATTERNS = [
    r"ferroelectric",
    r"antiferroelectric",
    r"polarization",
    r"\bpolar\b",
    r"orthorhombic",
    r"rhombohedral",
    r"\bpca2?1\b",
    r"\bphase\b",
    r"wake[- ]?up",
    r"fatigue",
    r"retention",
    r"\bdomain\b",
    r"hysteresis",
    r"memory window",
    r"negative capacitance",
    r"tunnel junction",
    r"field[- ]effect",
    r"\bfefet\b",
    r"\bfecap\b",
    r"\bftj\b",
]
FERRO_PATTERNS = [
    "ferroelectric",
    "ferroelectricity",
    "remanent polarization",
    "polarization switching",
    "hysteresis",
    "wake-up",
    "wake up",
    "coercive",
    "negative capacitance",
]
PHASE_PATTERNS = ["orthorhombic", "pca21", "pca2", "rhombohedral", "monoclinic", "tetragonal", "phase stability"]
PROCESS_PATTERNS = ["anneal", "annealing", "atomic layer deposition", "ald", "sputter", "rapid thermal", "temperature", "oxygen partial pressure"]
INTERFACE_PATTERNS = ["oxygen vacancy", "vacancies", "electrode", "interface", "tin", "ruo2", "tungsten", "oxygen scavenging"]
PROPERTY_PATTERNS = ["pr", "2pr", "remanent", "coercive field", "ec", "endurance", "retention", "fatigue", "leakage"]
DEVICE_PATTERNS = ["fefet", "fecap", "ftj", "tunnel junction", "ferroelectric field-effect", "capacitor", "memory"]
COMPUTE_PATTERNS = ["density functional", "dft", "first-principles", "ab initio", "molecular dynamics", "phase-field", "machine learning"]
UNRELATED_PATTERNS = ["barium titanate", "batio3", "pzt", "bifeo3", "polymer", "pvdf"]
CLASSIC_PATTERNS = [
    "ferroelectricity in hafnium oxide thin films",
    "cmos-compatible and highly scalable",
    "review and perspective on ferroelectric hfo2-based thin films",
    "roadmap on ferroelectric hafnia- and zirconia-based materials and devices",
    "reversible oxygen migration and phase transitions",
    "rhombohedral ferroelectric phase in epitaxially strained",
    "intrinsic ferroelectricity in y-doped hfo2",
    "contribution of oxygen vacancies to the ferroelectric behavior",
    "phase-exchange-driven wake-up and fatigue",
]

SOURCE_TIER_1 = [
    "nature",
    "science",
    "advanced materials",
    "nature materials",
    "nature communications",
    "acs nano",
    "nano letters",
]
SOURCE_TIER_2 = [
    "applied physics letters",
    "acs applied materials",
    "advanced electronic materials",
    "advanced functional materials",
    "physical review",
    "journal of applied physics",
    "ieee electron device letters",
    "ieee transactions on electron devices",
    "iedm",
    "symposium on vlsi",
    "materials today",
    "mrs bulletin",
    "npj computational materials",
]


@dataclass
class Candidate:
    key: str
    title: str
    year: int | None
    doi: str
    venue: str
    type: str
    cited_by_count: int
    landing_page_url: str
    oa_url: str
    pdf_url: str
    oa_status: str
    abstract: str
    queries_hit: set[str] = field(default_factory=set)
    themes_hit: set[str] = field(default_factory=set)
    raw: dict[str, Any] = field(default_factory=dict)


def openalex_get(params: dict[str, str | int]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{OPENALEX_URL}?{query}", headers={"User-Agent": "HfO2-FerroKG literature curation"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def reconstruct_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions: dict[int, str] = {}
    for word, indices in index.items():
        for idx in indices:
            positions[idx] = word
    return " ".join(positions[i] for i in sorted(positions))


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = doi.removeprefix("https://doi.org/")
    doi = doi.removeprefix("http://doi.org/")
    return doi


def clean_title(title: str) -> str:
    title = re.sub(r"<\s*sub\s*>\s*([^<]+?)\s*<\s*/\s*sub\s*>", r"\1", title, flags=re.I)
    title = re.sub(r"<\s*sup\s*>\s*([^<]+?)\s*<\s*/\s*sup\s*>", r"\1", title, flags=re.I)
    title = re.sub(r"<[^>]+>", "", title)
    return html.unescape(re.sub(r"\s+", " ", title)).strip()


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def contains_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def count_hits(text: str, patterns: list[str]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text, flags=re.I))


def fetch_candidates(per_query: int = 80, start_date: str = "2011-01-01", end_date: str = "2026-06-10") -> dict[str, Candidate]:
    candidates: dict[str, Candidate] = {}
    for item in SEARCH_QUERIES:
        data = openalex_get(
            {
                "search": item["query"],
                "per-page": per_query,
                "filter": f"from_publication_date:{start_date},to_publication_date:{end_date}",
                "select": ",".join(
                    [
                        "id",
                        "doi",
                        "display_name",
                        "publication_year",
                        "type",
                        "cited_by_count",
                        "primary_location",
                        "open_access",
                        "abstract_inverted_index",
                    ]
                ),
            }
        )
        for work in data.get("results", []):
            title = clean_title((work.get("display_name") or "").strip())
            if not title:
                continue
            doi = normalize_doi(work.get("doi"))
            key = doi or normalize_title(title)
            primary = work.get("primary_location") or {}
            source = primary.get("source") or {}
            oa = work.get("open_access") or {}
            landing = primary.get("landing_page_url") or work.get("id") or ""
            pdf_url = primary.get("pdf_url") or oa.get("oa_url") or ""
            abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
            candidate = candidates.get(key)
            if candidate is None:
                candidate = Candidate(
                    key=key,
                    title=title,
                    year=work.get("publication_year"),
                    doi=doi,
                    venue=source.get("display_name") or "",
                    type=work.get("type") or "",
                    cited_by_count=int(work.get("cited_by_count") or 0),
                    landing_page_url=landing,
                    oa_url=oa.get("oa_url") or "",
                    pdf_url=pdf_url,
                    oa_status=oa.get("oa_status") or "",
                    abstract=abstract,
                    raw=work,
                )
                candidates[key] = candidate
            candidate.queries_hit.add(item["query"])
            candidate.themes_hit.add(item["theme"])
        time.sleep(0.2)
    return candidates


def load_local_library(db_path: Path) -> tuple[set[str], set[str], dict[str, str]]:
    if not db_path.exists():
        return set(), set(), {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        paper_rows = conn.execute("SELECT paper_id, doi, title FROM papers").fetchall()
    dois: set[str] = set()
    titles: set[str] = set()
    doi_to_paper: dict[str, str] = {}
    for row in paper_rows:
        doi = normalize_doi(row["doi"])
        if doi:
            dois.add(doi)
            doi_to_paper[doi] = row["paper_id"]
        title = normalize_title(row["title"] or "")
        if title:
            titles.add(title)
    return dois, titles, doi_to_paper


def classify(candidate: Candidate) -> dict[str, Any]:
    text = f"{candidate.title} {candidate.abstract}".lower()
    title_text = candidate.title.lower()
    material_hits = count_hits(text, MATERIAL_PATTERNS)
    ferro_hits = count_hits(text, FERRO_PATTERNS)
    phase_hits = count_hits(text, PHASE_PATTERNS)
    process_hits = count_hits(text, PROCESS_PATTERNS)
    interface_hits = count_hits(text, INTERFACE_PATTERNS)
    property_hits = count_hits(text, PROPERTY_PATTERNS)
    device_hits = count_hits(text, DEVICE_PATTERNS)
    compute_hits = count_hits(text, COMPUTE_PATTERNS)
    unrelated_hits = count_hits(text, UNRELATED_PATTERNS)

    relevance = 0
    relevance += min(8, material_hits * 3)
    relevance += min(8, ferro_hits * 3)
    relevance += min(5, phase_hits * 2)
    relevance += min(5, process_hits * 2)
    relevance += min(5, interface_hits * 2)
    relevance += min(5, property_hits * 2)
    relevance += min(5, device_hits * 2)
    relevance += min(5, compute_hits * 2)
    if "hzo" in text or "hf0.5zr0.5o2" in text or "hafnium zirconium oxide" in text:
        relevance += 4
    if "ferroelectric" in title_text and contains_any(title_text, MATERIAL_PATTERNS):
        relevance += 5
    if unrelated_hits and material_hits == 0:
        relevance -= 8

    venue_lower = candidate.venue.lower()
    quality = 0
    if any(source in venue_lower for source in SOURCE_TIER_1):
        quality += 8
    elif any(source in venue_lower for source in SOURCE_TIER_2):
        quality += 5
    quality += min(8, round(math.log10(candidate.cited_by_count + 1) * 3))
    if candidate.year and candidate.year >= 2022 and relevance >= 18:
        quality += 3
    if candidate.oa_status in {"gold", "hybrid", "green", "bronze"} or candidate.pdf_url:
        quality += 1
    if "review" in title_text or candidate.type == "review":
        quality += 3
    if candidate.type == "preprint" or any(source in venue_lower for source in ["research square", "techrxiv"]):
        quality -= 4

    is_classic = any(pattern in title_text for pattern in CLASSIC_PATTERNS)

    if "review" in title_text or "roadmap" in title_text or candidate.type == "review":
        theme = "综述/路线图"
    elif is_classic:
        theme = "奠基/标志性论文"
    elif compute_hits:
        theme = "计算机制/物理约束"
    elif interface_hits:
        theme = "界面/氧空位/可靠性"
    elif process_hits:
        theme = "工艺-结构-性能"
    elif phase_hits:
        theme = "相结构/铁电机制"
    elif device_hits:
        theme = "器件与应用"
    else:
        theme = "核心HfO2/HZO铁电"

    score = relevance * 1.5 + quality
    if is_classic or (relevance >= 30 and quality >= 13) or (candidate.cited_by_count >= 300 and relevance >= 18):
        priority = "S 必下"
    elif relevance >= 22 or quality >= 9:
        priority = "A 强推荐"
    elif relevance >= 14 and quality >= 4:
        priority = "B 补充"
    else:
        priority = "C 低优先"

    kg_bits = []
    if property_hits:
        kg_bits.append("Pr/2Pr/Ec/可靠性指标")
    if process_hits:
        kg_bits.append("退火/沉积/厚度/气氛")
    if interface_hits:
        kg_bits.append("电极界面/氧空位")
    if phase_hits:
        kg_bits.append("相结构/取向")
    if device_hits:
        kg_bits.append("FeCAP/FeFET/FTJ器件")
    if compute_hits:
        kg_bits.append("DFT/相稳定/缺陷能")

    return {
        "strong_relevance_score": relevance,
        "quality_score": quality,
        "combined_score": round(score, 2),
        "priority": priority,
        "theme": theme,
        "is_classic_or_landmark": int(is_classic),
        "material_hits": material_hits,
        "ferroelectric_hits": ferro_hits,
        "phase_hits": phase_hits,
        "process_hits": process_hits,
        "interface_hits": interface_hits,
        "property_hits": property_hits,
        "device_hits": device_hits,
        "compute_hits": compute_hits,
        "kg_value": "；".join(kg_bits) if kg_bits else "背景/综述线索",
    }


def why_include(row: dict[str, Any]) -> str:
    theme = row["theme"]
    if theme == "奠基/标志性论文":
        return "领域奠基或标志性工作，必须用于论文引言、问题定义和知识图谱本体边界。"
    if theme == "计算机制/物理约束":
        return "用于把知识图谱约束落到可计算机制：相稳定、氧空位、界面/应变或缺陷能。"
    if theme == "工艺-结构-性能":
        return "强相关于样品级工艺参数和Pr/2Pr benchmark，可直接补充抽取字段。"
    if theme == "界面/氧空位/可靠性":
        return "支撑物理约束中的氧空位、电极氧亲和力、wake-up/fatigue机制。"
    if theme == "器件与应用":
        return "用于把材料事实连接到FeCAP/FeFET/FTJ器件级性能与设计场景。"
    if theme == "相结构/铁电机制":
        return "用于约束HfO2/HZO铁电相、取向和相稳定性解释。"
    if theme == "综述/路线图":
        return "用于论文引言、领域定位和高质量参考文献网络扩展。"
    return "核心HfO2/HZO铁电文献，适合作为KG和benchmark的强相关来源。"


def curate(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    local_dois, local_titles, doi_to_paper = load_local_library(args.db_path)
    candidates = fetch_candidates(per_query=args.per_query, start_date=args.start_date, end_date=args.end_date)

    rows: list[dict[str, Any]] = []
    for candidate in candidates.values():
        cls = classify(candidate)
        if cls["strong_relevance_score"] < args.min_relevance:
            continue
        local_by_doi = bool(candidate.doi and candidate.doi in local_dois)
        local_by_title = normalize_title(candidate.title) in local_titles
        local_status = "已有本地DOI记录" if local_by_doi else ("可能已有本地标题记录" if local_by_title else "建议下载/补库")
        download_url = candidate.pdf_url or candidate.oa_url or candidate.landing_page_url
        if local_by_doi or local_by_title:
            recommended_action = "本地已有，建议核对PDF质量/是否可解析"
        elif candidate.pdf_url:
            recommended_action = "优先直接下载OA PDF"
        elif candidate.oa_url:
            recommended_action = "从开放获取页面下载全文"
        elif candidate.doi:
            recommended_action = "用DOI/出版社页手动下载"
        else:
            recommended_action = "用标题检索下载"
        row = {
            "priority": cls["priority"],
            "theme": cls["theme"],
            "title": candidate.title,
            "year": candidate.year or "",
            "venue": candidate.venue,
            "type": candidate.type,
            "doi": candidate.doi,
            "landing_page_url": candidate.landing_page_url,
            "oa_url": candidate.oa_url,
            "pdf_url": candidate.pdf_url,
            "download_url": download_url,
            "oa_status": candidate.oa_status,
            "cited_by_count": candidate.cited_by_count,
            "strong_relevance_score": cls["strong_relevance_score"],
            "quality_score": cls["quality_score"],
            "combined_score": cls["combined_score"],
            "is_classic_or_landmark": cls["is_classic_or_landmark"],
            "local_library_status": local_status,
            "recommended_action": recommended_action,
            "local_paper_id": doi_to_paper.get(candidate.doi, ""),
            "kg_value": cls["kg_value"],
            "why_include": "",
            "queries_hit": " | ".join(sorted(candidate.queries_hit)),
            "themes_hit": " | ".join(sorted(candidate.themes_hit)),
            "abstract_snippet": candidate.abstract[:700],
            **{k: v for k, v in cls.items() if k.endswith("_hits")},
        }
        row["why_include"] = why_include(row)
        rows.append(row)

    deduped_by_title: dict[str, dict[str, Any]] = {}
    for row in rows:
        title_key = normalize_title(row["title"])
        row_score = (
            float(row["combined_score"])
            + (8 if row["doi"] else 0)
            + (4 if row["venue"] else 0)
            - (8 if row["type"] == "preprint" else 0)
        )
        previous = deduped_by_title.get(title_key)
        previous_score = previous.get("_dedupe_score", -9999) if previous else -9999
        if row_score > previous_score:
            row["_dedupe_score"] = row_score
            deduped_by_title[title_key] = row
    rows = list(deduped_by_title.values())
    for row in rows:
        row.pop("_dedupe_score", None)

    priority_rank = {"S 必下": 0, "A 强推荐": 1, "B 补充": 2, "C 低优先": 3}
    rows.sort(
        key=lambda r: (
            priority_rank.get(r["priority"], 9),
            -int(r["is_classic_or_landmark"]),
            -float(r["quality_score"]),
            -float(r["strong_relevance_score"]),
            -int(r["year"] or 0),
            -int(r["cited_by_count"]),
        )
    )

    if args.missing_only:
        rows = [row for row in rows if row["local_library_status"] == "建议下载/补库"]
    if args.require_title_material:
        rows = [row for row in rows if contains_any(row["title"], TITLE_MATERIAL_PATTERNS)]
    if args.require_title_ferro_or_phase:
        rows = [row for row in rows if contains_any(row["title"], TITLE_FERRO_OR_PHASE_PATTERNS)]

    rows = rows[: args.max_rows]
    for index, row in enumerate(rows, start=1):
        if index <= 35:
            row["download_batch"] = "第一批：论文主线必读/必下"
        elif index <= 90:
            row["download_batch"] = "第二批：强相关补库"
        else:
            row["download_batch"] = "第三批：专题扩展/可选"

    output_stem = "hzo_missing_high_quality_download_list" if args.missing_only else "hzo_high_quality_download_list"
    csv_path = output_dir / f"{output_stem}.csv"
    json_path = output_dir / f"{output_stem}.json"
    fieldnames = [
        "priority",
        "download_batch",
        "theme",
        "title",
        "year",
        "venue",
        "type",
        "doi",
        "landing_page_url",
        "oa_url",
        "pdf_url",
        "download_url",
        "oa_status",
        "cited_by_count",
        "strong_relevance_score",
        "quality_score",
        "combined_score",
        "is_classic_or_landmark",
        "local_library_status",
        "recommended_action",
        "local_paper_id",
        "kg_value",
        "why_include",
        "queries_hit",
        "themes_hit",
        "abstract_snippet",
        "material_hits",
        "ferroelectric_hits",
        "phase_hits",
        "process_hits",
        "interface_hits",
        "property_hits",
        "device_hits",
        "compute_hits",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "rows": len(rows),
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "priority_counts": {name: sum(1 for row in rows if row["priority"] == name) for name in priority_rank},
        "theme_counts": {},
        "local_status_counts": {},
    }
    for row in rows:
        summary["theme_counts"][row["theme"]] = summary["theme_counts"].get(row["theme"], 0) + 1
        summary["local_status_counts"][row["local_library_status"]] = summary["local_status_counts"].get(row["local_library_status"], 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--per-query", type=int, default=80)
    parser.add_argument("--max-rows", type=int, default=120)
    parser.add_argument("--min-relevance", type=int, default=14)
    parser.add_argument("--start-date", default="2011-01-01")
    parser.add_argument("--end-date", default="2026-06-10")
    parser.add_argument("--missing-only", action="store_true", help="Only export papers not found by local DOI/title.")
    parser.add_argument(
        "--require-title-material",
        action="store_true",
        help="Require HfO2/HZO/hafnia/HfZrO material evidence in the title for a stricter download queue.",
    )
    parser.add_argument(
        "--require-title-ferro-or-phase",
        action="store_true",
        help="Require title evidence for ferroelectric, polarization, phase, reliability, or ferroelectric device scope.",
    )
    args = parser.parse_args()
    curate(args)


if __name__ == "__main__":
    main()
