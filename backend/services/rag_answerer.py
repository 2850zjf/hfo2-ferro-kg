from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from backend.db.session import connect
from backend.services.vector_store import search_vector_index


@dataclass(frozen=True)
class EvidenceHit:
    title: str
    doi: str | None
    page_number: int | None
    evidence_text: str
    score: int


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_+\-.]+", text) if len(token) > 1}


def keyword_retrieve(question: str, limit: int = 8, db_path: Path | None = None) -> list[EvidenceHit]:
    q_tokens = tokenize(question)
    hits: list[EvidenceHit] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rf.page_number, rf.payload_json, p.title, p.doi
            FROM reviewed_facts rf
            LEFT JOIN papers p ON p.paper_id = rf.paper_id
            WHERE rf.review_status IN ('preapproved_machine', 'approved')
            """
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            prop = payload["property"]
            evidence = prop.get("evidence_text", "")
            searchable = " ".join(
                [
                    evidence,
                    prop.get("property_name", ""),
                    json.dumps(payload.get("material"), ensure_ascii=False),
                    json.dumps(payload.get("sample"), ensure_ascii=False),
                ]
            )
            score = len(q_tokens & tokenize(searchable))
            if score:
                hits.append(
                    EvidenceHit(
                        title=row["title"] or "Unknown paper",
                        doi=row["doi"],
                        page_number=row["page_number"],
                        evidence_text=evidence,
                        score=score,
                    )
                )
    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]


def answer_question(question: str, db_path: Path | None = None) -> str:
    hits = keyword_retrieve(question, db_path=db_path)
    vector_hits = search_vector_index(question, limit=5)
    if not hits and not vector_hits:
        return "当前数据库没有足够证据回答该问题。"

    lines = [
        "基于当前机器预审核事实，可以参考以下证据。正式论文结论建议等你人工复核后再使用。",
        "",
    ]
    if hits:
        lines.append("机器预审核事实：")
        for index, hit in enumerate(hits, start=1):
            doi = hit.doi or "DOI 未识别"
            page = f"p. {hit.page_number}" if hit.page_number else "页码未识别"
            lines.append(f"{index}. {hit.title} | {doi} | {page}")
            lines.append(f"   证据：{hit.evidence_text}")
    if vector_hits:
        lines.append("")
        lines.append("相关原文 chunk：")
        for index, hit in enumerate(vector_hits, start=1):
            doi = hit.get("doi") or "DOI 未识别"
            page = f"p. {hit.get('page_number')}" if hit.get("page_number") else "页码未识别"
            title = hit.get("title") or "Unknown paper"
            text = " ".join(str(hit.get("text", "")).split())[:360]
            lines.append(f"{index}. {title} | {doi} | {page} | score={hit['score']:.3f}")
            lines.append(f"   原文：{text}")
    if re.search(r"\bpr\b|polarization|2pr", question, re.I):
        lines.append("")
        lines.append("注意：系统严格区分 Pr 与 2Pr，机器预审核不会自动把 2Pr 当作 Pr。")
    return "\n".join(lines)
