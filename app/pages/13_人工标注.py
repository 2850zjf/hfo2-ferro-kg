from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.manual_annotation_service import (
    ANNOTATION_STATUSES,
    annotation_counts,
    get_annotation_item,
    list_annotation_items,
    save_annotation,
)
from backend.services.review_service import open_pdf_in_default_browser, open_pdf_with_default_app


PROPERTY_OPTIONS = [
    "double_remanent_polarization_2Pr",
    "remanent_polarization_Pr",
    "coercive_field_Ec",
    "saturation_polarization_Ps",
    "endurance_cycles",
    "retention_time",
    "memory_window",
    "leakage_current_density",
    "dielectric_constant",
    "wake_up",
    "fatigue",
    "unknown",
]

MATERIAL_FAMILIES = [
    "HZO",
    "HfO2",
    "La:HfO2",
    "Si:HfO2",
    "Al:HfO2",
    "Y:HfO2",
    "Gd:HfO2",
    "Sr:HfO2",
    "mixed_doped_HfO2",
    "unknown_hafnia",
]

PHASE_OPTIONS = [
    "",
    "orthorhombic",
    "monoclinic",
    "tetragonal",
    "cubic",
    "rhombohedral",
    "amorphous",
    "mixed",
    "unknown",
]

ANNOTATION_LABELS = {
    "unchecked": "未检查",
    "correct": "正确",
    "fixed": "已修正",
    "uncertain": "不确定",
    "reject": "剔除",
}


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _list_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _split_text(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _option_index(options: list[str], value: Any) -> int:
    text = "" if value is None else str(value)
    return options.index(text) if text in options else 0


def _summary_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        material = item.get("material") or {}
        sample = item.get("sample") or {}
        phase = item.get("phase") or {}
        prop = item.get("property") or {}
        rows.append(
            {
                "link_id": item["link_id"],
                "人工状态": ANNOTATION_LABELS.get(item.get("manual_status"), item.get("manual_status")),
                "关联质量": item.get("context_quality"),
                "AI状态": item.get("ai_review_status"),
                "材料": material.get("canonical_name") or material.get("raw_name"),
                "材料体系": material.get("material_family"),
                "性能": prop.get("property_name"),
                "数值": prop.get("normalized_value", prop.get("value")),
                "单位": prop.get("normalized_unit") or prop.get("unit"),
                "厚度nm": sample.get("film_thickness_nm"),
                "退火C": sample.get("annealing_temperature_c"),
                "电极/stack": sample.get("device_stack"),
                "相结构": phase.get("phase_name"),
                "页码": item.get("page_number"),
                "DOI": item.get("doi"),
                "论文": item.get("paper_title"),
            }
        )
    return rows


st.set_page_config(page_title="人工标注", layout="wide")
st.title("人工标注")
st.caption("简单检查一条样品级事实是否正确；需要修改时，直接改字段并保存。默认只保存人工标注，不覆盖原始抽取。")

counts = annotation_counts()
count_cols = st.columns(5)
for col, status in zip(count_cols, ["unchecked", "correct", "fixed", "uncertain", "reject"]):
    col.metric(ANNOTATION_LABELS[status], counts.get(status, 0))

filter_cols = st.columns([1, 1, 1, 2, 1])
with filter_cols[0]:
    status_filter = st.selectbox(
        "人工状态",
        ["all", "unchecked", "correct", "fixed", "uncertain", "reject"],
        format_func=lambda value: "全部" if value == "all" else ANNOTATION_LABELS.get(value, value),
    )
with filter_cols[1]:
    property_filter = st.selectbox("性能", ["all"] + PROPERTY_OPTIONS, index=1)
with filter_cols[2]:
    quality_filter = st.selectbox("关联质量", ["all", "strong", "partial", "weak", "ambiguous"], index=0)
with filter_cols[3]:
    query = st.text_input("搜索", placeholder="link_id / DOI / 论文标题 / 材料 / 证据句")
with filter_cols[4]:
    limit = st.number_input("加载条数", min_value=20, max_value=2000, value=300, step=50)

items = list_annotation_items(
    annotation_status=status_filter,
    property_name=property_filter,
    context_quality=quality_filter,
    query=query,
    limit=int(limit),
)

if not items:
    st.info("当前筛选条件下没有可标注条目。")
    st.stop()

st.dataframe(pd.DataFrame(_summary_rows(items)), use_container_width=True, hide_index=True, height=320)

link_ids = [item["link_id"] for item in items]
query_link_id = st.query_params.get("link_id")
default_index = link_ids.index(query_link_id) if query_link_id in link_ids else 0
selected_link_id = st.selectbox("选择要标注的 link_id", link_ids, index=default_index)
st.query_params["link_id"] = selected_link_id

selected = get_annotation_item(selected_link_id)
if not selected:
    st.error("没有找到这条样品级事实。")
    st.stop()

material = dict(selected.get("material") or {})
sample = dict(selected.get("sample") or {})
phase = dict(selected.get("phase") or {})
prop = dict(selected.get("property") or {})

st.divider()
top_cols = st.columns([2, 1, 1, 1])
with top_cols[0]:
    st.subheader(selected.get("paper_title") or "未识别论文标题")
    st.caption(f"DOI: {selected.get('doi') or '未识别'} | page: {selected.get('page_number') or ''} | link_id: {selected_link_id}")
with top_cols[1]:
    if st.button("用默认软件打开 PDF", use_container_width=True):
        result = open_pdf_with_default_app(selected.get("pdf_id"))
        (st.success if result.get("ok") else st.error)(result.get("message"))
with top_cols[2]:
    if st.button("用浏览器打开 PDF", use_container_width=True):
        result = open_pdf_in_default_browser(selected.get("pdf_id"), selected.get("page_number"))
        (st.success if result.get("ok") else st.error)(result.get("message"))
with top_cols[3]:
    st.metric("关联质量", selected.get("context_quality") or "unknown")

left, right = st.columns([1.15, 1])
with left:
    st.subheader("原文与证据")
    st.markdown("**证据句**")
    st.info(selected.get("evidence_text") or "没有证据句")
    st.markdown("**chunk 原文**")
    st.text_area(
        "chunk 原文",
        value=selected.get("source_text") or "",
        height=360,
        label_visibility="collapsed",
    )
    if selected.get("ai_risk_flags") or selected.get("ai_repair_suggestion"):
        with st.expander("AI 二次审核提示", expanded=True):
            st.write("风险标签：", ", ".join(str(item) for item in selected.get("ai_risk_flags") or []) or "无")
            st.write(selected.get("ai_repair_suggestion") or "")

with right:
    st.subheader("人工修改")
    with st.form(f"annotation_form_{selected_link_id}"):
        status_value = st.selectbox(
            "人工判断",
            ["unchecked", "correct", "fixed", "uncertain", "reject"],
            index=["unchecked", "correct", "fixed", "uncertain", "reject"].index(selected.get("manual_status") or "unchecked"),
            format_func=lambda value: ANNOTATION_LABELS.get(value, value),
        )

        st.markdown("**材料**")
        c1, c2 = st.columns(2)
        canonical_name = c1.text_input("材料名称", value=str(material.get("canonical_name") or material.get("raw_name") or ""))
        material_family_value = str(material.get("material_family") or "unknown_hafnia")
        material_family_options = (
            MATERIAL_FAMILIES
            if material_family_value in MATERIAL_FAMILIES
            else [material_family_value] + MATERIAL_FAMILIES
        )
        material_family = c2.selectbox(
            "材料体系",
            material_family_options,
            index=_option_index(material_family_options, material_family_value),
        )
        formula = st.text_input("化学式", value=str(material.get("formula") or ""))
        dopants = st.text_input("掺杂元素", value=_list_text(material.get("dopant_elements")))
        zr_fraction = st.text_input("Zr fraction", value="" if material.get("zr_fraction") is None else str(material.get("zr_fraction")))

        st.markdown("**性能**")
        c1, c2, c3 = st.columns([2, 1, 1])
        property_value = str(prop.get("property_name") or "unknown")
        property_options = (
            PROPERTY_OPTIONS
            if property_value in PROPERTY_OPTIONS
            else [property_value] + PROPERTY_OPTIONS
        )
        property_name = c1.selectbox(
            "性能名称",
            property_options,
            index=_option_index(property_options, property_value),
        )
        value = c2.text_input("数值", value="" if prop.get("normalized_value", prop.get("value")) is None else str(prop.get("normalized_value", prop.get("value"))))
        unit = c3.text_input("单位", value=str(prop.get("normalized_unit") or prop.get("unit") or ""))

        st.markdown("**样品和工艺**")
        c1, c2 = st.columns(2)
        thickness = c1.text_input("薄膜厚度 nm", value="" if sample.get("film_thickness_nm") is None else str(sample.get("film_thickness_nm")))
        deposition = c2.text_input("沉积方法", value=str(sample.get("deposition_method") or ""))
        c1, c2, c3 = st.columns(3)
        anneal_temp = c1.text_input("退火温度 °C", value="" if sample.get("annealing_temperature_c") is None else str(sample.get("annealing_temperature_c")))
        anneal_time = c2.text_input("退火时间 s", value="" if sample.get("annealing_time_s") is None else str(sample.get("annealing_time_s")))
        anneal_atm = c3.text_input("退火气氛", value=str(sample.get("annealing_atmosphere") or ""))
        device_stack = st.text_input("电极 / stack", value=str(sample.get("device_stack") or ""))
        c1, c2, c3 = st.columns(3)
        top_electrode = c1.text_input("上电极", value=str(sample.get("top_electrode") or ""))
        bottom_electrode = c2.text_input("下电极", value=str(sample.get("bottom_electrode") or ""))
        device_type = c3.text_input("器件类型", value=str(sample.get("device_type") or ""))

        st.markdown("**相结构和状态**")
        c1, c2, c3 = st.columns(3)
        phase_name = c1.selectbox("相结构", PHASE_OPTIONS, index=_option_index(PHASE_OPTIONS, phase.get("phase_name") or ""))
        space_group = c2.text_input("空间群", value=str(phase.get("space_group") or ""))
        context_quality = c3.selectbox(
            "关联质量",
            ["strong", "partial", "weak", "ambiguous"],
            index=_option_index(["strong", "partial", "weak", "ambiguous"], selected.get("context_quality") or "weak"),
        )
        c1, c2 = st.columns(2)
        wake_state = c1.text_input("wake-up 状态", value=str(sample.get("wake_up_or_endurance_state") or ""))
        cycle_number = c2.text_input("循环数", value="" if prop.get("cycle_number") is None else str(prop.get("cycle_number")))

        evidence_text = st.text_area("证据句", value=str(selected.get("evidence_text") or ""), height=100)
        notes = st.text_area("备注 / 批注", value=str(selected.get("manual_notes") or ""), height=100)
        apply_to_source = st.checkbox(
            "保存后应用到样品级关联数据（会影响后续 RAG、图谱和模型）",
            value=False,
        )
        submitted = st.form_submit_button("保存人工标注", type="primary")

    if submitted:
        material.update(
            {
                "canonical_name": canonical_name.strip(),
                "raw_name": material.get("raw_name") or canonical_name.strip(),
                "formula": formula.strip(),
                "material_family": material_family,
                "dopant_elements": _split_text(dopants),
                "zr_fraction": _float_or_none(zr_fraction),
            }
        )
        prop.update(
            {
                "property_name": property_name,
                "normalized_value": _float_or_none(value),
                "value": _float_or_none(value),
                "normalized_unit": unit.strip(),
                "unit": unit.strip(),
                "cycle_number": _int_or_none(cycle_number),
                "evidence_text": evidence_text.strip(),
            }
        )
        sample.update(
            {
                "film_thickness_nm": _float_or_none(thickness),
                "deposition_method": deposition.strip(),
                "annealing_temperature_c": _float_or_none(anneal_temp),
                "annealing_time_s": _float_or_none(anneal_time),
                "annealing_atmosphere": anneal_atm.strip(),
                "device_stack": device_stack.strip(),
                "top_electrode": top_electrode.strip(),
                "bottom_electrode": bottom_electrode.strip(),
                "device_type": device_type.strip(),
                "wake_up_or_endurance_state": wake_state.strip(),
            }
        )
        phase.update({"phase_name": phase_name, "space_group": space_group.strip()})
        save_annotation(
            link_id=selected_link_id,
            annotation_status=status_value,
            material=material,
            sample=sample,
            phase=phase,
            prop=prop,
            evidence_text=evidence_text.strip(),
            context_quality=context_quality,
            reviewer_notes=notes,
            apply_to_source=apply_to_source,
        )
        st.success("人工标注已保存。" + (" 已应用到样品级关联数据。" if apply_to_source else " 原始抽取未被覆盖。"))
        st.rerun()
