from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.init_db import init_database
from backend.services.ontology_builder import build_ontology, load_ontology_bundle


st.set_page_config(page_title="本体构建", layout="wide")
st.title("本体构建")
st.caption("先定义 HfO2-FerroKG 的数据本体，再按本体进行信息抽取和图谱构建。")

st.markdown(
    """
    本体构建会生成一个版本化 ontology bundle，固定实体、字段、关系、枚举、单位标准化、
    证据要求和审核状态。后续抽取候选会写入 `ontology_version`，方便复现实验和迁移。
    """
)

if st.button("构建 / 刷新 HfO2-FerroKG v1 本体", type="primary"):
    init_database()
    stats = build_ontology()
    st.success("本体构建完成")
    st.json(stats)

try:
    bundle = load_ontology_bundle(build_if_missing=True)
    ontology = bundle["ontology"]
    validation = bundle["validation"]
except Exception as exc:
    st.error(f"本体尚未构建：{exc}")
    st.stop()

left, right = st.columns([1, 1])
with left:
    st.subheader("本体版本")
    st.metric("ontology_version", bundle["version"])
    st.metric("实体类型", validation["entity_count"])
    st.metric("关系类型", validation["relation_count"])
    st.metric("性能枚举", validation["property_count"])
    st.code(bundle["checksum"])

with right:
    st.subheader("输出文件")
    st.write(f"Bundle: `{bundle['bundle_path']}`")
    st.write(f"Report: `{bundle['report_path']}`")
    st.write("抽取前置顺序：")
    st.code("\n".join(ontology["workflow_order"]))

st.subheader("实体类型")
entity_rows = []
for name, spec in ontology["node_classes"].items():
    entity_rows.append(
        {
            "entity": name,
            "primary_key": spec.get("primary_key"),
            "field_count": len(spec.get("fields", {})),
            "description": spec.get("description"),
        }
    )
st.dataframe(pd.DataFrame(entity_rows), use_container_width=True)

st.subheader("关系类型")
relation_rows = [
    {"relation": name, "from": spec["from"], "to": spec["to"]}
    for name, spec in ontology["relation_classes"].items()
]
st.dataframe(pd.DataFrame(relation_rows), use_container_width=True)

st.subheader("抽取契约")
contract = ontology["extraction_contract"]
st.write("每条性能候选至少需要：")
st.code("\n".join(contract["required_for_property_candidate"]))
st.write("严格规则：")
st.code("\n".join(contract["strict_rules"]))

st.subheader("铁电性能标准化")
st.json(ontology["property_normalization"])
